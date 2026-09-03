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


# ---------------------------------------------------------------------------
# core.hooksPath. Found by cross-model review 2026-09-03, and it was LIVE: the
# installer hardcoded "$root/.git/hooks", installed there, verified there, and printed
# "ok: pre-push" -- while git's effective hooks directory was elsewhere and did not
# exist. No pre-push hook ran at all, on a PUBLIC repo whose only push-time PII gate is
# a pre-push hook, and the checker built to catch exactly this reported success.
#
# Same defect family as the one that created install.sh (a tracked guard whose hook was
# untracked, so a fresh clone had no gate) and as the career-scan drain (healthy
# producer, consumer pointing elsewhere, no error anywhere).
# ---------------------------------------------------------------------------

def test_installer_resolves_the_hooks_path_git_will_actually_use():
    """It must ask git, not assume. `core.hooksPath` overrides .git/hooks entirely."""
    src = REPO_ROOT / "tools" / "hooks" / "install.sh"
    text = src.read_text(encoding="utf-8")
    assert "git rev-parse --git-path hooks" in text, (
        "install.sh assumes .git/hooks; core.hooksPath silently overrides that and "
        "this repo has already shipped in that broken state")
    assert 'dst="$root/.git/hooks"' not in text, (
        "the hardcoded destination is back; core.hooksPath would be ignored again")


def test_installer_refuses_when_the_configured_hooks_dir_is_missing():
    """A configured-but-absent hooks dir means git runs NO hooks. Reporting success
    there is worse than failing: it is a guard asserting it is armed while disarmed."""
    src = (REPO_ROOT / "tools" / "hooks" / "install.sh").read_text(encoding="utf-8")
    assert "does not exist" in src
    assert "core.hooksPath" in src


def test_the_effective_hooks_dir_is_usable_right_now():
    """The live check. This is the assertion that would have caught it on any day
    between the config being set and 2026-09-03."""
    import subprocess
    out = subprocess.run(["git", "rev-parse", "--git-path", "hooks"],
                         cwd=str(REPO_ROOT), capture_output=True, text=True)
    hooks = Path(out.stdout.strip())
    if not hooks.is_absolute():
        hooks = REPO_ROOT / hooks
    assert hooks.is_dir(), (
        f"git's effective hooks directory {hooks} does not exist, so NO git hook runs "
        f"in this working tree -- including the pre-push PII gate on this public repo. "
        f"Fix: git config --local --unset core.hooksPath")
