import json, sys
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import inbox_lock
import sys
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))   # a duplicate entry is harmless; no guard needed
from tools.agent_core import (load_dotenv, make_client, run_agent, _norm_name,
                              filter_known, load_known_names)
from tools.agent_discover import struct_to_candidates, load_preset, score_company_candidates

import company_queue
QUEUE_ROOT = _REPO_ROOT   # the queue lives at <root>/tools/.company-queue.json
SEEN_PATH = _REPO_ROOT / "tools" / ".agent_seen.json"
INBOX = _REPO_ROOT / "data" / "inbox.md"
PIPELINE = _REPO_ROOT / "data" / "job-pipeline.md"


def read_seen():
    return json.loads(SEEN_PATH.read_text(encoding="utf-8")) if SEEN_PATH.is_file() else {}


def write_seen(state):
    import os, tempfile
    fd, tmp = tempfile.mkstemp(dir=SEEN_PATH.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, SEEN_PATH)


def reconcile_candidates(candidates, pipeline_path):
    """Run company candidates through pipeline_reconcile, the check /discover-companies
    already uses (362.F5). filter_known matches exact normalized names only, so
    `Acme` / `Acme AI` and `Beacon` / `Beacon.ai` passed it.

    A name that resolves to any pipeline company, open or closed, is dropped as known:
    the collector's own policy (load_known_names) is that a company Nick looked at and
    turned down is never re-proposed. A near-match is kept and marked `possible_match`,
    because whether it is the same company is Nick's call. If the check cannot run,
    candidates pass through unchanged.
    """
    try:
        import pipeline_reconcile as pr
        res = pr.resolve([c.get("name", "") for c in candidates], Path(pipeline_path))
    except Exception:  # noqa: BLE001 -- an unreadable pipeline must not stop the drip
        return candidates
    known = {e["name"] for state in (pr.STATE_ACTIVE, pr.STATE_TERMINAL, pr.STATE_MIXED)
             for e in res[state]}
    near = {e["name"]: e["candidates"][0]["pipeline_company"] for e in res[pr.STATE_AMBIGUOUS]}
    out = []
    for c in candidates:
        name = c.get("name", "")
        if name in known:
            continue
        if name in near:
            c["possible_match"] = near[name]
        out.append(c)
    return out


def diff_unseen(candidates, seen_set):
    fresh, updated = [], set(seen_set)
    for c in candidates:
        k = _norm_name(c.get("name", ""))
        if k and k not in updated:
            fresh.append(c); updated.add(k)
    return fresh, updated


def render_inbox_block(record, candidates, today):
    entity = record["entity"]
    lines = [f"\n## {today} | Agent drip: {record['preset']} ({entity})",
             "<!-- review-gated: accept via /act or /networking -->"]
    for c in candidates:
        if entity == "company":
            # geo_flag is computed by company_scorer.geo_gate and carried onto the
            # candidate by agent_discover. It used to stop here: the company line
            # rendered name/description/score only, so an unverified location was
            # flagged and then made invisible at the only surface a human reads.
            # Absent key defaults to unverified -- a candidate that never went through
            # the scorer has not been geo-checked either. Origin 2026-09-14.
            # Key off the BAND, not the flag. geo_flag is a generic "review me" and is
            # True for a KNOWN Oakland or San Mateo HQ as well as for an unknown one, so
            # labelling every flagged company "unverified" reports a verified East Bay
            # address as unverified. Found by cross-model review 2026-09-14 (F2), forty
            # minutes after the flag-surfacing change that introduced it.
            _LABELS = {"sf": "", "bay": " [Bay, not SF]", "peninsula": " [Peninsula]",
                       "unknown": " [location unverified]", "excluded": " [out of geo]"}
            band = c.get("geo_band")
            if band in _LABELS:
                mark = _LABELS[band]
            else:
                # No band recorded: an older producer, or a candidate that never went
                # through the scorer. Either way its geography is unconfirmed.
                mark = "" if c.get("geo_flag") is False else " [location unverified]"
            if c.get("possible_match"):
                mark += f" [possible match: {c['possible_match']} in pipeline]"
            lines.append(f"- **{c.get('name')}** - {(c.get('description') or '')[:120]} "
                         f"(score {c.get('score','-')}){mark}")
        else:
            lines.append(f"- **{c.get('name')}** - {c.get('role','')} @ "
                         f"{c.get('company','')} · {c.get('location','')}")
    return "\n".join(lines) + "\n"


def append_inbox(block):
    """Add a proposal block to the inbox, locked and atomically.

    Was a read-concat-write_text that appended at EOF: non-atomic (a crash
    truncated the inbox) and it buried new proposals at the bottom while every
    other writer prepends, quietly breaking the newest-first convention. Now
    prepends through the shared writer like everything else.
    """
    written = True
    try:
        inbox_lock.prepend_entries(INBOX, block)
    except (inbox_lock.LockTimeout, inbox_lock.ConcurrentModification) as exc:
        print(f"ERROR: inbox not updated ({exc}) — proposals NOT written", file=sys.stderr)
        written = False
    return written


def queue_fresh(preset, entity, candidates, today):
    """Write proposals to the discovery queue (tools/company_queue.py). False on failure,
    so the caller leaves them unseen and the next run proposes them again."""
    try:
        company_queue.add(QUEUE_ROOT, preset, entity, candidates, today)
    except Exception as exc:  # noqa: BLE001 -- lock timeout, unreadable queue
        print(f"ERROR: discovery queue not updated ({exc}) — proposals NOT written",
              file=sys.stderr)
        return False
    return True


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", required=True, help="YYYY-MM-DD (Date.now unavailable in tools)")
    ap.add_argument("--presets-file",
        default=str(_REPO_ROOT / "data" / "discover-presets.yaml"))
    args = ap.parse_args()
    load_dotenv(); client = make_client()
    import yaml
    data = yaml.safe_load(Path(args.presets_file).read_text(encoding="utf-8")) or {}
    seen = read_seen(); summary = {}
    known = load_known_names([str(_REPO_ROOT / "data" / "scan-targets.yaml"),
                              str(_REPO_ROOT / "data" / "job-pipeline.md")])
    for name, preset in (data.get("presets") or {}).items():
        if not preset.get("monitor"):
            continue
        entity = preset.get("entity_type", "company")
        res = run_agent(client, preset["query"], preset.get("output_schema"),
                        preset.get("effort", "low"))
        if res["status"] != "completed":
            summary[name] = {"status": res["status"]}; continue
        cands = filter_known(struct_to_candidates(res["structured"], entity), known)
        if entity == "company":
            # Per-preset weights: load_preset merges the preset's own scoring_weights
            # over the global block. Reading only the global block scored Lane B with
            # stage 0.5, the axis the 2026-08-06 decision retired (P0 363.F2).
            _, weights = load_preset(args.presets_file, name)
            cands = reconcile_candidates(cands, PIPELINE)
            cands = score_company_candidates(cands, weights, preset.get("keywords"))
        fresh, updated = diff_unseen(cands, set(seen.get(name, [])))
        # Decision 3, funnel-intake.md (Nick, 2026-11-06): discovery output goes to the
        # queue /standup reads, not the inbox. The inbox was a drain nothing read:
        # 36 unreviewed blocks on 10/19, 44 on 11/17.
        if fresh and not queue_fresh(name, entity, fresh, args.today):
            # Not written, so not seen: marking them seen here lost them for good, since
            # the next run would treat every one as already proposed (582-588.F2).
            summary[name] = {"new": 0, "unwritten": len(fresh), "cost": res.get("cost")}
            continue
        seen[name] = sorted(updated)
        summary[name] = {"new": len(fresh), "cost": res.get("cost")}
    write_seen(seen)
    print(json.dumps({"summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
