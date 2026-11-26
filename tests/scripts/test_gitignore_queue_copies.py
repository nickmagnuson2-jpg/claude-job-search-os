"""The two discovery queues hold target-company names and are gitignored. The repo sits in an
iCloud-synced folder, whose conflict copies are named "name 2.ext", so an exact-name ignore
leaves the copy one `git add -A` from a public push (2026-11-25: "tools/.role-queue 2.json"
was found on disk; Grok s4e F2 named the company queue's copy)."""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("path", [
    "tools/.company-queue.json", "tools/.role-queue.json",
    "tools/.company-queue 2.json", "tools/.role-queue 2.json", "tools/.role-queue 3.json",
])
def test_the_queues_and_their_sync_copies_are_ignored(path):
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=REPO)
    assert r.returncode == 0, f"{path} is not gitignored"


def test_the_glob_does_not_ignore_tracked_queue_code():
    for path in ("tools/company_queue.py", "tools/role_queue_read.py"):
        r = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=REPO)
        assert r.returncode == 1, f"{path} must stay tracked"
