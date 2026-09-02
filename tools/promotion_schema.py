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

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GRANDFATHER = REPO_ROOT / "tools" / "promotion-schema-grandfather.json"

# A gate that names no number cannot trip on a count; it waits for a human to notice.
# 38 of the 69 live candidates read like this on 2026-08-25.
_NUMBER_IN_GATE = re.compile(
    r"\b(\d+\s*(?:more\s+)?(?:dated\s+)?(?:fire|occurrence)"
    r"|2nd|3rd|4th|5th|6th|second|third|fourth|fifth)\b",
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
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
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


def gate_names_a_number(gate: str) -> bool:
    return bool(_NUMBER_IN_GATE.search(gate or ""))


def load_grandfather(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object of file -> reason")
    return data


def check_file(name: str, fm: dict, grandfathered: bool) -> list[str]:
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

    if exit_path and exit_path not in VALID_EXIT_PATHS:
        violations.append(
            f"{name}: I2 exit_path {exit_path!r} is not one of {'|'.join(VALID_EXIT_PATHS)}"
        )

    # I3 and I4 are never waived. See the module docstring.
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

    violations: list[str] = []
    promoted_total = 0
    with_date = 0
    with_exit_path = 0
    by_exit: dict[str, int] = {}

    for p in files:
        fm = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        name = p.name
        if is_promoted_value(fm.get("promoted", "no")):
            promoted_total += 1
            if fm.get("promoted_date", "").strip():
                with_date += 1
        ep = fm.get("exit_path", "").strip()
        if ep:
            with_exit_path += 1
            by_exit[ep] = by_exit.get(ep, 0) + 1
        violations.extend(check_file(name, fm, grandfathered=name in grandfather))

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
