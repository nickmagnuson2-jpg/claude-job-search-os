"""Tests for tools/check_poller_self_match.py, the wait-loop self-match hook."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "tools" / "check_poller_self_match.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))
import check_poller_self_match as hook  # noqa: E402


def _run(command: str):
    r = subprocess.run([sys.executable, str(HOOK)],
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
                       capture_output=True, text=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, r.stderr


# Real commands from the transcripts, trimmed. Each one never ended (or could not).
@pytest.mark.parametrize("command", [
    # 2026-10-25: two of these queued at once waited on each other
    'while pgrep -f "mut2.sh|mutation_check.py" >/dev/null; do sleep 15; done; '
    'python3 tools/mutation_check.py tools/a.py',
    # 2026-09-06 shape: ps lists the waiting shell, whose text holds the body
    'until ! ps aux | grep -q "[m]utation_check.py"; do sleep 5; done; '
    'python3 tools/mutation_check.py tools/x.py',
    'while ps aux | /usr/bin/grep -q "[m]utation_check"; do sleep 15; done; '
    'echo "mutation_check finished"',
    # a second exit clause does not save the poll from matching its own body
    'until [ -f out.md ] || ! pgrep -f "bvm0csqhj|codex_verify.py" >/dev/null; do sleep 10; done; '
    'tail -4 tasks/bvm0csqhj.output',
    # ps | grep with no bracket matches grep's own process line
    'until ! ps aux | grep -q myjob; do sleep 2; done',
    # bracketing hides only the pattern's own spelling, not the body's
    'while pgrep -f "[t]ools/mutation_check.py" >/dev/null; do sleep 15; done; '
    'python3 tools/mutation_check.py a',
    # combined flags still carry -f
    'while pgrep -fl "tools/x.py" > /dev/null 2>&1; do sleep 2; done; python3 tools/x.py',
])
def test_a_loop_whose_poll_matches_its_own_text_is_blocked(command):
    code, err = _run(command)
    assert code == 2, err
    assert "can never end" in err and "script file" in err


@pytest.mark.parametrize("command", [
    # one waiter whose pattern appears only as itself: pgrep skips its ancestors
    'while pgrep -f "tools/mutation_check.py" >/dev/null; do sleep 15; done; echo go',
    'while ps aux | grep -q "[m]yjob"; do sleep 2; done',
    # not a loop: a one-off check returns at once
    'pgrep -fl mutation_check; python3 tools/mutation_check.py x',
    # without -f pgrep matches the process NAME, never the command text
    'while pgrep python3 >/dev/null; do sleep 1; done; python3 x',
    # the loop lives in a script file
    'zsh /tmp/mrun.sh fix tag tools/check_voice_pure.py',
    # quoted words are not keywords
    'echo "while pgrep -f x; do sleep 1; done x"',
    # grep that does not read ps
    'while cat log | grep -q "ready"; do sleep 1; done; echo ready',
])
def test_clean(command):
    code, err = _run(command)
    assert code == 0, err


def test_an_unreadable_payload_fails_open():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True)
    assert r.returncode == 0


def test_a_tokenizer_error_fails_open(monkeypatch):
    def boom(_):
        raise ValueError("unbalanced")
    monkeypatch.setattr(hook, "tokenize", boom)
    assert hook.find_violations('while pgrep -f x; do x; done') == []


def test_an_invalid_regex_falls_back_to_a_literal_match():
    assert hook._matches("a(b", "xa(b") and not hook._matches("a(b", "xyz")


def test_the_message_names_the_tool_and_pattern():
    msgs = hook.find_violations('while pgrep -f "abc" >/dev/null; do sleep 1; done; run abc')
    assert len(msgs) == 1 and "`pgrep`" in msgs[0] and "'abc'" in msgs[0]
    msgs = hook.find_violations('while ps aux | grep -q abc; do sleep 1; done')
    assert len(msgs) == 1 and "`ps|grep`" in msgs[0]


# --- shapes that pin each step of the reading

@pytest.mark.parametrize("command", [
    # a poll in the loop BODY is not the condition: the condition ends at `do`
    'while true; do sleep 1; pgrep -f abc >/dev/null || break; done; run abc',
    # an ordinary word before a one-off poll does not start a loop
    'echo start; pgrep -f abc; run abc',
    # a condition made only of skipped words has no command to read
    'while command; do sleep 1; done',
])
def test_more_clean_shapes(command):
    assert hook.find_violations(command) == []


def test_a_condition_with_no_do_still_reads_its_last_command():
    """A truncated loop has no `do`, so the last command in the condition is not
    followed by an operator; it must still be read."""
    assert hook.find_violations('X=abc; while pgrep -f abc')


def test_a_heredoc_body_is_not_read_as_the_pattern():
    """Only words and operators are read; a heredoc body after the poll must not become
    pgrep's pattern and hide the real one."""
    assert hook.find_violations("while pgrep -f abc <<EOF\nabc\nEOF\ndo sleep 1; done")


def test_each_tool_gets_its_own_reason():
    pg = hook.find_violations('while pgrep -f "abc" >/dev/null; do sleep 1; done; run abc')[0]
    ps = hook.find_violations('while ps aux | grep -q abc; do sleep 1; done')[0]
    assert "second command carrying the same text" in pg and "lists this waiting shell" not in pg
    assert "lists this waiting shell" in ps and "second command" not in ps


def test_main_return_codes_in_process(monkeypatch, capsys):
    class P:
        def __init__(self, ok, command=""):
            self.ok, self.command = ok, command
    monkeypatch.setattr(hook, "read_payload", lambda: P(False))
    assert hook.main() == 0
    monkeypatch.setattr(hook, "read_payload", lambda: P(True, "echo hi"))
    assert hook.main() == 0
    monkeypatch.setattr(hook, "read_payload",
                        lambda: P(True, "while ps aux | grep -q abc; do sleep 1; done"))
    assert hook.main() == 2
    err = capsys.readouterr().err
    assert "can never end" in err and "friction_log.py append check_poller_self_match.py" in err


# --- round 6 (2026-10-26): polarity and inverted filters

@pytest.mark.parametrize("command", [
    # 647.F2: `until pgrep` waits for the process to APPEAR; a self-match ends it early,
    # it cannot keep it waiting
    'until pgrep -f "tools/x.py" >/dev/null; do sleep 2; done; python3 tools/x.py',
    'while ! pgrep -f "tools/x.py" >/dev/null; do sleep 2; done; python3 tools/x.py',
    'until ps aux | grep -q myjob; do sleep 2; done',
    # 648.F2/650.F2/651.F1: `grep -v grep` drops every line holding "grep", the waiting
    # shell's own line included
    'while ps aux | grep -v grep | grep -q mutation_check; do sleep 5; done; '
    'python3 tools/mutation_check.py x',
    'until ! ps aux | grep -v grep | grep -q "myjob"; do sleep 2; done',
])
def test_shapes_that_end(command):
    code, err = _run(command)
    assert code == 0, err


@pytest.mark.parametrize("command", [
    # an inverted filter that does not match this command leaves the waiter's line in
    'while ps aux | grep -v zzqq | grep -q myjob; do sleep 2; done',
    # the inverted grep must read ps, not a different producer
    'while ps aux | grep -q myjob | grep -v grep; do sleep 2; done',
])
def test_an_inverted_filter_that_keeps_the_waiter_still_blocks(command):
    code, err = _run(command)
    assert code == 2, err


def test_the_pgrep_message_names_the_second_copy_condition():
    msgs = hook.find_violations('while pgrep -f "abc" >/dev/null; do sleep 1; done; run abc')
    assert len(msgs) == 1 and "second" in msgs[0] and "cannot end" not in msgs[0]


def test_a_mutation_check_waiter_is_told_to_drop_the_loop():
    """mutation_check queues on its own run lock, so the loop is not needed at all."""
    msgs = hook.find_violations('while pgrep -f "mutation_check.py" >/dev/null; do sleep 15; '
                                'done; python3 tools/mutation_check.py tools/a.py')
    assert len(msgs) == 1 and "--wait" in msgs[0] and "run lock" in msgs[0]
    other = hook.find_violations('while pgrep -f "abc" >/dev/null; do sleep 1; done; run abc')
    assert "--wait" not in other[0]


def test_a_negation_does_not_carry_into_the_next_pipeline():
    # `!` belongs to `test`; the pgrep after && is not negated, so this while loop holds
    # while abc runs, and abc is in its body.
    code, err = _run('while ! test -f done.txt && pgrep -f abc >/dev/null; do sleep 1; done; '
                     'run abc')
    assert code == 2, err


def test_a_negated_poll_under_if_is_not_a_loop():
    # Only while/until open a condition; `if ! pgrep` returns at once.
    code, err = _run('if ! pgrep -f abc >/dev/null; then run abc; fi')
    assert code == 0, err
