#!/usr/bin/env python3
"""title_cut_ruling.py - Nick's rulings on roles the career scan cut at fetch (the R1 log).

Funnel step 1 (data/workstreams/funnel-intake.md, "Start here", 2026-11-17). The nightly
scan drops roles on title (and a few on location) before scoring; every drop is already
recorded, with its reason, in the role queue's `cut_at_fetch` list (scanner.py,
fetch_company_roles). Nothing showed that list to Nick, so a wrong cut was never seen.
The morning page now lists every cut with a short key, and this tool records Nick's
"should have shown" ruling on one, with his reason, as one JSON line in a gitignored log.

The autonomy ladder (funnel-intake.md, "Autonomy ladder"): these rulings are the R1
disagreement log. A title class returns to silent auto-cut only after 20 consecutive cuts
with 0 corrections from Nick, so a ruling must be Nick's: `--by` defaults to nick, and a
session recording its own labels must pass `--by claude`.

Read-only on the queue: it is read directly and never quarantined or rewritten here.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/title_cut_ruling.py list [--grep TEXT]
  PYTHONIOENCODING=utf-8 python3 tools/title_cut_ruling.py mark --key KEY --reason "..."
  PYTHONIOENCODING=utf-8 python3 tools/title_cut_ruling.py log

Exit codes: 0 ok; 1 already ruled; 2 bad input (no queue, unknown key, empty reason).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.count_report import report  # noqa: E402
from tools.career_scanner.dedup import role_key  # noqa: E402
from tools.career_scanner.scanner import role_queue_path  # noqa: E402
from tools.inbox_lock import file_lock  # noqa: E402

RULINGS_REL = Path("data") / "calibration" / "title-cut-rulings.jsonl"
KEY_LEN = 8
RULING = "should_have_shown"


def cut_key(cut: dict) -> str:
    """Short, stable key for one cut role: the first 8 hex of sha1(role_key).

    role_key is the posting URL (or company::title without one), the same identity the
    scanner dedups on, so a posting with a URL keeps one key across nights. A posting with
    no URL is keyed by company and title, so a retitled one gets a new key: the scanner's
    own identity rule (dedup.role_key), kept rather than redefined here."""
    return hashlib.sha1(role_key(cut).encode("utf-8")).hexdigest()[:KEY_LEN]


def load_cuts(root: Path) -> dict:
    """{"error", "scanned_at", "cuts"}. Never raises: the morning page calls this."""
    path = role_queue_path(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"error": f"no role queue at {path}; has the scan run?",
                "scanned_at": None, "cuts": None}
    except (OSError, ValueError) as e:
        return {"error": f"unreadable role queue: {e}", "scanned_at": None, "cuts": None}
    if not isinstance(payload, dict) or not isinstance(payload.get("cut_at_fetch"), list):
        return {"error": "role queue has no cut_at_fetch list", "scanned_at": None,
                "cuts": None}
    raw = payload["cut_at_fetch"]
    # A corrupt queue is an error, never a shorter list: a partial list reads as the whole
    # scan on the page, and funnel_state would record it as that night's cuts (parked P1
    # from the funnel-page-1118 tip review, Codex F1 + Grok F2; fixed 2026-11-21).
    bad = sum(1 for c in raw if not isinstance(c, dict))
    if bad:
        return {"error": f"corrupt role queue: {bad} of {len(raw)} cut_at_fetch entries "
                         "are not an object", "scanned_at": None, "cuts": None}
    if "cut_at_fetch_count" in payload:  # absent on queues written before the count existed
        n = payload["cut_at_fetch_count"]
        if not isinstance(n, int) or isinstance(n, bool):
            return {"error": "corrupt role queue: cut_at_fetch_count is not an integer "
                             f"({n!r})", "scanned_at": None, "cuts": None}
        if n != len(raw):
            return {"error": f"corrupt role queue: cut_at_fetch_count is {n} but the list "
                             f"lists {len(raw)}", "scanned_at": None, "cuts": None}
    cuts = []
    for c in raw:
        # A field that is not a string (null, a number) would crash role_key and the
        # page; it is read as text instead (Grok F2, 2026-11-18).
        c = {**c, **{f: "" if c.get(f) is None else str(c[f])
                     for f in ("company", "title", "location", "url", "rule", "reason")}}
        cuts.append({**c, "key": cut_key(c), "role_key": role_key(c)})
    ff = payload.get("fetch_failures")
    return {"error": None, "scanned_at": payload.get("scanned_at"), "cuts": cuts,
            "fetch_failures": ff if isinstance(ff, int) and not isinstance(ff, bool) else 0}


def rulings_path(root: Path) -> Path:
    return Path(root) / RULINGS_REL


def load_rulings(root: Path) -> dict:
    """{"error", "rulings", "bad_lines"}. A line that is not a JSON object is counted,
    never silently dropped and never fatal."""
    path = rulings_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"error": None, "rulings": [], "bad_lines": 0}
    except (OSError, ValueError) as e:
        return {"error": f"unreadable rulings log: {e}", "rulings": None, "bad_lines": 0}
    rulings, bad = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(rec, dict) and rec.get("key"):
            rulings.append(rec)
        else:
            bad += 1
    return {"error": None, "rulings": rulings, "bad_lines": bad}


def find_cut(cuts: list[dict], key: str) -> list[dict]:
    key = key.strip().lower()
    return [c for c in cuts if c["key"] == key] if key else []


def mark(root: Path, key: str, reason: str, by: str = "nick",
         now: datetime | None = None) -> tuple[int, dict]:
    """Append one ruling. Returns (exit_code, result)."""
    reason = (reason or "").strip()
    if not reason:
        return 2, {"error": "a ruling needs a reason (--reason \"...\")"}
    loaded = load_cuts(root)
    if loaded["error"]:
        return 2, {"error": loaded["error"]}
    hits = find_cut(loaded["cuts"], key)
    if not hits:
        return 2, {"error": f"no cut with key {key!r} in the scan of {loaded['scanned_at']}; "
                            "run `list` to see the keys"}
    if len({role_key(h) for h in hits}) > 1:  # a key prefix two postings share (Codex F5)
        return 2, {"error": f"key {key!r} matches {len(hits)} different postings; "
                            "no ruling written. Tell Claude: the key needs lengthening (KEY_LEN)"}
    cut = hits[0]
    path = rulings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Check and append under one lock, so two marks of one role cannot both write
    # (Codex/Grok/Fable F3, round 2).
    with file_lock(path):
        return _check_and_append(root, cut, loaded["scanned_at"], reason, by, now)


def _check_and_append(root: Path, cut: dict, scanned_at, reason: str, by: str,
                      now: datetime | None) -> tuple[int, dict]:
    prior = load_rulings(root)
    if prior["error"]:
        return 2, {"error": prior["error"]}
    for r in prior["rulings"]:
        # Per author: a label Claude recorded must never block Nick's own ruling (Grok F3).
        if r.get("role_key") == role_key(cut) and (r.get("by") or "nick") == by:
            return 1, {"error": f"already ruled on {r.get('ruled_at')}: {r.get('reason')}",
                       "ruling": r}
    rec = {
        "ruled_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "key": cut["key"], "role_key": role_key(cut), "ruling": RULING,
        "reason": reason, "by": by,
        "company": cut.get("company", ""), "title": cut.get("title", ""),
        "location": cut.get("location", ""), "url": cut.get("url", ""),
        "rule": cut.get("rule", ""), "cut_reason": cut.get("reason", ""),
        "scan_at": scanned_at,
    }
    with open(rulings_path(root), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return 0, {"logged": rec, "log": str(RULINGS_REL)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="every cut in the latest scan, with its key")
    ls.add_argument("--grep", default="", help="only cuts whose company or title has TEXT")
    mk = sub.add_parser("mark", help="rule one cut 'should have shown'")
    mk.add_argument("--key", required=True)
    mk.add_argument("--reason", required=True)
    mk.add_argument("--by", default="nick", choices=("nick", "claude"))
    sub.add_parser("log", help="print the rulings log")
    a = ap.parse_args(argv)
    root = Path(a.repo_root)

    if a.cmd == "mark":
        code, out = mark(root, a.key, a.reason, a.by)
        print(json.dumps(out, indent=2, ensure_ascii=False),
              file=sys.stdout if code == 0 else sys.stderr)
        return code
    if a.cmd == "log":
        loaded = load_rulings(root)
        if loaded["error"]:
            print(loaded["error"], file=sys.stderr)
            return 2
        print(json.dumps(loaded, indent=2, ensure_ascii=False))
        return 0
    loaded = load_cuts(root)
    if loaded["error"]:
        print(loaded["error"], file=sys.stderr)
        return 2
    g = a.grep.casefold()
    shown = [c for c in loaded["cuts"]
             if g in f"{c.get('company', '')} {c.get('title', '')}".casefold()]
    report(f"cut_at_fetch in the role queue, scan {loaded['scanned_at']}",
           population=len(loaded["cuts"]), included=len(shown),
           excluded={"not matching --grep": len(loaded["cuts"]) - len(shown)})
    for c in shown:
        print(f"{c['key']}  {c.get('company', '')}: {c.get('title', '')}  ({c.get('reason', '')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
