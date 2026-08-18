#!/usr/bin/env python3
"""
gmail_attachments.py - Durably save Gmail attachments for a label or query.

Companion to gmail_fetch.py (which captures bodies only, no attachments).
Reuses the same OAuth token in tools/gmail_token.json.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/gmail_attachments.py \
      --label "Some Label" --out /path/to/attachments [--repo-root .] [--dry-run]
  PYTHONIOENCODING=utf-8 python3 tools/gmail_attachments.py \
      --query "from:someone@example.com has:attachment" --out ./out

Writes <out>/<YYYY-MM-DD>-<msgid8>-<original-filename> so repeated runs are
idempotent and provenance stays attached to the file name. Also writes
<out>/MANIFEST.md recording message id, date, sender, subject, and sha256 per file.
"""
import argparse, base64, hashlib, io, re, sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name or "unnamed").strip("-")
    return name[:120] or "unnamed"


def walk_parts(payload):
    stack = [payload]
    while stack:
        p = stack.pop()
        for sub in p.get("parts", []) or []:
            stack.append(sub)
        yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", help="Gmail label name (exact, case-insensitive)")
    ap.add_argument("--query", help="Gmail search query instead of a label")
    ap.add_argument("--out", required=True, help="Directory to write attachments into")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--max", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.label and not a.query:
        ap.error("need --label or --query")

    from gmail_fetch import get_or_refresh_creds
    from googleapiclient.discovery import build

    tools_dir = Path(a.repo_root).resolve() / "tools"
    svc = build("gmail", "v1", credentials=get_or_refresh_creds(tools_dir))

    kw = {"userId": "me", "maxResults": a.max}
    if a.label:
        labels = svc.users().labels().list(userId="me").execute()["labels"]
        match = [l for l in labels if l["name"].lower() == a.label.lower()]
        if not match:
            print(f"ERROR: no label named {a.label!r}", file=sys.stderr)
            return 2
        kw["labelIds"] = [match[0]["id"]]
    if a.query:
        kw["q"] = a.query

    msgs = svc.users().messages().list(**kw).execute().get("messages", [])
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows, saved, skipped = [], 0, 0

    for m in msgs:
        full = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
        hdr = {h["name"]: h["value"] for h in full["payload"].get("headers", [])}
        try:
            dt = parsedate_to_datetime(hdr.get("Date", "")).strftime("%Y-%m-%d")
        except Exception:
            dt = "0000-00-00"
        for part in walk_parts(full["payload"]):
            fname = part.get("filename")
            body = part.get("body", {}) or {}
            aid = body.get("attachmentId")
            if not fname or not aid:
                continue
            dest = out / f"{dt}-{m['id'][:8]}-{safe_name(fname)}"
            if dest.exists():
                skipped += 1
                continue
            if a.dry_run:
                print(f"[dry-run] would save {dest.name}")
                saved += 1
                continue
            att = svc.users().messages().attachments().get(
                userId="me", messageId=m["id"], id=aid).execute()
            data = base64.urlsafe_b64decode(att["data"].encode("utf-8"))
            dest.write_bytes(data)
            rows.append((dest.name, m["id"], dt, hdr.get("From", "?"),
                         hdr.get("Subject", "?"), len(data),
                         hashlib.sha256(data).hexdigest()[:16]))
            saved += 1
            print(f"saved {dest.name} ({len(data)} bytes)")

    if rows and not a.dry_run:
        man = out / "MANIFEST.md"
        new = not man.exists()
        with io.open(man, "a", encoding="utf-8") as f:
            if new:
                f.write("# Attachment manifest\n\n"
                        "Written by `tools/gmail_attachments.py`. Append-only.\n\n"
                        "| File | Msg ID | Date | From | Subject | Bytes | sha256[:16] |\n"
                        "|---|---|---|---|---|---|---|\n")
            f.write(f"\n<!-- run {datetime.now().strftime('%Y-%m-%d %H:%M')} -->\n")
            for r in rows:
                cells = [str(x).replace("|", "\\|") for x in r]
                f.write("| " + " | ".join(cells) + " |\n")

    print(f"\ndone: {saved} saved, {skipped} already present, {len(msgs)} messages scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
