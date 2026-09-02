"""Tests for friction_log.py `list` reporting its own filter scope.

Regression for a 2026-08-19 audit finding: `--surface` defaults to None and was
truthiness-tested, so `--surface ""` silently skipped the filter and a SCOPED
query returned the entire ledger -- 257 rows where the scoped answer was 1 --
serialized identically to a real 1-row answer.

That matters here specifically because the CLAUDE.md `--debug` ladder counts
fires PER SURFACE (1st logs, 2nd writes a feedback file, 3rd mandates a script
patch). Reading an unfiltered ledger as one surface's history overstates every
rung at once.

Fixed by making the report state its scope (`filters_applied`, `total_rows`),
NOT by rejecting the input: `friction_log list --unpromoted` is prescribed at
docs/usage.md:634, so exit codes and `status` must not move.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "friction_log.py"


def _list(*args):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "list", *args],
        capture_output=True, text=True,
        env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"},
    )
    lines = (r.stdout or "").strip().splitlines()
    return r.returncode, (json.loads(lines[-1]) if lines else {})


def test_unfiltered_list_says_it_is_unfiltered():
    code, out = _list()
    assert out["filters_applied"]["surface"] is None
    assert out["count"] == out["total_rows"], "no filter, so count must equal the ledger size"


def test_empty_surface_is_reported_as_no_filter_not_as_a_scope():
    """The audit reproduction. An empty surface must not masquerade as a scoped query."""
    code, out = _list("--surface", "")
    assert out["filters_applied"]["surface"] is None, \
        "an empty --surface must report as NO filter, never as a filter that ran"
    assert out["count"] == out["total_rows"], "the whole ledger came back"


def test_whitespace_surface_reports_the_filter_that_actually_ran():
    """`"   "` is TRUTHY, so the filter does run and matches nothing. The report must
    say so. Claiming "no filter applied" here would be a report lying about its own
    scope, which is a worse failure than the silent skip this work set out to fix."""
    code, out = _list("--surface", "   ")
    assert out["filters_applied"]["surface"] == "   ", \
        "the report must mirror the filter that actually ran, whitespace and all"
    assert out["count"] == 0
    assert out["total_rows"] > 0, "rows existed; the filter excluded them"


def test_real_surface_reports_the_scope_it_applied():
    code, out = _list("--surface", "gmail_fetch.py")
    assert out["filters_applied"]["surface"] == "gmail_fetch.py"
    assert out["count"] <= out["total_rows"]


def test_scoped_and_unscoped_results_are_distinguishable():
    """The property that actually matters: two results with the same shape must
    not be readable as the same thing when one covered everything."""
    _, scoped = _list("--surface", "gmail_fetch.py")
    _, empty = _list("--surface", "")
    assert scoped["filters_applied"] != empty["filters_applied"], \
        "a scoped query and a scope-of-everything must be tellable apart"
    assert scoped["count"] < empty["count"]


def test_exit_code_and_status_unchanged():
    """Additive-only: docs/usage.md:634 prescribes `list --unpromoted`."""
    code, out = _list("--surface", "")
    assert code == 0
    assert out["status"] == "ok"
    code2, out2 = _list("--unpromoted")
    assert code2 == 0
    assert out2["filters_applied"]["unpromoted"] is True
