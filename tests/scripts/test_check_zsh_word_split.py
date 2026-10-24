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
    'P="a b"; echo $P; printf "%s\\n" $P; print $P',                                # printers
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
