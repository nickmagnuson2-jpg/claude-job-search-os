#!/usr/bin/env python3
"""
outreach_status.py — Derive send-state for a recipient from data/outreach-log.md.

WHY THIS EXISTS (read before changing the semantics)
----------------------------------------------------
On 2026-08-10 a prep doc's Logistics block asserted: "CV already sent 8/4, do not
re-offer it." The sole basis was that a draft file existed under output/. The CV had
never been delivered — the address bounced on 8/7 and the recipient asked for it again
on 8/11. Three false facts, and the damaging one was SUPPRESSIVE: it instructed Nick not
to take the corrective action.

Two corrections to the naive fix are load-bearing, and both are easy to undo by
accident:

  1. TRANSMISSION IS NOT RECEIPT. The log records that recipient as `Sent`. A tool keyed
     on a single `sent` boolean returns true and re-authorizes the identical false
     sentence. `sent` and `delivered` are separate fields here, and only `delivered`
     may unlock suppressive phrasing.

  2. RECEIPT IS PER-ARTIFACT, NOT PER-RECIPIENT. Log rows are per-message and the only
     artifact signal is free text in the Summary cell. The defect recipient had
     `Replied` rows on UNRELATED threads, so a recipient-scoped `delivered` returns true
     and re-authorizes the same sentence a second way. `delivered` is therefore only
     ever computed over rows matching a named artifact token, and a query with no
     --artifact can never report delivered:true.

Everything else is bookkeeping. If a future edit collapses sent/delivered or drops the
artifact scope, the 8/10 defect is live again.

Exit codes: 0 clean (including not_found) | 1 unreadable/malformed log or a failed
write-back assertion | 2 ambiguous recipient or ambiguous --set-status target |
3 unrecognized status | 4 unknown artifact token.
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_vocab  # noqa: E402

DEFAULT_LOG = "data/outreach-log.md"
LOCAL_VALIDATORS = "tools/.local-validators.json"

# The frozen stamp format. check_prep_doc.py parses this exact string; bumping the
# format means bumping TOOL_VERSION and teaching check_prep_doc the new shape.
TOOL_VERSION = 2
STAMP_TEMPLATE = (
    "<!-- outreach_status: recipient={recipient} artifact={artifact} "
    "delivered={delivered} as_of={as_of} tool_version={version} -->"
)

KNOWN_STATUSES = {
    "Drafted", "Sent", "Replied", "No reply",
    "Bounced", "Delivered", "Delivery unknown",
}
# A bounce is still a transmission — it left Nick's outbox. That is exactly why `sent`
# alone must never license a suppressive claim.
#
# `No reply` is here deliberately: it means "sent, and nobody wrote back", which is a
# transmission with no receipt evidence. It contributes to `sent` and never to
# `delivered`, so it can never unlock suppression. (The spec's state-derivation
# bullet omitted it while its OQ1 resolution required it; OQ1 is the decision.)
SENT_STATUSES = {"Sent", "Replied", "Delivered", "Bounced", "No reply"}
# Statuses that carry information about whether the message actually ARRIVED.
RECEIPT_STATUSES = {"Replied", "Delivered", "Bounced"}
POSITIVE_RECEIPT = {"Replied", "Delivered"}

COLUMNS = ("date", "skill", "channel", "recipient", "company", "summary", "status")


# ---------------------------------------------------------------- normalization


def normalize(text: str) -> str:
    """Casefold, strip accents, collapse whitespace, strip surrounding punctuation."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    collapsed = re.sub(r"\s+", " ", stripped.casefold()).strip()
    return collapsed.strip(" .,;:!?\"'`()[]")


def split_recipients(cell: str) -> list[str]:
    """Split a Recipient cell into candidate people.

    Live cells hold things like "Jane Doe + John Smith" and
    "John Smith (via Robin)". The parenthetical is an introduction path, not a
    second recipient, so it is dropped rather than split out as a person.
    """
    cell = re.sub(r"\((?:via|through|cc)\b[^)]*\)", " ", cell or "", flags=re.I)
    parts = re.split(r"\s*[+&,]\s*", cell)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------- log parsing


class LogError(Exception):
    """Unreadable or structurally malformed log (exit 1)."""


def parse_log(path: Path) -> list[dict]:
    """Parse the markdown table into row dicts, preserving file order."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LogError(f"cannot read log: {exc}") from exc

    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # A well-formed row splits into ['', 7 cells, ''] == 9 parts.
        if len(cells) != 9:
            raise LogError(f"malformed table row at line {lineno}: {len(cells) - 2} columns, expected 7")
        values = cells[1:8]
        if values[0].lower() == "date" or set("".join(values)) <= set("- "):
            continue  # header row or the |---| separator
        row = dict(zip(COLUMNS, values))
        row["_line"] = lineno
        rows.append(row)
    if not rows:
        raise LogError("no data rows found in log")
    return rows


# ---------------------------------------------------------------- resolution


def resolve_recipient(rows: list[dict], query: str, company: str | None) -> tuple[str, list[dict], list[str]]:
    """Resolve `query` to rows via the ladder. Returns (resolution, rows, candidates).

    Ladder — the FIRST step yielding exactly one distinct person wins:
      1. exact normalized full-name match
      2. last-token (surname) match
      3. single-token candidate matched against a single-token query

    Steps 2 and 3 yielding more than one distinct person is `ambiguous`, never a guess.
    """
    pool = rows
    if company:
        needle = normalize(company)
        pool = [r for r in pool if needle in normalize(r["company"])]

    nquery = normalize(query)
    qtokens = nquery.split()

    # person-key -> rows, built once and reused by every ladder step
    exact: dict[str, list[dict]] = {}
    surname: dict[str, list[dict]] = {}
    single: dict[str, list[dict]] = {}

    for row in pool:
        for cand in split_recipients(row["recipient"]):
            ncand = normalize(cand)
            if not ncand:
                continue
            ctokens = ncand.split()
            if ncand == nquery:
                exact.setdefault(ncand, []).append(row)
            if len(qtokens) > 0 and ctokens and ctokens[-1] == qtokens[-1]:
                surname.setdefault(ncand, []).append(row)
            if len(ctokens) == 1 and len(qtokens) == 1 and ctokens[0] == qtokens[0]:
                single.setdefault(ncand, []).append(row)

    for resolution, bucket in (("exact", exact), ("surname", surname), ("single_token", single)):
        if not bucket:
            continue
        if len(bucket) == 1:
            only = next(iter(bucket.values()))
            # de-dup rows while preserving file order (a row can match twice)
            seen, uniq = set(), []
            for r in only:
                if id(r) not in seen:
                    seen.add(id(r))
                    uniq.append(r)
            return resolution, uniq, sorted(bucket)
        return "ambiguous", [], sorted(bucket)

    return "not_found", [], []


# ---------------------------------------------------------------- state


def derive_state(matched: list[dict], artifact: str | None) -> dict:
    """Compute sent / delivered / awaiting_reply. Monotonic over rows, not latest-wins."""
    statuses = [r["status"] for r in matched]
    unrecognized = sorted({s for s in statuses if s not in KNOWN_STATUSES})
    known = [r for r in matched if r["status"] in KNOWN_STATUSES]

    # TRANSMISSION sense, not mention sense. A Summary cell that merely NAMES the
    # artifact ("offered resume", "CV promised for <date>", "Re: send-resume thread") is
    # not receipt evidence -- and on the live log those rows carry `Replied`, so scoping
    # on mentions returns delivered:true for a CV that was never sent. That is the
    # 2026-08-10 defect wearing a different hat. See artifact_vocab.find_transmitted_in_text.
    artifact_rows = []
    if artifact:
        artifact_rows = [
            r for r in known
            if artifact in artifact_vocab.find_transmitted_in_text(r["summary"])
        ]

    sent = any(r["status"] in SENT_STATUSES for r in known)

    # Tri-state, and ONLY over artifact-scoped rows. With no --artifact this is always
    # "unknown" -- a recipient-level query must never be able to license suppression.
    delivered: object = "unknown"
    if artifact_rows:
        if any(r["status"] in POSITIVE_RECEIPT for r in artifact_rows):
            delivered = True
        else:
            receipts = [r for r in artifact_rows if r["status"] in RECEIPT_STATUSES]
            if receipts and _latest(receipts)["status"] == "Bounced":
                delivered = False

    awaiting = sent and not any(r["status"] == "Replied" for r in known)
    latest = _latest(known) if known else None

    return {
        "matched_rows": [_public(r) for r in matched],
        "artifact_rows": [_public(r) for r in artifact_rows],
        "latest_status": latest["status"] if latest else "",
        "sent": sent,
        "delivered": delivered,
        "awaiting_reply": awaiting,
        "unrecognized_statuses": unrecognized,
    }


def _latest(rows: list[dict]) -> dict:
    """Latest by date; same-date ties resolve to the last-occurring row in file order."""
    return max(rows, key=lambda r: (r["date"], r["_line"]))


def _public(row: dict) -> dict:
    return {k: row[k] for k in COLUMNS}


# ---------------------------------------------------------------- writer


def set_status(path: Path, rows: list[dict], recipient: str, date: str,
               artifact: str, new_status: str) -> tuple[int, dict]:
    """Set one row's Status cell, addressed by (recipient AND date AND artifact).

    Writes nothing unless exactly one row matches. Re-reads and asserts after the write;
    any assertion failure rolls the file back to its pre-write bytes.
    """
    resolution, matched, candidates = resolve_recipient(rows, recipient, None)
    if resolution == "ambiguous":
        return 2, {"action": "set-status", "ok": False,
                   "reason": "ambiguous recipient", "candidates": candidates}

    targets = [
        r for r in matched
        if r["date"] == date and artifact in artifact_vocab.find_in_text(r["summary"])
    ]
    if len(targets) != 1:
        return 2, {
            "action": "set-status", "ok": False,
            "reason": f"expected exactly 1 matching row, found {len(targets)}",
            "candidates": [_public(r) for r in targets],
        }

    target = targets[0]
    before_text = path.read_text(encoding="utf-8")
    before_lines = before_text.splitlines(keepends=True)
    idx = target["_line"] - 1
    original_line = before_lines[idx]

    cells = original_line.rstrip("\n").split("|")
    old_status = cells[7].strip()
    cells[7] = f" {new_status} "
    new_line = "|".join(cells) + ("\n" if original_line.endswith("\n") else "")

    after_lines = list(before_lines)
    after_lines[idx] = new_line
    path.write_text("".join(after_lines), encoding="utf-8")

    # Verify, and roll back on any surprise rather than leaving a half-edit behind.
    verify = path.read_text(encoding="utf-8").splitlines(keepends=True)
    problems = []
    if len(verify) != len(before_lines):
        problems.append(f"line count changed {len(before_lines)} -> {len(verify)}")
    elif verify[idx].split("|")[7].strip() != new_status:
        problems.append("target cell does not hold the new value")
    else:
        changed = [i for i, (a, b) in enumerate(zip(before_lines, verify)) if a != b]
        if changed != [idx]:
            problems.append(f"unexpected lines changed: {changed}")

    if problems:
        path.write_text(before_text, encoding="utf-8")
        return 1, {"action": "set-status", "ok": False,
                   "reason": "; ".join(problems), "rolled_back": True}

    return 0, {"action": "set-status", "row": _public(target),
               "before": old_status, "after": new_status, "ok": True}


# ---------------------------------------------------------------- query


def query_status(log_path: Path, recipient: str, company: str | None,
                 artifact: str | None, as_of: str | None) -> tuple[int, dict]:
    rows = parse_log(log_path)
    if as_of:
        rows = [r for r in rows if r["date"] <= as_of]

    resolution, matched, candidates = resolve_recipient(rows, recipient, company)

    result = {
        "recipient": recipient,
        "artifact": artifact or "none",
        "resolution": resolution,
        "candidates": candidates if resolution == "ambiguous" else [],
        "matched_rows": [],
        "artifact_rows": [],
        "latest_status": "",
        "sent": False,
        "delivered": "unknown",
        "awaiting_reply": False,
        "unrecognized_statuses": [],
        "as_of": as_of or "",
        "tool_version": TOOL_VERSION,
    }

    if resolution == "ambiguous":
        return 2, result
    if resolution == "not_found":
        return 0, result

    result.update(derive_state(matched, artifact))
    return (3 if result["unrecognized_statuses"] else 0), result


def render_stamp(result: dict) -> str:
    delivered = result["delivered"]
    rendered = delivered if isinstance(delivered, str) else ("true" if delivered else "false")
    return STAMP_TEMPLATE.format(
        recipient=normalize(result["recipient"]),
        artifact=result["artifact"],
        delivered=rendered,
        as_of=result["as_of"] or "",
        version=TOOL_VERSION,
    )


# ---------------------------------------------------------------- validate-local


def validate_local(log_path: Path) -> tuple[int, dict]:
    """Tier 2: assert real-data expectations from the gitignored validators file.

    Absent file -> SKIPPED on stdout and exit 0. Never a silent pass: a validator that
    quietly returns success when its expectations are missing is worse than no validator.
    """
    vpath = Path(LOCAL_VALIDATORS)
    if not vpath.exists():
        return 0, {"status": "SKIPPED", "reason": f"{LOCAL_VALIDATORS} absent"}

    cases = json.loads(vpath.read_text(encoding="utf-8")).get("w1", [])
    if not cases:
        return 0, {"status": "SKIPPED", "reason": f"no 'w1' cases in {LOCAL_VALIDATORS}"}

    results, failed = [], 0
    for case in cases:
        code, actual = query_status(
            log_path, case["recipient"], case.get("company"),
            case.get("artifact"), case.get("as_of"),
        )
        mismatches = {
            k: {"expected": v, "actual": actual.get(k)}
            for k, v in case.get("expect", {}).items() if actual.get(k) != v
        }
        failed += bool(mismatches)
        results.append({
            "recipient": case["recipient"], "artifact": case.get("artifact"),
            "as_of": case.get("as_of"), "exit_code": code,
            "resolution": actual["resolution"], "sent": actual["sent"],
            "delivered": actual["delivered"],
            "pass": not mismatches, "mismatches": mismatches,
        })

    return (1 if failed else 0), {
        "status": "FAIL" if failed else "PASS",
        "cases": len(results), "failed": failed, "results": results,
    }


# ---------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    # --recipient is NOT required at the parser level: --validate-local is a valid
    # zero-argument invocation and argparse must not SystemExit(2) on it.
    parser.add_argument("--recipient")
    parser.add_argument("--company")
    parser.add_argument("--artifact")
    parser.add_argument("--as-of", dest="as_of")
    parser.add_argument("--log", default=DEFAULT_LOG)
    parser.add_argument("--stamp", action="store_true",
                        help="emit the frozen provenance comment instead of JSON")
    parser.add_argument("--set-status", action="store_true")
    parser.add_argument("--date", help="required with --set-status")
    parser.add_argument("--status", help="required with --set-status")
    parser.add_argument("--validate-local", action="store_true", dest="validate_local")
    args = parser.parse_args()

    log_path = Path(args.log)

    if args.validate_local:
        code, payload = validate_local(log_path)
        print(json.dumps(payload, ensure_ascii=False))
        return code

    if args.artifact:
        canon = artifact_vocab.canonicalize(args.artifact)
        if canon is None:
            print(json.dumps({"error": f"unknown artifact token: {args.artifact}",
                              "valid_tokens": artifact_vocab.valid_tokens()}))
            return 4
        args.artifact = canon

    try:
        if args.set_status:
            missing = [f for f in ("recipient", "date", "artifact", "status")
                       if not getattr(args, f)]
            if missing:
                print(json.dumps({"error": f"--set-status requires: {', '.join('--' + m for m in missing)}"}))
                return 2
            if args.status not in KNOWN_STATUSES:
                print(json.dumps({"error": f"unrecognized status: {args.status}",
                                  "valid_statuses": sorted(KNOWN_STATUSES)}))
                return 3
            rows = parse_log(log_path)
            code, payload = set_status(log_path, rows, args.recipient,
                                       args.date, args.artifact, args.status)
            print(json.dumps(payload, ensure_ascii=False))
            return code

        if not args.recipient:
            print(json.dumps({"error": "--recipient is required (or use --validate-local)"}))
            return 2

        code, result = query_status(log_path, args.recipient, args.company,
                                    args.artifact, args.as_of)
    except LogError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1

    print(render_stamp(result) if args.stamp else json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
