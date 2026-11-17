#!/usr/bin/env python3
"""tools/suite_record.py: a full-suite pass recorded against the MERGED tree of base + branch.

Origin (2026-11-09): six PRs merged on per-branch tests only, and a "merged-result check"
collected 0 tests while its output read like a pass (an unquoted $TESTS passed as one zsh
argument, and `| tail` hid pytest's exit 5). These tests pin that a pass is recorded ONLY
when pytest exits 0 AND collected more than 0 tests, on the merged tree, built without
touching any checkout.

Every repo here is a throwaway git repo in tmp_path with a tiny pytest suite; the real
suite is never run.
"""
import importlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "suite_record.py"
LEDGER = Path("tools") / ".suite-ledger.jsonl"

PASSING = "def test_ok():\n    assert True\n"
FAILING = "def test_bad():\n    assert False\n"


@pytest.fixture
def sr():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        mod = importlib.import_module("suite_record")
    finally:
        sys.path.remove(str(ROOT / "tools"))
    return mod


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=check)


def _commit(repo, files: dict, msg: str, delete=()):
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    for rel in delete:
        _git(repo, "rm", "-q", rel)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    _git(r, "config", "commit.gpgsign", "false")
    _commit(r, {"tests/test_base.py": PASSING, "README": "base\n"}, "base")
    return r


@pytest.fixture
def tmp_parent(tmp_path, monkeypatch):
    """Route tempfile.mkdtemp() into a directory the test can inspect for leftovers."""
    d = tmp_path / "tmpparent"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


def _branch(repo, name, files, delete=()):
    _git(repo, "checkout", "-q", "-b", name, "main")
    sha = _commit(repo, files, f"on {name}", delete=delete)
    _git(repo, "checkout", "-q", "main")
    return sha


def _run(sr, repo, capsys, *extra):
    code = sr.main(["--repo-root", str(repo), "run", "--branch", *extra])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return code, out


def _check(sr, repo, capsys, tip, base="main"):
    code = sr.main(["--repo-root", str(repo), "check", "--tip", tip, "--base", base])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return code, out


def _ledger_rows(repo):
    p = repo / LEDGER
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _merged_tree(repo, base, tip):
    return _git(repo, "merge-tree", "--write-tree", base, tip).stdout.splitlines()[0]


def _worktrees(repo):
    return [ln for ln in _git(repo, "worktree", "list", "--porcelain").stdout.splitlines()
            if ln.startswith("worktree ")]


def test_passing_branch_records_and_check_exits_0(sr, repo, tmp_parent, capsys):
    tip = _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 0, out
    assert out["status"] == "ok" and out["recorded"] is True
    assert out["collected"] == 2 and out["passed"] == 2 and out["failed"] == 0
    rows = _ledger_rows(repo)
    assert len(rows) == 1
    row = rows[0]
    assert row["verdict"] == "pass"
    assert row["tree"] == _merged_tree(repo, "main", "feat")
    assert row["branch"] == "feat" and row["branch_tip"] == tip
    assert row["base_sha"] == _git(repo, "rev-parse", "main").stdout.strip()
    assert row["collected"] == 2 and row["passed"] == 2
    assert row["tool_version"] == sr.TOOL_VERSION
    for key in ("merged_commit", "duration_s", "recorded_at", "failed", "errors", "skipped"):
        assert key in row
    # The merged commit has both parents and the merged tree.
    parents = _git(repo, "rev-list", "--parents", "-n", "1", row["merged_commit"]).stdout.split()
    assert parents[1:] == [row["base_sha"], tip]
    assert _git(repo, "rev-parse", row["merged_commit"] + "^{tree}").stdout.strip() == row["tree"]

    ccode, cout = _check(sr, repo, capsys, "feat")
    assert ccode == 0 and cout["recorded"] is True and cout["tree"] == row["tree"]


def test_run_does_not_touch_the_checkout(sr, repo, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    sym_before = _git(repo, "symbolic-ref", "HEAD").stdout.strip()
    code, _ = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 0
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before
    assert _git(repo, "symbolic-ref", "HEAD").stdout.strip() == sym_before
    assert not (repo / "tests" / "test_feat.py").exists()
    status = _git(repo, "status", "--porcelain").stdout
    assert status.strip() in ("", "?? tools/")  # only the gitignored-in-real-repo ledger


def test_failing_test_does_not_record(sr, repo, tmp_parent, capsys):
    _branch(repo, "bad", {"tests/test_bad.py": FAILING})
    code, out = _run(sr, repo, capsys, "bad", "--base", "main")
    assert code == 1
    assert out["status"] == "fail" and out["code"] == "tests_failed"
    assert out["failed"] == 1 and out["passed"] == 1 and out["collected"] == 2
    assert "1 failed" in out["message"]
    assert _ledger_rows(repo) == []
    ccode, cout = _check(sr, repo, capsys, "bad")
    assert ccode == 1 and cout["recorded"] is False


def test_pytest_exit_5_does_not_record(sr, repo, tmp_parent, capsys):
    _branch(repo, "empty", {"README": "x\n"}, delete=["tests/test_base.py"])
    code, out = _run(sr, repo, capsys, "empty", "--base", "main")
    assert code == 1
    assert out["status"] == "fail" and out["code"] == "pytest_exit_5"
    assert out["returncode"] == 5
    assert "collected 0 tests" in out["message"]
    assert _ledger_rows(repo) == []


def test_zero_collected_with_exit_0_does_not_record(sr, repo, tmp_parent, capsys):
    """The origin defect: something swallowed exit 5 and the run looked like a pass."""
    conftest = ("def pytest_sessionfinish(session, exitstatus):\n"
                "    session.exitstatus = 0\n")
    _branch(repo, "swallow", {"conftest.py": conftest}, delete=["tests/test_base.py"])
    code, out = _run(sr, repo, capsys, "swallow", "--base", "main")
    assert code == 1
    assert out["returncode"] == 0
    assert out["code"] == "zero_collected" and out["message"] == "collected 0 tests"
    assert _ledger_rows(repo) == []


def test_all_skipped_does_not_record(sr, repo, tmp_parent, capsys):
    """pytest exits 0 when every collected test skips; nothing ran, so it is not a pass."""
    skipping = "import pytest\n\n\n@pytest.mark.skip\ndef test_a():\n    pass\n"
    _branch(repo, "allskip", {"tests/test_base.py": skipping})
    code, out = _run(sr, repo, capsys, "allskip", "--base", "main")
    assert code == 1
    assert out["returncode"] == 0 and out["collected"] == 1 and out["skipped"] == 1
    assert out["code"] == "all_skipped"
    assert _ledger_rows(repo) == []


def test_timeout_does_not_record_and_cleans_up(sr, repo, tmp_parent, capsys):
    slow = "import time\n\ndef test_slow():\n    time.sleep(60)\n"
    _branch(repo, "slow", {"tests/test_slow.py": slow})
    start = time.monotonic()
    code, out = _run(sr, repo, capsys, "slow", "--base", "main", "--timeout", "3")
    assert time.monotonic() - start < 40, "pytest was not killed at the timeout"
    assert code == 1
    assert out["code"] == "timeout" and out["message"] == "timeout after 3 s"
    assert _ledger_rows(repo) == []
    assert list(tmp_parent.iterdir()) == []
    assert len(_worktrees(repo)) == 1


def test_merge_conflict_names_paths_and_does_not_record(sr, repo, tmp_parent, capsys):
    _branch(repo, "left", {"README": "left\n"})
    _git(repo, "checkout", "-q", "main")
    _commit(repo, {"README": "main side\n"}, "main edit")
    code, out = _run(sr, repo, capsys, "left", "--base", "main")
    assert code == 1
    assert out["code"] == "merge_conflict"
    assert out["paths"] == ["README"]
    assert "README" in out["message"]
    assert _ledger_rows(repo) == []
    assert list(tmp_parent.iterdir()) == []
    ccode, cout = _check(sr, repo, capsys, "left")
    assert ccode == 1 and cout["code"] == "merge_conflict"


def test_unknown_branch_is_a_git_error(sr, repo, tmp_parent, capsys):
    code, out = _run(sr, repo, capsys, "no-such-branch", "--base", "main")
    assert code == 1
    assert out["code"] == "git_error" and "no-such-branch" in out["message"]
    assert _ledger_rows(repo) == []


def test_check_on_a_different_tree_exits_1(sr, repo, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    assert _run(sr, repo, capsys, "feat", "--base", "main")[0] == 0
    # New content on the branch -> a new merged tree with no record.
    _git(repo, "checkout", "-q", "feat")
    _commit(repo, {"tests/test_more.py": PASSING}, "more")
    _git(repo, "checkout", "-q", "main")
    code, out = _check(sr, repo, capsys, "feat")
    assert code == 1 and out["recorded"] is False
    # Base moving also changes the merged tree.
    _commit(repo, {"OTHER": "o\n"}, "base moves")
    _git(repo, "branch", "-f", "old", "feat~1")
    code, out = _check(sr, repo, capsys, "old")
    assert code == 1 and out["recorded"] is False


def test_check_ignores_non_pass_rows(sr, repo, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    tree = _merged_tree(repo, "main", "feat")
    (repo / "tools").mkdir()
    (repo / LEDGER).write_text(
        json.dumps({"kind": "suite", "verdict": "fail", "tree": tree}) + "\n"
        + json.dumps({"kind": "other", "verdict": "pass", "tree": tree}) + "\n",
        encoding="utf-8")
    code, out = _check(sr, repo, capsys, "feat")
    assert code == 1 and out["recorded"] is False


def test_check_unreadable_ledger_exits_2(sr, repo, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    (repo / "tools").mkdir()
    (repo / LEDGER).write_text("{not json\n", encoding="utf-8")
    code, out = _check(sr, repo, capsys, "feat")
    assert code == 2 and out["code"] == "ledger_unreadable"


def test_check_bad_ref_exits_2(sr, repo, tmp_parent, capsys):
    code, out = _check(sr, repo, capsys, "nope")
    assert code == 2 and out["code"] == "bad_ref"


def test_check_without_tip_is_usage_error_2(sr, repo):
    with pytest.raises(SystemExit) as e:
        sr.main(["--repo-root", str(repo), "check"])
    assert e.value.code == 2


def test_temp_worktree_removed_on_success_and_failure(sr, repo, tmp_parent, capsys):
    _branch(repo, "good", {"tests/test_feat.py": PASSING})
    _branch(repo, "bad", {"tests/test_bad.py": FAILING})
    assert _run(sr, repo, capsys, "good", "--base", "main")[0] == 0
    assert list(tmp_parent.iterdir()) == []
    assert len(_worktrees(repo)) == 1
    assert _run(sr, repo, capsys, "bad", "--base", "main")[0] == 1
    assert list(tmp_parent.iterdir()) == []
    assert len(_worktrees(repo)) == 1
    assert not (repo / ".git" / "worktrees").exists() or \
        list((repo / ".git" / "worktrees").iterdir()) == []


def test_ledger_resolves_to_main_checkout_from_linked_worktree(sr, repo, tmp_path,
                                                                tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "--detach", str(linked), "main")
    assert sr.ledger_path(linked) == (repo / LEDGER).resolve()
    code, out = _run(sr, linked, capsys, "feat", "--base", "main")
    assert code == 0, out
    assert len(_ledger_rows(repo)) == 1
    assert not (linked / LEDGER).exists()
    # check from either checkout sees the one shared record
    assert _check(sr, linked, capsys, "feat")[0] == 0
    assert _check(sr, repo, capsys, "feat")[0] == 0


def test_parse_junit_counts(sr, tmp_path):
    p = tmp_path / "j.xml"
    p.write_text('<?xml version="1.0"?><testsuites><testsuite name="pytest" errors="1" '
                 'failures="2" skipped="3" tests="10" time="0.1"/></testsuites>',
                 encoding="utf-8")
    assert sr.parse_junit(p) == {"collected": 10, "passed": 4, "failed": 2,
                                 "errors": 1, "skipped": 3}
    assert sr.parse_junit(tmp_path / "missing.xml") is None
    bad = tmp_path / "bad.xml"
    bad.write_text("<nope", encoding="utf-8")
    assert sr.parse_junit(bad) is None


def test_missing_junit_report_does_not_record(sr, repo, tmp_parent, capsys, monkeypatch):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    monkeypatch.setattr(sr, "parse_junit", lambda path: None)
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 1 and out["code"] == "no_report"
    assert _ledger_rows(repo) == []


def test_cli_subprocess_end_to_end(repo, tmp_path):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    env_tmp = tmp_path / "clitmp"
    env_tmp.mkdir()
    import os
    env = {**os.environ, "TMPDIR": str(env_tmp), "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, str(TOOL), "--repo-root", str(repo), "run",
                        "--branch", "feat", "--base", "main"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["recorded"] is True
    assert list(env_tmp.iterdir()) == []
    c = subprocess.run([sys.executable, str(TOOL), "--repo-root", str(repo), "check",
                        "--tip", "feat", "--base", "main"],
                       capture_output=True, text=True, env=env)
    assert c.returncode == 0, c.stdout + c.stderr


# --------------------------------------------------------------------------- mutation gaps
# Added 2026-11-16 after mutation_check left 21 survivors on 3e74ce5b. The serious class: a
# git failure whose error return was never exercised, so a mutant returning None made the
# CLI exit 0 with no output, which a caller reads as a recorded pass.

def _failing_git(sr, monkeypatch, prefix):
    """Make one git subcommand fail the way git does (rc 128); every other call is real."""
    real = sr._git

    def fake(root, *args, **kw):
        if tuple(args[:len(prefix)]) == prefix:
            return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: injected")
        return real(root, *args, **kw)
    monkeypatch.setattr(sr, "_git", fake)


@pytest.mark.parametrize("prefix", [
    ("rev-parse", "--path-format=absolute"),     # ledger_path
    ("merge-tree",),                             # a git error that is not a conflict
    ("commit-tree",),
    ("worktree", "add"),
])
def test_a_git_failure_in_run_is_named_and_records_nothing(sr, repo, tmp_parent, capsys,
                                                          monkeypatch, prefix):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    _failing_git(sr, monkeypatch, prefix)
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 1 and out["status"] == "fail" and out["code"] == "git_error"
    assert "injected" in out["message"] or "not a git repository" in out["message"]
    assert out["recorded"] is False
    assert _ledger_rows(repo) == []
    assert list(tmp_parent.iterdir()) == []


def test_a_git_failure_in_check_exits_2(sr, repo, tmp_parent, capsys, monkeypatch):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    _failing_git(sr, monkeypatch, ("merge-tree",))
    code, out = _check(sr, repo, capsys, "feat")
    assert code == 2 and out["code"] == "git_error" and "injected" in out["message"]


def test_bad_ref_message_names_the_ref_that_is_missing(sr, repo, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    code, out = _check(sr, repo, capsys, "feat", base="nobase")
    assert code == 2 and out["message"].startswith("nobase is not a commit")
    code, out = _check(sr, repo, capsys, "notip")
    assert code == 2 and out["message"].startswith("notip is not a commit")


def test_ledger_blank_lines_are_skipped_and_non_objects_refused(sr, repo, tmp_parent,
                                                                 capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    tree = _merged_tree(repo, "main", "feat")
    (repo / "tools").mkdir()
    row = json.dumps({"kind": "suite", "verdict": "pass", "tree": tree})
    (repo / LEDGER).write_text("\n   \n" + row + "\n\n", encoding="utf-8")
    assert _check(sr, repo, capsys, "feat")[0] == 0
    (repo / LEDGER).write_text(row + "\n[1, 2]\n", encoding="utf-8")
    code, out = _check(sr, repo, capsys, "feat")
    assert code == 2 and out["code"] == "ledger_unreadable"
    assert "line 2 is not an object" in out["message"]


def test_a_failing_run_reports_the_end_of_pytest_output(sr, repo, tmp_parent, capsys):
    _branch(repo, "bad", {"tests/test_bad.py": FAILING})
    code, out = _run(sr, repo, capsys, "bad", "--base", "main")
    assert code == 1 and out["code"] == "tests_failed"
    assert "test_bad" in out["output_tail"] and "1 failed" in out["output_tail"]


def test_tail_of_a_missing_log_is_empty(sr, tmp_path):
    assert sr._tail(tmp_path / "missing.log") == ""


def test_a_nonzero_exit_with_no_failures_is_named_by_its_code(sr, repo, tmp_parent, capsys):
    """Interrupted or internal-error runs fail by exit code, never as tests_failed."""
    conftest = ("def pytest_sessionfinish(session, exitstatus):\n"
                "    session.exitstatus = 3\n")
    _branch(repo, "exit3", {"conftest.py": conftest})
    code, out = _run(sr, repo, capsys, "exit3", "--base", "main")
    assert code == 1 and out["returncode"] == 3
    assert out["failed"] == 0 and out["errors"] == 0
    assert out["code"] == "pytest_exit_3" and out["message"] == "pytest exit 3"
    assert _ledger_rows(repo) == []


# --------------------------------------------------------------------------- review round 1
# Codex r1 on ddd86433.

def test_exit_0_with_failures_in_the_report_does_not_record(sr, repo, tmp_parent, capsys):
    """F1 (P0): a hook can force exit 0 while the JUnit report holds a failure. The report
    is the evidence; the exit code alone is not."""
    conftest = ("def pytest_sessionfinish(session, exitstatus):\n"
                "    session.exitstatus = 0\n")
    _branch(repo, "launder", {"conftest.py": conftest, "tests/test_bad.py": FAILING})
    code, out = _run(sr, repo, capsys, "launder", "--base", "main")
    assert code == 1 and out["returncode"] == 0
    assert out["failed"] == 1 and out["passed"] == 1
    assert out["code"] == "tests_failed"
    assert _ledger_rows(repo) == []


def test_exit_0_with_errors_in_the_report_does_not_record(sr, repo, tmp_parent, capsys):
    conftest = ("import pytest\n\n\n@pytest.fixture\ndef broken():\n"
                "    raise RuntimeError('setup')\n\n\n"
                "def pytest_sessionfinish(session, exitstatus):\n"
                "    session.exitstatus = 0\n")
    erroring = "def test_uses(broken):\n    pass\n"
    _branch(repo, "err", {"conftest.py": conftest, "tests/test_err.py": erroring})
    code, out = _run(sr, repo, capsys, "err", "--base", "main")
    assert code == 1 and out["returncode"] == 0 and out["errors"] == 1
    assert out["code"] == "tests_failed"
    assert _ledger_rows(repo) == []


def test_cleanup_leaves_other_worktrees_alone(sr, repo, tmp_path, tmp_parent, capsys):
    """F2 (P1): another worktree whose directory is absent (an unmounted volume, say) keeps
    its record. Only this tool's own worktree is cleaned up."""
    other = tmp_path / "elsewhere"
    _git(repo, "worktree", "add", "-q", "--detach", str(other), "main")
    import shutil
    shutil.rmtree(other)
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 0, out
    assert list(tmp_parent.iterdir()) == []
    listed = _worktrees(repo)
    assert len(listed) == 2 and any(str(other) in ln for ln in listed)


# Grok r1 on ddd86433.

def test_caller_pytest_addopts_cannot_shrink_the_suite(sr, repo, tmp_parent, capsys,
                                                       monkeypatch):
    """F3 (P1): `-k` from the caller's PYTEST_ADDOPTS would run only the passing test and
    record a full-suite pass for a tree with a failing one."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k test_ok")
    _branch(repo, "feat", {"tests/test_bad.py": FAILING})
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 1 and out["code"] == "tests_failed" and out["failed"] == 1
    assert _ledger_rows(repo) == []


def test_an_unreadable_ledger_is_named_not_a_traceback(sr, repo, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    (repo / LEDGER).mkdir(parents=True)          # a directory where the file should be
    code, out = _check(sr, repo, capsys, "feat")
    assert code == 2 and out["code"] == "ledger_unreadable"
    (repo / LEDGER).rmdir()
    (repo / LEDGER).write_bytes(b"\xff\xfe not utf-8\n")
    code, out = _check(sr, repo, capsys, "feat")
    assert code == 2 and out["code"] == "ledger_unreadable"


# Codex r2 and Grok r2 on 23b945bb: removing named variables is not a boundary.

NARROW = ("def pytest_collection_modifyitems(items):\n"
          "    items[:] = [i for i in items if i.name == 'test_ok']\n")


def test_a_caller_plugin_cannot_deselect_failing_tests(sr, repo, tmp_parent, capsys,
                                                       monkeypatch):
    """r2 F1 (P0): PYTEST_PLUGINS names a module in the tree that drops every failing test."""
    monkeypatch.setenv("PYTEST_PLUGINS", "narrow")
    _branch(repo, "narrow", {"narrow.py": NARROW, "tests/test_ok.py": PASSING,
                             "tests/test_bad.py": FAILING})
    code, out = _run(sr, repo, capsys, "narrow", "--base", "main")
    assert code == 1 and out["code"] == "tests_failed" and out["failed"] == 1
    assert _ledger_rows(repo) == []


def test_the_inner_pytest_sees_only_the_allowed_environment(sr, repo, tmp_parent, capsys,
                                                            monkeypatch):
    for k in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "PYTHONSTARTUP",
              "DRAFT_OVERRIDE", "SOME_TOOL_SWITCH"):
        monkeypatch.setenv(k, "x")
    allowed = sorted(sr.CHILD_ENV_KEEP)
    probe = ("import os\n\ndef test_env():\n"
             f"    allowed = set({allowed!r})\n"
             # set by the child itself, not inherited: pytest's own two, and macOS's
             # per-process text encoding
             "    own = {'PYTHONIOENCODING', 'PYTEST_CURRENT_TEST', 'PYTEST_VERSION'}\n"
             "    extra = {k for k in os.environ if k not in allowed and k not in own\n"
             "             and not k.startswith(('LC_', '__CF_'))}\n"
             "    assert extra == set(), extra\n"
             "    assert os.environ['PATH']\n")
    _branch(repo, "envprobe", {"tests/test_env.py": probe})
    code, out = _run(sr, repo, capsys, "envprobe", "--base", "main")
    assert code == 0, out.get("output_tail", out)


def test_a_stray_git_dir_does_not_redirect_the_run(sr, repo, tmp_path, tmp_parent, capsys,
                                                   monkeypatch):
    """r2 F2 (P1): a hook exports GIT_DIR; `git -C <root>` must still mean <root>, and the
    inner suite must not inherit it either."""
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 0, out
    assert out["tree"] == _merged_tree_clean_env(repo, "main", "feat")
    assert len(_ledger_rows(repo)) == 1


def _merged_tree_clean_env(repo, base, tip):
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(repo), "merge-tree", "--write-tree", base, tip],
                          capture_output=True, text=True, env=env, check=True
                          ).stdout.splitlines()[0]
