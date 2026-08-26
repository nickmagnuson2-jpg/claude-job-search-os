"""Tests for tools/blind_canary.py.

The properties under test are the two REFUSALS. Both exist because a negative
result is only worth having once the mechanism is shown to be capable of a
positive one -- the same discipline as tools/sweep.py's --control and the PII
scanner's empty-sweep error.

If a future edit makes either refusal return clean, the canary becomes a
rubber stamp: it would report "no leakage" on a scope where leakage was
undetectable, which is worse than not running it, because it manufactures
confidence.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "blind_canary.py"

CONTROL = "CANARY-7Q2"


def build(tmp_path, scratch_body, artifact_body, agent_body):
    eng = tmp_path / "eng"
    eng.mkdir()
    (eng / "scratch.md").write_text(scratch_body, encoding="utf-8")
    (eng / "artifact.md").write_text(artifact_body, encoding="utf-8")
    out = tmp_path / "agent.txt"
    out.write_text(agent_body, encoding="utf-8")
    return eng / "scratch.md", eng, out


def run(scratch, outside, output, control=CONTROL):
    cmd = [sys.executable, str(SCRIPT), "--scratch", str(scratch),
           "--outside", str(outside), "--output", str(output)]
    if control is not None:
        cmd += ["--control", control]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout)


SCRATCH = f"Elements under review. {CONTROL}\n- expected impact\n- blast radius\n"
ARTIFACT = ("The deck argues containment at 45 percent across Northstar telemetry.\n"
            "Criterion two is engineering weeks.\n")


def test_honest_blind_agent_is_clean(tmp_path):
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT,
                    f"{CONTROL} acknowledged. The two elements look orthogonal.\n")
    code, p = run(s, o, a)
    assert code == 0
    assert p["clean"] is True
    assert p["leaked_tokens"] == []


def test_contaminated_agent_is_caught(tmp_path):
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT,
                    f"{CONTROL} ok. Containment sits at 45 percent per Northstar telemetry.\n")
    code, p = run(s, o, a)
    assert code == 1
    assert p["clean"] is False
    assert "northstar" in p["leaked_tokens"]
    assert "containment" in p["leaked_tokens"]


def test_missing_control_withholds_verdict_even_when_nothing_leaked(tmp_path):
    """THE CENTRAL PROPERTY.

    Nothing leaked. The agent still gets no clean verdict, because a canary that
    was never shown to be able to detect anything has not cleared it.
    """
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT, "The two elements look orthogonal.\n")
    code, p = run(s, o, a)
    assert code == 3
    assert p["status"] == "refused"
    assert p["clean"] is None, "a withheld verdict must be None, never True"
    assert p["leaked_tokens"] == [], "precondition: this case genuinely has no leak"


def test_control_not_planted_in_scratch_is_refused(tmp_path):
    """If the control was never planted, it cannot prove anything either."""
    s, o, a = build(tmp_path, "Elements only, no control token here.\n", ARTIFACT,
                    f"{CONTROL} appears only here.\n")
    code, p = run(s, o, a)
    assert code == 3
    assert p["clean"] is None


def test_empty_outside_only_set_is_refused_not_clean(tmp_path):
    """No distinctive vocabulary outside -> nothing detectable -> not a pass."""
    same = f"identical vocabulary everywhere {CONTROL} elements orthogonal\n"
    s, o, a = build(tmp_path, same, same, f"{CONTROL} elements orthogonal\n")
    code, p = run(s, o, a)
    assert code == 2
    assert p["status"] == "refused"
    assert p["clean"] is None


def test_every_result_carries_its_denominator(tmp_path):
    """A verdict with no denominator is not a verdict."""
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT, f"{CONTROL} fine.\n")
    _, p = run(s, o, a)
    for key in ("outside_files_scanned", "outside_only_token_count",
                "scratch_token_count", "agent_token_count", "paths_scanned"):
        assert key in p, key
    assert p["outside_files_scanned"] >= 1


def test_limitation_is_always_stated(tmp_path):
    """A clean result must never read as 'the agent was blind'."""
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT, f"{CONTROL} fine.\n")
    _, p = run(s, o, a)
    assert "paraphras" in p["limitation"].lower()


def test_paraphrase_evades_detection_and_this_is_documented(tmp_path):
    """Honest negative test: the canary CANNOT catch a paraphrasing agent.

    Pinned deliberately. If someone later believes this tool proves blindness,
    this test is the counterexample sitting in the suite.
    """
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT,
                    f"{CONTROL} noted. Roughly half of contacts resolve without a human.\n")
    code, p = run(s, o, a)
    assert code == 0
    assert p["clean"] is True, "paraphrase is not detected -- known and documented limit"


def test_scratch_and_output_are_excluded_from_the_outside_set(tmp_path):
    """The agent's own output must not be scanned as 'outside material'."""
    eng = tmp_path / "eng"
    eng.mkdir()
    scratch = eng / "scratch.md"
    scratch.write_text(SCRATCH, encoding="utf-8")
    (eng / "artifact.md").write_text(ARTIFACT, encoding="utf-8")
    out = eng / "agent.txt"           # deliberately INSIDE the outside dir
    out.write_text(f"{CONTROL} nothing unusual here.\n", encoding="utf-8")
    code, p = run(scratch, eng, out)
    assert code == 0
    assert p["clean"] is True


def test_unreadable_input_exits_4(tmp_path):
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT, "x")
    code, _ = run(s, o, tmp_path / "does-not-exist.txt")
    assert code == 4


# ===========================================================================
# Self-describing scope (2026-08-19)
#
# `--control ""` used to skip the mechanism-unproven block entirely, so the
# payload came back status:ok / clean:true with NO `control` key at all. A
# reader could not tell a proven-clean run from an unproven one, and a WRONG
# token refuses correctly -- so an EMPTY one silently passing was backwards.
#
# The fix is a self-describing report, NOT input rejection: `control.checked`
# states whether the proof ran. Exit codes and `status` are deliberately
# unchanged, because a clean-but-unproven run is still a legitimate result --
# it just must not be readable as a proven one.
# ===========================================================================

@pytest.mark.parametrize("control", ["", "   ", None])
def test_control_block_is_always_emitted(tmp_path, control):
    """The key must exist even when no proof was run. Its ABSENCE was the defect."""
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT, "The two elements look orthogonal.\n")
    code, p = run(s, o, a, control=control)
    assert "control" in p, "control key absent: a reader cannot tell proven from unproven"
    assert p["control"]["checked"] is False
    assert p["control"]["proves_agent_read_input"] is False
    assert p["control"]["token"] is None
    assert "UNPROVEN" in p["control"]["note"]


@pytest.mark.parametrize("control", ["", "   ", None])
def test_unproven_run_still_reports_its_own_limits_when_clean(tmp_path, control):
    """The regression that matters: clean + unproven must not read as clean + proven."""
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT, "The two elements look orthogonal.\n")
    code, p = run(s, o, a, control=control)
    assert p["clean"] is True, "fixture should produce a clean result"
    assert p["control"]["checked"] is False, "clean result claims a proof that never ran"


def test_exit_code_and_status_unchanged_for_unproven_run(tmp_path):
    """Additive-only contract. blind_canary is referenced by the analysis method;
    flipping its exit code to close this would break callers to prevent a defect
    that has never fired organically."""
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT, "The two elements look orthogonal.\n")
    code, p = run(s, o, a, control="")
    assert code == 0
    assert p["status"] == "ok"


def test_proven_run_marks_itself_proven(tmp_path):
    """Happy path: control planted AND echoed means the mechanism is demonstrated."""
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT,
                    f"{CONTROL} acknowledged. The two elements look orthogonal.\n")
    code, p = run(s, o, a)
    assert code == 0
    assert p["control"]["checked"] is True
    assert p["control"]["proves_agent_read_input"] is True


def test_wrong_control_token_still_refuses(tmp_path):
    """Guard on the guard: the pre-existing refusal must survive the rewrite."""
    s, o, a = build(tmp_path, SCRATCH, ARTIFACT, "The two elements look orthogonal.\n")
    code, p = run(s, o, a, control="NEVER-PLANTED-XYZ")
    assert code == 3
    assert p["status"] == "refused"
    assert p["control"]["checked"] is True
    assert p["control"]["proves_agent_read_input"] is False
