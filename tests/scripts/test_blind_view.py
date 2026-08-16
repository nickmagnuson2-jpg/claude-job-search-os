"""Tests for tools/blind_view.py — the redacted view handed to a blind agent.

The whole point of this script is that anti-anchoring failure is SILENT: a contaminated
agent's output looks exactly like an independent one, so there is no tell to catch
afterward. That makes the extraction itself the only place the guarantee can live, and
these tests are the only thing standing behind it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "blind_view.py"

FRAME = {
    "schema_version": 3,
    "version": 4,
    "locked": False,
    "engagement": "acme-secret-project",
    "d1": {"problem_statement": "SECRET: where should Acme invest next?",
           "problem_type": "prioritization"},
    "facts": {"f1": {"text": "LEAK-CANARY throughput is 100/day", "tier": "A"}},
    "unknowns": {"u1": {"text": "LEAK-CANARY unknown", "disposition": "assumption"}},
    "elements": [
        {"id": "e1", "name": "expected impact", "measure": "convertible volume",
         "because": ["f1"], "inputs": ["i_vol", "i_csat"], "protected": True,
         "first_seen": 1},
        {"id": "e2", "name": "blast radius", "measure": "how far a mistake travels",
         "because": ["f1"], "inputs": ["i_csat"], "protected": True, "first_seen": 2},
    ],
    "closure": "LEAK-CANARY the set closes here",
    "exclusions": [{"element": "signal", "reason": "LEAK-CANARY a preference"}],
    "recommendation": {"text": "LEAK-CANARY do the thing", "confidence": "high"},
    "prediction": {"will_be_probed": "LEAK-CANARY the denominator"},
}


def frame(tmp_path, data=None):
    p = tmp_path / "frame.yaml"
    p.write_text(yaml.safe_dump(data or FRAME, sort_keys=False), encoding="utf-8")
    return p


def run(*args):
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                       capture_output=True, text=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        return r.returncode, json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.returncode, {"_raw": r.stdout, "_err": r.stderr}


# --------------------------------------------------------------- the guarantee

@pytest.mark.parametrize("view", ["interaction", "closure", "distinction"])
def test_no_view_leaks_anything_from_the_artifact(tmp_path, view):
    """The canary test. Every withheld field carries a LEAK-CANARY marker; none of them
    may survive into the file the agent is handed, in ANY view."""
    f = frame(tmp_path)
    out = tmp_path / "blind.yaml"
    code, res = run("--frame", f, "--view", view, "--out", out)
    assert code == 0, res
    text = out.read_text()
    assert "LEAK-CANARY" not in text, f"view {view!r} leaked withheld content"
    assert "SECRET" not in text
    assert "acme-secret-project" not in text, "even the engagement name is withheld"


@pytest.mark.parametrize("view,allowed", [
    ("interaction", {"id", "name", "measure"}),
    ("closure", {"id", "name"}),
    ("distinction", {"id", "name", "measure"}),
])
def test_each_view_emits_only_its_whitelisted_keys(tmp_path, view, allowed):
    """A WHITELIST, not a blacklist: a blacklist silently passes every field added to
    the schema later, which is the default-allow defect this repo already paid for."""
    f = frame(tmp_path)
    out = tmp_path / "blind.yaml"
    run("--frame", f, "--view", view, "--out", out)
    payload = yaml.safe_load(out.read_text())
    for el in payload["elements"]:
        assert set(el) <= allowed, f"{view} emitted {set(el) - allowed}"


def test_provenance_and_inputs_are_withheld(tmp_path):
    """`inputs` would hand over the very overlap F3 exists to detect independently, and
    `because` is provenance the agent must not be able to reason from."""
    f = frame(tmp_path)
    out = tmp_path / "blind.yaml"
    _, res = run("--frame", f, "--view", "interaction", "--out", out)
    assert "inputs" in res["withheld_element_keys"]
    assert "because" in res["withheld_element_keys"]
    assert "i_csat" not in out.read_text(), "the shared input must not be visible"


def test_the_report_names_what_was_withheld(tmp_path):
    """Auditable after the fact: the caller can see what the agent could not see."""
    f = frame(tmp_path)
    _, res = run("--frame", f, "--view", "interaction", "--out", tmp_path / "b.yaml")
    for key in ("d1", "facts", "closure", "recommendation", "prediction"):
        assert key in res["withheld_frame_keys"]


# ------------------------------------------------- the last-line guard, exercised
#
# MUTATION-TESTED 2026-08-16. An earlier inline version of this assertion SURVIVED its
# mutant: deleting it left the suite fully green, because the whitelist filter upstream
# means the condition can never occur in production. A guard whose trigger has never
# occurred is not evidence of anything. These tests call it directly with contaminated
# payloads, which is the only way to exercise a defence-in-depth check.

def _load_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("blind_view", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_leak_guard_refuses_a_contaminated_element_key():
    mod = _load_module()
    payload = {"elements": [{"id": "e1", "name": "x", "because": ["f1"]}]}
    with pytest.raises(SystemExit) as exc:
        mod.assert_no_leak(payload, ["id", "name", "measure"])
    assert exc.value.code != 0


def test_the_leak_guard_refuses_a_protected_top_level_key():
    mod = _load_module()
    payload = {"elements": [], "facts": {"f1": {"text": "leaked"}}}
    with pytest.raises(SystemExit) as exc:
        mod.assert_no_leak(payload, ["id", "name"])
    assert exc.value.code != 0


def test_the_leak_guard_passes_a_clean_payload():
    """Pin the negative side too, or the fix for a narrowing is an over-broad widening."""
    mod = _load_module()
    mod.assert_no_leak({"elements": [{"id": "e1", "name": "x"}], "view": "closure"},
                       ["id", "name", "measure"])


# --------------------------------------------------------------- refusals

def test_empty_element_set_hard_aborts(tmp_path):
    """An agent handed nothing returns a confident 'no problems found' — a FALSE CLEAN,
    not an absence of evidence. 7 fires of this rule in this repo."""
    data = dict(FRAME)
    data["elements"] = []
    f = frame(tmp_path, data)
    out = tmp_path / "blind.yaml"
    code, res = run("--frame", f, "--view", "interaction", "--out", out)
    assert code != 0
    assert "FALSE CLEAN" in res["message"]
    assert not out.exists(), "a refused view must not leave a file for an agent to read"


def test_missing_elements_key_hard_aborts(tmp_path):
    data = {k: v for k, v in FRAME.items() if k != "elements"}
    f = frame(tmp_path, data)
    code, _ = run("--frame", f, "--view", "interaction", "--out", tmp_path / "b.yaml")
    assert code != 0


def test_an_unnamed_element_refuses(tmp_path):
    """The views are keyed on names; a nameless element would silently narrow the set."""
    data = dict(FRAME)
    data["elements"] = [{"id": "e1", "measure": "x"}]
    f = frame(tmp_path, data)
    code, res = run("--frame", f, "--view", "interaction", "--out", tmp_path / "b.yaml")
    assert code != 0
    assert "no name" in res["message"]


def test_missing_frame_refuses(tmp_path):
    code, _ = run("--frame", tmp_path / "nope.yaml", "--view", "interaction")
    assert code != 0


# --------------------------------------------------------------- the payload

def test_the_view_tells_the_agent_it_is_blind(tmp_path):
    """The redaction is structural, but the agent still needs to know not to infer the
    missing context or ask for the frame."""
    f = frame(tmp_path)
    out = tmp_path / "blind.yaml"
    run("--frame", f, "--view", "interaction", "--out", out)
    payload = yaml.safe_load(out.read_text())
    assert "you_are_blind" in payload and payload["task"]
    assert "Do not ask for the full frame" in payload["you_are_blind"]


def test_element_count_survives_redaction(tmp_path):
    """The agent must know how many elements there are, or 'the set closes' is unanswerable."""
    f = frame(tmp_path)
    out = tmp_path / "blind.yaml"
    run("--frame", f, "--view", "closure", "--out", out)
    assert yaml.safe_load(out.read_text())["element_count"] == 2
