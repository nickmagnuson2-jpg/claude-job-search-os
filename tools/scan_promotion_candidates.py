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


def _is_scannable(name: str) -> bool:
    """Is this a memory RULE file (vs an index, archive, audit, or the router)?"""
    if name in ("MEMORY.md",):
        return False
    return not any(name.startswith(pfx) for pfx in (
        "MEMORY.backup", "archive-", "archived-", "audit-",
        "promotion-backlog", "index-",
    ))


def iter_candidate_files(memory_dir: Path):
    """Every memory rule file, WITHOUT the schema filter.

    This is the honest denominator. `load_memory_files` below drops files lacking
    `occurrences:`, so any ratio computed inside its loop is 100% by construction --
    the filter pre-selects its own numerator. Coverage must be measured here.
    """
    for p in sorted(memory_dir.glob("*.md")):
        if not _is_scannable(p.name):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        yield p, parse_frontmatter(text)


def schema_coverage(memory_dir: Path) -> dict:
    """How much of the corpus the two signals can actually see.

    An empty candidate list is only interpretable next to this number. On 2026-08-13 the
    scanner returned 1 candidate against 3.5% coverage, which read as an all-clear and was
    really an empty scan.

    Broken out by rule type, because the two signals have different scopes and a blended
    percentage hides that: `occurrences`/`promoted`/`reopen_gate` are only meaningful for
    FEEDBACK rules (a project/reference/user memory is a fact, not a rule that can recur),
    while `last_cited` staleness applies to every type. Backfill scope follows this split.
    """
    total = visible = 0
    fb_total = fb_visible = 0
    cited_total = 0
    for path, fm in iter_candidate_files(memory_dir):
        total += 1
        is_feedback = path.name.startswith("feedback_") or fm.get("type") == "feedback"
        if "occurrences" in fm:
            visible += 1
        if "last_cited" in fm:
            cited_total += 1
        if is_feedback:
            fb_total += 1
            if "occurrences" in fm:
                fb_visible += 1
    return {
        "files": total,
        "visible": visible,
        "invisible": total - visible,
        "pct": round(100 * visible / total, 1) if total else 0.0,
        "feedback_rules": fb_total,
        "feedback_visible": fb_visible,
        "feedback_invisible": fb_total - fb_visible,
        "feedback_pct": round(100 * fb_visible / fb_total, 1) if fb_total else 0.0,
        "with_last_cited": cited_total,
        "note": ("promotion signal covers ONLY feedback rules carrying occurrences:; "
                 "low coverage means an empty candidate list is an empty scan, not an "
                 "all-clear. Backfill scope = feedback_invisible."),
    }


def load_memory_files(memory_dir: Path):
    for p, fm in iter_candidate_files(memory_dir):
        if "occurrences" not in fm:
            continue  # invisible to the signals; counted by schema_coverage(), not here
        yield p, fm


def is_promoted(fm: dict) -> bool:
    v = str(fm.get("promoted", "no")).strip().lower()
    return v not in ("no", "false", "", "0")


# Always-loaded files pay their size as a per-session tax forever. Thresholds are the ones
# already stated in CLAUDE.md's Memory Hygiene section (~24KB/shard) and the observed
# CLAUDE.md working size; both are advisory, surfaced for judgment, never auto-acted.
SHARD_LIMIT_BYTES = 24 * 1024
CLAUDE_MD_LIMIT_BYTES = 40 * 1024


def oversized_context_files(memory_dir: Path, repo_root: Path) -> list[dict]:
    """Always-loaded files past their size budget. Reporting only -- never mutates."""
    targets = [(p, SHARD_LIMIT_BYTES) for p in sorted(memory_dir.glob("index-*.md"))]
    mem = memory_dir / "MEMORY.md"
    if mem.is_file():
        targets.append((mem, SHARD_LIMIT_BYTES))
    claude_md = repo_root / "CLAUDE.md"
    if claude_md.is_file():
        targets.append((claude_md, CLAUDE_MD_LIMIT_BYTES))

    out = []
    for path, limit in targets:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > limit:
            out.append({
                "file": path.name,
                "bytes": size,
                "limit": limit,
                "over_by": size - limit,
                "ratio": round(size / limit, 2),
            })
    return sorted(out, key=lambda d: -d["over_by"])


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
    # Post-backfill (2026-08-13) the corpus deliberately carries NO seeded `last_cited`:
    # seeding today would fake freshness, seeding mtime would fake staleness. The
    # last_cited hook stamps it on the first genuine Read. That leaves a third state --
    # visible to the promotion signal, invisible to the demotion signal, forever --
    # and "never cited since backfill" is arguably the strongest demotion evidence there
    # is. It is reported as its own bucket rather than silently skipped.
    never_cited = []
    needs_review = 0

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

        if str(fm.get("needs_review", "")).strip().lower() in ("true", "yes", "1"):
            needs_review += 1

        last_cited = fm.get("last_cited", "")
        if not last_cited:
            never_cited.append({
                "file": path.name,
                "occurrences": occurrences,
                "needs_review": str(fm.get("needs_review", "")).strip().lower() in ("true", "yes", "1"),
            })
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

    coverage = schema_coverage(memory_dir)
    oversized = oversized_context_files(memory_dir, repo_root)

    report = {
        "status": "ok",
        "scanned_dir": str(memory_dir),
        "date": today.isoformat(),
        "promotion_candidates": promotion_candidates,
        "demotion_candidates": demotion_candidates,
        "promotion_count": len(promotion_candidates),
        "demotion_count": len(demotion_candidates),
        # Visible to promotion, invisible to demotion: no last_cited has ever been stamped.
        # A count plus a bounded sample -- right after the Phase 1 backfill this is ~383
        # files, and dumping all of them would bury the two live signals above.
        "never_cited_count": len(never_cited),
        "never_cited_sample": never_cited[:10],
        # Files whose occurrences/promoted were backfilled, not counted. A `1` here means
        # "unknown", not "fired once" -- Phase 2 mining is what resolves them.
        "needs_review_count": needs_review,
        # An empty candidate list is only meaningful alongside coverage. Read them together.
        "schema_coverage": coverage,
        # The trigger /trim-context-file never had: it was mutation-verified and then never
        # invoked once, because nothing ever pinged that a file had grown. Reporting is not
        # acting -- this surfaces, the merged hygiene skill decides.
        "oversized_context_files": oversized,
        "oversized_count": len(oversized),
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
    lines += [
        "",
        "## Signal health",
        "",
        f"- Schema coverage: {coverage['feedback_visible']}/{coverage['feedback_rules']} "
        f"feedback rules visible to the promotion signal ({coverage['feedback_pct']}%).",
        f"- Never cited: {len(never_cited)} visible files have no `last_cited` yet "
        "(the hook stamps it on first genuine Read; until then they cannot go stale).",
        f"- Needs review: {needs_review} files carry backfilled counts -- `occurrences: 1` "
        "there means UNKNOWN, not once.",
        "",
    ]

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
