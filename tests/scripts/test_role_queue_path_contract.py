"""The producer/consumer path contract for the career-scan role queue.

WHY THIS FILE EXISTS
--------------------
From 2026-08-11 to 2026-09-02 the career scanner ran daily and scored roughly thirty
roles a night. Nobody ever saw one. The scanner wrote `data/inbox.md`; `/standup`'s
"Career-scan matches" section globbed the `inbox/` DIRECTORY for `*career-scan*` files
that had never existed and never could. Producer healthy, consumer pointing elsewhere,
no error anywhere, for three weeks.

That defect is invisible to unit tests: both halves work perfectly in isolation. It is
only detectable by asserting the two halves refer to the SAME path, which is what this
file does.
"""
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner.scanner import role_queue_path  # noqa: E402
from tools.role_queue_read import read_queue  # noqa: E402

STANDUP_SKILL = REPO_ROOT / ".claude" / "skills" / "standup" / "SKILL.md"


def test_the_reader_reads_the_path_the_writer_writes(tmp_path):
    """The core contract, exercised rather than asserted about."""
    import json
    from tools.career_scanner.scanner import write_role_queue

    role = {"company": "Acme", "title": "Deployment Strategist",
            "url": "https://boards.example/1", "score": 7, "published_at": "2026-09-01"}
    written = write_role_queue(tmp_path, [role], [], [])
    assert written == role_queue_path(tmp_path)

    out = read_queue(tmp_path)
    assert out["exists"] is True, "the reader could not find what the writer just wrote"
    assert out["new_count"] == 1
    assert out["roles"][0]["title"] == "Deployment Strategist"


def test_standup_invokes_the_reader_by_name():
    """The skill must actually call the tool.

    Without this, the reader can be correct and still be orphaned - which is precisely
    the shape of the original bug. A path contract that both halves satisfy is useless
    if the surface never calls either.
    """
    if not STANDUP_SKILL.is_file():
        pytest.skip("standup SKILL.md not present")
    text = STANDUP_SKILL.read_text(encoding="utf-8")
    assert "role_queue_read.py" in text, (
        "/standup does not invoke tools/role_queue_read.py, so scanned roles reach "
        "nobody. This is the 2026-09-02 defect restated."
    )


def test_standup_no_longer_globs_the_directory_that_never_had_career_scan_files():
    """The dead branch must be gone, not merely supplemented.

    A category that can never match trains the reader to ignore the section it lives
    in, which is how ~30 roles a day went unnoticed.
    """
    if not STANDUP_SKILL.is_file():
        pytest.skip("standup SKILL.md not present")
    text = STANDUP_SKILL.read_text(encoding="utf-8")
    # Look for the CATEGORISATION RULE, not any mention of the string. The skill now
    # documents the historical defect in prose, and a test that cannot tell an
    # explanation from a live rule would force the explanation to be deleted -- losing
    # the only record of why the section exists. Match the rule shape: a glob on the
    # same line as a routing arrow or the word "category".
    dead = [
        line for line in text.splitlines()
        if re.search(r"\*career-(scan|match)\*", line)
        and re.search(r"→|->|\bcategory\b", line)
    ]
    assert not dead, (
        "standup still categorises inbox/ files by a career-scan glob; no such file "
        f"has ever existed there and the scanner does not write them: {dead}"
    )


def test_a_missing_queue_is_reported_not_raised(tmp_path):
    """/standup must degrade, never fail, on a scan that has not run."""
    out = read_queue(tmp_path / "empty")
    assert out["exists"] is False
    assert out["new_count"] is None, "a missing queue must not report zero new roles"


def test_a_missing_queue_never_reports_zero(tmp_path):
    """Null and zero are different claims. 'The scan found nothing' and 'the scan did
    not run' must not render identically - that conflation is the false-zero defect
    one layer up."""
    out = read_queue(tmp_path / "nope")
    assert out["new_count"] is not 0  # noqa: F632 - identity check is the point


# ---------------------------------------------------------------------------
# P0: a scan must never erase roles the reader has not acknowledged.
#
# Found by cross-model verification 2026-09-02, and it was LIVE when found: two test
# scans in a row had marked 22 roles seen while replacing the queue's new[] with an
# empty list. Those roles -- including three 7/10 in-lane Deployment Strategist reqs --
# were permanently invisible to /standup. The seen-set tracked "written once", not
# "surfaced to a human", which is the same silent-loss class the drain was built to end.
#
# Fail-safe direction is deliberate: an unacknowledged role re-surfaces (mild noise),
# never disappears (data loss).
# ---------------------------------------------------------------------------

def _r(url, title="Deployment Strategist", score=7):
    return {"company": "Acme", "title": title, "url": url, "score": score,
            "published_at": "2026-09-01"}


def test_a_second_scan_does_not_erase_unread_roles(tmp_path):
    """The live 2026-09-02 loss, as a regression."""
    from tools.career_scanner.scanner import write_role_queue, read_pending
    write_role_queue(tmp_path, [_r("https://b/1")], [], [])
    # Second scan finds nothing new; the reader never ran in between.
    write_role_queue(tmp_path, [], [], [])
    pending = read_pending(tmp_path)
    assert len(pending) == 1, (
        "an unread role was erased by the next scan; this is the original silent-loss "
        "bug rebuilt inside its own fix"
    )


def test_pending_accumulates_across_scans(tmp_path):
    from tools.career_scanner.scanner import write_role_queue, read_pending
    write_role_queue(tmp_path, [_r("https://b/1")], [], [])
    write_role_queue(tmp_path, [_r("https://b/2", title="Engagement Manager")], [], [])
    assert len(read_pending(tmp_path)) == 2


def test_pending_does_not_duplicate_the_same_role(tmp_path):
    from tools.career_scanner.scanner import write_role_queue, read_pending
    write_role_queue(tmp_path, [_r("https://b/1")], [], [])
    write_role_queue(tmp_path, [_r("https://b/1")], [], [])
    assert len(read_pending(tmp_path)) == 1


def test_acknowledgement_clears_pending(tmp_path):
    """Only an explicit ack removes a role -- consumption, not production."""
    from tools.career_scanner.scanner import write_role_queue, read_pending
    from tools.role_queue_read import acknowledge
    write_role_queue(tmp_path, [_r("https://b/1")], [], [])
    assert len(read_pending(tmp_path)) == 1
    acknowledge(tmp_path)
    assert read_pending(tmp_path) == []


def test_a_failed_render_leaves_roles_pending(tmp_path):
    """Reading without acknowledging must NOT consume. If /standup dies mid-render the
    roles must still be there next morning."""
    from tools.career_scanner.scanner import write_role_queue, read_pending
    from tools.role_queue_read import read_queue
    write_role_queue(tmp_path, [_r("https://b/1")], [], [])
    read_queue(tmp_path)          # read, no ack
    assert len(read_pending(tmp_path)) == 1


# ---------------------------------------------------------------------------
# Orchestration-level tests. Everything above calls write_role_queue/read_queue
# DIRECTLY, so all of it survives deleting write_role_queue from scan_all_targets
# entirely -- named by the 2026-09-02 cross-model review as a surviving mutation.
# These drive the real pipeline function.
# ---------------------------------------------------------------------------

def _run_scan(tmp_path, monkeypatch, roles, targets=None):
    """Run scan_all_targets with the network and the scorer stubbed out."""
    from tools.career_scanner import scanner as sc
    monkeypatch.setattr(sc, "load_targets",
                        lambda root: targets if targets is not None
                        else [{"name": "Acme", "ats": "ashby", "slug": "acme"}])
    monkeypatch.setattr(sc, "fetch_company_roles",
                        lambda target, errors=None: [dict(r) for r in roles])
    monkeypatch.setattr(sc.time, "sleep", lambda *_: None)
    import tools.career_scanner.scorer as scorer
    monkeypatch.setattr(scorer, "load_scoring_context", lambda root: {})
    monkeypatch.setattr(scorer, "score_role", lambda role, ctx: 7)
    import tools.career_scanner.dedup as dd
    monkeypatch.setattr(dd, "load_pipeline_entries", lambda root: [])
    return sc.scan_all_targets(tmp_path)


def test_two_scans_before_any_read_lose_nothing(tmp_path, monkeypatch):
    """THE 2026-09-02 LIVE LOSS, driven through the real pipeline.

    Scan A finds a role and stamps it seen. Scan B finds the same role, now standing,
    and used to replace new[] with []. Twenty-two roles vanished this way.
    """
    from tools.career_scanner.scanner import read_pending
    r = _r("https://b/1")
    _run_scan(tmp_path, monkeypatch, [r])
    assert len(read_pending(tmp_path)) == 1
    _run_scan(tmp_path, monkeypatch, [r])          # nothing new; reader never ran
    assert len(read_pending(tmp_path)) == 1, "a scan erased an unread role"


def test_the_pipeline_writes_the_queue_before_marking_seen(tmp_path, monkeypatch):
    """If the queue write fails, nothing may be recorded as seen.

    Order is load-bearing: seen-but-never-queued is permanent invisibility, while
    queued-but-not-seen merely re-surfaces. This pins the direction.
    """
    from tools.career_scanner import scanner as sc
    from tools.career_scanner.dedup import load_seen

    def explode(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(sc, "write_role_queue", explode)
    with pytest.raises(OSError):
        _run_scan(tmp_path, monkeypatch, [_r("https://b/1")])
    assert load_seen(tmp_path) == {}, (
        "a role was marked seen although the queue write failed; it can never be "
        "surfaced again")


def test_a_scan_with_no_targets_is_a_failure_not_a_clean_zero(tmp_path, monkeypatch):
    from tools.role_queue_read import read_queue
    summary = _run_scan(tmp_path, monkeypatch, [], targets=[])
    assert summary["fetch_failures"] == 1
    out = read_queue(tmp_path)
    assert out["exists"] is True and out["fetch_failures"] == 1, (
        "a scan that examined nothing rendered as a scan that found nothing")


def test_acknowledging_by_key_leaves_the_rest_pending(tmp_path):
    """/standup renders the top N. Acking must not consume the tail it never showed."""
    from tools.career_scanner.scanner import write_role_queue, read_pending
    from tools.role_queue_read import read_queue, acknowledge
    write_role_queue(tmp_path, [_r("https://b/1"), _r("https://b/2"),
                                _r("https://b/3")], [], [])
    out = read_queue(tmp_path, top=2)
    assert len(out["ack_keys"]) == 2
    acknowledge(tmp_path, out["ack_keys"])
    left = read_pending(tmp_path)
    assert len(left) == 1 and left[0]["url"] == "https://b/3"


def test_acknowledging_a_missing_queue_does_not_raise(tmp_path):
    from tools.role_queue_read import acknowledge
    assert acknowledge(tmp_path / "nothing")["acknowledged"] == 0


def test_a_corrupt_queue_is_quarantined_not_overwritten(tmp_path):
    """The pending log is the only record that a role was found and not yet seen.

    Silently replacing a corrupt one destroys the evidence of what was lost.
    """
    from tools.career_scanner.scanner import write_role_queue, role_queue_path
    path = role_queue_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"new": [truncated', encoding="utf-8")
    write_role_queue(tmp_path, [_r("https://b/9")], [], [])
    assert path.with_suffix(path.suffix + ".corrupt").is_file(), (
        "a corrupt queue was overwritten; the record of what was lost is gone")


def test_a_wrong_shaped_queue_does_not_crash_the_reader(tmp_path):
    from tools.career_scanner.scanner import role_queue_path
    from tools.role_queue_read import read_queue
    path = role_queue_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"new": "not a list"}', encoding="utf-8")
    out = read_queue(tmp_path)
    assert out["roles"] == []


def test_pending_overflow_is_reported(tmp_path):
    """An unread log this long means the consumer is dead. Nothing is dropped -- the
    overflow is surfaced so a dead reader is visible instead of silent."""
    from tools.career_scanner.scanner import write_role_queue, PENDING_WARN_AT
    from tools.role_queue_read import read_queue
    write_role_queue(tmp_path, [_r(f"https://b/{i}")
                                for i in range(PENDING_WARN_AT + 1)], [], [])
    out = read_queue(tmp_path)
    assert out["pending_overflow"] is True
    assert out["new_count"] == PENDING_WARN_AT + 1, "roles were dropped at the cap"


# ---------------------------------------------------------------------------
# Written against SURVIVING MUTANTS, not against the code. Each test below was
# added because `tools/mutation_check.py tools/role_queue_read.py` showed the
# behaviour could be broken with the suite still green (2026-09-02).
# ---------------------------------------------------------------------------

def test_a_missing_queue_says_why(tmp_path):
    """`exists: false` with no reason is unactionable -- /standup cannot tell the
    reader whether the scan is broken or merely absent."""
    from tools.role_queue_read import read_queue
    out = read_queue(tmp_path / "gone")
    assert "scan" in out["reason"].lower()


def test_an_unreadable_queue_reports_rather_than_raises(tmp_path):
    from tools.career_scanner.scanner import role_queue_path
    from tools.role_queue_read import read_queue
    path = role_queue_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    out = read_queue(tmp_path)
    assert out["exists"] is False
    assert "unreadable" in out["reason"] and out["new_count"] is None


def test_a_non_iterable_new_field_does_not_crash_the_reader(tmp_path):
    from tools.career_scanner.scanner import role_queue_path
    from tools.role_queue_read import read_queue
    path = role_queue_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"new": 5}', encoding="utf-8")
    assert read_queue(tmp_path)["roles"] == []


def test_roles_are_ordered_by_score_then_recency(tmp_path):
    """Nick's stated ordering, 2026-09-02: fit first, recency as the tiebreaker.
    Sorting by date alone floated a 3/10 SEO role above every in-lane role."""
    from tools.career_scanner.scanner import write_role_queue
    from tools.role_queue_read import read_queue
    write_role_queue(tmp_path, [
        dict(_r("https://b/low", title="SEO Strategist", score=3),
             published_at="2026-09-02"),
        dict(_r("https://b/old", score=7), published_at="2026-08-01"),
        dict(_r("https://b/new", score=7), published_at="2026-09-01"),
    ], [], [])
    got = [r["url"] for r in read_queue(tmp_path)["roles"]]
    assert got == ["https://b/new", "https://b/old", "https://b/low"]


def _write_queue_at(tmp_path, scanned_at):
    import json
    from tools.career_scanner.scanner import role_queue_path
    path = role_queue_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"scanned_at": scanned_at, "new": [], "new_count": 0}),
                    encoding="utf-8")


def test_a_stale_queue_is_reported_stale(tmp_path):
    """A daily job silent for 36h is broken, not quiet. Without this the reader
    renders three-day-old postings as today's news."""
    from datetime import datetime, timedelta, timezone
    from tools.role_queue_read import read_queue, STALE_AFTER_HOURS
    old = datetime.now(timezone.utc) - timedelta(hours=STALE_AFTER_HOURS + 5)
    _write_queue_at(tmp_path, old.isoformat(timespec="seconds"))
    out = read_queue(tmp_path)
    assert out["is_stale"] is True and out["stale_hours"] > STALE_AFTER_HOURS


def test_a_fresh_queue_is_not_reported_stale(tmp_path):
    from datetime import datetime, timezone
    from tools.role_queue_read import read_queue
    _write_queue_at(tmp_path, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    assert read_queue(tmp_path)["is_stale"] is False


def test_a_naive_timestamp_is_read_as_utc_not_crashed_on(tmp_path):
    """The writer emits tz-aware, but a hand-edited or older queue may not."""
    from datetime import datetime, timezone
    from tools.role_queue_read import read_queue
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    _write_queue_at(tmp_path, naive)
    out = read_queue(tmp_path)
    assert out["stale_hours"] is not None and abs(out["stale_hours"]) < 1


def test_acknowledging_a_missing_queue_creates_nothing(tmp_path):
    """An ack must not conjure an empty queue -- that would turn "the scan never ran"
    into "the scan found nothing", the false zero one layer up."""
    from tools.career_scanner.scanner import role_queue_path
    from tools.role_queue_read import acknowledge
    out = acknowledge(tmp_path)
    assert out["acknowledged"] == 0 and out["remaining"] == 0
    assert "reason" in out
    assert not role_queue_path(tmp_path).is_file()


def test_acknowledge_reports_what_it_consumed(tmp_path):
    from tools.career_scanner.scanner import write_role_queue
    from tools.role_queue_read import acknowledge
    write_role_queue(tmp_path, [_r("https://b/1"), _r("https://b/2")], [], [])
    out = acknowledge(tmp_path, ["https://b/1"])
    assert out == {"acknowledged": 1, "remaining": 1}


def test_the_cli_reads_and_acks(tmp_path):
    """The skill invokes the CLI, not the function. Nothing above executes main()."""
    import json
    import subprocess
    from tools.career_scanner.scanner import write_role_queue, read_pending
    write_role_queue(tmp_path, [_r("https://b/1"), _r("https://b/2")], [], [])
    cli = [sys.executable, str(REPO_ROOT / "tools" / "role_queue_read.py"),
           "--repo-root", str(tmp_path)]
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"}
    read = subprocess.run(cli + ["--top", "1"], capture_output=True, text=True, env=env)
    assert read.returncode == 0, read.stderr
    payload = json.loads(read.stdout)
    assert payload["new_count"] == 2 and len(payload["ack_keys"]) == 1

    ack = subprocess.run(cli + ["--ack", payload["ack_keys"][0]],
                         capture_output=True, text=True, env=env)
    assert ack.returncode == 0, ack.stderr
    assert json.loads(ack.stdout)["acknowledged"] == 1
    assert len(read_pending(tmp_path)) == 1


def test_the_standup_skill_acknowledges_after_rendering():
    """Rendering without acking makes the queue grow forever; acking without
    rendering loses roles. The skill must do both, in that order."""
    if not STANDUP_SKILL.is_file():
        pytest.skip("standup SKILL.md not present")
    text = STANDUP_SKILL.read_text(encoding="utf-8")
    assert "--ack" in text, "/standup never acknowledges; the pending log only grows"
    assert text.index("--top 5") < text.index("--ack"), (
        "/standup acks before it renders; a crash mid-render would consume the roles")


# ---------------------------------------------------------------------------
# Scanner-side mutants. Same method as the block above: each of these was a
# surviving mutation in `tools/mutation_check.py tools/career_scanner/scanner.py`.
# ---------------------------------------------------------------------------

def test_a_dry_run_writes_no_queue(tmp_path, monkeypatch):
    """--dry-run must not consume or mutate state. Without this, previewing a scan
    silently marks roles seen and enqueues them."""
    from tools.career_scanner import scanner as sc
    from tools.career_scanner.dedup import load_seen
    monkeypatch.setattr(sc, "load_targets",
                        lambda root: [{"name": "Acme", "ats": "ashby", "slug": "a"}])
    monkeypatch.setattr(sc, "fetch_company_roles",
                        lambda t, errors=None: [dict(_r("https://b/1"))])
    monkeypatch.setattr(sc.time, "sleep", lambda *_: None)
    import tools.career_scanner.scorer as scorer
    monkeypatch.setattr(scorer, "load_scoring_context", lambda root: {})
    monkeypatch.setattr(scorer, "score_role", lambda role, ctx: 7)
    import tools.career_scanner.dedup as dd
    monkeypatch.setattr(dd, "load_pipeline_entries", lambda root: [])
    sc.scan_all_targets(tmp_path, dry_run=True)
    assert not sc.role_queue_path(tmp_path).is_file()
    assert load_seen(tmp_path) == {}


def test_a_dry_run_with_no_targets_writes_no_queue(tmp_path, monkeypatch):
    from tools.career_scanner import scanner as sc
    monkeypatch.setattr(sc, "load_targets", lambda root: [])
    summary = sc.scan_all_targets(tmp_path, dry_run=True)
    assert summary["fetch_failures"] == 1
    assert not sc.role_queue_path(tmp_path).is_file()


def test_the_scan_returns_a_summary_of_what_it_did(tmp_path, monkeypatch):
    summary = _run_scan(tmp_path, monkeypatch, [_r("https://b/1"), _r("https://b/2")])
    assert summary["total_fetched"] == 2
    assert summary["new_since_last_scan"] == 2
    assert summary["companies_scanned"] == 1
    assert summary["fetch_failures"] == 0


def test_an_acknowledged_role_does_not_come_back(tmp_path, monkeypatch):
    """The seen-set is what makes acknowledgement final. Without it a role would
    re-enter pending on every scan forever -- the daily-repeat defect returning."""
    from tools.career_scanner.scanner import read_pending
    from tools.role_queue_read import acknowledge
    r = _r("https://b/1")
    _run_scan(tmp_path, monkeypatch, [r])
    acknowledge(tmp_path)
    _run_scan(tmp_path, monkeypatch, [r])
    assert read_pending(tmp_path) == [], "an acknowledged role was re-surfaced"


def test_the_scan_ranks_by_score(tmp_path, monkeypatch):
    from tools.career_scanner import scanner as sc
    scores = {"https://b/low": 2, "https://b/high": 9}
    import tools.career_scanner.scorer as scorer
    monkeypatch.setattr(sc, "load_targets",
                        lambda root: [{"name": "Acme", "ats": "ashby", "slug": "a"}])
    monkeypatch.setattr(sc, "fetch_company_roles", lambda t, errors=None: [
        dict(_r("https://b/low")), dict(_r("https://b/high"))])
    monkeypatch.setattr(sc.time, "sleep", lambda *_: None)
    monkeypatch.setattr(scorer, "load_scoring_context", lambda root: {})
    monkeypatch.setattr(scorer, "score_role", lambda role, ctx: scores[role["url"]])
    import tools.career_scanner.dedup as dd
    monkeypatch.setattr(dd, "load_pipeline_entries", lambda root: [])
    summary = sc.scan_all_targets(tmp_path)
    assert [r["url"] for r in summary["roles"]] == ["https://b/high", "https://b/low"]


def test_the_same_role_twice_in_one_scan_is_enqueued_once(tmp_path):
    from tools.career_scanner.scanner import write_role_queue, read_pending
    write_role_queue(tmp_path, [_r("https://b/1"), _r("https://b/1")], [], [])
    assert len(read_pending(tmp_path)) == 1


def test_a_queue_that_is_valid_json_but_not_an_object_is_quarantined(tmp_path):
    """`[]` parses fine and then breaks every consumer. Treat it as corruption."""
    from tools.career_scanner.scanner import (
        write_role_queue, role_queue_path, read_pending)
    path = role_queue_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")
    write_role_queue(tmp_path, [_r("https://b/1")], [], [])
    assert path.with_suffix(path.suffix + ".corrupt").is_file()
    assert len(read_pending(tmp_path)) == 1


def test_corruption_is_announced_on_stderr(tmp_path, capsys):
    from tools.career_scanner.scanner import write_role_queue, role_queue_path
    path = role_queue_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{oops", encoding="utf-8")
    write_role_queue(tmp_path, [], [], [])
    assert "CORRUPT" in capsys.readouterr().err


def test_a_failed_queue_write_leaves_no_temp_file(tmp_path, monkeypatch):
    """A nightly job that leaks a .tmp per failure fills the directory silently."""
    import tools.career_scanner.scanner as sc
    monkeypatch.setattr(sc.json, "dump", lambda *a, **k: (_ for _ in ()).throw(
        ValueError("unserialisable")))
    with pytest.raises(ValueError):
        sc.write_queue_payload(tmp_path, {"new": []})
    assert list((tmp_path / "tools").glob("*.tmp")) == []


def test_a_parser_caught_failure_without_an_error_list_does_not_crash(tmp_path,
                                                                     monkeypatch):
    """`errors=None` is the documented default and the scan must survive it."""
    import tools.career_scanner.ashby as mod
    from tools.career_scanner.scanner import fetch_company_roles

    def dead(slug, errors=None):
        if errors is not None:
            errors.append({"reason": "HTTP 404"})
        return []

    monkeypatch.setattr(mod, "fetch_ashby", dead)
    assert fetch_company_roles(
        {"name": "Acme", "ats": "ashby", "slug": "a"}) == []


def test_a_missing_queue_is_not_announced_as_corruption(tmp_path, capsys):
    """"Not there yet" and "damaged" are different claims. Conflating them on a fresh
    machine would print CORRUPT on the very first run."""
    from tools.career_scanner.scanner import read_pending
    assert read_pending(tmp_path) == []
    assert "CORRUPT" not in capsys.readouterr().err


def test_the_scan_logs_its_per_company_progress(tmp_path, monkeypatch, capsys):
    """The launchd log is the only trace of a nightly run. A scan that says nothing
    per company cannot be diagnosed after the fact -- which is how 24 of 45 configured
    companies were skipped unnoticed for three weeks."""
    from tools.career_scanner import scanner as sc
    hits = {"Acme": [dict(_r("https://b/1"))], "Empty Co": []}
    monkeypatch.setattr(sc, "load_targets", lambda root: [
        {"name": "Acme", "ats": "ashby", "slug": "a"},
        {"name": "Empty Co", "ats": "ashby", "slug": "e"}])
    monkeypatch.setattr(sc, "fetch_company_roles",
                        lambda t, errors=None: hits[t["name"]])
    monkeypatch.setattr(sc.time, "sleep", lambda *_: None)
    import tools.career_scanner.scorer as scorer
    monkeypatch.setattr(scorer, "load_scoring_context", lambda root: {})
    monkeypatch.setattr(scorer, "score_role", lambda role, ctx: 7)
    import tools.career_scanner.dedup as dd
    monkeypatch.setattr(dd, "load_pipeline_entries", lambda root: [])
    sc.scan_all_targets(tmp_path, dry_run=True)
    err = capsys.readouterr().err
    assert "Scanning Acme (ashby)" in err and "Scanning Empty Co (ashby)" in err
    assert "Found 1 matching roles" in err
    assert "No roles found" in err


def test_the_scan_rate_limits_between_companies_but_not_after_the_last(tmp_path,
                                                                      monkeypatch):
    """0.5s between boards (T-02-09). Sleeping after the LAST company adds dead time
    to every nightly run; not sleeping at all hammers the ATS endpoints."""
    from tools.career_scanner import scanner as sc
    calls = []
    monkeypatch.setattr(sc, "load_targets", lambda root: [
        {"name": f"Co{i}", "ats": "ashby", "slug": str(i)} for i in range(3)])
    monkeypatch.setattr(sc, "fetch_company_roles", lambda t, errors=None: [])
    monkeypatch.setattr(sc.time, "sleep", lambda s: calls.append(s))
    import tools.career_scanner.scorer as scorer
    monkeypatch.setattr(scorer, "load_scoring_context", lambda root: {})
    monkeypatch.setattr(scorer, "score_role", lambda role, ctx: 0)
    import tools.career_scanner.dedup as dd
    monkeypatch.setattr(dd, "load_pipeline_entries", lambda root: [])
    sc.scan_all_targets(tmp_path, dry_run=True)
    assert calls == [0.5, 0.5], f"3 companies must sleep twice, got {calls}"
