#!/usr/bin/env python3
"""
check_scanner_examined_something.py — PreToolUse hook for Bash.

WHY THIS EXISTS
---------------
A checker with two entry points can be invoked through the wrong one and exit 0
having scanned nothing. `tools/check_public_pii.py` is the canonical case: it has
a sweep interface (`--scan <paths>`) and a PreToolUse hook interface (bare argv,
JSON payload read from stdin). Invoked as

    PYTHONIOENCODING=utf-8 python3 tools/check_public_pii.py <f1> <f2> <f3>

it falls through to the HOOK path, finds no stdin payload, reads nothing, matches
nothing, and exits 0. On 2026-08-14 that run was reported as a PII verification
over three files. It was a false pass: the correct invocation returned
`{"scanned": 3, "denylist_tokens": 414, "clean": true}` and happened to also be
clean, so the wrong conclusion and the right conclusion coincided. Nothing in the
output would have differed if a real leak had been sitting there.

**A tool that exits 0 without doing any work looks exactly like a tool that did
the work and found nothing.** Origin rule:
`memory/feedback_wrong_cli_interface_returns_a_false_pass.md` (2 fires; the
promotion criterion named in that file is exactly this: "make the wrong
invocation fail loud").

WHAT IT MEASURES (a property, not a presence)
---------------------------------------------
For each Bash command segment that invokes a `*.py` script, the hook RESOLVES THE
SCRIPT ON DISK AND READS ITS ARG PARSER. It blocks only when all of these hold —
each one a fact about the file and the command, not a convention:

  1. the script path resolves to a real readable file;
  2. its source contains a hook-style stdin read — `json.load(sys.stdin)` or
     `json.loads(sys.stdin.read())` — i.e. bare argv means "read a payload";
  3. its source declares NO positional argparse argument, so the path arguments
     on the command line are provably discarded rather than consumed;
  4. the command passes at least one positional (non-flag) argument;
  5. the command supplies NO stdin — not downstream of a `|`, and no `<` / `<<`
     redirect in the segment.

Under those five, the invocation cannot examine anything and cannot report a
non-vacuous result. That is not a style opinion; it is an always-wrong command
shape. If the caller used any flag (`--scan`, `--json`, ...) the hook stays out of
the way: engaging the flag interface means the arg parser was read.

BLOCK tier (exit 2). Per `feedback_warn_vs_block_hook_design.md`, a PreToolUse
exit-0 + stderr "warning" is never surfaced by Claude Code, so a warn here would
reach nobody — which is the same silence the rule is about. The correction is
single and known: use the tool's sweep flag, or feed it a real payload on stdin.

Detection is COMMAND POSITION, not substring: the interpreter/script token must sit
at the start of the command or right after a separator (`;` `&&` `||` `&` `|` `(`
newline), after optional `VAR=val` env assignments. Literal spans (quoted strings,
heredoc bodies) are blanked via the shared `hook_command_lint.strip_literals`
before a candidate is accepted, so `echo "python3 tools/check_public_pii.py a.md"`
and a heredoc body carrying the same line are clean. Arguments are then read from
the RAW command (quotes intact) so a quoted path is still seen as a positional.

Hook input: JSON via stdin. {"tool_input": {"command": "..."}, "cwd": "..."}

Exit codes:
  0 — clean, unresolvable, or any parse failure (fail OPEN)
  2 — an invocation that provably scans nothing; BLOCKED with the correction

Origin: 2026-08-14 pre-push PII audit (fire 1) and the same-day `pipe_write.py
update` mis-close (fire 2, the wider "acted on a partial read of an interface"
shape). See also [[feedback_name_the_scope_before_stating_the_conclusion]].
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from hook_command_lint import strip_literals  # noqa: E402
except ImportError:  # pragma: no cover - standalone/staging only
    # Fallback used ONLY when this file is run outside tools/ (staging, ad-hoc
    # copies). In the installed location tools/hook_command_lint.py is the single
    # source of truth and always wins the import above; do not extend this copy.
    _HEREDOC_FB = re.compile(
        r"(<<-?[ \t]*)(['\"]?)([A-Za-z_]\w*)\2([^\n]*\n)(.*?)(\n[ \t]*\3\b)",
        re.DOTALL,
    )

    def strip_literals(command: str) -> str:  # type: ignore[misc]
        command = _HEREDOC_FB.sub(
            lambda m: m.group(1) + m.group(2) + m.group(3) + m.group(2)
            + m.group(4) + " " + m.group(6),
            command,
        )
        command = re.sub(r"'[^']*'", " ", command)
        command = re.sub(r'"[^"$`]*"', " ", command)
        return command


# Command boundary = start | newline | separator. `||` and `&&` are matched before
# the single-char class so a `||` chain is not mistaken for a pipe (a pipe supplies
# stdin and is therefore allowed; `||` does not).
_INVOKE = re.compile(
    r"(?P<sep>^|\|\||&&|[\n;&|(])"
    r"\s*(?:\w+=\S+\s+)*"
    r"(?:(?:/usr/bin/env\s+)?python3?\s+)?"
    r"(?P<path>[^\s;&|()<>\"']+\.py)"
    r"(?P<args>[^\n;&|()]*)"
)

# The hook entry point: bare argv means "a JSON payload is coming on stdin".
_HOOK_STDIN = re.compile(r"json\.loads?\(\s*sys\.stdin(?:\.read\(\))?\s*\)")

# A positional argparse argument — add_argument("paths", ...) — or a subcommand
# table (add_subparsers/add_parser, where the bare word IS the subcommand) means the
# script really does consume bare arguments, so a positional call is legitimate.
# granola_save.py is the live example of the subparser shape: `granola_save.py write
# --output x` passes a bare word that is genuinely read.
_POSITIONAL_ARG = re.compile(
    r"add_argument\(\s*[\"'][^-]|add_subparsers\(|add_parser\(")

# Long flags the script actually names, harvested to make the block message
# actionable ("did you mean --scan?").
_LONG_FLAG = re.compile(r"[\"'](--[a-z][a-z0-9-]+)[\"']")

_MAX_SOURCE_BYTES = 2_000_000


def _resolve(path_token: str, cwd: str) -> Path | None:
    """Resolve a script path token against the payload cwd, the process cwd, and
    the repo root this hook lives in. Returns None if it is not a readable file."""
    token = path_token.strip("'\"")
    bases = [cwd, os.getcwd(), str(Path(__file__).resolve().parent.parent)]
    candidates = [Path(token)] if os.path.isabs(token) else [
        Path(b) / token for b in bases if b
    ]
    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def _read_source(script: Path) -> str | None:
    try:
        if script.stat().st_size > _MAX_SOURCE_BYTES:
            return None
        return script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _split_args(raw_args: str) -> list[str] | None:
    try:
        return shlex.split(raw_args, posix=True)
    except ValueError:
        return None


def _verdict(command: str, cwd: str):
    """Return (script, positionals, flags_in_source) for a provably-vacuous
    invocation, or None if the command is clean."""
    stripped = strip_literals(command)
    # Script paths that survive literal-stripping are genuinely invoked; a path
    # that only exists inside a quoted span or heredoc body is text, not a call.
    real_paths = {m.group("path") for m in _INVOKE.finditer(stripped)}
    if not real_paths:
        return None

    for m in _INVOKE.finditer(command):
        path_token = m.group("path")
        if path_token not in real_paths:
            continue
        if m.group("sep") == "|":          # stdin comes from the pipe
            continue
        raw_args = m.group("args")
        if "<" in raw_args:                # stdin comes from a redirect/heredoc
            continue

        args = _split_args(raw_args)
        if args is None:
            continue
        if any(a.startswith("-") for a in args):
            continue                       # flag interface engaged; parser was read
        positionals = [a for a in args if a]
        if not positionals:
            continue                       # bare call: the real hook invocation

        script = _resolve(path_token, cwd)
        if script is None:
            continue
        src = _read_source(script)
        if src is None:
            continue
        if not _HOOK_STDIN.search(src):
            continue                       # not a stdin-payload tool
        if _POSITIONAL_ARG.search(src):
            continue                       # it really does take positional args

        flags = sorted(set(_LONG_FLAG.findall(src)))
        return script, positionals, flags
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_input = data.get("tool_input", {}) or {}
    command = tool_input.get("command", "") or ""
    if not command:
        sys.exit(0)
    cwd = data.get("cwd") or ""

    try:
        verdict = _verdict(command, cwd)
    except Exception:                      # fail OPEN, always
        sys.exit(0)
    if verdict is None:
        sys.exit(0)

    script, positionals, flags = verdict
    hint = (
        "Its sweep/CLI interface is: " + " ".join(flags)
        if flags else
        "It declares no CLI flags at all — it is hook-only, so there is no way to "
        "sweep files with it from the shell."
    )
    print(
        "BLOCKED: this invocation would scan NOTHING and still exit 0.\n"
        "\n"
        f"  {script.name} reads a JSON hook payload from stdin (json.load(sys.stdin))\n"
        f"  and declares no positional argument, so the {len(positionals)} path(s) you "
        f"passed\n"
        f"  ({', '.join(positionals[:5])}) are discarded. With no stdin payload it reads\n"
        "  nothing, matches nothing, and exits 0 — a false pass indistinguishable\n"
        "  from a real clean result.\n"
        "\n"
        f"  {hint}\n"
        "\n"
        "Do instead one of:\n"
        f"  PYTHONIOENCODING=utf-8 python3 {script} "
        f"{flags[0] if flags else '<sweep-flag>'} {' '.join(positionals[:3])}\n"
        "  echo '{\"tool_input\": {...}}' | PYTHONIOENCODING=utf-8 python3 "
        f"{script}\n"
        "\n"
        "Then read the scope out of the output ('scanned: N'), not the exit code.\n"
        "Reference: memory/feedback_wrong_cli_interface_returns_a_false_pass.md\n",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
