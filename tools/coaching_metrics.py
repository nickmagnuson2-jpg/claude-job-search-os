#!/usr/bin/env python3
"""
coaching_metrics.py — Longitudinal projection of interview performance, for the console.

Reads: coaching/progress/_summary.md (the Per-Dimension Trend table)

WHY THIS EXISTS
---------------
The capability data is the only corpus in this repo with what analysis actually needs:
a date axis, repeated measures, a locked instrument (rubric v1.0, 2026-05-07), and an
outcome the owner controls. The pipeline has none of those — 103 closures carry two
distinct outcome values and no entered/closed dates — so "what is working" is not
answerable there and is answerable here.

It surfaced one finding immediately on the live file: four of five rubric dimensions
rose across the corpus while Delivery Crispness FELL (2.92 -> 2.77) and has scored <=3.0
on 17 of 25 calls. That dimension is the floor and it was invisible inside a 175KB doc.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No significance testing. n is 25-27 self-and-Claude-scored calls, the pre-2026-05-07
rows were backfilled under an earlier instrument, and a half-split is a crude trend
test. The numbers are reported with their n so a reader can discount them; dressing
them up as inference would be the exact defect this repo's name-the-scope rule governs.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/coaching_metrics.py [--repo-root PATH] [--out FILE]
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

DIMENSIONS = {
    3: "format_resilience",
    4: "delivery_crispness",
    5: "star_quality",
    6: "applied_listening",
    7: "authenticity",
}
OVERALL_COL = 8
SIGNAL_COL = 9
RUBRIC_LOCKED = "2026-05-07"
MIN_ROWS = 5


def num(cell: str) -> float | None:
    """First number in a cell. '3.8/5' -> 3.8, '4' -> 4.0, 'n/a' -> None."""
    m = re.search(r"\d+(?:\.\d+)?", cell or "")
    return float(m.group()) if m else None


def parse_trend(content: str) -> list[dict]:
    """Rows of the Per-Dimension Trend table, oldest first.

    Identified by shape, not by heading position: a table row whose first cell is a
    bare ISO date and which has at least 10 columns. The summary file carries several
    other tables, and heading-anchored parsing broke when sections were reordered.
    """
    rows = []
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 10 or not re.match(r"^\d{4}-\d{2}-\d{2}$", cols[0]):
            continue
        rows.append({
            "date": cols[0],
            "call": re.sub(r"\s*[(/].*", "", cols[1]).strip(),
            "format": re.sub(r"\s*[( ].*", "", cols[2]).strip(),
            "is_drill": "drill" in cols[2].lower() or "drill" in cols[1].lower(),
            "scores": {name: num(cols[i]) for i, name in DIMENSIONS.items()},
            "overall": num(cols[OVERALL_COL]),
            "signal_raw": cols[SIGNAL_COL],
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def half_split(series: list[tuple[str, float]]) -> dict | None:
    """Chronological first-half vs second-half means. None below 4 points."""
    if len(series) < 4:
        return None
    h = len(series) // 2
    early = statistics.mean(v for _, v in series[:h])
    late = statistics.mean(v for _, v in series[h:])
    return {"n": len(series), "early": round(early, 2),
            "late": round(late, 2), "delta": round(late - early, 2)}


def calibration(rows: list[dict]) -> dict:
    """Agreement between Nick's read and Claude's read of interviewer signal."""
    agree = disagree = 0
    for r in rows:
        found = re.findall(r"(high|med-high|med|low)[^/]*?\((Nick|Claude)\)",
                           r["signal_raw"], re.I)
        if len(found) == 2:
            if found[0][0].lower() == found[1][0].lower():
                agree += 1
            else:
                disagree += 1
    total = agree + disagree
    return {"scored_both": total, "agree": agree, "disagree": disagree,
            "agreement_pct": round(agree / total * 100) if total else None}


def compute(content: str) -> dict:
    rows = parse_trend(content)
    real = [r for r in rows if not r["is_drill"]]

    trends = {}
    for name in DIMENSIONS.values():
        series = [(r["date"], r["scores"][name]) for r in rows
                  if r["scores"][name] is not None]
        trends[name] = half_split(series)
    trends["overall"] = half_split(
        [(r["date"], r["overall"]) for r in rows if r["overall"] is not None])

    # The floor: how often each dimension sits at or below 3.0.
    floors = {}
    for name in DIMENSIONS.values():
        vals = [r["scores"][name] for r in rows if r["scores"][name] is not None]
        floors[name] = {
            "n": len(vals),
            "at_or_below_3": sum(1 for v in vals if v <= 3.0),
            "max": max(vals) if vals else None,
            "mean": round(statistics.mean(vals), 2) if vals else None,
        }

    by_format = {}
    for fmt, n in Counter(r["format"] for r in rows).items():
        vals = [r["overall"] for r in rows if r["format"] == fmt and r["overall"]]
        by_format[fmt] = {"n": n,
                          "mean_overall": round(statistics.mean(vals), 2) if vals else None}

    return {
        "rubric_locked": RUBRIC_LOCKED,
        "sessions": {"scored_rows": len(rows), "real": len(real),
                     "drills": len(rows) - len(real),
                     "date_range": [rows[0]["date"], rows[-1]["date"]] if rows else None},
        "trends": trends,
        "floors": floors,
        "by_format": by_format,
        "calibration": calibration(rows),
        "sessions_detail": [
            {"date": r["date"], "call": r["call"], "format": r["format"],
             "is_drill": r["is_drill"], "overall": r["overall"], **r["scores"]}
            for r in rows
        ],
        "caveats": [
            f"Rows before {RUBRIC_LOCKED} were backfilled under an earlier instrument.",
            "Scores are self-and-Claude assessed, not independent.",
            "Half-split is a crude trend test; no significance testing is performed.",
        ],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Longitudinal interview-performance metrics.")
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    src = args.repo_root / "coaching" / "progress" / "_summary.md"
    if not src.exists():
        print(f"ERROR: {src} not found", file=sys.stderr)
        return 1
    content = src.read_text(encoding="utf-8")

    result = compute(content)
    n = result["sessions"]["scored_rows"]
    # Hard abort, not a warning: an empty parse here reads as "no interviews scored",
    # which is indistinguishable from a real zero on a dashboard.
    if n < MIN_ROWS:
        print(f"REFUSED: parsed {n} scored row(s), below the {MIN_ROWS} minimum. "
              "The Per-Dimension Trend table shape has probably changed.",
              file=sys.stderr)
        return 2

    payload = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {args.out} ({n} scored sessions)")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
