"""tests/conftest.py: a live_data test runs when its declared data exists, in any checkout.

Before 2026-11-25 every live_data test skipped in every linked worktree, data or not, so a
worktree with the data (a symlinked memory tier, the home-folder tier) never ran the check
(Codex and Grok, S1 rounds 1-5, the same P1 five times). Now the declared paths decide:
present -> run; absent on a checkout that is not the owner's (no live memory tier: a public
clone) -> skip, "not the owner's data checkout"; absent in the owner's linked worktree ->
skip; absent in the owner's main checkout -> fail; no path declared -> fail. Org b7 ruling,
2026-11-25 (option 2).
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
# Loaded by path under its own name: pytest already registers another module "conftest".
_spec = importlib.util.spec_from_file_location("tests_root_conftest", REPO_ROOT / "tests" / "conftest.py")
ct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct)


# ---------------------------------------------------------------- the verdict function

def test_present_data_runs_in_a_linked_worktree_and_in_main(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.md").write_text("x", encoding="utf-8")
    for linked in (True, False):
        for owner in (True, False):
            assert ct.live_data_verdict(("data/x.md",), tmp_path, linked, owner)[0] == "run"


def test_absent_data_skips_in_a_linked_worktree_with_the_counted_prefix(tmp_path):
    verdict, reason = ct.live_data_verdict(("data/x.md",), tmp_path, True, True)
    assert verdict == "skip"
    assert reason.startswith(ct.LIVE_DATA_SKIP) and "data/x.md" in reason


def test_absent_data_fails_in_the_owners_main_checkout(tmp_path):
    verdict, reason = ct.live_data_verdict(("data/x.md",), tmp_path, False, True)
    assert verdict == "fail" and "data/x.md" in reason


@pytest.mark.parametrize("linked", [True, False])
def test_absent_data_skips_on_a_checkout_that_is_not_the_owners(tmp_path, linked):
    """A public clone has no live memory tier: skipped, and the reason says why."""
    verdict, reason = ct.live_data_verdict(("data/x.md",), tmp_path, linked, False)
    assert verdict == "skip"
    assert reason.startswith(ct.LIVE_DATA_SKIP) and "not the owner's data checkout" in reason


def test_a_mark_with_no_paths_fails_everywhere(tmp_path):
    for linked in (True, False):
        for owner in (True, False):
            verdict, reason = ct.live_data_verdict((), tmp_path, linked, owner)
            assert verdict == "fail" and "needs the paths" in reason


def test_one_missing_path_among_several_is_enough_to_skip(tmp_path):
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    verdict, reason = ct.live_data_verdict(("a.md", "b.md"), tmp_path, True, True)
    assert verdict == "skip" and "b.md" in reason and "a.md" not in reason


def test_an_absolute_path_is_checked_as_given(tmp_path):
    outside = tmp_path / "home-tier"
    outside.mkdir()
    elsewhere = tmp_path / "repo"
    elsewhere.mkdir()
    assert ct.live_data_verdict((outside,), elsewhere, True, True)[0] == "run"
    assert ct.live_data_verdict((str(outside / "nope"),), elsewhere, True, True)[0] == "skip"


# ---------------------------------------------------------------- wired into pytest

LIVE_TEST = '''import pytest

@pytest.mark.live_data("data/x.md")
def test_reads_data():
    pass

@pytest.mark.live_data
def test_bare_mark():
    pass
'''


def _repo(tmp_path, *, linked: bool, with_data: bool) -> Path:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tools").mkdir()
    shutil.copy(REPO_ROOT / "tests" / "conftest.py", root / "tests" / "conftest.py")
    for tool in ("conftest_guard.py", "hook_runtime.py", "vault_paths.py"):
        shutil.copy(REPO_ROOT / "tools" / tool, root / "tools" / tool)
    (root / "tests" / "test_live.py").write_text(LIVE_TEST, encoding="utf-8")
    if linked:
        (root / ".git").write_text("gitdir: /nowhere/.git/worktrees/x\n", encoding="utf-8")
    else:
        (root / ".git").mkdir()
    if with_data:
        (root / "data").mkdir()
        (root / "data" / "x.md").write_text("x", encoding="utf-8")
    return root


def _owner_tier(tmp_path, root: Path, present: bool) -> dict:
    """An env whose CLAUDE_CONFIG_DIR holds (or not) the live memory tier for root's main
    checkout, the marker the conftest reads for "this is the owner's data checkout"."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from hook_runtime import claude_project_dir
    from vault_paths import main_checkout
    config = tmp_path / "claude-config"
    if present:
        (claude_project_dir(main_checkout(root), config) / "memory").mkdir(parents=True)
    return {**os.environ, "CLAUDE_CONFIG_DIR": str(config)}


def _outcomes(root: Path, env: dict) -> tuple[dict, str]:
    r = subprocess.run([sys.executable, "-m", "pytest", "-v", "-rs", "-p", "no:cacheprovider",
                        "tests/test_live.py"], cwd=root, capture_output=True, text=True,
                       env=env)
    out = {}
    for line in r.stdout.splitlines():
        for name, test in (("reads", "::test_reads_data "), ("bare", "::test_bare_mark ")):
            if test in line:
                # a setup failure prints the test twice ("PASSED"/"ERROR" on one line in -v);
                # the last word on the line is the outcome
                for word in ("ERROR", "FAILED", "SKIPPED", "PASSED"):
                    if word in line:
                        out[name] = word
                        break
    return out, r.stdout


@pytest.mark.parametrize("linked,with_data,owner,expect", [
    (True, True, True, "PASSED"),     # the fix: a worktree that has the data runs the check
    (True, False, True, "SKIPPED"),
    (False, True, True, "PASSED"),
    (False, False, True, "ERROR"),    # setup failure: the owner's main checkout must have it
    (False, False, False, "SKIPPED"), # a public clone: no tier, no data -> skip, says why
    (False, True, False, "PASSED"),
])
def test_the_hook_decides_from_the_declared_data(tmp_path, linked, with_data, owner, expect):
    root = _repo(tmp_path, linked=linked, with_data=with_data)
    got, stdout = _outcomes(root, _owner_tier(tmp_path, root, owner))
    assert got.get("reads") == expect, stdout
    assert got.get("bare") == "ERROR", stdout
    if expect == "SKIPPED" and not owner:
        assert "not the owner's data checkout" in stdout


def test_the_live_data_marker_is_registered_with_its_contract(tmp_path):
    """An unregistered marker still matches, but only with a warning, and `pytest --markers`
    stops documenting the contract (mutation, 2026-11-25: dropping the registration
    survived every other test)."""
    root = _repo(tmp_path, linked=False, with_data=True)
    r = subprocess.run([sys.executable, "-m", "pytest", "--markers", "-p", "no:cacheprovider"],
                       cwd=root, capture_output=True, text=True)
    assert "@pytest.mark.live_data(*paths)" in r.stdout, r.stdout[-600:]
