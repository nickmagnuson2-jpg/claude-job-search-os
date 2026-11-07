#!/usr/bin/env python3
"""Check that the scanners accept every title Nick advertises, and that goals.md is fresh.

Read-only. target-drift-1106 (2026-11-06); spec: the target-drift spec in infra-backlog.
Not a hook (hence not named check_*.py): the nightly career scan calls nightly() and writes
the result into the role queue that /standup reads.

The 10/20 thesis refresh updated goals.md, professional-identity.md and LinkedIn together
and none of the tools that read them. For 17 days the scorer read the June role list, and
the fetch filter on 29 targets cut 4 of the 8 titles on Nick's LinkedIn Open to Work.
Nothing compared what Nick advertises with what the tools accept. This does.

For each title on the goals.md Open to Work line it reports:
  - cut_at_fetch: every active, scannable target whose title filters cut it, with the
    reason from scanner.title_cut_reason (the same function the nightly fetch runs);
  - in_lane: whether a fit-spec positive_title_pattern matches it;
  - not_fit: whether a fit-spec not_fit_title_pattern matches it.
Plus goals.md freshness (scorer.goals_freshness, limit scorer.GOALS_FRESH_DAYS).

Exit 0 clean, 1 drift, 2 unreadable input: a check that cannot read must not pass.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/target_drift.py [--repo-root .] [--today YYYY-MM-DD]
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.count_report import format_line  # noqa: E402
from tools.career_scanner.scanner import title_cut_reason  # noqa: E402
from tools.career_scanner.scorer import (  # noqa: E402
    GOALS_FRESH_DAYS, _extract_target_titles, goals_freshness)

GOALS = Path("data/goals.md")
TARGETS = Path("data/scan-targets.yaml")
FIT_SPEC = Path("data/calibration/fit-spec.yaml")


class Unreadable(Exception):
    """An input the check cannot read. The CLI exits 2; nightly() records it as drift."""


def _yaml(path: Path):
    import yaml
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise Unreadable(f"{path.name}: {e}") from e


def _targets(root: Path) -> tuple:
    """(targets the nightly scan fetches, the reach line saying what was left out and why)."""
    data = _yaml(root / TARGETS)
    companies = data.get("companies") if isinstance(data, dict) else None
    if not isinstance(companies, list):
        raise Unreadable(f"{TARGETS.name}: no companies list")
    # The same selection the nightly scan makes (scanner.load_targets).
    entries = [c for c in companies if isinstance(c, dict)]
    inactive = [c for c in entries if not c.get("active", True)]
    no_ats = [c for c in entries if c.get("active", True) and not c.get("ats")]
    targets = [c for c in entries if c.get("active", True) and c.get("ats")]
    reach = format_line(f"companies in {TARGETS}", population=len(companies), included=len(targets),
                        excluded={"inactive": len(inactive), "no ats (outreach-only)": len(no_ats),
                                  "not a mapping": len(companies) - len(entries)})
    return targets, reach


def _patterns(spec: dict, key: str) -> list:
    return [str(p).lower().strip() for p in (spec.get(key) or []) if str(p).strip()]


def check(root: Path, today: date) -> dict:
    """The drift report. Raises Unreadable when an input cannot be read."""
    root = Path(root)
    try:
        goals_text = (root / GOALS).read_text(encoding="utf-8")
    except OSError as e:
        raise Unreadable(f"{GOALS.name}: {e}") from e
    targets, reach = _targets(root)
    spec = _yaml(root / FIT_SPEC)
    if not isinstance(spec, dict):
        raise Unreadable(f"{FIT_SPEC.name}: not a mapping")
    positive = _patterns(spec, "positive_title_patterns")
    not_fit = _patterns(spec, "not_fit_title_patterns")

    fresh = goals_freshness(goals_text, today)
    titles, problems = [], []
    advertised = _extract_target_titles(goals_text)
    if not advertised:
        problems.append("no Open to Work titles in goals.md Target Criteria")
    for title in advertised:
        low = title.lower()
        cuts = []
        for t in targets:
            reason = title_cut_reason(title, t.get("role_filters") or [], t.get("role_excludes") or [],
                                      bool(t.get("allow_engineer_titles", False)))
            if reason:
                cuts.append({"target": t.get("name", "?"), "reason": reason})
        row = {"title": title, "cut_at_fetch": cuts,
               "in_lane": any(p in low for p in positive),
               "not_fit": any(p in low for p in not_fit)}
        titles.append(row)
        if cuts:
            problems.append(f"'{title}' is cut at fetch by {len(cuts)} of {len(targets)} scanned targets")
        if not row["in_lane"]:
            problems.append(f"'{title}' matches no fit-spec positive_title_pattern")
        if row["not_fit"]:
            problems.append(f"'{title}' matches a fit-spec not_fit_title_pattern")
    if fresh["goals_stale"]:
        age = fresh["goals_age_days"]
        problems.append(f"goals.md last reviewed {fresh['goals_last_reviewed'] or 'unknown'} "
                        f"({f'{age} days' if age is not None else 'no valid date'}; "
                        f"limit {GOALS_FRESH_DAYS})")
    return {"drift": bool(problems), "problems": problems, "titles": titles,
            "targets_checked": len(targets), "reach": reach,
            "today": today.isoformat(), **fresh}


def nightly(root: Path, today: date) -> dict:
    """For the nightly scan: never raises. An unreadable input is recorded as drift."""
    try:
        return check(root, today)
    except Unreadable as e:
        return {"drift": True, "problems": [f"unreadable input: {e}"], "titles": [],
                "today": today.isoformat(), "error": str(e)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--today", help="YYYY-MM-DD (default: the current date)")
    args = ap.parse_args(argv)
    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError:
        print(json.dumps({"error": f"bad --today: {args.today}"}))
        return 2
    try:
        report = check(Path(args.repo_root).resolve(), today)
    except Unreadable as e:
        print(json.dumps({"error": str(e)}))
        return 2
    print(json.dumps(report, indent=2))
    return 1 if report["drift"] else 0


if __name__ == "__main__":
    sys.exit(main())
