#!/usr/bin/env python3
"""replay_coverage.py: say which command shapes a hook replay actually measured.

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/replay_coverage.py --corpus FILE.jsonl \
        --old "python3 /path/old/tools/check_x.py" --new "python3 /path/new/tools/check_x.py" \
        --relevant 'REGEX' [--classes env_prefix,subshell_cd_write] [--env K=V ...] [--json]

Exit 0 every class measured and no twin divergence under --new | 1 an UNMEASURED class or a
class whose twins diverge under --new | 2 usage or input error.

WHY (codify #20 and #27, 2026-11-22). Two hook fixes were replayed over a real corpus and
reported "0 newly allowed", and both reports were vacuous:
  - #27: a linear cd model let `(cd /tmp && true); echo > feedback_x.md` through. The corpus
    held no relevant command of that shape, so no verdict could change. A count of 0 relevant
    rows in the class is the signal: the class is UNMEASURED.
  - #20: the corpus held hundreds of env-prefixed interpreter calls (`PYTHONIOENCODING=utf-8
    python3 ...`), but the old and new hooks were equally blind to the prefix, so their
    verdicts agreed. A zero count cannot see a blindness both versions share. The signal is
    TWIN DIVERGENCE: strip the shape from each row (here, drop the env assignments), run the
    hook on both forms, and count rows whose verdict differs. The stripped twin shows what the
    hook should have done.

WHAT IT REPORTS, per enumerated class: N = relevant rows in the class (distinct commands that
match the class AND --relevant, the caller's statement of which commands the hook can act on);
verdict changes old -> new; and, where the class has a meaning-preserving twin, "n diverged of
N" under each hook. A class with no such twin reports counts only and says so.

LIMITS. It measures ONLY the shapes enumerated in CLASSES. A shape nobody listed is invisible
to it, exactly as it was to the replays above. The next step, NOT BUILT, is injection: take
commands the hook blocks, wrap them in each known shape (prefix, subshell, cd, heredoc,
wrapper), and check the verdict holds. That generates synthetic commands, so it is a separate
tool. The detectors are regexes at command position, not a shell parser: they over- and
under-match quoted text, and a twin is only as good as its stripping function.

MECHANISM ONLY. What a hook blocks is the hook's policy; --relevant is the caller's.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from count_report import format_line  # noqa: E402  (a count states its reach)

_POS = r"(?:^|(?<=[;&|(\n]))[ \t]*"        # start of a simple command, indentation allowed
# A prefix assignment: the value stops at whitespace or ; & |, and a SPACE or TAB (never a
# newline) separates it from the command. `M=x` + newline, or `M=x;`, is its own statement.
# The value: quoted strings, a whole $(...) or `...`, a backslash escape, or plain characters.
# ( and ) are kept out of the plain class so a $(...) with spaces is never cut in half on
# backtracking (Grok r1 F3; on the 11-22 corpus the old form cut 11 of 4658 values, 0 now).
_ASSIGN = (r"[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|\$\([^()]*\)|`[^`]*`|\\.|"
           r"[^\s;&|'\"`\\()])*[ \t]+")
# What follows the assignments must start a command: anything but whitespace or an operator,
# so `$HOME/bin/x`, `~/x` and quoted names count, and `X=1 && y` (a statement) does not.
_ENV_PREFIX = re.compile(_POS + r"((?:" + _ASSIGN + r")+)(?=[^\s;&|<>()#])", re.M)
_HEREDOC_START = re.compile(r"<<(-?)[ \t]*['\"]?(\w+)['\"]?")


def _lines_outside_heredocs(cmd: str) -> list:
    """(line, is_body) for each line; heredoc bodies are data, not commands."""
    out, pending = [], []
    for line in cmd.split("\n"):
        if pending:
            strip_tabs, delim = pending[0]
            out.append((line, True))
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                pending.pop(0)
            continue
        out.append((line, False))
        pending = [(dash == "-", d) for dash, d in _HEREDOC_START.findall(line)]
    return out


def _commands_only(cmd: str) -> str:
    """The command with heredoc body lines blanked, for detection."""
    return "\n".join("" if body else line for line, body in _lines_outside_heredocs(cmd))


def _strip_env_prefix(cmd: str) -> str:
    return "\n".join(line if body else
                     _ENV_PREFIX.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)], line)
                     for line, body in _lines_outside_heredocs(cmd))


@dataclass(frozen=True)
class ShapeClass:
    name: str
    detect: "re.Pattern"
    twin: Optional[Callable[[str], str]] = None
    note: str = ""

    def hits(self, cmd: str) -> bool:
        """Is `cmd` in this class? With a twin, ONLY when the twin changes it, so detection and
        twin can never disagree about what the shape is (Grok r1 F1/F2, 2026-11-23)."""
        if self.twin is not None:
            return self.twin(cmd) != cmd
        return bool(self.detect.search(_commands_only(cmd)))


CLASSES = {c.name: c for c in (
    ShapeClass("env_prefix", _ENV_PREFIX, _strip_env_prefix,
               "VAR=x before a command; twin drops the assignments"),
    ShapeClass("subshell_cd_write", re.compile(r"\(\s*cd\s+[^)]*\)\s*(?:;|&&|\|\|)[^\n]*?(?:>>?|\btee\b)"),
               None, "a cd inside ( ) followed by a write outside it; no meaning-preserving twin"),
    ShapeClass("cd_dash", re.compile(_POS + r"cd\s+-(?=\s|$|;|&)"), None, "cd - (back to OLDPWD)"),
    ShapeClass("pushd_popd", re.compile(_POS + r"(?:pushd|popd)\b"), None, "directory stack"),
    ShapeClass("heredoc", re.compile(r"<<-?\s*['\"]?\w+"), None, "here-document body"),
    ShapeClass("top_level_cd", re.compile(_POS + r"cd\s+\S"), None, "cd at command position"),
)}


def load_corpus(path: str) -> tuple:
    """(distinct Bash commands in first-seen order, reach) for a JSONL corpus.

    reach conserves: rows = distinct + non-Bash + empty command + repeated command."""
    seen, out = set(), []
    reach = {"rows": 0, "not a Bash call": 0, "empty command": 0, "repeated command": 0}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            reach["rows"] += 1
            row = json.loads(line)
            if row.get("tool_name") != "Bash":
                reach["not a Bash call"] += 1
                continue
            cmd = (row.get("tool_input") or {}).get("command") or ""
            if not cmd:
                reach["empty command"] += 1
            elif cmd in seen:
                reach["repeated command"] += 1
            else:
                seen.add(cmd)
                out.append(cmd)
    return out, reach


def load_commands(path: str) -> list:
    """Distinct Bash commands in the corpus, in first-seen order."""
    return load_corpus(path)[0]


def verdict(hook_cmd: str, command: str, env: dict) -> int:
    """The hook's exit code for one Bash PreToolUse payload (2 = block)."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    r = subprocess.run(shlex.split(hook_cmd), input=payload, capture_output=True, text=True,
                       env=env, timeout=60)
    return r.returncode


def measure(commands, classes, relevant, old, new, env) -> list:
    rows = []
    for c in classes:
        hit = [x for x in commands if c.hits(x) and relevant.search(x)]
        rep = {"class": c.name, "note": c.note, "n": len(hit), "unmeasured": not hit,
               "changed_old_to_new": 0, "twin": c.twin is not None}
        cache = {}

        def v(hook, cmd):
            if (hook, cmd) not in cache:
                cache[(hook, cmd)] = verdict(hook, cmd, env)
            return cache[(hook, cmd)]
        rep["changed_old_to_new"] = sum(1 for x in hit if (v(old, x) == 2) != (v(new, x) == 2))
        if c.twin is not None:
            for label, hook in (("old", old), ("new", new)):
                d = [x for x in hit if (v(hook, x) == 2) != (v(hook, c.twin(x)) == 2)]
                rep[f"twin_diverged_{label}"] = len(d)
                rep[f"twin_examples_{label}"] = [x[:160] for x in d[:3]]
        rows.append(rep)
    return rows


def render(rows) -> str:
    out = []
    for r in rows:
        line = f"{r['class']:20s} N={r['n']:5d}  changed old->new={r['changed_old_to_new']}"
        if r["twin"]:
            line += (f"  twin diverged: old {r['twin_diverged_old']} of {r['n']}, "
                     f"new {r['twin_diverged_new']} of {r['n']}")
        else:
            line += "  (no meaning-preserving twin: count only)"
        if r["unmeasured"]:
            line += "  UNMEASURED: 0 relevant rows; this replay says nothing about this shape"
        out.append(line)
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--relevant", required=True,
                    help="regex: the commands this hook can act on (the caller's policy)")
    ap.add_argument("--classes", default=",".join(CLASSES))
    ap.add_argument("--env", action="append", default=[], metavar="K=V")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        names = [n.strip() for n in a.classes.split(",") if n.strip()]
        unknown = [n for n in names if n not in CLASSES]
        if unknown:
            raise ValueError(f"unknown class(es): {', '.join(unknown)}; known: {', '.join(CLASSES)}")
        relevant = re.compile(a.relevant)
        env = dict(os.environ)
        for kv in a.env:
            k, sep, val = kv.partition("=")
            if not sep:
                raise ValueError(f"--env needs K=V, got {kv!r}")
            env[k] = val
        commands, reach = load_corpus(a.corpus)
    except (ValueError, OSError, re.error, json.JSONDecodeError) as e:
        print(f"replay_coverage: {e}", file=sys.stderr)
        return 2
    rows = measure(commands, [CLASSES[n] for n in names], relevant, a.old, a.new, env)
    line = format_line(f"rows in {a.corpus}", reach["rows"], len(commands),
                       {k: v for k, v in reach.items() if k != "rows"})
    print(json.dumps({"reach": line, "distinct_commands": len(commands), "classes": rows},
                     indent=1) if a.json else line + "\n" + render(rows))
    # Fail closed: an UNMEASURED class, or twins that still diverge under --new.
    if any(r["unmeasured"] or r.get("twin_diverged_new", 0) for r in rows):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
