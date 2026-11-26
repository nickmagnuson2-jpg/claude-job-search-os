#!/usr/bin/env python3
"""check_network_git.py -- PreToolUse hook for Bash. BLOCKS a network git command, and
a `git stash` that changes the stash list.

GIT STASH (added 2026-11-25, overnight-112526 S2). The stash list is one stack shared by the
main checkout and every linked worktree, so a stash pushed or popped by one session can take
or bury another session's entry, and a stash used for a red/green check has twice stashed the
very test it meant to run. memory/feedback_dont_git_stash_for_a_before_after_check.md: 6
fires against a reopen gate of 2. Blocked: `git stash` with no subcommand or with any
subcommand except `list`, `show` and `create` (none of them changes the stack), at command position in the same places the
network check looks. A `git stash` Nick wants, he runs himself with `!`. Instead: copy the
file aside (`cp f <scratch>; edit; test; cp <scratch> f; cmp`), `git show HEAD:<path>`, or a
second worktree.

Every GitHub remote on this machine is SSH through a Secretive key that asks for Touch ID
on each use. A `git fetch`, `pull`, `push` or `ls-remote` from a Claude Bash call raises a
prompt Nick did not start: either a surprise he has to deny, or one he approves without
knowing what for. Fired twice (2026-11-04 session 81, 2026-11-05 session f6), both from a
skill step that prescribed `git ls-remote`; see
memory/feedback_no_network_git_without_checking_touch_id_rule.md.

The settings deny-list (`Bash(git fetch:*)` and friends) matches only the bare prefix, so
`git -C <dir> fetch`, `cd x && git pull`, `bash -c 'git push'` and `$(git ls-remote)` got
past it. This hook finds `git` at COMMAND POSITION in every simple command of the line,
including inside `$(...)`, backticks, `bash -c` / `sh -c` / `zsh -c` strings and `eval`,
after leading `VAR=val` assignments, shell keywords (`if`, `do`, `then`, ...) and the
wrappers `env`, `command`, `exec`, `nice`, `nohup`, `time`, `timeout`, `caffeinate`.
Quoted text, grep patterns, commit messages and heredoc bodies are arguments, not
commands, so they pass (the tokenizer is tools/shell_tokens.py, shared with the PII hook).

Decisions (Nick, 2026-11-05, org session):
  - DIRECT git only. A script that pushes (tools/backup-data.sh, run by /checkout) is
    exempt: Nick starts /checkout, and he taps Touch ID for that push knowingly.
  - NO override for Claude. A fetch Nick wants, he runs himself with `!`, the way pushes
    already work. `!` commands do not pass through PreToolUse hooks.

Network subcommands: fetch, pull, push, ls-remote, clone, fetch-pack, send-pack;
`remote update|prune|show` (show without -n contacts the remote); `submodule update`.
Exempt, because they make no SSH connection (restraint replay, 2026-11-05): `--help`/`-h`;
fetch, pull, ls-remote or clone whose remote is an explicit `https://` or `file://` URL; a
clone from a local path (no `scheme://`, no `host:` prefix). A push is never exempt.

Not covered (named, not hidden): a command built at runtime (`$cmd fetch`), `xargs git
...`, `find -exec git ...`, git aliases, and any script file. A non-SSH remote would not
prompt, but the hook does not resolve remotes; it blocks by subcommand.

BLOCK tier (exit 2): a PreToolUse warning on exit 0 is never shown to the model.
Fails open (exit 0) on an unreadable payload, like the other Bash hooks.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
from shell_tokens import simple_commands  # noqa: E402

NETWORK_SUBCOMMANDS = {"fetch", "pull", "push", "ls-remote", "clone", "fetch-pack", "send-pack"}
REMOTE_NETWORK = {"update", "prune", "show"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
# `time` is NOT here: it is in WRAPPERS, whose branch also consumes its own `-p`/`--`.
# As a keyword it was skipped alone and `time -p git push` passed (2026-11-16, found
# while fixing check_prior_art.py, which shares this parser).
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}"}
# Wrappers that run the rest of the line as a command, and how many words each option eats.
WRAPPERS = {"env", "command", "exec", "nice", "nohup", "timeout", "caffeinate", "time"}
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                  "--config-env", "--super-prefix", "--list-cmds"}
# Wrapper options that take the NEXT word as their value, PER WRAPPER (Codex r1 F3: `env -C
# /tmp`, `caffeinate -t 5`, `nice --adjustment 5` hid the git command that followed). Per
# wrapper because the same letter differs between them: exec's `-a NAME` takes a value and
# time's `-a` does not, so one shared set read `time -a -o F git push` as running `F`
# (E1 r3, 2026-11-16).
_WRAPPER_VALUE_OPTS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-P"},      # -P altpath (BSD), E1 r3 F2
    "time": {"-o", "--output", "-f", "--format"},         # BSD -o, GNU -o/-f, E1 r3 F1
    "nice": {"-n", "--adjustment"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "caffeinate": {"-t", "-w"},
    "exec": {"-a"},                                       # exec -a NAME (r3 F3)
}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
MAX_DEPTH = 6


def _strip_prefix(words: list[str]) -> list[str]:
    """Drop assignments, shell keywords and command wrappers from the front."""
    i = 0
    while i < len(words):
        w = words[i]
        if ASSIGNMENT.match(w) or w in KEYWORDS:
            i += 1
            continue
        base = os.path.basename(w)
        if base in WRAPPERS:
            i += 1
            own = []                        # command's OWN options: the dash words before
            for w in words[i:]:             # the command name (Codex r4 F1: git's `-v` is not one)
                if not w.startswith("-"):
                    break
                own.append(w)
            if base == "command" and any(w in ("-v", "-V") for w in own):
                return []       # `command -v git`: a lookup, nothing runs (Codex r3 F2)
            while i < len(words) and (words[i].startswith("-") or ASSIGNMENT.match(words[i])):
                w = words[i]
                short_s = re.fullmatch(r"-[A-Za-z]*S(.*)", w, re.S) if not w.startswith("--") else None
                if base == "env" and (short_s or w == "--split-string" or w.startswith("--split-string=")):
                    # The value IS a command line (Codex r2 F1): judge it, then what follows.
                    # Short form in any spelling: `-S x`, `-S'x'`, `-iS'x'` (Codex r5 F1).
                    if w.startswith("--split-string="):
                        value, after = w.split("=", 1)[1], words[i + 1:]
                    elif short_s and short_s.group(1):
                        value, after = short_s.group(1), words[i + 1:]
                    else:
                        value = words[i + 1] if i + 1 < len(words) else ""
                        after = words[i + 2:]
                    inner = simple_commands(value)
                    # Parsed as env's own arguments, since the value may start with more env
                    # options (`env -S '-i git push'`, Codex r3 F1).
                    return _strip_prefix(["env"] + (inner[0].words if inner else []) + after)
                i += 2 if w in _WRAPPER_VALUE_OPTS.get(base, ()) else 1
            if base == "timeout" and i < len(words) and re.fullmatch(r"[0-9.]+[smhd]?", words[i]):
                i += 1          # the duration
            continue
        break
    return words[i:]


# Options of fetch/pull/ls-remote/clone that take the NEXT word as their value, so that
# word is not mistaken for the remote.
_VALUE_FLAGS = {"--depth", "-j", "--jobs", "--upload-pack", "-u", "--origin", "-o",
                "--branch", "-b", "--reference", "--separate-git-dir", "--filter",
                "--shallow-since", "--shallow-exclude", "--server-option", "--sort"}


def _no_prompt(sub: str, rest: list[str]) -> bool:
    """Shapes that make no SSH connection, found by the restraint replay (2026-11-05, 43,481
    real commands): help; an explicit https:// remote for a READ (no SSH key, so no Touch
    ID); a clone from a local path. A push is never exempt, whatever the remote."""
    if "--help" in rest or "-h" in rest:
        return True
    if sub not in ("fetch", "pull", "ls-remote", "clone"):
        return False
    if sub == "clone" and any(":" in a and not a.startswith(("https://", "file://"))
                              for a in rest if not a.startswith("-")):
        # Conservative (Codex r2 F2): any argument that could be a remote (`host:path`,
        # `ssh://`, `git://`) blocks, so an option value the table below does not know can
        # only cause an over-block, never let an SSH clone through.
        return False
    i = 0
    while i < len(rest):
        w = rest[i]
        if w in _VALUE_FLAGS:
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        if w.startswith(("https://", "file://")):
            return True
        # A clone source with no `scheme://` and no `host:` prefix is a local path (Codex
        # r1 F2: `git clone local-repo dest`). `host:path` is SSH shorthand and still blocks.
        return sub == "clone" and "://" not in w and ":" not in w
    return False


def _git_subcommand(args: list[str]) -> tuple[str, list[str]] | None:
    """(subcommand, its arguments) of a `git` call (its arguments after `git`), or None
    when there is no subcommand or git only prints help."""
    i = 0
    while i < len(args):
        w = args[i]
        if w in ("--help", "-h"):
            return None         # `git --help fetch` prints help (Codex r1 F1)
        if w in GIT_VALUE_OPTS:
            i += 2              # `-C dir`, `-c key=value`: the option and its value
            continue
        if w.startswith("-"):
            i += 1              # a flag, or `--git-dir=x` style with its value attached
            continue
        return w, args[i + 1:]
    return None


def _git_network(args: list[str]) -> str | None:
    """The network subcommand of a `git` call (its arguments after `git`), or None."""
    found = _git_subcommand(args)
    if found is None:
        return None
    w, rest = found
    if w in NETWORK_SUBCOMMANDS:
        return None if _no_prompt(w, rest) else w
    if w == "remote":
        sub = next((a for a in rest if not a.startswith("-")), "")
        if sub in REMOTE_NETWORK and not (sub == "show" and "-n" in rest):
            return f"remote {sub}"
    if w == "submodule" and next((a for a in rest if not a.startswith("-")), "") == "update":
        return "submodule update"
    return None


# `git stash list` and `git stash show` only read the stack, and `git stash create` writes a
# commit object without touching the stack or the working tree (codex_verify.py uses it to
# name the reviewed tree; 21 of 107 replay hits were it, 2026-11-25). Every other form
# changes the stack: bare `git stash` and `git stash -<opt> ...` are `push`, and push/pop/
# apply/drop/clear/save/store/branch all add, take or rewrite an entry. The subcommand is the FIRST
# word after `stash`, never a later one, so `git stash -m list` (a push whose message is
# "list") is not read as `list`.
STASH_READ_ONLY = {"list", "show", "create"}


def _git_stash(args: list[str]) -> str | None:
    """'stash <sub>' when a `git` call changes the stash list, else None."""
    found = _git_subcommand(args)
    if found is None or found[0] != "stash":
        return None
    rest = found[1]
    if "--help" in rest or "-h" in rest:
        return None
    sub = rest[0] if rest and not rest[0].startswith("-") else "push"
    return None if sub in STASH_READ_ONLY else f"stash {sub}"


def _find_git(command: str, judge, depth: int = 0) -> str | None:
    """The first `git` call in `command` that `judge` flags, as text for the message."""
    if depth > MAX_DEPTH:
        # Too deep to see what runs: block rather than pass (Codex r2 F3). No ordinary
        # command nests bash -c / eval / $() this far.
        return "(command nested too deep to check)"
    for sc in simple_commands(command):
        words = _strip_prefix(sc.words)
        if words:
            head = os.path.basename(words[0])
            if head == "git":
                if judge(words[1:]):
                    return " ".join(words[:8])
            elif head in SHELLS:
                for j, w in enumerate(words[1:-1], start=1):
                    if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", w):
                        hit = _find_git(words[j + 1], judge, depth + 1)
                        if hit:
                            return hit
                        break
            elif head == "eval":
                hit = _find_git(" ".join(words[1:]), judge, depth + 1)
                if hit:
                    return hit
        for sub_cmd in sc.subs:
            hit = _find_git(sub_cmd, judge, depth + 1)
            if hit:
                return hit
    return None


def find_network_git(command: str, depth: int = 0) -> str | None:
    """The first network git command in `command`, as text for the message, or None."""
    return _find_git(command, _git_network, depth)


def find_git_stash(command: str, depth: int = 0) -> str | None:
    """The first `git stash` that changes the stash list, as text for the message, or None."""
    return _find_git(command, _git_stash, depth)


def main() -> None:
    p = read_payload()
    if not p.ok:
        sys.exit(0)             # policy: fail open on an unreadable payload
    if p.tool_name != "Bash":
        sys.exit(0)
    command = p.command or ""
    hit = find_network_git(command)
    if not hit:
        stash = find_git_stash(command)
        if not stash:
            sys.exit(0)
        print(
            f"BLOCKED: `git stash` from a Claude Bash call: `{stash}`.\n\n"
            "The stash list is one stack shared by the main checkout and every worktree, so "
            "another session can pop your entry or you can pop theirs; a stash taken for a "
            "red check has also stashed the test itself (6 fires, "
            "memory/feedback_dont_git_stash_for_a_before_after_check.md).\n\n"
            "Instead:\n"
            "  - Red check: `cp f <scratch>; edit f; run the test; cp <scratch> f; cmp`.\n"
            "  - Old version of a file: `git show HEAD:<path> > <scratch>`, or a second "
            "worktree.\n"
            "  - `git stash list` and `git stash show` are allowed. If a stash is really "
            "needed, ask Nick to run it himself with `!` in front.\n",
            file=sys.stderr)
        sys.exit(2)
    print(
        f"BLOCKED: network git from a Claude Bash call: `{hit}`.\n\n"
        "Every GitHub remote here is SSH through a Touch ID key, so this would raise a "
        "Touch ID prompt Nick did not start.\n\n"
        "Instead:\n"
        "  - To know what is on GitHub, read the existing origin/<branch> ref and say how "
        "old it is, or use the read-only `gh api` (e.g. "
        "`gh api --paginate repos/<owner>/<repo>/git/matching-refs/heads`).\n"
        "  - If the network call is really needed, ask Nick to run it himself with `!` "
        "in front of the command. There is no override for Claude (Nick, 2026-11-05).\n",
        file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
