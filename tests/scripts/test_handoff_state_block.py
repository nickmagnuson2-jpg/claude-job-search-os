"""The handoff's state block must come from a scan, never from a keyboard.

Rev 6 typed "40 of 432" against a live 66 of 488, one screen below its own
instruction to re-measure. This is the check that makes that impossible: if the
block in the doc disagrees with a fresh scan, this test fails.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import handoff_state as hs  # noqa: E402


def test_scan_returns_every_field_the_block_prints():
    s = hs.scan()
    for k in hs.FIELDS:
        assert k in s, f"scan() is missing {k}, so the block would print a stale literal"
        assert isinstance(s[k], int)


def test_handoff_state_block_matches_a_fresh_scan():
    text = hs.HANDOFF.read_text(encoding="utf-8")
    assert hs.BEGIN in text and hs.END in text, (
        "handoff has no generated STATE-BLOCK -- run: "
        "PYTHONIOENCODING=utf-8 python3 tools/handoff_state.py --write")
    in_doc = text.split(hs.BEGIN, 1)[1].split(hs.END, 1)[0]
    fresh = hs.state_block().split(hs.BEGIN, 1)[1].split(hs.END, 1)[0]
    doc_vals = {l.split(":")[0].strip(): l.split(":", 1)[1].strip()
                for l in in_doc.strip().splitlines() if ":" in l}
    new_vals = {l.split(":")[0].strip(): l.split(":", 1)[1].strip()
                for l in fresh.strip().splitlines() if ":" in l}
    drift = {k: (doc_vals.get(k), new_vals[k]) for k in hs.FIELDS
             if doc_vals.get(k) != new_vals[k]}
    assert not drift, (
        f"handoff state block is stale (doc, live): {drift}. "
        "Re-run tools/handoff_state.py --write rather than editing the numbers.")


def test_a_hand_edited_number_is_detected(tmp_path, monkeypatch):
    """The guard must fail on tampering, not merely pass when things are fine."""
    fake = tmp_path / "h.md"
    block = hs.state_block()
    tampered = block.replace("promotion_backlog:", "promotion_backlog: 999 #", 1)
    fake.write_text(tampered + "\n", encoding="utf-8")
    monkeypatch.setattr(hs, "HANDOFF", fake)
    with pytest.raises(AssertionError):
        test_handoff_state_block_matches_a_fresh_scan()


def test_scan_raises_rather_than_defaulting_when_the_queue_is_absent(monkeypatch):
    """A missing queue must be loud. A silent 0 would read as 'backlog cleared'."""
    monkeypatch.setattr(hs, "scan", hs.scan)
    import json as _json
    import subprocess as _sp

    class R:
        returncode = 0
        stdout = _json.dumps({"date": "2026-01-01", "queues": []})
        stderr = ""
    monkeypatch.setattr(_sp, "run", lambda *a, **k: R())
    with pytest.raises(RuntimeError, match="no 'promotion' queue"):
        hs.scan()


# --- write() and the error paths ----------------------------------------------
# Added 2026-08-25: the first pass left 17 mutants alive, including every branch of
# write(), the function that edits the handoff. An untested writer that silently
# mangles the doc is worse than no generator at all.
import json as _json  # noqa: E402
import subprocess as _sp  # noqa: E402


def test_state_block_carries_both_markers_and_every_field():
    b = hs.state_block()
    assert b.startswith(hs.BEGIN) and b.rstrip().endswith(hs.END)
    for k in hs.FIELDS:
        assert f"{k}:" in b


def test_write_replaces_an_existing_block_and_is_idempotent(tmp_path, monkeypatch):
    f = tmp_path / "h.md"
    f.write_text(f"before\n{hs.BEGIN}\nstale: 1\n{hs.END}\nafter\n", encoding="utf-8")
    monkeypatch.setattr(hs, "HANDOFF", f)
    hs.write()
    once = f.read_text(encoding="utf-8")
    assert "stale: 1" not in once
    assert once.startswith("before\n") and once.endswith("after\n")
    assert once.count(hs.BEGIN) == 1
    hs.write()
    assert f.read_text(encoding="utf-8") == once, "write() is not idempotent"


def test_write_inserts_at_the_anchor_when_no_block_exists(tmp_path, monkeypatch):
    f = tmp_path / "h.md"
    f.write_text("intro\n\n## What changed at rev 7\nbody\n", encoding="utf-8")
    monkeypatch.setattr(hs, "HANDOFF", f)
    hs.write()
    out = f.read_text(encoding="utf-8")
    assert hs.BEGIN in out and hs.END in out
    assert out.index(hs.END) < out.index("## What changed at rev 7")
    assert "intro" in out and "body" in out


def test_write_raises_when_there_is_neither_a_block_nor_an_anchor(tmp_path, monkeypatch):
    f = tmp_path / "h.md"
    f.write_text("nothing useful here\n", encoding="utf-8")
    monkeypatch.setattr(hs, "HANDOFF", f)
    with pytest.raises(RuntimeError, match="no STATE-BLOCK and no anchor"):
        hs.write()
    assert f.read_text(encoding="utf-8") == "nothing useful here\n"


def test_write_actually_persists_to_disk(tmp_path, monkeypatch):
    f = tmp_path / "h.md"
    f.write_text("x\n\n## What changed at rev 7\n", encoding="utf-8")
    monkeypatch.setattr(hs, "HANDOFF", f)
    returned = hs.write()
    assert returned.split(hs.BEGIN, 1)[1] in f.read_text(encoding="utf-8"), (
        "write() returned a block it never wrote")


def _fake_run(stdout="", rc=0, stderr=""):
    class R:
        returncode = rc
    R.stdout, R.stderr = stdout, stderr
    return lambda *a, **k: R()


def test_scan_raises_when_attention_exits_nonzero(monkeypatch):
    monkeypatch.setattr(_sp, "run", _fake_run(rc=1, stderr="boom"))
    with pytest.raises(RuntimeError, match="attention.py failed"):
        hs.scan()


def test_scan_raises_when_the_detail_schema_loses_a_field(monkeypatch):
    """A renamed key must be loud, never a silent 0 that reads as 'cleared'."""
    payload = _json.dumps({"date": "2026-01-01", "queues": [
        {"name": "promotion", "count": 5, "denominator": 10, "detail": {"partial": 1}}]})
    monkeypatch.setattr(_sp, "run", _fake_run(stdout=payload))
    with pytest.raises(RuntimeError, match="schema changed"):
        hs.scan()
