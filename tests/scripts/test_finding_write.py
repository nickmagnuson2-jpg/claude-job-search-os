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


def test_parked_filter_selects_only_deferred_debts(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / fw.LEDGER_NAME).write_text(json.dumps({
        "recorded": "2026-09-07T10:00:00+00:00", "paths": [],
        "findings": [
            {"id": "F1", "severity": "P0", "summary": "deferred",
             "disposition": "parked", "why": "design call"},
            {"id": "F2", "severity": "P1", "summary": "done",
             "disposition": "fixed", "why": "shipped"},
            {"id": "F3", "severity": "P2", "summary": "untouched",
             "disposition": None}]}) + "\n", encoding="utf-8")
    assert [f["addr"] for f in fw.collect(tmp_path, parked_only=True)] == ["1.F1"]


def test_cli_parked_flag(repo):
    out = json.loads(run_cli(repo, "list", "--parked").stdout)
    assert out["count"] == 0        # the fixture has fixed and open, no parked


def test_importable_as_a_package_module_from_the_repo_root():
    """cross_model_gate imports this module from inside the pre-push hook. Imported as
    `tools.finding_write`, its sibling `inbox_lock` resolves only through the module's
    own sys.path insert, so dropping that insert must break the import."""
    repo = Path(TOOLS_DIR).parent
    r = subprocess.run([sys.executable, "-c", "import tools.finding_write"],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- accept: Nick's decision that a parked P0 may ship (2026-10-14) -------------------

def _finding(repo: Path, run: int, fid: str) -> dict:
    row = json.loads(ledger_lines(repo)[run - 1])
    return next(f for f in row["findings"] if f["id"] == fid)


def test_accept_records_all_three_fields_on_a_parked_P0(repo):
    fw.set_disposition(repo, "1.F1", "parked", "deferred")
    out = fw.accept(repo, "1.F1", "ship the debt")
    f = _finding(repo, 1, "F1")
    assert f["accepted_by"] == "owner" and f["accepted_why"] == "ship the debt"
    assert f["accepted_at"] and out["accepted_at"] == f["accepted_at"]
    assert f["disposition"] == "parked"


@pytest.mark.parametrize("prep, addr", [
    (None, "1.F1"),                       # open P0: park it first
    ("fixed", "1.F1"),                    # closed P0: nothing to accept
    ("parked-P1", "1.F2"),                # a P1 never blocks
])
def test_accept_refuses_anything_but_a_parked_P0_and_writes_nothing(repo, prep, addr):
    if prep == "fixed":
        fw.set_disposition(repo, "1.F1", "fixed", "done")
    if prep == "parked-P1":
        fw.set_disposition(repo, "1.F2", "parked", "later")
    before = ledger_lines(repo)
    with pytest.raises(ValueError):
        fw.accept(repo, addr, "why")
    assert ledger_lines(repo) == before


def test_accept_requires_a_reason(repo):
    fw.set_disposition(repo, "1.F1", "parked", "deferred")
    before = ledger_lines(repo)
    with pytest.raises(ValueError):
        fw.accept(repo, "1.F1", "   ")
    assert ledger_lines(repo) == before


def test_accept_twice_is_refused(repo):
    fw.set_disposition(repo, "1.F1", "parked", "deferred")
    fw.accept(repo, "1.F1", "first")
    with pytest.raises(ValueError):
        fw.accept(repo, "1.F1", "second")
    assert _finding(repo, 1, "F1")["accepted_why"] == "first"


def test_any_new_disposition_clears_the_acceptance(repo):
    """An acceptance was given for one decision. Re-parking must need a fresh one, or
    the agent could re-park an accepted finding with a new reason and ship on the old
    acceptance."""
    fw.set_disposition(repo, "1.F1", "parked", "deferred")
    fw.accept(repo, "1.F1", "ok")
    fw.set_disposition(repo, "1.F1", "parked", "a different reason", force=True)
    f = _finding(repo, 1, "F1")
    for k in fw.ACCEPT_FIELDS:
        assert k not in f


def test_collect_reports_the_acceptor(repo):
    fw.set_disposition(repo, "1.F1", "parked", "deferred")
    fw.accept(repo, "1.F1", "ok")
    got = [f for f in fw.collect(repo) if f["addr"] == "1.F1"][0]
    assert got["accepted_by"] == "owner"


def test_cli_accept(repo):
    fw.set_disposition(repo, "1.F1", "parked", "deferred")
    r = run_cli(repo, "accept", "1.F1", "--why", "ship")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["accepted_by"] == "owner"


def test_cli_accept_on_an_open_P0_exits_2(repo):
    r = run_cli(repo, "accept", "1.F1", "--why", "ship")
    assert r.returncode == 2 and "Park it first" in r.stderr


# --- RULING Q20 (2026-11-16): a disposition written in a linked worktree also reaches the
# mirrored row in the MAIN checkout's ledger, so it survives `git worktree remove`. -------

import codex_verify as cv  # noqa: E402  (the same module object finding_write imports)

_REAL_RUN = subprocess.run
W_ROW = {
    "recorded": "2026-11-16T12:00:00+00:00", "model": "codex", "target": "t",
    "report": "output/analysis/111626-codex-x-r1.md", "paths": ["tools/x.py"],
    "findings": [
        {"id": "F1", "severity": "P0", "summary": "a P0", "disposition": "parked", "why": "later"},
        {"id": "F2", "severity": "P1", "summary": "a P1", "disposition": None},
    ],
    "waived": False,
}
MAIN_OWN = {**ROW_1, "model": "codex"}       # main's own review: no worktree field


def _git(cwd, *args):
    _REAL_RUN(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _write_ledger(root: Path, rows) -> Path:
    p = root / "tools" / fw.LEDGER_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


@pytest.fixture()
def mirrored(tmp_path):
    """A real git main checkout and linked worktree. The worktree's ledger holds W_ROW;
    main's holds its own review and the mirror of W_ROW (as codex_verify writes it)."""
    main = tmp_path / "main"
    (main / "tools").mkdir(parents=True)
    (main / "tools" / "x.py").write_text("x = 1\n", encoding="utf-8")
    _git(main.parent, "init", "-q", "-b", "main", str(main))
    _git(main, "add", "-A")
    _git(main, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "one")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(wt))
    main, wt = main.resolve(), wt.resolve()
    _write_ledger(wt, [W_ROW])
    _write_ledger(main, [MAIN_OWN, {**W_ROW, "worktree": str(wt)}])
    return main, wt


def _rows(root):
    return [json.loads(ln) for ln in fw.read_lines(fw.ledger_path(root))]


def _cli(capsys, root, *args):
    rc = fw.main(["--repo-root", str(root), *args])
    cap = capsys.readouterr()
    return rc, (json.loads(cap.out) if cap.out.strip() else None), cap.err


def test_set_in_a_worktree_updates_the_mirrored_row_in_main(mirrored, capsys):
    main, wt = mirrored
    before_main_own = fw.read_lines(fw.ledger_path(main))[0]
    rc, out, err = _cli(capsys, wt, "set", "1.F2", "--disposition", "parked", "--why", "freeze")
    assert rc == 0 and out["mirrored"] is True
    w = _rows(wt)[0]["findings"][1]
    m = _rows(main)[1]["findings"][1]
    assert (m["disposition"], m["why"], m["dispositioned"]) == ("parked", "freeze", w["dispositioned"])
    assert fw.read_lines(fw.ledger_path(main))[0] == before_main_own       # main's own row untouched
    assert _rows(main)[1]["findings"][0] == W_ROW["findings"][0]           # the other finding untouched


def test_accept_in_a_worktree_reaches_main_and_a_later_set_clears_it_there(mirrored, capsys):
    main, wt = mirrored
    rc, out, _ = _cli(capsys, wt, "accept", "1.F1", "--why", "ship it")
    assert rc == 0 and out["mirrored"] is True
    w, m = _rows(wt)[0]["findings"][0], _rows(main)[1]["findings"][0]
    for k in fw.ACCEPT_FIELDS:
        assert m[k] == w[k]
    rc, out, _ = _cli(capsys, wt, "set", "1.F1", "--disposition", "fixed", "--why", "done", "--force")
    assert rc == 0 and out["mirrored"] is True
    m = _rows(main)[1]["findings"][0]
    assert m["disposition"] == "fixed" and not any(k in m for k in fw.ACCEPT_FIELDS)


def _main_unchanged_after_set(capsys, main, wt, expect_in_err):
    before = fw.ledger_path(main).read_bytes() if fw.ledger_path(main).exists() else None
    rc, out, err = _cli(capsys, wt, "set", "1.F2", "--disposition", "parked", "--why", "w")
    assert rc == 0 and out["mirrored"] is False
    assert _rows(wt)[0]["findings"][1]["disposition"] == "parked"          # the worktree write stands
    after = fw.ledger_path(main).read_bytes() if fw.ledger_path(main).exists() else None
    assert after == before
    for s in expect_in_err:
        assert s in err


def test_no_mirrored_row_warns_with_the_rows_identity_and_changes_nothing(mirrored, capsys):
    main, wt = mirrored
    _write_ledger(main, [MAIN_OWN])
    _main_unchanged_after_set(capsys, main, wt,
                              [W_ROW["report"], W_ROW["model"], W_ROW["recorded"]])


def test_mains_own_review_with_the_same_identity_is_never_overwritten(mirrored, capsys):
    main, wt = mirrored
    _write_ledger(main, [MAIN_OWN, dict(W_ROW)])                           # no worktree field
    _main_unchanged_after_set(capsys, main, wt, [W_ROW["report"]])


def test_another_worktrees_mirror_is_not_touched(mirrored, capsys, tmp_path):
    main, wt = mirrored
    _write_ledger(main, [MAIN_OWN, {**W_ROW, "worktree": str(tmp_path / "other")}])
    _main_unchanged_after_set(capsys, main, wt, [W_ROW["report"]])


def test_two_matching_mirrors_are_ambiguous_and_change_nothing(mirrored, capsys):
    main, wt = mirrored
    mirror = {**W_ROW, "worktree": str(wt)}
    _write_ledger(main, [MAIN_OWN, mirror, mirror])
    _main_unchanged_after_set(capsys, main, wt, [W_ROW["report"]])


def test_a_mirror_without_that_finding_id_changes_nothing(mirrored, capsys):
    main, wt = mirrored
    _write_ledger(main, [MAIN_OWN, {**W_ROW, "worktree": str(wt),
                                    "findings": [W_ROW["findings"][0]]}])
    _main_unchanged_after_set(capsys, main, wt, ["F2"])


def test_a_missing_main_ledger_is_not_created(mirrored, capsys):
    main, wt = mirrored
    fw.ledger_path(main).unlink()
    _main_unchanged_after_set(capsys, main, wt, [W_ROW["report"]])
    assert not fw.ledger_path(main).exists()


def test_a_worktree_recorded_through_a_symlink_still_matches(mirrored, capsys, tmp_path):
    main, wt = mirrored
    link = tmp_path / "wt-link"
    link.symlink_to(wt)
    _write_ledger(main, [MAIN_OWN, {**W_ROW, "worktree": str(link)}])
    rc, out, _ = _cli(capsys, wt, "set", "1.F2", "--disposition", "parked", "--why", "w")
    assert rc == 0 and out["mirrored"] is True
    assert _rows(main)[1]["findings"][1]["disposition"] == "parked"


def test_outside_a_worktree_no_resolver_runs_and_mirrored_is_null(repo, capsys, monkeypatch):
    def boom(root):
        raise AssertionError("resolver called outside a worktree")
    monkeypatch.setattr(cv, "_main_checkout", boom)
    rc, out, _ = _cli(capsys, repo, "set", "1.F2", "--disposition", "parked", "--why", "w")
    assert rc == 0 and out["mirrored"] is None


def test_a_resolver_failure_warns_and_the_worktree_write_stands(mirrored, capsys, monkeypatch):
    main, wt = mirrored
    def boom(root):
        raise RuntimeError("cannot find the main checkout")
    monkeypatch.setattr(cv, "_main_checkout", boom)
    _main_unchanged_after_set(capsys, main, wt, ["cannot find the main checkout"])


def test_inside_a_test_run_a_real_main_ledger_is_never_written(mirrored, capsys, monkeypatch):
    main, wt = mirrored
    monkeypatch.setattr(cv, "_real_ledger_roots", lambda: {main})
    _main_unchanged_after_set(capsys, main, wt, ["real ledger"])


def test_a_failed_worktree_write_never_reaches_main(mirrored, capsys):
    main, wt = mirrored
    before = fw.ledger_path(main).read_bytes()
    rc, out, err = _cli(capsys, wt, "set", "1.F9", "--disposition", "parked", "--why", "w")
    assert rc == 2 and out is None
    assert fw.ledger_path(main).read_bytes() == before


def test_a_resolver_that_finds_no_main_checkout_means_no_mirror(mirrored, capsys, monkeypatch):
    """codex_verify's mirror treats a None main checkout as 'nothing to mirror' (None, no
    warning); the disposition sync agrees."""
    main, wt = mirrored
    before = fw.ledger_path(main).read_bytes()
    monkeypatch.setattr(cv, "_main_checkout", lambda root: None)
    rc, out, err = _cli(capsys, wt, "set", "1.F2", "--disposition", "parked", "--why", "w")
    assert rc == 0 and out["mirrored"] is None and err == ""
    assert fw.ledger_path(main).read_bytes() == before


def test_a_malformed_main_line_after_the_mirror_is_kept_and_does_not_double_match(mirrored, capsys):
    main, wt = mirrored
    p = fw.ledger_path(main)
    p.write_text(p.read_text(encoding="utf-8") + MALFORMED + "\n", encoding="utf-8")
    rc, out, _ = _cli(capsys, wt, "set", "1.F2", "--disposition", "parked", "--why", "w")
    assert rc == 0 and out["mirrored"] is True
    assert fw.read_lines(p)[-1] == MALFORMED
    assert json.loads(fw.read_lines(p)[1])["findings"][1]["disposition"] == "parked"


def test_duplicate_finding_ids_in_mains_mirror_are_never_resolved_first_match(mirrored, capsys):
    """The same rule _edit_finding keeps: two findings with one id do not identify a record."""
    main, wt = mirrored
    dup = W_ROW["findings"][1]
    _write_ledger(main, [MAIN_OWN, {**W_ROW, "worktree": str(wt),
                                    "findings": [W_ROW["findings"][0], dup, dict(dup)]}])
    # Main's findings differ from the worktree row's, so the signature does not match: the
    # set warns and writes nothing, never picking the first F2.
    _main_unchanged_after_set(capsys, main, wt, ["0 mirrored rows"])



# --- Codex findings-durable r1 (2026-11-16): F1 and F2 -------------------------------------

def test_the_worktree_lock_is_held_while_main_is_updated(mirrored, capsys, monkeypatch):
    """F1: the worktree lock was released before the main update, so two sessions could
    interleave and an older captured disposition could win in main. The worktree write and
    the main update now happen under one hold of the worktree ledger's lock."""
    import contextlib
    import inbox_lock
    main, wt = mirrored
    held, seen = [], []
    real_lock, real_update = inbox_lock.file_lock, inbox_lock.atomic_update

    @contextlib.contextmanager
    def tracking_lock(path, *a, **k):
        with real_lock(path, *a, **k):
            held.append(Path(path).resolve())
            try:
                yield
            finally:
                held.remove(Path(path).resolve())

    def tracking_update(path, transform, *a, **k):
        seen.append((Path(path).resolve(), list(held)))
        return real_update(path, transform, *a, **k)
    monkeypatch.setattr(inbox_lock, "file_lock", tracking_lock)
    monkeypatch.setattr(inbox_lock, "atomic_update", tracking_update)
    rc, out, _ = _cli(capsys, wt, "set", "1.F2", "--disposition", "parked", "--why", "w")
    assert rc == 0 and out["mirrored"] is True
    main_updates = [h for p, h in seen if p == fw.ledger_path(main).resolve()]
    assert main_updates and fw.ledger_path(wt).resolve() in main_updates[0]


def _two_runs(wt, main, s1, s2, mirror_both=True):
    r1 = {**W_ROW, "findings": [{"id": "F1", "severity": "P1", "summary": s1, "disposition": None}]}
    r2 = {**W_ROW, "findings": [{"id": "F1", "severity": "P1", "summary": s2, "disposition": None}]}
    _write_ledger(wt, [r1, r2])
    mirrors = [{**r1, "worktree": str(wt)}] + ([{**r2, "worktree": str(wt)}] if mirror_both else [])
    _write_ledger(main, [MAIN_OWN] + mirrors)


def test_runs_sharing_a_key_each_reach_only_their_own_mirror(mirrored, capsys):
    """F2: two runs with one (report, model, recorded) are told apart by their findings."""
    main, wt = mirrored
    _two_runs(wt, main, "first", "second")
    rc, out, _ = _cli(capsys, wt, "set", "2.F1", "--disposition", "parked", "--why", "run two")
    assert rc == 0 and out["mirrored"] is True
    rows = _rows(main)
    assert rows[1]["findings"][0].get("disposition") is None          # run one untouched
    assert rows[2]["findings"][0]["why"] == "run two"


def test_a_set_never_lands_on_another_runs_mirror_with_the_same_key(mirrored, capsys):
    """F2, Codex's reproduction: main holds only run one's mirror (as the old dedupe left
    it). A set on run two must not be written onto run one's finding."""
    main, wt = mirrored
    _two_runs(wt, main, "first", "second", mirror_both=False)
    _main_unchanged_after_set_addr(capsys, main, wt, "2.F1", [W_ROW["report"]])


def test_two_runs_with_identical_findings_are_ambiguous(mirrored, capsys):
    main, wt = mirrored
    _two_runs(wt, main, "same", "same")
    _main_unchanged_after_set_addr(capsys, main, wt, "2.F1", ["2 mirrored rows"])


def test_a_disposition_on_one_run_leaves_the_other_runs_match_intact(mirrored, capsys):
    """b2's edge case (ii): dispositions are not in the signature, so after run one is
    dispositioned (in both copies) run two still matches only its own mirror, and run one
    still matches its own on a later --force."""
    main, wt = mirrored
    _two_runs(wt, main, "first", "second")
    for args in (("1.F1", "parked", "one"), ("2.F1", "parked", "two"), ("1.F1", "fixed", "one again")):
        rc, out, _ = _cli(capsys, wt, "set", args[0], "--disposition", args[1], "--why", args[2], "--force")
        assert rc == 0 and out["mirrored"] is True
    rows = _rows(main)
    assert (rows[1]["findings"][0]["why"], rows[2]["findings"][0]["why"]) == ("one again", "two")


def _main_unchanged_after_set_addr(capsys, main, wt, addr, expect_in_err):
    before = fw.ledger_path(main).read_bytes()
    rc, out, err = _cli(capsys, wt, "set", addr, "--disposition", "parked", "--why", "w")
    assert rc == 0 and out["mirrored"] is False
    assert fw.ledger_path(main).read_bytes() == before
    for s in expect_in_err:
        assert s in err


# --- Part B amendment 2 (org, 2026-11-16): mirrors carry worktree_run; fail closed ---------

def _pos_mirror(wt, row, run):
    return {**row, "worktree": str(wt), "worktree_run": run}


def test_a_positioned_mirror_is_matched_by_its_run_number(mirrored, capsys):
    main, wt = mirrored
    _write_ledger(main, [MAIN_OWN, _pos_mirror(wt, W_ROW, 1)])
    rc, out, _ = _cli(capsys, wt, "set", "1.F2", "--disposition", "parked", "--why", "w")
    assert rc == 0 and out["mirrored"] is True
    assert _rows(main)[1]["findings"][1]["disposition"] == "parked"


def test_a_legacy_mirror_without_worktree_run_still_syncs_by_identity(mirrored, capsys):
    """Rows mirrored before amendment 2 (main row 951 on 2026-11-16) have no worktree_run;
    they keep the identity-only match."""
    main, wt = mirrored
    assert "worktree_run" not in _rows(main)[1]
    rc, out, _ = _cli(capsys, wt, "set", "1.F2", "--disposition", "parked", "--why", "w")
    assert rc == 0 and out["mirrored"] is True


def test_two_colliding_legacy_mirrors_still_warn_and_write_nothing(mirrored, capsys):
    """Defensive: two legacy mirrors with one identity (no position to tell them apart)."""
    main, wt = mirrored
    m = {**W_ROW, "worktree": str(wt)}
    _write_ledger(main, [MAIN_OWN, m, m])
    _main_unchanged_after_set(capsys, main, wt, ["2 mirrored rows"])


def test_a_position_past_the_end_of_the_worktree_ledger_refuses(mirrored, capsys):
    """Fail closed: a mirror pointing past the worktree ledger means positions shifted."""
    main, wt = mirrored
    _write_ledger(main, [MAIN_OWN, _pos_mirror(wt, W_ROW, 1), _pos_mirror(wt, W_ROW, 5)])
    _main_unchanged_after_set(capsys, main, wt, ["REFUSED", "past the end"])


def test_a_position_whose_identity_differs_refuses(mirrored, capsys):
    main, wt = mirrored
    other = {**W_ROW, "report": "output/analysis/someone-else.md"}
    _write_ledger(main, [MAIN_OWN, _pos_mirror(wt, other, 1)])
    _main_unchanged_after_set(capsys, main, wt, ["REFUSED", "identity"])


def test_this_runs_identity_at_another_position_refuses(mirrored, capsys):
    """The run's identity is mirrored, but at run 2 while the address is run 1: a shift."""
    main, wt = mirrored
    _write_ledger(wt, [W_ROW, dict(ROW_2)])
    _write_ledger(main, [MAIN_OWN, _pos_mirror(wt, W_ROW, 2)])
    _main_unchanged_after_set(capsys, main, wt, ["REFUSED", "run 2"])
