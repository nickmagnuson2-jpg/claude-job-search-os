"""Suite for tools/scan_promotion_candidates.py, the detector behind the promotion loop.

WHY THIS ONE MATTERS. It is the only reader of `occurrences`/`promoted`/`reopen_gate`/
`last_cited`, and it decides which memory rules surface as promotion candidates. Every
failure mode it has is SILENT in the worst direction: a rule that stops being detected does
not error, it simply stops appearing, and the backlog reads as an all-clear. Two such
defects are already documented in the source and are pinned here as regressions:

  * 2026-08-13 -- `promoted: partial -- <what's missing>` counted as promoted. 41 files had
    been set to `partial` precisely so half-landed rules would NOT read as done; the
    detector dropped every one. Fixing it took candidates from 12 to 35.
  * 2026-08-25 -- `promoted: "no -- CORRECTED ..."` counted as promoted, because the check
    was an exact match against ("no","false","",0). Writing down WHY a rule was unpromoted
    was the act that hid it: 11 rules at >=2 fires silently left the backlog, three at 5.

Before this file the module had no suite of its own; its 47/125 came from unrelated tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import scan_promotion_candidates as spc  # noqa: E402


def fm(**kw) -> str:
    meta = "\n".join(f"  {k}: {v}" for k, v in kw.items())
    return f"---\nname: r\ndescription: d\nmetadata:\n{meta}\n---\n\nbody\n"


# --- parse_frontmatter ------------------------------------------------------

def test_no_frontmatter_yields_an_empty_dict():
    assert spc.parse_frontmatter("just a body\n") == {}


def test_frontmatter_must_start_at_the_top_of_the_file():
    assert spc.parse_frontmatter("preamble\n---\nname: r\n---\n") == {}


def test_metadata_keys_are_flattened_alongside_top_level_keys():
    got = spc.parse_frontmatter(fm(occurrences=3, promoted="no"))
    assert got["name"] == "r"
    assert got["occurrences"] == "3"
    assert got["promoted"] == "no"


def test_quoted_values_have_their_quotes_stripped():
    assert spc.parse_frontmatter(fm(reopen_gate='"3rd fire"'))["reopen_gate"] == "3rd fire"


def test_blank_and_non_key_lines_are_skipped():
    assert spc.parse_frontmatter("---\nname: r\n\n- a bullet\nvalue\n---\n")["name"] == "r"


def test_a_later_top_level_key_ends_the_metadata_block():
    parsed = spc.parse_frontmatter(
        "---\nmetadata:\n  occurrences: 2\ntop: yes\n---\n")
    assert parsed["occurrences"] == "2"
    assert parsed["top"] == "yes"


# --- _is_scannable ----------------------------------------------------------

@pytest.mark.parametrize("name", [
    "MEMORY.md", "MEMORY.backup.md", "archive-2026-05.md", "archived-x.md",
    "audit-2026-07-07.md", "promotion-backlog-2026-07.md", "index-tools.md",
])
def test_non_rule_files_are_not_scanned(name):
    assert spc._is_scannable(name) is False


@pytest.mark.parametrize("name", [
    "feedback_x.md", "reference_y.md", "project_z.md", "user_a.md",
])
def test_rule_files_are_scanned(name):
    assert spc._is_scannable(name) is True


# --- is_promoted: the two documented incidents ------------------------------

@pytest.mark.parametrize("value", [
    "partial", "partial -- hook still missing", "PARTIAL -- x",
])
def test_partial_is_not_promoted(value):
    """2026-08-13: counting partial as promoted hid the exact gap it was invented to show."""
    assert spc.is_promoted({"promoted": value}) is False


@pytest.mark.parametrize("value", [
    "no", "No", "false", "0", "",
    "no -- CORRECTED 2026-08-25, promotion claim refuted",
    "not promoted", "not yet -- blocked on the detector",
])
def test_an_annotated_no_still_reads_as_unpromoted(value):
    """2026-08-25: an exact match let `no -- <reason>` read as a promotion tier. Writing
    down WHY a rule was unpromoted was the act that removed it from the backlog."""
    assert spc.is_promoted({"promoted": value}) is False


def test_a_missing_promoted_key_defaults_to_unpromoted():
    assert spc.is_promoted({}) is False


@pytest.mark.parametrize("value", ["skill", "hook", "principle", "hard-rule",
                                   "yes -- hook, 2026-08-19"])
def test_a_real_tier_reads_as_promoted(value):
    assert spc.is_promoted({"promoted": value}) is True


@pytest.mark.parametrize("value", ["none", "notation"])
def test_word_boundary_keeps_lookalike_tiers_promoted(value):
    """`\\b` is load-bearing: without it, any tier starting with 'no' would be swallowed
    and the rule would sit in the backlog forever."""
    assert spc.is_promoted({"promoted": value}) is True


def test_partially_promoted_is_detected_separately():
    assert spc.is_partially_promoted({"promoted": "partial -- x"}) is True
    assert spc.is_partially_promoted({"promoted": "skill"}) is False


# --- is_terminal: fails open ------------------------------------------------

@pytest.mark.parametrize("value", ["true", "yes", "1", "TRUE"])
def test_an_explicit_mark_suppresses(value):
    assert spc.is_terminal({"terminal": value}) is True


@pytest.mark.parametrize("value", ["ture", "maybe", "", "no", "0"])
def test_anything_else_leaves_the_rule_a_candidate(value):
    """Fails OPEN on purpose: a typo must not silently suppress a rule forever."""
    assert spc.is_terminal({"terminal": value}) is False


def test_a_missing_terminal_key_leaves_the_rule_a_candidate():
    assert spc.is_terminal({}) is False


# --- coverage: the honest denominator ---------------------------------------

def _write(d: Path, name: str, body: str):
    (d / name).write_text(body, encoding="utf-8")


def test_coverage_denominator_counts_files_without_the_schema(tmp_path):
    """The whole point of iter_candidate_files: any ratio computed inside the filtered
    loop is 100% by construction, because the filter pre-selects its own numerator."""
    _write(tmp_path, "feedback_a.md", fm(occurrences=2, promoted="no"))
    _write(tmp_path, "feedback_b.md", "---\nname: b\n---\n\nno schema\n")
    cov = spc.schema_coverage(tmp_path)
    assert cov["files"] == 2
    assert cov["visible"] == 1
    assert cov["invisible"] == 1
    assert cov["pct"] == 50.0


def test_coverage_splits_feedback_rules_from_everything_else(tmp_path):
    """A blended percentage hides that the promotion signal only applies to feedback."""
    _write(tmp_path, "feedback_a.md", fm(occurrences=2))
    _write(tmp_path, "reference_b.md", "---\nname: b\n---\n\nfact\n")
    cov = spc.schema_coverage(tmp_path)
    assert cov["feedback_rules"] == 1
    assert cov["feedback_visible"] == 1
    assert cov["feedback_pct"] == 100.0
    assert cov["pct"] == 50.0, "corpus-wide coverage must not be reported as the feedback one"


def test_index_and_archive_files_are_outside_the_denominator(tmp_path):
    _write(tmp_path, "feedback_a.md", fm(occurrences=1))
    _write(tmp_path, "index-tools.md", "---\nname: i\n---\n")
    _write(tmp_path, "archive-2026-05.md", "---\nname: a\n---\n")
    assert spc.schema_coverage(tmp_path)["files"] == 1


def test_coverage_on_an_empty_directory_does_not_divide_by_zero(tmp_path):
    cov = spc.schema_coverage(tmp_path)
    assert cov["files"] == 0
    assert cov["pct"] == 0.0
    assert cov["feedback_pct"] == 0.0


def test_load_memory_files_drops_files_without_occurrences(tmp_path):
    _write(tmp_path, "feedback_a.md", fm(occurrences=2))
    _write(tmp_path, "feedback_b.md", "---\nname: b\n---\n")
    assert [p.name for p, _ in spc.load_memory_files(tmp_path)] == ["feedback_a.md"]


# --- frozen shards and size budgets -----------------------------------------

def test_a_frozen_shard_is_recognised(tmp_path):
    p = tmp_path / "index-tools.md"
    p.write_text("# shard\n\n> FROZEN 2026-08-25. ARCHIVED, NOT MAINTAINED.\n", encoding="utf-8")
    assert spc.is_frozen_shard(p) is True


def test_an_ordinary_shard_is_not_frozen(tmp_path):
    p = tmp_path / "index-tools.md"
    p.write_text("# shard\n\nlive content\n", encoding="utf-8")
    assert spc.is_frozen_shard(p) is False


def test_a_frozen_shard_is_excluded_from_the_size_budget(tmp_path):
    """Frozen shards load nowhere, so their bytes are not a per-session tax; counting
    them buries the one file whose size still costs something every session."""
    big = "x" * (spc.SHARD_LIMIT_BYTES + 100)
    (tmp_path / "index-frozen.md").write_text(
        "> FROZEN 2026-08-25\n" + big, encoding="utf-8")
    (tmp_path / "index-live.md").write_text(big, encoding="utf-8")
    names = [d["file"] for d in spc.oversized_context_files(tmp_path, tmp_path)]
    assert "index-live.md" in names
    assert "index-frozen.md" not in names


def test_a_file_under_budget_is_not_reported(tmp_path):
    (tmp_path / "index-small.md").write_text("tiny", encoding="utf-8")
    assert spc.oversized_context_files(tmp_path, tmp_path) == []


def test_oversized_files_are_ranked_worst_first(tmp_path):
    (tmp_path / "index-a.md").write_text("x" * (spc.SHARD_LIMIT_BYTES + 10), encoding="utf-8")
    (tmp_path / "index-b.md").write_text("x" * (spc.SHARD_LIMIT_BYTES + 5000), encoding="utf-8")
    out = spc.oversized_context_files(tmp_path, tmp_path)
    assert [d["file"] for d in out] == ["index-b.md", "index-a.md"]
    assert out[0]["over_by"] > out[1]["over_by"]


def test_claude_md_gets_its_own_larger_budget(tmp_path):
    """A shard-sized CLAUDE.md is fine; using the shard limit for it would cry wolf."""
    (tmp_path / "CLAUDE.md").write_text(
        "x" * (spc.SHARD_LIMIT_BYTES + 100), encoding="utf-8")
    assert spc.oversized_context_files(tmp_path, tmp_path) == []
    (tmp_path / "CLAUDE.md").write_text(
        "x" * (spc.CLAUDE_MD_LIMIT_BYTES + 100), encoding="utf-8")
    assert [d["file"] for d in spc.oversized_context_files(tmp_path, tmp_path)] == ["CLAUDE.md"]


# --- the assertion that has to fail first -----------------------------------

def test_the_module_under_test_is_the_real_one():
    assert Path(spc.__file__).resolve() == (TOOLS / "scan_promotion_candidates.py").resolve()
    assert spc.SHARD_LIMIT_BYTES > 0 and spc.CLAUDE_MD_LIMIT_BYTES > spc.SHARD_LIMIT_BYTES


# --- parse_frontmatter: the block-header edge -------------------------------

def test_a_metadata_block_header_is_not_itself_a_key():
    """`metadata:` with no value opens the block; emitting it as a key would put an
    empty 'metadata' entry into every parsed rule."""
    parsed = spc.parse_frontmatter("---\nname: r\nmetadata:\n  occurrences: 2\n---\n")
    assert "metadata" not in parsed
    assert parsed["occurrences"] == "2"


def test_metadata_with_an_inline_value_is_kept_as_an_ordinary_key():
    parsed = spc.parse_frontmatter("---\nmetadata: inline-value\n---\n")
    assert parsed["metadata"] == "inline-value"


def test_with_last_cited_is_counted_independently_of_the_schema(tmp_path):
    """`last_cited` drives demotion and applies to every rule type, so its count must
    not be folded into the promotion-signal coverage number."""
    _write(tmp_path, "feedback_a.md", fm(occurrences=2, last_cited="2026-01-01"))
    _write(tmp_path, "feedback_b.md", fm(occurrences=2))
    cov = spc.schema_coverage(tmp_path)
    assert cov["with_last_cited"] == 1
    assert cov["visible"] == 2


def test_an_undecodable_file_is_skipped_not_fatal(tmp_path):
    """One corrupt byte sequence must not take down the whole scan."""
    _write(tmp_path, "feedback_ok.md", fm(occurrences=2))
    (tmp_path / "feedback_bad.md").write_bytes(b"\xff\xfe\x00binary")
    assert spc.schema_coverage(tmp_path)["files"] == 1


# --- main: the end-to-end signals -------------------------------------------

import json as _json           # noqa: E402
import subprocess as _sp       # noqa: E402
from datetime import date, timedelta  # noqa: E402

TOOL = TOOLS / "scan_promotion_candidates.py"


def _scan(memory_dir: Path, *extra) -> dict:
    r = _sp.run([sys.executable, str(TOOL), "--memory-dir", str(memory_dir), *extra],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
                env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout)


def test_a_recurring_unpromoted_rule_is_a_candidate(tmp_path):
    _write(tmp_path, "feedback_a.md", fm(occurrences=2, promoted="no"))
    out = _scan(tmp_path)
    assert out["promotion_count"] == 1
    assert out["promotion_candidates"][0]["file"] == "feedback_a.md"


def test_a_single_fire_is_not_yet_a_candidate(tmp_path):
    _write(tmp_path, "feedback_a.md", fm(occurrences=1, promoted="no"))
    assert _scan(tmp_path)["promotion_count"] == 0


def test_a_promoted_rule_is_not_a_candidate(tmp_path):
    _write(tmp_path, "feedback_a.md", fm(occurrences=5, promoted="skill"))
    assert _scan(tmp_path)["promotion_count"] == 0


def test_a_partial_rule_is_a_candidate_and_is_flagged_as_partial(tmp_path):
    """Surfaced in the same list because the remaining gap is real work, flagged so the
    backlog can distinguish finishing a half-built one from starting a new one."""
    _write(tmp_path, "feedback_a.md", fm(occurrences=3, promoted="partial -- hook missing"))
    out = _scan(tmp_path)
    assert out["promotion_count"] == 1
    assert out["promotion_candidates"][0]["partial"] is True


def test_a_terminal_rule_is_excluded_from_promotion_but_still_reported(tmp_path):
    """Suppression is never silent: a filtered row that vanishes is indistinguishable
    from one the scanner lost."""
    _write(tmp_path, "feedback_a.md",
           fm(occurrences=4, promoted="no", terminal="true", terminal_reason="behavioral"))
    out = _scan(tmp_path)
    assert out["promotion_count"] == 0
    assert out["terminal_count"] == 1
    assert out["terminal_rules"][0]["terminal_reason"] == "behavioral"


def test_non_numeric_occurrences_is_treated_as_zero_not_a_crash(tmp_path):
    _write(tmp_path, "feedback_a.md", fm(occurrences="many", promoted="no"))
    assert _scan(tmp_path)["promotion_count"] == 0


def test_a_stale_last_cited_is_a_demotion_candidate(tmp_path):
    old = (date.today() - timedelta(days=90)).isoformat()
    _write(tmp_path, "feedback_a.md", fm(occurrences=1, last_cited=old))
    out = _scan(tmp_path)
    assert out["demotion_count"] == 1
    assert out["demotion_candidates"][0]["age_days"] >= 90


def test_a_recent_last_cited_is_not_a_demotion_candidate(tmp_path):
    recent = (date.today() - timedelta(days=3)).isoformat()
    _write(tmp_path, "feedback_a.md", fm(occurrences=1, last_cited=recent))
    assert _scan(tmp_path)["demotion_count"] == 0


def test_the_stale_threshold_is_honoured(tmp_path):
    d = (date.today() - timedelta(days=30)).isoformat()
    _write(tmp_path, "feedback_a.md", fm(occurrences=1, last_cited=d))
    assert _scan(tmp_path, "--stale-days", "60")["demotion_count"] == 0
    assert _scan(tmp_path, "--stale-days", "10")["demotion_count"] == 1


def test_a_malformed_date_is_skipped_rather_than_guessed(tmp_path):
    _write(tmp_path, "feedback_a.md", fm(occurrences=1, last_cited="not-a-date"))
    out = _scan(tmp_path)
    assert out["demotion_count"] == 0
    assert out["status"] == "ok"


def test_a_rule_never_cited_gets_its_own_bucket(tmp_path):
    """Visible to promotion, invisible to demotion -- reported rather than silently
    skipped, because 'never cited since backfill' is itself demotion evidence."""
    _write(tmp_path, "feedback_a.md", fm(occurrences=2, promoted="no"))
    out = _scan(tmp_path)
    assert out["never_cited_count"] == 1
    assert out["demotion_count"] == 0


def test_the_never_cited_sample_is_bounded(tmp_path):
    """~383 files right after backfill; dumping all of them buries the live signals."""
    for i in range(15):
        _write(tmp_path, f"feedback_{i}.md", fm(occurrences=1))
    out = _scan(tmp_path)
    assert out["never_cited_count"] == 15
    assert len(out["never_cited_sample"]) == 10


def test_needs_review_is_counted(tmp_path):
    """A backfilled `1` means 'unknown', not 'fired once'."""
    _write(tmp_path, "feedback_a.md", fm(occurrences=1, needs_review="true"))
    assert _scan(tmp_path)["needs_review_count"] == 1


def test_coverage_travels_with_the_candidate_list(tmp_path):
    """An empty candidate list is only interpretable next to coverage: on 2026-08-13 a
    1-candidate result against 3.5% coverage read as an all-clear and was an empty scan."""
    _write(tmp_path, "feedback_a.md", fm(occurrences=2, promoted="no"))
    _write(tmp_path, "feedback_b.md", "---\nname: b\n---\n")
    out = _scan(tmp_path)
    assert out["schema_coverage"]["pct"] == 50.0
    assert out["schema_coverage"]["invisible"] == 1


def test_an_empty_memory_dir_reports_ok_with_zero_counts(tmp_path):
    out = _scan(tmp_path)
    assert out["status"] == "ok"
    assert out["promotion_count"] == 0 and out["demotion_count"] == 0
