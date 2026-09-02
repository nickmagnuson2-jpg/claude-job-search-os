from tools.agent_collect import diff_unseen, render_inbox_block


def test_diff_unseen_filters_seen():
    cands = [{"name": "Acme"}, {"name": "Beta Co"}]
    fresh, updated = diff_unseen(cands, {"acme"})
    assert [c["name"] for c in fresh] == ["Beta Co"]
    assert "beta co" in updated


def test_render_inbox_block_person():
    block = render_inbox_block({"preset": "deployment-leads", "entity": "person"},
        [{"name": "Casey Doe", "role": "Deployment Lead", "company": "Acme",
          "location": "SF"}], "2026-07-24")
    assert "Deployment Lead" in block and "review-gated" in block


def test_collect_main_scores_companies_and_writes_inbox(tmp_path, monkeypatch):
    """Collector: company preset -> Agent -> score -> new items -> inbox block + seen."""
    import tools.agent_collect as ac

    presets = tmp_path / "presets.yaml"
    presets.write_text(
        "scoring_weights: {stage: 0.5, sector: 0.3, keyword: 0.2}\n"
        "presets:\n"
        "  lane-x:\n"
        "    entity_type: company\n"
        "    query: q\n"
        "    monitor: {cadence: weekly}\n",
        encoding="utf-8",
    )
    seen_file = tmp_path / ".agent_seen.json"
    inbox_file = tmp_path / "inbox.md"
    monkeypatch.setattr(ac, "SEEN_PATH", seen_file)
    monkeypatch.setattr(ac, "INBOX", inbox_file)
    monkeypatch.setattr(ac, "load_dotenv", lambda: None)
    monkeypatch.setattr(ac, "make_client", lambda: object())
    monkeypatch.setattr(ac, "load_known_names", lambda paths: set())
    # Agent returns one company; scoring stamps a score.
    monkeypatch.setattr(ac, "run_agent", lambda *a, **k: {
        "status": "completed",
        "structured": {"companies": [{"name": "Acme", "hq": "San Francisco",
                                      "funding_stage": "Series A", "description": "d"}]},
        "costDollars": {"total": 0.02}})
    monkeypatch.setattr(ac, "score_company_candidates",
                        lambda cands, weights, keywords: [{**c, "score": 7} for c in cands])

    monkeypatch.setattr("sys.argv", ["agent_collect.py", "--today", "2026-07-24",
                                     "--presets-file", str(presets)])
    ac.main()

    body = inbox_file.read_text(encoding="utf-8")
    assert "Agent drip: lane-x (company)" in body
    assert "Acme" in body and "score 7" in body
    import json
    assert "acme" in json.loads(seen_file.read_text(encoding="utf-8"))["lane-x"]


def test_collect_main_skips_unmonitored_and_non_completed(tmp_path, monkeypatch):
    """Unmonitored presets are skipped; a failed run writes no inbox block."""
    import tools.agent_collect as ac
    presets = tmp_path / "presets.yaml"
    presets.write_text(
        "presets:\n"
        "  no-monitor: {entity_type: company, query: q}\n"
        "  failing: {entity_type: company, query: q, monitor: {cadence: weekly}}\n",
        encoding="utf-8",
    )
    seen_file = tmp_path / ".agent_seen.json"
    inbox_file = tmp_path / "inbox.md"
    monkeypatch.setattr(ac, "SEEN_PATH", seen_file)
    monkeypatch.setattr(ac, "INBOX", inbox_file)
    monkeypatch.setattr(ac, "load_dotenv", lambda: None)
    monkeypatch.setattr(ac, "make_client", lambda: object())
    monkeypatch.setattr(ac, "load_known_names", lambda paths: set())
    monkeypatch.setattr(ac, "run_agent", lambda *a, **k: {"status": "failed", "run_id": "r1"})

    monkeypatch.setattr("sys.argv", ["agent_collect.py", "--today", "2026-07-24",
                                     "--presets-file", str(presets)])
    ac.main()
    # failing preset produced no inbox file (no fresh candidates written)
    assert not inbox_file.exists()
