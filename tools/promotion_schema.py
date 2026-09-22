#!/usr/bin/env python3
"""promotion_schema.py -- make a promotion an OBSERVABLE EVENT, and make exit 2 cost something.

Why this exists
---------------
On 2026-08-25 the corpus held 509 feedback rules, 358 of them (70.3%) never promoted.
The drain rate could not even be measured, because only 38 of the 149 promoted files
carried a date anywhere. A promotion left no record, so "are we draining faster than we
capture" had no answer. Per CLAUDE.md's enforcement trichotomy, "a field a schema demands"
is a real tier; a prose request to write the date down is not.

The second job is harder and matters more. A rule leaves the queue by one of two exits:

    exit1     mechanical -- a hook, a schema field, a deterministic check. Verifiable.
    exit2     a principle no gate can express, promoted into always-loaded MEMORY.md.

exit2 is unverifiable and cheap, and exit1 is verifiable and expensive, so exit2 is the
path of least resistance and will be taken by default unless it is made to cost something.
The mechanism: **a detector is the price of admission to exit2**. An exit2 rule must ship
a violation signature detectable in a session transcript after the fact -- slot three of
the trichotomy, "a record a run cannot skip". A rule too vague to write a detector for is
too vague to follow, so failing this check sends the rule back for restating rather than
into the loaded channel.

That requirement is enforced here, in the schema, rather than asked for in a prompt.

Invariants
----------
I1  promoted yes/partial  => `promoted_date: YYYY-MM-DD` present and a real date.
I2  promoted yes/partial  => `exit_path` present and one of exit1|exit2|terminal.
I3  exit_path == exit2    => `detector_signature` present and non-empty.
I4  exit_path present     => `reopen_gate` names a NUMBER ("3rd fire"), never the no-op
                             "reopen on the next dated fire", which cannot trip on a count.
I5  a grandfather entry   => carries a non-empty written reason.
I6  promoted              => its leading token is in the vocabulary, never invented.
I7  exit_path == exit2    => the rule is named in MEMORY.md, the channel exit2 MEANS.

I1 and I2 are waived for files listed in the grandfather file, which records promotions
made before the field existed. The waiver is deliberately noisy: the legacy debt is
counted and named rather than hidden, the same discipline tools/mutation-allow.json uses
for surviving mutants. I3 and I4 are NEVER waived -- they govern promotions made from now
on, and waiving them would rebuild the escape hatch this module exists to close.

Exit codes
----------
    0  every scanned file satisfies the invariants
    2  at least one violation (BLOCK -- an exit-0 warning is not a gate, since Claude Code
       never surfaces hook stderr)
    1  bad usage / unreadable inputs
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

VALID_EXIT_PATHS = ("exit1", "exit2", "terminal")

# The vocabulary `promoted:` may open with. A qualifier after the token is ENCOURAGED --
# "partial -- open_draft.py reply mode (NOT proposed)" is the good shape -- but the TOKEN
# is what every consumer keys on, so it may not be invented.
#
# MEASURED 2026-09-21 across the live tier: 584 `no`, 119 `yes`, 52 `partial`, and 3
# strays ("n/a -- reference" x2, "SUPERSEDED ..." x1). The vocabulary was already closed
# in practice and enforced nowhere, which is the condition in which it drifts. The three
# strays currently read as PROMOTED by scan_promotion_candidates, because its recogniser
# fails OPEN: anything it does not recognise as no/not/false/partial is counted as a
# completed promotion and leaves the backlog silently.
#
# It includes the TIER NAMES as well as the yes/no/partial tokens, because
# scan_promotion_candidates deliberately reads a bare tier as a promotion and that
# behaviour is pinned by its own tests. Closing the vocabulary here rather than there is
# the point: the scanner must keep failing open on an unknown token (or every future tier
# lands in the backlog), so the vocabulary needs a gate somewhere that a human reads.
VALID_PROMOTED_TOKENS = (
    "no", "not", "false", "yes", "partial",
    "skill", "hook", "principle", "hard-rule", "framework", "script", "doc", "schema",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GRANDFATHER = REPO_ROOT / "tools" / "promotion-schema-grandfather.json"

# A gate that names no number cannot trip on a count; it waits for a human to notice.
# 38 of the 69 live candidates read like this on 2026-08-25.
# A gate "names a number" in any of the three shapes people actually write, and the
# ordinal branch is GENERATED rather than enumerated.
#
# MEASURED 2026-09-21, and the old pattern was wrong in three ways at once. It listed
# `2nd|3rd|4th|5th|6th` literally, so "7th fire" and "32nd fire" were rejected -- and the
# corpus holds a rule at 31 occurrences. It required the number BEFORE the noun, so
# "TRIPPED by fire 6" was rejected. And `(?:fire|occurrence)\b` could not match the
# plural, so "reopen at 3 fires" and "2 more fires" were both rejected. Every one of
# those is a well-formed gate, and the check was reporting them as unset.
#
# That matters more than a missed match: the fix for an I4 violation is to EDIT the rule
# file, so a detector that rejects good gates makes the corpus worse when it is obeyed.
_NUMBER_IN_GATE = re.compile(
    r"\b(?:"
    r"\d+\s*(?:st|nd|rd|th)"                                        # 2nd time, 3rd, 32nd fire
    r"|\d+\s*(?:more\s+)?(?:dated\s+)?(?:fire|occurrence|instance|catch|time)s?"
    r"|(?:fire|occurrence|instance)s?\s*#?\s*\d+"                   # fire 6, occurrence 2
    r"|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
    r")\b",
    re.IGNORECASE,
)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_frontmatter(text: str) -> dict:
    """Flat key/value scrape of the YAML frontmatter block.

    Deliberately not yaml.safe_load: these files nest the keys we want under `metadata:`
    and carry prose values with unescaped colons. We only ever read scalars, and a flat
    scrape reads the same key identically whether it sits at the top level or under
    metadata. Values keep their raw text; quotes are stripped.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    lines = text[3:end].splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()

        # YAML BLOCK SCALARS. `reopen_gate: >-` puts the value on the FOLLOWING lines,
        # and the flat scrape used to store the marker ">-" as the value. Measured
        # 2026-09-21: one file in the corpus writes its gate this way, and its gate is
        # word-perfect ("4th fire, OR any fix shipped with a test that was never run
        # against the reverted code") -- reported as naming no number for as long as the
        # check has existed. A parser that silently returns a marker instead of a value
        # makes every downstream check wrong about that file, in the direction of
        # inventing a defect.
        if re.fullmatch(r"[>|][-+]?", val):
            indent = len(line) - len(line.lstrip())
            parts = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                parts.append(nxt.strip())
                i += 1
            val = " ".join(x for x in parts if x)

        if val[:1] in ("'", '"') and val[-1:] == val[:1] and len(val) >= 2:
            val = val[1:-1]
        out[key] = val
    return out


def is_promoted_value(value: str) -> bool:
    """True for `yes` and `partial`. Both are promotions and both need a record.

    `partial` counts deliberately: it means enforcement is half-landed, which is a
    promotion event that happened on a date via some exit path, and the half that landed
    is exactly the part a future session will otherwise re-derive from scratch.
    """
    v = (value or "").strip().lower()
    return v.startswith("yes") or v.startswith("partial")


def promoted_token(value: str) -> str:
    """The leading word of a `promoted:` value, lowercased. '' when there is none."""
    m = re.match(r"\s*([A-Za-z][A-Za-z/-]*)", str(value or ""))
    if not m:
        return ""
    tok = m.group(1).lower().rstrip("-")
    # "n/a" is one token, not "n"; keep the slash so it cannot masquerade as a tier.
    return tok


def gate_names_a_number(gate: str) -> bool:
    return bool(_NUMBER_IN_GATE.search(gate or ""))


def load_grandfather(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object of file -> reason")
    return data


def check_file(name: str, fm: dict, grandfathered: bool,
               in_memory_md: bool | None = None) -> list[str]:
    """Return the list of invariant violations for one file. Empty list means clean."""
    violations: list[str] = []
    promoted = fm.get("promoted", "no")
    exit_path = fm.get("exit_path", "").strip()

    if is_promoted_value(promoted):
        if not grandfathered:
            stamp = fm.get("promoted_date", "").strip()
            if not stamp:
                violations.append(
                    f"{name}: I1 promoted is {promoted!r} but promoted_date is missing, "
                    "so this promotion is not an observable event"
                )
            elif not _ISO_DATE.match(stamp):
                violations.append(f"{name}: I1 promoted_date {stamp!r} is not YYYY-MM-DD")
            if not exit_path:
                violations.append(f"{name}: I2 promoted is {promoted!r} but exit_path is missing")

    # I6. The token is the part consumers key on, so it may not be invented. Without
    # this the `values:` list beside the field is decoration -- the same defect the frame
    # schema carried in its own enum fields until 2026-09-21.
    tok = promoted_token(promoted)
    if tok and tok not in VALID_PROMOTED_TOKENS:
        violations.append(
            f"{name}: I6 promoted opens with {tok!r}, which is not one of "
            f"{'|'.join(VALID_PROMOTED_TOKENS)}. A consumer keying on this token cannot "
            "see a value outside the vocabulary, and the recogniser fails OPEN -- an "
            "unknown token reads as a completed promotion and leaves the backlog"
        )

    if exit_path and exit_path not in VALID_EXIT_PATHS:
        violations.append(
            f"{name}: I2 exit_path {exit_path!r} is not one of {'|'.join(VALID_EXIT_PATHS)}"
        )

    # I3 and I4 are never waived. See the module docstring.
    # I7. exit2 is defined at the top of this file as "a principle no gate can express,
    # PROMOTED INTO ALWAYS-LOADED MEMORY.md". I3 charges the detector as the price of
    # admission and nothing ever checked that the rule was admitted.
    #
    # MEASURED 2026-09-21: 50 rules carry exit_path exit2 and 2 appear in MEMORY.md. The
    # other 48 paid the price and never got in. Each one reads as PROMOTED -- it leaves
    # the backlog, the detector stops surfacing it -- while the mechanism it claims, being
    # loaded into every conversation, does not exist for it. That is the same defect this
    # whole file was built to catch, at the one tier that cannot be verified from the file
    # itself, which is exactly why it went unnoticed.
    #
    # None when the caller cannot see MEMORY.md: unknowable is not a pass, so it is simply
    # not checked rather than assumed either way.
    if exit_path == "exit2" and in_memory_md is False:
        violations.append(
            f"{name}: I7 exit_path is exit2, which MEANS promoted into always-loaded "
            "MEMORY.md, and this rule is not named there. It reads as promoted and left "
            "the backlog, while the channel it claims does not carry it")

    if exit_path == "exit2" and not fm.get("detector_signature", "").strip():
        violations.append(
            f"{name}: I3 exit_path is exit2 but detector_signature is empty. A detector is "
            "the price of admission to the always-loaded channel: a rule too vague to detect "
            "is too vague to follow, and must be restated rather than loaded"
        )

    if exit_path and not gate_names_a_number(fm.get("reopen_gate", "")):
        violations.append(
            f"{name}: I4 reopen_gate names no number, so it can never trip on a count and "
            "waits for a human to notice"
        )

    return violations


def scan(memory_dir: Path, grandfather: dict) -> dict:
    files = sorted(p for p in memory_dir.glob("feedback_*.md") if p.is_file())
    if not files:
        # An empty scan is an error, never a clean bill of health. A guard with no failing
        # mode is the same bug wearing a safety vest.
        raise ValueError(f"no feedback_*.md files found under {memory_dir}")

    # Read the always-loaded channel ONCE. Its absence is not a failure: a caller may
    # point this at a corpus that has no MEMORY.md, and then I7 simply cannot run.
    memory_md = memory_dir / "MEMORY.md"
    memory_text = (memory_md.read_text(encoding="utf-8", errors="replace")
                   if memory_md.exists() else None)

    violations: list[str] = []
    promoted_total = 0
    with_date = 0
    with_exit_path = 0
    by_exit: dict[str, int] = {}

    for p in files:
        fm = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        name = p.name
        in_md = None if memory_text is None else (p.stem in memory_text)
        if is_promoted_value(fm.get("promoted", "no")):
            promoted_total += 1
            if fm.get("promoted_date", "").strip():
                with_date += 1
        ep = fm.get("exit_path", "").strip()
        if ep:
            with_exit_path += 1
            by_exit[ep] = by_exit.get(ep, 0) + 1
        violations.extend(check_file(name, fm, grandfathered=name in grandfather,
                                     in_memory_md=in_md))

    for name, reason in grandfather.items():
        if not str(reason).strip():
            violations.append(
                f"{name}: I5 grandfather entry has an empty reason. An allowlist without "
                "justification is how this decays back into green means done"
            )

    return {
        "scanned": len(files),
        "promoted_total": promoted_total,
        "promoted_with_date": with_date,
        "with_exit_path": with_exit_path,
        "by_exit_path": by_exit,
        "grandfathered": len(grandfather),
        "violations": violations,
        "ok": not violations,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-dir", required=True, type=Path)
    ap.add_argument("--grandfather", type=Path, default=DEFAULT_GRANDFATHER)
    ap.add_argument("--json", action="store_true", help="emit the full report as JSON")
    args = ap.parse_args(argv)

    if not args.memory_dir.is_dir():
        print(f"not a directory: {args.memory_dir}", file=sys.stderr)
        return 1
    try:
        gf = load_grandfather(args.grandfather)
        report = scan(args.memory_dir, gf)
    except (ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"scanned {report['scanned']} rules | promoted {report['promoted_total']} "
            f"({report['promoted_with_date']} dated) | dispositioned {report['with_exit_path']} "
            f"{report['by_exit_path']} | grandfathered {report['grandfathered']}"
        )
        for v in report["violations"]:
            print(f"  VIOLATION {v}")

    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
