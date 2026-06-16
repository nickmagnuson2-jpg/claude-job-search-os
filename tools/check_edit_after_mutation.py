#!/usr/bin/env python3
"""
check_edit_after_mutation.py — read-state guard for Claude Code.

Two modes, sharing one session state file (tools/.session-mutations.json):

  --mode record   (PostToolUse on Read|Write|Edit|MultiEdit)
      Records the file's mtime as Claude last saw it, keyed by session_id.

  --mode check    (PreToolUse on Edit|MultiEdit)
      Before an Edit, compares the file's CURRENT mtime to the recorded one.
      WARNs (exit 0, never blocks) if either:
        - the file was never Read this session (the "not read yet" risk), or
        - the file changed on disk since Claude last read/wrote it (a
          script/Bash write or external edit: the cross-tool stale-read case).

WARN-tier: always exit 0. The harness itself still hard-blocks the actual
failed Edit ("File has not been read yet" / "File has been modified since
read"); this hook's job is the PROACTIVE nudge to re-Read first, saving the
wasted attempt. The highest-value case is cross-tool: an atomic script
(networking_write.py, pipe_write.py, todo_write.py, friction_log.py) mutates a
file between Claude's Read and its Edit, invalidating the prior Read silently.

Per memory/feedback_read_before_edit_active_session.md and friction-log row
"Edit after intervening write" (highest-occurrence friction in the ledger).
Strictness: WARN, Edit-only (Write is a full rewrite that does not need a
prior Read, so warning there would be a false positive). Per
feedback_warn_vs_block_hook_design (default WARN; harness does the blocking).

State path overridable via SESSION_MUTATIONS_FILE (used by tests).
Fail-open: never raises, never blocks the workflow.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = Path(os.environ.get(
    "SESSION_MUTATIONS_FILE", str(REPO_ROOT / "tools" / ".session-mutations.json")))

RECORD_TOOLS = {"Read", "Write", "Edit", "MultiEdit"}
CHECK_TOOLS = {"Edit", "MultiEdit"}
PRUNE_AFTER = 86400  # drop entries older than 24h (stale-session defense)


# --- pure logic (unit-tested) ----------------------------------------------

def record(state: dict, session_id: str, file_path: str, mtime: float,
           now: float, tool: str = "") -> dict:
    """Record file_path's mtime under the current session. Resets the map on a
    new session_id and prunes entries older than PRUNE_AFTER."""
    if state.get("session_id") != session_id:
        state = {"session_id": session_id, "files": {}}
    files = state.setdefault("files", {})
    for k in list(files):
        if now - files[k].get("recorded_at", now) > PRUNE_AFTER:
            del files[k]
    files[file_path] = {"mtime": mtime, "recorded_at": now, "tool": tool}
    return state


def check(state: dict, session_id: str, file_path: str,
          current_mtime):
    """Return a warning string, or None if there is nothing to warn about."""
    name = Path(file_path).name
    if (state.get("session_id") != session_id
            or file_path not in state.get("files", {})):
        return (f"⚠️  You have not Read {name} this session. Read it "
                f"before editing, or the Edit will fail with 'File has not been "
                f"read yet'.")
    if current_mtime is None:
        return None
    recorded = state["files"][file_path].get("mtime")
    if recorded is not None and current_mtime > recorded:
        return (f"⚠️  {name} changed on disk since you last read it "
                f"(likely a script/Bash write or external edit). Re-Read it "
                f"before this Edit, or it will fail with 'File has been modified "
                f"since read'.")
    return None


# --- IO helpers -------------------------------------------------------------

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(STATE_FILE.parent), prefix=".sessmut_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, STATE_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        pass


def file_mtime(file_path: str):
    try:
        return os.path.getmtime(file_path)
    except OSError:
        return None


def _mode_from_argv() -> str:
    if "--mode" in sys.argv:
        i = sys.argv.index("--mode")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return "check"


def main() -> None:
    mode = _mode_from_argv()
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # fail-open

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "")
    session_id = data.get("session_id", "_nosession")

    if not file_path:
        sys.exit(0)

    try:
        if mode == "record":
            if tool_name not in RECORD_TOOLS:
                sys.exit(0)
            mtime = file_mtime(file_path)
            if mtime is None:
                sys.exit(0)
            state = record(load_state(), session_id, file_path, mtime,
                           time.time(), tool_name)
            save_state(state)
        else:  # check
            if tool_name not in CHECK_TOOLS:
                sys.exit(0)
            warning = check(load_state(), session_id, file_path,
                            file_mtime(file_path))
            if warning:
                print(warning)  # stdout, WARN convention (cf. check_edit_safety.py)
    except Exception:
        pass  # fail-open: a guard must never break the workflow
    sys.exit(0)


if __name__ == "__main__":
    main()
