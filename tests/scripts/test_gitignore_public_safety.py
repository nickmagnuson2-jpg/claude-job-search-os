"""Guards that sensitive paths stay gitignored in this PUBLIC repo.

fable-audit 2026-07-07 #10 — the linkedin-scanner cache holds scraped profiles,
parsed JSON, a pickled live session cookie, and login screenshots.
fable-audit 2026-07-07 #13 — .claude/settings.local.json is local-only config.
Both must be ignored so they can never be committed into the public tree.
fixes-1030 review 9.F1/9.F2 (2026-11-01): the Gmail OAuth token and sync state, with the
temps a death leaves behind and a Finder duplicate.
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


def test_gmail_token_and_its_leftovers_are_gitignored():
    # The token holds OAuth credentials. gmail_fetch writes it through a '<name>.tmp.<pid>'
    # temp, and a Finder duplicate of it is named 'gmail_token 2.json'.
    for name in ("gmail_token.json", "gmail_token.json.tmp.4242", "gmail_token 2.json"):
        assert _is_ignored(f"tools/{name}"), name


def test_gmail_state_files_and_their_leftovers_are_gitignored():
    for name in (".gmail_state.json", ".gmail_state_personal.json",
                 ".gmail_state.json.tmp.4242", ".gmail_state_personal.json.tmp.4242",
                 ".gmail_state 2.json"):
        assert _is_ignored(f"tools/{name}"), name


def test_calendar_tokens_and_their_leftovers_are_gitignored():
    # calendar_fetch.py and calendar_write.py write their OAuth tokens through
    # gmail_fetch._write_token_atomic, so a death leaves '<name>.tmp.<pid>' behind.
    for stem in ("calendar_token", "calendar_write_token"):
        for name in (f"{stem}.json", f"{stem}.json.tmp.4242", f"{stem} 2.json"):
            assert _is_ignored(f"tools/{name}"), name


def test_daily_stoic_state_and_its_temp_are_gitignored():
    # daily_stoic.py writes '<state>.json.tmp' and then replaces; the state holds Gmail ids.
    for name in (".daily_stoic_state.json", ".daily_stoic_state.json.tmp"):
        assert _is_ignored(f"tools/{name}"), name


def test_gmail_oauth_client_file_and_its_copies_are_gitignored():
    # The OAuth client file holds the client secret; gmail_fetch.py and calendar_fetch.py
    # both read it. A Finder duplicate or a .bak copy must be ignored too.
    for name in ("gmail_credentials.json", "gmail_credentials 2.json", "gmail_credentials.json.bak"):
        assert _is_ignored(f"tools/{name}"), name
