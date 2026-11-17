"""tools/land_branch.py: read-only landing check. Real temp git repos; gh, the push gate, the
audit record and the PII scan are stubs whose exit codes the test sets through env vars."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import land_branch as lb  # noqa: E402

FAKE_SHA = "ab" * 20     # a commit sha that is not in any object store


def sh(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


AUDIT_STUB = "import os, sys\nsys.exit(int(os.environ.get('LB_AUDIT_RC', '0')))\n"
PII_STUB = """import json, os, sys
mode = os.environ.get('LB_PII_MODE', 'clean')
sys.stdin.read()
r = {'scanned': 1, 'hits': [], 'ambiguous_unresolved': [], 'skipped': [], 'unreadable': []}
if mode == 'hit':
    r['hits'] = [{'file': 'x.py', 'tokens': ['Realname']}]
    print(json.dumps(r)); sys.exit(2)
if mode == 'ambiguous':
    r['ambiguous_unresolved'] = ['x.py:voice']
    print(json.dumps(r)); sys.exit(3)
print(json.dumps(r)); sys.exit(0)
"""
SUITE_STUB = "import os, sys\nsys.exit(int(os.environ.get('LB_SUITE_RC', '0')))\n"
HOOK_STUB = """#!/bin/bash
cat > "$LB_HOOK_LOG"
echo "waive=${CODEX_VERIFY_WAIVE:-unset} remote=$1 url=$2" >> "$LB_HOOK_LOG"
exit "${LB_GATE_RC:-0}"
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    sh(root, "init", "-q", "-b", "main")
    sh(root, "config", "user.email", "t@example.com")
    sh(root, "config", "user.name", "T")
    (root / "tools").mkdir()
    (root / "tools" / "audit_pii_record.py").write_text(AUDIT_STUB)
    (root / "tools" / "check_public_pii.py").write_text(PII_STUB)
    (root / "shared.json").write_text('{\n  "a": 1\n}\n')
    sh(root, "add", "-A")
    sh(root, "commit", "-qm", "base")
    base = sh(root, "rev-parse", "HEAD")
    sh(root, "remote", "add", "origin", "git@github.com:owner/repo.git")
    sh(root, "update-ref", "refs/remotes/origin/main", base)
    hook = Path(sh(root, "rev-parse", "--path-format=absolute", "--git-path", "hooks")) / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(HOOK_STUB)
    hook.chmod(0o755)
    monkeypatch.setenv("LB_HOOK_LOG", str(tmp_path / "hook.log"))
    for k in ("LB_AUDIT_RC", "LB_PII_MODE", "LB_GATE_RC", "LB_SUITE_RC"):
        monkeypatch.delenv(k, raising=False)
    return {"root": root, "base": base, "tmp": tmp_path}


def branch(repo, name, files: dict, upstream: str | None = None):
    """A branch off base in its own worktree, with one commit writing `files`."""
    wt = repo["tmp"] / "wt" / name
    sh(repo["root"], "worktree", "add", "-q", "-b", name, str(wt), repo["base"])
    for rel, text in files.items():
        p = wt / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    sh(wt, "add", "-A")
    sh(wt, "commit", "-qm", name)
    if upstream:
        sh(wt, "branch", f"--set-upstream-to={upstream}", name)
    return wt, sh(wt, "rev-parse", "HEAD")


def gh_for(heads: dict, commits: dict):
    def gh(path, root, paginate=False):
        if path.endswith("git/matching-refs/heads"):
            return [{"ref": f"refs/heads/{b}", "object": {"sha": s}} for b, s in heads.items()]
        sha = path.rsplit("/", 1)[1]
        if sha in commits:
            return commits[sha]
        raise RuntimeError("404")
    return gh


def gh_main_at(sha, parents=(), tree="0" * 40, extra_heads=None):
    heads = {"main": sha, **(extra_heads or {})}
    return gh_for(heads, {sha: {"parents": [{"sha": p} for p in parents],
                                "commit": {"tree": {"sha": tree}}}})


def gh_local_main(repo):
    return gh_main_at(repo["base"], [], sh(repo["root"], "rev-parse", f"{repo['base']}^{{tree}}"))


def gh_down(path, root, paginate=False):
    raise RuntimeError("gh: not authenticated")


def by_name(rep):
    return {r["branch"]: r for r in rep["branches"]}


# ---------------------------------------------------------------- pure helpers
def test_parse_worktrees_maps_branch_to_path():
    porcelain = ("worktree /a\nHEAD 1\nbranch refs/heads/main\n\n"
                 "worktree /b\nHEAD 2\ndetached\n\n"
                 "worktree /c\nHEAD 3\nbranch refs/heads/feat-x\n")
    assert lb.parse_worktrees(porcelain) == {"main": "/a", "feat-x": "/c"}


@pytest.mark.parametrize("url,slug", [
    ("git@github.com:owner/repo.git", "owner/repo"),
    ("https://github.com/owner/repo.git", "owner/repo"),
    ("https://github.com/owner/repo", "owner/repo"),
    ("git@gitlab.com:owner/repo.git", None),
])
def test_repo_slug(url, slug):
    assert lb.repo_slug(url) == slug


@pytest.mark.parametrize("up,is_main", [
    ("origin/main", True), ("main", True), ("upstream/main", True),
    ("origin/feat", False), ("origin/main-fix", False),
])
def test_upstream_is_main(up, is_main):
    assert lb.upstream_is_main(up) is is_main


# ---------------------------------------------------------------- GitHub main resolution
def test_rebuilds_github_main_from_local_parents(repo):
    _, side = branch(repo, "side", {"side.txt": "s\n"})
    tree = sh(repo["root"], "merge-tree", "--write-tree", repo["base"], side).splitlines()[0]
    m = lb.resolve_main_ref(repo["root"], lb.github_state(
        repo["root"], "owner/repo", gh_main_at(FAKE_SHA, [repo["base"], side], tree)))
    assert m["method"] == "rebuilt"
    assert m["github_main"] == FAKE_SHA
    assert sh(repo["root"], "rev-parse", f"{m['ref']}^{{tree}}") == tree
    assert sh(repo["root"], "rev-parse", f"{m['ref']}^1", f"{m['ref']}^2").split() == [repo["base"], side]
    assert any("stale" in w for w in m["warnings"])


def test_rebuild_falls_back_when_tree_differs(repo):
    _, side = branch(repo, "side", {"side.txt": "s\n"})
    m = lb.resolve_main_ref(repo["root"], lb.github_state(
        repo["root"], "owner/repo", gh_main_at(FAKE_SHA, [repo["base"], side], "1" * 40)))
    assert m["method"] == "fallback"
    assert m["ref"] == repo["base"]
    assert any("FALLING BACK" in w for w in m["warnings"])


def test_rebuild_falls_back_when_a_parent_is_missing(repo):
    m = lb.resolve_main_ref(repo["root"], lb.github_state(
        repo["root"], "owner/repo", gh_main_at(FAKE_SHA, [repo["base"], "cd" * 20], "1" * 40)))
    assert m["method"] == "fallback"
    assert any("parents missing" in w and "cdcdcdcd" in w for w in m["warnings"])


def test_uses_github_main_directly_when_it_is_local(repo):
    m = lb.resolve_main_ref(repo["root"], lb.github_state(repo["root"], "owner/repo",
                                                          gh_local_main(repo)))
    assert (m["method"], m["ref"], m["warnings"]) == ("local", repo["base"], [])


# ---------------------------------------------------------------- per-branch verdicts
def test_clean_branch_is_ready_with_explicit_refspec(repo):
    wt, tip = branch(repo, "feat", {"f.py": "x = 1\n"})
    rep = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    r = by_name(rep)["feat"]
    assert r["ready"], r["refusals"]
    assert rep["all_ready"]
    assert rep["push_commands"] == [f"cd {wt} && git push origin refs/heads/feat:refs/heads/feat"]


def test_upstream_main_is_refused(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"}, upstream="origin/main")
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    assert not r["ready"]
    assert any("upstream is origin/main" in x for x in r["refusals"])


def test_dirty_worktree_is_refused(repo):
    wt, _ = branch(repo, "feat", {"f.py": "x = 1\n"})
    (wt / "f.py").write_text("x = 2\n")
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    (msg,) = [x for x in r["refusals"] if "uncommitted" in x]
    assert "f.py" in msg and "tip is not on GitHub" in msg


def test_dirty_worktree_says_when_pushed_tip_is_unaffected(repo):
    wt, tip = branch(repo, "feat", {"f.py": "x = 1\n"})
    (wt / "f.py").write_text("x = 2\n")
    gh = gh_for({"main": repo["base"], "feat": tip},
                {repo["base"]: {"parents": [], "commit": {"tree": {"sha": "0" * 40}}}})
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh))["feat"]
    assert not r["ready"]
    assert any("already on GitHub at this sha" in x for x in r["refusals"])


def test_untracked_file_alone_does_not_refuse(repo):
    wt, _ = branch(repo, "feat", {"f.py": "x = 1\n"})
    (wt / "scratch.txt").write_text("notes\n")
    assert by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]["ready"]


def test_branch_without_worktree_is_refused(repo):
    sh(repo["root"], "branch", "orphan", repo["base"])
    r = by_name(lb.check(repo["root"], ["orphan"], gh=gh_local_main(repo)))["orphan"]
    assert r["refusals"] == ["no worktree for this branch"]


def test_main_itself_is_refused(repo):
    r = by_name(lb.check(repo["root"], ["main"], gh=gh_local_main(repo)))["main"]
    assert not r["ready"]


def test_conflict_with_main_names_the_file(repo):
    _, side = branch(repo, "side", {"shared.json": '{\n  "a": 2\n}\n'})
    tree = sh(repo["root"], "merge-tree", "--write-tree", repo["base"], side).splitlines()[0]
    branch(repo, "feat", {"shared.json": '{\n  "a": 3\n}\n'})
    rep = lb.check(repo["root"], ["feat"], gh=gh_main_at(FAKE_SHA, [repo["base"], side], tree))
    assert rep["main"]["method"] == "rebuilt"
    r = by_name(rep)["feat"]
    assert not r["ready"]
    assert any("conflicts with main: shared.json" in x for x in r["refusals"])


def test_pairwise_conflict_named_and_ordered_last(repo):
    branch(repo, "a1", {"shared.json": '{\n  "a": 1,\n  "x": 1\n}\n'})
    branch(repo, "b1", {"shared.json": '{\n  "a": 1,\n  "y": 1\n}\n'})
    branch(repo, "c1", {"c.py": "c = 1\n"})
    rep = lb.check(repo["root"], ["a1", "b1", "c1"], gh=gh_local_main(repo))
    assert rep["conflicts"] == [{"a": "a1", "b": "b1", "files": ["shared.json"], "error": None}]
    assert rep["merge_order"]["first"] == ["c1"]
    assert [(p["a"], p["b"]) for p in rep["merge_order"]["pairs"]] == [("a1", "b1")]
    # A conflicting pair still gets push commands: each merges clean against main.
    assert len(rep["push_commands"]) == 3
    out = lb.render(rep)
    assert "a1 x b1: shared.json" in out


def test_gate_receives_git_refs_line_and_no_waiver(repo, monkeypatch):
    _, tip = branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv("CODEX_VERIFY_WAIVE", "should never reach the hook")
    lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    log = Path(os.environ["LB_HOOK_LOG"]).read_text()
    assert f"refs/heads/feat {tip} refs/heads/feat {'0' * 40}" in log
    assert "waive=unset" in log
    assert "remote=origin url=git@github.com:owner/repo.git" in log


def test_gate_uses_remote_sha_when_branch_exists_on_github(repo):
    _, tip = branch(repo, "feat", {"f.py": "x = 1\n"})
    gh = gh_for({"main": repo["base"], "feat": repo["base"]},
                {repo["base"]: {"parents": [], "commit": {"tree": {"sha": "0" * 40}}}})
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh))["feat"]
    assert f"refs/heads/feat {repo['base']}" in Path(os.environ["LB_HOOK_LOG"]).read_text()
    assert r["github_tip"] == repo["base"]
    assert r["notes"]


def test_gate_failure_refuses(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv("LB_GATE_RC", "1")
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    assert "push gate rc 1" in r["refusals"]


def test_audit_failure_refuses(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv("LB_AUDIT_RC", "1")
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    assert "audit_pii_record check rc 1" in r["refusals"]


def test_pii_hit_refuses_and_is_listed(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv("LB_PII_MODE", "hit")
    rep = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    r = by_name(rep)["feat"]
    assert "PII denylist hits: 1" in r["refusals"]
    assert "x.py: Realname" in lb.render(rep)


def test_ambiguous_pii_settled_by_clean_audit(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv("LB_PII_MODE", "ambiguous")
    rep = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    assert by_name(rep)["feat"]["ready"]
    assert "ambiguous PII (settled by audit record): x.py:voice" in lb.render(rep)


def test_ambiguous_pii_without_audit_record_refuses(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv("LB_PII_MODE", "ambiguous")
    monkeypatch.setenv("LB_AUDIT_RC", "1")
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    assert any(x.startswith("ambiguous PII matches") for x in r["refusals"])


def test_pii_scan_is_given_only_added_or_modified_files(repo, monkeypatch, tmp_path):
    wt, tip = branch(repo, "feat", {"new.py": "n = 1\n", "shared.json": '{"a": 5}\n'})
    seen = {}

    def fake_scan(w, files):
        seen["files"] = files
        return {"rc": 0, "ok": True, "hits": [], "ambiguous": []}
    monkeypatch.setattr(lb, "pii_scan", fake_scan)
    lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    assert sorted(seen["files"]) == ["new.py", "shared.json"]


def test_suite_skipped_with_notice_when_tool_absent(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    r = by_name(lb.check(repo["root"], ["feat"], want_suite=True, gh=gh_local_main(repo)))["feat"]
    assert r["ready"]
    assert r["steps"]["suite"]["skipped"]


def test_suite_failure_refuses_when_tool_present(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n", "tools/suite_record.py": SUITE_STUB})
    monkeypatch.setenv("LB_SUITE_RC", "1")
    r = by_name(lb.check(repo["root"], ["feat"], want_suite=True, gh=gh_local_main(repo)))["feat"]
    assert "suite_record check rc 1" in r["refusals"]


def test_github_unreadable_is_not_ready_and_loud(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    rep = lb.check(repo["root"], ["feat"], gh=gh_down)
    assert not rep["all_ready"]
    assert any("GitHub read failed" in w for w in rep["main"]["warnings"])
    assert "GitHub unreadable: base not confirmed" in by_name(rep)["feat"]["refusals"]
    assert "GitHub main: UNREADABLE" in lb.render(rep)


def test_check_changes_no_ref_and_no_worktree(repo):
    _, side = branch(repo, "side", {"side.txt": "s\n"})
    tree = sh(repo["root"], "merge-tree", "--write-tree", repo["base"], side).splitlines()[0]
    branch(repo, "a1", {"shared.json": '{"a": 1, "x": 1}\n'})
    branch(repo, "b1", {"shared.json": '{"a": 1, "y": 1}\n'})
    snap = lambda: (sh(repo["root"], "for-each-ref"), sh(repo["root"], "worktree", "list"),
                    sh(repo["root"], "status", "--porcelain"))
    before = snap()
    lb.check(repo["root"], ["a1", "b1", "side"],
             gh=gh_main_at(FAKE_SHA, [repo["base"], side], tree))
    assert snap() == before


# ---------------------------------------------------------------- CLI
def test_cli_usage_error_exits_2(repo, monkeypatch):
    monkeypatch.chdir(repo["root"])
    assert lb.main([]) == 2
    assert lb.main(["--bogus-flag"]) == 2


def test_cli_exit_codes_follow_readiness(repo, monkeypatch, capsys):
    branch(repo, "ok1", {"f.py": "x = 1\n"})
    branch(repo, "bad1", {"g.py": "x = 1\n"}, upstream="origin/main")
    monkeypatch.setattr(lb, "gh_json", gh_local_main(repo))
    monkeypatch.setattr(lb.check, "__defaults__", (False, gh_local_main(repo)))
    assert lb.main(["--repo-root", str(repo["root"]), "ok1"]) == 0
    assert lb.main(["--repo-root", str(repo["root"]), "ok1", "bad1", "--json"]) == 1
    rep = json.loads(capsys.readouterr().out.split("ALL READY")[-1].strip())
    assert by_name(rep)["bad1"]["ready"] is False


# ---------------------------------------------------------------- PR pages (gh is read-only)
def _decode(url):
    from urllib.parse import parse_qs, urlsplit
    u = urlsplit(url)
    return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}


def test_ready_branch_gets_prefilled_compare_url(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    rep = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    (x,) = rep["landing"]
    path, q = _decode(x["compare_url"])
    assert x["compare_url"].startswith("https://github.com/owner/repo/compare/main...feat?")
    assert q["quick_pull"] == "1"
    assert q["title"] == "feat"                      # the branch's only commit subject
    assert "- feat" in q["body"] and lb.PR_FOOTER in q["body"]
    assert "gh pr create" not in lb.render(rep)
    assert x["compare_url"] in lb.render(rep)


def test_not_ready_branch_gets_no_push_command_or_url(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"}, upstream="origin/main")
    rep = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    assert rep["landing"] == [] and rep["push_commands"] == []


def test_conflict_pair_urls_carry_merge_order_note(repo):
    branch(repo, "a1", {"shared.json": '{\n  "a": 1,\n  "x": 1\n}\n'})
    branch(repo, "b1", {"shared.json": '{\n  "a": 1,\n  "y": 1\n}\n'})
    rep = lb.check(repo["root"], ["a1", "b1"], gh=gh_local_main(repo))
    notes = {x["branch"]: _decode(x["compare_url"])[1]["body"] for x in rep["landing"]}
    assert "Merge before b1" in notes["a1"] and "shared.json" in notes["a1"]
    assert "Merge after a1" in notes["b1"]


def test_pushed_flag_true_only_when_github_head_equals_tip(repo):
    _, tip = branch(repo, "feat", {"f.py": "x = 1\n"})
    base_c = {repo["base"]: {"parents": [], "commit": {"tree": {"sha": "0" * 40}}}}
    same = lb.check(repo["root"], ["feat"], gh=gh_for({"main": repo["base"], "feat": tip}, base_c))
    assert same["landing"][0]["pushed"] is True
    assert "notes" not in by_name(same)["feat"]
    absent = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    assert absent["landing"][0]["pushed"] is False
