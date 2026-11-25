"""tests for check_lessons_write.py (PreToolUse, Write|Edit|MultiEdit|Bash).

Runs the real hook in a subprocess against a temporary lessons file (LESSONS_HOOK_PATH).
Fixture rows are invented.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HOOK = Path(__file__).resolve().parents[2] / "tools" / "check_lessons_write.py"
TOOLS = Path(__file__).resolve().parents[2] / "tools"

LONG = "x" * 600
GOOD = "| Invented pattern alpha {} | Invented rule alpha | 1 | No | 2026-11-01 |".format(LONG)
OLD_BAD = "| Invented pattern beta | Invented rule beta | 2 | Superseded 2026-02-28 | 2026-02-28 |"
LESSONS = f"""# Lessons

## Section 1 — General Corrections

| # | Pattern | Rule | Date |
|---|---|---|---|
| 1 | an invented general pattern | an invented general rule | 2026-01-01 |

## Section 2 — Email / Outreach Corrections

| Pattern | Rule | Occurrences | Promoted | Date |
|---------|------|-------------|----------|------|
{GOOD}
{OLD_BAD}
"""
NEW_GOOD = "| Invented new pattern | Invented new rule | 1 | No | 2026-11-22 |"
NEW_BAD = "| Invented new pattern | Invented new rule | 1 | Someday | 2026-11-22 |"


@pytest.fixture
def lessons(tmp_path):
    d = tmp_path / "repo" / "memory"
    d.mkdir(parents=True)
    f = d / "lessons.md"
    f.write_text(LESSONS, encoding="utf-8")
    return f


def hook(payload: dict, lessons: Path, cwd: str | None = None):
    payload.setdefault("cwd", cwd or str(lessons.parent.parent))
    env = {**os.environ, "PYTHONPATH": str(TOOLS), "LESSONS_HOOK_PATH": str(lessons),
           "MEMORY_HOOK_REPO": str(lessons.parent.parent), "MEMORY_HOOK_DIR": str(lessons.parent / "none"),
           "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stderr


def write(path, content):
    return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": content}}


def edit(path, old, new):
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path), "old_string": old,
                                                "new_string": new}}


def bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


# ------------------------------------------------------------------ Write / Edit

def test_write_adding_a_valid_row_is_allowed(lessons):
    new = LESSONS.replace("|------|\n", f"|------|\n{NEW_GOOD}\n")
    assert hook(write(lessons, new), lessons)[0] == 0


def test_write_adding_an_invalid_row_is_blocked(lessons):
    new = LESSONS.replace("|------|\n", f"|------|\n{NEW_BAD}\n")
    code, err = hook(write(lessons, new), lessons)
    assert code == 2
    assert err.startswith("BLOCKED by check_lessons_write: memory/lessons.md Section 2")
    assert "Invented new pattern" in err
    assert "lessons_write.py set-promoted" in err
    # a block names the recurrence route too (S4 2026-11-24, org b7: a guard that blocks an
    # increment and names no route to bump is the source of the next bypass)
    assert "lessons_write.py bump <row-match> --evidence" in err


def test_an_untouched_broken_row_does_not_block(lessons):
    new = LESSONS.replace("Invented rule alpha", "Invented rule alpha, sharpened")
    code, err = hook(write(lessons, new), lessons)
    assert code == 0, err


def test_changing_a_row_into_an_invalid_one_is_blocked(lessons):
    code, err = hook(edit(lessons, "| 1 | No | 2026-11-01 |", "| 1 | Probably | 2026-11-01 |"), lessons)
    assert code == 2


@pytest.mark.parametrize("old,new", [
    ("| Invented rule alpha |", "| Invented rule | alpha |"),       # 6 cells
    ("| 2026-11-01 |", "| 2026-11-01 | <!-- note -->"),             # text after the closing pipe
])
def test_edit_breaking_the_cell_count_is_blocked(lessons, old, new):
    assert hook(edit(lessons, old, new), lessons)[0] == 2


def test_removing_the_section2_table_is_blocked(lessons):
    new = LESSONS.split("## Section 2")[0]
    code, err = hook(write(lessons, new), lessons)
    assert code == 2 and "removes the Section 2 table" in err


def test_section1_rows_are_not_judged(lessons):
    new = LESSONS.replace("| 1 | an invented general pattern", "| 2 | an edited general pattern")
    assert hook(write(lessons, new), lessons)[0] == 0


def test_other_files_are_not_judged(lessons, tmp_path):
    assert hook(write(tmp_path / "lessons.md", "anything"), lessons)[0] == 0
    assert hook(write(lessons.parent / "friction-log.md", "anything"), lessons)[0] == 0


def test_relative_path_resolves_against_cwd(lessons):
    new = LESSONS.replace("|------|\n", f"|------|\n{NEW_BAD}\n")
    assert hook(write("memory/lessons.md", new), lessons)[0] == 2


# ------------------------------------------------------------------ Bash

def test_heredoc_append_of_a_valid_row_is_allowed(lessons):
    cmd = f"cat >> {lessons} <<'EOF'\n{NEW_GOOD}\nEOF"
    code, err = hook(bash(cmd), lessons)
    assert code == 0, err


def test_echo_append_of_an_invalid_row_is_blocked(lessons):
    code, err = hook(bash(f'echo "{NEW_BAD}" >> {lessons}'), lessons)
    assert code == 2 and "Someday" in err


def test_append_with_no_row_is_blocked(lessons):
    code, err = hook(bash(f"echo 'just prose' >> {lessons}"), lessons)
    assert code == 2 and "adds no table row" in err


@pytest.mark.parametrize("cmd,needle", [
    ("cat > {f} <<'EOF'\n# replaced\nEOF", "an overwrite from Bash"),
    ("sed -i '' 's/| No |/| Yes |/' {f}", "in-place writer"),
    ("python3 - <<'PY'\np='memory/lessons.md'\nt=open(p).read()\nopen(p,'w').write(t)\nPY", "script that writes"),
    ("cp /tmp/x.md {f}", "cp into"),
])
def test_unvalidated_writers_are_blocked(lessons, cmd, needle):
    code, err = hook(bash(cmd.format(f=lessons)), lessons)
    assert code == 2, err
    assert needle in err


@pytest.mark.parametrize("cmd", [
    "cat {f}",
    "grep -n Someday {f}",
    "cp {f} /tmp/lessons-copy.md",
    "python3 - <<'PY'\nt=open('memory/lessons.md').read()\nopen('/tmp/out.txt','w').write(t)\nPY",
    "python3 - <<'PY'\nopen('output/x/reusable-lessons.md','w').write('x')\nPY",
    "PYTHONIOENCODING=utf-8 python3 {tools}/lessons_write.py append-row --pattern a --rule b --occurrences 1 --promoted No --date 2026-11-22",
])
def test_reads_and_the_sanctioned_writer_are_allowed(lessons, cmd):
    code, err = hook(bash(cmd.format(f=lessons, tools=TOOLS)), lessons)
    assert code == 0, err


@pytest.mark.parametrize("cmd", [
    "PYTHONIOENCODING=utf-8 python3 - <<'PY'\np='{f}'\nt=open(p).read()\nopen(p,'w').write(t)\nPY",
    "PYTHONIOENCODING=utf-8 python3 -c \"open('{f}','a').write('| x |')\"",
    "env A=1 B=2 python3 -c \"open('{f}','a').write('| x |')\"",
])
def test_an_assignment_prefix_does_not_hide_the_interpreter(lessons, cmd):
    """Grok install-r2 F1: the shared walk read PYTHONIOENCODING=utf-8 as the program."""
    code, err = hook(bash(cmd.format(f=lessons)), lessons)
    assert code == 2, err
    assert "script that writes" in err


def test_a_script_that_only_mentions_lessons_is_blocked_today(lessons):
    """PINS A KNOWN OVER-BLOCK (P1, parked by org 72, 2026-11-22). A script that names
    memory/lessons.md and writes through a target the hook cannot resolve (a Path(...)
    binding, a reassigned name) is blocked, even when it writes another file. A string
    literal target is already judged by its value. 8 of the 78 calls the prefix fix newly
    blocked in the replay have this shape. REOPEN when a session is blocked writing a
    non-memory file because its script only mentions a memory path; the fix there
    resolves the write target (Path(...) and reassignment), and this test then flips."""
    cmd = ("PYTHONIOENCODING=utf-8 python3 - <<'PY'\n"
           "from pathlib import Path\n"
           "# rows like memory/lessons.md Section 2\n"
           "p = Path('/tmp/notes-out.md')\n"
           "p.write_text('x')\nPY")
    code, err = hook(bash(cmd), lessons)
    assert code == 2 and "script that writes" in err


@pytest.mark.parametrize("cmd", [
    "cd {d} && python3 -c \"open('lessons.md','a').write('| x |')\"",
    "cd {r} && PYTHONIOENCODING=utf-8 python3 - <<'PY'\np='lessons.md'\nopen(p,'w').write(open(p).read())\nPY",
])
def test_a_write_after_cd_next_to_lessons_is_judged(lessons, cmd):
    """data audit item 1: a cd, then the file by bare name."""
    code, err = hook(bash(cmd.format(d=lessons.parent, r=lessons.parent)), lessons)
    assert code == 2, err


def test_unreadable_payload_fails_open(lessons):
    env = {**os.environ, "PYTHONPATH": str(TOOLS), "LESSONS_HOOK_PATH": str(lessons)}
    p = subprocess.run([sys.executable, str(HOOK)], input="[]", capture_output=True, text=True,
                       env=env, timeout=60)
    assert p.returncode == 0


# ------------------------------------------------------------------ mutation r1 survivors

def test_a_file_without_section2_gaining_one_is_judged(lessons):
    lessons.write_text("# Lessons\n\nno sections yet\n", encoding="utf-8")
    ok = "# Lessons\n\n## Section 2 — Email\n\n| Pattern | Rule | Occurrences | Promoted | Date |\n|---|---|---|---|---|\n" + NEW_GOOD + "\n"
    assert hook(write(lessons, ok), lessons)[0] == 0
    assert hook(write(lessons, ok.replace(NEW_GOOD, NEW_BAD)), lessons)[0] == 2


def test_a_later_section_is_not_judged(lessons):
    with_s3 = LESSONS + "\n## Section 3 — Other\n\n| a | b |\n|---|---|\n| one | two |\n"
    lessons.write_text(with_s3, encoding="utf-8")
    code, err = hook(write(lessons, with_s3.replace("| one | two |", "| three | four |")), lessons)
    assert code == 0, err


def test_creating_the_file_is_judged_on_its_rows_only(lessons):
    lessons.unlink()
    code, err = hook(write(lessons, LESSONS.replace(OLD_BAD + "\n", "")), lessons)
    assert code == 0, err


def test_keeping_the_heading_but_dropping_the_table_is_blocked(lessons):
    new = LESSONS.split("| Pattern | Rule | Occurrences |")[0] + "The table moved elsewhere.\n"
    assert "## Section 2" in new
    code, err = hook(write(lessons, new), lessons)
    assert code == 2 and "removes the Section 2 table" in err


def test_a_valid_edit_is_allowed(lessons):
    code, err = hook(edit(lessons, "Invented rule alpha", "Invented rule alpha, sharpened"), lessons)
    assert code == 0, err


def test_an_edit_the_tool_will_refuse_is_not_judged(lessons):
    assert hook(edit(lessons, "text that is not there", "x"), lessons)[0] == 0
    lessons.unlink()
    assert hook(edit(lessons, "Invented rule alpha", "x"), lessons)[0] == 0


def test_a_truncated_payload_naming_lessons_is_blocked(lessons, tmp_path):
    env = {**os.environ, "PYTHONPATH": str(TOOLS), "LESSONS_HOOK_PATH": str(lessons)}
    big = json.dumps(write(lessons, "x" * (1024 * 1024 + 10)))
    p = subprocess.run([sys.executable, str(HOOK)], input=big, capture_output=True, text=True,
                       env=env, timeout=60)
    assert p.returncode == 2 and "cut off at the intake cap" in p.stderr
    big = json.dumps(write(tmp_path / "other.md", "x" * (1024 * 1024 + 10)))
    p = subprocess.run([sys.executable, str(HOOK)], input=big, capture_output=True, text=True,
                       env=env, timeout=60)
    assert p.returncode == 0 and p.stderr == ""


# ------------------------------------------------------------------ Codex r1 F3 and F4

def test_printf_append_of_a_valid_row_is_allowed(lessons):
    cmd = f"printf '%s\\n' '{NEW_GOOD}' >> {lessons}"
    code, err = hook(bash(cmd), lessons)
    assert code == 0, err


def test_printf_append_of_an_invalid_row_is_blocked(lessons):
    cmd = f"printf '%s\\n' '{NEW_BAD}' >> {lessons}"
    code, err = hook(bash(cmd), lessons)
    assert code == 2 and "Someday" in err


def test_a_row_turned_into_a_copy_of_an_old_bad_row_is_judged(lessons):
    """Codex r1 F4: the old bad row is grandfathered once, not every copy of it."""
    new = LESSONS.replace(GOOD, OLD_BAD)
    code, err = hook(write(lessons, new), lessons)
    assert code == 2, err


def test_an_untouched_duplicate_pair_is_grandfathered(lessons):
    two_bad = LESSONS.replace(OLD_BAD, OLD_BAD + "\n" + OLD_BAD)
    lessons.write_text(two_bad, encoding="utf-8")
    code, err = hook(write(lessons, two_bad.replace("Invented rule alpha", "Invented rule alpha, edited")), lessons)
    assert code == 0, err


def test_a_file_without_section2_on_either_side_is_not_judged(lessons):
    lessons.write_text("# Lessons\n\nnothing yet\n", encoding="utf-8")
    code, err = hook(write(lessons, "# Lessons\n\nstill nothing\n"), lessons)
    assert code == 0, err


def test_a_merged_pointer_resolves_in_the_memory_folder(lessons):
    """Grok r1 F2: the hook judged lessons rows without the memory folder."""
    mem = lessons.parent / "none"
    mem.mkdir()
    (mem / "feedback_other_rule.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    ok = LESSONS.replace("|------|\n", "|------|\n" + NEW_GOOD.replace("| No |", "| merged into feedback_other_rule.md |") + "\n")
    assert hook(write(lessons, ok), lessons)[0] == 0
    bad = ok.replace("feedback_other_rule.md", "feedback_absent_rule.md")
    assert hook(write(lessons, bad), lessons)[0] == 2


def test_the_repair_row_writer_is_sanctioned(lessons):
    """org 72: repair-row (lessons_write.py) is the route for structural row repairs."""
    cmd = (f"CLAUDECODE=1 PYTHONIOENCODING=utf-8 python3 {TOOLS}/lessons_write.py repair-row --line 12 "
           f"--expect-sha {'0' * 64} --old '| a | b |' --new '| a | b |'")
    code, err = hook(bash(cmd), lessons)
    assert code == 0, err
