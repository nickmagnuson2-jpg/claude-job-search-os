"""tools/learning_state.py: one definition of every learning-loop state.

Each test maps to a Tests cell of output/memory-hygiene/112226-state-matrix.md. Fixtures are
synthetic (public repo).
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import learning_state as ls  # noqa: E402

D = dt.date


# ------------------------------------------------------------------ promoted: every live token maps

@pytest.mark.parametrize("value,state", [
    ("no", "open"), ("not yet", "open"), ("false", "open"),
    ("no -- REOPENED 2026-09-16", "open"), ("", "open"), (None, "open"),
    ("partial -- hook landed, test owed", "partial"),
    ("yes -- exit1, 2026-11-21", "closed"), ("Yes (framework/style.md example 3, 2026-01-02)", "closed"),
    ("Yes: encoded (framework/artifact-workflow.md)", "closed"),
    ("skill", "closed"), ("hook", "closed"), ("principle", "closed"), ("hard-rule", "closed"),
    ("framework", "closed"), ("script", "closed"), ("doc", "closed"), ("schema", "closed"),
])
def test_every_live_token_maps(value, state):
    d = ls.disposition(value)
    assert d.state == state
    assert d.defect is None


def test_every_vocabulary_token_has_a_disposition():
    for tok in ls.OPEN_TOKENS + ls.PARTIAL_TOKENS + ls.CLOSED_TOKENS:
        assert ls.disposition(tok).state in ("open", "partial", "closed")
    assert set(ls.PROMOTED_TOKENS) == set(ls.OPEN_TOKENS + ls.PARTIAL_TOKENS
                                          + ls.CLOSED_TOKENS + ls.POINTER_TOKENS)


@pytest.mark.parametrize("value", ["n/a -- merged; see another rule", "Encoded (somewhere)",
                                   "4", "**Yes**", "promoted", "done", "nothing"])
def test_unknown_is_a_defect_and_stays_in_the_backlog(value):
    d = ls.disposition(value)
    assert d.state == "defect"
    assert d.defect
    assert ls.in_backlog(d)
    assert not ls.claims_gate(d)


def test_token_is_lowercased_leading_word():
    assert ls.promoted_token("Yes: encoded") == "yes"
    assert ls.promoted_token("hard-rule -- CLAUDE.md") == "hard-rule"
    assert ls.promoted_token("n/a -- x") == "n/a"
    assert ls.promoted_token("**Yes**") == ""
    assert ls.promoted_token(None) == ""
    assert ls.promoted_token("not_yet") == "not"


def test_a_word_that_merely_starts_like_a_token_is_not_that_token():
    assert ls.disposition("nothing landed").state == "defect"
    assert ls.disposition("notation").state == "defect"
    assert ls.disposition("yesterday").state == "defect"


def test_strict_reader_inline_comment_case_reads_open():
    # The scanner's fail-open bug: `"no"  # comment` kept the quotes and read promoted.
    # Through memory_frontmatter the value arrives as "no"; the vocabulary reads it open.
    assert ls.disposition("no").state == "open"


# ------------------------------------------------------------------ pointers

def test_pointer_tokens_need_a_pointer():
    for v in ("superseded", "merged", "parked", "Superseded", "merged -- see the other one",
              "parked until later", "superseded by", "merged into"):
        d = ls.disposition(v)
        assert d.state == "defect", v
        assert "pointer" in d.defect


def test_superseded_by_a_dated_ruling_or_existing_path(tmp_path):
    assert ls.disposition("superseded by Nick ruling 2026-11-22").state == "closed"
    (tmp_path / "framework").mkdir()
    (tmp_path / "framework" / "voice.md").write_text("x", encoding="utf-8")
    ok = ls.disposition("superseded by framework/voice.md:296", repo_root=tmp_path)
    assert ok.state == "closed" and ls.is_retired(ok)
    bad = ls.disposition("superseded by framework/missing.md", repo_root=tmp_path)
    assert bad.state == "defect"
    assert ls.disposition("superseded by something vague").state == "defect"


def test_merged_into_an_existing_memory_file(tmp_path):
    (tmp_path / "feedback_target.md").write_text("x", encoding="utf-8")
    assert ls.disposition("merged into feedback_target", memory_dir=tmp_path).state == "closed"
    assert ls.disposition("merged -- into feedback_target.md", memory_dir=tmp_path).state == "closed"
    assert ls.disposition("merged into feedback_other", memory_dir=tmp_path).state == "defect"
    assert ls.disposition("merged into Some Prose").state == "defect"


def test_parked_needs_a_dated_ruling():
    assert ls.disposition("parked by D4, 2026-11-09").state == "closed"
    assert ls.disposition("parked by D4").state == "defect"


def test_retired_is_closed_but_listed_and_claims_no_gate():
    d = ls.disposition("superseded by ruling 2026-11-22")
    assert d.state == "closed"
    assert ls.is_retired(d)
    assert not ls.in_backlog(d)
    assert not ls.claims_gate(d)


# ------------------------------------------------------------------ backlog vs gate claim: two predicates

def test_partial_keeps_backlog_and_i8():
    d = ls.disposition("partial -- exit1, gate landed")
    assert ls.in_backlog(d)
    assert ls.claims_gate(d)


def test_closed_tier_claims_gate_and_leaves_backlog():
    for v in ("yes", "hook", "skill"):
        d = ls.disposition(v)
        assert not ls.in_backlog(d) and ls.claims_gate(d) and not ls.is_retired(d)


def test_open_is_backlog_without_gate_claim():
    d = ls.disposition("no")
    assert ls.in_backlog(d) and not ls.claims_gate(d) and not ls.is_retired(d)


# ------------------------------------------------------------------ due: one definition

def test_due_iff_occurrences_reach_reopen_at():
    assert ls.due_state(3, 3) == "due"
    assert ls.due_state(4, 3) == "due"
    assert ls.due_state(2, 3) == "not_due"
    assert ls.due_state("5", 5) == "due"


def test_no_reopen_at_is_no_number_never_due():
    assert ls.due_state(40, None) == "no_number"
    assert ls.due_state(40, "") == "no_number"


@pytest.mark.parametrize("bad", [0, -2, True, False, "3", 2.0, "3rd fire"])
def test_bad_reopen_at(bad):
    assert ls.due_state(9, bad) == "bad_reopen_at"


@pytest.mark.parametrize("bad", [-1, True, "many", None, 1.5, "TODO"])
def test_bad_occurrences(bad):
    assert ls.due_state(bad, 2) == "bad_occurrences"


def test_prose_gate_and_status_words_are_never_read():
    # Only the typed threshold counts; "TRIPPED" in any argument changes nothing.
    assert ls.due_state(3, None) == "no_number"
    assert ls.due_state(2, "TRIPPED") == "bad_reopen_at"


def test_lanes_without_gates_use_two():
    assert ls.LADDER_THRESHOLD == 2
    for lane in ls.LADDER_LANES:
        assert ls.due_state(2, lane=lane) == "due"
        assert ls.due_state(1, lane=lane) == "not_due"
    with pytest.raises(ValueError):
        ls.due_state(2, 3, lane="friction")
    with pytest.raises(ValueError):
        ls.due_state(2, 3, lane="nowhere")


# ------------------------------------------------------------------ memory rule state

def test_memory_state_due_only_in_backlog():
    s = ls.memory_state({"promoted": "no", "occurrences": 3, "reopen_at": 3})
    assert s.due == "due" and s.disposition.state == "open" and not s.terminal
    s = ls.memory_state({"promoted": "yes", "occurrences": 3, "reopen_at": 3})
    assert s.due == "-"
    s = ls.memory_state({"promoted": "no", "occurrences": 3, "reopen_at": 3, "terminal": "yes"})
    assert s.due == "-" and s.terminal


def test_memory_state_no_number_is_a_listed_defect():
    s = ls.memory_state({"promoted": "partial -- half", "occurrences": 6,
                         "reopen_gate": "TRIPPED at fire 5"})
    assert s.due == "no_number"
    assert any(x.startswith("reopen_at:") for x in s.defects)


def test_memory_state_unknown_token_is_open_with_defect():
    s = ls.memory_state({"promoted": "n/a -- x", "occurrences": 2, "reopen_at": 2})
    assert s.disposition.state == "defect"
    assert s.due == "due"
    assert any(x.startswith("promoted:") for x in s.defects)


def test_memory_state_missing_promoted_is_open():
    s = ls.memory_state({"occurrences": 1})
    assert s.disposition.state == "open"


def test_is_terminal():
    assert ls.is_terminal({"terminal": "yes"})
    assert ls.is_terminal({"terminal": "true"})
    assert not ls.is_terminal({"terminal": "no"})
    assert not ls.is_terminal({})


# ------------------------------------------------------------------ validate_fields (the writers' check)

def test_validate_clean():
    assert ls.validate_fields({"promoted": "no", "exit_path": "exit1", "reopen_at": 3,
                               "terminal": "no"}) == []


def test_validate_reports_each_key():
    out = ls.validate_fields({"promoted": "Encoded", "exit_path": "exit3", "reopen_at": 0,
                              "terminal": "maybe"})
    assert sorted(x.split(":")[0] for x in out) == ["exit_path", "promoted", "reopen_at", "terminal"]


def test_validate_limited_to_keys():
    f = {"promoted": "Encoded", "reopen_at": 3}
    assert ls.validate_fields(f, keys=["reopen_at"]) == []
    assert ls.validate_fields(f, keys=["promoted"])


def test_skill_exit_needs_file_and_step(tmp_path):
    (tmp_path / ".claude" / "skills" / "x").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "x" / "SKILL.md").write_text("x", encoding="utf-8")
    home = tmp_path / "home-skills"
    (home / "y").mkdir(parents=True)
    (home / "y" / "SKILL.md").write_text("y", encoding="utf-8")
    kw = dict(repo_root=tmp_path, skills_home=home)
    ok = {"exit_path": "skill", "skill_file": ".claude/skills/x/SKILL.md", "skill_step": "Step 5"}
    assert ls.validate_fields(ok, **kw) == []
    assert ls.validate_fields({**ok, "skill_file": "y/SKILL.md"}, **kw) == []
    assert ls.validate_fields({**ok, "skill_file": "missing/SKILL.md"}, **kw)
    assert ls.validate_fields({**ok, "skill_step": ""}, **kw)
    assert ls.validate_fields({"exit_path": "skill"}, **kw)
    # the skill keys are checked whichever of them changed
    assert ls.validate_fields({**ok, "skill_step": " "}, keys=["skill_step"], **kw)


def test_exit_paths():
    assert ls.EXIT_PATHS == ("exit1", "exit2", "skill", "terminal")
    assert ls.validate_fields({"exit_path": ""}) == []


# ------------------------------------------------------------------ friction

def test_is_resolved():
    assert ls.is_resolved("RESOLVED 2026-11-22: retagged")
    assert ls.is_resolved("  RESOLVED 2026-11-22")
    assert not ls.is_resolved("auto-logged by transcript scan")
    assert not ls.is_resolved(None)


def test_counts_and_unknown_kind():
    assert ls.counts("friction") and ls.counts("false-positive-block")
    assert not ls.counts("block-as-designed") and not ls.counts("harness")
    with pytest.raises(ValueError):
        ls.counts("block")


def test_non_counting_kinds_excluded_from_open_and_due():
    assert ls.friction_state("block-as-designed", "", 9) == "not_counting"
    assert ls.friction_state("harness", "", 9) == "not_counting"
    assert ls.friction_state("friction", "", 2) == "due"
    assert ls.friction_state("false-positive-block", "", 3) == "due"
    assert ls.friction_state("friction", "", 1) == "open"
    assert ls.friction_state("friction", "RESOLVED 2026-11-22: x", 9) == "resolved"
    assert ls.friction_state("", "", 2) == "defect_kind"
    assert ls.friction_state("block", "RESOLVED x", 2) == "defect_kind"
    assert ls.friction_state("friction", "", "x") == "bad_occurrences"


# ------------------------------------------------------------------ lessons.md Section 2

GOOD = "| A pattern | A rule | 2 | No | 2026-11-19 |"


def test_lessons_row_shapes():
    assert ls.parse_lessons_row(GOOD) == (["A pattern", "A rule", "2", "No", "2026-11-19"], None)
    assert ls.parse_lessons_row("| Pattern | Rule | Occurrences | Promoted | Date |") == (None, None)
    assert ls.parse_lessons_row("|---------|------|-------------|----------|------|") == (None, None)
    assert ls.parse_lessons_row("prose line") == (None, None)
    assert ls.parse_lessons_row("") == (None, None)


# The 9 shapes repaired by hand on 2026-11-22 (data/workstreams/memory-hygiene.md 07:19).
@pytest.mark.parametrize("row", [
    "| A pattern | A rule | 2 | No |",                          # Date cell missing (rows 224-228)
    "| A pattern | A rule with a | pipe | 2 | No | 2026-11-19 |",  # literal pipe (229, 310)
])
def test_lessons_wrong_cell_count_is_a_defect(row):
    cells, defect = ls.parse_lessons_row(row)
    assert cells is None and "cell" in defect


@pytest.mark.parametrize("promoted", ["4", "Encoded (framework/x.md)", "**Yes**", "Superseded"])
def test_lessons_bad_promoted_cell_is_a_defect(promoted):
    d, due, defects = ls.lessons_state(["p", "r", "2", promoted, "2026-11-19"])
    assert d.state == "defect"
    assert any(x.startswith("promoted:") for x in defects)


def test_lessons_todo_occurrences_and_bad_date_are_defects():
    _, due, defects = ls.lessons_state(["p", "r", "TODO", "No", "2026-11-19"])
    assert due == "bad_occurrences" and any(x.startswith("occurrences:") for x in defects)
    _, _, defects = ls.lessons_state(["p", "r", "1", "No", "Nov 19"])
    assert any(x.startswith("date:") for x in defects)


def test_lessons_yes_encoded_reads_closed():
    d, due, defects = ls.lessons_state(["p", "r", "3", "Yes: encoded (framework/x.md)", "2026-10-28"])
    assert d.state == "closed" and defects == []


def test_lessons_due_by_ladder():
    assert ls.lessons_state(["p", "r", "2", "No", "2026-11-19"])[1] == "due"
    assert ls.lessons_state(["p", "r", "1", "No", "2026-11-19"])[1] == "not_due"
    assert ls.lessons_state(["p", "r", "2", "Partial: half", "2026-11-19"])[1] == "due"
    assert ls.lessons_state(["p", "r", "5", "Yes", "2026-11-19"])[1] == "-"


# ------------------------------------------------------------------ kind

def test_trusted_sources_give_a_kind():
    assert ls.kind_of({"kind": "MACHINERY", "source": "061226 (P+H merged)"}) == "MACHINERY"
    assert ls.kind_of({"kind": "LESSON", "source": "112226 full-read re-check"}) == "LESSON"
    assert ls.kind_of({"kind": "HYBRID", "source": "061226 (P+H merged)"}) == "LESSON"
    assert ls.kind_of({"kind": "PRINCIPLE", "source": "061226 (P+H merged)"}) == "LESSON"


def test_description_only_source_is_not_a_kind():
    assert ls.kind_of({"kind": "FACT", "source": "112226 P1a description-read"}) is None
    assert ls.kind_of({"kind": "FACT"}) is None
    assert ls.kind_of({"kind": "OTHER", "source": "061226 (P+H merged)"}) is None
    assert ls.kind_of(None) is None


def test_half_lives_as_ruled():
    assert ls.HALF_LIFE_DAYS == {"MACHINERY": 60, "FACT": 180, "LESSON": 240}


# ------------------------------------------------------------------ freshness

def test_audit_sessions_do_not_reconfirm():
    assert ls.reconfirms(1)
    assert ls.reconfirms(19)
    assert not ls.reconfirms(20)
    assert not ls.reconfirms(140)


def test_unknown_kind():
    assert ls.freshness_state(None, D(2026, 11, 1), D(2026, 8, 24), D(2026, 11, 22)) == "unknown_no_kind"


def test_entry_within_half_life_is_current():
    assert ls.freshness_state("MACHINERY", D(2026, 9, 23), D(2026, 8, 24), D(2026, 11, 22)) == "current"


def test_entry_older_than_half_life_is_unverified():
    assert ls.freshness_state("MACHINERY", D(2026, 9, 22), D(2026, 8, 24), D(2026, 11, 22)) == "unverified"


def test_w_ge_h_with_no_entry_is_unverified():
    # W = 90 days >= H = 60: no re-confirmation observed within H
    assert ls.freshness_state("MACHINERY", None, D(2026, 8, 24), D(2026, 11, 22)) == "unverified"
    assert ls.freshness_state("MACHINERY", None, D(2026, 9, 23), D(2026, 11, 22)) == "unverified"


def test_w_lt_h_with_no_entry_is_unknown_and_window_start_is_never_a_date():
    assert ls.freshness_state("LESSON", None, D(2026, 8, 24), D(2026, 11, 22)) == "unknown_not_observed"
    assert ls.freshness_state("MACHINERY", None, D(2026, 9, 24), D(2026, 11, 22)) == "unknown_not_observed"


def test_entry_outside_window_raises():
    with pytest.raises(ValueError):
        ls.freshness_state("FACT", D(2026, 8, 1), D(2026, 8, 24), D(2026, 11, 22))
    with pytest.raises(ValueError):
        ls.freshness_state("FACT", D(2026, 12, 1), D(2026, 8, 24), D(2026, 11, 22))


def test_unknown_kind_name_raises():
    with pytest.raises(ValueError):
        ls.freshness_state("PRINCIPLE", None, D(2026, 8, 24), D(2026, 11, 22))


def test_lessons_text_after_the_closing_pipe_is_a_defect_not_a_silent_drop():
    # Real shape, 2026-11-22: a row ending in an HTML comment after its last pipe was
    # dropped silently by a parser that required the line to end with "|".
    cells, defect = ls.parse_lessons_row(GOOD + " <!-- a note -->")
    assert cells is None
    assert defect and "after the closing pipe" in defect


def test_lessons_date_cell_may_carry_later_fires():
    # Live convention: "2026-07-24 (6th: 2026-09-22)" records a later fire beside the first.
    _, _, defects = ls.lessons_state(["p", "r", "6", "Partial: half", "2026-07-24 (6th: 2026-09-22)"])
    assert defects == []


# ------------------------------------------------------------------ mutation r1: messages and branches

def test_pointer_defect_names_the_right_word():
    assert "'merged into <ref>'" in ls.disposition("merged").defect
    assert "'superseded by <ref>'" in ls.disposition("superseded").defect
    assert "'parked by <ref>'" in ls.disposition("parked").defect


def test_parked_by_a_path_without_date_is_a_defect():
    assert ls.disposition("parked by framework/voice.md").state == "defect"


def test_no_token_defect_quotes_the_value():
    d = ls.disposition("**Yes**")
    assert d.state == "defect" and "**Yes**" in d.defect


def test_skill_file_missing_says_it_is_needed(tmp_path):
    out = ls.validate_fields({"exit_path": "skill", "skill_step": "Step 5"},
                             repo_root=tmp_path, skills_home=tmp_path)
    assert out == ["skill_file: exit_path skill needs the skill or framework file"]


def test_memory_state_names_each_defect():
    s = ls.memory_state({"promoted": "no", "occurrences": 3, "reopen_at": 0})
    assert s.due == "bad_reopen_at" and s.defects == ("reopen_at: 0 is not a positive integer",)
    s = ls.memory_state({"promoted": "no", "occurrences": "x", "reopen_at": 2})
    assert s.due == "bad_occurrences" and s.defects == ("occurrences: 'x' is not a count",)
    s = ls.memory_state({"promoted": "no", "occurrences": 3})
    assert s.defects == ("reopen_at: missing; the rule can never be due",)
    s = ls.memory_state({"promoted": "no", "occurrences": 3, "reopen_at": 3})
    assert s.defects == ()
    s = ls.memory_state({"promoted": "Encoded", "occurrences": 1, "reopen_at": 3})
    assert len(s.defects) == 1 and s.defects[0].startswith("promoted: token 'encoded'")


def test_not_friction_kind_is_non_counting():
    # The rows retagged "misattributed: not friction" on 2026-11-22 (org 68).
    assert "not-friction" in ls.FRICTION_KINDS
    assert "not-friction" not in ls.COUNTING_KINDS
    assert not ls.counts("not-friction")
    assert ls.friction_state("not-friction", "", 9) == "not_counting"


# ------------------------------------------------------------------ Codex r1

def test_superseded_path_must_stay_inside_its_root(tmp_path):
    # F3: an existing file outside the root closed the record.
    root = tmp_path / "repo"
    (root / "framework").mkdir(parents=True)
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    assert ls.disposition("superseded by ../outside.md", repo_root=root).state == "defect"
    assert ls.disposition("superseded by framework/../../outside.md", repo_root=root).state == "defect"
    (root / "framework" / "link.md").symlink_to(tmp_path / "outside.md")
    assert ls.disposition("superseded by framework/link.md", repo_root=root).state == "defect"
    (root / "framework" / "in.md").write_text("x", encoding="utf-8")
    assert ls.disposition("superseded by framework/in.md", repo_root=root).state == "closed"



# ------------------------------------------------------------------ Grok r1 (on d13281cb)

def test_a_pointer_that_cannot_be_resolved_stays_open(tmp_path):
    # F1 (P0): with no memory_dir, merged closed on format alone.
    d = ls.disposition("merged into definitely_absent_xyz")
    assert d.state == "defect" and "memory folder" in d.defect
    assert ls.in_backlog(d)
    # superseded by a path is checked against the repo by default
    assert ls.disposition("superseded by framework/definitely-missing-xyz.md").state == "defect"
    assert ls.disposition("superseded by tools/learning_state.py").state == "closed"


def test_a_date_does_not_excuse_a_missing_path(tmp_path):
    # F2: "path (date)" skipped the existence check.
    d = ls.disposition("superseded by framework/missing.md (2026-11-22)", repo_root=tmp_path)
    assert d.state == "defect"
    (tmp_path / "framework").mkdir()
    (tmp_path / "framework" / "missing.md").write_text("x", encoding="utf-8")
    assert ls.disposition("superseded by framework/missing.md (2026-11-22)",
                          repo_root=tmp_path).state == "closed"


def test_skill_file_must_stay_inside_its_root(tmp_path):
    # F3: skill_file had no containment check.
    root, home = tmp_path / "repo", tmp_path / "skills"
    root.mkdir(); home.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "SKILL.md").write_text("x", encoding="utf-8")
    f = {"exit_path": "skill", "skill_file": "../outside/SKILL.md", "skill_step": "Step 1"}
    assert ls.validate_fields(f, repo_root=root, skills_home=home)


def test_merged_into_a_hyphenated_memory_file(tmp_path):
    # F5: live archive files are hyphenated (archive-2026-04-orphans.md).
    (tmp_path / "archive-2026-04-orphans.md").write_text("x", encoding="utf-8")
    assert ls.disposition("merged into archive-2026-04-orphans.md", memory_dir=tmp_path).state == "closed"
    assert ls.disposition("merged into archive-2026-04-orphans", memory_dir=tmp_path).state == "closed"


def test_missing_count_on_a_record_is_bad_occurrences():
    # F6: a missing count defaulted to 0 and read not_due.
    s = ls.memory_state({"promoted": "no", "reopen_at": 3})
    assert s.due == "bad_occurrences"
    assert s.defects == ("occurrences: None is not a count",)
    assert ls.due_state(None, 3) == "bad_occurrences"


def test_a_note_with_no_state_keys_is_not_a_learning_record():
    # 312 live reference/project/user files carry none of the state keys.
    s = ls.memory_state({"name": "reference_x", "description": "where a thing lives"})
    assert s.due == "-" and s.defects == ()
    assert not ls.is_record({"name": "x"})
    assert ls.is_record({"promoted": "no"}) and ls.is_record({"occurrences": 1})


# The shapes repaired by hand in lessons.md Section 2 on 2026-11-22 (memory-hygiene.md,
# 07:19 and 07:24 entries). Each must be rejected now (Grok r1 F8).
@pytest.mark.parametrize("shape,cells_or_line", [
    ("Date cell missing (rows 224-228)", "| A pattern | A rule | 2 | No |"),
    ("literal pipe in the Rule cell (229, 310)", "| A pattern | rule a | b | 2 | No | 2026-11-19 |"),
    ("text after the closing pipe", "| p | r | 1 | No | 2026-11-19 | <!-- note -->"),
    ("Occurrences never recorded (AR34)", ["p", "r", "TODO", "No", "2026-09-21"]),
    ("count in the Promoted cell (209)", ["p", "r", "2", "4", "2026-11-19"]),
    ("occurrence notes in the Occurrences cell (209)", ["p", "r", "fired twice, see notes", "No", "2026-11-19"]),
    ("Encoded instead of a token (30 AR rows)", ["p", "r", "2", "Encoded (framework/x.md)", "2026-10-28"]),
    ("bold markup hiding Yes (128, 129)", ["p", "r", "2", "**Yes**", "2026-03-01"]),
    ("Superseded with no pointer", ["p", "r", "2", "Superseded", "2026-02-28"]),
])
def test_each_repaired_lessons_shape_is_rejected(shape, cells_or_line):
    if isinstance(cells_or_line, str):
        cells, defect = ls.parse_lessons_row(cells_or_line)
        assert cells is None and defect, shape
    else:
        assert ls.lessons_state(cells_or_line)[2], shape


# ------------------------------------------------------------------ org correction (68, 2026-11-22, B7 finding)

def test_below_two_with_no_reopen_at_is_not_due_and_not_a_defect():
    assert ls.due_state(1, None) == "not_due"
    assert ls.due_state(0, None) == "not_due"
    assert ls.due_state(2, None) == "no_number"
    s = ls.memory_state({"promoted": "no", "occurrences": 1})
    assert s.due == "not_due" and s.defects == ()
    # a present but bad reopen_at stays a defect at any count
    assert ls.due_state(1, 0) == "bad_reopen_at"


# ------------------------------------------------------------------ mutation after Grok r1

def test_merged_pointer_cannot_leave_the_memory_folder(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    d = ls.disposition("merged into ../outside", memory_dir=mem)
    assert d.state == "defect" and "not a memory file name" in d.defect


def test_parked_by_an_existing_path_still_needs_a_date():
    d = ls.disposition("parked by tools/learning_state.py")
    assert d.state == "defect" and "YYYY-MM-DD" in d.defect
