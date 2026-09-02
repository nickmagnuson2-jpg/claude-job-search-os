#!/usr/bin/env python3
"""
check_edit_after_mutation.py — read-state guard for Claude Code.

Two modes, sharing one session state file (tools/.session-mutations.json):

  --mode record   (PostToolUse on Read|Write|Edit|MultiEdit|Bash)
      Records the file's mtime as Claude last saw it, keyed by session_id.
      Bash reads (cat/sed/head/grep/...) are parsed out of the command and
      recorded too, so the ledger reflects what was actually read rather
      than only what the Read tool touched (added 2026-08-18).

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

# SESSION_REPO_ROOT overrides for tests, which inject a fixture repo. Mirrors the
# PII_REPO_ROOT seam in check_public_pii.py -- an in-repo-only filter is otherwise
# untestable from a tmp_path fixture.
REPO_ROOT = Path(os.environ.get(
    "SESSION_REPO_ROOT", str(Path(__file__).resolve().parents[1])))
STATE_FILE = Path(os.environ.get(
    "SESSION_MUTATIONS_FILE", str(REPO_ROOT / "tools" / ".session-mutations.json")))

RECORD_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "Bash"}

# Shell commands whose file operands are READS. Recording these is what makes the
# ledger reflect reality.
#
# WHY (2026-08-18): the ledger recorded only the Read tool, so a file opened with
# `cat` / `sed -n` / `head` was invisible to it. Measured mid-session while building
# this: the live ledger held ONE file, while dozens had genuinely been read via Bash,
# because the harness instructs Bash-first reading. Any downstream gate asking "was
# this cited file actually read?" would therefore have fired on almost every correct
# session -- the FP class that gets a guard switched off, leaving it present-looking
# and enforcing nothing.
BASH_READ_COMMANDS = frozenset({
    "cat", "head", "tail", "less", "more", "nl", "wc",
    "sed", "awk", "grep", "egrep", "fgrep", "rg", "jq", "diff", "md5", "shasum",
})
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


def extract_bash_read_targets(command: str, root: Path) -> list[str]:
    """Existing in-repo files this shell command READ.

    Conservative on purpose: a path is recorded only if it survives every filter --
    the segment starts with a known read command, the token is not a flag, is not a
    quoted script/pattern operand (`sed -n '1,50p'`, `grep "foo"`), and the file
    actually EXISTS inside the repo. Over-recording is the dangerous direction here:
    a phantom entry would tell a downstream gate that an unread file was read, which
    is precisely the failure such a gate exists to catch. Under-recording only costs
    a redundant re-read.
    """
    out: list[str] = []
    for segment in _split_segments(command):
        toks = segment.split()
        if not toks:
            continue
        cmd = toks[0].rsplit("/", 1)[-1]        # /usr/bin/grep -> grep
        if cmd not in BASH_READ_COMMANDS:
            continue
        for tok in toks[1:]:
            if tok.startswith("-"):
                continue
            # Quoted operands (`sed -n '1,50p'`), variables and redirect glyphs.
            # MUTATION NOTE (2026-08-18): removing this kills no test, and that is
            # honest rather than a coverage hole -- such a token never names an
            # existing file, so the is_file() check below always catches it first.
            # Kept as defence-in-depth for a pathological filename that literally
            # contains a quote character. The load-bearing filters are the command
            # allowlist, is_file(), and the repo-scope check, all three of which
            # have mutants that DO die.
            if tok[:1] in "\"'$`<>|&":
                continue
            cand = Path(tok)
            try:
                abs_p = cand if cand.is_absolute() else (root / tok)
                if not abs_p.is_file():
                    continue
                rel = os.path.relpath(abs_p.resolve(), root)
                if rel.startswith(".."):         # outside the repo
                    continue
            except OSError:
                continue
            out.append(str(abs_p.resolve()))
    return out


def _split_segments(command: str) -> list[str]:
    """Split on `;`, `|`, `&&`, `||` and newlines. Pipelines matter here: in
    `cat f.md | grep x` the read is the `cat` stage, so the segments must be
    inspected separately."""
    buf, segs = [], []
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if command.startswith("&&", i) or command.startswith("||", i):
            segs.append("".join(buf)); buf = []; i += 2; continue
        if ch in ";|\n":
            segs.append("".join(buf)); buf = []; i += 1; continue
        buf.append(ch); i += 1
    if buf:
        segs.append("".join(buf))
    return [x for x in segs if x.strip()]


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

    # Bash carries no file_path: its read targets are parsed out of the command.
    if mode == "record" and tool_name == "Bash":
        try:
            command = tool_input.get("command", "") or ""
            state = load_state()
            now = time.time()
            for target in extract_bash_read_targets(command, REPO_ROOT):
                mtime = file_mtime(target)
                if mtime is not None:
                    state = record(state, session_id, target, mtime, now, "Bash")
            save_state(state)
        except Exception:
            pass  # fail-open: a guard must never break the workflow
        sys.exit(0)

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
