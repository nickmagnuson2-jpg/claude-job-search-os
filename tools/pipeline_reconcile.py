#!/usr/bin/env python3
"""pipeline_reconcile.py -- reconcile an agent-produced company list against the pipeline.

A research agent ranks companies from the public web. It cannot see
`data/job-pipeline.md`, so a company already closed looks identical to a fresh lead and
the rationale reads just as confidently. This module answers one deterministic question:

    which of these names are already in the pipeline, and on what rows?

It REPORTS. It does not decide. Nothing here suppresses a company, because the decision
to re-approach depends on why a loop closed and how long ago, which is the user's call.

Origin: `feedback_diff_agent_recommendations_against_closed_rows`, 2nd fire 2026-09-14.
Fire 1 was caught only because the company sat in always-loaded Critical Context; fire 2
involved two companies that did not, and both reached the user in a written summary.

NOT NAMED `check_*.py`, deliberately. In this repo that prefix is the PreToolUse hook
namespace: 41 such files, 34 wired as hooks, three test files enumerate the glob, and any
non-hook member must carry a written NON_HOOK_CHECKERS justification. This is a library
called from a skill, not a hook -- there is no tool call to intercept, because the failure
happens when an agent's output is summarized into prose. The first draft was named
`check_closed_rows.py` and `check_guard_edit_approval.py` blocked it, correctly.

DESIGN NOTES, each one earned:

1. ONLY normalized-exact resolves automatically. `name_dedup.normalize_name` already
   folds `Acme Co.` and `Acme Co` onto `acme co`, so a containment tier buys nothing for
   punctuation variants while silently merging distinct companies: `acme` is a substring
   of `acmecorp`, and a three-letter name is a substring of a four-letter one that adds a
   single character. Containment and similarity are EVIDENCE FOR A HUMAN, never identity.
   Raised by cross-model review 2026-09-14 and confirmed against live data.

2. Every matched row is returned. The pipeline is role-granular (`Company | Role | Stage`),
   so one company can hold a closed row and a live one. Synthesizing a single company
   status destroys that and can hide a live pursuit. Measured 2026-09-14: zero companies
   currently have mixed rows, so this is prevention, not a live bug fix.

3. Input failure is LOUD. `pipe_read.read_file` converts FileNotFoundError and OSError to
   an empty string, and `parse_all_rows` silently skips any row with fewer than 3 columns.
   Either path yields a clean-looking empty result from a broken read. This module reads
   the file itself, requires a header, and reports `parse_errors`.

4. Conservation is asserted, not assumed. Every input name lands in exactly one bucket.
   A checker that quietly drops a name is worse than no checker, because a deterministic
   "clean" verdict stops humans from repeating the manual check.

Reuse, per the repo's no-new-parsers rule: `pipe_read.parse_all_rows` for the table,
`stage_vocab.is_terminal_stage` for classification, `name_dedup.normalize_name` for
identity. Deliberately NOT `career_scanner/dedup.py`: its `load_pipeline_entries` returns
only `active_entries`, the exact complement of the rows this needs, and its `is_duplicate`
compares company strings for equality. It answers role-level duplication for the scanner,
which is a different question.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from name_dedup import normalize_name, similarity  # noqa: E402
from pipe_read import parse_all_rows  # noqa: E402
from stage_vocab import is_terminal_stage  # noqa: E402

# A name this short is almost always an acronym or a fragment; reporting similarity
# candidates for it produces noise rather than signal.
_MIN_FUZZY_LEN = 4
# Tuned to surface near-miss pairs as CANDIDATES for review. It is deliberately NOT a
# resolution threshold -- nothing auto-resolves on this number.
_FUZZY_CANDIDATE_THRESHOLD = 0.80

STATE_ACTIVE = "active_pipeline"
STATE_TERMINAL = "terminal_history_only"
STATE_MIXED = "mixed_history"
STATE_AMBIGUOUS = "ambiguous_name"
STATE_CLEAR = "not_found"


class PipelineUnreadable(RuntimeError):
    """Raised when the pipeline cannot be read or does not look like a pipeline.

    A hard failure on purpose. The alternative -- returning "no matches" -- is
    indistinguishable from a genuine all-clear and is the defect this module exists
    to prevent, one level down.
    """


def load_rows(pipeline_path: Path) -> tuple[list[dict], list[str]]:
    """Read and parse the pipeline. Raises rather than returning an empty result.

    Returns (rows, parse_errors). `parse_errors` names table-looking lines that
    `parse_all_rows` dropped, so a truncated or malformed file cannot present as clean.
    """
    try:
        content = pipeline_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PipelineUnreadable(f"cannot read {pipeline_path}: {exc}") from exc

    if not content.strip():
        raise PipelineUnreadable(f"{pipeline_path} is empty")
    if "| Company" not in content and "|Company" not in content:
        raise PipelineUnreadable(
            f"{pipeline_path} has no pipeline header; refusing to report a clean pass "
            "from a file that may not be the pipeline")

    rows = parse_all_rows(content, date.today())
    if not rows:
        raise PipelineUnreadable(
            f"{pipeline_path} parsed to zero rows despite having a header")

    parse_errors: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cols = [c.strip() for c in stripped.strip("|").split("|")]
        # No separator/header exemption here, deliberately. A real pipeline header and
        # its `|---|` separator both carry the full column count, so they never trip the
        # width check; an exemption for them survived mutation testing because nothing
        # could reach it. Worse, it would HIDE a genuinely malformed short header or a
        # bare `|` line, which is exactly what this scan exists to surface.
        if len(cols) < 3:
            parse_errors.append(stripped[:120])

    return rows, parse_errors


def _index(rows: list[dict]) -> dict[str, list[dict]]:
    """Group rows by normalized company name. Rows whose company is blank are dropped
    here rather than silently matching the empty string."""
    idx: dict[str, list[dict]] = {}
    for row in rows:
        key = normalize_name(row.get("company", ""))
        if not key:
            continue
        idx.setdefault(key, []).append(row)
    return idx


def _row_view(row: dict) -> dict:
    return {
        "company": row.get("company", ""),
        "role": row.get("role", ""),
        "stage": row.get("stage", ""),
        "date_updated": row.get("date_updated", ""),
        "url": row.get("url", ""),
        "terminal": is_terminal_stage(row.get("stage", "")),
    }


def _candidates(key: str, idx: dict[str, list[dict]]) -> list[dict]:
    """Similarity and containment candidates, for HUMAN review only.

    Never used to resolve. A containment hit is reported with its own flag so a reader
    can see why a name surfaced.
    """
    if len(key) < _MIN_FUZZY_LEN:
        return []
    # No `other == key` guard: _candidates runs only after an exact lookup missed, so a
    # self-match is unreachable here. A guard for it survived mutation testing because
    # nothing can exercise it, which is the signature of dead code, not of a missing test.
    out = []
    for other in idx:
        score = similarity(key, other)
        contained = key in other or other in key
        if score >= _FUZZY_CANDIDATE_THRESHOLD or contained:
            out.append({
                "pipeline_company": idx[other][0].get("company", ""),
                "score": round(score, 3),
                "containment": contained,
                "rows": [_row_view(r) for r in idx[other]],
            })
    return sorted(out, key=lambda c: (-c["score"], c["pipeline_company"]))


def resolve(names: list[str], pipeline_path: Path) -> dict:
    """Classify each name against the pipeline.

    Every input name appears in exactly one bucket. Conservation is asserted before
    returning, because a dropped name is the failure this module exists to prevent.
    """
    rows, parse_errors = load_rows(pipeline_path)
    idx = _index(rows)

    buckets: dict[str, list] = {
        STATE_ACTIVE: [], STATE_TERMINAL: [], STATE_MIXED: [],
        STATE_AMBIGUOUS: [], STATE_CLEAR: [],
    }
    blank: list[str] = []

    for raw in names:
        key = normalize_name(raw)
        if not key:
            blank.append(raw)
            continue

        matched = idx.get(key)
        if matched:
            views = [_row_view(r) for r in matched]
            terminal_flags = {v["terminal"] for v in views}
            if terminal_flags == {True}:
                state = STATE_TERMINAL
            elif terminal_flags == {False}:
                state = STATE_ACTIVE
            else:
                state = STATE_MIXED
            buckets[state].append({"name": raw, "matched": True, "rows": views})
            continue

        cands = _candidates(key, idx)
        if cands:
            buckets[STATE_AMBIGUOUS].append(
                {"name": raw, "matched": False, "candidates": cands})
        else:
            buckets[STATE_CLEAR].append({"name": raw, "matched": False})

    placed = sum(len(v) for v in buckets.values()) + len(blank)
    if placed != len(names):
        raise AssertionError(f"conservation failure: {len(names)} in, {placed} out")

    return {
        "status": "ok",
        "pipeline": str(pipeline_path),
        "rows_parsed": len(rows),
        "names_in": len(names),
        "blank_names": blank,
        "parse_errors": parse_errors,
        **buckets,
    }


def render(result: dict) -> str:
    """Markdown block for pasting into a dossier.

    The exclusion has to land in the ARTIFACT, not only in chat: both recorded fires were
    relayed in chat, and the dossier is what a later session reads.
    """
    lines = ["### Already in your pipeline", ""]
    any_hit = False

    for state, heading in (
        (STATE_ACTIVE, "Currently pursuing"),
        (STATE_MIXED, "Mixed history -- has both closed and live rows, do NOT exclude"),
        (STATE_TERMINAL, "Previously closed -- reconsider?"),
    ):
        items = result.get(state) or []
        if not items:
            continue
        any_hit = True
        lines.append(f"**{heading}**")
        lines.append("")
        for item in items:
            for row in item["rows"]:
                lines.append(
                    f"- **{item['name']}** -- {row['role'] or '(no role)'} | "
                    f"{row['stage']} | {row['date_updated']}")
        lines.append("")

    amb = result.get(STATE_AMBIGUOUS) or []
    if amb:
        any_hit = True
        lines.append("**Similar names -- NOT resolved automatically, check by hand**")
        lines.append("")
        for item in amb:
            for cand in item["candidates"]:
                why = "contains/contained" if cand["containment"] else f"score {cand['score']}"
                lines.append(
                    f"- **{item['name']}** resembles pipeline company "
                    f"**{cand['pipeline_company']}** ({why})")
        lines.append("")

    if result.get("parse_errors"):
        any_hit = True
        lines.append(
            f"**WARNING: {len(result['parse_errors'])} malformed pipeline row(s) skipped.** "
            "This result may be incomplete.")
        lines.append("")

    if not any_hit:
        lines.append(
            f"None of the {result['names_in']} names appear in the pipeline "
            f"({result['rows_parsed']} rows checked).")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Reconcile a company list against data/job-pipeline.md.")
    ap.add_argument("names", nargs="*",
                    help="company names; omit to read stdin, one per line")
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--format", choices=("json", "markdown"), default="json")
    args = ap.parse_args(argv)

    root = Path(args.repo_root) if args.repo_root else Path(__file__).resolve().parent.parent
    names = args.names or [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    if not names:
        print(json.dumps({"status": "error", "message": "no names supplied"}))
        return 2

    try:
        result = resolve(names, root / "data" / "job-pipeline.md")
    except PipelineUnreadable as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 2

    if args.format == "markdown":
        print(render(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    hits = len(result[STATE_ACTIVE]) + len(result[STATE_TERMINAL]) + len(result[STATE_MIXED])
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
