#!/usr/bin/env python3
"""role_queue_read.py - read the career-scan role queue for /standup.

THE DRAIN. From 2026-08-11 to 2026-09-02 the career scanner ran daily, scored ~30
roles a night, and wrote them to data/inbox.md -- which nothing read. /standup's
"Career-scan matches" section globbed the inbox/ DIRECTORY for `*career-scan*` files
that have never existed, so the section was permanently empty. The producer was
healthy and the consumer pointed somewhere else. This tool is the consumer.

Reads the queue written by career_scanner.scanner.write_role_queue. The path comes
from scanner.role_queue_path() rather than being re-declared here, so producer and
consumer cannot drift apart. Pinned by tests/scripts/test_role_queue_path_contract.py.

Degrades gracefully by design: /standup must never fail on this. A missing queue
reports `exists: false` with a reason rather than raising, and a stale queue is
reported as stale rather than presented as today's news.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner.scanner import (  # noqa: E402
    _queue_lock, read_pending, role_queue_path, read_queue_payload,
    write_queue_payload)
from tools.career_scanner.dedup import role_key  # noqa: E402

# A daily job that has not produced a queue in this long is not "quiet", it is broken.
STALE_AFTER_HOURS = 36


def read_queue(repo_root: Path, top: int = 5) -> dict:
    path = role_queue_path(repo_root)
    if not path.is_file():
        return {"exists": False, "reason": f"no queue at {path}; has the scan run?",
                "new_count": None, "roles": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": False, "reason": f"unreadable queue: {exc}",
                "new_count": None, "roles": []}

    stale_hours = None
    scanned_at = payload.get("scanned_at")
    if scanned_at:
        try:
            ts = datetime.fromisoformat(scanned_at)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            stale_hours = round(
                (datetime.now(timezone.utc) - ts).total_seconds() / 3600, 1)
        except ValueError:
            stale_hours = None

    new = payload.get("new", [])
    if not isinstance(new, list):
        new = []
    new = [r for r in new if isinstance(r, dict)]
    # Nick's stated ordering, 2026-09-02: "ranked by fit, and then secondarily by when
    # they were published." Score first, recency as the tiebreaker. Sorting by date
    # alone floated a 3/10 SEO role above every in-lane role on the first live run.
    new.sort(key=lambda r: (r.get("score") or 0, r.get("published_at") or ""),
             reverse=True)

    return {
        "exists": True,
        "scanned_at": scanned_at,
        "stale_hours": stale_hours,
        "is_stale": stale_hours is not None and stale_hours > STALE_AFTER_HOURS,
        "new_count": payload.get("new_count", len(new)),
        "standing_count": payload.get("standing_count"),
        # Surfaced so a scan that examined nothing cannot read as a scan that found
        # nothing -- the false-zero defect.
        "fetch_failures": payload.get("fetch_failures", 0),
        "fetch_failure_detail": payload.get("fetch_failure_detail", []),
        "pending_overflow": bool(payload.get("pending_overflow")),
        "roles": new[:top],
        # The reader acknowledges BY KEY, never by count. Acking "the top 5" would
        # silently consume whatever a scan inserted between the read and the ack.
        "ack_keys": [role_key(r) for r in new[:top]],
    }


def acknowledge(repo_root: Path, keys: list[str] | None = None) -> dict:
    """Clear acknowledged roles from the pending log. THE ONLY consuming operation.

    Reading does not consume: if /standup dies mid-render the roles must still be
    there tomorrow. Only an explicit ack -- issued after the roles have actually been
    put in front of Nick -- removes them.

    Args:
        keys: role_key values to clear, normally the `ack_keys` from the read that
            rendered them. None clears the entire pending log; use it only when the
            whole log was rendered, because anything a concurrent scan appended in the
            meantime is cleared unread.

    Returns:
        {"acknowledged": N, "remaining": M}. A missing queue acks nothing and does not
        raise -- /standup must never fail on this.
    """
    # Under the same lock the writer takes: ack is a read-modify-write on the same
    # file, and an unlocked one would drop roles a concurrent scan had just appended.
    with _queue_lock(repo_root):
        payload = read_queue_payload(repo_root)
        if not payload:
            return {"acknowledged": 0, "remaining": 0,
                    "reason": "no readable queue to acknowledge"}

        pending = [r for r in payload.get("new", []) if isinstance(r, dict)] \
            if isinstance(payload.get("new"), list) else []
        if keys is None:
            kept = []
        else:
            wanted = set(keys)
            kept = [r for r in pending if role_key(r) not in wanted]

        payload["new"] = kept
        payload["new_count"] = len(kept)
        payload["pending_overflow"] = False
        payload["acknowledged_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds")
        write_queue_payload(repo_root, payload)
    return {"acknowledged": len(pending) - len(kept), "remaining": len(kept)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--ack", action="append", metavar="KEY", default=[],
                    help="acknowledge one rendered role by its ack_keys value; "
                         "repeatable. Run AFTER the roles have been rendered.")
    ap.add_argument("--ack-all", action="store_true",
                    help="acknowledge the entire pending log. Only safe when the whole "
                         "log was rendered -- prefer --ack with explicit keys.")
    args = ap.parse_args()
    root = Path(args.repo_root)
    if args.ack or args.ack_all:
        result = acknowledge(root, None if args.ack_all else args.ack)
    else:
        result = read_queue(root, args.top)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
