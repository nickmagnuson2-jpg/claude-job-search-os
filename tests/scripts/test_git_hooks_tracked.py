"""The pre-push PII gate must be tracked, not just present on this machine.

Origin: .git/hooks/ is not version-controlled. tools/prepush_pii_guard.py was
tracked but the hook that CALLS it was not, so a fresh clone of this PUBLIC repo
silently had no push-time PII gate. The guard existed and never ran.
"""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_SRC = REPO_ROOT / "tools" / "hooks"


def _tracked(rel: str) -> bool:
    r = subprocess.run(["git", "ls-files", "--error-unmatch", rel],
                       cwd=REPO_ROOT, capture_output=True, text=True)
    return r.returncode == 0


def test_pre_push_hook_is_tracked():
    assert (HOOKS_SRC / "pre-push").is_file()
    assert _tracked("tools/hooks/pre-push"), \
        "tools/hooks/pre-push exists but is not tracked by git"


def test_installer_is_tracked_and_executable():
    inst = HOOKS_SRC / "install.sh"
    assert inst.is_file() and _tracked("tools/hooks/install.sh")


def test_tracked_hook_invokes_the_pii_guard():
    """A hook that no longer calls the guard is worse than no hook: it looks safe."""
    body = (HOOKS_SRC / "pre-push").read_text(encoding="utf-8")
    assert "prepush_pii_guard.py" in body
    assert "claude-job-search-os" in body, \
        "the hook must scope itself to the PUBLIC remote"


def test_installed_hook_matches_the_tracked_copy():
    """Drift means the thing running is not the thing reviewed."""
    installed = REPO_ROOT / ".git" / "hooks" / "pre-push"
    if not installed.exists():
        import pytest
        pytest.skip("hook not installed here; run bash tools/hooks/install.sh")
    assert installed.read_text(encoding="utf-8") == \
        (HOOKS_SRC / "pre-push").read_text(encoding="utf-8"), \
        "installed pre-push differs from tools/hooks/pre-push; re-run the installer"
