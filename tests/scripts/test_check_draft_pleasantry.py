"""Tests for tools/check_draft_pleasantry.py — the weekend/holiday pleasantry gate.

Covers the pure decision core (evaluate / timing_context) with fixed dates so
the timing logic is deterministic. Dates chosen:
  - 2026-07-24 Friday (weekday 4)      -> weekend rule
  - 2026-07-25 Saturday (weekday 5)    -> weekend rule
  - 2026-07-27 Monday (weekday 0)      -> ordinary weekday, no holiday -> allow
  - 2024-07-04 Thursday (weekday 3)    -> holiday branch, isolated from weekend
  - 2024-07-03 Wednesday               -> eve of the Fourth (tomorrow)
  - 2025-12-25 Thursday                -> Christmas, isolated from weekend
"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import check_draft_pleasantry as h  # noqa: E402

FRI = date(2026, 7, 24)
SAT = date(2026, 7, 25)
MON = date(2026, 7, 27)
JUL4_THU = date(2024, 7, 4)
JUL3_WED = date(2024, 7, 3)
XMAS_THU = date(2025, 12, 25)

NO_PLEASANTRY = "Hi Casey,\nResume attached. Talk Monday at 1.\nBest,\nNick"
WITH_WEEKEND = "Hi Casey,\nResume attached.\nHope you have a great weekend!\nBest,\nNick"


# --- timing_context ---------------------------------------------------------
@pytest.mark.parametrize("day,expect_block", [
    (FRI, True),         # Friday
    (SAT, True),         # Saturday
    (MON, False),        # ordinary Monday, no holiday
    (JUL4_THU, True),    # holiday (isolated weekday)
    (JUL3_WED, True),    # eve of the Fourth
    (XMAS_THU, True),    # Christmas (isolated weekday)
])
def test_timing_context(day, expect_block):
    assert (h.timing_context(day) is not None) == expect_block


# --- evaluate: blocks on weekend/holiday with no pleasantry -----------------
@pytest.mark.parametrize("day", [FRI, SAT, JUL4_THU, JUL3_WED, XMAS_THU])
def test_blocks_when_no_pleasantry_on_flagged_day(day):
    assert h.evaluate(NO_PLEASANTRY, day) is not None


# --- evaluate: allows when a pleasantry is present --------------------------
@pytest.mark.parametrize("body", [
    WITH_WEEKEND,
    "Hi,\nHope you had a great long weekend!\nResume attached.\nBest,\nNick",
    "Hi,\nHappy 4th of July! Resume attached.\nBest,\nNick",
    "Hi,\nHope your week is going well. Quick note.\nBest,\nNick",
    "Hi,\nEnjoy your vacation next week.\nBest,\nNick",
    "Hi,\nTake care,\nNick",
    "Hi,\nWishing you a restful break.\nNick",
])
def test_allows_when_pleasantry_present_even_on_weekend(body):
    assert h.evaluate(body, FRI) is None


# --- evaluate: allows on ordinary weekday regardless of pleasantry ----------
def test_allows_on_ordinary_weekday_without_pleasantry():
    assert h.evaluate(NO_PLEASANTRY, MON) is None


# --- evaluate: empty body is a no-op ----------------------------------------
def test_empty_body_allows():
    assert h.evaluate("", FRI) is None
    assert h.evaluate("   \n  ", SAT) is None
