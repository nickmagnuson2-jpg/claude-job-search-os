from unittest.mock import MagicMock
from tools.agent_core import run_agent, BETAS


def test_run_agent_returns_structured(monkeypatch):
    client = MagicMock()
    client.beta.agent.runs.request.side_effect = [
        {"id": "agent_run_x", "status": "running"},
        {"id": "agent_run_x", "status": "completed",
         "output": {"text": "t", "structured": {"companies": [{"name": "Acme"}]},
                    "grounding": []},
         "usage": {"agentComputeUnits": 0.1}, "cost": {"total": 0.02}},
    ]
    out = run_agent(client, "q", output_schema={"type": "object"}, poll_interval_s=0)
    assert out["status"] == "completed"
    assert out["structured"]["companies"][0]["name"] == "Acme"
    assert out["cost"]["total"] == 0.02


def test_company_struct_to_candidate_maps_keys():
    from tools.agent_discover import struct_to_candidates
    structured = {"companies": [{"name": "Acme", "funding_stage": "Series A",
                                 "hq": "San Francisco", "description": "AI infra"}]}
    cands = struct_to_candidates(structured, "company")
    assert cands[0]["name"] == "Acme"
    assert cands[0]["location"] == "San Francisco"   # hq -> location for scorer
    assert cands[0]["stage_text"] == "Series A"      # funding_stage -> stage_text


def test_resolve_schema_falls_back_to_default():
    from tools.agent_discover import resolve_schema, DEFAULT_SCHEMAS
    # preset with no schema -> entity default
    assert resolve_schema({}, "company") == DEFAULT_SCHEMAS["company"]
    assert resolve_schema({}, "person") == DEFAULT_SCHEMAS["person"]
    # preset schema wins when present
    custom = {"type": "object", "properties": {}}
    assert resolve_schema({"output_schema": custom}, "company") == custom
