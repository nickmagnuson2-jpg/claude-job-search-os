#!/usr/bin/env python3
"""
check_draft_artifact.py — PreToolUse hook for Claude Code (Bash).

Blocks open_draft.py when the staged draft carries an artifact (an ATTACH: line, or a link
other than the bio links) and no ARTIFACT: header saying what it is.

Why this exists:
  An artifact that left by email with no build log is invisible to everything that reads the
  logs (tools/build_check.py). Proposal 1c-S of the 2026-10-23 codify run, approved by Nick
  2026-10-26: the draft Claude stages is the one place a send can be stopped before Gmail.
  It is partial by construction: Nick attaches files and presses Send himself, so a file
  attached in Gmail never passes here. tools/sent_artifact_check.py finds those after the send.

What passes:
  - no ATTACH: line and no link other than the bio links;
  - an attachment that is a rendered CV (the exact CV name, with its RenderCV .yaml beside it
    under output/; the rule lives in tools/sent_artifact_check.py and is imported, not copied);
  - `ARTIFACT: none (<reason>)` with a reason, for a plain link such as a job posting;
  - `ARTIFACT: output/<slug>/BUILD-LOG.md` naming a log that exists with reader_ask filled or
    Nick's waiver (read by tools/build_check.ask_state, the one reader of those lines).
Any link counts, not a list of hosts, so a custom domain is caught. The bio links and the URL
pattern come from tools/sent_artifact_check.py, so the two checks agree on what a link is.

Exit codes:
  0 — clean / not applicable / fail-open (no draft, unreadable draft)
  2 — the draft carries an artifact with no valid ARTIFACT: header

Override: DRAFT_OVERRIDE=1 in the hook process environment (an inline prefix on the blocked
Bash command does NOT reach the hook).
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_check import ask_state  # noqa: E402
from check_draft_voice import extract_body, is_open_draft_invocation, read_hook_command  # noqa: E402
from sent_artifact_check import URL_RE, cv_index, is_bio, load_bio  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DRAFT_FILE = Path(__file__).parent / ".pending-draft.txt"

NONE_RE = re.compile(r"^none\s*\(\s*\S.*\)$", re.I)
LOG_RE = re.compile(r"^output/[^/\s]+/(?:[^\s]+/)?BUILD-LOG\.md$")


def headers(content: str) -> tuple[list[str], list[str], str]:
    """(ATTACH: values, ARTIFACT: values, SUBJECT:) from the lines before BODY:."""
    attach, artifact, subject = [], [], ""
    for line in content.split("\n"):
        if line.startswith("BODY:"):
            break
        if line.startswith("ATTACH:") and line[7:].strip():
            attach.append(line[7:].strip())
        elif line.startswith("ARTIFACT:"):
            artifact.append(line[9:].strip())
        elif line.startswith("SUBJECT:") and not subject:
            subject = line[8:]
    return attach, artifact, subject


def _is_rendered_cv(path: str, cvs: set[str]) -> bool:
    """The attached file itself is a rendered CV: the exact CV name AND its RenderCV .yaml
    beside it. A different file that only shares the name does not pass (699.F1)."""
    p = Path(path)
    return p.name in cvs and p.with_suffix(".yaml").is_file()


def artifact_items(content: str, root: Path) -> list[str]:
    """Attachments that are not a rendered CV, and links that are not a bio link."""
    attach, _, subject = headers(content)
    cvs = cv_index(root)
    bio, _err = load_bio(root / "tools")
    files = [a for a in attach if not _is_rendered_cv(a, cvs)]
    text = subject + "\n" + extract_body(content)
    links = []
    for u in URL_RE.findall(text):
        u = u.rstrip(".,;:!?")
        if not is_bio(u, bio) and u not in links:
            links.append(u)
    return files + links


def reader_tokens(log_text: str) -> list[str]:
    """The name words a draft must carry. A `reader_names:` line (comma-separated people,
    for a build with several readers) wins; otherwise the first two words of `reader:`.
    2026-10-27: a group reader line opens with the company ("Acme interviewers (...)"), so
    its first two words never named the person a follow-up went to."""
    names = re.search(r"^\s*reader_names:[ \t]*(\S.*)$", log_text, re.M)
    # An unfilled template placeholder ("<First Last, ...>") is not a list of names; its
    # filler words matched ordinary text, including the TO: label (review 735.F1).
    if names and not names.group(1).lstrip().startswith("<"):
        return [w for n in names.group(1).split(",") for w in re.findall(r"[A-Za-z][\w'-]*", n)]
    m = re.search(r"^\s*reader:\s*([A-Za-z][\w'-]*)(?:\s+([A-Za-z][\w'-]*))?", log_text, re.M)
    return [n for n in m.groups() if n] if m else []


def _carries(text: str, tokens: list[str]) -> bool:
    """A name token appears as a whole word: "dan" must not match "jordan@" (grok review)."""
    low = text.lower()
    return any(re.search(rf"(?<![a-z]){re.escape(n.lower())}(?![a-z])", low) for n in tokens)


def reader_named(log_text: str, content: str) -> bool:
    """Every recipient is a reader the log names, so a filled log for one person does not
    clear a draft to another (codex review, 2026-10-26). Each comma-separated TO entry
    (display name and address) must carry a name; a single entry whose address carries
    none, such as a shared inbox, may instead be named in the greeting. A named reader
    plus an outsider blocks (review 734.F1 / 735.F2). The TO: label is not read."""
    tokens = reader_tokens(log_text)
    to_value = " , ".join(l[len("TO:"):] for l in content.split("\n") if l.startswith("TO:"))
    recipients = [r.strip() for r in re.split(r"[,;]", to_value) if r.strip()]
    if recipients and all(_carries(r, tokens) for r in recipients):
        return True
    opening = "\n".join(extract_body(content).split("\n")[:3])
    return len(recipients) <= 1 and _carries(opening, tokens)


def header_problem(values: list[str], root: Path, content: str = "") -> str | None:
    """None when the ARTIFACT: header accounts for the draft; otherwise what is wrong."""
    if not values:
        return "no ARTIFACT: header"
    value = values[-1]
    if NONE_RE.match(value):
        # `none` covers plain links only: a file attached to the draft is a built thing
        # and needs its log (round-12 review, 692.F1 and six duplicates).
        attached = [a for a in artifact_items(content, root) if a in headers(content)[0]]
        if attached:
            return ("ARTIFACT: none covers links only; the attached file needs "
                    "output/<slug>/BUILD-LOG.md")
        return None
    if LOG_RE.match(value):
        path = root / value
        if not path.is_file():
            return f"ARTIFACT: names {value}, which does not exist"
        log_text = path.read_text(encoding="utf-8", errors="replace")
        state = ask_state(log_text)
        if state == "missing":
            return f"ARTIFACT: names {value}, whose reader_ask is not filled"
        if state == "filled" and not reader_named(log_text, content):
            return (f"ARTIFACT: names {value}, whose reader is not the person this draft "
                    "goes to (no match in TO: or the greeting)")
        return None
    return f"ARTIFACT: {value!r} is neither `none (<reason>)` nor output/<slug>/BUILD-LOG.md"


def decide(content: str, root: Path = REPO_ROOT) -> str | None:
    """The BLOCK message for this draft, or None to allow. Pure given the filesystem."""
    items = artifact_items(content, root)
    if not items:
        return None
    _, values, _ = headers(content)
    problem = header_problem(values, root, content)
    if problem is None:
        return None
    listing = "\n".join(f"    {i}" for i in items)
    return (
        "BLOCKED check_draft_artifact: the staged draft carries an artifact with no record.\n\n"
        f"  {problem}. The draft carries:\n{listing}\n\n"
        "Add one line above BODY: in tools/.pending-draft.txt:\n"
        "  ARTIFACT: none (<reason>)               a plain link, such as a job posting\n"
        "  ARTIFACT: output/<slug>/BUILD-LOG.md    a page, demo or file built for this reader\n"
        "The log must have reader_ask filled (framework/artifact-workflow.md). A rendered CV\n"
        "passes without it. If this is a false positive, log it:\n"
        '  PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append check_draft_artifact.py "FP: <what>"\n'
        "and override once with DRAFT_OVERRIDE=1 in the hook environment."
    )


def main():
    if os.environ.get("DRAFT_OVERRIDE") == "1":
        sys.exit(0)
    command = read_hook_command()
    if not is_open_draft_invocation(command):
        sys.exit(0)
    try:
        content = DRAFT_FILE.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        sys.exit(0)  # fail-open: no draft, or unreadable
    message = decide(content)
    if message:
        print(message, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
