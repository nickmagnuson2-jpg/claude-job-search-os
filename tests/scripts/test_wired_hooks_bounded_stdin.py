"""Every wired hook must return on a RETAINED stdin pipe. The adoption gate.

WHAT THIS IS. `tools/hook_runtime.py` exists so 33+ hooks stop hand-rolling their payload
intake. The handoff that specified it was explicit that the hard part is not the extraction
but ADOPTION: an import is a dependency the interpreter enforces, so it cannot DRIFT, but
nothing stops a new hook from writing its own copy and never importing anything. Two
structural gates were proposed and both correctly rejected --

  1. "no consumer defines its own intake"  -> proves ABSENCE of a copy, not adoption.
  2. "every consumer imports the module"   -> PRESENCE, and CLAUDE.md line 90 rejects
     presence-only checks as a valid exit1 outright.

So this gate does not look at imports at all. It asserts the BEHAVIOUR that adoption
delivers: hand the hook a valid payload on a pipe that is never closed, and it must still
exit. A hook with a private `json.load(sys.stdin)` waits for an EOF that never comes and
hangs until the host kills it -- the measured 300-second stall of 2026-09-06.

WHY BEHAVIOURAL IS THE RIGHT SHAPE HERE. A hook satisfies this by importing the shared
module, or by writing its own correct bounded read. Both are fine, because the property IS
the thing we want; the import is merely the cheapest way to get it. That is the difference
between checking adoption and checking a spelling.

THE REGISTRY IS THE UNION OF THREE SETTINGS FILES, via `check_hook_warn_tier.wired_hooks`.
Not re-derived here: a gate keyed to `.claude/settings.json` alone silently ignores anything
wired in `.claude/settings.local.json` or the global `~/.claude/settings.json`, which is the
allowlist-scope-undercovers failure this repo has now hit seven times. Measured 2026-09-07:
the union currently contributes nothing beyond the project file (the global file wires 21
hooks, all .js/.sh), but the gate must not depend on that staying true.

DRAIN, DO NOT GROW. `UNBOUNDED_ALLOW` is a frozen list of hooks that still hang, each with a
reason. `test_no_stale_allowlist_entries` fails when an allowlisted hook starts passing, so
the list can only shrink. A NEW wired hook is covered automatically, because the registry is
the wiring rather than a hand-kept list of adopters.
"""
import concurrent.futures
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))
from check_hook_warn_tier import (  # noqa: E402
    wired_hooks, DEFAULT_SETTINGS, DEFAULT_EXTRA_SETTINGS)

# A hook that has adopted the shared intake returns at STDIN_DEADLINE_S (2.0s). One that
# hasn't waits for an EOF that never arrives. 5s separates them with room for a loaded box.
BUDGET_S = 5.0
PAYLOAD = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})

# Wired tools that are NOT guards and are not probed. Declared explicitly rather than
# filtered by a `check_` prefix, so nothing drops out of scope silently.
NOT_A_GUARD = {
    "log_tool_failure.py": "PostToolUseFailure reporter, not a gate; it writes to the "
                           "friction ledger, so probing it would forge ledger entries.",
    "scan_transcript_failures.py": "Stop-hook scanner that walks transcripts and writes "
                                   "state; probing it would do real work with a fake payload.",
}

# STILL UNBOUNDED. Each of these hangs on a retained pipe today -- measured 2026-09-07,
# 24 of 24 that still own their intake. They are not broken, they are un-migrated: the
# shared module landed the same day and the first adoption wave covered 7 hooks. Drain this
# list by switching each to `hook_runtime.read_payload`; the entry then fails as stale.
# EMPTY, and it drained the same day it was written (2026-09-08). It held 25 hooks that
# hung on a retained pipe; all 32 wired guards now take their payload from
# hook_runtime.read_payload and every one returns. Keep the structure: a new hook that
# hand-rolls its intake lands here, and `test_no_stale_allowlist_entries` is what forces it
# back out again once fixed.
#
# The empty state is the point. This list was nearly built BEFORE the migration, as a
# ratchet over 25 accepted exceptions -- which would have converted the debt into approved
# policy and ended the work while looking like progress. That is how tools/mutation-allow.json
# reached 146 entries. Consolidate first, then gate over what is left.
UNBOUNDED_ALLOW: dict[str, str] = {}


def _registry() -> dict:
    """Wired tools, union of every settings file. Never a single-file read."""
    wired = wired_hooks(json.loads(pathlib.Path(DEFAULT_SETTINGS).read_text(encoding="utf-8")))
    for extra in DEFAULT_EXTRA_SETTINGS:
        p = pathlib.Path(extra)
        if p.is_file():
            for tool, events in wired_hooks(
                    json.loads(p.read_text(encoding="utf-8"))).items():
                wired.setdefault(tool, set()).update(events)
    return wired


def _probe(tool: str) -> float | None:
    """Run the hook with a payload on a pipe we never close. Seconds, or None if it hung."""
    path = TOOLS / tool
    if not path.is_file():
        return 0.0
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    t0 = time.monotonic()
    proc = subprocess.Popen([sys.executable, str(path)], stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            text=True, env=env, start_new_session=True)
    try:
        try:
            proc.stdin.write(PAYLOAD)
            proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass          # exited before we finished writing: bounded, and fast
        try:
            proc.wait(timeout=BUDGET_S)
            return time.monotonic() - t0
        except subprocess.TimeoutExpired:
            return None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        try:
            proc.stdin.close()
        except Exception:
            pass


def _probe_all(tools):
    """Probe concurrently: serially this is 25 x 5s of deliberate hanging."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        return dict(zip(tools, pool.map(_probe, tools)))


REGISTRY = sorted(_registry())
PROBED = [t for t in REGISTRY if t not in NOT_A_GUARD]


def test_the_registry_is_not_empty():
    """A gate whose input silently became empty passes everything.

    Per feedback_guard_must_hard_abort_on_empty_input: an enumeration that yields nothing
    must fail loudly, never report a clean run over zero items.
    """
    assert len(REGISTRY) >= 30, f"only {len(REGISTRY)} wired tools found -- registry broke"


def test_every_wired_tool_is_probed_or_explicitly_excluded():
    """Closes the scope. Nothing may fall out of coverage by accident.

    The exclusions are a named dict with reasons, not a `check_` prefix filter, because a
    prefix filter is how a scope undercovers without anyone noticing.
    """
    unaccounted = set(REGISTRY) - set(PROBED) - set(NOT_A_GUARD)
    assert not unaccounted, f"wired but neither probed nor excluded: {sorted(unaccounted)}"
    stale_exclusions = set(NOT_A_GUARD) - set(REGISTRY)
    assert not stale_exclusions, f"excluded but no longer wired: {sorted(stale_exclusions)}"


def test_allowlist_only_names_wired_tools():
    stale = set(UNBOUNDED_ALLOW) - set(REGISTRY)
    assert not stale, (
        f"UNBOUNDED_ALLOW names tools that are no longer wired: {sorted(stale)}. "
        "Remove them; an allowlist entry for a hook nobody runs is noise that hides the "
        "entries that still matter.")


def test_every_allowlist_entry_carries_a_reason():
    """An allowlist without justification is how 'green means done' comes back."""
    empty = [t for t, why in UNBOUNDED_ALLOW.items() if not (why or "").strip()]
    assert not empty, f"allowlisted with no written reason: {empty}"


@pytest.mark.parametrize("tool", [t for t in PROBED if t not in UNBOUNDED_ALLOW])
def test_wired_hook_returns_on_a_retained_pipe(tool):
    """THE GATE. A hook that has adopted the shared intake exits; one that hasn't hangs."""
    elapsed = _probe(tool)
    assert elapsed is not None, (
        f"{tool} did not exit within {BUDGET_S}s on a stdin pipe that was written to and "
        f"never closed. It is waiting for an EOF that will not arrive, and will stall the "
        f"session for the host's full timeout. Take the payload from "
        f"hook_runtime.read_payload() -- see tools/HOOK_AUTHORING.md.")


def test_no_stale_allowlist_entries():
    """THE RATCHET. An allowlisted hook that now passes must leave the list.

    Without this the allowlist is a place where migrated hooks go to be forgotten, and the
    24 entries would still read as 24 after the work was done.
    """
    allowed = [t for t in PROBED if t in UNBOUNDED_ALLOW]
    results = _probe_all(allowed)
    now_bounded = sorted(t for t, secs in results.items() if secs is not None)
    assert not now_bounded, (
        f"these are allowlisted as unbounded but now return on a retained pipe: "
        f"{now_bounded}. They have been migrated -- delete their UNBOUNDED_ALLOW entries "
        f"so the gate protects them from here on.")
