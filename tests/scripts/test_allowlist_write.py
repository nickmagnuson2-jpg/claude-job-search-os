"""Invariants for tools/allowlist_write.py, the locked mutation-allowlist writer.

The bug class: tools/mutation-allow.json is shared state that several concurrent
sessions write, and every one of them edited it with an ad-hoc load/mutate/dump script.
That is a read-modify-write with no lock; two overlapping sessions lose one set of
entries entirely, both reporting success. Measured 2026-09-06: two sessions wrote it
within the same hour and it survived by luck.

Every test is written to FAIL if the behaviour breaks, not merely to exercise it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
import allowlist_write as aw  # noqa: E402

K1 = "tools/foo.py::main::RETURN_NONE::0a1b2c3d"
K2 = "tools/bar.py::helper::IF_FALSE::9f8e7d6c"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    aw.allow_path(tmp_path).write_text(
        json.dumps({K1: "a pre-existing reason"}, indent=2) + "\n", encoding="utf-8")
    return tmp_path


# --- key validation -------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "tools/foo.py::main::RETURN_NONE",          # three parts
    "tools/foo.py::main::RETURN_NONE::a::b",    # five
    "foo::main::RETURN_NONE::0a1b2c3d",         # not a .py path
    "",
])
def test_a_malformed_key_is_refused(bad):
    """A typo'd key is not an error anywhere else: it never matches a mutant, so it
    sits in the file looking like coverage while the survivor keeps surviving."""
    with pytest.raises(ValueError):
        aw.validate_key(bad)


def test_a_well_formed_key_passes():
    aw.validate_key(K2)


# --- the reason is mandatory ----------------------------------------------------

@pytest.mark.parametrize("why", ["", "   ", None])
def test_an_entry_without_a_reason_is_refused(repo, why):
    before = aw.allow_path(repo).read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        aw.add(repo, K2, why)
    assert aw.allow_path(repo).read_text(encoding="utf-8") == before


def test_an_existing_reason_is_not_silently_replaced(repo):
    """Overwriting someone else's justification erases the argument it recorded."""
    with pytest.raises(ValueError, match="already allowlisted"):
        aw.add(repo, K1, "my different reason")
    assert aw.load(repo)[K1] == "a pre-existing reason"


def test_force_replaces_it(repo):
    aw.add(repo, K1, "a better traced reason", force=True)
    assert aw.load(repo)[K1] == "a better traced reason"


# --- writes land, and preserve everything else ----------------------------------

def test_add_preserves_every_pre_existing_entry(repo):
    aw.add(repo, K2, "second reason")
    data = aw.load(repo)
    assert data[K1] == "a pre-existing reason"
    assert data[K2] == "second reason"
    assert len(data) == 2


def test_remove_takes_only_the_named_key(repo):
    aw.add(repo, K2, "second reason")
    aw.remove(repo, K1)
    assert list(aw.load(repo)) == [K2]


def test_removing_an_absent_key_raises_and_writes_nothing(repo):
    before = aw.allow_path(repo).read_text(encoding="utf-8")
    with pytest.raises(KeyError):
        aw.remove(repo, K2)
    assert aw.allow_path(repo).read_text(encoding="utf-8") == before


def test_the_file_stays_sorted_and_newline_terminated(repo):
    """Sorted so concurrent additions by different sessions produce a minimal diff
    instead of reordering the file and burying the real change."""
    aw.add(repo, K2, "second")
    text = aw.allow_path(repo).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert list(json.loads(text)) == sorted([K1, K2])


def test_no_tmp_file_is_left_behind(repo):
    aw.add(repo, K2, "second")
    assert list((repo / "tools").glob("*.tmp")) == []


# --- the lock is actually taken -------------------------------------------------

def test_add_takes_the_advisory_lock(repo, monkeypatch):
    """THE POINT OF THE TOOL. Without the lock this is the same unlocked
    read-modify-write the ad-hoc scripts were doing, and the tests above would all
    still pass."""
    taken = []
    real = aw.inbox_lock.file_lock

    def spy(path, timeout=30.0):
        taken.append(Path(path).name)
        return real(path, timeout=timeout)

    monkeypatch.setattr(aw.inbox_lock, "file_lock", spy)
    aw.add(repo, K2, "second")
    assert taken == [aw.ALLOW_NAME]


def test_remove_takes_the_advisory_lock(repo, monkeypatch):
    taken = []
    real = aw.inbox_lock.file_lock

    def spy(path, timeout=30.0):
        taken.append(Path(path).name)
        return real(path, timeout=timeout)

    monkeypatch.setattr(aw.inbox_lock, "file_lock", spy)
    aw.remove(repo, K1)
    assert taken == [aw.ALLOW_NAME]


# --- CLI contract ---------------------------------------------------------------

def run_cli(repo: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOLS_DIR / "allowlist_write.py"),
         "--repo-root", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_cli_add_then_list(repo):
    assert run_cli(repo, "add", K2, "--why", "a reason").returncode == 0
    out = json.loads(run_cli(repo, "list").stdout)
    assert out["count"] == 2
    assert K2 in out["keys"]


def test_cli_list_prefix_filters(repo):
    run_cli(repo, "add", K2, "--why", "a reason")
    out = json.loads(run_cli(repo, "list", "--prefix", "tools/bar.py").stdout)
    assert out["keys"] == [K2]


def test_cli_blank_reason_exits_2(repo):
    proc = run_cli(repo, "add", K2, "--why", "   ")
    assert proc.returncode == 2
    assert "error" in json.loads(proc.stderr)


def test_cli_get_returns_the_reason(repo):
    out = json.loads(run_cli(repo, "get", K1).stdout)
    assert out["why"] == "a pre-existing reason"


# --- killing the 2026-09-07 mutation survivors ----------------------------------
# 12 of them, and the pattern is worth naming: the first 20 tests all cluster on the
# things I was thinking about while writing the tool (the lock, the mandatory reason,
# the key shape) and none touched the CLI dispatch or a single return value. Coverage
# followed attention, not risk.

def test_load_on_a_missing_file_is_empty_not_an_error(tmp_path):
    (tmp_path / "tools").mkdir()
    assert aw.load(tmp_path) == {}


def test_load_rejects_a_json_file_that_is_not_an_object(tmp_path):
    """A JSON array parses fine and would then be mutated as if it were a dict."""
    (tmp_path / "tools").mkdir()
    aw.allow_path(tmp_path).write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a JSON object"):
        aw.load(tmp_path)


def test_add_validates_the_key_ITSELF_not_just_via_the_helper(repo):
    """validate_key was only ever called directly by tests, so dropping the call from
    add() was invisible: a malformed key would be written straight into the file."""
    before = aw.allow_path(repo).read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        aw.add(repo, "tools/foo.py::main::RETURN_NONE", "a reason")
    assert aw.allow_path(repo).read_text(encoding="utf-8") == before


def test_add_reports_what_it_did(repo):
    added = aw.add(repo, K2, "a reason")
    assert added["action"] == "added"
    assert added["entries_before"] == 1 and added["entries_after"] == 2
    replaced = aw.add(repo, K2, "a better reason", force=True)
    assert replaced["action"] == "replaced"
    assert replaced["entries_after"] == 2      # a replacement does not grow the file


def test_remove_reports_what_it_did(repo):
    out = aw.remove(repo, K1)
    assert out["action"] == "removed"
    assert out["entries_before"] == 1 and out["entries_after"] == 0


def test_removing_an_absent_key_names_the_key(repo):
    """Without the guard, data.pop() raises KeyError anyway -- so the test above passes
    either way. What the guard buys is a message naming what was not found."""
    # NOT just KeyError: data.pop() raises one anyway, so a bare `pytest.raises`
    # passes with the guard deleted. The guard buys the MESSAGE.
    with pytest.raises(KeyError, match="is not in the allowlist"):
        aw.remove(repo, K2)


# --- the CLI dispatch, which nothing touched ------------------------------------

def test_cli_remove_works_end_to_end(repo):
    proc = run_cli(repo, "remove", K1)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["action"] == "removed"
    assert aw.load(repo) == {}


def test_cli_remove_of_an_absent_key_exits_2(repo):
    proc = run_cli(repo, "remove", K2)
    assert proc.returncode == 2
    assert "error" in json.loads(proc.stderr)


def test_cli_get_of_an_absent_key_exits_2(repo):
    """Same shape as remove: without the guard, data[key] raises KeyError and the
    handler still exits 2, so only the message distinguishes them."""
    proc = run_cli(repo, "get", K2)
    assert proc.returncode == 2
    assert "is not in the allowlist" in json.loads(proc.stderr)["error"]


def test_cli_add_of_a_malformed_key_exits_2(repo):
    proc = run_cli(repo, "add", "nonsense", "--why", "a reason")
    assert proc.returncode == 2
