"""Tests for tools/pipeline_reconcile.py.

ALL company names here are synthetic. `tests/` is tracked and therefore public
(`CLAUDE.md` Public-repo PII gate: "a public artifact is any file git does not ignore"),
so the two real fires that motivated this module are replayed as SHAPES, never as names:

  - the punctuation shape   ("ClosedCo" vs "ClosedCo.")   -> must resolve, normalized-exact
  - the containment shape   ("Zeta" vs "Zetab")           -> must NOT resolve, ambiguous

A live-data smoke run belongs in the gitignored owner layer, not here, and no test pins a
row count from the real pipeline because that number drifts every day.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from pipeline_reconcile import (  # noqa: E402
    STATE_ACTIVE, STATE_AMBIGUOUS, STATE_CLEAR, STATE_MIXED, STATE_TERMINAL,
    PipelineUnreadable, render, resolve,
)

HEADER = (
    "| Company | Role | Stage | Updated | Next | CV | Notes | URL |\n"
    "|---|---|---|---|---|---|---|---|\n"
)


def _pipeline(tmp_path: Path, rows: str) -> Path:
    p = tmp_path / "job-pipeline.md"
    p.write_text("# Pipeline\n\n" + HEADER + rows, encoding="utf-8")
    return p


def _row(company, role, stage, updated="2026-01-01"):
    return f"| {company} | {role} | {stage} | {updated} | - | - | - | http://x |\n"


# --------------------------------------------------------------------------
# Identity: normalized-exact resolves; containment and similarity never do.
# --------------------------------------------------------------------------

def test_punctuation_variant_resolves_via_normalized_exact(tmp_path):
    """The shape of the real miss: a trailing period must not hide a closed row."""
    p = _pipeline(tmp_path, _row("ClosedCo.", "Strategist", "Rejected"))
    out = resolve(["ClosedCo"], p)
    assert len(out[STATE_TERMINAL]) == 1
    assert out[STATE_TERMINAL][0]["rows"][0]["stage"] == "Rejected"
    assert not out[STATE_AMBIGUOUS]


def test_containment_collision_is_ambiguous_never_closed(tmp_path):
    """Kills a containment identity tier.

    `zeta` is a substring of `zetab`. If containment ever resolves automatically, this
    name lands in a terminal bucket and a real lead is suppressed. It must land in
    ambiguous, where a human sees it.
    """
    p = _pipeline(tmp_path, _row("Zetab", "Strategist", "Rejected"))
    out = resolve(["Zeta"], p)
    assert out[STATE_TERMINAL] == [], "containment must not resolve to a closed match"
    assert out[STATE_ACTIVE] == []
    assert len(out[STATE_AMBIGUOUS]) == 1
    cand = out[STATE_AMBIGUOUS][0]["candidates"][0]
    assert cand["containment"] is True
    assert cand["pipeline_company"] == "Zetab"


def test_high_similarity_without_containment_is_also_ambiguous(tmp_path):
    p = _pipeline(tmp_path, _row("Acmee", "Strategist", "Withdrawn"))
    out = resolve(["Acmez"], p)
    assert out[STATE_TERMINAL] == []
    assert len(out[STATE_AMBIGUOUS]) == 1


def test_short_names_do_not_generate_similarity_noise(tmp_path):
    """Below the length floor, a near-miss is not reported at all.

    The query must be a name that WOULD produce a candidate if the floor were removed,
    or the test cannot tell the floor from its absence. `zet` is contained in `zeta`, so
    containment would fire; the floor is the only thing suppressing it. An earlier
    version used two unrelated 2-char names that scored 0.5 and produced no candidate
    either way, and the floor mutant survived.
    """
    p = _pipeline(tmp_path, _row("Zeta", "Strategist", "Rejected"))
    out = resolve(["Zet"], p)
    assert out[STATE_AMBIGUOUS] == [], "the length floor must suppress this candidate"
    assert len(out[STATE_CLEAR]) == 1


def test_a_genuinely_new_name_is_clear(tmp_path):
    p = _pipeline(tmp_path, _row("ClosedCo", "Strategist", "Rejected"))
    out = resolve(["Unrelated Industries"], p)
    assert len(out[STATE_CLEAR]) == 1
    assert out[STATE_TERMINAL] == []


# --------------------------------------------------------------------------
# Row granularity: never synthesize one status for a company.
# --------------------------------------------------------------------------

def test_active_only_company_is_active_not_terminal(tmp_path):
    p = _pipeline(tmp_path, _row("LiveCo", "Strategist", "Applied"))
    out = resolve(["LiveCo"], p)
    assert len(out[STATE_ACTIVE]) == 1
    assert out[STATE_TERMINAL] == []


def test_mixed_history_is_its_own_state_and_keeps_every_row(tmp_path):
    """A company with a closed row AND a live row must not read as closed.

    Zero companies in the live pipeline had this shape when the module was written, so
    this is the regression test for a class that has not fired yet.
    """
    rows = (_row("MixedCo", "Old Role", "Rejected", "2026-01-01")
            + _row("MixedCo", "New Role", "Applied", "2026-06-01"))
    p = _pipeline(tmp_path, rows)
    out = resolve(["MixedCo"], p)
    assert out[STATE_TERMINAL] == []
    assert out[STATE_ACTIVE] == []
    assert len(out[STATE_MIXED]) == 1
    got = out[STATE_MIXED][0]["rows"]
    assert len(got) == 2, "every matched row must be returned, not collapsed"
    assert {r["terminal"] for r in got} == {True, False}


def test_multiple_terminal_rows_return_all_of_them(tmp_path):
    rows = (_row("ClosedCo", "Role A", "Rejected")
            + _row("ClosedCo", "Role B", "Withdrawn"))
    p = _pipeline(tmp_path, rows)
    out = resolve(["ClosedCo"], p)
    assert len(out[STATE_TERMINAL]) == 1
    assert len(out[STATE_TERMINAL][0]["rows"]) == 2


# --------------------------------------------------------------------------
# Fail loud. A clean-looking empty result from a broken read is the defect.
# --------------------------------------------------------------------------

def test_missing_file_raises_rather_than_reporting_clean(tmp_path):
    with pytest.raises(PipelineUnreadable):
        resolve(["AnyCo"], tmp_path / "does-not-exist.md")


def test_empty_file_raises_ON_THE_EMPTY_BRANCH(tmp_path):
    """Matches the MESSAGE, not just the type.

    Asserting only `PipelineUnreadable` lets the empty-content guard be deleted: an empty
    file also parses to zero rows and raises from the later guard, so the test passes
    against code with the first check removed. Mutation testing caught exactly that.
    """
    p = tmp_path / "job-pipeline.md"
    p.write_text("   \n\n", encoding="utf-8")
    with pytest.raises(PipelineUnreadable, match="is empty"):
        resolve(["AnyCo"], p)


def test_a_table_that_is_not_the_pipeline_raises_rather_than_reporting_clean(tmp_path):
    """The header guard's real job, and the only input that isolates it.

    `parse_all_rows` accepts ANY line starting with `|`, so an unrelated markdown table
    parses into rows. Without the header check those rows become a confident all-clear
    against a file that is not the pipeline. A prose-only file cannot catch this because
    it yields zero rows and trips the later guard instead.
    """
    p = tmp_path / "job-pipeline.md"
    p.write_text(
        "# Shopping\n\n| Item | Qty | Price |\n|---|---|---|\n| Apples | 3 | 2.00 |\n",
        encoding="utf-8")
    with pytest.raises(PipelineUnreadable, match="no pipeline header"):
        resolve(["Apples"], p)


def test_header_present_but_no_rows_raises(tmp_path):
    p = _pipeline(tmp_path, "")
    with pytest.raises(PipelineUnreadable, match="zero rows"):
        resolve(["AnyCo"], p)


def test_a_clean_pipeline_reports_no_parse_errors(tmp_path):
    """Pins the separator/header filtering in the parse_errors scan.

    Without it, the `|---|` separator and the header row are counted as malformed and
    every clean run emits a spurious warning.
    """
    p = _pipeline(tmp_path, _row("ClosedCo", "R", "Rejected"))
    assert resolve(["ClosedCo"], p)["parse_errors"] == []


def test_a_row_with_a_blank_company_is_not_indexed(tmp_path):
    """Without the blank-key skip, an empty company name becomes a matchable entry."""
    p = _pipeline(tmp_path, _row("ClosedCo", "R", "Rejected") + _row("", "Orphan", "Applied"))
    out = resolve(["ClosedCo"], p)
    assert len(out[STATE_TERMINAL]) == 1
    assert len(out[STATE_TERMINAL][0]["rows"]) == 1, "a blank-company row must not attach"


def test_a_blank_company_row_never_becomes_a_similarity_candidate(tmp_path):
    """Pins the END-TO-END property, and is honest that it does not kill the local guard.

    The empty string is a substring of every name, so a blank-company row reaching the
    index would attach a phantom no-name candidate to every ambiguous result. That cannot
    happen today, and NOT because of the `if not key` guard in `_index`: `parse_all_rows`
    already drops any row whose first column is empty (`not cols[0] -> continue`, verified
    2026-09-14 -- a blank-company table parses to zero rows). The guard is therefore
    unreachable and its mutant survives; it is allowlisted as defence against a future
    parser change rather than deleted.

    This test still earns its place: it pins the composed behaviour, so if the upstream
    parser ever starts admitting blank-company rows, the phantom candidate shows up here.
    """
    p = _pipeline(tmp_path, _row("Zetab", "R", "Rejected") + _row("", "Orphan", "Applied"))
    out = resolve(["Zeta"], p)
    assert len(out[STATE_AMBIGUOUS]) == 1
    names = [c["pipeline_company"] for c in out[STATE_AMBIGUOUS][0]["candidates"]]
    assert names == ["Zetab"], f"a phantom blank-company candidate leaked in: {names}"


def test_malformed_row_is_reported_not_silently_dropped(tmp_path):
    """parse_all_rows skips any row with <3 columns. That silence is the risk."""
    p = _pipeline(tmp_path, _row("ClosedCo", "Strategist", "Rejected") + "| Truncated |\n")
    out = resolve(["ClosedCo"], p)
    assert out["parse_errors"], "a dropped table row must surface"
    assert "Truncated" in out["parse_errors"][0]


def test_render_surfaces_the_parse_warning(tmp_path):
    p = _pipeline(tmp_path, _row("ClosedCo", "Strategist", "Rejected") + "| Truncated |\n")
    text = render(resolve(["ClosedCo"], p))
    assert "malformed pipeline row" in text


# --------------------------------------------------------------------------
# Conservation and determinism.
# --------------------------------------------------------------------------

def test_every_input_name_lands_in_exactly_one_bucket(tmp_path):
    rows = (_row("ClosedCo", "R", "Rejected")
            + _row("LiveCo", "R", "Applied")
            + _row("Zetab", "R", "Withdrawn"))
    p = _pipeline(tmp_path, rows)
    names = ["ClosedCo", "LiveCo", "Zeta", "Brand New Co", "ClosedCo"]
    out = resolve(names, p)
    placed = sum(len(out[s]) for s in
                 (STATE_ACTIVE, STATE_TERMINAL, STATE_MIXED, STATE_AMBIGUOUS, STATE_CLEAR))
    assert placed + len(out["blank_names"]) == len(names)
    assert out["names_in"] == len(names)


def test_duplicate_input_names_are_both_reported(tmp_path):
    p = _pipeline(tmp_path, _row("ClosedCo", "R", "Rejected"))
    out = resolve(["ClosedCo", "ClosedCo"], p)
    assert len(out[STATE_TERMINAL]) == 2, "a repeated name must not be silently deduped"


def test_blank_and_punctuation_only_names_are_tracked_not_dropped(tmp_path):
    p = _pipeline(tmp_path, _row("ClosedCo", "R", "Rejected"))
    out = resolve(["", "   ", "!!!"], p)
    assert len(out["blank_names"]) == 3
    assert out[STATE_CLEAR] == []


def test_repeated_runs_are_byte_identical(tmp_path):
    rows = _row("ClosedCo", "R", "Rejected") + _row("Zetab", "R", "Applied")
    p = _pipeline(tmp_path, rows)
    names = ["ClosedCo", "Zeta", "Other"]
    a = json.dumps(resolve(names, p), sort_keys=True)
    b = json.dumps(resolve(names, p), sort_keys=True)
    assert a == b


# --------------------------------------------------------------------------
# Rendered artifact: the exclusion must be readable, and must not read as a verdict.
# --------------------------------------------------------------------------

def test_render_labels_terminal_as_reconsider_not_as_excluded(tmp_path):
    p = _pipeline(tmp_path, _row("ClosedCo", "Strategist", "Rejected"))
    text = render(resolve(["ClosedCo"], p))
    assert "reconsider" in text.lower()
    assert "ClosedCo" in text and "Rejected" in text


def test_render_tells_mixed_history_not_to_be_excluded(tmp_path):
    rows = _row("MixedCo", "A", "Rejected") + _row("MixedCo", "B", "Applied")
    p = _pipeline(tmp_path, rows)
    text = render(resolve(["MixedCo"], p))
    assert "do NOT exclude" in text


def test_render_says_so_when_nothing_matches(tmp_path):
    p = _pipeline(tmp_path, _row("ClosedCo", "R", "Rejected"))
    text = render(resolve(["Totally Different"], p))
    assert "None of the" in text


def test_render_does_NOT_claim_clean_when_there_are_hits(tmp_path):
    """Pins the `not any_hit` branch.

    Without it the block prints both the matches and a line saying nothing matched, which
    is worse than either alone: a reader who skims to the summary line concludes clean.
    """
    p = _pipeline(tmp_path, _row("ClosedCo", "R", "Rejected"))
    text = render(resolve(["ClosedCo"], p))
    assert "None of the" not in text


def test_render_shows_the_ambiguous_block_with_its_reason(tmp_path):
    """The ambiguous section had NO render coverage; every mutant in it survived."""
    p = _pipeline(tmp_path, _row("Zetab", "R", "Rejected"))
    text = render(resolve(["Zeta"], p))
    assert "Similar names" in text
    assert "NOT resolved automatically" in text
    assert "Zetab" in text and "Zeta" in text
    assert "contains/contained" in text
    assert "None of the" not in text, "an ambiguous hit is a hit"


def test_render_shows_a_similarity_score_when_it_is_not_containment(tmp_path):
    p = _pipeline(tmp_path, _row("Acmee", "R", "Withdrawn"))
    text = render(resolve(["Acmez"], p))
    assert "score" in text


def test_render_separates_active_from_terminal(tmp_path):
    rows = _row("LiveCo", "R", "Applied") + _row("ClosedCo", "R", "Rejected")
    p = _pipeline(tmp_path, rows)
    text = render(resolve(["LiveCo", "ClosedCo"], p))
    assert "Currently pursuing" in text
    assert "reconsider" in text.lower()
    assert text.index("Currently pursuing") < text.index("Previously closed")


# --------------------------------------------------------------------------
# CLI contract.
# --------------------------------------------------------------------------

def _cli(args, cwd, stdin=""):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "pipeline_reconcile.py")] + args,
        capture_output=True, text=True, timeout=30, cwd=str(cwd),
        env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin:/usr/local/bin"},
        input=stdin)


def test_cli_exits_nonzero_when_a_name_is_already_in_the_pipeline(tmp_path):
    (tmp_path / "data").mkdir()
    _pipeline(tmp_path / "data", _row("ClosedCo", "R", "Rejected"))
    r = _cli(["--repo-root", str(tmp_path), "ClosedCo"], tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert json.loads(r.stdout)[STATE_TERMINAL]


def test_cli_exits_zero_on_a_clean_list(tmp_path):
    (tmp_path / "data").mkdir()
    _pipeline(tmp_path / "data", _row("ClosedCo", "R", "Rejected"))
    r = _cli(["--repo-root", str(tmp_path), "Brand New Co"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_exits_2_and_says_why_when_the_pipeline_is_unreadable(tmp_path):
    (tmp_path / "data").mkdir()
    r = _cli(["--repo-root", str(tmp_path), "AnyCo"], tmp_path)
    assert r.returncode == 2
    assert json.loads(r.stdout)["status"] == "error"


def test_cli_reads_names_from_stdin(tmp_path):
    (tmp_path / "data").mkdir()
    _pipeline(tmp_path / "data", _row("ClosedCo", "R", "Rejected"))
    r = _cli(["--repo-root", str(tmp_path)], tmp_path, stdin="ClosedCo\nOther\n")
    assert r.returncode == 1
    assert json.loads(r.stdout)["names_in"] == 2


def test_cli_with_no_names_at_all_errors_rather_than_reporting_clean(tmp_path):
    """Empty stdin and no argv is a caller bug, not an all-clear."""
    (tmp_path / "data").mkdir()
    _pipeline(tmp_path / "data", _row("ClosedCo", "R", "Rejected"))
    r = _cli(["--repo-root", str(tmp_path)], tmp_path, stdin="")
    assert r.returncode == 2
    assert json.loads(r.stdout)["status"] == "error"


def test_cli_markdown_format_emits_the_artifact_block_not_json(tmp_path):
    (tmp_path / "data").mkdir()
    _pipeline(tmp_path / "data", _row("ClosedCo", "R", "Rejected"))
    r = _cli(["--repo-root", str(tmp_path), "--format", "markdown", "ClosedCo"], tmp_path)
    assert r.returncode == 1
    assert r.stdout.lstrip().startswith("### Already in your pipeline")
    with pytest.raises(json.JSONDecodeError):
        json.loads(r.stdout)
