"""The cross-model verification gate: what qualifies, and what a waiver costs.

WHY THIS FILE EXISTS
--------------------
2026-09-02, Nick: "I want to make sure that for all of these important things we run
codex as a verification." That was recorded as a DECISION and, by the repo's own
enforcement rule, decisions converted at zero: the next day's three P0 fixes were
verified only because he asked again, in a message. Prose is not a tier.

2026-09-03, Nick, on why this must not become a checkbox: "As long as it's always
something I need to think about whether or not I want to have codex run a validation,
then that's how it doesn't become theater." So the waiver is deliberately a separate
conscious act (an env var on the push), never a default value in a file, and it is
RECORDED rather than silent.

The gate fires at PUSH, not commit: pushing is the outward act, it is where the PII
gate already makes him stop, and commits are far too frequent to carry a judgement call.
"""
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools import cross_model_gate as g  # noqa: E402


# --- what qualifies -----------------------------------------------------------

@pytest.mark.parametrize("path", [
    "output/analysis/090226-NEXT-SESSION-HANDOFF.md",
    "output/analysis/090226-role-pipeline-build-log.md",
    "output/analysis/090226-job-search-fire-hose-implementation-plan.md",
])
def test_governed_documents_qualify(path):
    """A handoff, a build log and a plan are exactly the artifacts a wrong call
    propagates from."""
    assert g.qualifies([(path, 5, 0)]).qualified is True


def test_a_large_code_change_qualifies_even_with_no_document():
    """The most valuable Codex run so far was on COMMITTED CODE, not a plan. A gate
    that only guards planning docs would have missed the drain fixes entirely."""
    v = g.qualifies([("tools/career_scanner/scanner.py", 200, 40)])
    assert v.qualified is True
    assert "tools/career_scanner/scanner.py" in v.reason


def test_a_wired_hook_qualifies_at_any_size():
    """A one-line change to a hook that BLOCKS can disable the guard entirely. Size is
    the wrong measure for these."""
    v = g.qualifies([("tools/check_public_pii.py", 1, 1)])
    assert v.qualified is True
    assert "hook" in v.reason.lower()


def test_a_small_ordinary_code_change_does_not_qualify():
    """Guard on the guard: if everything qualifies, the waiver becomes reflex and the
    gate is theatre. That is the failure mode this whole design is avoiding."""
    assert g.qualifies([("tools/friction_log.py", 4, 1)]).qualified is False


def test_docs_and_tests_alone_do_not_qualify():
    assert g.qualifies([("docs/usage.md", 300, 12),
                        ("tests/scripts/test_dedup.py", 90, 3)]).qualified is False


def test_many_small_code_edits_add_up():
    """Ten files at nine lines each is a large change wearing a disguise."""
    changes = [(f"tools/t{i}.py", 9, 2) for i in range(10)]
    assert g.qualifies(changes).qualified is True


# --- the ledger ---------------------------------------------------------------

def _row(tmp_path, **kw):
    row = {"recorded": "2026-09-03T10:00:00+00:00", "target": "the drain fixes",
           "report": "output/analysis/090326-codex-drain.md",
           "paths": ["tools/career_scanner/scanner.py"],
           "findings": [], "waived": False}
    row.update(kw)
    p = g.ledger_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def test_a_qualifying_push_with_no_verification_is_blocked(tmp_path):
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert v.blocked is True
    assert "codex_verify" in v.message


def test_a_verification_covering_the_changed_path_clears_the_gate(tmp_path):
    _row(tmp_path, paths=["tools/career_scanner/scanner.py"])
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert v.blocked is False


def test_a_verification_of_an_unrelated_path_does_not_clear_the_gate(tmp_path):
    """Otherwise one old run licenses every future push -- the shape that lets a stale
    green stand in for a real check."""
    _row(tmp_path, paths=["tools/todo_write.py"])
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert v.blocked is True


def test_a_verification_older_than_the_work_does_not_count(tmp_path):
    """Verifying code and THEN changing it is not verification."""
    _row(tmp_path, recorded="2026-09-01T10:00:00+00:00",
         paths=["tools/career_scanner/scanner.py"])
    since = time.mktime(time.strptime("2026-09-02", "%Y-%m-%d"))
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=since)
    assert v.blocked is True
    assert "older" in v.message.lower()


def test_a_non_qualifying_push_is_never_blocked(tmp_path):
    assert g.check(tmp_path, [("docs/usage.md", 3, 0)], since=0).blocked is False


def test_a_missing_ledger_blocks_rather_than_passes(tmp_path):
    """Fail CLOSED. A guard whose state file is absent must not read as satisfied --
    that is the false-zero defect wearing a different hat."""
    v = g.check(tmp_path / "fresh", [("tools/career_scanner/scanner.py", 200, 40)],
                since=0)
    assert v.blocked is True


def test_a_corrupt_ledger_blocks_rather_than_passes(tmp_path):
    p = g.ledger_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json\n", encoding="utf-8")
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert v.blocked is True


# --- the waiver ---------------------------------------------------------------

def test_the_waiver_is_recorded_not_silent(tmp_path):
    """Nick's condition for this not being theatre is that a waiver is a thing he
    thinks about. A waiver nobody can count afterwards is a thing he stops thinking
    about."""
    g.record_waiver(tmp_path, ["tools/career_scanner/scanner.py"], "hotfix, 1 line")
    rows = g.read_ledger(tmp_path)
    assert len(rows) == 1 and rows[0]["waived"] is True
    assert rows[0]["reason"] == "hotfix, 1 line"


def test_a_waiver_does_not_clear_a_LATER_push(tmp_path):
    """One waiver waives one push, not the habit."""
    g.record_waiver(tmp_path, ["tools/career_scanner/scanner.py"], "hotfix")
    later = time.time() + 60
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=later)
    assert v.blocked is True


def test_waivers_are_countable(tmp_path):
    """/standup surfaces the count. Drift you can see is drift you can correct."""
    for i in range(3):
        g.record_waiver(tmp_path, [f"tools/t{i}.py"], "no time")
    _row(tmp_path)
    assert g.waiver_count(tmp_path) == 3


# --- findings must not be write-only ------------------------------------------

def test_open_findings_are_reported(tmp_path):
    """THE 2026-09-02 DRAIN LESSON, applied to this tool before it can repeat it. A
    Codex report written to output/analysis/ and read by nobody is the same defect in
    a new costume: producer healthy, consumer pointing elsewhere, no error anywhere."""
    _row(tmp_path, findings=[{"id": "F1", "summary": "ack races a scan",
                              "disposition": None},
                             {"id": "F2", "summary": "unbounded pending",
                              "disposition": "parked: needs a design change"}])
    openf = g.open_findings(tmp_path)
    assert [f["id"] for f in openf] == ["F1"], "a dispositioned finding is not open"


def test_a_finding_with_an_empty_disposition_is_still_open(tmp_path):
    _row(tmp_path, findings=[{"id": "F1", "summary": "x", "disposition": "  "}])
    assert len(g.open_findings(tmp_path)) == 1


# --- the /standup consumer ----------------------------------------------------

def test_a_quiet_ledger_renders_nothing(tmp_path):
    """Same rule as the role queue: a daily '0 findings' line trains the reader to
    skip the section, which is how the original defect stayed invisible."""
    _row(tmp_path)
    assert g.summary(tmp_path) == ""


def test_open_findings_are_rendered_most_severe_first(tmp_path):
    _row(tmp_path, findings=[
        {"id": "F1", "severity": "P2", "summary": "cosmetic", "disposition": None},
        {"id": "F2", "severity": "P0", "summary": "data loss", "disposition": None},
    ])
    out = g.summary(tmp_path)
    assert out.index("data loss") < out.index("cosmetic")
    assert "2 open cross-model finding" in out


def test_waivers_are_surfaced_even_with_no_findings(tmp_path):
    """The count is the whole anti-theatre mechanism. Nick's condition was that
    skipping stays a conscious act; a waiver nobody ever sees is not one."""
    g.record_waiver(tmp_path, ["tools/x.py"], "no time")
    out = g.summary(tmp_path)
    assert "1 cross-model waiver" in out
    assert "routed around" in out


def test_the_standup_skill_actually_reads_the_ledger():
    """The 2026-09-02 defect restated: a healthy producer and a consumer pointing
    somewhere else, with no error anywhere. Pin the wiring, not just the function."""
    skill = REPO_ROOT / ".claude" / "skills" / "standup" / "SKILL.md"
    if not skill.is_file():
        pytest.skip("standup SKILL.md not present")
    text = skill.read_text(encoding="utf-8")
    assert "cross_model_gate" in text, (
        "/standup never reads the cross-model ledger, so findings reach nobody")
    assert "waiver" in text.lower()


# ---------------------------------------------------------------------------
# THE CLI IS THE ENFORCEMENT PATH. The git hook shells into main(); if main() is
# broken the hook exits 0 and the gate silently stops gating -- a guard that reports
# success while checking nothing, which is the worst failure shape this repo has.
# Everything above calls the functions directly and survives deleting main() entirely.
# ---------------------------------------------------------------------------

def _cli(tmp_path, numstat, env_extra=None, since="0"):
    import subprocess
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "cross_model_gate.py"),
         "--repo-root", str(tmp_path), "--since", since],
        input=numstat, capture_output=True, text=True, env=env)


def test_cli_exits_2_on_a_qualifying_unverified_push(tmp_path):
    """Exit 2 is the block. Exit 0 here would mean the hook waves it through."""
    r = _cli(tmp_path, "200\t40\ttools/career_scanner/scanner.py\n")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "BLOCKED" in r.stderr and "codex_verify.py" in r.stderr


def test_cli_exits_0_on_a_non_qualifying_push(tmp_path):
    r = _cli(tmp_path, "3\t0\tdocs/usage.md\n")
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_exits_0_and_RECORDS_when_waived(tmp_path):
    r = _cli(tmp_path, "200\t40\ttools/career_scanner/scanner.py\n",
             {"CODEX_VERIFY_WAIVE": "hotfix for a live outage"})
    assert r.returncode == 0
    assert "WAIVED" in r.stderr
    rows = g.read_ledger(tmp_path)
    assert len(rows) == 1 and rows[0]["reason"] == "hotfix for a live outage"


def test_cli_does_not_record_a_waiver_for_a_push_that_never_qualified(tmp_path):
    """Otherwise the waiver count inflates with pushes the gate would have passed
    anyway, and the number stops meaning 'times I skipped a real check'."""
    r = _cli(tmp_path, "3\t0\tdocs/usage.md\n", {"CODEX_VERIFY_WAIVE": "reflex"})
    assert r.returncode == 0
    assert g.read_ledger(tmp_path) == []


def test_cli_ignores_malformed_numstat_lines(tmp_path):
    """git emits binary files as `-\t-\tpath`. A crash here blocks every push."""
    r = _cli(tmp_path, "-\t-\tassets/logo.png\ngarbage\n\n3\t0\tdocs/usage.md\n")
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_counts_a_binary_file_as_zero_lines_not_a_crash(tmp_path):
    r = _cli(tmp_path, "-\t-\ttools/blob.py\n")
    assert r.returncode == 0


def test_cli_blocks_when_the_only_record_predates_the_work(tmp_path):
    import time
    _row(tmp_path, recorded="2026-09-01T10:00:00+00:00",
         paths=["tools/career_scanner/scanner.py"])
    since = str(time.mktime(time.strptime("2026-09-02", "%Y-%m-%d")))
    r = _cli(tmp_path, "200\t40\ttools/career_scanner/scanner.py\n", since=since)
    assert r.returncode == 2
    assert "older" in r.stderr.lower()


def test_the_installed_hook_actually_invokes_the_gate():
    """Pin the WIRING. tools/hooks/ is tracked but .git/hooks/ is not, and this repo
    has already shipped a tracked guard whose hook was never installed, leaving a fresh
    clone with no push-time gate at all."""
    hook = REPO_ROOT / "tools" / "hooks" / "pre-push"
    assert hook.is_file()
    text = hook.read_text(encoding="utf-8")
    assert "cross_model_gate.py" in text, "pre-push never calls the cross-model gate"
    assert "prepush_pii_guard.py" in text, "the PII gate was dropped from pre-push"
    assert "CODEX_VERIFY_WAIVE" in text


# --- ledger robustness (surviving-mutant coverage) ----------------------------

def test_blank_lines_in_the_ledger_are_skipped(tmp_path):
    p = g.ledger_path(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('\n\n{"waived": true}\n\n', encoding="utf-8")
    assert len(g.read_ledger(tmp_path)) == 1


def test_a_non_object_ledger_row_is_ignored_not_crashed_on(tmp_path):
    p = g.ledger_path(tmp_path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[1,2,3]\n"a string"\n{"waived": true}\n', encoding="utf-8")
    assert len(g.read_ledger(tmp_path)) == 1


def test_append_row_returns_where_it_wrote(tmp_path):
    assert g.append_row(tmp_path, {"waived": False}) == g.ledger_path(tmp_path)


def test_a_non_object_finding_is_ignored(tmp_path):
    _row(tmp_path, findings=["not a dict", {"id": "F1", "summary": "real",
                                            "disposition": None}])
    assert [f["id"] for f in g.open_findings(tmp_path)] == ["F1"]


def test_no_waiver_text_when_there_are_none(tmp_path):
    """Rendering '0 waivers' every morning is how a number stops being read."""
    _row(tmp_path, findings=[{"id": "F1", "summary": "x", "disposition": None}])
    assert "waiver" not in g.summary(tmp_path).lower()


@pytest.mark.parametrize("stamp", [None, 12345, "", "last Tuesday", []])
def test_an_unusable_recorded_timestamp_does_not_clear_the_gate_by_accident(tmp_path,
                                                                           stamp):
    """A row whose timestamp cannot be read must not be treated as fresh enough to
    license a push; nor may it crash the hook.

    THIS ASSERTION WAS `isinstance(v.blocked, bool)` UNTIL 2026-09-06, which passes
    whether the gate blocks or clears. The test carried the name of the guarantee and
    enforced nothing, while the code it named did in fact fail open: check() marked a
    row stale only `if recorded is not None`, so an unreadable stamp skipped the
    staleness branch entirely and fell through to clear. A vacuous assertion is worse
    than no test, because the name is read as coverage.
    """
    _row(tmp_path, recorded=stamp, paths=["tools/career_scanner/scanner.py"])
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert v.blocked is True
    assert "unreadable timestamp" in v.message


def test_a_naive_timestamp_is_read_as_utc(tmp_path):
    """The writer emits tz-aware, but a hand-edited row may not, and comparing a naive
    stamp against an epoch would raise."""
    _row(tmp_path, recorded="2026-09-03T10:00:00",
         paths=["tools/career_scanner/scanner.py"])
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert v.blocked is False


def test_a_naive_timestamp_is_read_as_UTC_not_local_time(tmp_path):
    """An 8-hour misreading is enough to accept a verification that predates the work,
    or reject one that does not. `since` here sits between the UTC and local readings
    of the same stamp, so only a UTC reading clears the gate."""
    import datetime as dt
    naive = "2026-09-03T12:00:00"
    utc_ts = dt.datetime(2026, 9, 3, 12, tzinfo=dt.timezone.utc).timestamp()
    local_ts = dt.datetime(2026, 9, 3, 12).timestamp()
    if abs(utc_ts - local_ts) < 3600:
        pytest.skip("machine runs in UTC; the two readings coincide")
    _row(tmp_path, recorded=naive, paths=["tools/career_scanner/scanner.py"])
    since = min(utc_ts, local_ts) + 60
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=since)
    assert v.blocked is (utc_ts < since)


def test_the_suggested_command_names_the_paths_that_TRIGGERED_the_block(tmp_path):
    """Offering the alphabetically-first file (a .gitignore edit) instead of the
    600-line code change makes the remedy read as boilerplate, and a remedy nobody
    follows is a gate nobody passes honestly."""
    v = g.check(tmp_path, [(".gitignore", 2, 0),
                           ("tools/codex_verify.py", 300, 4),
                           ("tools/cross_model_gate.py", 290, 10)], since=0)
    assert v.blocked is True
    line = [l for l in v.message.splitlines() if "--paths" in l][0]
    assert "tools/codex_verify.py" in line
    assert ".gitignore" not in line


def test_the_biggest_offender_is_named_first(tmp_path):
    v = g.check(tmp_path, [("tools/small.py", 10, 0),
                           ("tools/huge.py", 500, 0)], since=0)
    line = [l for l in v.message.splitlines() if "--paths" in l][0]
    assert line.index("tools/huge.py") < line.index("tools/small.py")


def test_the_hook_bounds_freshness_by_the_NEWEST_commit_pushed(tmp_path):
    """Codex's P0 on this gate, 2026-09-03, confirmed with real numbers: the hook was
    passing the BASE commit's time, so a verification recorded before the work was
    written still cleared it. The bound must be the newest commit in the range."""
    hook = (REPO_ROOT / "tools" / "hooks" / "pre-push").read_text(encoding="utf-8")
    assert 'git log -1 --format=%ct "$head"' in hook
    assert 'since_ref' not in hook, "the base-commit freshness bound is back"


def test_a_verification_predating_the_newest_work_is_rejected(tmp_path):
    """The property the hook fix exists to give. Stated at the gate, not just the shell,
    so it survives a rewrite of either."""
    import datetime as dt
    verified = dt.datetime(2026, 9, 3, 8, 0, tzinfo=dt.timezone.utc)
    work_committed = dt.datetime(2026, 9, 3, 9, 55, tzinfo=dt.timezone.utc)
    _row(tmp_path, recorded=verified.isoformat(),
         paths=["tools/career_scanner/scanner.py"])
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)],
                since=work_committed.timestamp())
    assert v.blocked is True, "code written after its verification was let through"


# --- F3, F5, F7: the three fail-OPEN defects, fixed 2026-09-06 ------------------
# Every failure mode this gate had pointed the same way: it CLEARED a push it should
# have blocked. None of them ever blocked wrongly. A gate whose only failure direction
# is permissive is not a gate, so these tests all assert on the blocking direction.

def _multi(tmp_path, *paths, **kw):
    """A push that qualifies on breadth, so `triggers` holds every path."""
    return [(p, 20, 5) for p in paths]


# F3 -- coverage is per-path, not per-push.

def test_a_row_covering_ONE_triggering_path_does_not_clear_the_others(tmp_path):
    """The whole F3 defect in one test: before 2026-09-06 a row naming a single path
    cleared an arbitrarily large push that the review never saw."""
    _row(tmp_path, paths=["tools/a.py"])
    changes = _multi(tmp_path, *[f"tools/{c}.py" for c in "abcdef"])
    v = g.check(tmp_path, changes, since=0)
    assert v.blocked is True
    assert "tools/b.py" in v.message
    assert "Uncovered" in v.message


def test_coverage_accumulates_across_rows(tmp_path):
    """Two focused reviews must together clear a push spanning both. Otherwise the fix
    for F3 forces one giant review, which is the payload size the wrapper caps."""
    names = [f"tools/{c}.py" for c in "abcdef"]
    _row(tmp_path, paths=names[:3])
    _row(tmp_path, paths=names[3:])
    assert g.check(tmp_path, _multi(tmp_path, *names), since=0).blocked is False


def test_only_the_TRIGGERING_paths_need_coverage(tmp_path):
    """A push that qualifies because of a hook must not also demand a review of the
    unrelated README that rode along."""
    _row(tmp_path, paths=["tools/check_thing.py"])
    v = g.check(tmp_path, [("tools/check_thing.py", 3, 1), ("README.md", 40, 2)],
                since=0)
    assert v.blocked is False


def test_a_sibling_repos_review_cannot_clear_a_code_push_here(tmp_path):
    """Runs 11 and 12 in the live ledger verified a peer repo's client deliverable.
    Under the old any-overlap rule such a row could license work here."""
    _row(tmp_path, paths=["output/example-client/deliverable.xlsx", "tools/a.py"])
    v = g.check(tmp_path, _multi(tmp_path, *[f"tools/{c}.py" for c in "abcdef"]),
                since=0)
    assert v.blocked is True


# F5 -- a run that did not complete verified nothing.

@pytest.mark.parametrize("row_kw,why", [
    ({"verified": False}, "explicitly marked unverified"),
    ({"rc": 1, "report_written": True}, "legacy row, non-zero exit"),
    ({"rc": 0, "report_written": False}, "legacy row, no report"),
])
def test_a_failed_run_does_not_clear_the_gate(tmp_path, row_kw, why):
    _row(tmp_path, paths=["tools/career_scanner/scanner.py"], **row_kw)
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert v.blocked is True, why
    assert "did not complete" in v.message


def test_a_successful_run_still_clears(tmp_path):
    _row(tmp_path, paths=["tools/career_scanner/scanner.py"], verified=True)
    assert g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)],
                   since=0).blocked is False


def test_a_waiver_is_not_treated_as_a_failed_run(tmp_path):
    """A waiver carries no rc and no report. It is a decision Nick typed, and the F5
    fix must not silently revoke it."""
    g.record_waiver(tmp_path, ["tools/career_scanner/scanner.py"], "deliberate")
    assert g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)],
                   since=0).blocked is False


def test_legacy_rows_without_the_verified_field_still_clear(tmp_path):
    """The twelve rows written before 2026-09-06 carry rc and report_written. The fix
    must not invalidate them wholesale and block Nick's next push."""
    _row(tmp_path, paths=["tools/career_scanner/scanner.py"], rc=0, report_written=True)
    assert g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)],
                   since=0).blocked is False


# F7 -- unknown ledger state is not verified state.

def test_a_corrupt_line_BESIDE_a_good_row_blocks(tmp_path):
    """The pre-existing corrupt-ledger test used a ledger with NO valid rows, so it
    passed for the wrong reason: it blocked on emptiness, not on corruption. With one
    good row present, the old code shredded the bad line silently and cleared."""
    _row(tmp_path, paths=["tools/career_scanner/scanner.py"])
    with g.ledger_path(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write("{half a row\n")
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert v.blocked is True
    assert "unreadable" in v.message


def test_a_valid_json_non_object_line_counts_as_corrupt(tmp_path):
    _row(tmp_path, paths=["tools/career_scanner/scanner.py"])
    with g.ledger_path(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write("[1, 2, 3]\n")
    assert g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)],
                   since=0).blocked is True


def test_read_ledger_with_health_separates_rows_from_damage(tmp_path):
    _row(tmp_path, paths=["tools/a.py"])
    with g.ledger_path(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write("{bad\n\n[1]\n")
    rows, corrupt = g.read_ledger_with_health(tmp_path)
    assert len(rows) == 1
    assert corrupt == 2


def test_read_ledger_still_returns_only_rows(tmp_path):
    """The wrapper's contract is unchanged for its other callers (summary,
    open_findings, waiver_count)."""
    _row(tmp_path, paths=["tools/a.py"])
    assert len(g.read_ledger(tmp_path)) == 1


# --- killing the 2026-09-06 mutation survivors ----------------------------------

def test_a_waiver_recorded_after_a_failed_run_still_clears(tmp_path):
    """The waiver branch must win on its own, not by accidentally falling through to
    the no-provenance default. A waiver row that also carries rc=1 is the case that
    separates the two, and without it the whole `if row.get("waived")` line can be
    deleted with the suite still green."""
    _row(tmp_path, paths=["tools/career_scanner/scanner.py"],
         waived=True, rc=1, report_written=False)
    assert g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)],
                   since=0).blocked is False


def test_the_block_message_names_only_the_conditions_that_actually_occurred(tmp_path):
    """A message that always lists stale, unusable and failed counts -- including
    zeros -- reads as diagnosis and is noise. Each clause must be conditional."""
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert v.blocked is True
    msg = v.message.lower()
    assert "older" not in msg
    assert "unreadable timestamp" not in msg
    assert "did not complete" not in msg


def test_a_stale_record_is_reported_without_the_other_two_clauses(tmp_path):
    _row(tmp_path, recorded="2026-09-01T10:00:00+00:00",
         paths=["tools/career_scanner/scanner.py"])
    since = time.mktime(time.strptime("2026-09-02", "%Y-%m-%d"))
    msg = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)],
                  since=since).message.lower()
    assert "older" in msg
    assert "unreadable timestamp" not in msg
    assert "did not complete" not in msg


def test_a_long_uncovered_list_is_truncated_with_a_count(tmp_path):
    names = [f"tools/f{i}.py" for i in range(8)]
    v = g.check(tmp_path, [(p, 20, 5) for p in names], since=0)
    assert v.blocked is True
    assert "(+2 more)" in v.message


def test_a_short_uncovered_list_carries_no_more_suffix(tmp_path):
    names = [f"tools/f{i}.py" for i in range(6)]
    v = g.check(tmp_path, [(p, 30, 5) for p in names], since=0)
    assert v.blocked is True
    assert "more)" not in v.message


# --- unlocated findings are surfaced, not silently equal ------------------------

def test_summary_marks_a_finding_with_no_location(tmp_path):
    _row(tmp_path, findings=[{"id": "F1", "severity": "P1", "summary": "vague",
                              "disposition": None}])
    out = g.summary(tmp_path)
    assert "(no location)" in out
    assert "1 with no location" in out


def test_summary_does_not_mark_a_located_finding(tmp_path):
    _row(tmp_path, findings=[{"id": "F1", "severity": "P1", "summary": "real",
                              "location": "tools/x.py:9", "disposition": None}])
    out = g.summary(tmp_path)
    assert "no location" not in out


def test_summary_counts_only_the_unlocated_ones(tmp_path):
    _row(tmp_path, findings=[
        {"id": "F1", "severity": "P0", "location": "a.py:1", "summary": "x",
         "disposition": None},
        {"id": "F2", "severity": "P1", "summary": "y", "disposition": None},
        {"id": "F3", "severity": "P2", "location": "  ", "summary": "z",
         "disposition": None}])
    assert "2 with no location" in g.summary(tmp_path)


# --- blast-radius tiers (2026-09-06) --------------------------------------------
# Nick chose the axis: "the latter is blast radius." Diff SIZE ranked a 400-line test
# refactor above a three-line Hard Rule edit. These tests pin the ordering that
# replaced it, and the bound that keeps the gate from becoming unpassable.

@pytest.mark.parametrize("path,tier", [
    ("CLAUDE.md", 3),
    ("memory/MEMORY.md", 3),
    (".claude/settings.json", 3),
    ("tools/check_public_pii.py", 3),
    ("tools/prepush_pii_guard.py", 3),
    ("output/example-client/090626-cover-letter.md", 3),
    (".claude/skills/apply/SKILL.md", 2),
    ("framework/style-guidelines.md", 2),
    ("tools/pipe_write.py", 2),
    ("output/analysis/090626-NEXT-HANDOFF.md", 2),
    ("tools/mutation_report.py", 1),
    ("tests/scripts/test_x.py", 1),
    ("README.md", 0),
    ("docs/usage.md", 0),
])
def test_blast_tier_of_a_path(path, tier):
    assert g.blast_tier([path])[0] == tier


def test_the_highest_tier_in_the_push_wins(the=None):
    """A push is as dangerous as its most dangerous file, never its average."""
    tier, triggers = g.blast_tier(["README.md", "tools/x.py", "CLAUDE.md"])
    assert tier == 3
    assert triggers == ["CLAUDE.md"]


def test_a_three_line_hard_rule_edit_outranks_a_large_test_refactor():
    """The whole point of the axis change, as one assertion."""
    rule = g.qualifies([("CLAUDE.md", 2, 1)])
    refactor = g.qualifies([("tests/scripts/test_x.py", 400, 380)])
    assert rule.tier > refactor.tier


def test_a_wired_hook_keeps_its_specific_reason_and_gets_tier_3():
    """The tier branch must not preempt the reason a reader acts on. Both matter:
    'changes a wired hook' says WHY, tier 3 says HOW MUCH."""
    v = g.qualifies([("tools/check_public_pii.py", 1, 1)])
    assert v.tier == 3
    assert "wired hook" in v.reason


# --- the WIRED_MODELS bound -----------------------------------------------------

def test_the_requirement_is_bounded_by_the_models_that_exist(monkeypatch):
    """Requiring two models while one is wired blocks every tier-2 push forever, and
    a gate that can only be waived is theatre. The tier still asks for two; the gate
    demands what is reachable."""
    monkeypatch.setattr(g, "WIRED_MODELS", ("codex",))
    assert g.TIER_MODELS[2] == 2
    assert g.models_required(2) == 1


def test_wiring_a_second_model_activates_the_real_requirement(monkeypatch):
    """The activation step is one line. This asserts it is actually one line."""
    monkeypatch.setattr(g, "WIRED_MODELS", ("codex", "gemini"))
    assert g.models_required(2) == 2
    assert g.models_required(3) == 2
    assert g.models_required(1) == 1
    assert g.models_required(0) == 0


def test_two_rows_from_the_SAME_model_are_one_verification(tmp_path, monkeypatch):
    """Independence is the product. A second pass by the same model reproduces the
    same blind spots, so it must not satisfy a two-model tier."""
    monkeypatch.setattr(g, "WIRED_MODELS", ("codex", "gemini"))
    _row(tmp_path, paths=["CLAUDE.md"], model="codex")
    _row(tmp_path, paths=["CLAUDE.md"], model="codex")
    v = g.check(tmp_path, [("CLAUDE.md", 3, 1)], since=0)
    assert v.blocked is True


def test_two_rows_from_DIFFERENT_models_clear_a_tier_3_path(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "WIRED_MODELS", ("codex", "gemini"))
    _row(tmp_path, paths=["CLAUDE.md"], model="codex")
    _row(tmp_path, paths=["CLAUDE.md"], model="gemini")
    assert g.check(tmp_path, [("CLAUDE.md", 3, 1)], since=0).blocked is False


def test_an_unstamped_row_is_attributed_to_the_only_model_there_was():
    """Every row before 2026-09-06 came from codex. Counting them as 'unknown' would
    let two legacy rows read as two independent models."""
    assert g.row_model({}) == "codex"
    assert g.row_model({"model": "  "}) == "codex"
    assert g.row_model({"model": "gemini"}) == "gemini"


def test_the_block_message_says_the_second_model_is_missing(tmp_path, monkeypatch):
    """A shortfall the operator cannot see is a shortfall they cannot fix."""
    monkeypatch.setattr(g, "WIRED_MODELS", ("codex",))
    v = g.check(tmp_path, [("CLAUDE.md", 3, 1)], since=0)
    assert v.blocked is True
    assert "only 1 is wired" in v.message
    assert "WIRED_MODELS" in v.message


def test_a_governed_document_keeps_its_own_reason(the=None):
    """Since governed docs are now ALSO tier 2 via BLAST_RULES, the dedicated branch
    can be deleted with qualification unchanged -- only the reason degrades from
    'changes a governed document' to a generic tier line. The reason is what the
    operator reads to decide what to verify, so it is load-bearing."""
    v = g.qualifies([("output/analysis/090626-NEXT-HANDOFF.md", 4, 1)])
    assert v.qualified is True
    assert "governed document" in v.reason
    assert v.tier == 2


def test_no_shortfall_note_when_every_wanted_model_is_wired(tmp_path, monkeypatch):
    """The counterpart to test_the_block_message_says_the_second_model_is_missing.
    Without it the condition can be made unconditional and the gate reports a
    shortfall that does not exist, which trains the reader to ignore the line."""
    monkeypatch.setattr(g, "WIRED_MODELS", ("codex", "gemini"))
    v = g.check(tmp_path, [("CLAUDE.md", 3, 1)], since=0)
    assert v.blocked is True
    assert "is wired" not in v.message
    assert "WIRED_MODELS" not in v.message
