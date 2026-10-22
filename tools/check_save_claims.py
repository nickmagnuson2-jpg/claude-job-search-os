#!/usr/bin/env python3
"""
check_save_claims.py — Stop hook for Claude Code.

Catches the failure where Claude TELLS the user a file was written and no write
actually happened. That claim has no tool call behind it, so no PreToolUse or
PostToolUse hook can see it. The only surface that can is the Stop hook, which
reads the finished assistant message.

Origin: 2026-08-04, onsite prep for a target company. Claude printed a voice-sim prompt to the
chat and wrote "Saved to output/acme/080426-casey-whiteboard-sim-prompt.md".
No Write call was ever made. Nick discovered it when he went to use the file.
Everything else that session (deck, transcripts, workflow files) was genuinely
written and verified — this one was asserted without checking.

Family: the verification-umbrella / never-assert-without-this-session-evidence
cluster. A completion claim is an assertion, and assertions about filesystem
state require a filesystem check.

WHAT IT DOES
  Reads the last assistant text message from the session transcript, strips
  fenced code blocks, finds PAST-TENSE completion claims ("saved to X",
  "written to X", "wrote X", "created X", "rendered X"), extracts
  the file path attached to each, and checks the path exists on disk.

WHAT IT DOES NOT DO
  - Does not fire on future/conditional phrasing ("I'll save this to X",
    "that would go in X", "drop it in X"). Only completed claims.
  - Does not fire on paths inside fenced code blocks (scripts legitimately
    reference files that do not exist yet).
  - Does not fire on directories, globs, or URLs.
  - Does not verify CONTENT, only existence. A file written with wrong content
    is a different failure and out of scope.

EXIT CODES
  0 — no claims, or every claimed path exists.
  2 — at least one claimed path is missing. Blocks the stop and returns the
      list to Claude so the claim gets corrected or the file gets written.
      Per tools/HOOK_AUTHORING.md, exit-0 warnings are invisible in Claude Code,
      so a real warning must be exit 2. This is an unambiguous, single-correction
      violation: either write the file or fix the sentence.

Override: SAVE_CLAIMS_OVERRIDE=1
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import claude_project_dir, last_assistant_text, read_payload  # noqa: E402
from pathlib import Path

OVERRIDE_ENV = "SAVE_CLAIMS_OVERRIDE"

# Past-tense / completed-action verbs. Deliberately narrow.
CLAIM_VERBS = r"(?:saved|written|wrote|created|persisted|rendered|copied|placed|added)"

# Phrasing that means the action has NOT happened yet. If any of these appear
# shortly before the verb, skip the claim.
FUTURE_MARKERS = re.compile(
    r"(?:\bI(?:'| a|'l)?ll\b|\bwill\b|\bwould\b|\bshould\b|\bcould\b|\bgoing to\b|"
    r"\babout to\b|\bplan to\b|\bcan be\b|\bto be\b|\bif you\b|\bonce you\b|\byou can\b|"
    r"\bnot\b|\bnever\b|\binstead of\b|\brather than\b)",
    re.I,
)

# A path-ish token: has a slash or a dot-extension, no spaces, not a URL.
PATH_TOKEN = re.compile(r"[A-Za-z0-9_./~-]*[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,6}\b")

# \b on BOTH sides is load-bearing, not decoration. Without it the verb matches INSIDE a
# word, and since the path window starts at the verb's end, the extracted token is the tail
# of whatever word contained it: "..._js_rendered_share_links.md" -> "_share_links.md", and
# "feedback_an_artifact_created_by_..." -> "_by_what_you_are_verifying_is_not_evidence.md".
# The hook then blocked on a path that appeared nowhere in the message and existed nowhere
# on disk, while the file it was really about had been written correctly. Underscore is a
# word character, so \b will not fire mid-identifier, which is exactly what is wanted.
# Fired 3x before this fix (2026-09-01, friction ledger).
CLAIM_RE = re.compile(rf"\b{CLAIM_VERBS}\b", re.I)

# How far after a claim verb we look for the path it refers to.
LOOKAHEAD = 140
# How far before the verb we look for future-tense markers.
LOOKBEHIND = 40

SKIP_PREFIXES = ("http://", "https://", "www.", "mailto:")
# Extensions that are almost always prose, not files (version numbers, decimals).
NOISE = re.compile(r"^\d+\.\d+$")
# A bare `Next.js` etc. is a library named in prose, not a file. An explicit list, not a
# Capitalized.js shape, so a false claim about a real `Deck.js` still reaches the
# existence check (Codex + Grok review). Fired 3x (2026-08-31..10-21).
LIBRARY_NAME = re.compile(
    r"^(?:Next|Node|Nuxt|Vue|React|Angular|Svelte|Ember|Express|D3|Three|Chart|Deno)\.js$")


URL_RE = re.compile(r"\b(?:https?|ftp)://\S+|\bwww\.\S+", re.I)


def strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks and URLs.

    Code blocks: scripts legitimately reference files that needn't exist.
    URLs: a remote path is not a filesystem claim, and the token matcher would
    otherwise grab the '//host/path.ext' tail after the scheme.
    """
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    return URL_RE.sub(" ", text)



def extract_claimed_paths(text: str):
    """Yield paths asserted as already written. Order-preserving, deduped."""
    text = strip_code_blocks(text)
    seen = set()
    out = []
    # Spans of every path-ish token, so a claim verb sitting INSIDE a filename cannot
    # start a window.
    #
    # `\b` on the verb guards against underscores (a word char, so \b will not fire
    # mid-identifier) but NOT against dots and hyphens, which are exactly what filenames
    # are made of. In `tide-chart-schedule.SAVED-noaa-090226.html` the `.` and `-` around
    # SAVED are both word boundaries, so `\bsaved\b` matched inside the filename, the
    # window opened at the verb's end, and the hook blocked on the fragment
    # `-noaa-090226.html` -- a "file" that appeared nowhere in the message and existed
    # nowhere on disk, while the real file had been written correctly.
    # Fired 2026-09-02 (friction ledger). Same class as the mid-word bug documented at
    # CLAIM_RE, which the \b fix only half-solved.
    path_spans = [pm.span() for pm in PATH_TOKEN.finditer(text)]
    for m in CLAIM_RE.finditer(text):
        if any(s <= m.start() and m.end() <= e for s, e in path_spans):
            continue
        before = text[max(0, m.start() - LOOKBEHIND):m.start()]
        if FUTURE_MARKERS.search(before):
            continue
        window = text[m.end():m.end() + LOOKAHEAD]
        # Stop the window at a sentence boundary so we don't grab the next claim's path.
        #
        # A BLANK LINE ENDS IT TOO, and that half is load-bearing. The sentence rule needs
        # `[A-Z]` after the period, so a paragraph ending "...to catch this class." followed
        # by "\n\n**[3] NEW — `feedback_x.md`**" did NOT cut: the next character is `*`.
        # The window then ran into the next paragraph and reported its filename as a claimed
        # write. That is the /lessons-learned Step 3 shape -- a PROPOSAL naming files that
        # correctly do not exist yet, because the skill mandates proposing before writing --
        # and it false-fired three times before this patch (friction ledger, 2026-09-01..02).
        # A claim and its referent live in the same paragraph; nothing across a blank line
        # is the referent of this verb.
        #
        # The sentence cut stays [A-Z] on purpose. Widening it to any non-lowercase start
        # (2026-10-21, 7497c5d) dropped real claims whose referent opens the next sentence:
        # "I saved the briefing. `output/acme/brief.md` is ready." Cross-model review
        # (Grok) caught it; the .claude/skills candidate path alone clears the false positive.
        cut = re.search(r"(?<=[.!?])\s+[A-Z]|\n[ \t]*\n", window)
        if cut:
            window = window[:cut.start()]
        for pm in PATH_TOKEN.finditer(window):
            tok = pm.group(0).strip("`'\"()[],;:")
            if (not tok or tok.lower().startswith(SKIP_PREFIXES) or NOISE.match(tok)
                    or LIBRARY_NAME.match(tok)):
                continue
            if "/" not in tok and not tok.endswith((".py", ".md", ".html", ".pdf",
                                                    ".json", ".yaml", ".yml", ".js",
                                                    ".sh", ".txt", ".jpg", ".png")):
                continue
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
            break  # first path after the verb is the referent
    return out


def project_root(raw: str | None = None) -> Path:
    """The project root, canonicalized ONCE so every resolver below can trust it.

    CLAUDE_PROJECT_DIR is normally absolute, but nothing guarantees it. A relative
    value ("." or "job-search") produced a nonsense memory-tier slug, so the hook
    searched a directory that does not exist and falsely blocked a real save. That
    is the exact failure direction this hook's memory-tier support was added to
    remove. Found by cross-model review 2026-09-07 (F1, P1).

    Fixed HERE rather than in the memory-tier lookup, because every candidate path
    and the `os.walk` basename search are built from root, and all are wrong for the
    same reason on a relative root. Normalizing at the single point of derivation
    fixes them together and leaves nothing to remember at the call sites.

    `abspath` and not `resolve()`: it normalizes "." and ".." and makes the path
    absolute WITHOUT following symlinks. Claude Code derives the memory-tier slug
    from the literal project path, so resolving symlinks here could point at a
    different tier than the one it actually writes to.
    """
    v = raw if raw is not None else (os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    return Path(os.path.abspath(os.path.expanduser(v)))


def memory_tier(root: Path, home: Path | None = None) -> Path:
    """This project's LIVE memory tier: `~/.claude/projects/<slugified-root>/memory/`.

    It sits OUTSIDE the repo and is where every `feedback_*`/`reference_*` rule and
    MEMORY.md are written, so the basename walk below can never reach it.
    """
    return claude_project_dir(root, home / ".claude" if home else None) / "memory"


def candidate_paths(tok: str, root: Path, home: Path | None = None) -> list[Path]:
    """Every place a claimed path may legitimately live, as written in prose.

    Each extra place exists because a real write was reported as a false claim:
      - root.parent/<tok>: a sibling repo named repo-relatively
        ("peer-repo/data/method.md"). Fired 3x, 2026-09-02..04.
      - .claude/skills/<tok>: a short skill path ("generate-cv/SKILL.md"). Fired 3x,
        2026-10-01, 2026-10-21 twice.
      - the memory tier: a bare rule-file name or a `memory/`-prefixed one. Fired 3x,
        2026-09-02, 2026-09-07 twice. Only those two forms: a deeper path is checked
        exactly, because a wrong directory is itself a defect worth catching.

    The extra places apply only to a relative token: one that starts with "~", "/" or
    "." is checked only where it points. A ".." segment anywhere else rules out the
    skills place, which resolves inside this repo; the sibling place accepts it, as the
    sibling lookup always did.
    """
    # Joining an absolute path onto root yields that path unchanged, so "/x" and "~/x"
    # come back as a single candidate from the escape check below.
    out = [root / os.path.expanduser(tok)]
    if not tok or tok.startswith(("~", "/", ".")):
        return out
    if "/" in tok:
        out.append(root.parent / tok)
    if ".." not in tok.split("/"):
        out.append(root / ".claude" / "skills" / tok)
    name = tok[len("memory/"):] if tok.startswith("memory/") else tok
    if name and "/" not in name:
        out.append(memory_tier(root, home) / name)
    return out


PRUNE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}


def exists_anywhere(tok: str, root: Path, home: Path | None = None) -> bool:
    """True if the claimed file exists.

    is_file(), never exists(): a DIRECTORY named like a file must not satisfy a file
    claim (cross-model review 2026-09-07, 20.F1 and 23.F2). A symlink to a regular file
    counts: the contract is existence, not authorship this turn.

    Bare basenames ("080326-deck-review-WORKFLOW.md") are common in prose, so a token
    with no separator that is not found at a candidate path is searched for by name
    across the repo before it is declared missing.
    """
    if any(c.is_file() for c in candidate_paths(tok, root, home)):
        return True
    # No basename can contain "/", so a slashed token would walk the whole repo for nothing.
    if "/" in tok or tok.startswith("~"):
        return False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS and not d.startswith(".")]
        if tok in filenames:
            return True
    return False


def main() -> int:
    if os.environ.get(OVERRIDE_ENV) == "1":
        return 0

    p = read_payload()
    if not p.ok:
        return 0
    payload = p.data

    # Recursion guard — never re-enter our own stop chain.
    if payload.get("stop_hook_active"):
        return 0

    transcript = payload.get("transcript_path") or ""
    text = last_assistant_text(transcript)
    if not text:
        return 0

    root = project_root()
    missing = []
    for tok in extract_claimed_paths(text):
        if not exists_anywhere(tok, root):
            missing.append(tok)

    if not missing:
        return 0

    listing = "\n".join(f"  - {m}" for m in missing)
    sys.stderr.write(
        "BLOCKED check_save_claims: you told the user these files were written, "
        "but they do not exist on disk:\n"
        f"{listing}\n\n"
        "Either actually write them now, or correct the sentence so it does not "
        "claim a write that did not happen. Do not restate the claim.\n"
        "If this is a false positive (a path you referenced but never claimed to "
        "have written), log it:\n"
        "  PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append "
        'check_save_claims.py "FP: <what got wrongly blocked>"\n'
        f"Override for this turn: {OVERRIDE_ENV}=1\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
