"""Tests for tools/backfill_memory_schema.py -- the Phase 1 corpus backfill.

This script rewrites ~380 files in the auto-memory directory in one pass, so the
properties that matter are the ones that make a bad pass impossible rather than
merely unlikely: conservation (no original byte is lost), correct indentation
(a key written outside the `metadata:` block is invisible to the parser that
reads it), idempotence, and refusal of an empty scope.

Every test runs against a tmp_path corpus -- the real memory dir is outside the
repo and gitignored, so nothing here depends on Tier 2 state.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "backfill_memory_schema.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))

import backfill_memory_schema as bms  # noqa: E402
from scan_promotion_candidates import parse_frontmatter  # noqa: E402

DATE = "2026-08-13"

WITH_METADATA = """---
name: feedback_example_rule
description: "An example"
metadata:
  node_type: memory
  type: feedback
  originSessionId: abc-123
  modified: 2026-08-05T04:20:23.769Z
---

**Rule:** body text here.

**Why:** because.
"""

FLAT_FRONTMATTER = """---
name: How to do the thing
description: A flat-frontmatter legacy file with no metadata block
type: feedback
originSessionId: def-456
---

Body stays put.
"""

ALREADY_VISIBLE = """---
name: feedback_already
description: "Already opted in"
metadata:
  type: feedback
  occurrences: 3
  promoted: no
  reopen_gate: "3rd fire -> promote"
  last_cited: 2026-08-13
---

Body.
"""

NON_FEEDBACK = """---
name: reference_some_fact
description: "A fact, not a rule"
metadata:
  node_type: memory
  type: reference
---

Body.
"""


def write_corpus(root: Path) -> None:
    (root / "feedback_with_metadata.md").write_text(WITH_METADATA, encoding="utf-8")
    (root / "feedback_flat.md").write_text(FLAT_FRONTMATTER, encoding="utf-8")
    (root / "feedback_already.md").write_text(ALREADY_VISIBLE, encoding="utf-8")
    (root / "reference_fact.md").write_text(NON_FEEDBACK, encoding="utf-8")
    (root / "index-tools.md").write_text("# index\n", encoding="utf-8")
    (root / "MEMORY.md").write_text("# router\n", encoding="utf-8")


def run(root: Path, *extra: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--memory-dir", str(root), "--date", DATE, *extra],
        capture_output=True, text=True,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:  # pragma: no cover - surfaces the real failure
        pytest.fail(f"non-JSON stdout (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc.returncode, payload


def test_dry_run_writes_nothing(tmp_path):
    write_corpus(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.glob("*.md")}
    rc, report = run(tmp_path)
    assert rc == 0
    assert report["stamped"] == 2
    assert report["applied"] is False
    after = {p.name: p.read_bytes() for p in tmp_path.glob("*.md")}
    assert before == after, "dry run mutated the corpus"


def test_apply_stamps_only_invisible_feedback_files(tmp_path):
    write_corpus(tmp_path)
    rc, report = run(tmp_path, "--apply")
    assert rc == 0, report
    assert report["stamped"] == 2
    assert report["already_visible"] == 1
    assert report["skipped_non_feedback"] == 1
    # index-*/MEMORY.md are not rule files and must not even be scanned
    assert report["scanned"] == 4

    fm = parse_frontmatter((tmp_path / "feedback_with_metadata.md").read_text(encoding="utf-8"))
    assert fm["occurrences"] == "1"
    assert fm["promoted"] == "no"
    assert fm["needs_review"] == "true"
    assert "schema backfill" in fm["reopen_gate"]
    assert "last_cited" not in fm, "last_cited must never be fabricated by the backfill"

    # untouched files are byte-identical
    assert (tmp_path / "feedback_already.md").read_text(encoding="utf-8") == ALREADY_VISIBLE
    assert (tmp_path / "reference_fact.md").read_text(encoding="utf-8") == NON_FEEDBACK


def test_flat_frontmatter_gets_a_metadata_block_the_parser_can_read(tmp_path):
    write_corpus(tmp_path)
    run(tmp_path, "--apply")
    text = (tmp_path / "feedback_flat.md").read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    assert fm["occurrences"] == "1" and fm["promoted"] == "no"
    assert "metadata:\n  node_type: memory\n" in text
    # its existing top-level `type: feedback` must not be duplicated inside metadata
    assert text.count("type: feedback") == 1
    assert text.endswith("Body stays put.\n")


def test_keys_land_inside_the_metadata_block_not_after_it(tmp_path):
    """A key at column 0 parses as a top-level key; the detector reads metadata children.

    This is the failure the last_cited hook nearly shipped -- wrong indent writes the
    key where the parser cannot see it, and the file looks stamped but stays invisible.
    """
    write_corpus(tmp_path)
    run(tmp_path, "--apply")
    text = (tmp_path / "feedback_with_metadata.md").read_text(encoding="utf-8")
    for key in ("occurrences", "promoted", "reopen_gate", "needs_review"):
        assert f"\n  {key}:" in text, f"{key} is not indented into metadata"
        assert f"\n{key}:" not in text, f"{key} escaped the metadata block"


def test_conservation_every_original_byte_survives(tmp_path):
    write_corpus(tmp_path)
    originals = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("feedback_*.md")}
    run(tmp_path, "--apply")
    for name, original in originals.items():
        new = (tmp_path / name).read_text(encoding="utf-8")
        assert len(new) >= len(original)
        for line in original.splitlines():
            assert line in new, f"{name}: original line lost: {line!r}"


def test_idempotent_second_run_is_a_no_op(tmp_path):
    write_corpus(tmp_path)
    run(tmp_path, "--apply")
    snapshot = {p.name: p.read_bytes() for p in tmp_path.glob("*.md")}
    rc, report = run(tmp_path, "--apply")
    assert rc == 0
    assert report["stamped"] == 0
    assert {p.name: p.read_bytes() for p in tmp_path.glob("*.md")} == snapshot


def test_empty_scope_is_an_error_not_a_clean_pass(tmp_path):
    """The 2026-08-13 false-clean reproduced: a scan over nothing must abort."""
    (tmp_path / "index-only.md").write_text("# not a rule file\n", encoding="utf-8")
    rc, report = run(tmp_path)
    assert rc == 2
    assert report["status"] == "error"
    assert "ZERO rule files" in report["reason"]


def test_missing_directory_exits_nonzero(tmp_path):
    rc, report = run(tmp_path / "nope")
    assert rc == 2 and report["status"] == "error"


def test_limit_caps_the_batch(tmp_path):
    write_corpus(tmp_path)
    rc, report = run(tmp_path, "--apply", "--limit", "1")
    assert rc == 0 and report["stamped"] == 1


def test_conservation_check_rejects_a_lossy_transform():
    """Mutation guard: if stamp() ever dropped content, conservation_ok must catch it."""
    added = ["  occurrences: 1\n"]
    good = "---\nname: x\nmetadata:\n" + added[0] + "---\nbody\n"
    original = "---\nname: x\nmetadata:\n---\nbody\n"
    assert bms.conservation_ok(original, good, added)
    lossy = good.replace("body\n", "")
    assert not bms.conservation_ok(original, lossy, added)
    extra = good.replace("body\n", "body\nsmuggled\n")
    assert not bms.conservation_ok(original, extra, added)


def test_file_without_frontmatter_is_reported_not_written(tmp_path):
    (tmp_path / "feedback_broken.md").write_text("no frontmatter here\n", encoding="utf-8")
    rc, report = run(tmp_path, "--apply")
    assert rc == 0
    assert report["skipped_no_frontmatter"] == ["feedback_broken.md"]
    assert (tmp_path / "feedback_broken.md").read_text(encoding="utf-8") == "no frontmatter here\n"
