"""Invariants for tools/finding_write.py, the cross-model finding drain.

The bug class this guards: the ledger is the only record of what a second model found,
it is append-only, and it is read by the pre-push gate. A writer that drops a row,
writes on an error path, or silently flips a recorded decision destroys the record it
exists to maintain. Measured origin (2026-09-06): 90 findings, 90 open, no writer.

Every test here is written to FAIL if the behavior breaks, not merely to exercise it.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
import finding_write as fw  # noqa: E402


ROW_1 = {
    "recorded": "2026-09-03T16:52:25+00:00",
    "target": "the gate",
    "report": "output/analysis/r1.md",
    "paths": ["tools/cross_model_gate.py"],
    "findings": [
        {"id": "F1", "severity": "P0", "summary": "hookless path", "disposition": None},
        {"id": "F2", "severity": "P1", "summary": "loop overwrites", "disposition": None},
    ],
    "waived": False,
}
ROW_2 = {
    "recorded": "2026-09-04T10:00:00+00:00",
    "target": "something else",
    "report": "output/analysis/r2.md",
    "paths": ["tools/other.py"],
    "findings": [
        {"id": "F1", "severity": "P2", "summary": "already done",
         "disposition": "fixed", "why": "prior pass"},
    ],
    "waived": False,
}
MALFORMED = "{this is not json"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    lines = [json.dumps(ROW_1), MALFORMED, json.dumps(ROW_2)]
    (tmp_path / "tools" / fw.LEDGER_NAME).write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def ledger_lines(repo: Path) -> list[str]:
    return fw.ledger_path(repo).read_text(encoding="utf-8").splitlines()


# --- addressing -----------------------------------------------------------------

@pytest.mark.parametrize("addr,expected", [("1.F3", (1, "F3")), ("12.F10", (12, "F10"))])
def test_parse_addr_accepts_valid(addr, expected):
    assert fw.parse_addr(addr) == expected


@pytest.mark.parametrize("addr", ["F3", "1", "1.", ".F3", "0.F1", "-1.F1", "a.F1", ""])
def test_parse_addr_rejects_invalid(addr):
    with pytest.raises(ValueError):
        fw.parse_addr(addr)


# --- the data-loss guard --------------------------------------------------------

def test_malformed_line_survives_a_write(repo):
    """A rewrite must not drop a row it could not parse. This is the one that
    turns a bad edit into permanent data loss."""
    before = ledger_lines(repo)
    fw.set_disposition(repo, "1.F1", "fixed", "verified at path:line")
    after = ledger_lines(repo)
    assert len(after) == len(before) == 3
    assert after[1] == MALFORMED


def test_untouched_rows_are_semantically_identical(repo):
    fw.set_disposition(repo, "1.F1", "fixed", "verified")
    after = ledger_lines(repo)
    assert json.loads(after[2]) == ROW_2
    # the sibling finding in the SAME row must not be touched
    row1 = json.loads(after[0])
    f2 = [f for f in row1["findings"] if f["id"] == "F2"][0]
    assert f2["disposition"] is None
    assert "why" not in f2


# --- writes happen, and only on success -----------------------------------------

def test_set_disposition_records_all_three_fields(repo):
    out = fw.set_disposition(repo, "1.F2", "parked", "needs a decision from the owner")
    stored = [f for f in json.loads(ledger_lines(repo)[0])["findings"]
              if f["id"] == "F2"][0]
    assert stored["disposition"] == "parked"
    assert stored["why"] == "needs a decision from the owner"
    assert stored["dispositioned"].endswith("+00:00")
    assert out["disposition"] == "parked"


@pytest.mark.parametrize("addr,disposition,why", [
    ("1.F1", "done", "bad disposition value"),
    ("1.F1", "fixed", ""),
    ("1.F1", "fixed", "   "),
    ("9.F1", "fixed", "no such run"),
    ("1.F9", "fixed", "no such finding"),
    ("2.F1", "fixed", "row 2 is malformed and cannot be edited"),
])
def test_error_paths_write_nothing(repo, addr, disposition, why):
    before = fw.ledger_path(repo).read_text(encoding="utf-8")
    with pytest.raises((KeyError, ValueError)):
        fw.set_disposition(repo, addr, disposition, why)
    assert fw.ledger_path(repo).read_text(encoding="utf-8") == before


def test_no_tmp_file_left_behind(repo):
    fw.set_disposition(repo, "1.F1", "fixed", "verified")
    assert list((repo / "tools").glob("*.tmp")) == []


# --- overwriting a recorded decision --------------------------------------------

def test_existing_disposition_requires_force(repo):
    with pytest.raises(ValueError, match="already dispositioned"):
        fw.set_disposition(repo, "3.F1", "rejected", "changed my mind")
    assert json.loads(ledger_lines(repo)[2])["findings"][0]["disposition"] == "fixed"


def test_force_overwrites(repo):
    fw.set_disposition(repo, "3.F1", "rejected", "re-verified as a false positive",
                       force=True)
    stored = json.loads(ledger_lines(repo)[2])["findings"][0]
    assert stored["disposition"] == "rejected"
    assert stored["why"] == "re-verified as a false positive"


# --- listing --------------------------------------------------------------------

def test_collect_addresses_by_run_number_not_by_parsed_index(repo):
    """Row 3 is the third LINE even though line 2 is unparseable. If addressing
    counted parsed rows instead, '3.F1' would silently point at the wrong finding."""
    addrs = [f["addr"] for f in fw.collect(repo)]
    assert addrs == ["1.F1", "1.F2", "3.F1"]


def test_collect_open_filter_excludes_dispositioned(repo):
    assert [f["addr"] for f in fw.collect(repo, only_open=True)] == ["1.F1", "1.F2"]


def test_collect_severity_filter(repo):
    assert [f["addr"] for f in fw.collect(repo, severity="P0")] == ["1.F1"]


def test_collect_on_missing_ledger_is_empty_not_an_error(tmp_path):
    (tmp_path / "tools").mkdir()
    assert fw.collect(tmp_path) == []


# --- CLI contract ---------------------------------------------------------------

def run_cli(repo: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOLS_DIR / "finding_write.py"),
         "--repo-root", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8")


def test_cli_set_then_list_open_shrinks(repo):
    before = json.loads(run_cli(repo, "list", "--open").stdout)["count"]
    assert run_cli(repo, "set", "1.F1", "--disposition", "fixed",
                   "--why", "verified").returncode == 0
    after = json.loads(run_cli(repo, "list", "--open").stdout)["count"]
    assert after == before - 1


def test_cli_bad_address_exits_2_with_json_error(repo):
    proc = run_cli(repo, "set", "9.F1", "--disposition", "fixed", "--why", "x")
    assert proc.returncode == 2
    assert "error" in json.loads(proc.stderr)


def test_cli_rejects_unknown_disposition(repo):
    proc = run_cli(repo, "set", "1.F1", "--disposition", "done", "--why", "x")
    assert proc.returncode != 0


# --- survivors from the 2026-09-06 mutation run ---------------------------------
# Each test below exists because a specific mutant survived: the code could be broken
# at that line with the suite still green. Killing a mutant is the point, not coverage.

@pytest.fixture()
def repo_weird(tmp_path: Path) -> Path:
    """Rows that are valid JSON but the wrong SHAPE. Distinct from MALFORMED, which
    fails to parse at all -- these reach the isinstance guards."""
    (tmp_path / "tools").mkdir()
    lines = [
        json.dumps([1, 2]),                       # line 1: valid JSON, not an object
        json.dumps({"findings": ["not a dict",    # line 2: findings holds a scalar
                                 {"id": "F1", "severity": "P1", "summary": "real",
                                  "disposition": None}]}),
    ]
    (tmp_path / "tools" / fw.LEDGER_NAME).write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def test_parse_addr_names_the_problem_for_a_non_numeric_run():
    """int('a') would raise ValueError anyway; the isdigit guard exists so the message
    says WHICH input was wrong. Without it the caller gets a bare int() error."""
    with pytest.raises(ValueError, match="positive integer"):
        fw.parse_addr("a.F1")


def test_collect_skips_a_non_object_row(repo_weird):
    """A JSON array on a line must not be treated as a row."""
    addrs = [f["addr"] for f in fw.collect(repo_weird)]
    assert addrs == ["2.F1"]


def test_collect_skips_a_non_dict_finding(repo_weird):
    """A scalar inside findings[] must not produce an addressable finding."""
    found = fw.collect(repo_weird)
    assert len(found) == 1
    assert found[0]["summary"] == "real"


def test_set_disposition_refuses_a_non_object_row(repo_weird):
    before = fw.ledger_path(repo_weird).read_text(encoding="utf-8")
    with pytest.raises(KeyError, match="not an object"):
        fw.set_disposition(repo_weird, "1.F1", "fixed", "should not land")
    assert fw.ledger_path(repo_weird).read_text(encoding="utf-8") == before


def test_cli_set_prints_the_updated_finding(repo):
    """The set path must report what it wrote. A silent success is indistinguishable
    from a no-op to anything reading stdout."""
    proc = run_cli(repo, "set", "1.F1", "--disposition", "fixed", "--why", "verified")
    out = json.loads(proc.stdout)
    assert out["addr"] == "1.F1"
    assert out["disposition"] == "fixed"
    assert out["why"] == "verified"


# --- the location field ----------------------------------------------------------

def test_collect_surfaces_the_location(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / fw.LEDGER_NAME).write_text(json.dumps({
        "recorded": "2026-09-06T10:00:00+00:00", "paths": [],
        "findings": [
            {"id": "F1", "severity": "P0", "location": "tools/x.py:5",
             "summary": "located", "disposition": None},
            {"id": "F2", "severity": "P1", "summary": "unlocated",
             "disposition": None}]}) + "\n", encoding="utf-8")
    got = {f["addr"]: f["location"] for f in fw.collect(tmp_path)}
    assert got == {"1.F1": "tools/x.py:5", "1.F2": None}


def test_unlocated_filter_selects_only_findings_with_no_path_line(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / fw.LEDGER_NAME).write_text(json.dumps({
        "recorded": "2026-09-06T10:00:00+00:00", "paths": [],
        "findings": [
            {"id": "F1", "severity": "P0", "location": "tools/x.py:5",
             "summary": "located", "disposition": None},
            {"id": "F2", "severity": "P1", "summary": "unlocated",
             "disposition": None},
            {"id": "F3", "severity": "P2", "location": "   ",
             "summary": "blank", "disposition": None}]}) + "\n", encoding="utf-8")
    assert [f["addr"] for f in fw.collect(tmp_path, unlocated_only=True)] == \
        ["1.F2", "1.F3"]


def test_cli_unlocated_flag(repo):
    out = json.loads(run_cli(repo, "list", "--unlocated").stdout)
    assert out["count"] == 3      # the fixture rows predate the field entirely


# ---------------------------------------------------------------------------
# Concurrency. Found by adversarial cross-model verification 2026-09-06 (F1, P0):
# the whole-ledger read-modify-write was unlocked, so a concurrent disposition or an
# appended audit row was silently restored away by a stale snapshot, with both
# commands reporting success.
# ---------------------------------------------------------------------------
import os                                                        # noqa: E402
import threading                                                 # noqa: E402
import time                                                      # noqa: E402

sys.path.insert(0, str(TOOLS_DIR))
import inbox_lock  # noqa: E402
import cross_model_gate as cmg  # noqa: E402


def _hold_lock_in_another_thread(path, release):
    """file_lock is RE-ENTRANT PER THREAD, so a same-thread hold proves nothing -- the
    call under test would simply re-enter and pass. Contention has to come from a
    different thread (or process)."""
    holding = threading.Event()

    def holder():
        with inbox_lock.file_lock(path):
            holding.set()
            release.wait(timeout=10)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert holding.wait(timeout=10), "holder never took the lock"
    return t


def test_set_disposition_waits_for_the_ledger_lock(repo):
    path = fw.ledger_path(repo)
    release = threading.Event()
    t = _hold_lock_in_another_thread(path, release)
    threading.Timer(0.3, release.set).start()
    start = time.time()
    fw.set_disposition(repo, "1.F1", "fixed", "why")
    elapsed = time.time() - start
    t.join(timeout=10)
    assert elapsed >= 0.25, (
        f"set_disposition returned in {elapsed:.3f}s while another thread held the "
        f"ledger lock, so it is not taking it")


def test_append_row_waits_for_the_same_lock(repo):
    """Both sides must take it. Locking only the rewriter still loses the append."""
    path = cmg.ledger_path(repo)
    release = threading.Event()
    t = _hold_lock_in_another_thread(path, release)
    threading.Timer(0.3, release.set).start()
    start = time.time()
    cmg.append_row(repo, {"recorded": "x", "target": "t", "report": None,
                          "paths": [], "findings": [], "waived": False})
    elapsed = time.time() - start
    t.join(timeout=10)
    assert elapsed >= 0.25, (
        f"append_row returned in {elapsed:.3f}s under a held lock, so it is not taking it")


def test_an_append_during_a_disposition_is_not_erased(repo, monkeypatch):
    """THE P0, end to end, with the race FORCED rather than hoped for.

    The old code read the whole ledger then replaced it; a row appended in between was
    absent from the snapshot and was deleted by the replace, with both commands printing
    success. Slowing the commit while holding the lock guarantees the appender actually
    contends -- without that the two can finish microseconds apart and the test passes
    while proving nothing.
    """
    before = len(fw.read_lines(fw.ledger_path(repo)))
    real_write = inbox_lock._write_atomic

    def slow_write(path, text):
        time.sleep(0.4)
        return real_write(path, text)

    monkeypatch.setattr(inbox_lock, "_write_atomic", slow_write)

    errors, appended = [], threading.Event()

    def appender():
        try:
            time.sleep(0.05)
            cmg.append_row(repo, {"recorded": "2026-09-06T00:00:00+00:00",
                                  "target": "APPENDED-DURING-WRITE",
                                  "report": None, "paths": [], "findings": [],
                                  "waived": False})
            appended.set()
        except Exception as exc:
            errors.append(exc)

    t = threading.Thread(target=appender, daemon=True)
    t.start()
    fw.set_disposition(repo, "1.F1", "fixed", "closed under concurrency")
    t.join(timeout=30)
    assert not errors, errors
    assert appended.is_set(), "the appender never completed"

    lines = fw.read_lines(fw.ledger_path(repo))
    assert any("APPENDED-DURING-WRITE" in ln for ln in lines), (
        "the appended audit row was erased by the whole-ledger rewrite")
    assert len(lines) == before + 1

    disp = [f for f in fw.collect(repo) if f["addr"] == "1.F1"][0]
    assert disp["disposition"] == "fixed", "the disposition was lost"


def test_a_duplicate_finding_id_is_refused_rather_than_shadowed(repo, tmp_path):
    """F2. Nothing rejects duplicate ids at ingest, so a first-match write pins the
    earlier one forever and the later one can never be selected. Fail loudly instead:
    the address genuinely does not identify one record."""
    path = fw.ledger_path(repo)
    rows = fw.read_lines(path)
    dup = json.dumps({
        "recorded": "2026-09-06T00:00:00+00:00", "target": "dupes", "report": None,
        "paths": [], "waived": False,
        "findings": [
            {"id": "D1", "severity": "P0", "summary": "first", "disposition": None},
            {"id": "D1", "severity": "P1", "summary": "second", "disposition": None},
        ]})
    path.write_text("\n".join(rows + [dup]) + "\n", encoding="utf-8")
    n = len(fw.read_lines(path))

    with pytest.raises(KeyError) as exc:
        fw.set_disposition(repo, f"{n}.D1", "fixed", "should refuse")
    assert "does not identify one record" in str(exc.value)

    after = fw.read_lines(path)
    assert after[n - 1] == dup, "the ledger must be untouched on the error path"
