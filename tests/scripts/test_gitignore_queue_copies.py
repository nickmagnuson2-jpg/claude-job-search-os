"""The two discovery queues hold target-company names and are gitignored. The repo sits in an
iCloud-synced folder, whose conflict copies are named "name 2.ext", so an exact-name ignore
leaves the copy one `git add -A` from a public push (2026-11-25: "tools/.role-queue 2.json"
was found on disk; Grok s4e F2 named the company queue's copy). The writers also leave full
copies under other names: the scanner's quarantine (`.corrupt`) and an interrupted atomic
write's temp file (inbox_lock, scanner). One glob, `tools/.*-queue*`, covers every shape."""
import fnmatch
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GLOB = "tools/.*-queue*"


def _ignored(path: str) -> bool:
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=REPO)
    assert r.returncode in (0, 1), r
    return r.returncode == 0


@pytest.mark.parametrize("path", [
    "tools/.company-queue.json", "tools/.role-queue.json",
    "tools/.company-queue 2.json", "tools/.role-queue 2.json", "tools/.role-queue 3.json",
    # any copy number, not only the 2 and 3 seen so far (Codex s4f F1)
    "tools/.role-queue 12.json", "tools/.company-queue 47.json",
    # the scanner's quarantine copy of a corrupt queue (scanner.py, Fable s4f F1)
    "tools/.role-queue.json.corrupt",
    # an interrupted atomic write: inbox_lock's mkstemp(prefix=".<name>.") (Fable s4f F3)
    "tools/..company-queue.json.ab12cd34.tmp", "tools/..role-queue.json.ab12cd34.tmp",
    # the scanner's temp file once it carries the queue's name as its prefix
    "tools/.role-queue.json.ab12cd34.tmp",
])
def test_the_queues_and_every_copy_of_them_are_ignored(path):
    assert _ignored(path), f"{path} is not gitignored"


def test_the_glob_ignores_no_tracked_file():
    """Every tracked path stays tracked: no tracked file matches the glob, and git ignores
    none of them (Fable s4f F4: the old check named two undotted files and could not fail)."""
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                             check=True).stdout.decode().split("\0")
    tracked = [p for p in tracked if p]
    assert tracked
    assert [p for p in tracked if fnmatch.fnmatchcase(p, GLOB)] == []
    r = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-z"], cwd=REPO,
                       input="\0".join(tracked).encode(), capture_output=True)
    ignored = [p for p in r.stdout.decode().split("\0") if p.startswith("tools/")]
    assert ignored == [], f"tracked tools files ignored: {ignored}"


def test_the_glob_is_in_gitignore():
    lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert GLOB in lines


def test_the_scanners_temp_file_is_covered_by_the_glob(tmp_path, monkeypatch):
    """An interrupted queue write leaves its temp file behind with the full queue in it, so
    the temp name must match the ignore glob (Fable s4f F3: mkstemp's default 'tmpXXXX.tmp'
    did not)."""
    import os
    import sys
    sys.path.insert(0, str(REPO))
    from tools.career_scanner import scanner

    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(Path(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    path = scanner.write_queue_payload(tmp_path, {"roles": []})
    assert len(seen) == 1
    rel = (path.parent / seen[0].name).relative_to(tmp_path).as_posix()
    assert fnmatch.fnmatchcase(rel, GLOB), rel
    assert _ignored(rel), rel
