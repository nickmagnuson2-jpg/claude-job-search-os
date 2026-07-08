#!/usr/bin/env python3
"""scan_promotion_candidates.py — mechanical detector for the memory-tier
promotion/demotion loop. Two deterministic signals, zero LLM judgment calls:

  PROMOTION (push validated/recurring rules OUT to skill/framework files):
    frontmatter `occurrences >= 2` AND `promoted: no` (or missing/false).

  DEMOTION (push stale/unused entries OUT to archive):
    frontmatter `last_cited` older than --stale-days (default 60).

Only scans memory files that have OPTED IN to the schema (i.e. have an
`occurrences:` key in frontmatter). Per the 2026-07-08 scope decision: no
backfill on the pre-existing ~250-file corpus — that corpus was already swept
by hand the same day this script was built (see memory/promotion-backlog-2026-07.md).
This script only prevents the NEXT accumulation.

Frontmatter schema (added under the existing `metadata:` block):
  metadata:
    occurrences: 1          # bump whenever a dated supplement is added (recurrence)
    promoted: no            # or a tier string: skill | hook | principle | hard-rule
    reopen_gate: "<text>"   # free-text promotion criterion, human-readable
    last_cited: 2026-07-08  # stamped by the last_cited PostToolUse hook on Read

Two entry points share this one script (per the 2026-07-08 "no drift" decision):
  - cron mode (--mode cron): headless, regenerates memory/promotion-backlog.md,
    and if new items crossed threshold, adds a Low-priority todo via todo_write.py.
  - interactive mode (--mode interactive, the default): prints a chat-facing
    summary; used by the /memory-refresh skill. Same detection logic either way.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/scan_promotion_candidates.py --memory-dir <path> [--mode cron|interactive] [--stale-days 60] [--repo-root .]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
KV_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


def parse_frontmatter(text: str) -> dict:
    """Minimal flat-key YAML-ish frontmatter parser -- no pyyaml dependency,
    matching this project's zero-dep convention for tools/ scripts. Handles
    only what this schema needs: flat key: value pairs, optionally nested one
    level under `metadata:`. Quoted string values have quotes stripped."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    result = {}
    in_metadata = False
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        kv = KV_RE.match(line)
        if not kv:
            continue
        indent, key, val = kv.groups()
        val = val.strip()
        if val.startswith('"') and val.endswith('"') and len(val) >= 2:
            val = val[1:-1]
        if key == "metadata" and not val:
            in_metadata = True
            continue
        if indent == "" and key != "metadata":
            in_metadata = False
        result[key] = val
    return result


def load_memory_files(memory_dir: Path):
    for p in sorted(memory_dir.glob("*.md")):
        if p.name in ("MEMORY.md",) or p.name.startswith("MEMORY.backup") \
                or p.name.startswith("archive-") or p.name.startswith("archived-") \
                or p.name.startswith("audit-") or p.name.startswith("promotion-backlog"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        fm = parse_frontmatter(text)
        if "occurrences" not in fm:
            continue  # opted-out / pre-existing corpus, not tracked (no backfill)
        yield p, fm


def is_promoted(fm: dict) -> bool:
    v = str(fm.get("promoted", "no")).strip().lower()
    return v not in ("no", "false", "", "0")


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--memory-dir", required=True, help="path to the auto-memory directory")
    ap.add_argument("--repo-root", default=".", help="job-search repo root, for todo_write.py + backlog output")
    ap.add_argument("--mode", choices=["cron", "interactive"], default="interactive")
    ap.add_argument("--stale-days", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true", help="cron mode: compute but don't write backlog/todo")
    args = ap.parse_args(argv)

    memory_dir = Path(args.memory_dir).resolve()
    repo_root = Path(args.repo_root).resolve()
    today = date.today()

    promotion_candidates = []
    demotion_candidates = []

    for path, fm in load_memory_files(memory_dir):
        try:
            occurrences = int(fm.get("occurrences", 0))
        except ValueError:
            occurrences = 0

        if occurrences >= 2 and not is_promoted(fm):
            promotion_candidates.append({
                "file": path.name,
                "occurrences": occurrences,
                "reopen_gate": fm.get("reopen_gate", ""),
                "description": fm.get("description", ""),
            })

        last_cited = fm.get("last_cited", "")
        if last_cited:
            try:
                lc_date = datetime.strptime(last_cited, "%Y-%m-%d").date()
                age_days = (today - lc_date).days
                if age_days >= args.stale_days:
                    demotion_candidates.append({
                        "file": path.name,
                        "last_cited": last_cited,
                        "age_days": age_days,
                    })
            except ValueError:
                pass  # malformed date, skip rather than guess

    report = {
        "status": "ok",
        "scanned_dir": str(memory_dir),
        "date": today.isoformat(),
        "promotion_candidates": promotion_candidates,
        "demotion_candidates": demotion_candidates,
        "promotion_count": len(promotion_candidates),
        "demotion_count": len(demotion_candidates),
    }

    if args.mode == "interactive":
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        sys.exit(0)

    # --- cron mode: regenerate the living backlog doc + surface a todo ---
    backlog_path = memory_dir / "promotion-backlog.md"
    lines = [
        "# Promotion / Demotion Backlog (living doc)",
        "",
        f"Auto-generated by `tools/scan_promotion_candidates.py` -- last run {today.isoformat()}.",
        "Only covers memory files with an `occurrences:` frontmatter field (opted into the schema, 2026-07-08+).",
        "Pre-2026-07-08 corpus was hand-swept once; see `promotion-backlog-2026-07.md` for that one-time pass.",
        "",
        "## Promotion candidates (occurrences >= 2, not yet promoted)",
        "",
    ]
    if promotion_candidates:
        for c in promotion_candidates:
            gate = f" -- gate: {c['reopen_gate']}" if c["reopen_gate"] else ""
            lines.append(f"- `{c['file']}` ({c['occurrences']}x){gate}")
    else:
        lines.append("_none_")
    lines += ["", "## Demotion candidates (unread >= {}d)".format(args.stale_days), ""]
    if demotion_candidates:
        for c in demotion_candidates:
            lines.append(f"- `{c['file']}` (last cited {c['last_cited']}, {c['age_days']}d ago)")
    else:
        lines.append("_none_")
    lines.append("")

    if not args.dry_run:
        backlog_path.write_text("\n".join(lines), encoding="utf-8")

    new_total = len(promotion_candidates) + len(demotion_candidates)
    if new_total and not args.dry_run:
        todo_script = repo_root / "tools" / "todo_write.py"
        if todo_script.exists():
            subprocess.run(
                ["python3", str(todo_script), "--repo-root", str(repo_root), "add",
                 f"Memory refresh: {len(promotion_candidates)} promotion + {len(demotion_candidates)} demotion candidates -- run /memory-refresh",
                 "Low"],
                capture_output=True, text=True,
            )

    report["backlog_written"] = str(backlog_path) if not args.dry_run else None
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main(sys.argv[1:])
