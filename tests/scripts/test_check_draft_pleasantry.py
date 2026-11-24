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


# --- Veterans Day: the direct thanks counts as the pleasantry ----------------
# Nick's ruling 2026-11-22 item 8.4: near Veterans Day, "Thank you for your service"
# is an accepted pleasantry; "Happy Veterans Day" is not the right greeting.
VETS_TUE = date(2025, 11, 11)   # Veterans Day on a Tuesday, isolated from the weekend rule
VETS_EVE_MON = date(2025, 11, 10)


def test_veterans_day_is_a_flagged_day():
    assert h.timing_context(VETS_TUE) is not None
    assert h.timing_context(VETS_EVE_MON) is not None


@pytest.mark.parametrize("body", [
    "Hi,\nThank you for your service this Veterans Day.\nBest,\nNick",
    "Hi,\nResume attached.\nThank you for your service.\nBest,\nNick",
    "Hi,\nthank  you for your service, and talk soon.\nNick",
])
@pytest.mark.parametrize("day", [VETS_TUE, VETS_EVE_MON])
def test_thank_you_for_your_service_counts_as_pleasantry(body, day):
    assert h.evaluate(body, day) is None


def test_happy_veterans_day_alone_still_blocks():
    # Not added to the accepted list on purpose: Nick ruled it the wrong greeting.
    assert h.evaluate("Hi,\nHappy Veterans Day!\nBest,\nNick", VETS_TUE) is not None


# --- main(): the hook entry point, run in-process -----------------------------
# Until 2026-11-23 no test called main(), so the override, the open_draft filter, the
# fail-open exits and the exit-2 block could each be removed with every test green
# (mutation on 2a486185: 20 survivors, all here). These tests never touch the real
# tools/.pending-draft.txt: DRAFT_FILE is pointed under tmp_path and the fixture asserts it.
OPEN_DRAFT_CMD = "python3 tools/open_draft.py"


class _FakeDate(date):
    @classmethod
    def today(cls):
        return cls(FRI.year, FRI.month, FRI.day)


@pytest.fixture
def hook(tmp_path, monkeypatch):
    draft = tmp_path / "pending-draft.txt"
    monkeypatch.setattr(h, "DRAFT_FILE", draft)
    assert h.DRAFT_FILE.resolve().is_relative_to(tmp_path.resolve())
    monkeypatch.setattr(h, "date", _FakeDate)
    monkeypatch.delenv("DRAFT_OVERRIDE", raising=False)
    monkeypatch.setattr(h, "read_hook_command", lambda: OPEN_DRAFT_CMD)
    return draft


def _exit_code():
    with pytest.raises(SystemExit) as e:
        h.main()
    return e.value.code


def _draft(body):
    return f"TO: someone@example.com\nSUBJECT: Hi\nBODY:\n{body}\n"


def test_main_blocks_with_exit_2_and_message(hook, capsys):
    hook.write_text(_draft(NO_PLEASANTRY))
    assert _exit_code() == 2
    assert "BLOCKED" in capsys.readouterr().err


def test_main_allows_a_draft_with_a_pleasantry(hook):
    hook.write_text(_draft(WITH_WEEKEND))
    assert _exit_code() == 0


def test_main_allows_thank_you_for_your_service(hook):
    hook.write_text(_draft("Hi,\nThank you for your service this Veterans Day.\nNick"))
    assert _exit_code() == 0


def test_main_override_env_allows(hook, monkeypatch):
    hook.write_text(_draft(NO_PLEASANTRY))
    monkeypatch.setenv("DRAFT_OVERRIDE", "1")
    assert _exit_code() == 0


def test_main_override_other_value_still_blocks(hook, monkeypatch):
    hook.write_text(_draft(NO_PLEASANTRY))
    monkeypatch.setenv("DRAFT_OVERRIDE", "0")
    assert _exit_code() == 2


def test_main_ignores_a_command_that_is_not_open_draft(hook, monkeypatch):
    hook.write_text(_draft(NO_PLEASANTRY))
    monkeypatch.setattr(h, "read_hook_command", lambda: "ls tools")
    assert _exit_code() == 0


def test_main_fails_open_when_no_draft_file(hook):
    assert not hook.exists()
    assert _exit_code() == 0


def test_main_fails_open_when_draft_unreadable(hook):
    hook.mkdir()  # read_text on a directory raises
    try:
        h.main()
        outcome = "returned"
    except SystemExit as e:
        outcome = f"exit {e.code}"
    except Exception as e:  # without the fail-open exit, main() goes on and crashes
        outcome = type(e).__name__
    assert outcome == "exit 0"


# --- script entry point: a subprocess runs a tmp_path COPY of the hook ----------
# Kills the `if __name__ == "__main__": main()` mutants (Codex r1 F3, 2026-11-23).
# DRAFT_FILE derives from the running script's own __file__, so the copy reads
# tmp_path/.pending-draft.txt and never the real tools/.pending-draft.txt.
import json as _json
import shutil as _shutil
import subprocess as _subprocess

_TOOLS = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
_HOOK_MODULES = ("check_draft_pleasantry.py", "check_draft_voice.py", "hook_command_lint.py", "hook_runtime.py", "shell_tokens.py")
_RUNNER = (
    "import datetime, runpy, sys\n"
    "class D(datetime.date):\n"
    "    @classmethod\n"
    "    def today(cls):\n"
    "        return cls(2026, 7, 24)\n"  # a Friday: a flagged day
    "datetime.date = D\n"
    "runpy.run_path(sys.argv[1], run_name='__main__')\n"
)


def _run_copy(tmp_path, body):
    copy = tmp_path / "check_draft_pleasantry.py"
    for name in _HOOK_MODULES:
        _shutil.copy(os.path.join(_TOOLS, name), tmp_path / name)
    (tmp_path / ".pending-draft.txt").write_text(_draft(body))
    # No PYTHONPATH: under runpy the hook's own sys.path.insert is the only way its
    # siblings import (Codex r2 F2), so removing that line must break this test.
    env = {k: v for k, v in os.environ.items() if k not in ("DRAFT_OVERRIDE", "PYTHONPATH")}
    payload = _json.dumps({"tool_name": "Bash", "tool_input": {"command": OPEN_DRAFT_CMD}})
    r = _subprocess.run([sys.executable, "-I", "-c", _RUNNER, str(copy)], input=payload,
                        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    assert copy.resolve().parent == tmp_path.resolve()
    return r


def test_script_entry_point_blocks_via_a_tmp_copy(tmp_path):
    r = _run_copy(tmp_path, NO_PLEASANTRY)
    assert r.returncode == 2, r.stderr
    assert "BLOCKED" in r.stderr


def test_script_entry_point_allows_via_a_tmp_copy(tmp_path):
    r = _run_copy(tmp_path, WITH_WEEKEND)
    assert r.returncode == 0, r.stderr


class _RacingDraft:
    """exists() says missing; read_text() then returns a blocking draft, as if the file
    were created between the two calls (Codex r2 F1)."""
    def exists(self):
        return False

    def read_text(self, *a, **k):
        return _draft(NO_PLEASANTRY)


def test_main_trusts_the_exists_check_over_a_draft_created_after_it(hook, monkeypatch):
    monkeypatch.setattr(h, "DRAFT_FILE", _RacingDraft())
    assert _exit_code() == 0
