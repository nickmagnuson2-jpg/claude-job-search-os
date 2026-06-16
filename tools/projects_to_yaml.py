#!/usr/bin/env python3
"""Generate RenderCV experience YAML stubs from data/projects/*.md.

Purpose: kill the from-memory-writing fabrication vector (lesson #54). The
canonical source for every CV experience entry is the corresponding project
file under data/projects/. This script reads those files, parses the
structured fields (Period, Role, Client, Location, Type, Key Achievements),
and emits a YAML stub that can be substituted into a CV YAML or used as the
starting point for a generated CV.

Output shape:
    sections:
      EXPERIENCE:
        - company: ZUORA
          position: Chief of Staff to Head of Product and Technology
          date: 2025 – Present
          location: Redwood City, CA
          highlights:
            - bullet 1
            - bullet 2

Editorial liberties (per-CV polish) still happen downstream in /generate-cv
and /apply — this script's job is to ensure every fact starts from source,
not from memory. The output is a STUB. Bullets in `Key Achievements` are
emitted verbatim (sans HTML comments and TODO drafts).

Usage:
    python3 tools/projects_to_yaml.py
    python3 tools/projects_to_yaml.py --include zuora,yahoo,mckinsey,espn,ibm
    python3 tools/projects_to_yaml.py --types employment,consulting,flagship
    python3 tools/projects_to_yaml.py --out /tmp/experience.yaml
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "data" / "projects"

DEFAULT_TYPES = {"flagship", "employment", "consulting", "contract", "co-founded"}

FIELD_RE = re.compile(r"^- \*\*(?P<key>[A-Za-z]+):\*\*\s*(?P<value>.*?)\s*$")
SECTION_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
BULLET_RE = re.compile(r"^\s*-\s+(.*)$")


@dataclass
class Project:
    slug: str
    period: str = ""
    role: str = ""
    client: str = ""
    location: str = ""
    type: str = ""
    key_achievements: list[str] = field(default_factory=list)
    raw_path: Path = field(default_factory=Path)

    @property
    def start_year(self) -> int:
        """For sorting most-recent-first."""
        m = re.search(r"(\d{4})", self.period)
        return int(m.group(1)) if m else 0

    @property
    def end_year(self) -> int:
        """For sorting; 'Present' becomes 9999."""
        if "present" in self.period.lower():
            return 9999
        years = re.findall(r"(\d{4})", self.period)
        return int(years[-1]) if years else 0


def parse_project(path: Path) -> Project | None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    proj = Project(slug=path.stem, raw_path=path)
    current_section: str | None = None
    in_key_achievements = False
    in_html_comment = False  # track multi-line <!-- ... --> blocks

    for line in lines:
        # Multi-line HTML comment tracking — must run before any other matching
        if in_html_comment:
            if "-->" in line:
                in_html_comment = False
            continue
        if "<!--" in line and "-->" not in line:
            in_html_comment = True
            continue

        # Section headers
        m = SECTION_RE.match(line)
        if m:
            current_section = m.group("title").strip().lower()
            in_key_achievements = current_section == "key achievements"
            continue

        # Metadata fields (top of file, before first ##)
        fm = FIELD_RE.match(line)
        if fm and current_section is None:
            key = fm.group("key").lower()
            value = fm.group("value").strip()
            if hasattr(proj, key):
                setattr(proj, key, value)
            continue

        # Key Achievements bullets
        if in_key_achievements:
            bm = BULLET_RE.match(line)
            if bm:
                content = bm.group(1).strip()
                # Strip inline (single-line) HTML comments
                content = HTML_COMMENT_RE.sub("", content).strip()
                # Skip empty placeholders
                if content and content != "-":
                    proj.key_achievements.append(content)

    # If the file has no Period/Role/Client, it's not a project entry
    if not (proj.period and proj.role and proj.client):
        return None
    return proj


def normalize_company(client: str) -> str:
    """Uppercase the client name as-is. Editorial cleanup (stripping 'Inc', etc.) happens per-CV downstream."""
    return client.strip().upper()


def normalize_date(period: str) -> str:
    """06/2025 – Present -> 2025 – Present. 02/2024 – 06/2024 -> 2024. 10/2022 – 02/2024 -> 2022 – 2024."""
    period = period.strip()
    # Match start and end with optional Present
    parts = re.split(r"\s*[–-]\s*", period)
    if len(parts) == 1:
        return parts[0].strip()
    start, end = parts[0].strip(), parts[1].strip()
    start_year = re.search(r"(\d{4})", start)
    start_year = start_year.group(1) if start_year else start
    if end.lower() == "present":
        return f"{start_year} – Present"
    end_year_m = re.search(r"(\d{4})", end)
    end_year = end_year_m.group(1) if end_year_m else end
    if start_year == end_year:
        return start_year
    return f"{start_year} – {end_year}"


def normalize_location(location: str) -> str:
    """United States (US) — Redwood City, CA -> Redwood City, CA."""
    # Split on em dash or hyphen with spaces
    if "—" in location:
        return location.split("—", 1)[1].strip()
    if " - " in location:
        return location.split(" - ", 1)[1].strip()
    return location.strip()


def project_to_entry(proj: Project) -> dict:
    entry = {
        "company": normalize_company(proj.client),
        "position": proj.role,
        "date": normalize_date(proj.period),
        "location": normalize_location(proj.location),
        "highlights": list(proj.key_achievements),
    }
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--projects-dir",
        type=Path,
        default=PROJECTS_DIR,
        help="Directory containing data/projects/*.md (default: data/projects/)",
    )
    ap.add_argument(
        "--include",
        type=str,
        default="",
        help="Comma-separated slugs in desired output order. Overrides --types.",
    )
    ap.add_argument(
        "--types",
        type=str,
        default=",".join(sorted(DEFAULT_TYPES)),
        help=f"Comma-separated types to include (default: {','.join(sorted(DEFAULT_TYPES))})",
    )
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit status JSON to stdout instead of YAML.",
    )
    ap.add_argument(
        "--no-wrap",
        action="store_true",
        help="Emit just the EXPERIENCE list (no `sections:` wrapper).",
    )
    args = ap.parse_args(argv)

    if not args.projects_dir.exists():
        print(json.dumps({"status": "error", "message": f"Not found: {args.projects_dir}"}))
        return 1

    projects: list[Project] = []
    skipped: list[str] = []
    for path in sorted(args.projects_dir.glob("*.md")):
        proj = parse_project(path)
        if proj is None:
            skipped.append(path.stem)
            continue
        projects.append(proj)

    if args.include:
        wanted = [s.strip() for s in args.include.split(",") if s.strip()]
        by_slug = {p.slug: p for p in projects}
        missing = [s for s in wanted if s not in by_slug]
        if missing:
            print(json.dumps({"status": "error", "message": f"Unknown slugs: {missing}"}))
            return 1
        ordered = [by_slug[s] for s in wanted]
    else:
        wanted_types = {t.strip() for t in args.types.split(",") if t.strip()}
        ordered = [p for p in projects if p.type in wanted_types]
        ordered.sort(key=lambda p: (p.end_year, p.start_year), reverse=True)

    entries = [project_to_entry(p) for p in ordered]

    if args.no_wrap:
        payload = entries
    else:
        payload = {"sections": {"EXPERIENCE": entries}}

    rendered = yaml.dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,
    )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        if args.json:
            print(json.dumps({
                "status": "ok",
                "out": str(args.out),
                "count": len(entries),
                "slugs": [p.slug for p in ordered],
                "skipped_files": skipped,
            }))
        else:
            print(f"Wrote {args.out} ({len(entries)} entries)")
    else:
        sys.stdout.write(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
