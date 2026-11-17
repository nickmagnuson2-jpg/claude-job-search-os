#!/usr/bin/env python3
"""suite_record.py -- run the FULL test suite on the merged tree of base + branch, and record
a pass a gate can check.

    run   --branch B [--base origin/main] [--timeout 3600]   build merged tree, run, record
    check --tip T    [--base origin/main]                    exit 0 if that tree has a pass

WHY (2026-11-09): six PRs merged on per-branch tests only, and no full suite ran on the
merged tree. A hand-run "merged-result check" then collected 0 tests while its output read
like a pass: zsh passed an unquoted $TESTS as one argument, and `| tail` hid pytest's exit 5.
This tool turns "the full suite passed on the merged tree" into a ledger row instead of a
claim, and refuses every way that run can look green without being green:

  - pytest runs from an argv LIST (no shell, no word-splitting) and its returncode is read
    directly (no pipe can hide it);
  - a pass is recorded ONLY IF returncode == 0 AND collected > 0 AND not every collected
    test skipped. The counts come from pytest's own JUnit XML report, not from parsing
    terminal text;
  - every other outcome (timeout, zero collected, all skipped, exit 5, failures, merge conflict, git
    error, missing report) prints a JSON object naming itself and exits 1.

The merged tree is built with `git merge-tree --write-tree` (no checkout is touched), turned
into a commit with `git commit-tree`, and run in a NEW detached worktree under a fresh
tempfile.mkdtemp() directory, which is always removed in a finally block.

Rows are keyed to the merged TREE, so rewording a commit keeps its record while any content
change on either side needs a new run. The ledger is <main checkout>/tools/.suite-ledger.jsonl
(gitignored), resolved through `git rev-parse --git-common-dir` so every linked worktree
shares one ledger:

    {"kind": "suite", "verdict": "pass", "tree", "branch", "base", "base_sha", "branch_tip",
     "merged_commit", "collected", "passed", "failed", "errors", "skipped", "duration_s",
     "recorded_at", "tool_version"}

Exit codes: run 0 recorded / 1 not recorded. check 0 pass on record / 1 none (incl. a merge
conflict) / 2 usage error, bad ref, or unreadable ledger.

LIMIT: the suite runs on tracked files only. Gitignored trees (data/, output/) are absent in
the temp worktree, exactly as they are on any fresh clone.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

TOOL_VERSION = "1"
LEDGER = Path("tools") / ".suite-ledger.jsonl"
DEFAULT_BASE = "origin/main"
DEFAULT_TIMEOUT = 3600
_IDENT = {"GIT_AUTHOR_NAME": "suite_record", "GIT_AUTHOR_EMAIL": "suite_record@localhost",
          "GIT_COMMITTER_NAME": "suite_record", "GIT_COMMITTER_EMAIL": "suite_record@localhost"}


class GitError(Exception):
    pass


class LedgerError(Exception):
    """The ledger exists but cannot be read. Never read as "no record"."""


class Conflict(Exception):
    def __init__(self, paths: list[str]):
        super().__init__(", ".join(paths))
        self.paths = paths


def _git(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          env=env)


def resolve_commit(root: Path, ref: str) -> str | None:
    r = _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 else None


def merged_tree(root: Path, base_sha: str, tip_sha: str) -> str:
    """The tree of merging tip into base, without touching any checkout."""
    r = _git(root, "merge-tree", "--write-tree", "--name-only", "--no-messages",
             base_sha, tip_sha)
    lines = r.stdout.splitlines()
    if r.returncode == 1:
        paths = list(dict.fromkeys(ln for ln in lines[1:] if ln))
        raise Conflict(paths)
    if r.returncode != 0:
        raise GitError(f"git merge-tree failed: {r.stderr.strip()}")
    return lines[0].strip()


def ledger_path(root: Path) -> Path:
    r = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if r.returncode != 0:
        raise GitError(f"not a git repository: {root}")
    return Path(r.stdout.strip()).resolve().parent / LEDGER


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise LedgerError(f"{path} line {n} is not JSON ({e.msg})") from None
        if not isinstance(row, dict):
            raise LedgerError(f"{path} line {n} is not an object")
        rows.append(row)
    return rows


def has_pass(path: Path, tree: str) -> bool:
    return any(r.get("kind") == "suite" and r.get("verdict") == "pass" and r.get("tree") == tree
               for r in read_rows(path))


def parse_junit(path: Path) -> dict | None:
    """Counts from pytest's JUnit XML report, or None when it is missing or unreadable."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    c = {k: sum(int(s.get(k, 0)) for s in suites)
         for k in ("tests", "failures", "errors", "skipped")}
    return {"collected": c["tests"],
            "passed": c["tests"] - c["failures"] - c["errors"] - c["skipped"],
            "failed": c["failures"], "errors": c["errors"], "skipped": c["skipped"]}


def _tail(log: Path, n: int = 15) -> str:
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def _run_pytest(wt: Path, junit: Path, log: Path, timeout: int) -> int | None:
    """pytest's returncode, or None on timeout (the whole process group is killed)."""
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            f"--junitxml={junit}"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with log.open("wb") as out:
        proc = subprocess.Popen(argv, cwd=wt, env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return None


def _out(payload: dict, code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return code


def _fail(code: str, message: str, **extra) -> int:
    return _out({"status": "fail", "code": code, "message": message, "recorded": False,
                 **extra}, 1)


def cmd_run(root: Path, branch: str, base: str, timeout: int) -> int:
    try:
        ledger = ledger_path(root)
    except GitError as e:
        return _fail("git_error", str(e))
    base_sha = resolve_commit(root, base)
    tip_sha = resolve_commit(root, branch)
    if base_sha is None or tip_sha is None:
        missing = base if base_sha is None else branch
        return _fail("git_error", f"{missing} is not a commit in {root}")
    try:
        tree = merged_tree(root, base_sha, tip_sha)
    except Conflict as e:
        return _fail("merge_conflict", f"merge conflict in: {', '.join(e.paths)}",
                     paths=e.paths)
    except GitError as e:
        return _fail("git_error", str(e))
    ct = _git(root, "commit-tree", tree, "-p", base_sha, "-p", tip_sha, "-m",
              f"suite_record: merge {branch} into {base}", env={**os.environ, **_IDENT})
    if ct.returncode != 0:
        return _fail("git_error", f"git commit-tree failed: {ct.stderr.strip()}")
    merged_commit = ct.stdout.strip()
    ids = {"tree": tree, "branch": branch, "base": base, "base_sha": base_sha,
           "branch_tip": tip_sha, "merged_commit": merged_commit}

    tmp = Path(tempfile.mkdtemp(prefix="suite-record-"))
    wt = tmp / "wt"
    try:
        add = _git(root, "worktree", "add", "--detach", str(wt), merged_commit)
        if add.returncode != 0:
            return _fail("git_error", f"git worktree add failed: {add.stderr.strip()}", **ids)
        junit, log = tmp / "junit.xml", tmp / "pytest.log"
        start = time.monotonic()
        rc = _run_pytest(wt, junit, log, timeout)
        duration = round(time.monotonic() - start, 1)
        if rc is None:
            return _fail("timeout", f"timeout after {timeout} s", duration_s=duration, **ids)
        counts = parse_junit(junit)
        common = {"returncode": rc, "duration_s": duration, "output_tail": _tail(log),
                  **ids, **(counts or {})}
        if rc == 5:
            return _fail("pytest_exit_5", "pytest exit 5: collected 0 tests", **common)
        if counts is None:
            return _fail("no_report", f"pytest exit {rc} wrote no readable JUnit report",
                         **common)
        if rc != 0:
            if counts["failed"] or counts["errors"]:
                return _fail("tests_failed",
                             f"{counts['failed']} failed, {counts['errors']} errors, "
                             f"{counts['passed']} passed", **common)
            return _fail(f"pytest_exit_{rc}", f"pytest exit {rc}", **common)
        if counts["collected"] <= 0:
            return _fail("zero_collected", "collected 0 tests", **common)
        if counts["skipped"] >= counts["collected"]:
            return _fail("all_skipped", f"all {counts['collected']} collected tests skipped",
                         **common)
        row = {"kind": "suite", "verdict": "pass", **ids, **counts, "duration_s": duration,
               "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "tool_version": TOOL_VERSION}
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return _out({"status": "ok", "action": "run", **row, "recorded": True,
                     "ledger": str(ledger)}, 0)
    finally:
        _git(root, "worktree", "remove", "--force", str(wt))
        _git(root, "worktree", "prune")
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_check(root: Path, tip: str, base: str) -> int:
    def err(code: str, message: str) -> int:
        return _out({"status": "error", "code": code, "message": message}, 2)

    base_sha = resolve_commit(root, base)
    tip_sha = resolve_commit(root, tip)
    if base_sha is None or tip_sha is None:
        return err("bad_ref", f"{base if base_sha is None else tip} is not a commit in {root}")
    try:
        tree = merged_tree(root, base_sha, tip_sha)
        ok = has_pass(ledger_path(root), tree)
    except Conflict as e:
        return _out({"status": "ok", "action": "check", "recorded": False,
                     "code": "merge_conflict", "paths": e.paths}, 1)
    except GitError as e:
        return err("git_error", str(e))
    except LedgerError as e:
        return err("ledger_unreadable", str(e))
    return _out({"status": "ok", "action": "check", "tree": tree, "base_sha": base_sha,
                 "tip": tip_sha, "recorded": ok}, 0 if ok else 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run or check the full suite on a merged tree.")
    ap.add_argument("--repo-root", default=".")
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--branch", required=True)
    run.add_argument("--base", default=DEFAULT_BASE)
    run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    run.add_argument("--repo-root", dest="repo_root_sub", default=None)
    chk = sub.add_parser("check")
    chk.add_argument("--tip", required=True)
    chk.add_argument("--base", default=DEFAULT_BASE)
    chk.add_argument("--repo-root", dest="repo_root_sub", default=None)
    args = ap.parse_args(argv)
    root = Path(args.repo_root_sub or args.repo_root)
    if args.command == "run":
        return cmd_run(root, args.branch, args.base, args.timeout)
    return cmd_check(root, args.tip, args.base)


if __name__ == "__main__":
    sys.exit(main())
