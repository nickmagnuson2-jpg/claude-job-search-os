#!/usr/bin/env python3
"""
outreach_status.py — Derive send-state for a recipient from data/outreach-log.md.

WHY THIS EXISTS (read before changing the semantics)
----------------------------------------------------
An interview prep doc's Logistics block once asserted: "CV already sent, do not
re-offer it." The sole basis was that a draft file existed under output/. The CV had
never been delivered: the address bounced and the recipient later asked for it
again. Three false facts, and the damaging one was SUPPRESSIVE: it instructed Nick not
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
artifact scope, the suppressive-claim defect is live again.

Exit codes: 0 clean (including not_found) | 1 unreadable/malformed log or a failed
write-back assertion | 2 ambiguous recipient, ambiguous --set-status target, or a missing
or malformed --append value | 3 unrecognized status | 4 unknown artifact token.

`--append` is the one writer of a NEW row (the three outreach skills call it instead of
editing the table by hand). It refuses an unknown status or artifact token and prints the
row's pointer, `<log>:<line>`, for a BUILD-LOG `sent:` line.
"""
import argparse
import json
import os
import tempfile
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_vocab  # noqa: E402
import md_table  # noqa: E402

DEFAULT_LOG = "data/outreach-log.md"
LOCAL_VALIDATORS = "tools/.local-validators.json"
# Exit code for "there was nothing to validate against". Not 0: until 2026-10-13 a
# missing validators file, or one with no cases for this tool, printed SKIPPED and
# exited as a success, and nothing ran the mode anyway. Not 2: argparse uses it.
NO_CASES_EXIT = 3

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
    # Terminal (2026-10-29, todo #120): the thread is resolved and expects nothing more,
    # so it leaves every awaiting-response list. Before this a "No reply" row sat in
    # /standup's awaiting list and outreach_pending's overdue list forever.
    "Closed",
}
# A bounce is still a transmission — it left Nick's outbox. That is exactly why `sent`
# alone must never license a suppressive claim.
#
# `No reply` is here deliberately: it means "sent, and nobody wrote back", which is a
# transmission with no receipt evidence. It contributes to `sent` and never to
# `delivered`, so it can never unlock suppression. (The spec's state-derivation
# bullet omitted it while its OQ1 resolution required it; OQ1 is the decision.)
SENT_STATUSES = {"Sent", "Replied", "Delivered", "Bounced", "No reply", "Closed"}
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
    return parse_text(_read(path))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LogError(f"cannot read log: {exc}") from exc


def parse_text(text: str) -> list[dict]:
    rows = []
    width = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # A well-formed row splits into ['', 7 or 8 cells, ''] == 9 or 10 parts. The
        # eighth cell is Artifact (added 2026-10-26); a 7-cell log is the pre-migration
        # shape and reads as an empty Artifact on every row.
        if len(cells) not in (9, 10):
            raise LogError(f"malformed table row at line {lineno}: {len(cells) - 2} columns, expected 7 or 8")
        # Every row has the width of the first table row (the header), so a half-done
        # migration fails loudly instead of reading as a mix of recorded and unrecorded.
        if width is None:
            width = len(cells)
        elif len(cells) != width:
            raise LogError(f"malformed table row at line {lineno}: {len(cells) - 2} columns, "
                           f"the header has {width - 2}")
        values = cells[1:-1]
        # Skip the header and the separator BEFORE validating the Artifact cell: the
        # header cell reads `Artifact` and the separator `---`, and neither is a token.
        if values[0].lower() == "date" or set("".join(values)) <= set("- "):
            continue  # header row or the |---| separator
        row = dict(zip(COLUMNS, values))
        row["artifact"] = values[7] if len(values) == 8 else ""
        problem = artifact_vocab.check_cell(row["artifact"])
        if problem:
            raise LogError(f"bad Artifact cell at line {lineno}: {problem}")
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
    # artifact (a CV merely offered, a deck promised for later, a thread whose SUBJECT is
    # about a resume) is not receipt evidence -- and on the live log such rows carry
    # `Replied`, so scoping
    # on mentions returns delivered:true for a CV that was never sent. That is the
    # 2026-08-10 defect wearing a different hat. See artifact_vocab.find_transmitted_in_text.
    # A filled Artifact cell is the record and wins over the Summary text; a row with an
    # empty cell (not recorded, the pre-column history) falls back to the Summary reading.
    # Without the cell the new types (page, takehome, note) were invisible to this query,
    # which reads only words like "CV" or "deck" out of the Summary (review, 2026-10-26).
    artifact_rows = []
    if artifact:
        artifact_rows = [
            r for r in known
            if (r.get("artifact", "").split(":", 1)[0] == artifact if r.get("artifact")
                else artifact in artifact_vocab.find_transmitted_in_text(r["summary"]))
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

    latest = _latest(known) if known else None
    # Only rows NEWER than the last Replied or Closed row decide: a Replied or Closed
    # ends that loop, and a later send opens a new one (10a, 849.F1, 856.F1; review
    # 2026-10-29: "a Replied anywhere" kept a recipient who answered once in May from
    # ever awaiting a reply again).
    closers = [r for r in known if r["status"] in ("Replied", "Closed")]
    cut = _latest(closers) if closers else None
    after = [r for r in known
             if cut is None or (r["date"], r["_line"]) > (cut["date"], cut["_line"])]
    awaiting = any(r["status"] in SENT_STATUSES and r["status"] != "Replied" for r in after)

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


def _real_date(value: str) -> bool:
    """YYYY-MM-DD and a date that exists: 2026-13-40 and 2026-02-30 are refused (694.F1)."""
    import datetime as _dt
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _row_has_artifact(row: dict, artifact: str) -> bool:
    """The Artifact cell names this type; an empty cell (pre-column history) falls back to
    the Summary text. One rule for set-status and the queries (review round 12, 705.F2)."""
    cell = row.get("artifact", "")
    if cell:
        return cell.split(":", 1)[0] == artifact
    return artifact in artifact_vocab.find_in_text(row["summary"])


def _public(row: dict) -> dict:
    return {**{k: row[k] for k in COLUMNS}, "artifact": row.get("artifact", "")}


# ---------------------------------------------------------------- writer


OPEN_STATUSES = {"Drafted", "Sent"}


def set_status(path: Path, recipient: str, date: str, artifact: str | None,
               new_status: str) -> tuple[int, dict]:
    """Set one row's Status cell, addressed by (recipient AND date [AND artifact]).

    `artifact` is optional, for messages that carry none; then two rows on the date refuse.
    """
    def select(rows):
        resolution, matched, candidates = resolve_recipient(rows, recipient, None)
        if resolution == "ambiguous":
            return _refuse("ambiguous recipient", candidates)
        targets = [
            r for r in matched
            if r["date"] == date
            and (artifact is None or _row_has_artifact(r, artifact))
        ]
        if len(targets) != 1:
            reason = f"expected exactly 1 matching row, found {len(targets)}"
            if len(targets) > 1:
                reason += "; narrow it with --artifact"
            return _refuse(reason, [_public(r) for r in targets])
        return targets[0]

    return write_status(path, select, new_status)


def set_latest_open(path: Path, recipient: str, new_status: str) -> tuple[int, dict]:
    """Set the status of the recipient's newest open (Drafted/Sent) row.

    The log is chronological, so on a same-date tie the later row is the later message.
    This is the one entry point for "they replied" from other tools.
    """
    def select(rows):
        resolution, matched, candidates = resolve_recipient(rows, recipient, None)
        if resolution == "ambiguous":
            return _refuse("ambiguous recipient", candidates)
        open_rows = [r for r in matched if r["status"] in OPEN_STATUSES]
        if not open_rows:
            return _refuse("no open row for recipient", [])
        return _latest(open_rows)

    return write_status(path, select, new_status)


def _refuse(reason: str, candidates: list) -> tuple[int, dict]:
    return 2, {"action": "set-status", "ok": False, "reason": reason, "candidates": candidates}


WRITE_ATTEMPTS = 3


def write_status(path: Path, select, new_status: str) -> tuple[int, dict]:
    """The only code that writes a Status cell. Same status = no-op.

    `select(rows)` returns the target row, or a `(code, payload)` refusal. Selection and
    the write use the same read of the file, and the file is re-read just before the
    atomic replace: if anything changed in between, the attempt is discarded and redone
    from a fresh read, so a concurrent edit to another row is kept rather than overwritten.
    Residual: a write landing between that last re-read and the replace itself.
    """
    for _ in range(WRITE_ATTEMPTS):
        text = _read(path)
        target = select(parse_text(text))
        if isinstance(target, tuple):
            return target

        lines = text.splitlines(keepends=True)
        idx = target["_line"] - 1
        cells = lines[idx].rstrip("\n").split("|")
        result = {"action": "set-status", "row": _public(target),
                  "before": cells[7].strip(), "after": new_status, "ok": True}
        if result["before"] == new_status:
            return 0, result
        cells[7] = f" {new_status} "
        lines[idx] = "|".join(cells) + ("\n" if lines[idx].endswith("\n") else "")

        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("".join(lines))
            if _read(path) != text:
                continue  # someone else wrote; redo from their version
            os.replace(tmp, path)
            return 0, result
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return 1, {"action": "set-status", "ok": False,
               "reason": f"the log changed during each of {WRITE_ATTEMPTS} attempts; re-run"}


# ---------------------------------------------------------------- row writer


# Cell order of a migrated (8-column) log. `--append` takes each as a flag of the same name.
APPEND_FIELDS = ("date", "skill", "channel", "recipient", "company", "summary", "status",
                 "artifact")
WIDE_LOG_PARTS = 10  # ['', 8 cells, '']


def _table_width(text: str) -> int:
    """Split-part count of the first table row (the header), 0 if there is no table."""
    first = next((ln for ln in text.splitlines() if ln.lstrip().startswith("|")), "")
    return len(first.split("|")) if first else 0


def _clean(value: str) -> str:
    """One line, no stray '|': a value can neither break its row nor add a column."""
    return md_table.safe_cell(" ".join(value.split()))


def check_append(values: dict) -> tuple[int, dict] | None:
    """None if `values` can be written as a row, else the (code, payload) refusal."""
    missing = [f for f in APPEND_FIELDS if not _clean(values.get(f) or "")]
    if missing:
        return 2, {"action": "append", "ok": False,
                   "reason": f"--append requires: {', '.join('--' + m for m in missing)}"
                             " (pass --artifact none when nothing rode along)"}
    if not _real_date(values["date"]):
        return 2, {"action": "append", "ok": False,
                   "reason": f"--date must be YYYY-MM-DD, got {values['date']!r}"}
    if values["status"] not in KNOWN_STATUSES:
        return 3, {"action": "append", "ok": False,
                   "reason": f"unrecognized status: {values['status']}",
                   "valid_statuses": sorted(KNOWN_STATUSES)}
    problem = artifact_vocab.check_cell(_clean(values["artifact"]))
    if problem:
        return 4, {"action": "append", "ok": False, "reason": problem,
                   "valid_tokens": artifact_vocab.valid_tokens()}
    return None


def insert_row(text: str, values: dict) -> tuple[str, int]:
    """The log text with one row inserted in date order, and that row's 1-based line.

    The row goes after the last row dated on or before it, so a same-day send lands
    after the day's earlier ones and a backdated send lands among its own dates. Raises
    LogError on a malformed log, or on a 7-column log (run the Artifact migration first).
    """
    rows = parse_text(text)
    if _table_width(text) != WIDE_LOG_PARTS:
        raise LogError("the log has no Artifact column; run the Artifact migration first")
    lines = text.splitlines(keepends=True)
    earlier = [r["_line"] for r in rows if r["date"] <= values["date"]]
    idx = max(earlier) if earlier else rows[0]["_line"] - 1
    if not lines[idx - 1].endswith("\n"):
        lines[idx - 1] += "\n"
    lines.insert(idx, "| " + " | ".join(_clean(values[f]) for f in APPEND_FIELDS) + " |\n")
    return "".join(lines), idx + 1


def append_row(path: Path, values: dict) -> tuple[int, dict]:
    """Validate, insert and write one row atomically. Payload carries `pointer`.

    The file is re-read just before the write; if it changed since the insert was
    computed, the attempt is redone from the fresh read, as write_status does.
    """
    refusal = check_append(values)
    if refusal:
        return refusal
    for _ in range(WRITE_ATTEMPTS):
        text = _read(path)
        new_text, line = insert_row(text, values)
        if _read(path) != text:
            continue  # someone else wrote; redo from their version
        md_table.write_atomic(path, new_text)
        row = {f: _clean(values[f]) for f in APPEND_FIELDS}
        return 0, {"action": "append", "ok": True, "row": row, "line": line,
                   "pointer": f"{path}:{line}"}
    return 1, {"action": "append", "ok": False,
               "reason": f"the log changed during each of {WRITE_ATTEMPTS} attempts; re-run"}


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

    Absent file, or no cases for this tool -> NO_CASES on stdout and exit 3. A validator
    that returns success when its expectations are missing is worse than no validator.
    """
    vpath = Path(LOCAL_VALIDATORS)
    if not vpath.exists():
        return NO_CASES_EXIT, {"status": "NO_CASES", "reason": f"{LOCAL_VALIDATORS} absent"}

    cases = json.loads(vpath.read_text(encoding="utf-8")).get("w1", [])
    if not cases:
        return NO_CASES_EXIT, {"status": "NO_CASES",
                               "reason": f"no 'w1' cases in {LOCAL_VALIDATORS}"}

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
    parser.add_argument("--latest", action="store_true",
                        help="with --set-status: target the newest open row instead of --date")
    parser.add_argument("--date", help="required with --set-status")
    parser.add_argument("--status", help="required with --set-status")
    parser.add_argument("--validate-local", action="store_true", dest="validate_local")
    parser.add_argument("--append", action="store_true",
                        help="write one new row; needs --date --skill --channel --recipient "
                             "--company --summary --status --artifact (cell order), where "
                             "--artifact is <type>:<ref> or none")
    parser.add_argument("--skill")
    parser.add_argument("--channel")
    parser.add_argument("--summary")
    args = parser.parse_args()

    log_path = Path(args.log)

    if args.validate_local:
        code, payload = validate_local(log_path)
        print(json.dumps(payload, ensure_ascii=False))
        return code

    if args.append:
        if args.set_status:
            print(json.dumps({"error": "--append and --set-status are mutually exclusive"}))
            return 2
        try:
            code, payload = append_row(log_path, {f: getattr(args, f) for f in APPEND_FIELDS})
        except LogError as exc:
            code, payload = 1, {"action": "append", "ok": False, "reason": str(exc)}
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
            if args.latest and args.date:
                print(json.dumps({"error": "--latest and --date are mutually exclusive"}))
                return 2
            required = ("recipient", "status") if args.latest else ("recipient", "date", "status")
            missing = [f for f in required if not getattr(args, f)]
            if missing:
                print(json.dumps({"error": f"--set-status requires: {', '.join('--' + m for m in missing)}"}))
                return 2
            if args.status not in KNOWN_STATUSES:
                print(json.dumps({"error": f"unrecognized status: {args.status}",
                                  "valid_statuses": sorted(KNOWN_STATUSES)}))
                return 3
            if args.latest:
                code, payload = set_latest_open(log_path, args.recipient, args.status)
            else:
                code, payload = set_status(log_path, args.recipient, args.date,
                                           args.artifact, args.status)
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
