#!/usr/bin/env python3
"""
backfill_transcript_failures.py — one-shot recovery of friction events
missed by the broken-PostToolUse-on-error hook design (2026-05-21 → 2026-05-27).

Walks every Claude Code session transcript JSONL in
~/.claude/projects/-Users-mag-Documents-Obsidian-30-projects-job-search/,
finds all tool_result entries with is_error: true, and appends a friction-log
row for each (with [auto-backfill] tag). friction_log.py's 5-row dedup window
will collapse repeated identical errors so the ledger doesn't explode.

Usage:
  python3 tools/backfill_transcript_failures.py              # all sessions
  python3 tools/backfill_transcript_failures.py --since 2026-05-21
  python3 tools/backfill_transcript_failures.py --dry-run    # report counts only
  python3 tools/backfill_transcript_failures.py --skip-session <id>  # skip current session

Output: JSON summary on stdout.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRICTION_LOG_PY = REPO_ROOT / "tools" / "friction_log.py"

PROJECT_DIR_HASH = "-Users-mag-Documents-Obsidian-30-projects-job-search"
SESSIONS_DIR = Path.home() / ".claude" / "projects" / PROJECT_DIR_HASH

EXCLUDE_SCRIPTS = {
    "friction_log.py",
    "log_tool_failure.py",
    "scan_transcript_failures.py",
    "backfill_transcript_failures.py",
    "check_script_error_logged.py",
}

GRACEFUL_EMPTY_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'"message"\s*:\s*"No task found',
        r'"message"\s*:\s*"No (match|matches|results?)',
        r'"message"\s*:\s*"Nothing to ',
        r'"message"\s*:\s*"Already (closed|done|completed)',
        r'"message"\s*:\s*"(File|Entry) not found',
    ]
]

SCRIPT_RE = re.compile(
    r"\bpython3?\b[^\n]*?\btools/([a-z0-9_]+)\.py\b", re.IGNORECASE
)


def extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            (x.get("text", "") if isinstance(x, dict) else str(x)) for x in content
        )
    return str(content)


def derive_surface(tool_name: str, tool_use: dict) -> str:
    if tool_name == "Bash":
        command = ((tool_use or {}).get("input") or {}).get("command", "") or ""
        matches = SCRIPT_RE.findall(command)
        scripts = [
            (m + ".py").lower()
            for m in matches
            if (m + ".py").lower() not in EXCLUDE_SCRIPTS
        ]
        if scripts:
            return scripts[0]
        first = command.strip().split()[0] if command.strip() else "bash"
        if "=" in first:
            for t in command.strip().split():
                if "=" not in t:
                    first = t
                    break
        return f"bash:{first}"
    return f"tool:{tool_name}"


def derive_nature(error_text: str) -> str:
    m = re.search(r'"message"\s*:\s*"([^"]{1,300})"', error_text)
    if m:
        nature = m.group(1)
    else:
        nature = ""
        for line in error_text.splitlines():
            line = line.strip()
            if not line or line.startswith("Exit code "):
                continue
            nature = line
            break
        if not nature:
            nature = error_text[:200]
    nature = nature[:200].replace("|", "/").replace("\n", " ").replace("\r", " ")
    return f"[auto-backfill] {nature}"


def is_excluded_bash(tool_use: dict) -> bool:
    command = ((tool_use or {}).get("input") or {}).get("command", "") or ""
    if re.search(r"\s--help\b|\s-h\b", command):
        return True
    matches = SCRIPT_RE.findall(command)
    if matches and all((m + ".py").lower() in EXCLUDE_SCRIPTS for m in matches):
        return True
    return False


def append_friction(surface: str, nature: str, dry_run: bool) -> bool:
    if dry_run:
        return True
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run(
            [
                "python3", str(FRICTION_LOG_PY), "append",
                surface, nature, "--fix",
                "[auto-backfill from transcript; original event predates working hook]",
            ],
            capture_output=True, text=True, timeout=10, env=env,
        )
        return result.returncode == 0
    except Exception:
        return False


def scan_session(path: Path, since: datetime.date | None) -> list[tuple[str, str, datetime.date | None]]:
    """Return list of (surface, nature, event_date) for is_error tool_results."""
    tool_uses: dict[str, dict] = {}
    found: list[tuple[str, str, datetime.date | None]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                ts_str = r.get("timestamp")
                event_date = None
                if ts_str:
                    try:
                        event_date = datetime.datetime.fromisoformat(
                            ts_str.replace("Z", "+00:00")
                        ).date()
                    except Exception:
                        pass
                msg = r.get("message") or {}
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    ct = c.get("type")
                    if ct == "tool_use":
                        tu_id = c.get("id")
                        if tu_id:
                            tool_uses[tu_id] = c
                    elif ct == "tool_result" and c.get("is_error"):
                        if since and event_date and event_date < since:
                            continue
                        tu_id = c.get("tool_use_id")
                        tu = tool_uses.get(tu_id, {})
                        tool_name = (tu or {}).get("name") or "Unknown"
                        if tool_name == "Bash" and is_excluded_bash(tu):
                            continue
                        err_text = extract_text(c.get("content", ""))
                        if any(p.search(err_text) for p in GRACEFUL_EMPTY_PATTERNS):
                            continue
                        surface = derive_surface(tool_name, tu)
                        if not surface or surface == "bash:":
                            continue
                        nature = derive_nature(err_text)
                        found.append((surface, nature, event_date))
    except Exception as e:
        print(f"  scan_session FAIL on {path.name}: {e}", file=sys.stderr)
    return found


def main() -> int:
    p = argparse.ArgumentParser(description="Backfill transcript-derived friction-log rows")
    p.add_argument("--since", help="YYYY-MM-DD — only include errors from this date forward")
    p.add_argument("--dry-run", action="store_true", help="Count only, do not write")
    p.add_argument("--skip-session", action="append", default=[], help="Session ID(s) to skip (e.g. current live session)")
    args = p.parse_args()

    since = None
    if args.since:
        try:
            since = datetime.date.fromisoformat(args.since)
        except Exception:
            print(f"Bad --since: {args.since}", file=sys.stderr)
            return 2

    if not SESSIONS_DIR.exists():
        print(json.dumps({"status": "error", "message": f"sessions dir not found: {SESSIONS_DIR}"}))
        return 1

    sessions = sorted(SESSIONS_DIR.glob("*.jsonl"))
    summary = {
        "sessions_scanned": 0,
        "sessions_skipped": 0,
        "errors_found": 0,
        "errors_logged": 0,
        "by_date": {},
        "by_surface": {},
        "dry_run": bool(args.dry_run),
        "since": args.since,
    }

    for sess in sessions:
        sid = sess.stem
        if sid in args.skip_session:
            summary["sessions_skipped"] += 1
            continue
        summary["sessions_scanned"] += 1
        found = scan_session(sess, since)
        for surface, nature, event_date in found:
            summary["errors_found"] += 1
            date_key = event_date.isoformat() if event_date else "unknown"
            summary["by_date"][date_key] = summary["by_date"].get(date_key, 0) + 1
            summary["by_surface"][surface] = summary["by_surface"].get(surface, 0) + 1
            if append_friction(surface, nature, args.dry_run):
                summary["errors_logged"] += 1

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
