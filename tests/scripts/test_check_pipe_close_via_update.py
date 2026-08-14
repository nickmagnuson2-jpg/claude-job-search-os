"""Tests for tools/check_pipe_close_via_update.py (PreToolUse Bash hook).

BLOCKs `pipe_write.py update <company> "<terminal stage>"`. Closing a row is
`remove --stage {Withdrawn,Rejected,Accepted}`; `update` leaves it in ## Active Pipeline
with a freeform stage, where todo_write.py sync counts the company as still LIVE and
therefore never syncs it.

Origin 2026-08-14: 30 rows found in this state on the live pipeline, the oldest four months
old. Root cause was a prescribing source — `.claude/skills/pipe/SKILL.md` step 0 of `update`
listed close stages as valid update targets — fixed in the same pass.

TWO FAILURE MODES THIS SUITE EXISTS TO PIN:

1. **The no-op hook.** The first draft used `hook_command_lint.strip_literals`, the standard
   command-hook helper. It blanks quoted spans — and the stage is ALWAYS quoted in real usage,
   so the hook returned None on the exact command that caused the incident. A guard that never
   fires is indistinguishable from a guard that finds nothing
   (`feedback_wrong_cli_interface_returns_a_false_pass`). `test_the_incident_shaped_command_is_blocked`
   is the anchor: same command shape as the real one, generic company name.

2. **The over-eager hook.** Blocking a legitimate stage advance, or a terminal word appearing
   as a flag VALUE or inside a grep/commit message, would block correct work with no recourse.
"""
import json
import os
import subprocess
import sys

from conftest import TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
from check_pipe_close_via_update import find_violation  # noqa: E402

# Same shape as the command run on 2026-08-14 that produced the incident.
INCIDENT_SHAPED_CMD = (
    'PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py update "ClosedCo" '
    '"Closed - comp below floor + pressure-cooker culture flag" --next-action "—"'
)


def _run_hook(command: str):
    """Drive the REAL CLI entry point over stdin, as Claude Code does."""
    proc = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "check_pipe_close_via_update.py")],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return proc


# ---------------------------------------------------------------------------
# BLOCK cases
# ---------------------------------------------------------------------------

def test_the_incident_shaped_command_is_blocked():
    """Anchor test. If this regresses, the hook has become a no-op again."""
    assert find_violation(INCIDENT_SHAPED_CMD) is not None


def test_incident_command_blocks_through_the_cli():
    proc = _run_hook(INCIDENT_SHAPED_CMD)
    assert proc.returncode == 2
    assert "BLOCKED" in proc.stderr
    assert "remove" in proc.stderr


def test_block_message_names_the_three_stages_and_their_meaning():
    """The message has to be actionable: the next call needs a stage chosen, not guessed."""
    err = _run_hook(INCIDENT_SHAPED_CMD).stderr
    for token in ("Withdrawn", "Rejected", "Accepted"):
        assert token in err
    assert "THEY passed" in err and "NICK passed" in err


def test_repo_root_flag_before_the_subcommand_still_blocks():
    cmd = 'python3 tools/pipe_write.py --repo-root . update "Acme" "Considered - passed (self, 7/7)"'
    assert find_violation(cmd) == "Considered - passed (self, 7/7)"


def test_bare_closed_blocks():
    assert find_violation('python3 tools/pipe_write.py update "Acme" "Closed"') == "Closed"


def test_declined_and_skipped_block():
    assert find_violation('python3 tools/pipe_write.py update "Acme" "Declined"') == "Declined"
    assert find_violation('python3 tools/pipe_write.py update "Acme" "Skipped"') == "Skipped"


def test_path_qualified_tool_still_blocks():
    cmd = 'python3 /Users/x/repo/tools/pipe_write.py update "Acme" "Rejected"'
    assert find_violation(cmd) == "Rejected"


# ---------------------------------------------------------------------------
# CLEAN cases — a false positive blocks correct work with no recourse
# ---------------------------------------------------------------------------

def test_legitimate_stage_advance_is_clean():
    cmd = 'python3 tools/pipe_write.py update "Acme" "Applied" --next-action "follow up"'
    assert find_violation(cmd) is None
    assert _run_hook(cmd).returncode == 0


def test_live_stage_narrating_passed_is_clean():
    """The costliest false positive: a live loop whose prose stage contains 'PASSED'."""
    cmd = ('python3 tools/pipe_write.py update "Acme" '
           '"Onsite loop scheduled (founder screen PASSED) - circuit Thu after 11am"')
    assert find_violation(cmd) is None


def test_correct_remove_call_is_clean():
    assert find_violation('python3 tools/pipe_write.py remove "Acme" --stage Rejected') is None


def test_terminal_word_as_a_flag_value_is_clean():
    """`--fit-reason "Closed - they passed"` on a legitimate advance must not trip."""
    cmd = ('python3 tools/pipe_write.py update "Acme" "Applied" '
           '--fit-reason "Closed - they passed on the last role"')
    assert find_violation(cmd) is None


def test_grep_for_the_pattern_is_clean():
    """Quoted span collapses to one shlex token, so it can never match the tool name."""
    cmd = 'grep -n "pipe_write.py update Acme Closed" docs/CHANGELOG.md'
    assert find_violation(cmd) is None


def test_heredoc_body_mentioning_the_pattern_is_clean():
    cmd = ("git commit -F - <<'EOF'\n"
           "fix: pipe_write.py update Acme Closed was the wrong call\n"
           "EOF")
    assert find_violation(cmd) is None


def test_other_subcommands_are_clean():
    assert find_violation('python3 tools/pipe_write.py add "Acme" "Ops Lead"') is None


def test_update_without_a_stage_arg_is_clean():
    assert find_violation('python3 tools/pipe_write.py update "Acme"') is None


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_malformed_json_fails_open():
    proc = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "check_pipe_close_via_update.py")],
        input="{not json", capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0


def test_empty_command_fails_open():
    assert _run_hook("").returncode == 0


def test_unbalanced_quotes_fail_open():
    assert find_violation('python3 tools/pipe_write.py update "Acme') is None


def test_hook_has_no_local_terminal_stage_list():
    """Terminal detection must delegate to stage_vocab — never a private keyword copy.

    Same guard as tests/scripts/test_stage_classification_consistency.py; asserted here too
    because this file is a hook, not a listed CONSUMER, and the drift that caused the
    2026-08-14 incident was exactly a second private copy of this logic.
    """
    src = (TOOLS_DIR / "check_pipe_close_via_update.py").read_text(encoding="utf-8")
    assert "from stage_vocab import is_terminal_stage" in src
    for banned in ("TERMINAL_STAGES =", "TERMINAL_KEYWORDS", "def is_terminal_stage"):
        assert banned not in src, f"{banned} — hook must not re-implement stage classification"
