"""Tests for tools/sweep.py — the shared sweep helper.

Two memory rules hit their promotion gate on 2026-08-13 naming nearly the same
structural target, and this helper is the single fix for both:

  feedback_guard_must_hard_abort_on_empty_input (4 fires)
      -> an empty path list must ABORT, never print "no matches"
  feedback_sampled_check_needs_a_conservation_anchor (3 fires)
      -> every verdict carries its own denominator; a sampled check is not self-proving

Plus feedback_replace_all_substring_check (3 fires, already promoted): word-boundary
matching is the default, substring is opt-in and labelled.

The 2026-08-13 false-clean is reproduced as a fixture below
(test_empty_path_list_aborts_rather_than_reporting_clean).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "sweep.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))

import sweep as sweep_mod  # noqa: E402
from sweep import EmptyScopeError, sweep  # noqa: E402


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "a.md").write_text("the Ace of spades\nplaced second\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("nothing to see\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("# Ace appears here too\n", encoding="utf-8")
    return tmp_path


def run(*args, stdin: str | None = None) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, input=stdin,
    )
    return proc.returncode, json.loads(proc.stdout)


# --- Property 2: an empty scope is an error -------------------------------------------

def test_empty_path_list_aborts_rather_than_reporting_clean():
    """THE fixture. A scan over nothing printed 'no matches' and was read as a result."""
    with pytest.raises(EmptyScopeError):
        sweep([], "anything")


def test_cli_empty_scope_exits_two_and_never_says_clean():
    rc, report = run("--pattern", "anything")
    assert rc == 2
    assert report["status"] == "error"
    assert report["scanned"] == 0
    assert "clean" not in report, "an aborted sweep must not emit a verdict at all"


def test_all_paths_unreadable_is_also_an_empty_scope(tmp_path):
    """A non-empty list that reads zero files is the same defect wearing a disguise."""
    with pytest.raises(EmptyScopeError):
        sweep([tmp_path / "does-not-exist.md"], "x")
    rc, report = run("--pattern", "x", str(tmp_path / "nope.md"))
    assert rc == 2 and report["scanned"] == 0


def test_a_directory_in_the_path_list_does_not_count_as_scanned(corpus):
    result = sweep([corpus, corpus / "a.md"], "Ace")
    assert result["scanned"] == 1, "a directory must not inflate the denominator"
    assert result["given"] == 2
    assert len(result["skipped"]) == 1


# --- Property 3: the denominator travels with the verdict -----------------------------

def test_result_always_reports_its_own_denominator(corpus):
    result = sweep(sorted(corpus.glob("*")), "Ace")
    assert result["scanned"] == 3
    assert result["matched"] == 2
    assert set(result["paths"]) == {str(p) for p in sorted(corpus.glob("*"))}


def test_a_clean_result_still_reports_scanned(corpus):
    result = sweep(sorted(corpus.glob("*")), "zzz_absent_token")
    assert result["clean"] is True
    assert result["scanned"] == 3, "a negative result without a denominator is not a verdict"


# --- Property 4: word boundaries by default -------------------------------------------

def test_word_boundary_is_the_default(corpus):
    """'Ace' must not fire inside 'placed' — the ~190-false-hit shape from 2026-08-12."""
    result = sweep(sorted(corpus.glob("*")), "ace", ignore_case=True)
    hits = [m["text"] for m in result["matches"]]
    assert all("placed" not in h for h in hits)
    assert result["match_mode"] == "word-boundary"


def test_substring_is_opt_in_and_labelled(corpus):
    result = sweep(sorted(corpus.glob("*")), "ace", substring=True, ignore_case=True)
    assert any("placed" in m["text"] for m in result["matches"])
    assert result["match_mode"] == "substring", "the mode must be visible in the output"


def test_regex_mode_is_not_boundary_wrapped(corpus):
    result = sweep(sorted(corpus.glob("*")), r"pl.ced", regex=True)
    assert result["match_count"] == 1 and result["match_mode"] == "regex"


def test_substring_and_regex_together_are_refused(corpus):
    rc, report = run("--pattern", "x", "--substring", "--regex", str(corpus / "a.md"))
    assert rc == 2 and "mutually exclusive" in report["reason"]


# --- The positive control -------------------------------------------------------------

def test_control_failure_makes_a_negative_result_untrusted(corpus):
    """If a token known to be present does not match, the mechanism did not read the files."""
    result = sweep(sorted(corpus.glob("*")), "zzz_absent", control="zzz_also_absent")
    assert result["control_ok"] is False
    assert result["clean"] is False, "no clean verdict without a passing control"


def test_control_success_permits_a_clean_verdict(corpus):
    result = sweep(sorted(corpus.glob("*")), "zzz_absent", control="nothing")
    assert result["control_ok"] is True and result["clean"] is True


def test_cli_control_failure_exits_three(corpus):
    rc, report = run("--pattern", "zzz", "--control", "zzz_also_absent", str(corpus / "a.md"))
    assert rc == 3 and report["control_ok"] is False


# --- CLI plumbing ---------------------------------------------------------------------

def test_cli_reports_matches_with_exit_one(corpus):
    rc, report = run("--pattern", "Ace", str(corpus / "a.md"), str(corpus / "b.md"))
    assert rc == 1
    assert report["scanned"] == 2 and report["matched"] == 1


def test_cli_stdin_paths(corpus):
    paths = "\n".join(str(p) for p in sorted(corpus.glob("*")))
    rc, report = run("--pattern", "Ace", "--stdin-paths", stdin=paths)
    assert rc == 1 and report["scanned"] == 3


def test_binary_suffixes_are_skipped_not_counted(tmp_path):
    (tmp_path / "x.md").write_text("token here\n", encoding="utf-8")
    (tmp_path / "y.pyc").write_bytes(b"\x00\x01token\x02")
    result = sweep([tmp_path / "x.md", tmp_path / "y.pyc"], "token")
    assert result["scanned"] == 1 and result["matched"] == 1


# --- Guard: the helper's own invariants cannot be quietly removed ---------------------

def test_helper_exposes_the_four_properties_by_name():
    """A rename that drops one of these keys silently breaks every caller's verdict."""
    tmp = Path(__file__).parent
    result = sweep([tmp / "test_sweep_helper.py"], "sweep")
    for key in ("scanned", "matched", "paths", "match_mode", "clean", "given"):
        assert key in result, f"{key} missing — callers rely on it to state a denominator"


def test_boundaries_apply_only_at_word_edges(tmp_path):
    """A token ending in punctuation or a space must still be matchable.

    Wrapping both edges unconditionally made `"def "` require a NON-word character after
    the space — i.e. it could never match a function definition, so it silently failed as
    a positive control. Found by dogfooding this helper on its own repo, 2026-08-13.
    """
    f = tmp_path / "m.py"
    f.write_text("def thing():\n    return 1\n", encoding="utf-8")
    assert sweep([f], "def ")["match_count"] == 1
    assert sweep([f], "():")["match_count"] == 1
    # the word-edge case still holds
    assert sweep([f], "thin")["match_count"] == 0


def test_compile_pattern_escapes_regex_metacharacters():
    """A token containing '.' or '(' must not silently widen the match."""
    pat = sweep_mod.compile_pattern("a.c")
    assert pat.search("a.c") and not pat.search("abc")
