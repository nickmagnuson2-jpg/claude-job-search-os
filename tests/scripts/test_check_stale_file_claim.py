#!/usr/bin/env python3
"""Tests for tools/check_stale_file_claim.py — the Stop hook that catches
"X hasn't been touched this session" when mtime says otherwise.

Covers the clean/block pair required by tools/HOOK_AUTHORING.md, plus the
false-positive classes measured in the 2026-08-13 smoke run over 4,085 real
assistant messages. Those classes are why the hook checks mtime rather than
asking whether a check was run — see the module docstring.

Canonical case: the 2026-07-31 late-round prep incident, where
`073126-prep-evidence.md` was called "untouched today" on the day Nick
created it.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "tools" / "check_stale_file_claim.py"

spec = importlib.util.spec_from_file_location("csfc", HOOK)
csfc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csfc)


def claims(text):
    return [t for t, _ in csfc.extract_stale_claims(text)]


def scoped(text):
    return csfc.extract_stale_claims(text)


# ---------- extraction: should CATCH ----------

def test_catches_the_origin_incident():
    text = "output/acme/073126-prep-evidence.md hasn't had a pass this session."
    assert claims(text) == ["output/acme/073126-prep-evidence.md"]


def test_catches_untouched_today():
    assert claims("data/goals.md is untouched today.") == ["data/goals.md"]


def test_catches_has_not_been_updated_yet():
    assert claims("tools/foo.py has not been updated yet.") == ["tools/foo.py"]


def test_catches_still_unchanged():
    assert claims("a/b.md is still unchanged.") == ["a/b.md"]


def test_catches_bare_basename():
    assert claims("notes.md hasn't been touched this session.") == ["notes.md"]


def test_catches_trailing_subject_negated_shape():
    assert claims("So far nothing has been modified in data/after.md.") == ["data/after.md"]


def test_inline_backticks_still_yield_the_path():
    assert claims("`data/a.md` hasn't been touched this session.") == ["data/a.md"]


# ---------- extraction: should NOT fire ----------

def test_requires_a_recency_marker():
    """A content-history claim is groundable by reading; out of scope."""
    assert claims("data/a.md hasn't been updated since June.") == []


def test_ignores_conditional():
    assert claims("If data/a.md hasn't been touched today, regenerate it.") == []


def test_ignores_whether_question():
    assert claims("Let me check whether data/a.md has not been updated yet.") == []


def test_ignores_fenced_code_block():
    assert claims("```\ndata/a.md hasn't been touched today\n```\nAll good.") == []


def test_ignores_blockquote():
    assert claims("> data/a.md hasn't been touched this session\n\nOld claim.") == []


def test_ignores_claim_with_no_path():
    assert claims("The memory corpus hasn't been touched today.") == []


def test_ignores_urls():
    assert claims("https://example.com/report.md hasn't been updated today.") == []


# --- the wrong-referent class: top false positive in the smoke run ---

def test_ignores_path_that_is_not_the_subject():
    text = ("Updated index-tools.md (x2) and index-system.md (x1). "
            "Repo untouched today and still in sync.")
    assert claims(text) == []


def test_ignores_path_separated_by_sentence_boundary():
    assert claims("I read tools/other.py. Everything else is untouched today.") == []


def test_ignores_path_separated_by_a_clause():
    text = "data/a.md, which I opened earlier and skimmed, so nothing is untouched today."
    assert claims(text) == []


def test_allows_short_filler_between_path_and_claim():
    assert claims("data/a.md is still untouched today.") == ["data/a.md"]


# ---------- scope: which threshold the claim is measured against ----------

def test_today_claim_gets_the_midnight_scope():
    assert scoped("a/b.md is untouched today.") == [("a/b.md", "today")]


def test_session_claim_gets_the_session_scope():
    assert scoped("a/b.md hasn't been touched this session.") == [("a/b.md", "session")]


def test_today_scope_wins_when_a_path_is_claimed_twice():
    text = "a/b.md hasn't been touched this session. a/b.md is untouched today."
    assert scoped(text) == [("a/b.md", "today")]


def test_today_claim_catches_a_file_edited_before_the_session_opened():
    """The origin incident: Nick created the file earlier that day, before the
    session opened. A session-start threshold would have missed it."""
    with tempfile.TemporaryDirectory() as tmp:
        _tmpfile(tmp, "w.md", age_seconds=3600)
        midnight = time.time() - 86400
        session = time.time() - 60
        out = csfc.evaluate("w.md is untouched today.", Path(tmp), midnight, session)
        assert [t for t, _ in out] == ["w.md"]


def test_session_claim_ignores_an_edit_that_predates_the_session():
    with tempfile.TemporaryDirectory() as tmp:
        _tmpfile(tmp, "w.md", age_seconds=3600)
        midnight = time.time() - 86400
        session = time.time() - 60
        out = csfc.evaluate("w.md hasn't been touched this session.",
                            Path(tmp), midnight, session)
        assert out == []


# ---------- evaluate: mtime is the arbiter ----------

def _tmpfile(tmp, name="a.md", age_seconds=0):
    p = Path(tmp) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(p, (old, old))
    return p


def test_blocks_when_file_changed_after_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        _tmpfile(tmp)
        out = csfc.evaluate("a.md is untouched today.", Path(tmp), time.time() - 3600, time.time() - 3600)
        assert [t for t, _ in out] == ["a.md"]


def test_passes_when_file_is_older_than_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        _tmpfile(tmp, age_seconds=86400 * 3)
        out = csfc.evaluate("a.md is untouched today.", Path(tmp), time.time() - 3600, time.time() - 3600)
        assert out == []


def test_self_report_survives_when_it_is_actually_true():
    """The dominant smoke-run false positive. Claude saying it left a file
    alone must pass, and does, because the file genuinely did not change."""
    with tempfile.TemporaryDirectory() as tmp:
        _tmpfile(tmp, "goals.md", age_seconds=86400 * 2)
        out = csfc.evaluate("goals.md untouched today, nothing routed in.",
                            Path(tmp), time.time() - 3600, time.time() - 3600)
        assert out == []


def test_self_report_blocks_when_it_is_wrong():
    with tempfile.TemporaryDirectory() as tmp:
        _tmpfile(tmp, "goals.md")
        out = csfc.evaluate("goals.md untouched today, nothing routed in.",
                            Path(tmp), time.time() - 3600, time.time() - 3600)
        assert [t for t, _ in out] == ["goals.md"]


def test_nonexistent_file_is_not_our_problem():
    with tempfile.TemporaryDirectory() as tmp:
        out = csfc.evaluate("ghost.md is untouched today.", Path(tmp), 0, 0)
        assert out == []


def test_session_start_never_precedes_midnight():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "t.jsonl"
        t.write_text("", encoding="utf-8")
        midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        assert csfc.session_start(str(t)) >= midnight


def test_session_start_falls_back_to_midnight():
    midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    assert csfc.session_start("/nope/none.jsonl") == midnight


# ---------- transcript plumbing ----------

def _transcript(tmp, assistant_text):
    p = Path(tmp) / "t.jsonl"
    rec = {"type": "assistant", "message": {"role": "assistant",
           "content": [{"type": "text", "text": assistant_text}]}}
    p.write_text(json.dumps(rec), encoding="utf-8")
    return p


def _run(root, transcript, env_extra=None):
    env = {"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8",
           "CLAUDE_PROJECT_DIR": str(root)}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"transcript_path": str(transcript)}),
        capture_output=True, text=True, env=env,
    )


def test_cli_blocks_a_contradicted_claim():
    with tempfile.TemporaryDirectory() as tmp:
        _tmpfile(tmp)
        t = _transcript(tmp, "a.md is untouched today.")
        r = _run(tmp, t)
        assert r.returncode == 2
        assert "BLOCKED check_stale_file_claim" in r.stderr
        assert "a.md" in r.stderr


def test_cli_passes_a_true_claim():
    with tempfile.TemporaryDirectory() as tmp:
        _tmpfile(tmp, age_seconds=86400 * 3)
        t = _transcript(tmp, "a.md is untouched today.")
        assert _run(tmp, t).returncode == 0


def test_cli_clean_message_passes():
    with tempfile.TemporaryDirectory() as tmp:
        t = _transcript(tmp, "Done. The pipeline is updated and the tests pass.")
        assert _run(tmp, t).returncode == 0


def test_cli_override_short_circuits():
    with tempfile.TemporaryDirectory() as tmp:
        _tmpfile(tmp)
        t = _transcript(tmp, "a.md is untouched today.")
        assert _run(tmp, t, {"STALE_CLAIM_OVERRIDE": "1"}).returncode == 0


def test_cli_stop_hook_active_short_circuits():
    with tempfile.TemporaryDirectory() as tmp:
        _tmpfile(tmp)
        t = _transcript(tmp, "a.md is untouched today.")
        r = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"transcript_path": str(t), "stop_hook_active": True}),
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": tmp},
        )
        assert r.returncode == 0


def test_cli_fails_open_on_bad_json():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(ROOT)})
    assert r.returncode == 0


def test_cli_fails_open_on_missing_transcript():
    r = subprocess.run([sys.executable, str(HOOK)],
                       input=json.dumps({"transcript_path": "/nope/none.jsonl"}),
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(ROOT)})
    assert r.returncode == 0
