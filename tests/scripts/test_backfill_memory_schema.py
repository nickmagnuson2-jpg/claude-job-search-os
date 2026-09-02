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


# --- mutation-hardening additions ------------------------------------------

FLAT_WITH_NODE_TYPE = """---
name: feedback_flat_nodetype
node_type: memory
type: feedback
---

Body.
"""

FLAT_NO_TYPE = """---
name: A flat legacy file naming neither node_type nor type
description: nothing but a name
---

Body.
"""

# `  promoted: no` also appears EARLIER in the frontmatter, so the naive
# "delete the added lines" reversal removes the wrong occurrence and the
# conservation anchor must refuse the write.
DUP_LINE_FILE = """---
name: feedback_dup
other:
  promoted: no
metadata:
  type: feedback
---

Body.
"""


def run_no_date(root: Path, *extra: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--memory-dir", str(root), *extra],
        capture_output=True, text=True,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_stamp_raises_on_a_file_with_no_frontmatter():
    """stamp() is the last line of defence if main's pre-filter is ever bypassed."""
    with pytest.raises(ValueError) as exc:
        bms.stamp("no frontmatter here\n", DATE)
    assert "no frontmatter" in str(exc.value), "ValueError must name the cause"


def test_new_keys_land_after_the_last_existing_metadata_child(tmp_path):
    """Position, not just indentation: the keys go INSIDE and at the END of metadata."""
    write_corpus(tmp_path)
    run(tmp_path, "--apply")
    text = (tmp_path / "feedback_with_metadata.md").read_text(encoding="utf-8")
    i_header = text.index("metadata:")
    i_last_child = text.index("modified: 2026-08-05")
    i_occ = text.index("occurrences: 1")
    assert i_header < i_occ, "occurrences was written before the metadata: header"
    assert i_last_child < i_occ, "occurrences was written before an existing metadata child"
    assert text.index("promoted: no") > i_last_child, "promoted jumped ahead of existing children"
    assert text.index("needs_review: true") > i_last_child, "needs_review jumped ahead"


def test_metadata_block_is_rewritten_exactly(tmp_path):
    """Byte-exact expectation for the into-metadata path."""
    write_corpus(tmp_path)
    run(tmp_path, "--apply")
    text = (tmp_path / "feedback_with_metadata.md").read_text(encoding="utf-8")
    expected = (
        "metadata:\n"
        "  node_type: memory\n"
        "  type: feedback\n"
        "  originSessionId: abc-123\n"
        "  modified: 2026-08-05T04:20:23.769Z\n"
        "  occurrences: 1\n"
        "  promoted: no\n"
        f'  reopen_gate: "{bms.BACKFILL_GATE.format(date=DATE)}"\n'
        "  needs_review: true\n"
        "---\n"
    )
    assert expected in text, f"metadata block not as specified:\n{text}"


def test_date_flag_is_the_provenance_stamped_into_the_gate(tmp_path):
    write_corpus(tmp_path)
    run(tmp_path, "--apply")
    fm = parse_frontmatter((tmp_path / "feedback_with_metadata.md").read_text(encoding="utf-8"))
    assert DATE in fm["reopen_gate"], f"--date not honoured: {fm['reopen_gate']!r}"


def test_default_date_is_today_not_a_none_placeholder(tmp_path):
    from datetime import date as _date
    write_corpus(tmp_path)
    rc, _ = run_no_date(tmp_path, "--apply")
    assert rc == 0
    fm = parse_frontmatter((tmp_path / "feedback_with_metadata.md").read_text(encoding="utf-8"))
    gate = fm["reopen_gate"]
    assert _date.today().isoformat() in gate, f"default date is not today: {gate!r}"
    assert "None" not in gate, f"args.date=None leaked into the gate: {gate!r}"


def test_flat_file_already_naming_node_type_does_not_get_a_duplicate(tmp_path):
    (tmp_path / "feedback_flat_nodetype.md").write_text(FLAT_WITH_NODE_TYPE, encoding="utf-8")
    rc, report = run(tmp_path, "--apply")
    assert rc == 0 and report["stamped"] == 1
    text = (tmp_path / "feedback_flat_nodetype.md").read_text(encoding="utf-8")
    assert text.count("node_type:") == 1, f"node_type duplicated:\n{text}"
    assert text.count("type: feedback") == 1, f"type duplicated:\n{text}"


def test_flat_file_naming_neither_key_gets_both(tmp_path):
    (tmp_path / "feedback_flat_notype.md").write_text(FLAT_NO_TYPE, encoding="utf-8")
    rc, report = run(tmp_path, "--apply")
    assert rc == 0 and report["stamped"] == 1
    text = (tmp_path / "feedback_flat_notype.md").read_text(encoding="utf-8")
    assert "metadata:\n  node_type: memory\n  type: feedback\n" in text, (
        f"node_type/type not synthesised for a file naming neither:\n{text}"
    )


def test_conservation_failure_skips_the_file_and_reports_partial(tmp_path):
    """A file whose frontmatter already contains one of the added lines verbatim.

    The reversal removes the wrong occurrence, so the anchor must refuse the write.
    """
    (tmp_path / "feedback_dup.md").write_text(DUP_LINE_FILE, encoding="utf-8")
    rc, report = run(tmp_path, "--apply")
    assert rc == 1, f"a refused write must exit 1, got {rc}: {report}"
    assert report["status"] == "partial", f"status should be partial: {report}"
    assert report["stamped"] == 0, "a file failing conservation must not count as stamped"
    assert report["failed"] == ["feedback_dup.md: conservation check failed -- not written"], report
    assert (tmp_path / "feedback_dup.md").read_text(encoding="utf-8") == DUP_LINE_FILE, (
        "the file was written despite failing the conservation anchor"
    )


def test_mode_counts_are_per_mode_not_a_total(tmp_path):
    (tmp_path / "feedback_a.md").write_text(WITH_METADATA, encoding="utf-8")
    (tmp_path / "feedback_b.md").write_text(WITH_METADATA, encoding="utf-8")
    (tmp_path / "feedback_c.md").write_text(FLAT_FRONTMATTER, encoding="utf-8")
    rc, report = run(tmp_path, "--apply")
    assert rc == 0, report
    assert report["modes"] == {"into-metadata": 2, "new-metadata-block": 1}, report["modes"]


def test_missing_directory_reason_names_the_directory_problem(tmp_path):
    missing = tmp_path / "nope"
    rc, report = run(missing)
    assert rc == 2
    assert "not a directory" in report["reason"], (
        f"a missing dir must be reported as such, not as an empty sweep: {report}"
    )
    assert str(missing) in report["reason"], report


def test_failed_write_leaves_no_stranded_tmp_file(tmp_path, monkeypatch, capsys):
    """os.replace blowing up must not leave a .tmp turd in the memory dir."""
    (tmp_path / "feedback_boom.md").write_text(WITH_METADATA, encoding="utf-8")

    def boom(src, dst):
        raise RuntimeError("replace failed")

    monkeypatch.setattr(bms.os, "replace", boom)
    with pytest.raises(RuntimeError):
        bms.main(["--memory-dir", str(tmp_path), "--date", DATE, "--apply"])
    capsys.readouterr()
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".tmp")
    assert leftovers == [], f"stranded temp file(s) after a failed write: {leftovers}"
    assert (tmp_path / "feedback_boom.md").read_text(encoding="utf-8") == WITH_METADATA
