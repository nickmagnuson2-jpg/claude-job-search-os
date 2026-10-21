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
        # Without the session override: with SEAL_READ_OK=1 inherited, a hook that DID
        # run on import would allow the read and this test could not tell.
        env={**{k: v for k, v in os.environ.items() if k != "SEAL_READ_OK"},
             "SEAL_REPO_ROOT": str(tmp_path)})
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



# --- The out-of-tree home, 2026-10-13 ----------------------------------------
# The sealed tree moved to <repo>-sealed/data/project-background/, beside the repo.
# Everything in that sibling is sealed, whether or not a path spells the old name.

def _home(root):
    return root.parent / (root.name + "-sealed")


def test_blocks_read_in_the_sibling_home(tmp_path):
    root = tmp_path / "repo"
    code, err = _run(root, "Read", {"file_path": str(_home(root) / SEALED / "a.md")})
    assert code == 2
    assert "repo-sealed" in err
    code, _ = _run(root, "Read", {"file_path": str(_home(root) / "other" / "a.md")})
    assert code == 2


def test_blocks_grep_and_glob_of_the_sibling_home_itself(tmp_path):
    root = tmp_path / "repo"
    assert _run(root, "Grep", {"pattern": "x", "path": str(_home(root))})[0] == 2
    assert _run(root, "Glob", {"pattern": "**/*.md", "path": "../repo-sealed"})[0] == 2
    assert _run(root, "Glob", {"pattern": "../repo-sealed/**/*.md"})[0] == 2


def test_allows_a_sibling_whose_name_only_starts_the_same(tmp_path):
    root = tmp_path / "repo"
    for name in ("repo-sealed-notes", "repo-seal", "other-sealed"):
        code, _ = _run(root, "Read", {"file_path": str(tmp_path / name / "a.md")})
        assert code == 0, name
    assert _run(root, "Bash", {"command": "ls ../repo-sealed-notes"})[0] == 0
    assert _run(root, "Bash", {"command": "cat ../repo-sealed.md"})[0] == 0


def test_blocks_bash_that_reads_the_sibling_home(tmp_path):
    root = tmp_path / "repo"
    for cmd in ("grep -r term ../repo-sealed", "ls ../REPO-Sealed/data",
                f"find {_home(root)} -name '*.md'", 'cat "../repo-sealed/data/x y.md"'):
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


def test_allows_bash_that_only_mentions_the_sibling_home(tmp_path):
    root = tmp_path / "repo"
    for cmd in ('git commit -m "moved the sealed tree to ../repo-sealed"',
                'echo "sealed files now live in repo-sealed"'):
        assert _run(root, "Bash", {"command": cmd})[0] == 0, cmd
    assert _run(root, "Bash", {"command": "SEAL_READ_OK=1 ls ../repo-sealed"})[0] == 0


def test_under_sealed_covers_the_sibling_home_and_nothing_beside_it(tmp_path):
    root = tmp_path / "repo"
    for p, expected in [("../repo-sealed", True), ("../Repo-Sealed/data", True),
                        ("../repo-sealed/data/project-background/a.md", True),
                        ("../repo-sealed-notes", False), ("../other-sealed", False),
                        ("..", False), ("repo-sealed", False)]:
        assert csr._under_sealed(root, p) is expected, p


def test_the_sibling_home_is_named_after_the_repo():
    assert csr.SEALED_HOME == csr._root().name + "-sealed"
    assert csr._is_a_sealed_path(f"../{csr.SEALED_HOME}/notes") is True
    assert csr._is_a_sealed_path(f"../{csr.SEALED_HOME}-notes/x") is False


# --- A path is judged by where it leads (cross-model review 305) --------------

def test_blocks_read_through_a_symlink_into_the_sibling_home(tmp_path):
    root = tmp_path / "repo"
    (_home(root) / SEALED).mkdir(parents=True)
    (_home(root) / SEALED / "a.md").write_text("x", encoding="utf-8")
    root.mkdir()
    (root / "alias").symlink_to(_home(root))
    (root / "notes").symlink_to(_home(root) / SEALED)
    assert _run(root, "Read", {"file_path": str(root / "alias" / SEALED / "a.md")})[0] == 2
    assert _run(root, "Read", {"file_path": "notes/a.md"})[0] == 2
    assert _run(root, "Grep", {"pattern": "x", "path": str(root / "alias")})[0] == 2


def test_blocks_read_through_a_symlink_to_the_old_in_tree_place(tmp_path):
    root = tmp_path / "repo"
    (root / SEALED).mkdir(parents=True)
    (root / "shortcut").symlink_to(root / SEALED)
    assert _run(root, "Read", {"file_path": str(root / "shortcut" / "a.md")})[0] == 2


def test_an_ordinary_symlink_is_not_sealed(tmp_path):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "d").symlink_to(root / "docs")
    assert _run(root, "Read", {"file_path": str(root / "d" / "a.md")})[0] == 0


def test_a_second_spelling_of_the_root_does_not_hide_the_sibling(tmp_path):
    """The root reached through a symlinked parent: the repo and the path given to the
    tool spell the same directory two ways."""
    real = tmp_path / "real"
    (real / "repo").mkdir(parents=True)
    (tmp_path / "link").symlink_to(real)
    spelled = tmp_path / "link" / "repo-sealed" / SEALED / "a.md"
    assert _run(real / "repo", "Read", {"file_path": str(spelled)})[0] == 2
    assert _run(tmp_path / "link" / "repo", "Read",
                {"file_path": str(real / "repo-sealed" / SEALED / "a.md")})[0] == 2
    assert csr._under_sealed(tmp_path / "link" / "repo", str(real / "repo-sealed")) is True


# ---------------------------------------------------------------------------
# A Python program may name the directory as TEXT. 2026-10-20: a program fed on stdin
# that wrote a brief saying "nothing from <the directory>" was blocked as a read. A
# first fix judged the string's SHAPE (the name mid-sentence) and was rejected in
# cross-model review: `open('read <dir>/a.md'.split()[1])` and an aliased os.system
# both passed. The rebuild follows the VALUE: it passes only when the string provably
# ends as output. The block cases below include every bypass from that review.
# ---------------------------------------------------------------------------

PY_TEXT = {
    "todays_fire_raw_string_written_out":
        f"tail = r'''Hard constraints: no em dashes; nothing from {SEALED} or any sealed source.'''\n"
        "open('notes/wf.js', 'w').write(tail)\n",
    "print": f"print('see the note on {SEALED} below')\n",
    "comment_only": f"# never read {SEALED} from here\nprint(1)\n",
    "bare_string_and_docstring":
        f"'''Notes about {SEALED}.'''\ndef f():\n    '''{SEALED} stays shut.'''\n    return 1\n",
    "with_open_write":
        f"with open('notes/a.md', 'w') as f:\n    f.write('the sealed tree is {SEALED}')\n",
    "json_dump_dict":
        f"import json\njson.dump({{'claim': '{SEALED} is sealed'}}, open('notes/a.json', 'w'))\n",
    "records_appended_then_dumped":
        "import json\nC = []\n"
        f"C.append(dict(claim='nothing from {SEALED}', turn=3))\n"
        "json.dump(C, open('notes/a.json', 'w'), indent=2)\n",
    "subscript_store_then_dumps":
        f"import json\nd = {{}}\nd['k'] = 'text on {SEALED}'\nprint(json.dumps(d))\n",
    "membership_test":
        f"for line in open('notes/log.md'):\n    if '{SEALED}' in line:\n        print(line)\n",
    "startswith_on_the_string_itself": f"print('{SEALED}/a.md'.startswith('data'))\n",
    "fstring_printed": f"n = 3\nprint(f'{{n}} files, none under {SEALED}')\n",
    "join_of_literals_printed": f"print('\\n'.join(['a', 'note on {SEALED}']))\n",
    "format_printed": f"print('{{}} is sealed'.format('{SEALED}'))\n",
    "percent_format_printed": f"print('%s is sealed' % '{SEALED}')\n",
    "stdout_and_stderr":
        f"import sys\nsys.stdout.write('{SEALED} note')\nprint('x {SEALED}', file=sys.stderr)\n",
    "assert_message": f"x = 1\nassert x, 'x must not touch {SEALED}'\n",
    "augmented_assignment_printed": f"s = ''\ns += 'note on {SEALED}'\nprint(s)\n",
    "variable_copied_then_printed": f"a = 'note on {SEALED}'\nb = a\nprint(b.strip())\n",
    # Two variables that feed each other: the analysis must not loop or give up.
    "variables_assigned_back_and_forth": f"a = 'note on {SEALED}'\nb = a\na = b\nprint(a)\n",
    "file_deleted_after_write":
        f"with open('notes/a.md', 'w') as f:\n    f.write('{SEALED} x')\ndel f\n",
    "annotated_file": f"f: 'TextIO' = open('notes/a.md', 'w')\nf.write('{SEALED} x')\n",
}

PY_READ = {
    # Cross-model review, F1 and F2.
    "split_back_into_a_path": f"print(open('read {SEALED}/a.md'.split()[1]).read())\n",
    "sliced_back_into_a_path":
        f"from pathlib import Path\nprint(Path('path: {SEALED}/a.md'[6:]).read_text())\n",
    "aliased_os_system": f"from os import system as run\nrun('cat {SEALED}/a.md')\n",
    "os_system_held_in_a_variable":
        f"import os\nrunner = os.system\nrunner('cat {SEALED}/a.md')\n",
    # A sink name rebound to something that runs or opens.
    "print_rebound": f"import os\nprint = os.system\nprint('cat {SEALED}/a.md')\n",
    "print_imported_as": f"from os import system as print\nprint('cat {SEALED}/a.md')\n",
    "str_imported_as":
        f"from subprocess import getoutput as str\nprint(str('cat {SEALED}/a.md'))\n",
    "json_imported_as": f"import subprocess as json\njson.dumps('cat {SEALED}/a.md')\n",
    "path_from_elsewhere": f"from shutil import Path\nPath('x').write_text('{SEALED} note')\n",
    "print_defined_as_a_function":
        f"import os\ndef print(s):\n    os.system(s)\nprint('cat {SEALED}/a.md')\n",
    "print_as_a_parameter":
        f"import os\ndef f(print):\n    print('cat {SEALED}/a.md')\nf(os.system)\n",
    "sink_called_from_a_list": f"import os\nfns = [os.system]\nfns[0]('cat {SEALED}/a.md')\n",
    "bound_method_self": f"print(open('{SEALED}/a.md'.count.__self__).read())\n",
    # A write whose receiver is a process, not a file.
    "write_to_a_shell":
        "import subprocess\np = subprocess.Popen(['bash'], stdin=subprocess.PIPE, text=True)\n"
        f"p.stdin.write('cat {SEALED}/a.md\\n')\n",
    "print_to_a_shell":
        "import subprocess\np = subprocess.Popen(['bash'], stdin=subprocess.PIPE, text=True)\n"
        f"print('cat {SEALED}/a.md', file=p.stdin)\n",
    "json_dump_to_a_shell":
        "import json, subprocess\np = subprocess.Popen(['sh'], stdin=subprocess.PIPE, text=True)\n"
        f"json.dump('cat {SEALED}/a.md', p.stdin)\n",
    "stdout_replaced":
        "import sys, subprocess\nsys.stdout = subprocess.Popen(['sh'], stdin=subprocess.PIPE, text=True).stdin\n"
        f"sys.stdout.write('cat {SEALED}/a.md')\n",
    "file_name_rebound_to_a_process":
        "import subprocess\nf = subprocess.Popen(['sh'], stdin=subprocess.PIPE, text=True).stdin\n"
        f"with open('notes/a.md', 'w') as f:\n    pass\nf.write('cat {SEALED}/a.md')\n",
    "self_bound_name": f"f = f\nf.write('{SEALED} x')\n",
    "write_text_on_an_unknown_object": f"import notes\nnotes.thing().write_text('{SEALED} x')\n",
    "class_defines_a_text_step":
        "class R:\n    def startswith(self, p):\n        return open(p).read()\n"
        f"print(R().startswith('{SEALED}/a.md'))\n",
    # A variable reached without spelling its name.
    "globals_lookup": f"import os\nt = 'cat {SEALED}/a.md'\nos.system(globals()['t'])\n",
    "main_module_attribute":
        f"t = '{SEALED}/a.md'\nprint(t)\nimport __main__\nprint(open(__main__.t).read())\n",
    "name_spelled_in_a_string":
        f"import operator, sys\nt = '{SEALED}/a.md'\nprint(t)\nprint(open(vars(sys.modules[__name__])['t']).read())\n",
    "variable_name_as_an_attribute":
        f"import notes\nt = '{SEALED}/a.md'\nprint(t)\nprint(open(notes.t).read())\n",
    "variable_name_as_a_string":
        f"import notes\nt = '{SEALED}/a.md'\nprint(t)\nprint(open(notes.lookup('t')).read())\n",
    # The value reaches a call that is not a text sink.
    "variable_opened": f"t = '{SEALED}/a.md'\nprint(open(t).read())\n",
    "loop_over_a_list": f"for p in ['{SEALED}/a.md']:\n    print(open(p).read())\n",
    "dict_value_subscripted": f"d = {{'p': '{SEALED}/a.md'}}\nprint(open(d['p']).read())\n",
    "appended_then_iterated":
        f"L = []\nL.append('{SEALED}/a.md')\nfor p in L:\n    print(open(p).read())\n",
    "walrus": f"print(open(p := '{SEALED}/a.md').read())\n",
    "replace_into_a_path": f"t = 'x{SEALED}/a.md'.replace('x', '')\nprint(open(t).read())\n",
    "os_path_join": "import os\nprint(open(os.path.join('data', 'project-background', 'a.md')).read())\n",
    "format_into_open": f"print(open('{{}}/a.md'.format('{SEALED}')).read())\n",
    "join_into_open": f"print(open('/'.join(['{SEALED}', 'a.md'])).read())\n",
    "concat_into_open": f"print(open('{SEALED}' + '/a.md').read())\n",
    "fstring_expression_opens": f"print(f\"{{open('{SEALED}/a.md').read()}}\")\n",
    "exec_of_a_string": f"exec(\"print(open('{SEALED}/a.md').read())\")\n",
    "bytes_path": f"print(open(b'{SEALED}/a.md').read())\n",
    "augmented_then_run": f"import os\ns = 'cat '\ns += '{SEALED}/a.md'\nos.system(s)\n",
    "unknown_function": f"def c(x):\n    return open(x).read()\nprint(c('{SEALED}/a.md'))\n",
    # Spelled outside any string or comment, or not Python at all.
    "name_in_code": f"x = 1\ny = {SEALED}\n",
    "unparseable": f"print('unterminated {SEALED} \n",
}


@pytest.mark.parametrize("name", sorted(PY_TEXT))
def test_python_text_is_a_mention(name):
    assert csr._python_only_mentions(PY_TEXT[name]) is True


@pytest.mark.parametrize("name", sorted(PY_READ))
def test_python_read_is_not_a_mention(name):
    assert csr._python_only_mentions(PY_READ[name]) is False


@pytest.mark.parametrize("command", [
    f"python3 - <<'PY'\n{PY_TEXT['todays_fire_raw_string_written_out']}PY",
    f"cd /tmp && PYTHONIOENCODING=utf-8 python3 - <<'PY'\n{PY_TEXT['records_appended_then_dumped']}PY",
    f"python3 <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 -u - <<'PY'\n{PY_TEXT['comment_only']}PY",
    f"python3 -W ignore - <<'PY'\n{PY_TEXT['with_open_write']}PY",
    f"python3 --no-site - <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 - notes/out.md <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 -c \"print('the sealed tree is {SEALED}')\"",
    f"python3 -uc \"print('the sealed tree is {SEALED}')\"",
])
def test_bash_python_text_is_allowed(tmp_path, command):
    assert _bash(tmp_path, command) == 0, command


@pytest.mark.parametrize("command", [
    f"python3 - <<'PY'\n{PY_READ['split_back_into_a_path']}PY",
    f"python3 -c \"{PY_READ['split_back_into_a_path'].strip()}\"",
    f"python3 - <<'PY'\n{PY_READ['aliased_os_system']}PY",
    # Text programs, but not a Python program on stdin or in -c.
    f"python3 notes/runner.py <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 -m notes_runner <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 -W ignore notes/runner.py <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 -c\"print('the sealed tree is {SEALED}')\"",
    f"node <<'EOF'\nconsole.log('see the note on {SEALED} below')\nEOF",
    f"cat <<'PY' | python3\n{PY_TEXT['print']}PY",
    # A text program does not cover a read beside it.
    f"python3 - <<'PY'\n{PY_TEXT['print']}PY\ncat {SEALED}/a.md",
    f"python3 - {SEALED}/a.md <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 -c \"print('{SEALED} note')\" {SEALED}/a.md",
])
def test_bash_python_read_is_blocked(tmp_path, command):
    assert _bash(tmp_path, command) == 2, command


@pytest.mark.parametrize("words, expected", [
    (["python3", "-"], ("stdin", None)),
    (["python3"], ("stdin", None)),
    (["python3", "-W", "ignore", "-"], ("stdin", None)),
    (["python3", "-c", "x"], ("code", 2)),
    (["python3", "-uc", "x"], ("code", 2)),
    (["python3", "-m", "x"], ("other", None)),
    (["python3", "-cx"], ("other", None)),
    (["python3", "-u", "a.py"], ("file", 2)),
    (["python3", "--no-site", "a.py"], ("file", 2)),
    (["node", "-"], None),
    ([], None),
])
def test_python_source(words, expected):
    assert csr._python_source(words, 0) == expected


def test_python_analysis_that_raises_is_a_read(monkeypatch):
    """A crash in the hook exits 1, which Claude Code treats as allow. The analysis
    must turn any exception into a read instead."""
    def boom(tree):
        raise RecursionError("too deep")
    monkeypatch.setattr(csr, "_PyFlow", boom)
    assert csr._python_only_mentions(PY_TEXT["print"]) is False


# Second review (Fable). F9: ordinary brief-writing idioms must pass. F1-F7: each gate
# tested on its own, without an import that would trip the module list first.
PY_TEXT_2 = {
    "textwrap_dedent": f'import textwrap\nprint(textwrap.dedent("""\n    see {SEALED}\n"""))\n',
    "loop_over_lines_written":
        f"lines = ['a', 'nothing from {SEALED}']\nwith open('notes/a.md', 'w') as f:\n"
        "    for line in lines:\n        f.write(line + '\\n')\n",
    "replace_then_printed": f"s = 'X {SEALED}'\ns = s.replace('X', 'Y')\nprint(s)\n",
    "split_then_printed": f"print('read {SEALED}/a.md'.split()[1])\n",
    "conditional_expression": f"x = 1\nprint('a' if x else 'no {SEALED}')\n",
    "sys_exit_message": f"import sys\nsys.exit('refusing {SEALED}')\n",
    "main_guard": f"if __name__ == '__main__':\n    print('note on {SEALED}')\n",
    "plain_format_template": f"print('{{0}} and {{name}}'.format('{SEALED}', name='x'))\n",
}

PY_READ_2 = {
    "print_keyword_unpacking": f"print('{SEALED} x', **{{'file': open('notes/a.md', 'w')}})\n",
    "match_capture_rebinds_print":
        f"import json\nmatch json.dumps:\n    case print:\n        print('{SEALED} x')\n",
    "except_as_rebinds_print":
        f"try:\n    1 / 0\nexcept ZeroDivisionError as print:\n    pass\nprint('{SEALED} x')\n",
    "store_into_sys_argv": f"import sys\na = sys.argv\na[0] = '{SEALED}/a.md'\nprint(a)\n",
    "append_to_sys_argv": f"import sys\na = sys.argv\na.append('{SEALED}/a.md')\nprint(a)\n",
    "dunder_globals":
        f"t = '{SEALED}/a.md'\nprint(t)\ndef f():\n    pass\n"
        "for v in f.__globals__.values():\n    print(open(v).read())\n",
    "dunder_builtins": f"t = '{SEALED}/a.md'\nprint(t)\nprint(open(__builtins__.eval('t')).read())\n",
    "format_template_lookup":
        f"t = '{SEALED}/a.md'\nprint(t)\ndef f():\n    pass\n"
        "print(open('{0.__globals__[t]}'.format(f)).read())\n",
    "format_on_a_variable": f"t = '{SEALED}/a.md'\nprint(t)\nk = '{{t}}'\nprint(k.format(t=1))\n",
    "format_map": f"t = '{SEALED}/a.md'\nprint(t)\nprint('{{t}}'.format_map({{}}))\n",
    "sys_modules": f"import sys\nt = '{SEALED}/a.md'\nprint(t)\nprint(sys.modules)\n",
    "setattr_on_sys":
        f"import sys\nsetattr(sys, 'stdout', open('notes/a.md', 'w'))\nprint('{SEALED} x')\n",
    "module_not_listed": f"import os\nprint('{SEALED} x')\n",
    "relative_import": f"from . import notes\nprint('{SEALED} x')\n",
    "deeply_nested": "x = " + "-" * 100000 + f"1\nprint('{SEALED} x')\n",
}


@pytest.mark.parametrize("name", sorted(PY_TEXT_2))
def test_python_text_is_a_mention_2(name):
    assert csr._python_only_mentions(PY_TEXT_2[name]) is True


@pytest.mark.parametrize("name", sorted(PY_READ_2))
def test_python_read_is_not_a_mention_2(name):
    assert csr._python_only_mentions(PY_READ_2[name]) is False


@pytest.mark.parametrize("command", [
    f"sh <(python3 -c \"print('cat {SEALED}/a.md')\")",
    f"python3 - > >(sh) <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 - 3> >(sh) <<'PY'\n{PY_TEXT['print']}PY",
    f"coproc sh; python3 - >&${{COPROC[1]}} <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 - 1>&3 <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 - > \"$OUT\" <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 - <&3 <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 -c \"print('{SEALED} x')\" >&3",
    f"echo $(cat <(python3 -c \"print('{SEALED} x')\"))",
])
def test_python_output_wired_to_another_command_is_blocked(tmp_path, command):
    assert _bash(tmp_path, command) == 2, command


@pytest.mark.parametrize("template, plain", [
    ("{0} and {name}", True), ("{}", True), ("{0:>{width}}", False),
    ("{0.attr}", False), ("{x[k]}", False), ("{", False), (3, False),
])
def test_plain_template(template, plain):
    assert csr._plain_template(template) is plain


# Third review (Grok, Fable). A builtin outside the list, `type()` above all, builds an
# object whose methods run code, and a method is a sink only on a proven receiver.
PY_READ_3 = {
    "type_builds_a_startswith_that_opens":
        f"R = type('R', (), {{'startswith': lambda self, p: open(p).read()}})\n"
        f"print(R().startswith('{SEALED}/a.md'))\n",
    "type_builds_a_contains_that_opens":
        f"R = type('R', (), {{'__contains__': lambda self, p: bool(open(p).read())}})\n"
        f"print('{SEALED}/a.md' in R())\n",
    "namedtuple_field_named_startswith":
        "import collections\nT = collections.namedtuple('T', 'startswith')\n"
        f"print(T(lambda p: open(p).read()).startswith('{SEALED}/a.md'))\n",
    "verdict_method_on_an_unknown_receiver":
        f"for line in open('notes/log.md'):\n    print(line.startswith('{SEALED}'))\n",
    "builtin_not_listed": f"print(hasattr(print, 'x'), '{SEALED} x')\n",
}


@pytest.mark.parametrize("name", sorted(PY_READ_3))
def test_python_read_is_not_a_mention_3(name):
    assert csr._python_only_mentions(PY_READ_3[name]) is False


@pytest.mark.parametrize("command", [
    # Output to plain files and fd 1/2 is still text.
    f"python3 - > notes/out.md 2>&1 <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 - >> notes/out.md 2>/dev/null <<'PY'\n{PY_TEXT['print']}PY",
    f"python3 -c \"print('{SEALED} x')\" 1>&2",
    # A program whose TEXT mentions the plumbing words is not plumbing.
    f"python3 - <<'PY'\nprint('do not use mkfifo or <(cmd) or coproc; nothing from {SEALED}')\nPY",
])
def test_python_text_with_plain_output_is_allowed(tmp_path, command):
    assert _bash(tmp_path, command) == 0, command


def test_plain_output_and_process_substitution_helpers():
    one = lambda cmd: simple_commands(cmd)[0]
    assert csr._plain_output(one("python3 - > a 2>&1 3>b &>c <<'P'\nx\nP")) is True
    assert csr._plain_output(one("python3 - >&3")) is False
    assert csr._plain_output(one("python3 - > $F")) is False
    assert csr._plain_output(one("python3 - < a.py")) is False
    assert csr._no_process_substitution(simple_commands("sh <(python3 -c x)")) is False
    assert csr._no_process_substitution(simple_commands("python3 - > a")) is True


# Mutation round 3: each gate reached by a program that passes every other gate.
PY_TEXT_4 = {
    "dict_call_container": f"d = dict()\nd['k'] = 'note on {SEALED}'\nprint(d)\n",
    "list_call_container": f"a = list()\na.append('note on {SEALED}')\nprint(a)\n",
}

PY_READ_4 = {
    "function_named_print": f"def print(s):\n    return open(s).read()\nprint('{SEALED}/a.md')\n",
    "parameter_named_print": f"def f(print):\n    print('{SEALED}/a.md')\nf(open)\n",
    "class_named_like_a_builtin":
        "class str:\n    def __contains__(self, p):\n        return bool(open(p).read())\n"
        f"print('{SEALED}/a.md' in str())\n",
    "store_into_a_call_that_is_not_a_container":
        f"a = sorted([])\na.append('{SEALED}/a.md')\nprint(a)\n",
    "uncalled_method_on_the_value": f"print('{SEALED}/a.md'.strip)\n",
    "print_file_not_a_writer": f"f = 'notes/a.md'\nprint('{SEALED} x', file=f)\n",
    "json_imported_as_another_name": f"import json as j\nprint(j.dumps('{SEALED} x'))\n",
    "json_dump_to_a_string": f"import json\njson.dump('{SEALED} x', 'notes/a.json')\n",
    # Python 3.14 t-strings build a Template, not a str; not followed, so a read.
    "t_string": f"print(t'nothing from {SEALED}')\n",
    "call_through_a_subscript": f"fns = [print]\nfns[0]('{SEALED}/a.md')\n",
}


@pytest.mark.parametrize("name", sorted(PY_TEXT_4))
def test_python_text_is_a_mention_4(name):
    assert csr._python_only_mentions(PY_TEXT_4[name]) is True


@pytest.mark.parametrize("name", sorted(PY_READ_4))
def test_python_read_is_not_a_mention_4(name):
    assert csr._python_only_mentions(PY_READ_4[name]) is False


# Fifth review (Fable, Grok): a program that spells the name in a print could open the
# directory through a path built from pieces. Every path must now fold to literals.
PY_TEXT_5 = {
    "folded_fstring_path":
        f"base = 'notes'\nopen(f'{{base}}/a.md', 'w').write('nothing from {SEALED}')\n",
    "read_a_literal_file_then_write":
        f"import json\nd = json.load(open('notes/a.json'))\nprint('{SEALED} x', len(d))\n",
}

PY_READ_5 = {
    "concatenated_path":
        f"print('nothing from {SEALED}')\nprint(open('data/project-' + 'background/a.md').read())\n",
    "fstring_pieces_path":
        f"a = 'project-'\nprint('nothing from {SEALED}')\nprint(open(f'data/{{a}}background/a.md').read())\n",
    "path_built_from_pieces":
        f"from pathlib import Path\nprint('nothing from {SEALED}')\n"
        "print((Path('data') / ('project-' + 'background') / 'a.md').read_text())\n",
    "path_method_argument_from_pieces":
        f"from pathlib import Path\nprint('nothing from {SEALED}')\n"
        "print(Path('data').joinpath('project-' + 'background', 'a.md').read_text())\n",
    "walk_above_the_repo":
        f"from pathlib import Path\nprint('nothing from {SEALED}')\n"
        "for p in Path('..').rglob('*.md'):\n    print(p.read_text())\n",
    "path_from_argv": f"import sys\nprint('nothing from {SEALED}')\nprint(open(sys.argv[1]).read())\n",
    "path_from_a_loop":
        f"print('nothing from {SEALED}')\nfor n in ['a', 'b']:\n    print(open(n).read())\n",
    "open_a_descriptor": f"open(3, 'w').write('cat {SEALED}/a.md')\n",
    "implicitly_concatenated_path":
        f"print('nothing from {SEALED}')\nprint(open('data/project-' 'background/a.md').read())\n",
    "open_with_no_path": f"open().write('{SEALED} x')\n",
    "path_with_keywords": f"from pathlib import Path\nPath('notes', x=1).write_text('{SEALED} x')\n",
}


@pytest.mark.parametrize("name", sorted(PY_TEXT_5))
def test_python_text_is_a_mention_5(name):
    assert csr._python_only_mentions(PY_TEXT_5[name]) is True


@pytest.mark.parametrize("name", sorted(PY_READ_5))
def test_python_read_is_not_a_mention_5(name):
    assert csr._python_only_mentions(PY_READ_5[name]) is False


def test_combine_and_static_folding():
    assert csr._combine([{"a", "b"}, {"c"}], "/") == {"a/c", "b/c"}
    assert csr._combine([{"a"}, None], "") is None
    assert csr._combine([], "") is None
    assert csr._combine([{str(i) for i in range(17)}] * 2, "") is None   # 289 > 256
    assert csr._combine([{str(i) for i in range(16)}] * 2, "") is not None


# Sixth review (Fable, Grok): pathlib has too many ways to build and walk a path to fold
# (cwd(), resolve(), parents[i], PosixPath, '..' in a glob pattern, absolute segments),
# so it is not an allowed module, and a comment no longer excuses the program around
# it. These were text under earlier versions and are reads now, by decision.
PY_READ_6 = {
    "path_open_write":
        f"from pathlib import Path\nPath('notes/a.md').open('w').write('{SEALED} x')\n",
    "path_write_text_concat":
        f"from pathlib import Path\nPath('notes/a.md').write_text('see ' + '{SEALED}' + ' below')\n",
    "pathlib_path_div_write_text":
        f"import pathlib\nroot = pathlib.Path('notes')\n(root / 'a.md').write_text('about {SEALED}')\n",
    "comment_in_an_unsafe_program":
        f'import os\n# never read {SEALED}\nprint(os.getcwd())\n',
    "path_parent_write_text":
        f"from pathlib import Path\n(Path('notes/a.md').parent / 'b.md').write_text('{SEALED} x')\n",
    "glob_inside_the_repo":
        f"from pathlib import Path\nfor p in Path('notes').glob('*.md'):\n    print(p.name.startswith('x'), '{SEALED} note')\n",
    "path_division_of_literals":
        f"from pathlib import Path\n(Path('notes') / 'sub' / 'a.md').write_text('{SEALED} x')\n",
    "comment_plus_assembled_path":
        f"# nothing from {SEALED}\nprint(open('data/project-' + 'background/a.md').read())\n",
    "open_held_in_a_variable":
        f"o = open\nprint('nothing from {SEALED}')\nprint(o('data/project-' + 'background/a.md').read())\n",
    "open_passed_to_map":
        f"print('nothing from {SEALED}')\nprint(list(map(open, ['notes/a.md'])))\n",
    "open_from_a_list": f"print('nothing from {SEALED}')\nprint([open][0]('notes/a.md').read())\n",
    "open_rebound": f"def open(p):\n    return p\nprint('{SEALED} x')\n",
    "open_with_file_keyword_assembled":
        f"print('{SEALED} x')\nprint(open(file='data/project-' + 'background/a.md').read())\n",
}


@pytest.mark.parametrize("name", sorted(PY_READ_6))
def test_python_read_is_not_a_mention_6(name):
    assert csr._python_only_mentions(PY_READ_6[name]) is False


def test_open_with_file_keyword_literal_is_text():
    assert csr._python_only_mentions(
        f"open(file='notes/a.md', mode='w').write('nothing from {SEALED}')\n") is True


@pytest.mark.parametrize("cluster, expected", [
    ("-uc", ("code", 2)), ("-mc", ("other", None)), ("-Wc", ("other", None)),
    ("-Xc", ("other", None)), ("-cu", ("other", None)), ("-c", ("code", 2)),
])
def test_python_option_cluster(cluster, expected):
    assert csr._python_source(["python3", cluster, "x"], 0) == expected


def test_combine_counts_before_building():
    big = {str(i) for i in range(1000)}
    assert csr._combine([big, big, big], "") is None        # 10^9 joins never built


def test_open_of_the_sealed_home_from_pieces():
    """The sibling home is named after the repo directory, so build it from the module."""
    home = csr.SEALED_HOME
    code = f"print('{SEALED} x')\nprint(open('../' + {home[:4]!r} + {home[4:]!r} + '/x.md').read())\n"
    assert csr._python_only_mentions(code) is False


# Seventh review (Grok): an allowed module exposes the modules it imported, and an
# allowed module has submodules. Each module now has a list of usable attributes.
_ASSEMBLED = "'data/project-' + 'background/a.md'"
PY_READ_7 = {
    "from_sys_import_modules":
        f"from sys import modules\nprint('{SEALED} x')\nprint(modules['builtins'].open({_ASSEMBLED}).read())\n",
    "submodule_import_from": f"import sys\nfrom json.tool import main\nprint('{SEALED} x')\nmain()\n",
    "dotted_import": f"import json.tool\nprint('{SEALED} x')\n",
    "module_through_a_module":
        f"import re\nprint('{SEALED} x')\nprint(re.enum.sys.modules['builtins'].open({_ASSEMBLED}).read())\n",
    "module_held_in_a_variable": f"import re\nm = re\nprint('{SEALED} x')\nprint(m.escape('x'))\n",
    "module_passed_as_a_value": f"import re\nprint('{SEALED} x', re)\n",
    "private_attribute":
        f"import collections\nprint('{SEALED} x')\nprint(collections._sys.modules['builtins'].open({_ASSEMBLED}).read())\n",
    "from_import_unlisted_name": f"from collections import abc\nprint('{SEALED} x', abc)\n",
    "relative_from_import": f"from .json import dumps\nprint(dumps('{SEALED} x'))\n",
}

PY_TEXT_7 = {
    "from_import_listed_name": f"from collections import Counter\nprint(Counter('ab'), '{SEALED} x')\n",
    "module_alias_listed_attribute": f"import re as r\nprint(r.escape('x'), '{SEALED} x')\n",
    "listed_module_attributes":
        f"import re, datetime\nprint(re.sub('a', 'b', 'a'), datetime.date.today(), '{SEALED} x')\n",
}


@pytest.mark.parametrize("name", sorted(PY_READ_7))
def test_python_read_is_not_a_mention_7(name):
    assert csr._python_only_mentions(PY_READ_7[name]) is False


@pytest.mark.parametrize("name", sorted(PY_TEXT_7))
def test_python_text_is_a_mention_7(name):
    assert csr._python_only_mentions(PY_TEXT_7[name]) is True


# Mutation round 7: gates the path rule now reaches first in older cases, each reached
# by a program that passes every other gate (no `open`, or a literal `open` path).
_CHAIN = "".join(f"p{i} = p{i - 1}\n" for i in range(1, 11))
PY_READ_8 = {
    "parameter_named_print_without_open":
        f"def f(print):\n    print('{SEALED} x')\nf(len)\n",
    "class_without_open": f"class str:\n    pass\nprint('{SEALED} x' in str())\n",
    "path_folded_through_too_many_names":
        f"p0 = 'notes/a.md'\n{_CHAIN}open(p10, 'w').write('{SEALED} x')\n",
    "percent_formatted_path": f"open('notes/%s' % 'a.md', 'w').write('{SEALED} x')\n",
    # A regression case: `open` as a keyword value fails closed whichever check catches
    # it. The isolating case for the call-position check is open_passed_as_a_value.
    "open_as_a_key_function":
        f"print('{SEALED} x', max('notes/a.md', 'data/project-' + 'background/a.md', key=open))\n",
    "open_file_keyword_after_mode":
        f"print('{SEALED} x')\nprint(open(mode='r', file='data/project-' + 'background/a.md').read())\n",
    # len IS an allowed builtin, so the builtin list passes; what decides is that len is
    # not a text sink (_argument's _PY_CONTAINERS test).
    "literal_into_a_builtin_that_is_not_a_sink": f"n = len('{SEALED} x')\n",
    "variable_into_a_builtin_that_is_not_a_sink": f"t = '{SEALED} x'\nn = len(t)\n",
    "name_spelled_as_arithmetic":
        "project = background = 2\ny = project-background\nprint(y)\n",
    "open_passed_as_a_value": f"print('notes/a.md', open, '{SEALED} x')\n",
    "walrus_without_open": f"print((t := '{SEALED} x'))\n",
}

PY_TEXT_8 = {
    "open_concatenated_literal_path": f"open('notes/' + 'a.md', 'w').write('{SEALED} x')\n",
    # A binding recorded as an identifier string (except-as, match capture) in an
    # otherwise text-only program must not cost the exemption (round 9, Fable F1).
    "except_as_binding":
        f"try:\n    n = int('x')\nexcept ValueError as e:\n    print('{SEALED} x', e)\n",
    "match_capture_binding":
        f"match 3:\n    case n:\n        print('{SEALED} x', n)\n",
}


@pytest.mark.parametrize("name", sorted(PY_READ_8))
def test_python_read_is_not_a_mention_8(name):
    assert csr._python_only_mentions(PY_READ_8[name]) is False


@pytest.mark.parametrize("name", sorted(PY_TEXT_8))
def test_python_text_is_a_mention_8(name):
    assert csr._python_only_mentions(PY_TEXT_8[name]) is True


def test_a_name_folding_past_the_cap_is_unknown(monkeypatch):
    code = f"p = 'notes/a.md'\np = 'notes/b.md'\nopen(p, 'w').write('{SEALED} x')\n"
    assert csr._python_only_mentions(code) is True
    monkeypatch.setattr(csr, "_PY_MAX_VALUES", 1)
    assert csr._python_only_mentions(code) is False


def test_no_listed_attribute_or_builtin_reaches_a_module_or_open():
    """The positive lists ARE the boundary (round 8, Fable F1; round 9, Fable F2/F3, Grok
    F1). Two checks: what each listed name resolves to, and a pin of both lists, so any
    addition fails here and must be made deliberately in this test too."""
    import builtins
    import importlib
    import types
    # Each listed attribute must be defined by its own module, that module's C
    # accelerator, builtins, or json.decoder (JSONDecodeError): an allowed set, so a
    # helper re-exported from os/io/gzip/tokenize fails. Openers are also caught by
    # name. Neither is complete on its own; the equality pin below is the primary
    # guard, and these are the backstop that explains WHY a pinned entry is safe.
    openers = {"open", "urlopen", "connect", "input", "getline", "parse", "main"}
    streams = {("sys", "stdout"), ("sys", "stderr"), ("sys", "stdin")}
    for mod, attrs in csr._PY_MODULE_ATTRS.items():
        m = importlib.import_module(mod)
        for a in attrs:
            v = getattr(m, a)
            assert not isinstance(v, types.ModuleType), (mod, a)
            assert not (isinstance(v, dict) and any(
                isinstance(x, types.ModuleType) for x in v.values())), (mod, a)
            assert v is not builtins.open, (mod, a)
            assert getattr(v, "__name__", "") not in openers, (mod, a)
            if (mod, a) not in streams:
                owner = getattr(v, "__module__", None) or type(v).__module__
                allowed = {mod, "_" + mod, "builtins"} | ({"json.decoder"} if mod == "json" else set())
                assert owner in allowed, (mod, a, owner)
    reaches = {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint",
               "type", "getattr", "setattr", "delattr", "vars", "globals", "locals", "dir",
               "object", "super", "classmethod", "staticmethod", "property", "memoryview",
               "help", "id", "hasattr", "callable", "iter", "next", "issubclass"}
    assert all(hasattr(builtins, b) for b in csr._PY_BUILTINS)
    assert csr._PY_BUILTINS & reaches == {"open"}
    assert csr._PY_BUILTINS == {
        "print", "open", "str", "dict", "list", "tuple", "set", "frozenset", "len",
        "sorted", "reversed", "enumerate", "range", "zip", "min", "max", "sum", "any",
        "all", "abs", "round", "int", "float", "bool", "isinstance", "repr", "map",
        "filter", "Exception", "ValueError", "KeyError", "TypeError", "IndexError",
        "OSError", "FileNotFoundError", "RuntimeError", "ZeroDivisionError",
        "AssertionError", "SystemExit", "StopIteration"}
    assert {m: sorted(a) for m, a in csr._PY_MODULE_ATTRS.items()} == {
        "collections": ["Counter", "OrderedDict", "defaultdict", "deque", "namedtuple"],
        "csv": ["DictReader", "DictWriter", "QUOTE_ALL", "QUOTE_MINIMAL", "reader", "writer"],
        "datetime": ["date", "datetime", "time", "timedelta", "timezone"],
        "difflib": ["Differ", "SequenceMatcher", "context_diff", "get_close_matches", "ndiff",
                    "unified_diff"],
        "itertools": ["accumulate", "batched", "chain", "combinations", "count", "cycle",
                      "dropwhile", "groupby", "islice", "pairwise", "permutations", "product",
                      "repeat", "starmap", "takewhile", "zip_longest"],
        "json": ["JSONDecodeError", "dump", "dumps", "load", "loads"],
        "math": ["ceil", "exp", "floor", "fsum", "inf", "isclose", "log", "log10", "pi",
                 "prod", "sqrt"],
        "re": ["A", "ASCII", "DOTALL", "I", "IGNORECASE", "M", "MULTILINE", "S", "VERBOSE",
               "X", "compile", "escape", "findall", "finditer", "fullmatch", "match", "search",
               "split", "sub", "subn"],
        "sys": ["argv", "exit", "stderr", "stdin", "stdout", "version", "version_info"],
        "textwrap": ["dedent", "fill", "indent", "shorten", "wrap"],
    }