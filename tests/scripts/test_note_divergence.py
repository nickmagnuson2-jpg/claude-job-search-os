"""Tests for tools/note_divergence.py.

Written to FAIL if the behaviour breaks, not merely to pass. Each test names the
specific wrong answer it is there to catch.
"""
import os
import sys
import time
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import note_divergence as nd  # noqa: E402


HEADER = "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |"
SEP = "|---|---|---|---|---|---|---|---|"


def build_repo(tmp_path: Path, rows: list[str]) -> Path:
    """Minimal repo: data/job-pipeline.md with a valid header plus the given rows."""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    body = "\n".join(["# Pipeline", "", HEADER, SEP, *rows, ""])
    (data / "job-pipeline.md").write_text(body, encoding="utf-8")
    (tmp_path / "output").mkdir(exist_ok=True)
    return tmp_path


def row(company: str, stage: str = "Applied", updated: str = "2026-09-01") -> str:
    return f"| {company} | Some Role | {stage} | {updated} | next | - | notes | - |"


def make_artifact(repo: Path, slug: str, name: str, mtime_date: date) -> Path:
    d = repo / "output" / slug
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text("x", encoding="utf-8")
    ts = time.mktime(mtime_date.timetuple())
    os.utime(f, (ts, ts))
    return f


# --- return contracts --------------------------------------------------------
# These exist to convert WEAK kills into strong ones. Without them, breaking a
# return statement is caught only because downstream code crashes on None, which
# means a test observed the mutation without any assertion firing. An assertion
# that names the expected type and shape is the difference between "something
# blew up" and "the contract is wrong".

def test_newest_artifact_returns_a_path_and_a_date(tmp_path):
    repo = build_repo(tmp_path, [])
    make_artifact(repo, "acme", "x.md", date(2026, 9, 10))
    result = nd.newest_artifact(repo / "output" / "acme")
    assert isinstance(result, tuple) and len(result) == 2
    path, d = result
    assert isinstance(path, Path)
    assert isinstance(d, date)


def test_scan_returns_the_documented_shape(tmp_path):
    repo = build_repo(tmp_path, [row("Acme AI", updated="2026-09-01")])
    make_artifact(repo, "acme-ai", "x.md", date(2026, 9, 10))
    out = nd.scan(repo, date(2026, 9, 20), min_lag_days=1)
    assert isinstance(out, dict)
    assert set(out) == {"target_date", "min_lag_days", "diverged", "metrics"}
    assert out["target_date"] == "2026-09-20"
    assert out["min_lag_days"] == 1
    assert isinstance(out["diverged"], list)
    assert set(out["metrics"]) == {
        "active_rows", "rows_with_artifacts", "rows_without_artifacts", "diverged_count",
    }


def test_render_returns_a_non_empty_string_in_both_branches(tmp_path):
    """Both the clean branch and the diverged branch must return real text."""
    clean_repo = build_repo(tmp_path / "clean", [row("Acme AI", updated="2026-09-20")])
    make_artifact(clean_repo, "acme-ai", "x.md", date(2026, 9, 10))
    clean = nd.render(nd.scan(clean_repo, date(2026, 9, 20), 1))
    assert isinstance(clean, str) and clean.strip()

    dirty_repo = build_repo(tmp_path / "dirty", [row("Acme AI", updated="2026-09-01")])
    make_artifact(dirty_repo, "acme-ai", "x.md", date(2026, 9, 10))
    dirty = nd.render(nd.scan(dirty_repo, date(2026, 9, 20), 1))
    assert isinstance(dirty, str) and dirty.strip()
    assert clean != dirty


def test_row_whose_artifacts_are_missing_is_excluded_by_assertion(tmp_path):
    """Pins the `artifact is None` guard with an assertion rather than a crash:
    the row must be counted as without-artifacts AND absent from diverged."""
    repo = build_repo(tmp_path, [row("Ghost Co", updated="2026-09-01"),
                                 row("Acme AI", updated="2026-09-01")])
    make_artifact(repo, "acme-ai", "x.md", date(2026, 9, 10))
    out = nd.scan(repo, date(2026, 9, 20), min_lag_days=1)
    assert out["metrics"]["rows_without_artifacts"] == 1
    assert out["metrics"]["rows_with_artifacts"] == 1
    assert [d["company"] for d in out["diverged"]] == ["Acme AI"]


# --- slugify -----------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Acme AI", "acme-ai"),
    ("LiveCo AI", "liveco-ai"),
    ("ActiveCo", "activeco"),
    ("Foo & Bar, Inc.", "foo-bar-inc"),
    ("  Spaced  Out  ", "spaced-out"),
])
def test_slugify_matches_output_convention(name, expected):
    assert nd.slugify(name) == expected


# --- newest_artifact ---------------------------------------------------------

def test_newest_artifact_ignores_ds_store(tmp_path):
    """Catches: reporting divergence because Finder touched the folder.

    .DS_Store is written merely by LOOKING at a directory. If it counted, every
    folder the user opened would read as work.
    """
    repo = build_repo(tmp_path, [])
    make_artifact(repo, "acme", "real.md", date(2026, 9, 1))
    make_artifact(repo, "acme", ".DS_Store", date(2026, 9, 30))
    path, d = nd.newest_artifact(repo / "output" / "acme")
    assert path is not None and path.name == "real.md"
    assert d == date(2026, 9, 1)


def test_newest_artifact_recurses_into_subdirectories(tmp_path):
    """Catches: a flat glob missing output/<slug>/scripts/foo.py."""
    repo = build_repo(tmp_path, [])
    make_artifact(repo, "acme", "old.md", date(2026, 9, 1))
    nested = repo / "output" / "acme" / "scripts"
    nested.mkdir(parents=True)
    f = nested / "new.py"
    f.write_text("x", encoding="utf-8")
    ts = time.mktime(date(2026, 9, 15).timetuple())
    os.utime(f, (ts, ts))
    path, d = nd.newest_artifact(repo / "output" / "acme")
    assert path.name == "new.py"
    assert d == date(2026, 9, 15)


def test_newest_artifact_picks_max_not_last_seen(tmp_path):
    """Catches `if mtime > newest_mtime` degrading to an unconditional assign.

    The names are chosen so alphabetical order is the REVERSE of mtime order: a
    scan that keeps the last file it happens to see returns aaa.md, which sorts
    first but is the oldest. Without this, any always-true comparison passes.
    """
    repo = build_repo(tmp_path, [])
    make_artifact(repo, "acme", "zzz.md", date(2026, 9, 20))   # newest, sorts last
    make_artifact(repo, "acme", "mmm.md", date(2026, 9, 10))
    make_artifact(repo, "acme", "aaa.md", date(2026, 9, 1))    # oldest, sorts first
    path, d = nd.newest_artifact(repo / "output" / "acme")
    assert path.name == "zzz.md"
    assert d == date(2026, 9, 20)


def test_newest_artifact_missing_directory_is_none(tmp_path):
    repo = build_repo(tmp_path, [])
    assert nd.newest_artifact(repo / "output" / "nope") == (None, None)


def test_newest_artifact_empty_directory_is_none(tmp_path):
    repo = build_repo(tmp_path, [])
    (repo / "output" / "empty").mkdir(parents=True)
    assert nd.newest_artifact(repo / "output" / "empty") == (None, None)


# --- scan --------------------------------------------------------------------

def test_flags_row_older_than_its_artifacts(tmp_path):
    """The core case: the row says the 1st, the work happened on the 10th."""
    repo = build_repo(tmp_path, [row("Acme AI", updated="2026-09-01")])
    make_artifact(repo, "acme-ai", "deck.html", date(2026, 9, 10))
    out = nd.scan(repo, date(2026, 9, 20), min_lag_days=1)
    assert out["metrics"]["diverged_count"] == 1
    d = out["diverged"][0]
    assert d["company"] == "Acme AI"
    assert d["lag_days"] == 9
    assert d["artifact_date"] == "2026-09-10"


def test_does_not_flag_row_newer_than_artifacts(tmp_path):
    """Row updated AFTER the work. Must stay silent.

    This is the false positive that would send the user back to finished work,
    which is the whole failure this tool exists to prevent.
    """
    repo = build_repo(tmp_path, [row("ActiveCo", updated="2026-09-20")])
    make_artifact(repo, "activeco", "slides.html", date(2026, 9, 18))
    out = nd.scan(repo, date(2026, 9, 20), min_lag_days=1)
    assert out["metrics"]["diverged_count"] == 0


def test_same_day_edit_is_not_divergence(tmp_path):
    """Catches an off-by-one that would flag every row touched today."""
    repo = build_repo(tmp_path, [row("Acme AI", updated="2026-09-10")])
    make_artifact(repo, "acme-ai", "x.md", date(2026, 9, 10))
    out = nd.scan(repo, date(2026, 9, 20), min_lag_days=1)
    assert out["metrics"]["diverged_count"] == 0


def test_min_lag_days_threshold_is_respected(tmp_path):
    repo = build_repo(tmp_path, [row("Acme AI", updated="2026-09-10")])
    make_artifact(repo, "acme-ai", "x.md", date(2026, 9, 12))
    assert nd.scan(repo, date(2026, 9, 20), 1)["metrics"]["diverged_count"] == 1
    assert nd.scan(repo, date(2026, 9, 20), 2)["metrics"]["diverged_count"] == 1
    assert nd.scan(repo, date(2026, 9, 20), 3)["metrics"]["diverged_count"] == 0


def test_row_with_no_output_directory_is_counted_not_flagged(tmp_path):
    """A row with no artifacts is unknowable, not clean. It must not be silently
    dropped into the same bucket as a verified-clean row."""
    repo = build_repo(tmp_path, [row("Ghost Co", updated="2026-09-01")])
    out = nd.scan(repo, date(2026, 9, 20), min_lag_days=1)
    assert out["metrics"]["diverged_count"] == 0
    assert out["metrics"]["rows_without_artifacts"] == 1
    assert out["metrics"]["rows_with_artifacts"] == 0


def test_unparseable_row_date_is_reported_not_skipped(tmp_path):
    """Catches the silent-skip failure: a row that cannot be compared is
    indistinguishable from a clean row unless it is reported."""
    repo = build_repo(tmp_path, [row("Acme AI", updated="not-a-date")])
    make_artifact(repo, "acme-ai", "x.md", date(2026, 9, 10))
    out = nd.scan(repo, date(2026, 9, 20), min_lag_days=1)
    assert out["metrics"]["diverged_count"] == 1
    d = out["diverged"][0]
    assert d["lag_days"] is None
    assert "unparseable" in d["reason"]


def test_results_sorted_worst_first(tmp_path):
    """Three distinct lags, declared in scrambled order.

    Two rows cannot distinguish a correct sort from a reversed one reliably, and
    an unparseable row must sort LAST rather than first despite lag_days=None.
    """
    repo = build_repo(tmp_path, [
        row("Middle Lag", updated="2026-09-05"),
        row("No Date", updated="garbage"),
        row("Big Lag", updated="2026-09-01"),
        row("Small Lag", updated="2026-09-09"),
    ])
    for slug in ("middle-lag", "no-date", "big-lag", "small-lag"):
        make_artifact(repo, slug, "x.md", date(2026, 9, 10))
    out = nd.scan(repo, date(2026, 9, 20), min_lag_days=1)
    assert [d["company"] for d in out["diverged"]] == [
        "Big Lag", "Middle Lag", "Small Lag", "No Date",
    ]
    assert [d["lag_days"] for d in out["diverged"]] == [9, 5, 1, None]


def test_scan_skips_row_with_empty_company(tmp_path):
    """Catches dropping the `if not slug` guard, which would build output/ and
    scan the entire output tree as if it were one company's directory."""
    repo = build_repo(tmp_path, [row("", updated="2026-09-01"), row("Acme AI")])
    make_artifact(repo, "acme-ai", "x.md", date(2026, 9, 10))
    out = nd.scan(repo, date(2026, 9, 20), min_lag_days=1)
    assert all(d["company"] for d in out["diverged"])


# --- render ------------------------------------------------------------------

def test_render_clean_states_the_denominator(tmp_path):
    """A clean result must say what it checked. 'No divergence' over zero rows
    is not the same claim as 'no divergence' over twelve."""
    repo = build_repo(tmp_path, [row("Acme AI", updated="2026-09-20")])
    make_artifact(repo, "acme-ai", "x.md", date(2026, 9, 10))
    text = nd.render(nd.scan(repo, date(2026, 9, 20), 1))
    assert "1 of 1" in text


def test_render_names_the_sent_folder_caveat(tmp_path):
    """The tool cannot see delivery. If the output stops saying so, a reader will
    treat 'built' as 'sent'."""
    repo = build_repo(tmp_path, [row("Acme AI", updated="2026-09-01")])
    make_artifact(repo, "acme-ai", "x.md", date(2026, 9, 10))
    text = nd.render(nd.scan(repo, date(2026, 9, 20), 1))
    assert "sent folder" in text


def test_render_emits_every_field_a_reader_needs(tmp_path):
    """Catches any of the per-row output lines being dropped.

    A report naming a company but not the artifact or the stage cannot be acted
    on without opening the file, which defeats the point of reporting at all.
    """
    repo = build_repo(tmp_path, [row("Acme AI", stage="Onsite", updated="2026-09-01")])
    make_artifact(repo, "acme-ai", "deck.html", date(2026, 9, 10))
    text = nd.render(nd.scan(repo, date(2026, 9, 20), 1))
    assert "Acme AI" in text
    assert "output/acme-ai/deck.html" in text
    assert "Onsite" in text
    assert "2026-09-01" in text
    assert "2026-09-10" in text
    assert "9d" in text


def test_render_shows_unknown_lag_for_unparseable_date(tmp_path):
    """Catches the ternary inverting: an unparseable row must not render a
    fabricated numeric lag."""
    repo = build_repo(tmp_path, [row("Acme AI", updated="garbage")])
    make_artifact(repo, "acme-ai", "x.md", date(2026, 9, 10))
    text = nd.render(nd.scan(repo, date(2026, 9, 20), 1))
    assert "unknown" in text
