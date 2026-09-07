#!/usr/bin/env python3
"""finding_write.py -- disposition a cross-model finding, atomically.

Why this exists: codex_verify.py writes every finding with "disposition": None, and
cross_model_gate.open_findings() reads that field to decide what /standup keeps
surfacing. Nothing ever WROTE one. Measured 2026-09-06: 90 findings across 12 runs,
90 of them open, zero dispositioned -- and two of the five P0s had already been fixed
in the code on 2026-09-03 while still reporting as open. A queue with no drain is not
a backlog, it is noise that costs credibility every time it is read.

That is the same defect the ledger was built to avoid: for three weeks the career
scanner scored roles a night into a file nothing read.

Addressing: "<run>.<finding id>", 1-based run number as shown by `list`, e.g. "1.F3".
The ledger is append-only, so a run number is stable.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/finding_write.py list [--open] [--severity P0]
  PYTHONIOENCODING=utf-8 python3 tools/finding_write.py set 1.F3 \
      --disposition fixed --why "cross_model_gate.py:236 now requires full coverage"

A disposition ALWAYS carries a reason. An allowlist without justification is how a
gate decays back into "green means done", so --why is required and may not be blank.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inbox_lock  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER_NAME = ".cross-model-ledger.jsonl"

# Matches the vocabulary in .claude/skills/codex-verify/SKILL.md: a finding stays open
# until someone marks it fixed, rejected with a reason, or parked with a reason.
DISPOSITIONS = ("fixed", "rejected", "parked")


def ledger_path(repo_root: Path) -> Path:
    return repo_root / "tools" / LEDGER_NAME


def read_lines(path: Path) -> list[str]:
    """Raw lines, kept verbatim.

    Deliberately NOT json-parsed here. cross_model_gate.read_ledger() silently skips a
    malformed row; a writer that did the same would DELETE it on rewrite. Unparseable
    lines are passed through untouched.
    """
    if not path.is_file():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def parse_addr(addr: str) -> tuple[int, str]:
    """'1.F3' -> (1, 'F3'). Raises ValueError on anything else."""
    run, sep, fid = addr.partition(".")
    if not sep or not run.strip() or not fid.strip():
        raise ValueError(f"address must be '<run>.<finding id>', got {addr!r}")
    if not run.strip().isdigit():
        raise ValueError(f"run number must be a positive integer, got {run!r}")
    n = int(run.strip())
    if n < 1:
        raise ValueError(f"run number is 1-based, got {n}")
    return n, fid.strip()


def collect(repo_root: Path, only_open: bool = False,
            severity: str | None = None,
            unlocated_only: bool = False) -> list[dict]:
    """Every finding, addressed. Malformed rows contribute nothing but are not lost."""
    out = []
    for i, line in enumerate(read_lines(ledger_path(repo_root)), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        for f in row.get("findings") or []:
            if not isinstance(f, dict):
                continue
            disp = (f.get("disposition") or "").strip()
            if only_open and disp:
                continue
            if severity and str(f.get("severity") or "") != severity:
                continue
            if unlocated_only and (f.get("location") or "").strip():
                continue
            out.append({
                "addr": f"{i}.{f.get('id')}",
                "run": i,
                "recorded": row.get("recorded"),
                "target": row.get("target"),
                "report": row.get("report"),
                "severity": f.get("severity"),
                # None on every row written before 2026-09-06. A finding with no
                # location cannot be verified at path:line, which is what the review
                # protocol requires before anything acts on it.
                "location": f.get("location"),
                "summary": f.get("summary"),
                "disposition": disp or None,
                "why": f.get("why"),
            })
    return out


def set_disposition(repo_root: Path, addr: str, disposition: str, why: str,
                    force: bool = False) -> dict:
    """Write one disposition. Returns the updated finding.

    Raises KeyError if the address does not resolve, ValueError on a bad input or on
    an already-dispositioned finding without force. Nothing is written on any error.
    """
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {', '.join(DISPOSITIONS)}, "
                         f"got {disposition!r}")
    if not why or not why.strip():
        raise ValueError("--why is required and may not be blank")

    run_no, fid = parse_addr(addr)
    path = ledger_path(repo_root)

    # LOCK-GUARDED READ-MODIFY-WRITE.
    #
    # The original rewrote the whole ledger from an unlocked snapshot. os.replace makes the
    # RENAME atomic for readers; it does NOT make the read-modify-write atomic against
    # other writers, and this file is append-only shared state. Two effects, both silent:
    #
    #   1. Two dispositions read version V, each changes a different finding, and the
    #      second replacement restores its stale copy of the first one. Both print success.
    #   2. A cross_model_gate.append_row() landing between the read and the replace is
    #      absent from the snapshot and is DELETED by it -- an audit row destroyed by a
    #      command that reports success.
    #
    # It also used a SHARED temp path (`<ledger>.tmp`), so two overlapping writers could
    # have one install the other's bytes and then report its own in-memory result -- a
    # disposition printed for finding A while the file records a change to B.
    #
    # atomic_update closes all of it: it holds the advisory lock across read/transform/
    # write, re-stats immediately before committing so a writer that does NOT take the
    # lock is detected rather than clobbered, and writes through a UNIQUE mkstemp temp in
    # the same directory with fsync before replace.
    #
    # Reusing inbox_lock rather than adding a third lock: its docstring already describes
    # exactly this defect for data/inbox.md, and a second implementation of the same
    # primitive is how the two drift apart.
    # Found by adversarial cross-model verification 2026-09-06 (F1, P0):
    # output/analysis/090626-codex-tools-finding-write-py-does-it-atomical.md
    captured: dict = {}

    def _transform(text: str) -> str:
        # Deliberately re-derived from `text` on EVERY call, not closed over from an
        # earlier read: atomic_update may invoke this more than once (it retries when it
        # detects an external write), and reusing a stale parse is the bug being fixed.
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if run_no > len(lines):
            raise KeyError(f"no run {run_no}: the ledger has {len(lines)} row(s)")

        line = lines[run_no - 1]
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise KeyError(f"run {run_no} is not valid JSON and cannot be edited") from exc
        if not isinstance(row, dict):
            raise KeyError(f"run {run_no} is not an object")

        # EXACTLY ONE match, never "the first". (F2, P1.) Nothing rejects duplicate ids at
        # ingest, so a model emitting two findings called F1 makes `collect()` advertise
        # both at the same address while a first-match write silently pins the earlier one
        # forever: after it is dispositioned the second can never be selected, and --force
        # just rewrites the first again. Failing loudly is the only honest answer, because
        # the address genuinely does not identify one record.
        matches = [f for f in (row.get("findings") or [])
                   if isinstance(f, dict) and str(f.get("id")) == fid]
        if not matches:
            raise KeyError(f"run {run_no} has no finding {fid!r}")
        if len(matches) > 1:
            raise KeyError(
                f"run {run_no} has {len(matches)} findings with id {fid!r}, so {addr!r} "
                f"does not identify one record. Fix the duplicate ids in the ledger row "
                f"before dispositioning it.")
        target = matches[0]

        existing = (target.get("disposition") or "").strip()
        if existing and not force:
            raise ValueError(
                f"{addr} is already dispositioned {existing!r}; pass --force to change it. "
                "Silently flipping a recorded decision is how the record stops meaning "
                "anything.")

        target["disposition"] = disposition
        target["why"] = why.strip()
        target["dispositioned"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        lines[run_no - 1] = json.dumps(row, ensure_ascii=False)
        captured["result"] = {
            "addr": addr, "severity": target.get("severity"),
            "summary": target.get("summary"), "disposition": disposition,
            "why": target["why"], "dispositioned": target["dispositioned"]}
        return "\n".join(lines) + "\n"

    inbox_lock.atomic_update(path, _transform)
    return captured["result"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="show findings and their addresses")
    p_list.add_argument("--open", action="store_true", dest="only_open",
                        help="only findings nobody has dispositioned")
    p_list.add_argument("--severity", default=None, help="filter, e.g. P0")
    p_list.add_argument("--unlocated", action="store_true", dest="unlocated_only",
                        help="only findings with no path:line (unverifiable as written)")

    p_set = sub.add_parser("set", help="disposition one finding")
    p_set.add_argument("addr", help="'<run>.<finding id>', e.g. 1.F3")
    p_set.add_argument("--disposition", required=True, choices=DISPOSITIONS)
    p_set.add_argument("--why", required=True,
                       help="the reason, recorded alongside the disposition")
    p_set.add_argument("--force", action="store_true",
                       help="overwrite an existing disposition")

    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()

    if args.cmd == "list":
        found = collect(root, only_open=args.only_open, severity=args.severity,
                        unlocated_only=args.unlocated_only)
        print(json.dumps({"count": len(found), "findings": found}, indent=2))
        return 0

    try:
        result = set_disposition(root, args.addr, args.disposition, args.why,
                                 force=args.force)
    except (KeyError, ValueError) as exc:
        print(json.dumps({"error": str(exc).strip("'")}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
