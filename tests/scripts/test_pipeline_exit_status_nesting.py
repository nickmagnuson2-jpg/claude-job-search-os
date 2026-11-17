# Separate from test_check_pipeline_exit_status.py: pins how the hook reads NESTED structure (substitutions, quotes, heredocs) through shell_tokens spans (Q11); that file pins the verdict rules.
"""check_pipeline_exit_status: a pipeline INSIDE a substitution does not decide the
status of the command it is an argument of (2026-11-16).

The six false positives found by the strip_literals pairing replay all had this shape:
a `| tr` / `| cut` inside `"$( ... )"` passed as an argument to printf or echo, with an
`||` after the whole command. That `||` reads printf/echo, not `tr`. The regex
strip_literals hid them by mis-pairing quotes; once quotes pair correctly the
substitution stays visible (as it must, so a real `$(python x)` is still seen) and
`_terminal_formatter` has to stop splitting on a `|` that is nested.

The positive cases pin what must STILL block: a top-level pipeline, a subshell group,
and an assignment whose value is a piped substitution (its status IS the last stage's).
"""
import json
import os
import subprocess
import sys

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
sys.path.insert(0, TOOLS)

import check_pipeline_exit_status as h  # noqa: E402


def _rc(command: str) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "check_pipeline_exit_status.py")],
                       input=payload, capture_output=True, text=True, timeout=30)
    return r.returncode


# --- _terminal_formatter, element level: pure, independent of strip_literals ------------

@pytest.mark.parametrize("element", [
    'printf "%s %s" "$f" "$(wc -l < "$f" | tr -d " ")" ',
    'echo "words: $(pbpaste | wc -w | tr -d " ") total" ',
    'printf "%s" "$(grep -E x "$p" | tail -1 | cut -c1-90)"',
    'echo `ls | head -1`',
])
def test_pipe_inside_an_argument_substitution_is_not_the_terminal_stage(element):
    assert h._terminal_formatter(element) is None


@pytest.mark.parametrize("element, fmt", [
    ("cmd 2>&1 | tail -5", "tail"),
    ('cmd "$(a | b)" | head -3', "head"),           # nested pipe ignored, top-level one counts
    ("( a | head )", "head"),                        # subshell group: status is the last stage
    ("x=$(a | head -1)", "head"),                    # assignment takes the substitution's status
    ('x="$(a | tail -2)"', "tail"),                  # quoting the value does not change the status
])
def test_top_level_and_assignment_pipelines_still_found(element, fmt):
    assert h._terminal_formatter(element) == fmt


# --- whole hook, the six replay shapes made generic --------------------------------------

@pytest.mark.parametrize("command", [
    # 1. printf with a piped substitution argument, || fallback for the whole command
    "for f in a.md b.md; do printf '%-30s %s\\n' \"$f\" \"$([ -f \"$f\" ] && wc -l < \"$f\" | tr -d ' ' || echo MISSING)\"; done",
    # 2. && printf ... || echo, the substitution holding the pipe
    "for p in x y; do [ -f $p ] && printf '%s %s\\n' $p \"$(wc -l < $p | tr -d ' ')\" || echo \"$p: MISSING\"; done",
    # 3. same with a cut stage
    "[ -f $p ] && printf '%s %s\\n' $p \"$(/usr/bin/grep -E 'x' $p | tail -1 | cut -c1-90)\" || echo \"$p: MISSING\"",
    # 4. diff && echo with a word count substitution, || echo
    "diff <(pbpaste) /tmp/a.txt >/dev/null && echo \"CONFIRMED ($(pbpaste | wc -w | tr -d ' ') words)\" || echo 'MISMATCH'",
    # 5. printf of a byte count, || printf
    "for f in a.xlsx b.xlsx; do [ -s \"$f\" ] && printf '%s %s\\n' \"$f\" \"$(wc -c <\"$f\" | tr -d ' ')b\" || printf '%s missing\\n' \"$f\"; done",
    # 6. echo of a count, then a separate statement reading $? of a non-pipeline
    "echo \"lines: $(git status --short | wc -l)\"; git fetch -q origin; echo \"fetch exit=$?\"",
    # zsh `${(f)"$( )"}` with no fallback: a pipe inside it is not a verdict (q11 shape corpus)
    'files=(${(f)"$(git status --porcelain | awk \'{print $2}\')"})',
    # an outer || on a command whose ARGUMENT substitution holds the pipe (q11 shape corpus)
    'echo "n: $(wc -l < f | tr -d " ")" || echo MISSING',
    # the || is the test's else-branch and does fire (Q14, Nick 2026-11-16)
    '[ -f f ] && wc -l < f | tr -d " " || echo MISSING',
    # prose in a heredoc body is data, not a command (q11 shape corpus)
    'cat >> f <<EOF\nrow \\`date\\` and x | head || else\nEOF',
    'cat >> f <<EOF\nrow $(date) and x | head || else\nEOF',
])
def test_replay_false_positive_shapes_pass(command):
    assert _rc(command) == 0


# The restraint replay only protects shapes that appear in transcripts: 39 commands used
# `${...$( )...}`, none with a dead fallback, so the replay could not see Grok q11-r1 F2.
# Shapes that reviewers or tests name go in these two lists with their expected verdict.
@pytest.mark.parametrize("command", [
    'cmd 2>&1 | tail -5; echo "exit=$?"',
    "grep -n x f | head -3 || echo none",
    'x=$(cmd | head -1); echo "rc=$?"',
    'echo "start"; cmd 2>&1 | tail -3; echo "exit=$?"',
    # a dead fallback INSIDE a substitution is still a dead fallback
    'printf "%s\\n" $(git grep -c x -- f | cut -d: -f2 || echo 0)',
    'echo "first: $(grep -o x f | head -1 || echo none)"',
    # && before a non-test command does not make the fallback live for the pipeline
    "cd /tmp && grep -n x f | head -3 || echo none",
    # zsh `${(f)"$( )"}` holding a dead fallback (q11 shape corpus; needs the ${...} sub span)
    'files=(${(f)"$(git status --porcelain | head -5 || echo none)"})',
    # a real substitution in a heredoc body is scanned (q11 shape corpus)
    'cat <<EOF\n$(cmd | head -1 || echo none)\nEOF',
])
def test_true_positive_shapes_still_block(command):
    assert _rc(command) == 2


# --- every substitution body is scanned, not only the first --------------------------

def test_every_substitution_is_scanned_not_only_the_first():
    assert _rc('echo "$(a)" "$(grep x f | head -1 || echo none)"') == 2


# --- shapes with an empty part must pass, not crash (mutation survivors, 2026-11-16) ------

@pytest.mark.parametrize("command", [
    "cmd |",                        # a trailing pipe: an empty last stage
    "x=$(); echo $?",               # an assignment whose substitution is empty
    "x=$(  ); echo \"rc=$?\"",
])
def test_an_empty_stage_or_substitution_is_clean_not_a_crash(command):
    assert _rc(command) == 0


def test_a_negated_test_still_guards_the_else_branch():
    assert _rc("! [ -f f ] && grep x g | head -3 || echo none") == 0


# Codex/Grok q11-r1: a dead fallback inside ${...$( )...} still blocks (Grok F2, P0), and a
# substitution inherits the pipefail state of its statement (Codex F1 + Grok F1, P1).
def test_a_dead_fallback_inside_a_parameter_expansion_blocks():
    assert _rc('echo ${x:-$(cmd | head -1 || echo none)}') == 2


@pytest.mark.parametrize("command", [
    'set -o pipefail; echo "$(cmd | head -1 || echo failed)"',
    'set -euo pipefail\nx=$(cmd | tail -1); echo $?',
])
def test_a_substitution_inherits_pipefail(command):
    assert _rc(command) == 0


def test_pipefail_turned_off_is_inherited_too():
    assert _rc('set -o pipefail; set +o pipefail; echo "$(cmd | head -1 || echo failed)"') == 2


def test_an_assignment_takes_the_status_of_a_substitution_inside_a_parameter_expansion():
    assert _rc('v=${x:-$(cmd | tail -1)}; echo $?') == 2
