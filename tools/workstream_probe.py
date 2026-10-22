#!/usr/bin/env python3
"""workstream_probe.py -- small, generic probes for data/workstreams/*.md.

WHY THIS EXISTS. Every workstream is meant to carry a PROBE: a command whose output
tools/workstream_state.py compares with the last value a person saw. On 2026-10-13 six
of thirteen had none, so they could never surface in /standup, and a seventh had a probe
whose tool printed more than the runner accepts. None of the seven needed a new tool of
its own; each needed one of the four mechanical readings below, pointed at a file or a
command the workstream already names. WHAT to read is policy and stays in the
workstream file's PROBE line. HOW to read it is here.

Every subcommand prints one JSON object, {"value": ...}, on stdout and exits 0. Anything
it cannot read is exit 2 with {"error": ...}: the runner reports that as a probe error,
which always surfaces. A reading that could not be taken is never printed as 0.

  count-lines PATH REGEX [--ignore-case]
      Lines of PATH matching REGEX.

  frontmatter PATH KEY
      One key from a file's YAML frontmatter, for example a rule file's `occurrences`.

  lift [--len KEY]... [--field KEY] [--ok-exit N]... -- ARGV...
      Run ARGV from the repo root, parse its stdout as JSON, and report the length of
      each --len list (one KEY: an integer; several: "a=2 | b=0") or the value at
      --field. For a tool whose full output is too large for the runner or is a list
      where the workstream wants a count. Dotted keys reach into nested objects.

  const-len FILE:NAME [FILE:NAME ...]
      Entries in module-level dict / set / list / tuple literals of Python files, read
      from the source without importing it. For an allowlist that should drain to zero.

PATH is relative to the repo root, absolute, `~`-prefixed, or `memory:<file name>` for a
file in this project's Claude memory directory (outside the repo; its location is
derived from the repo path the same way tools/backup-data.sh derives it).

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/workstream_probe.py count-lines data/x.md '^- done'
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # siblings import as `tools.X` too
from hook_runtime import claude_project_dir  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_PREFIX = "memory:"
LIFT_TIMEOUT = 100


class ProbeError(Exception):
    """The reading could not be taken. Never the same thing as a reading of zero."""


def memory_dir(root: Path) -> Path:
    return claude_project_dir(root) / "memory"


def resolve(spec: str, root: Path) -> Path:
    if spec.startswith(MEMORY_PREFIX):
        return memory_dir(root) / spec[len(MEMORY_PREFIX):]
    p = Path(os.path.expanduser(spec))
    return p if p.is_absolute() else root / p


def _read(spec: str, root: Path) -> str:
    path = resolve(spec, root)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProbeError(f"cannot read {spec}: {type(exc).__name__}") from exc


def count_lines(spec: str, pattern: str, root: Path, ignore_case: bool = False) -> int:
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ProbeError(f"bad pattern {pattern!r}: {exc}") from exc
    return sum(1 for line in _read(spec, root).splitlines() if rx.search(line))


def frontmatter(spec: str, key: str, root: Path):
    text = _read(spec, root)
    if not text.startswith("---\n") or "\n---" not in text[4:]:
        raise ProbeError(f"{spec} has no frontmatter block")
    head = text[4:text.index("\n---", 4)]
    for line in head.splitlines():
        m = re.match(r"\s*" + re.escape(key) + r"\s*:\s*(.*?)\s*$", line)
        if m:
            raw = m.group(1).strip("\"'")
            return int(raw) if re.fullmatch(r"-?\d+", raw) else raw
    raise ProbeError(f"{spec} frontmatter has no `{key}`")


def _dig(data, dotted: str):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ProbeError(f"command output has no `{dotted}`")
        cur = cur[part]
    return cur


def lift(argv: list[str], root: Path, lens: list[str], field: str | None,
         run=subprocess.run, ok_exit=(0,)):
    if not argv:
        raise ProbeError("lift needs a command after --")
    if bool(lens) == bool(field):
        raise ProbeError("lift takes --len (one or more) or --field, not both and not neither")
    try:
        r = run(argv, cwd=str(root), capture_output=True, text=True, timeout=LIFT_TIMEOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeError(f"command did not run: {type(exc).__name__}") from exc
    # A command that failed may still print JSON (an error object, a partial result).
    # Reading a count out of that reports a failure as a measurement.
    if r.returncode not in ok_exit:
        raise ProbeError(f"command exited {r.returncode}, expected one of {sorted(ok_exit)}")
    try:
        data = json.loads(r.stdout)
    except ValueError as exc:
        raise ProbeError(f"command stdout is not JSON (exit {r.returncode})") from exc
    if field:
        return _dig(data, field)
    sizes = []
    for key in lens:
        value = _dig(data, key)
        if not isinstance(value, (list, dict)):
            raise ProbeError(f"`{key}` is not a list or object, so it has no length")
        sizes.append((key, len(value)))
    if len(sizes) == 1:
        return sizes[0][1]
    return " | ".join(f"{k}={n}" for k, n in sizes)


def const_len(specs: list[str], root: Path):
    sizes = []
    for spec in specs:
        file, sep, name = spec.rpartition(":")
        if not sep or not file or not name:
            raise ProbeError(f"const-len takes FILE:NAME, got {spec!r}")
        try:
            tree = ast.parse(_read(file, root))
        except SyntaxError as exc:
            raise ProbeError(f"{file} does not parse") from exc
        found = None
        for node in tree.body:
            target = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value
            if isinstance(target, ast.Name) and target.id == name:
                if isinstance(value, ast.Dict):
                    found = len(value.keys)
                elif isinstance(value, (ast.Set, ast.List, ast.Tuple)):
                    found = len(value.elts)
                else:
                    raise ProbeError(f"{spec} is not a dict, set, list or tuple literal")
        if found is None:
            raise ProbeError(f"{file} has no module-level `{name}`")
        sizes.append((name, found))
    if len(sizes) == 1:
        return sizes[0][1]
    return " | ".join(f"{k}={n}" for k, n in sizes)


def main(argv: list[str] | None = None, root: Path = REPO_ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("count-lines")
    p.add_argument("path")
    p.add_argument("regex")
    p.add_argument("--ignore-case", action="store_true")
    p = sub.add_parser("frontmatter")
    p.add_argument("path")
    p.add_argument("key")
    p = sub.add_parser("lift")
    p.add_argument("--len", action="append", default=[], dest="lens")
    p.add_argument("--field", default=None)
    p.add_argument("--ok-exit", action="append", type=int, default=[], dest="ok_exit",
                   help="an exit code that means the command RAN (default 0; repeatable)")
    p.add_argument("argv", nargs=argparse.REMAINDER)
    p = sub.add_parser("const-len")
    p.add_argument("specs", nargs="+")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "count-lines":
            value = count_lines(a.path, a.regex, root, a.ignore_case)
        elif a.cmd == "frontmatter":
            value = frontmatter(a.path, a.key, root)
        elif a.cmd == "lift":
            value = lift(a.argv[1:] if a.argv[:1] == ["--"] else a.argv,
                         root, a.lens, a.field, ok_exit=tuple(a.ok_exit) or (0,))
        else:
            value = const_len(a.specs, root)
    except ProbeError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps({"value": value}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
