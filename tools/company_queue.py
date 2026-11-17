#!/usr/bin/env python3
"""company_queue.py - the discovery queue: where discovered companies wait for Nick's call.

Built 2026-11-17 to execute decisions Nick settled on 2026-11-06 in
data/workstreams/funnel-intake.md, which sat unbuilt for 11 days:

  1. Discovery output lands in a queue file /standup reads (the role-queue pattern),
     NOT in data/inbox.md. Before this, tools/agent_collect.py prepended every weekly
     result to the inbox, where nothing read it (36 blocks on 10/19, 44 on 11/17).
  2. "Accept" checks for a public Ashby / Greenhouse / Lever board at accept time:
     board found -> a scan target with `ats` and `slug`; none -> an outreach-only
     target (no `ats`). Both go through tools/act_apply.py target-add, the one writer
     for data/scan-targets.yaml. "Reject" goes through target-reject, which records the
     company under `rejected:` so the weekly collector never proposes it again.

Reading never consumes. Only `decide` moves an entry out of `pending`, and only after
act_apply reported success, so a failed write leaves the company in the queue.

CLI (top-level options before the subcommand):
  company_queue.py [--repo-root R] read [--top 4]
  company_queue.py [--repo-root R] decide --name "Acme" --accept [--lane a|b] [--dry-run]
  company_queue.py [--repo-root R] decide --name "Acme" --reject [--reason "..."]
  company_queue.py [--repo-root R] import-inbox [--inbox data/inbox.md] [--remove]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT))

import inbox_lock  # noqa: E402

QUEUE_NAME = "tools/.company-queue.json"


def queue_path(repo_root: Path) -> Path:
    return Path(repo_root) / QUEUE_NAME


def norm(name: str) -> str:
    """Same normalization as agent_core._norm_name, so keys agree with the collector."""
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def entry_key(entry: dict) -> str:
    if entry.get("entity") == "person":
        return f"person:{norm(entry.get('name', ''))}|{norm(entry.get('company', ''))}"
    return f"company:{norm(entry.get('name', ''))}"


def _empty() -> dict:
    return {"pending": [], "decided": []}


def _load_text(text: str) -> dict:
    if not text.strip():
        return _empty()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("company queue top level is not an object")
    data.setdefault("pending", [])
    data.setdefault("decided", [])
    return data


def _dump(data: dict) -> str:
    data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def load(repo_root: Path) -> dict:
    p = queue_path(repo_root)
    return _load_text(p.read_text(encoding="utf-8")) if p.is_file() else _empty()


def add(repo_root: Path, preset: str, entity: str, candidates: list[dict],
        today: str) -> dict:
    """Queue candidates. A key already pending or already decided is skipped, so a
    company Nick rejected here is never queued twice even if the collector's own seen
    set is reset."""
    counts = {"added": 0, "skipped": 0}

    def transform(text: str) -> str:
        data = _load_text(text)
        known = {e.get("key") for e in data["pending"]} | {e.get("key") for e in data["decided"]}
        counts["added"] = counts["skipped"] = 0
        for c in candidates:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            row = {"entity": entity, "preset": preset, "first_seen": today,
                   "name": c.get("name"),
                   **{k: c[k] for k in ("description", "score", "geo_band", "geo_flag",
                                        "possible_match", "url", "role", "company",
                                        "location") if k in c}}
            row["key"] = entry_key(row)
            if row["key"] in known:
                counts["skipped"] += 1
                continue
            known.add(row["key"])
            data["pending"].append(row)
            counts["added"] += 1
        return _dump(data)

    queue_path(repo_root).parent.mkdir(parents=True, exist_ok=True)
    inbox_lock.atomic_update(queue_path(repo_root), transform)
    return dict(counts)


def read(repo_root: Path, top: int = 4) -> dict:
    """What /standup renders. Never raises: a broken queue reports exists: false."""
    p = queue_path(repo_root)
    if not p.is_file():
        return {"exists": False, "reason": f"no queue at {p}; the discovery job has not "
                "written one yet", "pending_companies": None, "companies": []}
    try:
        data = _load_text(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"exists": False, "reason": f"unreadable queue: {exc}",
                "pending_companies": None, "companies": []}
    pend = [e for e in data["pending"] if isinstance(e, dict)]
    comps = [e for e in pend if e.get("entity") != "person"]
    people = [e for e in pend if e.get("entity") == "person"]

    def score(e):
        s = e.get("score")
        return s if isinstance(s, (int, float)) else -1

    comps.sort(key=lambda e: (score(e), e.get("first_seen") or ""), reverse=True)
    oldest = min((e.get("first_seen") or "" for e in pend), default=None) or None
    return {"exists": True, "updated_at": data.get("updated_at"),
            "pending_companies": len(comps), "pending_people": len(people),
            "oldest_first_seen": oldest, "decided_count": len(data["decided"]),
            "companies": [{k: e.get(k) for k in ("name", "description", "score",
                                                  "geo_band", "possible_match", "preset",
                                                  "first_seen")} for e in comps[:top]]}


# --- accept-time board check (decision 1, 2026-11-06) ---------------------------------

def slug_candidates(name: str) -> list[str]:
    base = (name or "").strip()
    low = base.lower()
    stripped = re.sub(r"[\s.,-]*(ai|inc|labs|hq)$", "", low).strip()
    out = []
    for s in (low, stripped):
        for v in (re.sub(r"[^a-z0-9]", "", s), re.sub(r"[^a-z0-9]+", "-", s).strip("-"),
                  s.replace(" ", "")):
            if v and v not in out:
                out.append(v)
    if stripped and f"{re.sub(r'[^a-z0-9]', '', stripped)}ai" not in out:
        out.append(f"{re.sub(r'[^a-z0-9]', '', stripped)}ai")
    return out


def _default_fetchers():
    from tools.career_scanner.ashby import fetch_ashby
    from tools.career_scanner.greenhouse import fetch_greenhouse
    from tools.career_scanner.lever import fetch_lever
    return {"ashby": fetch_ashby, "greenhouse": fetch_greenhouse, "lever": fetch_lever}


def detect_board(name: str, fetchers: dict | None = None) -> dict | None:
    """First public board with at least one posting, else None.

    A board with zero postings is indistinguishable from a wrong slug (every fetcher
    returns [] on an HTTP error), so None means "no board FOUND", never "no board".
    The caller records the company as outreach-only and says so."""
    fetchers = fetchers if fetchers is not None else _default_fetchers()
    for slug in slug_candidates(name):
        for ats, fetch in fetchers.items():
            try:
                roles = fetch(slug)
            except Exception:  # noqa: BLE001 -- one bad probe must not stop the check
                roles = []
            if roles:
                return {"ats": ats, "slug": slug, "n_roles": len(roles)}
    return None


def _act_apply(repo_root: Path, args: list[str]) -> dict:
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "act_apply.py"),
                           "--repo-root", str(repo_root), *args],
                          capture_output=True, text=True)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "error", "message": (proc.stderr or proc.stdout)[-500:]}


def decide(repo_root: Path, name: str, decision: str, reason: str | None = None,
           lane: str | None = None, dry_run: bool = False, detector=detect_board,
           runner=_act_apply) -> dict:
    data = load(repo_root)
    want = norm(name)
    match = [e for e in data["pending"]
             if isinstance(e, dict) and e.get("entity") != "person" and norm(e.get("name", "")) == want]
    if not match:
        return {"status": "error", "code": "not_found",
                "message": f"no pending company named {name!r} in the queue"}
    entry = match[0]
    if decision == "accept":
        board = detector(entry["name"])
        args = ["target-add", entry["name"]]
        if board:
            args += ["--ats", board["ats"], "--slug", board["slug"]]
        else:
            args += ["--outreach"]
        if lane:
            args += ["--lane", lane]
        args += ["--notes", f"Accepted from the discovery queue {datetime.now().date()} "
                 f"({entry.get('preset')}, first seen {entry.get('first_seen')})"]
    elif decision == "reject":
        board = None
        args = ["target-reject", entry["name"]]
        if reason:
            args += ["--reason", reason]
        if lane:
            args += ["--lane", lane]
    else:
        return {"status": "error", "code": "bad_decision", "message": decision}

    if dry_run:
        return {"status": "ok", "dry_run": True, "would_run": args, "board": board}
    result = runner(repo_root, args)
    if result.get("status") != "ok":
        return {"status": "error", "code": "act_apply_failed", "act_apply": result,
                "message": "scan-targets.yaml not changed; the company stays queued"}

    key = entry["key"]

    def transform(text: str) -> str:
        d = _load_text(text)
        moved = [e for e in d["pending"] if e.get("key") == key]
        d["pending"] = [e for e in d["pending"] if e.get("key") != key]
        for e in moved:
            d["decided"].append({**e, "decision": decision, "decided_on": str(datetime.now().date()),
                                 "board": board, "reason": reason})
        return _dump(d)

    inbox_lock.atomic_update(queue_path(repo_root), transform)
    return {"status": "ok", "decision": decision, "name": entry["name"], "board": board,
            "act_apply": result}


# --- one-time migration of the drip blocks already in the inbox ------------------------

_BLOCK_HDR = re.compile(r"^## (\d{4}-\d{2}-\d{2}) \| Agent drip: (\S+) \((company|person)\)\s*$")
_COMPANY_LINE = re.compile(r"^- \*\*(.+?)\*\* - (.*?) \(score ([^)]*)\)(.*)$")
_PERSON_LINE = re.compile(r"^- \*\*(.+?)\*\* - (.*?) @ (.*?) · (.*)$")


def parse_inbox_blocks(text: str) -> tuple[list[dict], list[tuple[int, int]]]:
    """Return (entries, spans) for every Agent-drip block. spans are line ranges
    [start, end) in text.splitlines(), so the caller can remove exactly those blocks."""
    lines = text.splitlines()
    entries, spans = [], []
    i = 0
    while i < len(lines):
        m = _BLOCK_HDR.match(lines[i])
        if not m:
            i += 1
            continue
        today, preset, entity = m.groups()
        j = i + 1
        while j < len(lines) and not lines[j].startswith("## "):
            line = lines[j]
            if entity == "company":
                cm = _COMPANY_LINE.match(line)
                if cm:
                    nm, desc, score, tail = cm.groups()
                    try:
                        sc = float(score)
                        sc = int(sc) if sc.is_integer() else sc
                    except ValueError:
                        sc = None
                    pm = re.search(r"\[possible match: (.+?) in pipeline\]", tail)
                    entries.append({"entity": "company", "preset": preset, "first_seen": today,
                                    "name": nm.strip(), "description": desc.strip(), "score": sc,
                                    **({"possible_match": pm.group(1)} if pm else {}),
                                    "inbox_note": tail.strip() or None})
            else:
                pm = _PERSON_LINE.match(line)
                if pm:
                    nm, role, company, loc = pm.groups()
                    entries.append({"entity": "person", "preset": preset, "first_seen": today,
                                    "name": nm.strip(), "role": role.strip(),
                                    "company": company.strip(), "location": loc.strip()})
            j += 1
        spans.append((i, j))
        i = j
    return entries, spans


def import_inbox(repo_root: Path, inbox: Path, remove: bool = False) -> dict:
    text = inbox.read_text(encoding="utf-8") if inbox.is_file() else ""
    entries, spans = parse_inbox_blocks(text)
    by = {}
    for e in entries:
        by.setdefault((e["preset"], e["entity"], e["first_seen"]), []).append(e)
    added = skipped = 0
    for (preset, entity, day), group in sorted(by.items(), key=lambda kv: kv[0][2]):
        r = add(repo_root, preset, entity, group, day)
        added += r["added"]
        skipped += r["skipped"]
    removed = 0
    if remove and spans:
        def transform(t: str) -> str:
            _, sp = parse_inbox_blocks(t)
            ls = t.splitlines(keepends=True)
            drop = set()
            for a, b in sp:
                drop.update(range(a, b))
            return "".join(l for k, l in enumerate(ls) if k not in drop)
        before = len(spans)
        inbox_lock.atomic_update(inbox, transform)
        removed = before
    return {"blocks": len(spans), "entries": len(entries), "added": added,
            "skipped": skipped, "blocks_removed": removed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read")
    r.add_argument("--top", type=int, default=4)
    d = sub.add_parser("decide")
    d.add_argument("--name", required=True)
    g = d.add_mutually_exclusive_group(required=True)
    g.add_argument("--accept", action="store_true")
    g.add_argument("--reject", action="store_true")
    d.add_argument("--reason")
    d.add_argument("--lane")
    d.add_argument("--dry-run", action="store_true")
    m = sub.add_parser("import-inbox")
    m.add_argument("--inbox", default=None)
    m.add_argument("--remove", action="store_true",
                   help="also delete the imported Agent-drip blocks from the inbox")
    a = ap.parse_args(argv)
    root = Path(a.repo_root)
    if a.cmd == "read":
        out = read(root, a.top)
    elif a.cmd == "decide":
        out = decide(root, a.name, "accept" if a.accept else "reject", a.reason, a.lane, a.dry_run)
    else:
        out = import_inbox(root, Path(a.inbox) if a.inbox else root / "data" / "inbox.md", a.remove)
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    return 0 if out.get("status", "ok") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
