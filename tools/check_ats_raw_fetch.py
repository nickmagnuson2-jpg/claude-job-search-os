#!/usr/bin/env python3
"""check_ats_raw_fetch.py - PreToolUse hook for Bash.

BLOCKS a hand-rolled fetch of a public ATS posting API (Ashby posting-api,
Greenhouse boards-api, Lever v0 postings) and points to `tools/ats_board.py`, which
reads the same boards through the project parsers in `tools/career_scanner/`.

Why block: a hand-rolled fetch loses the parsers' error channel. A dead or wrong
slug returns a 404 page or an empty body, and a one-liner that does
`json.load(...).get("jobs", [])` reports "0 roles" for a board that was never read:
the false zero documented in `tools/career_scanner/ashby.py` (2026-09-02).
Origin 2026-11-18: a session listed `tools/career_scanner/`, saw `ashby.py`, then
hand-rolled `urllib` anyway and got HTTP 403 (no User-Agent). Rule
`feedback_prefer_extending_existing_infra_over_new_build`, 13th fire. Built on
Nick's approval the same day ("Yes, build the number 2 hook"; "approved: write
check_ats_raw_fetch.py").

Restraint replay (2026-11-18, every Bash call in this project's transcripts, 1,821
files): 347 commands mention an ATS host; the per-command detector blocks 149, and a
hand read found all to be real fetches. 103 of the 147
Ashby fetches asked for compensation, which the parser did not return, so the same
change added `fetch_ashby_with_compensation` and `ats_board.py --comp`.

Detection, per simple command (tools/shell_tokens.py; a python heredoc body or -c
string is part of its python command, since it IS the program):
  1. a `curl`/`wget` command whose own words carry a posting-API host, or
  2. a python command whose own words, -c string or heredoc body carry both a
     posting-API host and an HTTP call (urlopen, urllib.request,
     requests.get/post/request/Session, an aliased or from-import of requests,
     httpx, http.client), or
  3. a `curl`/`wget` whose argument references a variable that holds the host
     (VAR=<host...> or `for VAR in <host...>` in the same line).
A host in one command and a fetch of another URL in a different command do not
combine (Codex/Grok r1 F1, 2026-11-18).
NOT covered: `jobs.ashbyhq.com/api/non-user-graphql` (single-posting fetch; no project
parser wraps it), the WebFetch tool, and scripts written to a file and run later.
This is a tripwire for the agent's habit, not a security boundary.

Override, for the case the rule allows (the project tool was run and failed, and
the failure is stated): prefix the fetch command itself with
`ATS_RAW_FETCH_OK="<why the parser could not do it>"`. The reason must be a non-empty
literal (a $variable does not count),
and an assignment on another command does not cover this one (Codex r1 F2).

Exit codes: 0 clean or unreadable payload (fail open); 2 blocked.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shell_tokens  # noqa: E402
from hook_runtime import read_payload  # noqa: E402

API_HOST = re.compile(
    r"api\.ashbyhq\.com/posting-api"
    r"|boards-api\.greenhouse\.io"
    r"|api\.greenhouse\.io/v1/boards"
    r"|api\.lever\.co/v0/postings",
    re.IGNORECASE,
)
PY_HTTP = re.compile(
    r"urlopen|urllib\.request|\brequests\.(?:get|post|request|Session)\b|\bhttpx\b|http\.client"
    # An import of requests itself, aliased or from-import (`import requests as r`,
    # `from requests import get`): with an ATS host in the same program, it fetches
    # the board under another name (Codex 1086.F1, 2026-11-19; Nick: "Fix first").
    r"|\b(?:import|from)\s+[^\n;]*?\brequests\b",
)
ASSIGN = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.DOTALL)
LEADERS = {"do", "then", "else", "time", "exec", "command", "xargs", "!", "nohup", "env", "timeout"}
SHELL_FETCHERS = {"curl", "wget"}
PYTHON = re.compile(r"^python3?(?:\.\d+)?$")


def _commands(command: str) -> list:
    """Simple commands, with command substitutions expanded into their own commands."""
    out, todo = [], [command]
    while todo:
        for c in shell_tokens.simple_commands(todo.pop()):
            out.append(c)
            todo.extend(c.subs)
    return out


def _split(c) -> tuple[dict, str, list]:
    """(leading env assignments, command name, remaining args) for one simple command."""
    env, words = {}, list(c.words)
    while words:
        m = ASSIGN.match(words[0])
        if m:
            env[m.group(1)] = m.group(2)
            words.pop(0)
        elif words[0] in LEADERS:
            lead = words.pop(0)
            if lead == "timeout":  # timeout [-opts] DURATION cmd
                while words and words[0].startswith("-"):
                    opt = words.pop(0)
                    if opt in ("-k", "-s", "--kill-after", "--signal") and words:  # take a value
                        words.pop(0)
                if words and re.fullmatch(r"(?:[\d.]+(?:[eE][+-]?\d+)?|inf(?:inity)?)[smhd]?",
                                          words[0], re.IGNORECASE):  # GNU strtod forms
                    words.pop(0)
        else:
            break
    name = os.path.basename(words[0]) if words else ""
    return env, name, words[1:]


def _text(c) -> str:
    return " ".join(c.words + [r.body for r in c.redirections if r.body] + list(c.subs))


VAR_REF = re.compile(r"\$\{?([A-Za-z_]\w*)")


def _assignments(env: dict, name: str, args: list) -> dict:
    """Variables this command sets: a bare VAR=v command, `export VAR=v`, or `for VAR in ...`."""
    if name in ("export", "declare", "typeset", "local", "readonly"):  # options are skipped
        return {m.group(1): m.group(2) for a in args if (m := ASSIGN.match(a))}
    if name == "for" and args:
        return {args[0]: " ".join(args[1:])}
    return env if not name else {}


def _blocking(command: str) -> list[dict]:
    """Envs of the commands that hand-fetch an ATS posting API.

    Walks the simple commands IN ORDER (Codex/Grok r3 F1), tracking which variable
    names currently hold an ATS host: an assignment of a host sets the name, any other
    assignment clears it. A command blocks when it is a curl/wget whose own words carry
    the host or reference a currently held name, or a python command whose own text
    carries the host and an HTTP call.
    """
    if not API_HOST.search(command):
        return []
    held: set[str] = set()
    out = []
    for c in _commands(command):
        env, name, args = _split(c)
        text = _text(c)
        if name in SHELL_FETCHERS:
            refs = {m for a in args for m in VAR_REF.findall(a)}
            prefixed = {k for k, v in env.items() if API_HOST.search(v)}
            if API_HOST.search(text) or refs & (held | prefixed):
                out.append(env)
        elif PYTHON.match(name) and PY_HTTP.search(text) and API_HOST.search(text):
            out.append(env)
        for k, v in _assignments(env, name, args).items():
            (held.add if API_HOST.search(v) else held.discard)(k)
    return out


def is_raw_ats_fetch(command: str) -> bool:
    """True when some command in the line hand-fetches a public ATS posting API.

    Judged per simple command (shell_tokens), so an ATS host in one command and a fetch
    of another URL in a later command do not combine. The one cross-command case kept
    is the history's for-loop shape: a fetch whose argument is a $variable, when the
    host is a `for` item or a VAR=value assignment in the same line.
    """
    return bool(_blocking(command))


def _reason_ok(value: str) -> bool:
    """A literal, non-empty reason. A $variable or backtick is not a reason (Codex r2 F2)."""
    v = value.strip(" \"'")
    return bool(v) and "$" not in v and "`" not in v


def has_override(command: str, envs: list[dict] | None = None) -> bool:
    """True when EVERY blocking fetch carries its own literal ATS_RAW_FETCH_OK reason."""
    envs = _blocking(command) if envs is None else envs
    return bool(envs) and all(_reason_ok(env.get("ATS_RAW_FETCH_OK", "")) for env in envs)


MESSAGE = (
    "BLOCKED [check_ats_raw_fetch]: hand-rolled fetch of a public ATS posting API.\n"
    "Use the project parser, which reports a failed fetch instead of a silent zero:\n"
    "  PYTHONIOENCODING=utf-8 python3 tools/ats_board.py ashby <slug> [--comp] [--title REGEX] [--json]\n"
    "  PYTHONIOENCODING=utf-8 python3 tools/ats_board.py greenhouse|lever <slug> [--title REGEX] [--json]\n"
    "  (in Python: tools.career_scanner.ashby.fetch_ashby / fetch_ashby_with_compensation,\n"
    "   greenhouse.fetch_greenhouse, lever.fetch_lever, each with errors=[])\n"
    "If the parser was run and cannot do this, say why and prefix the command with\n"
    "  ATS_RAW_FETCH_OK=\"<reason>\""
)


def main() -> None:
    p = read_payload()
    if not p.ok:
        sys.exit(0)  # policy: fail open on an unreadable payload
    envs = _blocking(p.command or "")
    if not envs or has_override("", envs):
        sys.exit(0)
    print(MESSAGE, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
