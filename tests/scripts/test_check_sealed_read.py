"""Tests for check_sealed_read.py — fable-audit 2026-07-07 #12.

The hook BLOCKS Read/Grep/Glob/Bash that target data/project-background/ (the
sealed vault), with a SEAL_READ_OK=1 override for intentional maintenance. Repo
root is injected via SEAL_REPO_ROOT so the tests never depend on real sealed files.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_sealed_read.py"
SEALED = "data/project-background"


def _run(root, tool, tool_input, env_extra=None):
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    env = {**os.environ, "SEAL_REPO_ROOT": str(root)}
    env.pop("SEAL_READ_OK", None)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True, env=env)
    return r.returncode, r.stderr


# --- should BLOCK (exit 2) --------------------------------------------------

def test_blocks_read_of_sealed_file(tmp_path):
    code, err = _run(tmp_path, "Read",
                     {"file_path": str(tmp_path / SEALED / "zuora-corpus/notes.md")})
    assert code == 2
    assert SEALED in err


def test_blocks_grep_into_sealed_dir(tmp_path):
    code, _ = _run(tmp_path, "Grep", {"pattern": "x", "path": str(tmp_path / SEALED)})
    assert code == 2


def test_blocks_glob_into_sealed_dir(tmp_path):
    code, _ = _run(tmp_path, "Glob", {"pattern": "*.md", "path": str(tmp_path / SEALED)})
    assert code == 2


def test_blocks_bash_referencing_sealed(tmp_path):
    code, _ = _run(tmp_path, "Bash", {"command": f"cat {SEALED}/mckinsey/x.md"})
    assert code == 2


# --- should ALLOW (exit 0) --------------------------------------------------

def test_allows_read_of_nonsealed_file(tmp_path):
    code, _ = _run(tmp_path, "Read", {"file_path": str(tmp_path / "data" / "profile.md")})
    assert code == 0


def test_allows_bash_not_referencing_sealed(tmp_path):
    code, _ = _run(tmp_path, "Bash", {"command": "ls data/people/"})
    assert code == 0


def test_env_override_allows_sealed_read(tmp_path):
    code, _ = _run(tmp_path, "Read",
                   {"file_path": str(tmp_path / SEALED / "notes.md")},
                   env_extra={"SEAL_READ_OK": "1"})
    assert code == 0


def test_bash_inline_override_allows_sealed(tmp_path):
    code, _ = _run(tmp_path, "Bash",
                   {"command": f"SEAL_READ_OK=1 cat {SEALED}/x.md"})
    assert code == 0


def test_ignores_non_target_tool(tmp_path):
    code, _ = _run(tmp_path, "Write",
                   {"file_path": str(tmp_path / SEALED / "x.md"), "content": "hi"})
    assert code == 0


# --- read bypasses closed 2026-10-12 ----------------------------------------
# Each of these read the sealed directory and passed the guard when its Bash test
# was `SEALED_REL in command` and its override test was a substring match.

import pytest  # noqa: E402


@pytest.mark.parametrize("spelling", [
    "data//project-background/x.md",
    "data/./project-background/x.md",
    'data/project-"background"/x.md',
    "data/project\\-background/x.md",
    "Data/Project-Background/x.md",
    "'data'/project-background/x.md",
])
def test_blocks_bash_read_through_another_spelling_of_the_path(tmp_path, spelling):
    code, _ = _run(tmp_path, "Bash", {"command": f"cat {spelling}"})
    assert code == 2


def test_blocks_read_through_a_case_variant_path(tmp_path):
    code, _ = _run(tmp_path, "Read",
                   {"file_path": str(tmp_path / "Data" / "Project-Background" / "x.md")})
    assert code == 2


def test_blocks_grep_whose_glob_points_into_the_sealed_dir(tmp_path):
    code, _ = _run(tmp_path, "Grep", {"pattern": "x", "glob": f"{SEALED}/**/*.md"})
    assert code == 2


def test_blocks_grep_whose_path_plus_glob_reach_the_sealed_dir(tmp_path):
    code, _ = _run(tmp_path, "Grep",
                   {"pattern": "x", "path": "data", "glob": "project-background/**"})
    assert code == 2


def test_blocks_glob_whose_pattern_points_into_the_sealed_dir(tmp_path):
    code, _ = _run(tmp_path, "Glob", {"pattern": f"{SEALED}/**/*.md"})
    assert code == 2


def test_allows_grep_that_searches_for_the_path_string_elsewhere(tmp_path):
    """Grep's `pattern` is what is searched for, not where."""
    code, _ = _run(tmp_path, "Grep", {"pattern": SEALED, "path": str(tmp_path / "tools")})
    assert code == 0


@pytest.mark.parametrize("command", [
    f"cat {SEALED}/x.md  # SEAL_READ_OK=1",
    f'echo "SEAL_READ_OK=1"; cat {SEALED}/x.md',
    f"cat {SEALED}/x.md <<'EOF'\nSEAL_READ_OK=1\nEOF",
    f"NOT_SEAL_READ_OK=1 cat {SEALED}/x.md",
    f"cat {SEALED}/x.md SEAL_READ_OK=1",
])
def test_the_override_token_only_counts_as_a_leading_assignment(tmp_path, command):
    code, _ = _run(tmp_path, "Bash", {"command": command})
    assert code == 2


@pytest.mark.parametrize("command", [
    f"SEAL_READ_OK=1 cat {SEALED}/x.md",
    f"PYTHONIOENCODING=utf-8 SEAL_READ_OK=1 python3 tools/x.py {SEALED}/x.md",
    f"export SEAL_READ_OK=1; cat {SEALED}/x.md",
    f"cd /tmp && SEAL_READ_OK=1 cat {SEALED}/x.md",
])
def test_a_real_inline_override_still_works(tmp_path, command):
    code, _ = _run(tmp_path, "Bash", {"command": command})
    assert code == 0


def test_bad_json_fails_open():
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input="not json", capture_output=True, text=True)
    assert r.returncode == 0


# ---------------------------------------------------------------------------
# A guard must match a READ, not a MENTION.
#
# The Bash matcher used to be `SEALED in command`, so it fired on prose that only
# NAMED the directory: a heredoc body, a commit message, a comment, and a `find`
# that named it in order to EXCLUDE it. Four fires. The cases below pin both
# directions: every mention must pass, and every read must still block.
# ---------------------------------------------------------------------------


def _bash(root, command, env_extra=None):
    return _run(root, "Bash", {"command": command}, env_extra)[0]


MENTIONS = {
    "heredoc_quoted_prose":
        f"cat >> notes/risk-memo.md <<'EOF'\nA second model can reach {SEALED}/ unless it is fenced.\nEOF",
    "heredoc_unquoted_prose":
        f"cat >> notes/risk-memo.md <<EOF\nThe sealed tree is {SEALED}/ and nothing here opens it.\nEOF",
    "heredoc_tee":
        f"tee -a notes/log.md <<'EOF'\nblocked while describing {SEALED}\nEOF",
    "heredoc_to_script_stdin_as_data":
        f"PYTHONIOENCODING=utf-8 python3 tools/example_write.py add --stdin <<'EOF'\nnote about {SEALED}/ being sealed\nEOF",
    "commit_message_double_quoted":
        f'git commit -m "fix(guard): stop firing on a mention of {SEALED}"',
    "commit_message_single_quoted":
        f"git commit -m 'fix(guard): stop firing on a mention of `{SEALED}/`'",
    "commit_message_leading_with_path":
        f'git commit -m "{SEALED}: guard matches reads only"',
    "commit_message_heredoc":
        f"git commit -F - <<'EOF'\nfix(guard): operand matching\n\n{SEALED}/ is named here, not read.\nEOF",
    "trailing_comment":
        f"ls tools/  # this never touches {SEALED}/",
    "comment_line":
        f"# reminder: {SEALED}/ is sealed\nls tools/",
    "echo_prose":
        f'echo "the sealed tree is {SEALED} and stays closed" >> notes/log.md',
    "script_prose_argument":
        f'PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append check_sealed_read.py "FP: blocked a mention of {SEALED}"',
    # 3rd fire: the directory is named only to keep the search OUT of it.
    "find_not_path_exclusion":
        f"find . -name '*.md' -not -path './{SEALED}/*' -print | xargs grep -l term",
    "find_bang_path_exclusion":
        f"find . -type f ! -path './{SEALED}/*' -name '*.md'",
    "find_prune_exclusion":
        f"find . -path ./{SEALED} -prune -o -name '*.md' -print",
    "find_prune_group_exclusion":
        f"find . \\( -path ./.git -o -path ./{SEALED} \\) -prune -o -type f -print",
    "grep_exclude_dir_equals":
        f"grep -rl --exclude-dir={SEALED} term notes/",
    "grep_exclude_dir_separate":
        f"grep -rl --exclude-dir '{SEALED}' term notes/",
    "rsync_exclude":
        f"rsync -a --exclude='{SEALED}/' notes/ /tmp/example-copy/",
    "rg_negated_glob":
        f"rg -l -g '!{SEALED}/**' term notes/",
}


@pytest.mark.parametrize("name", sorted(MENTIONS))
def test_bash_mention_is_allowed(tmp_path, name):
    assert _bash(tmp_path, MENTIONS[name]) == 0, MENTIONS[name]


READS = {
    # every reading command the rule names, bare operand
    "cat": f"cat {SEALED}/a.md",
    "head": f"head -n 5 {SEALED}/a.md",
    "tail": f"tail -n 5 {SEALED}/a.md",
    "less": f"less {SEALED}/a.md",
    "grep": f"grep -rn term {SEALED}/",
    "find": f"find {SEALED} -name '*.md'",
    "cp": f"cp {SEALED}/a.md /tmp/example-copy.md",
    "rsync": f"rsync -a {SEALED}/ /tmp/example-copy/",
    # the same commands with the operand QUOTED: stripping quotes must not hide it
    "cat_double_quoted": f'cat "{SEALED}/a.md"',
    "cat_single_quoted": f"cat '{SEALED}/a.md'",
    "head_double_quoted": f'head -n 5 "{SEALED}/a.md"',
    "tail_single_quoted": f"tail -n 5 '{SEALED}/a.md'",
    "less_double_quoted": f'less "{SEALED}/a.md"',
    "grep_double_quoted": f'grep -rn term "{SEALED}"',
    "find_single_quoted": f"find '{SEALED}' -type f",
    "cp_double_quoted": f'cp "{SEALED}/a.md" /tmp/example-copy.md',
    "rsync_single_quoted": f"rsync -a '{SEALED}/' /tmp/example-copy/",
    "quoted_name_with_space": f'cat "{SEALED}/a note.md"',
    "quoted_dir_with_space_before": f'cat "/tmp/my dir/{SEALED}/a.md"',
    "unknown_tool_quoted_operand": f'sometool "{SEALED}/a.md"',
    "unknown_tool_bare_operand": f"sometool --in {SEALED}/a.md",
    # redirects FROM the path
    "redirect_from": f"wc -l < {SEALED}/a.md",
    "redirect_from_no_space": f"wc -l <{SEALED}/a.md",
    "redirect_from_quoted": f'wc -l < "{SEALED}/a.md"',
    "redirect_from_loop": f'while read -r line; do echo "$line"; done < {SEALED}/a.md',
    "herestring_to_shell": f'bash <<< "cat {SEALED}/a.md"',
    # globs under it
    "glob": f"cat {SEALED}/*.md",
    "glob_recursive": f"ls {SEALED}/**/*.md",
    "glob_quoted_prefix": f'for f in "{SEALED}"/*; do head -1 "$f"; done',
    # reached through a variable or a cd
    "variable_bare": f'D={SEALED}; cat "$D/a.md"',
    "variable_quoted": f'D="{SEALED}"; cat "$D/a.md"',
    "variable_export": f'export D="{SEALED}" && ls "$D"',
    "cd_then_read": f"cd {SEALED} && cat a.md",
    "cd_quoted_then_read": f'cd "{SEALED}" && cat a.md',
    "pushd_then_read": f"pushd {SEALED}; ls; popd",
    "git_dash_c": f"git -C {SEALED} status",
    # code that opens it
    "python_c_open": f"python3 -c \"print(open('{SEALED}/a.md').read())\"",
    "python_c_open_after_statement": f"python3 -c \"import sys; print(open('{SEALED}/a.md').read())\"",
    "python_c_single_quoted": f"PYTHONIOENCODING=utf-8 python3 -c 'import pathlib; print(pathlib.Path(\"{SEALED}/a.md\").read_text())'",
    "python_heredoc": f"python3 - <<'EOF'\nprint(open('{SEALED}/a.md').read())\nEOF",
    "python_heredoc_no_dash": f"PYTHONIOENCODING=utf-8 python3 <<'EOF'\nimport os\nprint(os.listdir('{SEALED}'))\nEOF",
    "heredoc_piped_to_interpreter": f"cat <<'EOF' | python3\nprint(open('{SEALED}/a.md').read())\nEOF",
    "bash_heredoc": f"bash <<'EOF'\ncat {SEALED}/a.md\nEOF",
    "bash_c": f'bash -c "cat {SEALED}/a.md"',
    "zsh_lc": f"zsh -lc 'echo start; cat {SEALED}/a.md'",
    "eval_string": f'eval "echo start; cat {SEALED}/a.md"',
    "node_e": f"node -e \"const fs = require('fs'); console.log(fs.readFileSync('{SEALED}/a.md', 'utf8'))\"",
    "awk_program": f"awk 'BEGIN {{ while ((getline l < \"{SEALED}/a.md\") > 0) print l }}'",
    "sed_file": f"sed -n '1,5p' {SEALED}/a.md",
    "find_exec_sh_c": f"find . -maxdepth 0 -exec sh -c 'echo start; cat {SEALED}/a.md' \\;",
    # substitution inside something that is otherwise prose
    "subst_in_commit_message": f'git commit -m "note: $(cat {SEALED}/a.md)"',
    "backtick_in_commit_message": f'git commit -m "note: `cat {SEALED}/a.md`"',
    "subst_bare": f"echo $(head -1 {SEALED}/a.md)",
    "process_substitution": f"diff <(cat {SEALED}/a.md) notes/b.md",
    "heredoc_unquoted_with_subst": f"cat >> notes/log.md <<EOF\ncontents: $(cat {SEALED}/a.md)\nEOF",
    # path arrives as DATA for a command that turns stdin into reads
    "echo_piped_to_xargs": f'echo "start {SEALED}/a.md" | xargs cat',
    "echo_piped_to_shell": f'echo "cat {SEALED}/a.md" | sh',
    "heredoc_to_xargs": f"xargs cat <<'EOF'\n{SEALED}/a.md\nEOF",
    "heredoc_to_read_loop": f"while read -r f; do cat \"$f\"; done <<'EOF'\n{SEALED}/a.md\nEOF",
    # spelling variants of the same path
    "absolute": f"cat /tmp/example-repo/{SEALED}/a.md",
    "dot_slash": f"cat ./{SEALED}/a.md",
    "double_slash": "cat data//project-background/a.md",
    "dot_segment": "cat data/./project-background/a.md",
    "quote_spliced": 'cat data/project-"background"/a.md',
    "backslash_spliced": "cat data/project\\-background/a.md",
    "case_variant": "cat Data/Project-Background/a.md",
    "attached_flag_value": f"tar -czf /tmp/example.tgz -C{SEALED} .",
    "flag_equals_value": f"sometool --input={SEALED}/a.md",
    # position in a compound command
    "after_semicolon": f"ls tools/; cat {SEALED}/a.md",
    "after_and": f"true && cat {SEALED}/a.md",
    "after_pipe": f"true | cat {SEALED}/a.md",
    "second_line": f"ls tools/\ncat {SEALED}/a.md",
    "in_subshell": f"(cat {SEALED}/a.md)",
    "line_continuation": f"cat \\\n  {SEALED}/a.md",
    "git_show_blob": f"git show HEAD:{SEALED}/a.md",
    "git_show_blob_quoted": f'git show "HEAD:{SEALED}/a.md"',
    # a mention and a read in ONE command: the mention must not launder the read
    "mention_heredoc_then_read": f"cat >> notes/log.md <<'EOF'\nabout {SEALED}\nEOF\ncat {SEALED}/a.md",
    "comment_then_read": f"# only naming {SEALED}\ncat {SEALED}/a.md",
    "read_with_trailing_comment": f"cat {SEALED}/a.md  # just a look",
    "exclusion_of_subdir_inside_real_find": f"find {SEALED} -not -path '{SEALED}/skip/*' -type f",
    "path_include_filter_is_not_exclusion": f"find . -path './{SEALED}/*' -type f",
}


@pytest.mark.parametrize("name", sorted(READS))
def test_bash_read_is_still_blocked(tmp_path, name):
    assert _bash(tmp_path, READS[name]) == 2, READS[name]


# --- the override must sit in COMMAND position, not merely appear ------------

@pytest.mark.parametrize("command", [
    f"SEAL_READ_OK=1 cat {SEALED}/a.md",
    f"PYTHONIOENCODING=utf-8 SEAL_READ_OK=1 python3 -c \"open('{SEALED}/a.md')\"",
    f"export SEAL_READ_OK=1 && cat {SEALED}/a.md",
    f"ls tools/; SEAL_READ_OK=1 cat {SEALED}/a.md",
])
def test_bash_override_in_command_position_allows(tmp_path, command):
    assert _bash(tmp_path, command) == 0, command


@pytest.mark.parametrize("command", [
    f"cat {SEALED}/a.md  # SEAL_READ_OK=1 is the override",
    f'cat {SEALED}/a.md; echo "set SEAL_READ_OK=1 to override"',
    f"cat {SEALED}/a.md <<'EOF'\nSEAL_READ_OK=1\nEOF",
    f"cat {SEALED}/a.md NOSEAL_READ_OK=1",
])
def test_bash_override_named_in_prose_does_not_disarm(tmp_path, command):
    assert _bash(tmp_path, command) == 2, command


# --- Read / Grep / Glob payload shapes --------------------------------------

@pytest.mark.parametrize("tool,tool_input", [
    ("Read", {"file_path": f"{SEALED}/a.md"}),                       # relative
    ("Read", {"file_path": "Data/Project-Background/a.md"}),         # case-insensitive volume
    ("Grep", {"pattern": "term", "path": f"{SEALED}/sub"}),
    ("Grep", {"pattern": "term", "glob": f"{SEALED}/**/*.md"}),      # no path, glob reaches in
    ("Grep", {"pattern": "term", "path": ".", "glob": f"**/{SEALED}/*.md"}),
    ("Glob", {"pattern": f"{SEALED}/**/*.md"}),                      # no path, pattern reaches in
    ("Glob", {"pattern": "*.md", "path": f"{SEALED}/sub"}),
])
def test_tool_payload_reaching_sealed_is_blocked(tmp_path, tool, tool_input):
    assert _run(tmp_path, tool, tool_input)[0] == 2, tool_input


@pytest.mark.parametrize("tool,tool_input", [
    # searching FOR the path string, somewhere else, is a mention
    ("Grep", {"pattern": SEALED, "path": "tools"}),
    ("Grep", {"pattern": SEALED, "glob": "tools/*.py"}),
    ("Glob", {"pattern": "tools/*.py"}),
    ("Read", {"file_path": "data/project-background-notes.md"}),     # sibling name, not under it
])
def test_tool_payload_naming_sealed_is_allowed(tmp_path, tool, tool_input):
    assert _run(tmp_path, tool, tool_input)[0] == 0, tool_input



# --- positions added with the operand matcher, 2026-10-12 --------------------

@pytest.mark.parametrize("command", [
    "cd data && cat project-background/a.md",            # the parent is not spelled
    "ls project-background",
    f"grep -e term {SEALED}/a.md",                       # with -e the first word is a path
    f"grep -eterm {SEALED}/a.md",
    f"grep -T term {SEALED}/a.md",                       # -T takes no value
    f"grep -rn {SEALED} {SEALED}",                       # pattern AND target
    f'echo "{SEALED}/"*',                                # a glob lists the file names
    f'python3 tools/example_write.py --input="{SEALED}/a note.md"',
    f'python3 tools/example_write.py "/tmp/my dir/{SEALED}/a.md"',
    f'python3 tools/example_write.py "{SEALED}/ is where it lives"',
    f'python3 -c "print(1)" "notes on {SEALED}"',
    f'echo "see {SEALED}/a.md" | while read -r f; do cat "$f"; done',
    f'grep -o "{SEALED}[^ ]*" tools/example.py | xargs cat',
    f'git commit -m "msg" {SEALED}/a.md',
    f'git tag -m "{SEALED}" -F {SEALED}/a.md v1',
])
def test_bash_read_in_a_position_next_to_an_allowed_one_is_blocked(tmp_path, command):
    assert _bash(tmp_path, command) == 2, command


@pytest.mark.parametrize("command", [
    f'grep -rn "{SEALED}" tools/',                       # the text searched FOR
    f"/usr/bin/grep -rn -e '{SEALED}' tools/ tests/",
    f'rg -n "{SEALED}" tools/',
    f'git commit -am "seal: {SEALED} stays closed"',
    f"git commit -m \"$(cat <<'EOF'\nseal: {SEALED} stays closed\nEOF\n)\"",
    "cat data/project-background-notes.md",              # a sibling name
    f'echo "the sealed tree is {SEALED}" | tee -a notes/log.md',
    f'PYTHONIOENCODING=utf-8 python3 tools/example_write.py add --why "reads of {SEALED}/ are blocked"',
    f'if true; then echo "about {SEALED} here"; fi',
])
def test_bash_mention_in_an_added_position_is_allowed(tmp_path, command):
    assert _bash(tmp_path, command) == 0, command


# --- cross-model review of the operand matcher, 2026-10-12 -------------------

@pytest.mark.parametrize("command", [
    # the override covers the command it prefixes, not the call
    f"cat {SEALED}/a.md; export SEAL_READ_OK=1",
    f"false && SEAL_READ_OK=1 true; cat {SEALED}/a.md",
    f"SEAL_READ_OK=1 ls tools/; cat {SEALED}/a.md",
    # a search FOR the name that also searches IN the directory
    f"grep -r '{SEALED}' .",
    f"grep -rn '{SEALED}' data",
    f"grep -rn '{SEALED}' tools/ data/",
    f"grep -r '{SEALED}'",
    f"rg '{SEALED}'",
    f"rg '{SEALED}' .",
    f"grep -n '{SEALED}' data/*.md",
    # an exclusion that covers only part of the directory
    f"find . -name '*.md' -not -path './{SEALED}/skip/*'",
    f"find . -path './{SEALED}/skip' -prune -o -name '*.md' -print",
])
def test_bash_review_finding_is_blocked(tmp_path, command):
    assert _bash(tmp_path, command) == 2, command


@pytest.mark.parametrize("command", [
    f"SEAL_READ_OK=1 cat {SEALED}/a.md | head -3",
    f"SEAL_READ_OK=1 cat {SEALED}/a.md | python3 -c 'import sys; print(len(sys.stdin.read()))'",
    f"export SEAL_READ_OK=1\ncat {SEALED}/a.md\nls {SEALED}",
    f"cd /tmp && export SEAL_READ_OK=1 && cat {SEALED}/a.md",
    f"D={SEALED}; cd $D; export SEAL_READ_OK=1; sed -n 1,3p a.md",
    f"grep -n '{SEALED}' docs/us*.md",                   # a glob that cannot reach it
    f"cat tools/example.py | rg '{SEALED}'",              # rg reads the pipe, not the tree
    f"grep -n '{SEALED}' CLAUDE.md docs/usage.md",
])
def test_bash_review_counterpart_is_allowed(tmp_path, command):
    assert _bash(tmp_path, command) == 0, command


# --- the override and a command that only carries the name forward -----------
# Measured on 2026-10-12: 131 past maintenance commands put the name in a `cd` or a
# variable and the override on the reads that followed.

@pytest.mark.parametrize("command", [
    f"cd {SEALED}/sub && SEAL_READ_OK=1 cat a.md",
    f"C={SEALED}/sub\nSEAL_READ_OK=1 sed -n 1,5p $C/a.md | cut -c1-80",
    f'D={SEALED}; SEAL_READ_OK=1 ls "$D" | wc -l',
    f'export D={SEALED}; echo "$(SEAL_READ_OK=1 head -1 "$D/a.md")"',
])
def test_a_carrier_passes_when_the_call_carries_an_override(tmp_path, command):
    assert _bash(tmp_path, command) == 0, command


@pytest.mark.parametrize("command", [
    f"cd {SEALED} && cat a.md; echo SEAL_READ_OK=1",      # the token is only printed
    f"D={SEALED}; cat $D/a.md  # SEAL_READ_OK=1",
    f"cd {SEALED} && SEAL_READ_OK=1 cat a.md; cat {SEALED}/b.md",   # a spelled read needs its own
    f"D=$(cat {SEALED}/a.md); SEAL_READ_OK=1 true",       # the carrier's own substitution reads
])
def test_a_carrier_does_not_cover_a_spelled_read_or_a_printed_token(tmp_path, command):
    assert _bash(tmp_path, command) == 2, command


# ---------------------------------------------------------------------------
# Mutation survivors, 2026-10-12. Each case below was green with a decision in the
# parser forced the wrong way. The unit cases import the hook; the rest run it.
# ---------------------------------------------------------------------------

sys.path.insert(0, str(SCRIPT.parent))
import check_sealed_read as csr  # noqa: E402
from shell_tokens import simple_commands  # noqa: E402


@pytest.mark.parametrize("command", [
    f'tools/example.py add "a note about the {SEALED} guard"',              # run directly
    f'python3 -u tools/example.py add "a note about the {SEALED} guard"',   # an interpreter flag
    f'python3 -W ignore tools/example.py add "a note about the {SEALED} guard"',
    f'git -c user.name=x commit -m "tighten the {SEALED} guard"',
    f'git --no-pager commit -m "tighten the {SEALED} guard"',
    "X=1",                                                # an assignment and nothing else
    "X=1; ls docs",
    f'SEAL_READ_OK=1 true; echo "$(cd {SEALED} && pwd)"',   # a carrier inside a substitution
])
def test_bash_survivor_case_is_allowed(tmp_path, command):
    assert _bash(tmp_path, command) == 0, command


@pytest.mark.parametrize("command", [
    f'cat tools/example.py "notes on {SEALED} here"',     # cat opens its arguments
    f'python3 - tools/example.py "notes on {SEALED} here"',     # runs stdin, not the script
    f'python3 -c tools/example.py "notes on {SEALED} here"',
    f'python3 -m tools/example.py "notes on {SEALED} here"',
    f"cp -r --exclude={SEALED} . /tmp/out",               # cp has no such flag: it copies it
])
def test_bash_survivor_case_is_blocked(tmp_path, command):
    assert _bash(tmp_path, command) == 2, command


def test_a_tool_this_hook_is_not_wired_for_is_never_judged(tmp_path):
    code, _ = _run(tmp_path, "WebFetch", {"command": f"cat {SEALED}/a.md"})
    assert code == 0


def test_importing_the_hook_does_not_run_it(tmp_path):
    payload = json.dumps({"tool_name": "Read",
                          "tool_input": {"file_path": str(tmp_path / SEALED / "a.md")}})
    r = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(SCRIPT.parent)!r}); "
         "import check_sealed_read; print('imported')"],
        input=payload, capture_output=True, text=True,
        env={**os.environ, "SEAL_REPO_ROOT": str(tmp_path)})
    assert (r.returncode, r.stdout.strip()) == (0, "imported")


def test_repo_tool_index_on_a_command_with_no_head():
    assert csr._repo_tool_index(["X=1"], 1) is None


@pytest.mark.parametrize("word, quoted", [
    (f"{SEALED}/a.md", True),                 # one path under the directory
    (f"about the {SEALED} guard", False),     # unquoted: the shell splits it
    (f"{SEALED} and more words", True),       # the name opens the word
])
def test_is_prose_says_false_not_none(word, quoted):
    assert csr._is_prose(word, quoted) is False


def test_git_subcommand_is_an_empty_string_when_there_is_none():
    assert csr._git_subcommand(["git", "--version"], 0) == ""


@pytest.mark.parametrize("words, expected", [
    (["grep", "pat", "dir"], ({1}, ["dir"], False)),
    (["grep", "--", "-r", "docs"], ({2}, ["docs"], False)),
    (["grep", "--regexp", "p1", "--regexp", "p2", "dir"], ({2, 4}, ["dir"], False)),
    (["grep", "--regexp=p1", "dir"], ({1}, ["dir"], False)),
    (["grep", "--color", "-n", "pat", "dir"], ({3}, ["dir"], False)),
    (["grep", "--color", "pat", "dir"], ({2}, ["dir"], False)),
    (["grep", "--file", "pats.txt", "dir"], (set(), ["dir"], False)),
    (["grep", "--file=pats.txt", "dir"], (set(), ["dir"], False)),
    (["grep", "--include", "*.md", "pat", "dir"], ({3}, ["dir"], False)),
    (["grep", "--glob", "x", "dir"], ({2}, ["dir"], False)),        # not a grep option
    (["rg", "--glob", "!x", "pat", "dir"], ({3}, ["dir"], True)),
    (["rg", "--hidden", "pat", "dir"], ({2}, ["dir"], True)),
    (["grep", "--recursive", "pat", "dir"], ({2}, ["dir"], True)),
    (["grep", "-n", "pat", "f"], ({2}, ["f"], False)),
    (["grep", "-R", "pat", "dir"], ({2}, ["dir"], True)),
    (["grep", "-rn", "pat", "dir"], ({2}, ["dir"], True)),
    (["grep", "-e", "p1", "-e", "p2", "dir"], ({2, 4}, ["dir"], False)),
    (["grep", "-f", "pats.txt", "dir"], (set(), ["dir"], False)),
    (["grep", "-eio", "x", "dir"], (set(), ["x", "dir"], False)),   # value attached to -e
    (["grep", "-m", "1", "pat", "dir"], ({3}, ["dir"], False)),
])
def test_grep_layout(words, expected):
    assert csr._grep_layout(words, 0, words[0]) == expected


def test_a_search_target_built_from_a_variable_is_assumed_to_reach(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAL_REPO_ROOT", str(tmp_path))
    assert csr._contains_sealed("$DIR/notes") is True
    assert csr._contains_sealed("`pwd`/notes") is True
    assert csr._contains_sealed("docs") is False


def test_find_excludes_says_false_for_a_name_test():
    assert csr._find_excludes(["find", ".", "-name", "project-background"], 3, 0) is False


def test_find_prune_group_with_a_second_alternative_after_the_directory():
    words = ["find", ".", "(", "-path", f"./{SEALED}", "-o", "-name", "node_modules", ")",
             "-prune", "-o", "-type", "f", "-print"]
    assert csr._find_excludes(words, 4, 0) is True
    assert csr._find_excludes(words[:8], 4, 0) is False     # nothing prunes it


def test_mentions_is_an_empty_dict_for_a_command_with_no_head():
    assert csr._mentions(["X=1"], [False], 1) == {}


def test_an_overridden_read_passes_nothing_on():
    c = simple_commands(f"SEAL_READ_OK=1 cat {SEALED}/a.md")[0]
    assert csr._passes_the_name_on(c) is False


def test_bash_reads_sealed_returns_booleans():
    assert csr._bash_reads_sealed("ls docs") is False
    assert csr._bash_reads_sealed(f"export SEAL_READ_OK=1; cat {SEALED}/a.md") is False
    assert csr._bash_reads_sealed(f"cat {SEALED}/a.md") is True


def test_a_command_nested_too_deep_to_parse_blocks_only_when_it_names_the_directory():
    assert csr._bash_reads_sealed(f"cat {SEALED}/a.md", depth=9) is True
    assert csr._bash_reads_sealed("ls docs", depth=9) is False


@pytest.mark.parametrize("p, expected", [
    ("", False), ("data", False), (SEALED, True), (f"{SEALED}/sub/a.md", True),
    ("DATA/Project-Background/a.md", True), (f"{SEALED}-old/a.md", False),
])
def test_under_sealed(tmp_path, p, expected):
    assert csr._under_sealed(tmp_path, p) is expected
    assert csr._under_sealed(tmp_path, str(tmp_path / p)) is expected
