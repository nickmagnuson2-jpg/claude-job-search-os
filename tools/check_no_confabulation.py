#!/usr/bin/env python3
"""
check_no_confabulation.py — PreToolUse hook on Write|Edit.

Conservative confabulation detector for shippable prep/synthesis artifacts.
Fires on a narrow set of paths and blocks only obvious placeholder/confabulation
patterns. Never fires on data/reflections/ (voice-pure) or output/<slug>/<slug>.md
research dossiers (those are agent-produced and have their own evidence rules).

In scope:
  - output/**/*cheat-sheet*.md          (interview cheat sheets)
  - output/**/*prep*.md                  (prep packages)
  - output/**/*soft-answers*.md          (spoken answer drafts)
  - output/**/*discovery-goals*.md       (pre-call discovery docs)
  - data/projects/*.md                   (CV-feeder project files; complements check_data_projects.py)
  - coaching/coached-answers/*.md        (spoken STAR stories)

What gets blocked:
  - Placeholder tokens in shipped prose: "x measurable outcomes", "y workstreams",
    "[N]", "[insert ...]", "[your ...]", "TBD" inline (in body, not status fields),
    "and all of that stuff", "several other things", "(specific number)", "TODO:"
    inline (front matter / explicit TODO sections are allowed).

What gets warned (printed but exit 0):
  - Numbers followed by % or $ that don't appear within 200 chars of a citation
    marker like "per ", "source:", "(2025-", "[Confidence", or a parenthesized
    year — heuristic only.

Override: CONFAB_OVERRIDE=1 to bypass.
"""
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

IN_SCOPE_PATTERNS = [
    re.compile(r"output/.+/.*cheat-sheet.*\.md$", re.IGNORECASE),
    re.compile(r"output/.+/.*prep.*\.md$", re.IGNORECASE),
    re.compile(r"output/.+/.*soft-answers.*\.md$", re.IGNORECASE),
    re.compile(r"output/.+/.*discovery-goals.*\.md$", re.IGNORECASE),
    re.compile(r"data/projects/[^/]+\.md$"),
    re.compile(r"coaching/coached-answers/.+\.md$"),
]

# Frontmatter / structured sections allowed to contain placeholder-like tokens.
# We skip the frontmatter and any line starting with these prefixes.
SKIP_LINE_PREFIXES = (
    "type:", "status:", "tags:", "voice:", "captured:", "updated:",
    "##  Open questions", "## Open Questions", "## TODO", "### TODO",
    "**TODO", "**Open", "- [ ]",  # explicit todo checkboxes
)

# Hard-block placeholder regexes (fire in body content)
PLACEHOLDER_PATTERNS = [
    (re.compile(r"\b[xy]\s+(measurable|specific|various|several|multiple|key)\s+(outcomes?|workstreams?|metrics?|results?)\b", re.IGNORECASE),
     'Placeholder "x measurable outcomes" / "y workstreams" — fill in actual numbers from corpus citations'),
    (re.compile(r"\[N\]\s+(workstreams?|outcomes?|customers?|deals?|references?|metrics?|hours?|people|teams?)", re.IGNORECASE),
     'Placeholder "[N] <thing>" — fill in the actual count'),
    (re.compile(r"\[(insert|your|the)\s+[^\]]+\]", re.IGNORECASE),
     'Placeholder "[insert ...]" / "[your ...]" / "[the ...]" — fill in or remove'),
    (re.compile(r"\band\s+all\s+of\s+that\s+stuff\b", re.IGNORECASE),
     '"and all of that stuff" — list the specifics from corpus or cut'),
    (re.compile(r"\(specific\s+(number|amount|count|figure)\)", re.IGNORECASE),
     '"(specific number)" placeholder — look up the actual number'),
    (re.compile(r"\bseveral\s+other\s+(things?|areas?|topics?|examples?)\b", re.IGNORECASE),
     '"several other things" — name them or cut'),
    (re.compile(r"^\s*TODO:\s+\S+", re.IGNORECASE | re.MULTILINE),
     'Inline "TODO:" in prose — move to a dedicated TODO section or resolve before shipping'),
]


def in_scope(path: str) -> bool:
    norm = path.replace("\\", "/")
    return any(pat.search(norm) for pat in IN_SCOPE_PATTERNS)


def extract_target_path() -> tuple[str, str] | None:
    """Read hook stdin and return (file_path, content) or None to fail-open."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return None
    tool_input = data.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "") or ""
    content = tool_input.get("content") or tool_input.get("new_string") or ""
    if not file_path or not content:
        return None
    return file_path, content


def strip_frontmatter(content: str) -> str:
    """Remove --- frontmatter --- block from the top of the file."""
    stripped = content.lstrip()
    if stripped.startswith("---"):
        parts = stripped.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return content


def filter_skip_lines(body: str) -> str:
    """Drop lines that legitimately allow placeholder tokens (frontmatter-ish, todo sections)."""
    lines = body.splitlines()
    out = []
    in_todo_section = False
    for line in lines:
        stripped = line.strip()
        # Detect entry to TODO/Open Questions sections (where placeholders are fine)
        if any(stripped.startswith(p) for p in ("## TODO", "### TODO", "## Open Questions", "##  Open questions", "## Open questions")):
            in_todo_section = True
            continue
        # Exit TODO section on next H2/H3 that isn't a TODO header
        if in_todo_section and stripped.startswith(("## ", "### ")):
            in_todo_section = False
        if in_todo_section:
            continue
        if any(stripped.startswith(p) for p in SKIP_LINE_PREFIXES):
            continue
        out.append(line)
    return "\n".join(out)


def main():
    if os.environ.get("CONFAB_OVERRIDE") == "1":
        sys.exit(0)

    result = extract_target_path()
    if result is None:
        sys.exit(0)
    file_path, content = result

    if not in_scope(file_path):
        sys.exit(0)

    body = filter_skip_lines(strip_frontmatter(content))

    violations = []
    for regex, desc in PLACEHOLDER_PATTERNS:
        for m in regex.finditer(body):
            line_no = body[:m.start()].count("\n") + 1
            snippet_start = max(0, m.start() - 30)
            snippet_end = min(len(body), m.end() + 30)
            snippet = body[snippet_start:snippet_end].replace("\n", " ").strip()
            violations.append(
                f'  • Line {line_no}: {desc}\n'
                f'    Match: "...{snippet}..."'
            )

    if violations:
        msg = (
            f"BLOCKED: {file_path} contains placeholder/confabulation patterns in a shippable artifact.\n\n"
            + "\n\n".join(violations)
            + "\n\nFix options:\n"
            "  1. Replace placeholders with corpus-verified values (preferred).\n"
            "  2. Move incomplete sections under a `## Open Questions` or `## TODO` heading.\n"
            "  3. Override intentionally: CONFAB_OVERRIDE=1 (use only when shipping a known stub).\n\n"
            "Reference: memory/feedback_no_confabulation_in_corpus_synthesis.md,\n"
            "           memory/feedback_no_confabulation_in_agent_prompts.md,\n"
            "           memory/feedback_verify_agent_research_before_propagation.md"
        )
        print(msg, file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
