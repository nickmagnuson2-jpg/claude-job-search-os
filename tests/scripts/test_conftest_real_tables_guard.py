"""The conftest guard that fails a test which writes the owner's real data tables.

Run in a throwaway repo holding a copy of tests/conftest.py and a fake data/ folder,
so the control never touches real data.
"""
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _repo(tmp_path, test_body):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "data").mkdir()
    shutil.copy(REPO_ROOT / "tests" / "conftest.py", tmp_path / "tests" / "conftest.py")
    shutil.copy(REPO_ROOT / "tools" / "conftest_guard.py", tmp_path / "tools" / "conftest_guard.py")
    (tmp_path / "data" / "job-todos.md").write_text("# To-dos\n", encoding="utf-8")
    (tmp_path / "tests" / "test_inner.py").write_text(test_body, encoding="utf-8")
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           str(tmp_path / "tests" / "test_inner.py")],
                          cwd=tmp_path, capture_output=True, text=True)


def test_a_test_that_appends_to_a_real_table_fails_by_name(tmp_path):
    r = _repo(tmp_path, (
        "from pathlib import Path\n"
        "def test_writes():\n"
        "    p = Path(__file__).resolve().parents[1] / 'data' / 'job-todos.md'\n"
        "    p.write_text(p.read_text() + '| row |\\n')\n"))
    assert r.returncode != 0, r.stdout
    assert "test_writes changed the real data/job-todos.md" in r.stdout


def test_a_test_that_only_reads_passes(tmp_path):
    r = _repo(tmp_path, (
        "from pathlib import Path\n"
        "def test_reads():\n"
        "    assert (Path(__file__).resolve().parents[1] / 'data' / 'job-todos.md').read_text()\n"))
    assert r.returncode == 0, r.stdout
