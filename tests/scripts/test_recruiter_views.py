"""Tests for tools/recruiter_views.py.

The properties under test: a relative LinkedIn age becomes the right calendar date; the
company dimension's ruling decides agency vs in-house before the industry line does;
re-ingesting the same list from a later capture adds nothing; a bad row aborts the whole
ingest before anything is written; set-status touches only the latest view of that company;
and the Records column reports the files that mention the company.

All fixtures use invented names. This file is public.
"""
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import company_dim  # noqa: E402
import recruiter_views as rv  # noqa: E402

SCRIPT = REPO_ROOT / "tools" / "recruiter_views.py"
CAPTURED = datetime(2026, 1, 15, 14, 5)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "data" / "company-notes").mkdir(parents=True)
    (tmp_path / "data" / "networking.md").write_text(
        "| Jordan Lee | Globex | recruiter |\n", encoding="utf-8")
    (tmp_path / "data" / "job-pipeline.md").write_text(
        "| Initech | Ops Lead | Rejected |\n", encoding="utf-8")
    ledger = tmp_path / "data" / "recruiter-views.md"
    monkeypatch.setattr(rv, "LEDGER", ledger)
    return tmp_path


def dim(entries):
    return company_dim.Dimension(entries)


@pytest.mark.parametrize("age,expected", [
    ("45m", date(2026, 1, 15)),
    ("14h", date(2026, 1, 15)),
    ("15h", date(2026, 1, 14)),   # crosses midnight
    ("11d", date(2026, 1, 4)),
    ("2w", date(2026, 1, 1)),
    ("1mo", date(2025, 12, 16)),
    ("3d ago", date(2026, 1, 12)),
])
def test_viewed_on(age, expected):
    assert rv.viewed_on(age, CAPTURED) == expected


def test_viewed_on_rejects_garbage():
    with pytest.raises(ValueError):
        rv.viewed_on("yesterday", CAPTURED)


def test_classify_dimension_beats_industry():
    d = dim([{"canonical": "Acme", "type": "target"},
             {"canonical": "Hooli", "type": "recruiter"}])
    # dimension says target even though LinkedIn calls it a staffing firm
    assert rv.classify("Acme", "Staffing and Recruiting", d) == ("in-house", "dim")
    assert rv.classify("Hooli", "Software Development", d) == ("agency", "dim")


def test_classify_falls_back_to_industry():
    d = dim([{"canonical": "Acme", "type": "REVIEW"}])
    assert rv.classify("Acme", "Staffing and Recruiting", d) == ("agency", "industry")
    assert rv.classify("Umbrella", "Software Development", d) == ("in-house", "industry")
    assert rv.classify("Umbrella", "", None) == ("in-house", "industry")


def test_ingest_writes_rows_and_records(repo):
    out = rv.ingest(["Globex|Staffing and Recruiting|1h", "Initech|Software Development|11d",
                     "Umbrella||2w"], CAPTURED, repo, None)
    assert len(out["added"]) == 3
    rows = {r["Company"]: r for r in rv.read_rows()}
    assert rows["Globex"]["Kind"] == "agency (industry)"
    assert rows["Globex"]["Records"] == "networking"
    assert rows["Initech"]["Records"] == "pipeline"
    assert rows["Umbrella"]["Records"] == "none"
    assert rows["Umbrella"]["Industry"] == "-"
    assert all(r["Status"] == "new" for r in rows.values())
    # newest first on disk
    assert [r["Company"] for r in rv.read_rows()] == ["Globex", "Initech", "Umbrella"]


def test_reingest_from_later_capture_adds_nothing(repo):
    rv.ingest(["Globex|Staffing and Recruiting|11d"], CAPTURED, repo, None)
    # the next day LinkedIn shows the same view as 12d ago
    later = datetime(2026, 1, 16, 9, 0)
    out = rv.ingest(["Globex|Staffing and Recruiting|12d"], later, repo, None)
    assert out["added"] == [] and len(out["skipped_existing"]) == 1
    assert len(rv.read_rows()) == 1


def test_new_view_from_same_company_is_a_new_row(repo):
    rv.ingest(["Globex|Staffing and Recruiting|11d"], CAPTURED, repo, None)
    out = rv.ingest(["Globex|Staffing and Recruiting|1h"], CAPTURED, repo, None)
    assert len(out["added"]) == 1
    assert len(rv.read_rows()) == 2


def test_bad_row_writes_nothing(repo):
    with pytest.raises(ValueError):
        rv.ingest(["Globex|Staffing and Recruiting|1h", "Initech|bad"], CAPTURED, repo, None)
    assert not rv.LEDGER.exists()


def test_set_status_updates_latest_view_only(repo):
    rv.ingest(["Globex||11d", "Globex||1h"], CAPTURED, repo, None)
    out = rv.set_status("globex", "no-jobs", "View jobs empty")
    assert out["updated"]["Viewed"] == "2026-01-15"
    by_date = {r["Viewed"]: r for r in rv.read_rows()}
    assert by_date["2026-01-15"]["Status"] == "no-jobs"
    assert by_date["2026-01-15"]["Note"] == "View jobs empty"
    assert by_date["2026-01-04"]["Status"] == "new"


def test_set_status_unknown_company(repo):
    assert rv.set_status("Nobody", "skip", None)["status"] == "not_found"


def test_cli_roundtrip(tmp_path):
    ledger = tmp_path / "views.md"
    env = {"RECRUITER_VIEWS_LEDGER": str(ledger), "PYTHONIOENCODING": "utf-8",
           "PATH": "/usr/bin:/bin"}
    run = lambda *a: subprocess.run([sys.executable, str(SCRIPT), "--repo-root", str(tmp_path), *a],
                                    capture_output=True, text=True, env=env)
    r = run("ingest", "--captured-at", "2026-01-15 14:05", "--row", "Globex|Staffing and Recruiting|1h")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["added"][0]["Viewed"] == "2026-01-15"
    r = run("list", "--status", "new")
    assert json.loads(r.stdout)["count"] == 1
    assert run("set-status", "Nobody", "skip").returncode == 1
    assert run("ingest", "--captured-at", "2026-01-15 14:05", "--row", "x").returncode == 2


def test_different_company_same_day_is_not_a_duplicate(repo):
    # the dedup key is company AND date; a date match alone must not swallow another firm
    rv.ingest(["Globex||1h"], CAPTURED, repo, None)
    out = rv.ingest(["Initech||2h"], CAPTURED, repo, None)
    assert [r["Company"] for r in out["added"]] == ["Initech"]
    assert out["skipped_existing"] == []


def test_records_labels_each_source(repo):
    (repo / "data" / "company-notes" / "hooli.md").write_text("Hooli notes\n", encoding="utf-8")
    (repo / "data" / "people").mkdir()
    (repo / "data" / "people" / "pat-doe.md").write_text("Pat works at Vandelay\n", encoding="utf-8")
    (repo / "data" / "inbox.md").write_text("- ping Massive Dynamic\n", encoding="utf-8")
    (repo / "data" / "job-todos.md").write_text("- call Cyberdyne\n", encoding="utf-8")
    (repo / "output" / "soylent-co").mkdir(parents=True)
    (repo / "output" / "soylent-co" / "soylent-co.md").write_text("Soylent Co dossier\n",
                                                                 encoding="utf-8")
    for slug in ("wonka", "tyrell"):
        (repo / "output" / slug).mkdir()
        (repo / "output" / slug / f"{slug}.md").write_text("competes with Hooli\n",
                                                           encoding="utf-8")
    assert rv.records_for("Hooli", repo) == "company-notes, named in 2 other dossier(s)"
    assert rv.records_for("Vandelay", repo) == "people"
    assert rv.records_for("Massive Dynamic", repo) == "inbox"
    assert rv.records_for("Cyberdyne", repo) == "todos"
    # its own dossier is "dossier", not counted as a mention
    assert rv.records_for("Soylent Co", repo) == "dossier"
    assert rv.records_for("AB", repo) == "n/a (name too short)"


def test_malformed_ledger_row_is_refused(repo):
    rv.LEDGER.write_text(rv.PREAMBLE + rv.HEADER + "\n| 2026-01-01 | Globex | x |\n",
                         encoding="utf-8")
    with pytest.raises(ValueError):
        rv.read_rows()


def test_set_status_without_note_keeps_existing_note(repo):
    rv.ingest(["Globex||1h"], CAPTURED, repo, None)
    rv.set_status("Globex", "no-jobs", "first note")
    rv.set_status("Globex", "pitched", None)
    row = rv.read_rows()[0]
    assert (row["Status"], row["Note"]) == ("pitched", "first note")


def test_cli_status_filter_note_and_error_json(tmp_path):
    ledger = tmp_path / "nested" / "views.md"  # parent does not exist yet
    env = {"RECRUITER_VIEWS_LEDGER": str(ledger), "PYTHONIOENCODING": "utf-8",
           "PATH": "/usr/bin:/bin"}
    run = lambda *a: subprocess.run([sys.executable, str(SCRIPT), "--repo-root", str(tmp_path), *a],
                                    capture_output=True, text=True, env=env)
    assert run("ingest", "--captured-at", "2026-01-15 14:05",
               "--row", "Globex||1h", "--row", "Initech||3d").returncode == 0
    assert run("set-status", "Globex", "no-jobs", "--note", "empty board").returncode == 0
    listed = json.loads(run("list", "--status", "new").stdout)
    assert [r["Company"] for r in listed["rows"]] == ["Initech"]
    assert json.loads(run("list").stdout)["count"] == 2
    assert "empty board" in ledger.read_text(encoding="utf-8")
    bad = run("ingest", "--captured-at", "2026-01-15 14:05", "--row", "Globex||soon")
    assert json.loads(bad.stdout)["status"] == "error"


def test_load_dim_reads_missing_and_refuses_malformed(tmp_path):
    yml = tmp_path / "companies.yaml"
    yml.write_text("companies:\n- canonical: Hooli\n  type: recruiter\n", encoding="utf-8")
    assert rv.classify("Hooli", "Software Development", rv._load_dim(yml)) == ("agency", "dim")
    assert rv._load_dim(tmp_path / "missing.yaml") is None   # a fresh clone has no dimension
    bad = tmp_path / "bad.yaml"
    bad.write_text("companies:\n- canonical: Hooli\n  type: nonsense\n", encoding="utf-8")
    with pytest.raises(ValueError):   # a broken file must not quietly disable every ruling
        rv._load_dim(bad)


def test_load_dim_default_path_is_read_at_call_time(tmp_path, monkeypatch):
    yml = tmp_path / "companies.yaml"
    yml.write_text("companies:\n- canonical: Hooli\n  type: recruiter\n", encoding="utf-8")
    monkeypatch.setattr(company_dim, "DEFAULT_PATH", yml)
    assert rv._load_dim().type_of("Hooli") == "recruiter"


def test_reclassify_applies_a_later_dimension_ruling(repo):
    # LinkedIn files some staffing firms under a consulting industry; a human ruling fixes it
    rv.ingest(["Hooli|Business Consulting and Services|1h", "Initech|Staffing and Recruiting|2d"],
              CAPTURED, repo, None)
    rv.set_status("Hooli", "no-jobs", "kept")
    out = rv.reclassify(dim([{"canonical": "Hooli", "type": "recruiter"}]))
    assert out["changed"] == [{"company": "Hooli", "viewed": "2026-01-15",
                               "from": "in-house (industry)", "to": "agency (dim)"}]
    rows = {r["Company"]: r for r in rv.read_rows()}
    assert rows["Hooli"]["Kind"] == "agency (dim)"
    assert rows["Hooli"]["Status"] == "no-jobs"          # the rest of the row is untouched
    assert rows["Initech"]["Kind"] == "agency (industry)"  # industry still read on a '-'-free row
    assert rv.reclassify(dim([{"canonical": "Hooli", "type": "recruiter"}]))["changed"] == []


def test_reclassify_without_a_dimension_refuses_and_keeps_rulings(repo):
    rv.ingest(["Hooli|Business Consulting and Services|1h"], CAPTURED, repo,
              dim([{"canonical": "Hooli", "type": "recruiter"}]))
    before = rv.LEDGER.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        rv.reclassify(None)
    assert rv.LEDGER.read_text(encoding="utf-8") == before


def test_reclassify_reads_missing_industry_as_empty(repo):
    rv.ingest(["Umbrella||1h"], CAPTURED, repo, None)
    assert rv.reclassify(dim([]))["changed"] == []


def _cli(tmp_path):
    env = {"RECRUITER_VIEWS_LEDGER": str(tmp_path / "views.md"), "PYTHONIOENCODING": "utf-8",
           "PATH": "/usr/bin:/bin"}
    return lambda *a, **kw: subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(tmp_path), *a],
        capture_output=True, text=True, env=env, **kw)


def test_cli_reclassify(tmp_path):
    run = _cli(tmp_path)
    yml = tmp_path / "companies.yaml"
    yml.write_text("companies:\n- canonical: Globex\n  type: target\n", encoding="utf-8")
    run("ingest", "--captured-at", "2026-01-15 14:05", "--row", "Globex|Staffing and Recruiting|1h")
    r = run("--companies", str(yml), "reclassify")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["changed"][0]["to"] == "in-house (dim)"
    missing = run("--companies", str(tmp_path / "none.yaml"), "reclassify")
    assert missing.returncode == 2 and json.loads(missing.stdout)["status"] == "error"


def test_cli_ingest_uses_the_named_dimension(tmp_path):
    run = _cli(tmp_path)
    yml = tmp_path / "companies.yaml"
    yml.write_text("companies:\n- canonical: Hooli\n  type: recruiter\n", encoding="utf-8")
    r = run("--companies", str(yml), "ingest", "--captured-at", "2026-01-15 14:05",
            "--row", "Hooli|Software Development|1h")
    assert json.loads(r.stdout)["added"][0]["Kind"] == "agency (dim)"


def test_repeat_view_next_day_is_a_new_row(repo):
    # LinkedIn keeps one row per company; a second look the next day shows as "2h"
    rv.ingest(["Globex||1h"], CAPTURED, repo, None)
    next_day = datetime(2026, 1, 16, 10, 0)
    out = rv.ingest(["Globex||2h"], next_day, repo, None)
    assert len(out["added"]) == 1
    # but the same first view, now shown as "1d", is still recognized
    assert rv.ingest(["Globex||1d"], next_day, repo, None)["added"] == []


def test_week_old_view_seen_again_later_is_not_duplicated(repo):
    rv.ingest(["Globex||2w"], CAPTURED, repo, None)
    later = datetime(2026, 1, 20, 9, 0)   # LinkedIn still shows "2w" five days on
    assert rv.ingest(["Globex||2w"], later, repo, None)["added"] == []


def test_bad_age_aborts_before_any_write(repo):
    with pytest.raises(ValueError):
        rv.ingest(["Globex||1h", "Initech||soon"], CAPTURED, repo, None)
    assert not rv.LEDGER.exists()


def test_writers_wait_for_the_ledger_lock(tmp_path):
    import fcntl
    run = _cli(tmp_path)
    with open(tmp_path / ".recruiter-views.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)   # another writer holds the ledger
        for args in (("ingest", "--captured-at", "2026-01-15 14:05", "--row", "Globex||1h"),
                     ("set-status", "Globex", "skip")):
            with pytest.raises(subprocess.TimeoutExpired):
                run(*args, timeout=1.5)
    assert run("ingest", "--captured-at", "2026-01-15 14:05", "--row", "Globex||1h").returncode == 0
