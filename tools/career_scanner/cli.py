#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli.py - CLI entrypoint for career scanner.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/career_scanner/cli.py
  PYTHONIOENCODING=utf-8 python3 tools/career_scanner/cli.py --dry-run
  PYTHONIOENCODING=utf-8 python3 tools/career_scanner/cli.py --repo-root /path/to/repo

Output: JSON summary to stdout. Status messages to stderr.
"""
import argparse
import json
import sys
from pathlib import Path

# Repo root on sys.path so `tools.career_scanner.*` imports resolve when this
# script is invoked directly (python3 tools/career_scanner/cli.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


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
    p.add_argument(
        "--score-roles",
        action="store_true",
        help=("Read a JSON array of role dicts on stdin and print their scores. "
              "Exists so /scan-jobs reaches the SAME scorer scanner.py uses instead "
              "of re-deriving fit from a prose rubric. Thin wrapper by design."),
    )
    args = p.parse_args()

    # Resolve repo root: explicit arg, or two levels up from this script
    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        repo_root = Path(__file__).resolve().parent.parent.parent

    if args.score_roles:
        # Refuse unreadable input rather than emitting a confident number. A scorer
        # that falls through on junk is worse than one that is absent, because the
        # caller cannot tell a real 3 from a parse failure.
        raw = sys.stdin.read()
        try:
            roles = json.loads(raw)
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "error", "code": "bad_json",
                              "message": f"stdin is not valid JSON: {e}"}))
            return 2
        if not isinstance(roles, list):
            print(json.dumps({"status": "error", "code": "not_a_list",
                              "message": "expected a JSON array of role objects"}))
            return 2

        bad = [i for i, r in enumerate(roles) if not isinstance(r, dict)]
        if bad:
            print(json.dumps({"status": "error", "code": "non_object_element",
                              "message": f"elements at {bad[:10]} are not JSON objects",
                              "count": len(bad)}))
            return 2

        from tools.career_scanner.scorer import load_scoring_context, score_role
        ctx = load_scoring_context(repo_root)
        out = [{"title": r.get("title", ""), "company": r.get("company", ""),
                "score": score_role(r, ctx)} for r in roles]
        print(json.dumps({"status": "ok", "n": len(out), "roles": out}))
        return 0

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
    # sys.exit(main()), not a bare main(): --score-roles returns 2 on unreadable stdin,
    # and a discarded return value would surface that refusal as exit 0.
    sys.exit(main())
