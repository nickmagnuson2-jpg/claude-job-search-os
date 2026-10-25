"""Tests for tools/check_zsh_word_split.py, the PreToolUse Bash hook that blocks an unquoted
$VAR that zsh will not split.

The block cases are the real fires (memory: reference_zsh_no_word_split_false_pass_loops),
shortened. The clean cases are the near misses the hook must leave alone: arrays, explicit
splitting, quoting, printing, one-word values, and the pattern inside a string or heredoc.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
SCRIPT = TOOLS / "check_zsh_word_split.py"
sys.path.insert(0, str(TOOLS))
import check_zsh_word_split as hook  # noqa: E402


def _run(command: str):
    payload = json.dumps({"tool_input": {"command": command}})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                       capture_output=True, text=True)
    return r.returncode, r.stderr


# --- BLOCK: the shapes that fired ---------------------------------------------------------

@pytest.mark.parametrize("command,needle", [
    # 2026-10-23: six paths reached codex_verify as one --paths argument
    ('P="docs/a.md docs/b.md"; python3 tools/codex_verify.py --paths $P', "$P"),
    # 2026-08-14: a flag pair in a scalar
    ('R="--repo-root /tmp/x"; python3 tool.py update frag $R', "$R"),
    # 2026-09-23: git diff given one joined path, reported no changes
    ('F="a.py b.py c.py"; git diff --stat -- $F', "$F"),
    # braces do not help
    ('F="a.py b.py"; git add ${F}', "${F}"),
    # set -- $pair leaves $2 empty
    ('for pair in "a 1" "b 2"; do set -- $pair; echo $1 $2; done', "$pair"),
    # 2026-09-30 /standup: each quoted item reached python as one file name
    ('for c in "pipeline_staleness.py --target-date x" "todos.py --top 3"; do '
     'python3 tools/$c; done', "tools/$c"),
    # built up inside a loop, then passed as arguments
    ('A=""; for e in x y; do A="$A --accept $e"; done; python3 t.py $A', "$A"),
])
def test_spaced_value_passed_as_arguments_is_blocked(command, needle):
    code, err = _run(command)
    assert code == 2, err
    assert needle in err and "ONE argument" in err


@pytest.mark.parametrize("command,var", [
    # 2026-08-12: the PII rescan looped once over the whole list
    ('FILES=$(git show --name-only HEAD); for p in $FILES; do check "$p"; done', "FILES"),
    ('F="a.md b.md"; for f in $F; do git check-ignore -q "$f"; done', "F"),
    ('L=$(grep -n x f | cut -d: -f1); for s in ${L}; do echo "$s"; done', "L"),
])
def test_loop_over_a_scalar_is_blocked(command, var):
    code, err = _run(command)
    assert code == 2, err
    assert f"loops over the scalar ${var}" in err and "ONCE" in err


# --- CLEAN: what zsh handles correctly, or what the hook cannot judge ---------------------

@pytest.mark.parametrize("command", [
    'P=(docs/a.md docs/b.md); python3 tools/codex_verify.py --paths "${P[@]}"',   # array
    'P=(a b); for f in $P; do echo "$f"; done',                                     # array loop
    'P="a b"; git add ${=P}',                                                       # explicit split
    'P="a b"; for f in ${=P}; do echo "$f"; done',
    'P="a b"; git commit -m "$P"',                                                  # quoted
    'P="a b"; echo $P; print $P',                                                   # printers
    'P="python3 x.py --flag"; eval $P',                                             # eval re-splits
    'D=$(date +%F); python3 tools/x.py --target-date $D',                           # one word
    'A=abc; B=def; for f in $A $B; do echo "$f"; done',                             # one-word loop
    'python3 tools/x.py --paths $P',                         # P not assigned in this command
    'for f in a.py b.py; do python3 $f; done',                                      # plain items
    'for f in *.py; do python3 "$f"; done',
    "grep -n 'for f in $F; do' notes.md",                                           # in a string
    'git commit -F - <<\'EOF\'\nP="a b"; git add $P\nEOF',                          # heredoc body
    "bash -c 'P=\"a b\"; git add $P'",                                              # bash, quoted
    'setopt shwordsplit; P="a b"; git add $P',                                      # splitting on
    'emulate -L sh; F="a b"; for f in $F; do echo $f; done',
    'X="a b"; Y=$X; echo done',                                                     # assignment
    '',
])
def test_clean(command):
    code, err = _run(command)
    assert code == 0, err


def test_an_unreadable_payload_fails_open():
    r = subprocess.run([sys.executable, str(SCRIPT)], input="not json",
                       capture_output=True, text=True)
    assert r.returncode == 0


def test_the_message_names_the_fix():
    _, err = _run('P="a b"; git add $P')
    assert err.startswith("BLOCKED: zsh word-split")
    assert '"${P[@]}"' in err and "${=P}" in err


# --- the detector directly, so a mutant in one shape cannot hide behind another -----------

def test_find_violations_reports_each_shape_once_per_use():
    v = hook.find_violations('P="a b"; git add $P; git rm $P')
    assert len(v) == 2


def test_a_reassigned_array_is_no_longer_judged_as_a_scalar():
    assert hook.find_violations('P="a b"; P=(a b); git add $P') == []


def test_a_name_that_only_starts_with_the_variable_is_not_a_reference():
    assert hook.find_violations('P="a b"; echo x; git add $PATH') == []


def test_a_spaced_loop_variable_used_inside_quotes_is_clean():
    assert hook.find_violations('for c in "a b"; do python3 "tools/$c"; done') == []


@pytest.mark.parametrize("command", ["", 'setopt shwordsplit; P="a b"; git add $P',
                                     "for", "for x", "echo for"])
def test_nothing_to_judge_returns_an_empty_list(command):
    assert hook.find_violations(command) == []


def test_a_tokenizer_error_fails_open_with_an_empty_list(monkeypatch):
    def boom(_):
        raise ValueError("unreadable")
    monkeypatch.setattr(hook, "tokenize", boom)
    assert hook.find_violations('P="a b"; git add $P') == []


def test_a_quoted_loop_list_is_not_a_loop_over_the_scalar():
    assert hook.find_violations('F="a b"; for f in "$F"; do echo "$f"; done') == []


def test_the_hook_imports_its_libraries_without_the_caller_setting_the_path(tmp_path):
    # run_path does not put the script's folder on sys.path, so this passes only if the
    # hook adds it itself, as a hook run from another working directory needs.
    r = subprocess.run([sys.executable, "-c",
                        f"import runpy; runpy.run_path({str(SCRIPT)!r}, run_name='x')"],
                       cwd=str(tmp_path), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- round-8 review findings (582-589), 2026-10-24 -----------------------------------------

@pytest.mark.parametrize("command,needle", [
    ('export P="a b"; git add $P', "$P"),                              # 583.F3/584.F6
    ('local P="a b"; git add $P', "$P"),
    ('declare -r P="a b"; git add $P', "$P"),
    ('P="a b"; Q=$P; git add $Q', "$Q"),                               # 589.F1: a copy keeps the kind
    ('P="a b"; Q="${P}"; git add $Q', "$Q"),
    ('P="a b"; printf "%s\\n" $P', "$P"),                              # 586.F6: printf output differs
    ('P="a b"; command git add $P', "$P"),                             # wrappers read through
    ('P="a b"; git add $P; setopt shwordsplit', "$P"),                 # 582.F2: setopt AFTER the use
])
def test_round_8_misses_are_blocked(command, needle):
    code, err = _run(command)
    assert code == 2, err
    assert needle in err


@pytest.mark.parametrize("command", [
    'P="a b"; tool --name=$P',                                         # 585.F4: one argument wanted
    'P="hello world"; [[ $P == "hello world" ]] && echo ok',           # 589.F2
    'P="a b"; [ $P = "a b" ]; test $P = "a b"',
    'P="a b"; case $P in *) echo;; esac',
    'P="a b"; command echo $P; builtin print $P',                      # 582.F3/587.F6
    'declare -a P=(a b); git add $P',                                  # an array, by flag
    'setopt shwordsplit; P="a b"; git add $P',                         # splitting on BEFORE the use
])
def test_round_8_false_blocks_are_clean(command):
    code, err = _run(command)
    assert code == 0, err


def test_a_spaced_loop_item_is_still_caught_inside_a_word():
    v = hook.find_violations('for c in "x.py --flag"; do python3 tools/$c; done')
    assert len(v) == 1 and "tools/$c" in v[0]


@pytest.mark.parametrize("command", [
    'P="a b"; ssh host bash; git add $P',          # "bash" as an argument is not `emulate bash`
    'setopt extendedglob; P="a b"; git add $P',    # another option does not turn splitting on
])
def test_only_a_real_split_switch_stops_judging(command):
    assert _run(command)[0] == 2


def test_an_assignment_builtin_ends_at_its_operator():
    assert hook.find_violations('export P="a b"; echo $P') == []


def test_an_assignment_builtin_without_a_value_is_ignored():
    assert hook.find_violations('export P; local Q; git add $P') == []


@pytest.mark.parametrize("command", [
    'P="a b"; if [[ $P == "a b" ]]; then echo ok; fi',             # 596.F2/597.F1
    'P="a b"; while test $P = x; do break; done',
    'P="a b" git add $P',                                          # 591.F2: prefix assignment
])
def test_round_9_false_blocks_are_clean(command):
    code, err = _run(command)
    assert code == 0, err


def test_a_prefix_assignment_does_not_hide_a_later_real_one():
    assert hook.find_violations('P="a b" true; P="c d"; git add $P') != []


@pytest.mark.parametrize("command", [
    'P="out file.txt"; python3 x.py > $P',          # 604.F1/610.F1: redirect targets
    'P="a b"; grep a <<< $P',
    'P="a b"; sort < $P 2> $P',
])
def test_a_redirect_target_is_not_an_argument(command):
    code, err = _run(command)
    assert code == 0, err


def test_the_word_after_a_redirect_target_is_still_judged():
    assert hook.find_violations('P="a b"; python3 x.py > out.txt $P') != []


def test_a_heredoc_body_is_not_judged_as_an_argument():
    assert hook.find_violations('for c in "a b"; do cat <<EOF\ntools/$c\nEOF\ndone') == []


@pytest.mark.parametrize("command", [
    'P="a b"; command -p echo $P',                                     # 612.F1/624.F1
    'P="a b"; time -p echo $P',
    'P="a b"; exec -a name echo $P',
    'P="a b"; nohup -- echo $P',
    'P="a b" PYTHONIOENCODING=utf-8 git add $P',                       # 625.F4: prefix chain
])
def test_wrapper_options_and_prefix_chains_are_clean(command):
    code, err = _run(command)
    assert code == 0, err


@pytest.mark.parametrize("command", [
    'P="a b"; command -p git add $P',
    'P="a b"; exec -a name git add $P',
    'P="a b"; nohup -- git add $P',
    'P="a b" X=1; git add $P',                                         # a chain with no command
])
def test_wrapper_options_still_reach_the_real_command(command):
    code, err = _run(command)
    assert code == 2, err
