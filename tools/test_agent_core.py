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
