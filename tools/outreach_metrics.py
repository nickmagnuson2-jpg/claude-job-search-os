#!/usr/bin/env python3
"""
outreach_metrics.py — Analytics projection of the outreach log, for the console dashboard.

Reads: data/outreach-log.md, data/networking.md, data/job-pipeline.md

DISTINCT FROM outreach_pending.py, which answers "what is awaiting a reply right now"
for /standup. This answers "what has my outreach actually done over time" and emits the
per-dimension aggregates a dashboard groups by. Reply detection is NOT reimplemented
here: parse_networking_interactions + reconcile_key are imported from outreach_pending
so a reply logged in networking.md but never reflected in outreach-log's status column
counts the same way in both tools. Company name matching is imported from stage_vocab's
sibling normaliser below.

The status column decays (a send is logged, the reply often is not), which is why the
networking cross-reference is load-bearing rather than a nicety: it moved the measured
response rate from 34% to 58% on the live file.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/outreach_metrics.py [--repo-root PATH]
                                 [--target-date YYYY-MM-DD] [--out FILE]
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outreach_pending import (  # noqa: E402
    parse_networking_interactions,
    reconcile_key,
    read_file,
)
from pipe_read import parse_all_rows  # noqa: E402

COLD_SKILLS = {"cold-outreach"}
FOLLOWUP_SKILLS = {"follow-up"}


def norm_company(s: str) -> str:
    """Normalise a company name for cross-file joining. Deliberately conservative:
    strips only legal suffixes and punctuation, never distinguishing words."""
    s = (s or "").lower().strip()
    s = re.sub(r"\b(inc|llc|ltd|corp|co)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def parse_rows(content: str) -> list[dict]:
    """Every outreach-log row as a record. Header/separator/short rows are skipped."""
    rows = []
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 7:
            continue
        if cols[0] in ("Date", "---") or set(cols[0]) <= set("- "):
            continue
        rows.append({
            "date": cols[0], "skill": cols[1], "channel": cols[2],
            "recipient": cols[3], "company": cols[4],
            "subject": cols[5], "status": cols[6],
        })
    return rows


def replied(row: dict, interactions: dict) -> bool:
    """A touch counts as replied if its own status says so, OR networking.md logged an
    interaction with that person+company DATED AFTER this touch.

    The date comparison is load-bearing, not a refinement. Without it, one reply from a
    person contacted eight times scores as eight replies, and the response rate reads
    90% instead of 58% on the live file. outreach_pending.py has required
    `latest_interaction > sent_date` since a sibling bug shipped a 93% rate that should
    have been 48%; this is the same guard, and the two tools must agree."""
    status = row["status"].lower()
    if status.startswith("replied") or status == "reply":
        return True
    # Only an UNRESOLVED status is upgraded by the cross-reference. An explicit
    # "No reply" is a judgement already made and is never overridden — this matches
    # outreach_pending.py exactly, and test_outreach_tool_parity.py fails if it stops
    # matching. Dropping this condition moves the live rate from 58% to 64%.
    if status not in ("sent", "drafted", "pending"):
        return False
    try:
        sent = datetime.strptime(row["date"], "%Y-%m-%d").date()
    except ValueError:
        return False
    latest = interactions.get(reconcile_key(row["recipient"], row["company"]))
    return latest is not None and latest > sent


def sanity_check(n_replied: int, n_touches: int, cold: int, followup: int,
                 n_covered: int, n_pipeline: int) -> list[str]:
    """Plausibility invariants on the aggregates, returned as human-readable strings.

    Extracted from compute() so it is REACHABLE FROM A TEST. Inline, these branches
    could not be exercised through the normal path, so every mutant of them survived:
    a guard whose failure mode cannot be triggered is decorative, which is the exact
    thing this repo's mutation rule exists to catch. outreach_pending.py carries the
    same invariants for the same reason — a 93%-instead-of-48% response rate shipped
    green once already.
    """
    out = []
    if n_replied > n_touches:
        out.append(f"replied ({n_replied}) > touches ({n_touches})")
    if cold + followup > n_touches:
        out.append(f"cold+followup ({cold + followup}) > touches ({n_touches})")
    if n_touches and not (0 <= n_replied / n_touches <= 1):
        out.append("response_rate outside [0,100]")
    if n_covered > n_pipeline:
        out.append(f"covered companies ({n_covered}) exceed pipeline ({n_pipeline})")
    return out


def compute(outreach: str, networking: str, pipeline_rows: list[dict],
            today: date) -> dict:
    rows = parse_rows(outreach)
    interactions = parse_networking_interactions(networking)

    people = Counter(r["recipient"].lower().strip()
                     for r in rows if r["recipient"] not in ("", "—"))
    companies = {norm_company(r["company"]) for r in rows if r["company"] not in ("", "—")}
    companies.discard("")

    by_month = defaultdict(lambda: {"all": 0, "cold": 0, "followup": 0, "replied": 0})
    for r in rows:
        if not re.match(r"\d{4}-\d{2}", r["date"]):
            continue
        m = by_month[r["date"][:7]]
        m["all"] += 1
        if r["skill"] in COLD_SKILLS:
            m["cold"] += 1
        elif r["skill"] in FOLLOWUP_SKILLS:
            m["followup"] += 1
        if replied(r, interactions):
            m["replied"] += 1

    def facet(key):
        out = defaultdict(lambda: {"n": 0, "replied": 0})
        for r in rows:
            b = out[r[key] or "unspecified"]
            b["n"] += 1
            if replied(r, interactions):
                b["replied"] += 1
        return [{key: k, **v} for k, v in
                sorted(out.items(), key=lambda kv: -kv[1]["n"])]

    # .strip() in the guard means "" can never enter, so no discard is needed here.
    # The `companies` set above DOES need one: its guard admits whitespace-only cells,
    # which norm_company then flattens to "".
    pipe_cos = {norm_company(r["company"]) for r in pipeline_rows if r["company"].strip()}
    covered = pipe_cos & companies

    n_replied = sum(1 for r in rows if replied(r, interactions))
    cold = sum(1 for r in rows if r["skill"] in COLD_SKILLS)
    fup = sum(1 for r in rows if r["skill"] in FOLLOWUP_SKILLS)

    warnings = sanity_check(n_replied, len(rows), cold, fup, len(covered), len(pipe_cos))

    return {
        "generated": today.isoformat(),
        "sanity_warnings": warnings,
        "totals": {
            "touches": len(rows),
            "replied": n_replied,
            "response_rate": round(n_replied / len(rows) * 100, 1) if rows else 0.0,
            "distinct_recipients": len(people),
            "distinct_companies": len(companies),
            "cold": cold,
            "followup": fup,
            "cold_share": round(cold / len(rows) * 100, 1) if rows else 0.0,
            "followups_per_cold": round(fup / cold, 1) if cold else None,
        },
        "reach": {
            "touches_per_person": round(len(rows) / len(people), 1) if people else 0.0,
            "contacted_once": sum(1 for v in people.values() if v == 1),
            "contacted_5_plus": sum(1 for v in people.values() if v >= 5),
        },
        "coverage": {
            "pipeline_companies": len(pipe_cos),
            "with_outreach": len(covered),
            "without_outreach": len(pipe_cos - companies),
            "pct_without": round(len(pipe_cos - companies) / len(pipe_cos) * 100)
                           if pipe_cos else 0,
        },
        "by_month": [{"month": k, **v} for k, v in sorted(by_month.items())],
        "by_channel": facet("channel"),
        "by_skill": facet("skill"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--target-date")
    ap.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())
    root = args.repo_root

    outreach = read_file(root / "data" / "outreach-log.md")
    if not outreach.strip():
        print("ERROR: data/outreach-log.md is empty or missing; refusing to emit "
              "metrics that would read as a real zero.", file=sys.stderr)
        return 2

    networking = read_file(root / "data" / "networking.md")
    pipeline = parse_all_rows(read_file(root / "data" / "job-pipeline.md"), today)

    result = compute(outreach, networking, pipeline, today)
    payload = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {args.out} ({result['totals']['touches']} touches)")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
