"""End to end: the real tools/hooks/pre-push, with this checkout's real gates, on a real git
repo, fed the stdin git hands it for `git push origin <Mn>:refs/heads/main`.

Required by the merge-push build brief (output/overnight-merge-back/
111826-merge-push-build-brief.md, "Hard constraints"):
  (a) a merge whose tree is NOT git's clean merge blocks;
  (b) an open P0 on a path present only in the main checkout's ledger blocks;
  (c) a multi-ref push where only the FIRST ref is uncovered blocks (F9: the hook used to
      judge only the last ref);
  (d) an allowlist conflict resolved by key passes; resolved any other way blocks.
Plus the control (a covered chain passes) and the fail-closed diff (an unreadable range
blocks instead of exiting 0).

Fixture names are invented placeholders: this file is a public artifact.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC / "tools"))
import merge_chain as mc  # noqa: E402

PUBLIC_URL = "git@github.com:example/claude-job-search-os.git"
CODE = "tools/career_scanner/scanner.py"
BIG = "".join(f"x{i} = {i}\n" for i in range(200))
ALLOW = mc.ALLOWLIST


def dump(d):
    return json.dumps(d, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


class Repo:
    def __init__(self, root: Path):
        self.root = root

    def git(self, *a, check=True):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                               "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                               *a], cwd=self.root, capture_output=True, text=True,
                              check=check).stdout.strip()

    def branch(self, start, name, files):
        self.git("checkout", "-q", "-b", name, start)
        for rel, text in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-qm", name)
        tip = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        return tip

    def tree(self, sha):
        return self.git("rev-parse", f"{sha}^{{tree}}")

    def review(self, sha, paths, **kw):
        row = {"recorded": "2026-11-18T10:00:00+00:00", "report": f"r-{sha[:8]}.md",
               "model": "codex", "tree": self.tree(sha), "paths": paths, "findings": [],
               "waived": False, **kw}
        with (self.root / "tools" / ".cross-model-ledger.jsonl").open("a") as fh:
            fh.write(json.dumps(row) + "\n")

    def audit(self, sha):
        row = {"kind": "audit", "verdict": "clean", "tree": self.tree(sha), "tip": sha}
        with (self.root / "tools" / ".audit-pii-ledger.jsonl").open("a") as fh:
            fh.write(json.dumps(row) + "\n")

    def evidence(self, sha, paths):
        self.review(sha, paths)
        self.audit(sha)

    def push(self, lines: list[str], url=PUBLIC_URL):
        env = {k: v for k, v in os.environ.items()
               if k not in ("CODEX_VERIFY_WAIVE", "AUDIT_PII_WAIVE", "PUSH_PII_OVERRIDE")}
        env["PYTHONIOENCODING"] = "utf-8"
        p = subprocess.run(["bash", str(self.root / "tools" / "hooks" / "pre-push"),
                            "origin", url], cwd=self.root, input="".join(lines),
                           capture_output=True, text=True, timeout=300, env=env)
        return p.returncode, p.stdout + p.stderr


def main_line(tip, base):
    return f"{tip} {tip} refs/heads/main {base}\n"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "main"
    (root / "tools" / "hooks").mkdir(parents=True)
    for f in (SRC / "tools").glob("*.py"):
        shutil.copy2(f, root / "tools" / f.name)
    shutil.copy2(SRC / "tools" / "hooks" / "pre-push", root / "tools" / "hooks" / "pre-push")
    (root / ".gitignore").write_text("tools/.*\n__pycache__/\n")
    (root / ALLOW).write_text(dump({"k::base": "base reason"}))
    r = Repo(root)
    r.git("init", "-q", "-b", "main")
    r.git("add", "-A")
    r.git("commit", "-qm", "base")
    (root / "tools" / ".pii-denylist.txt").write_text("# fixture\nZorptech\n")
    return r


def covered_chain(r: Repo):
    base = r.git("rev-parse", "HEAD")
    t1 = r.branch(base, "one", {CODE: BIG})
    t2 = r.branch(base, "two", {"tools/todo_write_extra.py": BIG})
    r.evidence(t1, [CODE])
    r.evidence(t2, ["tools/todo_write_extra.py"])
    m1 = mc.build(r.root, base, t1, "one")
    m2 = mc.build(r.root, m1, t2, "two")
    return base, t1, t2, m1, m2


def test_control_a_covered_chain_passes(repo):
    base, *_, m2 = covered_chain(repo)
    rc, out = repo.push([main_line(m2, base)])
    assert rc == 0, out


def test_a_merge_that_is_not_gits_clean_merge_blocks(repo):
    base = repo.git("rev-parse", "HEAD")
    t1 = repo.branch(base, "one", {CODE: BIG})
    repo.evidence(t1, [CODE])
    sneak = repo.branch(t1, "sneak", {"tools/extra.py": BIG})
    bad = repo.git("commit-tree", repo.tree(sneak), "-p", base, "-p", t1, "-m", "m")
    rc, out = repo.push([main_line(bad, base)])
    assert rc != 0, out


def test_an_open_p0_in_the_main_ledger_blocks(repo):
    base, t1, t2, m1, m2 = covered_chain(repo)
    repo.review(t1, [CODE], report="p0.md", findings=[
        {"id": "F1", "severity": "P0", "summary": "x", "location": f"{CODE}:1"}])
    rc, out = repo.push([main_line(m2, base)])
    assert rc != 0 and "P0" in out, out


def test_multi_ref_push_with_only_the_first_ref_uncovered_blocks(repo):
    base, t1, t2, m1, m2 = covered_chain(repo)
    loose = repo.branch(base, "loose", {"tools/uncovered.py": BIG})
    repo.audit(loose)
    first = f"refs/heads/loose {loose} refs/heads/loose {base}\n"
    rc, out = repo.push([first, main_line(m2, base)])
    assert rc != 0, out
    assert "tools/uncovered.py" in out


def allow_chain(r: Repo):
    base = r.git("rev-parse", "HEAD")
    t1 = r.branch(base, "one", {CODE: BIG,
                                ALLOW: dump({"k::base": "base reason", "k::one": "r1"})})
    t2 = r.branch(base, "two", {"tools/two.py": BIG,
                                ALLOW: dump({"k::base": "base reason", "k::two": "r2"})})
    r.evidence(t1, [CODE, ALLOW])
    r.evidence(t2, ["tools/two.py", ALLOW])
    m1 = mc.build(r.root, base, t1, "one")
    return base, t1, t2, m1


def test_allowlist_conflict_resolved_by_key_passes(repo):
    base, t1, t2, m1 = allow_chain(repo)
    m2 = mc.build(repo.root, m1, t2, "two")
    assert mc.walk(repo.root, base, m2)[-1].resolved_allowlist
    rc, out = repo.push([main_line(m2, base)])
    assert rc == 0, out


def test_allowlist_conflict_resolved_by_hand_blocks(repo):
    base, t1, t2, m1 = allow_chain(repo)
    good = mc.expected_tree(repo.root, m1, t2)[0]
    repo.git("checkout", "-q", "-b", "hand", m1)
    repo.git("read-tree", "-u", "--reset", good)
    (repo.root / ALLOW).write_text(dump({"k::base": "base reason", "k::one": "r1"}))
    repo.git("add", "-A")
    hand_tree = repo.git("write-tree")
    repo.git("checkout", "-q", "-f", "main")
    m2 = repo.git("commit-tree", hand_tree, "-p", m1, "-p", t2, "-m", "hand resolved")
    rc, out = repo.push([main_line(m2, base)])
    assert rc != 0, out


def test_an_unreadable_range_fails_closed(repo):
    base = repo.git("rev-parse", "HEAD")
    missing = "ab" * 20
    rc, out = repo.push([f"refs/heads/x {missing} refs/heads/x {base}\n"],
                        url="file:///elsewhere")
    assert rc != 0, out


def new_ref_line(tip):
    return f"refs/heads/new {tip} refs/heads/new {'0' * 40}\n"


def test_a_new_ref_with_no_findable_base_blocks(repo):
    # Codex 2026-11-19 F1: with no origin/main the hook diffed the commit against the
    # working tree, which is empty in a clean checkout, and the push exited 0.
    base = repo.git("rev-parse", "HEAD")
    tip = repo.branch(base, "new", {"tools/uncovered.py": BIG})
    repo.audit(tip)
    rc, out = repo.push([new_ref_line(tip)], url="file:///elsewhere")
    assert rc != 0, out
    assert "could not find where new ref" in out


def test_a_new_ref_is_judged_from_its_merge_base_with_origin_main(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.git("update-ref", "refs/remotes/origin/main", base)
    tip = repo.branch(base, "new", {"tools/uncovered.py": BIG})
    rc, out = repo.push([new_ref_line(tip)], url="file:///elsewhere")
    assert rc != 0 and "tools/uncovered.py" in out, out
    repo.review(tip, ["tools/uncovered.py"])
    rc, out = repo.push([new_ref_line(tip)], url="file:///elsewhere")
    assert rc == 0, out


def test_a_chain_with_an_empty_net_diff_still_checks_each_merge(repo):
    # Codex 2026-11-19 F2: one merge adds a file and a later one removes it, so base..tip
    # is empty, but both commits are sent and the first carries unreviewed content.
    base = repo.git("rev-parse", "HEAD")
    add = repo.branch(base, "add", {"tools/uncovered.py": BIG})
    repo.git("checkout", "-q", "-b", "drop", add)
    repo.git("rm", "-q", "tools/uncovered.py")
    repo.git("commit", "-qm", "drop")
    drop = repo.git("rev-parse", "HEAD")
    repo.git("checkout", "-q", "main")
    m1 = mc.build(repo.root, base, add, "add")
    m2 = mc.build(repo.root, m1, drop, "drop")
    assert repo.git("diff", "--numstat", base, m2) == ""
    rc, out = repo.push([main_line(m2, base)], url="file:///elsewhere")
    assert rc != 0, out
    assert "tools/uncovered.py" in out and m1[:8] in out
