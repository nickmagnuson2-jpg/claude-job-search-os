"""Every tool wired as a hook in settings.json must have a suite NAMED for it.

WHY THIS IS A TEST AND NOT A HOOK. There is no tool call to intercept: the defect is a
file that does not exist, discovered by looking at two directories. A PreToolUse hook
would have nothing to fire on. This is the check a gate reads.

WHY IT IS NOT COVERED BY THE MUTATION SWEEP. `mutation_sweep` skips any tool that maps to
zero test files, so a wired hook with no tests anywhere never enters the target list and
never appears in the baseline -- it is absent, not failing, which is the quiet half of the
problem. And `mutation_check.map_tests` selects by import reference as well as by filename,
so a hook with no suite of its own still gets a survival rate computed from tests written
for something else. On 2026-09-02 that produced `check_email_via_skill` at 23/23 and
`open_draft` at 113/113, both read as "tests that catch nothing" when the truth was "no
tests at all". Neither surface can see this; this test can.

KNOWN_MISSING is frozen debt, not permission. It exists so this test passes on the tree it
was written against and BLOCKS the next hook wired without a suite. Deleting an entry when
you write the suite is the point; adding one requires a reason in the same commit.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
TESTS = REPO_ROOT / "tests" / "scripts"

# Wired before this test existed, measured on 2026-09-02. Each is real exposure.
KNOWN_MISSING = {
    "check_data_projects": "no tests anywhere; never entered the mutation baseline",
    "check_edit_safety": "no tests anywhere; never entered the mutation baseline",
    "check_no_confabulation": "no tests anywhere; never entered the mutation baseline",
    "check_script_error_logged": "no tests anywhere; never entered the mutation baseline",
    "log_tool_failure": "no tests anywhere; never entered the mutation baseline",
    # check_email_via_skill retired 2026-09-02: tests/scripts/test_check_email_via_skill.py
    # scan_transcript_failures retired 2026-09-02: tests/scripts/test_scan_transcript_failures.py
}


def wired_hook_stems() -> set[str]:
    """Tool stems referenced from settings.json hook commands."""
    if not SETTINGS.exists():
        return set()
    return set(re.findall(r"tools/(\w+)\.py", SETTINGS.read_text(encoding="utf-8")))


def has_own_suite(stem: str) -> bool:
    return bool(list(TESTS.glob(f"test_{stem}*.py")))


def test_every_wired_hook_has_a_suite_named_for_it():
    missing = sorted(s for s in wired_hook_stems() if not has_own_suite(s))
    new = [s for s in missing if s not in KNOWN_MISSING]
    assert not new, (
        f"wired hook(s) with no tests/scripts/test_<name>.py: {new}. A hook without its "
        "own suite gets a survival rate from unrelated files that merely import it, which "
        "reads like a real measurement and is not one. Write the suite, or add an entry to "
        "KNOWN_MISSING with a reason.")


def stale_entries(mapping, covered) -> list[str]:
    """Allowlisted tools that now DO have a suite. Extracted so it can be tested."""
    return sorted(s for s in mapping if s in covered)


def test_the_stale_check_can_actually_fail():
    """Same falsifiability problem as the reason check: no entry has a suite today, so
    the live assertion below passes regardless of what the predicate does."""
    assert stale_entries({"a": "r"}, set()) == []
    assert stale_entries({"a": "r"}, {"a"}) == ["a"]
    assert stale_entries({"a": "r", "b": "r"}, {"b"}) == ["b"]


def test_known_missing_is_retired_as_suites_get_written():
    """A stale allowlist entry is how frozen debt turns into permanent debt."""
    stale = sorted(s for s in KNOWN_MISSING if has_own_suite(s))
    assert not stale, (
        f"{stale} now have their own suite -- delete them from KNOWN_MISSING so the entry "
        "cannot excuse a future regression.")


def blank_reasons(mapping: dict[str, str]) -> list[str]:
    """Entries whose reason is missing or whitespace. Extracted so it can be tested."""
    return sorted(k for k, v in mapping.items() if not str(v).strip())


def test_every_known_missing_entry_carries_a_reason():
    assert not blank_reasons(KNOWN_MISSING), (
        f"KNOWN_MISSING entries with no reason: {blank_reasons(KNOWN_MISSING)}. An "
        "allowlist without justification is how this decays back into 'green means done'.")


def test_the_reason_check_can_actually_fail():
    """Asserted against a synthetic blank, not only against live data.

    Every entry currently carries a reason, so `test_every_known_missing_entry_carries_a
    _reason` passes no matter what the predicate does -- inverting its condition left it
    green. A check that cannot fail on any input the repo contains is not a check yet.
    """
    assert blank_reasons({"a": "real reason"}) == []
    assert blank_reasons({"a": ""}) == ["a"]
    assert blank_reasons({"a": "   "}) == ["a"]
    assert blank_reasons({"a": "", "b": "ok", "c": "\t"}) == ["a", "c"]


def test_known_missing_only_lists_tools_that_exist():
    """Guards against a rename silently parking a live hook in the allowlist."""
    gone = sorted(k for k in KNOWN_MISSING if not (REPO_ROOT / "tools" / f"{k}.py").exists())
    assert not gone, f"KNOWN_MISSING names tools that no longer exist: {gone}"


def test_the_scan_actually_found_hooks():
    """The whole gate passes vacuously if the settings regex stops matching.

    Every other assertion here is of the form "nothing bad in this list". An empty list
    satisfies all of them, so a broken path, a renamed settings key, or a regex that stops
    matching turns this file green while checking nothing. This is the assertion that has
    to fail first. (`guard_must_hard_abort_on_empty_input`.)
    """
    stems = wired_hook_stems()
    assert SETTINGS.exists(), f"settings.json not found at {SETTINGS}"
    assert len(stems) >= 20, (
        f"only {len(stems)} wired hook(s) found in settings.json; this file has ~31. "
        "A low count means the scan broke, not that hooks were removed -- every other "
        "assertion in this module passes vacuously when this list is short.")
    assert TESTS.is_dir(), f"tests dir not found at {TESTS}"
