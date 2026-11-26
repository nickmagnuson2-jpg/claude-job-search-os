#!/usr/bin/env python3
"""workstream_write.py -- append a dated section to a workstream file, with the time read from
the clock by this script, never typed.

    add-section FILE --title TITLE (--body-file PATH | --body TEXT | --body-file -)
    add-next    FILE --action TEXT --done-when TEXT

Writes, at the end of FILE:

    ## YYYY-MM-DD HH:MM by `date` | TITLE
    <body>

WHY (C3, output/overnight-112126/DECISIONS.md; rule feedback_log_timestamps_from_date_not_estimates):
a typed time in a log heading drifted 35 min on 2026-11-21 (hooks.md, "18:12" written at 18:47),
and briefs and check-ins carried "~15:20" and "21:1x" stamps. Here the time has no input slot:
the CLI takes no time argument, the title may not start with a date or time, and the body may
not carry a dated heading of its own.

APPEND ONLY (the add-only rule for workstream files, orchestration.md 2026-11-21 17:26): the
script never reads, rewrites or reorders the file. It takes inbox_lock.file_lock on FILE (so
two writer calls serialize) and appends with one O_APPEND write, the same primitive `cat >>`
uses, so a lock-unaware `cat >>` running at the same moment is never lost. A whole-file
read-modify-write could drop it.

ADD-NEXT appends one {"date", "action", "done_when"} item to the ```json next block under the
header table. The date is today's, from the clock: workstream_state refuses an item dated after
today, and an item dated before today is already stale, so there is no --date. The file is
parsed by workstream_state's own reader (imported, never copied, so the writer and the check
that failed S3's suite on 2026-11-23 cannot disagree); a block that is already invalid is
refused rather than appended to. The item is inserted as text just before the list's closing
`]`: no existing item is re-serialized, reordered or touched, and the result is re-parsed and
checked to equal the old items plus the new one, with every original byte still in place. The
read-modify-write runs under inbox_lock.atomic_update, which retries when a lock-unaware writer
(a `cat >>`) changes the file mid-transform.

Scope: FILE must already exist, end in .md, and sit in a directory named data/workstreams.
Creating a workstream is a registry decision, not a log entry.

Output: JSON. ok -> {"status": "ok", "file", "heading", "bytes"} exit 0;
error -> {"status": "error", "code", "message"} exit 1 (usage errors exit 2 via argparse).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inbox_lock import ConcurrentModification, LockTimeout, atomic_update, file_lock  # noqa: E402
import workstream_state as _ws  # noqa: E402

# The workstream_state names add-next and the exempt refusal reuse. Private ones by org f1's ruling (2026-11-23), so
# no other branch touches workstream_state.py; test_every_workstream_state_name_the_writer_
# imports_still_exists fails by name if one is renamed.
STATE_NAMES = ("_visible_lines", "read_header", "_read_next", "NEXT_FENCE", "MIN_ACTION",
               "load_exemptions", "ExemptFileError", "EXEMPT_FILE")

HEADING_FMT = "## {stamp} by `date` | {title}"
STAMP_FMT = "%Y-%m-%d %H:%M"
LOCK_TIMEOUT_S = 30
# A body line that would read as a dated section heading of its own.
DATED_HEADING = re.compile(r"^[ \t]*##[ \t]+\d{4}-\d{2}-\d{2}")
# A title that starts with a date or a time is a typed stamp in another slot.
TYPED_STAMP = re.compile(r"^\s*(?:~\s*)?(?:\d{4}-\d{2}-\d{2}|\d{1,2}:\d)")


class WriteError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    """The only source of the stamp. Tests replace this; the CLI has no way to."""
    return datetime.now().astimezone()


def check_target(path: Path) -> Path:
    if path.suffix != ".md":
        raise WriteError("not_markdown", f"{path} is not a .md file")
    resolved = path.resolve()
    if resolved.parent.name != "workstreams" or resolved.parent.parent.name != "data":
        raise WriteError("not_a_workstream",
                         f"{path} is not in a data/workstreams/ directory")
    if not resolved.is_file():
        raise WriteError("no_such_workstream",
                         f"{path} does not exist; creating a workstream is a registry "
                         "decision, not a log entry")
    return resolved


def check_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise WriteError("empty_title", "--title is empty")
    if "\n" in title or "\r" in title:
        raise WriteError("multiline_title", "--title must be one line")
    if TYPED_STAMP.match(title):
        raise WriteError("typed_stamp_in_title",
                         f"--title starts with a typed date or time ({title[:20]!r}); the "
                         "heading's stamp comes from the clock, so leave it out")
    return title


def check_body(body: str) -> str:
    for n, line in enumerate(body.splitlines(), 1):
        if DATED_HEADING.match(line):
            raise WriteError("dated_heading_in_body",
                             f"body line {n} is a dated '## YYYY-MM-DD' heading; each dated "
                             "section is one add-section call")
    return body.strip("\n")


def render(title: str, body: str, now: datetime) -> tuple[str, str]:
    """(heading, block to append). The block opens with a blank line, like `cat >>` entries."""
    heading = HEADING_FMT.format(stamp=now.strftime(STAMP_FMT), title=title)
    block = f"\n{heading}\n{body}\n" if body else f"\n{heading}\n"
    return heading, block


def newest_next_date(text: str, today) -> str | None:
    """The newest item date in a VALID json next block, else None (no table, no block, or a
    block workstream_state refuses: those files are not this check's to judge)."""
    header = _ws.read_header(text)
    if not header["table"]:
        return None
    items, reasons = _ws._read_next(_ws._visible_lines(text), header["end"], today)
    if reasons:
        return None
    return max(i["date"] for i in items)


def stale_next_message(target, newest: str, today: str) -> str:
    return (f"the newest next item in {target} is dated {newest}, older than today ({today}): "
            f"run `tools/workstream_write.py add-next {target} --action ... --done-when ...` "
            "first")


def check_not_exempt(target: Path, today) -> None:
    """Refuse a dated section in a workstream listed in backlog-exempt.json after its cutover.

    2026-11-25 20:14: a section written this way into table-writers.md made workstream_state
    report a problem and broke every suite and the preflight for about 20 minutes. The list
    is read with workstream_state's own parser, so this writer and the check that fails the
    suite cannot disagree. A malformed list refuses (the suite fails on it anyway). A dated
    section ON the cutover day is allowed: the backlog check flags only headings after it.
    KNOWN GAP: a `cat >>` heading write bypasses this writer, and no hook reads the list
    (check_workstream_heading_time checks typed times and stale next items only); no fire
    recorded through that path (org 3e, 2026-11-26: reopen when it fires once)."""
    try:
        cutover, slugs = _ws.load_exemptions(target.parent)
    except _ws.ExemptFileError as e:
        raise WriteError("exempt_file_invalid", f"{e}; fix it before writing a dated "
                         "section") from None
    if cutover is not None and target.stem in slugs and today > cutover:
        raise WriteError("exempt_workstream",
                         f"{target.stem} is listed in {_ws.EXEMPT_FILE} (cutover {cutover}), "
                         "and a dated section after the cutover breaks the backlog check and "
                         "every suite. Fill its backlog header rows and remove the slug from "
                         f"{_ws.EXEMPT_FILE} first, or log in a non-exempt workstream")


def add_section(path: Path, title: str, body: str) -> dict:
    target = check_target(path)
    title = check_title(title)
    body = check_body(body)
    try:
        with file_lock(target, timeout=LOCK_TIMEOUT_S):
            now = _now()
            check_not_exempt(target, now.date())
            # STALE NEXT (Nick, 2026-11-23, codify #5): a dated section newer than the header's
            # newest next item is the shape that failed S3's 07:59 suite. Refused here because
            # this heading never appears in a command the guard could read.
            today = now.date().isoformat()
            newest = newest_next_date(target.read_text(encoding="utf-8"), now.date())
            if newest is not None and newest < today:
                raise WriteError("stale_next", stale_next_message(target, newest, today))
            # Stamped inside the lock, so sections from serialized writers stay in clock order.
            heading, block = render(title, body, now)
            data = block.encode("utf-8")
            if target.stat().st_size and not _ends_with_newline(target):
                data = b"\n" + data
            fd = os.open(target, os.O_WRONLY | os.O_APPEND)
            try:
                written = os.write(fd, data)
            finally:
                os.close(fd)
    except LockTimeout as e:
        raise WriteError("lock_timeout", str(e)) from None
    if written != len(data):
        raise WriteError("short_write", f"wrote {written} of {len(data)} bytes to {target}")
    return {"status": "ok", "file": str(target), "heading": heading, "bytes": written}


def _check_text(field: str, value: str) -> str:
    if len(value.strip()) < _ws.MIN_ACTION:
        raise WriteError(f"short_{field}",
                         f"--{field.replace('_', '-')} must be at least {_ws.MIN_ACTION} "
                         "characters" + (" stating the observable result that means done"
                                         if field == "done_when" else ""))
    return value


def _insert_item(text: str, item: dict, today) -> str:
    """`text` with `item` appended to its next block, or WriteError. Pure: no I/O."""
    header = _ws.read_header(text)
    if not header["table"]:
        raise WriteError("no_header_table", "no '| Field | Value |' header table, so there "
                         "is no next block to append to")
    lines = _ws._visible_lines(text)
    items, reasons = _ws._read_next(lines, header["end"], today)
    if reasons:
        raise WriteError("next_block_invalid", "the next block is already invalid, fix it "
                         "first: " + "; ".join(reasons))
    start = next(i for i, ln in enumerate(lines) if ln.strip() == _ws.NEXT_FENCE)
    close = next(k for k in range(start + 1, len(lines)) if lines[k].strip() == "```")
    raw = text.splitlines(keepends=True)
    before, block, after = "".join(raw[:start + 1]), "".join(raw[start + 1:close]), \
        "".join(raw[close:])
    k = block.rindex("]")
    head = block[:k].rstrip()
    rendered = "\n".join("  " + ln for ln in
                         json.dumps(item, indent=2, ensure_ascii=False).splitlines())
    insertion = ",\n" + rendered
    new = before + head + insertion + block[len(head):] + after
    # Verify, never assume: same items plus one, and every original byte still in place.
    new_items, new_reasons = _ws._read_next(_ws._visible_lines(new),
                                            _ws.read_header(new)["end"], today)
    if new_reasons or new_items != items + [item] or new.replace(insertion, "", 1) != text:
        raise WriteError("insert_unverified", "the appended block did not re-parse to the "
                         "old items plus the new one; nothing was written: "
                         + "; ".join(new_reasons))
    return new


def add_next(path: Path, action: str, done_when: str) -> dict:
    target = check_target(path)
    action, done_when = _check_text("action", action), _check_text("done_when", done_when)
    seen = {}

    def transform(text: str) -> str:
        now = _now()
        item = {"date": now.date().isoformat(), "action": action, "done_when": done_when}
        seen["item"] = item
        return _insert_item(text, item, now.date())

    try:
        res = atomic_update(target, transform, timeout=LOCK_TIMEOUT_S)
    except LockTimeout as e:
        raise WriteError("lock_timeout", str(e)) from None
    except ConcurrentModification as e:
        raise WriteError("concurrent_modification", str(e)) from None
    return {"status": "ok", "file": str(target), "item": seen["item"],
            "attempts": res["attempts"]}


def _ends_with_newline(path: Path) -> bool:
    with path.open("rb") as f:
        f.seek(-1, os.SEEK_END)
        return f.read(1) == b"\n"


def _read_body(args) -> str:
    if args.body is not None:
        return args.body
    if args.body_file == "-":
        return sys.stdin.read()
    try:
        return Path(args.body_file).read_text(encoding="utf-8")
    except OSError as e:
        raise WriteError("body_unreadable", f"cannot read --body-file: {e}") from None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Append a clock-stamped section to a workstream.")
    sub = ap.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add-section")
    add.add_argument("file")
    add.add_argument("--title", required=True)
    src = add.add_mutually_exclusive_group(required=True)
    src.add_argument("--body-file")
    src.add_argument("--body")
    nxt = sub.add_parser("add-next")
    nxt.add_argument("file")
    nxt.add_argument("--action", required=True)
    nxt.add_argument("--done-when", required=True)
    args = ap.parse_args(argv)
    try:
        if args.command == "add-next":
            result = add_next(Path(args.file), args.action, args.done_when)
        else:
            result = add_section(Path(args.file), args.title, _read_body(args))
    except WriteError as e:
        print(json.dumps({"status": "error", "code": e.code, "message": str(e)},
                         ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
