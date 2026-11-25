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
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
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
HOOK_RE = re.compile(r"^tools/(check_|prepush_)|_guard\.py$")

# The enforcement machinery ITSELF, named explicitly because matching on filename
# prefixes cannot reach it. Until 2026-09-07 tiering keyed on the `check_` and
# `prepush_` prefixes, so tools/prepush_pii_guard.py scored tier 3 while
# tools/cross_model_gate.py -- the file that DECIDES what needs review -- sat at tier 1,
# and an ordinary 60-line edit to it did not qualify for the gate at all. The gate
# exempted its own decision engine, and whether a file was protected depended on what it
# happened to be called. Found by cross-model review (F1, P0) after Nick said a change
# here had a high blast radius and the code disagreed with him. It was wrong.
#
# LISTED, not matched. These four share nothing in their names, and a regex contorted to
# cover them would be the same accident lying in wait for the fifth.
ENFORCEMENT_ASSETS: frozenset[str] = frozenset({
    "tools/cross_model_gate.py",   # decides what needs verifying
    "tools/codex_verify.py",       # produces the rows this gate reads
    "tools/hooks/pre-push",        # the tracked hook that invokes both
    "tools/hooks/install.sh",      # installs it; a broken install means NO gate at all
})


def is_enforcement_asset(path: str) -> bool:
    r"""Is this file part of the machinery that does the enforcing? ONE definition.

    Two used to exist and they disagreed: HOOK_RE was unanchored
    (`tools/(check_|prepush_|.*_guard)`) while the tier-3 rule was anchored
    (`^tools/(check_|prepush_)|_guard\.py$`), and each was read by a different consumer.
    Duplicated domain logic inside a guard is how one rule acquires two meanings, which
    is the pattern this repo already has a rule against.
    """
    return path in ENFORCEMENT_ASSETS or bool(HOOK_RE.search(path))

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
# Entries are (tier, matcher) where matcher is any str -> bool. A CALLABLE rather than
# a bare pattern so the enforcement-asset predicate can be a rule here directly, instead
# of being transcribed into a regex that then drifts from the one qualifies() reads.
@dataclass(frozen=True)
class Rule:
    """One classification rule: a tier, what it matches, and WHY, together.

    The reason lives here rather than in a branch of qualifies() because tier and
    explanation are two answers to one question. When they were computed by separate
    mechanisms over the same paths they drifted, and the drift was not cosmetic: the
    `docs` branch fired ahead of the tier check and overwrote `triggers`, so a push
    carrying CLAUDE.md and a handoff required coverage of the handoff ONLY. The Hard
    Rule file rode along unverified. Consolidated 2026-09-07.
    """
    tier: int
    matches: Callable[[str], bool]
    reason: str


# Ordered for readability only. classify() takes the highest matching tier and then
# UNIONS every rule at that tier, so neither the tier nor the required-coverage set
# depends on where a rule sits in this list. That is one less thing to get wrong when
# a rule is added, and it is pinned by two order-independence tests.
BLAST_RULES: list[Rule] = [
    # T3 -- the machinery that does the enforcing, including this file. Same predicate
    # qualifies() reads, so classification and qualification cannot disagree.
    Rule(3, is_enforcement_asset,
         "changes enforcement machinery (a wired hook, a guard, or the gate itself)"),
    # T3 -- reaches a person outside the repo, or Nick's mouth in a live room, or
    # silently governs every future session. Wrong here is expensive and invisible.
    Rule(3, re.compile(
        r"(^|/)CLAUDE\.md$"
        r"|(^|/)MEMORY\.md$"
        r"|^\.claude/settings(\.local)?\.json$"
        r"|^tools/\.pending-draft"
        r"|cover-letter|/cv-|resume"
        r"|prep|cheat-?sheet").search,
         "changes a governing document or an outward-facing artifact"),
    # T2 -- governs future runs, or mutates the owner's real data. A wrong call here
    # propagates through everything the skill or the writer touches next.
    Rule(2, re.compile(
        r"^\.claude/skills/"
        r"|^framework/"
        r"|^tools/(pipe|todo|networking|person|act)_write\.py$"
        r"|^tools/(finding_write|.*_ledger)\.py$").search,
         "changes a skill, a framework doc, or a data writer"),
    # T2 -- a governed document seeds the next session, the build, or the record
    # everything later cites.
    Rule(2, GOVERNED_DOC_RE.search,
         "changes a governed document"),
    # T1 -- reversible, local, and it fails where someone can see it.
    Rule(1, re.compile(r"^(tools/|tests/)").search,
         "changes local code"),
]


def classify(paths) -> tuple[int, list[str], str]:
    """The winning tier, EVERY path that reaches it, and why.

    The triggers are the required-coverage set that check() enforces, so this is a
    UNION over all rules at the winning tier, not a winner-take-all pick. A strict
    `rule.tier > best.tier` shipped in 1e36d9c and kept only the first rule to reach
    the maximum, which silently exempted its peers: tools/cross_model_gate.py plus
    CLAUDE.md are both tier 3 under different rules, and only the gate was required --
    the very exemption that commit claimed to close. Found by cross-model review (F1,
    P0) the same day. Winner-take-all is the wrong operation for a coverage set.

    Only the WINNING tier is unioned. Including every matched path would demand
    coverage of everything in the push, which is how a gate becomes a reflex waiver.
    """
    tiers = [rule.tier for rule in BLAST_RULES
             if any(rule.matches(p) for p in paths)]
    if not tiers:
        return 0, [], ""
    top = max(tiers)

    triggers: list[str] = []
    reasons: list[str] = []
    for rule in BLAST_RULES:
        if rule.tier != top:
            continue
        hits = [p for p in paths if rule.matches(p) and p not in triggers]
        if not hits:
            continue
        triggers.extend(hits)
        reasons.append(rule.reason)
    return top, triggers, "; ".join(reasons)


def blast_tier(paths) -> tuple[int, list[str]]:
    """Highest blast-radius tier among these paths, and the paths that set it.

    Kept as the two-value view over classify() for callers that do not need the
    reason. A wrapper, not a second implementation.
    """
    tier, triggers, _reason = classify(paths)
    return tier, triggers


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
WIRED_MODELS: tuple[str, ...] = ("codex", "grok", "fable")

DEFAULT_MODEL = "codex"

# The family that WROTE the work under review. A verifier from this family is additive,
# never sufficient: same training, correlated blind spots, and its agreement is the
# cheapest thing it can produce.
#
# WHY IT IS WIRED AT ALL (2026-09-16). codex_verify previously REFUSED any Anthropic
# model outright. On a live client deliverable, a Fable pass found the single most valuable
# defect of the day -- that 97% of the rows counted as bookings were logged as transfers,
# which no other pass reached -- after codex and grok had each returned findings from
# their own targets. Refusing it outright cost more than it protected. So the rule moved
# from "never" to "never ALONE": a path is covered only when the models that looked at it
# include at least one OUTSIDE this family. Fable can raise the count; it cannot be the
# count.
AUTHOR_FAMILY = "anthropic"

# Label -> family, for any row that lacks the `family` field. Named for the legacy rows
# it was built for, but it must cover EVERY wired model, not only the old ones: a row
# carrying `model: fable` and no family would otherwise resolve to the literal "fable",
# read as a family outside the author's, and clear a push by itself -- the exact hole the
# AUTHOR_FAMILY rule exists to close. Added fable 2026-09-16 for that reason, not because
# any fable row predates the field.
#
# Duplicated deliberately rather than imported from codex_verify.MODELS: that import
# would be circular, since codex_verify imports this module. A parity test pins them.
LEGACY_MODEL_FAMILIES = {"codex": "openai", "grok": "xai", "fable": "anthropic"}

# TEMPORARY EXCEPTION, Nick 2026-10-30: Grok (xai) hit its provider usage limit, which resets
# when the monthly cycle ends on 2026-11-12, and Nick chose not to buy more. Until then the
# author family (Fable) may fill ONE of the slots a tier demands, but at least one family
# OUTSIDE the author's is still required, so Fable alone never clears a push. Every push
# that relies on it says so in the gate message. The date is the expiry: from 2026-11-13
# the normal rule applies again with nothing to revert. Delete this block after that.
FABLE_STANDIN_UNTIL = date(2026, 11, 12)


def fable_standin_active(today: date | None = None) -> bool:
    """True while the Grok-outage exception lets the author family fill one slot."""
    return (today or date.today()) <= FABLE_STANDIN_UNTIL


def models_required(tier: int) -> int:
    """What the gate will actually demand: the tier's appetite, bounded by reality."""
    return min(TIER_MODELS.get(tier, 0), len(WIRED_MODELS))


def row_model(row: dict) -> str:
    """Which INDEPENDENT PERSPECTIVE produced this row -- its model FAMILY.

    Counting the `model` label was a P0 found by cross-model review 2026-09-07: two rows
    spelled "codex" and "gpt5" would have satisfied a two-model tier while both came
    from OpenAI. A label measures spelling; a family measures independence, which is the
    whole product. A second CLI routing to the same provider is a second invoice.

    Falls back to the model name, then to DEFAULT_MODEL, so the 31 rows predating the
    field keep counting as the one perspective they were rather than becoming
    uncountable and blocking every push they used to clear.
    """
    fam = row.get("family")
    if isinstance(fam, str) and fam.strip():
        return fam.strip()
    label = row.get("model")
    label = label.strip() if isinstance(label, str) and label.strip() else DEFAULT_MODEL
    # Map the label INTO the family namespace. Returning it raw was a P0 (grok, F1,
    # 2026-09-07): a legacy row {model: codex} returned "codex" while a current row
    # {family: openai} returned "openai", so one provider counted twice and cleared a
    # tier-3 push. Two namespaces in one set is the same label-counting defect the
    # family field was added to remove.
    return LEGACY_MODEL_FAMILIES.get(label, label)


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
    tier, tier_paths, tier_reason = classify(paths)

    def _v(reason: str, triggers: list) -> Verdict:
        """Every qualifying branch carries the blast tier, not just the size reason.
        A branch that forgot to set it would silently demand tier-1 coverage for a
        Hard Rule edit, which is the defect this whole change exists to remove."""
        t = max(tier, 1)
        return Verdict(True, reason, triggers=triggers, tier=t,
                       need_models=models_required(t))

    # ONE branch, because the reason now travels with the tier. There used to be three
    # here -- hooks, governed docs, then tier -- each re-testing the same paths to
    # produce its own wording. Two of them were subsets of the third, and their only
    # remaining job was the message. Worse, the `docs` arm ran FIRST and set `triggers`
    # to the governed docs, so a push carrying CLAUDE.md plus a handoff demanded
    # coverage of the handoff alone. Taking the highest-tier rule fixes that: the paths
    # that set the tier are the paths that must be covered.
    if tier >= 2:
        return _v(f"{tier_reason}: {', '.join(tier_paths[:3])}", tier_paths)

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


def main_checkout_of(repo_root: Path) -> Path | None:
    """The main checkout when repo_root is a linked worktree, found from git's common dir
    (never a hard-coded path); None when repo_root is not a linked worktree. Raises
    RuntimeError when a linked worktree's main checkout cannot be placed. The one locator
    both ledger readers use: read_gate_ledger here and audit_pii_record.has_clean_record."""
    root = Path(repo_root).resolve()
    if not (root / ".git").is_file():
        return None
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute",
                        "--git-common-dir"], capture_output=True, text=True, timeout=10)
    common = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    if common is None or common.name != ".git" or not common.is_dir():
        raise RuntimeError(f"git did not name a .git common dir for {root}")
    # No `main == root` check: root's .git is a file (tested above), so a common dir that
    # is root/.git fails the is_dir() test first (S2d mutation survivor, deleted).
    return common.parent.resolve()


def _row_key(row: dict) -> tuple:
    """What makes a mirrored row the same run as a worktree row: finding_write's match."""
    return row.get("report"), row.get("model"), row.get("recorded")


def read_gate_ledger(repo_root: Path) -> tuple[list[dict], int, str]:
    """The rows the push gate judges: this checkout's ledger, plus, from a linked worktree,
    the main checkout's (S2d, 2026-11-25).

    WHY. The gate run from a branch's worktree read only that worktree's copy. A review run
    from ANOTHER worktree at the same tree reached its own copy and main's mirror, never
    this one, so the push was refused and both reviews were repeated (S5, tree 734f1e8f).
    main's ledger is the copy codex_verify mirrors every review into and finding_write keeps
    in step, dispositions included, so reading it is enough; nothing is copied. On the same
    run (report, model, recorded) this worktree's own row wins.

    FAILS CLOSED: if main's ledger cannot be located, is missing or cannot be read, one
    unreadable line is counted, which blocks, and the third value says why."""
    rows, corrupt = read_ledger_with_health(repo_root)
    try:
        main = main_checkout_of(repo_root)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        return rows, corrupt + 1, f"the main checkout's ledger could not be located ({e})"
    if main is None:
        return rows, corrupt, ""
    if not ledger_path(main).is_file():
        return rows, corrupt + 1, f"the main checkout's ledger is missing ({ledger_path(main)})"
    try:
        main_rows, main_corrupt = read_ledger_with_health(main)
    except (OSError, UnicodeDecodeError) as e:
        return rows, corrupt + 1, f"the main checkout's ledger cannot be read ({e})"
    own = {_row_key(r) for r in rows}
    merged = rows + [r for r in main_rows if _row_key(r) not in own]
    note = f"the main checkout's ledger has {main_corrupt} unreadable line(s)" if main_corrupt else ""
    return merged, corrupt + main_corrupt, note


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


def parked_findings(repo_root: Path) -> list[dict]:
    """Findings someone verified as REAL and deliberately deferred.

    THREE STATES, NOT TWO. `fixed` and `rejected` are CLOSED: the work is done or the
    claim did not survive. `parked` is a DEBT -- verified, still true, not yet paid.

    It was wired to the same silence as the closed states for a few hours on
    2026-09-07, because open_findings() filters on "has any disposition". Nine findings
    were parked that evening, including two live defects in the follow-up date logic
    that feeds the morning brief, and /standup stopped showing every one of them. That
    is the promotion failure this repo already has a rule about, in a new costume: the
    status flips, the item leaves the backlog, and the detector goes quiet.

    A debt that stops being displayed is a debt nobody pays.
    """
    out = []
    for row in read_ledger(repo_root):
        for f in row.get("findings") or []:
            if isinstance(f, dict) and (f.get("disposition") or "").strip() == "parked":
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
    parked = sorted(parked_findings(repo_root),
                    key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 9))
    if not openf and not waivers and not parked:
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
    if parked:
        # A SEPARATE section, deliberately. Folding these into the open count would
        # overstate what nobody has looked at; dropping them entirely is what this
        # function did until 2026-09-07. Reasons are truncated because they are written
        # to be read at the ledger, not in a daily brief.
        # P0s awaiting acceptance come first and by name: they are blocking pushes now
        # and accepting them is Nick's decision. Legacy acceptances are counted, not listed; the
        # 2026-10-14 grandfathering would otherwise put ~100 rows in every brief.
        import finding_write

        def _render(f: dict) -> None:
            why = " ".join((f.get("why") or "").split())
            lines.append(f"  [{f.get('severity', 'P2')}] {f.get('summary', '')}")
            if why:
                lines.append(f"        why: {why[:150]}{'...' if len(why) > 150 else ''}")

        waiting = [f for f in parked if f.get("severity") == "P0"
                   and not f.get("accepted_by")]
        legacy = [f for f in parked
                  if f.get("accepted_by") == finding_write.LEGACY_ACCEPTOR]
        if waiting:
            lines.append(f"\n{len(waiting)} parked P0(s) BLOCKING pushes until Nick "
                         f"accepts (`! ... finding_write.py accept <addr> --why ...`):")
            for f in waiting:
                _render(f)
        skip = {id(f) for f in waiting + legacy}
        lines.append(f"\n{len(parked)} parked finding(s) -- verified real, deferred "
                     f"with a reason. Not closed. `finding_write list --parked` for "
                     f"addresses:"
                     + (f" ({len(legacy)} P0s accepted as legacy on 2026-10-14, "
                        f"not listed)" if legacy else ""))
        for f in parked:
            if id(f) not in skip:
                _render(f)
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


_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def tip_tree(repo_root: Path, tip: str | None) -> str | None:
    """The tree of the commit being pushed, or None when it cannot be read.

    None is not a wildcard: a tree-keyed row compares against it and never matches.
    """
    if not tip:
        return None
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{tip}^{{tree}}"],
                             cwd=str(repo_root), capture_output=True, text=True,
                             timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    tree = out.stdout.strip()
    return tree if out.returncode == 0 and _SHA_RE.match(tree) else None


def families_cover(fams: set, need: int, standin_used: list, pth: str) -> bool:
    """Enough distinct families, AND at least one from outside the author's.

    The second condition is what keeps a same-family verifier additive. Without it,
    wiring Fable would let two Anthropic rows -- or one, at tier 1 -- clear a push
    with nothing outside the family that wrote the code having looked at it, which
    is the anchoring failure this gate exists to defeat, not a relaxation of it.
    One function for check() and check_chain(), so the two cannot disagree on it.
    """
    # The tier's appetite must be met by families OUTSIDE the author's. Counting the
    # author's own family toward it lets a same-family verifier OCCUPY one of the two
    # slots a tier-2 push demands, so {openai, anthropic} would clear a bar that used
    # to require {openai, xai}. That is a weakening dressed as an addition, and it is
    # exactly what "additive, never sufficient" was supposed to prevent -- the first
    # version of this check tested `fams - {AUTHOR_FAMILY}` for non-emptiness, which
    # is true at tier 1 and false at tier 2. Found by cross-model review (F2, P0,
    # 2026-09-16) on the same commit that wired the same-family model.
    outside = fams - {AUTHOR_FAMILY}
    if len(outside) >= need:
        return True
    # Grok-outage exception (see FABLE_STANDIN_UNTIL): Fable fills one slot, never all.
    if (fable_standin_active() and AUTHOR_FAMILY in fams and outside
            and len(outside) + 1 >= need):
        standin_used.append(pth)
        return True
    return False


def check(repo_root: Path, changes: list[tuple[str, int, int]],
          since: float, tip: str | None = None) -> Verdict:
    """Block a qualifying push that no verification covers.

    TWO KEYS, chosen per row (Nick, 2026-11-16: "Tree hash").

    A row carrying a `tree` field covers the push only when that tree equals the pushed
    tip's tree (`tip`, resolved here). Time is ignored for it: identical tree means the
    reviewer saw exactly the content being pushed, so a re-commit or a message amend
    after the review no longer voids it. A tree that differs, is null, or cannot be
    compared (no `tip`, or a tip that does not resolve) covers NOTHING, however recent
    the row. Fails closed.

    A row WITHOUT `tree` is legacy and keeps the committer-time rule: `since` is the
    epoch time of the NEWEST commit being pushed, and a verification that predates the
    work is not a verification of it. (The caller passed the base commit until
    2026-09-03; the hook was fixed and this line was not, so it described the opposite
    of the behaviour for three days.)

    The ledger is local and writable, so a hand-forged `tree` clears a push the same way
    a hand-forged `recorded` stamp does under the time rule. The tree key opens no hole
    the time key did not already have.
    """
    v = qualifies(changes)
    if not v.qualified:
        # An open P0 blocks ANY push of its path, not only one big enough to need a
        # verification: a small fix-up push went straight past the finding on the very
        # file it changed (cross-model 2026-09-23, Codex round 2, F1).
        blocking = open_p0_against(repo_root, {p for p, _a, _r in changes})
        if blocking:
            v.blocked = True
            v.changed = sorted({p for p, _a, _r in changes})
            v.message = ("This push needs no cross-model verification, but it changes "
                         "paths with open P0 findings.\n" + _p0_block_text(blocking))
            return v
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
    rows, corrupt, note = read_gate_ledger(repo_root)

    if corrupt:
        v.blocked = True
        v.changed = sorted(changed)
        v.message = (
            f"This push {v.reason}.\n"
            + (f"{note}.\n" if note else "")
            + f"The ledger has {corrupt} unreadable line(s), so its coverage is unknown "
            f"and it cannot be trusted to clear anything.\n\n"
            f"  Inspect:   tools/{LEDGER_NAME}\n"
            f"  Or waive:  CODEX_VERIFY_WAIVE='<why>' git push\n")
        return v

    covered: dict[str, set[str]] = {}
    stale = unusable = failed = other_tree = 0
    pushed_tree = tip_tree(repo_root, tip)

    def _relevant(row) -> bool:
        """Does this row say anything about a path this push actually needs covered?

        The three counters below feed the block message, and until 2026-09-07 they
        incremented for EVERY discarded row in the ledger. A push needing two files
        reported "32 matching record(s) are OLDER than the work" when 25 of the 32 were
        about unrelated artifacts. `matching` was a lie, the number read as evidence of
        a different defect, and it sent a P0 escalation at the wrong thing. A wrong
        diagnostic is worse than none: it gets believed and acted on.
        """
        return bool(required & set(row.get("paths") or []))

    for row in rows:
        if not row_is_usable(row):
            failed += _relevant(row)
            continue
        if "tree" in row:
            # Tree-keyed: same final tree or nothing. Time is not consulted.
            reviewed = row["tree"]
            if not (pushed_tree and isinstance(reviewed, str) and reviewed == pushed_tree):
                other_tree += _relevant(row)
                continue
            for pth in row.get("paths") or []:
                covered.setdefault(pth, set()).add(row_model(row))
            continue
        recorded = _parse_ts(row.get("recorded"))
        if recorded is None:
            # Unreadable timestamp: freshness is UNKNOWN, and unknown is not fresh.
            # The old code only marked a row stale when the stamp parsed, so a garbage
            # stamp skipped the staleness branch and fell straight through to "clear".
            # The test named for this asserted isinstance(blocked, bool), which passes
            # either way. Found by cross-model review 2026-09-03 (F7).
            unusable += _relevant(row)
            continue
        if recorded < since:
            stale += _relevant(row)
            continue
        for pth in row.get("paths") or []:
            covered.setdefault(pth, set()).add(row_model(row))

    # A path is covered only when ENOUGH DISTINCT MODELS have looked at it. Two rows
    # from the same model are one perspective recorded twice, which is the anchoring
    # failure this gate exists to defeat, so they count once.
    need = max(1, v.need_models)
    standin_used: list[str] = []

    def path_is_covered(pth: str) -> bool:
        return families_cover(covered.get(pth, set()), need, standin_used, pth)

    missing = sorted(p for p in required if not path_is_covered(p))
    # EVERY CHANGED PATH, not the trigger set. `required` is narrowed to the highest-tier
    # paths, so reading it ignored an open P0 on a lower-tier file pushed alongside a
    # gate file. Cross-model review 2026-09-23 (F1, P0), on the commit that built this.
    blocking = open_p0_against(repo_root, changed)
    if not missing and not blocking:
        v.blocked = False
        v.message = (f"cleared: all {len(required)} tier-{v.tier} path(s) covered by "
                     f"{need} independent model(s)")
        if standin_used:
            v.message += (f"; NOTE: {len(standin_used)} path(s) cleared with Fable standing in "
                          f"for Grok (exception until {FABLE_STANDIN_UNTIL.isoformat()})")
        return v

    v.blocked = True
    v.changed = sorted(changed)
    if not missing:
        v.message = f"This push {v.reason}.\n" + _p0_block_text(blocking)
        return v
    suggest = " ".join(missing[:3])
    notes = []
    if stale:
        notes.append(f"{stale} matching record(s) are OLDER than the work, so they "
                     f"verified something else")
    if unusable:
        notes.append(f"{unusable} record(s) have an unreadable timestamp")
    if failed:
        notes.append(f"{failed} record(s) came from a run that did not complete")
    if other_tree:
        notes.append(f"{other_tree} record(s) reviewed a different tree than the pushed "
                     f"tip" + ("" if pushed_tree else " (the tip's tree was not given or "
                                                    "did not resolve)"))
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
        f"  Or Nick waives:  CODEX_VERIFY_WAIVE='<why>' git push\n\n"
        f"A waiver is recorded and counted; /standup surfaces the running total."
        + (f"\n\nAlso: {_p0_block_text(blocking)}" if blocking else ""))
    return v


def check_chain(repo_root: Path, base: str, tip: str | None) -> Verdict | None:
    """The gate for a push of merge commits (Nick, 2026-11-18: "No PRs").

    land_branch.py --merge-push builds M1 = merge(main, b1), M2 = merge(M1, b2), ...
    Nobody reviews a merge tree; each branch was reviewed at its own tip. So when
    base..tip is a verified chain (tools/merge_chain.py: every commit is git's own
    merge of its parents, first parents chaining from base) each merge Mi is judged
    on its own: the paths p1..Mi changes need rows whose `tree` is p2's tree (the
    branch tip that was reviewed), at the full family rule for that merge's tier.
    Only tree rows count: a time-keyed row cannot say which branch it saw.

    The push is also judged whole: when the merges together are a large or broad
    change, each merge must have its share of those paths reviewed at its branch tip,
    even when that merge alone is too small to qualify.

    Open P0s are checked over every path any merge in the push changes.

    Returns None when base..tip is NOT a verified chain, so the caller applies
    check() to the whole push, which blocks a merge no row reviewed. A git failure
    while walking the range, or a diff git cannot produce for a verified chain,
    blocks (fails closed).
    """
    import merge_chain
    if not (base and tip):
        return None
    try:
        merges = merge_chain.walk(repo_root, base, tip)
    except merge_chain.ChainError:
        return None
    except merge_chain.GitError as e:
        # Grok 10.F3 (Nick, 2026-11-21, option A): a git failure says nothing about
        # whether this is a chain. Falling back to check() gave the wrong verdict on a
        # chain whose net diff is empty, so it blocks.
        v = Verdict(qualified=True, reason="may be a chain of merge commits")
        v.blocked = True
        v.message = (f"This push {v.reason}, but the merge chain could not be read: {e}\n"
                     f"  Re-run the push. If it repeats, check the repository: git fsck")
        return v
    v = Verdict(qualified=True, reason="is a chain of merge commits")
    try:
        per_merge = [(m, merge_chain.numstat(repo_root, m.p1, m.sha)) for m in merges]
        changed = sorted({p for p, _a, _r in merge_chain.numstat(repo_root, base, tip)})
    except (merge_chain.ChainError, merge_chain.GitError) as e:
        v.blocked = True
        v.message = f"This push is a merge chain, but its diff could not be read: {e}"
        return v
    v.changed = changed
    rows, corrupt, note = read_gate_ledger(repo_root)
    if corrupt:
        v.blocked = True
        v.message = (f"This push {v.reason}.\n{(note + '.\n') if note else ''}The ledger has {corrupt} unreadable "
                     f"line(s), so its coverage is unknown and it cannot be trusted to "
                     f"clear anything.\n\n  Inspect:   tools/{LEDGER_NAME}\n"
                     f"  Or waive:  CODEX_VERIFY_WAIVE='<why>' git push\n")
        return v

    by_tree: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        if row_is_usable(row) and isinstance(row.get("tree"), str):
            cov = by_tree.setdefault(row["tree"], {})
            for pth in row.get("paths") or []:
                cov.setdefault(pth, set()).add(row_model(row))

    # The push is also judged WHOLE (Grok 2026-11-19 F1). Merges that are each too small
    # to need review can add up to a large or broad change, which qualifies() exists to
    # catch in one diff (test_many_small_code_edits_add_up). Lines are summed per path
    # across the merges, so a file added in one merge and removed in a later one counts.
    summed: dict[str, list[int]] = {}
    for _m, changes in per_merge:
        for p, a, r in changes:
            s = summed.setdefault(p, [0, 0])
            s[0] += a
            s[1] += r
    whole = qualifies([(p, a, r) for p, (a, r) in summed.items()])
    whole_req = set(whole.triggers or []) if whole.qualified else set()

    gaps: list[str] = []
    standin_used: list[str] = []
    for m, changes in per_merge:
        mv = qualifies(changes)
        mine = {p for p, _a, _r in changes}
        shared = mine & whole_req
        required = (set(mv.triggers or mine) if mv.qualified else set()) | shared
        need = max(1, mv.need_models if mv.qualified else 0,
                   whole.need_models if shared else 0)
        tier = max(mv.tier if mv.qualified else 0, whole.tier if shared else 0)
        branch_tree = tip_tree(repo_root, m.p2)
        cov = by_tree.get(branch_tree, {}) if branch_tree else {}
        missing = sorted(p for p in required
                         if not families_cover(cov.get(p, set()), need, standin_used, p))
        if missing:
            gaps.append(f"  merge {m.sha[:8]} (branch tip {m.p2[:8]}, tier {tier}, "
                        f"{need} model(s)): " + ", ".join(missing[:6])
                        + (f" (+{len(missing) - 6} more)" if len(missing) > 6 else ""))

    # Every path a sent commit changes, not only the net diff (Grok 2026-11-19 F2): a
    # file added by one merge and removed by a later one is still pushed.
    blocking = open_p0_against(repo_root, set(changed) | set(summed))
    if not gaps and not blocking:
        v.blocked = False
        v.message = (f"cleared: each of {len(merges)} merge(s) is covered by reviews of "
                     f"its branch tip's tree")
        if standin_used:
            v.message += (f"; NOTE: {len(standin_used)} path(s) cleared with Fable standing "
                          f"in for Grok (exception until {FABLE_STANDIN_UNTIL.isoformat()})")
        return v
    v.blocked = True
    parts = [f"This push {v.reason}."]
    if gaps:
        parts.append("Not covered by a review of the branch tip's tree:\n"
                     + "\n".join(gaps)
                     + "\n\n  Review the branch at that tip: tools/codex_verify.py "
                     "--paths <paths>, run in the branch's worktree.\n"
                     "  Or Nick waives:  CODEX_VERIFY_WAIVE='<why>' git push")
    if blocking:
        parts.append(_p0_block_text(blocking))
    v.message = "\n".join(parts)
    return v


def open_p0_against(repo_root: Path, required: set) -> list[dict]:
    """Open P0 findings recorded against any path in `required` (the caller passes
    every path the push changes).

    B2, closeout 2026-09-23. check() counted only freshness and model families, so a
    fresh second-family run cleared a push while the same ledger held open P0s against
    the pushed code: "fix before the push clears" was enforced by nothing.

    Deliberately NOT filtered by freshness or by whether the run completed. A stale row
    cannot COVER a push, but age is not a disposition: its open P0 is still an
    unanswered finding. Only P0 blocks; P1s are carried, per the pre-lock rule.
    Addressing comes from finding_write.collect(), the one scheme `set` accepts.

    A PARKED P0 BLOCKS until Nick accepts it (2026-10-14). Until then this asked for
    `only_open`, which drops every finding with any disposition, so `parked` cleared a
    P0 exactly as `fixed` did while parked_findings() below calls it unpaid debt. The
    agent could park and push in one motion, and 102 P0s were parked that way. Now
    parking records the deferral, and `finding_write.py accept`, which is Nick's to run,
    records that the debt may ship. Nothing local enforces that; his Touch ID on every
    push does (2026-10-15).
    """
    import finding_write
    found = finding_write.collect(repo_root, severity="P0")
    # From a linked worktree, main's mirror too (S2d, read_gate_ledger): a P0 open only in
    # main still blocks; a run this worktree holds is judged from its own copy. If main
    # cannot be placed, check() already blocks through read_gate_ledger's corrupt count.
    try:
        main = main_checkout_of(repo_root)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        main = None
    if main is not None and ledger_path(main).is_file():
        own = {(r.get("report"), r.get("recorded")) for r in read_ledger(repo_root)}
        found += [f for f in finding_write.collect(main, severity="P0")
                  if (f["report"], f["recorded"]) not in own]
    return [f for f in found if required & set(f["paths"]) and blocks_push(f)]


def blocks_push(f: dict) -> bool:
    """Does this P0 still stop a push? Open, or parked without an acceptance."""
    disp = (f.get("disposition") or "").strip()
    return not disp or (disp == "parked" and not f.get("accepted_by"))


def _p0_block_text(blocking: list[dict]) -> str:
    addrs = [f["addr"] for f in blocking]
    shown = ", ".join(addrs[:12]) + (f" (+{len(addrs) - 12} more)" if len(addrs) > 12
                                     else "")
    parked = [f["addr"] for f in blocking if (f.get("disposition") or "").strip()]
    return (f"{len(addrs)} unresolved P0 finding(s) are recorded against the paths being "
            f"pushed: {shown}\n\n"
            f"  List:      PYTHONIOENCODING=utf-8 python3 tools/finding_write.py list "
            f"--severity P0\n"
            f"  Resolve:   PYTHONIOENCODING=utf-8 python3 tools/finding_write.py set "
            f"<addr> --disposition fixed|rejected|parked --why '<reason>'\n"
            f"  A parked P0 still blocks until Nick accepts it:\n"
            f"             ! PYTHONIOENCODING=utf-8 python3 tools/finding_write.py accept "
            f"<addr> --why '<reason>'\n"
            + (f"  Parked, awaiting acceptance: {', '.join(parked[:12])}\n" if parked
               else "")
            + "  Or Nick waives:  CODEX_VERIFY_WAIVE='<why>' git push\n")


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--since", type=float, default=0.0)
    ap.add_argument("--tip", default=None,
                    help="the pushed commit; tree-keyed rows match its tree")
    ap.add_argument("--base", default=None,
                    help="the remote's current tip; when base..tip is a verified merge "
                         "chain, each merge is checked against its branch tip's reviews")
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
        # Record whenever the waiver SKIPS something: a required verification, or an
        # open P0 on a pushed path. Since open P0s block every push, a small push waived
        # past one exited 0 unrecorded (cross-model 2026-09-23 final, Codex F1/Grok F4).
        if v.qualified or open_p0_against(root, {p for p, _a, _r in changes}):
            record_waiver(root, [p for p, _a, _r in changes], waive)
            print(f"cross-model verification WAIVED: {waive}\n"
                  f"  recorded; total waivers: {waiver_count(root)}", file=sys.stderr)
        return 0

    v = check_chain(root, args.base, args.tip) if args.base else None
    if v is None:
        v = check(root, changes, since=args.since, tip=args.tip)
    if v.blocked:
        print(f"\nBLOCKED: cross-model verification\n\n{v.message}\n", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
