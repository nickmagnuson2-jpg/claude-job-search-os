#!/usr/bin/env python3
"""
gen_pii_denylist.py — Generate the gitignored PII denylist used by check_public_pii.py.

Reads the canonical real-entity sources (data/networking.md contacts + interaction
log, data/job-pipeline.md company column) and emits distinctive tokens that must
NEVER appear in a public-repo artifact (tests/, .claude/skills/, framework/, docs/,
root *.md, tool comments). Output: tools/.pii-denylist.txt (one token per line,
gitignored — the list itself is PII).

Design choices that keep the deterministic hook from false-positive hell:
  - Person names: only full "First Last" phrases (>=2 tokens). Distinctive, low FP.
    Bare first names are left to the semantic /audit-pii subagent pass.
  - Companies: multi-word names kept whole; single-word names kept ONLY if they are
    distinctive brand tokens, not ordinary English words. A company name that is also a
    common word (e.g. a firm named after an everyday noun) would match ordinary prose,
    so it is excluded here and caught by the subagent instead.
  - KEEP list: real employers/schools/public authors/generic products are public-safe
    per the PII boundary (feedback_nick_pii_redaction_boundary) — never denylisted.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/gen_pii_denylist.py [--repo-root PATH] [--dry-run]

Origin: 2026-06-11, promotion of feedback_generalize_examples_in_public_artifacts
to hook tier (Nick requested the hook + subagent audit).
"""
import argparse
import json
import re
from pathlib import Path

OUTPUT_REL = "tools/.pii-denylist.txt"

# Public-safe entities — real employers, schools, public-figure authors, generic
# products. These appear legitimately in public skill/framework docs. Lowercased.
KEEP = {
    "zuora", "mckinsey", "tuck", "duke", "yahoo", "espn", "yahoo sports",
    "notion", "stripe", "anthropic", "google", "github", "openai", "linkedin",
    "ali rohde", "ryan holiday", "molly graham", "raphael", "daily stoic",
    "claude", "granola", "wispr", "obsidian", "vercel", "neon",
}

# Generic glue / industry-suffix words. A single-token company name that is just one
# of these (or, via the system dictionary, any ordinary English word) is excluded from
# the deterministic denylist and left to the semantic subagent audit. The dictionary is
# the primary filter for word-like company names; this set only covers generic terms a
# dictionary might miss. Deliberately holds NO real company names (that would itself be PII).
STOPWORDS = {
    "the", "a", "an", "of", "and", "co", "inc", "ai", "labs", "health",
    "care", "agents", "robotics", "ventures", "capital", "partners", "group", "omni",
}


SYSTEM_DICT = Path("/usr/share/dict/words")


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return ""


def load_dictionary() -> set[str]:
    """Lowercased English words from the system dictionary, for filtering single-word
    company names that are ordinary words (would false-positive against prose). Empty
    set if the dictionary is unavailable — callers fall back to STOPWORDS only."""
    try:
        return {w.strip().lower() for w in SYSTEM_DICT.read_text(encoding="utf-8").splitlines() if w.strip()}
    except (FileNotFoundError, PermissionError):
        return set()


def parse_networking_names(content: str) -> set[str]:
    """Full person names from the contacts table (col 0) and ### interaction headers."""
    names: set[str] = set()
    for line in content.splitlines():
        # Contacts table rows: | Name | Company | Role | ...
        if line.startswith("|"):
            cols = [c.strip() for c in line.strip("|").split("|")]
            if cols and cols[0] and cols[0].lower() not in ("name", "---") and not cols[0].startswith("--"):
                names.add(cols[0])
        # Interaction-log subsection headers: ### Name — Company
        m = re.match(r"^###\s+(.+?)(?:\s+[—–|]\s+.+)?$", line)
        if m:
            names.add(m.group(1).strip())
    return names


def parse_pipeline_companies(content: str) -> set[str]:
    """Company names from the pipeline table (col 0)."""
    companies: set[str] = set()
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if cols and cols[0] and cols[0].lower() not in ("company", "---") and not cols[0].startswith("--"):
            companies.add(cols[0])
    return companies


def is_distinctive_single(token: str, dictionary: set[str]) -> bool:
    """A single-word token is safe to denylist only if it is a distinctive brand name,
    not a common English word (which would match ordinary prose in skill/framework docs)."""
    t = token.strip().lower()
    if len(t) < 4 or t in STOPWORDS or t in KEEP:
        return False
    # Tokens with a digit, dot, or internal capital are obviously brandish (e.g.
    # "7x.ai", "FooBar.ai", "LocateThing") — always distinctive regardless of the dictionary.
    if any(ch.isdigit() for ch in token) or "." in token or re.search(r"[a-z][A-Z]", token):
        return True
    return t not in dictionary


def build_denylist(names: set[str], companies: set[str], dictionary: set[str]) -> list[str]:
    out: set[str] = set()

    for name in names:
        clean = name.strip().strip("*").strip()
        # Drop trailing parentheticals / qualifiers
        clean = re.sub(r"\s*\(.*\)$", "", clean).strip()
        if not clean or clean.lower() in KEEP:
            continue
        tokens = clean.split()
        # Only keep multi-token full names — distinctive, low false-positive risk.
        if len(tokens) >= 2 and all(re.match(r"^[A-Za-zÀ-ÿ.'\-]+$", t) for t in tokens):
            out.add(clean)

    for company in companies:
        clean = company.strip().strip("*").strip()
        clean = re.sub(r"\s*\(.*\)$", "", clean).strip()
        if not clean or clean.lower() in KEEP:
            continue
        tokens = clean.split()
        if len(tokens) >= 2:
            # Multi-word company — keep whole phrase (still skip if every word is a stopword)
            if not all(t.lower() in STOPWORDS for t in tokens):
                out.add(clean)
        elif is_distinctive_single(clean, dictionary):
            out.add(clean)

    return sorted(out, key=lambda s: (s.lower(), s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the list as JSON instead of writing the file.")
    args = ap.parse_args()

    root = Path(args.repo_root) if args.repo_root else Path.cwd()
    networking = read_file(root / "data" / "networking.md")
    pipeline = read_file(root / "data" / "job-pipeline.md")

    names = parse_networking_names(networking)
    companies = parse_pipeline_companies(pipeline)
    dictionary = load_dictionary()
    tokens = build_denylist(names, companies, dictionary)

    if args.dry_run:
        print(json.dumps({"count": len(tokens), "tokens": tokens}, indent=2, ensure_ascii=False))
        return

    out_path = root / OUTPUT_REL
    header = (
        "# PII denylist — GITIGNORED, do not commit.\n"
        "# Auto-generated by tools/gen_pii_denylist.py from networking.md + job-pipeline.md.\n"
        "# Consumed by tools/check_public_pii.py (deterministic hook). One token/phrase per line.\n"
        "# Regenerate after adding contacts/pipeline rows. Hand-edits are overwritten on regen.\n"
    )
    out_path.write_text(header + "\n".join(tokens) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "written": str(out_path), "count": len(tokens)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
