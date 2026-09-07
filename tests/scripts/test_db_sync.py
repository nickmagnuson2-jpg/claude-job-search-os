"""Tests for tools/db_sync.py — the markdown -> Postgres derived read model.

No database is touched. sync() is the only function that connects, and every test here
either stops before it or asserts it was NOT reached.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import db_sync  # noqa: E402

HEADER = "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |"
SEP = "| --- | --- | --- | --- | --- | --- | --- | --- |"


def make_pipeline(rows, archived_rows=()):
    lines = ["# Job Application Pipeline", "", "## Active Pipeline", "", HEADER, SEP]
    lines += rows
    if archived_rows:
        lines += ["", "## Archived", "", HEADER, SEP, *archived_rows]
    return "\n".join(lines) + "\n"


def row(company="Acme", role="Analyst", stage="Applied", updated="2026-09-01",
        action="Follow up", cv="—", notes="—", url="—"):
    return f"| {company} | {role} | {stage} | {updated} | {action} | {cv} | {notes} | {url} |"


def write_pipeline(tmp_path, content):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "job-pipeline.md").write_text(content, encoding="utf-8")
    return tmp_path


# --- normalize -------------------------------------------------------------

def test_normalize_converts_emdash_placeholder_to_empty_string():
    out = db_sync.normalize({"role": "—", "stage": "Applied", "next_action": "—",
                             "cv_used": "—", "notes": "—", "url": "—",
                             "date_updated": "2026-09-01"})
    assert out["role"] == ""
    assert out["url"] == ""
    assert out["stage"] == "Applied"


def test_normalize_parses_date_updated_into_a_date_object():
    out = db_sync.normalize({"date_updated": "2026-09-01"})
    assert out["date_updated"] == date(2026, 9, 1)


def test_normalize_maps_unparseable_date_to_none_not_a_crash():
    assert db_sync.normalize({"date_updated": "not-a-date"})["date_updated"] is None
    assert db_sync.normalize({"date_updated": "—"})["date_updated"] is None
    assert db_sync.normalize({"date_updated": ""})["date_updated"] is None


# --- load_rows -------------------------------------------------------------

def test_load_rows_returns_every_row_active_and_archived(tmp_path):
    repo = write_pipeline(tmp_path, make_pipeline(
        [row(company="Live1"), row(company="Live2")],
        [row(company="Dead1", stage="Rejected")],
    ))
    rows = db_sync.load_rows(repo, date(2026, 9, 6))
    assert len(rows) == 3
    assert sum(r["archived"] for r in rows) == 1
    assert {r["company"] for r in rows} == {"Live1", "Live2", "Dead1"}


def test_load_rows_marks_rows_under_archived_heading_as_archived(tmp_path):
    # Stage text alone says nothing here; the heading is what makes it archived.
    repo = write_pipeline(tmp_path, make_pipeline(
        [row(company="Live")],
        [row(company="Old", stage="Applied")],
    ))
    rows = {r["company"]: r for r in db_sync.load_rows(repo, date(2026, 9, 6))}
    assert rows["Old"]["archived"] is True
    assert rows["Live"]["archived"] is False


def test_load_rows_raises_when_pipeline_file_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        db_sync.load_rows(tmp_path, date(2026, 9, 6))


def test_archived_rows_are_never_marked_stale_or_needing_attention(tmp_path):
    repo = write_pipeline(tmp_path, make_pipeline(
        [row(company="Live")],
        [row(company="Ancient", stage="Applied", updated="2020-01-01", action="—")],
    ))
    old = next(r for r in db_sync.load_rows(repo, date(2026, 9, 6)) if r["company"] == "Ancient")
    assert old["stale"] is False
    assert old["missing_action"] is False
    assert old["needs_attention"] is False


def test_active_row_past_its_threshold_is_stale(tmp_path):
    repo = write_pipeline(tmp_path, make_pipeline(
        [row(company="Stalled", stage="Applied", updated="2026-08-01")]))
    r = db_sync.load_rows(repo, date(2026, 9, 6))[0]
    assert r["stale"] is True
    assert r["days_since_update"] == 36


# --- the min-rows guard ----------------------------------------------------

def test_main_refuses_to_sync_when_pipeline_is_empty(tmp_path, capsys):
    repo = write_pipeline(tmp_path, make_pipeline([]))
    code = db_sync.main(["--repo-root", str(repo)])
    assert code == 2
    assert "REFUSED" in capsys.readouterr().err


def test_main_refuses_below_min_rows_threshold(tmp_path, capsys):
    repo = write_pipeline(tmp_path, make_pipeline([row(company="OnlyOne")]))
    code = db_sync.main(["--repo-root", str(repo), "--min-rows", "5"])
    assert code == 2
    assert "below --min-rows=5" in capsys.readouterr().err


def test_refusal_happens_before_any_connection_attempt(tmp_path, monkeypatch):
    """The guard must fire before sync() is reached, not after."""
    repo = write_pipeline(tmp_path, make_pipeline([row(company="OnlyOne")]))
    monkeypatch.setenv(db_sync.ENV_VAR, "postgres://should-never-be-used/db")
    called = []
    monkeypatch.setattr(db_sync, "sync", lambda *a, **k: called.append(a))
    assert db_sync.main(["--repo-root", str(repo), "--min-rows", "5"]) == 2
    assert called == [], "sync() was called despite the row-count refusal"


def test_sync_proceeds_when_row_count_meets_threshold(tmp_path, monkeypatch):
    rows = [row(company=f"C{i}") for i in range(6)]
    repo = write_pipeline(tmp_path, make_pipeline(rows))
    monkeypatch.setenv(db_sync.ENV_VAR, "postgres://fake/db")
    seen = {}
    monkeypatch.setattr(db_sync, "sync", lambda r, dsn, src: seen.update(n=len(r), dsn=dsn) or len(r))
    assert db_sync.main(["--repo-root", str(repo), "--min-rows", "5"]) == 0
    assert seen["n"] == 6


# --- dry run and missing credentials ---------------------------------------

def test_dry_run_never_connects_even_with_a_dsn_present(tmp_path, monkeypatch, capsys):
    rows = [row(company=f"C{i}") for i in range(6)]
    repo = write_pipeline(tmp_path, make_pipeline(rows))
    monkeypatch.setenv(db_sync.ENV_VAR, "postgres://should-never-be-used/db")
    monkeypatch.setattr(db_sync, "sync", lambda *a, **k: pytest.fail("dry-run connected"))
    assert db_sync.main(["--repo-root", str(repo), "--dry-run"]) == 0
    assert '"row_count": 6' in capsys.readouterr().out


def test_missing_dsn_is_an_error_not_a_silent_noop(tmp_path, monkeypatch, capsys):
    rows = [row(company=f"C{i}") for i in range(6)]
    repo = write_pipeline(tmp_path, make_pipeline(rows))
    monkeypatch.delenv(db_sync.ENV_VAR, raising=False)
    assert db_sync.main(["--repo-root", str(repo)]) == 1
    assert db_sync.ENV_VAR in capsys.readouterr().err


def test_sync_failure_surfaces_as_exit_1_not_a_crash(tmp_path, monkeypatch, capsys):
    rows = [row(company=f"C{i}") for i in range(6)]
    repo = write_pipeline(tmp_path, make_pipeline(rows))
    monkeypatch.setenv(db_sync.ENV_VAR, "postgres://fake/db")

    def boom(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(db_sync, "sync", boom)
    assert db_sync.main(["--repo-root", str(repo)]) == 1
    assert "no route to host" in capsys.readouterr().err


# --- sync(): the destructive path, against a fake driver -------------------

class FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append(("execute", " ".join(sql.split())[:60], params))

    def executemany(self, sql, rows):
        self.log.append(("executemany", " ".join(sql.split())[:30], list(rows)))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.log.append(("commit", None, None))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_psycopg(monkeypatch):
    """Install a fake `psycopg` module so sync() runs its real body with no database."""
    import types
    log = []
    mod = types.ModuleType("psycopg")
    mod.connect = lambda dsn: FakeConn(log)
    monkeypatch.setitem(sys.modules, "psycopg", mod)
    return log


def test_sync_creates_schema_clears_table_and_inserts_in_that_order(fake_psycopg):
    rows = [db_sync.normalize({"company": "A", "date_updated": "2026-09-01"})]
    db_sync.sync(rows, "postgres://fake/db", "data/job-pipeline.md")
    ops = [(kind, sql) for kind, sql, _ in fake_psycopg if kind != "commit"]
    assert "CREATE TABLE" in ops[0][1]
    assert "DELETE FROM pipeline" in ops[1][1], "the table must be cleared before insert"
    assert ops[2][0] == "executemany", "rows must be inserted after the delete"


def test_sync_inserts_every_row_it_was_given(fake_psycopg):
    rows = [db_sync.normalize({"company": f"C{i}", "date_updated": "2026-09-01"})
            for i in range(7)]
    db_sync.sync(rows, "postgres://fake/db", "data/job-pipeline.md")
    inserted = next(r for k, _, r in fake_psycopg if k == "executemany")
    assert len(inserted) == 7
    assert {r["company"] for r in inserted} == {f"C{i}" for i in range(7)}


def test_sync_commits_the_transaction(fake_psycopg):
    db_sync.sync([db_sync.normalize({"company": "A", "date_updated": "2026-09-01"})],
                 "postgres://fake/db", "src")
    assert ("commit", None, None) in fake_psycopg, "sync() never committed"


def test_sync_records_the_row_count_in_sync_meta(fake_psycopg):
    rows = [db_sync.normalize({"company": f"C{i}", "date_updated": "2026-09-01"})
            for i in range(4)]
    db_sync.sync(rows, "postgres://fake/db", "data/job-pipeline.md")
    meta = next(p for k, sql, p in fake_psycopg if k == "execute" and "sync_meta" in sql)
    assert meta[1] == 4
    assert meta[2] == "data/job-pipeline.md"


def test_sync_returns_the_number_of_rows_written(fake_psycopg):
    rows = [db_sync.normalize({"company": f"C{i}", "date_updated": "2026-09-01"})
            for i in range(3)]
    assert db_sync.sync(rows, "postgres://fake/db", "src") == 3


def test_main_reports_missing_pipeline_file_as_exit_1(tmp_path, capsys):
    (tmp_path / "data").mkdir()
    assert db_sync.main(["--repo-root", str(tmp_path)]) == 1
    assert "ERROR" in capsys.readouterr().err


def test_main_reports_the_synced_counts_on_stdout(tmp_path, monkeypatch, capsys):
    """stdout is how a launchd run reports what happened; a silent success is a bug.
    Kills the DROP_CALL mutant on the summary print rather than allowlisting it."""
    rows = [row(company=f"C{i}") for i in range(6)]
    repo = write_pipeline(tmp_path, make_pipeline(rows, [row(company="Dead", stage="Rejected")]))
    monkeypatch.setenv(db_sync.ENV_VAR, "postgres://fake/db")
    monkeypatch.setattr(db_sync, "sync", lambda r, dsn, src: len(r))
    assert db_sync.main(["--repo-root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Synced 7 rows" in out
    assert "6 active" in out
    assert "1 archived" in out
