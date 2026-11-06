#!/usr/bin/env python3
"""check_network_git.py -- PreToolUse hook for Bash. BLOCKS a network git command.

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
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "time"}
# Wrappers that run the rest of the line as a command, and how many words each option eats.
WRAPPERS = {"env", "command", "exec", "nice", "nohup", "timeout", "caffeinate", "time"}
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                  "--config-env", "--super-prefix", "--list-cmds"}
# Wrapper options that take the NEXT word as their value (Codex r1 F3: `env -C /tmp`,
# `caffeinate -t 5`, `nice --adjustment 5` hid the git command that followed).
_WRAPPER_VALUE_OPTS = {"-u", "--unset", "-C", "--chdir",                         # env
                       "-n", "--adjustment",                                       # nice
                       "-s", "--signal", "-k", "--kill-after",                     # timeout
                       "-t", "-w",                                                 # caffeinate
                       "-a"}                                                       # exec -a NAME (r3 F3)
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
                if base == "env" and (w in ("-S", "--split-string") or w.startswith("--split-string=")):
                    # The value IS a command line (Codex r2 F1): judge it, then what follows.
                    value = w.split("=", 1)[1] if "=" in w else (words[i + 1] if i + 1 < len(words) else "")
                    after = words[i + 1:] if "=" in w else words[i + 2:]
                    inner = simple_commands(value)
                    # Parsed as env's own arguments, since the value may start with more env
                    # options (`env -S '-i git push'`, Codex r3 F1).
                    return _strip_prefix(["env"] + (inner[0].words if inner else []) + after)
                i += 2 if w in _WRAPPER_VALUE_OPTS else 1
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


def _git_network(args: list[str]) -> str | None:
    """The network subcommand of a `git` call (its arguments after `git`), or None."""
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
        rest = args[i + 1:]
        if w in NETWORK_SUBCOMMANDS:
            return None if _no_prompt(w, rest) else w
        if w == "remote":
            sub = next((a for a in rest if not a.startswith("-")), "")
            if sub in REMOTE_NETWORK and not (sub == "show" and "-n" in rest):
                return f"remote {sub}"
        if w == "submodule" and next((a for a in rest if not a.startswith("-")), "") == "update":
            return "submodule update"
        return None
    return None


def find_network_git(command: str, depth: int = 0) -> str | None:
    """The first network git command in `command`, as text for the message, or None."""
    if depth > MAX_DEPTH:
        # Too deep to see what runs: block rather than pass (Codex r2 F3). No ordinary
        # command nests bash -c / eval / $() this far.
        return "(command nested too deep to check)"
    for sc in simple_commands(command):
        words = _strip_prefix(sc.words)
        if words:
            head = os.path.basename(words[0])
            if head == "git":
                sub = _git_network(words[1:])
                if sub:
                    return " ".join(words[:8])
            elif head in SHELLS:
                for j, w in enumerate(words[1:-1], start=1):
                    if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", w):
                        hit = find_network_git(words[j + 1], depth + 1)
                        if hit:
                            return hit
                        break
            elif head == "eval":
                hit = find_network_git(" ".join(words[1:]), depth + 1)
                if hit:
                    return hit
        for sub_cmd in sc.subs:
            hit = find_network_git(sub_cmd, depth + 1)
            if hit:
                return hit
    return None


def main() -> None:
    p = read_payload()
    if not p.ok:
        sys.exit(0)             # policy: fail open on an unreadable payload
    if p.tool_name != "Bash":
        sys.exit(0)
    hit = find_network_git(p.command or "")
    if not hit:
        sys.exit(0)
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
