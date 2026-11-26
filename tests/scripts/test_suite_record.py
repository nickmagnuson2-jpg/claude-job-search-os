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


# Codex r3 F2 / Grok r3 F1 on 38b6a96c.

RETARGET = ("import pathlib\n\n\ndef test_retarget():\n"
            "    g = pathlib.Path('.git')\n"
            "    admin = pathlib.Path(g.read_text().split('gitdir: ', 1)[1].strip())\n"
            "    g.write_text('gitdir: ' + str(admin.parent / 'elsewhere') + '\\n')\n")


def test_cleanup_target_is_fixed_before_the_suite_runs(sr, repo, tmp_path, tmp_parent, capsys):
    """Code in the merged tree runs with write access to the temp worktree, so cleanup must
    not ask that worktree's .git file what to delete after pytest has run."""
    other = tmp_path / "elsewhere"
    _git(repo, "worktree", "add", "-q", "--detach", str(other), "main")
    _branch(repo, "retarget", {"tests/test_retarget.py": RETARGET})
    code, out = _run(sr, repo, capsys, "retarget", "--base", "main")
    assert code == 0, out
    assert sorted(p.name for p in (repo / ".git" / "worktrees").iterdir()) == ["elsewhere"]
    assert len(_worktrees(repo)) == 2
    assert list(tmp_parent.iterdir()) == []


# --- mutation kills, 2026-11-23 (codify #3 wiring: 3 survivors of 128) -----------------
def test_run_bad_ref_message_names_the_ref_that_is_missing(sr, repo, tmp_parent, capsys):
    code, out = _run(sr, repo, capsys, "no-such-branch", "--base", "main")
    assert code == 1 and out["message"].startswith("no-such-branch is not a commit")


def test_run_a_stack_check_error_is_a_git_error(sr, repo, tmp_parent, capsys, monkeypatch):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})

    def boom(*a, **k):
        raise sr.stack_check.StackCheckError("injected stack failure")
    monkeypatch.setattr(sr.stack_check, "check", boom)
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 1 and out["code"] == "git_error" and "injected stack failure" in out["message"]


def test_script_imports_stack_check_without_its_dir_on_sys_path(tmp_path):
    # -I keeps the script's own directory off sys.path, as runpy does; the module's own
    # sys.path.insert is then the only way `import stack_check` resolves (cf. outreach Codex r2 F2).
    r = subprocess.run([sys.executable, "-I", str(TOOL), "--help"], capture_output=True,
                       text=True, cwd=tmp_path, timeout=60)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- skips by reason (S3, 2026-11-24)
# Nick read 49 "not passed" tests as failures; the ledger said only "skipped": 49. Every row
# and failure payload now names why each skip happened, and the counts add up.
SKIPS = ("import pytest\n\n\n"
         "@pytest.mark.skip(reason='LIVE-DATA-ABSENT: no data')\ndef test_a():\n    pass\n\n\n"
         "@pytest.mark.skip(reason='LIVE-DATA-ABSENT: no data')\ndef test_b():\n    pass\n\n\n"
         "@pytest.mark.skip(reason='no main checkout')\ndef test_c():\n    pass\n\n\n"
         "@pytest.mark.xfail(reason='known bug')\ndef test_d():\n    assert False\n")


def test_pass_row_groups_skips_by_reason_and_they_sum(sr, repo, tmp_parent, capsys):
    _branch(repo, "skips", {"tests/test_skips.py": SKIPS})
    code, out = _run(sr, repo, capsys, "skips", "--base", "main")
    assert code == 0, out
    row = _ledger_rows(repo)[0]
    assert row["skipped"] == 4
    by = {s["reason"]: s["count"] for s in row["skips_by_reason"]}
    assert by == {"LIVE-DATA-ABSENT: no data": 2, "no main checkout": 1, "known bug": 1}
    assert row["skips_by_reason"][0]["count"] == 2          # most common first
    assert sum(by.values()) == row["skipped"]
    assert out["skips_by_reason"] == row["skips_by_reason"]


def test_failure_payload_also_carries_skips_by_reason(sr, repo, tmp_parent, capsys):
    _branch(repo, "bad", {"tests/test_skips.py": SKIPS, "tests/test_bad.py": FAILING})
    code, out = _run(sr, repo, capsys, "bad", "--base", "main")
    assert code == 1 and out["code"] == "tests_failed"
    assert sum(s["count"] for s in out["skips_by_reason"]) == out["skipped"] == 4


def test_junit_cases_reasons_failing_and_unreadable(sr, tmp_path):
    p = tmp_path / "j.xml"
    p.write_text('<?xml version="1.0"?><testsuites><testsuite tests="4">'
                 '<testcase classname="a" name="t1"><skipped message="r1"/></testcase>'
                 '<testcase classname="a" name="t2"><skipped message=" "/></testcase>'
                 '<testcase classname="b" name="t3"><failure message="x"/></testcase>'
                 '<testcase classname="b" name="t4"><error message="y"/></testcase>'
                 '<testcase classname="c" name="t5"/>'
                 '</testsuite></testsuites>', encoding="utf-8")
    assert sr.junit_cases(p) == {
        "skips_by_reason": [{"reason": "(no reason given)", "count": 1},
                            {"reason": "r1", "count": 1}],
        "failing": ["b::t3", "b::t4"]}
    empty = {"skips_by_reason": [], "failing": []}
    assert sr.junit_cases(tmp_path / "missing.xml") == empty
    (tmp_path / "bad.xml").write_text("<nope", encoding="utf-8")
    assert sr.junit_cases(tmp_path / "bad.xml") == empty


# --- The push gate's verdict before the suite (S5, 2026-11-26) --------------------------
# 11-25: a stacked branch passed its suite and then failed the push gate on its parent's
# paths 3 times (s2b, s6c, s4e), about 16 minutes of suite each. run now asks
# cross_model_gate.check_chain about the merged commit it builds before starting pytest.

_BIG = "".join(f"x{i} = {i}\n" for i in range(200))


def _gate_mod(name):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(ROOT / "tools"))


def _tree(repo, ref):
    return _git(repo, "rev-parse", f"{ref}^{{tree}}").stdout.strip()


def _review(repo, tree, paths, findings=()):
    """Rows from two families outside the author's, so any tier is covered."""
    p = repo / "tools" / ".cross-model-ledger.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for fam, model in (("openai", "codex"), ("xai", "grok")):
            fh.write(json.dumps({
                "recorded": "2026-11-26T10:00:00+00:00", "target": "t", "model": model,
                "family": fam, "report": "output/analysis/r.md", "paths": list(paths),
                "tree": tree, "verified": True, "waived": False,
                "findings": list(findings) if fam == "openai" else []}) + "\n")


def _stacked(repo):
    """s6c's shape: parent P changes tools/x.py and was reviewed only at P's tree; child
    C, stacked on P, was reviewed only for its own path at C's tree."""
    _git(repo, "checkout", "-q", "-b", "parent", "main")
    p = _commit(repo, {"tools/x.py": _BIG, "tests/test_x.py": PASSING}, "parent")
    _git(repo, "checkout", "-q", "-b", "child")
    c = _commit(repo, {"tools/y.py": _BIG, "tests/test_y.py": PASSING}, "child")
    _git(repo, "checkout", "-q", "main")
    _review(repo, _tree(repo, p), ["tools/x.py"])
    _review(repo, _tree(repo, c), ["tools/y.py"])
    return p, c


def test_a_stacked_branch_the_gate_would_refuse_never_starts(sr, repo, tmp_parent, capsys):
    _p, _c = _stacked(repo)
    code, out = _run(sr, repo, capsys, "child", "--base", "main")
    assert code == 1, out
    assert out["code"] == "gate_would_refuse"
    assert "tools/x.py" in out["message"]
    assert _ledger_rows(repo) == []
    assert len(_worktrees(repo)) == 1          # no temp worktree was ever added
    assert list(tmp_parent.iterdir()) == []


def test_a_branch_reviewed_at_its_tip_runs_and_says_the_gate_cleared(sr, repo, tmp_parent,
                                                                    capsys):
    _p, c = _stacked(repo)
    _review(repo, _tree(repo, c), ["tools/x.py", "tools/y.py"])
    code, out = _run(sr, repo, capsys, "child", "--base", "main")
    assert code == 0, out
    assert out["recorded"] is True
    assert out["gate"].startswith("cleared")
    assert "gate" not in _ledger_rows(repo)[0]     # the ledger row keeps its schema


def test_a_small_branch_runs_with_no_review(sr, repo, tmp_parent, capsys):
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 0, out
    assert "gate" in out


def test_an_open_p0_on_a_pushed_path_refuses(sr, repo, tmp_parent, capsys):
    _p, c = _stacked(repo)
    p0 = {"id": "F1", "severity": "P0", "summary": "broken", "disposition": None}
    _review(repo, _tree(repo, c), ["tools/x.py", "tools/y.py"], findings=[p0])
    code, out = _run(sr, repo, capsys, "child", "--base", "main")
    assert code == 1, out
    assert out["code"] == "gate_would_refuse"
    assert "P0" in out["message"]
    assert _ledger_rows(repo) == []


def _chain(repo):
    mc = _gate_mod("merge_chain")
    base = _git(repo, "rev-parse", "main").stdout.strip()
    t1 = _branch(repo, "one", {"tools/x.py": _BIG, "tests/test_x.py": PASSING})
    m1 = mc.build(repo, base, t1, "one")
    _git(repo, "branch", "chain", m1)
    return t1, m1


def test_an_unreviewed_merge_chain_tip_is_refused_not_skipped(sr, repo, tmp_parent, capsys):
    """Codex 2026-11-26 F1 (P0): a skip on chain shape let a hand-built chain run with no
    review. A verified chain is judged as the chain land_branch pushes."""
    t1, _m1 = _chain(repo)
    code, out = _run(sr, repo, capsys, "chain", "--base", "main")
    assert code == 1, out
    assert out["code"] == "gate_would_refuse"
    assert "tools/x.py" in out["message"] and t1[:8] in out["message"]
    assert _ledger_rows(repo) == []


def test_a_merge_chain_reviewed_at_its_branch_tip_runs(sr, repo, tmp_parent, capsys):
    """land_branch --merge-push --suite runs the suite on its chain tip; rows keyed to
    each branch tip's tree clear it, as they clear the push."""
    t1, _m1 = _chain(repo)
    _review(repo, _tree(repo, t1), ["tools/x.py"])
    code, out = _run(sr, repo, capsys, "chain", "--base", "main")
    assert code == 0, out
    assert out["gate"].startswith("cleared")


def test_a_branch_that_merged_main_into_itself_is_still_judged(sr, repo, tmp_parent, capsys):
    """Org ruling 2026-11-26: an ordinary branch whose newest commit is a merge does not
    verify as a chain (its own commit has one parent), so it is judged, not skipped."""
    _git(repo, "checkout", "-q", "-b", "feat", "main")
    _commit(repo, {"tools/x.py": _BIG, "tests/test_x.py": PASSING}, "feat")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, {"README": "moved on\n"}, "main moves")
    _git(repo, "checkout", "-q", "feat")
    _git(repo, "merge", "-q", "--no-edit", "main")
    _git(repo, "checkout", "-q", "main")
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 1, out
    assert out["code"] == "gate_would_refuse"
    assert "tools/x.py" in out["message"]


def test_a_gate_that_cannot_answer_refuses(sr, repo, tmp_parent, capsys, monkeypatch):
    g = _gate_mod("cross_model_gate")
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})
    monkeypatch.setattr(g, "check_chain", lambda *a, **k: None)
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 1 and out["code"] == "gate_unreadable", out
    assert _ledger_rows(repo) == []


def test_a_gate_that_raises_refuses(sr, repo, tmp_parent, capsys, monkeypatch):
    g = _gate_mod("cross_model_gate")
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})

    def boom(*a, **k):
        raise RuntimeError("ledger exploded")
    monkeypatch.setattr(g, "check_chain", boom)
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 1 and out["code"] == "gate_unreadable", out
    assert "ledger exploded" in out["message"]


def test_a_git_failure_reading_the_chain_refuses(sr, repo, tmp_parent, capsys, monkeypatch):
    mc = _gate_mod("merge_chain")
    _branch(repo, "feat", {"tests/test_feat.py": PASSING})

    def boom(*a, **k):
        raise mc.GitError("rev-list failed")
    monkeypatch.setattr(mc, "walk", boom)
    code, out = _run(sr, repo, capsys, "feat", "--base", "main")
    assert code == 1 and out["code"] == "gate_unreadable", out
    assert "rev-list failed" in out["message"]
