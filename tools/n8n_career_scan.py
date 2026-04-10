#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
n8n_career_scan.py - Run career page scanner on schedule via n8n.

Triggered by n8n Execute Command node. Runs the career scanner CLI,
which fetches roles from all active targets in scan-targets.yaml,
scores them, deduplicates against pipeline, and writes new matches
to data/inbox.md.

Usage:
  PYTHONIOENCODING=utf-8 python tools/n8n_career_scan.py [--repo-root PATH]

n8n Execute Command config:
  Command: PYTHONIOENCODING=utf-8 python tools/n8n_career_scan.py --repo-root /path/to/repo
  Schedule: Daily at 7am (cron: 0 7 * * *)
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=None)
    args = p.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).parent.parent

    # Verify scan-targets.yaml exists
    targets_path = repo_root / "data" / "scan-targets.yaml"
    if not targets_path.exists():
        print(f"No scan-targets.yaml at {targets_path} - skipping scan.", file=sys.stderr)
        print(json.dumps({"skipped": True, "reason": "no scan-targets.yaml"}))
        sys.exit(0)

    # Run scanner CLI via subprocess
    try:
        result = subprocess.run(
            [sys.executable, str(repo_root / "tools" / "career_scanner" / "cli.py"),
             "--repo-root", str(repo_root)],
            capture_output=True, text=True, encoding="utf-8",
            timeout=300,  # 5 minute timeout for scanning all companies
        )
    except subprocess.TimeoutExpired:
        print("Scanner timed out after 300 seconds", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(f"Scanner failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Forward scanner JSON output
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"Invalid scanner output: {result.stdout[:200]}", file=sys.stderr)
        sys.exit(1)

    new_roles = data.get("new_roles", 0)
    if new_roles == 0:
        print("No new roles found.", file=sys.stderr)

    # Print summary for n8n
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
