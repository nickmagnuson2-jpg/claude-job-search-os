#!/usr/bin/env python3
"""
fetch_source_email.py — pull ONE labeled email full-body for the source-email curation workflow.

The 15-min gmail_fetch sync truncates bodies (see memory
reference_gmail_fetch_truncation). This bypasses the truncating sanitize/write
path: it uses format=full via fetch_labeled_messages() and the raw body from
_process_message(), then dumps the complete text + headers to --out so it can be
hand-curated into data/source-emails/ (structured header, trimmed tracking URLs,
reflection backlink).

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/fetch_source_email.py \
      --label "Interesting thoughts" --match "html" --since 2026-05-01 \
      --out /tmp/source_raw.md

Reuses gmail_fetch.py auth + fetch helpers (no new credentials).
"""
import argparse
import sys
from pathlib import Path

# Import reusable helpers from the sibling fetcher.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gmail_fetch import (  # noqa: E402
    get_or_refresh_creds,
    fetch_labeled_messages,
    _process_message,
)


def find_label_id(service, label_name: str) -> str:
    resp = service.users().labels().list(userId="me").execute()
    labels = resp.get("labels", [])
    for lbl in labels:
        if lbl.get("name", "").lower() == label_name.lower():
            return lbl["id"]
    names = ", ".join(sorted(l.get("name", "") for l in labels))
    raise SystemExit(
        f"ERROR: label {label_name!r} not found. Available: {names}"
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", default=".")
    p.add_argument("--label", required=True, help="Gmail label NAME (resolved to ID).")
    p.add_argument(
        "--match",
        required=True,
        help="Case-insensitive substring; matched against subject first, then body.",
    )
    p.add_argument("--since", default=None, metavar="YYYY-MM-DD")
    p.add_argument("--max", type=int, default=50, dest="max_messages")
    p.add_argument("--out", required=True, help="Write full raw body + headers here.")
    args = p.parse_args()

    repo_root = Path(args.repo_root).resolve()
    tools_dir = repo_root / "tools"

    creds = get_or_refresh_creds(tools_dir, auth_mode=False)
    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise SystemExit("ERROR: googleapiclient not installed.")
    service = build("gmail", "v1", credentials=creds)

    label_id = find_label_id(service, args.label)
    print(f"Label {args.label!r} -> {label_id}")

    msgs = fetch_labeled_messages(
        service,
        label_id=label_id,
        since_date=args.since,
        max_messages=args.max_messages,
    )
    needle = args.match.lower()

    subj_hits, body_hits = [], []
    for m in msgs:
        meta, body = _process_message(m)
        if needle in meta["subject"].lower():
            subj_hits.append((meta, body))
        elif needle in (body or "").lower():
            body_hits.append((meta, body))

    candidates = subj_hits or body_hits
    if not candidates:
        print(
            f"No message matched {args.match!r} in {len(msgs)} labeled message(s).",
            file=sys.stderr,
        )
        print("Subjects scanned (newest first):", file=sys.stderr)
        for m in msgs[:20]:
            meta, _ = _process_message(m)
            print(f"  - {meta['date']} | {meta['subject']}", file=sys.stderr)
        sys.exit(1)

    if len(candidates) > 1:
        print(f"{len(candidates)} candidates (using first / newest):")
        for meta, _ in candidates:
            print(f"  - {meta['date']} | {meta['subject']} | {meta['sender']}")

    meta, body = candidates[0]
    out = Path(args.out).expanduser()
    out.write_text(
        f"MESSAGE-ID: {meta['message_id']}\n"
        f"FROM: {meta['sender']}\n"
        f"SUBJECT: {meta['subject']}\n"
        f"DATE: {meta['date']}\n"
        f"BODY-CHARS: {len(body or '')}\n"
        f"{'=' * 72}\n"
        f"{body or ''}\n",
        encoding="utf-8",
    )
    print(f"\nWrote {len(body or '')} body chars -> {out}")
    print(f"  SUBJECT: {meta['subject']}")
    print(f"  FROM:    {meta['sender']}")
    print(f"  DATE:    {meta['date']}")


if __name__ == "__main__":
    main()
