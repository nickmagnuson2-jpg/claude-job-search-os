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
import contextlib
import json
import re
import sys
from pathlib import Path

# Import reusable helpers from the sibling fetcher.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gmail_fetch import (  # noqa: E402
    get_or_refresh_creds,
    fetch_labeled_messages,
    _process_message,
)


# --- pure helpers (no network; unit-tested in tests/scripts/test_fetch_source_email.py) ---

MESSAGE_ID_RE = re.compile(r"^>\s*\*\*Message-ID:\*\*\s*(\S+)\s*$", re.MULTILINE)


def captured_message_ids(source_dir: Path) -> set:
    """Message-IDs already curated into data/source-emails/.

    The identity key is the Gmail message id recorded in each captured file's header,
    NOT the subject: subjects repeat across a newsletter and would silently mark a new
    issue as already-pulled. Recurses, so the daily-stoic subdir counts too.
    """
    ids = set()
    if not source_dir.is_dir():
        return ids
    for f in source_dir.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ids.update(MESSAGE_ID_RE.findall(text))
    return ids


def partition_unpulled(metas, captured_ids):
    """Split label contents into (unpulled, pulled) preserving input order.

    A meta with no message_id counts as UNPULLED. Treating an unidentifiable message as
    already-captured would hide it forever, which is the failure this whole mode exists
    to fix; showing it twice is merely noise.
    """
    unpulled, pulled = [], []
    for m in metas:
        mid = (m.get("message_id") or "").strip()
        (pulled if mid and mid in captured_ids else unpulled).append(m)
    return unpulled, pulled


@contextlib.contextmanager
def stdout_to_stderr(active: bool):
    """Divert chatty progress output to stderr so --json emits ONE parseable document.

    The Gmail helpers print 'Backfill: found N...' / 'Fetched 10/17...' to stdout as they
    work. A consumer piping stdout into a JSON parser gets those lines first and dies.
    Found by running the tool for real; 37 passing fixture tests could not see it, because
    no fixture goes near the network path that does the printing.
    """
    if not active:
        yield
        return
    with contextlib.redirect_stdout(sys.stderr):
        yield


def resolve_source_dir(repo_root: Path, source_dir_arg) -> Path:
    """Where curated source emails live. Explicit arg wins; otherwise repo default."""
    if source_dir_arg:
        return Path(source_dir_arg).expanduser()
    return repo_root / "data" / "source-emails"


def build_list_payload(label: str, metas, captured_ids, source_dir: Path) -> dict:
    """The full --list result. Pure, so the mode's actual logic is testable.

    Kept out of main() deliberately: main is a network + argparse shell, and logic that
    lives there is unreachable by any test.
    """
    unpulled, pulled = partition_unpulled(metas, captured_ids)
    return {
        "status": "ok",
        "label": label,
        "scanned": len(metas),
        "source_dir": str(source_dir),
        "source_dir_exists": source_dir.is_dir(),
        "captured_ids_seen": len(captured_ids),
        "pulled_count": len(pulled),
        "unpulled_count": len(unpulled),
        "unpulled": [
            {
                "date": m.get("date"),
                "subject": m.get("subject"),
                "sender": m.get("sender"),
                "message_id": m.get("message_id"),
            }
            for m in unpulled
        ],
    }


def render_list_text(payload: dict) -> str:
    """Human-readable form of build_list_payload, for a terminal run."""
    lines = [
        "",
        f"{payload['unpulled_count']} unpulled / {payload['scanned']} scanned "
        f"({payload['pulled_count']} already in {payload['source_dir']})",
    ]
    for m in payload["unpulled"]:
        lines.append(f"  - {m['date']} | {m['subject']} | {m['sender']}")
    if not payload["unpulled"]:
        lines.append("  (nothing new in the label)")
    return "\n".join(lines)


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
        default=None,
        help="Case-insensitive substring; matched against subject first, then body. "
        "Required unless --list.",
    )
    p.add_argument("--since", default=None, metavar="YYYY-MM-DD")
    p.add_argument("--max", type=int, default=50, dest="max_messages")
    p.add_argument(
        "--out", default=None, help="Write full raw body + headers here. Required unless --list."
    )
    p.add_argument(
        "--list",
        action="store_true",
        dest="list_mode",
        help="READ-ONLY. Enumerate the label and mark which messages are not yet curated "
        "into data/source-emails/. Writes nothing. This is the mode that answers "
        "'what is sitting in the label that I have not reacted to yet?'",
    )
    p.add_argument("--json", action="store_true", help="With --list, emit JSON instead of text.")
    p.add_argument(
        "--source-dir",
        default=None,
        help="Curated source-email dir (default: <repo-root>/data/source-emails).",
    )
    args = p.parse_args()

    if not (args.label or "").strip():
        p.error("--label must not be empty")
    if not args.list_mode:
        missing = [f for f, v in (("--match", args.match), ("--out", args.out)) if not v]
        if missing:
            p.error(f"{' and '.join(missing)} required unless --list is passed")
        if not args.match.strip():
            p.error("--match must not be empty")

    repo_root = Path(args.repo_root).resolve()
    tools_dir = repo_root / "tools"

    creds = get_or_refresh_creds(tools_dir, auth_mode=False)
    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise SystemExit("ERROR: googleapiclient not installed.")
    service = build("gmail", "v1", credentials=creds)

    quiet = args.list_mode and args.json
    with stdout_to_stderr(quiet):
        label_id = find_label_id(service, args.label)
        print(f"Label {args.label!r} -> {label_id}")

        msgs = fetch_labeled_messages(
            service,
            label_id=label_id,
            since_date=args.since,
            max_messages=args.max_messages,
        )
    if args.list_mode:
        source_dir = resolve_source_dir(repo_root, args.source_dir)
        with stdout_to_stderr(quiet):
            payload = build_list_payload(
                args.label,
                [_process_message(m)[0] for m in msgs],
                captured_message_ids(source_dir),
                source_dir,
            )
        print(json.dumps(payload, ensure_ascii=False) if args.json else render_list_text(payload))
        return

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
