#!/usr/bin/env python3
"""
log_tool_failure.py — PostToolUseFailure hook for Claude Code.

Fires on tool-call failures (Bash non-zero exit, Edit/Write tool_use_errors,
PreToolUse hook BLOCK responses). Captures the friction surface and appends
to memory/friction-log.md via friction_log.py — bypassing the PostToolUse
gap where successful-only hooks never see errors (per GitHub issue #6371,
documented behavior; PostToolUseFailure was added to address it).

Input schema (from stdin JSON, per Claude Code hooks reference):
  {
    "session_id": "...",
    "hook_event_name": "PostToolUseFailure",
    "tool_name": "Bash" | "Edit" | "Write" | ...,
    "tool_input": { ... tool-specific ... },
    # Failure payload — Claude Code docs are incomplete here (issue #19372).
    # Defensive: try multiple shapes.
    "failure_details": { "exit_code": int, "stdout": str, "stderr": str, "error_type": str },
    # OR flatter:
    "error": str,
    "exit_code": int,
    # OR (older shape, may coexist):
    "tool_response": { "stdout": str, "stderr": str }
  }

Exit codes: always 0 (WARN-tier, never blocks).
"""
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import friction_surface as fs  # shared surface/nature derivation (single source of truth)
import hook_trace  # shared rotating trace-log writer (size-capped, single source of truth)

REPO_ROOT = Path(__file__).resolve().parents[1]
FRICTION_LOG_PY = REPO_ROOT / "tools" / "friction_log.py"
TRACE_LOG = REPO_ROOT / "tools" / ".hook-trace.log"

# Scripts that should never trigger logging (infinite-loop guard)
EXCLUDE_SCRIPTS = fs.EXCLUDE_SCRIPTS

# Skip help-call failures (intentional non-zero exits)
HELP_RE = re.compile(r"\s--help\b|\s-h\b")

# Skip "graceful empty" patterns — atomic-script returns that use status:error
# as a "nothing to do" signal rather than a real defect.
GRACEFUL_EMPTY_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'"message"\s*:\s*"No task found',
        r'"message"\s*:\s*"No (match|matches|results?)',
        r'"message"\s*:\s*"Nothing to ',
        r'"message"\s*:\s*"Already (closed|done|completed)',
        r'"message"\s*:\s*"(File|Entry) not found',
    ]
]

# Pull a parseable script name from a Bash command (shared definition)
SCRIPT_RE = fs.SCRIPT_RE


def trace(msg: str) -> None:
    """Best-effort diagnostic trace. Never throws. Size-capped via hook_trace."""
    hook_trace.append(TRACE_LOG, f"[{datetime.datetime.now().isoformat()}] {msg}")


def extract_failure_text(data: dict) -> str:
    """Pull the most informative error text from whichever shape the hook gives us."""
    fd = data.get("failure_details") or {}
    if isinstance(fd, dict):
        for key in ("stderr", "stdout", "error_type"):
            v = fd.get(key)
            if v:
                return str(v)
    err = data.get("error")
    if err:
        return str(err)
    resp = data.get("tool_response") or {}
    if isinstance(resp, dict):
        for key in ("stderr", "stdout"):
            v = resp.get(key)
            if v:
                return str(v)
    return "tool failed (no parseable error text)"


def extract_exit_code(data: dict) -> str:
    fd = data.get("failure_details") or {}
    if isinstance(fd, dict) and "exit_code" in fd:
        return str(fd["exit_code"])
    if "exit_code" in data:
        return str(data["exit_code"])
    return "?"


def derive_surface(tool_name: str, tool_input: dict, error_text: str) -> str:
    """The 'surface' is what failed. Delegates to the shared module, which now
    attributes PreToolUse blocks and inline `python3 -c` failures correctly
    (was bash:git / bash:cd). See tools/friction_surface.py."""
    command = (tool_input or {}).get("command", "") or ""
    return fs.derive_surface(tool_name, command, error_text)


def derive_nature(error_text: str) -> str:
    """One-line summary for the friction-log table cell (shared, [auto] tag)."""
    return fs.derive_nature(error_text, "auto")


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw else {}
    except Exception as e:
        trace(f"log_tool_failure STDIN_PARSE_FAIL: {e!r} raw_len={len(raw)} head={raw[:200]!r}")
        return 0

    trace(f"log_tool_failure INVOKED event={data.get('hook_event_name')!r} tool={data.get('tool_name')!r} keys={list(data.keys())!r}")

    tool_name = (data.get("tool_name") or "").strip()
    tool_input = data.get("tool_input") or {}

    # Filter help-call failures (intentional non-zero from --help/-h)
    if tool_name == "Bash":
        cmd = tool_input.get("command", "") or ""
        if HELP_RE.search(cmd):
            return 0
        # Excluded scripts (infinite-loop guard)
        matches = SCRIPT_RE.findall(cmd)
        if matches and all((m + ".py").lower() in EXCLUDE_SCRIPTS for m in matches):
            return 0

    error_text = extract_failure_text(data)
    exit_code = extract_exit_code(data)

    # Skip graceful-empty signals
    for pat in GRACEFUL_EMPTY_PATTERNS:
        if pat.search(error_text):
            trace(f"log_tool_failure SKIP graceful-empty match")
            return 0

    # Skip unresolved-exit failures whose only extractable text has no error
    # signal — almost always a chained/piped command whose real (benign)
    # failure point left no text, and what we captured is an earlier stage's
    # successful stdout. See friction_surface.looks_like_real_error docstring.
    if exit_code == "?" and not fs.looks_like_real_error(error_text):
        trace("log_tool_failure SKIP unresolved-exit + no error signal (likely masked benign nonzero)")
        return 0

    surface = derive_surface(tool_name, tool_input, error_text)
    nature = derive_nature(error_text)

    # Pre-flight: skip if surface is unparseable (defensive)
    if not surface or surface == "bash:":
        trace(f"log_tool_failure SKIP unparseable surface")
        return 0

    # Append via friction_log.py (handles dedup internally)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run(
            [
                "python3",
                str(FRICTION_LOG_PY),
                "append",
                surface,
                nature,
                "--fix",
                f"[auto-logged at error time, exit={exit_code}; update via friction_log.py append once resolved]",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except Exception as e:
        trace(f"log_tool_failure SUBPROCESS_FAIL: {e!r}")
        return 0

    out = (result.stdout or "").strip()
    try:
        parsed = json.loads(out.splitlines()[-1]) if out else {}
    except Exception:
        parsed = {}

    occurrences = parsed.get("occurrences", "?")
    promotion = parsed.get("promotion_action", "none")

    sys.stderr.write(
        f"✓ friction-log auto-logged: {surface} (occurrence {occurrences}, promotion={promotion}).\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
