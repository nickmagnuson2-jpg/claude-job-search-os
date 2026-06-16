#!/usr/bin/env python3
"""
my_world_synthesis.py — deterministic gating + atomic append for the /my-world
longitudinal three-axis synthesis (data/reflections/_longitudinal.md).

Subcommands:
  status [--threshold N]
      Report whether the deep synthesis pass should fire. Lists dated
      reflections, compares against the _longitudinal.md covered-through marker,
      returns JSON {should_synthesize, new_count, new_files, ...}.

  append --covered-through YYYY-MM-DD [--date YYYY-MM-DD] [--input PATH]
      Atomic newest-first prepend of a synthesis block (from --input or stdin)
      to _longitudinal.md; creates the file if missing; updates frontmatter
      markers.

Options (all subcommands):
  --repo-root PATH   Repository root. Defaults to cwd. MUST come before the subcommand.

Output: JSON to stdout.
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

REFLECTIONS_DIR = "data/reflections"
LONGITUDINAL_REL = "data/reflections/_longitudinal.md"
DEFAULT_THRESHOLD = 5
BASELINE_WINDOW = 12
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

HEADER = """---
voice: cloud-generated
last-synthesis: {last}
covered-through: {covered}
---

# Longitudinal Themes — /my-world Three-Axis Synthesis

> Claude's voice. Periodic cross-reflection pattern pass over data/reflections/.
> Three axes: thinking patterns / decision biases / communication tendencies. Newest first.
> Paired with the dated reflections (Nick's voice) and _themes.md (/reflect per-session synthesis).

---
"""


# --- I/O helpers (self-contained — mirror pipe_write/person_write) ---

def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def out_ok(action: str, **extra) -> None:
    d = {"status": "ok", "action": action}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))


def out_error(message: str, code: str = "error", **extra) -> None:
    d = {"status": "error", "message": message, "code": code}
    d.update(extra)
    print(json.dumps(d, ensure_ascii=False))
    sys.exit(1)


# --- core ---

def list_reflections(reflections_dir: Path) -> list:
    """Sorted [(date_str, Path)] for dated reflections. Excludes
    underscore-prefixed (_themes/_longitudinal/_template) and non-dated files."""
    if not reflections_dir.is_dir():
        return []
    out = []
    for p in reflections_dir.glob("*.md"):
        if p.name.startswith("_"):
            continue
        m = DATE_RE.match(p.name)
        if not m:
            continue
        out.append((m.group(1), p))
    out.sort(key=lambda t: t[1].name)
    return out


def read_covered_through(longitudinal_path: Path):
    """Parse 'covered-through:' from the frontmatter. None if absent/malformed."""
    content = read_file(longitudinal_path)
    if not content:
        return None
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"covered-through:\s*(\d{4}-\d{2}-\d{2})", line.strip())
        if m:
            return m.group(1)
    return None


def cmd_status(args, repo_root: Path) -> None:
    reflections = list_reflections(repo_root / REFLECTIONS_DIR)
    total = len(reflections)
    covered = read_covered_through(repo_root / LONGITUDINAL_REL)
    threshold = args.threshold

    if covered is None:
        is_baseline = True
        new = reflections[-BASELINE_WINDOW:] if total > BASELINE_WINDOW else reflections
        should = total >= threshold
    else:
        is_baseline = False
        new = [(d, p) for (d, p) in reflections if d > covered]
        should = len(new) >= threshold

    new_files = [str(p.relative_to(repo_root)) for (_, p) in new]
    newest = new[-1][0] if new else covered
    out_ok("status",
           should_synthesize=should,
           new_count=len(new),
           threshold=threshold,
           new_files=new_files,
           covered_through=covered,
           newest_reflection=newest,
           total_reflections=total,
           is_baseline=is_baseline)


def cmd_append(args, repo_root: Path) -> None:
    longitudinal = repo_root / LONGITUDINAL_REL
    covered_through = args.covered_through
    last = args.date or date.today().strftime("%Y-%m-%d")

    if args.input:
        block = read_file(Path(args.input))
    else:
        block = sys.stdin.read()
    block = block.strip("\n")
    if not block.strip():
        out_error("No synthesis content provided (--input or stdin was empty)", "bad_input")

    existing = read_file(longitudinal)

    # First run: create the file with frontmatter + header + first block.
    if not existing:
        content = HEADER.format(last=last, covered=covered_through) + "\n" + block + "\n"
        write_atomic(longitudinal, content)
        out_ok("append", path=str(longitudinal.relative_to(repo_root)),
               covered_through=covered_through, created=True)
        return

    lines = existing.splitlines()

    # Update frontmatter markers (within the first --- ... --- block).
    in_fm = False
    fm_end = 0
    for i, line in enumerate(lines):
        if line.strip() == "---":
            if not in_fm:
                in_fm = True
            else:
                fm_end = i
                break
    warning = None
    found = {"last-synthesis:": False, "covered-through:": False}
    if fm_end == 0:
        # Malformed/unclosed frontmatter: don't lose the synthesis, but signal
        # that markers could not be safely updated.
        warning = "malformed_frontmatter_markers_not_updated"
    else:
        for i in range(1, fm_end):
            if lines[i].startswith("last-synthesis:"):
                lines[i] = f"last-synthesis: {last}"
                found["last-synthesis:"] = True
            elif lines[i].startswith("covered-through:"):
                lines[i] = f"covered-through: {covered_through}"
                found["covered-through:"] = True
        # Insert any marker keys that were absent, just before the closing fence.
        inserts = []
        if not found["last-synthesis:"]:
            inserts.append(f"last-synthesis: {last}")
        if not found["covered-through:"]:
            inserts.append(f"covered-through: {covered_through}")
        if inserts:
            lines[fm_end:fm_end] = inserts
            fm_end += len(inserts)

    # Insert newest-first: before the first "## " entry; else after the intro divider.
    insert_at = None
    for i in range(fm_end + 1, len(lines)):
        if lines[i].startswith("## "):
            insert_at = i
            break
    if insert_at is None:
        for i in range(fm_end + 1, len(lines)):
            if lines[i].strip() == "---":
                insert_at = i + 1
                break
        if insert_at is None:
            insert_at = len(lines)

    block_lines = block.split("\n") + ["", "---", ""]
    # End-of-file fallback: separate the block from a non-blank last line.
    if insert_at == len(lines) and lines and lines[-1].strip() != "":
        block_lines = [""] + block_lines
    new_lines = lines[:insert_at] + block_lines + lines[insert_at:]
    content = "\n".join(new_lines)
    if existing.endswith("\n"):
        content += "\n"
    write_atomic(longitudinal, content)
    extra = {}
    if warning:
        extra["warning"] = warning
    out_ok("append", path=str(longitudinal.relative_to(repo_root)),
           covered_through=covered_through, created=False, **extra)


def parse_args():
    p = argparse.ArgumentParser(
        description="Gating + atomic append for /my-world longitudinal synthesis.")
    p.add_argument("--repo-root", default=None,
                   help="Repository root. Defaults to cwd. Before the subcommand.")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("status")
    s.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)

    a = sub.add_parser("append")
    a.add_argument("--covered-through", dest="covered_through", required=True)
    a.add_argument("--date", default=None)
    a.add_argument("--input", default=None)

    return p.parse_args()


def main():
    args = parse_args()
    if not args.command:
        out_error("Usage: my_world_synthesis.py <status|append> [args...]")
    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    if args.command == "status":
        cmd_status(args, repo_root)
    elif args.command == "append":
        cmd_append(args, repo_root)
    else:
        out_error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
