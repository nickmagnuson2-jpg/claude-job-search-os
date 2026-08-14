#!/usr/bin/env python3
"""
check_stale_file_claim.py — Stop hook for Claude Code.

Catches the failure where Claude TELLS the user a named file has not been
touched this session — when the filesystem says it was. A recency claim is an
absence claim about *edits*, and it has no tool call behind it, so no PreToolUse
or PostToolUse hook can see it. The only surface that can is the Stop hook,
which reads the finished assistant message.

Origin: 2026-07-31, a late-round interview-prep session. Twice I told Nick that
`073126-prep-evidence.md` "hasn't had a pass this session" and was
"untouched today." I never ran `ls -la`, never checked git, never opened the
file. Nick: "I created that whiteboard evidence today, so that's a little
wrong." He had built it that day. The false premise then produced a second,
worse error: I used it to tell him a workstream was under-served, devaluing
work he had just done.

Why a hook and not another rule: the absence-assertion rule was already a
CLAUDE.md Hard Rule when this fired — the highest behavioral tier short of a
hook — and it did not hold. Per `memory/feedback_llm_verification_system.md`
(Rule #1, 2026-07-31 supplement) and the ladder in
`memory/feedback_llm_self_policing_fails.md`: memory < skill < hook <
independent reviewer.

WHY IT CHECKS TRUTH, NOT PROCESS
  The first design asked "did you run `ls` before claiming this?" A smoke run
  over 4,085 real assistant messages produced 60 hits and ~zero true positives.
  The dominant false positive was the legitimate self-report — "`data/goals.md`
  untouched," meaning *I chose not to edit it*. That claim is well-grounded
  (Claude knows its own tool calls) and its evidence signature is identical to
  the defect: no tool call naming the path. Distinguishing the two is semantic,
  and per Rule #0 of the origin memory, a judgment-call trigger is the wrong
  thing to hook.

  So the hook runs the check itself. It fires only when the claim is
  *contradicted by mtime* — the file changed after this session began. That is
  deterministic, needs no intent inference, and catches the origin incident
  exactly. A self-report survives unless it is actually wrong, in which case
  blocking it is correct.

WHAT IT DOES
  Reads the last assistant text message, strips code blocks / blockquotes /
  URLs, finds recency-marked staleness assertions whose *immediate subject* is
  a file path, resolves the path, and compares its mtime to session start.

WHAT IT DOES NOT DO
  - Does not fire without a recency marker ("this session", "today", "yet").
    "Hasn't been updated since June" is a content claim, groundable by reading.
  - Does not fire on conditionals or questions.
  - Does not fire on paths inside fenced code, inline-code-only spans, or
    markdown blockquotes — those quote specs, users, and memory files.
  - Does not fire when the path is not the claim's immediate subject. A path
    floating elsewhere in the sentence is the top false-positive class.
  - Does not fire on files that do not exist (that is check_save_claims.py).
  - Does not judge whether an edit was meaningful, only whether one happened.

EXIT CODES
  0 — no claims, or every claim is consistent with mtime.
  2 — a claim contradicts the filesystem. Per tools/HOOK_AUTHORING.md, exit-0
      warnings are invisible in Claude Code, so a real warning must be exit 2.
      Single-correction violation: the file did change; restate accordingly.

Override: STALE_CLAIM_OVERRIDE=1
"""
import json
import os
import re
import sys
import time
from pathlib import Path

OVERRIDE_ENV = "STALE_CLAIM_OVERRIDE"

# Staleness assertions. Deliberately narrow.
STALENESS_RE = re.compile(
    r"(?:"
    r"(?:has|have|had|was|were|is|are)\s*(?:n['’]t|\s+not)\s+(?:\w+\s+){0,2}?"
    r"(?:touched|updated|modified|changed|edited|revised|revisited|rewritten"
    r"|worked\s+on|had\s+a\s+pass|seen\s+a\s+pass|gotten\s+a\s+pass)"
    r"|never\s+(?:been\s+)?(?:touched|updated|modified|changed|edited|revised)"
    r"|\b(?:untouched|unmodified|unchanged|unedited)\b"
    r")",
    re.I,
)

# A question or hypothesis, not an assertion.
CONDITIONAL_RE = re.compile(
    r"\b(?:if|unless|whether|in\s+case|suppose|assuming|when)\b", re.I
)

# Pins the claim to mtime rather than to file content. Without one of these the
# claim is about content history and this hook stays out of it.
#
# The two families take different thresholds, and the difference is load-bearing:
# the origin incident's file was created earlier on 2026-07-31, BEFORE the
# session opened. A session-start threshold would have missed it. "Today" claims
# are measured from local midnight; "this session" claims from session start.
RECENCY_TODAY_RE = re.compile(r"\b(?:today|this\s+morning|all\s+day)\b", re.I)
RECENCY_SESSION_RE = re.compile(
    r"\b(?:this\s+session|so\s+far|yet|in\s+this\s+conversation"
    r"|since\s+we\s+started|still)\b",
    re.I,
)

PATH_TOKEN = re.compile(r"[A-Za-z0-9_./~-]*[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,6}\b")
URL_RE = re.compile(r"\b(?:https?|ftp)://\S+|\bwww\.\S+", re.I)
NOISE = re.compile(r"^\d+\.\d+$")
SKIP_PREFIXES = ("http://", "https://", "www.", "mailto:")
FILE_EXT = (".py", ".md", ".html", ".pdf", ".json", ".yaml", ".yml", ".js",
            ".sh", ".txt", ".jpg", ".png", ".tsv", ".csv")

# Subject adjacency. The path must sit immediately before the claim with only
# short filler between ("X is untouched", "X has not been touched"). This is
# the fix for the top false-positive class found in the smoke run, where a
# 200-char lookbehind attached the claim to an unrelated nearby path.
MAX_GAP_CHARS = 34
MAX_GAP_WORDS = 4
GAP_BREAKERS = re.compile(r"[.!?;:)\]]|,\s*(?:and|but|so|which|where)\b", re.I)

# "nothing has been modified in X" — subject-negated, path trails the verb.
TRAILING_RE = re.compile(
    r"\bnothing\s+(?:has|have|was|were)\s+been\s+"
    r"(?:touched|updated|modified|changed|edited|revised|rewritten)\b",
    re.I,
)
TRAILING_GAP = 24

COND_WINDOW = 60
RECENCY_WINDOW = 120

PRUNE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}


def strip_noise(text: str) -> str:
    """Remove fenced code blocks, blockquotes, and URLs; unwrap inline code."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\s*>.*$", " ", text, flags=re.M)
    text = URL_RE.sub(" ", text)
    return text.replace("`", "")


def _clean_token(raw: str) -> str | None:
    tok = raw.strip("'\"()[],;:")
    if not tok or tok.lower().startswith(SKIP_PREFIXES) or NOISE.match(tok):
        return None
    if "/" not in tok and not tok.endswith(FILE_EXT):
        return None
    return tok


def _gap_is_filler(gap: str, max_chars: int) -> bool:
    """True if the text between path and claim is short connective filler."""
    if len(gap) > max_chars:
        return False
    if GAP_BREAKERS.search(gap):
        return False
    return len(gap.split()) <= MAX_GAP_WORDS


def _scope(text: str, start: int, end: int) -> str | None:
    """Return 'today', 'session', or None for the claim at [start, end)."""
    window = text[max(0, start - RECENCY_WINDOW):end + RECENCY_WINDOW]
    if RECENCY_TODAY_RE.search(window):
        return "today"
    if RECENCY_SESSION_RE.search(window):
        return "session"
    return None


def extract_stale_claims(text: str) -> list[tuple[str, str]]:
    """Yield (path, scope) for paths that are the immediate subject of a
    recency-marked staleness assertion. Order-preserving, deduped by path;
    the wider scope ('today') wins on a repeat."""
    text = strip_noise(text)
    out: list[str] = []
    scopes: dict[str, str] = {}

    def record(tok: str, scope: str) -> None:
        if tok not in scopes:
            out.append(tok)
            scopes[tok] = scope
        elif scope == "today":
            scopes[tok] = scope

    for m in STALENESS_RE.finditer(text):
        before = text[max(0, m.start() - COND_WINDOW):m.start()]
        if CONDITIONAL_RE.search(before):
            continue
        scope = _scope(text, m.start(), m.end())
        if scope is None:
            continue

        # Subject-first: nearest path immediately behind the claim.
        head = text[:m.start()]
        pm = None
        for cand in PATH_TOKEN.finditer(head):
            pm = cand
        if not pm or not _gap_is_filler(head[pm.end():], MAX_GAP_CHARS):
            continue
        tok = _clean_token(pm.group(0))
        if tok:
            record(tok, scope)

    # Subject-negated shape, path trailing the verb.
    for m in TRAILING_RE.finditer(text):
        before = text[max(0, m.start() - COND_WINDOW):m.start()]
        if CONDITIONAL_RE.search(before):
            continue
        scope = _scope(text, m.start(), m.end())
        if scope is None:
            continue
        tail = text[m.end():m.end() + TRAILING_GAP + 60]
        pm = PATH_TOKEN.search(tail)
        if not pm or not _gap_is_filler(tail[:pm.start()], TRAILING_GAP):
            continue
        tok = _clean_token(pm.group(0))
        if tok:
            record(tok, scope)

    return [(t, scopes[t]) for t in out]


def resolve_existing(tok: str, root: Path) -> Path | None:
    """Return the real file for a claimed token, or None if it does not exist."""
    direct = Path(os.path.expanduser(tok))
    if not direct.is_absolute():
        direct = root / direct
    if direct.is_file():
        return direct
    if "/" in tok or tok.startswith("~"):
        return None
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS and not d.startswith(".")]
        if tok in filenames:
            return Path(dirpath) / tok
    return None


def local_midnight() -> float:
    return time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))


def session_start(transcript_path: str) -> float:
    """Epoch seconds for the start of this session.

    The transcript is created when the session opens. Never earlier than local
    midnight, so a long-running session cannot widen the 'this session' window
    into yesterday.
    """
    midnight = local_midnight()
    p = Path(transcript_path)
    if not p.exists():
        return midnight
    try:
        return max(p.stat().st_ctime, midnight)
    except OSError:
        return midnight


def evaluate(text: str, root: Path, midnight: float,
             session: float) -> list[tuple[str, float]]:
    """Return (path, mtime) for claims the filesystem contradicts."""
    bad = []
    for tok, scope in extract_stale_claims(text):
        target = resolve_existing(tok, root)
        if target is None:
            continue
        try:
            mtime = target.stat().st_mtime
        except OSError:
            continue
        if mtime >= (midnight if scope == "today" else session):
            bad.append((tok, mtime))
    return bad


def read_last_assistant_text(transcript_path: str) -> str:
    """Return concatenated text of the final assistant message in the transcript."""
    p = Path(transcript_path)
    if not p.exists():
        return ""
    last = ""
    try:
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message") or {}
                if rec.get("type") != "assistant" and msg.get("role") != "assistant":
                    continue
                blocks = msg.get("content")
                if isinstance(blocks, str):
                    last = blocks
                elif isinstance(blocks, list):
                    parts = [
                        b.get("text", "")
                        for b in blocks
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    if any(parts):
                        last = "\n".join(parts)
    except OSError:
        return ""
    return last


def main() -> int:
    if os.environ.get(OVERRIDE_ENV) == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("stop_hook_active"):
        return 0

    transcript = payload.get("transcript_path") or ""
    text = read_last_assistant_text(transcript)
    if not text:
        return 0

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    bad = evaluate(text, root, local_midnight(), session_start(transcript))
    if not bad:
        return 0

    lines = [
        f"  - {tok}   last modified {time.strftime('%Y-%m-%d %H:%M', time.localtime(mt))}"
        for tok, mt in bad
    ]
    listing = "\n".join(lines)

    sys.stderr.write(
        "BLOCKED check_stale_file_claim: you told the user these files were "
        "untouched, but they changed during this session:\n"
        f"{listing}\n\n"
        "The claim is contradicted by mtime. Restate it with what the "
        "filesystem actually says. Do not repeat it unverified.\n"
        "If this is a false positive (you meant a narrower scope, such as "
        "'untouched by these commits'), say so explicitly and log it:\n"
        "  PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append "
        'check_stale_file_claim.py "FP: <what got wrongly blocked>"\n'
        f"Override for this turn: {OVERRIDE_ENV}=1\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
