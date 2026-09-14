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


# --- the geo flag must reach the human -------------------------------------------
#
# WHY THIS EXISTS (2026-09-14). score_company returns geo_flag, agent_discover copies
# it onto every candidate, and render_inbox_block dropped it: the company line rendered
# "name - description (score N)" and nothing else. So an unverified location was
# computed, flagged, and then made invisible at the only surface a human reads. A
# 45-company triage was run off those lines; 12 failed geography and several had HQ
# conflicts between two of their own primary sources. Note the PERSON branch has always
# rendered location and the COMPANY branch never did.

def test_company_line_surfaces_an_unverified_location():
    block = render_inbox_block(
        {"entity": "company", "preset": "lane-b"},
        [{"name": "Acme AI", "description": "AI for trades", "score": 3, "geo_flag": True}],
        "2026-09-14")
    assert "Acme AI" in block
    assert "score 3" in block
    low = block.lower()
    assert ("unverified" in low or "location?" in low or "geo?" in low), (
        "an unverified-location company must be visibly marked on its own inbox line -- "
        "the flag is already computed and carried onto the candidate, and dropping it at "
        f"render is what made the score unreadable. Got: {block!r}")


def test_company_line_is_unmarked_when_the_location_is_verified():
    block = render_inbox_block(
        {"entity": "company", "preset": "lane-b"},
        [{"name": "Acme AI", "description": "AI for trades", "score": 5, "geo_flag": False}],
        "2026-09-14")
    low = block.lower()
    assert "unverified" not in low, (
        "a verified-location company must carry no marker, or the marker means nothing")


def test_company_line_treats_a_missing_geo_flag_as_unverified():
    """A candidate that never went through the scorer has not been geo-checked either."""
    block = render_inbox_block(
        {"entity": "company", "preset": "lane-b"},
        [{"name": "Acme AI", "description": "AI for trades", "score": 4}],
        "2026-09-14")
    assert "unverified" in block.lower(), (
        "absent geo_flag must default to unverified, not to verified -- defaulting a "
        "missing check to 'passed' is the same fail-open this whole change removes")


def test_a_known_non_sf_bay_location_is_not_labelled_unverified():
    """geo_flag is a generic REVIEW flag, not an unknown-location flag.

    Found by cross-model review 2026-09-14 (F2). geo_gate sets flag=True for Oakland
    and San Mateo as well as for unknown, so labelling every flagged company
    "[location unverified]" tells the reader a verified Oakland HQ is unverified. The
    renderer must key off the BAND, not the flag.
    """
    block = render_inbox_block(
        {"entity": "company", "preset": "lane-b"},
        [{"name": "Acme AI", "description": "AI for trades", "score": 4,
          "geo_flag": True, "geo_band": "bay"}],
        "2026-09-14")
    low = block.lower()
    assert "unverified" not in low, (
        f"a KNOWN Bay-Area location must not be reported as unverified. Got: {block!r}")
    assert "bay" in low, "the band the reader needs is which geography, not that a flag fired"


def test_unknown_band_still_says_unverified():
    block = render_inbox_block(
        {"entity": "company", "preset": "lane-b"},
        [{"name": "Acme AI", "description": "AI for trades", "score": 3,
          "geo_flag": True, "geo_band": "unknown"}],
        "2026-09-14")
    assert "unverified" in block.lower()
