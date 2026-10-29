#!/usr/bin/env python3
"""
inbox_drain_apply.py — execute the approved half of the inbox drain.

This is the only irreversible step in the drain, so every guard here exists to
make it refuse rather than guess.

What it will act on, and nothing else:
  - rows in the decision log whose bulk_rule has an APPROVAL row in
    inbox-drain-approvals.jsonl. An individual row, or a bulk row under an
    unapproved rule, is never touched. Approval is per RULE, because that is the
    unit Nick actually read and approved.

Three refusals, each with an incident behind it:
  - **corpus sha drift.** If data/inbox.md is not the file the decisions were
    made against, the decisions are no longer attributable to known inputs.
    Abort; re-run inbox_extract.py and re-adjudicate.
  - **per-block sha mismatch.** Blocks are located by their own content hash,
    never by the line numbers in the extractor output: removing block N shifts
    every line number after it, and a launchd collector can prepend to this file
    at any time. A block whose content no longer hashes to what was decided on
    is a different block now, so the run aborts rather than delete it.
  - **post-condition.** The live block count must fall by exactly the number
    removed. Anything else means the splice took something it was not asked to.

Routing runs BEFORE removal. A block with a non-delete destination is written to
its destination first and only then removed, so a failure between the two leaves
the block in the inbox rather than losing it.

Dry-run is the default. `--apply` is required to write.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/inbox_drain_apply.py --repo-root .
  PYTHONIOENCODING=utf-8 python3 tools/inbox_drain_apply.py --repo-root . --apply
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import approval_signing  # noqa: E402
import inbox_lock  # noqa: E402
from inbox_census import census  # noqa: E402
from inbox_decision_log import (  # noqa: E402
    APPROVALS_REL, LOG_REL, approval_staleness, current_log, load_approvals, unit_id,
)
import datetime as _dt  # noqa: E402

EXECUTED_REL = "output/career-scan/inbox-drain-executed.jsonl"


def load_executed(root: Path) -> set:
    """block_ids already applied. Append-only; makes a re-run idempotent.

    Without this the applier tries to relocate blocks it removed on an earlier
    batch, cannot find them, and aborts - which is correct behaviour for a
    MUTATED block and wrong for an already-removed one. Recording execution is
    the only way to tell those two apart, because a header is not unique
    (53 blocks in this corpus share the header "Career Scan Results").
    """
    path = root / EXECUTED_REL
    if not path.is_file():
        return set()
    out = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.update(json.loads(line)["block_ids"])
    return out


def record_executed(root: Path, block_ids: list, note: str) -> None:
    path = root / EXECUTED_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "executed_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "block_ids": sorted(block_ids), "count": len(block_ids), "note": note,
        }, ensure_ascii=False) + "\n")
from inbox_extract import extract  # noqa: E402

INBOX_REL = "data/inbox.md"


# ── S16: what a routing command may be (2026-10-27) ──────────────────────────────
# Before this, each logged writer_invocation ran through a shell, so whatever text sat in
# the decision log ran: a redirect, `git push`, or an env assignment that switches a guard
# off (SEAL_READ_OK=1). Now a command is parsed, never shell-run, and must be one of these
# writers. The writer list is the three the log uses (act_apply 91 rows, todo_write 25,
# personal_todo_write 20, counted 2026-10-27) plus the three other atomic data writers.
# `&&` is kept as a sequence: 32 of the 136 logged commands chain several writer calls, and
# each piece is checked on its own and run in order, stopping at the first failure.
WRITERS = {"act_apply.py", "todo_write.py", "personal_todo_write.py",
           "networking_write.py", "person_write.py", "pipe_write.py"}
ALLOWED_ENV = {"PYTHONIOENCODING"}          # the only assignment any logged command makes
_OPERATOR = re.compile(r"^[;&|<>()]+$")


class Refused(ValueError):
    """A routing command outside the allowlist. Nothing is run."""


def parse_invocation(command: str, root: Path) -> list[tuple[list[str], dict]]:
    """[(argv, env), ...] for each `&&`-joined writer call, or raise Refused."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError as e:
        raise Refused(f"cannot parse: {e}")
    pieces, cur = [], []
    for t in tokens:
        if t == "&&":
            pieces.append(cur)
            cur = []
        elif _OPERATOR.match(t):
            raise Refused(f"shell operator {t!r} is not allowed")
        else:
            cur.append(t)
    pieces.append(cur)

    out = []
    here = root.resolve()
    for words in pieces:
        env = {}
        while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
            name, value = words.pop(0).split("=", 1)
            if name not in ALLOWED_ENV:
                raise Refused(f"environment variable {name} is not allowed")
            env[name] = value
        if len(words) < 2 or words[0] != "python3":
            raise Refused(f"must be `python3 tools/<writer>.py ...`, got {words[:2]}")
        script = words[1]
        if Path(script).parent.as_posix() != "tools" or Path(script).name not in WRITERS:
            raise Refused(f"{script} is not an allowed writer")
        for i, w in enumerate(words[2:], start=2):
            if w == "Applied":
                raise Refused("marking a job Applied is Nick's, never a drain's")
            if w.startswith("<") and w.endswith(">"):
                raise Refused(f"unresolved placeholder {w[:60]!r}")
            pathlike = words[i - 1] == "--repo-root" or w.startswith(("/", "~", ".."))
            if pathlike:
                p = Path(os.path.expanduser(w))
                full = (p if p.is_absolute() else here / p).resolve()
                if full != here and here not in full.parents:
                    raise Refused(f"path outside the repo: {w}")
                if words[i - 1] == "--repo-root" and full != here:
                    raise Refused(f"--repo-root must be this repo, got {w}")
        out.append(([sys.executable, str(here / script)] + words[2:], env))
    return out


def run_invocation(command: str, root: Path) -> subprocess.CompletedProcess:
    """Run each checked piece in order without a shell; stop at the first failure."""
    proc = None
    for argv, env in parse_invocation(command, root):
        proc = subprocess.run(argv, shell=False, cwd=str(root), capture_output=True,
                              text=True, timeout=60, env={**os.environ, **env})
        if proc.returncode != 0:
            break
    return proc


def signed_approvals(root: Path, allowed_signers: Path | None = None) -> tuple[dict, list]:
    """(approvals with a valid signature by Nick, rule ids whose latest row is unsigned).

    An approval counts only if `approval_signing.verify` accepts it: a row anyone can
    append is not Nick's approval until his Touch ID key has signed it.
    """
    allowed_signers = allowed_signers or approval_signing.ALLOWED_SIGNERS
    good, unsigned = {}, []
    for rid, row in load_approvals(root).items():
        if approval_signing.verify(approval_signing.payload(row), row.get("signature"),
                                   allowed_signers):
            good[rid] = row
        else:
            unsigned.append(rid)
    return good, unsigned


def fail(message: str, code: str, **extra) -> None:
    print(json.dumps({"status": "error", "message": message, "code": code, **extra},
                     ensure_ascii=False))
    sys.exit(1)


def approved_rows(root: Path, approvals: dict) -> tuple[list[dict], dict, list]:
    """Rows under an approved rule, a per-rule tally, and the approvals voided as stale.

    An approval is a UNIT: the signature covers the rule's exact block_ids. It applies
    only while the blocks now decided under that rule are exactly that list. A block
    added after signing, or a signed block moved to another rule, voids the whole
    approval rather than applying a set Nick never signed (Codex and Grok final-g4 F1;
    Codex final-r2-g4 F1, 2026-10-28: a per-block filter still applied a subset).

    Reads current_log(), NOT load_log(). The decision log is append-only and a
    revision is a later row for the same block, so the raw file still contains
    every superseded decision. Reading it raw made this applier plan against a
    retired rule whose invocations were the ones the revision existed to fix -
    caught in dry-run on 2026-09-08, before anything was written.
    """
    members: dict = {}
    for r in current_log(root):
        rid = unit_id(r)
        if rid in approvals:
            members.setdefault(rid, []).append(r)
    rows, tally, stale = [], {}, []
    for rid, rs in members.items():
        s = approval_staleness(approvals[rid], rs)
        if s:
            stale.append(s)
            continue
        for r in rs:
            rows.append({**r, "rule_id": rid})
            tally.setdefault(rid, {"rule_id": rid, "destination": r["destination"],
                                   "producer": r["producer"], "blocks": 0})
            tally[rid]["blocks"] += 1
    for rid in approvals:
        if rid not in members:
            stale.append(approval_staleness(approvals[rid], []))
    return rows, tally, stale


def main() -> None:
    ap = argparse.ArgumentParser(description="Execute the approved inbox-drain decisions.")
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="actually write; without it this is a dry run")
    args = ap.parse_args()

    root = Path(args.repo_root) if args.repo_root else Path.cwd()
    inbox = root / INBOX_REL
    if not inbox.is_file():
        fail(f"inbox not found: {inbox}", "file_not_found")

    approvals, unsigned = signed_approvals(root)
    rows, tally, stale = approved_rows(root, approvals)
    already = load_executed(root)
    skipped = [r["block_id"] for r in rows if r["block_id"] in already]
    rows = [r for r in rows if r["block_id"] not in already]
    if not rows:
        fail("no rows under a SIGNED approval; nothing to do. Approve a rule with "
             "inbox_decision_log.py approve --rule <id> (Touch ID signs it).",
             "nothing_approved", unsigned_approvals_ignored=unsigned,
             stale_approvals_ignored=stale)

    text = inbox.read_text(encoding="utf-8")
    live = census(text)
    pinned = rows[0]["corpus_sha256"]

    by_sha = {b["sha256"]: b for b in extract(text)}
    recorded = {b["block_id"]: b for b in
                (json.loads(l) for l in
                 (root / "output/career-scan/inbox-blocks.jsonl").open(encoding="utf-8"))}
    known_shas = {b["sha256"] for b in recorded.values()}

    # Corpus drift. A whole-file sha is a coarse proxy: the drain removes blocks in
    # approved batches, so the file legitimately changes between batches and the
    # proxy would block the drain's own second pass. The property that actually
    # matters is that nothing ARRIVED or MUTATED under us -- a launchd collector
    # prepending a block, or an edit to a block still awaiting a decision.
    # So: drift is accepted only when the current block set is a strict SUBSET of
    # what was extracted and adjudicated. Any block present now that was not in the
    # decided corpus aborts, which is the case the sha check existed to catch.
    if live["source_sha256"] != pinned:
        arrivals = [b["block_id"] for b in by_sha.values() if b["sha256"] not in known_shas]
        if arrivals:
            fail("data/inbox.md contains blocks that were never adjudicated, so the "
                 "file changed under us rather than only shrinking. Re-run "
                 "tools/inbox_extract.py and adjudicate them before applying.",
                 "corpus_drift", decided_against=pinned[:16],
                 live=live["source_sha256"][:16], arrivals=arrivals[:10],
                 arrival_count=len(arrivals))

    targets, missing = [], []
    for r in rows:
        rec = recorded.get(r["block_id"])
        if rec is None or rec["sha256"] not in by_sha:
            missing.append(r["block_id"])
            continue
        targets.append({**r, "sha256": rec["sha256"], "block": by_sha[rec["sha256"]]})
    if missing:
        fail(f"{len(missing)} approved block(s) could not be located by content hash "
             f"in the current file. Nothing was written.", "block_not_found",
             missing=missing[:10])

    routed = [t for t in targets if t["destination"] not in ("delete", "archive")]
    # Every routing command is checked before anything runs, dry run included, so a
    # refused command stops the batch with the inbox untouched.
    refused = []
    for t in routed:
        try:
            parse_invocation(t["writer_invocation"], root)
        except Refused as e:
            refused.append({"block_id": t["block_id"], "reason": str(e)})
    if refused:
        fail(f"{len(refused)} routing command(s) are outside the writer allowlist; "
             "nothing was written.", "invocation_refused", refused=refused[:10])
    # ARCHIVE IS NOT DELETE. There is no archive writer, so an archive decision
    # means "leave this block where it is". The one archive row in this drain is
    # a drip carrying a hand-written, struck-through location correction, and
    # feedback_scanner_output_is_unreviewed_until_a_primary_source_confirms_it
    # says in its How-to-apply to correct in the file and strike through rather
    # than delete, so the scanner's failure mode stays legible to the next
    # reader. Removing it would erase the evidence a promoted rule points at.
    to_remove = [t for t in targets if t["destination"] != "archive"]
    preserved = [t for t in targets if t["destination"] == "archive"]

    plan = {
        "status": "ok",
        "mode": "APPLY" if args.apply else "dry-run",
        "rules": sorted(tally.values(), key=lambda e: -e["blocks"]),
        "blocks_to_route_then_remove": len(routed),
        "blocks_to_remove": len(to_remove),
        "blocks_preserved_in_place": [t["block_id"] for t in preserved],
        "already_executed_skipped": len(skipped),
        "unsigned_approvals_ignored": unsigned,
        "stale_approvals_ignored": stale,
        "live_blocks_before": live["live_h2_headers"],
        "live_blocks_after_expected": live["live_h2_headers"] - len(to_remove),
    }
    if not args.apply:
        plan["routing_commands"] = [t["writer_invocation"] for t in routed]
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    # 1. Route first. A failure here leaves the inbox untouched.
    route_results = []
    for t in routed:
        cmd = t["writer_invocation"]
        proc = run_invocation(cmd, root)
        route_results.append({"block_id": t["block_id"], "rc": proc.returncode,
                              "stdout": proc.stdout.strip()[:200],
                              "stderr": proc.stderr.strip()[:200]})
        if proc.returncode != 0:
            fail(f"routing failed for {t['block_id']}; the inbox was NOT modified.",
                 "routing_failed", command=cmd, results=route_results)

    # 2. Remove, under the lock, keyed on content hash.
    doomed = {t["sha256"] for t in to_remove}

    def transform(current: str) -> str:
        lines = current.splitlines(keepends=True)
        drop = set()
        for b in extract(current):
            if b["sha256"] in doomed:
                drop.update(range(b["line_start"], b["line_end"] + 1))
        return "".join(l for i, l in enumerate(lines, start=1) if i not in drop)

    res = inbox_lock.atomic_update(inbox, transform)
    record_executed(root, [t["block_id"] for t in targets],
                    "rules: " + ",".join(sorted(tally)))

    after = census(inbox.read_text(encoding="utf-8"))
    expected = live["live_h2_headers"] - len(to_remove)
    if after["live_h2_headers"] != expected:
        fail(f"post-condition FAILED: expected {expected} live blocks, found "
             f"{after['live_h2_headers']}. The splice removed the wrong amount.",
             "postcondition_failed", before=live["live_h2_headers"],
             after=after["live_h2_headers"], removed_requested=len(to_remove))

    print(json.dumps({
        **plan,
        "routed": route_results,
        "lock": res,
        "live_blocks_after": after["live_h2_headers"],
        "new_corpus_sha256": after["source_sha256"],
        "lines_before": live["total_lines"], "lines_after": after["total_lines"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
