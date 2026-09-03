#!/usr/bin/env python3
"""cross_model_gate.py -- require a cross-model verification before a big push.

WHY THIS EXISTS
---------------
On 2026-09-02 Codex found three real defects Claude had missed, then broke a framing
Claude had already built a tool on, then found that a fix for a silent-loss defect had
REBUILT the same defect class and was losing roles live. Three for three on finding
something the author could not see. Nick's conclusion, the same day: "I want to make
sure that for all of these important things we run codex as a verification."

That was recorded as a decision, and by this repo's own enforcement rule a decision is
not a tier. The proof arrived the next morning: the verification ran only because Nick
asked for it again, in a message. "Written down and followed" is not built.

WHAT THIS IS
------------
The third tier from CLAUDE.md's ladder -- a check a gate reads. It fires at PUSH, not
at commit: pushing is the outward-facing act, it is already where the PII gate makes
Nick stop, and commits are far too frequent to carry a judgement call without becoming
noise.

WHY THE WAIVER IS AN ENV VAR AND NOT A FIELD
--------------------------------------------
Nick, 2026-09-03, on what keeps this from becoming a checkbox: "As long as it's always
something I need to think about whether or not I want to have codex run a validation,
then that's how it doesn't become theater." A waiver written into a file acquires a
default value, and a default is the opposite of a decision. An env var on the push is
typed once, deliberately, per push -- and it is RECORDED, so the waivers can be counted
and surfaced. Drift you can see is drift you can correct.

FAILS CLOSED. A missing or corrupt ledger BLOCKS. A guard whose state file is absent
must never read as satisfied; that is the false-zero defect wearing a different hat,
and this repo has now shipped it twice.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER_NAME = ".cross-model-ledger.jsonl"

# A change this size is where a wrong call stops being cheap to undo. Calibrated
# against the 2026-09-02 drain work, which was ~250 changed lines across the scanner
# and the reader and carried three P0s.
LARGE_CHANGE_LINES = 120

# Breadth is its own signal, independent of volume. Ten files at eleven lines each is
# 110 lines -- under the threshold -- and is a wide refactor, the shape most likely to
# break something at a seam no single file's tests cover.
BROAD_CHANGE_FILES = 6

# Documents from which a wrong decision PROPAGATES: a handoff seeds the next session, a
# plan seeds the build, a build log becomes the record everything later cites.
GOVERNED_DOC_RE = re.compile(
    r"output/analysis/.*(handoff|build-log|plan|HANDOFF|BUILD-LOG|PLAN)", re.I)

# Size is the wrong measure for a guard: a one-line edit to a hook that BLOCKs can turn
# it into a no-op, and a no-op guard is worse than no guard because it reports success.
HOOK_RE = re.compile(r"tools/(check_|prepush_|.*_guard)")

CODE_RE = re.compile(r"^(tools/|\.claude/skills/).*\.(py|sh)$")


@dataclass
class Verdict:
    qualified: bool = False
    reason: str = ""
    blocked: bool = False
    message: str = ""
    changed: list = field(default_factory=list)
    # The paths that CAUSED qualification, not merely the ones in the push. The
    # suggested command is built from these: offering the alphabetically-first files
    # (a .gitignore edit) instead of the 600-line code change makes the remedy read as
    # boilerplate, and a remedy nobody follows is a gate nobody passes honestly.
    triggers: list = field(default_factory=list)


def ledger_path(repo_root: Path) -> Path:
    return Path(repo_root) / "tools" / LEDGER_NAME


def qualifies(changes: list[tuple[str, int, int]]) -> Verdict:
    """Does this diff need a second model looking at it?

    `changes` is [(path, added, removed)], the shape `git diff --numstat` gives.

    Deliberately NOT "everything qualifies". If the gate fires on every push the waiver
    becomes a reflex keystroke and the whole mechanism is theatre -- which is the
    specific failure Nick named when he agreed to build it.
    """
    hooks = [p for p, _a, _r in changes if HOOK_RE.match(p)]
    if hooks:
        return Verdict(True, f"changes a wired hook or guard: {', '.join(hooks[:3])}",
                       triggers=hooks)

    docs = [p for p, _a, _r in changes if GOVERNED_DOC_RE.search(p)]
    if docs:
        return Verdict(True, f"changes a governed document: {', '.join(docs[:3])}",
                       triggers=docs)

    code = [(p, a, r) for p, a, r in changes if CODE_RE.match(p)]
    touched = sum(a + r for _p, a, r in code)
    if touched >= LARGE_CHANGE_LINES:
        ranked = [p for p, _a, _r in sorted(code, key=lambda c: -(c[1] + c[2]))]
        names = ", ".join(ranked[:3])
        return Verdict(True, f"large code change ({touched} lines): {names}",
                       triggers=ranked)
    if len(code) >= BROAD_CHANGE_FILES:
        return Verdict(True, f"broad code change ({len(code)} files touched)",
                       triggers=[p for p, _a, _r in code])

    return Verdict(False, "no governed document, wired hook, or large code change")


def read_ledger(repo_root: Path) -> list[dict]:
    """Rows, or a raise-free empty list. Callers MUST distinguish empty from satisfied."""
    path = ledger_path(repo_root)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append_row(repo_root: Path, row: dict) -> Path:
    path = ledger_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return path


def record_waiver(repo_root: Path, paths: list[str], reason: str) -> Path:
    """A waiver is a ledger row like any other. Silence would make it uncountable."""
    return append_row(repo_root, {
        "recorded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": "WAIVED", "report": None, "paths": list(paths),
        "findings": [], "waived": True, "reason": reason})


def waiver_count(repo_root: Path) -> int:
    return sum(1 for r in read_ledger(repo_root) if r.get("waived"))


def open_findings(repo_root: Path) -> list[dict]:
    """Findings nobody has dispositioned yet.

    THE DRAIN LESSON, applied to this tool before it can repeat it. From 2026-08-11 to
    2026-09-02 the career scanner scored ~30 roles a night into a file nothing read. A
    Codex report written to output/analysis/ and consumed by no one is that same defect
    in a new costume, so every finding carries a disposition and /standup surfaces the
    ones that do not have one.
    """
    out = []
    for row in read_ledger(repo_root):
        for f in row.get("findings") or []:
            if not isinstance(f, dict):
                continue
            if not (f.get("disposition") or "").strip():
                out.append(f)
    return out


SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def summary(repo_root: Path) -> str:
    """What /standup renders. THE CONSUMER.

    Without this the ledger is a write-only file and this tool reproduces the exact
    defect it was built in the shadow of: the career scanner scored ~30 roles a night
    for three weeks into a file nothing read, with no error anywhere.
    """
    openf = sorted(open_findings(repo_root),
                   key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 9))
    waivers = waiver_count(repo_root)
    if not openf and not waivers:
        return ""            # renders nothing; a daily "0" trains the reader to skip
    lines = []
    for f in openf:
        lines.append(f"  [{f.get('severity', 'P2')}] {f.get('summary', '')}")
    head = f"{len(openf)} open cross-model finding(s)" if openf else ""
    if waivers:
        w = (f"{waivers} cross-model waiver(s) recorded -- skipping is meant to be a "
             f"deliberate act; a rising count means the gate is being routed around")
        head = f"{head}\n{w}" if head else w
    return "\n".join([head] + lines) if lines else head


def _parse_ts(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.timestamp()


def check(repo_root: Path, changes: list[tuple[str, int, int]],
          since: float) -> Verdict:
    """Block a qualifying push that no verification covers.

    `since` is the epoch time of the oldest commit being pushed: a verification that
    predates the work is not a verification of it.
    """
    v = qualifies(changes)
    if not v.qualified:
        v.blocked = False
        v.message = "no cross-model verification required"
        return v

    changed = {p for p, _a, _r in changes}
    rows = read_ledger(repo_root)
    stale = False
    for row in rows:
        covered = set(row.get("paths") or [])
        if not covered & changed:
            continue
        recorded = _parse_ts(row.get("recorded"))
        if recorded is not None and recorded < since:
            stale = True
            continue
        v.blocked = False
        kind = "waiver" if row.get("waived") else "verification"
        v.message = f"cleared by a recorded {kind} at {row.get('recorded')}"
        return v

    v.blocked = True
    v.changed = sorted(changed)
    suggest = " ".join((v.triggers or v.changed)[:3])
    older = (" The only matching record is OLDER than the work, so it verified "
             "something else." if stale else "")
    v.message = (
        f"This push {v.reason}.{older}\n"
        f"No cross-model verification covers it.\n\n"
        f"  Run one:   PYTHONIOENCODING=utf-8 python3 tools/codex_verify.py \\\n"
        f"               --target '<what to check>' --paths {suggest}\n"
        f"  Or waive:  CODEX_VERIFY_WAIVE='<why>' git push\n\n"
        f"A waiver is recorded and counted; /standup surfaces the running total.")
    return v


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--since", type=float, default=0.0)
    ap.add_argument("--numstat", default="-",
                    help="path to `git diff --numstat` output, or - for stdin")
    args = ap.parse_args(argv)

    raw = sys.stdin.read() if args.numstat == "-" else \
        Path(args.numstat).read_text(encoding="utf-8")
    changes = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, r, path = parts
        changes.append((path, int(a) if a.isdigit() else 0,
                        int(r) if r.isdigit() else 0))

    root = Path(args.repo_root)
    waive = os.environ.get("CODEX_VERIFY_WAIVE", "").strip()
    if waive:
        v = qualifies(changes)
        if v.qualified:
            record_waiver(root, [p for p, _a, _r in changes], waive)
            print(f"cross-model verification WAIVED: {waive}\n"
                  f"  recorded; total waivers: {waiver_count(root)}", file=sys.stderr)
        return 0

    v = check(root, changes, since=args.since)
    if v.blocked:
        print(f"\nBLOCKED: cross-model verification\n\n{v.message}\n", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
