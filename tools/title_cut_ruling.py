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

Acceptances (L2b, 2026-11-24). A class can climb only if agreement is recorded too, so
`accept --scan` records Nick's EXPLICIT acceptance of the cuts the morning page put in front
of him for the current scan. Nick, NEEDS-NICK 10, 2026-11-24: "a title cut ... that I saw
but didn't correct. Does not count as accepted. I need an explicit acceptance." So nothing
here infers agreement from silence: a cut counts toward the ladder only through a line in
one of the two logs. Acceptances live in their own log, written only by this tool, because
the morning page reads every line of the rulings log as "should have shown".
`ladder` counts both logs with decision_write.ladder(), the one trust-ladder function.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/title_cut_ruling.py list [--grep TEXT]
  PYTHONIOENCODING=utf-8 python3 tools/title_cut_ruling.py mark --key KEY --reason "..."
  PYTHONIOENCODING=utf-8 python3 tools/title_cut_ruling.py accept --scan SCANNED_AT
  PYTHONIOENCODING=utf-8 python3 tools/title_cut_ruling.py ladder
  PYTHONIOENCODING=utf-8 python3 tools/title_cut_ruling.py log

Exit codes: 0 ok; 1 already ruled, or the scan already accepted; 2 bad input (no queue,
unknown key, empty reason, a scan that is not the current one or not yet shown by the page,
or a scan-targets.yaml changed since the page showed it).
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
from tools.career_scanner.scanner import cut_list_problem, role_queue_path  # noqa: E402
from tools.inbox_lock import file_lock  # noqa: E402

RULINGS_REL = Path("data") / "calibration" / "title-cut-rulings.jsonl"
ACCEPTS_REL = Path("data") / "calibration" / "title-cut-accepts.jsonl"
KEY_LEN = 8
RULING = "should_have_shown"
ACCEPTED = "accepted"
RECEIPT = "scan_accepted"   # one per accept run, even when it appends no cut (Codex r1 F2)


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
    # An absent list is this reader's own policy; every present-but-corrupt list, including
    # one that is not a list at all, goes through the shared check (Codex c2 F1).
    if not isinstance(payload, dict) or "cut_at_fetch" not in payload:
        return {"error": "role queue has no cut_at_fetch list", "scanned_at": None,
                "cuts": None}
    raw = payload["cut_at_fetch"]
    # A corrupt queue is an error, never a shorter list: a partial list reads as the whole
    # scan on the page, and funnel_state would record it as that night's cuts.
    problem = cut_list_problem(payload)
    if problem:
        return {"error": f"corrupt role queue: {problem}", "scanned_at": None, "cuts": None}
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


def accepts_path(root: Path) -> Path:
    return Path(root) / ACCEPTS_REL


def load_rulings(root: Path) -> dict:
    """{"error", "rulings", "bad_lines"}. A line that is not a JSON object is counted,
    never silently dropped and never fatal."""
    return _read_log(rulings_path(root))


def load_accepts(root: Path) -> dict:
    """The acceptances log, same shape and rules as load_rulings ("rulings" holds the lines)."""
    return _read_log(accepts_path(root))


def _read_log(path: Path) -> dict:
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


def resolve_scan(store: dict, scan: str) -> tuple[str | None, str | None]:
    """(scanned_at, None) for `scan` given as the store's ISO key or as the label the morning
    page prints for it (morning_page._when, e.g. "Tue Nov 24, 3:00 PM"), else (None, why).
    The page's own formatter is imported, never copied, so the two cannot drift."""
    from tools.morning_page import _when  # lazy, read-only use of the page's label
    scan = (scan or "").strip()
    if scan in store:
        return scan, None
    hits = [k for k in store if _when(k) == scan]
    if len(hits) == 1:
        return hits[0], None
    valid = "; ".join(f"{k} ({_when(k)})" for k in sorted(store)) or "none"
    if hits:
        return None, f"--scan {scan!r} names {len(hits)} scans; pass the ISO value. Scans: {valid}"
    return None, f"--scan {scan!r} is not a scan in the cut-scan store. Scans: {valid}"


def shown_cuts(root: Path, scan: str) -> tuple[int, dict]:
    """The cuts the morning page put in front of Nick for `scan`: its two-minute list, cuts
    to_review and new against the stored scan before it (morning_page.py "to_look"), decided
    by funnel_state's own functions, never re-derived here.

    The CURRENT scan only (b7, 2026-11-24, after Codex r1 F1): the store keeps role keys, not
    the list the page showed, so an older scan's list could only be rebuilt from today's queue
    and today's scan-targets, and could then include a cut Nick was never shown. Nick's rule
    is an explicit acceptance of what he saw, so that is refused. For the same reason, a
    scan-targets.yaml changed since the page recorded the scan is refused: to_review would be
    judged against a config the page did not use.
    (0, {"scanned_at", "cuts"}) or (2, {"error"}). Read-only: it reads the queue, the store
    and scan-targets once each and writes nothing."""
    from tools import funnel_state as fs  # lazy: funnel_state imports this module
    from tools.morning_page import _when
    root = Path(root)
    loaded = load_cuts(root)
    if loaded["error"]:
        return 2, {"error": loaded["error"]}
    store_path = fs.scans_path(root)
    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
        recorded = store_path.stat().st_mtime
    except FileNotFoundError:
        store, recorded = {}, None
    except (OSError, ValueError) as e:
        return 2, {"error": f"cut-scan store unreadable: {e}"}
    if not isinstance(store, dict):
        return 2, {"error": "cut-scan store is not an object of scans"}
    scan, why = resolve_scan(store, scan)
    if why:
        return 2, {"error": why + " (the morning page records a scan when it shows it)"}
    current = loaded["scanned_at"]
    if scan != current:
        return 2, {"error": f"a newer scan has replaced that list: {current} ({_when(current)}). "
                            "Re-read the page and accept that one; only the list on the page "
                            "now can be accepted"}
    targets = root / "data" / "scan-targets.yaml"
    try:
        changed = recorded is not None and targets.stat().st_mtime > recorded
    except OSError:
        changed = False                     # a missing file is refused by load_companies below
    if changed:
        return 2, {"error": "data/scan-targets.yaml changed after the page showed this scan, so "
                            "its list cannot be rebuilt as shown; accept the next scan's list"}
    # The previous scan comes from the snapshot already read, never from record_scan: that is
    # the store's writer, and a page render pruning between two reads would make it write
    # (Codex r1 F4). Same rule as record_scan: the latest stored scan before this one.
    earlier = sorted(k for k in store if k < scan)
    if not earlier:
        # The page compared this scan against one since pruned (KEEP_SCANS), or there was
        # none; "new" would read as every cut (L2b live smoke: 173 for the oldest stored scan).
        return 2, {"error": f"the scan of {scan} has no earlier scan in the cut-scan store, so "
                            "its list of new cuts cannot be rebuilt"}
    previous = set(store[earlier[-1]])
    companies, err = fs.load_companies(root)
    if err:
        return 2, {"error": err}
    by_name = {fs.normalize_name(str(c.get("name", ""))): c
               for c in companies if isinstance(c, dict) and c.get("name")}
    shown = [c for c in loaded["cuts"] if c["role_key"] not in previous
             and fs.to_review(c, by_name.get(fs.normalize_name(str(c.get("company", "")))))]
    return 0, {"scanned_at": scan, "cuts": shown}


def accept(root: Path, scan: str, by: str = "nick",
           now: datetime | None = None) -> tuple[int, dict]:
    """Record Nick's explicit acceptance of every cut on the page's list for `scan`, except
    the ones he ruled "should have shown" (in any scan) and the ones already accepted. This
    command is the only way a cut counts as agreed: a cut that is shown and never accepted
    counts for nothing (Nick, NEEDS-NICK 10, 2026-11-24). A scan is accepted once: a second
    accept of it exits 1."""
    code, got = shown_cuts(root, scan)
    if code:
        return code, got
    scan = got["scanned_at"]
    path = accepts_path(root)  # beside the cut-scan store, which shown_cuts found: the dir exists
    with file_lock(path):
        rulings, accepts = load_rulings(root), load_accepts(root)
        for log in (rulings, accepts):
            if log["error"]:
                return 2, {"error": log["error"]}
        mine = [r for r in accepts["rulings"] if (r.get("by") or "nick") == by]
        # The receipt, not the cut lines, says a scan was accepted: a run that accepted no cut
        # still writes one, so a second accept of that scan is refused too.
        if any(r.get("scan_at") == scan for r in mine):
            return 1, {"error": f"the scan of {scan} was already accepted"}
        corrected = {r.get("role_key") for r in rulings["rulings"] if (r.get("by") or "nick") == by}
        done = {r.get("role_key") for r in mine if r.get("ruling") == ACCEPTED}
        at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        new = [c for c in got["cuts"] if c["role_key"] not in corrected | done]
        with open(path, "a", encoding="utf-8") as f:
            for c in new:
                f.write(json.dumps({
                    "accepted_at": at, "key": c["key"], "role_key": c["role_key"],
                    "ruling": ACCEPTED, "by": by, "company": c.get("company", ""),
                    "title": c.get("title", ""), "rule": c.get("rule", ""),
                    "cut_reason": c.get("reason", ""), "scan_at": scan}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"accepted_at": at, "key": f"scan:{scan}", "ruling": RECEIPT,
                                "by": by, "scan_at": scan, "accepted": len(new)}) + "\n")
            f.flush()
            os.fsync(f.fileno())
    return 0, {"scan": scan, "shown": len(got["cuts"]), "accepted": len(new),
               "skipped_corrected": sum(c["role_key"] in corrected for c in got["cuts"]),
               "skipped_already_accepted": sum(c["role_key"] in done and c["role_key"] not in corrected
                                               for c in got["cuts"]),
               "log": str(ACCEPTS_REL)}


def ladder_events(rulings: list, accepts: list) -> list:
    """Nick's corrections and acceptances as decision_write.ladder() events, one asked +
    resolved pair per line; class = the gate's cut reason at cut time (20 distinct values over
    753 cuts in the 11-24 queue, none carrying a per-role detail). Only Nick's lines count.

    lean_shown is False here and ONLY here, never stored in either log: the title-cut rule
    (funnel-intake.md "Autonomy ladder", D2 of 2026-10-28: "20 consecutive cuts with 0
    corrections") counts every cut Nick explicitly reviewed, and the cut is the gate's action
    under review, not a lean Claude showed him. Without it ladder() would count none."""
    # Timestamps are to the second. On a tie the correction sorts LAST, so a correction made in
    # the same second as an acceptance always resets the class: the safe direction, never an
    # agreement too many (Codex r1 F3). ladder() re-sorts by time, stably, keeping this order.
    lines = [(r.get("ruled_at") or "", 1, "correction", r) for r in rulings] + \
            [(a.get("accepted_at") or "", 0, "agreement", a) for a in accepts
             if a.get("ruling") == ACCEPTED]
    events = []
    for n, (at, _, outcome, r) in enumerate(sorted(lines, key=lambda x: x[:2])):
        if (r.get("by") or "nick") != "nick":
            continue
        eid = f"cut-{n}"
        events.append({"type": "asked", "id": eid, "class": r.get("cut_reason") or "(no reason)"})
        events.append({"type": "resolved", "id": eid, "at": at, "outcome": outcome,
                       "lean_shown": False})
    return events


def ladder(root: Path, now: datetime | None = None) -> tuple[int, dict]:
    """Per cut class: the rung from decision_write.ladder(), the one trust-ladder count."""
    from tools.decision_write import ladder as count  # the shared function, never a copy
    rulings, accepts = load_rulings(root), load_accepts(root)
    for log in (rulings, accepts):
        if log["error"]:
            return 2, {"error": log["error"]}
    return 0, {"classes": count(ladder_events(rulings["rulings"], accepts["rulings"]), now),
               "bad_lines": rulings["bad_lines"] + accepts["bad_lines"]}


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
    ac = sub.add_parser("accept", help="accept every cut on today's page list you did not correct")
    ac.add_argument("--scan", required=True,
                    help="the current scan: its scanned_at, or the label the page prints")
    ac.add_argument("--by", default="nick", choices=("nick", "claude"))
    sub.add_parser("ladder", help="per cut class: rung, agreements since the last correction")
    a = ap.parse_args(argv)
    root = Path(a.repo_root)

    if a.cmd in ("mark", "accept", "ladder"):
        code, out = (mark(root, a.key, a.reason, a.by) if a.cmd == "mark"
                     else accept(root, a.scan, a.by) if a.cmd == "accept" else ladder(root))
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
