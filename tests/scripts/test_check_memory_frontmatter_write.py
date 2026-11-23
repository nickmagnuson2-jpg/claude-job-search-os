"""tests for check_memory_frontmatter_write.py (PreToolUse, Write|Edit|MultiEdit|Bash).

Runs the real hook in a subprocess with a payload on stdin, against a temporary memory
folder (MEMORY_HOOK_DIR). HOOK is the hook file (staged copy until installed in tools/);
PYTHONPATH carries the repo's tools/ so its imports resolve. Fixtures are invented.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HOOK = Path(__file__).resolve().parents[2] / "tools" / "check_memory_frontmatter_write.py"
TOOLS = Path(__file__).resolve().parents[2] / "tools"

RULE = """---
name: feedback_widget_rule
description: "An invented rule."
metadata:
  node_type: memory
  type: feedback
  occurrences: 2
  promoted: no
  reopen_gate: "REOPEN on the 3rd fire"
---

**Rule:** invented prose.
"""


@pytest.fixture
def mem(tmp_path):
    d = tmp_path / "projects" / "memory"
    d.mkdir(parents=True)
    (d / "feedback_widget_rule.md").write_text(RULE, encoding="utf-8")
    (d / "feedback_gadget_rule.md").write_text(RULE.replace("widget", "gadget"), encoding="utf-8")
    (d / "MEMORY.md").write_text("# index\n", encoding="utf-8")
    return d


def hook(payload: dict, mem: Path, cwd: str | None = None):
    payload.setdefault("cwd", cwd or str(mem.parent))
    env = {**os.environ, "PYTHONPATH": str(TOOLS), "MEMORY_HOOK_DIR": str(mem),
           "MEMORY_HOOK_REPO": str(TOOLS.parent), "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stderr


def write(path, content):
    return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": content}}


def edit(path, old, new, **kw):
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path), "old_string": old,
                                                "new_string": new, **kw}}


def bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


# ------------------------------------------------------------------ Write

def test_valid_write_is_allowed(mem):
    assert hook(write(mem / "feedback_widget_rule.md",
                      RULE.replace("promoted: no", "promoted: hook, 2026-11-22")), mem)[0] == 0


def test_new_file_may_set_its_initial_occurrences(mem):
    code, err = hook(write(mem / "feedback_new_rule.md", RULE.replace("widget", "new")), mem)
    assert code == 0, err


@pytest.mark.parametrize("bad,needle", [
    (RULE.replace('description: "An invented rule."',
                  'description: "Quoted" then more text'), "frontmatter"),
    (RULE.replace("promoted: no", "promoted: someday"), "promoted"),
    (RULE.replace("promoted: no", "promoted: merged"), "promoted"),
    ("no frontmatter at all\n", "frontmatter"),
])
def test_invalid_write_is_blocked(mem, bad, needle):
    code, err = hook(write(mem / "feedback_widget_rule.md", bad), mem)
    assert code == 2
    assert err.startswith("BLOCKED by check_memory_frontmatter_write: memory rule frontmatter")
    assert needle in err
    assert "memory_write.py set" in err


def test_write_that_changes_occurrences_is_blocked(mem):
    code, err = hook(write(mem / "feedback_widget_rule.md",
                           RULE.replace("occurrences: 2", "occurrences: 3")), mem)
    assert code == 2
    assert "occurrences: 2 -> 3 is a count change" in err
    assert "memory_write.py bump feedback_widget_rule.md" in err


@pytest.mark.parametrize("name", ["MEMORY.md", "index-tools.md", "archive-2026-11.md",
                                  "promotion-backlog.md", "notes.txt"])
def test_out_of_scope_names_are_not_judged(mem, name):
    assert hook(write(mem / name, "anything\n"), mem)[0] == 0


def test_a_file_outside_the_memory_folder_is_not_judged(mem, tmp_path):
    assert hook(write(tmp_path / "feedback_widget_rule.md", "no frontmatter\n"), mem)[0] == 0


def test_a_relative_path_resolves_against_cwd(mem):
    code, _ = hook(write("memory/feedback_widget_rule.md", "no frontmatter\n"), mem,
                   cwd=str(mem.parent))
    assert code == 2


def test_an_older_defect_in_an_untouched_key_does_not_block(mem):
    broken = RULE.replace("promoted: no", "promoted: someday")
    (mem / "feedback_widget_rule.md").write_text(broken, encoding="utf-8")
    code, err = hook(write(mem / "feedback_widget_rule.md",
                           broken.replace("invented prose.", "invented prose, edited.")), mem)
    assert code == 0, err


# ------------------------------------------------------------------ Edit / MultiEdit

def test_edit_is_judged_on_the_reconstructed_file(mem):
    code, err = hook(edit(mem / "feedback_widget_rule.md", "promoted: no", "promoted: maybe"), mem)
    assert code == 2 and "promoted" in err
    code, _ = hook(edit(mem / "feedback_widget_rule.md", "promoted: no",
                        "promoted: hook, 2026-11-22"), mem)
    assert code == 0


def test_edit_of_occurrences_is_blocked(mem):
    code, err = hook(edit(mem / "feedback_widget_rule.md", "occurrences: 2", "occurrences: 3"), mem)
    assert code == 2 and "count change" in err


def test_edit_with_absent_old_string_is_left_to_the_tool(mem):
    assert hook(edit(mem / "feedback_widget_rule.md", "not in the file", "x"), mem)[0] == 0


def test_edit_replace_all(mem):
    code, _ = hook(edit(mem / "feedback_widget_rule.md", "no", "maybe", replace_all=True), mem)
    assert code == 2


def test_multiedit_applies_edits_in_order(mem):
    payload = {"tool_name": "MultiEdit", "tool_input": {
        "file_path": str(mem / "feedback_widget_rule.md"),
        "edits": [{"old_string": "promoted: no", "new_string": "promoted: X"},
                  {"old_string": "promoted: X", "new_string": "promoted: hook, 2026-11-22"}]}}
    assert hook(payload, mem)[0] == 0
    payload["tool_input"]["edits"][1]["new_string"] = "promoted: unknownword"
    assert hook(payload, mem)[0] == 2


def test_edit_of_a_missing_file_is_left_to_the_tool(mem):
    assert hook(edit(mem / "feedback_absent_rule.md", "a", "b"), mem)[0] == 0


# ------------------------------------------------------------------ Bash

def test_heredoc_create_is_validated(mem):
    ok = f"cat > {mem}/feedback_new_rule.md <<'EOF'\n{RULE.replace('widget', 'new')}EOF"
    assert hook(bash(ok), mem)[0] == 0
    bad = f"cat > {mem}/feedback_new_rule.md <<'EOF'\n---\nname: x\ndescription: \"a\" b\n---\nEOF"
    code, err = hook(bash(bad), mem)
    assert code == 2 and "feedback_new_rule.md" in err


def test_variable_target_is_expanded(mem):
    cmd = f"M={mem}; cat > $M/feedback_new_rule.md <<'EOF'\nno frontmatter\nEOF"
    assert hook(bash(cmd), mem)[0] == 2


def test_plain_append_is_allowed(mem):
    cmd = f"cat >> {mem}/feedback_widget_rule.md <<'EOF'\n\n## Supplement 2026-11-22\n\nMore prose.\nEOF"
    assert hook(bash(cmd), mem)[0] == 0


@pytest.mark.parametrize("body", ["\n  occurrences: 3\n", "promoted: hook\n",
                                  "---\nname: x\npromoted: yes\n---\n"])
def test_append_that_sets_state_is_blocked(mem, body):
    cmd = f"cat >> {mem}/feedback_widget_rule.md <<'EOF'\n{body}EOF"
    code, err = hook(bash(cmd), mem)
    assert code == 2 and "an append may not set a state key on a line of its own" in err


def test_append_opening_with_a_divider_is_allowed(mem):
    """Replay: 9 legitimate supplements open with a `---` markdown divider."""
    cmd = f"cat >> {mem}/feedback_widget_rule.md <<'EOF'\n\n---\n\n## Fire 3 2026-11-22\n\nProse.\nEOF"
    code, err = hook(bash(cmd), mem)
    assert code == 0, err


def test_a_loop_body_is_judged_by_its_real_command(mem):
    ok = (f"M={mem}; for f in feedback_widget_rule.md feedback_gadget_rule.md; "
          "do printf '\\n## Note 2026-11-22\\n' >> $M/$f; done")
    code, err = hook(bash(ok), mem)
    assert code == 0, err
    bad = (f"M={mem}; for f in feedback_widget_rule.md; do sort /tmp/a > $M/$f; done")
    code, err = hook(bash(bad), mem)
    assert code == 2 and "a redirect from `sort`" in err


def test_a_tilde_path_append_is_not_read_as_an_in_place_write(mem, tmp_path):
    """The append's redirect keeps `~`; the whole-command scan expands it. Both must be
    counted as the same write (replay ks5pbZ, a prose append blocked in a draft)."""
    tilde = "~/" + str(mem.relative_to(tmp_path))
    cmd = f"cat >> {tilde}/feedback_widget_rule.md <<'EOF'\n\nmore prose\nEOF"
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(TOOLS),
           "MEMORY_HOOK_DIR": str(mem), "MEMORY_HOOK_REPO": str(TOOLS.parent),
           "PYTHONIOENCODING": "utf-8"}
    payload = {**bash(cmd), "cwd": str(tmp_path)}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    bad = cmd.replace("more prose", "promoted: someday").replace(">>", ">")
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({**bash(bad), "cwd": str(tmp_path)}),
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 2, p.stderr


def test_echo_append_and_create(mem):
    assert hook(bash(f'echo "more prose" >> {mem}/feedback_widget_rule.md'), mem)[0] == 0
    assert hook(bash(f'echo "no frontmatter" > {mem}/feedback_widget_rule.md'), mem)[0] == 2


@pytest.mark.parametrize("cmd,needle", [
    ("sed -i '' 's/occurrences: 2/occurrences: 3/' {m}/feedback_widget_rule.md", "in-place writer"),
    ("echo x | tee {m}/feedback_widget_rule.md", "in-place writer"),
    ("python3 - <<'PY'\np='{m}/feedback_widget_rule.md'\nopen(p,'w').write('x')\nPY", "script that writes"),
    ("python3 -c \"open('{m}/feedback_widget_rule.md','w').write('x')\"", "script that writes"),
    ("node -e \"require('fs').writeFileSync('{m}/feedback_widget_rule.md','x')\"", "script that writes"),
    ("cp /tmp/x.md {m}/feedback_widget_rule.md", "cp into the memory folder"),
    ("mv /tmp/feedback_new_rule.md {m}", "mv into the memory folder"),
    ("sort /tmp/a > {m}/feedback_widget_rule.md", "a redirect from `sort`"),
])
def test_unvalidated_writers_are_blocked(mem, cmd, needle):
    code, err = hook(bash(cmd.format(m=mem)), mem)
    assert code == 2, err
    assert needle in err


# Grok install-r2 F1: a leading VAR=value hid the interpreter, so the form CLAUDE.md
# requires (PYTHONIOENCODING=utf-8 python3 ...) was never judged.
@pytest.mark.parametrize("cmd", [
    "PYTHONIOENCODING=utf-8 python3 -c \"open('{m}/feedback_widget_rule.md','w').write('x')\"",
    "PYTHONIOENCODING=utf-8 python3 - <<'PY'\np='{m}/feedback_widget_rule.md'\nopen(p,'w').write('x')\nPY",
    "env PYTHONIOENCODING=utf-8 python3 -c \"open('{m}/feedback_widget_rule.md','w').write('x')\"",
    "A=1 B=2 PYTHONIOENCODING=utf-8 python3 -c \"open('{m}/feedback_widget_rule.md','w').write('x')\"",
    "env A=1 B=2 python3 - <<'PY'\nopen('{m}/feedback_widget_rule.md','w').write('x')\nPY",
    # A wrapper option value (`nobody`) is also a possible command position; the
    # interpreter must still be found behind it.
    "sudo -u nobody python3 -c \"open('{m}/feedback_widget_rule.md','w').write('x')\"",
])
def test_an_assignment_prefix_does_not_hide_the_interpreter(mem, cmd):
    code, err = hook(bash(cmd.format(m=mem)), mem)
    assert code == 2, err
    assert "script that writes" in err


@pytest.mark.parametrize("cmd", [
    "PYTHONIOENCODING=utf-8 python3 - <<'PY'\nprint(open('{m}/feedback_widget_rule.md').read())\nPY",
    "PYTHONIOENCODING=utf-8 python3 {tools}/memory_write.py --help",
    "LC_ALL=C sort {m}/feedback_widget_rule.md > /tmp/sorted.txt",
])
def test_a_prefixed_read_or_sanctioned_writer_is_allowed(mem, cmd):
    code, err = hook(bash(cmd.format(m=mem, tools=TOOLS)), mem)
    assert code == 0, err


def test_a_prefixed_redirect_is_judged_by_its_real_command(mem):
    ok = f"LC_ALL=C printf '\\n## Note 2026-11-22\\n' >> {mem}/feedback_widget_rule.md"
    code, err = hook(bash(ok), mem)
    assert code == 0, err
    bad = f'LC_ALL=C echo "no frontmatter" > {mem}/feedback_widget_rule.md'
    code, err = hook(bash(bad), mem)
    assert code == 2, err


# cd into the memory folder, then a write by bare name (data audit item 1, 2026-11-22):
# 121 replay calls, all allowed before; 78 of the allowed hand-edit occurrences.
@pytest.mark.parametrize("cmd", [
    "cd {m} && python3 - <<'PY'\np='feedback_widget_rule.md'\ns=open(p).read()\n"
    "open(p,'w').write(s.replace('occurrences: 2','occurrences: 3'))\nPY",
    "M={m}; cd \"$M\" && PYTHONIOENCODING=utf-8 python3 -c \"open('feedback_widget_rule.md','w').write('x')\"",
    "cd {m} && sed -i '' 's/occurrences: 2/occurrences: 3/' feedback_widget_rule.md",
    "cd {m}/ && echo 'no frontmatter' > feedback_widget_rule.md",
    "cd {m} && cp /tmp/x.md feedback_widget_rule.md",
    "cd /tmp; cd {m}; printf 'x' | tee feedback_widget_rule.md",
    # `cd -` restores the earlier dir, so a relative cd after it resolves from there
    # (mutation: the `-` case was untested).
    "cd {p}; cd /tmp; cd - && cd memory && echo 'no frontmatter' > feedback_widget_rule.md",
    # a relative cd resolves against the dir the last cd left, not the call's cwd
    "cd {p} && cd memory && echo 'no frontmatter' > feedback_widget_rule.md",
    # replay U5oJe6: a sed -i bump and a prose append to the SAME bare name.
    # replay hJAMpo: a helper that writes every rule file passed to it by bare name.
    "cd {m} && python3 - <<'PY'\nimport pathlib\ndef bump(p):\n    t=pathlib.Path(p).read_text()\n"
    "    pathlib.Path(p).write_text(t.replace('occurrences: 2','occurrences: 3'))\n"
    "bump('feedback_widget_rule.md')\nPY",
    "cd {m}/ && sed -i '' 's/occurrences: 2/occurrences: 3/' feedback_widget_rule.md && "
    "cat >> feedback_widget_rule.md <<'EOF'\n\n## Fire 3\nEOF",
])
def test_a_write_after_cd_into_memory_is_judged(mem, cmd):
    code, err = hook(bash(cmd.format(m=mem, p=mem.parent)), mem, cwd="/tmp")
    assert code == 2, err


@pytest.mark.parametrize("cmd", [
    "cd {m} && echo 'more prose' >> feedback_widget_rule.md",
    "cd {m} && cat feedback_widget_rule.md",
    "cd {m} && python3 - <<'PY'\nprint(open('feedback_widget_rule.md').read())\nPY",
    "cd /tmp && echo 'no frontmatter' > feedback_widget_rule.md",
    # org 72's `cd -` case, with a file the hook does not guard: allowed
    "M={m}; cd $M && ls; cd - && echo x > notes-7b.txt",
    # in-place writers to files outside memory (mutation: `if scope(nt)` forced true lived)
    "sed -i '' 's/a/b/' /tmp/notes-7b.md",
    "echo x | tee /tmp/notes-7b.md",
    "cd /tmp && sed -i '' 's/a/b/' feedback_widget_rule.md",
])
def test_a_write_resolved_outside_memory_or_valid_is_allowed(mem, cmd):
    code, err = hook(bash(cmd.format(m=mem)), mem, cwd="/tmp")
    assert code == 0, err


# Codex combined F1 (P0): a cd that may not run in this shell (a subshell, an untaken
# branch, the right side of a failed &&) must not move where a later write lands.
@pytest.mark.parametrize("cmd", [
    "(cd /tmp && true); echo 'no frontmatter' > feedback_widget_rule.md",
    "if false; then cd /tmp; fi; echo 'no frontmatter' > feedback_widget_rule.md",
    "false && cd /tmp; echo 'no frontmatter' > feedback_widget_rule.md",
    "(cd /tmp && true); sed -i '' 's/occurrences: 2/occurrences: 3/' feedback_widget_rule.md",
    "(cd /tmp); python3 -c \"open('feedback_widget_rule.md','w').write('x')\"",
])
def test_a_cd_that_may_not_run_does_not_move_the_write(mem, cmd):
    code, err = hook(bash(cmd), mem, cwd=str(mem))
    assert code == 2, err


# Codex combined F2 (P1): Path(<name>) where the name is bound once to a literal is
# judged by that literal, as open(<name>, 'w') already was.
@pytest.mark.parametrize("script", [
    "from pathlib import Path\nprint(Path('{m}/feedback_widget_rule.md').read_text())\n"
    "out = '/tmp/result-7b.md'\nPath(out).write_text('ok')",
    "import pathlib\nprint(open('{m}/feedback_widget_rule.md').read())\n"
    "out = '/tmp/result-7b.md'\npathlib.Path(out).write_text('ok')",
])
def test_a_path_of_a_name_bound_to_a_literal_is_judged_by_the_literal(mem, script):
    cmd = "python3 - <<'PY'\n" + script.format(m=mem) + "\nPY"
    code, err = hook(bash(cmd), mem)
    assert code == 0, err


@pytest.mark.parametrize("cmd", [
    "M={m}; cd $M && ls; cd - && echo x > feedback_widget_rule.md",
    "cd {m} && cd /tmp && echo x > feedback_widget_rule.md",
])
def test_a_rule_name_written_after_leaving_memory_is_still_judged(mem, cmd):
    """KNOWN, MEASURED OVER-BLOCK. A relative write is judged against every dir the
    command named, because the walk does not model subshells or branches (Codex
    combined F1, P0). So a rule-file name written after leaving memory is judged as if
    in memory. Replay, 4,136 distinct Bash calls: the same 397 blocked with or without
    this, 0 calls differ. REOPEN on the first session blocked writing a non-memory file
    this way; the fix there models subshell and branch scope."""
    code, err = hook(bash(cmd.format(m=mem)), mem, cwd="/tmp")
    assert code == 2, err


def test_a_non_rule_name_after_an_unresolvable_cd_is_allowed(mem):
    """Scope B (measured: A and B differ on 0 replay calls). After a cd the hook cannot
    resolve, a relative cd stays unresolved, and only a rule-file or lessons.md NAME is
    blocked. Resolving the second cd from the call's cwd would invent a dir."""
    cmd = "cd \"$WT\" && cd memory && echo x > notes-7b.md"
    code, err = hook(bash(cmd), mem, cwd=str(mem.parent))
    assert code == 0, err


def test_a_relative_script_file_is_found_from_the_dir_cd_left(mem, tmp_path):
    """`cd d && python3 patch.py` reads d/patch.py, not cwd/patch.py."""
    d = tmp_path / "scripts"
    d.mkdir()
    (d / "patch.py").write_text(f"open('{mem}/feedback_widget_rule.md','w').write('x')\n",
                                encoding="utf-8")
    code, err = hook(bash(f"cd {d} && python3 patch.py"), mem, cwd="/tmp")
    assert code == 2 and "script that writes" in err


# Codex combined-r2 F1 (P1): a name reassigned by ANY later binding is not judged by
# its first literal (r2 had this for open(out, 'w') too).
@pytest.mark.parametrize("write", ["Path(out).write_text('x')", "open(out, 'w').write('x')"])
@pytest.mark.parametrize("rebind", [
    "out = str(Path('{m}/feedback_widget_rule.md'))",
    "for out in ['{m}/feedback_widget_rule.md']: pass",
    "with open('{m}/feedback_widget_rule.md') as out: pass\nout = out.name",
])
def test_a_name_rebound_after_its_literal_stays_opaque(mem, write, rebind):
    script = ("from pathlib import Path\nout = '/tmp/result-7b.md'\n"
              + rebind.format(m=mem) + "\n" + write)
    code, err = hook(bash("python3 - <<'PY'\n" + script + "\nPY"), mem)
    assert code == 2, err


def test_a_relative_cd_from_a_known_dir_keeps_a_valid_append_valid(mem):
    """`cd memory` from the memory folder's parent is a known dir, so a prose append is
    judged as an append, not blocked as a write after an unknown cd."""
    cmd = "cd memory && echo 'more prose' >> feedback_widget_rule.md"
    code, err = hook(bash(cmd), mem, cwd=str(mem.parent))
    assert code == 0, err


def test_an_in_place_bump_is_judged_when_the_same_file_is_also_appended(mem):
    """r2 skipped the sed -i because the append's redirect had marked the path handled."""
    f = mem / "feedback_widget_rule.md"
    cmd = (f"sed -i '' 's/occurrences: 2/occurrences: 3/' {f} && "
           f"cat >> {f} <<'EOF'\n\n## Fire 3\nEOF")
    code, err = hook(bash(cmd), mem)
    assert code == 2 and "in-place writer" in err


@pytest.mark.parametrize("cmd", [
    "cd \"$WT\" && python3 -c \"open('feedback_widget_rule.md','w').write('x')\"",
    "cd $(dirname {m}/x) && echo 'no frontmatter' > feedback_widget_rule.md",
    "cd \"$WT\" && cp /tmp/x.md feedback_widget_rule.md",
    "cd \"$WT\" && sed -i '' 's/occurrences: 2/occurrences: 3/' feedback_widget_rule.md",
    # a relative cd after an unknown one is still unknown (not the call's cwd)
    "cd \"$WT\" && cd memory && echo 'more prose' >> feedback_widget_rule.md",
    "cd {m} && pushd /tmp && popd && echo 'more prose' >> feedback_widget_rule.md",
])
def test_a_rule_name_written_after_an_unresolvable_cd_is_blocked(mem, cmd):
    code, err = hook(bash(cmd.format(m=mem)), mem, cwd=str(mem.parent))
    assert code == 2, err
    assert "cd" in err


def test_a_script_file_outside_tools_is_read(mem, tmp_path):
    s = tmp_path / "patch.py"
    s.write_text(f"open('{mem}/feedback_widget_rule.md','w').write('x')\n", encoding="utf-8")
    assert hook(bash(f"python3 {s}"), mem)[0] == 2


def test_a_repo_tool_is_not_judged(mem):
    cmd = f"python3 {TOOLS}/memory_write.py set {mem}/feedback_widget_rule.md promoted=no"
    assert hook(bash(cmd), mem)[0] == 0


@pytest.mark.parametrize("cmd", [
    "cat {m}/feedback_widget_rule.md",
    "sed -n 1,5p {m}/feedback_widget_rule.md",
    "grep -n promoted {m}/*.md",
    "cp {m}/feedback_widget_rule.md /tmp/copy.md",
    "rsync -a {m}/ /tmp/backup/",
    "ln -s {m}/feedback_widget_rule.md /tmp/link.md",
    "python3 - <<'PY'\nprint(open('{m}/feedback_widget_rule.md').read())\nPY",
    "python3 - <<'PY'\nt=open('{m}/feedback_widget_rule.md').read()\nopen('/tmp/out.txt','w').write(t)\nPY",
    "cat > {m}/MEMORY.md <<'EOF'\nindex\nEOF",
    "git commit -m 'sed -i on {m}/feedback_widget_rule.md'",
])
def test_reads_copies_out_and_mentions_are_allowed(mem, cmd):
    code, err = hook(bash(cmd.format(m=mem)), mem)
    assert code == 0, err


# ------------------------------------------------------------------ intake

def test_unreadable_payload_fails_open(mem):
    env = {**os.environ, "PYTHONPATH": str(TOOLS), "MEMORY_HOOK_DIR": str(mem),
           "MEMORY_HOOK_REPO": str(TOOLS.parent)}
    p = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True,
                       text=True, env=env, timeout=60)
    assert p.returncode == 0


def test_other_tools_are_not_judged(mem):
    assert hook({"tool_name": "Read", "tool_input": {"file_path": str(mem / "feedback_widget_rule.md")}},
                mem)[0] == 0


@pytest.mark.parametrize("script,blocked", [
    ("p='{m}/feedback_widget_rule.md'\nt=open(p).read()\nopen(p,'w').write(t)", True),
    ("from pathlib import Path\nPath('{m}/feedback_widget_rule.md').write_text('x')", True),
    ("import os\nos.replace('/tmp/x', '{m}/feedback_widget_rule.md')", True),
    ("t=open('{m}/feedback_widget_rule.md').read()\nf=open('/tmp/a.txt','w')\nf.write(t)", False),
    ("t=open('{m}/feedback_widget_rule.md').read()\nout='/tmp/a.txt'\nopen(out,'w').write(t)", False),
    ("out='{m}/feedback_widget_rule.md'\nopen(out,'w').write('x')", True),
    ("out='/tmp/a.txt'\nout='{m}/feedback_widget_rule.md'\nopen(out,'w').write('x')", True),
    ("t=open('{m}/feedback_widget_rule.md').read()\nout='/tmp/'\nout+='a.txt'\nopen(out,'w').write(t)", True),
    ("p='{m}/feedback_widget_rule.md'\nq=p\nopen(q,'w').write('x')", True),
    ("import sys\nt=open('{m}/feedback_widget_rule.md').read()\nprint(t, file=sys.stderr)", False),
    ("t=open('{m}/feedback_widget_rule.md').read()\nfh.write(t)", True),
    ("print(open('{m}/feedback_widget_rule.md').read())", False),
])
def test_script_write_targets(mem, script, blocked):
    cmd = "python3 - <<'PY'\n" + script.format(m=mem) + "\nPY"
    code, err = hook(bash(cmd), mem)
    assert code == (2 if blocked else 0), err


# ------------------------------------------------------------------ mutation r1 survivors

def _home_mem(tmp_path):
    home = tmp_path / "home"
    mem = home / ".claude" / "projects" / "x" / "memory"
    mem.mkdir(parents=True)
    (mem / "feedback_widget_rule.md").write_text(RULE, encoding="utf-8")
    return home, mem


def _hook_home(payload, home, mem):
    payload.setdefault("cwd", str(home))
    env = {**os.environ, "PYTHONPATH": str(TOOLS), "MEMORY_HOOK_DIR": str(mem),
           "MEMORY_HOOK_REPO": str(TOOLS.parent), "HOME": str(home), "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stderr


def test_a_tilde_path_is_expanded(tmp_path):
    home, mem = _home_mem(tmp_path)
    code, err = _hook_home(write("~/.claude/projects/x/memory/feedback_widget_rule.md",
                                 "no frontmatter\n"), home, mem)
    assert code == 2, err


def test_a_dollar_home_target_is_expanded(tmp_path):
    home, mem = _home_mem(tmp_path)
    cmd = "cat > $HOME/.claude/projects/x/memory/feedback_widget_rule.md <<'EOF'\nno frontmatter\nEOF"
    code, err = _hook_home(bash(cmd), home, mem)
    assert code == 2, err
    assert "no_frontmatter" in err             # judged as a create, not as an unknown writer
    assert "in-place writer" not in err


def test_a_tilde_path_in_a_script_is_seen(tmp_path):
    home, mem = _home_mem(tmp_path)
    cmd = ("python3 - <<'PY'\nimport os\np=os.path.expanduser("
           "'~/.claude/projects/x/memory/feedback_widget_rule.md')\nopen(p,'w').write('x')\nPY")
    code, err = _hook_home(bash(cmd), home, mem)
    assert code == 2, err


def test_a_script_writing_only_an_index_file_is_allowed(mem):
    cmd = f"python3 - <<'PY'\nopen('{mem}/MEMORY.md','w').write('# index')\nPY"
    code, err = hook(bash(cmd), mem)
    assert code == 0, err


def test_a_here_string_script_is_judged(mem):
    cmd = f"python3 <<< \"open('{mem}/feedback_widget_rule.md','w').write('x')\""
    code, err = hook(bash(cmd), mem)
    assert code == 2, err


def test_a_note_that_quotes_a_write_call_is_not_a_script(mem):
    cmd = (f"cat > /tmp/b2-note.txt <<'EOF'\nthe bad shape was open('{mem}/feedback_widget_rule.md','w')"
           ".write(x)\nEOF")
    code, err = hook(bash(cmd), mem)
    assert code == 0, err


@pytest.mark.parametrize("cmd", ["wc -l < {m}/feedback_widget_rule.md",
                                 "python3 tools/x.py < {m}/feedback_widget_rule.md"])
def test_an_input_redirect_is_a_read(mem, cmd):
    code, err = hook(bash(cmd.format(m=mem)), mem)
    assert code == 0, err


@pytest.mark.parametrize("old", ["not in the file", ""])
def test_an_edit_the_tool_will_refuse_is_not_judged_even_on_a_broken_file(mem, old):
    (mem / "feedback_widget_rule.md").write_text("no frontmatter here\n", encoding="utf-8")
    code, err = hook(edit(mem / "feedback_widget_rule.md", old, "x"), mem)
    assert code == 0, err


def test_a_truncated_payload_naming_a_rule_file_is_blocked(mem):
    big = json.dumps(write(mem / "feedback_widget_rule.md", "x" * (1024 * 1024 + 10)))
    env = {**os.environ, "PYTHONPATH": str(TOOLS), "MEMORY_HOOK_DIR": str(mem),
           "MEMORY_HOOK_REPO": str(TOOLS.parent)}
    p = subprocess.run([sys.executable, str(HOOK)], input=big, capture_output=True, text=True,
                       env=env, timeout=60)
    assert p.returncode == 2
    assert "cut off at the intake cap" in p.stderr


def test_a_truncated_payload_elsewhere_fails_open(mem, tmp_path):
    big = json.dumps(write(tmp_path / "notes.md", "x" * (1024 * 1024 + 10)))
    env = {**os.environ, "PYTHONPATH": str(TOOLS), "MEMORY_HOOK_DIR": str(mem),
           "MEMORY_HOOK_REPO": str(TOOLS.parent)}
    p = subprocess.run([sys.executable, str(HOOK)], input=big, capture_output=True, text=True,
                       env=env, timeout=60)
    assert p.returncode == 0
    assert p.stderr == ""


def test_a_script_given_the_memory_folder_as_an_argument_is_judged(mem):
    """Replay 2026-10-14: python3 - "$M" <<'PY' ... open(os.path.join(M, fn), 'w')."""
    cmd = (f"M={mem}\npython3 - \"$M\" <<'PY'\nimport sys,os\nM=sys.argv[1]\n"
           "p=os.path.join(M,'feedback_widget_rule.md')\nt=open(p).read()\nopen(p,'w').write(t)\nPY")
    code, err = hook(bash(cmd), mem)
    assert code == 2 and "script that writes" in err


def test_a_script_given_another_folder_is_not_judged(mem, tmp_path):
    cmd = (f"python3 - {tmp_path} <<'PY'\nimport sys,os\np=os.path.join(sys.argv[1],'x.md')\n"
           "open(p,'w').write('x')\nPY")
    code, err = hook(bash(cmd), mem)
    assert code == 0, err


# ------------------------------------------------------------------ Codex r1 F3: printf

def test_printf_create_of_valid_frontmatter_is_allowed(mem):
    body = RULE.replace("widget", "new").replace("'", "")
    cmd = f"printf '%s\\n' '{body.rstrip()}' > {mem}/feedback_new_rule.md"
    code, err = hook(bash(cmd), mem)
    assert code == 0, err


def test_printf_create_of_invalid_frontmatter_is_blocked(mem):
    cmd = f"printf '%s\\n' 'no frontmatter' > {mem}/feedback_new_rule.md"
    code, err = hook(bash(cmd), mem)
    assert code == 2 and "no_frontmatter" in err


@pytest.mark.parametrize("fmt", ["%d\\n", "%-10s\\n", "%b", "\\x41"])
def test_printf_with_a_format_the_hook_cannot_evaluate_is_opaque(mem, fmt):
    cmd = f"printf '{fmt}' 1 > {mem}/feedback_new_rule.md"
    code, err = hook(bash(cmd), mem)
    assert code == 2 and "a redirect from `printf`" in err


def test_printf_append_with_percent_s_is_judged_on_its_output(mem):
    ok = f"printf '\\n%s\\n' '## Note 2026-11-22' >> {mem}/feedback_widget_rule.md"
    assert hook(bash(ok), mem)[0] == 0
    bad = f"printf '%s: %s\\n' promoted hook >> {mem}/feedback_widget_rule.md"
    code, err = hook(bash(bad), mem)
    assert code == 2 and "set a state key" in err


# ------------------------------------------------------------------ printf_text, directly

def _printf():
    sys.path.insert(0, str(TOOLS))
    sys.path.insert(0, str(HOOK.parent))
    import importlib
    return importlib.import_module(HOOK.stem).printf_text


@pytest.mark.parametrize("words,expected", [
    (["%s\\n", "a", "b"], "a\nb\n"),            # format reused while arguments remain
    (["%s:%s\\n", "k", "v", "k2"], "k:v\nk2:\n"),  # a short last round pads with ""
    (["100%%\\n"], "100%\n"),
    (["a\\tb\\\\c\\n"], "a\tb\\c\n"),
    (["plain"], "plain"),
    (["plain\\n", "extra"], "plain\n"),          # no %s: printed once, extra args ignored
    (["%s"], ""),
])
def test_printf_text_matches_the_shell(words, expected):
    assert _printf()(words) == expected


@pytest.mark.parametrize("words", [[], ["-v", "x"], ["%d", "1"], ["\\x41"], ["%"]])
def test_printf_text_refuses_what_it_cannot_evaluate(words):
    assert _printf()(words) is None


# ------------------------------------------------------------------ Codex confirmation F1

@pytest.mark.parametrize("cmd", [
    "V='  promoted: someday'; printf '%s\\n' \"$V\" >> {m}/feedback_widget_rule.md",
    "V='  promoted: someday'; cat >> {m}/feedback_widget_rule.md <<EOF\n$V\nEOF",
    "V='  promoted: someday'; echo \"$V\" >> {m}/feedback_widget_rule.md",
])
def test_an_append_is_judged_after_variable_expansion(mem, cmd):
    code, err = hook(bash(cmd.format(m=mem)), mem)
    assert code == 2 and "set a state key" in err, err


@pytest.mark.parametrize("cmd", [
    "echo \"$UNKNOWN\" >> {m}/feedback_widget_rule.md",
    "echo \"$(date)\" >> {m}/feedback_widget_rule.md",
    "cat >> {m}/feedback_widget_rule.md <<EOF\n`whoami`\nEOF",
])
def test_an_append_whose_content_cannot_be_resolved_is_blocked(mem, cmd):
    code, err = hook(bash(cmd.format(m=mem)), mem)
    assert code == 2 and "a redirect from" in err, err


@pytest.mark.parametrize("cmd", [
    "cat >> {m}/feedback_widget_rule.md <<'EOF'\nRun with $M and `cmd` set.\nEOF",
    "printf '%s\\n' 'literal $M stays' >> {m}/feedback_widget_rule.md",
    "V='## Note 2026-11-22'; printf '%s\\n' \"$V\" >> {m}/feedback_widget_rule.md",
    "printf '\\n## Note (\"they'\"'\"'re fine\")\\n\\n`Home` and $M stay literal\\n' >> {m}/feedback_widget_rule.md",
])
def test_literal_or_resolvable_content_is_allowed(mem, cmd):
    code, err = hook(bash(cmd.format(m=mem)), mem)
    assert code == 0, err


# ------------------------------------------------------------------ Grok confirmation F1, F2

@pytest.mark.parametrize("cmd", [
    "V='  promoted: someday'; printf '%s\\n' '$V' \"$V\" >> {m}/feedback_widget_rule.md",
    "V='  promoted: someday'; echo '$V' >> {m}/feedback_gadget_rule.md; printf '%s\\n' \"$V\" >> {m}/feedback_widget_rule.md",
])
def test_a_literal_twin_does_not_exempt_an_expandable_word(mem, cmd):
    code, err = hook(bash(cmd.format(m=mem)), mem)
    assert code == 2 and "set a state key" in err, err


@pytest.mark.parametrize("value", ["two", "-1", "true", "1.5"])
def test_a_create_must_set_a_whole_number_count(mem, value):
    body = RULE.replace("widget", "new").replace("occurrences: 2", f"occurrences: {value}")
    code, err = hook(write(mem / "feedback_new_rule.md", body), mem)
    assert code == 2 and "is not a non-negative integer" in err, err   # learning_state's message


def test_a_create_with_count_zero_is_allowed(mem):
    body = RULE.replace("widget", "new").replace("occurrences: 2", "occurrences: 0")
    assert hook(write(mem / "feedback_new_rule.md", body), mem)[0] == 0
