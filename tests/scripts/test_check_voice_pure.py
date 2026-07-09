"""Tests for tools/check_voice_pure.py — the voice-pure dated-reflection gate.

Covers the core purpose (block Claude-voice prose >100 chars outside a
blockquote in data/reflections/YYYY-MM-DD*.md) AND the 2026-07-08 lazy-
continuation fix: a paragraph immediately following a blockquote paragraph,
with no "> " of its own, is treated as a continuation of the same quote
rather than new unquoted prose (regression for the documented "False-positive
blocks valid Edit appends" friction row, memory/friction-log.md 2026-06-04).

The exemption is bounded to exactly ONE paragraph past the blockquote anchor
(a same-session follow-up fix): an earlier version left prev_was_blockquote
True indefinitely, letting a single short "> ok" line unlock an unbounded run
of unquoted paragraphs after it — verified live to let arbitrary-length
Claude-authored prose through this gate. See
test_lazy_continuation_does_not_chain_past_one_paragraph below.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_voice_pure.py"
_spec = importlib.util.spec_from_file_location("check_voice_pure", SCRIPT)
cvp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cvp)

LONG = "x" * 101  # exceeds LONG_PARA_THRESHOLD (100)


def violations(content, check_frontmatter=False):
    return cvp.find_violations(content, check_frontmatter=check_frontmatter)


# --- core purpose must still hold: unquoted long prose is blocked ----------

def test_blocks_unquoted_long_prose():
    assert violations(f"Some intro.\n\n{LONG}")


def test_allows_blockquoted_long_prose():
    assert not violations(f"> {LONG}")


def test_allows_short_unquoted_context_note():
    assert not violations("A short note under 100 chars.")


def test_allows_heading():
    assert not violations(f"# {LONG}")


def test_allows_bullet_list():
    body = "\n".join(f"- item {i} {LONG}" for i in range(2))
    assert not violations(body)


# --- lazy continuation: paragraph right after a blockquote, no marker ------

def test_lazy_continuation_after_long_blockquote_is_allowed():
    content = f"> {LONG}\n\n{LONG} continuing the same quote without a marker."
    assert not violations(content)


def test_lazy_continuation_after_short_blockquote_anchor_is_allowed():
    # Mirrors an Edit "append" where new_string = short anchor tail (already
    # quoted, <=100 chars) + a newly appended long paragraph.
    content = f"> end of prior quote text\n\n{LONG} newly appended verbatim text."
    assert not violations(content)


def test_lazy_continuation_does_not_chain_past_one_paragraph():
    # Regression for the unbounded-exemption bug found in code review
    # (2026-07-08): a blockquote anchor exempts exactly the ONE paragraph
    # immediately after it, not an indefinite run. A genuine multi-paragraph
    # verbatim quote is expected to carry "> " on each of its own paragraphs.
    content = f"> {LONG}\n\n{LONG} second, exempt as the one lazy continuation.\n\n{LONG} third, must be flagged, not silently exempted too."
    v = violations(content)
    # Exactly one violation: the second paragraph is the bounded exemption
    # (allowed), the third is genuinely new unquoted prose (flagged).
    assert len(v) == 1


def test_lazy_continuation_single_paragraph_still_allowed():
    # The bounded exemption still covers exactly the reported bug shape: one
    # appended paragraph right after the blockquote anchor.
    content = f"> {LONG}\n\n{LONG} second, exempt as the one lazy continuation."
    assert not violations(content)


def test_re_quoting_after_a_lazy_paragraph_re_arms_the_exemption():
    # A THIRD paragraph is allowed again if it re-establishes the quote with
    # its own "> " marker — the bound is per-anchor, not a one-shot budget
    # for the whole document.
    content = (
        f"> {LONG}\n\n"
        f"{LONG} second, exempt as the one lazy continuation.\n\n"
        f"> {LONG} third, re-quoted with its own marker."
    )
    assert not violations(content)


def test_short_unquoted_aside_between_long_ones_breaks_the_chain():
    # A short UNQUOTED paragraph is its own allowed shape (a short context
    # note), but it is not itself part of the quote — it correctly breaks
    # the lazy-continuation chain, so a long paragraph after it is evaluated
    # fresh (conservative default: don't extend leniency past an unquoted
    # break just because it happened to be short).
    content = f"> {LONG}\n\nshort aside\n\n{LONG} no longer covered by lazy continuation."
    assert violations(content)


def test_short_quoted_aside_between_long_ones_preserves_the_chain():
    content = f"> {LONG}\n\n> short quoted aside\n\n{LONG} still continuing the quote."
    assert not violations(content)


# --- lazy continuation must NOT bypass the gate for genuinely new prose ----

def test_unquoted_prose_after_a_heading_still_blocks():
    content = f"# Heading\n\n{LONG}"
    assert violations(content)


def test_unquoted_prose_after_a_bullet_list_still_blocks():
    content = f"- item {LONG}\n\n{LONG} unrelated prose paragraph."
    assert violations(content)


def test_unquoted_prose_as_first_paragraph_still_blocks():
    assert violations(f"{LONG} with nothing before it.")


def test_unquoted_prose_two_paragraphs_after_a_blockquote_still_blocks():
    # First continuation is exempt (lazy), but only while the chain holds —
    # an intervening heading/list/code-fence breaks it (this test uses a
    # heading break) and prose after the break is evaluated fresh.
    content = f"> {LONG}\n\n# Heading breaks the quote\n\n{LONG} new unquoted prose."
    assert violations(content)


# --- frontmatter checks (Write only) untouched by this change --------------

def test_write_requires_frontmatter():
    errs = violations("no frontmatter here", check_frontmatter=True)
    assert any("frontmatter" in e.lower() for e in errs)


def test_write_with_correct_frontmatter_and_quoted_body_is_clean():
    content = f"---\nvoice: pure-voice\n---\n\n> {LONG}"
    assert not violations(content, check_frontmatter=True)
