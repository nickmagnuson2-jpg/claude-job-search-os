"""Synthetic voice-hook fixture reproducing the 2026-08-13 blind spot.

The remedy string PRESCRIBES the construction that content-rules B7 bans. This is the
shape that survived the 8/11 sweep: voice-reference.md and content-rules were both
corrected, but the hook -- which fires on every draft -- kept recommending the phrase.

Element [1] (the diagnostic message) also names the banned phrase, by design. The
checker must read ONLY element [2], or every correctly-authored rule false-positives.
"""
import re

PATTERNS = [
    (
        re.compile(r"\bnot\s+as\b[^.?!\n]{1,50}?\bbut\s+as\b", re.IGNORECASE),
        '"not as X, but as Y" — performs the contrast instead of stating substance',
        'use "X rather than Y" (corpus-validated)',
    ),
]
