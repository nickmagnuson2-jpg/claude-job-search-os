"""Tests for Ranker cache-poisoning fix — fable-audit 2026-07-07 #15.

A transient LLM ranking failure makes rank_for_profiles return None; the old
Ranker.rank attached that None and cached it, so the profile returned a null rank
forever. The fix: don't cache a None rank on write, and evict any already-poisoned
(rank=None) entry on read.

Isolated so it runs with no optional deps: mocks anthropic/termcolor and the heavy
scanner submodules (Scraper/ProfileParser/cache), then cleans the submodule mocks up
so other test files import the real ones.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / "tools" / "linkedin-scanner"

if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = MagicMock()
_added_termcolor = "termcolor" not in sys.modules
if _added_termcolor:
    sys.modules["termcolor"] = MagicMock()
_added_src = []
for _m in ("src.Scraper", "src.ProfileParser", "src.cache"):
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()
        _added_src.append(_m)

if str(SCANNER) not in sys.path:
    sys.path.insert(0, str(SCANNER))

import src.llm as llm            # noqa: E402
import src.Ranker as rankermod   # noqa: E402

Ranker = rankermod.Ranker

# Don't leak the scanner-submodule mocks into other test files.
if _added_termcolor:
    del sys.modules["termcolor"]
for _m in _added_src:
    del sys.modules[_m]


def _bare_ranker():
    """A Ranker with no __init__ side effects (Scraper() would launch a browser)."""
    r = Ranker.__new__(Ranker)
    r.loaded_cache = True
    r.cached_profiles = {}
    return r


# ── read path: evict poisoned entries ─────────────────────────────────────────

def test_cached_profile_evicts_null_rank():
    r = _bare_ranker()
    r.cached_profiles = {"k": {"rank": None,
                               "metadata": {"rank_prompt_id": llm.RANK_PROMPT_ID}}}
    assert r.cached_profile("k") is None
    assert "k" not in r.cached_profiles  # evicted, not returned


def test_cached_profile_returns_valid_entry():
    r = _bare_ranker()
    good = {"rank": {"aggregate_rating": 20},
            "metadata": {"rank_prompt_id": llm.RANK_PROMPT_ID}}
    r.cached_profiles = {"k": good}
    assert r.cached_profile("k") is good


# ── write path: never cache a None rank ───────────────────────────────────────

def test_rank_none_is_not_cached(monkeypatch):
    r = _bare_ranker()
    r.scraper = MagicMock()
    r.scraper.profile_key.return_value = "k"
    r.profile_parser = MagicMock()
    r.profile_parser.cached_profile.return_value = {"experience": [{}]}
    monkeypatch.setattr(llm, "rank_for_profile", lambda *a, **kw: None)
    discarded = {}
    r.discard_profile = lambda url, **kw: discarded.__setitem__("url", url)

    out = r.rank("http://x", use_cache=True, discard_failed=True)

    assert out is None
    assert r.cached_profiles == {}, "a None rank must not poison the cache"
    assert discarded.get("url") == "http://x"


def test_rank_success_is_cached(monkeypatch):
    r = _bare_ranker()
    r.scraper = MagicMock()
    r.scraper.profile_key.return_value = "k"
    r.profile_parser = MagicMock()
    r.profile_parser.cached_profile.return_value = {"experience": [{}]}
    monkeypatch.setattr(llm, "rank_for_profile", lambda *a, **kw: {"aggregate_rating": 20})

    out = r.rank("http://x", use_cache=True)

    assert out is not None
    assert "k" in r.cached_profiles
    assert r.cached_profiles["k"]["rank"] == {"aggregate_rating": 20}
