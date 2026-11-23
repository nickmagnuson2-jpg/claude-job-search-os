"""Tests for tools/replay_coverage.py: zero-count and twin-divergence coverage for hook replays.

Origin: codify #20 (a prefix blindness shared by the old and new hook, invisible to a verdict
diff) and #27 (a shape with no relevant rows in the corpus), 2026-11-22. The fixture hooks below
reproduce both: BLIND judges words[0] only, so an env prefix hides the interpreter; FIXED strips
the prefix first.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import replay_coverage as rc  # noqa: E402

TOOL = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "replay_coverage.py")

_HOOK = """import json, re, sys
cmd = json.load(sys.stdin)["tool_input"]["command"]
if {fixed}:
    cmd = re.sub(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\\S*\\s+)+", "", cmd)
words = cmd.split()
sys.exit(2 if words and words[0].startswith("python") and "feedback_" in cmd else 0)
"""


@pytest.fixture
def hooks(tmp_path):
    out = {}
    for name, fixed in (("blind", False), ("fixed", True)):
        p = tmp_path / f"{name}.py"
        p.write_text(_HOOK.format(fixed=fixed))
        out[name] = f"{sys.executable} {p}"
    return out


def _corpus(tmp_path, commands, extra=()):
    p = tmp_path / "corpus.jsonl"
    rows = [{"tool_name": "Bash", "tool_input": {"command": c}} for c in commands]
    rows += list(extra)
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p)


REL = r"feedback_"


def _measure(tmp_path, hooks, commands, classes, old="blind", new="fixed"):
    cmds = rc.load_commands(_corpus(tmp_path, commands))
    import re
    return {r["class"]: r for r in rc.measure(cmds, [rc.CLASSES[c] for c in classes],
                                               re.compile(REL), hooks[old], hooks[new],
                                               dict(os.environ))}


# --- #20: a blindness both hooks share is invisible to a verdict diff, visible to the twin ----
def test_twin_divergence_counts_a_shared_prefix_blindness(tmp_path, hooks):
    cmds = ["PYTHONIOENCODING=utf-8 python3 w.py feedback_a.md",
            "LC_ALL=C python3 w.py feedback_b.md",
            "python3 w.py feedback_c.md"]
    r = _measure(tmp_path, hooks, cmds, ["env_prefix"], old="blind", new="blind")["env_prefix"]
    assert r["n"] == 2
    assert r["changed_old_to_new"] == 0          # the verdict diff sees nothing
    assert r["twin_diverged_new"] == 2           # the twin sees both


def test_no_twin_divergence_once_the_hook_is_fixed(tmp_path, hooks):
    cmds = ["PYTHONIOENCODING=utf-8 python3 w.py feedback_a.md"]
    r = _measure(tmp_path, hooks, cmds, ["env_prefix"])["env_prefix"]
    assert (r["twin_diverged_old"], r["twin_diverged_new"]) == (1, 0)
    assert r["changed_old_to_new"] == 1


# --- #27: a shape with no relevant rows is UNMEASURED ------------------------------------------
def test_a_class_with_no_relevant_rows_is_unmeasured(tmp_path, hooks):
    cmds = ["(cd /tmp && true); echo hi > notes.txt",   # the shape, but not relevant
            "python3 w.py feedback_a.md"]
    r = _measure(tmp_path, hooks, cmds, ["subshell_cd_write"])["subshell_cd_write"]
    assert r["n"] == 0 and r["unmeasured"] is True
    assert r["twin"] is False


def test_a_class_with_a_relevant_row_is_measured(tmp_path, hooks):
    cmds = ["(cd /tmp && true); echo x > feedback_x.md"]
    r = _measure(tmp_path, hooks, cmds, ["subshell_cd_write"])["subshell_cd_write"]
    assert r["n"] == 1 and r["unmeasured"] is False


# --- detectors and twin ----------------------------------------------------------------------
@pytest.mark.parametrize("cmd,twin", [
    ("PYTHONIOENCODING=utf-8 python3 x.py", "python3 x.py"),
    ('cd a && A=1 B="x y" python3 - <<EOF\nprint(1)\nEOF', "cd a && python3 - <<EOF\nprint(1)\nEOF"),
    ("ls; LC_ALL=C printf a >> f", "ls; printf a >> f"),
    ("X=1 && echo hi", "X=1 && echo hi"),               # a bare assignment is not a prefix
    # Validation on the real corpus, 2026-11-23: each of these was wrongly stripped.
    ("M=~/mem\ncat > $M/feedback_x.md", "M=~/mem\ncat > $M/feedback_x.md"),   # statement + newline
    ("M=~/mem; f=$M/x python3 w.py", "M=~/mem; python3 w.py"),               # ; ends M's statement
    ("python3 - <<'PY'\np='feedback_x.md'\nPY", "python3 - <<'PY'\np='feedback_x.md'\nPY"),  # body
    ("A=1 python3 - <<'PY'\nB=2 x\nPY", "python3 - <<'PY'\nB=2 x\nPY"),       # prefix yes, body no
])
def test_strip_env_prefix(cmd, twin):
    assert rc._strip_env_prefix(cmd) == twin


@pytest.mark.parametrize("name,cmd,hit", [
    ("env_prefix", "echo FOO=bar baz", False),            # an argument, not a prefix
    ("cd_dash", "cd - && ls", True),
    ("cd_dash", "cd -p", False),
    ("pushd_popd", "pushd /tmp; popd", True),
    ("heredoc", "cat <<'EOF'\nx\nEOF", True),
    ("subshell_cd_write", "(cd a); ls", False),           # no write after it
])
def test_detectors(name, cmd, hit):
    assert bool(rc.CLASSES[name].detect.search(cmd)) is hit


def test_an_assignment_only_inside_a_heredoc_body_is_not_a_prefix_row(tmp_path, hooks):
    cmds = ["python3 - <<'PY'\nP=1 feedback_x.md\nPY"]
    r = _measure(tmp_path, hooks, cmds, ["env_prefix"])["env_prefix"]
    assert r["n"] == 0


def test_load_commands_is_distinct_and_bash_only(tmp_path):
    p = _corpus(tmp_path, ["a", "a", "b"],
                extra=[{"tool_name": "Write", "tool_input": {"file_path": "x"}}])
    assert rc.load_commands(p) == ["a", "b"]


# --- CLI ---------------------------------------------------------------------------------------
def _cli(tmp_path, hooks, cmds, *extra):
    return subprocess.run([sys.executable, TOOL, "--corpus", _corpus(tmp_path, cmds),
                           "--old", hooks["blind"], "--new", hooks["fixed"],
                           "--relevant", REL, *extra], capture_output=True, text=True)


def test_cli_exit_0_when_every_class_is_measured_and_clean(tmp_path, hooks):
    r = _cli(tmp_path, hooks, ["PYTHONIOENCODING=utf-8 python3 w.py feedback_a.md"],
             "--classes", "env_prefix")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "N=    1" in r.stdout and "twin diverged: old 1 of 1, new 0 of 1" in r.stdout


def test_cli_exit_1_and_says_unmeasured(tmp_path, hooks):
    r = _cli(tmp_path, hooks, ["python3 w.py feedback_a.md"], "--classes", "subshell_cd_write")
    assert r.returncode == 1
    assert "UNMEASURED" in r.stdout


def test_cli_exit_1_when_twins_diverge_under_new(tmp_path, hooks):
    r = subprocess.run([sys.executable, TOOL, "--corpus",
                        _corpus(tmp_path, ["PYTHONIOENCODING=utf-8 python3 w.py feedback_a.md"]),
                        "--old", hooks["fixed"], "--new", hooks["blind"], "--relevant", REL,
                        "--classes", "env_prefix", "--json"], capture_output=True, text=True)
    assert r.returncode == 1
    assert json.loads(r.stdout)["classes"][0]["twin_diverged_new"] == 1


def test_cli_exit_2_on_an_unknown_class(tmp_path, hooks):
    r = _cli(tmp_path, hooks, ["ls"], "--classes", "no_such_class")
    assert r.returncode == 2 and "unknown class" in r.stderr


def test_load_corpus_reach_conserves_and_the_cli_prints_it(tmp_path, hooks):
    p = _corpus(tmp_path, ["a", "a", "", "b"],
                extra=[{"tool_name": "Write", "tool_input": {"file_path": "x"}}])
    cmds, reach = rc.load_corpus(p)
    assert cmds == ["a", "b"]
    assert reach == {"rows": 5, "not a Bash call": 1, "empty command": 1, "repeated command": 1}
    r = subprocess.run([sys.executable, TOOL, "--corpus", p, "--old", hooks["blind"],
                        "--new", hooks["fixed"], "--relevant", REL, "--classes", "env_prefix"],
                       capture_output=True, text=True)
    assert ("population 5 | included 2 | excluded: not a Bash call 1, empty command 1, "
            "repeated command 1") in r.stdout
