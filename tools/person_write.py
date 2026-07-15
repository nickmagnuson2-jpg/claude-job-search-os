#!/usr/bin/env python3
"""
person_write.py — Scaffold + atomic dated-append for data/people/<slug>.md
relationship dossiers.

Per-person dossiers are the judgment layer on top of data/networking.md (which
stays the roster + raw interaction log). This script does the two deterministic
operations; freeform narrative sections (Where This Stands, Pressure Points,
Next Move) are edited via re-read + Write, like other dossiers.

Subcommands:
  create <name> [--company CO] [--role ROLE] [--relationship TYPE] [--slug SLUG]
                [--profile PATH]
      Scaffold data/people/<slug>.md from the embedded template. Idempotent:
      if the file already exists it is NOT clobbered (returns action "exists").

  add-entry <name-or-slug> --section <commitments|owed|touchpoints> --text TEXT
                [--date YYYY-MM-DD]
      Atomic newest-first append of a dated bullet under the named section.
      Resolves the person by exact slug, then by fuzzy name/title match.

  list
      Enumerate existing dossiers as JSON [{slug, name, path}].

Options (all subcommands):
  --repo-root PATH   Repository root. Defaults to cwd.
  --dry-run          Return JSON contract without writing.

Output: JSON to stdout
  Success: {"status": "ok", "action": "...", "summary": "..."}
  Failure: {"status": "error", "message": "...", "code": "..."}

Usage (--repo-root/--dry-run are top-level flags — they MUST come BEFORE the
subcommand; argparse rejects them after it with "unrecognized arguments"):
  PYTHONIOENCODING=utf-8 python3 tools/person_write.py --repo-root . create "Priya Anand" \
      --company "Acme AI" --role "Interviewer / Principal" --relationship interviewer
  PYTHONIOENCODING=utf-8 python3 tools/person_write.py --repo-root . add-entry priya-anand \
      --section commitments --text "Will relay feedback via Jordan's process"
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

PEOPLE_DIR = "data/people"

# Section key -> (heading regex, display heading). The script is authoritative
# for the scaffold; framework/templates/person.md is the human-readable mirror.
SECTIONS = {
    "commitments": (r"^##\s+Commitments\b", "Commitments (what they committed to)"),
    "owed":        (r"^##\s+What I Owe\b",  "What I Owe"),
    "touchpoints": (r"^##\s+Touchpoints\b", "Touchpoints"),
}

TEMPLATE = """# {NAME}

> Relationship dossier. Synthesized context for an active relationship: the quotes that matter, what they committed to, what I owe them, the arc, and the next move.
> The roster row and the raw interaction log live in `data/networking.md`. This file is NOT a duplicate of that log; it is the judgment layer on top of it.
> Created {DATE} via `/networking promote` or `tools/person_write.py create`.

## Snapshot

- **Company:** {COMPANY}
- **Role:** {ROLE}
- **Relationship:** {RELATIONSHIP}
- **Slug:** {SLUG}
- **Roster + raw log:** `data/networking.md`
- **Profile / research:** {PROFILE}

## Where This Stands

_Freeform. The relationship arc and current state in two or three sentences. Edit via re-read + Write, not the append script._

(TODO: synthesize)

## What Matters To Them / Pressure Points

_Freeform. What they care about, what moves them, sensitivities, how they like to be engaged._

(TODO: synthesize)

## Commitments (what they committed to)

<!-- newest first; append via: person_write.py add-entry <name> --section commitments --text "..." -->

## What I Owe

<!-- newest first; append via: person_write.py add-entry <name> --section owed --text "..." -->

## Touchpoints

<!-- newest first; pointer to the full log in data/networking.md; append via: person_write.py add-entry <name> --section touchpoints --text "..." -->

## Next Move

_Freeform._

(TODO: synthesize)
"""


# ---------------------------------------------------------------------------
# I/O helpers (self-contained — no cross-script imports)
# ---------------------------------------------------------------------------

def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def out_ok(action: str, summary: str, **extra) -> None:
    d = {"status": "ok", "action": action, "summary": summary}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))


def out_error(message: str, code: str = "error", **extra) -> None:
    d = {"status": "error", "message": message, "code": code}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))
    sys.exit(1)


# ---------------------------------------------------------------------------
# Slug + resolution
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Lowercase, accent-fold, hyphenate. 'Priya Anand González' ->
    'priya-anand-gonzalez' (matches the output/<slug> convention)."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_str = ascii_str.lower()
    ascii_str = re.sub(r"[^a-z0-9]+", "-", ascii_str)
    return ascii_str.strip("-")


def title_of(path: Path) -> str:
    """First H1 in the file, or the slug as a fallback."""
    for line in read_file(path).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def list_dossiers(people_dir: Path) -> list:
    if not people_dir.is_dir():
        return []
    out = []
    for p in sorted(people_dir.glob("*.md")):
        out.append({"slug": p.stem, "name": title_of(p), "path": str(p)})
    return out


def resolve_person(people_dir: Path, ref: str) -> tuple:
    """Resolve a name-or-slug to (path, matches). Exact slug wins; else fuzzy
    substring match on slug or title. Returns (path|None, [candidate dicts])."""
    dossiers = list_dossiers(people_dir)
    ref_slug = slugify(ref)

    exact = [d for d in dossiers if d["slug"] == ref_slug]
    if exact:
        return Path(exact[0]["path"]), exact

    def is_fuzzy(d):
        if ref_slug and (ref_slug in d["slug"] or ref_slug in slugify(d["name"])):
            return True
        return ref.lower() in d["name"].lower()

    fuzzy = [d for d in dossiers if is_fuzzy(d)]
    if len(fuzzy) == 1:
        return Path(fuzzy[0]["path"]), fuzzy
    return None, fuzzy


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_create(args, people_dir: Path, dry_run: bool) -> None:
    slug = args.slug if args.slug else slugify(args.name)
    if not slug:
        out_error(f"Could not derive a slug from name: {args.name!r}", "bad_slug")
    path = people_dir / f"{slug}.md"
    today = date.today().strftime("%Y-%m-%d")

    if path.exists():
        out_ok("exists", f"Dossier already exists: {path} (not clobbered)",
               slug=slug, path=str(path))
        return

    content = (
        TEMPLATE
        .replace("{NAME}", args.name)
        .replace("{DATE}", today)
        .replace("{COMPANY}", args.company or "—")
        .replace("{ROLE}", args.role or "—")
        .replace("{RELATIONSHIP}", args.relationship or "—")
        .replace("{SLUG}", slug)
        .replace("{PROFILE}", args.profile or "—")
    )

    if dry_run:
        out_ok("create", f"Would create: {path}",
               dry_run=True, slug=slug, path=str(path))
        return

    write_atomic(path, content)
    out_ok("create", f"Created dossier: {path}", slug=slug, path=str(path))


def cmd_add_entry(args, people_dir: Path, dry_run: bool) -> None:
    if args.section not in SECTIONS:
        out_error(f"Unknown section: {args.section!r} (use commitments|owed|touchpoints)",
                  "bad_section")
    heading_re, _ = SECTIONS[args.section]

    path, candidates = resolve_person(people_dir, args.name)
    if path is None:
        if not candidates:
            out_error(f"No dossier found for: {args.name!r}. Create it first with "
                      f"`person_write.py create`.", "not_found")
        out_error(f"Ambiguous person ref {args.name!r} — {len(candidates)} matches",
                  "ambiguous_match",
                  matches=[{"slug": c["slug"], "name": c["name"]} for c in candidates])

    today = args.date if args.date else date.today().strftime("%Y-%m-%d")
    bullet = f"- {today} — {args.text}"

    if dry_run:
        out_ok("add-entry", f"Would append to {args.section}: {bullet}",
               dry_run=True, path=str(path), section=args.section)
        return

    content = read_file(path)
    lines = content.splitlines()

    # Find the section heading.
    sec_idx = -1
    for i, line in enumerate(lines):
        if re.match(heading_re, line):
            sec_idx = i
            break
    if sec_idx == -1:
        out_error(f"Section heading for {args.section!r} not found in {path}",
                  "missing_section")

    # Insert newest-first, anchored on the section's HTML comment marker so the
    # bullet lands directly beneath it (above older bullets) and the blank line
    # separating the section from the next heading is preserved.
    sec_end = len(lines)
    for j in range(sec_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            sec_end = j
            break

    insert_at = sec_idx + 1
    for j in range(sec_idx + 1, sec_end):
        if lines[j].lstrip().startswith("<!--"):
            insert_at = j + 1
            break
    else:
        # No comment marker — insert right after the heading.
        insert_at = sec_idx + 1

    lines.insert(insert_at, bullet)
    new_content = "\n".join(lines)
    if content.endswith("\n"):
        new_content += "\n"
    write_atomic(path, new_content)

    out_ok("add-entry", f"Appended to {args.section}: {bullet}",
           path=str(path), section=args.section, entry=bullet)


def cmd_list(args, people_dir: Path, dry_run: bool) -> None:
    dossiers = list_dossiers(people_dir)
    out_ok("list", f"{len(dossiers)} dossier(s)", dossiers=dossiers)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    # --repo-root and --dry-run go BEFORE the subcommand (matches the
    # pipe_write.py / networking_write.py convention documented in CLAUDE.md).
    p = argparse.ArgumentParser(description="Scaffold + atomic append for data/people/ dossiers.")
    p.add_argument("--repo-root", default=None, help="Repository root. Defaults to cwd.")
    p.add_argument("--dry-run", action="store_true", help="Return JSON without writing.")

    sub = p.add_subparsers(dest="command")

    c = sub.add_parser("create")
    c.add_argument("name")
    c.add_argument("--company", default=None)
    c.add_argument("--role", default=None)
    c.add_argument("--relationship", default=None)
    c.add_argument("--slug", default=None)
    c.add_argument("--profile", default=None)

    a = sub.add_parser("add-entry")
    a.add_argument("name")
    a.add_argument("--section", required=True)
    a.add_argument("--text", required=True)
    a.add_argument("--date", default=None)

    sub.add_parser("list")

    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.command:
        out_error("Usage: person_write.py <create|add-entry|list> [args...]")

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    people_dir = repo_root / PEOPLE_DIR

    if args.command == "create":
        cmd_create(args, people_dir, args.dry_run)
    elif args.command == "add-entry":
        cmd_add_entry(args, people_dir, args.dry_run)
    elif args.command == "list":
        cmd_list(args, people_dir, args.dry_run)
    else:
        out_error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
