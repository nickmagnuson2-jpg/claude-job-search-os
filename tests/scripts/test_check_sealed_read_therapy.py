"""Tests for the therapy-vault extension of check_sealed_read.py (2026-10-27).

`personal/data/therapy/` sits in the sibling personal repo and is labeled SEALED there
("same rule as job-search/data/project-background", personal/CLAUDE.md). A job-search
session must not read it. Nick approved the guard on 2026-10-27: "definitely fix the block
of reading the personal therapy files". The repo root is injected via SEAL_REPO_ROOT, so
the sibling is `<tmp>/../personal/data/therapy` and no real file is touched.
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


def _layout(tmp_path):
    root = tmp_path / "job-search"
    therapy = tmp_path / "personal" / "data" / "therapy"
    therapy.mkdir(parents=True)
    (therapy / "2026-01-01-session.md").write_text("x")
    (root / "data" / "workbooks").mkdir(parents=True)
    return root, therapy


# --- should BLOCK (exit 2) --------------------------------------------------

def test_blocks_read_of_therapy_file(tmp_path):
    root, therapy = _layout(tmp_path)
    code, err = _run(root, "Read", {"file_path": str(therapy / "2026-01-01-session.md")})
    assert code == 2
    assert "therapy" in err


def test_blocks_grep_into_therapy_dir(tmp_path):
    root, therapy = _layout(tmp_path)
    code, _ = _run(root, "Grep", {"pattern": "x", "path": str(therapy)})
    assert code == 2


def test_blocks_glob_into_therapy_dir(tmp_path):
    root, therapy = _layout(tmp_path)
    code, _ = _run(root, "Glob", {"pattern": "*.md", "path": str(therapy)})
    assert code == 2


def test_blocks_bash_cat_of_therapy_path(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": "cat ../personal/data/therapy/2026-01-01-session.md"})
    assert code == 2


def test_blocks_bash_cd_then_relative_read(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": "cd ../personal/data && cat therapy/2026-01-01-session.md"})
    assert code == 2


def test_blocks_symlink_into_therapy(tmp_path):
    root, therapy = _layout(tmp_path)
    link = root / "data" / "link-to-notes"
    link.symlink_to(therapy)
    code, _ = _run(root, "Read", {"file_path": str(link / "2026-01-01-session.md")})
    assert code == 2


# --- should ALLOW (exit 0) --------------------------------------------------

def test_allows_job_search_file_with_therapy_in_its_name(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Read", {"file_path": str(root / "data" / "workbooks" / "notes-on-therapy-topics.md")})
    assert code == 0


def test_allows_other_personal_data_file(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Read", {"file_path": str(tmp_path / "personal" / "data" / "practice-log.md")})
    assert code == 0


def test_allows_grep_for_the_word_in_one_named_file(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": "grep -c therapy data/workbooks/notes.md"})
    assert code == 0


def test_allows_the_bare_word_as_a_recursive_grep_pattern(tmp_path):
    # In command text the name counts only when path-shaped: it is an ordinary English
    # word, and a replay over 282 real transcripts found about a dozen bare-word blocks.
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": 'grep -ri "therapy" data/'})
    assert code == 0


def test_allows_the_word_inside_sed_replacement_text(tmp_path):
    # A title scrub, the shape a real replay used. Known limitation:
    # with `/` as the sed delimiter right after the word (`s/... therapy/.../`), the word
    # looks path-shaped and blocks; use another delimiter such as `|`.
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": "sed -i '' 's/\"title\": \"Acme therapy group\",/\"title\": \"Weekly\",/' tools/x.py"})
    assert code == 0


def test_blocks_listing_the_vault_directory(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": f"ls {tmp_path}/personal/data/therapy/"})
    assert code == 2


def test_allows_the_word_in_echoed_prose(tmp_path):
    root, _ = _layout(tmp_path)
    code, _ = _run(root, "Bash", {"command": 'echo "therapy themes are personal"'})
    assert code == 0


def test_override_allows_intentional_read(tmp_path):
    # Paired with the same read unoverridden, so this fails if the vault branch goes:
    # alone, an exit 0 here would also hold for a guard that never knew the vault.
    root, therapy = _layout(tmp_path)
    target = {"file_path": str(therapy / "2026-01-01-session.md")}
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
                "grep -r therapy/ ../personal",
                "rg x ../personal/",
                f"grep -rn x {tmp_path}/personal/data"):
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


def test_blocks_cd_into_a_parent_then_a_bare_name(tmp_path):
    root, _ = _layout(tmp_path)
    for cmd in ("cd ../personal/data && ls therapy",
                "cd ../personal/data && grep -r x therapy",
                f"cd {tmp_path}/personal && grep -r x data"):
        assert _run(root, "Bash", {"command": cmd})[0] == 2, cmd


def test_blocks_a_parent_reached_after_cd_to_its_own_parent(tmp_path):
    # Grok round 2 F1: words resolve against every directory the call cds to.
    root, _ = _layout(tmp_path)
    (root / "tools").mkdir()
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
                "cd ../personal/data && ls therapy",
                "cd ../personal && cat data/therapy/a.md"):
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
    assert _run(root, "Bash", {"command": "X=1; cat ../personal/data/therapy/a.md"})[0] == 2
    assert _run(root, "Bash", {"command": "X=1; grep -r x ../personal"})[0] == 2
    assert _run(root, "Bash", {"command": "X=1; echo ok"})[0] == 0


def test_a_cd_reached_in_two_steps_is_tracked(tmp_path):
    # Neither word spells a parent the regex knows; only the cd tracking sees that the
    # second cd lands in personal/data, so the bare vault name then resolves into it.
    root, _ = _layout(tmp_path)
    assert _run(root, "Bash", {"command": "cd ../personal && cd data && ls therapy"})[0] == 2
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
