"""Guard: every inbox writer goes through tools/inbox_lock, and only that module
implements the header splice.

Origin 2026-08-07: four writers had independently reimplemented "find the '# Inbox'
header, splice after it, rewrite the file". Two used a plain write_text (a crash
mid-write truncated the user's inbox), one appended at EOF instead of prepending
(silently breaking the newest-first convention), and the two "atomic" ones shared a
fixed temp-file name so concurrent writers could tear each other's temp file before
either rename. None took a lock, so the 3-hourly collector could revert a live
session's triage.

Per CLAUDE.md: when the same domain rule appears in a second tool, consolidate to one
module plus a single-source-of-truth guard test. This is that guard.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import inbox_lock  # noqa: E402

WRITERS = [
    "tools/granola_auto_debrief.py",
    "tools/career_scanner/scanner.py",
    "tools/agent_collect.py",
]


@pytest.mark.parametrize("rel", WRITERS)
def test_writer_delegates_to_shared_module(rel):
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "inbox_lock" in src, f"{rel} must write inboxes via tools/inbox_lock"


@pytest.mark.parametrize("rel", WRITERS)
def test_writer_does_not_reimplement_the_header_splice(rel):
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert 'startswith("# Inbox")' not in src, (
        f"{rel} hand-rolls the inbox header scan again — use "
        f"inbox_lock.prepend_entries instead")


@pytest.mark.parametrize("rel", WRITERS)
def test_writer_is_importable(rel):
    """A fixture suite can pass while a module is unimportable — it did, on
    2026-08-07, when an inserted import referenced Path before pathlib was
    imported. Import each writer for real."""
    mod_dir = (REPO_ROOT / rel).parent
    code = f"import sys; sys.path.insert(0, {str(mod_dir)!r}); sys.path.insert(0, {str(REPO_ROOT)!r}); import {Path(rel).stem}"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT)
    assert r.returncode == 0, f"{rel} failed to import:\n{r.stderr}"


def test_only_the_shared_module_owns_the_splice():
    src = (REPO_ROOT / "tools" / "inbox_lock.py").read_text(encoding="utf-8")
    assert 'startswith("# Inbox")' in src


def test_prepend_puts_newest_first_and_keeps_one_header(tmp_path):
    inbox = tmp_path / "inbox.md"
    inbox.write_text(inbox_lock.DEFAULT_INBOX_HEADER + "\n## older\n", encoding="utf-8")
    inbox_lock.prepend_entries(inbox, "## newer")
    out = inbox.read_text(encoding="utf-8")
    assert out.count("# Inbox") == 1
    assert out.index("## newer") < out.index("## older")
    assert out.startswith("# Inbox")


def test_prepend_creates_a_headered_file_when_absent(tmp_path):
    inbox = tmp_path / "inbox.md"
    inbox_lock.prepend_entries(inbox, "## first")
    out = inbox.read_text(encoding="utf-8")
    assert out.startswith("# Inbox") and "## first" in out


def test_prepend_is_a_noop_on_empty_entries(tmp_path):
    inbox = tmp_path / "inbox.md"
    inbox.write_text("# Inbox\n", encoding="utf-8")
    before = inbox.stat().st_mtime_ns
    res = inbox_lock.prepend_entries(inbox, ["", "  "])
    assert res["status"] == "noop"
    assert inbox.stat().st_mtime_ns == before   # must not perturb Obsidian's watcher
