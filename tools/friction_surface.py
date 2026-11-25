#!/usr/bin/env python3
"""
friction_surface.py — shared surface/nature derivation for the friction-log
auto-capture hooks (log_tool_failure.py PostToolUseFailure + scan_transcript_failures.py Stop).

Single source of truth so the two hooks cannot drift. Improves attribution for
two failure shapes the older per-hook logic collapsed into useless buckets:

  - PreToolUse BLOCKs: the command never ran, so the failing "surface" is the
    script being mis-called (e.g. todo_write.py), recoverable only from the
    block message ("BLOCKED: todo_write.py invoked with --priority"), not the
    command's first token (which logged as bash:git / bash:cd).
  - Inline `python3 -c "..."` scripts: a TypeError/Traceback logged under
    bash:cd, lumping every unrelated inline script together. We attribute to the
    real .py in the traceback, else the first imported module in the -c body.

Origin: 2026-06-02 — Nick: "all errors should be logged automatically." Three of
that session's errors WERE auto-logged but under coarse surfaces (bash:cd
Traceback occ-10, kwarg-block under bash:git). This module fixes attribution so
the auto-rows are promotable, not noise. Composes with
feedback_command_hook_match_position_not_substring (command-position parsing) and
feedback_verify_auto_capture_actually_fires (the capture fired; quality was the gap).

Pure functions, stdlib only. No I/O.
"""
import re

# tools/*.py actually invoked by python (not embedded in echo'd JSON/strings).
SCRIPT_RE = re.compile(
    r"\bpython3?\b[^\n]*?\btools/([a-z0-9_]+)\.py\b", re.IGNORECASE
)

# Infrastructure scripts that must never be logged as their own friction surface.
EXCLUDE_SCRIPTS = {
    "friction_log.py",
    "log_tool_failure.py",
    "scan_transcript_failures.py",
    "backfill_transcript_failures.py",
    "check_script_error_logged.py",
}

# PreToolUse block message: "... BLOCKED: <operand>.py invoked ..." — the operand
# is the script being mis-called (the useful surface).
# Fallback: the check_*.py named in "[python3 /.../check_foo.py]:".
# Any BLOCKED marker (to know we're in a block at all).

# Inline python: `python3 -c` / `python -c`.
_INLINE_C_RE = re.compile(r"\bpython3?\b\s+-c\b")
# A real .py file in a traceback frame (exclude <string>/<stdin>).
_TRACEBACK_PYFILE_RE = re.compile(r'File "([^"<][^"]*\.py)"')
# First import in a -c body (legacy; superseded by _imported_modules).
_IMPORT_RE = re.compile(r"(?:^|;|\s)(?:import|from)\s+([a-zA-Z_][\w]*)")

# Shell-segment splitter: &&, ||, |, ;, & — used for command-position detection.
_SHELL_SPLIT_RE = re.compile(r"&&|\|\||[|;&]")

# Stdlib modules that are noise as a friction surface (no useful attribution).
_STDLIB_NOISE = {
    "os", "sys", "json", "re", "glob", "shutil", "datetime", "subprocess",
    "pathlib", "time", "math", "collections", "itertools", "functools",
    "argparse", "typing", "io", "csv", "random", "string", "hashlib",
}

# Recognizable error signal in free text. Used to gate the PostToolUseFailure
# exit-code-unknown fallback (see looks_like_real_error): a chained/piped bash
# command (`cmd1 | cmd2`) can report a real nonzero exit driven entirely by a
# benign downstream stage (`grep -c` finding 0 matches, `grep -q` no match),
# while the only text we can extract is an EARLIER stage's successful stdout
# (e.g. `git status && grep ...` -> "On branch main", `head file.py | grep ...`
# -> the file's own shebang line). That stdout has no error signal in it.
# Origin: 2026-07-08 friction-log audit — 10+ rows logged nature="On branch
# main" / "#!/usr/bin/env python3" / "# Job Search To-Dos", all exit=? auto
# rows, none of them an actual failure of the named command.
_ERROR_SIGNAL_RE = re.compile(
    r"(?-i:\b[A-Z]\w*(?:Error|Exception)\b)"  # PascalCase Python exception
    # names (TypeError, FileNotFoundError, MyCustomException, ...) — case-
    # sensitive on purpose so lowercase words merely containing "error" as a
    # substring (e.g. "Terror") don't false-match; Python exception classes
    # are always capitalized.
    r"|\busage\s*:"  # argparse-style usage line. Own alternative with only a
    # LEADING \b: "usage:" always ends in a non-word char (the colon), and a
    # trailing \b right after a colon is never satisfied (colon-to-space or
    # colon-to-end-of-string is not a word-boundary transition), so folding
    # this into the shared-trailing-\b group below would make it unmatchable.
    r"|\b(error|fatal|traceback|not found|no such file|"
    r"permission denied|cannot|invalid|failed|denied|refused|"
    r"unrecognized|illegal option|command not found|no matches found|"
    # 2026-11-16 restraint replay (RULING Q10): two real failure shapes carried no signal
    # and would have been dropped by the Bash-lane gate: macOS permission refusals on
    # grep/sed/ls, and the harness's "Command timed out after 2m 0s" (exit 143).
    r"operation not permitted|timed out|"
    # Codex + Grok P0 on 1a6c4750: fatal ends carried no signal and were dropped.
    r"killed|segmentation fault|no space left on device|out of memory|bus error|"
    r"exit code (?:12[89]|1[3-9]\d|2[0-5]\d)|"
    r"ignored by one of your \.gitignore files)\b",
    re.IGNORECASE,
)


def looks_like_real_error(text: str) -> bool:
    """True if text contains a recognizable error signal (see _ERROR_SIGNAL_RE
    docstring for why this matters). Pure text heuristic, WARN-tier — false
    negatives just mean a real-but-oddly-worded failure goes unlogged, which
    is an acceptable trade against the alternative (drowning genuine friction
    rows in benign-stdout noise)."""
    return bool(text) and bool(_ERROR_SIGNAL_RE.search(text))


def python_invoked(command: str) -> bool:
    """True iff python/python3 runs in COMMAND POSITION in some segment of the
    command (start of a segment, after env-var assignments) — not merely as a
    substring inside a grep pattern, filename, or quoted string.

    Command-position-aware on purpose: `grep -n python3 file` and
    `echo "run python3" | head` must both return False. Composes with the
    command-position-not-substring discipline (HOOK_AUTHORING.md).
    """
    if not command:
        return False
    for seg in _SHELL_SPLIT_RE.split(command):
        parts = seg.strip().split()
        i = 0
        # Skip leading env-var assignments (FOO=bar python3 ...).
        while i < len(parts) and "=" in parts[i] and not parts[i][0] in ("'", '"'):
            i += 1
        if i < len(parts):
            base = parts[i].rsplit("/", 1)[-1]  # strip any path prefix
            if base in ("python", "python3"):
                return True
    return False


def _imported_modules(body: str) -> list:
    """Ordered top-level module names imported in a `-c` body, including
    comma-separated lists (`import os, glob, ss_log_append as s`) and
    `from x.y import z` (-> x). Best-effort, stdlib only."""
    mods = []
    for stmt in re.split(r"[;\n]", body):
        stmt = stmt.strip()
        m = re.match(r"from\s+([a-zA-Z_][\w.]*)", stmt)
        if m:
            mods.append(m.group(1).split(".")[0])
            continue
        m = re.match(r"import\s+(.+)", stmt)
        if m:
            for piece in m.group(1).split(","):
                tokens = piece.strip().split()
                if not tokens:
                    continue
                name = tokens[0].split(".")[0]
                if re.match(r"^[a-zA-Z_]\w*$", name):
                    mods.append(name)
    return mods


def _first_token(command: str) -> str:
    """First non-env-assignment token of a bash command (env prefixes stripped)."""
    parts = command.strip().split()
    if not parts:
        return "bash"
    first = parts[0]
    if "=" in first:
        for t in parts:
            if "=" not in t:
                return t
    return first


def _inline_c_body(command: str) -> str:
    """Best-effort extraction of the -c body (handles single/double quotes)."""
    # Find the -c then the following quoted string.
    idx = _INLINE_C_RE.search(command)
    if not idx:
        return ""
    rest = command[idx.end():].lstrip()
    if not rest:
        return ""
    q = rest[0]
    if q in ("'", '"'):
        end = rest.find(q, 1)
        return rest[1:end] if end != -1 else rest[1:]
    # Unquoted -c body (rare) — take to end.
    return rest


def _attribute_from_body(error_text: str, body: str, fallback: str) -> str:
    """Shared attribution logic for any inline-python invocation shape once its
    script BODY has been extracted (whether from `-c '...'` or a heredoc):
    1. A real .py in the traceback is the most reliable signal.
    2. Else the first NON-stdlib imported module in the body.
    3. Else `fallback` (a shape-specific label, so a -c failure and a heredoc
       failure with no imports don't collide into one indistinguishable row)."""
    files = _TRACEBACK_PYFILE_RE.findall(error_text or "")
    if files:
        return files[-1].rsplit("/", 1)[-1].lower()
    mods = _imported_modules(body)
    non_std = [m for m in mods if m.lower() not in _STDLIB_NOISE]
    if non_std:
        return f"{non_std[0]}.py"
    if mods:
        return f"inline:{mods[0]}"
    return fallback


def _surface_from_inline(command: str, error_text: str) -> str:
    """Attribute an inline `python3 -c` failure to the real module, not bash:cd."""
    if not _INLINE_C_RE.search(command):
        return ""
    body = _inline_c_body(command)
    return _attribute_from_body(error_text, body, "inline-python")


# `python3 <<'EOF' ... EOF` / `python3 - <<EOF ... EOF` heredoc invocation.
# Captures the delimiter (optionally quoted) so the body can be sliced out to
# its matching closing line. Distinct from _INLINE_C_RE (`-c`) — this shape
# was falling through ALL surface-attribution checks straight to the generic
# `bash:<first-token>` fallback (e.g. `cd dir && python3 <<EOF ...` -> just
# "bash:cd"), and worse, whatever traceback text WAS captured for these tended
# to be short/truncated ("File \"<string>\", line 4" with no exception line),
# which is generic enough that any two unrelated heredoc scripts hit Jaccard
# 1.0 in dedup — silently merging distinct one-off script failures into a
# single inflated, wrongly-promoted-to-mandatory row. Origin: 2026-07-08
# friction-log audit, the bash:cd "File \"<string>\", line 4" row (10 occ).
_HEREDOC_PY_RE = re.compile(r"\bpython3?\b[^\n]*<<-?\s*(['\"]?)(\w+)\1")


def _heredoc_body(command: str) -> str:
    """Best-effort extraction of a python heredoc's body text.

    Two hardening fixes (2026-07-08 code review) against misattribution:
    1. The closing-delimiter match uses a word boundary, not end-of-line, so
       an idiomatic same-line close like `EOF)` (a heredoc feeding a `$(...)`
       command substitution — e.g. `x=$(python3 <<EOF ... EOF)`) is still
       recognized as the close. The stricter `\\s*$` version missed this and
       fell through to the no-match branch below.
    2. If no closing delimiter is found at all (truly unterminated in the
       captured text), return "" rather than the unbounded remainder of the
       command. Confirmed live: without this, an unrelated LATER command in
       the same compound string (e.g. a trailing `python3 -c "import numpy"`)
       gets swept into "the heredoc body" and its import wrongly becomes the
       attributed surface, masking the real failure.
    """
    m = _HEREDOC_PY_RE.search(command)
    if not m:
        return ""
    delim = m.group(2)
    rest = command[m.end():]
    nl = rest.find("\n")
    if nl == -1:
        return ""
    after_open = rest[nl + 1:]
    end_re = re.compile(rf"^\s*{re.escape(delim)}\b", re.MULTILINE)
    m2 = end_re.search(after_open)
    return after_open[:m2.start()] if m2 else ""


def _surface_from_heredoc(command: str, error_text: str) -> str:
    """Attribute an inline `python3 <<EOF ... EOF` heredoc failure — see
    _HEREDOC_PY_RE docstring for why this exists as a separate path from
    `-c`."""
    if not _HEREDOC_PY_RE.search(command):
        return ""
    body = _heredoc_body(command)
    return _attribute_from_body(error_text, body, "inline-python-heredoc")


def extract_text(content) -> str:
    """tool_result content may be a string or a list of {type:text, text:...} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            (x.get("text", "") if isinstance(x, dict) else str(x)) for x in content
        )
    return str(content)


def is_excluded_bash(tool_use: dict) -> bool:
    """Don't log Bash errors for --help calls or friction-log infrastructure scripts."""
    command = ((tool_use or {}).get("input") or {}).get("command", "") or ""
    if re.search(r"\s--help\b|\s-h\b", command):
        return True
    matches = SCRIPT_RE.findall(command)
    return bool(matches) and all((m + ".py").lower() in EXCLUDE_SCRIPTS for m in matches)


# --- harness events and hook blocks (RULINGS Q9 + Q10, data/workstreams/hooks.md, 2026-11-16) ---
# 4c's audit (output/infra-backlog/111626-telemetry/friction-attribution.md): of 726 automatic
# rows, 74 sat on the wrong surface and 370 were not friction. Two causes are fixed here:
# harness events (auto-mode classifier, permission denials, user rejections) were filed under
# whatever script the command named, and a hook BLOCK was attributed from text anywhere in the
# output instead of the harness header. Q9: a block that worked as designed goes on its own
# `block:<hook>` surface, which friction_log.promotion_for keeps off the script-patch ladder.
_HARNESS_KINDS = (
    ("classifier", re.compile(r"auto mode classifier|auto mode cannot determine|classifier gave no "
                              r"verdict|temporarily unavailable, so auto mode", re.IGNORECASE)),
    ("permission", re.compile(r"\bPermission to use \S+.*\bhas been denied|requires approval|"
                              r"can't be checked before it runs|was blocked\. For security|"
                              r"Contains brace with quote character|<tool_use_error>Blocked:",
                              re.IGNORECASE | re.DOTALL)),
    ("user", re.compile(r"user (?:doesn't|does not) want to proceed|tool use was rejected|"
                        r"Request interrupted by user", re.IGNORECASE)),
)
# The harness writes a hook block as `PreToolUse:<Tool> hook error: [<command>]: <stderr>`.
# Only that header names the hook; a BLOCKED word elsewhere in output is data.
_HOOK_HEADER_RE = re.compile(
    r"^(?:Exit code \S+\s*)?PreToolUse:\w+ hook error: \[[^\]\n]*?tools/([A-Za-z0-9_]+\.py)[^\]\n]*\]")


# A harness message OPENS the tool result; the phrase must start within this many characters
# ("ls in '<path>' was blocked" puts a path first). Searching the whole result filed a real
# failure that quoted a denial as a harness event and dropped it (Codex r2 3.F2, P0).
_HARNESS_PHRASE_WITHIN = 300
_HARNESS_SCAN_CAP = 50_000


def harness_kind(error_text: str) -> str:
    """'classifier' | 'permission' | 'user' when the text is a harness event, else ''.

    A Bash result opens with "Exit code N" and is the command's own output; in 1,020+ real
    harness results none did, so such a result is never a harness event, whatever it quotes.
    Otherwise a harness phrase must start near the opening of the text."""
    head = (error_text or "").lstrip()
    if head.startswith("Exit code"):
        return ""
    # The phrase must START near the opening, but may run long: "Permission to use Bash with
    # command <the whole command> has been denied" ends after the command, so cutting the text
    # short filed a long denial as the script's friction (Codex + Grok r4 F1, P0). The cap only
    # bounds the regex's work on a huge result.
    head = head[:_HARNESS_SCAN_CAP]
    for kind, rx in _HARNESS_KINDS:
        m = rx.search(head)
        if m and m.start() <= _HARNESS_PHRASE_WITHIN:
            return kind
    return ""


# A designed block is the hook's own message, and every real one (1,021 of 1,021 header
# results in the transcripts, 2026-11-16) opens its stderr with BLOCKED. Anything else after
# the header is the hook dying: argparse, IndentationError, Killed, a segfault, empty stderr.
# Crash is the default; a fixed list of crash phrases missed all of those (r2 3.F1, 4.F1, P0).
_BLOCK_SIGNAL_RE = re.compile(r"[\s:]*BLOCKED\b")


def _hook_header(error_text: str):
    """(hook, is_designed_block) for a harness hook header at the start of the text, else None."""
    text = (error_text or "").lstrip()
    m = _HOOK_HEADER_RE.match(text)
    if not m:
        return None
    return m.group(1).lower(), bool(_BLOCK_SIGNAL_RE.match(text, m.end()))


def hook_block(error_text: str) -> str:
    """The hook named in a harness header whose stderr opens with BLOCKED, else ''."""
    h = _hook_header(error_text)
    return h[0] if h and h[1] else ""


def hook_crash(error_text: str) -> str:
    """The hook named in a harness header whose stderr does NOT open with BLOCKED, else ''."""
    h = _hook_header(error_text)
    return h[0] if h and not h[1] else ""


_TRACEBACK_HEADER_RE = re.compile(r"Traceback \(most recent call last\):")
_TOOLS_DIR_RE = re.compile(r"(?:^|/)tools/[^/]+\.py$")


def traceback_blame(scripts: list[str], error_text: str) -> str:
    """Which named tools script a failure belongs to, read from the traceback when there is one.

    `scripts` are the tools/*.py the command runs, in order (already lowercased, exclusions
    removed). Before 2026-11-25 both auto-loggers blamed scripts[0] for ANY traceback, so
    `python3 tools/pipe_write.py ... 2>&1 | python3 -c "import json,sys; json.load(sys.stdin)"`
    failing in the caller's json.load was filed as pipe_write friction (S6's replay found every
    retained finding_write JSONDecodeError event was raised by the caller, not the tool;
    output/overnight-112526/S6-friction-triage.md section 3).

      - no traceback in the output: scripts[0], unchanged (a tool's own JSON error, exit code);
      - a traceback frame in a named script: the deepest such script raised it, or was the
        entry point of the library call that did, so it keeps the blame;
      - else a frame in another file under a tools/ directory (a tools module the caller
        imported): that module;
      - else no frame in any tool: the caller's own code raised it, so the row is
        "inline python3 (caller of <scripts[0]>)", never the tool.
    """
    if not scripts:
        return ""
    text = error_text or ""
    if not _TRACEBACK_HEADER_RE.search(text):
        return scripts[0]
    frames = _TRACEBACK_PYFILE_RE.findall(text)
    names = [f.replace("\\", "/").rsplit("/", 1)[-1].lower() for f in frames]
    for name in reversed(names):
        if name in scripts:
            return name
    for frame, name in zip(reversed(frames), reversed(names)):
        if _TOOLS_DIR_RE.search(frame.replace("\\", "/")) and "site-packages" not in frame \
                and name not in EXCLUDE_SCRIPTS:
            return name
    return f"inline python3 (caller of {scripts[0]})"


def derive_surface(tool_name: str, command: str, error_text: str = "") -> str:
    """The 'surface' is what failed. Precedence:
    0. harness event       -> harness:<kind>  (never a script's friction; callers skip it)
    1. hook block header   -> block:<hook>    (any tool; Q9 model-habit ladder)
    2. non-Bash tool       -> tool:<name>
    3. explicit tools/*.py -> that script, unless the traceback shows the caller raised it
                              (traceback_blame)
    4. inline python -c    -> real .py in traceback, else first import
    5. fallback            -> bash:<first-non-env-token>
    A BLOCKED word anywhere else in the output is NOT a hook block (2026-11-16: run-log text
    printed by a grep was filed under check_guard_edit_approval.py).
    """
    kind = harness_kind(error_text)
    if kind:
        return f"harness:{kind}"
    hook = hook_block(error_text)
    if hook:
        return f"block:{hook}"
    crashed = hook_crash(error_text)
    if crashed:
        return crashed
    tool_name = (tool_name or "").strip()
    if tool_name != "Bash":
        return f"tool:{tool_name}" if tool_name else "tool:unknown"

    command = command or ""
    matches = SCRIPT_RE.findall(command)
    scripts = [(m + ".py").lower() for m in matches if (m + ".py").lower() not in EXCLUDE_SCRIPTS]
    if scripts:
        return traceback_blame(scripts, error_text)

    s = _surface_from_inline(command, error_text)
    if s:
        return s

    s = _surface_from_heredoc(command, error_text)
    if s:
        return s

    return f"bash:{_first_token(command)}"


def derive_nature(error_text: str, tag: str = "auto") -> str:
    """One-line nature for the ledger cell, prefixed with a provenance tag.

    For a Python traceback the informative line is the LAST one (the
    `ExceptionType: message`), not the "Traceback (most recent call last):"
    header — so tracebacks are summarized by their exception line.
    """
    error_text = error_text or ""
    m = re.search(r'"message"\s*:\s*"([^"]{1,300})"', error_text)
    if m:
        nature = m.group(1)
    elif "Traceback (most recent call last)" in error_text:
        # Last non-empty line is the exception (e.g. "TypeError: ...") — but the
        # harness appends a "Shell cwd was reset ..." footer (and may wrap in
        # <error> tags) after a `cd` command, which would otherwise become the
        # nature. Strip those trailing footers first.
        lines = [ln.strip() for ln in error_text.splitlines() if ln.strip()]
        while lines and re.match(r"^(Shell cwd was reset|</?error>)", lines[-1], re.IGNORECASE):
            lines.pop()
        nature = lines[-1] if lines else error_text[:200]
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
    return f"[{tag}] {nature}"
