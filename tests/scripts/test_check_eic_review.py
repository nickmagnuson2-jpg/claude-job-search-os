"""Tests for the tools/check_eic_review.py gate (the review record itself is tested in
test_eic_review.py).

WHAT THIS PROTECTS. A post sent to the EIC for review must not be recorded as published until
her reply has been fetched and her edits logged. That needs: a sent email to an EIC address
opens a review (and nothing else does); a review closes only with a real Gmail id plus an
EIC-tagged lessons row or a stated no-edits reason; and the gate blocks the published record,
by every write path it claims, exactly while a review is open.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import check_eic_review as gate  # noqa: E402
import draft_capture as dc  # noqa: E402
import eic_review as er  # noqa: E402
from eic_reply_stub import her_reply  # noqa: E402

EIC = "editor@example.com"
TAG = "EDITOR NOTE"
REPLY = "0123456789abcdef"
PUBLISHED = "output/linkedin-profile/voice-sources/103026-linkedin-post-published.md"
TODAY = time.strftime("%Y-%m-%d")


def draft(to: str) -> str:
    return f"TO: {to}\nSUBJECT: LinkedIn post for review\nBODY:\nHi,\n\nCan you edit this post?\n\nNick\n"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Throwaway config, review store, capture store and lessons log for every test."""
    cfg = tmp_path / "eic.txt"
    cfg.write_text(f"# the EIC\n{EIC}\ntag: {TAG}\n", encoding="utf-8")
    monkeypatch.setenv("EIC_REVIEWERS_PATH", str(cfg))
    monkeypatch.setenv("EIC_REVIEW_DIR", str(tmp_path / "reviews"))
    monkeypatch.setenv("DRAFT_CAPTURE_DIR", str(tmp_path / "captures"))
    lessons = tmp_path / "lessons.md"
    lessons.write_text(
        "| Pattern | Rule | Occ | Promoted | Date |\n|---|---|---|---|---|\n"
        f"| **{TAG}.** She reordered the hook. | Lead with the shared thing. | 1 | No | {TODAY} |\n"
        f"| **NICK EDIT.** He linked a word. | Link a word, not a URL. | 1 | No | {TODAY} |\n",
        encoding="utf-8")
    monkeypatch.setenv("LESSONS_PATH", str(lessons))
    # close() looks her reply and its thread up in Gmail (tested in
    # test_eic_review_hardening.py); here it is always her reply in the thread of the post
    # just sent. One second after, not an hour: the lessons row must be dated on or after her
    # reply's day, and an hour crosses midnight after 23:00 (Fable r5 F1).
    monkeypatch.setattr(er, "fetch_reply", lambda rid: her_reply())
    return tmp_path


def sent_to(to: str, outcome: str = "unchanged") -> dict:
    cid = dc.capture(draft(to), skill="draft-email")
    return dc.confirm(cid, outcome, sent_text=draft(to) if outcome == "edited" else None,
                      no_lesson="test" if outcome == "edited" else None)


# --- the gate --------------------------------------------------------------------

@pytest.mark.parametrize("tool,tool_input", [
    ("Write", {"file_path": PUBLISHED, "content": "x"}),
    ("Edit", {"file_path": "/abs/repo/" + PUBLISHED, "old_string": "a", "new_string": "b"}),
    ("Bash", {"command": f"cat > {PUBLISHED} <<'EOF'\npost\nEOF"}),
    ("Bash", {"command": f"echo post | tee {PUBLISHED}"}),
    ("Bash", {"command": f"cp /tmp/post.md {PUBLISHED}"}),
])
def test_the_gate_blocks_every_write_path_while_a_review_is_open(tool, tool_input):
    cid = sent_to(EIC)["id"]
    msg = gate.decide(tool, tool_input)
    assert msg is not None and "BLOCKED check_eic_review" in msg and cid in msg


def test_the_gate_allows_the_published_record_once_every_review_is_closed():
    cid = sent_to(EIC)["id"]
    er.close(cid, REPLY, lesson_ref="She reordered the hook")
    assert gate.decide("Write", {"file_path": PUBLISHED, "content": "x"}) is None


@pytest.mark.parametrize("tool,tool_input", [
    ("Write", {"file_path": "output/linkedin-profile/voice-sources/103026-linkedin-post-draft.md"}),
    ("Write", {"file_path": "output/linkedin-profile/linkedin-post-analytics-log.md"}),
    ("Write", {"file_path": "output/acme-ai/101626-published-notes.md"}),
    ("Bash", {"command": f"cat {PUBLISHED}"}),
    ("Bash", {"command": f"grep -n post {PUBLISHED} > /tmp/out.txt"}),
])
def test_the_gate_ignores_everything_that_is_not_writing_the_published_record(tool, tool_input):
    sent_to(EIC)
    assert gate.decide(tool, tool_input) is None


def test_the_gate_is_silent_when_no_review_exists():
    assert gate.decide("Write", {"file_path": PUBLISHED, "content": "x"}) is None


def test_the_hook_process_exits_2_on_a_block_and_0_otherwise(env):
    sent_to(EIC)
    payload = json.dumps({"tool_name": "Write",
                          "tool_input": {"file_path": PUBLISHED, "content": "x"}})
    run = lambda p: subprocess.run([sys.executable, str(TOOLS / "check_eic_review.py")],
                                   input=p, capture_output=True, text=True, env=os.environ.copy())
    blocked = run(payload)
    assert blocked.returncode == 2 and "BLOCKED check_eic_review" in blocked.stderr
    other = json.dumps({"tool_name": "Write",
                        "tool_input": {"file_path": "output/notes.md", "content": "x"}})
    allowed = run(other)
    assert allowed.returncode == 0 and allowed.stderr == ""
    assert run("not json").returncode == 0
