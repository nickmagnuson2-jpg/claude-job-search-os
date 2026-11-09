#!/usr/bin/env python3
"""Check that the scanners accept every title Nick advertises, and that goals.md is fresh.

Read-only. target-drift-1106 (2026-11-06); spec: the target-drift spec in infra-backlog.
Not a hook (hence not named check_*.py): the nightly career scan calls nightly() and writes
the result into the role queue that /standup reads.

A thesis refresh updated goals.md and LinkedIn but none of the tools that read them: the
scorer kept reading a superseded role list, and the fetch filters cut half the titles Nick
advertises.
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
from tools.career_scanner.scanner import scannable, title_cut_reason  # noqa: E402
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
    except (OSError, UnicodeError, yaml.YAMLError) as e:   # bad bytes too (Codex F2, r2)
        raise Unreadable(f"{path.name}: {e}") from e


def _targets(root: Path) -> tuple:
    """(targets the nightly scan fetches, the reach line saying what was left out and why)."""
    data = _yaml(root / TARGETS)
    companies = data.get("companies") if isinstance(data, dict) else None
    if not isinstance(companies, list):
        raise Unreadable(f"{TARGETS.name}: no companies list")
    bad = [i for i, c in enumerate(companies) if not isinstance(c, dict)]
    if bad:
        raise Unreadable(f"{TARGETS.name}: companies entries {bad} are not a mapping")
    # The same selection the nightly scan makes: scanner.scannable, one function for both.
    targets = [c for c in companies if scannable(c)]
    inactive = [c for c in companies if not c.get("active", True)]
    no_ats = [c for c in companies if c.get("active", True) and not c.get("ats")]
    reach = format_line(f"companies in {TARGETS}", population=len(companies), included=len(targets),
                        excluded={"inactive": len(inactive), "no ats (outreach-only)": len(no_ats)})
    return targets, reach


def _filter_list(target: dict, key: str) -> list:
    """A target's role_filters / role_excludes, which must be a list (Codex F2, review r3)."""
    value = target.get(key) or []
    # r4 (Grok F1): the elements must be strings too; [7] or a dangling "-" crashed the scan.
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise Unreadable(f"{TARGETS.name}: {target.get('name', '?')} {key} is not a list of strings")
    return value


def _weights(spec: dict) -> None:
    """r4 (Grok F2): weights, when present, must be a mapping of numbers. The scorer ignores a
    bad value rather than crash; this makes the bad file visible instead of silently defaulted."""
    w = spec.get("weights")
    if w is None:
        return
    if not isinstance(w, dict) or not all(_is_number(v) for v in w.values()):
        raise Unreadable(f"{FIT_SPEC.name}: weights is not a mapping of numbers")


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _patterns(spec: dict, key: str) -> list:
    value = spec.get(key) or []
    if not isinstance(value, list):   # a scalar would be read character by character (Codex F4)
        raise Unreadable(f"{FIT_SPEC.name}: {key} is not a list")
    return [str(p).lower().strip() for p in value if str(p).strip()]


def check(root: Path, today: date) -> dict:
    """The drift report. Raises Unreadable when an input cannot be read."""
    root = Path(root)
    try:
        goals_text = (root / GOALS).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as e:
        raise Unreadable(f"{GOALS.name}: {e}") from e
    targets, reach = _targets(root)
    spec = _yaml(root / FIT_SPEC)
    if not isinstance(spec, dict):
        raise Unreadable(f"{FIT_SPEC.name}: not a mapping")
    _weights(spec)
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
            reason = title_cut_reason(title, _filter_list(t, "role_filters"), _filter_list(t, "role_excludes"),
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
    """For the nightly scan: never raises. An unreadable input is recorded as drift.

    Any other exception is recorded too: three review rounds each found a new input shape
    that escaped the Unreadable guard and killed the scan before it wrote the queue, so the
    contract is enforced here for every exception, not per shape (review r3).
    """
    try:
        return check(root, today)
    except Unreadable as e:
        return {"drift": True, "problems": [f"unreadable input: {e}"], "titles": [],
                "today": today.isoformat(), "error": str(e)}
    except Exception as e:   # noqa: BLE001 -- the nightly scan must still write its queue
        return {"drift": True, "problems": [f"drift check failed: {type(e).__name__}: {e}"],
                "titles": [], "today": today.isoformat(), "error": f"{type(e).__name__}: {e}"}


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
