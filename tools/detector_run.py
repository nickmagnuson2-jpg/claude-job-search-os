#!/usr/bin/env python3
"""detector_run.py -- close the loop: a rule's own fires increment its own counter.

THE PROBLEM THIS SOLVES. Measured 2026-08-25: 397 of 513 feedback rules (77.4%) sit at
`occurrences: 1`. A rule enters the promotion backlog only at 2, and nothing increments
that number automatically -- a session has to notice "this is the second time." That makes
the intake gate to the entire enforcement pipeline a self-policing judgment call, which is
the mechanism `feedback_llm_self_policing_fails` documents as structurally insufficient.
So the drain was being worked while the intake counter ran on someone remembering.

THE EXEMPLAR. This is not a new pattern. `tools/friction_log.py` plus its
`check_script_error_logged.py` hook already close exactly this loop for tooling friction:
detect the fire, increment the count, name the ladder rung. That is the one lane in this
repo whose loop closes without a human. This does the same for behavioural rules, whose
fires appear in the session transcript rather than in a tool's exit status.

    detector fires -> occurrences increments -> the rule crosses 2 -> it enters the backlog

THE SAFETY PROPERTY THAT MATTERS. **A detector is never run until it has been proven to
fire on its own control line.** Each rule carries `detector_signature` (one regex) and
`detector_control` (a verbatim line from its own Origin incident). Before scanning
anything, every detector is probed against its control; any that does not fire is REFUSED,
reported, and excluded from the scan. Per
`feedback_a_negative_result_from_an_unvalidated_instrument_is_not_evidence`, a quiet scan
from an unproven detector is not evidence of no fires -- it is no evidence at all, wearing
the costume of a clean result. Measured on the first corpus of 37 detectors, 10 could not
fire on their own control, so this is not a hypothetical guard.

COUNTING IS DEDUPED, because a rule that fires once must not read as firing forty times.
The key is (rule, session_id, matched_line_hash), mirroring friction_log.py's dedupe by
(surface, nature) and scan_transcript_failures.py's by tool_use_id.

DRY RUN IS THE DEFAULT. `--apply` is required to write. Auto-incrementing a counter that
gates real build work is a mutation, and a regex that over-fires would inflate the backlog
silently.

Exit codes:
    0  scan completed (fires may or may not have been found)
    2  at least one registered detector FAILED its control probe and was refused
    1  bad usage / unreadable inputs
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import detector_probe as dp  # noqa: E402

FRONTMATTER_KEYS = ("detector_signature", "detector_control", "occurrences", "exit_path")


def parse_frontmatter(text: str) -> dict:
    """Read the frontmatter with the SAME strict reader that wrote it.

    This used to be a flat regex scrape that stripped surrounding quotes and returned the
    raw text. That reads a YAML double-quoted scalar wrong: `apply_memory_verdicts.py`
    emits `"\\berror\\b"` and verifies it with yaml.safe_load, but a scrape hands back the
    literal two-character `\\b` instead of `\b`. Every regex written that way then failed
    to compile or failed to match, and 40 of 50 detectors were refused on their first live
    run despite having been verified minutes earlier.

    That is the repo's own rule about verifying with the strictest reader downstream rather
    than the most convenient one, on a value this module is the downstream reader of. The
    writer's reader is yaml.safe_load, so this is yaml.safe_load.
    """
    m = re.match(r"\A---\n(.*?\n)---\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        loaded = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    out: dict = {}
    for k, v in loaded.items():
        if isinstance(v, dict):
            out.update({ik: iv for ik, iv in v.items()})
        else:
            out[k] = v
    return {k: ("" if v is None else v) for k, v in out.items()}


def load_detectors(memory_dir: Path) -> list[dict]:
    """Every rule carrying a detector_signature. The rule file is the single source of truth.

    Deliberately NOT a separate registry file: a second list of detectors would drift from
    the rules it describes, and reconciling the two is the duplicated-domain-logic defect
    this repo already has a rule about.
    """
    out = []
    for path in sorted(memory_dir.glob("feedback_*.md")):
        fm = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        sig = (fm.get("detector_signature") or "").strip()
        if not sig:
            continue
        try:
            occ = int(fm.get("occurrences", 0))
        except (TypeError, ValueError):
            occ = 0
        out.append({
            "name": path.name,
            "regex": sig,
            "control": (fm.get("detector_control") or "").strip(),
            "occurrences": occ,
        })
    return out


def validate(detectors: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split into (proven, refused). A detector with no control line is REFUSED, not trusted.

    An empty control is the same failure as a non-matching one: there is no evidence the
    regex can fire. Treating "no control supplied" as "nothing to check, carry on" is how a
    guard silently becomes decorative.
    """
    proven, refused = [], []
    for d in detectors:
        if not d["control"]:
            refused.append({**d, "why": "no detector_control line to probe against"})
            continue
        try:
            re.compile(d["regex"])
        except re.error as exc:
            refused.append({**d, "why": f"regex does not compile: {exc}"})
            continue
        r = dp.probe(f"`{d['regex']}`", d["control"])
        if r["fired"]:
            proven.append(d)
        else:
            refused.append({**d, "why": f"does not fire on its own control ({r['status']})"})
    return proven, refused


def _fire_key(rule: str, session: str, line: str) -> str:
    h = hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:16]
    return f"{rule}|{session}|{h}"


def iter_text_units(raw_line: str):
    """Yield the human/model TEXT inside one JSONL transcript record.

    A transcript line is a JSON envelope, not prose. Scanning the raw line means a regex
    matching anywhere inside a 50 KB record -- in a tool_use_id, a queue-operation event, a
    task-notification wrapper, a base64 blob -- counts as a fire. Measured before this fix:
    2,512 "fires" across 45 rules, whose top hits were transcript machinery rather than
    anything anyone said. The unit of analysis WAS the finding.

    So: parse the envelope and yield only assistant/user text blocks and tool-result text.
    A record that will not parse falls back to the raw line, because a detector that
    silently skips malformed records is a detector that under-reports and calls it quiet.
    """
    try:
        rec = json.loads(raw_line)
    except (ValueError, TypeError):
        yield raw_line
        return
    if not isinstance(rec, dict):
        yield raw_line
        return
    # Transcript machinery carries no authored text worth scanning.
    if rec.get("type") in {"queue-operation", "summary", "file-history-snapshot"}:
        return
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("text"), str):
                yield block["text"]
            inner = block.get("content")
            if isinstance(inner, str):
                yield inner
            elif isinstance(inner, list):
                for sub in inner:
                    if isinstance(sub, dict) and isinstance(sub.get("text"), str):
                        yield sub["text"]


# A single JSONL transcript record can be hundreds of KB on one line. Running 50 regexes
# with unanchored bridges over text that size backtracks catastrophically -- the first live
# run timed out at two minutes. Scanning per line with a length cap keeps it linear and
# matches how the false-positive rate was measured in the first place.
MAX_LINE_CHARS = 200_000


def scan_text(text: str, session: str, proven: list[dict], seen: set) -> list[dict]:
    """Find fires in one transcript. Scans authored TEXT, deduped by (rule, session, text)."""
    compiled = [(d, re.compile(d["regex"])) for d in proven]
    fires = []
    for raw in text.splitlines():
        if not raw or len(raw) > MAX_LINE_CHARS:
            continue
        for unit in iter_text_units(raw):
            if not unit:
                continue
            for d, pat in compiled:
                if not pat.search(unit):
                    continue
                key = _fire_key(d["name"], session, unit)
                if key in seen:
                    continue
                seen.add(key)
                fires.append({"rule": d["name"], "session": session, "line": unit[:300],
                              "occurrences_before": d["occurrences"]})
    return fires


def count_oversized(text: str) -> int:
    return sum(1 for line in text.splitlines() if len(line) > MAX_LINE_CHARS)


def scan(memory_dir: Path, transcripts: list[Path]) -> dict:
    detectors = load_detectors(memory_dir)
    if not detectors:
        # An empty registry is a REPORTED state, not a silent clean scan. `scanned: null`
        # rather than 0 is deliberate: 0 reads as "looked and found none" when the truth is
        # "never looked", and that is the exact false-clean shape this whole tool exists to
        # refuse. `available` records how many transcripts WOULD have been scanned, so the
        # output cannot be misread as a completed sweep.
        return {"registered": 0, "proven": 0, "refused": [], "fires": [],
                "transcripts_scanned": None, "transcripts_available": len(transcripts),
                "ok": True,
                "note": ("no rule carries a detector_signature yet, so NOTHING WAS SCANNED. "
                         "This is not a clean result; it is an empty registry.")}

    proven, refused = validate(detectors)
    seen: set = set()
    fires: list[dict] = []
    scanned = 0
    skipped = 0
    for path in transcripts:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        skipped += count_oversized(text)
        fires.extend(scan_text(text, path.stem, proven, seen))

    return {
        "registered": len(detectors),
        "proven": len(proven),
        "refused": refused,
        "fires": fires,
        "transcripts_scanned": scanned,
        "lines_skipped_oversized": skipped,
        "ok": not refused,
    }


def verdict_rows(fires: list[dict]) -> list[str]:
    """One `apply_memory_verdicts.py` row per rule, incremented by its distinct fire count."""
    counts: dict[str, int] = {}
    before: dict[str, int] = {}
    for f in fires:
        counts[f["rule"]] = counts.get(f["rule"], 0) + 1
        before[f["rule"]] = f["occurrences_before"]
    return [f"{rule}\toccurrences={before[rule] + n}" for rule, n in sorted(counts.items())]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-dir", required=True, type=Path)
    ap.add_argument("--transcripts", type=Path, help="directory of *.jsonl session transcripts")
    ap.add_argument("--apply", action="store_true",
                    help="emit the verdict rows to stdout for apply_memory_verdicts.py")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.memory_dir.is_dir():
        print(f"not a directory: {args.memory_dir}", file=sys.stderr)
        return 1
    files = sorted(args.transcripts.glob("*.jsonl")) if args.transcripts and args.transcripts.is_dir() else []
    report = scan(args.memory_dir, files)

    if args.json:
        print(json.dumps(report, indent=2))
    elif args.apply:
        for row in verdict_rows(report["fires"]):
            print(row)
    else:
        if report["registered"] == 0:
            print(f"registered 0 | NOTHING SCANNED ({report['transcripts_available']} "
                  f"transcripts available) | {report['note']}")
        else:
            print(f"registered {report['registered']} | proven {report['proven']} | "
                  f"refused {len(report['refused'])} | "
                  f"transcripts scanned {report['transcripts_scanned']} | "
                  f"oversized lines skipped {report['lines_skipped_oversized']} | "
                  f"fires {len(report['fires'])}")
        for r in report["refused"]:
            print(f"  REFUSED {r['name']}: {r['why']}")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
