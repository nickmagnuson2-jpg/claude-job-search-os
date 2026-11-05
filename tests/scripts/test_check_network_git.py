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
