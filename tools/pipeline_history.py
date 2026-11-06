#!/usr/bin/env python3
"""pipeline_history.py - one pipeline-history check for both role scanners.

Nick, 2026-11-06: a role at a company he has history with ALWAYS shows, labelled
("they declined you 10/03 (Ops Lead)", "you passed 09/12 (Strategy)").
The only role held back is an exact repeat of a posting he passed on, and it goes to a
visible skipped list, never silently. Spec: output/infra-backlog/110626-pipeline-history-
check-spec.md (reviewed by Codex + Grok, ledger runs 940 and 941).

Before this module the two scanners defined the rule two ways and neither matched it:
/scan-jobs left every role at ANY company ever in the pipeline out of its proposals with
no reason, and the nightly scanner tagged only a relisting of the same role at an
archived row, so a different role at a company that had declined him came back unlabelled.

One company matcher (same_company / company_overlap) is used for the history, for
/scan-jobs' possible_match mark, and for the nightly duplicate check (dedup.find_match).

The history source is the job-pipeline.md table through pipe_read.parse_all_rows, the
one parser for all rows. load_history is the single seam for a later source: job-search-49's
pipeline-events-1106 adds data/pipeline-events.jsonl.
"""
from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from tools.name_dedup import normalize_name

SAME_ROLE_RATIO = 0.90          # dedup.find_match's D-10 threshold, one value for both
SHOWN_ROWS = 2

DECLINED_YOU, YOU_PASSED, CLOSED, IN_PIPELINE, UNKNOWN = (
    "declined_you", "you_passed", "closed", "in_pipeline", "unknown")
ACTIVE = (IN_PIPELINE,)


def load_history(root: Path) -> list[dict]:
    """Every pipeline row, active and archived, with its outcome. [] when unreadable:
    a scan must never fail because the history could not be read."""
    try:
        from tools.pipe_read import parse_all_rows
        text = (Path(root) / "data" / "job-pipeline.md").read_text(encoding="utf-8")
        rows = parse_all_rows(text, date.today())
    except (ImportError, OSError, UnicodeDecodeError):
        return []
    return [{"company": r.get("company", ""), "role": r.get("role", ""),
             "stage": r.get("stage", ""), "date": r.get("date_updated", ""),
             "notes": r.get("notes", ""), "url": r.get("url", ""),
             "archived": bool(r.get("archived")), "outcome": classify(r.get("stage", ""))}
            for r in rows if (r.get("company") or "").strip()]


def classify(stage: str) -> str:
    """declined_you | you_passed | closed | in_pipeline | unknown (a blank stage)."""
    from tools.stage_vocab import is_terminal_stage
    s = (stage or "").strip().lower()
    if not s:
        return UNKNOWN
    if re.search(r"\brejected\b|\bthey passed\b|\bthey declined\b", s):
        return DECLINED_YOU
    if re.search(r"\bwithdrawn\b|\bskipped\b|^considered\s*-\s*passed\b", s):
        return YOU_PASSED
    return CLOSED if is_terminal_stage(stage) else IN_PIPELINE


def same_company(a: str, b: str) -> bool:
    """Exact after normalize_name (case, accents, punctuation, spacing). Empty never matches."""
    na, nb = normalize_name(a), normalize_name(b)
    return bool(na) and na == nb


def company_overlap(a: str, b: str) -> bool:
    """One name is a whole-word part of the other ("Acme" / "Acme AI"), not exact.
    A substring is not enough: "Northwindtech" is not "Northwind" (613.F1)."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb or na == nb:
        return False
    return bool(re.search(rf"\b{re.escape(nb)}\b", na) or re.search(rf"\b{re.escape(na)}\b", nb))


def match_company(name: str, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(rows at the same company, rows at a possibly-same company)."""
    exact = [r for r in rows if same_company(name, r["company"])]
    possible = [r for r in rows if company_overlap(name, r["company"])]
    return exact, possible


def _url(u) -> str:
    return (u or "").strip().rstrip("/").lower()


def same_posting(role: dict, row: dict) -> bool:
    """The same posting: the same non-empty URL. A title alone is not a posting."""
    return bool(_url(role.get("url"))) and _url(role.get("url")) == _url(row.get("url"))


def same_role(role: dict, row: dict) -> bool:
    """The same posting, or a title ratio >= 0.90 after dedup's title normalisation."""
    from tools.career_scanner.dedup import _norm_title
    if same_posting(role, row):
        return True
    a, b = _norm_title(role.get("title", "")), _norm_title(row.get("role", ""))
    return bool(a) and bool(b) and SequenceMatcher(None, a, b).ratio() >= SAME_ROLE_RATIO


def _when(row: dict) -> str:
    m = re.match(r"^\d{4}-(\d{2})-(\d{2})", (row.get("date") or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else "date unknown"


def _phrase(row: dict) -> str:
    o, when = row["outcome"], _when(row)
    role = f" ({row['role']})" if (row.get("role") or "").strip() else ""
    if o == DECLINED_YOU:
        return f"they declined you {when}{role}"
    if o == YOU_PASSED:
        # No reason from `notes`: on the live pipeline (2026-11-06) notes do not lead
        # with the reason ("DOSSIER REFRESHED ... via /act"), so a first-clause reason
        # misinforms. The date and role point Nick at the row.
        return f"you passed {when}{role}"
    if o == CLOSED:
        return f"closed {when}: {row['stage']}{role}"
    if o == IN_PIPELINE:
        return f"in pipeline: {row['stage']}{role}"
    return f"stage unknown, {when}{role}"


def history(role: dict, rows: list[dict]) -> dict:
    """{label, declined_repeat, already_in_pipeline, possible_match}.

    declined_repeat: an exact repeat of a posting he passed on (same company, same URL).
    already_in_pipeline: the same role at the same company is an active pipeline row.
    Every other role shows, with the label; neither flag ever hides a role silently.
    """
    exact, possible = match_company(role.get("company", ""), rows)
    newest = sorted(exact, key=lambda r: r.get("date") or "", reverse=True)
    parts = [_phrase(r) for r in newest[:SHOWN_ROWS]]
    if len(newest) > SHOWN_ROWS:
        parts.append(f"+{len(newest) - SHOWN_ROWS} more")
    declined_repeat = any(r["outcome"] == YOU_PASSED and same_posting(role, r) for r in exact)
    if not declined_repeat and any(r["outcome"] == YOU_PASSED and same_role(role, r) for r in exact):
        parts.insert(0, "likely the role you passed on")
    names = sorted({r["company"] for r in possible})
    if names:
        parts.append("possible match: " + ", ".join(names))
    return {"label": "; ".join(parts),
            "declined_repeat": declined_repeat,
            "already_in_pipeline": any(r["outcome"] in ACTIVE and same_role(role, r) for r in exact),
            "possible_match": names[0] if names else ""}
