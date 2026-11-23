#!/usr/bin/env python3
"""workstream_write.py -- append a dated section to a workstream file, with the time read from
the clock by this script, never typed.

    add-section FILE --title TITLE (--body-file PATH | --body TEXT | --body-file -)

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
from inbox_lock import LockTimeout, file_lock  # noqa: E402

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


def add_section(path: Path, title: str, body: str) -> dict:
    target = check_target(path)
    title = check_title(title)
    body = check_body(body)
    try:
        with file_lock(target, timeout=LOCK_TIMEOUT_S):
            # Stamped inside the lock, so sections from serialized writers stay in clock order.
            heading, block = render(title, body, _now())
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
    args = ap.parse_args(argv)
    try:
        result = add_section(Path(args.file), args.title, _read_body(args))
    except WriteError as e:
        print(json.dumps({"status": "error", "code": e.code, "message": str(e)},
                         ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
