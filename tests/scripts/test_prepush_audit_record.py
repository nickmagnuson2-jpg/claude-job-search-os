#!/usr/bin/env python3
"""The /audit-pii push gate: a public push needs a clean semantic-pass record for its tree.

Design (Nick, 2026-11-05):
  - /audit-pii, on a CLEAN verdict, writes a record keyed to the TREE of the commit being
    pushed (tools/audit_pii_record.py -> gitignored tools/.audit-pii-ledger.jsonl), so a
    message-only rewrite keeps its record.
  - prepush_pii_guard BLOCKS a push whose pushed tip tree has no clean record, unless
    AUDIT_PII_WAIVE='<reason>' is set; a waiver is appended to the same ledger.
  - Deletion-only pushes pass. A missing denylist skips only the deterministic scan; the
    record check still applies. A manual run (no hook stdin) reports, never blocks.

Limit (also in the tool docstring): Claude writes the record, so it proves the pass RAN,
not that it was good.

Token fixtures are invented and never real: this file is a public artifact.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "tools" / "prepush_pii_guard.py"
RECORD = ROOT / "tools" / "audit_pii_record.py"
ZERO_SHA = "0" * 40
FIXTURE_DENYLIST = "# fixture\nZorptech\n"
LEDGER = Path("tools") / ".audit-pii-ledger.jsonl"


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    dl = tmp_path / "tools" / ".pii-denylist.txt"
    dl.parent.mkdir(parents=True, exist_ok=True)
    dl.write_text(FIXTURE_DENYLIST, encoding="utf-8")
    return tmp_path


def commit(repo, rel, content, msg=None):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _git(repo, "add", "-f", rel)
    _git(repo, "commit", "-q", "-m", msg or f"add {rel}")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def tree(repo, sha):
    return _git(repo, "rev-parse", f"{sha}^{{tree}}").stdout.strip()


def guard(repo, stdin="", env=None):
    e = {k: v for k, v in os.environ.items() if k not in ("AUDIT_PII_WAIVE", "PUSH_PII_OVERRIDE")}
    return subprocess.run([sys.executable, str(GUARD)], input=stdin, capture_output=True,
                          text=True, cwd=str(repo), env={**e, **(env or {})})


def record_tool(repo, *args):
    return subprocess.run([sys.executable, str(RECORD), "--repo-root", str(repo), *args],
                          capture_output=True, text=True, cwd=str(repo))


def push_line(sha, ref="main"):
    return f"refs/heads/{ref} {sha} refs/heads/{ref} {ZERO_SHA}\n"


def rows(repo):
    p = repo / LEDGER
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_row(repo, row):
    p = repo / LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


# --- the record tool ---------------------------------------------------------------------

def test_record_writes_a_clean_row_keyed_to_the_tips_tree(repo):
    sha = commit(repo, "a.md", "ok\n")
    r = record_tool(repo, "record", "--tip", sha, "--files", "3",
                    "--accept", "a.md:Widget", "--accept", "b.md:Gadget")
    assert r.returncode == 0, r.stderr
    (row,) = rows(repo)
    assert row["kind"] == "audit" and row["verdict"] == "clean"
    assert row["tree"] == tree(repo, sha) and row["tip"] == sha
    assert row["files_scanned"] == 3
    assert row["accepted"] == ["a.md:Widget", "b.md:Gadget"]
    assert row["recorded"]


def test_record_refuses_a_tip_that_is_not_a_commit(repo):
    commit(repo, "a.md", "ok\n")
    r = record_tool(repo, "record", "--tip", "deadbeef" * 5)
    assert r.returncode == 1
    assert json.loads(r.stdout)["code"] == "not_a_commit"
    assert rows(repo) == []


def test_check_exits_zero_only_when_the_tree_has_a_clean_record(repo):
    sha = commit(repo, "a.md", "ok\n")
    assert record_tool(repo, "check", "--tip", sha).returncode == 1
    record_tool(repo, "record", "--tip", sha)
    assert record_tool(repo, "check", "--tip", sha).returncode == 0


def test_the_ledger_is_gitignored_in_this_repo():
    r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(LEDGER)])
    assert r.returncode == 0, "tools/.audit-pii-ledger.jsonl must be gitignored"


# --- the guard's decision ----------------------------------------------------------------

def test_a_clean_push_without_a_record_is_blocked_and_says_how_to_clear_it(repo):
    sha = commit(repo, "a.md", "ok\n")
    r = guard(repo, push_line(sha))
    assert r.returncode == 1
    assert "/audit-pii" in r.stderr and "AUDIT_PII_WAIVE" in r.stderr
    assert sha[:8] in r.stderr


def test_a_clean_push_with_a_record_for_its_tree_passes(repo):
    sha = commit(repo, "a.md", "ok\n")
    record_tool(repo, "record", "--tip", sha)
    assert guard(repo, push_line(sha)).returncode == 0


def test_a_record_for_a_different_tree_does_not_count(repo):
    old = commit(repo, "a.md", "ok\n")
    record_tool(repo, "record", "--tip", old)
    new = commit(repo, "b.md", "more\n")
    assert guard(repo, push_line(new)).returncode == 1


def test_a_message_only_rewrite_keeps_its_record(repo):
    sha = commit(repo, "a.md", "ok\n")
    record_tool(repo, "record", "--tip", sha)
    _git(repo, "commit", "-q", "--amend", "-m", "reworded")
    amended = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert amended != sha and tree(repo, amended) == tree(repo, sha)
    assert guard(repo, push_line(amended)).returncode == 0


def test_every_pushed_tip_needs_its_own_record(repo):
    a = commit(repo, "a.md", "ok\n")
    record_tool(repo, "record", "--tip", a)
    _git(repo, "checkout", "-q", "-b", "other")
    b = commit(repo, "b.md", "other\n")
    r = guard(repo, push_line(a) + push_line(b, "other"))
    assert r.returncode == 1 and b[:8] in r.stderr and a[:8] not in r.stderr


def test_a_row_whose_verdict_is_not_clean_does_not_count(repo):
    sha = commit(repo, "a.md", "ok\n")
    write_row(repo, {"kind": "audit", "verdict": "hits", "tree": tree(repo, sha), "tip": sha})
    assert guard(repo, push_line(sha)).returncode == 1


def test_a_waiver_row_is_not_a_clean_record_for_the_next_push(repo):
    sha = commit(repo, "a.md", "ok\n")
    assert guard(repo, push_line(sha), {"AUDIT_PII_WAIVE": "hotfix"}).returncode == 0
    assert guard(repo, push_line(sha)).returncode == 1


def test_the_waiver_allows_the_push_and_is_recorded_with_its_reason(repo):
    sha = commit(repo, "a.md", "ok\n")
    r = guard(repo, push_line(sha), {"AUDIT_PII_WAIVE": "docs typo, Nick"})
    assert r.returncode == 0
    assert "AUDIT_PII_WAIVE" in r.stderr and "docs typo, Nick" in r.stderr
    (row,) = rows(repo)
    assert row["kind"] == "waiver" and row["reason"] == "docs typo, Nick"
    assert row["tree"] == tree(repo, sha) and row["tip"] == sha and row["recorded"]


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_waiver_reason_does_not_waive(repo, value):
    sha = commit(repo, "a.md", "ok\n")
    assert guard(repo, push_line(sha), {"AUDIT_PII_WAIVE": value}).returncode == 1
    assert rows(repo) == []


def test_the_pii_override_does_not_clear_a_missing_record(repo):
    sha = commit(repo, "a.md", "ok\n")
    assert guard(repo, push_line(sha), {"PUSH_PII_OVERRIDE": "1"}).returncode == 1


def test_a_deletion_only_push_needs_no_record(repo):
    commit(repo, "a.md", "ok\n")
    assert guard(repo, push_line(ZERO_SHA)).returncode == 0


def test_a_denylist_hit_still_blocks_even_with_a_record(repo):
    sha = commit(repo, "a.md", "Zorptech is here\n")
    record_tool(repo, "record", "--tip", sha)
    r = guard(repo, push_line(sha))
    assert r.returncode == 1 and "Zorptech" in r.stderr


def test_a_missing_denylist_still_requires_the_record(repo):
    (repo / "tools" / ".pii-denylist.txt").unlink()
    sha = commit(repo, "a.md", "ok\n")
    assert guard(repo, push_line(sha)).returncode == 1
    record_tool(repo, "record", "--tip", sha)
    r = guard(repo, push_line(sha))
    assert r.returncode == 0 and "no denylist" in r.stderr


def test_an_unreadable_ledger_blocks_the_push(repo):
    sha = commit(repo, "a.md", "ok\n")
    record_tool(repo, "record", "--tip", sha)
    with (repo / LEDGER).open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    r = guard(repo, push_line(sha))
    assert r.returncode == 1 and "ledger" in r.stderr.lower()


def test_a_manual_run_reports_the_missing_record_but_does_not_block(repo):
    commit(repo, "a.md", "ok\n")
    r = guard(repo, "")
    assert r.returncode == 0
    assert "no clean /audit-pii record" in r.stderr


def test_a_manual_run_reports_a_present_record(repo):
    sha = commit(repo, "a.md", "ok\n")
    record_tool(repo, "record", "--tip", sha)
    r = guard(repo, "")
    assert r.returncode == 0 and "clean /audit-pii record" in r.stderr
    assert "no clean /audit-pii record" not in r.stderr


# --- mutation round 1 (2026-11-05): survivors killed ---------------------------------------

def test_record_prints_its_result_as_json(repo):
    sha = commit(repo, "a.md", "ok\n")
    out = json.loads(record_tool(repo, "record", "--tip", sha).stdout)
    assert out["status"] == "ok" and out["action"] == "record" and out["tip"] == sha


def test_check_reports_an_unreadable_ledger_with_exit_2(repo):
    sha = commit(repo, "a.md", "ok\n")
    write_row(repo, {"kind": "audit", "verdict": "clean", "tree": tree(repo, sha)})
    with (repo / LEDGER).open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    r = record_tool(repo, "check", "--tip", sha)
    assert r.returncode == 2 and json.loads(r.stdout)["code"] == "ledger_unreadable"


def test_a_ledger_row_that_is_not_an_object_is_unreadable(repo):
    sha = commit(repo, "a.md", "ok\n")
    (repo / LEDGER).write_text("[1, 2]\n", encoding="utf-8")
    r = record_tool(repo, "check", "--tip", sha)
    assert r.returncode == 2 and json.loads(r.stdout)["code"] == "ledger_unreadable"


def test_blank_lines_in_the_ledger_are_skipped(repo):
    sha = commit(repo, "a.md", "ok\n")
    (repo / LEDGER).write_text("\n   \n", encoding="utf-8")
    record_tool(repo, "record", "--tip", sha)
    with (repo / LEDGER).open("a", encoding="utf-8") as f:
        f.write("\n")
    assert record_tool(repo, "check", "--tip", sha).returncode == 0


def test_record_creates_the_tools_folder_when_missing(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    sha = commit(tmp_path, "a.md", "ok\n")
    assert not (tmp_path / "tools").exists()
    assert record_tool(tmp_path, "record", "--tip", sha).returncode == 0
    assert len(rows(tmp_path)) == 1


def test_the_pii_override_on_a_hit_still_needs_the_record(repo):
    sha = commit(repo, "a.md", "Zorptech is here\n")
    r = guard(repo, push_line(sha), {"PUSH_PII_OVERRIDE": "1"})
    assert r.returncode == 1 and "no clean /audit-pii record" in r.stderr
    record_tool(repo, "record", "--tip", sha)
    assert guard(repo, push_line(sha), {"PUSH_PII_OVERRIDE": "1"}).returncode == 0


def test_a_pushed_sha_that_is_not_a_commit_is_named(repo):
    (repo / "tools" / ".pii-denylist.txt").unlink()
    commit(repo, "a.md", "ok\n")
    r = guard(repo, push_line("ab" * 20))
    assert r.returncode == 1 and "is not a commit" in r.stderr


def test_a_manual_run_names_an_unreadable_ledger(repo):
    commit(repo, "a.md", "ok\n")
    (repo / LEDGER).write_text("{not json\n", encoding="utf-8")
    r = guard(repo, "")
    assert r.returncode == 0 and "audit ledger unreadable" in r.stderr


def test_a_clean_push_with_a_denylist_does_not_claim_the_denylist_is_missing(repo):
    sha = commit(repo, "a.md", "ok\n")
    record_tool(repo, "record", "--tip", sha)
    r = guard(repo, push_line(sha))
    assert r.returncode == 0 and "no denylist" not in r.stderr and "scan clean" in r.stderr


def test_a_missing_denylist_never_claims_the_scan_was_clean(repo):
    (repo / "tools" / ".pii-denylist.txt").unlink()
    sha = commit(repo, "a.md", "ok\n")
    record_tool(repo, "record", "--tip", sha)
    r = guard(repo, push_line(sha))
    assert r.returncode == 0 and "scan clean" not in r.stderr


# --- Codex r1 F1 (P0, 2026-11-05): an error inside the record check must block -------------
# The top-level handler lets PUSH_PII_OVERRIDE turn any exception into "allow". The record
# check must therefore catch its own errors, so only AUDIT_PII_WAIVE can pass it.

def test_the_pii_override_cannot_pass_a_pushed_sha_that_is_not_a_commit(repo):
    (repo / "tools" / ".pii-denylist.txt").unlink()
    commit(repo, "a.md", "ok\n")
    r = guard(repo, push_line("ab" * 20), {"PUSH_PII_OVERRIDE": "1"})
    assert r.returncode == 1 and "is not a commit" in r.stderr


def test_the_pii_override_cannot_pass_a_ledger_that_cannot_be_opened(repo):
    sha = commit(repo, "a.md", "ok\n")
    (repo / LEDGER).mkdir(parents=True)
    r = guard(repo, push_line(sha), {"PUSH_PII_OVERRIDE": "1"})
    assert r.returncode == 1 and "no clean /audit-pii record" in r.stderr


def test_a_waiver_that_cannot_be_recorded_does_not_pass(repo):
    sha = commit(repo, "a.md", "ok\n")
    (repo / LEDGER).mkdir(parents=True)
    r = guard(repo, push_line(sha), {"AUDIT_PII_WAIVE": "hotfix"})
    assert r.returncode == 1 and "could not be recorded" in r.stderr


def _import_guard():
    import importlib
    sys.path.insert(0, str(ROOT / "tools"))
    return importlib.import_module("prepush_pii_guard")


def test_tree_or_blank_returns_the_real_tree(repo):
    g = _import_guard()
    sha = commit(repo, "a.md", "ok\n")
    assert g._tree_or_blank(repo, sha) == tree(repo, sha)


def test_tree_or_blank_never_raises(repo, monkeypatch):
    """It runs inside record_gate's error path; an exception escaping it would reach the
    top-level handler that PUSH_PII_OVERRIDE turns into allow (Codex r1 F1)."""
    g = _import_guard()

    def boom(*_a, **_k):
        raise OSError("git missing")
    monkeypatch.setattr(g.rec, "tree_of", boom)
    assert g._tree_or_blank(repo, "HEAD") == ""


# --- Codex r2 F1 (P0) / F2 (P1), 2026-11-05 -------------------------------------------------
# The override covers the deterministic scan only; a scan EXCEPTION with the override set
# must still reach the record check. A manual run never blocks, whatever the ledger is.

def _main_with_scan_error(repo, monkeypatch, capsys, stdin, env):
    import io
    g = _import_guard()
    def boom(*_a, **_k):
        raise g.GitReadError("simulated scan failure")
    monkeypatch.setattr(g, "new_blobs", boom)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    for k in ("PUSH_PII_OVERRIDE", "AUDIT_PII_WAIVE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    rc = g.main()
    return rc, capsys.readouterr().err


def test_a_scan_error_with_the_override_still_needs_the_record(repo, monkeypatch, capsys):
    sha = commit(repo, "a.md", "ok\n")
    rc, err = _main_with_scan_error(repo, monkeypatch, capsys, push_line(sha),
                                    {"PUSH_PII_OVERRIDE": "1"})
    assert "allowing UNSCANNED push" in err
    assert rc == 1 and "no clean /audit-pii record" in err


def test_a_scan_error_with_the_override_and_a_record_passes(repo, monkeypatch, capsys):
    sha = commit(repo, "a.md", "ok\n")
    record_tool(repo, "record", "--tip", sha)
    rc, err = _main_with_scan_error(repo, monkeypatch, capsys, push_line(sha),
                                    {"PUSH_PII_OVERRIDE": "1"})
    assert rc == 0 and "allowing UNSCANNED push" in err


def test_a_scan_error_without_the_override_blocks(repo, monkeypatch, capsys):
    sha = commit(repo, "a.md", "ok\n")
    record_tool(repo, "record", "--tip", sha)
    rc, err = _main_with_scan_error(repo, monkeypatch, capsys, push_line(sha), {})
    assert rc == 1 and "could not scan this push" in err


def test_a_manual_run_with_an_unopenable_ledger_does_not_block(repo):
    commit(repo, "a.md", "ok\n")
    (repo / LEDGER).mkdir(parents=True)
    r = guard(repo, "")
    assert r.returncode == 0 and "audit ledger unreadable" in r.stderr


def test_a_deletion_only_push_prints_no_record_report(repo):
    commit(repo, "a.md", "ok\n")
    r = guard(repo, push_line(ZERO_SHA))
    assert r.returncode == 0 and "/audit-pii record" not in r.stderr
