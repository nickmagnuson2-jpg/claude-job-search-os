"""Tests for the personal-vault branch of check_sealed_read.py (added 2026-10-27).

A sealed folder in the sibling personal repo is labeled SEALED there, under the same rule as
job-search's own sealed tree, and a job-search session must not read it. Which folder is
sealed is itself private, so the guard reads it from the gitignored
tools/.sealed-folders.conf (tools/vault_paths.py, 2026-11-01). These tests write that file
under a tmp repo root (SEAL_REPO_ROOT) naming the fictional folder data/alcove, so no real
file is touched. With no config the guard fails closed: everything under the vault's data/
counts as sealed (the tests at the end).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_sealed_read.py"


def _run(root, tool, tool_input, env_extra=None):
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    env = {**os.environ, "SEAL_REPO_ROOT": str(root)}
    env.pop("SEAL_READ_OK", None)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True, env=env)
    return r.returncode, r.stderr


def _layout(tmp_path, configured=True):
    root = tmp_path / "job-search"
    vault = tmp_path / "personal" / "data" / "alcove"
    vault.mkdir(parents=True)
    (vault / "2026-01-01-note.md").write_text("x")
    (root / "data" / "workbooks").mkdir(parents=True)
    (root / "tools").mkdir()
    if configured:
        (root / "tools" / ".sealed-folders.conf").write_text("# sealed\ndata/alcove\n")
    return root, vault


# --- should BLOCK (exit 2) --------------------------------------------------

def test_blocks_read_of_sealed_file(tmp_path):
    root, vault = _layout(tmp_path)
    code, err = _run(root, "Read", {"file_path": str(vault / "2026-01-01-note.md")})
    assert code == 2
    assert "sealed" in err


def test_blocks_grep_into_vault_dir(tmp_path):
    root, vault = _layout(tmp_path)
    code, _ = _run(root, "Grep", {"pattern": "x", "path": str(vault)})
    assert code == 2


def test_blocks_glob_into_vault_dir(tmp_path):
    root, vault = _layout(tmp_path)
    code, _ = _run(root, "Glob", {"pattern": "*.md", "path": str(vault)})
    assert code == 2


def test_blocks_bash_cat_of_a_vault_path(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": "cat ../personal/data/alcove/2026-01-01-note.md"})
    assert code == 2


def test_blocks_bash_cd_then_relative_read(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": "cd ../personal/data && cat alcove/2026-01-01-note.md"})
    assert code == 2


def test_blocks_symlink_into_the_vault(tmp_path):
    root, vault = _layout(tmp_path)
    link = root / "data" / "link-to-notes"
    link.symlink_to(vault)
    code, _ = _run(root, "Read", {"file_path": str(link / "2026-01-01-note.md")})
    assert code == 2


# --- should ALLOW (exit 0) --------------------------------------------------

def test_allows_job_search_file_with_the_folder_name_in_its_name(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Read", {"file_path": str(root / "data" / "workbooks" / "notes-on-alcove-topics.md")})
    assert code == 0


def test_allows_other_personal_data_file(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Read", {"file_path": str(tmp_path / "personal" / "data" / "practice-log.md")})
    assert code == 0


def test_allows_grep_for_the_word_in_one_named_file(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": "grep -c alcove data/workbooks/notes.md"})
    assert code == 0


def test_allows_the_bare_word_as_a_recursive_grep_pattern(tmp_path):
    # In command text the name counts only when path-shaped: it is an ordinary English
    # word, and a replay over 282 real transcripts found about a dozen bare-word blocks.
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": 'grep -ri "alcove" data/'})
    assert code == 0


def test_allows_the_word_inside_sed_replacement_text(tmp_path):
    # A title scrub, the shape a real replay used. Known limitation:
    # with `/` as the sed delimiter right after the word (`s/... alcove/.../`), the word
    # looks path-shaped and blocks; use another delimiter such as `|`.
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": "sed -i '' 's/\"title\": \"Acme alcove group\",/\"title\": \"Weekly\",/' tools/x.py"})
    assert code == 0


def test_blocks_listing_the_vault_directory(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": f"ls {tmp_path}/personal/data/alcove/"})
    assert code == 2


def test_allows_the_word_in_echoed_prose(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": 'echo "alcove themes are personal"'})
    assert code == 0


def test_override_allows_intentional_read(tmp_path):
    # Paired with the same read unoverridden, so this fails if the vault branch goes:
    # alone, an exit 0 here would also hold for a guard that never knew the vault.
    root, vault = _layout(tmp_path)
    target = {"file_path": str(vault / "2026-01-01-note.md")}
    assert _run(root, "Read", target)[0] == 2
    assert _run(root, "Read", target, env_extra={"SEAL_READ_OK": "1"})[0] == 0


# --- the vault's parents (cross-model review 2026-10-27, Codex F1, Grok F1-F3) ---
# The allow tests above are restraint tests: they would also pass on a guard with no
# vault branch, and are not evidence the branch exists. The block tests are.

def test_blocks_grep_and_glob_aimed_at_a_parent(tmp_path):
    root, _ = _layout(tmp_path)
    for parent in ("../personal/data", "../personal", str(tmp_path / "personal" / "data")):
        assert _run(root, "Grep", {"pattern": "x", "path": parent})[0] == 2, parent
        assert _run(root, "Glob", {"pattern": "**/*.md", "path": parent})[0] == 2, parent


def test_blocks_glob_whose_pattern_starts_in_a_parent(tmp_path):
    root, _ = _layout(tmp_path)
    assert _run(root, "Glob", {"pattern": "../personal/data/**/*.md"})[0] == 2


def test_blocks_bash_recursion_through_a_parent(tmp_path):
    root, _ = _layout(tmp_path)
    for cmd in ("grep -r x ../personal/data",
                "find ../personal/data -type f -maxdepth 3",
                "ls -R ../personal/data",
                "grep -r alcove/ ../personal",
                "rg x ../personal/",
                f"grep -rn x {tmp_path}/personal/data"):
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


def test_blocks_cd_into_a_parent_then_a_bare_name(tmp_path):
    root, _ = _layout(tmp_path)
    for cmd in ("cd ../personal/data && ls alcove",
                "cd ../personal/data && grep -r x alcove",
                f"cd {tmp_path}/personal && grep -r x data"):
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


def test_blocks_a_parent_reached_after_cd_to_its_own_parent(tmp_path):
    # Grok round 2 F1: words resolve against every directory the call cds to.
    root, _ = _layout(tmp_path)
    for cmd in ("cd .. && grep -r x personal/data",
                "cd .. && ls -R personal",
                "cd .. && find personal/data -type f",
                "cd tools && grep -r x ../../personal/data",
                f"cd {tmp_path} && ls -R personal"):
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


def test_blocks_a_parent_inside_a_wrapper_or_variable(tmp_path):
    # Grok round 2 F2: the parent sits in the shared name regex, so the in-repo handling
    # of bash -c, python -c, heredocs and assignments applies to it.
    root, _ = _layout(tmp_path)
    for cmd in ("bash -c 'grep -r x ../personal/data'",
                "python3 -c \"from pathlib import Path; print(list(Path('../personal/data').rglob('*')))\"",
                "python3 - <<'PY'\nfrom pathlib import Path\nprint(list(Path('../personal/data').rglob('*')))\nPY",
                'D=../personal/data; grep -r x "$D"'):
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


def test_allows_other_folders_and_words_named_personal(tmp_path):
    root, _ = _layout(tmp_path)
    for cmd in ("ls ../personal/output/house-project",
                "cat ../personal/CLAUDE.md",
                "PYTHONIOENCODING=utf-8 python3 tools/personal_todo_write.py list",
                "python3 tools/gmail_fetch.py --personal --repo-root .",
                'git commit -m "personal/data line in the guard"',
                "grep -rn personal data/",
                "ls personal"):
        assert _run(root, "Bash", {"command": cmd})[0] == 0, cmd


def test_personal_repo_work_passes_but_a_walk_of_its_root_blocks(tmp_path):
    # Round 4 (restraint replay): blocking every mention of the personal repo root changed
    # 64 real calls, most of them ordinary work there. The root counts only for a command
    # that walks it; a walk of another folder there, git, or a named file passes.
    root, _ = _layout(tmp_path)
    for cmd in ("cd ../personal && git status --short",
                "cd ../personal && grep -rn x output/house-project",
                "cd ../personal && find output -name '*.md'",
                "cd ../personal && fd notes output",
                "cd ../personal && fd -e md notes output",
                "cd ../personal && fd --type f -E data notes output",
                "cd ../personal && fd notes --search-path output",
                "cd ../personal && fd notes output -x cat",
                "cd -- ../personal && git status",
                "pushd -q ../personal && ls data",
                "rg --files ../personal/output",
                "bash -c 'cd ../personal && git status'",
                "bash -c",
                "eval",
                "bash --login script.sh",
                "find -L ../personal/output -name x",
                "tar -xf archive.tar -C ../personal/output",
                "tar -tf ../personal/backup.tar",
                "cp ../personal/CLAUDE.md /tmp/x",
                "cp -R ../personal/output /tmp/x",
                "rsync -a ../personal/output /tmp/x",
                "find -s ../personal/output",
                "echo ../personal && grep -r x .",       # only cd/pushd move the cwd
                "cd ../personal && fd notes output -x ls .",
                "cd ../personal && ls data",
                "cd ../personal && cat data/practice-log.md",
                "ls ../personal"):
        assert _run(root, "Bash", {"command": cmd})[0] == 0, cmd
    for cmd in ("cd ../personal && grep -r x .",
                "cd ../personal && grep -rl x",
                "cd ../personal && find . -name '*.md'",
                "cd ../personal && du -sh *",
                "cd ../personal && fd secret",          # Codex round 3 F1
                "cd ../personal && fd -e md secret",    # Codex round 4 F1
                "cd ../personal && fd --extension=md -t f secret",
                "cd ../personal && fd -H -e md secret",
                "fd -e md secret --search-path ../personal",
                "fd secret --base-directory=../personal",
                "cd -- ../personal && fd secret",       # Codex round 4 F2
                "pushd -q ../personal && fd secret",
                "rg --files ../personal",               # Grok round 4 F1
                "cd ../personal && rg --files",
                "bash -c 'grep -r x ../personal'",      # Grok round 4 F2
                "zsh -lc 'cd ../personal && fd secret'",
                'sh -c "ls -R ../personal"',
                "eval 'grep -r x ../personal'",
                "find -L ../personal -name x",          # Grok round 4 F3
                "find -H -O2 ../personal",
                "find -D tree ../personal",
                "find -- ../personal -type f",          # Codex final-r3-g2 F1
                "find -L -- ../personal -name x",
                "bash --login -c 'grep -r x ../personal'",   # round 5 F1, both models
                "bash -o errexit -c 'grep -r x ../personal'",
                "zsh --norc -c 'fd secret ../personal'",
                "tar -cf /tmp/p.tar ../personal",           # Codex round 5 F2
                "tar --create -f /tmp/p.tar ../personal",
                "cp -R ../personal /tmp/x",
                "rsync -a ../personal /tmp/x",
                "zip -r /tmp/p.zip ../personal",
                "find -s ../personal",                      # Grok round 5 F2
                "fd --search-path ../personal secret",
                "find -E ../personal -name x",
                "find -f ../personal",
                "cd -P -- ../personal && grep -r x .",
                "fd secret ../personal",
                "ls -R ../personal",
                "cd ../personal/data && ls alcove",
                "cd ../personal && cat data/alcove/a.md"):
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


def test_only_recursive_flags_make_a_walk(tmp_path):
    # Mutation survivors 2026-10-28: which flags count was not pinned.
    root, _ = _layout(tmp_path)
    for cmd in ("ls -lr ../personal",            # -r is reverse sort for ls, not recursion
                "ls -la ../personal",
                "ls --color ../personal",
                "grep --color x ../personal",
                "grep -c x ../personal",
                "chmod -R u+r ../personal"):     # -R on a command that opens no file
        assert _run(root, "Bash", {"command": cmd})[0] == 0, cmd
    for cmd in ("ls -lR ../personal",
                "ls --recursive ../personal",
                "grep --recursive x ../personal",
                "grep --directories=recurse x ../personal",
                "grep -Rn x ../personal",
                "tree ../personal"):
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


def test_an_assignment_only_command_does_not_end_the_check(tmp_path):
    # A command with no command word (`X=1`) must be judged, not crash the hook: a crash
    # exits as "allow" for the whole call, so the read after it would pass.
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": "X=1; cat ../personal/data/alcove/a.md"})[0] == 2
    assert _run(root, "Bash", {"command": "X=1; grep -r x ../personal"})[0] == 2
    assert _run(root, "Bash", {"command": "X=1; echo ok"})[0] == 0


def test_a_cd_reached_in_two_steps_is_tracked(tmp_path):
    # Neither word spells a parent the regex knows; only the cd tracking sees that the
    # second cd lands in personal/data, so the bare vault name then resolves into it.
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": "cd ../personal && cd data && ls alcove"})[0] == 2
    assert _run(root, "Bash", {"command": "cd ../personal && cd data && ls"})[0] == 0


def test_a_glob_that_starts_at_the_personal_root_blocks_but_a_grep_filter_does_not(tmp_path):
    root, _ = _layout(tmp_path)
    assert _run(root, "Glob", {"pattern": "../personal/**/notes.md"})[0] == 2
    # Grep's `glob` filters file names inside `path`; it is not a place to search.
    assert _run(root, "Grep", {"pattern": "x", "path": "data", "glob": "../personal/*.md"})[0] == 0


def test_allows_a_named_file_beside_the_vault_in_bash(tmp_path):
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": "cat ../personal/data/practice-log.md"})[0] == 0
    assert _run(root, "Bash", {"command": "grep -c x ../personal/data/practice-log.md"})[0] == 0


# --- helpers, in process (mutation survivors 2026-10-28) ---------------------

sys.path.insert(0, str(SCRIPT.parent))
import check_sealed_read as csr  # noqa: E402
from shell_tokens import simple_commands  # noqa: E402


def test_walks_reads_tar_and_copy_flags_exactly():
    W = lambda *w: csr._walks(list(w), 0)
    assert W("tar", "-cf", "x", "d") and W("tar", "-czf", "x", "d") and W("tar", "--create")
    assert not W("tar", "-tf", "x") and not W("tar", "--checkpoint", "-xf", "x")
    assert W("cp", "-R", "a", "b") and W("cp", "-a", "a", "b") and W("rsync", "--archive")
    assert W("zip", "--recursive") and W("scp", "-r", "a", "b")
    assert not W("cp", "a", "b") and not W("cp", "--preserve", "a", "b")
    assert not W("cp", "-v", "a", "b")


def test_cd_alone_or_with_only_options_names_no_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAL_REPO_ROOT", str(tmp_path))
    csr._CWDS.clear()
    csr._note_cds(simple_commands("cd; cd -P; pushd -q; cd -"))
    assert csr._CWDS == []
    csr._note_cds(simple_commands("cd -P -- sub"))
    assert csr._CWDS == [os.path.realpath(str(tmp_path / "sub"))]
    csr._CWDS.clear()


def test_shell_wrapper_script_is_the_word_after_the_c_cluster():
    S = lambda cmd: csr._wrapped_scripts(simple_commands(cmd)[0])
    assert S("bash -lc 'ls -R x'") == ["ls -R x"]
    assert S("bash --login -o errexit -c 'ls x'") == ["ls x"]
    assert S("bash -c") == [] and S("bash script.sh") == [] and S("python3 -c 'x'") == []
    assert S("eval ls -R x") == ["ls -R x"] and S("eval") == []


# --- no config: fail closed (2026-11-01) -------------------------------------
# Without tools/.sealed-folders.conf the guard cannot tell which vault folder is sealed,
# so every folder under the vault's data/ counts as sealed. Each test pairs the read with
# the same read under a config, so it fails if the fail-closed branch is removed.

def test_without_config_any_file_under_the_vault_data_blocks(tmp_path):
    root, _ = _layout(tmp_path, configured=False)
    beside = {"file_path": str(tmp_path / "personal" / "data" / "practice-log.md")}
    assert _run(root, "Read", beside)[0] == 2
    root2, _ = _layout(tmp_path / "b")
    beside2 = {"file_path": str(tmp_path / "b" / "personal" / "data" / "practice-log.md")}
    assert _run(root2, "Read", beside2)[0] == 0


def test_without_config_bash_reads_under_the_vault_data_block(tmp_path):
    root, _ = _layout(tmp_path, configured=False)
    for cmd in ("cat ../personal/data/practice-log.md",
                "cat ../personal/data/other/notes.md",
                "cd ../personal && cat data/practice-log.md"):
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd
    root2, _ = _layout(tmp_path / "b")
    assert _run(root2, "Bash", {"command": "cat ../personal/data/practice-log.md"})[0] == 0


def test_without_config_the_personal_root_outside_data_still_passes(tmp_path):
    root, _ = _layout(tmp_path, configured=False)
    for cmd in ("cat ../personal/CLAUDE.md", "ls ../personal/output/house-project"):
        assert _run(root, "Bash", {"command": cmd})[0] == 0, cmd


def test_a_malformed_config_fails_closed(tmp_path):
    root, _ = _layout(tmp_path)
    (root / "tools" / ".sealed-folders.conf").write_text("../outside\n")
    beside = {"file_path": str(tmp_path / "personal" / "data" / "practice-log.md")}
    assert _run(root, "Read", beside)[0] == 2


def test_a_second_configured_folder_is_sealed_too(tmp_path):
    root, _ = _layout(tmp_path)
    (root / "tools" / ".sealed-folders.conf").write_text("data/alcove\ndata/other\n")
    other = tmp_path / "personal" / "data" / "other"
    other.mkdir()
    assert _run(root, "Read", {"file_path": str(other / "a.md")})[0] == 2
    assert _run(root, "Bash", {"command": "cat ../personal/data/other/a.md"})[0] == 2
    assert _run(root, "Bash", {"command": "cat ../personal/data/practice-log.md"})[0] == 0


# --- the folder named inside text the path checks cannot resolve (mutation survivors,
# 2026-11-02). The old guard spelled the name in one regex literal, which had no mutation
# points; built from config, each alternative must be needed by a test.

_IN_TEXT = [
    "python3 -c \"print(open('../personal/data/alcove/a.md').read())\"",
    "python3 - <<'PY'\nprint(open('../personal/data/alcove/a.md').read())\nPY",
    'D=../personal/data/alcove; cat "$D/a.md"',
    "grep -r x alcove/",
    "xargs cat <<< ../personal/data/alcove/a.md",
    "python3 -c \"import os; print(os.listdir('../personal/data/alcove'))\"",
]


def test_a_configured_folder_named_inside_code_or_a_variable_blocks(tmp_path):
    root, _ = _layout(tmp_path)
    for cmd in _IN_TEXT:
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


def test_without_config_a_vault_file_named_inside_code_blocks(tmp_path):
    root, _ = _layout(tmp_path, configured=False)
    for cmd in ("python3 -c \"print(open('../personal/data/practice-log.md').read())\"",
                "python3 - <<'PY'\nprint(open('../personal/data/practice-log.md').read())\nPY"):
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd
    root2, _ = _layout(tmp_path / "b")
    assert _run(root2, "Bash", {"command": "python3 -c \"print(open('../personal/data/practice-log.md').read())\""})[0] == 0


def test_a_bare_word_without_a_slash_is_not_read_as_the_top_level_folder(tmp_path):
    # Renamed from "...does not seal the same name elsewhere" (Fable r1 F4): with a slash,
    # `alcove/` anywhere in command text IS blocked, as the old guard's literal was.
    root, _ = _layout(tmp_path)
    (root / "tools" / ".sealed-folders.conf").write_text("alcove\n")
    (tmp_path / "personal" / "alcove").mkdir()
    assert _run(root, "Bash", {"command": "cat ../personal/alcove/a.md"})[0] == 2
    assert _run(root, "Bash", {"command": "python3 -c \"print(open('../other/alcove').read())\""})[0] == 0


# --- review round 1 (Fable 7.F1, 6.F2/7.F2/10.F1, 6.F1/7.F3; Codex g1b F1), 2026-11-02 ---

def test_in_a_worktree_the_main_checkout_anchors_the_vault_and_config(tmp_path):
    """A linked worktree has a .git FILE naming the main checkout's .git/worktrees/<name>.
    The vault and the config are found from the main checkout, not beside the worktree."""
    main, vault = _layout(tmp_path)
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    wt = tmp_path / "caches" / "wt"
    (wt / "tools").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")
    assert _run(wt, "Read", {"file_path": str(vault / "2026-01-01-note.md")})[0] == 2
    # Configured from the main checkout: a vault file outside the sealed folder passes.
    beside = {"file_path": str(tmp_path / "personal" / "data" / "practice-log.md")}
    assert _run(wt, "Read", beside)[0] == 0


def test_a_configured_folder_that_is_not_a_directory_keeps_fail_closed_on(tmp_path):
    root, vault = _layout(tmp_path)
    (root / "tools" / ".sealed-folders.conf").write_text("data/sesions\n")    # a typo
    assert _run(root, "Read", {"file_path": str(vault / "2026-01-01-note.md")})[0] == 2
    beside = {"file_path": str(tmp_path / "personal" / "data" / "practice-log.md")}
    assert _run(root, "Read", beside)[0] == 2


def test_a_bom_or_trailing_comment_in_the_config_still_names_the_folder(tmp_path):
    for text in ("\ufeffdata/alcove\n", "data/alcove   # the sealed one\n"):
        root, vault = _layout(tmp_path / str(len(text)))
        (root / "tools" / ".sealed-folders.conf").write_text(text, encoding="utf-8")
        assert _run(root, "Read", {"file_path": str(vault / "2026-01-01-note.md")})[0] == 2
        beside = {"file_path": str(tmp_path / str(len(text)) / "personal" / "data" / "x.md")}
        assert _run(root, "Read", beside)[0] == 0, repr(text)    # configured, not fail closed


def test_an_absolute_or_escaping_or_missing_config_folder_fails_closed_at_the_guard(tmp_path):
    # data/../sesions normalizes to an in-vault folder that does not exist: fail closed
    # because it is missing, not because it escapes (Fable r2 F2).
    for i, text in enumerate(("/abs/path\n", "data/../../x\n", "data/../sesions\n")):
        root, _ = _layout(tmp_path / str(i))
        (root / "tools" / ".sealed-folders.conf").write_text(text)
        beside = {"file_path": str(tmp_path / str(i) / "personal" / "data" / "practice-log.md")}
        code, err = _run(root, "Read", beside)
        assert code == 2, (text, err)


def test_without_config_a_quoted_vault_path_handed_to_a_repo_tool_blocks(tmp_path):
    cmd = 'python3 tools/x.py "see ../personal/data/alcove/a.md"'
    root, _ = _layout(tmp_path, configured=False)
    (root / "tools" / "x.py").write_text("")
    assert _run(root, "Bash", {"command": cmd})[0] == 2
    root2, _ = _layout(tmp_path / "b")
    (root2 / "tools" / "x.py").write_text("")
    assert _run(root2, "Bash", {"command": cmd})[0] == 2      # configured mode blocked it too


# --- review round 2 (Fable 16.F1, 20.F4; 16.F7/17.F3; 17.F4) -------------------------------

def _wt_layout(tmp_path):
    main, vault = _layout(tmp_path)
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    wt = tmp_path / "caches" / "wt"
    (wt / "tools").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")
    return main, wt, vault


def _run_in(root, cwd, command):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                          "cwd": str(cwd)})
    env = {**os.environ, "SEAL_REPO_ROOT": str(root)}
    env.pop("SEAL_READ_OK", None)
    return subprocess.run([sys.executable, str(SCRIPT)], input=payload, capture_output=True,
                          text=True, env=env).returncode


def test_a_relative_read_is_judged_from_where_the_call_runs(tmp_path):
    main, wt, _ = _wt_layout(tmp_path)
    # The session already sits in the vault's data/ (a parent of the sealed folder) and the
    # command names nothing: `.` is judged from the call's cwd, not the checkout.
    data = tmp_path / "personal" / "data"
    assert _run_in(wt, data, "ls -R .") == 2
    assert _run_in(wt, wt, "ls -R .") == 0                   # the worktree itself is fine


def test_an_unreadable_worktree_git_file_does_not_crash_the_guard(tmp_path):
    main, wt, vault = _wt_layout(tmp_path)
    (wt / ".git").write_bytes(b"gitdir: \xff\xfe\n")
    code, _ = _run(wt, "Read", {"file_path": str(vault / "2026-01-01-note.md")})
    assert code in (0, 2)                     # a verdict, never a crash (exit 1 = allow)


def test_the_vault_location_comes_from_the_configured_vault_root(tmp_path):
    """The writer finds the vault through tools/.personal-vault.conf; the guard must protect
    that same folder, not a fixed name beside the checkout (Fable r2 17.F1)."""
    root = tmp_path / "job-search"
    (root / "tools").mkdir(parents=True)
    vault = tmp_path / "pv"
    sealed = vault / "data" / "alcove"
    sealed.mkdir(parents=True)
    (sealed / "a.md").write_text("x")
    (root / "tools" / ".personal-vault.conf").write_text(f"{vault}\n")
    (root / "tools" / ".sealed-folders.conf").write_text("data/alcove\n")
    assert _run(root, "Read", {"file_path": str(sealed / "a.md")})[0] == 2
    assert _run(root, "Read", {"file_path": str(vault / "data" / "other.md")})[0] == 0



# --- Codex round 3 (2026-11-03), two P0s fixed 2026-11-04 with Nick's guard-edit approval ---

import pytest  # noqa: E402


@pytest.mark.parametrize("cmd", [
    # git reads the vault's tree or history: the walk is the repo git runs in.
    "git -C ../personal grep x",
    "git -C ../personal log -p",
    "git -C ../personal log --patch -- data",
    "git -C ../personal show HEAD",
    "git -C ../personal archive HEAD",
    "git -C ../personal ls-files",
    "cd ../personal && git grep x",
    "git --work-tree=../personal grep x",
    "git --git-dir=../personal/.git log -p",
    "git -C ../personal/data log -p",
])
def test_git_reading_the_vault_tree_or_history_blocks(tmp_path, cmd):
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


@pytest.mark.parametrize("cmd", [
    "git -C ../personal status --short",
    "git -C ../personal log --oneline -5",
    "cd ../personal && git status",
    "git grep x",
    "git log -p -- tools/x.py",
])
def test_git_work_that_reads_no_vault_content_still_passes(tmp_path, cmd):
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": cmd})[0] == 0, cmd


def test_the_vault_location_honours_PERSONAL_VAULT_ROOT_like_the_writer(tmp_path):
    """vault_paths.vault_root() lets PERSONAL_VAULT_ROOT beat the config file, so a writer
    run with it set writes there; the guard must protect the same folder."""
    root = tmp_path / "job-search"
    (root / "tools").mkdir(parents=True)
    vault = tmp_path / "pv2"
    sealed = vault / "data" / "alcove"
    sealed.mkdir(parents=True)
    (sealed / "a.md").write_text("x")
    (root / "tools" / ".sealed-folders.conf").write_text("data/alcove\n")
    env = {"PERSONAL_VAULT_ROOT": str(vault)}
    assert _run(root, "Read", {"file_path": str(sealed / "a.md")}, env)[0] == 2
    assert _run(root, "Bash", {"command": "cat ../pv2/data/alcove/a.md"}, env)[0] == 2
    assert _run(root, "Read", {"file_path": str(vault / "data" / "other.md")}, env)[0] == 0


# Mutation survivors on the 2026-11-04 git branch (handoff/mut-1104-check_sealed_read.txt).
@pytest.mark.parametrize("cmd", [
    "git --no-pager -C ../personal grep x",        # a flag global before the subcommand
    "git -C ../personal diff",                      # diff prints content by default
    "git -C ../personal diff HEAD~1 -- data",
    "git -C ../personal log -Mp",                   # p inside a short-flag cluster
])
def test_git_content_reads_found_past_flags_and_by_default(tmp_path, cmd):
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


@pytest.mark.parametrize("cmd", [
    "git -C ../personal diff --stat",
    "git -C ../personal diff --stat=200",           # the =value form of a quiet flag
    "git -C ../personal diff --name-only HEAD~1",
    "git -C ../personal status -u",                 # -u means a patch only for log-like commands
    "git -C ../personal log -M",
    "git --namespace=../personal grep x",           # a value global that is not a directory
    "git -C ../personal",                           # no subcommand at all
    "git",
])
def test_git_without_content_or_without_a_vault_dir_passes(tmp_path, cmd):
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": cmd})[0] == 0, cmd


# Codex round 4 (2026-11-04) on d37d8370: 14 P0s, three classes.
@pytest.mark.parametrize("cmd", [
    # 1. content-printing modes of subcommands outside the old content list (fail closed)
    "git -C ../personal status -v",
    "git -C ../personal status --verbose",
    "git -C ../personal commit --dry-run -v",
    "git -C ../personal diff-files -p",
    "git -C ../personal diff-index -p HEAD",
    "git -C ../personal diff-tree -p HEAD",
    "git -C ../personal range-diff HEAD~2 HEAD~1 HEAD",
    "git -C ../personal whatever-new-subcommand",
    # 2. a patch flag wins over a summary flag
    "git -C ../personal diff --stat -p",
    "git -C ../personal diff --stat --patch",
    "git -C ../personal diff --name-only -U3",
    "git -C ../personal log --patch-with-stat",
    "git -C ../personal log --stat -p",
    "git -C ../personal show --stat",
    # 3. relative --git-dir / --work-tree are read from the -C directory
    "git -C .. --work-tree=personal grep x",
    "git -C .. --git-dir=personal/.git log -p",
])
def test_round4_git_reads_of_the_vault_block(tmp_path, cmd):
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


@pytest.mark.parametrize("cmd", [
    "git -C ../personal status",
    "git -C ../personal status --short --branch",
    "git -C ../personal log --oneline --stat",
    "git -C ../personal log --name-only -5",
    "git -C ../personal diff --stat",
    "git -C ../personal branch -a",
    "git -C ../personal rev-parse HEAD",
    "git -C ../personal remote -v",
    "git -C ../personal fetch",
    "git -C ../personal add data/x.md",
    "git -C ../personal commit -m msg",
    "git -C ../personal push",
    "git -C ../personal stash list",
    "git -C ../personal worktree list",
])
def test_round4_git_work_that_prints_no_file_content_passes(tmp_path, cmd):
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": cmd})[0] == 0, cmd


@pytest.mark.parametrize("cmd", [
    # Codex r5 61.F1/66.F1 (P0): git config reading a blob or file prints its content
    # A blob named by its hash names no sealed path, so only the subcommand rule can see it.
    "git -C ../personal config --blob 3f2a9c1e --list",
    "git -C ../personal config --blob=3f2a9c1e --list",
    "git -C ../personal config --get-regexp . --blob 3f2a9c1e",
    "git -C ../personal config --file .git/info/notes --list",
    "git -C ../personal config -f .git/info/notes --list",
])
def test_round5_git_config_reading_a_file_blocks(tmp_path, cmd):
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


def test_round5_plain_git_config_still_passes(tmp_path):
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": "git -C ../personal config user.name"})[0] == 0
