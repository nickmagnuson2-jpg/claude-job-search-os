"""tools/memory_frontmatter.py: the one strict reader and validating writer of memory frontmatter.

Spec: output/memory-hygiene/112226-state-matrix.md, "Shared mechanism" row 1, and design v4
section 1b. Fixtures are synthetic rule files (public repo).
"""
import datetime as dt
import difflib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import memory_frontmatter as mf  # noqa: E402

RULE = """---
name: feedback_example_rule
description: An example rule
metadata:
  node_type: memory
  type: feedback
  occurrences: 3
  promoted: no
  reopen_gate: "3rd fire: build the check"
  last_cited: 2026-11-01
---

**Rule:** an example.
"""


def _file(tmp_path, text=RULE, name="feedback_example_rule.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ------------------------------------------------------------------ read

def test_read_flattens_metadata_and_keeps_top_level():
    fields, defect = mf.read(RULE)
    assert defect is None
    assert fields["name"] == "feedback_example_rule"
    assert fields["occurrences"] == 3
    assert fields["reopen_gate"] == "3rd fire: build the check"
    assert "metadata" not in fields


def test_yaml_boolean_and_quoted_string_read_the_same():
    a, _ = mf.read("---\npromoted: no\n---\n")
    b, _ = mf.read('---\npromoted: "no"\n---\n')
    c, _ = mf.read("---\npromoted: false\n---\n")
    assert a["promoted"] == b["promoted"] == "no"
    assert c["promoted"] == "no"
    t, _ = mf.read("---\nterminal: true\n---\n")
    y, _ = mf.read("---\npromoted: yes\n---\n")
    assert t["terminal"] == "yes" and y["promoted"] == "yes"


def test_inline_comment_is_not_part_of_the_value():
    fields, defect = mf.read('---\npromoted: "no"  # waits for the 3rd fire\nterminal: false # judgment\n---\n')
    assert defect is None
    assert fields["promoted"] == "no"
    assert fields["terminal"] == "no"


def test_null_is_empty_string_and_dates_are_iso():
    fields, _ = mf.read("---\nexit_path:\nlast_cited: 2026-11-01\n---\n")
    assert fields["exit_path"] == ""
    assert fields["last_cited"] == "2026-11-01"
    assert isinstance(fields["last_cited"], str)


def test_block_scalar_reads_whole():
    fields, defect = mf.read("---\nreopen_gate: >-\n  4th fire, or any fix\n  shipped untested\n---\n")
    assert defect is None
    assert fields["reopen_gate"] == "4th fire, or any fix shipped untested"


def test_missing_block_is_a_named_defect():
    fields, defect = mf.read("**Rule:** no frontmatter here.\n")
    assert fields == {}
    assert defect.code == "no_frontmatter"


def test_unterminated_block_is_a_named_defect():
    fields, defect = mf.read("---\nname: x\n\n**Rule:** body\n")
    assert fields == {}
    assert defect.code == "no_frontmatter"


def test_malformed_yaml_is_a_named_defect_not_an_empty_dict():
    # The shape of the 7 live malformed files: a scalar line followed by an indented
    # mapping line under it.
    text = "---\nname: x\ndescription: one line\n  metadata: broken\n    type: feedback\n---\n"
    fields, defect = mf.read(text)
    assert fields == {}
    assert defect.code == "yaml_error"
    assert defect.detail


def test_non_mapping_block_is_a_named_defect():
    fields, defect = mf.read("---\n- a\n- b\n---\n")
    assert fields == {}
    assert defect.code == "not_a_mapping"


def test_empty_block_is_not_a_mapping():
    fields, defect = mf.read("---\n---\nbody\n")
    assert fields == {}
    assert defect.code == "not_a_mapping"


def test_key_at_both_levels_is_a_defect():
    text = "---\npromoted: yes\nmetadata:\n  promoted: no\n---\n"
    fields, defect = mf.read(text)
    assert fields == {}
    assert defect.code == "duplicate_key"
    assert "promoted" in defect.detail


def test_only_metadata_is_flattened():
    fields, defect = mf.read("---\nother:\n  inner: 1\n---\n")
    assert defect is None
    assert fields["other"] == {"inner": 1}
    assert "inner" not in fields


def test_read_file(tmp_path):
    fields, defect = mf.read_file(_file(tmp_path))
    assert defect is None and fields["promoted"] == "no"


def test_split_returns_block_and_rest():
    block, rest = mf.split(RULE)
    assert block.startswith("name: feedback_example_rule\n")
    assert block.endswith("last_cited: 2026-11-01\n")
    assert rest == "\n**Rule:** an example.\n"
    assert mf.split("no block\n") is None


def test_normalize_recurses():
    assert mf.normalize([True, None, dt.date(2026, 1, 2)]) == ["yes", "", "2026-01-02"]
    assert mf.normalize({"a": False}) == {"a": "no"}
    assert mf.normalize(3) == 3
    assert mf.normalize("text") == "text"
    assert mf.normalize(dt.datetime(2026, 1, 2, 3, 4)) == "2026-01-02T03:04:00"


# ------------------------------------------------------------------ write

def test_write_replaces_only_the_named_line(tmp_path):
    p = _file(tmp_path)
    new = mf.write(p, {"promoted": "partial -- hook landed, test owed", "reopen_at": 3})
    assert new["promoted"] == "partial -- hook landed, test owed"
    assert new["reopen_at"] == 3
    before = RULE.splitlines()
    after = p.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.ndiff(before, after))
    assert [x[2:] for x in diff if x.startswith("- ")] == ["  promoted: no"]
    assert [x[2:] for x in diff if x.startswith("+ ")] == [
        "  promoted: partial -- hook landed, test owed", "  reopen_at: 3"]
    # the new key goes at the end of metadata, at metadata's indentation
    assert after[after.index("  reopen_at: 3") - 1] == "  last_cited: 2026-11-01"
    assert p.read_text(encoding="utf-8").endswith("\n**Rule:** an example.\n")


def test_write_quotes_a_string_yaml_would_read_as_bool(tmp_path):
    p = _file(tmp_path)
    mf.write(p, {"promoted": "yes"})
    assert "promoted: 'yes'" in p.read_text(encoding="utf-8")
    assert mf.read_file(p)[0]["promoted"] == "yes"


def test_write_replaces_a_multiline_value(tmp_path):
    text = "---\nname: x\nreopen_gate: >-\n  one\n  two\npromoted: no\n---\nbody\n"
    p = _file(tmp_path, text)
    mf.write(p, {"reopen_gate": "single line now"})
    out = p.read_text(encoding="utf-8")
    assert "  one" not in out and "  two" not in out
    assert mf.read_file(p)[0]["reopen_gate"] == "single line now"
    assert mf.read_file(p)[0]["promoted"] == "no"


def test_write_new_key_without_metadata_goes_at_end_of_block(tmp_path):
    p = _file(tmp_path, "---\nname: x\npromoted: no\n---\nbody\n")
    mf.write(p, {"reopen_at": 2})
    assert p.read_text(encoding="utf-8") == "---\nname: x\npromoted: no\nreopen_at: 2\n---\nbody\n"


def test_write_refuses_occurrences(tmp_path):
    p = _file(tmp_path)
    with pytest.raises(mf.WriteRefused) as e:
        mf.write(p, {"occurrences": 4})
    assert "occurrences" in str(e.value)
    assert p.read_text(encoding="utf-8") == RULE


def test_write_refuses_an_invalid_state_and_writes_nothing(tmp_path):
    p = _file(tmp_path)
    with pytest.raises(mf.WriteRefused) as e:
        mf.write(p, {"promoted": "encoded somewhere"})
    assert any("promoted" in r for r in e.value.reasons)
    assert p.read_text(encoding="utf-8") == RULE


def test_write_refuses_a_pointer_token_without_pointer(tmp_path):
    p = _file(tmp_path)
    with pytest.raises(mf.WriteRefused):
        mf.write(p, {"promoted": "superseded"})
    assert p.read_text(encoding="utf-8") == RULE


def test_write_refuses_bad_reopen_at(tmp_path):
    p = _file(tmp_path)
    for bad in (0, -1, True, "3"):
        with pytest.raises(mf.WriteRefused):
            mf.write(p, {"reopen_at": bad})
    assert p.read_text(encoding="utf-8") == RULE


def test_write_refuses_a_malformed_file(tmp_path):
    text = "---\nname: x\ndescription: one\n  metadata: broken\n---\n"
    p = _file(tmp_path, text)
    with pytest.raises(mf.WriteRefused) as e:
        mf.write(p, {"promoted": "no"})
    assert "yaml_error" in str(e.value)
    assert p.read_text(encoding="utf-8") == text


def test_write_is_not_blocked_by_an_older_defect_in_another_key(tmp_path):
    text = RULE.replace("  promoted: no", "  promoted: n/a -- legacy")
    p = _file(tmp_path, text)
    mf.write(p, {"reopen_at": 3})
    assert mf.read_file(p)[0]["reopen_at"] == 3


def test_write_validates_merged_pointer_against_memory_dir(tmp_path):
    p = _file(tmp_path)
    with pytest.raises(mf.WriteRefused):
        mf.write(p, {"promoted": "merged into feedback_missing_target"}, memory_dir=tmp_path)
    (tmp_path / "feedback_target.md").write_text("---\nname: t\n---\n", encoding="utf-8")
    new = mf.write(p, {"promoted": "merged into feedback_target"}, memory_dir=tmp_path)
    assert new["promoted"] == "merged into feedback_target"


def test_write_refuses_a_value_that_would_not_read_back(tmp_path):
    p = _file(tmp_path)
    with pytest.raises(mf.WriteRefused):
        mf.write(p, {"description": {"nested": "mapping"}, "promoted": "no"})
    assert p.read_text(encoding="utf-8") == RULE


def test_write_leaves_no_temp_file(tmp_path):
    p = _file(tmp_path)
    mf.write(p, {"reopen_at": 3})
    assert sorted(x.name for x in tmp_path.iterdir()) == ["feedback_example_rule.md"]


# ------------------------------------------------------------------ check_text (the hooks' entry)

def test_check_text_clean_change_passes():
    new = RULE.replace("  promoted: no", "  promoted: partial -- half landed")
    assert mf.check_text(new, RULE) == []


def test_check_text_reports_a_frontmatter_defect():
    bad = "---\nname: x\ndescription: one\n  metadata: broken\n---\n"
    reasons = mf.check_text(bad, RULE)
    assert len(reasons) == 1 and reasons[0].startswith("frontmatter: yaml_error")


def test_check_text_blocks_an_invalid_changed_key():
    new = RULE.replace("  promoted: no", "  promoted: Encoded (somewhere)")
    reasons = mf.check_text(new, RULE)
    assert any(r.startswith("promoted:") for r in reasons)


def test_check_text_ignores_an_older_defect_in_an_untouched_key():
    old = RULE.replace("  promoted: no", "  promoted: n/a -- legacy")
    new = old.replace("  occurrences: 3", "  occurrences: 4")
    assert mf.check_text(new, old) == []
    # with no old text every key is checked, so the legacy value is reported
    assert any(r.startswith("promoted:") for r in mf.check_text(new))


def test_check_text_allows_a_hand_recorded_fire():
    new = RULE.replace("  occurrences: 3", "  occurrences: 4")
    assert mf.check_text(new, RULE) == []


def test_check_text_with_unreadable_old_checks_every_key():
    old = "---\nname: x\ndescription: one\n  metadata: broken\n---\n"
    new = RULE.replace("  promoted: no", "  promoted: n/a -- legacy")
    assert any(r.startswith("promoted:") for r in mf.check_text(new, old))


# ------------------------------------------------------------------ record_fire (the one count writer)

def test_record_fire_adds_exactly_one_and_the_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    p = _file(tmp_path)
    n = mf.record_fire(p, "2026-11-22 the same miss again in a draft", today=dt.date(2026, 11, 22))
    assert n == 4
    text = p.read_text(encoding="utf-8")
    assert mf.read(text)[0]["occurrences"] == 4
    assert text == RULE.replace("  occurrences: 3", "  occurrences: 4") + (
        "- Fire 4 recorded 2026-11-22: 2026-11-22 the same miss again in a draft\n")
    before, after = RULE.splitlines(), text.splitlines()
    assert [(a, b) for a, b in zip(before, after) if a != b] == [("  occurrences: 3", "  occurrences: 4")]


def test_record_fire_refuses_without_claudecode(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    p = _file(tmp_path)
    with pytest.raises(mf.WriteRefused) as e:
        mf.record_fire(p, "2026-11-22 a fire")
    assert "CLAUDECODE" in str(e.value)
    monkeypatch.setenv("CLAUDECODE", "0")
    with pytest.raises(mf.WriteRefused):
        mf.record_fire(p, "2026-11-22 a fire")
    assert p.read_text(encoding="utf-8") == RULE


@pytest.mark.parametrize("evidence", ["", "   ", "the same miss again, no date"])
def test_record_fire_refuses_without_dated_evidence(tmp_path, monkeypatch, evidence):
    monkeypatch.setenv("CLAUDECODE", "1")
    p = _file(tmp_path)
    with pytest.raises(mf.WriteRefused):
        mf.record_fire(p, evidence)
    assert p.read_text(encoding="utf-8") == RULE


@pytest.mark.parametrize("line", ["  occurrences: many", "  occurrences: -1", "  occurrences: true"])
def test_record_fire_refuses_a_bad_count(tmp_path, monkeypatch, line):
    monkeypatch.setenv("CLAUDECODE", "1")
    text = RULE.replace("  occurrences: 3", line)
    p = _file(tmp_path, text)
    with pytest.raises(mf.WriteRefused):
        mf.record_fire(p, "2026-11-22 a fire")
    assert p.read_text(encoding="utf-8") == text


def test_record_fire_refuses_a_missing_count(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    text = RULE.replace("  occurrences: 3\n", "")
    p = _file(tmp_path, text)
    with pytest.raises(mf.WriteRefused):
        mf.record_fire(p, "2026-11-22 a fire")
    assert p.read_text(encoding="utf-8") == text


# ------------------------------------------------------------------ mutation r1: branches the first tests missed

def test_write_refused_message_joins_reasons():
    assert str(mf.WriteRefused(["a: one", "b: two"])) == "a: one; b: two"


def test_write_keeps_a_four_space_metadata_indent(tmp_path):
    text = "---\nname: x\nmetadata:\n    type: feedback\n    promoted: no\n---\nbody\n"
    p = _file(tmp_path, text)
    mf.write(p, {"reopen_at": 2, "promoted": "partial -- half"})
    out = p.read_text(encoding="utf-8")
    assert out == ("---\nname: x\nmetadata:\n    type: feedback\n    promoted: partial -- half\n"
                   "    reopen_at: 2\n---\nbody\n")


def test_write_metadata_as_last_line_adds_at_default_indent(tmp_path):
    p = _file(tmp_path, "---\nname: x\nmetadata:\n---\nbody\n")
    mf.write(p, {"reopen_at": 2})
    assert mf.read_file(p)[0]["reopen_at"] == 2


def test_consistency_check_refuses_an_unreadable_result():
    with pytest.raises(mf.WriteRefused) as e:
        mf._refuse_unless_consistent({"name": "x"}, "---\nname: x\n  bad: [\n---\n", {})
    assert "does not re-read" in str(e.value)


def test_consistency_check_refuses_a_wrong_readback_and_a_side_change():
    new_text = "---\nname: y\npromoted: no\n---\n"
    with pytest.raises(mf.WriteRefused) as e:
        mf._refuse_unless_consistent({"name": "x", "promoted": "no"}, new_text, {"promoted": "yes"})
    assert sorted(e.value.reasons) == ["name: changed although not updated",
                                       "promoted: reads back as 'no', not 'yes'"]


def test_failed_replace_leaves_no_temp_file(tmp_path, monkeypatch):
    p = _file(tmp_path)

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(mf.os, "replace", boom)
    with pytest.raises(OSError):
        mf.write(p, {"reopen_at": 3})
    assert sorted(x.name for x in tmp_path.iterdir()) == ["feedback_example_rule.md"]
    assert p.read_text(encoding="utf-8") == RULE


def test_write_refuses_empty_updates(tmp_path):
    p = _file(tmp_path)
    with pytest.raises(mf.WriteRefused) as e:
        mf.write(p, {})
    assert e.value.reasons == ["nothing to write"]


def test_write_refuses_a_file_without_frontmatter(tmp_path):
    p = _file(tmp_path, "body only\n")
    with pytest.raises(mf.WriteRefused) as e:
        mf.write(p, {"reopen_at": 2})
    assert "no_frontmatter" in str(e.value)
    assert p.read_text(encoding="utf-8") == "body only\n"


def test_record_fire_names_a_frontmatter_defect(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    p = _file(tmp_path, "body only\n")
    with pytest.raises(mf.WriteRefused) as e:
        mf.record_fire(p, "2026-11-22 a fire")
    assert e.value.reasons[0].startswith("frontmatter: no_frontmatter")


def test_record_fire_on_a_file_without_final_newline(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    p = _file(tmp_path, RULE.rstrip("\n"))
    mf.record_fire(p, "2026-11-22 a fire", today=dt.date(2026, 11, 22))
    assert p.read_text(encoding="utf-8").endswith(
        "**Rule:** an example.\n- Fire 4 recorded 2026-11-22: 2026-11-22 a fire\n")


def test_record_fire_refuses_when_the_count_edit_touches_another_key(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    p = _file(tmp_path)
    real = mf._set_keys
    monkeypatch.setattr(mf, "_set_keys", lambda t, u: real(t, u).replace("name: feedback_example_rule", "name: other"))
    with pytest.raises(mf.WriteRefused):
        mf.record_fire(p, "2026-11-22 a fire")
    assert p.read_text(encoding="utf-8") == RULE


def test_write_keeps_metadata_indent_when_a_top_level_key_follows(tmp_path):
    text = "---\nmetadata:\n    type: feedback\n    promoted: no\nname: x\n---\nbody\n"
    p = _file(tmp_path, text)
    mf.write(p, {"reopen_at": 2})
    assert p.read_text(encoding="utf-8") == (
        "---\nmetadata:\n    type: feedback\n    promoted: no\n    reopen_at: 2\nname: x\n---\nbody\n")


# ------------------------------------------------------------------ Codex r1 (run on c57eec51..eaa76d59)

@pytest.mark.parametrize("text", [
    "---\npromoted: no\npromoted: yes\n---\n",
    "---\nmetadata:\n  promoted: no\n  promoted: yes\n---\n",
    "---\nmetadata:\n  a: 1\nmetadata:\n  promoted: yes\n---\n",
])
def test_duplicate_key_at_any_level_is_a_defect(text):
    # F1 (P0): safe_load keeps the last of two keys, so `no` then `yes` read closed.
    fields, defect = mf.read(text)
    assert fields == {}
    assert defect.code == "duplicate_key"


def test_write_and_record_fire_keep_the_file_mode(tmp_path, monkeypatch):
    # F4: mkstemp creates 0600; the original mode must survive.
    monkeypatch.setenv("CLAUDECODE", "1")
    p = _file(tmp_path)
    p.chmod(0o644)
    mf.write(p, {"reopen_at": 3})
    assert p.stat().st_mode & 0o777 == 0o644
    p.chmod(0o640)
    mf.record_fire(p, "2026-11-22 a fire")
    assert p.stat().st_mode & 0o777 == 0o640


# ------------------------------------------------------------------ Codex r2 (final round, on 91ac7393)

@pytest.mark.parametrize("block", [
    "metadata:\n  promoted: no  # waits for the 3rd fire\n  occurrences: 2\n",
    "metadata:\n  promoted: no\n    # rationale that must survive\n  occurrences: 2\n",
])
def test_write_refuses_to_replace_lines_that_carry_a_comment(tmp_path, block):
    # F1 (P0): a comment is invisible to read(), so replacing its line deleted it silently.
    text = "---\nname: x\n" + block + "---\nbody\n"
    p = _file(tmp_path, text)
    with pytest.raises(mf.WriteRefused) as e:
        mf.write(p, {"promoted": "partial -- half"})
    assert "comment" in str(e.value) and "promoted" in str(e.value)
    assert p.read_text(encoding="utf-8") == text


def test_record_fire_refuses_a_commented_count(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    text = RULE.replace("  occurrences: 3", "  occurrences: 3  # two in drafts, one in a brief")
    p = _file(tmp_path, text)
    with pytest.raises(mf.WriteRefused):
        mf.record_fire(p, "2026-11-22 a fire")
    assert p.read_text(encoding="utf-8") == text


def test_a_comment_on_another_key_does_not_block(tmp_path):
    text = RULE.replace("  last_cited: 2026-11-01", "  last_cited: 2026-11-01  # telemetry only")
    p = _file(tmp_path, text)
    mf.write(p, {"promoted": "partial -- half"})
    assert "  last_cited: 2026-11-01  # telemetry only" in p.read_text(encoding="utf-8")


def test_writers_refuse_a_symlink(tmp_path, monkeypatch):
    # F2: os.replace on the link swapped it for a regular file and left the target unchanged.
    monkeypatch.setenv("CLAUDECODE", "1")
    target = _file(tmp_path)
    alias = tmp_path / "alias.md"
    alias.symlink_to(target)
    with pytest.raises(mf.WriteRefused) as e:
        mf.write(alias, {"reopen_at": 3})
    assert "symlink" in str(e.value)
    with pytest.raises(mf.WriteRefused):
        mf.record_fire(alias, "2026-11-22 a fire")
    assert alias.is_symlink() and target.read_text(encoding="utf-8") == RULE


# ------------------------------------------------------------------ comment detection by yaml's own tokens
# Org 72 ruling (2026-11-22): the regex refused 7 of 8 live promoted values that hold
# "PR #61" inside quotes, and Grok r2 (on 6439d2e2) showed a comment glued to a quoted
# value ("no"# waits) passed it. A "#" is a comment only where PyYAML's scanner says so.

@pytest.mark.parametrize("line", [
    '  promoted: "yes -- hook, tools/check_x.py (PR #61), 2026-11-17"',   # inside a quoted scalar
    "  promoted: 'yes -- hook (#61)'",                                     # single-quoted
    "  promoted: yes -- hook PR#61",                                       # unquoted, no space before #
])
def test_a_hash_inside_the_value_is_not_a_comment(tmp_path, line):
    text = RULE.replace("  promoted: no", line)
    p = _file(tmp_path, text)
    mf.write(p, {"promoted": "partial -- half"})
    assert mf.read_file(p)[0]["promoted"] == "partial -- half"


@pytest.mark.parametrize("line", [
    "  promoted: no  # waits for the 3rd fire",          # trailing comment
    '  promoted: "no"# waits',                           # glued to a quoted value (Grok r2)
    "  promoted: 'no'# waits",
    "  promoted: no\n    # rationale on its own line",   # comment on its own line under the key
])
def test_a_real_comment_on_the_key_is_refused(tmp_path, line):
    text = RULE.replace("  promoted: no", line)
    p = _file(tmp_path, text)
    with pytest.raises(mf.WriteRefused) as e:
        mf.write(p, {"promoted": "partial -- half"})
    assert "comment" in str(e.value)
    assert p.read_text(encoding="utf-8") == text


def test_a_hash_in_block_scalar_content_is_not_a_comment(tmp_path):
    text = RULE.replace('  reopen_gate: "3rd fire: build the check"',
                        "  reopen_gate: >-\n    3rd fire\n    # not a comment here")
    p = _file(tmp_path, text)
    mf.write(p, {"reopen_gate": "4th fire"})
    assert mf.read_file(p)[0]["reopen_gate"] == "4th fire"


def test_comment_check_follows_line_shifts_between_updates(tmp_path):
    # Replacing a 3-line value with 1 line moves every later line up by 2; the second key's
    # comment must still be seen, and an uncommented key must not be refused.
    text = ("---\nname: x\nreopen_gate: >-\n  one\n  two\nterminal: no\n"
            "promoted: no  # keep me\n---\nbody\n")
    p = _file(tmp_path, text)
    with pytest.raises(mf.WriteRefused):
        mf.write(p, {"reopen_gate": "single", "promoted": "partial -- half"})
    assert p.read_text(encoding="utf-8") == text
    text2 = ("---\nname: x\nreopen_gate: >-\n  one\n  two\nterminal: no  # judgment\n"
             "promoted: no\n---\nbody\n")
    p2 = _file(tmp_path, text2, "feedback_second.md")
    mf.write(p2, {"reopen_gate": "single", "promoted": "partial -- half"})
    assert "terminal: no  # judgment" in p2.read_text(encoding="utf-8")


# ------------------------------------------------------------------ expect_sha256 (77's Codex r1 on apply, F1 P0)

def _sha(p):
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_write_refuses_a_file_changed_after_the_caller_hashed_it(tmp_path):
    p = _file(tmp_path)
    seen = _sha(p)
    changed = RULE.replace("  last_cited: 2026-11-01", "  last_cited: 2026-11-22")  # e.g. a stamp hook
    p.write_text(changed, encoding="utf-8")
    with pytest.raises(mf.WriteRefused) as e:
        mf.write(p, {"reopen_at": 3}, expect_sha256=seen)
    assert "sha256" in str(e.value)
    assert p.read_text(encoding="utf-8") == changed


def test_write_with_the_current_sha_proceeds(tmp_path):
    p = _file(tmp_path)
    mf.write(p, {"reopen_at": 3}, expect_sha256=_sha(p))
    assert mf.read_file(p)[0]["reopen_at"] == 3


def test_record_fire_refuses_a_file_changed_after_the_caller_hashed_it(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    p = _file(tmp_path)
    seen = _sha(p)
    changed = RULE.replace("  last_cited: 2026-11-01", "  last_cited: 2026-11-22")
    p.write_text(changed, encoding="utf-8")
    with pytest.raises(mf.WriteRefused):
        mf.record_fire(p, "2026-11-22 a fire", expect_sha256=seen)
    assert p.read_text(encoding="utf-8") == changed
    assert mf.record_fire(p, "2026-11-22 a fire", expect_sha256=_sha(p)) == 4


def test_crlf_file_reads_as_before_through_the_checked_read(tmp_path, monkeypatch):
    # Codex r3 (3324fd6b) F1: the byte read dropped read_text's newline translation, so a
    # CRLF file became no_frontmatter for write() and record_fire().
    monkeypatch.setenv("CLAUDECODE", "1")
    p = tmp_path / "feedback_crlf.md"
    p.write_bytes(RULE.replace("\n", "\r\n").encode("utf-8"))
    mf.write(p, {"reopen_at": 3})
    assert mf.read_file(p)[0]["reopen_at"] == 3
    p.write_bytes(RULE.replace("\n", "\r\n").encode("utf-8"))
    sha = __import__("hashlib").sha256(p.read_bytes()).hexdigest()
    assert mf.record_fire(p, "2026-11-22 a fire", expect_sha256=sha) == 4
