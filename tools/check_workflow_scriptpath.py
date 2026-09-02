#!/usr/bin/env python3
"""
check_workflow_scriptpath.py — PreToolUse hook for the `Workflow` tool.

WHY THIS EXISTS
---------------
`Workflow({name: "plan-hardening"})` does NOT run the file on disk. It resolves a
**cached snapshot** of the workflow script that was persisted under
`<session>/workflows/scripts/<name>-<runId>.js`. Nothing in the launch output says
which version is running, so the failure is silent AND expensive: the run executes,
produces plausible output, and the output came from the wrong code.

Measured, 2026-08-21, two fires in one session
(memory/feedback_workflow_name_resolves_stale_script_snapshot.md):
  1. A run launched by `name` at 09:43 executed the **v1** script (24,410 bytes)
     while **v2** (28,622 bytes) had been on disk since 09:41. ~40 minutes and
     millions of tokens spent on a design already measured as non-converging.
     Detected only by reading the persisted script's `meta.description`.
  2. Hours later a relaunch by `name` shipped the 28,622-byte snapshot while disk
     held 30,360 — silently omitting an INVARIANT 6 guard added minutes earlier.
     **That second fire happened while the operator already knew about the first**,
     which is the tell that vigilance is not the fix.

WHAT IT CHECKS (properties, not presence)
-----------------------------------------
This hook does not ask "did you fill in a field." It asks three factual questions
about the launch payload:

  A. Is the launch keyed by `name` with no `scriptPath`? That launch provably
     cannot read the on-disk file — it is the origin failure, verbatim.
  B. Does `scriptPath` RESOLVE TO A FILE ON DISK? A scriptPath that does not
     resolve is worse than `name`: it looks compliant and may silently fall back.
     Resolution is attempted against the payload `cwd`, `$CLAUDE_PROJECT_DIR`,
     and the process cwd before the path is declared dead.
  C. Does `scriptPath` point INTO a persisted snapshot directory
     (`.../workflows/scripts/<name>-<runId>.js`)? That is literally the stale
     cached copy — passing it via scriptPath re-runs the exact artifact this rule
     exists to prevent, while looking like compliance.

BLOCK tier (exit 2). Per memory/feedback_warn_vs_block_hook_design.md: PreToolUse
stderr at exit 0 is never surfaced by Claude Code, so a "warning" here would reach
nobody. The violation is unambiguous and has a single known correction (swap
`name:` for `scriptPath:` with the repo-relative path), and the cost of letting it
through is a multi-million-token run of the wrong code — exactly the profile that
justifies BLOCK.

FAIL-OPEN cases (exit 0): unparseable JSON, a non-Workflow tool, an empty payload,
and any call carrying neither `name` nor `scriptPath` (status/list/resume-only
calls, which do not launch a script from source at all).

Hook input: JSON via stdin from Claude Code.
  {"tool_name": "Workflow", "cwd": "...", "tool_input": {"name"|"scriptPath": ...}}

Exit codes:
  0 — clean (scriptPath resolves to a real file outside the snapshot dir)
  2 — BLOCKED: `name` launch, unresolvable scriptPath, or a snapshot-dir scriptPath

Origin: memory/feedback_workflow_name_resolves_stale_script_snapshot.md (2026-08-21).
"""
import json
import os
import re
import sys

# `<anything>/workflows/scripts/<file>` is the persisted per-run snapshot location.
# Pointing scriptPath there re-runs the cached copy under a compliant-looking key.
SNAPSHOT_DIR = re.compile(r"(?:^|/)workflows/scripts/")


def _candidate_roots(data: dict) -> list:
    """Directories a repo-relative scriptPath could legitimately resolve against."""
    roots = []
    for value in (data.get("cwd"), os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()):
        if isinstance(value, str) and value and value not in roots:
            roots.append(value)
    return roots


def resolve_script_path(script_path: str, data: dict):
    """Return the first existing file this scriptPath names, or None.

    Absolute paths are checked as given. Relative paths are tried against the
    payload cwd, $CLAUDE_PROJECT_DIR, and the process cwd, in that order.
    """
    if not isinstance(script_path, str) or not script_path.strip():
        return None
    script_path = script_path.strip()
    if os.path.isabs(script_path):
        return script_path if os.path.isfile(script_path) else None
    for root in _candidate_roots(data):
        candidate = os.path.join(root, script_path)
        if os.path.isfile(candidate):
            return candidate
    return None


def _block(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    tool_name = data.get("tool_name") or ""
    # Wired on a `Workflow` matcher, but stay inert if it ever sees another tool.
    if tool_name and tool_name != "Workflow":
        sys.exit(0)

    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        sys.exit(0)

    script_path = tool_input.get("scriptPath")
    name = tool_input.get("name")

    if not script_path:
        if not name:
            # No launch key at all (status/list/resume-only) — nothing to judge.
            sys.exit(0)
        _block(
            "BLOCKED: Workflow launched by `name` ({!r}). `name` resolves a CACHED "
            "SNAPSHOT of the workflow script, not the file on disk, and the launch "
            "output does not say which version it ran.\n"
            "\n"
            "Use scriptPath instead:\n"
            '  Workflow({{scriptPath: ".claude/workflows/<workflow>.js", args: {{...}}}})\n'
            "\n"
            "Then confirm the echoed `Script file:` names the repo path, not a path "
            "under <session>/workflows/scripts/.\n"
            "Measured cost of skipping this: a ~40-minute, multi-million-token run of "
            "v1 while v2 sat on disk (2026-08-21, fired twice in one session).\n"
            "Reference: memory rule "
            "[[feedback_workflow_name_resolves_stale_script_snapshot]].\n".format(name),
        )

    if not isinstance(script_path, str) or not script_path.strip():
        _block(
            "BLOCKED: Workflow `scriptPath` is empty or not a string. Pass the "
            "repo-relative path to the workflow file, e.g. "
            '".claude/workflows/plan-hardening.js".\n'
            "Reference: memory rule "
            "[[feedback_workflow_name_resolves_stale_script_snapshot]].\n"
        )

    if SNAPSHOT_DIR.search(script_path.strip()):
        _block(
            "BLOCKED: Workflow `scriptPath` points into a persisted snapshot "
            "directory ({}). That IS the stale cached script — running it reproduces "
            "the exact failure `scriptPath` exists to prevent.\n"
            "\n"
            "Point scriptPath at the source file in the repo instead:\n"
            '  Workflow({{scriptPath: ".claude/workflows/<workflow>.js", args: {{...}}}})\n'
            "Reference: memory rule "
            "[[feedback_workflow_name_resolves_stale_script_snapshot]].\n".format(
                script_path.strip()
            )
        )

    resolved = resolve_script_path(script_path, data)
    if resolved is None:
        roots = ", ".join(_candidate_roots(data)) or "(no roots available)"
        _block(
            "BLOCKED: Workflow `scriptPath` does not resolve to a file on disk: "
            "{}\n"
            "Roots tried: {}\n"
            "\n"
            "A scriptPath that does not resolve is worse than `name` — it looks "
            "compliant while nothing verified that the current script exists. Fix "
            "the path (or `ls` the workflows directory) before launching an "
            "expensive run.\n"
            "Reference: memory rule "
            "[[feedback_workflow_name_resolves_stale_script_snapshot]].\n".format(
                script_path.strip(), roots
            )
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
