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
    license a push; nor may it crash the hook."""
    _row(tmp_path, recorded=stamp, paths=["tools/career_scanner/scanner.py"])
    v = g.check(tmp_path, [("tools/career_scanner/scanner.py", 200, 40)], since=0)
    assert isinstance(v.blocked, bool)


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
