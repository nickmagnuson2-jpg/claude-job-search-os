"""Tests for voice_features.py calendly-token config — fable-audit 2026-07-07 #6.

The tool previously hardcoded a real personal token (calendly.com/<handle>) in a
public tool file. The fix reads it from VOICE_CALENDLY_HANDLE with a placeholder
default, so nothing personal ships in the public repo. These tests lock in both the
configurability and the no-real-token guarantee.
"""
import importlib
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _reload(monkeypatch, handle):
    """(Re)import voice_features with VOICE_CALENDLY_HANDLE set/unset so the
    module-level CALENDLY_TOKEN is recomputed under the patched environment."""
    if handle is None:
        monkeypatch.delenv("VOICE_CALENDLY_HANDLE", raising=False)
    else:
        monkeypatch.setenv("VOICE_CALENDLY_HANDLE", handle)
    import voice_features
    return importlib.reload(voice_features)


def test_calendly_token_defaults_to_placeholder(monkeypatch):
    vf = _reload(monkeypatch, None)
    assert vf.CALENDLY_TOKEN == "calendly.com/your-handle"


def test_calendly_token_reads_env_override(monkeypatch):
    vf = _reload(monkeypatch, "calendly.com/somebody-else")
    assert vf.CALENDLY_TOKEN == "calendly.com/somebody-else"


def test_no_real_calendly_token_hardcoded_in_source():
    """The public source must not hardcode ANY personal calendly handle. Asserted
    without naming the real handle (which would just re-embed it in a public test):
    every calendly.com/<handle> literal in the source must be the placeholder default."""
    import re
    src = (TOOLS / "voice_features.py").read_text(encoding="utf-8")
    handles = re.findall(r"calendly\.com/[\w-]+", src)
    assert all(h == "calendly.com/your-handle" for h in handles), (
        f"non-placeholder calendly handle hardcoded in voice_features.py: {handles}")
