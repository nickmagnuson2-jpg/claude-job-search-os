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
    """`description` became allowed on 2026-08-25 (D3), so this now uses `name`.

    `name` is the sharpest available probe: it is a real top-level frontmatter key,
    so a permissive implementation would happily rewrite it, and rewriting it would
    break every [[wikilink]] pointing at the rule.
    """
    v = tmp_path / "v.txt"
    v.write_text("feedback_example.md\tname=hijacked\n", encoding="utf-8")
    rc, rep = run(corpus, v, "--apply")
    assert rc == 2, rep  # no valid pairs remain -> empty change-set
    assert (corpus / "feedback_example.md").read_text(encoding="utf-8") == FILE


def test_body_prose_that_looks_like_a_description_is_never_touched(corpus, tmp_path):
    """The decoy guard, for the new key: a `description:` line in the BODY must survive."""
    f = corpus / "feedback_example.md"
    f.write_text(FILE + "\ndescription: this line is prose, not frontmatter.\n",
                 encoding="utf-8")
    v = tmp_path / "v.txt"
    v.write_text("feedback_example.md\tdescription=real one\n", encoding="utf-8")
    rc, rep = run(corpus, v, "--apply")
    assert rc == 0 and rep["changed"] == 1, rep
    after = f.read_text(encoding="utf-8")
    assert "description: this line is prose, not frontmatter." in after
    assert _strict_fm(after)["description"] == "real one"


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


# --- terminal-key insertion (added 2026-08-13, Task 9 corpus resolution) ---------
# The 41 self-declared terminal-behavioral files carry no `terminal:` line at all,
# and set_key can only overwrite an EXISTING key. Insertion is opt-in per key so a
# typo'd key name still fails loudly instead of silently appending a new line.

def test_terminal_is_inserted_after_promoted_when_absent(corpus, tmp_path):
    v = tmp_path / "v.txt"
    v.write_text('feedback_example.md\tterminal=true\n', encoding="utf-8")
    rc, rep = run(corpus, v, "--apply")
    assert rc == 0 and rep["changed"] == 1
    lines = (corpus / "feedback_example.md").read_text(encoding="utf-8").splitlines()
    assert "  terminal: true" in lines
    assert lines.index("  terminal: true") == lines.index("  promoted: no") + 1


def test_terminal_reason_is_insertable_and_quoted_value_survives(corpus, tmp_path):
    v = tmp_path / "v.txt"
    v.write_text('feedback_example.md\tterminal=true\tterminal_reason="judgment call"\n',
                 encoding="utf-8")
    rc, rep = run(corpus, v, "--apply")
    assert rc == 0
    text = (corpus / "feedback_example.md").read_text(encoding="utf-8")
    assert '  terminal_reason: "judgment call"' in text


def test_insertion_leaves_body_and_other_keys_byte_identical(corpus, tmp_path):
    before = (corpus / "feedback_example.md").read_text(encoding="utf-8")
    v = tmp_path / "v.txt"
    v.write_text('feedback_example.md\tterminal=true\n', encoding="utf-8")
    run(corpus, v, "--apply")
    after = (corpus / "feedback_example.md").read_text(encoding="utf-8")
    assert after.replace("  terminal: true\n", "", 1) == before


def test_non_insertable_absent_key_still_reports_key_absent(tmp_path):
    """occurrences is settable but NOT insertable: a file missing it is a schema
    defect to surface, not a hole to quietly fill."""
    d = tmp_path / "m"
    d.mkdir()
    (d / "feedback_x.md").write_text(
        "---\nname: x\nmetadata:\n  promoted: no\n---\n\nbody\n", encoding="utf-8")
    v = tmp_path / "v.txt"
    v.write_text("feedback_x.md\toccurrences=2\n", encoding="utf-8")
    rc, rep = run(d, v)
    assert rc == 1 and any("key-absent" in f for f in rep["failed"])


def test_terminal_already_present_is_overwritten_not_duplicated(tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "feedback_y.md").write_text(
        "---\nname: y\nmetadata:\n  promoted: no\n  terminal: false\n---\n\nbody\n",
        encoding="utf-8")
    v = tmp_path / "v.txt"
    v.write_text("feedback_y.md\tterminal=true\n", encoding="utf-8")
    rc, rep = run(d, v, "--apply")
    text = (d / "feedback_y.md").read_text(encoding="utf-8")
    assert rc == 0 and text.count("terminal:") == 1 and "  terminal: true" in text


def test_conservation_allows_only_inserted_allowed_keys():
    original = "---\nmetadata:\n  promoted: no\n---\nbody\n"
    good = "---\nmetadata:\n  promoted: no\n  terminal: true\n---\nbody\n"
    bad_prose = "---\nmetadata:\n  promoted: no\n---\nbody\nsneaky\n"
    bad_key = "---\nmetadata:\n  promoted: no\n  sneaky: true\n---\nbody\n"
    assert amv.conservation_ok(original, good)
    assert not amv.conservation_ok(original, bad_prose)
    assert not amv.conservation_ok(original, bad_key)


def test_conservation_allows_insert_merged_into_an_adjacent_replace():
    """SequenceMatcher merges an insertion that abuts a changed line into ONE
    `replace` opcode with unequal spans. Treating unequal spans as failure rejected
    all 40 real terminal-mark files, which is how this was found."""
    original = "---\nmetadata:\n  promoted: no\n  needs_review: true\n---\nbody\n"
    good = ("---\nmetadata:\n  promoted: no\n  terminal: true\n"
            "  needs_review: false\n---\nbody\n")
    dropped = "---\nmetadata:\n  terminal: true\n  needs_review: false\n---\nbody\n"
    ate_prose = ("---\nmetadata:\n  promoted: no\n  terminal: true\n"
                 "  needs_review: false\n---\n")
    assert amv.conservation_ok(original, good)
    assert not amv.conservation_ok(original, dropped)    # promoted line lost
    assert not amv.conservation_ok(original, ate_prose)  # body lost


def test_terminal_reason_lands_after_terminal_not_after_promoted(corpus, tmp_path):
    v = tmp_path / "v.txt"
    v.write_text('feedback_example.md\tterminal=true\tterminal_reason="because"\n',
                 encoding="utf-8")
    rc, rep = run(corpus, v, "--apply")
    assert rc == 0
    lines = (corpus / "feedback_example.md").read_text(encoding="utf-8").splitlines()
    assert lines.index('  terminal_reason: "because"') == lines.index("  terminal: true") + 1


# --- description support (D3 precondition, added 2026-08-25) -------------------
# `description` is the recall key. The 2026-08-25 tier split measured it as the only
# channel that carries traffic, so D3 re-keys all 503 of them. It differs from every
# other allowed key in two ways that matter: it is TOP-LEVEL (not indented under
# `metadata:`) and it holds arbitrary prose, so a colon or a `#` in the value can
# produce frontmatter that the in-house regex parser reads happily and a strict YAML
# reader rejects. That is the 2026-08-20 failure shape, so these tests assert against
# yaml.safe_load, never against the tool's own parser.
import yaml  # noqa: E402

DESC_CASES = {
    "plain": "After a plain rewrite",
    "colon": "When asserting absence, name the scope: say why it would have contained it",
    "hash": "Bump occurrences on every repeat fire #2 onward",
    "quote": 'Prose saying "written down and followed" is not built yet',
    "both": 'Trigger: the phrase "let us plan X" #gate',
    "leading_pct": "%50 of runs skip the gate",
}


def _strict_fm(text: str) -> dict:
    """Parse with the STRICTEST reader available, never the tool's own regex."""
    m = amv.FRONTMATTER_RE.match(text)
    assert m, "frontmatter missing"
    return yaml.safe_load(m.group(1))


def _write_desc(corpus, tmp_path, value):
    v = tmp_path / "v.txt"
    v.write_text(f"feedback_example.md\tdescription={value}\n", encoding="utf-8")
    return run(corpus, v, "--apply")


@pytest.mark.parametrize("label", sorted(DESC_CASES))
def test_description_roundtrips_under_strict_yaml(corpus, tmp_path, label):
    val = DESC_CASES[label]
    rc, rep = _write_desc(corpus, tmp_path, val)
    assert rc == 0, rep
    assert rep["changed"] == 1, rep
    got = _strict_fm((corpus / "feedback_example.md").read_text(encoding="utf-8"))
    assert got["description"] == val, f"{label}: round-trip lost the value"


def test_description_rewrite_leaves_body_and_other_keys_untouched(corpus, tmp_path):
    before = (corpus / "feedback_example.md").read_text(encoding="utf-8")
    rc, rep = _write_desc(corpus, tmp_path, DESC_CASES["colon"])
    assert rc == 0 and rep["changed"] == 1, rep
    after = (corpus / "feedback_example.md").read_text(encoding="utf-8")
    assert after != before, "nothing was written -- test would pass vacuously"
    assert after.split("---", 2)[2] == before.split("---", 2)[2]
    fm = _strict_fm(after)
    assert fm["metadata"]["occurrences"] == 1
    assert fm["metadata"]["promoted"] is False or fm["metadata"]["promoted"] == "no"
    assert fm["name"] == "feedback_example"
    assert len(after.splitlines()) == len(before.splitlines())


def test_multiline_description_is_refused_not_written(corpus, tmp_path):
    """A newline would truncate the key or swallow the following line."""
    text = (corpus / "feedback_example.md").read_text(encoding="utf-8")
    _, status = amv.set_key(text, "description", "line one\nline two")
    assert status != "set", "a multi-line description must be refused"


def test_description_absent_is_reported_not_inserted(tmp_path):
    """All 503 files carry one; an absent key is a schema defect, never an insert."""
    f = tmp_path / "feedback_nodesc.md"
    f.write_text("---\nname: feedback_nodesc\nmetadata:\n  occurrences: 1\n---\n\nbody\n",
                 encoding="utf-8")
    v = tmp_path / "v.txt"
    v.write_text("feedback_nodesc.md\tdescription=anything\n", encoding="utf-8")
    rc, rep = run(tmp_path, v, "--apply")
    assert rep["changed"] == 0
    assert "description: anything" not in f.read_text(encoding="utf-8")


# --- the strict-YAML gate must actually refuse, not merely exist ---------------
# Added 2026-08-25 after `mutation_check --isolation` left 5 mutants alive inside
# strict_roundtrip_ok: every early `return False` could be flipped with the suite
# still green, i.e. the guard was decorative at exactly the spots that justify it.

def test_roundtrip_rejects_text_with_no_frontmatter():
    assert amv.strict_roundtrip_ok("no frontmatter here\n", "description", "x") is False


def test_roundtrip_rejects_frontmatter_that_is_not_valid_yaml():
    text = '---\ndescription: "unterminated\nmetadata: [1, 2\n---\n\nbody\n'
    assert amv.strict_roundtrip_ok(text, "description", "unterminated") is False


def test_roundtrip_rejects_frontmatter_that_parses_to_a_non_mapping():
    text = "---\n- just\n- a\n- list\n---\n\nbody\n"
    assert amv.strict_roundtrip_ok(text, "description", "x") is False


def test_roundtrip_rejects_a_value_that_parses_back_differently():
    """Parsing is not enough. It must give back exactly what was intended."""
    text = '---\ndescription: yes\n---\n\nbody\n'
    # YAML reads a bare `yes` as the boolean True, not the string "yes".
    assert amv.strict_roundtrip_ok(text, "description", "yes") is False


def test_roundtrip_accepts_a_correctly_quoted_value():
    text = '---\ndescription: "a: b #c"\n---\n\nbody\n'
    assert amv.strict_roundtrip_ok(text, "description", "a: b #c") is True


def test_malformed_frontmatter_blocks_the_write_end_to_end(tmp_path):
    """The gate must be wired into main, not just callable."""
    f = tmp_path / "feedback_broken.md"
    original = '---\nname: feedback_broken\ndescription: "old"\nmetadata: [1, 2\n---\n\nbody\n'
    f.write_text(original, encoding="utf-8")
    v = tmp_path / "v.txt"
    v.write_text("feedback_broken.md\tdescription=new value\n", encoding="utf-8")
    rc, rep = run(tmp_path, v, "--apply")
    assert rc == 1, rep
    assert any("strict YAML round-trip failed" in e for e in rep["failed"]), rep
    assert f.read_text(encoding="utf-8") == original, "refused write still mutated the file"


# ---------------------------------------------------------------- nested-key round-trip
# 2026-08-25: strict_roundtrip_ok did a top-level lookup only, so every key living under
# `metadata:` came back None and the check refused the write while blaming the value.

NESTED_FM = (
    '---\nname: x\ndescription: "top level"\nmetadata:\n  occurrences: 1\n'
    '  detector_signature: "\\\\berror\\\\b"\n---\nBody\n'
)


def test_strict_roundtrip_finds_a_key_nested_under_metadata():
    assert amv.strict_roundtrip_ok(NESTED_FM, "detector_signature", r"\berror\b") is True


def test_strict_roundtrip_still_finds_a_top_level_key():
    assert amv.strict_roundtrip_ok(NESTED_FM, "description", "top level") is True


def test_strict_roundtrip_rejects_a_wrong_nested_value():
    assert amv.strict_roundtrip_ok(NESTED_FM, "detector_signature", "something else") is False


def test_strict_roundtrip_rejects_a_key_that_is_absent_everywhere():
    assert amv.strict_roundtrip_ok(NESTED_FM, "not_a_key", "v") is False
