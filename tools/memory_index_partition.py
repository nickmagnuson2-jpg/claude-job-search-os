#!/usr/bin/env python3
"""Partition the auto-memory directory for the two-tier index restructure.

Reads every memory file, buckets by type prefix, pulls mtime + frontmatter
description, reuses the existing MEMORY.md one-liner where present, and flags
promotion status by grepping each feedback file for promotion markers.

Output: JSON to stdout. Non-destructive (read-only).

Run: PYTHONIOENCODING=utf-8 python3 tools/memory_index_partition.py [--memory-dir DIR]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

DEFAULT_DIR = os.path.expanduser(
    "~/.claude/projects/-Users-mag-Documents-Obsidian-30-projects-job-search/memory"
)

# Files that are NOT memory entries (skip from partition)
SKIP_EXACT = {"MEMORY.md"}
SKIP_PREFIX = ("audit-", "archive-", "index-", "MEMORY.backup", ".tmp")

TYPE_PREFIXES = ("feedback_", "project_", "reference_", "user_")

# Recency cutoff: "last ~14 days" from 2026-06-04 -> 2026-05-21
RECENT_CUTOFF = "2026-05-21"

# The authoritative promotion signal is the "**Current tier:**" line value.
TIER_RE = re.compile(r"^\s*-?\s*\*{0,2}Current tier:\*{0,2}\s*(.+)$", re.MULTILINE)


def classify_tier(text):
    """Return (tier_value, tier_class).

    tier_class in:
      promoted          -> hook/script/skill enforce it (demote from hot set)
      judgment          -> behavioral but explicitly un-promotable (keep/consolidate)
      promotable        -> plain behavioral; candidate for the promotion sweep
      unclassified      -> no Current tier line (older convention)
    """
    m = TIER_RE.search(text)
    if not m:
        return ("", "unclassified")
    val = m.group(1).strip()
    low = val.lower()
    if re.search(r"\b(hook|script|skill)\b", low):
        return (val, "promoted")
    if "judgment" in low or "no structural gate" in low or "intentional" in low:
        return (val, "judgment")
    if "behavioral" in low:
        return (val, "promotable")
    return (val, "unclassified")

# REOPEN gate / promotion-criterion signal for non-promoted triage
GATE_RE = re.compile(r"(REOPEN gate?:|Promotion criterion|Tier ladder:|2nd fire|2nd occurrence|If .* overflows)", re.IGNORECASE)


def get_type(name):
    for p in TYPE_PREFIXES:
        if name.startswith(p):
            return p.rstrip("_")
    return "other"


def parse_description(text):
    """Pull the frontmatter description: field (may be quoted, single-line)."""
    m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    if not m:
        return ""
    desc = m.group(1).strip().strip('"').strip("'")
    return desc[:300]


def extract_gate(text):
    """Return the first REOPEN/promotion-criterion-bearing line for triage."""
    for line in text.splitlines():
        if GATE_RE.search(line):
            return line.strip()[:400]
    return ""


def build_index_map(memory_dir):
    """filename -> existing curated one-liner from MEMORY.md (without leading '- ')."""
    path = os.path.join(memory_dir, "MEMORY.md")
    mapping = {}
    if not os.path.exists(path):
        return mapping
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.search(r"\]\(([^)]+\.md)\)", line)
            if m:
                fn = os.path.basename(m.group(1))
                mapping[fn] = line.rstrip("\n")
    return mapping


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-dir", default=DEFAULT_DIR)
    args = ap.parse_args()
    mdir = args.memory_dir

    index_map = build_index_map(mdir)

    entries = []
    for name in sorted(os.listdir(mdir)):
        if not name.endswith(".md"):
            continue
        if name in SKIP_EXACT or name.startswith(SKIP_PREFIX):
            continue
        full = os.path.join(mdir, name)
        if not os.path.isfile(full):
            continue
        with open(full, encoding="utf-8") as f:
            text = f.read()
        mtime = datetime.fromtimestamp(os.path.getmtime(full), tz=timezone.utc)
        mdate = mtime.strftime("%Y-%m-%d")
        typ = get_type(name)
        if typ == "feedback":
            tier_val, tier_class = classify_tier(text)
        else:
            tier_val, tier_class = ("", None)
        entries.append({
            "file": name,
            "type": typ,
            "mtime": mdate,
            "recent": mdate >= RECENT_CUTOFF,
            "indexed": name in index_map,
            "tier": tier_val,
            "tier_class": tier_class,
            "gate": extract_gate(text) if tier_class in ("promotable", "unclassified") else "",
            "description": parse_description(text),
            "existing_line": index_map.get(name, ""),
        })

    # Summary
    by_type = {}
    for e in entries:
        by_type.setdefault(e["type"], {"total": 0, "recent": 0, "unindexed": 0,
                                       "promoted": 0, "judgment": 0,
                                       "promotable": 0, "unclassified": 0})
        b = by_type[e["type"]]
        b["total"] += 1
        if e["recent"]:
            b["recent"] += 1
        if not e["indexed"]:
            b["unindexed"] += 1
        if e["type"] == "feedback":
            b[e["tier_class"]] += 1

    out = {
        "memory_dir": mdir,
        "recent_cutoff": RECENT_CUTOFF,
        "total_files": len(entries),
        "by_type": by_type,
        "entries": entries,
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
