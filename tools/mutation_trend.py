#!/usr/bin/env python3
"""
mutation_trend.py — longitudinal record of how much of the tool corpus is protected.

WHY. `mutation_report.py` answers "where does the corpus stand right now". It cannot answer
"is this getting better", because each sweep overwrites the last. The single number worth
watching is the share of mutated decisions that survive: 34% on 2026-09-02 (3,657 of 10,794).
A number nobody records cannot be a target.

APPEND-ONLY, newest-last, one row per recorded sweep. Never rewrite a historical row: the
whole value is the series. Re-recording the same baseline mtime is refused rather than
duplicated, so a second `record` after a re-read is a no-op instead of a fake data point.

  record   read a baseline.jsonl, append one dated row (refuses an unchanged baseline)
  show     print the series with deltas

Both take --state-dir (default: the live baseline directory).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import mutation_state  # noqa: E402
DEFAULT_STATE = mutation_state.state_dir()
TREND_NAME = mutation_state.TREND_NAME


# The aggregation lives in mutation_state, because mutation_report.py had its own inline
# copy of the same domain rule and the two had already diverged. `summarise` is
# re-exported because callers and tests use it from here. The trend file is read with the
# same JSONL reader as the baseline (mutation_state.read_rows), which is what the deleted
# load_trend duplicated line for line.
summarise = mutation_state.summarise


def cmd_record(state_dir: Path, note: str) -> int:
    baseline = state_dir / "baseline.jsonl"
    if not baseline.exists():
        print(json.dumps({"status": "error", "message": f"no baseline at {baseline}"}))
        return 1
    trend_path = state_dir / TREND_NAME
    stamp = dt.datetime.fromtimestamp(baseline.stat().st_mtime).isoformat(timespec="seconds")
    existing = mutation_state.read_rows(trend_path)
    if any(e.get("baseline_mtime") == stamp for e in existing):
        print(json.dumps({"status": "skipped",
                          "message": "this baseline is already recorded; nothing appended",
                          "baseline_mtime": stamp}))
        return 0
    entry = {"recorded": dt.date.today().isoformat(), "baseline_mtime": stamp}
    rows = mutation_state.read_rows(baseline)
    deduped = mutation_state.latest_per_tool(rows)
    entry |= summarise(deduped)
    # Recorded so a future reader can see the append-only file was collapsed, and by how
    # much. A silent dedupe would make the 175-vs-123 discrepancy invisible again.
    entry["baseline_rows"] = len(rows)
    entry["baseline_tools"] = len(deduped)
    if note:
        entry["note"] = note
    with trend_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(json.dumps({"status": "ok", "appended": entry}, indent=2))
    return 0


def cmd_show(state_dir: Path) -> int:
    entries = mutation_state.read_rows(state_dir / TREND_NAME)
    if not entries:
        print("no trend recorded yet — run: python3 tools/mutation_trend.py record")
        return 0
    print(f"{'date':<12}{'survival':>10}{'delta':>9}{'survived':>10}"
          f"{'mutants':>9}{'clean':>7}{'no verdict':>12}")
    print("-" * 69)
    prev = None
    for e in entries:
        pct = e.get("survival_pct")
        delta = "" if prev is None or pct is None else f"{pct - prev:+.2f}"
        print(f"{e['recorded']:<12}{(str(pct) + '%'):>10}{delta:>9}"
              f"{e['survived']:>10}{e['mutants']:>9}{e['tools_clean']:>7}"
              f"{e['tools_no_verdict']:>12}")
        if pct is not None:
            prev = pct
    first, last = entries[0], entries[-1]
    if len(entries) > 1 and first.get("survival_pct") and last.get("survival_pct"):
        print(f"\nsince {first['recorded']}: "
              f"{last['survival_pct'] - first['survival_pct']:+.2f} points")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["record", "show"])
    ap.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--note", default="", help="short label for this data point")
    args = ap.parse_args(argv)
    if args.command == "record":
        return cmd_record(args.state_dir, args.note)
    return cmd_show(args.state_dir)


if __name__ == "__main__":
    sys.exit(main())
