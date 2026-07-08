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
_BLOCK_OPERAND_RE = re.compile(r"BLOCKED:\s*([a-z0-9_]+\.py)\b", re.IGNORECASE)
# Fallback: the check_*.py named in "[python3 /.../check_foo.py]:".
_BLOCK_CHECK_RE = re.compile(r"\b(check_[a-z0-9_]+\.py)\b", re.IGNORECASE)
# Any BLOCKED marker (to know we're in a block at all).
_BLOCK_MARKER_RE = re.compile(r"\bBLOCKED\b", re.IGNORECASE)

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


def _surface_from_block(error_text: str) -> str:
    """If the failure is a PreToolUse BLOCK, attribute to the mis-called script."""
    if not error_text or not _BLOCK_MARKER_RE.search(error_text):
        return ""
    m = _BLOCK_OPERAND_RE.search(error_text)
    if m:
        cand = m.group(1).lower()
        if cand not in EXCLUDE_SCRIPTS:
            return cand
    m = _BLOCK_CHECK_RE.search(error_text)
    if m:
        cand = m.group(1).lower()
        if cand not in EXCLUDE_SCRIPTS:
            return cand
    return ""


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


def _surface_from_inline(command: str, error_text: str) -> str:
    """Attribute an inline `python3 -c` failure to the real module, not bash:cd."""
    if not _INLINE_C_RE.search(command):
        return ""
    # 1. A real .py in the traceback is the most reliable signal.
    files = _TRACEBACK_PYFILE_RE.findall(error_text or "")
    if files:
        return files[-1].rsplit("/", 1)[-1].lower()
    # 2. Imported modules in the -c body: prefer the first NON-stdlib module
    #    (handles `import os, glob, ss_log_append as s` -> ss_log_append.py,
    #    not the junk `inline:os`). Fall back to inline:<stdlib> when every
    #    import is stdlib noise, then inline-python when there's no import.
    body = _inline_c_body(command)
    mods = _imported_modules(body)
    non_std = [m for m in mods if m.lower() not in _STDLIB_NOISE]
    if non_std:
        return f"{non_std[0]}.py"
    if mods:
        return f"inline:{mods[0]}"
    return "inline-python"


def derive_surface(tool_name: str, command: str, error_text: str = "") -> str:
    """The 'surface' is what failed. Precedence:
    1. non-Bash tool      -> tool:<name>
    2. explicit tools/*.py -> that script (existing, well-tested path)
    3. PreToolUse BLOCK    -> the mis-called operand script
    4. inline python -c    -> real .py in traceback, else first import
    5. fallback            -> bash:<first-non-env-token>
    """
    tool_name = (tool_name or "").strip()
    if tool_name != "Bash":
        return f"tool:{tool_name}" if tool_name else "tool:unknown"

    command = command or ""
    matches = SCRIPT_RE.findall(command)
    scripts = [(m + ".py").lower() for m in matches if (m + ".py").lower() not in EXCLUDE_SCRIPTS]
    if scripts:
        return scripts[0]

    s = _surface_from_block(error_text)
    if s:
        return s

    s = _surface_from_inline(command, error_text)
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
