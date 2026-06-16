#!/usr/bin/env python3
"""
scan_transcript_failures.py — Stop hook for Claude Code.

Fires when Claude finishes responding (end of turn). Reads the session transcript
JSONL, finds all `tool_result` entries with `is_error: true` since the last
processed cursor for this session, and appends a friction-log row for each.

Belt-and-suspenders to the PostToolUseFailure hook:
  - PostToolUseFailure is the primary capture (per Claude Code hooks reference).
  - This Stop hook catches anything PostToolUseFailure missed (older Claude Code
    versions where PostToolUseFailure may not exist; edge-case tool failures the
    primary hook didn't recognize; etc.).
  - Dedup happens inside friction_log.py via its 5-row lookback, so double-firing
    on the same error is safe.

Workarounds baked in (from official-doc GitHub issues):
  - Issue #15813 / #40655: transcript JSONL write race condition — poll for
    line-count stability with exponential backoff before reading.
  - Issue #44450 (worktree): `transcript_path` may resolve to a stale/wrong
    directory hash; fall back to CLAUDE_PROJECT_DIR + session_id construction.
  - `stop_hook_active: true` recursion guard — exit 0 if we're being called
    inside our own stop chain.

State file: tools/.transcript-scan-cursor.json
  {
    "<session_id>": {"last_line": int, "last_seen_ts": str}
  }

Exit codes: always 0 (WARN-tier, never blocks the stop).
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import friction_surface as fs  # shared surface/nature derivation (single source of truth)

REPO_ROOT = Path(__file__).resolve().parents[1]
FRICTION_LOG_PY = REPO_ROOT / "tools" / "friction_log.py"
TRACE_LOG = REPO_ROOT / "tools" / ".hook-trace.log"
CURSOR_FILE = REPO_ROOT / "tools" / ".transcript-scan-cursor.json"

EXCLUDE_SCRIPTS = fs.EXCLUDE_SCRIPTS

GRACEFUL_EMPTY_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'"message"\s*:\s*"No task found',
        r'"message"\s*:\s*"No (match|matches|results?)',
        r'"message"\s*:\s*"Nothing to ',
        r'"message"\s*:\s*"Already (closed|done|completed)',
        r'"message"\s*:\s*"(File|Entry) not found',
    ]
]

SCRIPT_RE = fs.SCRIPT_RE

# A masked failure: the pipeline exited 0 (so is_error is falsy) because a pipe
# or `|| true`/`; true` swallowed python's non-zero exit, but the output carries
# a Python traceback. The is_error filter never sees these; this signature does.
# Origin: 2026-06-05 — `python3 -c "..." 2>&1 | head` TypeError logged nowhere
# because `head` zeroed the exit code. See memory/friction-log.md 2026-06-05.
TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):")
# Trailing harness footer lines to ignore when locating the final output line.
_CRASH_FOOTER_RE = re.compile(r"^(Shell cwd was reset|</?error>)", re.IGNORECASE)
# An exception line that a real uncaught crash ENDS with (module-qualified ok).
_EXC_LINE_RE = re.compile(
    r"^[A-Za-z_][\w.]*(Error|Exception|KeyboardInterrupt|SystemExit|StopIteration)\b"
)


def looks_like_python_crash(text: str) -> bool:
    """Distinguish a real uncaught python crash from a command that merely PRINTS
    a traceback as data (displaying the friction log, dumping captured subprocess
    output, dry-run JSON). A crash ends with the exception line; a display has
    more output after any embedded traceback.

    Requires the traceback header AND that the last meaningful line — after
    stripping the harness `Shell cwd was reset`/`<error>` footers — is an
    exception line. Smoke-derived: bare TRACEBACK_RE false-positived on ~6
    display commands across real transcripts (2026-06-05)."""
    if not text or "Traceback (most recent call last):" not in text:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    while lines and _CRASH_FOOTER_RE.match(lines[-1]):
        lines.pop()
    if not lines:
        return False
    return bool(_EXC_LINE_RE.match(lines[-1]))


def is_masked_python_failure(tool_name: str, command: str, text: str, is_error) -> bool:
    """True for a Bash result that LOOKS successful (is_error falsy) but ran
    python in command position AND crashed with a traceback whose non-zero exit
    was masked by a downstream pipe. Real (is_error true) failures are handled by
    the normal path, so they return False here. Non-python commands that merely
    DISPLAY a traceback (grep/cat of a log) return False via the command-position
    python check; python commands that PRINT a traceback as data return False via
    looks_like_python_crash (exception must be the final line)."""
    if is_error:
        return False
    if (tool_name or "") != "Bash":
        return False
    if not fs.python_invoked(command or ""):
        return False
    return looks_like_python_crash(text or "")


# A masked SHELL failure: like the python case, the pipeline exited 0 (is_error
# falsy) because a pipe (`| awk`) or `|| echo` swallowed the non-zero exit, but
# the output carries a shell error that is NOT a python traceback. Two families
# seen 2026-06-08: zsh aborting on an unmatched glob (`no matches found`), and a
# GNU-only flag rejected by BSD tools on darwin (`ls: illegal option`,
# `unrecognized option`). See memory/friction-log.md 2026-06-08.
#
# FP defense: signatures are LINE-ANCHORED (re.MULTILINE `^`). The dominant false
# positive is scanning/grepping the friction log itself, whose rows start with
# `| ` (or grep's `N:|` prefix) — never at column 0 — so a phrase sitting inside
# a table cell cannot match. friction-infra commands are also dropped upstream by
# is_excluded_bash (EXCLUDE_SCRIPTS). Residual `echo "<literal>"` FP is accepted:
# WARN-tier (exit 0), deduped by friction_log.py.
MASKED_SHELL_RE = re.compile(
    r"^(?:zsh:|\(eval\):\d+:)\s*no matches found:"        # zsh unmatched glob
    r"|^[\w./-]+:\s*(?:illegal|unrecognized|invalid) option\b",  # BSD/GNU bad flag
    re.MULTILINE,
)


def masked_shell_error_line(text: str):
    """Return the first line carrying a masked-shell-failure signature, stripped,
    or None. Returning the LINE (not the whole output) gives a clean nature."""
    if not text:
        return None
    m = MASKED_SHELL_RE.search(text)
    if not m:
        return None
    # Expand the match to its full line.
    start = text.rfind("\n", 0, m.start()) + 1
    end = text.find("\n", m.start())
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def is_masked_shell_failure(tool_name: str, command: str, text: str, is_error) -> bool:
    """True for a Bash result that LOOKS successful (is_error falsy) but whose
    output carries a masked shell error (zsh nomatch / bad-flag) NOT covered by
    the python-traceback path. Real (is_error true) failures use the normal path.
    Python tracebacks are left to is_masked_python_failure (don't double-flag)."""
    if is_error:
        return False
    if (tool_name or "") != "Bash":
        return False
    if "Traceback (most recent call last):" in (text or ""):
        return False
    return masked_shell_error_line(text or "") is not None


def trace(msg: str) -> None:
    try:
        TRACE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(TRACE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat()}] scan_transcript {msg}\n")
    except Exception:
        pass


def resolve_transcript_path(data: dict) -> Path | None:
    """
    Prefer transcript_path from hook payload. Per issue #44450, this can point
    to a non-existent file in git worktree scenarios; fall back to constructing
    the path from CLAUDE_PROJECT_DIR + session_id if the primary doesn't exist.
    """
    tp = data.get("transcript_path")
    if tp:
        p = Path(os.path.expanduser(tp))
        if p.exists():
            return p
        trace(f"resolve_transcript_path PRIMARY MISS: {p}")

    session_id = data.get("session_id")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or str(REPO_ROOT)
    # Hash convention: replace / with - in the project dir path
    project_hash = project_dir.replace("/", "-")
    candidate = Path.home() / ".claude" / "projects" / project_hash / f"{session_id}.jsonl"
    if candidate.exists():
        trace(f"resolve_transcript_path FALLBACK HIT: {candidate}")
        return candidate
    trace(f"resolve_transcript_path FALLBACK MISS: {candidate}")
    return None


def wait_for_flush(path: Path, max_wait: float = 4.0) -> None:
    """
    Per issue #15813 / #40655 — Stop hook fires before transcript is fully
    fsync'd. Poll for line-count stability with exponential backoff.
    Returns once line count is stable for one polling interval or max_wait elapsed.
    """
    try:
        os.sync()
    except Exception:
        pass

    interval = 0.05
    prev_lines = -1
    elapsed = 0.0
    stable_count = 0
    while elapsed < max_wait:
        try:
            with open(path, "rb") as f:
                lines = sum(1 for _ in f)
        except Exception:
            return
        if lines == prev_lines:
            stable_count += 1
            if stable_count >= 2:  # two consecutive stable reads → flush done
                return
        else:
            stable_count = 0
            prev_lines = lines
        time.sleep(interval)
        elapsed += interval
        interval = min(interval * 1.5, 0.5)


def load_cursor() -> dict:
    if not CURSOR_FILE.exists():
        return {}
    try:
        return json.loads(CURSOR_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cursor(state: dict) -> None:
    try:
        CURSOR_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def extract_text(content) -> str:
    """tool_result content may be a string or a list of {type:text, text:...} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            (x.get("text", "") if isinstance(x, dict) else str(x)) for x in content
        )
    return str(content)


def derive_surface(tool_name: str, tool_use: dict, error_text: str) -> str:
    """Delegates to the shared module (PreToolUse-block + inline-`-c` aware)."""
    command = ((tool_use or {}).get("input") or {}).get("command", "") or ""
    return fs.derive_surface(tool_name, command, error_text)


def derive_nature(error_text: str) -> str:
    """Shared nature derivation with the Stop-hook [auto-stop] provenance tag."""
    return fs.derive_nature(error_text, "auto-stop")


def is_excluded_bash(tool_use: dict) -> bool:
    """Don't log Bash errors for friction-log infrastructure scripts."""
    command = ((tool_use or {}).get("input") or {}).get("command", "") or ""
    if re.search(r"\s--help\b|\s-h\b", command):
        return True
    matches = SCRIPT_RE.findall(command)
    if matches and all((m + ".py").lower() in EXCLUDE_SCRIPTS for m in matches):
        return True
    return False


def append_friction(surface: str, nature: str, exit_hint: str = "") -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    fix_text = f"[auto-logged from transcript scan{exit_hint}; update via friction_log.py append once resolved]"
    try:
        subprocess.run(
            [
                "python3", str(FRICTION_LOG_PY), "append",
                surface, nature, "--fix", fix_text,
            ],
            capture_output=True, text=True, timeout=10, env=env,
        )
    except Exception as e:
        trace(f"append_friction SUBPROCESS_FAIL: {e!r}")


def scan(transcript_path: Path, start_line: int) -> tuple[int, int]:
    """
    Walk JSONL starting from start_line. Find tool_result entries with
    is_error: true, pair with prior tool_use to extract command/tool name,
    append friction rows. Returns (new_line_position, errors_logged_count).
    """
    # Build tool_use_id → tool_use_dict map by walking the file once
    tool_uses: dict[str, dict] = {}
    errors_found: list[tuple[str, dict, str]] = []  # (tool_use_id, tool_use, error_text)
    line_no = 0
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
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
                    elif ct == "tool_result":
                        if line_no <= start_line:
                            continue
                        tu_id = c.get("tool_use_id")
                        tu = tool_uses.get(tu_id, {})
                        err_text = extract_text(c.get("content", ""))
                        is_err = c.get("is_error")
                        if is_err:
                            errors_found.append((tu_id, tu, err_text))
                        else:
                            # Masked failure: exit code swallowed by a pipe/||,
                            # but the output carries a python traceback OR a shell
                            # error (zsh nomatch / bad-flag) the is_error filter
                            # never saw.
                            tname = (tu or {}).get("name") or ""
                            cmd = ((tu or {}).get("input") or {}).get("command", "") or ""
                            if (is_masked_python_failure(tname, cmd, err_text, is_err)
                                    or is_masked_shell_failure(tname, cmd, err_text, is_err)):
                                errors_found.append((tu_id, tu, err_text))
    except Exception as e:
        trace(f"scan READ_FAIL: {e!r}")
        return start_line, 0

    logged = 0
    for tu_id, tu, err_text in errors_found:
        tool_name = (tu or {}).get("name") or "Unknown"
        # Skip excluded bash invocations
        if tool_name == "Bash" and is_excluded_bash(tu):
            continue
        # Skip graceful-empty patterns
        if any(p.search(err_text) for p in GRACEFUL_EMPTY_PATTERNS):
            continue
        surface = derive_surface(tool_name, tu, err_text)
        # For a masked SHELL failure the informative text is the error LINE, not
        # the whole (successful-looking) output; pass just that to derive_nature.
        shell_line = masked_shell_error_line(err_text)
        if shell_line and "Traceback (most recent call last):" not in err_text:
            nature = fs.derive_nature(shell_line, "auto-stop")
        else:
            nature = derive_nature(err_text)
        if not surface or surface == "bash:":
            continue
        append_friction(surface, nature)
        logged += 1

    return line_no, logged


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw else {}
    except Exception as e:
        trace(f"STOP_HOOK STDIN_PARSE_FAIL: {e!r} head={raw[:200]!r}")
        return 0

    trace(f"STOP_HOOK INVOKED keys={list(data.keys())!r} stop_hook_active={data.get('stop_hook_active')}")

    # Recursion guard — if Stop hook is firing because of our own friction-log
    # write triggering another Stop, exit immediately. Per Claude Code docs.
    if data.get("stop_hook_active") is True:
        trace("STOP_HOOK SKIP recursion guard")
        return 0

    transcript_path = resolve_transcript_path(data)
    if not transcript_path:
        trace("STOP_HOOK SKIP no transcript")
        return 0

    # Wait for transcript flush (issue #15813 workaround)
    wait_for_flush(transcript_path)

    session_id = data.get("session_id") or transcript_path.stem
    state = load_cursor()
    prev = state.get(session_id, {})
    start_line = int(prev.get("last_line", 0))

    new_line, logged = scan(transcript_path, start_line)

    state[session_id] = {
        "last_line": new_line,
        "last_seen_ts": datetime.datetime.now().isoformat(),
    }
    save_cursor(state)

    if logged > 0:
        sys.stderr.write(
            f"✓ transcript-scan: {logged} new error(s) logged (session {session_id[:8]}, lines {start_line}→{new_line}).\n"
        )
    trace(f"STOP_HOOK DONE logged={logged} cursor={start_line}→{new_line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
