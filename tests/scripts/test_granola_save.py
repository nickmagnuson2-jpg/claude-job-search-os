"""Tests for tools/granola_save.py."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "granola_save.py"


# --- capture-time attribution warning (2026-08-25) --------------------------
# Granola emits Me/Them only when system audio and the mic are on separate channels. On a
# speakerphone call every voice lands on the mic and the whole conversation is attributed to
# the owner. A 2026-06-24 call sat in the corpus for two months that way and was ranked 4th
# on a filler table before anyone noticed. Warn at capture, where the setup is still fixable.

def _write(tmp_path, transcript):
    import subprocess, sys, json as _json
    payload = {"meeting_id": "x", "title": "t", "captured": "2026-08-25 10:00",
               "transcript": transcript, "summary": "s", "type": "recruiter",
               "session_desc": "d"}
    inp = tmp_path / "in.json"
    inp.write_text(_json.dumps(payload), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "write", "--output", str(tmp_path / "o.md"),
         "--input", str(inp)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout)


def test_collapsed_channel_warns_at_capture(tmp_path):
    out = _write(tmp_path, "Me: " + "word " * 200)
    assert "attribution_warning" in out
    assert "NOT SEPARATED" in out["attribution_warning"]


def test_healthy_two_speaker_capture_does_not_warn(tmp_path):
    out = _write(tmp_path, "Me: " + "word " * 200 + " Them: " + "reply " * 150)
    assert "attribution_warning" not in out


def test_unlabelled_transcript_warns_about_zero_turns(tmp_path):
    out = _write(tmp_path, "no speaker labels at all " * 50)
    assert "attribution_warning" in out
    assert "zero turns" in out["attribution_warning"]


def test_warning_never_blocks_the_write(tmp_path):
    """The warning is advisory. A collapsed transcript is still valuable CONTENT and must
    still be persisted -- refusing to save it would lose the call to protect a metric."""
    out = _write(tmp_path, "Me: " + "word " * 200)
    assert out["status"] == "ok"
    assert (tmp_path / "o.md").exists()


def test_transcript_with_labels_but_no_words_does_not_crash(tmp_path):
    """Pins the `if not total` guard in attribution_warning.

    Labels present, zero words behind them: a real shape when a call connects and nothing is
    said. Without the guard this divides by zero and takes down the whole save, losing the
    transcript to protect a metric -- the exact inversion of what the warning is for."""
    out = _write(tmp_path, "Me:  Them:  Me:  Them: ")
    assert out["status"] == "ok"
    assert (tmp_path / "o.md").exists()

