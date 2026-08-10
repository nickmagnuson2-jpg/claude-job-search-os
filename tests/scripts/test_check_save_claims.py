#!/usr/bin/env python3
"""Tests for tools/check_save_claims.py — the Stop hook that catches
"I saved it to X" when X was never written.

Covers the clean/block pair required by tools/HOOK_AUTHORING.md, plus the
false-positive classes that would make the hook unusable if it fired on them.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "tools" / "check_save_claims.py"

spec = importlib.util.spec_from_file_location("csc", HOOK)
csc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csc)


def extract(text):
    return csc.extract_claimed_paths(text)


# ---------- extraction: should CATCH ----------

def test_catches_saved_to():
    assert "output/acme/foo.md" in extract("Saved to `output/acme/foo.md` — done.")


def test_catches_written_to():
    assert "tools/bar.py" in extract("The script is written to tools/bar.py now.")


def test_catches_wrote():
    assert "data/x.md" in extract("I wrote data/x.md with the full transcript.")


def test_catches_created():
    assert "a/b/c.html" in extract("Created a/b/c.html and rendered it.")


def test_catches_rendered():
    assert "deck.pdf" in extract("Rendered deck.pdf at 15 pages.")


def test_catches_multiple_distinct_claims():
    got = extract("Wrote tools/a.py. Also saved to tools/b.py for later.")
    assert "tools/a.py" in got and "tools/b.py" in got


# ---------- extraction: should NOT catch (false-positive guards) ----------

def test_ignores_future_tense():
    assert extract("I'll save this to output/acme/new.md next.") == []


def test_ignores_would():
    assert extract("That would be written to tools/nope.py.") == []


def test_ignores_you_can():
    assert extract("You can save it to output/whatever.md yourself.") == []


def test_ignores_negation():
    assert extract("I have not written tools/missing.py yet.") == []


def test_ignores_fenced_code_blocks():
    text = "Here is the script:\n```python\nopen('tools/inside_block.py')\n# wrote tools/x.py\n```\n"
    assert extract(text) == []


def test_ignores_urls():
    assert extract("Saved to https://example.com/thing.html for reference.") == []


def test_ignores_version_numbers():
    assert extract("Rendered with Python 3.11 support.") == []


# ---------- end-to-end hook behaviour ----------

def _run(transcript_text, tmpdir, stop_active=False):
    """Build a one-message transcript and run the hook against it."""
    tpath = Path(tmpdir) / "t.jsonl"
    rec = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": transcript_text}]},
    }
    tpath.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    payload = {"transcript_path": str(tpath), "stop_hook_active": stop_active}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PYTHONIOENCODING": "utf-8", "CLAUDE_PROJECT_DIR": tmpdir, "PATH": "/usr/bin:/bin"},
    )


def test_blocks_when_claimed_file_missing():
    with tempfile.TemporaryDirectory() as td:
        r = _run("Saved to output/ghost.md — all set.", td)
        assert r.returncode == 2
        assert "output/ghost.md" in r.stderr
        assert "BLOCKED check_save_claims" in r.stderr


def test_clean_when_claimed_file_exists():
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "real.md").write_text("hi", encoding="utf-8")
        r = _run("Saved to real.md — all set.", td)
        assert r.returncode == 0, r.stderr


def test_recursion_guard():
    with tempfile.TemporaryDirectory() as td:
        r = _run("Saved to output/ghost.md.", td, stop_active=True)
        assert r.returncode == 0


def test_missing_transcript_is_noop():
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"transcript_path": "/nonexistent/x.jsonl"}),
        capture_output=True, text=True,
        env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"},
    )
    assert r.returncode == 0


def test_malformed_stdin_is_noop():
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json", capture_output=True, text=True,
        env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"},
    )
    assert r.returncode == 0


def test_override_env_bypasses():
    with tempfile.TemporaryDirectory() as td:
        tpath = Path(td) / "t.jsonl"
        rec = {"type": "assistant", "message": {"role": "assistant",
               "content": [{"type": "text", "text": "Saved to output/ghost.md."}]}}
        tpath.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"transcript_path": str(tpath)}),
            capture_output=True, text=True,
            env={"PYTHONIOENCODING": "utf-8", "CLAUDE_PROJECT_DIR": td,
                 "SAVE_CLAIMS_OVERRIDE": "1", "PATH": "/usr/bin:/bin"},
        )
        assert r.returncode == 0


def test_regression_the_actual_incident():
    """The exact sentence from 2026-08-04 that started this."""
    with tempfile.TemporaryDirectory() as td:
        r = _run(
            "Saved to `output/acme/080426-casey-whiteboard-sim-prompt.md`. "
            "Paste this into the Claude App and switch to voice:", td)
        assert r.returncode == 2
        assert "080426-casey-whiteboard-sim-prompt.md" in r.stderr


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", __file__]))


# ---------- bare-basename resolution (FP class found in live smoke 2026-08-04) ----------

def test_bare_basename_found_in_subdirectory_is_clean():
    """We refer to files by name without their directory constantly. A bare
    basename that exists one level down must NOT block."""
    with tempfile.TemporaryDirectory() as td:
        sub = Path(td) / "output" / "acme"
        sub.mkdir(parents=True)
        (sub / "some-doc.md").write_text("x", encoding="utf-8")
        r = _run("Saved to some-doc.md — updated, no longer stale.", td)
        assert r.returncode == 0, r.stderr


def test_bare_basename_genuinely_absent_still_blocks():
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "output").mkdir()
        r = _run("Saved to phantom-doc.md just now.", td)
        assert r.returncode == 2
        assert "phantom-doc.md" in r.stderr


def test_wrong_directory_still_blocks():
    """A path WITH a directory component is checked exactly — a wrong
    directory is itself a defect worth catching."""
    with tempfile.TemporaryDirectory() as td:
        real = Path(td) / "output" / "acme"
        real.mkdir(parents=True)
        (real / "doc.md").write_text("x", encoding="utf-8")
        r = _run("Saved to coaching/doc.md for later.", td)
        assert r.returncode == 2
