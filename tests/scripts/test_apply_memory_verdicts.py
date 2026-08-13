"""Tests for tools/apply_memory_verdicts.py.

This script rewrites frontmatter values across the memory corpus from a verdict file,
so the properties that matter are: it changes ONLY schema values (never prose), it
refuses an empty change-set, it never silently counts a no-op as applied, and it
rejects keys outside the schema block.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "apply_memory_verdicts.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))

import apply_memory_verdicts as amv  # noqa: E402

FILE = """---
name: feedback_example
description: "example"
metadata:
  node_type: memory
  type: feedback
  occurrences: 1
  promoted: no
  reopen_gate: "UNREVIEWED -- schema backfill 2026-08-13"
  needs_review: true
---

**Rule:** body prose that must never be touched.

occurrences: 1 appears in the body too, as a decoy.
"""


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "feedback_example.md").write_text(FILE, encoding="utf-8")
    return tmp_path


def run(memdir: Path, verdicts: Path, *extra):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--memory-dir", str(memdir),
         "--verdicts", str(verdicts), *extra],
        capture_output=True, text=True,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_dry_run_changes_nothing(corpus, tmp_path):
    v = tmp_path / "v.txt"
    v.write_text("feedback_example.md\toccurrences=3\n", encoding="utf-8")
    before = (corpus / "feedback_example.md").read_bytes()
    rc, rep = run(corpus, v)
    assert rc == 0 and rep["changed"] == 1 and rep["applied_mode"] is False
    assert (corpus / "feedback_example.md").read_bytes() == before


def test_apply_sets_only_the_frontmatter_value(corpus, tmp_path):
    v = tmp_path / "v.txt"
    v.write_text('feedback_example.md\toccurrences=3\tpromoted="yes -- hook, 2026-05-14"\n', encoding="utf-8")
    rc, rep = run(corpus, v, "--apply")
    assert rc == 0, rep
    text = (corpus / "feedback_example.md").read_text(encoding="utf-8")
    assert "  occurrences: 3\n" in text
    assert '  promoted: "yes -- hook, 2026-05-14"\n' in text
    # the decoy line in the BODY must be untouched
    assert "occurrences: 1 appears in the body too, as a decoy." in text
    assert "**Rule:** body prose that must never be touched." in text


def test_empty_verdict_file_is_an_error(corpus, tmp_path):
    v = tmp_path / "v.txt"
    v.write_text("# only a comment\n\n", encoding="utf-8")
    rc, rep = run(corpus, v, "--apply")
    assert rc == 2 and "ZERO verdict records" in rep["reason"]


def test_no_op_is_reported_unchanged_not_applied(corpus, tmp_path):
    v = tmp_path / "v.txt"
    v.write_text("feedback_example.md\toccurrences=1\n", encoding="utf-8")
    rc, rep = run(corpus, v, "--apply")
    assert rc == 0 and rep["changed"] == 0 and rep["unchanged"] == 1


def test_keys_outside_the_schema_are_rejected(corpus, tmp_path):
    v = tmp_path / "v.txt"
    v.write_text("feedback_example.md\tdescription=hijacked\n", encoding="utf-8")
    rc, rep = run(corpus, v, "--apply")
    assert rc == 2, rep  # no valid pairs remain -> empty change-set
    assert (corpus / "feedback_example.md").read_text(encoding="utf-8") == FILE


def test_missing_file_is_reported_not_silently_skipped(corpus, tmp_path):
    v = tmp_path / "v.txt"
    v.write_text("feedback_nope.md\toccurrences=2\n", encoding="utf-8")
    rc, rep = run(corpus, v, "--apply")
    assert rc == 1 and any("no such file" in f for f in rep["failed"])


def test_absent_key_is_reported(corpus, tmp_path):
    (corpus / "feedback_bare.md").write_text("---\nname: x\n---\nbody\n", encoding="utf-8")
    v = tmp_path / "v.txt"
    v.write_text("feedback_bare.md\toccurrences=2\n", encoding="utf-8")
    rc, rep = run(corpus, v, "--apply")
    assert rc == 1 and any("key-absent" in f for f in rep["failed"])


def test_conservation_rejects_a_line_count_change():
    original = "---\nname: x\n  occurrences: 1\n---\nbody\n"
    assert amv.conservation_ok(original, original.replace("1", "3"))
    assert not amv.conservation_ok(original, original + "extra\n")
    assert not amv.conservation_ok(original, original.replace("body", "tampered"))


# --- merge_mining_verdicts.py: the generator that feeds this applier -------------------

import merge_mining_verdicts as mmv  # noqa: E402


def _records(tmp_path, *lines):
    d = tmp_path / "recs"
    d.mkdir(exist_ok=True)
    (d / "work01.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def test_merge_refuses_an_empty_records_dir(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit):
        mmv.read_records(tmp_path / "empty")


def test_merge_rejects_an_unknown_disposition(tmp_path):
    d = _records(tmp_path, "feedback_x.md\t1\tinvented-disposition\t-")
    with pytest.raises(SystemExit):
        mmv.read_records(d)


def test_half_landed_becomes_partial_not_yes(tmp_path):
    """Marking a half-enforced rule `yes` would hide a real gap — the ghost in reverse."""
    recs = mmv.read_records(_records(
        tmp_path, "feedback_x.md\t2\talready-landed;genuinely-open\ttools/foo.py"))
    lines, _ = mmv.build_verdicts(recs, None)
    assert any('promoted="partial -- tools/foo.py"' in l for l in lines)
    assert not any('promoted="yes' in l for l in lines)


def test_fully_landed_becomes_yes(tmp_path):
    recs = mmv.read_records(_records(tmp_path, "feedback_x.md\t2\talready-landed\ttools/foo.py"))
    lines, _ = mmv.build_verdicts(recs, None)
    assert any('promoted="yes -- tools/foo.py"' in l for l in lines)


def test_zero_fire_records_keep_needs_review_flagged(tmp_path):
    """occurrences 0 means the origin was a success — the fix is a retype, not a count edit."""
    recs = mmv.read_records(_records(tmp_path, "feedback_x.md\t0\tcount-correction\t-"))
    lines, stats = mmv.build_verdicts(recs, None)
    assert stats["zero_fire_flagged"] == 1
    assert not any("needs_review" in l for l in lines)
    assert any("occurrences=1" in l for l in lines), "0 is written as the schema floor of 1"


def test_reopen_gate_is_never_generated(tmp_path):
    recs = mmv.read_records(_records(
        tmp_path, "feedback_x.md\t3\talready-landed;count-correction\ttools/foo.py"))
    lines, _ = mmv.build_verdicts(recs, None)
    assert not any("reopen_gate" in l for l in lines), "a script cannot compose a promotion gate"
