#!/usr/bin/env python3
"""
check_script_error_logged.py — PostToolUse hook for Bash calls.

Detects when a `tools/*.py` script returned a JSON error and AUTO-INVOKES
`tools/friction_log.py append` to record the friction mechanically. Claude
no longer needs to remember to log — the hook handles it.

If the same (surface, nature) was already logged in the last few rows
of the ledger (retry/duplicate), the hook skips to avoid spurious
occurrence-count inflation.

Triggered by .claude/settings.json PostToolUse hook on Bash calls.
WARN-tier: exit 0 always, never blocks workflow.

Exclusions (no logging):
  - The friction_log.py script itself (infinite-loop guard).
  - Help/--help calls (these intentionally exit nonzero with usage JSON).
  - Empty stdout (no JSON to inspect).

After auto-logging, the hook writes a one-line summary to stderr including
the occurrence count and any promotion action triggered. Claude may then
update the row's --fix description by calling friction_log.py append again
once the actual fix is applied (the dedup window prevents double-counting).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRICTION_LOG_PY = REPO_ROOT / "tools" / "friction_log.py"
LEDGER = REPO_ROOT / "memory" / "friction-log.md"

# Pattern: tools/*.py that is actually invoked by python (avoid matching
# tools/*.py paths embedded inside echo'd JSON, quoted strings, or comments).
SCRIPT_RE = re.compile(
    r"\bpython3?\b[^\n]*?\btools/([a-z0-9_]+)\.py\b", re.IGNORECASE
)

# Scripts that should never trigger logging themselves
EXCLUDE_SCRIPTS = {"friction_log.py", "check_script_error_logged.py"}

ERROR_MARKER_RE = re.compile(r'"status"\s*:\s*"error"')
# JSON message field (best-effort extraction; non-greedy)
JSON_MESSAGE_RE = re.compile(r'"message"\s*:\s*"([^"]{1,300})"')

# Graceful-empty patterns — scripts that use {"status": "error"} as a "nothing
# to do" signal rather than a real defect (sweep operations, idempotent done,
# query-no-match). Skip auto-logging these — they're expected returns, not
# friction. Caught 2026-05-21 when /checkout Step 4b sweep on absent prep todos
# auto-logged "No task found matching: ..." as friction.
GRACEFUL_EMPTY_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'"message"\s*:\s*"No task found',
        r'"message"\s*:\s*"No (match|matches|results?)',
        r'"message"\s*:\s*"Nothing to ',
        r'"message"\s*:\s*"Already (closed|done|completed)',
        r'"message"\s*:\s*"(File|Entry) not found',
    ]
]

# Dedup: skip if the same (surface, nature[:40]) appears in the last N rows
DEDUP_LOOKBACK_ROWS = 5


def extract_nature(combined: str, surface: str) -> str:
    """Pull a concise one-line nature from script error output."""
    # Prefer JSON "message" field (cap length — long Usage strings clutter the ledger)
    m = JSON_MESSAGE_RE.search(combined)
    if m:
        nature = m.group(1).strip()[:120]
    else:
        # Fall back: first non-empty line of stderr/stdout, stripped
        for line in combined.splitlines():
            line = line.strip()
            if line and "Exit code" not in line and len(line) > 5:
                nature = line[:200]
                break
        else:
            nature = "script returned error (no parseable message)"
    # Sanitize for table cell: no pipes, no newlines
    nature = nature.replace("|", "/").replace("\n", " ").replace("\r", " ")
    # Add auto-tag so we know this came from the hook (vs Claude calling explicitly)
    return f"[auto] {nature}"


def is_recent_duplicate(surface: str, nature: str) -> bool:
    """Check whether the same friction was logged in the last N ledger rows."""
    if not LEDGER.exists():
        return False
    try:
        raw = LEDGER.read_text(encoding="utf-8")
    except Exception:
        return False
    if "## Entries" not in raw:
        return False
    post = raw.split("## Entries", 1)[1]
    # Pull data rows (lines starting with '| ' that aren't header/separator)
    data_rows = []
    for line in post.splitlines():
        line = line.rstrip()
        if not line.startswith("|"):
            continue
        if line.startswith("| Date ") or set(line.replace("|", "").strip()) == {"-"}:
            continue
        data_rows.append(line)
        if len(data_rows) >= DEDUP_LOOKBACK_ROWS:
            break
    surface_norm = surface.strip().lower()
    nature_key = nature[:40].lower()
    for row in data_rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) < 3:
            continue
        row_surface = cells[1].lower()
        row_nature = cells[2].lower()
        if row_surface == surface_norm and row_nature[:40] == nature_key:
            return True
    return False


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = (data.get("tool_name") or "").strip()
    if tool_name != "Bash":
        return 0

    tool_input = data.get("tool_input") or {}
    command = (tool_input.get("command") or "").strip()
    if not command:
        return 0

    matches = SCRIPT_RE.findall(command)
    if not matches:
        return 0

    scripts = [(m + ".py").lower() for m in matches]
    scripts = [s for s in scripts if s not in EXCLUDE_SCRIPTS]
    if not scripts:
        return 0

    if re.search(r"\s--help\b|\s-h\b", command):
        return 0

    tool_response = data.get("tool_response") or {}
    stdout = tool_response.get("stdout") or ""
    stderr = tool_response.get("stderr") or ""
    combined = stdout + "\n" + stderr

    if not ERROR_MARKER_RE.search(combined):
        # Strict signal only: the atomic-script contract is {"status": "error"}.
        # Earlier fallback ("error" lowercase anywhere) matched too broadly —
        # caught the word inside successful output (e.g., todo descriptions
        # mentioning "error handling") and produced false-positive auto-logs
        # (caught 2026-05-21 on todo_daily_metrics.py success path).
        return 0

    # Skip graceful-empty patterns (sweeps, idempotent done, query-no-match).
    # These use status:error as a "nothing to do" signal — not real friction.
    for pat in GRACEFUL_EMPTY_PATTERNS:
        if pat.search(combined):
            return 0

    surface = scripts[0]
    nature = extract_nature(combined, surface)

    if is_recent_duplicate(surface, nature):
        sys.stderr.write(
            f"friction-log: skipping duplicate ({surface} same nature in last "
            f"{DEDUP_LOOKBACK_ROWS} rows).\n"
        )
        return 0

    # Auto-invoke friction_log.py append
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
                "[auto-logged at error time; update via friction_log.py append once fixed]",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except Exception as e:
        sys.stderr.write(f"friction-log: auto-log failed ({e}); call manually.\n")
        return 0

    out = (result.stdout or "").strip()
    try:
        parsed = json.loads(out.splitlines()[-1]) if out else {}
    except Exception:
        parsed = {}

    occurrences = parsed.get("occurrences", "?")
    promotion = parsed.get("promotion_action", "none")

    if promotion == "none":
        sys.stderr.write(
            f"✓ friction-log auto-logged: {surface} (occurrence {occurrences}). "
            f"Update --fix via friction_log.py append once resolved.\n"
        )
    elif promotion == "memory":
        sys.stderr.write(
            f"⚠️  friction-log auto-logged: {surface} (occurrence {occurrences}). "
            f"PROMOTION TRIGGERED: create memory/feedback_<slug>.md NOW per "
            f"[[friction-log-before-fix]]. Do not defer.\n"
        )
    elif promotion == "script-patch":
        sys.stderr.write(
            f"🚨 friction-log auto-logged: {surface} (occurrence {occurrences}). "
            f"MANDATORY SCRIPT PATCH: fix the underlying script/hook/template "
            f"in this session, no defer. Per [[friction-log-before-fix]].\n"
        )
    else:
        sys.stderr.write(
            f"friction-log auto-logged: {surface} (occurrence {occurrences}, "
            f"promotion={promotion}).\n"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
