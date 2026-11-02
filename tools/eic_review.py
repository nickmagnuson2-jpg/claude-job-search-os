#!/usr/bin/env python3
"""eic_review.py - an email sent to the EIC opens a review that only her reply can close.

WHY THIS EXISTS (2026-10-30). Nick sends public posts to his editor-in-chief (EIC) before
they go out, and her edits are the highest-weight voice signal there is (a memory-tier
rule in the private auto-memory folder, 2026-10-19). That rule was written down
and fired twice anyway. On 2026-10-30 the post came back edited; the session never fetched
her reply, reviewed her final version as if it were another draft, argued to restore a
sentence she had cut, and logged none of her changes until Nick objected. Prose did not
hold, so the obligation is now a record a run cannot skip:

  * `draft_capture.py confirm` on an email to an EIC address opens a review here.
  * The review closes only with her reply's Gmail message id AND a memory/lessons.md row
    carrying the EIC tag (her diff, logged), or an explicit reason she made no edits.
    The id is looked up in Gmail: it must be an inbound message from an EIC address, dated
    after the review opened, and the lessons row must be dated on or after that day.
  * Re-confirming the capture as not sent cancels its review.
  * `tools/check_eic_review.py` blocks writing a published-post record
    (output/linkedin-profile/voice-sources/*-published.md) while any review is open.

WHAT THIS DOES NOT COVER, stated rather than buried: nothing here can stop a session from
arguing with her version in chat. It enforces that her reply is fetched and her diff is
logged before the post is recorded as published. The chat behaviour stays at memory tier.

PRIVATE CONFIG. The EIC's address never appears in this public file. It lives in the
gitignored `tools/.eic-reviewers.txt`, one address per line (`#` comments allowed), with an
optional `tag: <text>` line naming the lessons-row tag that marks her edits (default
`EIC EDIT`) and an optional `subject: <text>` line: only an email whose subject contains that
text, ignoring case, opens a review (default `LinkedIn post`), so personal mail to the same
address does not block publishing. EIC_REVIEWERS_PATH overrides the file and EIC_REVIEW_DIR the store, so a test
never touches the real ones.

USAGE
    PYTHONIOENCODING=utf-8 python3 tools/eic_review.py list
    PYTHONIOENCODING=utf-8 python3 tools/eic_review.py close <capture-id> --reply-id <gmail id> --lesson "<text from an EIC-tagged lessons row>"
    PYTHONIOENCODING=utf-8 python3 tools/eic_review.py close <capture-id> --reply-id <gmail id> --no-edits "<why>"
    PYTHONIOENCODING=utf-8 python3 tools/eic_review.py open <capture-id>      # backfill one by hand
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAG = "EIC EDIT"
DEFAULT_SUBJECT = "LinkedIn post"
# A Gmail message id as gmail_fetch.py prints it: 16 lowercase hex characters.
GMAIL_ID = re.compile(r"^[0-9a-f]{16}$")
# A lessons reference shorter than this matches too many rows to mean one of them.
MIN_LESSON_REF = 8
# Gmail labels that mark a message as not an inbound reply.
NOT_INBOUND = {"SENT", "DRAFT"}
LESSON_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ReviewError(Exception):
    pass


def reviewers_path() -> Path:
    override = os.environ.get("EIC_REVIEWERS_PATH")
    return Path(override) if override else Path(__file__).parent / ".eic-reviewers.txt"


def store_dir() -> Path:
    override = os.environ.get("EIC_REVIEW_DIR")
    return Path(override) if override else REPO_ROOT / "output" / "eic-reviews"


def lessons_path() -> Path:
    override = os.environ.get("LESSONS_PATH")
    return Path(override) if override else REPO_ROOT / "memory" / "lessons.md"


def load_config() -> tuple[set[str], str, str]:
    """(addresses, tag, subject filter). A missing file means no EIC is configured."""
    try:
        text = reviewers_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return set(), DEFAULT_TAG, DEFAULT_SUBJECT
    addresses, tag, subject = set(), DEFAULT_TAG, DEFAULT_SUBJECT
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.lower().startswith("tag:"):
            tag = line[4:].strip() or DEFAULT_TAG
        elif line.lower().startswith("subject:"):
            subject = line[8:].strip() or DEFAULT_SUBJECT
        else:
            addresses.add(line.lower())
    return addresses, tag, subject


def recipients(to_field: str) -> set[str]:
    """Bare lowercase addresses from a TO header: `a@x.com, Name <b@y.com>`."""
    found = re.findall(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", to_field or "")
    return {a.lower() for a in found}


def is_eic_email(meta: dict) -> bool:
    addresses, _, subject = load_config()
    return (meta.get("kind") == "email"
            and bool(addresses & recipients(meta.get("to", "")))
            and subject.lower() in (meta.get("subject") or "").lower())


def _record_path(capture_id: str) -> Path:
    return store_dir() / f"{capture_id}.json"


def _write(record: dict) -> None:
    path = _record_path(record["capture_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read(capture_id: str) -> dict:
    try:
        return json.loads(_record_path(capture_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReviewError(f"no EIC review for capture {capture_id!r}") from None


def open_for(meta: dict, now: float | None = None) -> bool:
    """Open a review for a sent email to the EIC. True when one was opened now.

    Never reopens or overwrites: a review that exists keeps its state, so re-confirming
    the same capture cannot erase a close."""
    opening = (is_eic_email(meta) and meta.get("status") in ("unchanged", "edited")
               and not _record_path(meta["id"]).exists())
    if opening:
        _write({
            "capture_id": meta["id"],
            "subject": meta.get("subject", ""),
            "sent_confirmed_at": meta.get("confirmed_at", ""),
            "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "status": "open",
        })
    return opening


def cancel_for(meta: dict, now: float | None = None) -> bool:
    """Cancel the open review of a capture re-confirmed as not sent. True when cancelled.

    A closed review stays closed: her reply was read, and a later correction to the send
    record does not undo that."""
    path = _record_path(meta["id"])
    if meta.get("status") != "not-sent" or not path.exists():
        return False
    record = read(meta["id"])
    if not isinstance(record, dict) or record.get("status") != "open":
        return False
    record.update({"status": "cancelled",
                   "cancelled_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))})
    _write(record)
    return True


def reviews() -> list[dict]:
    root = store_dir()
    if not root.exists():
        return []
    try:
        # os.listdir, not Path.glob: glob returns nothing for a folder it cannot read,
        # which would read as "no reviews" and let the post through.
        names = sorted(n for n in os.listdir(root) if n.endswith(".json"))
    except OSError:
        return [{"capture_id": f"(store {root} unreadable)", "status": "open",
                 "unreadable": True}]
    out = []
    for name in names:
        path = root / name
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = None
        if not isinstance(record, dict) or not isinstance(record.get("capture_id"), str):
            # An unreadable or non-object record, or one with no usable capture_id (Codex
            # r2 F2: `{}` crashed the block message), is reported as open: failing toward
            # the block is the safe direction for a gate whose whole point is not to let a
            # post through.
            record = {"capture_id": path.stem, "status": "open", "unreadable": True}
        out.append(record)
    return out


def open_reviews() -> list[dict]:
    """Reviews still owed. An unreadable record counts as owed, so the gate fails closed.
    A cancelled review (the send was corrected to not sent) is not owed."""
    return [r for r in reviews()
            if r.get("unreadable") or r.get("status") not in ("closed", "cancelled")]


def _tagged_rows(ref: str, tag: str) -> list[str]:
    """Lessons table rows that contain both `ref` and `tag`."""
    try:
        text = lessons_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    return [line for line in text.splitlines()
            if line.lstrip().startswith("|") and ref in line and tag in line]


def _row_date(row: str) -> str | None:
    """The row's Date cell, the last cell in the Section 2 table, as YYYY-MM-DD."""
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return cells[-1] if cells and LESSON_DATE.fullmatch(cells[-1]) else None


def fetch_reply(reply_id: str) -> dict:
    """The message `reply_id` as Gmail reports it: {"from", "labels", "epoch"}.

    Read-only metadata request through the same credentials tools/gmail_fetch.py uses.
    Tests replace this function; nothing else in this module talks to Gmail."""
    import gmail_fetch
    from googleapiclient.discovery import build
    creds = gmail_fetch.get_or_refresh_creds(Path(__file__).parent)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    msg = service.users().messages().get(userId="me", id=reply_id, format="metadata",
                                         metadataHeaders=["From"]).execute()
    headers = (msg.get("payload") or {}).get("headers") or []
    return {"from": gmail_fetch._extract_header(headers, "From"),
            "labels": list(msg.get("labelIds") or []),
            "epoch": int(msg["internalDate"]) / 1000}


def _check_reply(record: dict, reply_id: str, addresses: set[str]) -> None:
    """Her reply exists in Gmail, came from an EIC address, is inbound, and postdates the
    review. Any failure to ask Gmail refuses the close: this is the check that a reply was
    read, so it cannot pass on an unanswered question."""
    try:
        reply = fetch_reply(reply_id)
    except (Exception, SystemExit) as exc:  # gmail_fetch exits on missing credentials
        raise ReviewError(f"could not look up {reply_id} in Gmail ({exc}); "
                          f"the review stays open") from None
    if not addresses & recipients(reply.get("from", "")):
        raise ReviewError(f"{reply_id} is from {reply.get('from')!r}, not an EIC address")
    labels = set(reply.get("labels") or [])
    if labels & NOT_INBOUND:
        raise ReviewError(f"{reply_id} is labelled {sorted(labels & NOT_INBOUND)}, so it is "
                          f"not her reply")
    opened = time.mktime(time.strptime(record["opened_at"], "%Y-%m-%dT%H:%M:%S"))
    if float(reply.get("epoch", 0)) < opened:
        raise ReviewError(f"{reply_id} is dated before the review opened "
                          f"({record['opened_at']}), so it is not her reply to this post")


def close(capture_id: str, reply_id: str, lesson_ref: str | None = None,
          no_edits: str | None = None, now: float | None = None) -> dict:
    """Close a review: her reply was fetched, and her diff was logged or she made none."""
    record = read(capture_id)
    if not isinstance(record, dict):
        raise ReviewError(f"the review record for {capture_id} is not a JSON object")
    if record.get("status") in ("closed", "cancelled"):
        raise ReviewError(f"{capture_id} is already {record['status']}")
    if not GMAIL_ID.match((reply_id or "").strip()):
        raise ReviewError("--reply-id must be her reply's Gmail message id (16 hex "
                          "characters, as tools/gmail_fetch.py prints it)")
    if (lesson_ref is None) == (no_edits is None):
        raise ReviewError("pass exactly one of --lesson or --no-edits")
    addresses, tag, _ = load_config()
    if lesson_ref is not None:
        if len(lesson_ref.strip()) < MIN_LESSON_REF:
            raise ReviewError(f"--lesson must be at least {MIN_LESSON_REF} characters of "
                              f"text from one lessons row")
        rows = _tagged_rows(lesson_ref, tag)
        if not rows:
            raise ReviewError(f"--lesson {lesson_ref!r} is not in a {lessons_path()} table "
                              f"row tagged {tag!r}; log her edits as rows first")
        opened_day = record["opened_at"][:10]
        if not any((_row_date(r) or "") >= opened_day for r in rows):
            raise ReviewError(f"--lesson {lesson_ref!r} is only in rows dated before the "
                              f"review opened ({opened_day}) or with no readable date; "
                              f"log her edits to this post as a new row")
    elif not no_edits.strip():
        raise ReviewError("--no-edits needs a reason")
    _check_reply(record, reply_id.strip(), addresses)
    if lesson_ref is not None:
        record["lesson_ref"] = lesson_ref
    else:
        record["no_edits_reason"] = no_edits.strip()
    record.update({
        "status": "closed",
        "reply_id": reply_id.strip(),
        "closed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
    })
    _write(record)
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="every review, open first")
    o = sub.add_parser("open", help="open a review for a confirmed capture by hand")
    o.add_argument("capture_id")
    c = sub.add_parser("close", help="close a review once her reply is read")
    c.add_argument("capture_id")
    c.add_argument("--reply-id", required=True, help="her reply's Gmail message id")
    c.add_argument("--lesson", help="text from the EIC-tagged lessons row of her edits")
    c.add_argument("--no-edits", help="why her reply carried no edits")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "list":
            rows = sorted(reviews(), key=lambda r: r.get("status") == "closed")
            still_open = open_reviews()
            # A script's own directory is on sys.path, so this sibling import resolves.
            from count_report import report
            # The reach line goes to stderr so stdout stays one JSON document.
            report(f"EIC review records in {store_dir()}, every *.json read",
                   len(rows), len(still_open),
                   {"closed": len(rows) - len(still_open)}, file=sys.stderr)
            print(json.dumps({"status": "ok", "open": len(still_open),
                              "reviews": rows}, indent=2))
            return 0
        if args.cmd == "open":
            import draft_capture
            meta = draft_capture.read_meta(args.capture_id)
            if not is_eic_email(meta):
                raise ReviewError(f"{args.capture_id} is not a sent email to an EIC address")
            opened = open_for(meta)
            print(json.dumps({"status": "ok", "opened": opened, **read(args.capture_id)}))
            return 0
        record = close(args.capture_id, args.reply_id, lesson_ref=args.lesson,
                       no_edits=args.no_edits)
        print(json.dumps({"status": "ok", **record}, indent=2))
        return 0
    except (ReviewError, OSError, KeyError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
