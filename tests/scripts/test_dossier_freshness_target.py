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


# ---------------------------------------------------------------------------
# Direct pins of the pipeline-table reader (added 2026-10-24, before moving it onto
# pipe_read.parse_all_rows). Only cases where this reader and the shared rule agree.
# ---------------------------------------------------------------------------
_PIPE_HEADER = ("| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |\n"
                "|---|---|---|---|---|---|---|---|\n")


def _pipe_doc(*rows, archived=()):
    text = "# Job Pipeline\n\n## Active\n" + _PIPE_HEADER + "".join(r + "\n" for r in rows)
    if archived:
        text += "\n## Archived\n" + _PIPE_HEADER + "".join(r + "\n" for r in archived)
    return text


_SKIPPED_ROWS = (
    "|  | PM | Applied | 2026-02-01 | x | — | — | — |",
    "| Short | PM |",
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import dossier_freshness as _df  # noqa: E402


def test_load_pipeline_targets_slugs_live_companies_only(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-pipeline.md").write_text(_pipe_doc(
        "| Acme Labs | PM | Applied | 2026-02-01 | x | — | — | — |", *_SKIPPED_ROWS,
        "| Beta | CoS | To Evaluate | 2026-02-01 | x | — | — | — |",
        "| Gone | PM | Closed - rejected | 2026-02-01 | — | — | — | — |",
        "| Paused | PM | Deprioritized | 2026-02-01 | — | — | — | — |",
        "| Nope | PM | Declined | 2026-02-01 | — | — | — | — |"), encoding="utf-8")
    assert _df.load_pipeline_targets(tmp_path) == {"acme-labs", "beta"}
    assert _df.load_pipeline_targets(tmp_path / "missing") == set()


def test_load_pipeline_targets_uses_the_shared_rules(tmp_path):
    """BEHAVIOR CHANGE 2026-10-24 (moved onto pipe_read + stage_vocab). Before: a live
    stage under an Archived heading, lowercase 'accepted' and an indented row counted
    as targets, while 'deadline', 'lost' and 'Passed the screen' matched the old
    substring keywords and dropped live leads."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-pipeline.md").write_text(_pipe_doc(
        "| Dl | PM | Applied, deadline Friday | 2026-02-01 | x | — | — | — |",
        "| Lo | PM | Interview, almost lost the slot | 2026-02-01 | x | — | — | — |",
        "| Pa | PM | Passed the screen | 2026-02-01 | x | — | — | — |",
        "| Lower | PM | accepted | 2026-02-01 | — | — | — | — |",
        " | Indented | PM | Applied | 2026-02-01 | x | — | — | — |",
        archived=("| Shelved | PM | Applied | 2026-02-01 | x | — | — | — |",)), encoding="utf-8")
    assert _df.load_pipeline_targets(tmp_path) == {"dl", "lo", "pa"}


def test_an_unreadable_pipeline_gives_no_targets_rather_than_none(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-pipeline.md").write_bytes(b"| Company |\n| \xff\xfe bad bytes |\n")
    assert _df.load_pipeline_targets(tmp_path) == set()
