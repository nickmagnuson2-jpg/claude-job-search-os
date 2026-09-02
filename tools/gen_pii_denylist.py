#!/usr/bin/env python3
"""
gen_pii_denylist.py — Generate the gitignored PII denylist used by check_public_pii.py.

Reads the canonical real-entity sources (data/networking.md contacts + interaction
log, data/job-pipeline.md company column, data/scan-targets.yaml, plus the
hand-maintained tools/.pii-manual-additions.txt for real entities that live only in
data/projects/, source articles, or the personal vault) and emits distinctive tokens that must
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
import unicodedata
from pathlib import Path

OUTPUT_REL = "tools/.pii-denylist.txt"
AMBIGUOUS_REL = "tools/.pii-denylist-ambiguous.txt"
RETIRED_REL = "tools/.pii-denylist-retired.txt"
MANUAL_REL = "tools/.pii-manual-additions.txt"

MANUAL_TEMPLATE = """# Hand-maintained PII additions — GITIGNORED, do not commit.
#
# WHY THIS FILE EXISTS: the generator's automatic sources are data/networking.md,
# data/job-pipeline.md and data/scan-targets.yaml. A real person or company that
# exists ONLY in data/projects/, a source article, a reflection, or the personal
# vault is invisible to all three, so the deterministic hook cannot protect them.
# Routing them through networking.md just for coverage would pollute the job-search
# roster with people who are not job-search contacts.
#
# FORMAT: one name or company per line. Blank lines and # comments ignored.
#   [block]      (default) exact-phrase match, BLOCKS the write.
#   [ambiguous]  WARN-only tier, for a single word that is also ordinary English.
#
# Entries here are MERGED on every regen; this file is never overwritten.
#
# SECTION ORDER IS DELIBERATE: [block] is LAST so appending a line to the end of the
# file -- the obvious thing to do -- lands it in the STRICTER tier. An earlier draft
# ended with [ambiguous], so a naive append silently became WARN-only, and a
# PreToolUse WARN is never surfaced by Claude Code. The entry would have looked added
# and protected nothing.

[ambiguous]

[block]
"""

# Public-safe entities — real employers, schools, public-figure authors, generic
# products. These appear legitimately in public skill/framework docs. Lowercased.
KEEP = {
    "zuora", "mckinsey", "tuck", "duke", "yahoo", "espn", "yahoo sports",
    "notion", "stripe", "anthropic", "google", "github", "openai", "linkedin",
    "ali rohde", "ryan holiday", "molly graham", "raphael", "daily stoic",
    "claude", "granola", "wispr", "obsidian", "vercel", "neon",
}

# The repo's own FICTIONAL CAST, used across public skill docs, examples/ and test
# fixtures. Declared fictional in examples/README.md and relied on by person_components'
# first-name rule above.
#
# These must never reach the denylist, and the path by which they can is not hypothetical:
# on 2026-09-02 `data/networking.md` was found to contain a demo interaction logged against
# a cast persona. The harvester cannot tell a demo row from a real contact -- both are
# `### Name — Company` -- so fixing the status-prefix bug promoted a fictional surname to
# BLOCK and the always-on hook immediately blocked three legitimate public files, including
# the examples README that DEFINES the persona.
#
# Excluded by NAME here rather than by trying to detect demo rows: the cast is a short,
# known, declared list, and a heuristic that guesses which log entries are real would fail
# in the dangerous direction.
FICTIONAL_CAST = {
    "priya anand", "jordan lee", "sarah chen", "casey doe", "casey morgan",
    "sam carter", "robin", "alex chen",
}
FICTIONAL_SURNAMES = {n.split()[-1] for n in FICTIONAL_CAST if " " in n}

# Generic glue / industry-suffix words. A single-token company name that is just one
# of these (or, via the system dictionary, any ordinary English word) is excluded from
# the deterministic denylist and left to the semantic subagent audit. The dictionary is
# the primary filter for word-like company names; this set only covers generic terms a
# dictionary might miss. Deliberately holds NO real company names (that would itself be PII).
STOPWORDS = {
    "the", "a", "an", "of", "and", "co", "inc", "ai", "labs", "health",
    "care", "agents", "robotics", "ventures", "capital", "partners", "group",
}
# REMOVED 2026-08-19: one entry here was a REAL pipeline company (present in both
# job-pipeline.md and networking.md). Suppressing it kept it out of BOTH the block
# denylist and the ambiguous tier, so the always-on hook would not have stopped that
# company's name reaching a public artifact. It is not in the system dictionary, so with
# the suppression gone it qualifies as distinctive and lands on the BLOCK tier.
#
# Found by the /audit-pii semantic pass. The deterministic layer could not find it BY
# CONSTRUCTION: the hole was in the deterministic layer itself.
#
# The token is deliberately NOT named here. The first draft of this very comment spelled
# it out, which put the company into this public file and tripped the hook the fix had
# just repaired -- documenting a leak must not re-commit it. The live check is the
# data-driven test in tests/scripts/test_gen_pii_stopwords_guard.py, not a name here.


SYSTEM_DICT = Path("/usr/share/dict/words")


def slugify(name: str) -> str:
    """Lowercase, accent-fold, hyphenate — matches the output/<slug> convention used
    throughout this repo (tools/person_write.py:slugify). E.g. 'Casey Doe' ->
    'casey-doe', 'Acme Ventures' -> 'acme-ventures'. Path-embedded slug forms
    (folder names, filenames) are a distinct denylist-evasion surface from the
    space-separated name-forms above them, per feedback_denylist_needs_slug_forms_not_just_names."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_str = ascii_str.lower()
    ascii_str = re.sub(r"[^a-z0-9]+", "-", ascii_str)
    return ascii_str.strip("-")


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
            names.add(_strip_status_prefix(m.group(1).strip()))
    return names


def _strip_status_prefix(value: str) -> str:
    """Drop a leading `[ARCHIVED]`-style status tag from a header.

    Without this the captured name is `[ARCHIVED] First Last`, which matches nothing, so
    the real name reaches no tier at all. Seven headers carried one on 2026-09-02 and the
    contact in the first of them was on no denylist tier.
    """
    return re.sub(r"^\[[^\]]{1,24}\]\s*", "", value).strip()


def parse_networking_companies(content: str) -> set[str]:
    """Company names from the RIGHT half of a `### Name — Company` header.

    `parse_networking_names` keeps only group(1) and discards this half by design, so a
    company that appears only in an interaction-log header reached NO tier. On 2026-09-02
    that was 179 headers, and one of those companies was sitting in a tracked public file
    as a test fixture while the deterministic scan reported the tree clean.

    These route to the AMBIGUOUS/WARN tier, not BLOCK: they are parsed out of prose-shaped
    headers rather than a structured column, so the extraction is less trustworthy than
    `parse_pipeline_companies`, and a false BLOCK on an always-on PreToolUse hook stops
    real work. /audit-pii Step 1 is the reader that makes this tier visible.
    """
    out: set[str] = set()
    for line in content.splitlines():
        m = re.match(r"^###\s+.+?\s+[—–|]\s+(.+?)\s*$", line)
        if not m:
            continue
        company = _strip_status_prefix(m.group(1))
        # Trailing parentheticals and trailing punctuation are annotation, not the name.
        company = re.sub(r"\s*\([^)]*\)\s*$", "", company).strip(" .,;:-")
        if len(company) >= 3:
            out.add(company)
    return out


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


# Closed-compound sector words the system dictionary omits. They are not brand names and
# must never BLOCK on their own: "<Something> Healthcare" is a naming convention, not an
# identifier. Kept as an explicit list because the general rule that would derive it
# (split into two dictionary words) also dissolves real brands - see _is_ordinary_word.
INDUSTRY_WORDS = {
    "healthcare", "healthtech", "fintech", "biotech", "edtech", "insurtech", "adtech",
    "medtech", "proptech", "agtech", "cleantech", "deeptech", "martech", "regtech",
    "hardware", "software", "wellness", "marketplace", "ecommerce", "workflow",
}


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
    return not _is_ordinary_word(t, dictionary)


def _is_ordinary_word(t: str, dictionary: set[str]) -> bool:
    """Is `t` ordinary English, allowing for what the system dictionary leaves out?

    THE DICTIONARY IS NOT A WORD LIST. /usr/share/dict/words holds singulars and omits
    closed compounds, so a bare `t in dictionary` test called ordinary plurals and sector
    compounds distinctive brand tokens. Measured 2026-09-01 across all 522 tracked
    public files: 7 BLOCK hits, 6 of them this exact shape, firing on the repo's own
    documentation. A gate that cries wolf on its own docs is a gate that gets bypassed.

    Plurals get a general rule: strip a trailing "s"/"es" and re-test, requiring the stem
    to be >= 4 characters so "Labs" does not become "Lab" by accident.

    CLOSED COMPOUNDS DO NOT. A split-into-two-dictionary-words test was written and
    REJECTED the same day: "Northwind" is "north" + "wind", both ordinary words, and the
    rule dissolved the repo's own placeholder brand. Nothing structural separates a
    compound brand from a compound common noun, so the compounds that actually appear here
    are named explicitly in INDUSTRY_WORDS. A short honest list beats a general rule that
    silently deletes coverage.
    """
    if t in dictionary or t in INDUSTRY_WORDS:
        return True
    # Stem floor of 3, not 4: a 4-letter plural of a 3-letter word (the "<X>s" shape) is
    # the common case and a floor of 4 silently skipped every one of them.
    for suffix in ("es", "s"):
        if t.endswith(suffix) and len(t) - len(suffix) >= 3:
            if t[: -len(suffix)] in dictionary or t[: -len(suffix)] in INDUSTRY_WORDS:
                return True
    return False


# Words that carry no identifying weight on their own. A company is routinely "<Name> AI"
# or "<Name> Labs", and promoting the suffix alone would fire on most files in this repo -
# which is how a gate stops being trusted and starts being bypassed.
CORPORATE_SUFFIXES = {
    "ai", "inc", "llc", "ltd", "co", "corp", "corporation", "company", "labs", "lab",
    "group", "technologies", "technology", "tech", "systems", "software", "solutions",
    "services", "holdings", "partners", "ventures", "capital", "global", "digital",
    "media", "networks", "studio", "studios", "works", "collective", "io", "com",
}


def company_components(company: str, dictionary: set[str]) -> tuple[list[str], list[str]]:
    """Split a multi-token company into (block_worthy, warn_worthy) component tokens.

    THE GAP THIS CLOSES (2026-09-01). A multi-token company was emitted only as the whole
    phrase plus its slug. `find_pii` matches a phrase literally, so a public file naming
    ONE word of a two-word company was not a hit -- which is exactly how a real pipeline
    company reached a public framework file with the always-on hook enabled. 171 of 470
    entries were multi-token, so the exposure was general, not a one-off.

    The tier split is the whole design. A distinctive component (not in the dictionary,
    not a corporate suffix) is safe to BLOCK. An ordinary-English component goes to the
    WARN tier instead: blocking it would fire on ordinary prose, and dropping it is what
    made the leak invisible in the first place.
    """
    block, warn = [], []
    for tok in re.split(r"[^A-Za-z0-9]+", company):
        if len(tok) < 4 or tok.lower() in CORPORATE_SUFFIXES or tok.lower() in KEEP:
            continue
        (block if is_distinctive_single(tok, dictionary) else warn).append(tok)
    return block, warn


def person_components(name: str, dictionary: set[str]) -> list[str]:
    """The BLOCK-worthy component tokens of a person's name: the SURNAME only.

    Never the first name. This repo's public docs run on a fictional cast -- Sarah Chen,
    Jordan Lee, Priya Anand -- and a real contact who shares a first name with any of them
    would turn every placeholder into a block. The surname carries the identifying weight
    anyway, and a surname that is also an ordinary word (Brown, Field, Baker) is filtered
    by the same distinctiveness test the rest of this module uses.
    """
    if name.strip().lower() in FICTIONAL_CAST:
        return []
    toks = [x for x in re.split(r"[^A-Za-zÀ-ÿ'\-]+", name) if x]
    if len(toks) < 2:
        return []
    if toks[-1].lower() in FICTIONAL_SURNAMES:
        return []
    # No length or KEEP guard here on purpose: is_distinctive_single already rejects both
    # (< 4 chars, and anything in KEEP). Restating them produced a branch that could be
    # inverted with no observable effect - a mutant nothing could kill, which is a guard
    # that is not doing work.
    return [toks[-1]] if is_distinctive_single(toks[-1], dictionary) else []


def build_denylist(names: set[str], companies: set[str], dictionary: set[str]) -> list[str]:
    out: set[str] = set()

    for name in names:
        clean = name.strip().strip("*").strip()
        # Drop trailing parentheticals / qualifiers
        clean = re.sub(r"\s*\(.*\)$", "", clean).strip()
        if not clean or clean.lower() in KEEP or clean.lower() in FICTIONAL_CAST:
            continue
        tokens = clean.split()
        # Only keep multi-token full names — distinctive, low false-positive risk.
        if len(tokens) >= 2 and all(re.match(r"^[A-Za-zÀ-ÿ.'\-]+$", t) for t in tokens):
            out.add(clean)
            # Slug-form variant (path-embedded, e.g. output/<slug>/, data/people/<slug>.md).
            slug = slugify(clean)
            if slug and slug not in KEEP:
                out.add(slug)
            # Surname on its own: a public file citing "<Surname>'s actual title" is the
            # same leak as one citing the full name, and the phrase matcher misses it.
            out.update(person_components(clean, dictionary))

    for company in companies:
        clean = company.strip().strip("*").strip()
        clean = re.sub(r"\s*\(.*\)$", "", clean).strip()
        if not clean or clean.lower() in KEEP or clean.lower() in FICTIONAL_CAST:
            continue
        tokens = clean.split()
        if len(tokens) >= 2:
            # Multi-word company — keep whole phrase (still skip if every word is a stopword)
            if not all(t.lower() in STOPWORDS for t in tokens):
                out.add(clean)
                slug = slugify(clean)
                if slug and slug not in KEEP:
                    out.add(slug)
                # Distinctive components ADD to the phrase, never replace it: dropping
                # the phrase would stop catching files that spell the name in full.
                block_parts, _ = company_components(clean, dictionary)
                out.update(block_parts)
        elif is_distinctive_single(clean, dictionary):
            out.add(clean)
            slug = slugify(clean)
            if slug and slug not in KEEP:
                out.add(slug)

    return sorted(out, key=lambda s: (s.lower(), s))


def build_ambiguous_list(companies: set[str], dictionary: set[str]) -> list[str]:
    """Single-token company names that ARE ordinary English words.

    These are deliberately excluded from the BLOCK denylist: matching them would
    false-positive on ordinary prose (a "summit" in a sentence, a "patch" in a code
    comment). But excluding them entirely is what let a live pipeline company reach
    six public files on 2026-08-10 while all three of its interviewers were correctly
    denylisted.

    So they get a second tier: WARN, not BLOCK.

    CITATION CORRECTED 2026-08-14 -- this docstring previously read "Per
    feedback_warn_vs_block_hook_design -- reserve BLOCK for unambiguous violations,
    WARN for judgment calls. The human decides; the hook only refuses to stay silent."
    That is the rule's SUPERSEDED form, and the last clause is false. The rule's
    2026-05-28 correction states that a PreToolUse WARN (exit 0 + stderr) is NOT
    surfaced by Claude Code at all -- see tools/HOOK_AUTHORING.md L77. So on the
    PreToolUse write path this tier detects and informs nobody: a real pipeline
    company reached two public files on 2026-08-14 with the hook warning correctly
    into a void, caught by hand at staging rather than by the guard.

    Why the paraphrase was wrong: the memory file had been archived and deleted while
    13 tools/*.py still cited it, so the correction was unreachable at build time.
    Restored 2026-08-14 as memory/feedback_warn_vs_block_hook_design.md.

    WHERE THIS TIER IS ACTUALLY READ: /audit-pii Step 1, which surfaces
    ambiguous_hits[] to a human. That is the tier's only functioning consumer.
    Whether it should ALSO block on the public-artifact write path is an open
    decision (see feedback_warn_tier_is_invisible_so_it_is_not_a_tier); behavior is
    deliberately unchanged here.
    """
    out = set()
    for company in companies:
        clean = company.strip().strip("*").strip()
        if not clean:
            continue
        if " " in clean:
            # The PHRASE is distinctive enough for BLOCK and is emitted there. Its
            # ordinary-English COMPONENTS are not, and used to be emitted nowhere at all
            # -- the 2026-09-01 gap. They land here so /audit-pii still sees them, rather
            # than being blocked (which would fire on ordinary prose) or dropped (which
            # is what made the leak invisible).
            _, warn_parts = company_components(clean, dictionary)
            out.update(w for w in warn_parts
                       if w.lower() not in STOPWORDS and w.lower() not in KEEP)
            continue
        if is_distinctive_single(clean, dictionary):
            continue  # already in the BLOCK tier
        if clean.lower() in STOPWORDS or clean.lower() in KEEP:
            continue
        out.add(clean)
    return sorted(out)


def merge_retired(tokens: list[str], retired_path: Path) -> list[str]:
    """Union `tokens` with every token this generator has emitted before.

    THE GAP THIS CLOSES (2026-09-01). The denylist is rebuilt from CURRENT data/ on every
    run, so the moment a company leaves scan-targets.yaml it loses its protection -- while
    its name stays in whatever public files were written while it WAS a target. Measured
    that day: a company retargeted away a week earlier sat in two files on the public
    remote, on neither tier, because the generator had already forgotten it.

    Coverage has to outlive the pursuit. The name in the public file does not disappear
    when the row does.

    Hand-editable ON PURPOSE. Sticky accumulation with no way out means one bad token
    poisons the gate forever, so the file is a plain sorted list: delete a line and it
    stays deleted. A missing file is a first run, not an error -- raising here would stop
    the denylist being built at all, which fails the gate open.
    """
    keep = set(tokens)
    try:
        for line in Path(retired_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                keep.add(line)
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        pass
    return sorted(keep, key=lambda s: (s.lower(), s))


def record_retired(tokens: list[str], retired_path: Path) -> None:
    """Append this run's tokens to the retired file, deduplicated and sorted.

    Written on the way OUT so the next run inherits them. Sorted so the diff of a
    gitignored file a human is expected to prune by hand stays readable.
    """
    merged = merge_retired(list(tokens), retired_path)
    header = (
        "# Tokens the PII denylist generator has emitted at any point.\n"
        "# Merged into every rebuild so a company that leaves data/ keeps its coverage:\n"
        "# the row goes away, the name in the already-written public file does not.\n"
        "# Hand-prunable -- delete a line and it stays deleted. Gitignored.\n"
    )
    Path(retired_path).parent.mkdir(parents=True, exist_ok=True)
    Path(retired_path).write_text(header + "\n".join(merged) + "\n", encoding="utf-8")


def parse_scan_target_companies(path: Path) -> set[str]:
    """Company names from data/scan-targets.yaml, both companies: and rejected:.

    These are real companies Nick is targeting or has reviewed, so they belong in
    the denylist. Rejected ones count too: he still looked at them, and a rejected
    name is just as likely to be reached for as a convenient test fixture.

    Origin 2026-08-12: a real Lane B target was used as a fixture name in a public
    test file and the deterministic hook did not block it, because this generator
    read only networking.md and job-pipeline.md. The whole target pool was invisible
    to the guard.

    Missing or malformed file degrades to an empty set rather than failing: the
    denylist must still generate from the other sources.
    """
    if not path.is_file():
        return set()
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return set()
    out: set[str] = set()
    for key in ("companies", "rejected"):
        for entry in (data.get(key) or []):
            if isinstance(entry, dict) and entry.get("name"):
                out.add(str(entry["name"]).strip())
    return out


def load_manual_additions(path: Path) -> tuple[set[str], set[str]]:
    """Read the hand-maintained additions file -> (block_tokens, ambiguous_tokens).

    A missing file is NOT an error: it is created from a template on first run so the
    affordance is discoverable, and an empty template yields two empty sets.
    """
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(MANUAL_TEMPLATE, encoding="utf-8")
        except OSError:
            pass  # read-only checkout: the file is optional, never fatal
        return set(), set()

    block: set[str] = set()
    ambiguous: set[str] = set()
    section = block
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set(), set()

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low == "[block]":
            section = block
            continue
        if low == "[ambiguous]":
            section = ambiguous
            continue
        section.add(line)
    return block, ambiguous


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
    companies |= parse_scan_target_companies(root / "data" / "scan-targets.yaml")
    dictionary = load_dictionary()
    tokens = build_denylist(names, companies, dictionary)
    ambiguous = build_ambiguous_list(companies, dictionary)
    # WARN tier, per the 2026-09-02 decision: harvested from prose-shaped headers, so less
    # trustworthy than the pipeline column, and a false BLOCK on an always-on hook is worse
    # than a missed WARN that /audit-pii Step 1 surfaces to a human anyway.
    ambiguous = sorted(set(ambiguous) | parse_networking_companies(networking))

    # Hand-maintained additions merge LAST and bypass the distinctiveness filters on
    # purpose: a human put them there deliberately, so the generator must not
    # second-guess them the way it does an auto-parsed token.
    manual_block, manual_ambiguous = load_manual_additions(root / MANUAL_REL)
    manual_added = sorted(t for t in manual_block if t not in set(tokens))
    tokens = sorted(set(tokens) | manual_block)
    # Coverage outlives the pursuit: fold in every token ever emitted, then bank this
    # run's own so the next rebuild inherits them.
    retired_path = root / RETIRED_REL
    tokens = merge_retired(tokens, retired_path)
    ambiguous = sorted((set(ambiguous) | manual_ambiguous) - manual_block)

    if args.dry_run:
        print(json.dumps({"count": len(tokens), "tokens": tokens,
                          "ambiguous_count": len(ambiguous), "ambiguous": ambiguous,
                          "manual_added": manual_added,
                          "manual_source": str(root / MANUAL_REL)},
                         indent=2, ensure_ascii=False))
        return

    out_path = root / OUTPUT_REL
    header = (
        "# PII denylist — GITIGNORED, do not commit.\n"
        "# Auto-generated by tools/gen_pii_denylist.py from networking.md + job-pipeline.md + scan-targets.yaml.\n"
        "# Consumed by tools/check_public_pii.py (deterministic hook). One token/phrase per line.\n"
        "# Regenerate after adding contacts/pipeline/scan-target rows. Hand-edits are overwritten on regen.\n"
    )
    out_path.write_text(header + "\n".join(tokens) + "\n", encoding="utf-8")

    # Bank this run's tokens AFTER the denylist is safely on disk. Doing it earlier would
    # let a crash between the two leave a retired file naming tokens no denylist carries.
    record_retired(tokens, retired_path)

    # Second tier: single-word company names that are ordinary English words.
    # WARN-only. Excluding them entirely is what let a live pipeline company reach six
    # public files on 2026-08-10 while all three of its interviewers were denylisted.
    amb_path = root / AMBIGUOUS_REL
    amb_header = (
        "# AMBIGUOUS PII tier — GITIGNORED, do not commit.\n"
        "# Single-token company names that are also ordinary English words, so matching\n"
        "# them would false-positive on prose. check_public_pii.py WARNs on these (exit 0),\n"
        "# it never BLOCKs.\n"
        "# NOTE: a PreToolUse exit-0 WARN is NOT surfaced by Claude Code (HOOK_AUTHORING.md\n"
        "# L77), so on the write path this tier reaches nobody. Its only functioning reader\n"
        "# is /audit-pii Step 1, which reports ambiguous_hits[] to a human. Treat a non-empty\n"
        "# ambiguous_hits[] there as requiring an explicit verdict, not as advisory noise.\n"
        "# See memory/feedback_warn_vs_block_hook_design.md (corrected 2026-05-28).\n"
    )
    amb_path.write_text(amb_header + "\n".join(ambiguous) + "\n", encoding="utf-8")

    print(json.dumps({"status": "ok", "written": str(out_path), "count": len(tokens),
                      "ambiguous_written": str(amb_path), "ambiguous_count": len(ambiguous),
                      "retired_written": str(retired_path),
                      "manual_added_count": len(manual_added),
                      "manual_added": manual_added},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
