"""The PostToolUse hook that warns when Edit was used on a file Edit silently breaks.

WHY IT HAD TO BE THIS ONE. check_edit_safety is wired in .claude/settings.json under
PostToolUse with matcher "Edit" -- it runs on every single Edit call -- and had no suite
of its own. Its failure mode is silent non-enforcement: if the warning stops being
emitted, nothing errors, no test reddens, and the long-row Edit corruption this project
documents in CLAUDE.md ("Edit silently fails on rows >500 chars") goes back to being
invisible.

WHAT IT GUARDS. Two things, in this order: (1) a hard-coded roster of write-only data
files (job-todos.md, job-pipeline.md) that must be mutated via their atomic scripts or a
full Write, warned about by BASENAME regardless of where they live; (2) any other .md
file on disk that already contains a row longer than 500 chars, where an Edit may have
been silently dropped. Non-markdown paths are ignored entirely.

POSTTOOLUSE, NOT PRETOOLUSE -- the exit-code contract is different. PreToolUse uses
exit 2 to BLOCK the tool call before it runs. PostToolUse runs AFTER the edit has already
been applied, so there is nothing left to block; exit 2 there only feeds stderr back to
Claude as an error to consider. This hook deliberately takes neither route: it always
exits 0 and writes its warning to STDOUT, and its own module docstring says so ("Never
exits non-zero -- never blocks workflow"). Every assertion below therefore pins
returncode == 0 and reads the SIGNAL out of stdout. An exit code alone would be vacuous
here: 0 is what a totally dead hook also returns.

TESTED THROUGH THE REAL ENTRY POINT, not the helpers. Per tools/HOOK_AUTHORING.md: a
53-test suite once stayed green while the shipped hook was broken, because every test
called the helper directly. Everything here spawns the actual script and feeds it JSON
on stdin, exactly as Claude Code does.

Both directions carry equal weight. A false negative drops the guard silently. A false
positive -- warning on ordinary short .md files -- is noise on every Edit in the repo,
which is how a warning stops being read at all.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "tools" / "check_edit_safety.py"

# PostToolUse: 0 = hook completed, tool call stands. This hook never returns anything else.
OK = 0

WARN_MARK = "⚠"  # the warning sign the hook leads both messages with


def run(payload: object = None, *, raw: str | None = None) -> subprocess.CompletedProcess:
    """Spawn the real hook with a PostToolUse-shaped payload on stdin."""
    stdin = raw if raw is not None else json.dumps(payload)
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, str(HOOK)], input=stdin,
                          capture_output=True, text=True, env=env)


def edit_payload(file_path: object) -> dict:
    """The shape Claude Code actually sends for a PostToolUse Edit."""
    return {
        "tool_name": "Edit",
        "tool_input": {"file_path": file_path, "old_string": "a", "new_string": "b"},
        "tool_response": {"success": True},
    }


def md_with(tmp_path: Path, name: str, lines: list[str]) -> str:
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


# --- guarantee 1: the write-only roster warns by basename -------------------

@pytest.mark.parametrize("name", ["job-todos.md", "job-pipeline.md"])
def test_editing_a_write_only_data_file_emits_a_warning(name, tmp_path):
    """These two files hold table rows far over the Edit limit. An Edit against them is
    documented in CLAUDE.md as silently failing, so the warning is the only signal the
    mutation never landed."""
    path = md_with(tmp_path, name, ["| short row |"])
    r = run(edit_payload(path))
    assert r.returncode == OK, r.stderr
    assert WARN_MARK in r.stdout
    assert name in r.stdout


def test_the_write_only_warning_names_write_as_the_remedy():
    """A warning that does not say what to do instead gets skimmed past. The message has
    to name Write, because the whole point is that re-running Edit will fail again."""
    out = run(edit_payload("/repo/data/job-pipeline.md")).stdout
    assert "Write" in out
    assert "Edit" in out


def test_the_write_only_roster_fires_even_when_the_file_is_not_on_disk():
    """Ordering matters and is load-bearing: the roster check runs BEFORE the
    path.exists() guard. If it were moved after, every roster warning would depend on the
    test/CI machine having the real data file, and the guard would silently go dark
    wherever the gitignored data tree is absent."""
    out = run(edit_payload("/nonexistent/dir/job-todos.md")).stdout
    assert "job-todos.md" in out


def test_the_roster_matches_the_basename_anywhere_on_the_path():
    """The roster is keyed on filename, not full path, so a copy under output/ or a
    worktree is covered too."""
    out = run(edit_payload("/some/other/tree/job-pipeline.md")).stdout
    assert "job-pipeline.md" in out


def test_a_roster_lookalike_name_is_not_treated_as_write_only(tmp_path):
    """Set membership is exact. `old-job-pipeline.md` is a different file and must fall
    through to the ordinary long-row check rather than inheriting the hard warning."""
    path = md_with(tmp_path, "old-job-pipeline.md", ["| short |"])
    r = run(edit_payload(path))
    assert r.returncode == OK
    assert r.stdout == ""


# --- guarantee 2: long rows in any other .md file ---------------------------

def test_a_markdown_file_with_a_row_over_the_threshold_is_flagged(tmp_path):
    """The 500-char row is the actual corruption surface: Edit reports success and the
    row is left untouched. Without this branch a long-rowed dossier gets edited blind."""
    path = md_with(tmp_path, "notes.md", ["short", "x" * 501])
    r = run(edit_payload(path))
    assert r.returncode == OK, r.stderr
    assert WARN_MARK in r.stdout
    assert "notes.md" in r.stdout


def test_the_long_row_warning_reports_the_count_and_the_line_numbers(tmp_path):
    """Line numbers are what make the warning actionable -- they point at the row to
    re-check. They are 1-based; an off-by-one sends the reader to the wrong row."""
    path = md_with(tmp_path, "notes.md", ["short", "y" * 600, "short"])
    out = run(edit_payload(path)).stdout
    assert "1 rows" in out
    assert "[2]" in out


def test_only_the_first_three_long_rows_are_listed_and_the_rest_elided(tmp_path):
    """The sample cap keeps the message readable on a file where most rows are long.
    The ellipsis is the signal that the list is truncated, not complete."""
    path = md_with(tmp_path, "big.md", ["z" * 600] * 5)
    out = run(edit_payload(path)).stdout
    assert "5 rows" in out
    assert "[1, 2, 3]" in out
    assert "..." in out


def test_exactly_three_long_rows_are_listed_without_an_ellipsis(tmp_path):
    """The boundary of the truncation: at three there is nothing elided, so an ellipsis
    would be a lie about what the reader is seeing."""
    path = md_with(tmp_path, "three.md", ["z" * 600] * 3)
    out = run(edit_payload(path)).stdout
    assert "[1, 2, 3]" in out
    assert "..." not in out


def test_a_row_exactly_at_the_threshold_is_not_flagged(tmp_path):
    """The comparison is strictly greater-than. Pinning both sides of 500 is what stops a
    `>=` slip from turning every ordinary long paragraph into a warning."""
    path = md_with(tmp_path, "edge.md", ["e" * 500])
    assert run(edit_payload(path)).stdout == ""


def test_a_row_one_char_over_the_threshold_is_flagged(tmp_path):
    """The other side of the same boundary -- together these two pin the operator."""
    path = md_with(tmp_path, "edge.md", ["e" * 501])
    assert WARN_MARK in run(edit_payload(path)).stdout


def test_the_threshold_counts_characters_per_line_not_per_file(tmp_path):
    """A long document made of short rows is perfectly safe to Edit. Summing the file
    instead of measuring rows would warn on nearly every markdown file in the repo."""
    path = md_with(tmp_path, "long-but-safe.md", ["a" * 100] * 50)
    assert run(edit_payload(path)).stdout == ""


# --- the clean direction: files that must pass untouched --------------------

def test_a_short_markdown_file_produces_no_output(tmp_path):
    """The common case. Every Edit in this repo runs through this hook, so silence on
    ordinary files is a requirement, not a nicety."""
    path = md_with(tmp_path, "clean.md", ["# Title", "", "Some ordinary prose."])
    r = run(edit_payload(path))
    assert r.returncode == OK
    assert r.stdout == ""
    assert r.stderr == ""


def test_an_empty_markdown_file_produces_no_output(tmp_path):
    p = tmp_path / "empty.md"
    p.write_text("", encoding="utf-8")
    assert run(edit_payload(str(p))).stdout == ""


@pytest.mark.parametrize("name", ["script.py", "settings.json", "notes.txt", "SKILL.MD"])
def test_non_markdown_paths_are_ignored_even_with_very_long_lines(name, tmp_path):
    """The .md gate is a suffix check and is case-sensitive, so `SKILL.MD` is skipped too.
    Python and JSON files have long lines routinely and Edit handles them fine; warning
    there would be pure noise."""
    p = tmp_path / name
    p.write_text("q" * 900 + "\n", encoding="utf-8")
    r = run(edit_payload(str(p)))
    assert r.returncode == OK
    assert r.stdout == ""


def test_a_markdown_path_that_does_not_exist_produces_no_output():
    """Only the roster branch fires without the file. For anything else there is nothing
    to measure, and inventing a warning would fire on every new-file Edit."""
    r = run(edit_payload("/nonexistent/dir/whatever.md"))
    assert r.returncode == OK
    assert r.stdout == ""


def test_a_directory_path_ending_in_md_does_not_crash(tmp_path):
    """read_text() on a directory raises IsADirectoryError. The blanket except must keep
    that from surfacing as a hook failure on an unusual path."""
    d = tmp_path / "weird.md"
    d.mkdir()
    r = run(edit_payload(str(d)))
    assert r.returncode == OK
    assert r.stdout == ""


def test_undecodable_bytes_in_a_markdown_file_do_not_crash(tmp_path):
    """The hook reads with errors="ignore" on purpose. A latin-1 byte in a dossier must
    not turn the hook into a per-Edit traceback."""
    p = tmp_path / "bytes.md"
    p.write_bytes(b"caf\xe9 short line\n")
    r = run(edit_payload(str(p)))
    assert r.returncode == OK
    assert r.stdout == ""


# --- malformed and incomplete input: this hook fails OPEN and SILENT --------

def test_malformed_stdin_fails_open_with_no_output():
    """Established from the source, not assumed: main() wraps everything in
    `except Exception: pass`. Bad JSON therefore exits 0 and prints nothing. Fail-open is
    right for a PostToolUse advisory, and the cost -- a crashed hook is indistinguishable
    from a clean file -- is precisely why the positive cases above exist."""
    r = run(raw="{not json")
    assert r.returncode == OK
    assert r.stdout == ""


def test_empty_stdin_fails_open_with_no_output():
    r = run(raw="")
    assert r.returncode == OK
    assert r.stdout == ""


def test_a_json_array_instead_of_an_object_fails_open():
    """Valid JSON of the wrong type: .get() does not exist on a list, so this exercises
    the except branch rather than the parse branch."""
    r = run(raw="[1, 2, 3]")
    assert r.returncode == OK
    assert r.stdout == ""


@pytest.mark.parametrize("payload", [
    {},
    {"tool_input": {}},
    {"tool_input": {"file_path": ""}},
    {"tool_name": "Edit"},
], ids=["no-tool-input", "empty-tool-input", "blank-path", "name-only"])
def test_incomplete_payloads_produce_no_output(payload):
    """Missing fields mean there is nothing to judge. The hook must stay quiet rather
    than warning on tool calls it cannot evaluate."""
    r = run(payload)
    assert r.returncode == OK
    assert r.stdout == ""


@pytest.mark.parametrize("bad_path", [None, 123, ["/repo/x.md"], {"p": "x.md"}],
                         ids=["null", "int", "list", "dict"])
def test_a_non_string_file_path_is_swallowed_not_raised(bad_path):
    """file_path is used as a string immediately (.endswith). A non-string raises
    AttributeError/TypeError; the blanket except must absorb it so a weird payload never
    turns into a visible hook error after a successful edit."""
    r = run(edit_payload(bad_path))
    assert r.returncode == OK
    assert r.stdout == ""


# --- exit-code contract, stated once and pinned -----------------------------

@pytest.mark.parametrize("case", ["roster", "long-row", "clean", "garbage"])
def test_the_hook_never_exits_non_zero_in_any_branch(case, tmp_path):
    """PostToolUse runs after the edit is already applied, so exit 2 cannot block
    anything -- it only injects an error for Claude to react to. This hook is advisory by
    design and returns 0 on every path, warning or not. That is also why every other test
    here asserts on stdout: exit code alone cannot distinguish a working hook from a dead
    one."""
    if case == "roster":
        r = run(edit_payload("/repo/data/job-todos.md"))
    elif case == "long-row":
        r = run(edit_payload(md_with(tmp_path, "n.md", ["w" * 900])))
    elif case == "clean":
        r = run(edit_payload(md_with(tmp_path, "n.md", ["ok"])))
    else:
        r = run(raw="{{{")
    assert r.returncode == 0, f"{case}: {r.stderr}"


@pytest.mark.parametrize("case", ["roster", "long-row"])
def test_warnings_go_to_stdout_and_stderr_stays_empty(case, tmp_path):
    """Channel is a real behavioural fact, not a detail: a caller scraping stderr for
    hook output would see nothing at all from this hook."""
    path = "/repo/data/job-todos.md" if case == "roster" else md_with(
        tmp_path, "n.md", ["w" * 900])
    r = run(edit_payload(path))
    assert r.stdout.strip() != ""
    assert r.stderr == ""


# --- the assertions that have to fail first ---------------------------------

def test_the_hook_file_exists_and_its_rules_are_non_empty():
    """VACUITY GUARD. Every "produces no output" assertion above is satisfied by a hook
    that never warns about anything -- including one that was deleted, renamed, or had its
    roster emptied. This test fails first in all of those cases, so the clean-direction
    tests cannot be read as evidence on their own."""
    assert HOOK.is_file(), f"hook not found at {HOOK}"
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import check_edit_safety as mod
    assert mod.WRITE_ONLY_FILES, "an empty roster makes every roster test vacuous"
    assert {"job-todos.md", "job-pipeline.md"} <= mod.WRITE_ONLY_FILES
    assert isinstance(mod.LONG_LINE_THRESHOLD, int)
    assert 0 < mod.LONG_LINE_THRESHOLD <= 500, (
        "raising the threshold above 500 silently stops flagging rows Edit still drops")


def test_the_hook_is_still_wired_as_a_posttooluse_edit_hook():
    """VACUITY GUARD. A tested hook that is no longer in settings.json enforces nothing
    in production, and nothing else in the suite would notice. This also pins the
    PostToolUse position the exit-code assertions above depend on."""
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    wired = [(event, group.get("matcher"))
             for event, groups in settings.get("hooks", {}).items()
             for group in groups
             for hook in group.get("hooks", [])
             if "check_edit_safety.py" in hook.get("command", "")]
    assert ("PostToolUse", "Edit") in wired, wired


def test_a_known_bad_input_really_does_warn(tmp_path):
    """POSITIVE CONTROL for the live CLI, not the module. The guard above reads the
    constants in-process; this one proves the shipped entry point still turns a bad input
    into a warning end to end."""
    path = md_with(tmp_path, "control.md", ["c" * 501])
    assert WARN_MARK in run(edit_payload(path)).stdout
