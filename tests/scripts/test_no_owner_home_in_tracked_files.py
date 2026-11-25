"""No tracked file names this machine's home folder: the repo is public and fork-safe.

Origin 2026-11-25 (S1, org item 1): the owner's literal home path sat in 20 tracked files
(a tool, install.sh, 14 launchd plists, 4 tests). They now derive it at run time
(hook_runtime.claude_project_dir, vault_paths.main_checkout, os.path.expanduser) and the
plists are templates that tools/launchd/install.sh renders. The check uses THIS machine's
home, so it is real on the owner's machine and trivially green on a fork.

One explicit exception, by pattern, not by skipping files: the launchd label and file-name
prefix "com.<handle>.jobsearch." carries the owner's handle. It is launchd's job identity,
not a home path; renaming it unloads every agent and touches every tool that names a label,
so it is Nick's decision (MORNING, 2026-11-25), and only that exact token is allowed.
"""
import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOME = os.path.expanduser("~").rstrip("/")
NEEDLES = (HOME + "/", HOME.replace("/", "-") + "-")
LABEL_PREFIX = re.compile(r"com\.[A-Za-z0-9]+\.jobsearch\.")   # the one allowed exception


def offenders(root: Path, needles=NEEDLES) -> list[str]:
    files = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True,
                           text=True, check=True).stdout.split("\0")
    out = []
    for rel in filter(None, files):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError, IsADirectoryError):
            continue
        text = LABEL_PREFIX.sub("", text)
        if any(n in text for n in needles):
            out.append(rel)
    return out


def test_no_tracked_file_names_this_machines_home():
    assert offenders(REPO_ROOT) == []


def test_the_check_sees_a_planted_home_path(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text("P = '/home/someone/repo'\n", encoding="utf-8")
    (tmp_path / "b.plist").write_text("<string>com.someone.jobsearch.x</string>\n",
                                      encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.py", "b.plist"], check=True)
    needles = ("/home/someone/", "-home-someone-")
    assert offenders(tmp_path, needles) == ["a.py"]
