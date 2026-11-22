"""tools/land_branch.py: read-only landing check. Real temp git repos; gh, the push gate, the
audit record and the PII scan are stubs whose exit codes the test sets through env vars."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import land_branch as lb  # noqa: E402

FAKE_SHA = "ab" * 20     # a commit sha that is not in any object store


def sh(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


AUDIT_STUB = ("import json, os, sys\n"
              "open(os.environ['LB_ARGV_LOG'], 'a').write(json.dumps(['audit', *sys.argv[1:]]) + '\\n')\n"
              "sys.exit(int(os.environ.get('LB_AUDIT_RC', '0')))\n")
PII_STUB = """import json, os, sys
mode = os.environ.get('LB_PII_MODE', 'clean')
paths = sys.stdin.read().split()
open(os.environ['LB_ARGV_LOG'], 'a').write(json.dumps(['pii', *sys.argv[1:], *paths]) + '\\n')
r = {'scanned': 1, 'hits': [], 'ambiguous_unresolved': [], 'skipped': [], 'unreadable': []}
if mode == 'hit':
    r['hits'] = [{'file': 'x.py', 'tokens': ['Realname']}]
    print(json.dumps(r)); sys.exit(2)
if mode == 'ambiguous':
    r['ambiguous_unresolved'] = ['x.py:widget']
    print(json.dumps(r)); sys.exit(3)
if mode == 'private':
    r.update(scanned=0, skipped=[{'path': 'data/x.md', 'why': 'gitignored'}])
    print(json.dumps(r)); sys.exit(1)
if mode == 'unreadable':
    r.update(scanned=0, unreadable=[{'path': 'x.py', 'why': 'OSError'}], error='1 unreadable')
    print(json.dumps(r)); sys.exit(1)
if mode == 'garbage':
    print('not json'); sys.exit(0)
print(json.dumps(r)); sys.exit(0)
"""
SUITE_STUB = ("import json, os, sys\n"
              "open(os.environ['LB_ARGV_LOG'], 'a').write(json.dumps(['suite', *sys.argv[1:]]) + '\\n')\n"
              "sys.exit(int(os.environ.get('LB_SUITE_RC', '0')))\n")
HOOK_STUB = """#!/bin/bash
cat > "$LB_HOOK_LOG"
echo "waive=${CODEX_VERIFY_WAIVE:-unset}/${AUDIT_PII_WAIVE:-unset}/${PUSH_PII_OVERRIDE:-unset} remote=$1 url=$2" >> "$LB_HOOK_LOG"
if [ "${LB_GATE_RC:-0}" != 0 ]; then
  echo "prepush: checking"
  echo "BLOCKED: stub gate says no for refs/heads/x" >&2
  echo "  Fix: run the stub remedy" >&2
fi
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
    monkeypatch.setenv("LB_ARGV_LOG", str(tmp_path / "argv.log"))
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


WAIVERS = ("CODEX_VERIFY_WAIVE", "AUDIT_PII_WAIVE", "PUSH_PII_OVERRIDE")


def test_waiver_list_is_exactly_the_three_gate_overrides():
    # A literal list, so dropping one from the tool cannot silently shrink the test (Grok r2 F2).
    assert tuple(lb.WAIVER_ENV) == WAIVERS


@pytest.mark.parametrize("var", WAIVERS)
def test_gate_receives_git_refs_line_and_no_waiver(repo, monkeypatch, var):
    _, tip = branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv(var, "should never reach the hook")
    lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    log = Path(os.environ["LB_HOOK_LOG"]).read_text().splitlines()
    assert log[0] == f"refs/heads/feat {tip} refs/heads/feat {'0' * 40}"
    assert "waive=unset/unset/unset" in log[1]
    assert "remote=origin url=git@github.com:owner/repo.git" in log[1]


def test_gate_uses_remote_sha_when_branch_exists_on_github(repo):
    _, tip = branch(repo, "feat", {"f.py": "x = 1\n"})
    gh = gh_for({"main": repo["base"], "feat": repo["base"]},
                {repo["base"]: {"parents": [], "commit": {"tree": {"sha": "0" * 40}}}})
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh))["feat"]
    first = Path(os.environ["LB_HOOK_LOG"]).read_text().splitlines()[0]
    assert first == f"refs/heads/feat {tip} refs/heads/feat {repo['base']}"
    assert r["github_tip"] == repo["base"]
    assert r["ready"], r["refusals"]
    assert "the push fast-forwards" in r["notes"][0]


def test_gate_failure_refuses(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv("LB_GATE_RC", "1")
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    assert "push gate rc 1: BLOCKED: stub gate says no for refs/heads/x" in r["refusals"]
    assert r["steps"]["gate"]["reason"] == "BLOCKED: stub gate says no for refs/heads/x"


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
    assert "ambiguous PII (settled by audit record): x.py:widget" in lb.render(rep)


def test_ambiguous_pii_without_audit_record_refuses(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv("LB_PII_MODE", "ambiguous")
    monkeypatch.setenv("LB_AUDIT_RC", "1")
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    assert any(x.startswith("ambiguous PII matches") for x in r["refusals"])


def test_pii_scan_is_given_only_added_or_modified_files(repo, monkeypatch, tmp_path):
    wt, tip = branch(repo, "feat", {"new.py": "n = 1\n", "shared.json": '{"a": 5}\n'})
    seen = {}

    def fake_scan(t, w, files):
        seen["files"] = files
        return {"rc": 0, "ok": True, "hits": [], "ambiguous": []}
    monkeypatch.setattr(lb, "pii_scan", fake_scan)
    lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    assert sorted(seen["files"]) == ["new.py", "shared.json"]


def test_suite_skipped_with_notice_when_tool_absent(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    r = by_name(lb.check(repo["root"], ["feat"], want_suite=True, gh=gh_local_main(repo)))["feat"]
    assert r["ready"]
    assert r["steps"]["suite"]["rc"] is None and "suite step skipped" in r["steps"]["suite"]["note"]


def test_suite_failure_refuses_when_tool_present(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    (repo["root"] / "tools" / "suite_record.py").write_text(SUITE_STUB)
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
    # Grok r2 F1: with GitHub main unknown the checkers can never read as current.
    assert rep["checkers"]["current"] is False
    assert rep["checkers"]["warnings"][0].startswith("GitHub main unknown")
    assert "(NOT current main)" in lb.render(rep)


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


# ---------------------------------------------------------------- error paths (mutation round 1)
def test_run_reports_127_when_the_command_cannot_start():
    rc, out, err = lb.run(["/nonexistent/land-branch-binary"])
    assert rc == 127 and out == "" and "Error" in err


def test_run_sets_utf8_io_and_strips_waivers(monkeypatch):
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.setenv("AUDIT_PII_WAIVE", "x")
    rc, out, _ = lb.run([sys.executable, "-c",
                         "import os; print(os.environ.get('PYTHONIOENCODING'), "
                         "os.environ.get('AUDIT_PII_WAIVE'), "
                         "os.environ.get('PYTHONDONTWRITEBYTECODE'))"])
    assert (rc, out.strip()) == (0, "utf-8 None 1")


def test_tail_keeps_last_lines():
    assert lb.tail("a\nb\nc\n", 2) == "b\nc"


def _fake_gh(tmp_path, monkeypatch, body: str, rc: int = 0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "gh").write_text(f"#!/bin/bash\necho \"$@\" > {tmp_path}/gh.args\n"
                               f"printf '%s' '{body}'\nexit {rc}\n")
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def test_gh_json_parses_and_joins_pages(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch, '[{"a": 1}][{"a": 2}]')
    assert lb.gh_json("repos/o/r/x", tmp_path, paginate=True) == [{"a": 1}, {"a": 2}]
    assert (tmp_path / "gh.args").read_text().split() == ["api", "--paginate", "repos/o/r/x"]


def test_gh_json_single_page_unchanged(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch, '{"s": "x][y"}')
    assert lb.gh_json("repos/o/r/c", tmp_path) == {"s": "x][y"}
    assert (tmp_path / "gh.args").read_text().split() == ["api", "repos/o/r/c"]


def test_gh_json_failure_raises(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch, "", rc=1)
    with pytest.raises(RuntimeError, match="rc=1"):
        lb.gh_json("repos/o/r/c", tmp_path)


def test_github_state_without_main_raises(repo):
    with pytest.raises(RuntimeError, match="no refs/heads/main"):
        lb.github_state(repo["root"], "owner/repo", gh_for({"feat": repo["base"]}, {}))


def test_unreadable_github_falls_back_loudly(repo):
    m = lb.resolve_main_ref(repo["root"], None)
    assert (m["method"], m["ref"], m["github_main"]) == ("fallback", repo["base"], None)
    assert m["warnings"] == ["GitHub unreadable: checking against LOCAL origin/main, "
                             "which may be stale"]


def test_commit_tree_failure_falls_back(repo, monkeypatch):
    _, side = branch(repo, "side", {"side.txt": "s\n"})
    tree = sh(repo["root"], "merge-tree", "--write-tree", repo["base"], side).splitlines()[0]
    real = lb.git
    monkeypatch.setattr(lb, "git", lambda a, c: (1, "", "boom") if a[0] == "commit-tree"
                        else real(a, c))
    m = lb.resolve_main_ref(repo["root"], lb.github_state(
        repo["root"], "owner/repo", gh_main_at(FAKE_SHA, [repo["base"], side], tree)))
    assert (m["method"], m["ref"]) == ("fallback", repo["base"])
    assert any("commit-tree failed: boom" in w for w in m["warnings"])


def test_merge_check_bad_ref_is_an_error_not_clean(repo):
    m = lb.merge_check(repo["root"], repo["base"], "no-such-ref")
    assert m["clean"] is False and m["files"] == [] and m["error"]


def test_missing_hook_and_tools_each_refuse(repo):
    wt, _ = branch(repo, "feat", {"f.py": "x = 1\n"})
    hook = Path(sh(repo["root"], "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
    (hook / "pre-push").unlink()
    (repo["root"] / "tools" / "audit_pii_record.py").unlink()
    (repo["root"] / "tools" / "check_public_pii.py").unlink()
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    s = r["steps"]
    assert s["gate"]["error"] == "no pre-push hook installed" and s["gate"]["rc"] is None
    assert s["audit"]["error"].startswith("missing ") and s["audit"]["ok"] is False
    assert s["pii"]["error"].startswith("missing ") and s["pii"]["ok"] is False
    assert "push gate rc None: no pre-push hook installed" in r["refusals"]
    assert any(x.startswith("PII scan rc None: missing ") for x in r["refusals"])


def test_changed_files_bad_base_is_empty(repo):
    assert lb.changed_files(repo["root"], "no-such-ref", repo["base"]) == []
    assert lb.commit_subjects(repo["root"], "no-such-ref", repo["base"]) == []


@pytest.mark.parametrize("mode,ok,key,value", [
    ("private", True, "note", "no public A/M files"),
    ("unreadable", False, "error", "1 unreadable"),
    ("garbage", False, "error", "scan output not JSON: not json"),
    ("clean", True, "note", None),
])
def test_pii_scan_result_shapes(repo, monkeypatch, mode, ok, key, value):
    wt, _ = branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv("LB_PII_MODE", mode)
    res = lb.pii_scan(repo["root"] / "tools", wt, ["f.py"])
    assert res["ok"] is ok
    assert res.get(key) == value


def test_pii_scan_with_no_files_is_ok_without_running():
    res = lb.pii_scan(Path("/nonexistent"), Path("/nonexistent"), [])
    assert res["ok"] is True and res["rc"] is None and res["note"] == "no A/M files"


def test_unreadable_pii_scan_refuses_with_rc(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv("LB_PII_MODE", "unreadable")
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    assert "PII scan rc 1: 1 unreadable" in r["refusals"]


def test_main_refusal_text(repo):
    r = by_name(lb.check(repo["root"], ["main"], gh=gh_local_main(repo)))["main"]
    assert r["refusals"] == ["refusing to land main itself"]


def test_unresolvable_branch_refuses(repo):
    wt, _ = branch(repo, "feat", {"f.py": "x = 1\n"})
    r = lb.check_branch(repo["root"], "ghost", {"ghost": str(wt)}, None, repo["base"], "", False)
    assert r["refusals"] == ["branch does not resolve"] and "tip" not in r


def test_git_status_failure_refuses(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    real = lb.git
    monkeypatch.setattr(lb, "git", lambda a, c: (128, "", "status broke") if "status" in a[:2]
                        else real(a, c))
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    assert "git status failed in worktree: status broke" in r["refusals"]


def test_upstream_recorded_or_none(repo):
    branch(repo, "a1", {"f.py": "x = 1\n"})
    branch(repo, "b1", {"g.py": "x = 1\n"}, upstream="origin/main")
    rep = by_name(lb.check(repo["root"], ["a1", "b1"], gh=gh_local_main(repo)))
    assert rep["a1"]["upstream"] is None and rep["b1"]["upstream"] == "origin/main"


def test_no_main_ref_refuses(repo):
    wt, _ = branch(repo, "feat", {"f.py": "x = 1\n"})
    r = lb.check_branch(repo["root"], "feat", {"feat": str(wt)}, None, None, "", False)
    assert "no main ref to check against" in r["refusals"]
    assert "merge_main" not in r["steps"]


def test_suite_step_absent_unless_asked(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    (repo["root"] / "tools" / "suite_record.py").write_text(SUITE_STUB)
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    assert "suite" not in r["steps"]


def test_pr_body_without_conflict_has_no_merge_note(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    rep = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    assert "Merge note" not in _decode(rep["landing"][0]["compare_url"])[1]["body"]


def test_conflict_pair_listed_when_either_side_is_ready(repo):
    branch(repo, "a1", {"shared.json": '{\n  "a": 1,\n  "x": 1\n}\n'}, upstream="origin/main")
    branch(repo, "b1", {"shared.json": '{\n  "a": 1,\n  "y": 1\n}\n'}, upstream="origin/main")
    branch(repo, "c1", {"shared.json": '{\n  "a": 1,\n  "z": 1\n}\n'})
    rep = lb.check(repo["root"], ["a1", "b1", "c1"], gh=gh_local_main(repo))
    assert len(rep["conflicts"]) == 3
    assert [(p["a"], p["b"]) for p in rep["merge_order"]["pairs"]] == [("a1", "c1"), ("b1", "c1")]


def test_non_github_origin_is_a_loud_github_failure(repo):
    sh(repo["root"], "remote", "set-url", "origin", "git@gitlab.com:o/r.git")
    branch(repo, "feat", {"f.py": "x = 1\n"})
    rep = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    assert rep["main"]["warnings"][0].startswith("GitHub read failed: origin is not a GitHub URL")
    assert not rep["all_ready"]


# ---------------------------------------------------------------- the printed report, exactly
def test_render_exact():
    rep = {
        "main": {"github_main": "g" * 40, "local_origin_main": "l" * 40, "ref": "r" * 40,
                 "method": "rebuilt", "warnings": ["W1"]},
        "checkers": {"path": "/m/tools", "head": "c" * 40, "dirty": [], "current": False,
                     "warnings": ["C1"]},
        "branches": [
            {"branch": "ok1", "tip": "a" * 40, "ready": True, "refusals": [], "notes": ["N1"],
             "steps": {"merge_main": {"clean": True}, "gate": {"ok": True, "rc": 0},
                       "pii": {"ok": True, "rc": 3, "hits": [], "ambiguous": ["x.py:widget"]},
                       "audit": {"ok": True, "rc": 0},
                       "suite": {"ok": True, "rc": None, "note": "skipped note"}}},
            {"branch": "bad1", "tip": "b" * 40, "ready": False, "refusals": ["R1"],
             "steps": {"pii": {"ok": True, "rc": 2, "hits": ["y.py: Tok"],
                               "ambiguous": ["y.py:gadget"]}}},
        ],
        "conflicts": [{"a": "ok1", "b": "bad1", "files": ["m.json"]}],
        "merge_order": {"first": ["c1"], "pairs": [{"a": "ok1", "b": "bad1", "files": ["m.json"]}]},
        "landing": [{"push": "P1", "compare_url": "U1", "pushed": True},
                    {"push": "P2", "compare_url": "U2", "pushed": False}],
        "all_ready": False,
    }
    expected = "\n".join([
        f"GitHub main: {'g' * 40}",
        f"local origin/main: {'l' * 40}",
        f"checked against: {'r' * 40} (rebuilt)",
        f"checkers: /m/tools at {'c' * 40} (NOT current main)",
        "!!! W1",
        "!!! C1",
        "",
        "READY      ok1  aaaaaaaa  merge_main=ok gate=ok(rc 0) pii=ok(rc 3) audit=ok(rc 0) suite=ok",
        "           note: N1",
        "           ambiguous PII (settled by audit record): x.py:widget",
        "           suite: skipped note",
        "NOT READY  bad1  bbbbbbbb  pii=ok(rc 2)",
        "           - R1",
        "           PII hits: y.py: Tok",
        "           ambiguous PII (UNSETTLED): y.py:gadget",
        "",
        "Pairwise conflicts:",
        "  ok1 x bad1: m.json",
        "",
        "Merge order (on GitHub, no rebase):",
        "  1. c1",
        "  then ok1, then bad1 (conflict in m.json; resolve the second after the first lands)",
        "A hand-resolved conflict is not landed until the merged file is checked on GitHub main:",
        "  - m.json: parses as JSON (python3 -m json.tool) and keeps every key from both sides",
        "",
        "scope: branches named on the command line | population 2 | included 1 | excluded: not ready 1",
        "Push commands and PR pages, one per ready branch (gh is read-only by design, so open each page and click Create):",
        "  P1  [already on GitHub at this tip]",
        "    U1",
        "  P2",
        "    U2",
        "",
        "NOT ALL READY",
    ])
    assert lb.render(rep) == expected


def test_render_exact_no_conflicts_github_down():
    rep = {"main": {"github_main": None, "local_origin_main": "l" * 40, "ref": "l" * 40,
                    "method": "fallback", "warnings": []},
           "checkers": {"path": "/m/tools", "head": "l" * 40, "dirty": [], "current": True,
                        "warnings": []},
           "branches": [{"branch": "ok1", "tip": "a" * 40, "ready": True, "refusals": [],
                         "steps": {"pii": {"ok": True, "rc": 0, "hits": [], "ambiguous": []},
                                   "suite": {"ok": False, "rc": 1}}}],
           "conflicts": [], "merge_order": {"first": ["ok1"], "pairs": []},
           "landing": [], "all_ready": True}
    assert lb.render(rep) == "\n".join([
        "GitHub main: UNREADABLE", f"local origin/main: {'l' * 40}",
        f"checked against: {'l' * 40} (fallback)",
        f"checkers: /m/tools at {'l' * 40} (current)", "",
        "READY      ok1  aaaaaaaa  pii=ok(rc 0) suite=FAIL(rc 1)", "",
        "Pairwise conflicts: none", "",
        "Merge order (on GitHub, no rebase):", "  1. ok1", "",
        "scope: branches named on the command line | population 1 | included 1 | excluded: not ready 0",
        "Push commands and PR pages, one per ready branch (gh is read-only by design, so open each page and click Create):",
        "", "ALL READY"])


@pytest.mark.parametrize("files,line", [
    (["a.json"], "  - a.json: parses as JSON (python3 -m json.tool) and keeps every key from both sides"),
    (["b.py"], "  - b.py: keeps both sides' changes and its tests pass"),
])
def test_post_merge_check_lines(files, line):
    out = lb.post_merge_checks([{"a": "x", "b": "y", "files": files}])
    assert out[1] == line and len(out) == 2
    assert lb.post_merge_checks([]) == []


def test_cli_messages_and_script_entry(repo, tmp_path, capsys):
    assert lb.main([]) == 2
    assert "usage: land_branch.py" in capsys.readouterr().err
    plain = tmp_path / "plain"
    plain.mkdir()
    assert lb.main(["--repo-root", str(plain), "x"]) == 2
    assert f"not a git repository: {plain}" in capsys.readouterr().err
    p = subprocess.run([sys.executable, str(Path(lb.__file__)), "--repo-root", str(plain), "x"],
                       capture_output=True, text=True)
    assert p.returncode == 2 and "not a git repository" in p.stderr


def test_cli_prints_the_report(repo, monkeypatch, capsys):
    branch(repo, "ok1", {"f.py": "x = 1\n"})
    monkeypatch.setattr(lb.check, "__defaults__", (False, gh_local_main(repo)))
    assert lb.main(["--repo-root", str(repo["root"]), "ok1"]) == 0
    assert capsys.readouterr().out.rstrip().endswith("ALL READY")


# ---------------------------------------------------------------- mutation round 2 survivors
@pytest.mark.parametrize("text,reason", [
    ("ok line\nBLOCKED: first why\nBLOCKED: second\n  Fix: x", "BLOCKED: first why"),
    ("\n  first plain line\nlast", "first plain line"),
    ("", "(no output)"),
])
def test_gate_reason(text, reason):
    assert lb.gate_reason(text) == reason


def test_passing_gate_has_no_reason(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    assert r["steps"]["gate"]["reason"] is None


def test_failed_merge_base_never_falls_through_to_head(repo):
    # With the merge-base empty, `..tip` would mean HEAD..tip and list the branch's files.
    _, tip = branch(repo, "feat", {"f.py": "x = 1\n"})
    assert lb.changed_files(repo["root"], "no-such-ref", tip) == []
    assert lb.commit_subjects(repo["root"], "no-such-ref", tip) == []
    assert lb.changed_files(repo["root"], repo["base"], tip) == ["f.py"]
    assert lb.commit_subjects(repo["root"], repo["base"], tip) == ["feat"]


@pytest.mark.parametrize("mode", ["clean", "private", "ambiguous"])
def test_ok_pii_scan_carries_no_error(repo, monkeypatch, mode):
    wt, _ = branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setenv("LB_PII_MODE", mode)
    res = lb.pii_scan(repo["root"] / "tools", wt, ["f.py"])
    assert res["ok"] is True and "error" not in res


def test_readable_github_adds_no_warning(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    rep = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    assert rep["main"]["warnings"] == []


# ---------------------------------------------------------------- cross-model round 1
def short_(sha):
    return sha[:8]


def test_branch_cannot_judge_itself_with_its_own_tools(repo, monkeypatch):
    """Codex r1 F1 (P0): a branch that rewrites its own audit, PII and suite tools must still
    be judged by the main checkout's copies."""
    forged_audit = "import sys\nsys.exit(0)\n"
    forged_pii = ("import json, sys\nsys.stdin.read()\nprint(json.dumps({'scanned': 1, "
                  "'hits': [], 'ambiguous_unresolved': []}))\n")
    branch(repo, "feat", {"tools/audit_pii_record.py": forged_audit,
                          "tools/check_public_pii.py": forged_pii,
                          "tools/suite_record.py": "import sys\nsys.exit(0)\n"})
    (repo["root"] / "tools" / "suite_record.py").write_text(SUITE_STUB)
    monkeypatch.setenv("LB_AUDIT_RC", "1")
    monkeypatch.setenv("LB_PII_MODE", "hit")
    monkeypatch.setenv("LB_SUITE_RC", "1")
    r = by_name(lb.check(repo["root"], ["feat"], want_suite=True, gh=gh_local_main(repo)))["feat"]
    assert not r["ready"]
    assert "audit_pii_record check rc 1" in r["refusals"]
    assert "PII denylist hits: 1" in r["refusals"]
    assert "suite_record check rc 1" in r["refusals"]
    trusted = str((repo["root"] / "tools").resolve())
    assert all(str(Path(r["steps"][k]["tool"]).resolve()).startswith(trusted)
               for k in ("audit", "suite"))


def test_trusted_tools_get_the_worktree_and_tip(repo):
    wt, tip = branch(repo, "feat", {"f.py": "x = 1\n"})
    (repo["root"] / "tools" / "suite_record.py").write_text(SUITE_STUB)
    lb.check(repo["root"], ["feat"], want_suite=True, gh=gh_local_main(repo))
    rows = [json.loads(x) for x in Path(os.environ["LB_ARGV_LOG"]).read_text().splitlines()]
    assert ["audit", "--repo-root", str(wt), "check", "--tip", tip] in rows
    assert ["pii", "--scan", "--stdin-paths", str(wt / "f.py")] in rows
    assert ["suite", "check", "--tip", tip] in rows


def test_trusted_tools_resolve_to_main_checkout_from_a_worktree(repo):
    wt, _ = branch(repo, "feat", {"f.py": "x = 1\n"})
    assert lb.trusted_tools(wt).resolve() == (repo["root"] / "tools").resolve()
    assert lb.trusted_tools(repo["tmp"]) is None      # never the candidate's own tools/


def test_unresolved_trusted_tools_refuse_every_checker(repo, monkeypatch):
    """Codex r2 F2: no fallback to the candidate's tools/ when the common dir is unknown."""
    branch(repo, "feat", {"f.py": "x = 1\n"})
    monkeypatch.setattr(lb, "trusted_tools", lambda root: None)
    rep = lb.check(repo["root"], ["feat"], want_suite=True, gh=gh_local_main(repo))
    r = by_name(rep)["feat"]
    assert [r["steps"][k]["error"] for k in ("audit", "pii", "suite")] == [lb.NO_TRUSTED] * 3
    assert "audit_pii_record check rc None" in r["refusals"]
    assert f"PII scan rc None: {lb.NO_TRUSTED}" in r["refusals"]
    assert "suite_record check rc None" in r["refusals"]
    c = rep["checkers"]
    assert (c["path"], c["current"], c["warnings"]) == (None, False, [lb.NO_TRUSTED])
    assert "checkers: UNRESOLVED at ? (NOT current main)" in lb.render(rep)


def test_unreadable_checker_tools_status_is_never_current(repo, monkeypatch):
    """Codex r2 F1: a failed git status on the checkers' tools/ must not read as clean."""
    real = lb.git
    monkeypatch.setattr(lb, "git", lambda a, c: (128, "", "index locked")
                        if a[:2] == ["--no-optional-locks", "status"] and a[-1] == "tools"
                        else real(a, c))
    c = lb.checker_provenance(repo["root"] / "tools", {"github_main": repo["base"]})
    assert c["current"] is False and c["dirty"] == []
    assert c["warnings"] == [f"could not inspect {repo['root']}/tools for uncommitted changes "
                             "(git status rc 128): index locked"]


def test_diverged_github_branch_is_refused_as_non_fast_forward(repo):
    _, side = branch(repo, "side", {"side.txt": "s\n"})
    branch(repo, "feat", {"f.py": "x = 1\n"})
    base_c = {repo["base"]: {"parents": [], "commit": {"tree": {"sha": "0" * 40}}}}
    for remote in (side, FAKE_SHA):
        r = by_name(lb.check(repo["root"], ["feat"],
                             gh=gh_for({"main": repo["base"], "feat": remote}, base_c)))["feat"]
        assert not r["ready"]
        assert any("rejected as non-fast-forward" in x and short_(remote) in x
                   for x in r["refusals"]), r["refusals"]


def test_fallback_against_stale_main_refuses_every_branch(repo):
    _, side = branch(repo, "side", {"side.txt": "s\n"})
    branch(repo, "feat", {"f.py": "x = 1\n"})
    rep = lb.check(repo["root"], ["feat"],
                   gh=gh_main_at(FAKE_SHA, [repo["base"], side], "1" * 40))
    assert rep["main"]["method"] == "fallback"
    r = by_name(rep)["feat"]
    assert any(x.startswith(f"checked against local origin/main {short_(repo['base'])}, not "
                            f"GitHub main {short_(FAKE_SHA)}") for x in r["refusals"])
    assert not rep["all_ready"] and rep["landing"] == []


def test_github_main_present_locally_is_not_stale(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    rep = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    assert rep["main"]["method"] == "local"
    assert by_name(rep)["feat"]["ready"]


def test_upstream_main_refused_even_without_a_tracking_ref(repo):
    wt, _ = branch(repo, "feat", {"f.py": "x = 1\n"})
    sh(wt, "config", "branch.feat.remote", "nosuchremote")
    sh(wt, "config", "branch.feat.merge", "refs/heads/main")
    r = by_name(lb.check(repo["root"], ["feat"], gh=gh_local_main(repo)))["feat"]
    assert r["upstream"] == "nosuchremote/main"
    assert any(x.startswith("upstream is nosuchremote/main") for x in r["refusals"])


def test_upstream_without_remote_config_is_local(repo):
    wt, _ = branch(repo, "feat", {"f.py": "x = 1\n"})
    sh(wt, "config", "branch.feat.merge", "refs/heads/main")
    assert lb.configured_upstream(wt, "feat") == "./main"


def test_gate_gets_the_push_url_not_the_fetch_url(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    sh(repo["root"], "remote", "set-url", "--push", "origin", "git@github.com:owner/repo-push.git")
    lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    assert "url=git@github.com:owner/repo-push.git" in Path(os.environ["LB_HOOK_LOG"]).read_text()


def test_merge_check_rc1_without_a_tree_line_is_an_error(repo, monkeypatch):
    monkeypatch.setattr(lb, "git", lambda a, c: (1, "fatal: something odd\n", ""))
    m = lb.merge_check(repo["root"], "a", "b")
    assert m["clean"] is False and m["error"] == "merge-tree rc 1"


def test_status_runs_without_optional_locks(repo, monkeypatch):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    seen = []
    real = lb.git
    monkeypatch.setattr(lb, "git", lambda a, c: (seen.append(a), real(a, c))[1])
    lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    assert ["--no-optional-locks", "status", "--porcelain", "--untracked-files=no"] in seen


# ---------------------------------------------------------------- checker provenance (b2, r2 bar)
def test_checkers_current_when_main_checkout_is_github_main(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    rep = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))
    c = rep["checkers"]
    assert (c["head"], c["current"], c["warnings"], c["dirty"]) == (repo["base"], True, [], [])
    body = _decode(rep["landing"][0]["compare_url"])[1]["body"]
    assert f"(checkers at {short_(repo['base'])})" in body
    assert "NOT current" not in lb.render(rep)


def test_checkers_behind_github_main_warn_and_say_so_in_pr_text(repo):
    _, side = branch(repo, "side", {"side.txt": "s\n"})
    tree = sh(repo["root"], "merge-tree", "--write-tree", repo["base"], side).splitlines()[0]
    branch(repo, "feat", {"f.py": "x = 1\n"})
    rep = lb.check(repo["root"], ["feat"], gh=gh_main_at(FAKE_SHA, [repo["base"], side], tree))
    c = rep["checkers"]
    assert c["current"] is False
    assert c["warnings"] == [
        f"checkers (audit, PII, suite) ran from {repo['root']} at {short_(repo['base'])}, not "
        f"GitHub main {short_(FAKE_SHA)}: those results come from that older or different copy"]
    out = lb.render(rep)
    assert f"!!! {c['warnings'][0]}" in out and "(NOT current main)" in out
    body = _decode(rep["landing"][0]["compare_url"])[1]["body"]
    assert f"checkers at {short_(repo['base'])}, NOT current main" in body


def test_checkers_with_uncommitted_tool_changes_warn(repo):
    branch(repo, "feat", {"f.py": "x = 1\n"})
    p = repo["root"] / "tools" / "audit_pii_record.py"
    p.write_text(p.read_text() + "# local edit\n")
    c = lb.check(repo["root"], ["feat"], gh=gh_local_main(repo))["checkers"]
    assert c["dirty"] == ["tools/audit_pii_record.py"] and c["current"] is False
    assert c["warnings"] == [f"checkers ran with uncommitted changes in {repo['root']}/tools: "
                             "tools/audit_pii_record.py"]


def test_checkers_outside_a_repo_are_never_current(tmp_path):
    c = lb.checker_provenance(tmp_path / "tools", {"github_main": "a" * 40, "ref": "b" * 40})
    assert c["head"] is None and c["current"] is False
    assert "at ?, not GitHub main aaaaaaaa" in c["warnings"][0]


def test_module_loads_by_path_without_tools_on_sys_path(tmp_path):
    """The count_report import must not depend on the caller's sys.path (mutation r7)."""
    code = ("import importlib.util, sys; "
            f"spec = importlib.util.spec_from_file_location('lbx', {str(Path(lb.__file__))!r}); "
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
            "print(m.format_line('x', 1, 1, {}))")
    p = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "scope: x | population 1 | included 1 | excluded: none"


# ================================================================ --merge-push (2026-11-18)
# Nick's rulings: no PRs; allowlist conflicts resolved by allowlist_merge, any other conflict
# holds that branch; SSH fetch only (stale main prints the fetch command); full suite on the
# final merged tree, 0 failures. Brief: output/overnight-merge-back/111826-merge-push-build-brief.md
import merge_chain as mc  # noqa: E402

MP_HOOK = """#!/bin/bash
in="$(cat)"
printf '%s\\n' "$in" >> "$LB_HOOK_LOG"
case "$in" in
  *refs/heads/main*)
    root="$(git rev-parse --show-toplevel)"
    { echo "PUSHGATE root=$root url=$2 stdin=$in"
      cat "$root/tools/.cross-model-ledger.jsonl" 2>/dev/null
      cat "$root/tools/.audit-pii-ledger.jsonl" 2>/dev/null; } >> "$LB_PUSH_LOG"
    if [ "${LB_PUSH_GATE_RC:-0}" != 0 ]; then echo "BLOCKED: stub push gate says no" >&2; fi
    exit "${LB_PUSH_GATE_RC:-0}";;
esac
exit "${LB_GATE_RC:-0}"
"""
GOOD_BODY = "## Summary\n- does the thing\n\n" + lb.PR_FOOTER + "\n"


@pytest.fixture
def mp(repo, monkeypatch):
    """repo plus a tracked hook identical to the installed one, gitignored ledgers, a
    denylist, and a suite stub: a main checkout whose checkers are current."""
    root = repo["root"]
    (root / ".gitignore").write_text("tools/.*\noutput/\n")
    (root / "tools" / "hooks").mkdir()
    (root / "tools" / "hooks" / "pre-push").write_text(MP_HOOK)
    (root / "tools" / "hooks" / "pre-push").chmod(0o755)
    (root / "tools" / "suite_record.py").write_text(SUITE_STUB)
    sh(root, "add", "-A")
    sh(root, "commit", "-qm", "checkers")
    base = sh(root, "rev-parse", "HEAD")
    sh(root, "update-ref", "refs/remotes/origin/main", base)
    repo["base"] = base
    installed = Path(sh(root, "rev-parse", "--path-format=absolute", "--git-path", "hooks")) / "pre-push"
    installed.write_text(MP_HOOK)
    (root / "tools" / ".pii-denylist.txt").write_text("Realname\n")
    monkeypatch.setenv("LB_PUSH_LOG", str(repo["tmp"] / "push.log"))
    monkeypatch.delenv("LB_PUSH_GATE_RC", raising=False)
    return repo


def describe(repo, name, title=None, body=GOOD_BODY):
    d = repo["root"] / "output" / "landing-descriptions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(f"{title or 'Add ' + name}\n\n{body}")


def ready_branch(repo, name, files):
    wt, tip = branch(repo, name, files)
    describe(repo, name)
    return wt, tip


def run_mp(repo, names, dry_run=True, **kw):
    kw.setdefault("busy", lambda: [])
    if dry_run:     # a dry run judges with the tools next to land_branch.py; use the stubs
        kw.setdefault("checkers", repo["root"] / "tools")
    kw.setdefault("gh", gh_local_main(repo))
    return lb.merge_push(repo["root"], names, dry_run=dry_run, **kw)


def held(rep):
    return {h["branch"]: h["reasons"] for h in rep["held"]}


def test_mp_dry_run_builds_a_verified_chain_and_one_push_command(mp):
    _, t1 = ready_branch(mp, "one", {"one.py": "x = 1\n"})
    _, t2 = ready_branch(mp, "two", {"two.py": "y = 2\n"})
    rep = run_mp(mp, ["one", "two"])
    assert rep["ok"], rep
    assert [b["branch"] for b in rep["built"]] == ["one", "two"]
    chain = mc.walk(mp["root"], mp["base"], rep["chain_tip"])
    assert [m.p2 for m in chain] == [t1, t2]
    root = mp["root"]
    assert rep["commands"] == [
        f"! git -C {root} push origin {rep['chain_tip']}:refs/heads/main",
        f"! git -C {root} merge --ff-only {rep['chain_tip']}",
        f"! PYTHONIOENCODING=utf-8 python3 {root}/tools/land_branch.py --verify-landed "
        f"{mp['base']}..{rep['chain_tip']}",
    ]
    assert sum("push origin" in c for c in rep["commands"]) == 1


def test_mp_merge_message_is_the_reviewed_description(mp):
    _, t1 = ready_branch(mp, "one", {"one.py": "x = 1\n"})
    describe(mp, "one", title="Add the one feature")
    rep = run_mp(mp, ["one"])
    msg = sh(mp["root"], "log", "-1", "--format=%B", rep["chain_tip"])
    assert msg.splitlines()[0] == "Add the one feature"
    assert "- does the thing" in msg
    assert f"Merge-push: one {t1}" in msg
    assert msg.rstrip().endswith(lb.PR_FOOTER)


def test_mp_push_gate_gets_main_refspec_line_and_push_url(mp):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    rep = run_mp(mp, ["one"])
    log = Path(os.environ["LB_PUSH_LOG"]).read_text()
    tip = rep["chain_tip"]
    assert f"stdin={tip} {tip} refs/heads/main {mp['base']}" in log
    assert "url=git@github.com:owner/repo.git" in log
    assert rep["gate"]["ok"] is True


def test_mp_gate_block_refuses_with_reason(mp, monkeypatch):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    monkeypatch.setenv("LB_PUSH_GATE_RC", "1")
    rep = run_mp(mp, ["one"])
    assert not rep["ok"]
    assert rep["commands"] == []
    assert any("BLOCKED: stub push gate says no" in r for r in rep["refusals"])


def test_mp_holds_branch_without_description_and_lands_rest(mp):
    branch(mp, "nodesc", {"n.py": "n = 1\n"})
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    rep = run_mp(mp, ["nodesc", "one"])
    assert rep["ok"]
    assert [b["branch"] for b in rep["built"]] == ["one"]
    assert any("no reviewed description" in r for r in held(rep)["nodesc"])


@pytest.mark.parametrize("title,body,why", [
    ("x" * 73, GOOD_BODY, "longer than 72"),
    (" Leading space", GOOD_BODY, "whitespace"),
    ("Has an em dash — here", GOOD_BODY, "em dash"),
    ("Fine title", "## Summary\n- no footer\n", "footer"),
    ("Fine title", lb.PR_FOOTER + "\n", "body"),
    ("Mentions Realname", GOOD_BODY, "denylist"),
])
def test_mp_description_checks_hold_the_branch(mp, title, body, why):
    branch(mp, "one", {"one.py": "x = 1\n"})
    describe(mp, "one", title=title, body=body)
    rep = run_mp(mp, ["one"])
    assert rep["built"] == []
    assert any(why in r for r in held(rep)["one"]), held(rep)


def test_mp_description_check_holds_when_no_denylist(mp):
    (mp["root"] / "tools" / ".pii-denylist.txt").unlink()
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    rep = run_mp(mp, ["one"])
    assert any("denylist" in r for r in held(rep)["one"])


def test_mp_not_ready_branch_is_held_with_its_refusals(mp):
    wt, _ = ready_branch(mp, "dirty", {"d.py": "d = 1\n"})
    (wt / "d.py").write_text("changed\n")
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    rep = run_mp(mp, ["dirty", "one"])
    assert [b["branch"] for b in rep["built"]] == ["one"]
    assert any("uncommitted changes" in r for r in held(rep)["dirty"])


def test_mp_real_conflict_holds_the_later_branch_only(mp):
    ready_branch(mp, "first", {"shared.json": '{\n  "a": 2\n}\n'})
    ready_branch(mp, "second", {"shared.json": '{\n  "a": 3\n}\n'})
    rep = run_mp(mp, ["first", "second"])
    assert rep["ok"]
    assert [b["branch"] for b in rep["built"]] == ["first"]
    assert any("shared.json" in r for r in held(rep)["second"])


def test_mp_allowlist_conflict_is_resolved_not_held(mp):
    a = mc.ALLOWLIST
    ready_branch(mp, "one", {a: '{\n  "k::one": "r1"\n}\n'})
    ready_branch(mp, "two", {a: '{\n  "k::two": "r2"\n}\n'})
    rep = run_mp(mp, ["one", "two"])
    assert rep["ok"], rep
    assert [b["resolved_allowlist"] for b in rep["built"]] == [False, True]
    text = sh(mp["root"], "show", f"{rep['chain_tip']}:{a}")
    assert json.loads(text) == {"k::one": "r1", "k::two": "r2"}
    assert rep["verify_resolved"] == [a]


def test_mp_allowlist_conflict_with_main_is_not_a_refusal(mp):
    a = mc.ALLOWLIST
    root = mp["root"]
    (root / a).write_text('{\n  "k::main": "rm"\n}\n')
    sh(root, "add", a)
    sh(root, "commit", "-qm", "main allowlist")
    old_base = mp["base"]
    # the branch forks BEFORE main added its key, so its own key conflicts with main's lines
    mp["base"] = old_base
    _, tip = ready_branch(mp, "one", {a: '{\n  "k::one": "r1"\n}\n'})
    new_main = sh(root, "rev-parse", "HEAD")
    sh(root, "update-ref", "refs/remotes/origin/main", new_main)
    mp["base"] = new_main
    rep = run_mp(mp, ["one"], gh=gh_main_at(new_main, [old_base], sh(root, "rev-parse", f"{new_main}^{{tree}}")))
    assert rep["ok"], rep
    assert json.loads(sh(root, "show", f"{rep['chain_tip']}:{a}")) == {"k::main": "rm", "k::one": "r1"}


def test_mp_refuses_when_github_main_is_not_local_and_prints_fetch(mp):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    rep = run_mp(mp, ["one"], gh=gh_main_at(FAKE_SHA, [mp["base"], FAKE_SHA]))
    assert not rep["ok"]
    assert rep["commands"] == []
    assert rep["fetch_command"] == f"! git -C {mp['root']} fetch origin main"
    assert any("not in the local object store" in r for r in rep["refusals"])


def test_mp_refuses_when_another_push_is_running(mp):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    rep = run_mp(mp, ["one"], busy=lambda: ["4242 00:03 ssh git@github.com git-receive-pack"])
    assert not rep["ok"]
    assert any("another push" in r and "4242" in r for r in rep["refusals"])


def test_busy_processes_matches_pushes_and_github_ssh_only():
    ps = ("  PID ELAPSED COMMAND\n"
          "  101 00:05 ssh git@github.com git-receive-pack 'o/r.git'\n"
          "  102 00:01 git push origin main\n"
          "  103 00:01 git -C /x fetch origin main\n"
          "  104 00:09 /Applications/Secretive.app/.../SecretAgent\n"
          "  105 00:01 vim notes-about-git-push.md\n"
          "  106 00:01 python3 land_branch.py --merge-push\n")
    got = lb.busy_processes(ps_output=ps)
    assert [ln.split()[0] for ln in got] == ["101", "102", "103"]


def test_mp_real_refuses_when_checkers_are_not_current(mp):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    (mp["root"] / "tools" / "suite_record.py").write_text("# edited\n")
    rep = run_mp(mp, ["one"], dry_run=False)
    assert not rep["ok"]
    assert any("checkers" in r for r in rep["refusals"])


def test_mp_dry_run_does_not_need_current_checkers(mp):
    """A dry run judges with the tools next to land_branch.py, so a new gate can be tried
    before it lands; the main checkout's tools being behind is not a reason to refuse."""
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    (mp["root"] / "tools" / "suite_record.py").write_text("# edited\n")
    rep = run_mp(mp, ["one"], dry_run=True)
    assert not any("checkers" in r for r in rep["refusals"]), rep["refusals"]
    assert [b["branch"] for b in rep["built"]] == ["one"]


def test_mp_real_refuses_when_installed_hook_differs_from_tracked(mp):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    installed = Path(sh(mp["root"], "rev-parse", "--path-format=absolute", "--git-path",
                        "hooks")) / "pre-push"
    installed.write_text(MP_HOOK + "# older copy\n")
    rep = run_mp(mp, ["one"], dry_run=False)
    assert not rep["ok"]
    assert any("install.sh" in r for r in rep["refusals"])


def _rows(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def seed_branch_ledgers(wt, tip):
    tree_ = sh(wt, "rev-parse", f"{tip}^{{tree}}")
    review = {"report": "r.md", "model": "codex", "recorded": "2026-11-18T10:00:00+00:00",
              "tree": tree_, "paths": ["one.py"], "findings": []}
    audit = {"kind": "audit", "verdict": "clean", "tree": tree_, "tip": tip,
             "recorded": "2026-11-18T10:00:00+00:00"}
    (wt / "tools" / ".cross-model-ledger.jsonl").write_text(json.dumps(review) + "\n")
    (wt / "tools" / ".audit-pii-ledger.jsonl").write_text(json.dumps(audit) + "\n")
    return review, audit


def test_mp_dry_run_previews_with_backfill_but_writes_nothing(mp):
    wt, tip = ready_branch(mp, "one", {"one.py": "x = 1\n"})
    review, audit = seed_branch_ledgers(wt, tip)
    main_ledger = mp["root"] / "tools" / ".cross-model-ledger.jsonl"
    rep = run_mp(mp, ["one"])
    assert rep["ok"]
    assert not main_ledger.exists()
    assert not (mp["root"] / "tools" / ".audit-pii-ledger.jsonl").exists()
    log = Path(os.environ["LB_PUSH_LOG"]).read_text()
    assert review["tree"] in log and '"copied_from"' in log
    assert rep["backfill"] == {"cross": 1, "audit": 1, "written": False}


def test_mp_real_run_backfills_once_and_runs_suite_on_chain_tip(mp):
    wt, tip = ready_branch(mp, "one", {"one.py": "x = 1\n"})
    review, audit = seed_branch_ledgers(wt, tip)
    rep = run_mp(mp, ["one"], dry_run=False)
    assert rep["ok"], rep
    main_tools = mp["root"] / "tools"
    rows = _rows(main_tools / ".cross-model-ledger.jsonl")
    assert len(rows) == 1 and rows[0]["tree"] == review["tree"]
    assert rows[0]["copied_from"] == str(wt)
    arows = _rows(main_tools / ".audit-pii-ledger.jsonl")
    assert len(arows) == 1 and arows[0]["tree"] == audit["tree"]
    run_mp(mp, ["one"], dry_run=False)                       # idempotent
    assert len(_rows(main_tools / ".cross-model-ledger.jsonl")) == 1
    assert len(_rows(main_tools / ".audit-pii-ledger.jsonl")) == 1
    argv = [json.loads(x) for x in Path(os.environ["LB_ARGV_LOG"]).read_text().splitlines()]
    assert ["suite", "run", "--branch", rep["chain_tip"], "--base", mp["base"],
            "--repo-root", str(mp["root"])] in argv


def test_mp_suite_failure_refuses_and_shows_output(mp, monkeypatch):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    monkeypatch.setenv("LB_SUITE_RC", "1")
    rep = run_mp(mp, ["one"], dry_run=False)
    assert not rep["ok"]
    assert rep["commands"] == []
    assert rep["suite"]["ok"] is False and "rc" in rep["suite"]
    assert any("suite" in r for r in rep["refusals"])


def test_mp_nothing_built_is_not_ok(mp):
    branch(mp, "nodesc", {"n.py": "n = 1\n"})
    rep = run_mp(mp, ["nodesc"])
    assert not rep["ok"] and rep["chain_tip"] is None and rep["commands"] == []


def test_render_merge_push_lists_built_held_and_commands(mp):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    branch(mp, "nodesc", {"n.py": "n = 1\n"})
    rep = run_mp(mp, ["one", "nodesc"])
    text = lb.render_merge_push(rep)
    assert "BUILT  one" in text and "HELD   nodesc" in text
    assert text.count("push origin") == 1
    assert "DRY RUN" in text


def test_cli_merge_push_and_verify_landed_flags(mp, monkeypatch, capsys):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    monkeypatch.setattr(lb, "gh_json", gh_local_main(mp))
    monkeypatch.setattr(lb, "busy_processes", lambda: [])
    real = lb.merge_push
    monkeypatch.setattr(lb, "merge_push", lambda *a, **k: real(
        *a, **{**k, "checkers": mp["root"] / "tools"}))
    rc = lb.main(["--repo-root", str(mp["root"]), "--merge-push", "--dry-run", "one"])
    assert rc == 0
    assert "push origin" in capsys.readouterr().out
    assert lb.main(["--repo-root", str(mp["root"]), "--dry-run", "one"]) == 2


# ---------------------------------------------------------------- --verify-landed
def gh_landed(heads_main, behind: dict, allow_text=None):
    def gh(path, root, paginate=False):
        if "/compare/" in path:
            tip = path.split("/compare/")[1].split("...")[0]
            return {"behind_by": behind.get(tip, 0)}
        if "/contents/" in path and allow_text is not None:
            import base64
            return {"encoding": "base64",
                    "content": base64.b64encode(allow_text.encode()).decode()}
        raise RuntimeError("404 " + path)
    return gh


def built_chain(mp, allow=False):
    a = mc.ALLOWLIST
    files1 = {a: '{\n  "k::one": "r1"\n}\n'} if allow else {"one.py": "x = 1\n"}
    files2 = {a: '{\n  "k::two": "r2"\n}\n'} if allow else {"two.py": "y = 2\n"}
    ready_branch(mp, "one", files1)
    ready_branch(mp, "two", files2)
    return run_mp(mp, ["one", "two"])


def test_verify_landed_ok_appends_landing_record(mp):
    rep = built_chain(mp)
    wsdir = mp["root"] / "data" / "workstreams"
    wsdir.mkdir(parents=True)
    (wsdir / "orchestration.md").write_text("# Orchestration\n")
    res = lb.verify_landed(mp["root"], f"{mp['base']}..{rep['chain_tip']}",
                           gh=gh_landed(rep["chain_tip"], {}))
    assert res["ok"], res
    text = (wsdir / "orchestration.md").read_text()
    assert text.startswith("# Orchestration\n")
    assert "merge-push landed 2 branch(es)" in text
    assert "one" in text and "two" in text and rep["chain_tip"] in text


def test_verify_landed_not_landed_writes_nothing(mp):
    rep = built_chain(mp)
    wsdir = mp["root"] / "data" / "workstreams"
    wsdir.mkdir(parents=True)
    (wsdir / "orchestration.md").write_text("# Orchestration\n")
    tip2 = rep["built"][1]["tip"]
    res = lb.verify_landed(mp["root"], f"{mp['base']}..{rep['chain_tip']}",
                           gh=gh_landed(rep["chain_tip"], {tip2: 1}))
    assert not res["ok"]
    assert any(tip2[:8] in p for p in res["problems"])
    assert (wsdir / "orchestration.md").read_text() == "# Orchestration\n"


def test_verify_landed_checks_resolved_allowlist_on_github(mp):
    rep = built_chain(mp, allow=True)
    (mp["root"] / "data" / "workstreams").mkdir(parents=True)
    (mp["root"] / "data" / "workstreams" / "orchestration.md").write_text("")
    good = sh(mp["root"], "show", f"{rep['chain_tip']}:{mc.ALLOWLIST}") + "\n"
    rng = f"{mp['base']}..{rep['chain_tip']}"
    assert lb.verify_landed(mp["root"], rng, gh=gh_landed(None, {}, good))["ok"]
    bad = lb.verify_landed(mp["root"], rng, gh=gh_landed(None, {}, '{"k::one": "r1"}'))
    assert not bad["ok"] and any(mc.ALLOWLIST in p for p in bad["problems"])
    broken = lb.verify_landed(mp["root"], rng, gh=gh_landed(None, {}, '{"k::one": '))
    assert not broken["ok"]


def test_verify_landed_rejects_a_range_that_is_not_a_chain(mp):
    _, tip = branch(mp, "plain", {"p.py": "p = 1\n"})
    res = lb.verify_landed(mp["root"], f"{mp['base']}..{tip}", gh=gh_landed(None, {}))
    assert not res["ok"]


# ---------------------------------------------------------------- merge-push edge cases
def test_check_notes_an_allowlist_only_main_conflict(mp):
    a = mc.ALLOWLIST
    root = mp["root"]
    (root / a).write_text('{\n  "k::main": "rm"\n}\n')
    sh(root, "add", a)
    sh(root, "commit", "-qm", "main allowlist")
    _, tip = ready_branch(mp, "one", {a: '{\n  "k::one": "r1"\n}\n'})
    new_main = sh(root, "rev-parse", "HEAD")
    sh(root, "update-ref", "refs/remotes/origin/main", new_main)
    gh = gh_main_at(new_main, [mp["base"]], sh(root, "rev-parse", f"{new_main}^{{tree}}"))
    r = by_name(lb.check(root, ["one"], gh=gh, allowlist_ok=True))["one"]
    assert r["notes"] == [f"conflicts with main only in {a}; --merge-push resolves it by key"]
    plain = by_name(lb.check(root, ["one"], gh=gh))["one"]
    assert any("conflicts with main" in x for x in plain["refusals"])


def test_busy_processes_reads_ps_and_reports_a_ps_failure(monkeypatch):
    ps = "  PID ELAPSED COMMAND\n  101 00:05 git push origin main\n  102 00:01 vim x\n"
    monkeypatch.setattr(lb, "run", lambda cmd, **kw: (0, ps, ""))
    assert lb.busy_processes() == ["101 00:05 git push origin main"]
    monkeypatch.setattr(lb, "run", lambda cmd, **kw: (1, "", "boom"))
    assert lb.busy_processes() == ["? ps failed rc 1: cannot tell whether a push is running"]


@pytest.mark.parametrize("text,why", [
    ("", "description has no title line"),
    ("\n\n## Summary\n- x\n\n" + lb.PR_FOOTER + "\n", "description has no title line"),
    ("Title\nnot blank\n## Summary\n" + lb.PR_FOOTER + "\n", "line 2 of the description must be blank"),
    ("Title only", "line 2 of the description must be blank"),
])
def test_check_description_title_and_blank_line(mp, text, why):
    d = mp["root"] / lb.DESC_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "one.md").write_text(text)
    desc, probs = lb.check_description(mp["root"], "one")
    assert desc is None and why in probs


def test_check_description_good_text_has_no_problems(mp):
    describe(mp, "one")
    desc, probs = lb.check_description(mp["root"], "one")
    assert probs == [] and desc == "Add one\n\n" + GOOD_BODY


def test_jsonl_skips_bad_lines_and_non_objects(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text('{"a": 1}\nnot json\n[1, 2]\n"s"\n{"b": 2}\n')
    assert lb._jsonl(p) == [{"a": 1}, {"b": 2}]
    assert lb._jsonl(tmp_path / "missing.jsonl") == []


def test_backfill_dedups_across_worktrees_and_keeps_distinct_audit_rows(mp):
    wt1, tip1 = ready_branch(mp, "one", {"one.py": "x = 1\n"})
    wt2, _ = ready_branch(mp, "two", {"two.py": "y = 2\n"})
    review, audit = seed_branch_ledgers(wt1, tip1)
    audit2 = {**audit, "recorded": "2026-11-18T11:00:00+00:00"}
    for wt in (wt1, wt2):
        (wt / "tools" / ".cross-model-ledger.jsonl").write_text(json.dumps(review) + "\n")
        (wt / "tools" / ".audit-pii-ledger.jsonl").write_text(
            json.dumps(audit) + "\n" + json.dumps(audit2) + "\n"
            + json.dumps({**audit, "verdict": "findings", "recorded": "x"}) + "\n")
    cross, aud = lb.collect_backfill(mp["root"], [{"worktree": str(wt1)}, {"worktree": str(wt2)}])
    assert len(cross) == 1 and cross[0]["copied_from"] == str(wt1)
    assert [r["recorded"] for r in aud] == [audit["recorded"], audit2["recorded"]]


def test_write_backfill_with_no_audit_rows_creates_no_audit_ledger(mp):
    lb.write_backfill(mp["root"], [], [])
    assert not (mp["root"] / "tools" / ".audit-pii-ledger.jsonl").exists()


def test_preview_gate_sees_main_gate_data_and_cleans_up(mp, monkeypatch, tmp_path):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    main_row = {"report": "main.md", "model": "codex", "tree": "f" * 40, "findings": []}
    (mp["root"] / "tools" / ".cross-model-ledger.jsonl").write_text(json.dumps(main_row) + "\n")
    scratch = tmp_path / "tmpdirs"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    rep = run_mp(mp, ["one"])
    assert rep["ok"], rep
    assert '"report": "main.md"' in Path(os.environ["LB_PUSH_LOG"]).read_text()
    assert list(scratch.iterdir()) == []
    assert "merge-push-preview" not in sh(mp["root"], "worktree", "list")


def test_preview_gate_reports_a_bad_base_and_a_missing_hook(mp, tmp_path):
    root = mp["root"]
    bad = lb.preview_gate(root, root, root / "tools", FAKE_SHA, mp["base"], "u", [], [])
    assert bad["ok"] is False and bad["rc"] is None
    assert bad["reason"].startswith("preview worktree:")
    nohook = tmp_path / "checkers"
    nohook.mkdir()
    got = lb.preview_gate(root, root, nohook, mp["base"], mp["base"], "u", [], [])
    assert got == {"rc": None, "ok": False, "reason": f"no hook at {nohook}/hooks/pre-push"}


def test_mp_refuses_without_trusted_tools(mp, monkeypatch):
    monkeypatch.setattr(lb, "trusted_tools", lambda root: None)
    rep = run_mp(mp, ["one"])
    assert rep["refusals"] == [lb.NO_TRUSTED] and not rep["ok"]


def test_mp_refuses_when_github_main_is_unreadable(mp):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    rep = run_mp(mp, ["one"], gh=gh_down)
    assert not rep["ok"] and rep["base"] is None and rep["built"] == []
    assert any(r.startswith("GitHub main unreadable: ") for r in rep["refusals"]), rep["refusals"]


def test_mp_dry_run_refuses_a_stale_installed_hook_before_running_it(mp):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    installed = Path(sh(mp["root"], "rev-parse", "--path-format=absolute", "--git-path",
                        "hooks")) / "pre-push"
    installed.write_text(MP_HOOK + "# older copy\n")
    hook_log = Path(os.environ["LB_HOOK_LOG"])
    hook_log.unlink(missing_ok=True)
    rep = run_mp(mp, ["one"])
    assert not rep["ok"] and rep["built"] == [] and "check" not in rep
    assert len(rep["refusals"]) == 1 and "install.sh" in rep["refusals"][0]
    assert not hook_log.exists()            # the stale hook never ran


def test_mp_dry_run_runs_no_suite_and_resolves_nothing_without_allowlist(mp):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    rep = run_mp(mp, ["one"])
    assert rep["ok"], rep
    assert rep["suite"] is None and rep["verify_resolved"] == []


def test_mp_holds_a_ready_branch_stacked_on_a_held_one(mp):
    root = mp["root"]
    _, held_tip = branch(mp, "held", {"secret.py": "s = 1\n"})     # no description
    wt = mp["tmp"] / "wt" / "stacked"
    sh(root, "worktree", "add", "-q", "-b", "stacked", str(wt), held_tip)
    (wt / "ready.py").write_text("r = 1\n")
    sh(wt, "add", "-A")
    sh(wt, "commit", "-qm", "stacked")
    describe(mp, "stacked")
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    rep = run_mp(mp, ["held", "stacked", "one"])
    assert [b["branch"] for b in rep["built"]] == ["one"]
    assert any("contains held branch(es) held" in r for r in held(rep)["stacked"]), held(rep)
    rc = subprocess.run(["git", "merge-base", "--is-ancestor", held_tip, rep["chain_tip"]],
                        cwd=root).returncode
    assert rc == 1                              # the held commit is not in the push


def test_mp_holds_a_branch_stacked_on_a_held_ref_with_no_worktree(mp):
    """grok F1, 2026-11-19: a branch held for "no worktree" has no tip in its check row.
    It is named by its ref, so a ready branch stacked on it is still held."""
    root = mp["root"]
    held_wt, held_tip = branch(mp, "held", {"secret.py": "s = 1\n"})
    wt = mp["tmp"] / "wt" / "stacked"
    sh(root, "worktree", "add", "-q", "-b", "stacked", str(wt), held_tip)
    (wt / "ready.py").write_text("r = 1\n")
    sh(wt, "add", "-A")
    sh(wt, "commit", "-qm", "stacked")
    describe(mp, "stacked")
    sh(root, "worktree", "remove", "--force", str(held_wt))     # the ref stays
    rep = run_mp(mp, ["held", "stacked"])
    assert "no worktree for this branch" in held(rep)["held"]
    assert held(rep)["stacked"] == ["contains held branch(es) held: landing it would land them"]
    assert rep["built"] == []


def _held_repo(mp):
    root = mp["root"]
    _, h = branch(mp, "held", {"secret.py": "s = 1\n"})
    _, other = branch(mp, "other", {"o.py": "o = 1\n"})
    stacked = sh(root, "commit-tree", sh(root, "rev-parse", f"{h}^{{tree}}"), "-p", h,
                 "-m", "on held")
    return root, h, other, stacked


def test_held_inside_names_a_held_branch_by_its_ref(mp):
    root, h, other, stacked = _held_repo(mp)
    assert lb.held_inside(root, [{"branch": "held", "tip": None}], mp["base"],
                          stacked) == ["held"]
    assert lb.held_inside(root, [{"branch": "held"}], mp["base"], other) == []
    # A recorded tip is used as given: no ref by that name is needed to find it.
    assert lb.held_inside(root, [{"branch": "gone", "tip": h}], mp["base"],
                          stacked) == ["gone"]


def test_held_inside_ignores_a_held_tip_already_in_base_and_an_unknown_ref(mp):
    root, h, other, stacked = _held_repo(mp)
    on_main = [{"branch": "landed", "tip": mp["base"]}]
    assert lb.held_inside(root, on_main, mp["base"], stacked) == []
    assert lb.held_inside(root, [{"branch": "no-such-branch"}], mp["base"], stacked) == []


def test_held_inside_reports_a_git_error_as_inside(mp, monkeypatch):
    root, h, other, stacked = _held_repo(mp)
    real = lb.git

    def fake(args, cwd):
        if args[:2] == ["merge-base", "--is-ancestor"] and args[3] == other:
            return 128, "", "fatal: bad object"
        return real(args, cwd)
    monkeypatch.setattr(lb, "git", fake)
    assert lb.held_inside(root, [{"branch": "held", "tip": h}], mp["base"], other) == [
        "held (ancestry unreadable, git rc 128)"]


def test_mp_nothing_built_says_so(mp):
    branch(mp, "nodesc", {"n.py": "n = 1\n"})
    rep = run_mp(mp, ["nodesc"])
    assert rep["refusals"] == ["nothing to land: every branch is held"]


def test_mp_refuses_a_built_chain_that_does_not_verify(mp, monkeypatch):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})

    def broken(*a, **k):
        raise mc.ChainError("forced")
    monkeypatch.setattr(lb.mc, "walk", broken)
    rep = run_mp(mp, ["one"])
    assert rep["refusals"] == ["the built chain does not verify: forced"]
    assert rep["chain_tip"] is None and rep["gate"] is None


def _rep(**kw):
    rep = {"dry_run": False, "ok": False, "refusals": [], "held": [], "built": [],
           "base": None, "chain_tip": None, "gate": None, "suite": None,
           "backfill": {"cross": 0, "audit": 0, "written": False}, "commands": [],
           "fetch_command": None}
    rep.update(kw)
    return rep


def test_render_merge_push_ok_real_run_exact():
    rep = _rep(ok=True, base="b" * 40, chain_tip="c" * 40,
               built=[{"branch": "one", "tip": "1" * 40, "merge": "c" * 40,
                       "resolved_allowlist": True}],
               held=[{"branch": "two", "reasons": ["r1", "r2"]}],
               backfill={"cross": 2, "audit": 1, "written": True},
               gate={"ok": True, "rc": 0, "tail": "ignored"},
               suite={"ok": True, "rc": 0, "tail": "ignored"},
               commands=["! push", "! ff"])
    assert lb.render_merge_push(rep) == "\n".join([
        "MERGE-PUSH",
        f"base: GitHub main {'b' * 40}",
        f"BUILT  one  {'1' * 8} -> merge {'c' * 8}  (allowlist resolved by key)",
        "HELD   two",
        "         - r1",
        "         - r2",
        "evidence copied to the main checkout: 2 review row(s), 1 audit row(s) (written)",
        "push gate preview: ok (rc 0)",
        f"full suite on {'c' * 8}: pass (rc 0)",
        "",
        "One push lands every BUILT branch. Run from the main checkout, in order:",
        "  ! push",
        "  ! ff",
    ])


def test_render_merge_push_failures_exact():
    blocked = _rep(dry_run=True, gate_tools="/t", chain_tip="c" * 40,
                   gate={"ok": False, "rc": 1, "tail": "line a\nline b"},
                   refusals=["push gate rc 1: no"])
    assert lb.render_merge_push(blocked) == "\n".join([
        "DRY RUN (judged with /t; nothing written)",
        "evidence copied to the main checkout: 0 review row(s), 0 audit row(s) (not written)",
        "push gate preview: BLOCKED (rc 1)",
        "  line a",
        "  line b",
        "full suite: not run (dry run; pass --suite to run it)",
        "!!! push gate rc 1: no",
    ])
    suite_fail = _rep(chain_tip="c" * 40, gate={"ok": True, "rc": 0, "tail": "x"},
                      suite={"ok": False, "rc": 1, "tail": "FAILED t1"})
    assert lb.render_merge_push(suite_fail).splitlines()[-3:] == [
        "push gate preview: ok (rc 0)", f"full suite on {'c' * 8}: FAIL (rc 1)", "  FAILED t1"]
    stale = _rep(fetch_command="! git fetch", refusals=["stale"])
    assert lb.render_merge_push(stale).splitlines()[-4:] == [
        "!!! stale", "", "Run this first, then re-run:", "  ! git fetch"]
    assert "full suite" not in lb.render_merge_push(stale)


@pytest.mark.parametrize("rng", ["abc", "..abc", "abc.."])
def test_verify_landed_rejects_a_malformed_range(mp, rng):
    res = lb.verify_landed(mp["root"], rng, gh=gh_landed(None, {}))
    assert res["problems"] == [f"expected BASE..TIP, got {rng!r}"] and not res["ok"]


def test_verify_landed_names_a_non_chain(mp):
    _, tip = branch(mp, "plain", {"p.py": "p = 1\n"})
    res = lb.verify_landed(mp["root"], f"{mp['base']}..{tip}", gh=gh_landed(None, {}))
    assert len(res["problems"]) == 1 and res["problems"][0].startswith("not a merge-push chain:")


def test_verify_landed_needs_a_github_origin(mp):
    rep = built_chain(mp)
    sh(mp["root"], "remote", "set-url", "--push", "origin", "/srv/git/repo.git")
    res = lb.verify_landed(mp["root"], f"{mp['base']}..{rep['chain_tip']}", gh=gh_landed(None, {}))
    assert res["problems"] == ["origin is not a GitHub URL"]


def test_verify_landed_reports_each_failed_compare(mp):
    rep = built_chain(mp)
    res = lb.verify_landed(mp["root"], f"{mp['base']}..{rep['chain_tip']}", gh=gh_down)
    assert not res["ok"]
    assert len(res["problems"]) == 3
    assert all("compare failed: gh: not authenticated" in p for p in res["problems"])


def test_verify_landed_without_orchestration_file_is_not_ok(mp):
    rep = built_chain(mp)
    res = lb.verify_landed(mp["root"], f"{mp['base']}..{rep['chain_tip']}", gh=gh_landed(None, {}))
    assert not res["ok"]
    assert len(res["problems"]) == 1 and res["problems"][0].startswith("landing record not written")


def test_cli_dry_run_without_merge_push_says_why(mp, capsys):
    assert lb.main(["--repo-root", str(mp["root"]), "--dry-run", "one"]) == 2
    assert "--dry-run needs --merge-push" in capsys.readouterr().err


@pytest.mark.parametrize("ok,rc,want", [(True, 0, "LANDED; record appended to /r"),
                                        (False, 1, "NOT LANDED:\n  - p1")])
def test_cli_verify_landed(mp, monkeypatch, capsys, ok, rc, want):
    seen = []

    def fake(root, rng, gh=None):
        seen.append(rng)
        return {"ok": ok, "problems": [] if ok else ["p1"], "record": "/r"}
    monkeypatch.setattr(lb, "verify_landed", fake)
    assert lb.main(["--repo-root", str(mp["root"]), "--verify-landed", "a..b"]) == rc
    assert seen == ["a..b"]
    assert capsys.readouterr().out.strip() == want


# ---------------------------------------------------------------- git failures (grok 10.F3)
# merge_chain raises GitError, which is NOT a ChainError, when git fails. Every caller here
# must turn it into a hold, refusal or problem, never a crash and never "not a chain".
def _git_error(*a, **k):
    raise mc.GitError("rev-list failed (rc 128): forced")


def test_mp_holds_a_branch_whose_merge_hits_a_git_error(mp, monkeypatch):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    monkeypatch.setattr(lb.mc, "build", _git_error)
    rep = run_mp(mp, ["one"])
    assert rep["held"] == [{"branch": "one", "tip": rep["held"][0]["tip"],
                            "reasons": ["rev-list failed (rc 128): forced"]}]
    assert rep["refusals"] == ["nothing to land: every branch is held"]


def test_mp_refuses_when_walking_the_built_chain_hits_a_git_error(mp, monkeypatch):
    ready_branch(mp, "one", {"one.py": "x = 1\n"})
    monkeypatch.setattr(lb.mc, "walk", _git_error)
    rep = run_mp(mp, ["one"])
    assert rep["refusals"] == ["the built chain does not verify: rev-list failed (rc 128): forced"]
    assert rep["chain_tip"] is None and rep["gate"] is None


def test_verify_landed_reports_a_git_error_as_a_problem(mp, monkeypatch):
    _, tip = branch(mp, "plain", {"p.py": "p = 1\n"})
    monkeypatch.setattr(lb.mc, "walk", _git_error)
    res = lb.verify_landed(mp["root"], f"{mp['base']}..{tip}", gh=gh_landed(None, {}))
    assert res["ok"] is False
    assert res["problems"] == ["not a merge-push chain: rev-list failed (rc 128): forced"]


def test_check_refuses_when_the_allowlist_resolution_hits_a_git_error(mp, monkeypatch):
    a = mc.ALLOWLIST
    root = mp["root"]
    (root / a).write_text('{\n  "k::main": "rm"\n}\n')
    sh(root, "add", a)
    sh(root, "commit", "-qm", "main allowlist")
    ready_branch(mp, "one", {a: '{\n  "k::one": "r1"\n}\n'})
    new_main = sh(root, "rev-parse", "HEAD")
    sh(root, "update-ref", "refs/remotes/origin/main", new_main)
    gh = gh_main_at(new_main, [mp["base"]], sh(root, "rev-parse", f"{new_main}^{{tree}}"))
    monkeypatch.setattr(lb.mc, "expected_tree", _git_error)
    r = by_name(lb.check(root, ["one"], gh=gh, allowlist_ok=True))["one"]
    assert r["ready"] is False
    assert "notes" not in r
    assert any(x.startswith("conflicts with main: ") and "forced" in x for x in r["refusals"])
    assert r["steps"]["merge_main"]["error"] == "rev-list failed (rc 128): forced"
