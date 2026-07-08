#!/usr/bin/env python3
"""One-time migration: reflow ## Completed rows that drifted past 4 columns
(root cause: cmd_done/cmd_clear in todo_write.py embedded a literal '|' inside
a Notes cell before the fmt_completed()/fmt_active() escaping fix landed
2026-07-08). No data is dropped -- every extra column's text is preserved,
joined into the single Notes cell with ' -- ' separators matching the
existing convention on already-clean rows.

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/migrate_todos_column_drift.py --repo-root .            # dry run (default)
    PYTHONIOENCODING=utf-8 python3 tools/migrate_todos_column_drift.py --repo-root . --apply     # write changes
"""
import argparse
import sys
from pathlib import Path


def parse_cols(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_data_row(line: str) -> bool:
    if not line.startswith("|"):
        return False
    if line.lstrip("|").strip().startswith("---"):
        return False
    if line.startswith("| Task") or line.startswith("| Company"):
        return False
    return True


def reflow(cols: list[str]) -> list[str]:
    """Collapse a drifted Completed row (>4 cols) to the canonical
    Task | Priority | Completed | Notes shape."""
    if len(cols) <= 4:
        return cols
    task, priority = cols[0], cols[1]
    rest = cols[2:]
    # Find the canonical completed-date cell: prefer one that starts with
    # "Completed " or "Withdrawn "; else the first bare YYYY-MM-DD.
    completed_idx = None
    for i, c in enumerate(rest):
        if c.startswith("Completed ") or c.startswith("Withdrawn "):
            completed_idx = i
            break
    if completed_idx is None:
        for i, c in enumerate(rest):
            if len(c) == 10 and c[4] == "-" and c[7] == "-":
                completed_idx = i
                break
    if completed_idx is None:
        completed_idx = 0  # fallback: first remaining cell is the date

    completed = rest[completed_idx]
    other = [c for i, c in enumerate(rest) if i != completed_idx and c not in ("—", "-", "")]
    notes = " — ".join(other) if other else "—"
    return [task, priority, completed, notes]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run, print diff only)")
    ap.add_argument("--limit", type=int, default=0, help="only show first N diffs in dry-run (0 = all)")
    args = ap.parse_args()

    path = Path(args.repo_root) / "data" / "job-todos.md"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    in_completed = False
    changed = 0
    shown = 0
    new_lines = []
    flagged = []
    for line in lines:
        if line.startswith("## Completed"):
            in_completed = True
            new_lines.append(line)
            continue
        if line.startswith("## ") and in_completed:
            in_completed = False

        if in_completed and is_data_row(line):
            cols = parse_cols(line)
            if len(cols) > 4:
                new_cols = reflow(cols)
                if new_cols[1] not in ("Low", "Med", "High"):
                    # Heuristic produced a suspicious Priority value -- this row's
                    # drift likely predates the join-bug pattern (e.g. a literal
                    # '|' inside the original task text). Skip auto-reflow;
                    # flag for manual fix instead of guessing.
                    flagged.append((line, cols))
                    new_lines.append(line)
                    continue
                new_line = "| " + " | ".join(new_cols) + " |\n"
                changed += 1
                if not args.apply and (args.limit == 0 or shown < args.limit):
                    print(f"--- was ({len(cols)} cols):")
                    print(line.rstrip("\n"))
                    print(f"+++ now (4 cols):")
                    print(new_line.rstrip("\n"))
                    print()
                    shown += 1
                new_lines.append(new_line)
                continue
        new_lines.append(line)

    print(f"\n{'APPLIED' if args.apply else 'DRY RUN'}: {changed} rows would be reflowed to 4 columns.")
    if flagged:
        print(f"\n{len(flagged)} row(s) SKIPPED (suspicious heuristic result, need manual fix):")
        for line, cols in flagged:
            print(f"  [{len(cols)} cols] {line.rstrip(chr(10))[:160]}...")

    if args.apply:
        path.write_text("".join(new_lines), encoding="utf-8")
        print(f"Written to {path}")
    else:
        print("Re-run with --apply to write changes. No file was modified.")


if __name__ == "__main__":
    sys.exit(main())
