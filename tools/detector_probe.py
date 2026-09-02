#!/usr/bin/env python3
"""detector_probe.py -- a detector that cannot fire on its own control is not a detector.

WHY THIS EXISTS. `tools/promotion_schema.py` makes a `detector_signature` mandatory for
any rule taking exit 2 (the always-loaded channel). That requirement is a PRESENCE check:
it proves a field is non-empty, not that the regex inside it detects anything. A rule can
satisfy it with a pattern that matches nothing, which is precisely the failure the exit-2
contract exists to prevent -- a promotion that reports as drained and changes nothing.

This is the property half. Given a detector's prose and a control line known to be present
in the original incident, it extracts the candidate patterns and RUNS them. A detector that
does not fire on its own control is unproven, per
`feedback_a_negative_result_from_an_unvalidated_instrument_is_not_evidence`: a NOT FOUND
from a tool never shown able to read the format is not evidence of absence.

EXTRACTION IS BEST-EFFORT AND SAYS SO. Detector prose is written by agents and mixes
regexes with description. This pulls candidates from backtick spans and inline-code spans,
keeps those that compile AND contain regex metacharacters, and reports `no_pattern_found`
when it finds none -- never a silent pass. `no_pattern_found` is a FAILURE to validate, not
a clean result: an empty extraction and a working detector must never look alike.

WHITESPACE. Every pattern is tried against the raw control and against a
`re.sub(r"\\s+", " ", ...)` normalization of it. The 2nd fire of the rule above was a
phrase that WAS present but line-wrapped inside a narrow table cell, so a detector that
only works pre-wrap is a detector that fails in production.

Exit codes:
    0  every probed detector fired on its control
    2  at least one did not fire, or no pattern could be extracted
    1  bad usage / unreadable input
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Characters that distinguish a regex from a plain backticked filename or phrase.
_METACHARS = re.compile(r"[\\\[\](){}|+*?^$]|\(\?[im]")

# Backtick spans (``...`` and `...`) are where agents put patterns.
_CODE_SPAN = re.compile(r"``(.+?)``|`([^`\n]+)`", re.DOTALL)

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WS.sub(" ", text).strip()


def extract_patterns(prose: str) -> list[str]:
    """Candidate regexes from detector prose, longest first.

    Longest-first matters: agents often write the full pattern in one span and a fragment
    of it in another, and reporting the fragment as "the detector" would overstate what
    was actually validated.
    """
    out: list[str] = []
    for m in _CODE_SPAN.finditer(prose or ""):
        span = (m.group(1) or m.group(2) or "").strip()
        if not span or not _METACHARS.search(span):
            continue
        try:
            re.compile(span)
        except re.error:
            continue
        if span not in out:
            out.append(span)
    return sorted(out, key=len, reverse=True)


def probe(prose: str, control: str) -> dict:
    """Does any extracted pattern fire on the control line, raw or whitespace-normalized?"""
    patterns = extract_patterns(prose)
    if not patterns:
        return {"status": "no_pattern_found", "fired": False, "patterns_tried": 0,
                "matched_pattern": None, "needed_normalization": False}

    norm = normalize(control)
    for pat in patterns:
        try:
            if re.search(pat, control, re.MULTILINE):
                return {"status": "fired", "fired": True, "patterns_tried": len(patterns),
                        "matched_pattern": pat, "needed_normalization": False}
            if re.search(pat, norm, re.MULTILINE):
                return {"status": "fired", "fired": True, "patterns_tried": len(patterns),
                        "matched_pattern": pat, "needed_normalization": True}
        except re.error:
            continue
    return {"status": "did_not_fire", "fired": False, "patterns_tried": len(patterns),
            "matched_pattern": None, "needed_normalization": False}


def probe_records(records: list[dict]) -> dict:
    """Probe a list of {name, prose, control} records."""
    if not records:
        # An empty run is an error, never a clean bill of health.
        raise ValueError("no detector records to probe")
    results = []
    for rec in records:
        r = probe(rec.get("prose", ""), rec.get("control", ""))
        r["name"] = rec.get("name", "?")
        results.append(r)
    fired = sum(1 for r in results if r["fired"])
    return {
        "probed": len(results),
        "fired": fired,
        "did_not_fire": sum(1 for r in results if r["status"] == "did_not_fire"),
        "no_pattern_found": sum(1 for r in results if r["status"] == "no_pattern_found"),
        "needed_normalization": sum(1 for r in results if r.get("needed_normalization")),
        "results": results,
        "ok": fired == len(results),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", required=True, type=Path,
                    help="JSON list of {name, prose, control}")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.records.is_file():
        print(f"records file not found: {args.records}", file=sys.stderr)
        return 1
    try:
        report = probe_records(json.loads(args.records.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError, AttributeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"probed {report['probed']} | fired {report['fired']} | "
              f"did not fire {report['did_not_fire']} | "
              f"no pattern found {report['no_pattern_found']} | "
              f"needed whitespace normalization {report['needed_normalization']}")
        for r in report["results"]:
            if not r["fired"]:
                print(f"  UNPROVEN {r['name']}: {r['status']} "
                      f"({r['patterns_tried']} pattern(s) tried)")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
