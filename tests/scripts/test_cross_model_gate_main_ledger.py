"""The push gate, run from a linked worktree, also reads the main checkout's ledger (S2d).

Origin (overnight 2026-11-25): S5's Codex and Grok rows at tree 734f1e8f were written
from a review worktree. They reached that worktree's ledger and main's mirror, never the
branch worktree whose pre-push gate reads its own copy, so land_branch refused and both
reviews were repeated. Org ruling (b7): read side. main's ledger is the copy every review
is mirrored into and finding_write keeps in step; on the same run the worktree's row wins;
main's ledger missing or unreadable fails closed with a stated reason.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from tools import cross_model_gate as g  # noqa: E402

PATH = "tools/career_scanner/scanner.py"
CHANGE = [(PATH, 200, 40)]


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


@pytest.fixture()
def repos(tmp_path):
    main, wt = tmp_path / "main", tmp_path / "wt"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "base")
    _git(main, "worktree", "add", "-q", "-b", "branch", str(wt))
    return main, wt


def _row(root, **kw):
    row = {"recorded": "2026-11-25T10:00:00+00:00", "model": "codex",
           "report": "output/analysis/112526-codex-x.md", "paths": [PATH],
           "findings": [], "waived": False, "verified": True}
    row.update(kw)
    p = g.ledger_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def _open_p0():
    return [{"id": "F1", "severity": "P0", "location": f"{PATH}:10",
             "summary": "a real defect", "disposition": None}]


def test_a_row_only_in_main_covers_the_worktree_push(repos):
    main, wt = repos
    _row(wt, report="output/analysis/unrelated.md", paths=["docs/x.md"])   # wt ledger exists
    assert g.check(wt, CHANGE, since=0).blocked is True                    # red before main's row
    _row(main, worktree="/elsewhere/review-wt")
    v = g.check(wt, CHANGE, since=0)
    assert v.blocked is False, v.message


def test_the_cli_from_a_worktree_with_the_default_repo_root_reads_main(repos, tmp_path,
                                                                        monkeypatch, capsys):
    """S1's 11-26 evidence: the gate run from s4d's worktree with no --repo-root BLOCKED,
    because the default (the worktree holding the gate) read only that worktree's ledger;
    only --repo-root <main> passed. The CLI default must reach main's row too."""
    main, wt = repos
    _row(main, worktree="/elsewhere/review-wt")
    ns = tmp_path / "ns.txt"
    ns.write_text(f"200\t40\t{PATH}\n", encoding="utf-8")
    monkeypatch.setattr(g, "REPO_ROOT", wt)              # the gate file's own checkout
    monkeypatch.delenv("CODEX_VERIFY_WAIVE", raising=False)
    rc = g.main(["--numstat", str(ns)])
    assert rc == 0, capsys.readouterr()


def test_an_open_p0_only_in_main_on_the_pushed_paths_blocks(repos):
    main, wt = repos
    _row(wt)                                                               # covers on its own
    _row(main, report="output/analysis/unrelated.md", paths=["docs/x.md"])  # main ledger exists
    assert g.check(wt, CHANGE, since=0).blocked is False
    _row(main, report="output/analysis/112526-grok-x.md", model="grok", findings=_open_p0())
    assert g.check(wt, CHANGE, since=0).blocked is True


def test_the_worktree_copy_wins_over_main_on_the_same_run(repos):
    main, wt = repos
    fixed = _open_p0()
    fixed[0]["disposition"] = "fixed"
    _row(wt, findings=fixed)                                    # dispositioned here
    _row(main, findings=_open_p0(), worktree=str(wt))           # stale mirror: still open
    rows, corrupt, note = g.read_gate_ledger(wt)
    same = [r for r in rows if r["report"] == "output/analysis/112526-codex-x.md"]
    assert len(same) == 1 and same[0]["findings"][0]["disposition"] == "fixed"
    assert corrupt == 0 and note == ""
    assert g.check(wt, CHANGE, since=0).blocked is False


def test_another_models_p0_in_main_with_the_same_report_and_second_still_blocks(repos):
    """Codex 11-26 F1 (P0): open_p0_against treated (report, recorded) as a run's
    identity, so a worktree Codex row suppressed main's Grok P0 that shared both. The
    run identity is codex_verify.row_identity, which includes the model."""
    main, wt = repos
    shared = {"report": "output/analysis/shared.md"}
    _row(wt, **shared)                                                       # codex, covers
    _row(main, report="output/analysis/unrelated.md", paths=["docs/x.md"])
    _row(main, **shared, model="grok", findings=_open_p0())                  # same report+second
    assert len(g.open_p0_against(wt, {PATH})) == 1
    assert g.check(wt, CHANGE, since=0).blocked is True


def test_a_distinct_main_run_in_the_same_second_keeps_its_p0(repos):
    """Codex 11-27 1656.F1 (P0): the gate keyed a run as (report, model, recorded) only.
    `recorded` has whole seconds, so a worktree row and a DISTINCT main-ledger run that
    reuse one report path in one second collided: main's row was dropped from the merge
    and its open P0 with it, and the push passed. The run identity is
    codex_verify.row_identity, which adds the findings signature."""
    main, wt = repos
    _row(wt)                                                     # covers, no findings
    _row(main, findings=_open_p0())                              # same report/model/second
    assert len(g.open_p0_against(wt, {PATH})) == 1
    assert g.check(wt, CHANGE, since=0).blocked is True


def test_a_distinct_main_run_in_the_same_second_is_merged(repos):
    """The read_gate_ledger half of 1656.F1: the distinct main run is merged, not
    dropped as if it were this worktree's own row."""
    main, wt = repos
    _row(wt)
    _row(main, findings=_open_p0())
    rows, corrupt, _ = g.read_gate_ledger(wt)
    assert corrupt == 0 and len(rows) == 2


def _fixed_p0():
    f = _open_p0()
    f[0]["disposition"] = "fixed"
    return f


def test_a_distinct_main_run_with_identical_findings_keeps_its_p0(repos):
    """Codex 11-27 s2d 5.F1 (P0): row_identity alone cannot tell apart two runs whose
    findings are identical (codex_verify.mirror_to_main says the same, r2 F1). A main run
    in the same second with the same report, model and finding, still open there, was
    taken for this worktree's mirror and its P0 dropped. A main row is this worktree's
    own run only if it names this worktree (codex_verify.is_mirror_of)."""
    main, wt = repos
    _row(wt, findings=_fixed_p0())
    _row(main, findings=_open_p0())                         # main-native: names no worktree
    assert len(g.open_p0_against(wt, {PATH})) == 1
    assert g.check(wt, CHANGE, since=0).blocked is True


def test_an_identical_run_mirrored_from_another_worktree_is_merged(repos, tmp_path):
    main, wt = repos
    _row(wt, findings=_fixed_p0())
    _row(main, findings=_open_p0(), worktree=str(tmp_path / "other-review-wt"))
    rows, corrupt, _ = g.read_gate_ledger(wt)
    assert corrupt == 0 and len(rows) == 2
    assert len(g.open_p0_against(wt, {PATH})) == 1


def test_a_mirror_whose_run_number_names_another_run_is_not_ours(repos):
    """worktree_run is the run's position in this worktree's ledger; a mirror that claims
    a position holding a different run is not that run's copy, so its P0 counts."""
    main, wt = repos
    _row(wt, report="output/analysis/unrelated.md", paths=["docs/x.md"])    # run 1
    _row(wt, findings=_fixed_p0())                                          # run 2
    _row(main, findings=_open_p0(), worktree=str(wt), worktree_run=1)
    assert len(g.open_p0_against(wt, {PATH})) == 1


def test_a_mirror_with_its_run_number_is_still_ours(repos):
    main, wt = repos
    _row(wt, report="output/analysis/unrelated.md", paths=["docs/x.md"])    # run 1
    _row(wt, findings=_fixed_p0())                                          # run 2
    _row(main, findings=_open_p0(), worktree=str(wt), worktree_run=2)       # stale mirror
    assert g.open_p0_against(wt, {PATH}) == []
    rows, _, _ = g.read_gate_ledger(wt)
    assert len(rows) == 2


def test_a_malformed_main_line_does_not_unmatch_the_runs_after_it(repos):
    """Main's rows are numbered as collect() numbers them; a line that does not parse is
    skipped, and the stale mirror after it is still recognised as this worktree's run."""
    main, wt = repos
    fixed = _open_p0()
    fixed[0]["disposition"] = "fixed"
    _row(wt, findings=fixed)
    p = g.ledger_path(main)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json\n", encoding="utf-8")
    _row(main, findings=_open_p0(), worktree=str(wt))           # stale mirror, line 2
    assert g.open_p0_against(wt, {PATH}) == []


def test_a_waiver_in_main_does_not_cover_a_worktree_push(repos):
    """Fable 11-26 F1: read_gate_ledger merged main's waiver rows, so a waiver typed for
    a push from main, on overlapping paths and after the branch's commits, cleared the
    branch's push from its worktree. A waiver clears only its own push."""
    main, wt = repos
    _row(wt, report="output/analysis/unrelated.md", paths=["docs/x.md"])
    g.record_waiver(main, [PATH], "a push from main")
    rows, corrupt, _ = g.read_gate_ledger(wt)
    assert corrupt == 0 and not any(r.get("waived") for r in rows)
    assert g.check(wt, CHANGE, since=0).blocked is True


def test_a_waiver_in_the_worktrees_own_ledger_is_still_read(repos):
    main, wt = repos
    _row(main, report="output/analysis/unrelated.md", paths=["docs/x.md"])
    g.record_waiver(wt, [PATH], "this push")
    rows, _, _ = g.read_gate_ledger(wt)
    assert sum(1 for r in rows if r.get("waived")) == 1


def test_main_ledger_missing_fails_closed_with_a_reason(repos):
    main, wt = repos
    _row(wt)
    rows, corrupt, note = g.read_gate_ledger(wt)
    assert corrupt == 1 and "missing" in note
    v = g.check(wt, CHANGE, since=0)
    assert v.blocked is True and "main checkout's ledger is missing" in v.message


def test_main_ledger_unreadable_fails_closed(repos):
    main, wt = repos
    _row(wt)
    p = g.ledger_path(main)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xfe not json\n")
    v = g.check(wt, CHANGE, since=0)
    assert v.blocked is True
    rows, corrupt, note = g.read_gate_ledger(wt)
    assert corrupt >= 1 and note


def test_main_located_from_git_common_dir_not_a_fixed_path(repos):
    main, wt = repos
    assert g.main_checkout_of(wt) == main.resolve()
    assert g.main_checkout_of(main) is None                # the main checkout reads only its own


def test_outside_a_linked_worktree_nothing_changes(tmp_path):
    _row(tmp_path)
    rows, corrupt, note = g.read_gate_ledger(tmp_path)
    assert len(rows) == 1 and corrupt == 0 and note == ""



def test_a_linked_worktree_that_cannot_be_placed_raises(repos, tmp_path):
    """Fails closed: vault_paths.main_checkout falls back to the worktree itself when its
    .git file names no main checkout, and the wrapper must raise rather than return it
    (that fallback would read the worktree's own ledger as main's). 11-26 S4."""
    main, wt = repos
    (wt / ".git").write_text(f"gitdir: {tmp_path / 'nowhere' / '.git' / 'worktrees' / 'w'}\n",
                             encoding="utf-8")
    with pytest.raises(RuntimeError):
        g.main_checkout_of(wt)


def test_main_that_cannot_be_located_blocks_with_a_reason(repos, monkeypatch):
    """Kills the RETURN_NONE survivor on read_gate_ledger's locate-failure return."""
    main, wt = repos
    _row(wt)

    def boom(root):
        raise RuntimeError("git did not name a .git common dir")
    monkeypatch.setattr(g, "main_checkout_of", boom)
    rows, corrupt, note = g.read_gate_ledger(wt)
    assert len(rows) == 1 and corrupt == 1 and "could not be located" in note
    v = g.check(wt, CHANGE, since=0)
    assert v.blocked is True and "could not be located" in v.message


def test_a_push_from_the_main_checkout_reads_its_ledger_once(repos):
    """b7 check on b03b3ef6: in the main checkout .git is a directory, so main_checkout_of
    returns None at its first test and only main's own ledger is read: once, no doubled
    rows, no unreadable line, and a covering row clears the push."""
    main, wt = repos
    _row(main)
    assert g.main_checkout_of(main) is None
    rows, corrupt, note = g.read_gate_ledger(main)
    assert len(rows) == 1 and corrupt == 0 and note == ""
    v = g.check(main, CHANGE, since=0)
    assert v.blocked is False, v.message


def test_main_checkout_with_an_open_p0_counts_it_once(repos):
    main, wt = repos
    _row(main)
    _row(main, report="output/analysis/112526-grok-x.md", model="grok", findings=_open_p0())
    blocking = g.open_p0_against(main, {PATH})
    assert len(blocking) == 1
