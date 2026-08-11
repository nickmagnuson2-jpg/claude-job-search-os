"""Outreach-target support in scan-targets.yaml.

`scan-targets.yaml` began life as a career-page scan list: every entry had an
`ats` + `slug` and the nightly scanner walked all of them. Lane B discovery
produces companies that are worth *contacting* but have no job board to scan
(seed-stage, no Greenhouse/Lever/Ashby presence).

So the file now holds two kinds of row:
  - scan targets   — have `ats`; the nightly career-scan walks them
  - outreach targets — no `ats`; the scanner must skip them silently

Silently matters: `fetch_company_roles` defaults a missing `ats` to "generic",
finds no `careers_url`, and prints a warning. Without filtering in load_targets
that fires once per outreach target per nightly run, forever.

A third top-level key, `rejected:`, records companies looked at and turned down,
so the weekly Exa collector stops re-proposing them to the inbox.
"""
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from tools.career_scanner.scanner import load_targets  # noqa: E402
from agent_core import load_known_names  # noqa: E402


CONFIG = """\
companies:
  - name: Scannable Co
    ats: greenhouse
    slug: scannable
    active: true
    lane: a
    outreach: false

  - name: Outreach Only Co
    active: true
    lane: lane-b v5
    outreach: true

  - name: Inactive Scan Co
    ats: lever
    slug: inactive
    active: false

rejected:
  - name: Turned Down Co
    date: 2025-01-15
    reason: not AI-native
"""


def _write(tmp_path, text=CONFIG):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "scan-targets.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_outreach_only_target_is_not_scanned(tmp_path):
    """No `ats` means nothing to scan — the nightly run must not pick it up."""
    _write(tmp_path)
    names = [t["name"] for t in load_targets(tmp_path)]
    assert "Outreach Only Co" not in names


def test_scannable_target_is_still_scanned(tmp_path):
    """Control: the filter must not disable scanning outright."""
    _write(tmp_path)
    names = [t["name"] for t in load_targets(tmp_path)]
    assert "Scannable Co" in names


def test_inactive_target_still_excluded(tmp_path):
    """Pre-existing active:false behaviour is unchanged."""
    _write(tmp_path)
    names = [t["name"] for t in load_targets(tmp_path)]
    assert "Inactive Scan Co" not in names


def test_lane_and_outreach_fields_survive_a_roundtrip(tmp_path):
    """`lane` is a free string, `outreach` a boolean — neither is dropped."""
    p = _write(tmp_path)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    by_name = {c["name"]: c for c in data["companies"]}
    assert by_name["Outreach Only Co"]["lane"] == "lane-b v5"
    assert by_name["Outreach Only Co"]["outreach"] is True
    assert by_name["Scannable Co"]["outreach"] is False


def test_outreach_targets_count_as_known_for_dedup(tmp_path):
    """The collector must not re-propose a company already on the target list."""
    p = _write(tmp_path)
    known = load_known_names([str(p)])
    assert any("outreach only co" in k for k in known)


def test_rejected_companies_count_as_known_for_dedup(tmp_path):
    """The whole point of recording rejections: stop the weekly re-proposal.

    A company Nick looked at and turned down is in neither `companies:` nor the
    per-preset seen-set, so without this it returns to the inbox every week.
    """
    p = _write(tmp_path)
    known = load_known_names([str(p)])
    assert any("turned down co" in k for k in known)


def test_missing_rejected_key_is_not_an_error(tmp_path):
    """Graceful degradation: the key is optional."""
    p = _write(tmp_path, "companies:\n  - name: Solo Co\n    ats: lever\n    slug: solo\n")
    known = load_known_names([str(p)])
    assert any("solo co" in k for k in known)
