#!/usr/bin/env python3
"""memory_write.py: the command-line writer for memory-rule frontmatter state.

WHY IT EXISTS (B2 of the 2026-11-22 learning-loop build, org job-search-68). A replay of
every Bash call in the session transcripts that wrote a memory rule file found state
fields (`promoted`, `occurrences`, `last_cited`, ...) changed with `sed -i` or an inline
Python `str.replace`. Nothing validated those edits, so a typo'd token or a broken YAML
line landed silently. The write-time hook (check_memory_frontmatter_write.py) now blocks
those shapes and routes them here, where every change goes through
`memory_frontmatter.write()` or `memory_frontmatter.record_fire()`.

Subcommands:

  set <file> <key>=<value> [<key>=<value> ...]
      Set frontmatter keys through memory_frontmatter.write(): validated against
      learning_state, atomic, nothing else in the file changes. `occurrences` is refused:
      a count is never machine-written (state matrix; Nick's 2026-09-15 decision). Use
      `bump` to record a fire you witnessed.

  bump <file> --fire "<what happened, YYYY-MM-DD>"
      Record ONE fire you witnessed: occurrences + 1 and a dated evidence line at the end
      of the body, through memory_frontmatter.record_fire(), the only code path that
      changes `occurrences`.

WHO MAY CALL IT. A session recording what it saw. No detector, launchd job, scan or other
automatic path may call `bump` (or `set` on a count it inferred): a count records a
witnessed event. Enforcement is a TRIPWIRE, not a boundary: both subcommands refuse unless
CLAUDECODE=1 is in the environment, which Claude Code sets in every Bash call and launchd
and cron do not. Headless Claude jobs (`claude -p`, codex_verify, mutation runs) also
carry CLAUDECODE, so the tripwire does not stop them; the rule above does.

<file> is a rule file name (resolved in the live memory folder of the main checkout) or a
path. --memory-dir overrides the folder (tests, dry runs on a copy).

Output: one JSON object on stdout. Exit 0 on success, 1 when the write was refused
(nothing written; `reasons` lists why), 2 on a usage error.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/memory_write.py set feedback_x.md promoted="hook, 2026-11-22" reopen_at=3
  PYTHONIOENCODING=utf-8 python3 tools/memory_write.py bump feedback_x.md --fire "2026-11-22: blocked a sed on lessons.md"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_frontmatter as mf  # noqa: E402
from hook_runtime import claude_project_dir  # noqa: E402
from vault_paths import main_checkout  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

# Pointers (a superseded path, a skill_file) resolve against the MAIN checkout: gitignored
# targets (framework/, memory/, data/) exist only there, so a run from a worktree would
# refuse a valid pointer (77's finding, 2026-11-22).
POINTER_ROOT = main_checkout(REPO_ROOT)
TRIPWIRE_ENV = "CLAUDECODE"


def default_memory_dir() -> Path:
    """The live memory tier of the main checkout (a worktree resolves to main's folder)."""
    return claude_project_dir(main_checkout(REPO_ROOT)) / "memory"


def resolve(spec: str, memory_dir: Path) -> Path:
    """A bare file name resolves inside memory_dir; anything with a slash is a path."""
    if "/" in spec:
        return Path(os.path.expanduser(spec))
    return memory_dir / spec


def tripwire_refusal() -> str | None:
    if os.environ.get(TRIPWIRE_ENV) != "1":
        return (f"{TRIPWIRE_ENV}=1 is not set: memory_write records what a session "
                f"witnessed and is not for detectors, launchd or cron jobs")
    return None


# Keys the state matrix types as integers. A CLI value is a string, and the validator
# refuses reopen_at="3" (Grok r1 F1, 2026-11-22), so a digit string becomes an int here.
INT_KEYS = frozenset({"reopen_at"})


def parse_assignments(pairs: list[str]) -> tuple[dict, list[str]]:
    updates: dict = {}
    errors: list[str] = []
    for pair in pairs:
        key, sep, value = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            errors.append(f"{pair!r}: expected key=value")
        elif key in updates:
            errors.append(f"{key}: given twice")
        else:
            updates[key] = int(value) if key in INT_KEYS and value.isdigit() else value
    return updates, errors


def emit(payload: dict, code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return code


def cmd_set(args, memory_dir: Path) -> int:
    updates, errors = parse_assignments(args.assignments)
    if errors:
        return emit({"action": "usage_error", "reasons": errors}, 2)
    refused = sorted(k for k in updates if k in mf.NEVER_MACHINE_WRITTEN)
    if refused:
        return emit({"action": "refused", "file": str(args.path),
                     "reasons": [f"{k}: never machine-written; record a witnessed fire "
                                 f"with `memory_write.py bump <file> --fire ...`"
                                 for k in refused]}, 1)
    try:
        fields = mf.write(args.path, updates, memory_dir=memory_dir, repo_root=POINTER_ROOT)
    except mf.WriteRefused as exc:
        return emit({"action": "refused", "file": str(args.path),
                     "reasons": list(exc.reasons)}, 1)
    return emit({"action": "set", "file": str(args.path),
                 "set": {k: fields.get(k) for k in updates}}, 0)


def cmd_bump(args, memory_dir: Path) -> int:
    try:
        n = mf.record_fire(args.path, args.fire)
    except mf.WriteRefused as exc:
        return emit({"action": "refused", "file": str(args.path),
                     "reasons": list(exc.reasons)}, 1)
    return emit({"action": "bumped", "file": str(args.path), "occurrences": n}, 0)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="memory_write.py", description=__doc__.split("\n")[0])
    p.add_argument("--memory-dir", type=Path, default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("set", help="set frontmatter keys (never occurrences)")
    ps.add_argument("file")
    ps.add_argument("assignments", nargs="+", metavar="key=value")
    pb = sub.add_parser("bump", help="record one witnessed fire")
    pb.add_argument("file")
    pb.add_argument("--fire", required=True, help="what happened, with a YYYY-MM-DD date")
    args = p.parse_args(argv)

    refusal = tripwire_refusal()
    if refusal:
        return emit({"action": "refused", "reasons": [refusal]}, 1)
    memory_dir = args.memory_dir or default_memory_dir()
    args.path = resolve(args.file, memory_dir)
    if not args.path.is_file():
        return emit({"action": "refused", "file": str(args.path),
                     "reasons": [f"no such file: {args.path}"]}, 1)
    return cmd_set(args, memory_dir) if args.cmd == "set" else cmd_bump(args, memory_dir)


if __name__ == "__main__":
    sys.exit(main())
