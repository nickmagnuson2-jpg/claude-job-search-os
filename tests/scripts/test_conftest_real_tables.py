"""The autouse guard in tests/conftest.py that fails a test which changes a real table."""
import importlib.util
from pathlib import Path

CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
spec = importlib.util.spec_from_file_location("conftest_under_test", CONFTEST)
conftest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conftest)


def test_an_unchanged_table_is_not_reported():
    fp = {"job-todos.md": (1, 10)}
    assert conftest._changed_tables(fp, dict(fp)) == []


def test_a_modified_table_is_reported():
    assert conftest._changed_tables({"job-todos.md": (1, 10)},
                                    {"job-todos.md": (2, 11)}) == ["job-todos.md"]


def test_a_deleted_table_is_reported():
    # 647.F1: the comparison ran over the AFTER keys only, so a deleted table passed.
    assert conftest._changed_tables({"networking.md": (1, 10)}, {}) == ["networking.md"]


def test_a_created_table_is_reported():
    assert conftest._changed_tables({}, {"outreach-log.md": (1, 10)}) == ["outreach-log.md"]


def test_the_fingerprint_drops_a_missing_table(tmp_path, monkeypatch):
    # The deletion case reaches _changed_tables only if a missing file is left out.
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-todos.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(conftest, "REPO_ROOT", tmp_path)
    assert set(conftest._fingerprints()) == {"job-todos.md"}
