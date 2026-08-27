#!/usr/bin/env python3
"""Gate-behaviour tests for prepush_pii_guard.py — the last line before a PUBLIC push.

WHY THIS FILE EXISTS. The first corpus-wide mutation sweep (2026-08-26) measured this
tool at **38 survivors of 63 mutants, 60%**, on 5 tests. It is the highest-consequence
number in the corpus: this guard is the last automated check before code leaves the
machine for a public repo, and a leak is irreversible once pushed. Among the survivors:

  - `if found:` inverted           -> the guard NEVER blocks. Suite stays green.
  - `return 1` -> `return None`    -> sys.exit(None) is exit 0. Blocked becomes allowed.
  - every BLOCKED / clean / override message deletable, with nothing failing.
  - the whole `PUSH_PII_OVERRIDE` branch, untested in either direction.
  - the no-denylist fail-open path, untested.
  - all four path filters in tracked_public_files and public_blobs_at.

The existing test file covers the 2026-07-07 regression (committed-blob vs working-tree
scan) and covers it well. It does not cover the gate's DECISION at all.

The rule these tests follow: for a gate, **the exit code is the behaviour**. Every test
pins the exit code, and separately pins the message the operator has to act on -- a block
with no explanation of what to fix is barely better than no block, because the next move
is to override it.

Token fixtures are invented (`Zorptech`, `Pat Zorp`) and never real, per the public-repo
PII rule -- this file is itself a public artifact.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[2] / "tools" / "prepush_pii_guard.py"
ZERO_SHA = "0" * 40
FIXTURE_DENYLIST = "# fixture\nZorptech\nPat Zorp\n"


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


def commit(repo, rel, content):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _git(repo, "add", "-f", rel)
    _git(repo, "commit", "-q", "-m", f"add {rel}")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def run(repo, stdin="", env=None):
    return subprocess.run(
        [sys.executable, str(GUARD)], input=stdin, capture_output=True, text=True,
        cwd=str(repo), env={**os.environ, **(env or {})})


def push_line(sha):
    return f"refs/heads/main {sha} refs/heads/main {ZERO_SHA}\n"


# --- 1. the decision itself -------------------------------------------------

def test_a_hit_blocks_with_a_nonzero_exit(repo):
    """`if found:` inverted, or `return 1` turned into `return None`, both leave the
    suite green and the gate open. Exit 0 from this tool means the push proceeds."""
    sha = commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, push_line(sha))
    assert r.returncode == 1, f"a denylist hit MUST block the push; got {r.returncode}"


def test_the_block_names_the_file_and_the_token(repo):
    """A block with no detail sends the operator straight to the override flag."""
    sha = commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, push_line(sha))
    assert "framework/leak.md" in r.stderr
    assert "Pat Zorp" in r.stderr
    assert "BLOCKED" in r.stderr


def test_the_block_says_how_to_fix_it_and_how_to_override(repo):
    """The remediation text is deletable with the suite green. It is the difference
    between a gate people fix and a gate people disable."""
    sha = commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, push_line(sha))
    assert "placeholder" in r.stderr.lower()
    assert "PUSH_PII_OVERRIDE=1" in r.stderr
    assert "/audit-pii" in r.stderr, "the semantic pass is the half this tool cannot do"


def test_every_offending_file_is_reported_not_just_the_first(repo):
    """Fixing one and re-pushing into a second block is how a gate loses its reader."""
    commit(repo, "framework/a.md", "Contact: Pat Zorp\n")
    sha = commit(repo, "docs/b.md", "Vendor: Zorptech\n")
    r = run(repo, push_line(sha))
    assert r.returncode == 1
    assert "framework/a.md" in r.stderr and "docs/b.md" in r.stderr


def test_every_token_in_one_file_is_reported(repo):
    sha = commit(repo, "framework/leak.md", "Pat Zorp works at Zorptech\n")
    r = run(repo, push_line(sha))
    assert r.returncode == 1
    assert "Pat Zorp" in r.stderr and "Zorptech" in r.stderr


def test_a_clean_push_exits_zero_and_says_what_it_checked(repo):
    """`(N tokens)` is the denominator. A clean report that does not say how much it
    knew about is the shape of a scan that checked nothing."""
    sha = commit(repo, "framework/ok.md", "Vendor: Acme (placeholder)\n")
    r = run(repo, push_line(sha))
    assert r.returncode == 0, r.stderr
    assert "clean" in r.stderr
    assert "2 tokens" in r.stderr, "the token count is the denominator of the claim"
    assert "/audit-pii" in r.stderr, "clean here is not clean overall; say so"


# --- 2. the override, in both directions ------------------------------------

def test_the_override_allows_the_push(repo):
    sha = commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, push_line(sha), env={"PUSH_PII_OVERRIDE": "1"})
    assert r.returncode == 0, "an explicit override must allow the push"


def test_the_override_announces_itself_before_listing_hits(repo):
    """Two separate lines, two separate mutants: the banner saying an override is in
    effect, and the per-file list. Losing the banner leaves a list of hits above a
    successful push with nothing saying why it was allowed."""
    sha = commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, push_line(sha), env={"PUSH_PII_OVERRIDE": "1"})
    assert "PUSH_PII_OVERRIDE=1" in r.stderr
    assert "allowing" in r.stderr.lower()


def test_the_override_still_names_every_hit_it_waved_through(repo):
    """Silent override is indistinguishable from a clean scan in a terminal scrollback,
    and this is the one path where real PII is knowingly shipped."""
    sha = commit(repo, "framework/leak.md", "Pat Zorp at Zorptech\n")
    r = run(repo, push_line(sha), env={"PUSH_PII_OVERRIDE": "1"})
    assert "overridden" in r.stderr.lower()
    assert "framework/leak.md" in r.stderr
    assert "Pat Zorp" in r.stderr and "Zorptech" in r.stderr


@pytest.mark.parametrize("value", ["0", "", "true", "yes", "2", "TRUE"])
def test_only_the_exact_value_1_overrides(repo, value):
    """A loose truthiness check here means a stray PUSH_PII_OVERRIDE=0 in a shell
    profile disables the gate permanently and invisibly."""
    sha = commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, push_line(sha), env={"PUSH_PII_OVERRIDE": value})
    assert r.returncode == 1, f"PUSH_PII_OVERRIDE={value!r} must NOT disable the gate"


def test_the_override_does_not_rescue_a_clean_run_into_a_block(repo):
    sha = commit(repo, "framework/ok.md", "Vendor: Acme\n")
    r = run(repo, push_line(sha), env={"PUSH_PII_OVERRIDE": "1"})
    assert r.returncode == 0
    assert "overridden" not in r.stderr.lower(), "nothing was overridden; do not say so"


# --- 3. the denylist ---------------------------------------------------------

def test_a_missing_denylist_fails_open_but_says_so_loudly(repo):
    """Fail-open is deliberate -- never wedge a push on infrastructure -- but a missing
    denylist right before a public push is itself the suspicious event."""
    (repo / "tools" / ".pii-denylist.txt").unlink()
    sha = commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, push_line(sha))
    assert r.returncode == 0
    assert "no denylist" in r.stderr
    assert "gen_pii_denylist.py" in r.stderr, "name the fix, not just the problem"


def test_a_comments_only_denylist_counts_as_missing(repo):
    """An all-comment file parses to zero tokens. Treating it as a live denylist would
    report `clean (0 tokens)` -- a pass that checked nothing, worded like a result."""
    (repo / "tools" / ".pii-denylist.txt").write_text("# nothing\n\n", encoding="utf-8")
    sha = commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, push_line(sha))
    assert r.returncode == 0
    assert "no denylist" in r.stderr


# --- 4. what gets scanned ----------------------------------------------------

def test_an_ignored_untracked_file_is_never_in_the_pushed_tree(repo):
    """This is how data/ and output/ are actually protected, and it is worth pinning
    because it is NOT the is_gitignored() call that does it.

    `git check-ignore` reports a TRACKED path as not-ignored, and both scan sources
    (ls-tree, ls-files) yield only tracked paths -- so that filter can never fire. What
    keeps ignored content out of the scan is that it is not in the tree at all. Verified
    empirically 2026-08-26; the two is_gitignored branches are allowlisted as dead with
    that reason rather than papered over with a test that passes for another reason.
    """
    commit(repo, ".gitignore", "framework/ignored.md\n")
    (repo / "framework").mkdir(exist_ok=True)
    (repo / "framework" / "ignored.md").write_text("Contact: Pat Zorp\n", encoding="utf-8")
    sha = commit(repo, "framework/ok.md", "Vendor: Acme\n")
    names = _git(repo, "ls-tree", "-r", "--name-only", sha).stdout.split()
    assert "framework/ignored.md" not in names, "an ignored file must not enter the tree"
    r = run(repo, push_line(sha))
    assert r.returncode == 0


def test_a_force_added_ignored_file_IS_scanned(repo):
    """The consequence of the above, stated so nobody relies on the filter: `git add -f`
    makes an ignored path tracked, git then stops calling it ignored, and it is scanned
    like any other public file. That is the SAFE direction -- it ships, so it is scanned
    -- but it is the opposite of what the is_gitignored() line implies."""
    commit(repo, ".gitignore", "framework/ignored.md\n")
    sha = commit(repo, "framework/ignored.md", "Contact: Pat Zorp\n")
    r = run(repo, push_line(sha))
    assert r.returncode == 1, "force-added means tracked means public means scanned"


def test_binary_files_are_skipped(repo):
    """A png whose bytes happen to decode into a token is a false positive that costs
    real trust in the gate."""
    sha = commit(repo, "docs/img.png", "Zorptech\n")
    r = run(repo, push_line(sha))
    assert r.returncode == 0, "binary extensions are not scanned as text"


# --- 5. the pre-push stdin protocol -----------------------------------------

def test_every_pushed_sha_is_scanned_not_only_the_first(repo):
    """`git push` with several refs sends several lines. Scanning only one leaves the
    others unchecked while the run still reports clean."""
    clean_sha = commit(repo, "framework/ok.md", "Vendor: Acme\n")
    dirty_sha = commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, push_line(clean_sha) + push_line(dirty_sha))
    assert r.returncode == 1, "a later ref carrying PII must still block"


def test_a_deletion_only_push_is_allowed_without_a_worktree_fallback(repo):
    """All-zero shas mean nothing is being added. Falling back to the working-tree scan
    here would block a branch deletion on PII that is not being pushed at all."""
    commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, push_line(ZERO_SHA))
    assert r.returncode == 0


def test_malformed_stdin_lines_do_not_crash_the_guard(repo):
    """A guard that crashes fails open. It must survive junk on stdin."""
    sha = commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, "garbage\n\n" + push_line(sha) + "one-field\n")
    assert r.returncode == 1, "the valid line must still be scanned"
    assert "Traceback" not in r.stderr


def test_an_empty_pipe_falls_back_to_the_working_tree(repo):
    """Manual invocation. The fallback is what makes this runnable by hand before a
    commit, and it must actually scan something."""
    commit(repo, "framework/ok.md", "Vendor: Acme\n")
    (repo / "framework" / "leak.md").write_text("Contact: Pat Zorp\n", encoding="utf-8")
    _git(repo, "add", "-f", "framework/leak.md")
    r = run(repo, "")
    assert r.returncode == 1, "the worktree fallback must scan tracked public files"


# --- 6. the working-tree fallback filters the same way -----------------------
#
# The fallback is a SECOND implementation of the same filtering (tracked_public_files
# vs public_blobs_at). Both had every filter branch surviving, and a filter that only
# works on one of the two paths is a filter you cannot reason about.

def test_the_fallback_skips_binary_and_non_public_paths(repo):
    commit(repo, "framework/ok.md", "Vendor: Acme\n")
    commit(repo, "docs/img.png", "Pat Zorp\n")
    commit(repo, "data/notes.md", "Pat Zorp\n")
    r = run(repo, "")
    assert r.returncode == 0, "binary extensions and non-public prefixes are not scanned"


def test_the_fallback_still_blocks_a_real_public_hit(repo):
    """The counterpart, so the filters above cannot be satisfied by scanning nothing."""
    commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    r = run(repo, "")
    assert r.returncode == 1
    assert "framework/leak.md" in r.stderr


def test_a_tracked_file_missing_from_disk_does_not_crash_the_fallback(repo):
    """`git ls-files` lists what is tracked, not what is present. A deleted-but-tracked
    file must be skipped, not read."""
    commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    commit(repo, "framework/gone.md", "Vendor: Acme\n")
    (repo / "framework" / "gone.md").unlink()
    r = run(repo, "")
    assert r.returncode == 1, "the surviving file is still scanned"
    assert "Traceback" not in r.stderr


# --- 7. fail-open, but visibly -----------------------------------------------

def test_an_infrastructure_failure_allows_the_push_and_says_why(repo):
    """Deliberate: never wedge a push on a broken guard. But a silent fail-open is
    indistinguishable from a clean scan, which is how a dead gate goes unnoticed. With
    git absent, every subprocess call raises."""
    sha = commit(repo, "framework/leak.md", "Contact: Pat Zorp\n")
    empty_bin = repo / "emptybin"
    empty_bin.mkdir()
    r = run(repo, push_line(sha), env={"PATH": str(empty_bin)})
    assert r.returncode == 0, "an infrastructure failure must not wedge the push"
    assert "error" in r.stderr.lower(), "a silent fail-open reads exactly like a clean run"


# --- 8. a terminal is not a hook ---------------------------------------------

def test_a_tty_stdin_ignores_the_pipe_and_scans_the_working_tree(repo):
    """`sys.stdin.isatty()` is how a human run is told apart from a hook run.

    Distinguishing the two needs a real terminal, so this opens a pty. The setup is
    deliberately asymmetric -- the pushed commit is CLEAN and the working tree is DIRTY
    -- because that is the only arrangement where the two branches give different exit
    codes. Feed a valid push line down the pty: honouring isatty means ignore it and
    scan the tree (block); ignoring isatty means trust the line and scan the clean sha
    (allow).
    """
    import pty
    clean_sha = commit(repo, "framework/ok.md", "Vendor: Acme\n")
    (repo / "framework" / "leak.md").write_text("Contact: Pat Zorp\n", encoding="utf-8")
    _git(repo, "add", "-f", "framework/leak.md")

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [sys.executable, str(GUARD)], stdin=slave,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(repo))
        os.close(slave)
        os.write(master, push_line(clean_sha).encode())
        try:
            _, err = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("the guard blocked reading a tty it should never have read")
    finally:
        os.close(master)

    assert proc.returncode == 1, (
        "a tty means a manual run: the working tree must be scanned, not the piped sha")
    assert "framework/leak.md" in err
