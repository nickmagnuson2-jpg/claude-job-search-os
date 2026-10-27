#!/usr/bin/env python3
"""
check_save_claims.py — Stop hook for Claude Code.

Catches the failure where Claude TELLS the user a file was written and no write
actually happened. That claim has no tool call behind it, so no PreToolUse or
PostToolUse hook can see it. The only surface that can is the Stop hook, which
reads the finished assistant message.

Origin: interview prep for a target company. Claude printed a voice-sim prompt to the
chat and wrote "Saved to output/acme/<MMDDYY>-casey-sim-prompt.md".
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

  It also checks the inverse claim, an ABSENCE claim ("lessons.md is missing",
  "X does not exist"), with the same lookup: if the named file is on disk in the
  repo, its ./memory/, a sibling repo, .claude/skills or the live memory tier,
  the claim is false and the stop is blocked. Skipped when the sentence is a
  conditional or a question, or when a location scope follows ("in the sidecar",
  "in ./memory", "at that path"), since a scoped claim may be true.
  Origin: feedback_two_memory_locations_check_both_before_absence (4 fires).

WHAT IT DOES NOT DO
  - Does not fire on future/conditional phrasing ("I'll save this to X",
    "that would go in X", "drop it in X"). Only completed claims.
  - Does not fire on paths inside fenced code blocks (scripts legitimately
    reference files that do not exist yet).
  - Does not fire on directories, globs, or URLs.
  - Does not verify CONTENT, only existence. A file written with wrong content
    is a different failure and out of scope.

EXIT CODES
  0 — no claims; every path claimed as written exists; and no file claimed
      missing is on disk.
  2 — a path claimed as written is missing (fix: write it, or correct the
      sentence), or a file claimed missing is on disk (fix: correct or scope the
      absence sentence; never write anything). Blocks the stop and says which.
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

# An inline-code span holding whitespace is a quoted COMMAND (`cp a.md out/`), and a file
# named inside it is what the command would touch, not a file the message says was written.
# Two false blocks in one session (2026-10-26, friction ledger): `cp x-handoff.md
# output/analysis/` and a copy-folder example were read as claimed writes. A bare
# `notes.md` span has no whitespace and still counts.
# Spans are found in order and then filtered: matching only spans WITH whitespace would
# pair a closing backtick with the next opening one ("`a.md` and `b.md`").
CODE_SPAN = re.compile(r"`[^`\n]+`")


def strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks and URLs.

    Code blocks: scripts legitimately reference files that needn't exist.
    URLs: a remote path is not a filesystem claim, and the token matcher would
    otherwise grab the '//host/path.ext' tail after the scheme.
    """
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    return URL_RE.sub(" ", text)



def _is_command(span: str) -> bool:
    """A code span reads as a command when its first word is a bare program name (no `/`,
    no `.`) followed by more words. `cp a.md out/` is one; `output/acme/my brief.md`, a
    real file whose name has a space, is not (codex review, 2026-10-26)."""
    words = span.strip("`").split()
    return len(words) >= 2 and re.fullmatch(r"[A-Za-z][\w-]*", words[0]) is not None


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
    command_spans = [cm.span() for cm in CODE_SPAN.finditer(text) if _is_command(cm.group(0))]
    all_tokens = list(PATH_TOKEN.finditer(text))
    path_spans = [pm.span() for pm in all_tokens]
    path_tokens = [pm for pm in all_tokens
                   if not any(s <= pm.start() and pm.end() <= e for s, e in command_spans)]
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
        # A line opening with a "File:" label also ends the window: /lessons-learned lists
        # proposed target files that way, and a claim verb above it ("I copied it") is not
        # about that file (2nd ledger fire, 2026-10-21; promotion drain build 296).
        cut = re.search(r"(?<=[.!?])\s+[A-Z]|\n[ \t]*\n|\n[ \t]*File:", window)
        if cut:
            window = window[:cut.start()]
        # Tokens come from the FULL text and must lie wholly inside the window. Matching
        # inside the window read a filename the 140-char limit cut in half as a whole token
        # ("job-todos.p"), and an existing file was reported missing (2026-10-26). A path the
        # limit cuts is out of reach, not a fragment: taking it whole instead added two false
        # blocks in a replay of 15,146 real messages, both paths in the next bullet.
        lo, hi = m.end(), m.end() + len(window)
        for pm in (pm for pm in path_tokens if lo <= pm.start() and pm.end() <= hi):
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


# The inverse claim: "X is missing" while X is on disk. Origin 2026-06-12: `lessons.md`
# was declared gone after checking only the live memory tier; it lives in the repo's
# ./memory/ (feedback_two_memory_locations_check_both_before_absence, 4 fires). Base is
# that rule's detector_signature, narrowed by a replay of 14,896 real assistant messages
# (2026-10-22): the signature's 40-char gap blocked 13 times and 10 were about something
# other than the file ("X's history is missing the 9/04 entries", "X claims a file that
# doesn't exist", "X is missing companies", "X is absent from that list"). So the file
# must be the direct subject (only markup or a colon between) and the phrase must end
# its clause. A "**X doesn't exist:**" case label ends in a colon and is not a claim.
ABSENCE_RE = re.compile(
    r"(?P<tok>[A-Za-z0-9_./~-]*[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,6})\b[\s`*:]{0,6}"
    r"(?:is missing|is gone|does not exist|doesn['’]t exist|no longer exists"
    r"|is absent|not found)\b"
    r"(?=[\s`*]*(?:[.,;!?)\n]|$|\b(?:but|and|so|yet|now|anymore)\b))", re.I)
# A scope after the phrase makes it a claim about one location, which may be true.
ABSENCE_SCOPED = re.compile(
    r"[^\n.!?]{0,60}?\b(?:(?:in|from|under) (?:the sidecar|the live tier|~/\.claude|\./memory"
    r"|the repo|that (?:dir|directory|folder|location|root))|at that path)", re.I)
# A hypothesis or a question in the same sentence is not an assertion.
ABSENCE_CONDITIONAL = re.compile(r"\b(?:if|when|unless|whether|in case)\b", re.I)
SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")
# An inline-code span that holds the absence phrase itself quotes a tool's message
# ("The error says `x.md is missing`"), so it is not the assistant's claim. A span
# holding only the file name ("`x.md` is missing") is untouched. Final review 552.F2.
QUOTED_DIAGNOSTIC = re.compile(
    r"`[^`\n]*(?:is missing|is gone|does not exist|doesn['’]t exist|no longer exists"
    r"|is absent|not found)[^`\n]*`", re.I)


def extract_absence_claims(text: str) -> list[str]:
    """File tokens the message asserts are missing. Code blocks, blockquotes and URLs
    are skipped: they quote scripts, the user, or memory files."""
    text = strip_code_blocks(re.sub(r"^\s*>.*$", " ", text, flags=re.M))
    text = QUOTED_DIAGNOSTIC.sub(" ", text)
    out = []
    for m in ABSENCE_RE.finditer(text):
        tok = m.group("tok")
        starts = [s.end() for s in SENTENCE_END.finditer(text, 0, m.start())]
        end = SENTENCE_END.search(text, m.end())
        if (ABSENCE_CONDITIONAL.search(text, starts[-1] if starts else 0, m.end())
                or (end and end.group() == "?")
                or ABSENCE_SCOPED.match(text, m.end()) or tok in out):
            continue
        out.append(tok)
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

    Bare basenames ("010526-deck-review-WORKFLOW.md") are common in prose, so a token
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

    # Recursion guard — never re-enter our own stop chain. Read through the accessor, which
    # raises on an unread payload, so the `.ok` check above is observable (HOOK_AUTHORING.md);
    # `.data` is silently empty there and made that check an equivalent mutant.
    if p.stop_hook_active:
        return 0

    transcript = payload.get("transcript_path") or ""
    text = last_assistant_text(transcript)  # "" when there is nothing to read: no claims

    root = project_root()
    missing = [t for t in extract_claimed_paths(text) if not exists_anywhere(t, root)]
    present = [t for t in extract_absence_claims(text) if exists_anywhere(t, root)]

    if not missing and not present:
        return 0

    for tok in present:
        sys.stderr.write(
            f"BLOCKED check_save_claims (absence): you told the user {tok} does not exist, "
            "but a file by that name is on disk (repo, its ./memory/, a sibling repo, "
            ".claude/skills, or the live memory tier). Check both memory roots and correct "
            "the sentence, or scope it to the location you checked.\n\n")
    if missing:
        listing = "\n".join(f"  - {m}" for m in missing)
        sys.stderr.write(
            "BLOCKED check_save_claims: you told the user these files were written, "
            "but they do not exist on disk:\n"
            f"{listing}\n\n"
            "Either actually write them now, or correct the sentence so it does not "
            "claim a write that did not happen. Do not restate the claim.\n")
    sys.stderr.write(
        "If this is a false positive, log it:\n"
        "  PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append "
        'check_save_claims.py "FP: <what got wrongly blocked>"\n'
        f"Override for this turn: {OVERRIDE_ENV}=1\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
