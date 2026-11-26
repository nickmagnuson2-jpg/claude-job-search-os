"""check_network_git.py: a network git command typed by Claude is blocked in any form.

Every GitHub remote here is SSH through a Touch ID key, so a fetch, pull, push or
ls-remote from a Claude Bash call raises a prompt Nick did not start (fired twice,
feedback_no_network_git_without_checking_touch_id_rule). The settings deny-list matches
only the bare prefix, so `git -C x fetch`, `cd x && git pull` and `bash -c '...'` got
past it. Nick, 2026-11-05: direct git only (scripts exempt), no Claude override; a fetch
he wants, he runs himself with `!`.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "check_network_git.py"


def _run(command: str, tool: str = "Bash"):
    payload = {"tool_name": tool, "tool_input": {"command": command}}
    r = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stderr


@pytest.mark.parametrize("cmd", [
    "git fetch",
    "git fetch origin main",
    "git pull --ff-only",
    "git push -u origin feature",
    "git ls-remote origin",
    "git clone git@github.com:owner/repo.git",
    "git remote update",
    "git remote prune origin",
    "git remote show origin",
    "git submodule update --init",
    "git fetch-pack origin",
    "git send-pack origin main",
    # forms the prefix deny-list misses
    "git -C ../other fetch",
    "git -C ~/Library/x push origin x",
    "git --no-pager fetch",
    "git -c core.sshCommand=ssh fetch",
    "git --git-dir=/tmp/x.git fetch",
    "GIT_DIR=$HOME/.other-git git push origin master",
    "env GIT_TERMINAL_PROMPT=0 git fetch",
    "timeout 60 git fetch -q origin",
    "time -p git push origin x",                  # time's own option (prior-art r2 F1 sibling)
    "time -- git fetch",
    "time -o /tmp/t git push origin x",           # time(1) -o FILE (E1 r3 F1, Codex + Grok)
    "/usr/bin/time -o /tmp/t git push origin x",
    "time --output /tmp/t git fetch",
    "time -f %e git push origin x",              # GNU time -f FORMAT
    "time -a -o /tmp/t git push origin x",
    "env -P /usr/bin git push origin x",          # BSD env -P altpath (E1 r3 F2, Grok)
    "caffeinate -s git push origin x",            # caffeinate -s is a flag (timeout's -s takes a value)
    "command git pull",
    "/usr/bin/git fetch",
    "cd ../repo && git pull",
    "ls; git fetch",
    "true || git push",
    "echo x | git push",
    "(cd x && git fetch)",
    "bash -c 'git fetch origin'",
    "sh -c \"git pull\"",
    "zsh -c 'cd x; git ls-remote origin'",
    "eval 'git fetch'",
    "echo $(git ls-remote origin)",
    "x=`git ls-remote origin`",
    "for r in a b; do git -C $r fetch; done",
    "if git fetch; then echo ok; fi",
])
def test_network_git_blocks(cmd):
    code, err = _run(cmd)
    assert code == 2, cmd
    assert "Touch ID" in err and "!" in err, err


@pytest.mark.parametrize("cmd", [
    "git status",
    "git log --oneline origin/main..HEAD",
    "git diff origin/main...HEAD",
    "git merge-tree --write-tree origin/main HEAD",
    "git remote -v",
    "git remote get-url origin",
    "git config remote.origin.url",
    "git branch -r",
    "git worktree add ../x -b x origin/main",
    "git commit -m 'never git push from a session'",
    "git commit -F - <<'EOF'\nfix: do not git fetch here\nEOF",
    "grep -n 'git fetch' tools/x.py",
    "echo \"git push is denied\"",
    "printf '%s' 'cd x && git push -u origin x' | pbcopy",
    "rg 'git (fetch|pull)' docs/",
    "gh api repos/o/r/git/matching-refs/heads --jq '.[].ref'",
    "bash tools/backup-data.sh",
    "python3 tools/codex_verify.py --target 'git push gate'",
    "cat .git/config",
    "gitk --all",
    "git-lfs version",
    "echo git fetch",
])
def test_ordinary_git_and_mentions_pass(cmd):
    code, err = _run(cmd)
    assert code == 0, (cmd, err)


def test_a_non_bash_tool_passes():
    assert _run("git fetch", tool="Read")[0] == 0


def test_an_unreadable_payload_fails_open():
    r = subprocess.run([sys.executable, str(SCRIPT)], input="not json",
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0


def test_the_message_names_the_blocked_command():
    code, err = _run("git -C ../repo fetch origin")
    assert code == 2 and "fetch" in err


# Restraint replay over 43,481 real Bash commands (2026-11-05): three no-prompt shapes.
@pytest.mark.parametrize("cmd", [
    "git ls-remote --help",                                   # prints help, no network
    "git fetch -h",
    "git fetch -q https://github.com/owner/repo.git",         # HTTPS: no SSH key, no Touch ID
    "git fetch https://github.com/owner/repo.git +main:refs/remotes/origin/main",
    "git ls-remote https://github.com/owner/repo.git refs/heads/main",
    "git -C ../x ls-remote https://github.com/owner/repo.git",
    "git clone -q /Users/someone/Documents/repo /tmp/copy",   # a local path, no network
    "git clone ./local-repo dest",
    "git clone ~/code/repo",
])
def test_no_prompt_shapes_pass(cmd):
    code, err = _run(cmd)
    assert code == 0, (cmd, err)


@pytest.mark.parametrize("cmd", [
    "git push https://github.com/owner/repo.git main",        # a push is always blocked
    "git fetch git@github.com:owner/repo.git",                # SSH remote
    "git clone ssh://git@github.com/owner/repo.git",
    "git ls-remote origin",                                    # a named remote may be SSH
    "git fetch origin https://github.com/owner/repo.git",     # first positional is the remote
])
def test_prompting_shapes_still_block(cmd):
    assert _run(cmd)[0] == 2, cmd


# Mutation survivors, 2026-11-05 (output/guards-1105/mut-check_network_git.txt).
@pytest.mark.parametrize("cmd", [
    "FOO=1 BAR=2 git fetch",                      # more than one leading assignment
    "if FOO=1 git fetch; then :; fi",             # keyword, then an assignment
    "timeout 60 env X=1 git fetch",               # one wrapper after another
    "nice -n 5 git fetch",                        # a wrapper option that takes a value
    "env -u HOME git push origin x",
    "timeout -s KILL 60 git pull",
    "bash -c 'echo hi' && git fetch",             # a clean command before the bad one
    "eval 'echo hi'; git push",
    "echo $(date) && git fetch",
    "bash -c 'echo $(date)'; git ls-remote origin",
])
def test_survivor_shapes_block(cmd):
    assert _run(cmd)[0] == 2, cmd


@pytest.mark.parametrize("cmd", [
    "git clone --depth 1 https://github.com/owner/repo.git",   # a value flag before the URL
    "git fetch --depth 1 https://github.com/owner/repo.git",
    "git stash show -p",                                        # `show` of another subcommand
    "git notes prune",
    "git remote show -n origin",                                # -n: no network
    "bash tools/run.sh 'git fetch origin'",                     # an argument to a script
])
def test_survivor_shapes_pass(cmd):
    code, err = _run(cmd)
    assert code == 0, (cmd, err)


def test_importing_the_hook_does_not_run_it():
    """The replay and other tools import find_network_git; the module must not read stdin."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("cng_import_probe", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    spec.loader.exec_module(mod)            # raises SystemExit if main() ran on import
    assert mod.find_network_git("git fetch") and not mod.find_network_git("git status")


# Codex r1 (2026-11-05): F1/F2 over-blocks, F3 value-taking wrapper options.
@pytest.mark.parametrize("cmd", [
    "git --help fetch",                       # F1: a help flag before the subcommand
    "git -h fetch",
    "git clone local-repo dest",              # F2: a bare relative path is local
    "git clone repo",
    "git clone file:///Users/x/repo copy",
])
def test_codex_r1_no_network_shapes_pass(cmd):
    code, err = _run(cmd)
    assert code == 0, (cmd, err)


@pytest.mark.parametrize("cmd", [
    "env -C /tmp git fetch",                  # F3: wrapper options that take a value
    "caffeinate -t 5 git fetch",
    "nice --adjustment 5 git fetch",
    "nice --adjustment=5 git fetch",
    "git clone host:owner/repo.git",          # SSH shorthand stays blocked
    "git clone git://example.com/repo.git",
])
def test_codex_r1_network_shapes_block(cmd):
    assert _run(cmd)[0] == 2, cmd


# Codex r2 (2026-11-05): F1/F2 under-blocks, F3 deep nesting fails closed.
@pytest.mark.parametrize("cmd", [
    "env -S 'git push origin x'",                        # F1: -S carries a command line
    "env --split-string='git fetch origin'",
    "env -S 'FOO=1 git pull'",
    "git clone -c advice.detachedHead=false git@host:owner/repo.git",   # F2
    "git clone --template /tmp/t git@github.com:owner/repo.git",
    "git clone --unknown-option value host:repo",
])
def test_codex_r2_shapes_block(cmd):
    assert _run(cmd)[0] == 2, cmd


def test_nesting_deeper_than_the_bound_fails_closed():
    """F3: past MAX_DEPTH the hook cannot see what runs, so it blocks rather than pass."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("cng_depth_probe", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    spec.loader.exec_module(mod)
    deep = "echo hi"
    for _ in range(mod.MAX_DEPTH + 2):
        deep = "eval " + __import__("shlex").quote(deep)
    assert mod.find_network_git(deep)                   # blocks: too deep to judge
    shallow = "eval " + __import__("shlex").quote("echo hi")
    assert mod.find_network_git(shallow) is None


@pytest.mark.parametrize("cmd", [
    "git clone -c core.autocrlf=false local-repo dest",  # an option value with no ':' stays local
    "git clone --depth 1 ./repo copy",
])
def test_codex_r2_local_clones_still_pass(cmd):
    code, err = _run(cmd)
    assert code == 0, (cmd, err)


@pytest.mark.parametrize("cmd", [
    "env -S git fetch origin",            # the words after the -S value continue the command
    "env --split-string=git fetch",       # the = form: the value is in the same word
])
def test_words_after_the_split_string_value_continue_the_command(cmd):
    assert _run(cmd)[0] == 2, cmd     # mutation survivor, 2026-11-05 r3 chain


# Codex r3 (2026-11-05).
@pytest.mark.parametrize("cmd", [
    "env -S '-i git push origin x'",          # F1: the -S value starts with env options
    "env -S '-u HOME git fetch'",
    "env -S '--chdir=/tmp git pull'",
    "exec -a mygit git push origin x",         # F3: exec -a takes a value
])
def test_codex_r3_shapes_block(cmd):
    assert _run(cmd)[0] == 2, cmd


@pytest.mark.parametrize("cmd", [
    "command -v git",                          # F2: a lookup, not an execution
    "command -V git fetch",
    "command -v git && git status",
])
def test_codex_r3_lookups_pass(cmd):
    code, err = _run(cmd)
    assert code == 0, (cmd, err)


@pytest.mark.parametrize("cmd", [
    "command git push -v origin main",        # Codex r4 F1: git's own -v is not command -v
    "command git fetch -V",
])
def test_codex_r4_git_flags_are_not_command_lookup_flags(cmd):
    assert _run(cmd)[0] == 2, cmd


@pytest.mark.parametrize("cmd", [
    "env -S'git fetch origin'",               # Codex r5 F1: value attached to -S
    "env -iS'git push origin x'",             # -S inside a short-flag cluster
    "env -iS 'git pull'",
])
def test_codex_r5_attached_split_string_blocks(cmd):
    assert _run(cmd)[0] == 2, cmd


# --- git stash (overnight-112526 S2, 2026-11-25) --------------------------------------
# feedback_dont_git_stash_for_a_before_after_check: 6 fires. The stash list is one stack
# shared by the main checkout and every worktree, and a stash taken for a red check has
# stashed the test it meant to run. Blocked in every form that changes the stack; list,
# show and create (writes an object, leaves the stack) pass. Restraint replay 2026-11-25:
# 81,536 distinct Bash commands, 86 blocked, 86 of 86 real stack-changing calls.

@pytest.mark.parametrize("cmd", [
    "git stash",
    "git stash -q",
    "git stash -u",
    "git stash push -q -- tools/foo.py",
    "git stash push -m 'before'",
    "git stash -m list",                 # a push whose message is "list"
    "git stash pop",
    "git stash apply stash@{0}",
    "git stash drop stash@{0}",
    "git stash clear",
    "git stash save wip",
    "git stash store -m x abc123",
    "git stash branch tmp",
    "git stash --keep-index",
    "git stash -- tools/foo.py",
    # forms a prefix deny-list misses, the brief's named shape first
    "git -C some/dir stash",
    "git -C ../wt stash pop -q",
    "cd ~/Library/Caches/claude-worktrees/x && git stash -q",
    "python3 -m pytest -q tests/x.py; git stash -q && python3 -m pytest -q tests/x.py",
    "bash -c 'git stash'",
    "echo $(git stash)",
    "env GIT_DIR=.git git stash",
    "time git stash pop",
])
def test_git_stash_that_changes_the_stack_blocks(cmd):
    code, err = _run(cmd)
    assert code == 2, f"{cmd!r} should block"
    assert "git stash" in err and "BLOCKED" in err


@pytest.mark.parametrize("cmd", [
    "git status",
    "git stash list",
    "git stash list | head -2",
    "git stash show",
    "git stash show -p stash@{0}",
    "git stash create",
    "git -C some/dir stash list",
    "git stash --help",
    "git stash -h",
    "git help stash",
    "git diff stash@{0} main -- tests/x.py",
    "echo 'do not git stash here'",
    "grep -n 'git stash' tools/*.py",
    'git commit -m "explain why git stash is blocked"',
    "cat > notes.md <<'EOF'\ngit stash\nEOF",
])
def test_stash_reads_and_mentions_pass(cmd):
    code, err = _run(cmd)
    assert code == 0, f"{cmd!r} should pass: {err}"


def test_stash_block_message_names_the_alternatives():
    code, err = _run("git stash -q")
    assert code == 2
    assert "cp " in err and "git show HEAD:" in err and "`!`" in err


def test_network_block_still_wins_over_stash():
    code, err = _run("git stash -q && git push origin x")
    assert code == 2 and "network git" in err


@pytest.mark.parametrize("args, expected", [
    (["stash"], "stash push"),
    (["stash", "-q"], "stash push"),
    (["stash", "pop"], "stash pop"),
    (["-C", "d", "stash", "drop"], "stash drop"),
    (["stash", "list"], None),
    (["stash", "show"], None),
    (["stash", "create"], None),
    (["stash", "--help"], None),
    (["stash", "pop", "-h"], None),
    (["status"], None),
    (["--help", "stash"], None),
    ([], None),
])
def test_git_stash_judge(args, expected):
    sys.path.insert(0, str(SCRIPT.parent))
    import check_network_git as cng
    assert cng._git_stash(args) == expected


@pytest.mark.parametrize("cmd", ["git stash -q", "git -C some/dir stash pop"])
def test_a_stash_block_does_not_fall_through_to_the_network_message(cmd):
    """Mutation 2026-11-25 (S2): dropping the stash branch's sys.exit(2) fell through to the
    network message and still exited 2, so only the text showed the difference."""
    code, err = _run(cmd)
    assert code == 2
    assert "network git" not in err and "None" not in err



# Codex S2 tip r1 F1 (parked 2.F1, fixed in S2c 2026-11-25): help counts only right after
# `stash` or right after its subcommand; anywhere else it may be an option's value.
@pytest.mark.parametrize("cmd", [
    "git stash push -m --help",
    "git stash push -m -h",
    "git stash -m --help",
    "git stash push --message --help",
    "git stash push -q -m -h -- tools/x.py",
])
def test_help_as_an_option_value_still_blocks(cmd):
    code, err = _run(cmd)
    assert code == 2, f"{cmd!r} should block"
    assert "git stash" in err


@pytest.mark.parametrize("cmd", [
    "git stash --help",
    "git stash -h",
    "git stash push --help",
    "git stash pop -h",
])
def test_help_in_first_position_still_passes(cmd):
    code, err = _run(cmd)
    assert code == 0, f"{cmd!r} should pass: {err}"
