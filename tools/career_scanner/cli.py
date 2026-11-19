#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli.py - CLI entrypoint for career scanner.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/career_scanner/cli.py
  PYTHONIOENCODING=utf-8 python3 tools/career_scanner/cli.py --dry-run
  PYTHONIOENCODING=utf-8 python3 tools/career_scanner/cli.py --repo-root /path/to/repo
  PYTHONIOENCODING=utf-8 python3 tools/career_scanner/cli.py --once-a-day   (launchd)

Output: JSON summary to stdout. Status messages to stderr.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Repo root on sys.path so `tools.career_scanner.*` imports resolve when this
# script is invoked directly (python3 tools/career_scanner/cli.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# How far ahead of `now` a scanned_at may sit and still count: clock adjustments between
# two runs on one machine are seconds, not minutes.
_CLOCK_SLACK = timedelta(minutes=5)


def scanned_today(repo_root: Path, now: datetime | None = None) -> str | None:
    """Why today's scan is already done, or None when a scan should run.

    Done means the role queue was written today (local date) by a scan with zero fetch
    failures. A scan with any failure does not count, so a catch-up slot still runs
    after, say, a 06:30 scan that started before the network was up. Anything the guard
    cannot read means "scan": a missed skip costs one extra scan, a wrong skip costs a day.
    A scanned_at in the future is unusable too (a hand edit or a clock change).

    `now` defaults to naive local wall time. A naive `now` is read with the system's
    time-zone RULES, not one fixed offset: datetime.now().astimezone() carries the offset
    in force now, so on the fall-back day a 00:30 PDT scan read with the 09:00 PST offset
    lands on the previous date (Codex r1 F1).

    Reads the queue file directly and never writes: scanner.read_queue_payload
    quarantines a corrupt queue, which is the scan's job, not the guard's.
    """
    from tools.career_scanner.scanner import role_queue_path

    now = now or datetime.now()
    try:
        payload = json.loads(role_queue_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    stamp, failures = payload.get("scanned_at"), payload.get("fetch_failures")
    # `new` too: /standup's reader refuses a queue without it (role_queue_read.py), and a
    # queue the reader refuses must not stop the catch-up scan that would rewrite it
    # (Grok r1 F1).
    if (not isinstance(stamp, str) or type(failures) is not int
            or not isinstance(payload.get("new"), list)):
        return None
    try:
        scanned = datetime.fromisoformat(stamp)
        if scanned.tzinfo is None:
            # The writer stamps UTC (scanner._queue_payload).
            scanned = scanned.replace(tzinfo=timezone.utc)
        # astimezone(None) converts each instant with the system rules in force at it.
        local = scanned.astimezone(now.tzinfo)
        future = scanned > now.astimezone() + _CLOCK_SLACK   # Codex r1 F2
    except (ValueError, OverflowError):
        # Parses but cannot be placed in local time (year 1, year 9999): Codex r2 F1.
        return None
    if future:
        return None
    if local.date() != now.date() or failures != 0:
        return None
    return f"queue already written today ({local.isoformat(timespec='minutes')}, 0 fetch failures)"


def main():
    p = argparse.ArgumentParser(
        description="Scan career pages for matching roles"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and score but don't write to the role queue",
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
    p.add_argument(
        "--once-a-day",
        action="store_true",
        help=("Skip the scan when today's queue was already written by a scan with no "
              "fetch failures. launchd passes it so the 09:00-12:00 catch-up slots run "
              "only on a day the early scan missed or failed (Nick, R11, 2026-11-18). "
              "Manual and --dry-run scans ignore it."),
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

    if args.once_a_day and not args.dry_run:
        done = scanned_today(repo_root)
        if done:
            print(json.dumps({"status": "skipped", "reason": done}))
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
            # 362.F3: `new_roles` counts every open role not in the pipeline. Whether a
            # role is new since the last scan is this flag, never that count.
            "new": r.get("new", False),
            "first_seen": r.get("first_seen", ""),
            "previously_closed": r.get("previously_closed", ""),
            "history": r.get("history", ""),
        }
        # Every role, not the first ten: /scan-companies shows all roles (D-05;
        # 614.F2/625.F5).
        for r in result.get("roles", [])
    ]
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # sys.exit(main()), not a bare main(): --score-roles returns 2 on unreadable stdin,
    # and a discarded return value would surface that refusal as exit 0.
    sys.exit(main())
