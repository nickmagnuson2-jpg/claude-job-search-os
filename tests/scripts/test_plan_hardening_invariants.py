"""Invariant tests for .claude/workflows/plan-hardening.js (see framework/plan-hardening-v2-spec.md §9).

Every test here breaks ONE invariant on purpose and asserts the run FAILS. Per the
standing rule that a green test is not evidence, a suite that only exercised the happy
path would prove nothing about a guard.

The driver (plan_hardening_driver.mjs) executes the REAL workflow source with the
workflow DSL stubbed, rather than asserting about it with regexes. It wraps the source
in an AsyncFunction the way the harness does, because the script uses top-level return.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "tests" / "scripts" / "plan_hardening_driver.mjs"
WORKFLOW = ROOT / ".claude" / "workflows" / "plan-hardening.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run(scenario):
    p = subprocess.run([shutil.which("node"), str(DRIVER), scenario],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, f"driver crashed: {p.stderr[:500]}"
    return json.loads(p.stdout)


# --- the workflow must be loadable at all, or every test below passes vacuously ---

def test_workflow_source_parses():
    p = subprocess.run([shutil.which("node"), "--check", str(WORKFLOW)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_happy_path_completes():
    """Control. If this fails, the break-it tests below prove nothing."""
    r = run("happy_no_holes")
    assert r["ok"], r.get("error")
    assert r["result"]["terminal_state"] == "CLOSED"
    assert r["result"]["premise_status"] == "resolved"


# --- INVARIANT 1: payload floor (v1 reconstructed a plan from a 3-char payload) ---

def test_invariant1_short_plan_aborts_rather_than_reconstructing():
    r = run("payload_floor")
    assert not r["ok"]
    assert "INVARIANT 1" in r["error"] and "payload floor" in r["error"]


def test_invariant1_applies_to_the_revised_plan_too():
    """The fix stage output is a payload as well, and that is exactly where v1 died."""
    r = run("fix_payload_floor")
    assert not r["ok"]
    assert "INVARIANT 1" in r["error"] and "fix stage" in r["error"]


def test_invariant1_no_agent_runs_after_a_short_payload():
    r = run("payload_floor")
    assert r["calls"] == [], f"agents ran despite an aborted payload: {r['calls']}"


# --- INVARIANT 2: blindness of the oracle arm ---

def test_invariant2_plan_leaking_into_the_blind_prompt_aborts():
    """The plan-blind goal agent is the oracle arm; if it sees the plan it is worthless."""
    r = run("blindness")
    assert not r["ok"]
    assert "INVARIANT 2" in r["error"] and "blindness" in r["error"]


def test_invariant2_fires_before_any_goal_agent_runs():
    r = run("blindness")
    assert not any(c.startswith("goal:") for c in r["calls"]), r["calls"]


# --- INVARIANT 3: the hard stop ---

def test_invariant3_open_premise_stops_before_execution_critique():
    r = run("premise_open")
    assert r["ok"], r.get("error")
    assert r["result"]["terminal_state"] == "PREMISE-OPEN"
    assert r["result"]["premise_status"] == "open"


def test_invariant3_no_target_or_probe_agent_runs_when_premise_is_open():
    """This is the whole point of the gate: execution critique must be UNREACHABLE."""
    r = run("premise_open")
    forbidden = [c for c in r["calls"]
                 if c == "target-generation" or c.startswith("probe:") or c == "fix"
                 or c.startswith("retest:")]
    assert forbidden == [], f"execution stage ran under an open premise: {forbidden}"


def test_untypeable_plan_is_emitted_as_a_premise_finding_and_stops():
    """A plan doing more than one thing should be split, not hardened."""
    r = run("untypeable")
    assert r["ok"], r.get("error")
    assert r["result"]["terminal_state"] == "PREMISE-OPEN"
    assert not any(c.startswith("probe:") for c in r["calls"]), r["calls"]


# --- INVARIANT 4: ID stability and coverage ---

def test_invariant4_missing_disposition_for_a_hole_id_aborts():
    r = run("id_coverage")
    assert not r["ok"]
    assert "INVARIANT 4" in r["error"] and "E1" in r["error"]


def test_invariant4_retests_are_keyed_to_hole_ids():
    r = run("full_retest")
    assert r["ok"], r.get("error")
    retests = sorted(c for c in r["calls"] if c.startswith("retest:") and "new" not in c)
    assert retests == ["retest:E1", "retest:E2"], retests


# --- INVARIANT 5: an accepted finding needs a written reason ---

def test_invariant5_accepted_without_a_reason_aborts():
    """Same contract as tools/mutation-allow.json: an exception without a justification
    is how a gate decays into 'we looked at it'."""
    r = run("reason_required")
    assert not r["ok"]
    assert "INVARIANT 5" in r["error"]
    assert "E1" in r["error"] and "E2" in r["error"]


# --- structural properties the spec requires ---

def test_minor_holes_are_dropped_not_carried():
    """Volume is the failure mode this design exists to fix."""
    r = run("full_retest")
    assert r["ok"], r.get("error")
    ids = [f["id"] for f in r["result"]["findings"] if f["layer"] == "execution"]
    assert len(ids) == 2, f"a minor hole survived into the register: {ids}"


def test_new_holes_agent_runs_on_every_fix_pass():
    r = run("full_retest")
    assert any("new" in c for c in r["calls"] if c.startswith("retest:")), r["calls"]


def test_register_names_the_spec_it_implements():
    r = run("full_retest")
    assert r["result"]["spec"] == "framework/plan-hardening-v2-spec.md"


# --- INVARIANT 6: bounded mutation (v1 reconstructed a plan and silently renamed one) ---

def test_invariant6_a_reconstructed_plan_aborts():
    """The reviser paraphrased instead of editing. Retest would judge an unreviewed artifact."""
    r = run("reconstructed_plan")
    assert not r["ok"]
    assert "INVARIANT 6a" in r["error"], r["error"]
    assert "survive verbatim" in r["error"], r["error"]


def test_invariant6_growth_past_budget_aborts():
    """Every original line survives, but the reviser padded past the stated growth budget."""
    r = run("bloated_plan")
    assert not r["ok"]
    assert "INVARIANT 6b" in r["error"], r["error"]
    assert "against a budget of" in r["error"], r["error"]


def test_invariant6_fires_before_any_retest_agent_runs():
    """A reconstructed plan must never reach retest — that is the whole point of the guard."""
    r = run("reconstructed_plan")
    assert not r["ok"]
    assert not [c for c in r["calls"] if c.startswith("retest:")], r["calls"]


def test_invariant6_does_not_fire_on_a_legitimate_fix():
    """Restraint half. A normal edit must pass, or the guard is just a blocker."""
    r = run("full_retest")
    assert r["ok"], r.get("error")


# --- S4 SPLIT: the fix stage must scale past a single agent's output budget ---
# Regression for the measured failure of run wf_a440ab49-405 (2026-08-21): one agent asked to
# disposition 25 holes and rewrite the plan returned E1-E8 and stopped. INV4 aborted the run,
# correctly, but the stage could not complete on any plan yielding more than ~8 holes.

def test_s4_scales_to_many_holes():
    r = run("many_holes")
    assert r["ok"], r.get("error")
    assert r["result"]["terminal_state"] == "CLOSED"


def test_s4_spawns_exactly_one_disposition_agent_per_hole():
    """ID coverage is structural: one agent in, one disposition out. Not checked after the fact."""
    r = run("many_holes")
    fixes = [c for c in r["calls"] if c.startswith("fix:")]
    assert len(fixes) == 25, fixes
    assert sorted(fixes) == sorted(f"fix:E{i}" for i in range(1, 26))


def test_s4_reviser_is_separate_from_the_deciders():
    """The reviser applies edits and reviews nothing — that separation is the whole fix."""
    r = run("many_holes")
    assert r["calls"].count("revise") == 1, r["calls"]


def test_s4_a_dead_hole_agent_still_aborts():
    """The split must not weaken INV4: a null from one hole's agent stops the run."""
    r = run("id_coverage")
    assert not r["ok"]
    assert "INVARIANT 4" in r["error"], r["error"]


# --- PERSISTENCE: registerPath / outPath were advertised in the meta and silently ignored ---

def test_persist_agents_run_when_paths_are_given():
    r = run("persist_both")
    assert r["ok"], r.get("error")
    assert "persist:register" in r["calls"], r["calls"]
    assert "persist:plan" in r["calls"], r["calls"]
    assert r["result"]["persisted"]["all_writes_returned"] is True


def test_no_persist_agent_without_paths():
    """Restraint half: the stage must stay silent when nothing was asked of it."""
    r = run("persist_none")
    assert r["ok"], r.get("error")
    assert not [c for c in r["calls"] if c.startswith("persist:")], r["calls"]
    assert "persisted" not in r["result"]


def test_a_failed_write_is_not_reported_as_durable():
    """A dead write agent must flip all_writes_returned false. Reporting an unverified write as
    success is how a persisted result quietly evaporates — the v1 payload-loss shape, one layer out."""
    r = run("persist_fails")
    assert r["ok"], r.get("error")
    assert r["result"]["persisted"]["all_writes_returned"] is False, r["result"]["persisted"]


def test_invariant6b_budget_scales_with_commissioned_fixes():
    """Regression for the 2026-08-21 FALSE POSITIVE: a faithful edit absorbing 25 fixes measured
    2.30x growth at 84% retention with identical section headers, and a flat 1.15x cap aborted it.
    A ratio alone cannot separate "padded" from "absorbed 25 real fixes"; the budget must scale."""
    r = run("many_holes_grown")
    assert r["ok"], r.get("error")
    bm = r["result"]["bounded_mutation"]
    assert bm["growth"] > 1.15, bm
    assert bm["retention"] == 1, bm


def test_invariant6b_ceiling_holds_when_per_fix_allowance_would_dominate():
    """Without a hard ceiling, 25 fixes on a short plan permitted 12.66x — a toothless guard."""
    r = run("many_holes")
    assert r["ok"], r.get("error")
    bm = r["result"]["bounded_mutation"]
    assert bm["budget"] <= (bm["length"] / bm["growth"]) * 3 + 1, bm


def test_bounded_mutation_is_reported_even_on_success():
    """Accretion must be visible in the register, never silent — a passing run still shows growth."""
    r = run("full_retest")
    assert r["ok"], r.get("error")
    assert set(r["result"]["bounded_mutation"]) >= {"growth", "retention", "budget", "length"}


# --- BOUNDED RETRY: an invariant violation from ONE agent must not discard the whole run ---
# Measured 2026-08-21: two runs (999k and 1.6M tokens) were killed by a single agent's bad
# output, discarding retest/validate/persist both times. The retry is capped at one attempt —
# an unbounded revise-recheck loop is how v1 died, so the cap is part of the guarantee.

def test_a_flaky_hole_agent_is_retried_not_fatal():
    r = run("flaky_hole")
    assert r["ok"], r.get("error")
    assert "fix:E1:retry" in r["calls"], r["calls"]
    assert r["result"]["terminal_state"] == "CLOSED"


def test_reviser_gets_one_retry_when_it_breaks_the_budget():
    r = run("flaky_revise")
    assert r["ok"], r.get("error")
    assert "revise:retry" in r["calls"], r["calls"]
    assert r["result"]["bounded_mutation"]["growth"] < 2


def test_retry_is_capped_and_still_aborts_on_a_persistently_bad_reviser():
    """The cap is load-bearing: a reviser that never complies must still fail the run."""
    r = run("bloated_plan")
    assert not r["ok"]
    assert "INVARIANT 6b" in r["error"], r["error"]
    assert r["calls"].count("revise:retry") <= 1, r["calls"]


# --- INVARIANT 7: the measurement gate (spec section 9) -----------------------
# Added 2026-08-25, 2nd fire of feedback_measure_before_restructuring_a_thesis.
# An 88-agent / 5.24M-token hardening run returned UNCONVERGED and its output was marked
# do-not-execute; a ~15 minute measurement the next session invalidated one worklist item
# outright and returned a third option that was in nobody's hypothesis space. The gate has
# to be enforced in code: a prose instruction is exactly what the model rationalises past,
# which is why these tests drive a premise agent that returns RESOLVED while admitting an
# unrun measurement.

def test_invariant7_unmeasured_premise_forces_the_gate_open():
    """The model said resolved. The gate must overrule it."""
    r = run("unmeasured_premise")
    assert r["ok"], r.get("error")
    assert r["result"]["premise_status"] == "open"
    assert r["result"]["terminal_state"] == "PREMISE-OPEN"


def test_invariant7_no_execution_agent_runs_on_an_unmeasured_premise():
    r = run("unmeasured_premise")
    forbidden = [c for c in r["calls"]
                 if c == "target-generation" or c.startswith("probe:")
                 or c.startswith("fix:") or c.startswith("retest:")]
    assert forbidden == [], f"execution critique ran on an unmeasured premise: {forbidden}"


def test_invariant7_emits_a_finding_that_names_the_command_to_run():
    """A stop that doesn't say what to measure just blocks the user."""
    r = run("unmeasured_premise")
    findings = r["result"]["findings"]
    m = [f for f in findings if f["id"].startswith("M")]
    assert m, f"no measurement finding emitted: {findings}"
    assert "recall is the bottleneck" in m[0]["claim"]
    assert "grep the transcripts" in m[0]["failure_scenario"]
    assert "15 min" in m[0]["failure_scenario"]


def test_invariant7_does_not_fire_when_the_measurement_was_already_run():
    """The guard must discriminate, not just always stop."""
    r = run("measured_premise")
    assert r["ok"], r.get("error")
    assert r["result"]["premise_status"] == "resolved"
    assert r["result"]["terminal_state"] != "PREMISE-OPEN"
    assert any(c == "target-generation" for c in r["calls"]), \
        "a fully measured premise must proceed to execution critique"


def test_invariant7_does_not_fire_when_nothing_is_measurable():
    """An empty list is a legitimate answer and must not deadlock the run."""
    r = run("happy_no_holes")
    assert r["ok"], r.get("error")
    assert r["result"]["premise_status"] == "resolved"
