#!/usr/bin/env python3
"""draft_capture.py - keep the exact first draft, and what was actually sent, under one ID.

WHY THIS EXISTS (2026-10-06). The question "how much does Nick have to fix a draft before
he sends it" could not be answered from anything on disk:

  * tools/.pending-draft.txt is ONE file. open_draft.py renamed it `.sent` the moment Gmail
    compose opened, before any edit and with no evidence of a send, and the next draft
    overwrote it.
  * The drafting skills archive the approved draft, then quote that same text into
    data/networking.md, so the two records that looked like "draft" and "sent" were the
    draft twice.
  * The Gmail fetch reads inbound mail only.

A plan to evaluate the drafting skills from historical draft-and-sent pairs failed review by
three models for exactly this reason. What all of them asked for first is this file: record
the draft at the moment it leaves, and record what was sent when Nick says so.

WHAT IT RECORDS. One directory per draft under output/draft-captures/ (private, gitignored,
in the nightly backup):

    <id>/draft-v0.txt   the pending draft, byte for byte, as it was when compose opened
    <id>/meta.json      id, kind, skill, recipient, subject, opened_at, status, and after
                        confirmation: confirmed_at, how the sent text was obtained, and
                        how much of the draft changed
    <id>/sent.txt       the text Nick sent, only when it differs from the draft

STATUS. `opened` means compose was opened and nothing else is known. It is NOT evidence of a
send. Only `confirm` moves it: `unchanged`, `edited` or `not-sent`.

USAGE
    PYTHONIOENCODING=utf-8 python3 tools/draft_capture.py pending
    PYTHONIOENCODING=utf-8 python3 tools/draft_capture.py confirm latest unchanged
    PYTHONIOENCODING=utf-8 python3 tools/draft_capture.py confirm <id> edited --body-file sent.txt --screenshot shot.png
    PYTHONIOENCODING=utf-8 python3 tools/draft_capture.py confirm <id> not-sent
    PYTHONIOENCODING=utf-8 python3 tools/draft_capture.py cv output/<slug>/MMDDYY-magnuson.content.yaml

`--screenshot` records where the sent text came from. Nick's habit is to screenshot the sent
mail; the body file is then a transcription of that image, and the record says so, because
a transcription is weaker evidence than a paste.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DRAFT_NAME = "draft-v0.txt"
SENT_NAME = "sent.txt"
META_NAME = "meta.json"
OUTCOMES = ("unchanged", "edited", "not-sent")


def store_dir() -> Path:
    """Where captures live. DRAFT_CAPTURE_DIR overrides it, so a test never writes into
    the real store."""
    override = os.environ.get("DRAFT_CAPTURE_DIR")
    return Path(override) if override else REPO_ROOT / "output" / "draft-captures"


def _write_meta(directory: Path, meta: dict) -> None:
    tmp = directory / (META_NAME + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, directory / META_NAME)


def read_meta(capture_id: str) -> dict:
    return json.loads((store_dir() / capture_id / META_NAME).read_text(encoding="utf-8"))


def _header(text: str, name: str) -> str:
    """A `NAME: value` line from the part of a pending draft that precedes BODY:."""
    for line in text.split("\n"):
        if line.startswith("BODY:"):
            break
        if line.startswith(name + ":"):
            return line[len(name) + 1:].strip()
    return ""


def body_of(draft_text: str) -> str:
    """The message body of a pending draft: everything after `BODY:`, or the whole text
    when there is no such line (a CV, or a body pasted on its own)."""
    lines = draft_text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("BODY:"):
            return "\n".join([line[5:].strip()] + lines[i + 1:]).strip()
    return draft_text.strip()


def capture(draft_text: str, kind: str = "email", skill: str = "", source: str = "",
            now: float | None = None) -> str:
    """Record a draft exactly as given and return its ID. Never overwrites a capture."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    digest = hashlib.sha1(draft_text.encode("utf-8")).hexdigest()[:6]
    base = f"{stamp}-{digest}"
    root = store_dir()
    capture_id, n = base, 1
    while True:
        try:
            (root / capture_id).mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            n += 1
            capture_id = f"{base}-{n}"
    directory = root / capture_id
    (directory / DRAFT_NAME).write_text(draft_text, encoding="utf-8")
    _write_meta(directory, {
        "id": capture_id, "kind": kind, "skill": skill, "source": source,
        "to": _header(draft_text, "TO"), "subject": _header(draft_text, "SUBJECT"),
        "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "status": "opened",
    })
    return capture_id


def capture_file_once(path: Path, kind: str, skill: str) -> tuple[str, bool]:
    """Capture a file the FIRST time it is seen, and never again. (id, was_new).

    For artifacts that are rewritten in place: a CV's content file is saved once and then
    corrected by review passes and by Nick, at the same path. The first version to pass
    through here is the only one that can be compared with what is finally sent, so a
    later call for the same path returns the existing capture and stores nothing.
    """
    source = str(Path(path).resolve())
    for meta in captures():
        if meta.get("kind") == kind and meta.get("source") == source:
            return meta["id"], False
    return capture(Path(path).read_text(encoding="utf-8"), kind=kind, skill=skill,
                   source=source), True


def captures() -> list[dict]:
    """Every capture's meta, oldest first. A directory with no readable meta is skipped."""
    found = []
    root = store_dir()
    if not root.is_dir():
        return found
    for d in sorted(root.iterdir()):
        try:
            found.append(json.loads((d / META_NAME).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return found


def pending(kind: str | None = None) -> list[dict]:
    """Captures nobody has confirmed: compose was opened and that is all that is known."""
    return [m for m in captures()
            if m.get("status") == "opened" and (kind is None or m.get("kind") == kind)]


def _words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def changed_word_ratio(draft: str, sent: str) -> float:
    """Share of words that differ between the draft and what was sent, 0.0 to 1.0.

    Words that survive in order count as kept; the ratio is taken over the longer text, so
    adding a paragraph and deleting one both register. 0.0 is sent as drafted. 1.0 is a
    rewrite with nothing kept. Whitespace and line breaks are not edits.
    """
    a, b = _words(draft), _words(sent)
    if not a and not b:
        return 0.0
    kept = sum(block.size for block in
               difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return round(1.0 - kept / max(len(a), len(b)), 4)


class CaptureError(Exception):
    """A confirmation that cannot be recorded as asked."""


def resolve(capture_id: str) -> str:
    """`latest` is the most recently opened capture that is still unconfirmed."""
    if capture_id != "latest":
        if not (store_dir() / capture_id / META_NAME).is_file():
            raise CaptureError(f"no capture with id {capture_id!r}")
        return capture_id
    waiting = pending()
    if not waiting:
        raise CaptureError("no unconfirmed capture; name an id to change a confirmed one")
    return waiting[-1]["id"]


def confirm(capture_id: str, outcome: str, sent_text: str | None = None,
            screenshots: list[str] | None = None, force: bool = False,
            now: float | None = None) -> dict:
    """Record what happened to a draft. Returns the updated meta."""
    if outcome not in OUTCOMES:
        raise CaptureError(f"outcome must be one of {', '.join(OUTCOMES)}")
    capture_id = resolve(capture_id)
    directory = store_dir() / capture_id
    meta = read_meta(capture_id)
    if meta.get("status") != "opened" and not force:
        raise CaptureError(f"{capture_id} is already confirmed as {meta.get('status')!r}; "
                           f"pass --force to replace that")
    if outcome == "edited" and not (sent_text or "").strip():
        raise CaptureError("an edited send needs the text that was sent (--body-file)")
    if outcome != "edited" and sent_text is not None:
        raise CaptureError(f"a body was given but the outcome is {outcome!r}; "
                           f"use `edited` if the text changed")

    draft_body = body_of((directory / DRAFT_NAME).read_text(encoding="utf-8"))
    sent_file = directory / SENT_NAME
    if outcome == "edited":
        sent_file.write_text(sent_text, encoding="utf-8")
        ratio = changed_word_ratio(draft_body, body_of(sent_text))
        meta["sent_words"] = len(_words(body_of(sent_text)))
    else:
        # A replaced confirmation must not leave an older sent.txt beside a record that
        # now says unchanged or not sent.
        sent_file.unlink(missing_ok=True)
        meta.pop("sent_words", None)
        ratio = 0.0 if outcome == "unchanged" else None
    meta.update({
        "status": outcome,
        "confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "draft_words": len(_words(draft_body)),
        "changed_word_ratio": ratio,
        # How the sent text reached this record. A transcription of a screenshot is weaker
        # evidence than pasted text, and a later reader has to be able to tell.
        "sent_source": ("screenshot-transcription" if screenshots
                        else "pasted" if outcome == "edited" else None),
        "screenshots": list(screenshots or []),
    })
    _write_meta(directory, meta)
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pending", help="list captures that have not been confirmed")
    c = sub.add_parser("confirm", help="record what happened to a draft")
    c.add_argument("id", help="a capture id, or `latest`")
    c.add_argument("outcome", choices=OUTCOMES)
    c.add_argument("--body-file", type=Path, help="the text that was sent (for `edited`)")
    c.add_argument("--screenshot", action="append", default=[],
                   help="image the sent text was transcribed from; repeatable")
    c.add_argument("--force", action="store_true", help="replace an existing confirmation")
    v = sub.add_parser("cv", help="capture the first generated version of a CV")
    v.add_argument("path", type=Path)
    args = ap.parse_args(argv)

    try:
        if args.cmd == "pending":
            print(json.dumps({"status": "ok", "pending": pending()}, indent=2))
            return 0
        if args.cmd == "cv":
            cid, was_new = capture_file_once(args.path, kind="cv", skill="generate-cv")
            print(json.dumps({"status": "ok", "id": cid, "new": was_new}))
            return 0
        sent = None
        if args.body_file is not None:
            sent = args.body_file.read_text(encoding="utf-8")
        meta = confirm(args.id, args.outcome, sent_text=sent,
                       screenshots=args.screenshot, force=args.force)
        print(json.dumps({"status": "ok", **meta}, indent=2))
        return 0
    except (CaptureError, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
