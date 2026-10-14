#!/usr/bin/env python3
"""check_owner_only_actions.py -- PreToolUse hook (Bash, and Write|Edit).

BLOCKS the agent from the three acts that let a push past the cross-model gate on a
decision only Nick may make:

  1. `finding_write.py accept <addr>` -- accepting a parked P0 so it stops blocking.
  2. Setting CODEX_VERIFY_WAIVE -- waiving the gate for a push.
  3. Writing tools/.cross-model-ledger.jsonl directly -- which could forge either.

WHY (2026-10-14). The gate read "parked" as resolved, so the agent could park a real P0
and push in the same motion: 102 P0s were parked by that day. The verify skill said to
announce a park and let Nick push, which is prose, and the only thing that had ever
enforced it was the permission classifier denying a park-and-push as a CI bypass. Codex
335.F1 named the fix: a parked P0 keeps blocking until an acceptance the gate reads, and
the agent cannot write the acceptance. The waiver had the same hole. Nick chose this
design on 2026-10-14, including the waiver.

Nick runs these himself, with `! <command>` in the session or in his own terminal.
There is deliberately NO override variable: one the agent can type is the waiver again.

WHAT IT CANNOT DO. It reads the command text, so it is bypassable by the actor it
constrains: a script file the command runs, a path or name built from parts, `eval`,
and python that writes the ledger file with plain file I/O are not seen. Python `-c`
code and heredoc programs are read by substring, and only for the import plus the call
(see _IMPORTS_FW), because a name-only rule blocked the agent's routine source edits. Per feedback_llm_self_policing_fails the
point is to turn a silent self-clearing into a deliberate circumvention; using one of
these gaps on purpose is a rule violation, not a design the hook endorses.

BLOCK tier (exit 2): a PreToolUse warning on exit 0 reaches nobody.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
from hook_command_lint import python_calls  # noqa: E402
from shell_tokens import simple_commands  # noqa: E402

LEDGER = ".cross-model-ledger.jsonl"
WAIVE = "CODEX_VERIFY_WAIVE"

_ASSIGN = re.compile(r"[A-Za-z_]\w*=")
# Builtins and programs whose arguments are environment assignments.
_SETTERS = {"export", "env", "declare", "typeset", "readonly", "local"}
# Redirection operators that write their target (`<`, `<<`, `<<<`, `<&` read).
_WRITE_REDIRS = (">", ">>", ">|", "&>", "&>>", "<>")
# Programs that write a file named in their arguments.
_FILE_WRITERS = {"tee", "cp", "mv", "install", "dd", "truncate", "touch", "ln", "rm"}
_IN_PLACE = {"sed", "perl", "gsed"}
# In python code (`-c`, heredoc), read by substring. Each rule needs the IMPORT as well
# as the call or name, because the agent routinely runs python that EDITS these tools'
# source, and that text names every one of these. Replay of 18,791 historical commands
# (2026-10-14): a name-only rule fired on 23 such edits and on the hook's own build.
_IMPORTS_FW = re.compile(r"\b(import\s+finding_write|from\s+(tools\.)?finding_write\s+import"
                         r"|import\s+tools\.finding_write)")
_IMPORTS_GATE = re.compile(r"\b(import\s+cross_model_gate|from\s+(tools\.)?cross_model_gate"
                           r"\s+import|import\s+tools\.cross_model_gate)")
_CALLS_ACCEPT = re.compile(r"\baccept\s*\(")
_CALLS_LEDGER_WRITER = re.compile(r"\b(append_row|record_waiver)\s*\(")
_SETS_ENV = re.compile(r"\b(environ|putenv)\b")
# Programs whose LAST argument is the file they write; the others are sources.
_DEST_LAST = {"cp", "mv", "install", "ln", "rsync", "scp", "ditto"}
# Shells whose `-c STRING` runs STRING as a command line (Codex 9b588cd F1).
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
# Programs that run the rest of their arguments as a command (Codex d71cd64 F1).
_WRAPPERS = {"command", "exec", "nohup", "time", "nice", "sudo", "doas", "stdbuf",
             "timeout", "builtin"}
# Wrapper options that take the next word as their value.
_WRAPPER_VALUE_OPTS = {"-n", "-u", "-g", "-s", "-k", "-o", "-e", "-a"}


def _unwrap(words: list[str]) -> tuple[bool, list[str]]:
    """(sets the waiver, the command words left once assignments, `env`, and wrapper
    programs like `command`, `exec`, `nohup` and `timeout 5` are peeled off)."""
    rest = list(words)
    while rest:
        if _ASSIGN.match(rest[0]):
            if rest[0].startswith(WAIVE + "="):
                return True, rest
            rest = rest[1:]
            continue
        prog = os.path.basename(rest[0])
        if prog == "env":
            rest = rest[1:]
            while rest and (rest[0].startswith("-") or _ASSIGN.match(rest[0])):
                if rest[0].startswith(WAIVE + "="):
                    return True, rest
                rest = rest[2:] if rest[0] in ("-u", "-C", "-S") else rest[1:]
            continue
        if prog in _WRAPPERS:
            rest = rest[1:]
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in _WRAPPER_VALUE_OPTS else rest[1:]
            if prog == "timeout" and rest:
                rest = rest[1:]                         # the duration
            continue
        break
    return False, rest


def _is_ledger(word: str) -> bool:
    return os.path.basename(word.rstrip("/")) == LEDGER


def _accept_subcommand(args: list[str]) -> bool:
    """Is the subcommand of `finding_write.py <args>` `accept`? Skips --repo-root P."""
    i = 0
    while i < len(args):
        if args[i] == "--repo-root":
            i += 2
            continue
        if args[i].startswith("--repo-root="):
            i += 1
            continue
        # `accept --help` prints usage and accepts nothing (replay, 2026-10-14).
        return args[i] == "accept" and not {"-h", "--help"} & set(args[i + 1:])
    return False


def _code_violation(code: str) -> str | None:
    if _IMPORTS_FW.search(code) and _CALLS_ACCEPT.search(code):
        return "accept"
    if WAIVE in code and _SETS_ENV.search(code):
        return "waive"
    if _IMPORTS_GATE.search(code) and _CALLS_LEDGER_WRITER.search(code):
        return "ledger"
    return None


# Programs whose arguments, piped into a shell or python, become its program text.
_PRODUCERS = {"echo", "printf", "cat"}


def _stdin_texts(cmd) -> list[str]:
    """What this simple command reads on stdin, where the command line shows it:
    heredoc bodies, here-strings, and the words or heredoc of an echo/printf/cat
    piped into it (Grok d71cd64 F2: `bash <<EOF`, `bash <<< ...`, `echo ... | bash`)."""
    out = []
    for r in cmd.redirections:
        if r.body is not None:
            out.append(r.body)
        elif r.op.lstrip("0123456789") == "<<<":
            out.append(r.target)
    src = cmd.fed_by
    if src is not None:
        _w, words = _unwrap(src.words)
        if words and os.path.basename(words[0]) in _PRODUCERS:
            out.append(" ".join(w for w in words[1:] if not w.startswith("-")))
            out.extend(r.body for r in src.redirections if r.body is not None)
    return out


def _command_violation(cmd) -> str | None:
    words, redirections = cmd.words, cmd.redirections
    # 1. `CODEX_VERIFY_WAIVE=why git push`, also behind env and wrapper programs.
    waives, rest = _unwrap(words)
    if waives:
        return "waive"
    if rest:
        prog = os.path.basename(rest[0])
        # `export CODEX_VERIFY_WAIVE=...`, `declare ...`.
        if prog in _SETTERS and any(w.startswith(WAIVE + "=") for w in rest[1:]):
            return "waive"
        args = [w for w in rest[1:] if not w.startswith("-")]
        if prog in _DEST_LAST and args and _is_ledger(args[-1]):
            return "ledger"
        # `cp /tmp/.cross-model-ledger.jsonl tools/`: a ledger-named source into tools/.
        if prog in _DEST_LAST and len(args) > 1 \
                and os.path.basename(args[-1].rstrip("/")) == "tools" \
                and any(_is_ledger(w) for w in args[:-1]):
            return "ledger"
        if prog in _FILE_WRITERS - _DEST_LAST and any(
                _is_ledger(w.split("=", 1)[-1]) for w in args):     # dd of=FILE
            return "ledger"
        # In place: `-i`, or any short-flag cluster holding i (`-pi`, `-Ei`).
        if prog in _IN_PLACE and any(
                w.startswith("--in-place") or (w.startswith("-") and not w.startswith("--")
                                      and "i" in w[1:]) for w in rest[1:]) \
                and any(_is_ledger(w) for w in rest[1:]):
            return "ledger"
        # `bash -c STRING`, `sh -lc STRING`, `eval STRING`: the string is a command.
        if prog in _SHELLS:
            reads_stdin = True
            for n, w in enumerate(rest[1:], start=1):
                if w.startswith("-") and not w.startswith("--"):
                    if "c" in w[1:]:
                        reads_stdin = False
                        found = find_violation(rest[n + 1]) if n + 1 < len(rest) else None
                        if found:
                            return found
                        break
                    if "s" in w[1:]:
                        break                          # `bash -s ARGS`: stdin
                    continue
                reads_stdin = False                    # `bash script.sh`: a file
                break
            if reads_stdin:
                for text in _stdin_texts(cmd):
                    found = find_violation(text)
                    if found:
                        return found
        if prog == "eval":
            tail = rest[2:] if rest[1:2] == ["--"] else rest[1:]
            found = find_violation(" ".join(tail)) if tail else None
            if found:
                return found
    for r in redirections:
        if r.op.lstrip("0123456789") in _WRITE_REDIRS and _is_ledger(r.target):
            return "ledger"
    # 2. python: the accept subcommand, or code that does any of the three.
    for call in python_calls(rest):
        if call.script and os.path.basename(call.script) == "finding_write.py" \
                and _accept_subcommand(call.args):
            return "accept"
        if call.module and call.module.split(".")[-1] == "finding_write" \
                and _accept_subcommand(call.args):
            return "accept"
        if call.code is not None:
            found = _code_violation(call.code)
            if found:
                return found
        if call.script is None and call.code is None and call.module is None:
            for text in _stdin_texts(cmd):      # `python3 - <<'EOF'`, `echo ... | python3`
                found = _code_violation(text)
                if found:
                    return found
    return None


def find_violation(command: str) -> str | None:
    """'accept' | 'waive' | 'ledger' for the first owner-only act in `command`, or
    None. Pure. Each simple command is judged on its own; `$(...)` and backticks are
    commands too."""
    for cmd in simple_commands(command):
        found = _command_violation(cmd)
        if found:
            return found
        for inner in cmd.subs:
            found = find_violation(inner)
            if found:
                return found
    return None


MESSAGES = {
    "accept": "accepting a parked P0 (`finding_write.py accept`)",
    "waive": f"setting {WAIVE}",
    "ledger": f"writing tools/{LEDGER} directly",
}


def block(kind: str) -> None:
    print(
        f"BLOCKED (owner-only): {MESSAGES[kind]} is Nick's decision, not the agent's.\n"
        "\n"
        "Stop and tell Nick what is blocking, with the finding addresses and why each is\n"
        "parked. He runs it himself with `! <command>` or in his own terminal:\n"
        "  ! PYTHONIOENCODING=utf-8 python3 tools/finding_write.py accept <addr> --why '...'\n"
        "  ! CODEX_VERIFY_WAIVE='<why>' git push\n"
        "To record a disposition, use `finding_write.py set` (fixed|rejected|parked).\n"
        "There is no override. See tools/check_owner_only_actions.py.",
        file=sys.stderr)
    sys.exit(2)


def main() -> None:
    p = read_payload()
    if not p.ok:
        sys.exit(0)          # fail open on an unreadable payload, like the other hooks
    if p.tool_name == "Bash":
        found = find_violation(p.command or "")
        if found:
            block(found)
    elif p.tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        if _is_ledger(p.file_path or ""):
            block("ledger")
    sys.exit(0)


if __name__ == "__main__":
    main()
