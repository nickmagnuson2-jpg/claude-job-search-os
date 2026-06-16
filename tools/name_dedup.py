#!/usr/bin/env python3
"""
name_dedup.py — Fuzzy name-duplicate detection for contact/entity adds.

Exact-string matching misses spelling variants that are the SAME person:
  - "Jordan Sample" vs "Jordan Samples"   (transposition / dropped letter)
  - "Alex Example" vs "Alexander Example"  (nickname / full name)
  - "Jose Doe" vs "José Doe"               (accent folding)

These create silent duplicate roster rows. This module is the pre-add guard:
normalize names, then compare with difflib, so a near-match BLOCKS the add until
the caller confirms (e.g. via --force) or picks the existing record.

Stdlib only (difflib + unicodedata) — no external deps, safe to import anywhere.

Origin: 2026-06-12 — a contact was added under a variant spelling while the same
person already existed under a slightly different spelling; the exact-match dup
check did not catch the variant.
See memory/feedback_dedup_before_appending_to_tracking_doc.md.
"""
import re
import unicodedata
from difflib import SequenceMatcher

# Default similarity threshold. Tuned so real-world variants that motivated this
# guard are caught (one-letter spelling differences ~0.94, nickname/full ~0.96)
# while clearly distinct names (different surnames) stay well below. A false
# positive costs one --force; a false negative is a silent duplicate, which is
# the failure this guard exists to prevent.
DEFAULT_THRESHOLD = 0.84


def normalize_name(name: str) -> str:
    """Lowercase, fold accents, strip punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def similarity(a: str, b: str) -> float:
    """Normalized similarity ratio in [0, 1]."""
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


def find_near_duplicates(name, existing, threshold=DEFAULT_THRESHOLD):
    """Return [(existing_name, ratio), ...] sorted by ratio desc.

    Includes normalized-exact matches (ratio 1.0), e.g. accent-only differences,
    since those are duplicates too. The caller decides how to handle the list
    (block, warn, prompt). An empty list means "no near-duplicate found."
    """
    results = []
    for e in existing:
        if e is None:
            continue
        r = similarity(name, e)
        if r >= threshold:
            results.append((e, round(r, 3)))
    results.sort(key=lambda x: x[1], reverse=True)
    return results
