#!/usr/bin/env python3
"""rederivation_log.py -- the ledger of things we rebuilt that already existed.

WHY THIS IS NOT THE FRICTION LOG. `friction_log.py` records "an error that burned a turn
during a script invocation, hook fire, or template use." A re-derivation is not an error.
Nothing failed. A correct, promoted, carefully written artifact existed and was not found,
and the work got done a second time from scratch. Different detector, different ladder,
different fix.

WHY IT IS NOT A SECTION PER SKILL. Thirty-eight skills would give thirty-eight mostly-empty
sections, which is the shape that produced zero consultations across 1,229 transcripts for
the memory shards. The `surface` emerges from the rows that actually fire. Surfaces that
never fire cost nothing.

THE FIELD THAT MATTERS IS `why_missed`. Everything else is bookkeeping. `rebuilt` and
`existed` tell you a re-derivation happened; `why_missed` tells you WHICH ENTRY POINT NEEDS
THE RETRIEVAL WIRED IN, which is the only action this ledger exists to produce. A row
without it is a complaint rather than a finding, so it is required.

THE LADDER, and it differs from friction's on purpose. It advances on WHETHER RETRIEVAL WAS
ALREADY WIRED at this surface, not on raw count:
  not yet wired, any count -> WIRE RETRIEVAL INTO THAT ENTRY POINT. The skill, hook or doc
                              that starts this task type loads the prior learnings itself,
                              as a dependency rather than as a discipline. The worked
                              instance is STEP 0 in the mckinsey-slides skill, 2026-09-17.
  wired, and it fired AGAIN -> the retrieval MECHANISM is wrong, not the pointer. Stop
                              adding pointers and fix how this corpus is searched.

WHY NOT RAW COUNT, found by running this tool on its own seed data 2026-09-17. Three
instruments were re-derived at one entry point from ONE root cause, a single one-way link.
A count-based ladder read that as three fires and jumped straight to "the mechanism is
broken" on day one, which is false and is how an instrument trains people to ignore it. N
things missed through one hole is one hole. The question the ladder answers is "did the fix
hold," so the fix has to be recorded before a later fire can mean anything.

ORIGIN. 2026-09-17. Three instruments re-derived in one afternoon on a 2026-09 client take-home:
per-number provenance tagging, the function test for cutting, and the read-surface audit.
All three existed in the 2026-08 engagement corpus. `framework/deck-rubric.md` had named its own consumer
for five weeks while nothing pointed back at it. Nick's framing, the same day: archaeology
that does not land is waste, and half-closed strands cost twice.

Usage:
  rederivation_log.py append <surface> <rebuilt> --existed <path:line> --why-missed <text>
  rederivation_log.py list [--surface <name>] [--due]
  rederivation_log.py query <text>
  rederivation_log.py ladder            # what each surface owes right now

JSON-only on stdout. Atomic write. Ledger at memory/rederivation-log.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

LEDGER_REL = Path("memory") / "rederivation-log.md"

HEADER = """# Re-derivation Log

> Things we rebuilt that already existed. **Not** the friction log: nothing errored here, a
> correct artifact simply was not found. Mutate via `tools/rederivation_log.py` only.
>
> **The actionable column is `why missed`.** It names which entry point needs retrieval wired
> into it. The rest is bookkeeping.
>
> **Ladder:** a surface that has not had retrieval wired owes the wiring, whatever the count.
> A surface that WAS wired and fired again means the retrieval mechanism is wrong, not the
> pointer. N things missed through one hole is one hole; the rung tracks whether the fix held.
> Wirings are recorded in the `## Retrieval wired` section below the table.
>
> Origin: 2026-09-17, three instruments re-derived in one afternoon that all existed in the
> the 2026-08 engagement corpus. See `output/analysis/091726-deck-rubric-gap-register.md`.

| surface | rebuilt | already existed | why missed | count | first..last | owes |
|---|---|---|---|---|---|---|
"""

WIRED_MARK = "wired"          # recorded in the `owes` column once retrieval is in place
OWES_WIRE = "wire retrieval into this entry point"
OWES_MECHANISM = "retrieval MECHANISM is wrong -- it was wired and fired again"


def ledger_path(root: Path) -> Path:
    return root / LEDGER_REL


def _cell(s: str) -> str:
    """Escape a value for a markdown table cell.

    A raw `|` in ordinary prose shifted every subsequent field one column left on read:
    `rebuilt` swallowed by the split, `existed` read from `why_missed`, and the dates read
    from text, which then compared lexicographically in _rung() and produced nonsense rungs.
    `why_missed` is the one column this ledger exists to produce, so losing it silently is
    the worst available failure. Found by cross-model review 2026-09-17, reproduced before fix.
    """
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _uncell(s: str) -> str:
    return str(s).replace("\\|", "|").replace("\\\\", "\\")


def _split_row(line: str) -> list[str]:
    """Split a table row on UNESCAPED pipes only."""
    import re as _re
    parts = _re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [_uncell(x.strip()) for x in parts]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _owes(wired: bool, fired_since_wiring: bool) -> str:
    """The ladder rung. Advances on whether the fix HELD, never on raw count."""
    if not wired:
        return OWES_WIRE
    return OWES_MECHANISM if fired_since_wiring else WIRED_MARK


WIRED_HEAD = ("\n## Retrieval wired\n\n| surface | how | date | fires at wiring |\n"
              "|---|---|---|---|\n")


def read_wired(path: Path) -> list[dict]:
    """Surfaces that have had retrieval wired in, and what was wired."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    # MATCH THE HEADING AT LINE START, never the bare string. The ledger's own header
    # explains where wirings are recorded and therefore CONTAINS the literal "## Retrieval
    # wired" in prose; a substring split lands on that sentence and parses the entire main
    # table as wired rows. Found 2026-09-17 by running the tool on the real ledger, where it
    # reported `wired: True` before anything had been wired.
    parts = re.split(r"(?m)^## Retrieval wired\s*$", text)
    if len(parts) < 2:
        return []
    out = []
    for line in parts[-1].splitlines():
        if not line.startswith("|") or line.startswith("|---") or "| surface |" in line:
            continue
        c = _split_row(line)
        if len(c) >= 3:
            out.append({"surface": c[0], "how": c[1], "date": c[2],
                        "fires_at_wiring": int(c[3]) if len(c) > 3 and c[3].isdigit() else 0})
    return out


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---") or "| surface |" in line:
            continue
        cells = _split_row(line)
        if len(cells) < 7:
            continue
        span = cells[5].split("..")
        rows.append({
            "surface": cells[0], "rebuilt": cells[1], "existed": cells[2],
            "why_missed": cells[3], "count": int(cells[4]) if cells[4].isdigit() else 1,
            "first": span[0], "last": span[-1], "owes": cells[6],
        })
    return rows


def write_rows(path: Path, rows: list[dict], wired: list[dict] | None = None) -> None:
    """Atomic. A half-written ledger is worse than no ledger."""
    wired = wired if wired is not None else read_wired(path)
    body = HEADER + "".join(
        f"| {_cell(r['surface'])} | {_cell(r['rebuilt'])} | {_cell(r['existed'])} "
        f"| {_cell(r['why_missed'])} | {r['count']} | {r['first']}..{r['last']} "
        f"| {_cell(r['owes'])} |\n"
        for r in sorted(rows, key=lambda r: (-r["count"], r["surface"])))
    body += WIRED_HEAD + "".join(
        f"| {_cell(w['surface'])} | {_cell(w['how'])} | {w['date']} "
        f"| {w.get('fires_at_wiring', 0)} |\n" for w in wired)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _wired_for(surface: str, wired: list[dict]) -> dict | None:
    return next((w for w in wired if _norm(w["surface"]) == _norm(surface)), None)


def _rung(surface: str, rows: list[dict], wired: list[dict]) -> tuple[str, str]:
    """(owes, note). Depends on whether retrieval was wired and whether it fired since."""
    w = _wired_for(surface, wired)
    fires = [r for r in rows if _norm(r["surface"]) == _norm(surface)]
    n = len(fires)
    if not w:
        return OWES_WIRE, (
            f"`{surface}` has {n} re-derivation(s) and retrieval has never been wired here. "
            f"Wire it into the entry point so it loads prior learnings as a dependency, not "
            f"a habit. Record it with `wire` when done.")
    # WATERMARK, NOT DATES. A date comparison at day precision cannot see the case that
    # matters most: wire retrieval in, and it fails again the same session. `last > date` is
    # False when both are the same day, so the ladder reported "holding" on the exact
    # recurrence it exists to escalate. Found by cross-model review 2026-09-17 and
    # reproduced before the fix. Total fires at this surface is monotonic, so comparing it
    # against the count captured at wiring time is correct at any time resolution.
    since = sum(r["count"] for r in fires) - w.get("fires_at_wiring", 0)
    if since > 0:
        return OWES_MECHANISM, (
            f"`{surface}` was wired on {w['date']} ({w['how']}) and has fired {since} "
            f"time(s) SINCE. Stop adding pointers: the retrieval MECHANISM is the defect. "
            f"How is this corpus searched at the moment of need?")
    return WIRED_MARK, f"`{surface}` wired {w['date']}: {w['how']}. Holding so far."


def cmd_append(args, root: Path) -> dict:
    path = ledger_path(root)
    rows, wired = read_rows(path), read_wired(path)
    today = args.date or date.today().isoformat()
    key = (_norm(args.surface), _norm(args.rebuilt))

    action = "added"
    for r in rows:
        if (_norm(r["surface"]), _norm(r["rebuilt"])) == key:
            r["count"] += 1
            r["last"] = today
            action = "incremented"
            break
    else:
        rows.append({"surface": args.surface, "rebuilt": args.rebuilt,
                     "existed": args.existed, "why_missed": args.why_missed,
                     "count": 1, "first": today, "last": today, "owes": ""})

    owes, note = _rung(args.surface, rows, wired)
    for r in rows:
        if _norm(r["surface"]) == key[0]:
            r["owes"] = owes
    write_rows(path, rows, wired)
    return {"status": "ok", "action": action, "surface": args.surface,
            "surface_fires": sum(1 for r in rows if _norm(r["surface"]) == key[0]),
            "owes": owes, "ladder_note": note}


def cmd_wire(args, root: Path) -> dict:
    """Record that retrieval was wired in. A later fire here means the MECHANISM is wrong."""
    path = ledger_path(root)
    rows, wired = read_rows(path), read_wired(path)
    today = args.date or date.today().isoformat()
    at = sum(r["count"] for r in rows if _norm(r["surface"]) == _norm(args.surface))
    existing = _wired_for(args.surface, wired)
    if existing:
        existing.update({"how": args.how, "date": today, "fires_at_wiring": at})
    else:
        wired.append({"surface": args.surface, "how": args.how, "date": today,
                      "fires_at_wiring": at})
    owes, note = _rung(args.surface, rows, wired)
    for r in rows:
        if _norm(r["surface"]) == _norm(args.surface):
            r["owes"] = owes
    write_rows(path, rows, wired)
    return {"status": "ok", "action": "wired", "surface": args.surface,
            "how": args.how, "date": today, "owes": owes, "ladder_note": note}


def cmd_list(args, root: Path) -> dict:
    rows = read_rows(ledger_path(root))
    if args.surface:
        rows = [r for r in rows if _norm(args.surface) in _norm(r["surface"])]
    if args.due:
        rows = [r for r in rows if r["owes"] != WIRED_MARK]
    return {"status": "ok", "count": len(rows), "rows": rows}


def cmd_query(args, root: Path) -> dict:
    q = _norm(args.text)
    rows = [r for r in read_rows(ledger_path(root))
            if q in _norm(r["rebuilt"] + " " + r["surface"] + " " + r["why_missed"])]
    return {"status": "ok", "count": len(rows), "rows": rows}


def cmd_ladder(args, root: Path) -> dict:
    path = ledger_path(root)
    rows, wired = read_rows(path), read_wired(path)
    surfaces = sorted({r["surface"] for r in rows})
    out = []
    for s in surfaces:
        owes, note = _rung(s, rows, wired)
        out.append({"surface": s,
                    "fires": sum(1 for r in rows if _norm(r["surface"]) == _norm(s)),
                    "wired": bool(_wired_for(s, wired)), "owes": owes, "note": note})
    out.sort(key=lambda o: (o["owes"] == WIRED_MARK, -o["fires"]))
    return {"status": "ok", "surfaces": len(out), "total_rederivations": len(rows),
            "due": [o for o in out if o["owes"] != WIRED_MARK], "all": out}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo-root", default=".")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="record a re-derivation")
    a.add_argument("surface", help="the entry point where it happened (skill, task type)")
    a.add_argument("rebuilt", help="what was re-derived")
    a.add_argument("--existed", required=True, help="where it already existed, path:line")
    a.add_argument("--why-missed", required=True, dest="why_missed",
                   help="why the existing thing was not found. THE actionable field")
    a.add_argument("--date", help="ISO date, defaults to today")

    w = sub.add_parser("wire", help="record that retrieval was wired into a surface")
    w.add_argument("surface")
    w.add_argument("--how", required=True, help="what was wired, e.g. STEP 0 in <skill>")
    w.add_argument("--date")

    l = sub.add_parser("list")
    l.add_argument("--surface")
    l.add_argument("--due", action="store_true", help="only surfaces owing an action")

    q = sub.add_parser("query")
    q.add_argument("text")

    sub.add_parser("ladder", help="what each surface owes right now")

    args = p.parse_args(argv)
    root = Path(args.repo_root).resolve()
    fn = {"append": cmd_append, "list": cmd_list, "query": cmd_query,
          "ladder": cmd_ladder, "wire": cmd_wire}
    try:
        print(json.dumps(fn[args.cmd](args, root), indent=2))
        return 0
    except Exception as exc:  # surfaced as JSON so a caller never parses a traceback
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
