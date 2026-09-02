"""An allowlist entry must carry verified analysis, not just a non-empty reason.

WHY. A surviving mutant is acceptable ONLY with a written reason -- that rule already
existed, and `mutation_check` fails a run on a blank one. But "non-empty" is a very low
bar: "not worth it" satisfies it, and an allowlist that accepts assertions is how a corpus
decays back into "green means done" one plausible sentence at a time. The allowlist is the
one place where a survivor becomes invisible, so it is the one place the bar has to be high.

WHAT COUNTS AS ANALYSIS, and why these three conditions:

  1. SUBSTANCE (>= 80 chars). Reachability arguments do not fit in a clause. The one entry
     that did -- 57 chars, "Drops the `continue` inside that same unreachable branch" --
     was a cross-reference to an argument made elsewhere, which is exactly the shape that
     lets an unverified entry ride in behind a verified one.

  2. A NAMED VERDICT CLASS. The writer has to commit to WHY it survives: equivalent, dead
     branch, unreachable by construction, non-portable to fixture, CLI plumbing,
     whitespace-only. A verdict you cannot name is a verdict you have not reached.

  3. A TRACED MECHANISM. A backticked construct, a call, a mutant key, a line reference or
     a date -- evidence that someone opened the file. Every good entry in this list already
     does this; several name the SIBLING mutant that was NOT allowlisted and was killed
     instead, which is the strongest possible form: it shows the author distinguished the
     unkillable from the merely-unkilled.

This gate is deliberately structural, not semantic. It cannot tell a true equivalence
argument from a false one. What it can do is make the false one expensive to write and
obvious to review -- and stop the one-line "equivalent, trust me" entry entirely.

Origin: 2026-09-02, on the instruction that equivalence be understood a few steps deeper
before anything is allowlisted.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOW = REPO_ROOT / "tools" / "mutation-allow.json"

MIN_CHARS = 80

VERDICT_CLASS = re.compile(
    r"equivalen|unreachab|by construction|dead branch|non-portable|not portable|"
    r"same (?:reason|shape|arm|branch|reachability)|identical shape|"
    r"cli plumbing|whitespace only|exit (?:code|status)|cannot change|"
    r"no input can|effectively unreachable|reachable only", re.I)

TRACED_MECHANISM = re.compile(r"`[^`]+`|\b\w+\([^)]*\)|::|\bline \d+|\b20\d\d-\d\d-\d\d\b")


def entries() -> dict[str, str]:
    return json.loads(ALLOW.read_text(encoding="utf-8"))


def deficiencies(reason: str) -> list[str]:
    """Which of the three conditions this reason fails. Pure, so it is testable."""
    text = str(reason)
    out = []
    if len(text.strip()) < MIN_CHARS:
        out.append(f"too short ({len(text.strip())} < {MIN_CHARS})")
    if not VERDICT_CLASS.search(text):
        out.append("no named verdict class")
    if not TRACED_MECHANISM.search(text):
        out.append("no traced mechanism")
    return out


def test_every_allowlisted_mutant_carries_verified_analysis():
    bad = {k: deficiencies(v) for k, v in entries().items() if deficiencies(v)}
    assert not bad, (
        "allowlist entries without verified analysis:\n"
        + "\n".join(f"  {k}: {', '.join(v)}" for k, v in bad.items())
        + "\n\nAn allowlisted mutant is one nobody will ever look at again. Say WHY it "
          "cannot be killed (equivalent / dead branch / unreachable / non-portable), and "
          "show the construct you traced. If you cannot, it is a coverage gap, not an "
          "equivalent mutant -- write the test instead.")


def test_no_entry_is_blank():
    """mutation_check already fails on this; asserted here so the two cannot drift."""
    assert not [k for k, v in entries().items() if not str(v).strip()]


# --- the checker itself must be falsifiable ---------------------------------

def test_a_bare_assertion_is_rejected():
    assert "no named verdict class" in " ".join(deficiencies(
        "Not worth chasing, it is fine, we looked at this one and decided to move on ok."))


def test_a_one_line_cross_reference_is_rejected():
    """The exact shape that was in the list before this gate existed."""
    assert deficiencies("Drops the `continue` inside that same unreachable branch.")


def test_a_verdict_with_no_traced_mechanism_is_rejected():
    assert "no traced mechanism" in " ".join(deficiencies(
        "This mutant is equivalent because the branch it changes cannot alter the result "
        "in any way that a caller could ever observe from outside the function."))


def test_a_real_analysis_passes():
    assert deficiencies(
        "EQUIVALENT BY ARGUMENT, not by assumption: `_is_ordinary_word` has exactly one "
        "caller, `return not _is_ordinary_word(t, dictionary)`, and `not None` and "
        "`not False` are both True. 2026-09-01.") == []


def test_each_condition_fails_independently():
    """Guards against one condition masking another as the regexes evolve."""
    long_enough = "x" * MIN_CHARS
    assert deficiencies("equivalent `x`") == [f"too short (14 < {MIN_CHARS})"]
    assert deficiencies(long_enough + " `traced`") == ["no named verdict class"]
    assert deficiencies(long_enough + " equivalent") == ["no traced mechanism"]


# --- the assertion that has to fail first -----------------------------------

def test_the_allowlist_is_actually_being_read():
    """Every assertion above is satisfied by an empty file. If the path moves or the JSON
    stops parsing, this fails instead of reporting a clean allowlist that was never read."""
    assert ALLOW.is_file(), f"allowlist not found at {ALLOW}"
    data = entries()
    assert isinstance(data, dict)
    assert len(data) > 20, f"only {len(data)} entries read; the file should carry dozens"
