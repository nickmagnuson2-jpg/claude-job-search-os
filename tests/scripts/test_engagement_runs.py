"""Tests for tools/engagement_runs.py.

The number this emits replaces a sentence that went stale inside a week. So the failures that
matter are the ones that would make it go quietly wrong again: a run that stops being counted,
a broken frame that shrinks the count instead of announcing itself, or an empty scan that
reports zero as if zero were an answer.

Run alone as well as in the suite, per the --isolation half of mutation_check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from engagement_runs import find_frames, main, read_run, render, scan  # noqa: E402

yaml = pytest.importorskip("yaml")


def engagement(root: Path, slug: str, version=None, locked=False, raw=None):
    d = root / slug
    d.mkdir(parents=True)
    f = d / "frame.yaml"
    if raw is not None:
        f.write_text(raw, encoding="utf-8")
    else:
        f.write_text(yaml.safe_dump({"version": version, "locked": locked}), encoding="utf-8")
    return f


# --- finding runs ---------------------------------------------------------------------


def test_each_engagement_directory_counts_once(tmp_path):
    engagement(tmp_path, "alpha", 4)
    engagement(tmp_path, "beta", 53)
    assert scan(tmp_path)["count"] == 2


def test_a_frame_snapshot_is_a_version_not_a_run(tmp_path):
    """output/<slug>/frame.v12.yaml is a version of one run. Counting snapshots would make
    a single long engagement look like fifty."""
    engagement(tmp_path, "alpha", 4)
    (tmp_path / "alpha" / "frame.v1.yaml").write_text("version: 1", encoding="utf-8")
    (tmp_path / "alpha" / "frame.v2.yaml").write_text("version: 2", encoding="utf-8")
    assert scan(tmp_path)["count"] == 1


def test_a_directory_with_no_frame_is_not_an_engagement(tmp_path):
    engagement(tmp_path, "alpha", 4)
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "readme.md").write_text("x", encoding="utf-8")
    assert scan(tmp_path)["count"] == 1


def test_a_nested_frame_is_not_a_second_run(tmp_path):
    """The glob is one level deep on purpose: a fixture frame inside a run is not a run."""
    engagement(tmp_path, "alpha", 4)
    deep = tmp_path / "alpha" / "fixtures"
    deep.mkdir()
    (deep / "frame.yaml").write_text("version: 1", encoding="utf-8")
    assert scan(tmp_path)["count"] == 1


def test_runs_are_sorted_by_slug_so_the_output_is_stable(tmp_path):
    engagement(tmp_path, "zeta", 1)
    engagement(tmp_path, "alpha", 2)
    assert [p.parent.name for p in find_frames(tmp_path)] == ["alpha", "zeta"]


# --- what each run reports ------------------------------------------------------------


def test_a_run_reports_its_slug_and_frame_version(tmp_path):
    engagement(tmp_path, "alpha", 53)
    run, = scan(tmp_path)["runs"]
    assert run["slug"] == "alpha" and run["version"] == 53


def test_locked_is_reported_and_defaults_to_false(tmp_path):
    engagement(tmp_path, "open_one", 4)
    engagement(tmp_path, "shut_one", 9, locked=True)
    got = {r["slug"]: r["locked"] for r in scan(tmp_path)["runs"]}
    assert got == {"open_one": False, "shut_one": True}


def test_an_unparseable_frame_is_COUNTED_and_flagged_not_dropped(tmp_path):
    """Dropping it shrinks the count silently, which is the exact failure this replaces."""
    engagement(tmp_path, "alpha", 4)
    engagement(tmp_path, "broken", raw="version: [unclosed\n  nope")
    result = scan(tmp_path)
    assert result["count"] == 2
    broken, = [r for r in result["runs"] if r["slug"] == "broken"]
    assert broken["version"] is None and "error" in broken


def test_a_frame_with_no_version_key_reports_none_rather_than_zero(tmp_path):
    """Zero is a number a reader will compare against; None is visibly absent."""
    engagement(tmp_path, "alpha", raw="locked: false")
    run, = scan(tmp_path)["runs"]
    assert run["version"] is None


def test_an_empty_frame_file_does_not_crash(tmp_path):
    engagement(tmp_path, "alpha", raw="")
    assert scan(tmp_path)["count"] == 1


# --- the empty case, which must not be a cheerful zero --------------------------------


def test_no_engagements_raises_rather_than_reporting_zero(tmp_path):
    with pytest.raises(ValueError, match="no engagement frames"):
        scan(tmp_path)


def test_the_empty_error_names_the_path_it_looked_in(tmp_path):
    """A wrong path and a never-run method must not produce the same message."""
    with pytest.raises(ValueError, match=str(tmp_path)):
        scan(tmp_path)


def test_cli_exits_two_on_an_empty_scan(tmp_path, capsys):
    assert main(["--output-dir", str(tmp_path)]) == 2
    assert "no engagement frames" in capsys.readouterr().err


# --- the registry contract ------------------------------------------------------------


def test_value_mirrors_count_so_the_registry_default_field_works(tmp_path):
    """The workstream probe lifts `value` unless told otherwise. If these ever disagree the
    registry silently reports a different number than the CLI."""
    engagement(tmp_path, "alpha", 4)
    engagement(tmp_path, "beta", 53)
    r = scan(tmp_path)
    assert r["value"] == r["count"] == 2


def test_cli_json_is_parseable_and_carries_the_field_the_probe_lifts(tmp_path, capsys):
    engagement(tmp_path, "alpha", 4)
    assert main(["--output-dir", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["value"] == 1


# --- rendering ------------------------------------------------------------------------


def test_render_lists_the_furthest_run_first(tmp_path):
    engagement(tmp_path, "small", 4)
    engagement(tmp_path, "big", 53)
    out = render(scan(tmp_path))
    assert out.index("big") < out.index("small")


def test_render_states_the_count(tmp_path):
    engagement(tmp_path, "alpha", 4)
    assert "runs of the analysis method: 1" in render(scan(tmp_path))


def test_render_marks_locked_and_open(tmp_path):
    engagement(tmp_path, "shut_one", 9, locked=True)
    engagement(tmp_path, "open_one", 4)
    out = render(scan(tmp_path))
    assert "locked" in out and "open" in out


def test_render_does_not_print_a_version_number_for_an_unparseable_frame(tmp_path):
    engagement(tmp_path, "broken", raw="version: [unclosed")
    assert "UNPARSEABLE" in render(scan(tmp_path))


def test_read_run_handles_a_missing_file_without_raising(tmp_path):
    got = read_run(tmp_path / "gone" / "frame.yaml")
    assert got["version"] is None and "error" in got
