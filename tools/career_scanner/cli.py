#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli.py - CLI entrypoint for career scanner.

Usage:
  PYTHONIOENCODING=utf-8 python tools/career_scanner/cli.py
  PYTHONIOENCODING=utf-8 python tools/career_scanner/cli.py --dry-run
  PYTHONIOENCODING=utf-8 python tools/career_scanner/cli.py --repo-root /path/to/repo

Output: JSON summary to stdout. Status messages to stderr.
"""
import argparse
import json
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(
        description="Scan career pages for matching roles"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and score but don't write to inbox",
    )
    p.add_argument(
        "--repo-root",
        default=None,
        help="Repository root path (default: auto-detect from script location)",
    )
    args = p.parse_args()

    # Resolve repo root: explicit arg, or two levels up from this script
    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        repo_root = Path(__file__).resolve().parent.parent.parent

    from tools.career_scanner.scanner import scan_all_targets

    result = scan_all_targets(repo_root, dry_run=args.dry_run)

    # Print summary to stdout as JSON (without full role list)
    output = {k: v for k, v in result.items() if k != "roles"}
    output["top_roles"] = [
        {
            "title": r.get("title", ""),
            "company": r.get("company", ""),
            "score": r.get("score", 0),
            "url": r.get("url", ""),
        }
        for r in result.get("roles", [])[:10]
    ]
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
