#!/usr/bin/env python3
"""check_hook_warn_tier.py -- a hook that writes to stderr and exits 0 reaches NOBODY.

Claude Code does not surface hook stderr to the agent at exit 0. A hook built that way is
a detector wired to nothing: it runs, it prints, and every downstream reader assumes it
fired. `.claude/settings.json` wires ~29 tools across PreToolUse / PostToolUse /
PostToolUseFailure / Stop, so this is not hypothetical.

This is the guard test named in the reopen_gate of `feedback_warn_vs_block_hook_design`
("Build the guard test over tools/check_*.py"), whose 4th fire WAS a hook of exactly this
shape: check_changelog_currency.py, a Stop hook, silent across six pushes while its own
docstring claimed "lives on Stop where stderr surfaces."

THE PROPERTY CHECKED. For every tool wired as a hook in settings.json:

    writes to stderr  AND  cannot terminate with code 2   ->  VIOLATION

"Cannot terminate with code 2" is decided over the whole module: any sys.exit(2), exit(2),
or `return 2` anywhere makes the tool blocking-capable. That is deliberately generous.
These tools follow one shape (sys.exit(main()), helpers returning the code), so a module
with no 2 anywhere provably cannot block, while a module with a 2 somewhere is at least
capable of it. Being generous matters: an earlier version looked only at sys.exit args and
main()'s returns and produced a FALSE POSITIVE on check_prep_doc_format.py, which blocks
via `return 2` from a helper. A guard that cries wolf on a correctly-wired hook becomes
the thing everyone routes around.

WHY NOT AUTO-DISTINGUISH GATE FROM LOGGER. Some exit-0 hooks are correct: a logger whose
real output is a file (log_tool_failure.py, scan_transcript_failures.py) writes to stderr
only incidentally. No static analysis separates "logger, exit 0 is right" from "gate, exit
0 is a bug" -- that is design intent, not a code property. So intent must be DECLARED, in
tools/hook-warn-allow.json, with a written reason per tool. An entry with an empty reason
fails, for the same reason tools/mutation-allow.json requires one: an allowlist without
justification is how this decays back into "green means done."

That converts every warn-only hook from accidental to deliberate-and-written-down, which
is the actual fix. A hook nobody chose to make silent is the failure mode.

Exit codes:
    0  every wired hook can block, or is allowlisted with a reason
    2  at least one wired hook writes to stderr, cannot block, and is not allowlisted
    1  bad usage / unreadable inputs
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
DEFAULT_ALLOW = REPO_ROOT / "tools" / "hook-warn-allow.json"

_TOOL_IN_COMMAND = re.compile(r"tools/([A-Za-z0-9_]+\.py)")


def wired_hooks(settings: dict) -> dict:
    """Map tool filename -> set of hook events it is wired under."""
    out = {}
    for event, groups in (settings.get("hooks") or {}).items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            for hook in (group or {}).get("hooks", []) or []:
                for tool in _TOOL_IN_COMMAND.findall((hook or {}).get("command", "") or ""):
                    out.setdefault(tool, set()).add(event)
    return out


def writes_stderr(tree) -> bool:
    """Any call routing output to stderr: `f(..., file=sys.stderr)` or `sys.stderr.write(...)`.

    Deliberately NOT restricted to the name `print`. An earlier version keyed on
    `func.id == "print"`, which mutation testing exposed as a real hole: a hook using a
    logging helper, a partial, or any wrapper taking `file=sys.stderr` was read as writing
    nothing and silently passed the audit. The kwarg is the property that matters; the
    callee name is not.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "file" and ast.unparse(kw.value).endswith("stderr"):
                return True
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "write"
                and ast.unparse(func.value).endswith("stderr")):
            return True
    return False


def can_block(tree) -> bool:
    """True when the module can terminate with code 2, by exit call or by return value."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func) in ("sys.exit", "exit", "os._exit"):
            for arg in node.args:
                try:
                    if ast.literal_eval(arg) == 2:
                        return True
                except (ValueError, TypeError, SyntaxError):
                    continue
        if isinstance(node, ast.Return) and node.value is not None:
            try:
                if ast.literal_eval(node.value) == 2:
                    return True
            except (ValueError, TypeError, SyntaxError):
                continue
    return False


def load_allow(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object of tool -> reason")
    return data


def audit(settings_path: Path, tools_dir: Path, allow: dict) -> dict:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    wired = wired_hooks(settings)
    if not wired:
        raise ValueError(f"no tools/*.py hooks wired in {settings_path}")

    violations = []
    silent = []
    checked = 0

    for tool in sorted(wired):
        path = tools_dir / tool
        if not path.is_file():
            violations.append(f"{tool}: wired in settings.json under {sorted(wired[tool])} "
                              f"but not present in {tools_dir}")
            continue
        checked += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not writes_stderr(tree) or can_block(tree):
            continue
        silent.append({"tool": tool, "events": sorted(wired[tool]),
                       "allowlisted": tool in allow})
        if tool not in allow:
            violations.append(
                f"{tool}: wired under {sorted(wired[tool])}, writes to stderr, and cannot "
                "terminate with code 2, so nothing it prints reaches the agent. Either make "
                f"it exit 2, or declare it warn-only with a written reason in {DEFAULT_ALLOW.name}")

    for tool, reason in allow.items():
        if not str(reason).strip():
            violations.append(f"{tool}: allowlist entry has an empty reason. An allowlist "
                              "without justification is how this decays back into green-means-done")
        elif tool not in wired:
            violations.append(f"{tool}: allowlisted as a warn-only hook but is not wired in "
                              "settings.json. Remove the stale entry")

    return {"wired": len(wired), "checked": checked, "silent": silent,
            "violations": violations, "ok": not violations}


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    ap.add_argument("--tools-dir", type=Path, default=REPO_ROOT / "tools")
    ap.add_argument("--allow", type=Path, default=DEFAULT_ALLOW)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.settings.is_file():
        print(f"settings file not found: {args.settings}", file=sys.stderr)
        return 1
    try:
        report = audit(args.settings, args.tools_dir, load_allow(args.allow))
    except (ValueError, SyntaxError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        allow_n = sum(1 for s in report["silent"] if s["allowlisted"])
        print(f"wired {report['wired']} | checked {report['checked']} | "
              f"stderr-only {len(report['silent'])} ({allow_n} declared warn-only)")
        for v in report["violations"]:
            print(f"  VIOLATION {v}")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
