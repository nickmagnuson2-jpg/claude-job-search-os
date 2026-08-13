"""Synthetic voice-hook fixture — PATTERNS remedies that prescribe nothing banned.

Placeholder-worded on purpose (this repo is public). Mirrors the 3-tuple shape of
check_draft_voice.py's PATTERNS: (regex, diagnostic message, remedy).
"""
import re

PATTERNS = [
    (
        re.compile(r"\bplaceholder-tell\b", re.IGNORECASE),
        '"placeholder-tell" — banned filler (fixture)',
        "state the ask directly",
    ),
    (
        re.compile(r"\bnot\s+as\b[^.?!\n]{1,50}?\bbut\s+as\b", re.IGNORECASE),
        '"not as X, but as Y" — performs the contrast instead of stating substance',
        "drop the contrast and name the built artifact",
    ),
]
