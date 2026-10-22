#!/usr/bin/env python3
"""slug.py — the one slugify for the repo.

Fifteen slug functions grew independently across tools/ (2026-10-22 census). They agreed
on lowercase + non-alphanumerics-to-hyphens + trim and differed on four points, now
arguments or settled here:

  - Accents are FOLDED ('Núñez' -> 'nunez'), per the CLAUDE.md slug convention.
    Three company-slug copies did not fold, so a networking name with an accent missed
    the people file that /networking promote had already created under the folded slug.
  - `maxlen` caps the length WITHOUT leaving a trailing hyphen.
  - `fallback` is returned when nothing alphanumeric survives.
  - `drop_punctuation` deletes punctuation instead of splitting on it ("wasn't" ->
    "wasnt", "v1.2" -> "v12"), as the title/filename callers always did. Off by default:
    the person/company slug convention splits on it, and the PII denylist derives slug
    forms of real names from this function.

Stdlib only and import-cheap, so a reporter can use it without loading a heavy tool.
"""
import re
import unicodedata


def slugify(text: str, maxlen: int | None = None, fallback: str = "",
            drop_punctuation: bool = False) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    if drop_punctuation:
        t = re.sub(r"[^\w\s-]", "", t)
    t = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    if maxlen:
        t = t[:maxlen].rstrip("-")
    return t or fallback
