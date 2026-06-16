#!/usr/bin/env python3
"""
dossier_freshness.py — Pre-process dossier staleness for /weekly-review and /act.

Reads: output/**/*.md (dossiers only — files where filename matches parent directory name)

Outputs JSON to stdout with keys:
  recent_dossiers{}             — {this_week: [], older_than_30_days: []}
  summary{}                     — total_dossiers, updated_this_week, stale_count
  staleness_alerts[]            — dossiers >30 days old with last_updated and days_old

Usage:
  python3 tools/dossier_freshness.py [--lookback-days N] [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--lookback-days", type=int, default=7,
                   help="Days to consider 'recent' (default: 7).")
    p.add_argument("--target-date", default=None,
                   help="Date to use as 'today' (YYYY-MM-DD). Defaults to actual today.")
    p.add_argument("--repo-root", default=None,
                   help="Repository root path. Defaults to cwd.")
    p.add_argument("--all-stale", action="store_true",
                   help="Alert on ALL stale dossiers, including non-target ones "
                        "(legacy behavior). Default: only alert on dossiers whose "
                        "company is in the active pipeline.")
    return p.parse_args()


# Pipeline stages that mean the lead is dead — a stale dossier for these
# shouldn't generate a freshness nudge.
TERMINAL_STAGE_KEYWORDS = (
    "withdrawn", "closed", "rejected", "deprioritized", "skipped",
    "dead", "passed", "declined", "lost",
)


def load_pipeline_targets(repo_root: Path) -> set:
    """Return the set of pipeline company slugs at a non-terminal (active) stage.

    A dossier is a freshness 'target' only if its company is being actively
    pursued. Terminal-stage leads (Withdrawn/Closed/Rejected/...) and dead-lane
    dossiers (abandoned food/climate/early-BH phases never pipelined) stop
    nagging. Matched by EXACT company-slug equality — no substring fallback,
    which previously mis-matched 'exa' -> 'exact' and notes mentions.
    Origin: 2026-06-02 — daily nudge flagged 54 stale dossiers, ~all dead-lane.
    """
    pipeline = repo_root / "data" / "job-pipeline.md"
    if not pipeline.exists():
        return set()
    try:
        lines = pipeline.read_text(encoding="utf-8").splitlines()
    except (IOError, UnicodeDecodeError):
        return set()
    slugs = set()
    for line in lines:
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        company, stage = cells[0], cells[2].lower()
        if (not company or company.lower() == "company"
                or set(company) <= set("-: ")):
            continue
        if any(k in stage for k in TERMINAL_STAGE_KEYWORDS):
            continue  # dead lead — not a target
        slugs.add(re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-"))
    return slugs


def is_target_dossier(slug: str, pipe_slugs: set) -> bool:
    """Target iff the dossier's company is in the pipeline at an active stage."""
    return slug in pipe_slugs


def read_header(path: Path, lines: int = 15) -> str:
    """Read only the first N lines of a file."""
    try:
        result = []
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= lines:
                    break
                result.append(line)
        return "".join(result)
    except (IOError, UnicodeDecodeError):
        return ""


def extract_last_updated(header: str) -> date | None:
    """Extract 'Last updated: YYYY-MM-DD' from file header."""
    m = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", header)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def main():
    args = parse_args()

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    output_dir = repo_root / "output"

    if not output_dir.exists():
        print(json.dumps({
            "recent_dossiers": {"this_week": [], "older_than_30_days": []},
            "summary": {"total_dossiers": 0, "updated_this_week": 0, "stale_count": 0},
            "staleness_alerts": [],
            "target_date": today.strftime("%Y-%m-%d"),
        }))
        return

    lookback_cutoff = today - timedelta(days=args.lookback_days)
    stale_cutoff = today - timedelta(days=30)

    pipe_slugs = load_pipeline_targets(repo_root)

    this_week = []
    older_than_30 = []
    staleness_alerts = []
    suppressed_non_target = 0

    for md_file in sorted(output_dir.rglob("*.md")):
        # Dossier detection: file stem must match parent directory name
        if md_file.stem != md_file.parent.name:
            continue

        header = read_header(md_file)
        last_updated = extract_last_updated(header)
        rel_path = str(md_file.relative_to(repo_root))
        slug = md_file.stem
        target = is_target_dossier(slug, pipe_slugs)

        entry = {
            "slug": slug,
            "path": rel_path,
            "last_updated": last_updated.strftime("%Y-%m-%d") if last_updated else None,
            "days_old": (today - last_updated).days if last_updated else None,
            "is_target": target,
        }

        is_stale = (last_updated is None) or (last_updated <= stale_cutoff)

        if last_updated and last_updated >= lookback_cutoff:
            this_week.append(entry)
        if is_stale:
            older_than_30.append(entry)
            # Only alert on stale dossiers for companies still in the pipeline,
            # unless --all-stale restores legacy behavior. Non-target stale
            # dossiers (abandoned lanes) stop generating daily nudges.
            if target or args.all_stale:
                staleness_alerts.append({
                    **entry,
                    "refresh_command": f'/research-company "{slug.replace("-", " ").title()}"',
                })
            else:
                suppressed_non_target += 1

    # Sort staleness alerts by most stale first
    staleness_alerts.sort(key=lambda e: -(e["days_old"] or 999))

    result = {
        "recent_dossiers": {
            "this_week": this_week,
            "older_than_30_days": older_than_30,
        },
        "summary": {
            "total_dossiers": len(this_week) + len(older_than_30),
            "updated_this_week": len(this_week),
            "stale_count": len(older_than_30),
            "stale_target_count": len(staleness_alerts) if not args.all_stale
                else sum(1 for e in older_than_30 if e["is_target"]),
            "suppressed_non_target": suppressed_non_target,
        },
        "staleness_alerts": staleness_alerts,
        "target_date": today.strftime("%Y-%m-%d"),
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
