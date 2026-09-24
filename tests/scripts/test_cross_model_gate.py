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
    unrelated README that rode along.

    Two rows, two FAMILIES: this is a tier-3 path and tier 3 has demanded two
    independent models since grok was wired 2026-09-07. The assertion under test is
    which PATHS need coverage, so the model count is satisfied deliberately rather than
    left to make the test fail for an unrelated reason."""
    _row(tmp_path, model="codex", family="openai", paths=["tools/check_thing.py"])
    _row(tmp_path, model="grok", family="xai", paths=["tools/check_thing.py"])
    v = g.check(tmp_path, [("tools/check_thing.py", 3, 1), ("README.md", 40, 2)],
                since=0)
    assert v.blocked is False
    assert "README.md" not in (v.message or "")


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


def test_a_same_family_row_ALONE_does_not_cover_a_path(tmp_path, monkeypatch):
    """Fable was wired 2026-09-16 because it found what the cross-family models missed.
    The protection that came off (`no Anthropic verifier exists`) moved here: a verifier
    from the family that WROTE the code is additive, never sufficient, so one Fable row
    must not clear even a tier-1 path."""
    monkeypatch.setattr(g, "WIRED_MODELS", ("codex", "grok", "fable"))
    _row(tmp_path, paths=["tools/thing.py"], model="fable")
    v = g.check(tmp_path, [("tools/thing.py", 400, 10)], since=0)
    assert v.blocked is True, "a same-family verifier cleared a push on its own"


def test_a_same_family_row_CANNOT_fill_a_slot_a_tier_demands(tmp_path, monkeypatch):
    """CORRECTED 2026-09-16 by cross-model review (F2, P0). This test previously asserted
    the OPPOSITE and was wrong: it let a same-family verifier occupy the second of the two
    slots a tier-2 push demands, so {openai, anthropic} cleared a bar that used to require
    {openai, xai}. "Additive, never sufficient" is true at tier 1 and FALSE at tier 2 if
    the author's own family counts toward the requirement.

    So the tier's appetite must be met by OUTSIDE families alone. A same-family verifier
    earns its keep through the findings it produces, not by clearing a push."""
    monkeypatch.setattr(g, "WIRED_MODELS", ("codex", "grok", "fable"))
    _row(tmp_path, paths=["CLAUDE.md"], model="codex")
    _row(tmp_path, paths=["CLAUDE.md"], model="fable")
    assert g.check(tmp_path, [("CLAUDE.md", 3, 1)], since=0).blocked is True


def test_two_OUTSIDE_families_still_clear_a_tier_3_path(tmp_path, monkeypatch):
    """The fix must not make the gate unsatisfiable: two genuinely independent families
    clear it, with or without a same-family row alongside."""
    monkeypatch.setattr(g, "WIRED_MODELS", ("codex", "grok", "fable"))
    _row(tmp_path, paths=["CLAUDE.md"], model="codex")
    _row(tmp_path, paths=["CLAUDE.md"], model="grok")
    assert g.check(tmp_path, [("CLAUDE.md", 3, 1)], since=0).blocked is False
    _row(tmp_path, paths=["CLAUDE.md"], model="fable")
    assert g.check(tmp_path, [("CLAUDE.md", 3, 1)], since=0).blocked is False


def test_a_label_only_fable_row_resolves_to_the_author_family(tmp_path, monkeypatch):
    """The hole this closes: a row carrying `model: fable` and no `family` field would
    resolve to the literal "fable", read as a family OUTSIDE the author's, and clear a
    push on its own -- defeating the rule at the one place it matters. Live rows written
    by codex_verify carry `family`; a hand-written or third-party row may not."""
    assert g.row_model({"model": "fable"}) == "anthropic"
    monkeypatch.setattr(g, "WIRED_MODELS", ("codex", "grok", "fable"))
    _row(tmp_path, paths=["tools/thing.py"], model="fable")   # no family field
    assert g.check(tmp_path, [("tools/thing.py", 400, 10)], since=0).blocked is True


def test_every_wired_model_has_a_label_fallback_family(monkeypatch):
    """Any wired model missing from the map inherits the same hole. Asserted over the
    wired set so adding a fourth model cannot reintroduce it silently."""
    missing = [m for m in g.WIRED_MODELS if m not in g.LEGACY_MODEL_FAMILIES]
    assert not missing, f"no label->family fallback for {missing}"


def test_an_unstamped_row_is_attributed_to_the_only_model_there_was():
    """Every row before 2026-09-06 came from codex. Counting them as 'unknown' would
    let two legacy rows read as two independent models.

    Now expressed as a FAMILY: identities live in one namespace, so an unstamped row
    must resolve to openai rather than to the raw label "codex". Mixing the two
    namespaces let one provider clear a two-model tier (grok F1, 2026-09-07)."""
    assert g.row_model({}) == "openai"
    assert g.row_model({"model": "  "}) == "openai"
    assert g.row_model({"model": "codex"}) == "openai"
    # An unknown label has no known family; it stays itself rather than silently
    # joining someone else's bucket.
    assert g.row_model({"model": "gemini"}) == "gemini"


def test_the_legacy_family_map_agrees_with_the_model_table():
    """SINGLE SOURCE OF TRUTH, enforced rather than asserted in a comment. The mapping
    is duplicated here because importing codex_verify would be circular, and this repo
    has a rule that duplicated domain logic gets a parity test."""
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT))
    from tools import codex_verify as cvv
    for label, family in g.LEGACY_MODEL_FAMILIES.items():
        assert label in cvv.MODELS, f"{label} is not a wired model"
        assert cvv.MODELS[label].family == family, (
            f"{label}: gate says {family}, table says {cvv.MODELS[label].family}")


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


# --- the hook must actually REACH the gate (2026-09-06) -------------------------

def test_the_hook_captures_stdin_before_the_pii_guard_consumes_it():
    """THE BUG THIS EXISTS FOR. githooks(5) delivers the pushed refs on stdin, and
    stdin can be read once. prepush_pii_guard.py calls sys.stdin.read(); the hook then
    ran its `while read` loop against an already-drained stdin, left $head empty, and
    took the `[ -z "$head" ] && exit 0` path. The cross-model gate NEVER RAN on a push
    to the public remote -- silently, exit 0, no output -- which is the only remote the
    PII branch fires on.

    Structural rather than behavioural because the behaviour depends on live ledger
    state, but it pins the exact ordering that broke: capture, THEN consume.
    """
    src = (REPO_ROOT / "tools" / "hooks" / "pre-push").read_text(encoding="utf-8")
    capture = src.index('refs="$(cat)"')
    # the INVOCATION, not the mention in the header comment
    guard = src.index('python3 "$root/tools/prepush_pii_guard.py"')
    loop = src.index("while read -r")
    assert capture < guard, "stdin must be captured BEFORE the PII guard reads it"
    assert capture < loop, "stdin must be captured BEFORE the ref loop"
    assert "<<REFS" in src, "the ref loop must read the captured copy, not raw stdin"


def test_the_installed_hook_matches_the_tracked_one():
    """.git/hooks/ is not version controlled. A fix landed in tools/hooks/ and never
    installed is a fix that does not run."""
    tracked = (REPO_ROOT / "tools" / "hooks" / "pre-push").read_bytes()
    installed = REPO_ROOT / ".git" / "hooks" / "pre-push"
    if not installed.is_file():
        pytest.skip("no installed hook in this checkout")
    assert installed.read_bytes() == tracked, (
        "run: bash tools/hooks/install.sh")


# REMOVED 2026-09-06: test_hook_and_gate_agree_on_the_current_HEAD_push.
# It was written to catch the stdin-drain defect and it DID NOT. Verified by
# reinstalling the defect deliberately (refs capture removed, heredoc removed,
# confirmed applied) and re-running: the test still passed. Two revisions were tried,
# the second requiring the gate's own "BLOCKED: cross-model verification" marker in
# stderr rather than a bare nonzero exit; neither failed against the broken hook and
# the reason was not established before the session ended.
#
# It is deleted rather than kept-and-weakened because a test named for a guarantee it
# does not enforce is worse than no test: the name reads as coverage. That is the same
# defect as the isinstance(v.blocked, bool) assertion replaced earlier today.
#
# The structural guard above IS proven -- it fails against the reinstalled defect --
# and it pins the exact ordering that broke. A real behavioural parity test needs a
# throwaway git repo with a controlled ledger so the verdict does not depend on live
# state; that is the right shape and it was not built tonight.


# --- parked is a DEBT, not a closed state (2026-09-07) --------------------------

def _f(fid, sev="P1", disp=None, why=None, summary="a finding"):
    d = {"id": fid, "severity": sev, "summary": summary, "disposition": disp}
    if why:
        d["why"] = why
    return d


def test_a_parked_finding_still_appears_in_the_summary(tmp_path):
    """THE BUG THIS EXISTS FOR. open_findings() filters on 'has any disposition', so
    parking a finding gave it the same silence as fixed and rejected. Nine were parked
    on 2026-09-07 -- including two live defects in the follow-up date logic that feeds
    the morning brief -- and /standup stopped showing every one of them."""
    _row(tmp_path, findings=[_f("F1", "P0", "parked", "real, deferred, needs a design call")])
    out = g.summary(tmp_path)
    assert "parked finding" in out
    assert "a finding" in out
    assert "needs a design call" in out


def test_a_parked_finding_is_NOT_counted_as_open(tmp_path):
    """The open count must keep meaning 'nobody has looked at these'. Folding parked
    in would overstate it and destroy the distinction the drain was built for."""
    _row(tmp_path, findings=[_f("F1", "P1", "parked", "deferred"), _f("F2")])
    assert len(g.open_findings(tmp_path)) == 1
    assert "1 open cross-model finding" in g.summary(tmp_path)


@pytest.mark.parametrize("disp", ["fixed", "rejected"])
def test_closed_states_do_NOT_appear_in_the_parked_section(tmp_path, disp):
    """fixed and rejected are done. Surfacing them would make the section noise, and a
    noisy section is one the reader learns to skip."""
    _row(tmp_path, findings=[_f("F1", "P1", disp, "closed out")])
    assert g.parked_findings(tmp_path) == []
    assert "parked finding" not in g.summary(tmp_path)


def test_a_ledger_with_ONLY_parked_findings_still_renders(tmp_path):
    """The early return fires when there is nothing to say. A parked debt is something
    to say, so it must not short-circuit to silence."""
    _row(tmp_path, findings=[_f("F1", "P1", "parked", "deferred")])
    assert g.summary(tmp_path) != ""


def test_a_parked_finding_with_no_reason_still_shows(tmp_path):
    """--why is required by the writer, but a hand-edited row may lack it. Dropping the
    finding because its reason is missing would hide the debt to protect the format."""
    _row(tmp_path, findings=[_f("F1", "P1", "parked", None, summary="no reason given")])
    assert "no reason given" in g.summary(tmp_path)


def test_parked_findings_are_ordered_most_severe_first(tmp_path):
    _row(tmp_path, findings=[_f("F1", "P2", "parked", "c", summary="low"),
                             _f("F2", "P0", "parked", "a", summary="high"),
                             _f("F3", "P1", "parked", "b", summary="mid")])
    out = g.summary(tmp_path)
    assert out.index("high") < out.index("mid") < out.index("low")


def test_no_parked_section_when_there_is_nothing_parked(tmp_path):
    """test_closed_states_do_NOT_appear_in_the_parked_section passes for the WRONG
    REASON: with no open findings and no waivers, summary() early-returns "" and the
    parked branch is never reached, so the assertion holds trivially. This forces the
    branch to be evaluated by giving it an open finding to render first."""
    _row(tmp_path, findings=[_f("F1", "P1", None), _f("F2", "P2", "fixed", "done")])
    out = g.summary(tmp_path)
    assert out != ""
    assert "open cross-model finding" in out
    assert "parked finding" not in out


def test_a_parked_finding_without_a_reason_prints_no_why_line(tmp_path):
    """An empty `why:` label is worse than none: it reads as a reason that was given
    and lost, rather than one that was never recorded."""
    _row(tmp_path, findings=[_f("F1", "P1", "parked", None, summary="bare")])
    out = g.summary(tmp_path)
    assert "bare" in out
    assert "why:" not in out


def test_a_long_reason_is_truncated_with_an_ellipsis(tmp_path):
    _row(tmp_path, findings=[_f("F1", "P1", "parked", "x" * 400, summary="s")])
    out = g.summary(tmp_path)
    assert "..." in out
    assert "x" * 200 not in out


def test_a_short_reason_is_printed_whole_without_an_ellipsis(tmp_path):
    """The counterpart. Without it the comparison can be inverted and every reason
    grows an ellipsis it did not earn."""
    _row(tmp_path, findings=[_f("F1", "P1", "parked", "short and complete", summary="s")])
    out = g.summary(tmp_path)
    assert "why: short and complete" in out
    assert "..." not in out


# --- the durability half of e998825 (2026-09-06) --------------------------------
# append_row takes an advisory lock and then flushes and fsyncs. The lock is covered
# by test_inbox_lock.py; the flush and the fsync were not covered by anything, and
# both survived mutation -- the two lines that ARE the durability guarantee of a fix
# whose commit subject is "the ledger writer erased audit rows it reported success
# for" could be deleted with the whole suite green.
#
# Durability across a power loss is not observable in-process, and the enclosing
# `with` closes the handle anyway, so neither line changes anything a normal test can
# see. The one observable handle is ORDERING: at the instant fsync runs, the row must
# already have left Python's userspace buffer, or fsync is syncing nothing.

def test_append_row_fsyncs_and_does_it_AFTER_flushing(tmp_path, monkeypatch):
    """Kills both survivors at once.

    Drop os.fsync and the spy never fires. Drop fh.flush() and the spy fires while the
    row is still buffered in userspace, so an independent read of the file at that
    instant sees nothing -- which is precisely what fsync-without-flush means: a
    durability call that persists an empty buffer.
    """
    import os as _os
    seen: list[str] = []
    real_fsync = _os.fsync

    def spy(fd):
        try:
            seen.append(g.ledger_path(tmp_path).read_text(encoding="utf-8"))
        except OSError:
            seen.append("")
        return real_fsync(fd)

    monkeypatch.setattr(_os, "fsync", spy)
    g.append_row(tmp_path, {"recorded": "2026-09-06T23:00:00+00:00",
                            "target": "durability-probe", "paths": ["tools/x.py"],
                            "findings": [], "waived": False})

    assert seen, "os.fsync was never called: the durability guarantee is absent"
    assert any("durability-probe" in s for s in seen), (
        "fsync ran before the row left Python's buffer -- fh.flush() is missing, so "
        "fsync is syncing an empty file and persists nothing")


def test_the_row_is_on_disk_and_readable_after_append_row_returns(tmp_path):
    """The weaker guarantee that must also hold, and that no test asserted either."""
    g.append_row(tmp_path, {"recorded": "2026-09-06T23:00:00+00:00",
                            "target": "visible", "paths": [], "findings": []})
    rows, corrupt = g.read_ledger_with_health(tmp_path)
    assert corrupt == 0
    assert [r["target"] for r in rows] == ["visible"]


# --- the gate must not exempt itself ------------------------------------------
#
# Found 2026-09-07 by cross-model review (F1, P0) after Nick said a change here "has a
# high blast radius" and the code disagreed. It did: tiering keyed on FILENAME PREFIX,
# so tools/prepush_pii_guard.py was tier 3 and tools/cross_model_gate.py -- the file
# that DECIDES what needs review -- was tier 1. Protection depended on what a file
# happened to be called.

ENFORCEMENT_PATHS = [
    "tools/cross_model_gate.py",
    "tools/codex_verify.py",
    "tools/hooks/pre-push",
    "tools/hooks/install.sh",
]


@pytest.mark.parametrize("path", ENFORCEMENT_PATHS)
def test_the_enforcement_machinery_is_tier_3(path):
    """The gate, the wrapper that feeds it, and the hook that runs it. A wrong call in
    any of these silently governs every future push, which is the tier-3 definition."""
    assert g.blast_tier([path])[0] == 3


def test_a_small_change_to_the_gate_qualifies():
    """The exact shape that returned qualified=False before this fix: an ordinary
    maintenance edit to the gate plus its test, both under every size threshold."""
    v = g.qualifies([("tools/cross_model_gate.py", 60, 0),
                     ("tests/scripts/test_cross_model_gate.py", 40, 0)])
    assert v.qualified is True
    assert v.tier == 3


@pytest.mark.parametrize("path", ENFORCEMENT_PATHS + [
    "tools/check_public_pii.py", "tools/prepush_pii_guard.py",
])
def test_blast_classification_and_the_qualifying_branch_agree(path):
    """SINGLE SOURCE OF TRUTH. Before this fix two patterns described 'is this
    enforcement machinery' and disagreed: HOOK_RE was unanchored
    `tools/(check_|prepush_|.*_guard)` while the tier-3 rule was
    `^tools/(check_|prepush_)|_guard\\.py$`. Two definitions of one concept drift, and
    then the concept has two meanings. Both consumers must now read one predicate."""
    assert g.is_enforcement_asset(path) is True
    assert g.blast_tier([path])[0] == 3
    assert g.qualifies([(path, 1, 1)]).qualified is True


@pytest.mark.parametrize("path", [
    "tools/todo_write.py", "docs/usage.md", "tests/scripts/test_inbox_census.py",
])
def test_ordinary_files_are_not_enforcement_assets(path):
    """The predicate must not widen into 'everything under tools/'. A gate that fires
    on every push makes the waiver a reflex keystroke, which is the theatre Nick named
    as the thing to avoid."""
    assert g.is_enforcement_asset(path) is False


# --- one table: tier and reason come from the same rule ------------------------
#
# Consolidation, 2026-09-07. Before this, the tier came from BLAST_RULES and the reason
# came from a separate ladder of `if` branches in qualifies() that re-tested the same
# paths. Two mechanisms over one concept, which is the HOOK_RE defect one level up.
#
# It was not merely untidy. The `docs` branch fired BEFORE the tier check and set
# `triggers` to the governed docs, so a push carrying CLAUDE.md AND a handoff reported
# tier 3 while requiring coverage of the handoff only. The Hard Rule file was exempt.

def test_every_rule_carries_a_reason():
    """SINGLE SOURCE OF TRUTH. A rule that sets a tier without saying why forces the
    explanation back into a branch somewhere else, which is how the two drifted."""
    for rule in g.BLAST_RULES:
        assert rule.reason.strip(), f"tier-{rule.tier} rule has no reason"


def test_classify_returns_tier_triggers_and_reason_from_the_same_rule():
    tier, triggers, reason = g.classify(["tools/cross_model_gate.py"])
    assert tier == 3
    assert triggers == ["tools/cross_model_gate.py"]
    assert "enforcement machinery" in reason


def test_a_tier_1_path_also_gets_a_reason():
    """The old branch ladder had no tier-1 arm, so a tier-1 path had a tier and no
    explanation. In the table shape every rule carries both by construction."""
    tier, _triggers, reason = g.classify(["tools/inbox_census.py"])
    assert tier == 1
    assert reason.strip()


def test_the_highest_tier_rule_sets_the_triggers_not_the_first_branch():
    """THE BUG THIS FIXES. CLAUDE.md is tier 3 and a handoff is tier 2. Coverage must
    be required for the tier-3 path; before consolidation it was required for the
    handoff instead, leaving the Hard Rule file unverified on the same push."""
    v = g.qualifies([("CLAUDE.md", 3, 1),
                     ("output/analysis/090726-handoff.md", 10, 0)])
    assert v.tier == 3
    assert "CLAUDE.md" in v.triggers
    assert "output/analysis/090726-handoff.md" not in v.triggers


def test_the_verdict_reason_is_the_classified_reason(): 
    """No second mechanism. What qualifies the push and what explains it are the same
    lookup, so they cannot disagree about why."""
    paths = ["tools/check_public_pii.py"]
    _tier, _triggers, reason = g.classify(paths)
    v = g.qualifies([(paths[0], 1, 1)])
    assert v.reason.startswith(reason)


def test_classification_does_not_depend_on_rule_order(monkeypatch):
    """classify() takes the HIGHEST matching tier, not the first rule that hits.

    Added because a hand-mutant that swapped highest-tier for first-match survived the
    whole suite: BLAST_RULES is written highest-tier-first, so the two implementations
    agree on every naturally-ordered case and no test could tell them apart. The code
    comment asserted the ordering was non-load-bearing and nothing checked it.

    Reversing the table puts the tier-1 rule first. First-match then returns tier 1 for
    a push containing CLAUDE.md, which would demand ONE model for a Hard Rule edit.
    """
    monkeypatch.setattr(g, "BLAST_RULES", list(reversed(g.BLAST_RULES)))
    tier, triggers, _reason = g.classify(["tools/inbox_census.py", "CLAUDE.md"])
    assert tier == 3
    assert triggers == ["CLAUDE.md"]


# --- every path at the winning tier must be covered, not just the first ---------
#
# Codex F1 (P0) on commit 1e36d9c, 2026-09-07. The consolidation used a strict
# `rule.tier > best.tier`, so only the FIRST rule reaching the maximum tier supplied
# triggers. `check()` turns triggers into the required coverage set, so peer paths at
# the same tier rode through unverified.
#
# This is the SAME defect the consolidation was written to fix, moved one step: the
# commit message claimed CLAUDE.md was no longer exempt, and it was exempt again as
# soon as it was pushed alongside the gate, because both are tier 3 under different
# rules. Winner-take-all is the wrong operation for a coverage set.

def test_two_rules_at_the_same_tier_both_contribute_triggers():
    """framework/ and a governed doc are both tier 2, under different rules. A
    verification covering one must not license the other."""
    v = g.qualifies([("framework/rules.md", 1, 0),
                     ("output/analysis/x-handoff.md", 1, 0)])
    assert v.tier == 2
    assert set(v.triggers) == {"framework/rules.md", "output/analysis/x-handoff.md"}


def test_the_gate_and_CLAUDE_md_are_both_required_when_pushed_together():
    """Both tier 3, different rules. This is the exact case the previous commit
    claimed to fix and did not."""
    v = g.qualifies([("tools/cross_model_gate.py", 1, 1), ("CLAUDE.md", 3, 1)])
    assert v.tier == 3
    assert set(v.triggers) == {"tools/cross_model_gate.py", "CLAUDE.md"}


def test_lower_tier_paths_stay_out_of_the_required_set():
    """The union is over the WINNING tier only. Widening it to every matched path
    would demand coverage of every file in the push, which is how a gate becomes a
    reflex waiver."""
    v = g.qualifies([("CLAUDE.md", 3, 1),
                     ("output/analysis/x-handoff.md", 1, 0),
                     ("tools/inbox_census.py", 2, 0)])
    assert v.tier == 3
    assert set(v.triggers) == {"CLAUDE.md"}


def test_same_tier_aggregation_does_not_depend_on_rule_order(monkeypatch):
    """The earlier order test used a tier-1/tier-3 pair, so it only proved the numeric
    maximum was picked. It could not see the same-tier loss."""
    paths = ["framework/rules.md", "output/analysis/x-handoff.md"]
    forward = g.classify(paths)
    monkeypatch.setattr(g, "BLAST_RULES", list(reversed(g.BLAST_RULES)))
    reversed_result = g.classify(paths)
    assert forward[0] == reversed_result[0]
    assert set(forward[1]) == set(reversed_result[1])


def test_the_reason_names_only_rules_that_actually_matched():
    """Two mutation survivors lived here. Both tier-3 rules are scanned when the tier
    is 3, but only the one with hits may contribute wording -- otherwise a push that
    touches the gate alone reports that it also changes a governing document, which is
    a false statement in the block message a human is about to act on."""
    _tier, _triggers, reason = g.classify(["tools/cross_model_gate.py"])
    assert "enforcement machinery" in reason
    assert "governing document" not in reason
    assert ";" not in reason, f"a rule with no hits contributed wording: {reason!r}"


# --- independence is counted by FAMILY, not by label --------------------------
#
# Cross-model review F2 (P0), 2026-09-07: the gate counted distinct `model` strings, so
# two rows labelled "codex" and "gpt5" would have satisfied a tier-3 two-model
# requirement while both came from OpenAI. Counting labels measures spelling, not
# independence.

def test_two_rows_from_the_same_family_count_as_one_perspective(tmp_path):
    _row(tmp_path, model="codex", family="openai", paths=["CLAUDE.md"])
    _row(tmp_path, model="gpt5", family="openai", paths=["CLAUDE.md"])
    v = g.check(tmp_path, [("CLAUDE.md", 3, 1)], since=0)
    assert v.blocked is True, "two OpenAI rows are one perspective, not two"


def test_two_rows_from_different_families_satisfy_a_tier_3_path(tmp_path):
    _row(tmp_path, model="codex", family="openai", paths=["CLAUDE.md"])
    _row(tmp_path, model="grok", family="xai", paths=["CLAUDE.md"])
    v = g.check(tmp_path, [("CLAUDE.md", 3, 1)], since=0)
    assert v.blocked is False


def test_a_legacy_row_without_family_falls_back_to_its_model_name(tmp_path):
    """31 rows predate the field. They must keep counting as the one perspective they
    were, not become uncountable."""
    _row(tmp_path, model="codex", paths=["tools/career_scanner/scanner.py"])
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert v.blocked is False


def test_tier_3_now_demands_two_models():
    """The whole point of wiring the second one."""
    assert len(g.WIRED_MODELS) >= 2
    assert g.models_required(3) == 2
    assert g.models_required(2) == 2


# --- diagnostics must count only RELEVANT rows ---------------------------------
#
# 2026-09-07. The block message said "32 matching record(s) are OLDER than the work".
# 25 of those 32 were about entirely unrelated files: the counter incremented for every
# old row in the ledger. "matching" was false, the number sounded like evidence, and it
# caused a P0 escalation against the wrong defect. A diagnostic that misleads is worse
# than no diagnostic, because it gets believed.

def test_stale_counts_only_rows_touching_a_required_path(tmp_path):
    later = time.time() + 60
    _row(tmp_path, recorded="2026-09-01T10:00:00+00:00",
         paths=["tools/career_scanner/scanner.py"])          # relevant, stale
    for i in range(5):
        _row(tmp_path, recorded="2026-09-01T10:00:00+00:00",
             paths=[f"output/analysis/unrelated-{i}.md"])     # irrelevant, stale
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=later)
    assert v.blocked is True
    assert "1 matching record" in v.message, v.message
    assert "6 matching" not in v.message


def test_unreadable_timestamps_count_only_when_relevant(tmp_path):
    _row(tmp_path, recorded="not-a-date", paths=["tools/career_scanner/scanner.py"])
    for i in range(4):
        _row(tmp_path, recorded="not-a-date", paths=[f"docs/other-{i}.md"])
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert "1 record(s) have an unreadable timestamp" in v.message, v.message


def test_failed_runs_count_only_when_relevant(tmp_path):
    _row(tmp_path, verified=False, paths=["tools/career_scanner/scanner.py"])
    for i in range(3):
        _row(tmp_path, verified=False, paths=[f"framework/x-{i}.md"])
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert "1 record(s) came from a run that did not complete" in v.message, v.message


def test_a_legacy_label_and_its_family_are_ONE_perspective(tmp_path):
    """Grok F1 (P0), 2026-09-07, against the family fix committed hours earlier.

    row_model returned the `family` when present and the raw `model` label otherwise,
    so a legacy row {model: codex} yielded "codex" while a new row {family: openai}
    yielded "openai". Two different strings, one provider, and the set-of-identities
    count read them as two independent perspectives -- clearing a tier-3 push with a
    single vendor. The fix for label-counting reintroduced label-counting through a
    namespace collision.
    """
    _row(tmp_path, model="codex", paths=["CLAUDE.md"])                    # legacy
    _row(tmp_path, model="codex", family="openai", paths=["CLAUDE.md"])   # current
    v = g.check(tmp_path, [("CLAUDE.md", 3, 1)], since=0)
    assert v.blocked is True, "one provider must not satisfy a two-model tier"


def test_known_legacy_labels_resolve_to_their_family():
    assert g.row_model({"model": "codex"}) == g.row_model({"family": "openai"})
    assert g.row_model({"model": "grok"}) == g.row_model({"family": "xai"})


# --- B2 (closeout 2026-09-23): the gate reads open findings -------------------------
#
# check() counted only freshness and model families, so a fresh run from a second
# family cleared a push while that same ledger held open P0s against the pushed code.
# "Fix before the push clears" was enforced by nothing.

SCANNER = "tools/career_scanner/scanner.py"


def _p0(fid="F1", disposition=None, severity="P0"):
    return {"id": fid, "severity": severity, "summary": "a real defect",
            "location": f"{SCANNER}:10", "disposition": disposition}


def test_an_open_P0_on_a_pushed_path_blocks_an_otherwise_covered_push(tmp_path):
    _row(tmp_path, findings=[_p0()])
    v = g.check(tmp_path, [(SCANNER, 200, 40)], since=0)
    assert v.blocked is True
    assert "1.F1" in v.message                      # the finding_write address
    assert "finding_write" in v.message


def test_the_open_P0_names_every_blocking_address(tmp_path):
    _row(tmp_path, findings=[_p0("F1"), _p0("F2")])
    _row(tmp_path, findings=[_p0("F7")])
    v = g.check(tmp_path, [(SCANNER, 200, 40)], since=0)
    assert v.blocked is True
    for addr in ("1.F1", "1.F2", "2.F7"):
        assert addr in v.message


@pytest.mark.parametrize("disp", ["fixed", "rejected", "parked"])
def test_a_dispositioned_P0_does_not_block(tmp_path, disp):
    _row(tmp_path, findings=[_p0(disposition=disp)])
    assert g.check(tmp_path, [(SCANNER, 200, 40)], since=0).blocked is False


@pytest.mark.parametrize("sev", ["P1", "P2"])
def test_an_open_P1_or_P2_does_not_block(tmp_path, sev):
    """Only P0 blocks; P1s are carried, per the pre-lock disposition rule."""
    _row(tmp_path, findings=[_p0(severity=sev)])
    assert g.check(tmp_path, [(SCANNER, 200, 40)], since=0).blocked is False


def test_an_open_P0_on_an_unrelated_path_does_not_block(tmp_path):
    _row(tmp_path)                                               # covers the push
    _row(tmp_path, paths=["tools/todo_write.py"], findings=[_p0()])
    assert g.check(tmp_path, [(SCANNER, 200, 40)], since=0).blocked is False


def test_an_OLD_open_P0_still_blocks(tmp_path):
    """Age is not a disposition. A stale row cannot COVER the push, but its open P0 is
    still an unanswered finding against the code being pushed."""
    _row(tmp_path, recorded="2026-09-01T10:00:00+00:00", findings=[_p0()])
    _row(tmp_path, recorded="2026-09-10T10:00:00+00:00")        # fresh, clean cover
    since = time.mktime(time.strptime("2026-09-05", "%Y-%m-%d"))
    v = g.check(tmp_path, [(SCANNER, 200, 40)], since=since)
    assert v.blocked is True and "1.F1" in v.message


def test_an_open_P0_from_a_failed_run_still_blocks(tmp_path):
    """A run that did not complete cannot clear a push; findings it did record are
    still findings."""
    _row(tmp_path)
    _row(tmp_path, verified=False, findings=[_p0()])
    assert g.check(tmp_path, [(SCANNER, 200, 40)], since=0).blocked is True


def test_a_blank_disposition_is_open(tmp_path):
    _row(tmp_path, findings=[_p0(disposition="   ")])
    assert g.check(tmp_path, [(SCANNER, 200, 40)], since=0).blocked is True


def test_the_P0_block_does_not_claim_the_path_is_uncovered(tmp_path):
    _row(tmp_path, findings=[_p0()])
    msg = g.check(tmp_path, [(SCANNER, 200, 40)], since=0).message
    assert "Uncovered" not in msg


def test_uncovered_and_open_P0_are_both_reported(tmp_path):
    _row(tmp_path, recorded="2026-09-01T10:00:00+00:00", findings=[_p0()])
    since = time.mktime(time.strptime("2026-09-05", "%Y-%m-%d"))
    msg = g.check(tmp_path, [(SCANNER, 200, 40)], since=since).message
    assert "Uncovered" in msg and "1.F1" in msg


def test_a_small_push_to_a_path_with_an_open_P0_is_blocked(tmp_path):
    """Cross-model 2026-09-23 (Codex round 2, F1, P0). The P0 check ran only after
    qualification, so a push too small to need verification went straight past an open
    P0 on the very file it changed."""
    _row(tmp_path, paths=["tools/friction_log.py"], findings=[_p0()])
    v = g.check(tmp_path, [("tools/friction_log.py", 4, 1)], since=0)
    assert v.qualified is False
    assert v.blocked is True and "1.F1" in v.message


def test_a_small_push_with_no_open_P0_still_needs_nothing(tmp_path):
    _row(tmp_path, paths=["tools/friction_log.py"], findings=[_p0(disposition="fixed")])
    v = g.check(tmp_path, [("tools/friction_log.py", 4, 1)], since=0)
    assert v.blocked is False and "no cross-model verification required" in v.message


def test_the_address_matches_finding_write(tmp_path):
    """One addressing scheme. The gate prints the address finding_write.set accepts."""
    import finding_write as fw
    _row(tmp_path, findings=[])
    _row(tmp_path, findings=[_p0("F3")])
    msg = g.check(tmp_path, [(SCANNER, 200, 40)], since=0).message
    addrs = [f["addr"] for f in fw.collect(tmp_path, only_open=True, severity="P0")]
    assert addrs == ["2.F3"] and "2.F3" in msg


def test_a_long_P0_list_is_truncated_with_a_count(tmp_path):
    _row(tmp_path, findings=[_p0(f"F{i}") for i in range(1, 15)])
    msg = g.check(tmp_path, [(SCANNER, 200, 40)], since=0).message
    assert "14 open P0" in msg and "(+2 more)" in msg and "1.F13" not in msg


def test_twelve_P0s_carry_no_more_suffix(tmp_path):
    _row(tmp_path, findings=[_p0(f"F{i}") for i in range(1, 13)])
    msg = g.check(tmp_path, [(SCANNER, 200, 40)], since=0).message
    assert "more)" not in msg.split("open P0")[1].split("List:")[0]


def test_an_open_P0_on_a_changed_NON_TRIGGER_path_still_blocks(tmp_path):
    """Cross-model 2026-09-23 F1 (P0). The P0 check read the TRIGGER set, which a
    higher-tier path narrows to itself, so an open P0 on a lower-tier changed file in the
    same push was ignored. The check reads every changed path."""
    hook = "tools/check_draft_voice.py"                    # tier-3 trigger
    low = "tools/friction_log.py"                           # changed, not a trigger
    _row(tmp_path, paths=[hook])                            # covers the trigger
    _row(tmp_path, paths=[low], findings=[_p0("F9")])
    v = g.check(tmp_path, [(hook, 3, 1), (low, 4, 1)], since=0)
    assert v.qualified and low not in (v.triggers or [])
    assert v.blocked is True and "2.F9" in v.message


def test_cli_RECORDS_a_waiver_that_bypasses_an_open_P0_on_a_small_push(tmp_path):
    """Cross-model 2026-09-23 final round (Codex F1 / Grok F4, P0). Once open P0s block
    every push, waiving a small push past one is a real skip; it exited 0 and recorded
    nothing because the recording was gated on qualification."""
    _row(tmp_path, paths=["docs/usage.md"], findings=[_p0()])
    r = _cli(tmp_path, "3\t0\tdocs/usage.md\n", {"CODEX_VERIFY_WAIVE": "ship the typo fix"})
    assert r.returncode == 0 and "WAIVED" in r.stderr
    assert [x for x in g.read_ledger(tmp_path) if x.get("waived")][0]["reason"] == \
        "ship the typo fix"
