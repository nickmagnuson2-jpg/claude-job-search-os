from unittest.mock import MagicMock
from tools.agent_core import run_agent, BETAS


def test_run_agent_returns_structured(monkeypatch):
    client = MagicMock()
    client.beta.agent.runs.request.side_effect = [
        {"id": "agent_run_x", "status": "running"},
        {"id": "agent_run_x", "status": "completed",
         "output": {"text": "t", "structured": {"companies": [{"name": "Acme"}]},
                    "grounding": []},
         "usage": {"agentComputeUnits": 0.1}, "costDollars": {"total": 0.02}},
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


def test_score_company_candidates_sorts_and_excludes(monkeypatch):
    """score_company_candidates: stamps score, drops geo-excluded, sorts desc."""
    import tools.agent_discover as ad
    monkeypatch.setattr(ad, "load_scoring_context", lambda root: {}, raising=False)

    def fake_score(c, ctx, weights=None, keywords=None):
        table = {"Acme": (8, False), "FarCo": (2, True), "MidCo": (5, False)}
        score, excluded = table[c["name"]]
        return {"score": score, "excluded": excluded, "geo_flag": excluded}
    import tools.career_scanner.company_scorer as cs
    monkeypatch.setattr(cs, "score_company", fake_score)

    cands = [{"name": "Acme"}, {"name": "FarCo"}, {"name": "MidCo"}]
    out = ad.score_company_candidates(cands, weights=None, keywords=[])
    assert [c["name"] for c in out] == ["Acme", "MidCo"]   # FarCo excluded, sorted desc
    assert out[0]["score"] == 8


# --------------------------------------------------------------------------- #
# agent_discover excludes the company queue (l1b-funnel-fixes-1124, 2026-11-24)
# --------------------------------------------------------------------------- #
def _write_queue(root, pending, decided):
    import json as _json
    q = root / "tools" / ".company-queue.json"
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text(_json.dumps({"pending": pending, "decided": decided}), encoding="utf-8")


def _queue_fixture(root):
    _write_queue(root,
                 pending=[{"entity": "company", "name": "Queuedco", "key": "queuedco"},
                          {"entity": "person", "name": "Casey Doe", "key": "casey doe|acme"}],
                 decided=[{"entity": "company", "name": "Decidedco", "key": "decidedco",
                           "decision": "reject"}])


def test_known_names_include_pending_and_decided_queue_companies(tmp_path):
    from tools.agent_core import filter_known
    from tools.agent_discover import known_names
    _queue_fixture(tmp_path)
    known = known_names([], "company", tmp_path)
    cands = [{"name": "Queuedco"}, {"name": "Decidedco"}, {"name": "Newco"},
             {"name": "Casey Doe"}]
    kept = [c["name"] for c in filter_known(cands, known)]
    # pending and decided companies are dropped; a new name and a person name pass
    assert kept == ["Newco", "Casey Doe"]


def test_known_names_tags_a_near_match_instead_of_dropping_it(tmp_path):
    """company-key-1121 ruling: a looser match is kept and tagged, never dropped."""
    from tools.agent_core import filter_known
    from tools.agent_discover import known_names
    _queue_fixture(tmp_path)
    out = filter_known([{"name": "Queuedco AI"}], known_names([], "company", tmp_path))
    assert [c["name"] for c in out] == ["Queuedco AI"]
    assert out[0].get("possible_duplicate") == "Queuedco"


def test_known_names_person_run_ignores_the_queue(tmp_path):
    from tools.agent_discover import known_names
    _queue_fixture(tmp_path)
    assert len(known_names([], "person", tmp_path)) == 0


def test_known_names_without_a_queue_is_the_file_set(tmp_path):
    from tools.agent_discover import known_names
    targets = tmp_path / "targets.yaml"
    targets.write_text("companies:\n  - name: Fileco\n", encoding="utf-8")
    assert len(known_names([str(targets)], "company", tmp_path)) == 1


def _run_main(monkeypatch, root, argv, structured):
    import tools.agent_discover as ad
    calls = []
    monkeypatch.setattr(ad, "_REPO_ROOT", root)
    monkeypatch.setattr(ad, "load_dotenv", lambda: None)
    monkeypatch.setattr(ad, "make_client", lambda: object())

    def fake_run(client, query, schema, effort, timeout_s=600):
        calls.append(query)
        return {"status": "completed", "structured": structured, "cost": None,
                "grounding": None}
    monkeypatch.setattr(ad, "run_agent", fake_run)
    monkeypatch.setattr(ad, "score_company_candidates", lambda c, w, k: c)
    monkeypatch.setattr("sys.argv", ["agent_discover.py", *argv])
    return ad, calls


def test_main_drops_a_queue_pending_company(tmp_path, monkeypatch, capsys):
    import json as _json
    _queue_fixture(tmp_path)
    ad, calls = _run_main(monkeypatch, tmp_path, ["--query", "q", "--entity", "company"],
                          {"companies": [{"name": "Queuedco"}, {"name": "Newco"}]})
    ad.main()
    out = _json.loads(capsys.readouterr().out)
    assert [c["name"] for c in out["candidates"]] == ["Newco"]
    assert calls == ["q"]


def test_main_refuses_an_unreadable_queue_before_the_paid_run(tmp_path, monkeypatch, capsys):
    import json as _json
    import pytest as _pytest
    q = tmp_path / "tools" / ".company-queue.json"
    q.parent.mkdir(parents=True)
    q.write_text("{not json", encoding="utf-8")
    ad, calls = _run_main(monkeypatch, tmp_path, ["--query", "q", "--entity", "company"],
                          {"companies": [{"name": "Newco"}]})
    with _pytest.raises(SystemExit) as e:
        ad.main()
    assert e.value.code == 1
    assert calls == []          # no agent run was paid for
    assert "company queue unreadable" in _json.loads(capsys.readouterr().out)["error"]


import pytest  # noqa: E402


@pytest.mark.parametrize("shape", ["directory", "broken symlink"])
def test_main_refuses_a_queue_path_that_is_not_a_file_before_the_paid_run(
        tmp_path, monkeypatch, capsys, shape):
    """Codex row 1359 P1: a directory or broken symlink used to read as an empty queue."""
    import json as _json
    q = tmp_path / "tools" / ".company-queue.json"
    q.parent.mkdir(parents=True)
    if shape == "directory":
        q.mkdir()
    else:
        q.symlink_to(tmp_path / "gone.json")
    ad, calls = _run_main(monkeypatch, tmp_path, ["--query", "q", "--entity", "company"],
                          {"companies": [{"name": "Newco"}]})
    with pytest.raises(SystemExit) as e:
        ad.main()
    assert e.value.code == 1
    assert calls == []
    assert "company queue unreadable" in _json.loads(capsys.readouterr().out)["error"]
