#!/usr/bin/env python3
"""
voice_extract.py - Pull labeled Gmail emails, clean to prose-only, save corpus.

Reuses gmail_fetch.py auth (get_or_refresh_creds). Pulls all messages with a
given Gmail label, filters to emails Nick SENT (not received), strips quoted
replies and signatures, and saves cleaned prose to data/voice-corpus/.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/voice_extract.py
  PYTHONIOENCODING=utf-8 python3 tools/voice_extract.py --label "job-search/voice-ref"
  PYTHONIOENCODING=utf-8 python3 tools/voice_extract.py --dry-run

Default label: job-search/voice-ref
Default output dir: data/voice-corpus/
Default sender filter: you@example.com
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Reuse gmail_fetch.py functions including injection sanitization
sys.path.insert(0, str(Path(__file__).parent))
from gmail_fetch import (
    get_or_refresh_creds,
    extract_plain_text,
    _parse_email_date,
    INJECTION_REGEX,
    INVISIBLE_UNICODE_REGEX,
)


SENDER_EMAIL = "you@example.com"  # set to your own sender address
DEFAULT_LABEL = "job-search/voice-ref"


# ── Cleaning helpers ──────────────────────────────────────────────────────────

def strip_quoted_reply(text: str) -> str:
    """Remove quoted reply blocks. Keeps only Nick's prose."""
    # "On Mon, Apr 28, 2026 at 2:00 PM, John <john@x.com> wrote:" and everything after
    text = re.split(
        r"\n\s*On\s+\w+,?\s+\w+\s+\d+,?\s+\d{4}.*?wrote:\s*",
        text,
        maxsplit=1,
        flags=re.IGNORECASE | re.DOTALL,
    )[0]
    # Lines starting with > (quoted reply markers)
    lines = [ln for ln in text.split("\n") if not ln.lstrip().startswith(">")]
    text = "\n".join(lines)
    # "----- Original Message -----" or similar dividers
    text = re.split(r"\n\s*-{3,}\s*Original Message\s*-{3,}", text, maxsplit=1)[0]
    text = re.split(r"\n\s*_{5,}\s*\n", text, maxsplit=1)[0]
    # Gmail "From: ... Sent: ... To: ... Subject:" forwarded headers
    text = re.split(r"\n\s*From:\s+.*\n\s*Sent:\s+", text, maxsplit=1, flags=re.IGNORECASE)[0]
    return text.strip()


def strip_signature(text: str) -> str:
    """Remove common signature patterns. Keeps just the prose."""
    # "-- " (RFC 3676 signature delimiter)
    text = re.split(r"\n--\s*\n", text, maxsplit=1)[0]
    # "Sent from my iPhone" / "Sent from my mobile"
    text = re.split(r"\n+Sent from my \w+", text, maxsplit=1, flags=re.IGNORECASE)[0]
    # Common signature blocks: "Best,\nNick" or "Thanks,\nNick" — KEEP these,
    # they're voice signature. Don't strip closings.
    return text.strip()


def clean_body(raw_body: str) -> str:
    """Full pipeline: strip HTML, quoted reply, signature, normalize whitespace."""
    # Strip HTML tags
    if re.search(r"<(html|body|p|div|span|br)\b", raw_body, re.IGNORECASE):
        try:
            from bs4 import BeautifulSoup
            text = BeautifulSoup(raw_body, "html.parser").get_text(separator="\n")
        except ImportError:
            text = re.sub(r"<[^>]+>", " ", raw_body)
            text = re.sub(r"&nbsp;", " ", text)
            text = re.sub(r"&amp;", "&", text)
    else:
        text = raw_body

    # Strip invisible unicode (defense-in-depth)
    text = INVISIBLE_UNICODE_REGEX.sub("", text)
    # Redact prompt-injection phrases (defense-in-depth — this corpus will be read by Claude)
    text = INJECTION_REGEX.sub("[REDACTED - potential injection]", text)
    # Strip quoted replies
    text = strip_quoted_reply(text)
    # Strip signatures
    text = strip_signature(text)
    # Normalize whitespace
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_body(payload: dict) -> str:
    """Walk Gmail message payload tree, collect text/plain or text/html."""
    import base64

    def walk(part):
        results = []
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if data:
            try:
                decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                results.append((mime, decoded))
            except Exception:
                pass
        for sub in part.get("parts", []):
            results.extend(walk(sub))
        return results

    parts = walk(payload)
    plain = next((d for m, d in parts if m == "text/plain"), None)
    html = next((d for m, d in parts if m == "text/html"), None)
    return plain or html or ""


# ── Main ──────────────────────────────────────────────────────────────────────

def find_label_id(service, label_name: str) -> str:
    """Look up Gmail label ID by name. Case-sensitive match."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == label_name:
            return label["id"]
    available = ", ".join(sorted(label["name"] for label in labels))
    print(f"ERROR: Label '{label_name}' not found.", file=sys.stderr)
    print(f"Available labels: {available}", file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=DEFAULT_LABEL, help="Gmail label name")
    ap.add_argument("--sender", default=SENDER_EMAIL, help="Filter to emails sent BY this address")
    ap.add_argument("--output-dir", default="data/voice-corpus", help="Directory for cleaned corpus")
    ap.add_argument("--repo-root", default=".", help="Repo root for resolving paths")
    ap.add_argument("--dry-run", action="store_true", help="List matches without writing files")
    ap.add_argument("--include-replies", action="store_true", help="Include Re: / Fwd: emails (default: initial outreach only)")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    tools_dir = repo_root / "tools"
    output_dir = repo_root / args.output_dir

    # Auth
    creds = get_or_refresh_creds(tools_dir)
    from googleapiclient.discovery import build
    service = build("gmail", "v1", credentials=creds)

    # Find label ID
    label_id = find_label_id(service, args.label)
    print(f"Found label '{args.label}' (id: {label_id})", file=sys.stderr)

    # List all messages with the label
    print(f"Fetching messages with label '{args.label}'...", file=sys.stderr)
    all_messages = []
    page_token = None
    while True:
        kwargs = {"userId": "me", "labelIds": [label_id], "maxResults": 100}
        if page_token:
            kwargs["pageToken"] = page_token
        resp = service.users().messages().list(**kwargs).execute()
        all_messages.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    print(f"Found {len(all_messages)} messages with this label", file=sys.stderr)

    if not all_messages:
        print("No messages to process. Exiting.", file=sys.stderr)
        return

    # Process each message
    output_dir.mkdir(parents=True, exist_ok=True)
    kept = 0
    skipped_not_sender = 0
    skipped_too_short = 0
    skipped_reply_or_forward = 0
    summary = []

    for i, msg_meta in enumerate(all_messages, 1):
        msg = service.users().messages().get(
            userId="me", id=msg_meta["id"], format="full"
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        sender = headers.get("from", "")
        to = headers.get("to", "")
        subject = headers.get("subject", "(no subject)")
        date = headers.get("date", "")

        # Filter: only emails Nick sent
        if args.sender.lower() not in sender.lower():
            skipped_not_sender += 1
            continue

        # Filter: initial outreach only (skip Re: / Fwd: unless --include-replies)
        if not args.include_replies:
            subject_normalized = subject.strip().lower()
            if re.match(r"^(re|fwd?|fw)\s*:", subject_normalized):
                skipped_reply_or_forward += 1
                continue

        raw_body = extract_body(msg["payload"])
        cleaned = clean_body(raw_body)

        # Filter: too short
        if len(cleaned) < 90:
            skipped_too_short += 1
            continue

        # Save
        dt = _parse_email_date(date)
        date_prefix = dt.strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:40] or "email"
        filename = f"{date_prefix}-{slug}.md"

        content = (
            f"# {subject}\n\n"
            f"> **To:** {to}\n"
            f"> **Date:** {date}\n"
            f"> **Message-ID:** {msg_meta['id']}\n\n"
            f"{cleaned}\n"
        )

        if args.dry_run:
            print(f"[DRY] Would write: {filename} ({len(cleaned)} chars)", file=sys.stderr)
        else:
            (output_dir / filename).write_text(content, encoding="utf-8")

        kept += 1
        summary.append({
            "filename": filename,
            "to": to,
            "subject": subject,
            "date": date,
            "char_count": len(cleaned),
            "word_count": len(cleaned.split()),
        })

    # Final report
    result = {
        "label": args.label,
        "total_with_label": len(all_messages),
        "kept": kept,
        "skipped_not_from_nick": skipped_not_sender,
        "skipped_too_short": skipped_too_short,
        "skipped_reply_or_forward": skipped_reply_or_forward,
        "include_replies": bool(args.include_replies),
        "output_dir": str(output_dir),
        "messages": summary,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
