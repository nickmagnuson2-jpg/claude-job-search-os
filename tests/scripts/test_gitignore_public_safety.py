"""Guards that sensitive paths stay gitignored in this PUBLIC repo.

fable-audit 2026-07-07 #10 — the linkedin-scanner cache holds scraped profiles,
parsed JSON, a pickled live session cookie, and login screenshots.
fable-audit 2026-07-07 #13 — .claude/settings.local.json is local-only config.
Both must be ignored so they can never be committed into the public tree.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _is_ignored(rel_path):
    """True when git treats rel_path as ignored (check-ignore -q exits 0)."""
    r = subprocess.run(
        ["git", "-C", str(REPO), "check-ignore", "-q", rel_path],
        capture_output=True,
    )
    return r.returncode == 0


def test_linkedin_scanner_cache_is_gitignored():
    # A representative file inside the cache dir (the dir itself may not exist).
    assert _is_ignored("tools/linkedin-scanner/src/cache/ranked_profiles.json")


def test_linkedin_scanner_cache_cookie_is_gitignored():
    assert _is_ignored("tools/linkedin-scanner/src/cache/session_cookie.pkl")


def test_settings_local_json_is_gitignored():
    assert _is_ignored(".claude/settings.local.json")
