"""Tests for tools/dossier_freshness.py"""
import textwrap
from pathlib import Path

import pytest

from conftest import run_script


def make_dossier(tmp_path: Path, slug: str, last_updated: str) -> None:
    """Create a minimal dossier file at output/<slug>/<slug>.md"""
    dossier_dir = tmp_path / "output" / slug
    dossier_dir.mkdir(parents=True, exist_ok=True)
    content = f"# {slug.replace('-', ' ').title()}\n\nLast updated: {last_updated}\n\n## Overview\nTest content.\n"
    (dossier_dir / f"{slug}.md").write_text(content, encoding="utf-8")


def make_non_dossier(tmp_path: Path, slug: str, filename: str) -> None:
    """Create a non-dossier file (CV, prep doc, etc.)"""
    out_dir = tmp_path / "output" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / filename).write_text("# CV\nContent.", encoding="utf-8")


def test_no_output_dir(tmp_path):
    """Script handles missing output/ directory gracefully."""
    result = run_script("dossier_freshness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))
    assert result["summary"]["total_dossiers"] == 0
    assert result["staleness_alerts"] == []


def test_fresh_dossier_detected(tmp_path):
    """A dossier updated this week appears in recent_dossiers.this_week."""
    make_dossier(tmp_path, "amae-health", "2026-02-24")

    result = run_script("dossier_freshness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    slugs_this_week = [d["slug"] for d in result["recent_dossiers"]["this_week"]]
    assert "amae-health" in slugs_this_week
    assert result["summary"]["updated_this_week"] == 1


def test_stale_dossier_alert(tmp_path):
    """A dossier older than 30 days appears in staleness_alerts."""
    make_dossier(tmp_path, "stripe", "2025-12-01")

    result = run_script("dossier_freshness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    alert_slugs = [a["slug"] for a in result["staleness_alerts"]]
    assert "stripe" in alert_slugs
    assert result["summary"]["stale_count"] == 1


def test_non_dossier_files_excluded(tmp_path):
    """CV and prep files in output/ are not treated as dossiers."""
    make_dossier(tmp_path, "amae-health", "2026-02-24")
    make_non_dossier(tmp_path, "amae-health", "022426-chief-of-staff.md")
    make_non_dossier(tmp_path, "amae-health", "022426-prep.md")

    result = run_script("dossier_freshness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    # Only amae-health.md should be detected, not the CV or prep files
    assert result["summary"]["total_dossiers"] == 1


def test_multiple_dossiers_mixed_freshness(tmp_path):
    """Mix of fresh and stale dossiers handled correctly."""
    make_dossier(tmp_path, "fresh-co", "2026-02-25")
    make_dossier(tmp_path, "stale-co", "2025-10-01")
    make_dossier(tmp_path, "middle-co", "2026-02-01")

    result = run_script("dossier_freshness.py",
                        "--target-date", "2026-02-26",
                        "--repo-root", str(tmp_path))

    this_week = [d["slug"] for d in result["recent_dossiers"]["this_week"]]
    stale = [d["slug"] for d in result["recent_dossiers"]["older_than_30_days"]]

    assert "fresh-co" in this_week
    assert "stale-co" in stale
    assert "middle-co" not in this_week
    assert "middle-co" not in stale
