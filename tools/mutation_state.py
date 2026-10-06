#!/usr/bin/env python3
"""mutation_state.py — the ONE definition of where mutation work lives.

WHY THIS EXISTS. Five places hardcoded the same string:

    tools/job_quiesce.py:65        tools/mutation_report.py:36
    tools/mutation_sweep.py:73     tools/mutation_sweep.py:488   tools/mutation_trend.py:28

That is a constant kept in sync by prose, which is the duplication family this repo already
paid for once in the hooks (33 private copies of one stdin read, 20 of them missing a bound).
CLAUDE.md's enforcement-tier rule names the fix: an import is a dependency the interpreter
enforces, and it cannot drift from its source.

THE SECOND REASON, and the one Nick actually hit. The store used to be
`output/analysis/082626-mutation-baseline/`. Under this repo's own output convention an
`MMDDYY-` prefix means a DATED ONE-OFF, so the permanent state store was named like an
August snapshot and sat among 59 other analysis documents that mention mutation. Every
session read it as "that old run" and started its own. Measured consequence on 2026-09-07: a
13-tool re-run was driven by a hand-written shell runner whose results went to a scratchpad
and never reached `baseline.jsonl`, while `mutation_sweep.py` -- which already does exactly
that job, with resume and launchd quiescing -- sat unused three directories away.

So the directory is named for what it is, and it is named HERE, once.

WHAT BELONGS IN THE STORE. Everything a future run needs in order to build on this one:
the target list, the append-only baseline, the survival trend, the quiesce marker. Anything
written anywhere else is, by construction, work the next session will redo.

Stdlib only, no side effects on import: this is imported by a PreToolUse hook.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The canonical store. Deliberately NOT date-prefixed -- see the module docstring.
STATE_DIR = REPO_ROOT / "output" / "mutation-state"

TARGETS_NAME = "targets.json"
BASELINE_NAME = "baseline.jsonl"
TREND_NAME = "survival-trend.jsonl"
QUIESCE_MARKER_NAME = ".quiesced-jobs.json"

#: The allowlist is CODE-adjacent, not state: it is committed, reviewed, and read by
#: mutation_check on every run. It stays in tools/ and is named here only so nothing has to
#: guess at it.
ALLOW_PATH = REPO_ROOT / "tools" / "mutation-allow.json"


def state_dir() -> Path:
    """The store. A function because three tools resolve it at import time.

    Until 2026-10-06 this fell back to the pre-rename directory
    (output/analysis/082626-mutation-baseline/) when the canonical one was absent, with
    helpers to match: a `base` argument on every path function, a `targets_path` and an
    `is_inside_store` that nothing called. The old directory no longer exists and the
    store is gitignored, so there is no checkout the fallback could serve. All of it
    was removed rather than tested: 13 of this file's 18 surviving mutants were in it.
    """
    return STATE_DIR


def baseline_path() -> Path:
    return STATE_DIR / BASELINE_NAME


def trend_path() -> Path:
    return STATE_DIR / TREND_NAME


def quiesce_marker_path() -> Path:
    return STATE_DIR / QUIESCE_MARKER_NAME


# ---------------------------------------------------------------------------------
# READING the store. Mechanism, not policy: every consumer wants the same answer to
# "what rows are in this baseline and what do they total".
#
# These lived in mutation_trend.py while mutation_report.py carried its own inline
# copy -- a second implementation of one domain rule, which is the pattern
# feedback_consolidate_duplicated_domain_logic_and_verify_on_real_data forbids and
# which tools/stage_vocab.py is the exemplar for. The copies had already diverged:
# on 2026-09-08 `latest_per_tool` was added to the trend recorder and NOT to the
# report, so the two would have printed different corpus survival rates from the
# same file the moment any tool was measured twice.

def read_rows(path: Path | None = None) -> list[dict]:
    """Every row in the append-only baseline, in file order."""
    path = path or baseline_path()
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def is_engine_failure(rec: dict) -> bool:
    """Is this banked row an engine failure rather than a measurement?

    NOT "the child exited non-zero". mutation_check exits 2 for GENUINE SURVIVORS,
    which is the tool working and must stay a normal banked result. The failure
    condition is a structured engine error, or any UNAUDITED_* row -- a scan that
    did not happen. Getting this distinction wrong in either direction is costly:
    too broad and every imperfect tool fails the sweep, too narrow and this whole
    guard is decorative.

    Lives here, next to latest_per_tool, since 2026-10-06: the sweep and the health
    watchdog both have to answer "which tools are actually measured", and the watchdog's
    own answer (count the lines) read four tools retried for weeks as more than complete.
    """
    status = str((rec or {}).get("status") or "")
    if status.startswith("UNAUDITED"):
        return True
    return status == "error"


def latest_per_tool(rows: list[dict]) -> list[dict]:
    """One row per tool, LAST wins. baseline.jsonl is append-only.

    A re-run supersedes its predecessor, so counting both double-counts the tool's
    mutants in the corpus denominator. Not hypothetical: the store holds 175 rows for
    123 distinct tools, and merging a 13-tool re-measurement would have double-counted
    1,201 mutants.

    Last-wins is not a judgment about which run was better. If a later row is wrong,
    fix or remove the row; do not teach this function to prefer.
    """
    by_tool: dict[str, dict] = {}
    for r in rows:
        tool = r.get("tool")
        if tool:
            by_tool[tool] = r
    return list(by_tool.values())


def summarise(rows: list[dict]) -> dict:
    """Corpus-level counts from one baseline's rows.

    Only rows with an integer `survived` count. A tool that errored has NO verdict, and
    folding it in as a zero would report an unmeasured tool as a protected one -- the
    exact misreading this whole exercise exists to stop.
    """
    scored = [r for r in rows if isinstance(r.get("survived"), int)
              and isinstance(r.get("mutants"), int) and r["mutants"] > 0]
    mutants = sum(r["mutants"] for r in scored)
    survived = sum(r["survived"] for r in scored)
    return {
        "tools_total": len(rows),
        "tools_scored": len(scored),
        "tools_no_verdict": len(rows) - len(scored),
        "tools_clean": sum(1 for r in scored if r["survived"] == 0),
        "mutants": mutants,
        "survived": survived,
        "survival_pct": round(100 * survived / mutants, 2) if mutants else None,
        # These two were DROPPED when this function was first moved here, because it was
        # retyped from a truncated read instead of moved verbatim -- 8 tests died on
        # KeyError: 'own_suite'. Recovered from git. A tool with no suite of its own gets a
        # survival rate computed from tests written for something else, so "how many rows
        # even have their own suite" is part of reading this store honestly.
        "own_suite": sum(1 for r in rows if r.get("own") is True),
        "own_suite_unknown": sum(1 for r in rows if r.get("own") is None),
    }
