#!/usr/bin/env python3
"""learning_ledger.py -- one record shape across every lane the learning loop captures into.

THE PROBLEM. The loop captures into five places in four formats, and only ONE of them has a
drain that runs without a human:

    lane                     format                          auto-detector   drain
    memory/feedback_*.md     YAML frontmatter                none            scan_promotion_candidates.py
    memory/friction-log.md   | Date | Surface | Nature | ... |  YES (hook)   ladder in friction_log.py
    memory/lessons.md S1     | # | Pattern | Rule | Date |    none           none
    memory/lessons.md S2     | Pattern | Rule | Occ | Prom |  none           documented, no scanner
    anti-pattern-tracker.md  ### heading + **Status:**       none           read by prep, no ladder

So "is this the 2nd fire?" -- the question the whole loop turns on -- can only be answered
for two of the five, and "what is due for promotion across everything" cannot be answered at
all. A rule at 2 fires in lessons.md is invisible to the only scanner that exists.

WHAT THIS DOES, AND DELIBERATELY DOES NOT DO. It NORMALIZES; it does not migrate. Every file
keeps its own format, its own writers, and its own history -- nothing is reformatted and
nothing is lost. This reads all five and yields one record shape:

    {lane, ref, what, when, occurrences, disposition, evidence}

WHY NOT CONVERT EVERYTHING TO JSON. Because the format was never the defect. The friction
lane closes its loop end to end and its store is a markdown table; what makes it work is
that `friction_log.py` owns the writes. The four broken lanes are broken because nothing
writes to them automatically, not because they are markdown. And the corpus is consulted by
`grep` -- moving 514 rules into JSON would break the one retrieval mechanism it actually has,
to fix a problem it does not have.

Exit codes:
    0  read every lane it could find
    1  bad usage
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DISPOSITION_OPEN = "open"
DISPOSITION_DONE = "promoted"
DISPOSITION_TERMINAL = "terminal"


def _row_cells(line: str) -> list[str]:
    """Split a markdown table row into cells. Returns [] for separators and non-rows."""
    s = line.strip()
    if not s.startswith("|"):
        return []
    cells = [c.strip() for c in s.strip("|").split("|")]
    if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
        return []  # the |---|---| separator
    return cells


def _int(value: str, default: int = 1) -> int:
    m = re.search(r"\d+", value or "")
    return int(m.group(0)) if m else default


def _disposition(value: str) -> str:
    """Only `yes`/`promoted` closes a record.

    `partial` deliberately stays OPEN and needs no branch of its own: half-landed
    enforcement is unfinished work, and counting it as done retires the rule from the
    backlog while the other half of the surface is still ungated. An earlier version had an
    explicit `partial` branch returning the same value, which was dead code pretending to be
    a decision -- the intent belongs in this docstring, not in an unreachable line.
    """
    v = (value or "").strip().lower()
    if v.startswith("yes") or v.startswith("promoted"):
        return DISPOSITION_DONE
    return DISPOSITION_OPEN


def read_memory(memory_dir: Path) -> list[dict]:
    out = []
    for path in sorted(memory_dir.glob("feedback_*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")[:6000]
        def fm(key, default=""):
            m = re.search(rf"^\s*{key}:\s*(.+)$", text, re.M)
            return m.group(1).strip().strip("\"'") if m else default
        terminal = fm("terminal").lower() == "true"
        promoted = fm("promoted", "no")
        disp = (DISPOSITION_TERMINAL if terminal
                else DISPOSITION_DONE if promoted.lower().startswith("yes")
                else DISPOSITION_OPEN)
        out.append({
            "lane": "memory", "ref": path.name,
            "what": fm("description") or path.stem,
            "when": fm("last_cited"), "occurrences": _int(fm("occurrences"), 0),
            "disposition": disp, "evidence": fm("reopen_gate"),
        })
    return out


def read_lessons(path: Path) -> list[dict]:
    """Both sections. Section 1 has no occurrence column, so it counts as 1 -- stated, not
    silently defaulted, because a lane that cannot express a repeat can never trip a gate."""
    if not path.is_file():
        return []
    out = []
    section = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## Section 1"):
            section = 1
            continue
        if line.startswith("## Section 2"):
            section = 2
            continue
        if section is None:
            continue
        cells = _row_cells(line)
        if not cells:
            continue
        if section == 1 and len(cells) >= 4:
            if cells[0].lower().startswith("#"):
                continue
            out.append({"lane": "lessons-1", "ref": f"lessons.md#{cells[0]}",
                        "what": cells[1], "when": cells[3], "occurrences": 1,
                        "disposition": DISPOSITION_OPEN,
                        "evidence": "Section 1 has no occurrence column"})
        elif section == 2 and len(cells) >= 5:
            if cells[0].lower() == "pattern":
                continue
            out.append({"lane": "lessons-2", "ref": f"lessons.md:{cells[4]}",
                        "what": cells[0][:200], "when": cells[4],
                        "occurrences": _int(cells[2]),
                        "disposition": _disposition(cells[3]), "evidence": cells[1][:200]})
    return out


def read_friction(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cells = _row_cells(line)
        if len(cells) < 6 or cells[0].lower() == "date":
            continue
        # The Promotion column here is NOT a done/not-done flag. It records which LADDER
        # RUNG the row reached: "-" (nothing demanded), "memory (do now)" at the 2nd fire,
        # "script-patch (mandatory)" at the 3rd. Live values, 2026-08-25: 170 / 54 / 79.
        # So a rung is a DEMAND, and this lane has no field anywhere recording whether the
        # demand was met -- 79 rows say a script patch is mandatory and not one of them can
        # say whether it was written. The lane closes its detection loop and not its drain.
        rung = cells[5].strip()
        demanded = bool(rung) and rung not in {"-", "--", "\u2014", "\u2013"}
        out.append({"lane": "friction", "ref": f"{cells[1]}:{cells[0]}",
                    "what": cells[2][:200], "when": cells[0],
                    "occurrences": _int(cells[4]),
                    "disposition": DISPOSITION_OPEN if demanded else "no-action-needed",
                    "evidence": f"rung: {rung or '-'}"})
    return out


def read_antipatterns(path: Path) -> list[dict]:
    """### heading + **Status:** blocks. No occurrence column exists, so every entry reads
    as 1 and this lane can never trip a count gate -- which is the finding, not a gap to
    paper over with a guess."""
    if not path.is_file():
        return []
    out, current, resolved = [], None, False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## "):
            resolved = "resolved" in line.lower()
            continue
        if line.startswith("### "):
            current = line[4:].strip()
            out.append({"lane": "anti-pattern", "ref": current[:80], "what": current[:200],
                        "when": "", "occurrences": 1,
                        "disposition": DISPOSITION_DONE if resolved else DISPOSITION_OPEN,
                        "evidence": "no occurrence column in this lane"})
        elif current and line.startswith("**Status:**") and out:
            out[-1]["evidence"] = line.replace("**Status:**", "").strip()[:200]
    return out


def collect(repo_root: Path, memory_dir: Path) -> dict:
    records = (read_memory(memory_dir)
               + read_lessons(repo_root / "memory" / "lessons.md")
               + read_friction(repo_root / "memory" / "friction-log.md")
               + read_antipatterns(repo_root / "coaching" / "anti-pattern-tracker.md"))
    lanes: dict[str, dict] = {}
    for r in records:
        s = lanes.setdefault(r["lane"], {"records": 0, "open": 0, "due": 0, "promoted": 0,
                                         "terminal": 0})
        s["records"] += 1
        s[r["disposition"]] = s.get(r["disposition"], 0) + 1
        # "due" = the ladder has demanded something and nothing records it being done.
        if r["disposition"] == DISPOSITION_OPEN and r["occurrences"] >= 2:
            s["due"] += 1
    return {"total": len(records), "lanes": lanes, "records": records,
            "due_total": sum(v["due"] for v in lanes.values())}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-dir", required=True, type=Path)
    ap.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--due", action="store_true", help="list only records at 2+ and still open")
    args = ap.parse_args(argv)

    if not args.memory_dir.is_dir():
        print(f"not a directory: {args.memory_dir}", file=sys.stderr)
        return 1
    report = collect(args.repo_root, args.memory_dir)

    if args.json:
        print(json.dumps(report, indent=2))
    elif args.due:
        for r in report["records"]:
            if r["disposition"] == DISPOSITION_OPEN and r["occurrences"] >= 2:
                print(f"{r['occurrences']:>3}  {r['lane']:<12} {r['ref'][:44]:<46} {r['what'][:60]}")
    else:
        print(f"{'lane':<14}{'records':>9}{'open':>7}{'due':>6}{'promoted':>10}{'terminal':>10}")
        for lane, s in sorted(report["lanes"].items()):
            print(f"{lane:<14}{s['records']:>9}{s.get('open',0):>7}{s['due']:>6}"
                  f"{s.get('promoted',0):>10}{s.get('terminal',0):>10}")
        print(f"{'TOTAL':<14}{report['total']:>9}{'':>7}{report['due_total']:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
