"""The target filter in dossier_freshness.py: abandoned lanes must not raise alerts.

Ported 2026-09-02 from tools/test_dossier_freshness_target.py, which pytest never
collected -- a PASS/FAIL-counter script ending in sys.exit(). Split into one test per
behaviour so a failure names the behaviour instead of printing to stdout.

TWO FIXTURE NAMES WERE SCRUBBED IN THE PORT. The original used a real contact's employer
as a fixture company. It sat in a tracked, public file on origin/main, and the
deterministic PII scan called it clean because `gen_pii_denylist.parse_networking_names`
keeps only group(1) of a `### Name — Company` header and discards the company half. Fixture
companies here are drawn from the repo's fictional cast only.

WHAT IT GUARDS (2026-06-02): a stale dossier for a company that is not an active target is
noise -- the lane was abandoned on purpose. Alerting on it trains the reader to skim, which
costs the one alert that mattered. `--all-stale` restores the pre-filter behaviour, and
`older_than_30_days` always lists everyone so the filter never destroys data.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "dossier_freshness.py"

ACTIVE_TARGET = "northwind"        # active pipeline stage -> must alert
TERMINAL_TARGET = "contoso"        # in pipeline but Withdrawn -> suppressed
NOT_IN_PIPELINE = "initech"        # abandoned lane -> suppressed
ALSO_NOT_IN_PIPELINE = "zzz-fixture-only-co"
FRESH = "fresh-co"
STALE = {ACTIVE_TARGET, TERMINAL_TARGET, NOT_IN_PIPELINE, ALSO_NOT_IN_PIPELINE}


def _dossier(root: Path, slug: str, last_updated: str) -> None:
    d = root / "output" / slug
    d.mkdir(parents=True)
    (d / f"{slug}.md").write_text(
        f"# {slug}\n\nLast updated: {last_updated}\n\nbody\n", encoding="utf-8")


@pytest.fixture(scope="module")
def repo() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "data").mkdir()
    (root / "data" / "job-pipeline.md").write_text(
        "| Company | Role | Stage |\n|---|---|---|\n"
        "| Northwind | FDAS | Phone Screen |\n"
        "| Contoso | Ops | Withdrawn |\n", encoding="utf-8")
    _dossier(root, ACTIVE_TARGET, "2026-02-24")
    _dossier(root, TERMINAL_TARGET, "2026-02-24")
    _dossier(root, NOT_IN_PIPELINE, "2026-02-23")
    _dossier(root, ALSO_NOT_IN_PIPELINE, "2026-02-25")
    _dossier(root, FRESH, "2026-06-01")
    return root


def _run(root: Path, all_stale: bool = False) -> dict:
    cmd = [sys.executable, str(SCRIPT), "--repo-root", str(root),
           "--target-date", "2026-06-02"]
    if all_stale:
        cmd.append("--all-stale")
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def report(repo):
    return _run(repo)


@pytest.fixture(scope="module")
def alerts(report):
    return {a["slug"] for a in report["staleness_alerts"]}


def test_a_stale_active_target_alerts(alerts):
    assert ACTIVE_TARGET in alerts


def test_a_terminal_stage_company_is_suppressed(alerts):
    """Withdrawn is a closed lane; a staleness alert on it is pure noise."""
    assert TERMINAL_TARGET not in alerts


@pytest.mark.parametrize("slug", [NOT_IN_PIPELINE, ALSO_NOT_IN_PIPELINE])
def test_a_stale_dossier_outside_the_pipeline_is_suppressed(alerts, slug):
    assert slug not in alerts


def test_a_fresh_dossier_never_alerts(alerts):
    assert FRESH not in alerts


def test_the_suppression_counts_are_reported(report):
    """Suppression must be visible: a filtered row that vanishes is indistinguishable
    from one the scanner lost."""
    assert report["summary"]["suppressed_non_target"] == 3
    assert report["summary"]["stale_target_count"] == 1


def test_the_filter_never_destroys_data(report):
    """older_than_30_days lists every stale dossier regardless of target status, so the
    filter changes what is ALERTED, never what is known."""
    listed = {e["slug"] for e in report["recent_dossiers"]["older_than_30_days"]}
    assert STALE <= listed


def test_all_stale_restores_the_pre_filter_behaviour(repo):
    assert STALE <= {a["slug"] for a in _run(repo, all_stale=True)["staleness_alerts"]}
