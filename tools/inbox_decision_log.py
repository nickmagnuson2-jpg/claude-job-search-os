#!/usr/bin/env python3
"""
inbox_decision_log.py — schema-checked append + coverage report for the inbox drain.

The drain's deliverable is the LOG, not the routing. The router gets designed from
these rows, so a row that cannot be replayed is a note rather than seeding data.
This module is the only writer: it validates every field against the schema in
output/analysis/090826-inbox-drain-taxonomy-and-log.md before anything lands.

What it refuses, and why each refusal earns its place:
  - a block_id not present in the extractor output   (a decision about nothing)
  - a corpus_sha that differs from the extractor's   (decisions stop being
    attributable to known inputs the moment the file moves under them)
  - a second row for a block already logged          (append-only, one decision
    per block, so the bulk:individual ratio stays countable)
  - mode=bulk with no bulk_rule                      (the rule IS the compression
    measurement; a bulk row without one is unattributable)
  - an unknown destination or confidence value       (the taxonomy is derived, not
    extensible by typo)

`decided_by` carries a third value the spec did not enumerate: `claude-proposed`.
Rung 3 is propose-only (2026-09-07 grey-area pass), so a drain run by Claude alone
produces proposals, not decisions. Recording them as `claude-proposed-nick-approved`
would fabricate an approval and destroy the agreement rate the field exists to
measure.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/inbox_decision_log.py append \
      --rows-file rows.json --repo-root .
  PYTHONIOENCODING=utf-8 python3 tools/inbox_decision_log.py status --repo-root .
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

BLOCKS_REL = "output/career-scan/inbox-blocks.jsonl"
LOG_REL = "output/career-scan/inbox-drain-decisions.jsonl"
APPROVALS_REL = "output/career-scan/inbox-drain-approvals.jsonl"

DESTINATIONS = {
    "pipeline-row", "networking-interaction", "person-dossier", "job-todo",
    "personal-todo", "company-note", "target-accept", "target-reject",
    "contact", "note", "decision-accomplishment", "archive", "delete",
}
PRODUCERS = {"career_scan_live", "call_debrief", "dated_captures",
             "agent_drips", "unrecognised"}
MODES = {"bulk", "individual"}
CONFIDENCE = {"high", "medium", "low"}
DECIDED_BY = {"nick", "claude-proposed-nick-approved", "claude-proposed"}

REQUIRED = ("block_id", "corpus_sha256", "producer", "decided_at", "mode",
            "bulk_rule", "destination", "writer_invocation", "reason",
            "decided_by", "confidence", "residual_flag")


def fail(message: str, code: str, **extra) -> None:
    print(json.dumps({"status": "error", "message": message, "code": code, **extra},
                     ensure_ascii=False))
    sys.exit(1)


def load_blocks(root: Path) -> dict:
    path = root / BLOCKS_REL
    if not path.is_file():
        fail(f"extractor output missing: {path}. Run tools/inbox_extract.py first.",
             "blocks_missing")
    blocks = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            b = json.loads(line)
            blocks[b["block_id"]] = b
    if not blocks:
        fail("extractor output is empty — refusing to validate against nothing.",
             "blocks_empty")
    return blocks


def rule_id(bulk_rule: str) -> str:
    """Stable short id for a bulk rule, derived from its own text.

    Keyed on the rule TEXT rather than an assigned number so an approval cannot
    silently transfer to a different rule if the rules are ever reordered or
    re-emitted. Change the wording and the id changes, which is the correct
    behaviour: an approval applies to the rule Nick actually read.
    """
    return hashlib.sha1(bulk_rule.encode("utf-8")).hexdigest()[:8]


def load_approvals(root: Path) -> dict:
    """Latest approval per rule_id. Append-only file, last row wins."""
    path = root / APPROVALS_REL
    if not path.is_file():
        return {}
    out = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                out[row["rule_id"]] = row
    return out


def load_log(root: Path) -> list[dict]:
    path = root / LOG_REL
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def validate(row: dict, blocks: dict, seen: set, corpus_sha: str,
             supersede: bool = False) -> None:
    missing = [k for k in REQUIRED if k not in row]
    if missing:
        fail(f"row missing required fields: {missing}", "schema_missing_fields",
             block_id=row.get("block_id"))
    bid = row["block_id"]
    if bid not in blocks:
        fail(f"unknown block_id: {bid}", "unknown_block")
    if bid in seen and not supersede:
        fail(f"block already logged: {bid}. The log is append-only, one decision "
             f"per block. Pass --supersede to record a REVISED decision: the "
             f"original row stays in the file and the later row wins, so the "
             f"correction is auditable instead of destroying the thing the log "
             f"exists to preserve.", "duplicate_block")
    if row["corpus_sha256"] != corpus_sha:
        fail(f"corpus sha mismatch for {bid}: row {row['corpus_sha256'][:12]} vs "
             f"extractor {corpus_sha[:12]}", "sha_mismatch")
    if row["producer"] != blocks[bid]["producer"]:
        fail(f"producer disagrees with the extractor for {bid}: row "
             f"{row['producer']} vs {blocks[bid]['producer']}", "producer_mismatch")
    for field, allowed in (("mode", MODES), ("destination", DESTINATIONS),
                           ("confidence", CONFIDENCE), ("decided_by", DECIDED_BY),
                           ("producer", PRODUCERS)):
        if row[field] not in allowed:
            fail(f"{bid}: {field}={row[field]!r} not in {sorted(allowed)}",
                 "bad_enum")
    if row["mode"] == "bulk" and not row["bulk_rule"]:
        fail(f"{bid}: mode=bulk requires a bulk_rule — the rule is the compression "
             f"measurement.", "bulk_without_rule")
    if row["mode"] == "individual" and row["bulk_rule"]:
        fail(f"{bid}: mode=individual must not carry a bulk_rule.",
             "individual_with_rule")
    if not str(row["reason"]).strip():
        fail(f"{bid}: empty reason.", "empty_reason")
    if row["destination"] not in ("archive", "delete"):
        inv = row["writer_invocation"]
        if not inv:
            fail(f"{bid}: destination {row['destination']} needs a writer_invocation "
                 f"— a logged decision that cannot be replayed is a note.",
                 "missing_invocation")
        # A NON-EMPTY invocation is not the same as an EXECUTABLE one. A rule
        # shipped 2026-09-08 carrying `--company "<each row in <file>>"` passed the
        # emptiness check on 13 rows and would have created 13 targets literally
        # named "<each row in ...>" had it been approved. Placeholder brackets are
        # the signature; refuse them.
        if "<" in inv and ">" in inv:
            fail(f"{bid}: writer_invocation contains a placeholder and would not "
                 f"execute as written: {inv[:120]}", "placeholder_invocation")


def cmd_append(root: Path, rows_file: Path, supersede: bool = False) -> None:
    blocks = load_blocks(root)
    corpus_sha = next(iter(blocks.values()))["corpus_sha256"]
    existing = load_log(root)
    seen = {r["block_id"] for r in existing}

    rows = json.loads(rows_file.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        fail("rows file must be a non-empty JSON array.", "bad_rows_file")

    stamp = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        row.setdefault("decided_at", stamp)
        validate(row, blocks, seen, corpus_sha, supersede=supersede)
        seen.add(row["block_id"])

    log_path = root / LOG_REL
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps({k: row[k] for k in REQUIRED}, ensure_ascii=False) + "\n")

    print(json.dumps({"status": "ok", "appended": len(rows),
                      "logged_total": len(existing) + len(rows),
                      "of_blocks": len(blocks)}, ensure_ascii=False))


def cohort_id(destination: str, confidence: str) -> str:
    """Approval unit for INDIVIDUAL rows, which have no rule to approve.

    An individual decision needed a human read, so there is no rule text to
    approve. The workable unit is (destination, confidence): "every high-
    confidence delete" is one decision covering many rows, and the confidence
    split is exactly what separates a routine call from a judgment call.
    """
    return f"ind:{destination}:{confidence}"


def cmd_approve(root: Path, rule_ids: list[str], approver: str, note: str) -> None:
    """Record Nick's approval of one or more bulk rules.

    Approvals are a SEPARATE append-only stream. The decision log is never
    rewritten - a row says what was proposed and when, and rewriting it in place
    would destroy the very thing the log exists to preserve. Joining the two at
    read time is what makes the agreement rate computable later.
    """
    log = current_log(root)
    if not log:
        fail("no decisions logged yet", "empty_log")
    by_rule = {}
    for r in log:
        if r["mode"] == "bulk":
            by_rule.setdefault(rule_id(r["bulk_rule"]), []).append(r["block_id"])
        else:
            by_rule.setdefault(cohort_id(r["destination"], r["confidence"]),
                               []).append(r["block_id"])

    unknown = [r for r in rule_ids if r not in by_rule]
    if unknown:
        fail(f"unknown rule id(s): {unknown}", "unknown_rule",
             known=sorted(by_rule))

    stamp = datetime.now().isoformat(timespec="seconds")
    path = root / APPROVALS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for rid in rule_ids:
            fh.write(json.dumps({
                "rule_id": rid,
                "approved_at": stamp,
                "approved_by": approver,
                "blocks": len(by_rule[rid]),
                "block_ids": by_rule[rid],
                "note": note,
                "executed": False,
            }, ensure_ascii=False) + "\n")

    print(json.dumps({"status": "ok", "approved": rule_ids,
                      "blocks_covered": sum(len(by_rule[r]) for r in rule_ids)},
                     ensure_ascii=False))


def current_log(root: Path) -> list[dict]:
    """The log with supersessions applied: last row per block_id wins.

    History stays in the file; this is the read-time view of it.
    """
    latest = {}
    for r in load_log(root):
        latest[r["block_id"]] = r
    return list(latest.values())


def cmd_status(root: Path) -> None:
    blocks = load_blocks(root)
    log = current_log(root)
    logged = {r["block_id"] for r in log}
    missing = [b for b in blocks if b not in logged]

    def hist(key):
        out = {}
        for r in log:
            out[r[key]] = out.get(r[key], 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    bulk = sum(1 for r in log if r["mode"] == "bulk")
    ind = sum(1 for r in log if r["mode"] == "individual")
    residual = [r["block_id"] for r in log if r["residual_flag"]]

    approvals = load_approvals(root)
    rules, cohorts = {}, {}
    for r in log:
        if r["mode"] == "bulk":
            rid = rule_id(r["bulk_rule"])
            e = rules.setdefault(rid, {"rule_id": rid, "blocks": 0,
                                       "destination": r["destination"],
                                       "producer": r["producer"],
                                       "confidence": r["confidence"]})
            e["blocks"] += 1
        else:
            cid = cohort_id(r["destination"], r["confidence"])
            e = cohorts.setdefault(cid, {"rule_id": cid, "blocks": 0,
                                         "destination": r["destination"],
                                         "producer": "individual",
                                         "confidence": r["confidence"]})
            e["blocks"] += 1
    for c in cohorts.values():
        a = approvals.get(c["rule_id"])
        c["approved"] = bool(a)
        c["approved_by"] = a["approved_by"] if a else None
        c["executed"] = a.get("executed", False) if a else False
    for rid, e in rules.items():
        a = approvals.get(rid)
        e["approved"] = bool(a)
        e["approved_by"] = a["approved_by"] if a else None
        e["executed"] = a.get("executed", False) if a else False
    approved_blocks = (sum(e["blocks"] for e in rules.values() if e["approved"])
                       + sum(e["blocks"] for e in cohorts.values() if e["approved"]))

    print(json.dumps({
        "status": "ok",
        "blocks": len(blocks),
        "logged": len(log),
        "unlogged": len(missing),
        "unlogged_ids": missing[:25],
        "bulk": bulk,
        "individual": ind,
        "bulk_to_individual": (round(bulk / ind, 2) if ind else None),
        "by_destination": hist("destination"),
        "by_producer": hist("producer"),
        "by_confidence": hist("confidence"),
        "by_decided_by": hist("decided_by"),
        "residual_count": len(residual),
        "residual_ids": residual,
        "bulk_rules": sorted(rules.values(), key=lambda e: -e["blocks"]),
        "individual_cohorts": sorted(cohorts.values(), key=lambda e: -e["blocks"]),
        "blocks_under_approved_rules": approved_blocks,
        "blocks_awaiting_a_decision": len(log) - approved_blocks,
        "executed_any": any(e["executed"] for e in rules.values()),
    }, ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description="Inbox drain decision log.")
    ap.add_argument("command", choices=["append", "status", "approve"])
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--rows-file", default=None)
    ap.add_argument("--rule", action="append", default=[],
                    help="bulk rule id to approve; repeatable")
    ap.add_argument("--approver", default="nick")
    ap.add_argument("--note", default="")
    ap.add_argument("--supersede", action="store_true",
                    help="record a revised decision for a block already logged")
    args = ap.parse_args()

    root = Path(args.repo_root) if args.repo_root else Path.cwd()
    if args.command == "append":
        if not args.rows_file:
            fail("append needs --rows-file", "missing_arg")
        rows_file = Path(args.rows_file)
        if not rows_file.is_file():
            fail(f"rows file not found: {rows_file}", "rows_file_missing")
        cmd_append(root, rows_file, supersede=args.supersede)
    elif args.command == "approve":
        if not args.rule:
            fail("approve needs at least one --rule", "missing_arg")
        cmd_approve(root, args.rule, args.approver, args.note)
    else:
        cmd_status(root)


if __name__ == "__main__":
    main()
