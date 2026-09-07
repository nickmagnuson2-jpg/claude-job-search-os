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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inbox_lock  # noqa: E402

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

# --- BLAST RADIUS ------------------------------------------------------------
# Nick, 2026-09-06, choosing the tiering axis: "the latter is blast radius."
#
# Diff SIZE was the wrong measure and had been from the start. A three-line edit to a
# Hard Rule outranks a four-hundred-line test refactor, and the old gate scored it the
# other way round. Blast radius asks a different question: if this is wrong, what does
# it reach before anyone notices?
#
# It is tierable as a GATE rather than a judgement call for one reason -- it is almost
# entirely a function of the destination path, which is mechanically computable. That
# is what makes this a check a gate reads instead of prose asking for care.
BLAST_RULES: list[tuple[int, re.Pattern]] = [
    # T3 -- reaches a person outside the repo, or Nick's mouth in a live room, or
    # silently governs every future session. Wrong here is expensive and invisible.
    (3, re.compile(
        r"(^|/)CLAUDE\.md$"
        r"|(^|/)MEMORY\.md$"
        r"|^\.claude/settings(\.local)?\.json$"
        r"|^tools/(check_|prepush_)|_guard\.py$"
        r"|^tools/\.pending-draft"
        r"|cover-letter|/cv-|resume"
        r"|prep|cheat-?sheet")),
    # T2 -- governs future runs, or mutates the owner's real data. A wrong call here
    # propagates through everything the skill or the writer touches next.
    (2, re.compile(
        r"^\.claude/skills/"
        r"|^framework/"
        r"|^tools/(pipe|todo|networking|person|act)_write\.py$"
        r"|^tools/(finding_write|.*_ledger)\.py$")),
    # T2 -- a governed document seeds the next session, the build, or the record
    # everything later cites. Same pattern the qualifying branch uses, so the two
    # cannot drift into disagreeing about what "governed" means.
    (2, GOVERNED_DOC_RE),
    # T1 -- reversible, local, and it fails where someone can see it.
    (1, re.compile(r"^(tools/|tests/)")),
]

# How many INDEPENDENT models a tier wants. Independence is the whole product: a second
# pass by the same model reproduces the same blind spots, so two rows from `codex` are
# one verification wearing two hats.
TIER_MODELS = {0: 0, 1: 1, 2: 2, 3: 2}

# The models actually wired and callable from this machine. Add a name here and the
# tier-2 and tier-3 requirements activate on the next push -- that is the whole
# activation step, deliberately one line.
#
# WHY THIS BOUND EXISTS. Requiring two models while one is wired would block every
# tier-2 push permanently, and a gate that can only be waived is the theatre Nick named
# as the thing to avoid when he agreed to build this. So the requirement is
# min(what the tier wants, what exists). The tier is still COMPUTED and RECORDED at full
# strength, so the shortfall is visible rather than silently absent.
WIRED_MODELS: tuple[str, ...] = ("codex",)

DEFAULT_MODEL = "codex"


def blast_tier(paths) -> tuple[int, list[str]]:
    """Highest blast-radius tier among these paths, and the paths that set it."""
    best, triggers = 0, []
    for tier, pattern in BLAST_RULES:
        hits = [p for p in paths if pattern.search(p)]
        if hits and tier > best:
            best, triggers = tier, hits
    return best, triggers


def models_required(tier: int) -> int:
    """What the gate will actually demand: the tier's appetite, bounded by reality."""
    return min(TIER_MODELS.get(tier, 0), len(WIRED_MODELS))


def row_model(row: dict) -> str:
    """Which model produced this row. Rows predating the field are the only one there
    was, so naming it keeps the distinct-model count honest rather than optimistic."""
    m = row.get("model")
    return str(m).strip() if isinstance(m, str) and m.strip() else DEFAULT_MODEL


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
    # Blast-radius tier (0-3) and how many independent models it actually demands
    # after bounding by WIRED_MODELS. Recorded even when 0, so /standup can show it.
    tier: int = 0
    need_models: int = 0


def ledger_path(repo_root: Path) -> Path:
    return Path(repo_root) / "tools" / LEDGER_NAME


def qualifies(changes: list[tuple[str, int, int]]) -> Verdict:
    """Does this diff need a second model looking at it?

    `changes` is [(path, added, removed)], the shape `git diff --numstat` gives.

    Deliberately NOT "everything qualifies". If the gate fires on every push the waiver
    becomes a reflex keystroke and the whole mechanism is theatre -- which is the
    specific failure Nick named when he agreed to build it.
    """
    paths = [p for p, _a, _r in changes]
    tier, tier_paths = blast_tier(paths)

    def _v(reason: str, triggers: list) -> Verdict:
        """Every qualifying branch carries the blast tier, not just the size reason.
        A branch that forgot to set it would silently demand tier-1 coverage for a
        Hard Rule edit, which is the defect this whole change exists to remove."""
        t = max(tier, 1)
        return Verdict(True, reason, triggers=triggers, tier=t,
                       need_models=models_required(t))

    hooks = [p for p, _a, _r in changes if HOOK_RE.match(p)]
    if hooks:
        return _v(f"changes a wired hook or guard: {', '.join(hooks[:3])}", hooks)

    docs = [p for p, _a, _r in changes if GOVERNED_DOC_RE.search(p)]
    if docs:
        return _v(f"changes a governed document: {', '.join(docs[:3])}", docs)

    # AFTER hooks and governed docs on purpose: those name WHY in terms a reader acts
    # on ("changes a wired hook"), and both are already tier 3, so _v() gives them the
    # right tier anyway. This branch catches the tier-2+ paths that no earlier rule
    # describes -- CLAUDE.md, a skill, a data writer.
    if tier >= 2:
        return _v(f"touches tier-{tier} blast radius: {', '.join(tier_paths[:3])}",
                  tier_paths)

    code = [(p, a, r) for p, a, r in changes if CODE_RE.match(p)]
    touched = sum(a + r for _p, a, r in code)
    if touched >= LARGE_CHANGE_LINES:
        ranked = [p for p, _a, _r in sorted(code, key=lambda c: -(c[1] + c[2]))]
        names = ", ".join(ranked[:3])
        return _v(f"large code change ({touched} lines): {names}", ranked)
    if len(code) >= BROAD_CHANGE_FILES:
        return _v(f"broad code change ({len(code)} files touched)",
                  [p for p, _a, _r in code])

    return Verdict(False, "no governed document, wired hook, or large code change")


def read_ledger_with_health(repo_root: Path) -> tuple[list[dict], int]:
    """Rows plus the count of lines that could not be read as a row.

    The corrupt count is the half that used to be thrown away. This module's contract
    says a corrupt ledger BLOCKS, and it could not: read_ledger() dropped unreadable
    lines silently, so a ledger with one good row and one shredded row was
    indistinguishable from a healthy one. Unknown state must never read as verified.
    Found by cross-model review 2026-09-03 (F7), fixed 2026-09-06.
    """
    path = ledger_path(repo_root)
    if not path.is_file():
        return [], 0
    rows, corrupt = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            corrupt += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            corrupt += 1
    return rows, corrupt


def read_ledger(repo_root: Path) -> list[dict]:
    """Rows, or a raise-free empty list. Callers MUST distinguish empty from satisfied."""
    return read_ledger_with_health(repo_root)[0]


def row_is_usable(row: dict) -> bool:
    """Can this row license a push?

    A row exists for every codex_verify invocation, INCLUDING one that crashed, wrote
    no report, or parsed no findings -- the row is the audit trail and must be kept.
    But such a row verified nothing, and until 2026-09-06 it cleared the gate anyway:
    codex_verify.py recorded `rc` and `report_written` and check() read neither.
    Found by cross-model review 2026-09-03 (F5).

    A waiver is usable by design: it is a decision Nick typed, not a run that failed.

    THE DISTINCTION THAT MATTERS: "provenance recorded, and it says the run failed" is
    not the same as "no provenance recorded at all". F5 is about the first. Treating the
    second as failure was the first cut of this fix and it broke eleven tests, because a
    hand-written or older-shaped row carries neither field and is not evidence of
    anything having gone wrong. Failing closed on absent evidence would have blocked
    every push those rows cleared, which is a new defect, not a stricter gate.
    """
    if row.get("waived"):
        return True
    if "verified" in row:
        return bool(row["verified"])
    if "rc" in row:                       # written by codex_verify, pre-`verified`
        return row["rc"] == 0 and bool(row.get("report_written"))
    return True                           # predates provenance tracking entirely


def append_row(repo_root: Path, row: dict) -> Path:
    """Append one ledger row, under the same advisory lock finding_write.py takes.

    An O_APPEND write is already atomic against other APPENDS, so this lock is not about
    appends colliding with each other. It is about the whole-file REWRITE in
    finding_write.set_disposition(): a row appended between that function's read and its
    replace is absent from the snapshot and is erased by it, with both commands reporting
    success and nothing recording that an audit row was destroyed.

    Both sides must take the lock for it to mean anything -- locking only the rewriter
    still loses the append. Found by adversarial cross-model verification 2026-09-06
    (F1, P0): output/analysis/090626-codex-tools-finding-write-py-does-it-atomical.md
    """
    path = ledger_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with inbox_lock.file_lock(path):
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
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
        # A finding with no location cannot be verified at path:line, which the review
        # protocol requires before anything acts on it. Marking it here is the whole
        # point of recording the field: an unactionable finding that LOOKS like the
        # others inflates the open count and costs a verification pass to discover.
        mark = "" if (f.get("location") or "").strip() else " (no location)"
        lines.append(f"  [{f.get('severity', 'P2')}]{mark} {f.get('summary', '')}")
    unlocated = sum(1 for f in openf if not (f.get("location") or "").strip())
    head = f"{len(openf)} open cross-model finding(s)" if openf else ""
    if unlocated:
        head += (f", {unlocated} with no location (cannot be verified at path:line; "
                 f"triage these first, they are the cheapest to close)")
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

    `since` is the epoch time of the NEWEST commit being pushed: a verification that
    predates the work is not a verification of it. (The caller passed the base commit
    until 2026-09-03; the hook was fixed and this line was not, so it described the
    opposite of the behaviour for three days.)
    """
    v = qualifies(changes)
    if not v.qualified:
        v.blocked = False
        v.message = "no cross-model verification required"
        return v

    changed = {p for p, _a, _r in changes}
    # COVERAGE IS PER-PATH, NOT PER-PUSH. Until 2026-09-06 a single overlapping path
    # cleared the entire push: a row covering tools/codex_verify.py licensed a push
    # that also rewrote CLAUDE.md and forty other files, none of them reviewed. The
    # ledger also spans sibling repos, so a client-deliverable review could clear a
    # code push here. Found by cross-model review 2026-09-03 (F3).
    # Every path that CAUSED qualification must be covered. Coverage accumulates across
    # rows, so two focused reviews still clear a push that spans both.
    required = set(v.triggers or changed)
    rows, corrupt = read_ledger_with_health(repo_root)

    if corrupt:
        v.blocked = True
        v.changed = sorted(changed)
        v.message = (
            f"This push {v.reason}.\n"
            f"The ledger has {corrupt} unreadable line(s), so its coverage is unknown "
            f"and it cannot be trusted to clear anything.\n\n"
            f"  Inspect:   tools/{LEDGER_NAME}\n"
            f"  Or waive:  CODEX_VERIFY_WAIVE='<why>' git push\n")
        return v

    covered: dict[str, set[str]] = {}
    stale = unusable = failed = 0
    for row in rows:
        if not row_is_usable(row):
            failed += 1
            continue
        recorded = _parse_ts(row.get("recorded"))
        if recorded is None:
            # Unreadable timestamp: freshness is UNKNOWN, and unknown is not fresh.
            # The old code only marked a row stale when the stamp parsed, so a garbage
            # stamp skipped the staleness branch and fell straight through to "clear".
            # The test named for this asserted isinstance(blocked, bool), which passes
            # either way. Found by cross-model review 2026-09-03 (F7).
            unusable += 1
            continue
        if recorded < since:
            stale += 1
            continue
        for pth in row.get("paths") or []:
            covered.setdefault(pth, set()).add(row_model(row))

    # A path is covered only when ENOUGH DISTINCT MODELS have looked at it. Two rows
    # from the same model are one perspective recorded twice, which is the anchoring
    # failure this gate exists to defeat, so they count once.
    need = max(1, v.need_models)
    missing = sorted(p for p in required if len(covered.get(p, ())) < need)
    if not missing:
        v.blocked = False
        v.message = (f"cleared: all {len(required)} tier-{v.tier} path(s) covered by "
                     f"{need} independent model(s)")
        return v

    v.blocked = True
    v.changed = sorted(changed)
    suggest = " ".join(missing[:3])
    notes = []
    if stale:
        notes.append(f"{stale} matching record(s) are OLDER than the work, so they "
                     f"verified something else")
    if unusable:
        notes.append(f"{unusable} record(s) have an unreadable timestamp")
    if failed:
        notes.append(f"{failed} record(s) came from a run that did not complete")
    wanted = TIER_MODELS.get(v.tier, 0)
    if wanted > len(WIRED_MODELS):
        notes.append(f"tier {v.tier} wants {wanted} independent models but only "
                     f"{len(WIRED_MODELS)} is wired ({', '.join(WIRED_MODELS)}), so "
                     f"{need} is being required -- add one to WIRED_MODELS to activate")
    older = (" " + "; ".join(notes) + "." if notes else "")
    v.message = (
        f"This push {v.reason}.{older}\n"
        f"Uncovered: {', '.join(missing[:6])}"
        f"{f' (+{len(missing) - 6} more)' if len(missing) > 6 else ''}\n\n"
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
