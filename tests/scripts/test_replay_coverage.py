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
    assert rc.CLASSES[name].hits(cmd) is hit


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


# --- Grok r1 F1/F2/F3 (2026-11-23; F5 parked): detect and twin must agree on what a prefix is -------
@pytest.mark.parametrize("cmd,hit", [
    ("X=1 && python3 w.py feedback_a.md", False),       # F1: an assignment statement, not a prefix
    ("FOO=1 $HOME/bin/python3 w.py", True),             # F2: command starts with $
    ("FOO=1 ~/bin/python3 w.py", True),
    ("FOO=1 'python3' w.py", True),
    ("FOO=1 \"python3\" w.py", True),
])
def test_env_prefix_detect(cmd, hit):
    assert rc.CLASSES["env_prefix"].hits(cmd) is hit


@pytest.mark.parametrize("cmd,twin", [
    ("FOO=1 $HOME/bin/python3 w.py", "$HOME/bin/python3 w.py"),                  # F2
    ("FOO=1 ~/bin/python3 w.py", "~/bin/python3 w.py"),
    ("T=$(date '+%H:%M'); python3 w.py", "T=$(date '+%H:%M'); python3 w.py"),     # F3: a statement
    ("for f in a; do\n  A=1 python3 w.py\ndone", "for f in a; do\n  python3 w.py\ndone"),  # indented
    ("FOO=$(echo hi) python3 w.py feedback_a.md", "python3 w.py feedback_a.md"),  # F3
    ("T=$(date '+%H:%M') python3 w.py", "python3 w.py"),                          # F3, real shape
    ("FOO=`date +%s` python3 w.py", "python3 w.py"),
    ("FOO=x\\ y python3 w.py", "python3 w.py"),
    ("SB=$(mktemp -d /tmp/x.XXXX)\npython3 w.py", "SB=$(mktemp -d /tmp/x.XXXX)\npython3 w.py"),  # a statement
])
def test_strip_env_prefix_grok_r1(cmd, twin):
    assert rc._strip_env_prefix(cmd) == twin


def test_every_env_prefix_hit_has_a_twin_that_differs(tmp_path):
    # The invariant behind F1/F2: a row counted in the class is one the twin actually changes.
    for cmd in ["X=1 && python3 w.py", "FOO=1 $HOME/x", "A=1 B=2 python3 w.py", "ls; M=x;",
                "for f in a; do\n  A=1 python3 -c \"\nx=[c for c in l.split('|')]\n\"\ndone",
                "A='x\ny' python3 w.py"]:   # a value spanning lines: the regex sees it, the per-line twin cannot
        if rc.CLASSES["env_prefix"].hits(cmd):
            assert rc._strip_env_prefix(cmd) != cmd, cmd


# --- mutation kills, 2026-11-23 (replay_coverage r2: 13 survivors of 83) ---------------------
@pytest.mark.parametrize("cmd,twin", [
    # the body ends at its delimiter: a prefix after it is a command again, one inside is data
    ("cat <<EOF\nB=2 x\nEOF\nA=1 python3 w.py", "cat <<EOF\nB=2 x\nEOF\npython3 w.py"),
    # <<- strips leading tabs before matching the delimiter; plain << does not
    ("cat <<-EOF\n\tB=2 x\n\tEOF\nA=1 python3 w.py", "cat <<-EOF\n\tB=2 x\n\tEOF\npython3 w.py"),
    ("cat <<EOF\n\tEOF\nB=2 x\nEOF\nA=1 python3 w.py", "cat <<EOF\n\tEOF\nB=2 x\nEOF\npython3 w.py"),
])
def test_heredoc_body_ends_at_its_delimiter(cmd, twin):
    assert rc._strip_env_prefix(cmd) == twin


def test_load_corpus_skips_blank_lines(tmp_path):
    p = tmp_path / "c.jsonl"
    row = json.dumps({"tool_name": "Bash", "tool_input": {"command": "a"}})
    p.write_text(row + "\n\n   \n" + row.replace('"a"', '"b"') + "\n")
    cmds, reach = rc.load_corpus(str(p))
    assert cmds == ["a", "b"] and reach["rows"] == 2


def test_each_hook_runs_once_per_distinct_command(tmp_path, hooks, monkeypatch):
    # Every verdict is a subprocess per corpus row; measure() must not ask twice.
    calls = []
    real = rc.verdict

    def counting(hook, cmd, env):
        calls.append((hook, cmd))
        return real(hook, cmd, env)
    monkeypatch.setattr(rc, "verdict", counting)
    cmds = ["PYTHONIOENCODING=utf-8 python3 w.py feedback_a.md"]
    _measure(tmp_path, hooks, cmds, ["env_prefix"])
    assert len(calls) == len(set(calls)) == 4    # old/new x original/twin


def test_render_says_unmeasured_only_when_unmeasured():
    rows = [{"class": "c", "n": 1, "unmeasured": False, "changed_old_to_new": 0, "twin": False}]
    assert "UNMEASURED" not in rc.render(rows)
    rows[0].update(n=0, unmeasured=True)
    assert "UNMEASURED" in rc.render(rows)


def test_env_flag_is_passed_to_the_hook_and_bad_form_is_a_usage_error(tmp_path):
    # old blocks only when S4_PROBE reaches it; new always allows. So old->new changes the
    # verdict on the row ONLY if --env was passed through (Grok r3 F1: with one hook for both,
    # all-0 and all-2 looked the same and dropping the env line stayed green).
    envhook = tmp_path / "envhook.py"
    envhook.write_text("import os, sys\nsys.exit(2 if os.environ.get('S4_PROBE') == 'on' else 0)\n")
    allow = tmp_path / "allow.py"
    allow.write_text("import sys\nsys.exit(0)\n")
    corpus = _corpus(tmp_path, ["A=1 python3 w.py feedback_a.md"])
    base = ["--corpus", corpus, "--old", f"{sys.executable} {envhook}", "--new",
            f"{sys.executable} {allow}", "--relevant", REL, "--classes", "env_prefix", "--json"]
    out = subprocess.run([sys.executable, TOOL, *base, "--env", "S4_PROBE=on"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    row = json.loads(out.stdout)["classes"][0]
    assert row["n"] == 1 and row["changed_old_to_new"] == 1
    bad = subprocess.run([sys.executable, TOOL, *base, "--env", "S4_PROBE"], capture_output=True, text=True)
    assert bad.returncode == 2 and "--env needs K=V" in bad.stderr


def test_main_returns_0_in_process_when_clean(tmp_path, hooks, capsys):
    # sys.exit(None) also exits 0, so only an in-process call sees main()'s return value.
    corpus = _corpus(tmp_path, ["PYTHONIOENCODING=utf-8 python3 w.py feedback_a.md"])
    assert rc.main(["--corpus", corpus, "--old", hooks["blind"], "--new", hooks["fixed"],
                    "--relevant", REL, "--classes", "env_prefix"]) == 0


def test_script_imports_count_report_without_its_dir_on_sys_path(tmp_path):
    r = subprocess.run([sys.executable, "-I", TOOL, "--help"], capture_output=True, text=True,
                       cwd=tmp_path, timeout=60)
    assert r.returncode == 0, r.stderr
