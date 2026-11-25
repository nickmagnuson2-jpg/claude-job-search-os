"""tests for tools/lessons_write.py: the sanctioned writer for memory/lessons.md Section 2.

The real entry point runs in a subprocess against a temporary lessons file (LESSONS_PATH).
Fixture rows are invented.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "lessons_write.py"

LONG = "x" * 600   # rows over 500 characters are the reason Edit is not the route

LESSONS = f"""# Lessons

## Section 1 — General Corrections

| # | Pattern (what went wrong) | Rule (what to do instead) | Date |
|---|--------------------------|---------------------------|------|
| 1 | an invented general pattern | an invented general rule | 2026-01-01 |

---

## Section 2 — Email / Outreach Corrections

| Pattern | Rule | Occurrences | Promoted | Date |
|---------|------|-------------|----------|------|
| Invented pattern alpha about a nudge to Casey Doe at Acme {LONG} | Invented rule alpha | 1 | No | 2026-11-01 |
| Invented pattern beta about a thank-you note | Invented rule beta | 2 | No | 2026-10-01 |
| Invented pattern gamma about a thank-you note too | Invented rule gamma | 1 | No | 2026-09-01 |
"""


def run(args, path: Path, memory_dir: Path | None = None):
    env = {**os.environ, "LESSONS_PATH": str(path), "PYTHONIOENCODING": "utf-8",
           "MEMORY_DIR": str(memory_dir or path.parent / "no-memory-here")}
    p = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                       env=env, timeout=60)
    out = json.loads(p.stdout) if p.stdout.strip().startswith("{") else None
    return p.returncode, out, p


@pytest.fixture
def lessons(tmp_path):
    f = tmp_path / "lessons.md"
    f.write_text(LESSONS, encoding="utf-8")
    return f


def lines_of(f):
    return f.read_text(encoding="utf-8").split("\n")


# ------------------------------------------------------------------ set-promoted

def test_set_promoted_rewrites_only_that_cell_on_a_long_row(lessons):
    code, out, p = run(["set-promoted", "Invented pattern alpha", "Yes: encoded (voice guide, 2026-11-22)"], lessons)
    assert code == 0, p.stdout + p.stderr
    assert out["action"] == "set-promoted"
    assert out["old"] == "No"
    assert out["new"] == "Yes: encoded (voice guide, 2026-11-22)"
    before, after = LESSONS.split("\n"), lines_of(lessons)
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(before) == len(after)
    assert changed == [out["line"] - 1]
    assert after[changed[0]] == (f"| Invented pattern alpha about a nudge to Casey Doe at Acme {LONG} | "
                                 "Invented rule alpha | 1 | Yes: encoded (voice guide, 2026-11-22) | 2026-11-01 |")


def test_set_promoted_refuses_a_bad_token(lessons):
    code, out, _ = run(["set-promoted", "Invented pattern alpha", "Probably"], lessons)
    assert code == 1
    assert out["action"] == "refused"
    assert lessons.read_text(encoding="utf-8") == LESSONS


def test_set_promoted_refuses_a_pointer_without_target(lessons):
    code, out, _ = run(["set-promoted", "Invented pattern alpha", "superseded"], lessons)
    assert code == 1
    assert lessons.read_text(encoding="utf-8") == LESSONS


@pytest.mark.parametrize("match,needle", [
    ("about a thank-you", "matches 2 Section 2 rows"),
    ("no row has this text", "matches 0 Section 2 rows"),
    ("short", "at least 12 characters"),
    ("an invented general pattern", "matches 0 Section 2 rows"),   # Section 1 is out of scope
])
def test_set_promoted_needs_exactly_one_section2_row(lessons, match, needle):
    code, out, _ = run(["set-promoted", match, "No"], lessons)
    assert code == 1
    assert any(needle in r for r in out["reasons"])
    assert lessons.read_text(encoding="utf-8") == LESSONS


def test_set_promoted_refuses_a_pipe_in_the_value(lessons):
    code, out, _ = run(["set-promoted", "Invented pattern alpha", "Yes | hook"], lessons)
    assert code == 1
    assert "Promoted: may not contain '|' or a newline" in out["reasons"]
    assert lessons.read_text(encoding="utf-8") == LESSONS


# ------------------------------------------------------------------ append-row

def row_args(**over):
    base = {"pattern": "Invented new pattern", "rule": "Invented new rule",
            "occurrences": "1", "promoted": "No", "date": "2026-11-22"}
    base.update(over)
    out = []
    for k, v in base.items():
        out += [f"--{k}", v]
    return out


def test_append_row_inserts_at_top_of_section2(lessons):
    code, out, p = run(["append-row", *row_args()], lessons)
    assert code == 0, p.stdout + p.stderr
    after = lines_of(lessons)
    header = after.index("| Pattern | Rule | Occurrences | Promoted | Date |")
    assert after[header + 2] == "| Invented new pattern | Invented new rule | 1 | No | 2026-11-22 |"
    assert out["line"] == header + 3
    assert len(after) == len(LESSONS.split("\n")) + 1
    assert [ln for ln in after if ln != after[header + 2]] == LESSONS.split("\n")


@pytest.mark.parametrize("over,needle", [
    ({"date": "Nov 22"}, None),
    ({"occurrences": "twice"}, None),
    ({"promoted": "Someday"}, None),
    ({"rule": "a | b"}, "Rule: may not contain '|' or a newline"),
    ({"pattern": "line one\nline two"}, "Pattern: may not contain '|' or a newline"),
    ({"pattern": "  "}, "Pattern: empty"),
])
def test_append_row_refuses_an_invalid_row(lessons, over, needle):
    code, out, _ = run(["append-row", *row_args(**over)], lessons)
    assert code == 1
    assert out["action"] == "refused"
    if needle:
        assert needle in out["reasons"]
    assert lessons.read_text(encoding="utf-8") == LESSONS


# ------------------------------------------------------------------ file shape

def test_missing_file_is_refused(tmp_path):
    code, out, _ = run(["append-row", *row_args()], tmp_path / "absent.md")
    assert code == 1
    assert out["reasons"] == [f"no such file: {tmp_path / 'absent.md'}"]


@pytest.mark.parametrize("broken,needle", [
    (LESSONS.replace("## Section 2", "## Section Two"), "expected exactly one '## Section 2' heading, found 0"),
    (LESSONS + "\n## Section 2 again\n", "found 2"),
    (LESSONS.replace("| Pattern | Rule | Occurrences | Promoted | Date |", "| Pattern | Rule | Promoted | Date |"),
     "Section 2 header is"),
    (LESSONS.replace("|---------|------|-------------|----------|------|\n", ""), "no separator row"),
], ids=["no-section2", "two-section2", "four-column-header", "no-separator"])
def test_unexpected_section2_shape_is_refused(tmp_path, broken, needle):
    f = tmp_path / "lessons.md"
    f.write_text(broken, encoding="utf-8")
    code, out, _ = run(["append-row", *row_args()], f)
    assert code == 1
    assert any(needle in r for r in out["reasons"])
    assert f.read_text(encoding="utf-8") == broken


def test_default_path_is_main_checkouts_lessons():
    sys.path.insert(0, str(REPO / "tools"))
    import lessons_write
    from vault_paths import main_checkout
    env = os.environ.pop("LESSONS_PATH", None)
    try:
        assert lessons_write.lessons_path() == main_checkout(REPO) / "memory" / "lessons.md"
    finally:
        if env is not None:
            os.environ["LESSONS_PATH"] = env


def test_append_row_accepts_a_date_cell_with_later_fires(lessons):
    """Live rows carry later fires after the first date: '2026-07-24 (6th: 2026-09-22)'."""
    code, out, p = run(["append-row", *row_args(date="2026-07-24 (2nd: 2026-09-22)")], lessons)
    assert code == 0, p.stdout + p.stderr
    assert "| Invented new pattern | Invented new rule | 1 | No | 2026-07-24 (2nd: 2026-09-22) |" in lines_of(lessons)


def test_set_promoted_refuses_a_matched_row_that_cannot_be_read(tmp_path):
    broken = LESSONS.replace("| Invented rule beta | 2 | No | 2026-10-01 |",
                             "| Invented rule beta | 2 | No | 2026-10-01 | <!-- note -->")
    f = tmp_path / "lessons.md"
    f.write_text(broken, encoding="utf-8")
    code, out, _ = run(["set-promoted", "Invented pattern beta", "No"], f)
    assert code == 1
    assert out["reasons"][0].startswith("the matched row cannot be read: ")
    assert f.read_text(encoding="utf-8") == broken


def test_section2_without_a_table_is_refused(tmp_path):
    f = tmp_path / "lessons.md"
    broken = "# Lessons\n\n## Section 2 — Email\n\nno table yet\n\n## Section 3\n\n| a | b |\n|---|---|\n"
    f.write_text(broken, encoding="utf-8")
    code, out, _ = run(["append-row", *row_args()], f)
    assert code == 1
    assert out["reasons"] == ["Section 2 has no table"]


def test_atomic_write_leaves_no_temp_file_when_replace_fails(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "tools"))
    import lessons_write
    target = tmp_path / "lessons.md"
    target.write_text("original\n", encoding="utf-8")

    def boom(src, dst):
        raise OSError("replace failed")
    monkeypatch.setattr(lessons_write.os, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        lessons_write.atomic_write(target, "new\n")
    assert target.read_text(encoding="utf-8") == "original\n"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["lessons.md"]


def test_a_merged_pointer_resolves_in_the_memory_folder(lessons, tmp_path):
    """Grok r1 F2: lessons_state was called without the memory folder, so every merged
    pointer was a defect."""
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "feedback_other_rule.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    code, out, p = run(["set-promoted", "Invented pattern beta", "merged into feedback_other_rule.md"],
                       lessons, mem)
    assert code == 0, p.stdout + p.stderr
    code, out, _ = run(["set-promoted", "Invented pattern gamma", "merged into feedback_absent_rule.md"],
                       lessons, mem)
    assert code == 1
    assert any("does not exist" in r for r in out["reasons"])


def test_default_memory_dir_is_the_main_checkouts_live_tier():
    sys.path.insert(0, str(REPO / "tools"))
    import lessons_write
    from hook_runtime import claude_project_dir
    from vault_paths import main_checkout
    env = os.environ.pop("MEMORY_DIR", None)
    try:
        assert lessons_write.memory_dir() == claude_project_dir(main_checkout(REPO)) / "memory"
    finally:
        if env is not None:
            os.environ["MEMORY_DIR"] = env


# ------------------------------------------------------------------ repair-row (org 72)

import hashlib  # noqa: E402

BROKEN = "| Invented pattern delta | Invented rule delta | 2 | Yes | 2026-08-11 | <!-- 2nd: 2026-10-13 -->"
FIXED = "| Invented pattern delta | Invented rule delta | 2 | Yes | 2026-08-11 <!-- 2nd: 2026-10-13 --> |"


@pytest.fixture
def broken(tmp_path):
    f = tmp_path / "lessons.md"
    f.write_text(LESSONS + BROKEN + "\n", encoding="utf-8")
    f.chmod(0o640)
    return f


def repair(f, *, line=None, sha=None, old=BROKEN, new=FIXED, claudecode="1"):
    lines = f.read_text(encoding="utf-8").split("\n")
    line = line or lines.index(BROKEN) + 1
    sha = sha or hashlib.sha256(f.read_bytes()).hexdigest()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    if claudecode is not None:
        env["CLAUDECODE"] = claudecode
    env.update({"LESSONS_PATH": str(f), "PYTHONIOENCODING": "utf-8",
                "MEMORY_DIR": str(f.parent / "no-memory-here")})
    p = subprocess.run([sys.executable, str(SCRIPT), "repair-row", "--line", str(line),
                        "--expect-sha", sha, "--old", old, "--new", new],
                       capture_output=True, text=True, env=env, timeout=60)
    out = json.loads(p.stdout) if p.stdout.strip().startswith("{") else None
    return p.returncode, out, p


def test_repair_row_moves_text_across_a_cell_boundary(broken):
    before = broken.read_text(encoding="utf-8")
    code, out, p = repair(broken)
    assert code == 0, p.stdout + p.stderr
    assert out["action"] == "repair-row"
    assert out["sha256_before"] == hashlib.sha256(before.encode("utf-8")).hexdigest()
    after = broken.read_text(encoding="utf-8")
    assert after == before.replace(BROKEN, FIXED)
    assert oct(broken.stat().st_mode & 0o777) == "0o640"


@pytest.mark.parametrize("kw,needle", [
    ({"new": FIXED.replace("Invented rule delta", "Invented rule DELTA")}, "changes the row's text"),
    ({"new": FIXED.replace("Invented pattern delta", "Invented pattern deltas")}, "changes the row's text"),
    ({"new": FIXED.replace("Invented pattern delta", "Inventedpattern delta")}, "changes the row's text"),
    ({"new": FIXED.replace("2nd: 2026-10-13", "2nd:2026-10-13")}, "changes the row's text"),
    ({"sha": "0" * 64}, "file changed: sha256 is"),
    ({"old": BROKEN + " "}, "does not equal --old byte for byte"),
    ({"line": 3}, "is not a Section 2 row"),
    ({"new": "| Invented pattern delta | Invented rule delta | 2 | Yes 2026-08-11 <!-- 2nd: 2026-10-13 --> |"},
     "not a readable row"),
    ({"new": FIXED.replace("| Yes |", "| Yes2026-08-11 |").replace("| 2026-08-11 <!--", "| <!--")},
     "date"),
    ({"claudecode": None}, "CLAUDECODE=1 is not set"),
    ({"claudecode": "0"}, "CLAUDECODE=1 is not set"),
])
def test_repair_row_refusals(broken, kw, needle):
    before = broken.read_text(encoding="utf-8")
    code, out, p = repair(broken, **kw)
    assert code == 1, p.stdout + p.stderr
    assert any(needle.lower() in r.lower() for r in out["reasons"]), out["reasons"]
    assert broken.read_text(encoding="utf-8") == before


def test_set_promoted_keeps_the_file_mode(lessons):
    lessons.chmod(0o640)
    code, _, _ = run(["set-promoted", "Invented pattern alpha", "Yes"], lessons)
    assert code == 0
    assert oct(lessons.stat().st_mode & 0o777) == "0o640"


@pytest.mark.parametrize("module", ["lessons_write", "memory_write", "apply_memory_verdicts"])
def test_pointers_resolve_against_the_main_checkout(module):
    """77's finding: gitignored pointer targets exist only in the main checkout, so a
    worktree run must not validate against its own tree."""
    sys.path.insert(0, str(REPO / "tools"))
    import importlib
    from vault_paths import main_checkout
    m = importlib.import_module(module)
    assert m.POINTER_ROOT == main_checkout(REPO)


def test_a_superseded_pointer_to_a_main_only_path_is_accepted_from_a_worktree(lessons, monkeypatch):
    """Run in-process with the pointer root moved to a folder holding the target, and the
    tool's own checkout lacking it: the pointer must resolve."""
    sys.path.insert(0, str(REPO / "tools"))
    import lessons_write
    main = lessons.parent / "main"
    (main / "framework").mkdir(parents=True)
    (main / "framework" / "voice-guide.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(lessons_write, "POINTER_ROOT", main)
    cells = ["p", "r", "1", "superseded by framework/voice-guide.md", "2026-11-22"]
    assert lessons_write.validate_cells(cells) == []
    monkeypatch.setattr(lessons_write, "POINTER_ROOT", lessons.parent / "elsewhere")
    assert lessons_write.validate_cells(cells) != []


# ------------------------------------------------------------------ set-promoted --expect-sha

def test_set_promoted_with_the_current_sha_writes(lessons):
    sha = hashlib.sha256(lessons.read_bytes()).hexdigest()
    code, out, p = run(["set-promoted", "Invented pattern alpha", "Yes", "--expect-sha", sha], lessons)
    assert code == 0, p.stdout + p.stderr
    assert out["sha256_before"] == sha


def test_set_promoted_with_a_stale_sha_is_refused(lessons):
    """77's apply review F2: a change between the apply's check and the write must refuse."""
    stale = hashlib.sha256(lessons.read_bytes()).hexdigest()
    lessons.write_text(LESSONS.replace("Invented rule beta", "Invented rule beta, edited"),
                       encoding="utf-8")
    changed = lessons.read_text(encoding="utf-8")
    code, out, _ = run(["set-promoted", "Invented pattern alpha", "Yes", "--expect-sha", stale], lessons)
    assert code == 1
    assert out["reasons"][0].startswith("file changed: sha256 is ")
    assert lessons.read_text(encoding="utf-8") == changed


def test_set_promoted_without_expect_sha_still_writes(lessons):
    code, out, _ = run(["set-promoted", "Invented pattern alpha", "Yes"], lessons)
    assert code == 0
    assert "sha256_before" in out


def test_repair_row_allows_whitespace_changes_at_cell_edges(broken):
    new = FIXED.replace("| Invented rule delta |", "|  Invented rule delta   |")
    code, out, p = repair(broken, new=new)
    assert code == 0, p.stdout + p.stderr


# ------------------------------------------------------------------ bump (S4, 2026-11-24)
# The 11-22 cutover left no sanctioned way to add 1 to a row's Occurrences (ff lesson 7,
# row 167). bump adds one, records dated evidence in the Pattern cell, extends the Date
# cell, and writes only through the same row validator as the other subcommands.

BETA = "Invented pattern beta about a thank-you note |"


def bump(f, *args, claudecode="1"):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    if claudecode is not None:
        env["CLAUDECODE"] = claudecode
    env.update({"LESSONS_PATH": str(f), "PYTHONIOENCODING": "utf-8",
                "MEMORY_DIR": str(f.parent / "no-memory-here")})
    p = subprocess.run([sys.executable, str(SCRIPT), "bump", *args],
                       capture_output=True, text=True, env=env, timeout=60)
    out = json.loads(p.stdout) if p.stdout.strip().startswith("{") else None
    return p.returncode, out, p


def row_with(f, needle):
    hits = [ln for ln in lines_of(f) if needle in ln]
    assert len(hits) == 1, hits
    return hits[0]


def test_bump_adds_one_and_records_dated_evidence(lessons):
    before = lessons.read_text(encoding="utf-8")
    code, out, p = bump(lessons, BETA, "--evidence", "2026-11-23: recurred on an invented follow-up")
    assert code == 0, p.stdout + p.stderr
    assert out["action"] == "bumped"
    assert (out["occurrences_before"], out["occurrences_after"]) == (2, 3)
    row = row_with(lessons, "Invented pattern beta")
    assert row == ("| Invented pattern beta about a thank-you note **3rd occurrence (2026-11-23):** "
                   "2026-11-23: recurred on an invented follow-up | Invented rule beta | 3 | No | "
                   "2026-10-01 (3rd: 2026-11-23) |")
    assert out["before"] in before and out["after"] == row
    # every other line is unchanged
    old, new = before.split("\n"), lessons.read_text(encoding="utf-8").split("\n")
    assert len(old) == len(new)
    assert [i for i, (a, b) in enumerate(zip(old, new)) if a != b] == [old.index(out["before"])]


@pytest.mark.parametrize("n, word", [(1, "2nd"), (2, "3rd"), (3, "4th"), (10, "11th"),
                                     (11, "12th"), (12, "13th"), (20, "21st"), (21, "22nd"),
                                     (22, "23rd"), (110, "111th")])
def test_bump_ordinals(tmp_path, n, word):
    f = tmp_path / "lessons.md"
    f.write_text(LESSONS.replace("| Invented rule gamma | 1 |", f"| Invented rule gamma | {n} |"),
                 encoding="utf-8")
    code, out, p = bump(f, "Invented pattern gamma", "--evidence", "2026-11-23 invented")
    assert code == 0, p.stdout
    row = row_with(f, "Invented pattern gamma")
    assert f"**{word} occurrence (2026-11-23):**" in row
    assert f"| {n + 1} | No | 2026-09-01 ({word}: 2026-11-23) |" in row


def test_bump_on_a_long_row(lessons):
    code, out, p = bump(lessons, "Invented pattern alpha", "--evidence", "2026-11-23 invented")
    assert code == 0, p.stdout
    row = row_with(lessons, "Invented pattern alpha")
    assert len(row) > 600 and "| 2 | No | 2026-11-01 (2nd: 2026-11-23) |" in row


def test_bump_dry_run_writes_nothing_and_shows_both_rows(lessons):
    before = lessons.read_bytes()
    code, out, p = bump(lessons, BETA, "--evidence", "2026-11-23 invented", "--dry-run",
                        claudecode=None)
    assert code == 0, p.stdout
    assert out["action"] == "dry-run"
    assert lessons.read_bytes() == before
    assert out["before"] == row_with(lessons, "Invented pattern beta")
    assert "| 3 | No | 2026-10-01 (3rd: 2026-11-23) |" in out["after"]
    assert out["line"] == lines_of(lessons).index(out["before"]) + 1


@pytest.mark.parametrize("args, needle", [
    ([BETA, "--evidence", "no date in this evidence"], "YYYY-MM-DD"),
    ([BETA, "--evidence", "2026-11-23 a | pipe"], "'|'"),
    ([BETA, "--evidence", "2026-11-23 a\nnewline"], "'|'"),
    ([BETA, "--evidence", "   "], "YYYY-MM-DD"),
    (["thank-you note", "--evidence", "2026-11-23 x"], "matches 2 Section 2 rows"),
    (["no such row anywhere", "--evidence", "2026-11-23 x"], "matches 0 Section 2 rows"),
    (["short", "--evidence", "2026-11-23 x"], "at least 12"),
    ([BETA, "--evidence", "2026-11-23 x", "--expect-sha", "0" * 64], "file changed"),
])
def test_bump_refusals_write_nothing(lessons, args, needle):
    before = lessons.read_bytes()
    code, out, p = bump(lessons, *args)
    assert code == 1, p.stdout + p.stderr
    assert out["action"] == "refused"
    assert any(needle in r for r in out["reasons"]), out["reasons"]
    assert lessons.read_bytes() == before


@pytest.mark.parametrize("claudecode", [None, "0"])
def test_bump_tripwire_refuses_a_write_outside_a_session(lessons, claudecode):
    before = lessons.read_bytes()
    code, out, p = bump(lessons, BETA, "--evidence", "2026-11-23 x", claudecode=claudecode)
    assert code == 1
    assert any("CLAUDECODE=1" in r for r in out["reasons"])
    assert lessons.read_bytes() == before


@pytest.mark.parametrize("occ", ["two", "-1", "1.5"])
def test_bump_refuses_a_non_integer_occurrences_cell(tmp_path, occ):
    f = tmp_path / "lessons.md"
    f.write_text(LESSONS.replace("| Invented rule beta | 2 |", f"| Invented rule beta | {occ} |"),
                 encoding="utf-8")
    before = f.read_bytes()
    code, out, p = bump(f, BETA, "--evidence", "2026-11-23 x")
    assert code == 1, p.stdout
    assert any("Occurrences" in r for r in out["reasons"]), out["reasons"]
    assert f.read_bytes() == before


def test_bump_with_the_current_sha_writes(lessons):
    sha = hashlib.sha256(lessons.read_bytes()).hexdigest()
    code, out, p = bump(lessons, BETA, "--evidence", "2026-11-23 x", "--expect-sha", sha)
    assert code == 0, p.stdout
    assert out["sha256_before"] == sha


def test_bump_refuses_a_row_that_cannot_be_read(tmp_path):
    f = tmp_path / "lessons.md"
    f.write_text(LESSONS + BROKEN + "\n", encoding="utf-8")
    before = f.read_bytes()
    code, out, p = bump(f, "Invented pattern delta", "--evidence", "2026-11-23 x")
    assert code == 1
    assert any("cannot be read" in r for r in out["reasons"]), out["reasons"]
    assert f.read_bytes() == before


def test_bump_refuses_when_the_result_fails_the_row_validator(tmp_path):
    # a Date cell that does not open with YYYY-MM-DD is a defect lessons_state reports;
    # bump must not write a row the validator rejects
    f = tmp_path / "lessons.md"
    f.write_text(LESSONS.replace("| 2 | No | 2026-10-01 |", "| 2 | No | Oct 2026 |"),
                 encoding="utf-8")
    before = f.read_bytes()
    code, out, p = bump(f, BETA, "--evidence", "2026-11-23 x")
    assert code == 1
    assert any("date" in r for r in out["reasons"]), out["reasons"]
    assert f.read_bytes() == before


def test_bump_keeps_the_file_mode(lessons):
    lessons.chmod(0o640)
    code, out, p = bump(lessons, BETA, "--evidence", "2026-11-23 x")
    assert code == 0
    assert oct(lessons.stat().st_mode & 0o777) == "0o640"


# Row 167, the first real case (ff lesson 7, 2026-11-24). Org b7 ruled the live dry run
# waits for the install; tonight's proof is this test. The row is read at run time from the
# main checkout's gitignored lessons file and copied into a tmp_path table, so its real text
# (a contact's name) never enters this tracked file. It runs from a linked worktree too
# (main_checkout resolves to main), and skips only where main's file is absent, such as a
# clean merged tree.
ROW_167_MATCH = "Claude closes a warm first-reply DM with a scheduling-mechanics offer"


def test_bump_on_row_167_real_text(tmp_path):
    sys.path.insert(0, str(REPO / "tools"))
    import learning_state as ls
    from vault_paths import main_checkout
    live = main_checkout(REPO) / "memory" / "lessons.md"
    if not live.is_file():
        pytest.skip(f"LIVE-DATA-ABSENT: {live} not present")
    rows = [ln for ln in live.read_text(encoding="utf-8").split("\n") if ROW_167_MATCH in ln]
    assert len(rows) == 1, f"row 167 match found {len(rows)} rows"
    real = rows[0]
    cells, defect = ls.parse_lessons_row(real)
    assert cells is not None, defect
    occ_before, date_before = int(cells[2]), cells[4]
    f = tmp_path / "lessons.md"
    f.write_text(LESSONS + real + "\n", encoding="utf-8")
    ev = "2026-11-24: recurred on an invented follow-up (ff lesson 7)"
    code, out, p = bump(f, ROW_167_MATCH, "--evidence", ev, "--dry-run", claudecode=None)
    assert code == 0, p.stdout + p.stderr
    assert out["before"] == real
    assert f.read_text(encoding="utf-8") == LESSONS + real + "\n"   # dry run wrote nothing
    code, out, p = bump(f, ROW_167_MATCH, "--evidence", ev)
    assert code == 0, p.stdout + p.stderr
    new_cells, defect = ls.parse_lessons_row(row_with(f, ROW_167_MATCH))
    assert new_cells is not None, defect
    n = occ_before + 1
    word = {2: "2nd", 3: "3rd"}.get(n, f"{n}th")
    assert new_cells[2] == str(n)
    assert new_cells[4] == f"{date_before} ({word}: 2026-11-24)"
    assert new_cells[0] == f"{cells[0]} **{word} occurrence (2026-11-24):** {ev}"
    assert new_cells[1] == cells[1] and new_cells[3] == cells[3]
    _disp, _due, defects = ls.lessons_state(new_cells, memory_dir=tmp_path / "no-memory-here",
                                            repo_root=main_checkout(REPO))
    assert list(defects) == []


# ------------------------------------------------------------------ lock across check and write
#
# Codex 1335.F1 / Grok 1341.F1 (P1, parked 2026-11-24): --expect-sha was checked, then the
# file was read and replaced with nothing holding it, so a write landing in between was
# overwritten. Every writer now holds inbox_lock.file_lock across read and replace, and
# re-hashes the file just before os.replace (a writer that ignores the lock is caught there).

def _inproc(monkeypatch, path, tmp_path):
    sys.path.insert(0, str(REPO / "tools"))
    import lessons_write
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(lessons_write, "POINTER_ROOT", tmp_path)
    monkeypatch.setattr(lessons_write, "memory_dir", lambda: tmp_path / "no-memory-here")
    return lessons_write


def _interpose(monkeypatch, lw, path):
    """After the writer's own read, another writer changes the file."""
    real = lw.read_lines

    def read_then_someone_writes(p):
        out = real(p)
        p.write_text(p.read_text(encoding="utf-8").replace("Invented rule gamma",
                                                           "Invented rule gamma, edited"),
                     encoding="utf-8")
        return out
    monkeypatch.setattr(lw, "read_lines", read_then_someone_writes)


def _calls(lw, path):
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    lines = path.read_text(encoding="utf-8").split("\n")
    return {
        "bump": lambda: lw.bump(path, "Invented pattern alpha", "nudge again, 2026-11-25", sha),
        "bump-no-sha": lambda: lw.bump(path, "Invented pattern alpha", "nudge again, 2026-11-25"),
        "set-promoted": lambda: lw.set_promoted(path, "Invented pattern alpha", "Yes", sha),
        "set-promoted-no-sha": lambda: lw.set_promoted(path, "Invented pattern alpha", "Yes"),
        "repair-row": lambda: lw.repair_row(path, lines.index(BROKEN) + 1, sha, BROKEN, FIXED),
        "append-row": lambda: lw.append_row(path, ["Invented pattern omega", "rule", "1",
                                                   "No", "2026-11-25"]),
    }


@pytest.mark.parametrize("cmd", ["bump", "bump-no-sha", "set-promoted", "set-promoted-no-sha",
                                 "repair-row", "append-row"])
def test_a_write_between_read_and_replace_is_refused_not_overwritten(broken, tmp_path,
                                                                     monkeypatch, cmd):
    lw = _inproc(monkeypatch, broken, tmp_path)
    call = _calls(lw, broken)[cmd]
    _interpose(monkeypatch, lw, broken)
    with pytest.raises(lw.Refused) as exc:
        call()
    assert exc.value.reasons[0].startswith("file changed while writing"), exc.value.reasons
    after = broken.read_text(encoding="utf-8")
    assert "Invented rule gamma, edited" in after          # the other writer's change survives
    assert "Invented pattern omega" not in after and "2nd occurrence" not in after
    assert sorted(x.name for x in broken.parent.iterdir() if x.name.startswith(".lessons-")) == []


@pytest.mark.parametrize("cmd", ["bump", "set-promoted", "repair-row", "append-row"])
def test_without_interference_each_writer_still_writes(broken, tmp_path, monkeypatch, cmd):
    lw = _inproc(monkeypatch, broken, tmp_path)
    before = broken.read_text(encoding="utf-8")
    _calls(lw, broken)[cmd]()
    assert broken.read_text(encoding="utf-8") != before


@pytest.mark.parametrize("cmd", ["bump", "set-promoted", "repair-row", "append-row"])
def test_each_writer_waits_for_the_lock_and_refuses_when_it_cannot_take_it(broken, tmp_path,
                                                                         monkeypatch, cmd):
    """A second lessons_write holding the lock: this one must not read or write."""
    lw = _inproc(monkeypatch, broken, tmp_path)
    monkeypatch.setattr(lw, "LOCK_TIMEOUT", 0.0)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time; sys.path.insert(0, sys.argv[1]); import inbox_lock\n"
         "with inbox_lock.file_lock(sys.argv[2]):\n print('HELD', flush=True); time.sleep(30)",
         str(REPO / "tools"), str(broken)],
        stdout=subprocess.PIPE, text=True,
        env={**os.environ, "JOBSEARCH_LOCK_DIR": str(tmp_path / "locks")})
    try:
        assert holder.stdout.readline().strip() == "HELD"
        before = broken.read_text(encoding="utf-8")
        with pytest.raises(lw.Refused) as exc:
            _calls(lw, broken)[cmd]()
        assert "locked" in exc.value.reasons[0], exc.value.reasons
        assert broken.read_text(encoding="utf-8") == before
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_main_reports_a_lock_refusal_as_json(lessons, tmp_path, monkeypatch):
    lw = _inproc(monkeypatch, lessons, tmp_path)
    monkeypatch.setenv("LESSONS_PATH", str(lessons))

    def refuse(*a, **k):
        raise lw.Refused(["lessons.md is locked by another writer"])
    monkeypatch.setattr(lw, "set_promoted", refuse)
    assert lw.main(["set-promoted", "Invented pattern alpha", "Yes"]) == 1
