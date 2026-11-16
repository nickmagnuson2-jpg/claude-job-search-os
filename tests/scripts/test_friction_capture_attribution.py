"""Friction capture attribution (RULINGS Q9 + Q10, data/workstreams/hooks.md, 2026-11-16).

4c's audit (output/infra-backlog/111626-telemetry/friction-attribution.md) classified the
726 automatic friction rows: 370 not friction, 74 on the wrong surface, and 43 of the 132
rows at "script-patch (mandatory)" resting on bad occurrences. Each test below is one of
the causes it found, written from a real row.

Q9 (Nick): a hook that BLOCKED as designed is counted on a separate MODEL-HABIT ladder,
never on the script-patch ladder of the hook that worked. Its surface is `block:<hook>`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import friction_log as fl  # noqa: E402
import friction_surface as fs  # noqa: E402
import scan_transcript_failures as st  # noqa: E402

HOOK = "[PYTHONIOENCODING=utf-8 python3 $CLAUDE_PROJECT_DIR/tools/{}]"


# --- A. a hook block is read from the harness header, for every tool ----------------

def test_a_bash_hook_block_is_attributed_to_the_hook_not_the_script_in_the_command():
    """friction_surface.py:313-316: SCRIPT_RE on the command won over the block header."""
    err = ("PreToolUse:Bash hook error: " + HOOK.format("check_zsh_word_split.py")
           + ": BLOCKED: zsh word-split mistake (zsh does not split an unquoted $VAR).")
    s = fs.derive_surface("Bash", "for f in $FILES; do python3 tools/pipe_write.py x; done", err)
    assert s == "block:check_zsh_word_split.py"


def test_a_write_hook_block_is_attributed_to_the_hook_not_tool_write():
    """friction_surface.py:309-310: non-Bash blocks were filed as tool:<name>."""
    err = ("PreToolUse:Write hook error: " + HOOK.format("check_public_pii.py")
           + ": BLOCKED: 1 denylisted token in a public file")
    assert fs.derive_surface("Write", "", err) == "block:check_public_pii.py"


# --- B. BLOCKED inside ordinary output is not a hook block ---------------------------

def test_blocked_text_in_ordinary_output_is_not_a_hook_surface():
    """friction_surface.py:165-179: a grep exit 1 whose output was run-log text naming a
    hook was filed under check_guard_edit_approval.py (a2, 2026-11-16 12:09)."""
    out = ("Exit code 1\n| 12:09 | 0e | check_guard_edit_approval.py BLOCKED a dry run |")
    s = fs.derive_surface("Bash", "/usr/bin/grep -n zz output/run-log.md", out)
    assert not s.startswith("block:") and "check_guard_edit_approval" not in s


# --- C. harness events are never a script's friction --------------------------------

@pytest.mark.parametrize("text,kind", [
    ("The server-side auto mode classifier gave no verdict (the response ended before its "
     "verdict arrived), so auto mode cannot determine the safety of Bash.", "classifier"),
    ("claude-sonnet-5[1m] is temporarily unavailable, so auto mode cannot determine the "
     "safety of Bash right now.", "classifier"),
    ("Permission for this action was denied by the Claude Code auto mode classifier. "
     "Reason: judged dangerous.", "classifier"),
    ("Permission to use Bash with command git push --dry-run has been denied.", "permission"),
    ("The user doesn't want to proceed with this tool use. The tool use was rejected.", "user"),
    ("[Request interrupted by user for tool use]", "user"),
    ("This command requires approval", "permission"),
    ("Quoted text in this command can't be checked before it runs", "permission"),
    ("ls in '/x/.claude/skills' was blocked. For security, Claude Code may only list files", "permission"),
    ("Contains brace with quote character (expansion obfuscation)", "permission"),
    ("<tool_use_error>Blocked: sleep 25 followed by: cat /tmp/x</tool_use_error>", "permission"),
])
def test_harness_events_get_a_harness_surface(text, kind):
    s = fs.derive_surface("Bash", "python3 tools/check_public_pii.py --scan x", text)
    assert s == f"harness:{kind}"


# --- D. the Stop lane needs an error signal ------------------------------------------

def _transcript(tmp_path, entries):
    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return p


def _use(tu_id, command):
    return {"message": {"content": [{"type": "tool_use", "id": tu_id, "name": "Bash",
                                     "input": {"command": command}}]}}


def _result(tu_id, text, is_error=True):
    return {"message": {"content": [{"type": "tool_result", "tool_use_id": tu_id,
                                     "content": text, "is_error": is_error}]}}


@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(st, "append_friction",
                        lambda surface, nature, exit_hint="": calls.append((surface, nature)))
    return calls


def test_a_bare_exit_1_with_no_error_signal_is_not_logged(tmp_path, captured):
    """scan_transcript_failures.py:369-371 logged every is_error result; a grep or diff
    that exits 1 by design printed data, not an error."""
    t = _transcript(tmp_path, [_use("t1", "diff a.txt b.txt"),
                               _result("t1", "Exit code 1\n3c3\n< old line\n---\n> new line")])
    _, logged, _ = st.scan(t, 0)
    assert logged == 0 and captured == []


def test_a_real_error_is_still_logged(tmp_path, captured):
    t = _transcript(tmp_path, [_use("t1", "python3 tools/pipe_write.py add x"),
                               _result("t1", "Exit code 2\nusage: pipe_write.py [-h]\nerror: bad")])
    _, logged, _ = st.scan(t, 0)
    assert logged == 1 and captured[0][0] == "pipe_write.py"


def test_a_hook_block_is_logged_on_the_block_surface(tmp_path, captured):
    err = ("PreToolUse:Bash hook error: " + HOOK.format("check_unquoted_glob.py")
           + ": BLOCKED: unquoted glob in '--include=*'.")
    t = _transcript(tmp_path, [_use("t1", "grep -r x --include=*.md ."), _result("t1", err)])
    _, logged, _ = st.scan(t, 0)
    assert logged == 1 and captured[0][0] == "block:check_unquoted_glob.py"


def test_a_harness_event_is_not_logged_as_friction(tmp_path, captured):
    t = _transcript(tmp_path, [
        _use("t1", "python3 tools/check_public_pii.py --scan x"),
        _result("t1", "Permission to use Bash with command python3 tools/x.py has been denied.")])
    _, logged, _ = st.scan(t, 0)
    assert logged == 0


# --- E. a merge never moves occurrences onto another surface -------------------------

def test_cross_surface_rows_never_match():
    """friction_log.py:218-229, 349-358: the classifier timeout x12 landed on
    check_public_pii.py because a near-identical nature matched across surfaces."""
    row = {"surface": "check_public_pii.py", "nature": "[auto-stop] classifier gave no verdict "
           "auto mode cannot determine the safety of Bash"}
    kw = fl.keywords("[auto-stop] classifier gave no verdict auto mode cannot determine the "
                     "safety of Bash")
    assert fl.match_score(row, "harness:classifier", kw) == 0.0
    assert fl.match_score(row, "check_public_pii.py", kw) > 0.0


# --- F. Q9: designed blocks ride a model-habit ladder ---------------------------------

@pytest.mark.parametrize("n", [1, 2, 3, 5, 40])
def test_a_block_surface_never_reaches_the_script_patch_rung(n):
    p = fl.promotion_for(n, surface="block:check_unquoted_glob.py")
    assert "script-patch" not in p


def test_a_block_surface_reaches_the_model_habit_rung_at_three():
    assert "model-habit" in fl.promotion_for(3, surface="block:check_unquoted_glob.py")


def test_ordinary_surfaces_keep_todays_ladder():
    assert fl.promotion_for(3) == fl.promotion_for(3, surface="pipe_write.py")
    assert "script-patch" in fl.promotion_for(3, surface="pipe_write.py")


@pytest.mark.parametrize("text", [
    "Exit code 2\ngrep: tools/.changelog-currency-cursor.json: Operation not permitted",
    "Exit code 143\nCommand timed out after 2m 0s",
])
def test_real_failures_without_a_classic_error_word_are_still_logged(tmp_path, captured, text):
    """Restraint replay, 2026-11-16: these two shapes carried no signal, so the new
    Bash-lane gate would have dropped them."""
    t = _transcript(tmp_path, [_use("t1", "grep -rn x tools/"), _result("t1", text)])
    _, logged, _ = st.scan(t, 0)
    assert logged == 1


@pytest.mark.parametrize("text", [
    "Exit code 137\nKilled: 9",
    "Exit code 139\nzsh: segmentation fault  python3 tools/x.py",
    "Exit code 1\nOSError: [Errno 28] No space left on device",
])
def test_fatal_results_are_logged(tmp_path, captured, text):
    """Codex + Grok P0 on 1a6c4750: fatal ends carried no classic error word."""
    t = _transcript(tmp_path, [_use("t1", "python3 tools/pipe_write.py add x"), _result("t1", text)])
    _, logged, _ = st.scan(t, 0)
    assert logged == 1


def test_a_hook_crash_is_the_hook_s_friction_not_a_designed_block():
    """Codex + Grok P0 on 1a6c4750: a crashed hook also gets the harness header."""
    err = ("PreToolUse:Bash hook error: " + HOOK.format("check_bare_python.py")
           + ": /opt/homebrew/bin/python3: can't open file '/x/tools/check_bare_python.py': "
             "[Errno 1] Operation not permitted")
    assert fs.derive_surface("Bash", "ls", err) == "check_bare_python.py"


def test_dedup_keeps_surfaces_apart_even_for_identical_natures(tmp_path, monkeypatch):
    rows = [{"surface": "harness:classifier", "nature": "classifier gave no verdict auto mode",
             "date": "2026-11-01", "fix": "-", "occurrences": "12", "promotion": "x", "_raw": "a"},
            {"surface": "check_public_pii.py", "nature": "classifier gave no verdict auto mode",
             "date": "2026-11-02", "fix": "-", "occurrences": "1", "promotion": "x", "_raw": "b"}]
    kw = fl.keywords(rows[0]["nature"])
    assert fl.match_score(rows[1], rows[0]["surface"], kw) == 0.0


# --- r2 P0s (3.F1, 4.F1, 3.F2): crash is the default, harness is anchored -----------

HEADER = "PreToolUse:Bash hook error: " + HOOK.format("check_x_demo.py") + ": "


@pytest.mark.parametrize("body", [
    "usage: check_x_demo.py [-h] [--strict]\ncheck_x_demo.py: error: unrecognized arguments: -q",
    '  File "tools/check_x_demo.py", line 12\n    return x\nIndentationError: unexpected indent',
    "Killed: 9",
    "zsh: segmentation fault  python3 tools/check_x_demo.py",
    "",
])
def test_a_hook_header_without_its_blocked_signal_is_a_crash_not_a_block(body):
    """3.F1 / 4.F1: only an explicit BLOCKED makes a designed block; any other hook death
    is the hook's own friction (script-patch ladder), never block:<hook>."""
    err = HEADER + body
    assert fs.hook_block(err) == ""
    assert fs.hook_crash(err) == "check_x_demo.py"
    assert fs.derive_surface("Bash", "ls", err) == "check_x_demo.py"


def test_a_hook_header_with_its_blocked_signal_is_a_block():
    err = HEADER + "BLOCKED: unquoted glob in '--include=*'."
    assert fs.hook_block(err) == "check_x_demo.py"
    assert fs.hook_crash(err) == ""


def test_a_blocked_word_deep_in_a_crash_dump_does_not_make_it_a_block():
    """The signal is the hook's own message at the start of its stderr, not a word that a
    traceback or echoed input happens to contain later."""
    err = HEADER + "Traceback (most recent call last):\n" + "  frame\n" * 80 + "ValueError: BLOCKED"
    assert fs.hook_block(err) == ""
    assert fs.hook_crash(err) == "check_x_demo.py"


@pytest.mark.parametrize("text", [
    "Exit code 1\nTraceback (most recent call last):\nRuntimeError: Permission to use Bash "
    "with command git push has been denied.",
    "Exit code 2\nlog line: The user doesn't want to proceed with this tool use.",
    "Exit code 1\nquoting: the auto mode classifier gave no verdict earlier",
])
def test_a_command_failure_that_quotes_a_harness_message_is_not_harness(text):
    """3.F2: a Bash result starts with its exit code; a harness denial never does. A real
    failure whose output quotes a denial stays the script's friction."""
    assert fs.harness_kind(text) == ""
    assert fs.derive_surface("Bash", "python3 tools/pipe_write.py update x", text) == "pipe_write.py"


def test_a_harness_phrase_far_into_a_non_bash_result_is_not_harness():
    text = "Error fetching URL: " + "x" * 400 + " This command requires approval"
    assert fs.harness_kind(text) == ""


@pytest.mark.parametrize("text,kind", [
    ("This command requires approval", "permission"),
    ("Permission to use Bash with command cd /x && ls\nwith more lines has been denied.", "permission"),
    ("ls in '/Users/someone/.claude/commands' was blocked. For security, Claude Code may only "
     "list files in the allowed working directories", "permission"),
    ("<tool_use_error>Blocked: sleep 45 followed by tail</tool_use_error>", "permission"),
    ("Permission for this action was denied by the Claude Code auto mode classifier.", "classifier"),
    ("  The user doesn't want to proceed with this tool use.", "user"),
])
def test_real_harness_openings_still_classify(text, kind):
    assert fs.harness_kind(text) == kind
