"""One definition of due: the scanner, the ledger and learning_state give the same due set.

Nick, 2026-11-22: "I only want to have 1 definition of due." On 2026-11-25 three readers
gave three answers on the same live tier: scan_promotion_candidates 150 (it parsed the
prose reopen_gate), learning_ledger 248 (open and 2 or more fires) and
learning_state.memory_state 140 (the typed reopen_at). Friction: ledger 413 (the rung
cell), learning_state.friction_state 300. Both readers now import learning_state; this
test holds them there, on a fixture and on the live tier.

The live test is marked live_data, so the merge-push preflight runs it in the main checkout
(land_branch.PREFLIGHT_RUNS runs `-m live_data`) and it skips in a linked worktree.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import friction_log  # noqa: E402
import learning_ledger as ll  # noqa: E402
import learning_state as ls  # noqa: E402
import memory_frontmatter as mf  # noqa: E402
from hook_runtime import claude_project_dir  # noqa: E402
from vault_paths import main_checkout  # noqa: E402

SCANNER = REPO_ROOT / "tools" / "scan_promotion_candidates.py"
# Derived, never one machine's folder name: the live tier belongs to the main checkout.
LIVE_MEMORY = claude_project_dir(main_checkout(REPO_ROOT)) / "memory"


def learning_state_due(memory_dir: Path) -> set[str]:
    out = set()
    for p in sorted(memory_dir.glob("*.md")):
        fields, defect = mf.read(p.read_text(encoding="utf-8", errors="replace"))
        if not defect and ls.memory_state(fields, memory_dir=memory_dir).due == "due":
            out.add(p.name)
    return out


def scanner_due(memory_dir: Path, repo_root: Path) -> set[str]:
    """The scanner's READ path only. Interactive mode prints the report and exits before
    the two writes (the backlog file and the to-do, both after the interactive exit and
    both behind --dry-run), and --dry-run is passed as well. The live test below also
    proves the tier is byte-identical after the run (org ce, 2026-11-25: a test harness
    must not become the way around check_memory_frontmatter_write)."""
    r = subprocess.run([sys.executable, str(SCANNER), "--memory-dir", str(memory_dir),
                        "--repo-root", str(repo_root), "--mode", "interactive",
                        "--dry-run", "--min-coverage", "0"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    due = {c["file"] for c in report["promotion_candidates"] if c["gate_state"] == "due"}
    assert report["due_count"] == len(due)
    return due


def ledger_due(memory_dir: Path) -> set[str]:
    return {r["ref"] for r in ll.read_memory(memory_dir) if r["due"]}


def rule(occ, reopen_at=None, promoted="no", gate="3rd fire", extra=""):
    reopen = "" if reopen_at is None else f"  reopen_at: {reopen_at}\n"
    return (f'---\nname: x\ndescription: "a rule"\nmetadata:\n  type: feedback\n'
            f'  occurrences: {occ}\n  promoted: "{promoted}"\n  reopen_gate: "{gate}"\n'
            f'{reopen}{extra}---\nBody\n')


FIXTURE = {
    # name: (text, due?)
    "feedback_due.md": (rule(3, 3), True),
    "feedback_prose_says_due.md": (rule(2, 5, gate="TRIPPED 2026-09-14"), False),
    "feedback_prose_says_not.md": (rule(2, 2, gate="10th fire"), True),
    "feedback_no_number.md": (rule(6), False),
    "feedback_once.md": (rule(1, 2), False),
    "feedback_closed.md": (rule(9, 2, promoted="yes -- hook"), False),
    "feedback_partial.md": (rule(4, 3, promoted="partial -- half"), True),
    "feedback_terminal.md": (rule(9, 2, extra="  terminal: true\n"), False),
    "reference_counted.md": (rule(4, 4), True),
    "feedback_bad_reopen.md": (rule(4, "three"), False),
    # Codex s1 r1 F1: the scanner's legacy population (2+ fires, its own promoted reader)
    # dropped both of these before judging due.
    "feedback_due_at_one.md": (rule(1, 1), True),
    "feedback_unknown_promoted.md": (rule(2, 2, promoted="n/a"), True),
    # Codex s1 r2 F1: the scanner's filename filter dropped a record before judging it.
    "archive-live-rule.md": (rule(2, 2), True),
    "index-not-a-record.md": ("---\nname: idx\ndescription: an index\n---\n- a list\n", False),
    # Grok s1 F1: the scanner's strict UTF-8 read and its own parser hid these two.
    "feedback_bad_byte.md": (rule(2, 2).encode("utf-8") + b"Body \xff\n", True),
    "feedback_flow_style.md": ("---\nname: x\ndescription: a rule\nmetadata: {type: feedback, "
                               "occurrences: 2, promoted: 'no', reopen_at: 2}\n---\nBody\n", True),
    # 2026-11-25 live: archive-2026-10.md has no frontmatter and "occurrences:" lines in its
    # body; the ledger's old 6,000-character scrape counted it as a record.
    "archive-rollup.md": ("# Archive\n- old rule\n  occurrences: 4\n  promoted: no\n", False),
}


def _fixture(tmp_path) -> Path:
    d = tmp_path / "memory"
    d.mkdir()
    for name, (text, _) in FIXTURE.items():
        if isinstance(text, bytes):
            (d / name).write_bytes(text)
        else:
            (d / name).write_text(text, encoding="utf-8")
    return d


def test_three_readers_agree_on_a_fixture(tmp_path):
    d = _fixture(tmp_path)
    expected = {n for n, (_, due) in FIXTURE.items() if due}
    assert learning_state_due(d) == expected
    assert ledger_due(d) == expected
    assert scanner_due(d, tmp_path) == expected


def test_the_ledger_and_the_scanner_read_learning_states_population(tmp_path):
    """One population (learning_state.iter_records): the ledger's memory records are exactly
    its records, and every scanner candidate is one of them."""
    d = _fixture(tmp_path)
    population = {r.path.name for r in ls.iter_records(d)}
    assert "archive-rollup.md" not in population and "index-not-a-record.md" not in population
    assert {"feedback_bad_byte.md", "feedback_flow_style.md", "archive-live-rule.md"} <= population
    assert {r["ref"] for r in ll.read_memory(d)} == population
    r = subprocess.run([sys.executable, str(SCANNER), "--memory-dir", str(d), "--repo-root",
                        str(tmp_path), "--mode", "interactive", "--dry-run", "--min-coverage", "0"],
                       capture_output=True, text=True)
    assert {c["file"] for c in json.loads(r.stdout)["promotion_candidates"]} <= population


def test_lessons_due_is_parse_lessons_row_and_lessons_state(tmp_path):
    """Grok s1 F4: the ledger's Section 2 due equals learning_state's row reader."""
    lines = ["| Pattern | Rule | Occurrences | Promoted | Date |", "|---|---|---|---|---|",
             "| a | r | 2 | No | 2026-08-13 |", "| b | r | 1 | No | 2026-08-13 |",
             "| c | r | 4 | No | 2026-08-13 ||", "| d | r | 3 | Yes | 2026-08-13 |",
             "| e | r | 5 | partial -- half | 2026-08-13 |"]
    p = tmp_path / "lessons.md"
    p.write_text("## Section 2\n" + "\n".join(lines) + "\n", encoding="utf-8")
    want = set()
    for line in lines:
        row, _ = ls.parse_lessons_row(line)
        if row is not None and ls.lessons_state(row)[1] == "due":
            want.add(row[0])
    got = {r["what"] for r in ll.read_lessons(p) if r["due"]}
    assert got == want == {"a", "e"}


def test_friction_due_is_friction_state_on_a_fixture(tmp_path):
    p = tmp_path / "friction-log.md"
    p.write_text(
        "| Date | Surface | Nature | Fix | Occurrences | Promotion | Kind |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 2026-08-01 | a.py | x | f | 2 | — | friction |\n"
        "| 2026-08-01 | b.py | x | RESOLVED 2026-09-01 | 5 | script-patch (mandatory) | friction |\n"
        "| 2026-08-01 | c.py | x | f | 5 | script-patch (mandatory) | harness |\n", encoding="utf-8")
    assert [r["ref"] for r in ll.read_friction(p) if r["due"]] == ["a.py:2026-08-01"]


def tier_snapshot(memory_dir: Path) -> dict[str, tuple[int, str]]:
    """name -> (mtime_ns, sha256) for every file directly in the folder."""
    return {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(memory_dir.iterdir()) if p.is_file()}


def test_the_scanner_read_path_writes_nothing(tmp_path):
    d = _fixture(tmp_path)
    before = tier_snapshot(d)
    scanner_due(d, tmp_path)
    assert tier_snapshot(d) == before
    assert not (d / "promotion-backlog.md").exists()


@pytest.mark.live_data(LIVE_MEMORY)
def test_three_readers_agree_on_the_live_tier(capsys):
    before = tier_snapshot(LIVE_MEMORY)
    want = learning_state_due(LIVE_MEMORY)
    got_ledger = ledger_due(LIVE_MEMORY)
    got_scanner = scanner_due(LIVE_MEMORY, REPO_ROOT)
    assert tier_snapshot(LIVE_MEMORY) == before, "the parity run changed the live tier"
    assert got_ledger == want, sorted(got_ledger ^ want)[:20]
    assert got_scanner == want, sorted(got_scanner ^ want)[:20]
    with capsys.disabled():
        print(f"\n[due parity] live tier: learning_state {len(want)}, ledger "
              f"{len(got_ledger)}, scanner {len(got_scanner)}")


@pytest.mark.live_data("memory/friction-log.md")
def test_ledger_friction_due_matches_friction_state_on_the_live_ledger(capsys):
    path = REPO_ROOT / "memory" / "friction-log.md"
    _, _, rows, _ = friction_log.read_ledger()
    want = 0
    for row in rows:
        d = friction_log.parse_row(row)
        if d and ls.friction_state(d["kind"], d["fix"], d["occurrences"]) == "due":
            want += 1
    got = sum(1 for r in ll.read_friction(path) if r["due"])
    assert got == want
    with capsys.disabled():
        print(f"\n[due parity] live friction: ledger {got}, friction_state {want} "
              f"of {len(rows)} rows")
