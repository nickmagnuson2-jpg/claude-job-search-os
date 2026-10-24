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
    monkeypatch.setattr(ac, "PIPELINE", tmp_path / "no-pipeline.md")
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
    monkeypatch.setattr(ac, "PIPELINE", tmp_path / "no-pipeline.md")
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


PIPE = """# Job Pipeline

## Active Pipeline

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
|---|---|---|---|---|---|---|---|
| Acme AI | Ops Lead | Applied | 2026-01-10 | follow up | — | — | — |
| Northwind Labs | Strategist | Researching | 2026-01-10 | — | — | — | — |
"""


def test_reconcile_drops_a_pipeline_company_and_marks_a_near_match(tmp_path):
    """362.F5: exact-name filtering let `Acme` through while `Acme AI` was in the pipeline."""
    from tools.agent_collect import reconcile_candidates
    pipe = tmp_path / "job-pipeline.md"
    pipe.write_text(PIPE, encoding="utf-8")
    cands = [{"name": "Northwind Labs"}, {"name": "Acme"}, {"name": "Beacon"}]
    out = reconcile_candidates(cands, pipe)
    assert [c["name"] for c in out] == ["Acme", "Beacon"], "an exact pipeline company is known"
    assert out[0]["possible_match"] == "Acme AI"
    assert "possible_match" not in out[1]


def test_reconcile_passes_candidates_through_when_it_cannot_run(tmp_path, monkeypatch):
    import pipeline_reconcile as pr
    from tools.agent_collect import reconcile_candidates

    def broken(*a, **k):
        raise OSError("unreadable")
    monkeypatch.setattr(pr, "resolve", broken)
    cands = [{"name": "Acme"}]
    assert reconcile_candidates(cands, tmp_path / "x.md") == cands


def test_a_possible_match_is_printed_in_the_inbox_line():
    from tools.agent_collect import render_inbox_block
    block = render_inbox_block({"preset": "p", "entity": "company"},
                               [{"name": "Acme", "description": "d", "score": 7,
                                 "geo_band": "sf", "possible_match": "Acme AI"}], "2026-01-02")
    assert "**Acme**" in block and "[possible match: Acme AI in pipeline]" in block


def _collect_env(tmp_path, monkeypatch, presets_yaml, agent_result):
    import tools.agent_collect as ac
    presets = tmp_path / "presets.yaml"
    presets.write_text(presets_yaml, encoding="utf-8")
    monkeypatch.setattr(ac, "SEEN_PATH", tmp_path / ".agent_seen.json")
    monkeypatch.setattr(ac, "INBOX", tmp_path / "inbox.md")
    monkeypatch.setattr(ac, "PIPELINE", tmp_path / "no-pipeline.md")
    calls = []
    monkeypatch.setattr(ac, "load_dotenv", lambda: calls.append("dotenv"))
    monkeypatch.setattr(ac, "make_client", lambda: object())
    monkeypatch.setattr(ac, "load_known_names", lambda paths: set())
    monkeypatch.setattr(ac, "run_agent", lambda client, query, *a, **k: calls.append(query) or agent_result)
    monkeypatch.setattr("sys.argv", ["agent_collect.py", "--today", "2026-07-24",
                                     "--presets-file", str(presets)])
    return ac, calls


def test_an_unmonitored_preset_never_reaches_the_agent(tmp_path, monkeypatch, capsys):
    """The older test could not see this: a skipped preset and a failed one both leave no
    inbox file. Counting agent calls can."""
    import json
    ac, calls = _collect_env(tmp_path, monkeypatch,
                             "presets:\n  quiet: {entity_type: company, query: q-quiet}\n"
                             "  live: {entity_type: company, query: q-live, monitor: {cadence: weekly}}\n",
                             {"status": "failed", "run_id": "r1"})
    ac.main()
    assert calls == ["dotenv", "q-live"], "the API key loads before the first agent call"
    assert json.loads(capsys.readouterr().out) == {"summary": {"live": {"status": "failed"}}}


def test_a_person_preset_is_neither_reconciled_nor_scored_as_companies(tmp_path, monkeypatch):
    ac, _ = _collect_env(tmp_path, monkeypatch,
                         "presets:\n  people: {entity_type: person, query: q, monitor: {cadence: weekly}}\n",
                         {"status": "completed",
                          "structured": {"people": [{"name": "Casey Doe", "title": "Ops",
                                                     "company": "Acme", "location": "SF"}]}})

    def company_only(*a, **k):
        raise AssertionError("a company step ran on a person preset")
    monkeypatch.setattr(ac, "reconcile_candidates", company_only)
    monkeypatch.setattr(ac, "score_company_candidates", company_only)
    ac.main()
    assert "Casey Doe" in (tmp_path / "inbox.md").read_text(encoding="utf-8")


def test_a_run_whose_candidates_were_all_seen_writes_no_inbox_block(tmp_path, monkeypatch):
    import json
    ac, _ = _collect_env(tmp_path, monkeypatch,
                         "presets:\n  live: {entity_type: company, query: q, monitor: {cadence: weekly}}\n",
                         {"status": "completed",
                          "structured": {"companies": [{"name": "Acme", "description": "d"}]}})
    (tmp_path / ".agent_seen.json").write_text(json.dumps({"live": ["acme"]}), encoding="utf-8")
    monkeypatch.setattr(ac, "score_company_candidates", lambda c, w, k: c)
    ac.main()
    assert not (tmp_path / "inbox.md").exists()


def test_a_failed_inbox_write_is_reported_on_stderr(tmp_path, monkeypatch, capsys):
    ac, _ = _collect_env(tmp_path, monkeypatch,
                         "presets:\n  live: {entity_type: company, query: q, monitor: {cadence: weekly}}\n",
                         {"status": "completed",
                          "structured": {"companies": [{"name": "Acme", "description": "d"}]}})
    monkeypatch.setattr(ac, "score_company_candidates", lambda c, w, k: c)

    def locked(*a, **k):
        raise ac.inbox_lock.LockTimeout("inbox locked")
    monkeypatch.setattr(ac.inbox_lock, "prepend_entries", locked)
    ac.main()
    assert "proposals NOT written" in capsys.readouterr().err


def test_the_script_imports_and_runs_main_only_as_a_script(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / "tools" / "agent_collect.py"
    imported = subprocess.run([sys.executable, "-c",
                               f"import runpy; runpy.run_path({str(script)!r}, run_name='x')"],
                              cwd=str(tmp_path), capture_output=True, text=True)
    assert imported.returncode == 0, imported.stderr
    as_script = subprocess.run([sys.executable, str(script), "--no-such-flag"],
                               cwd=str(tmp_path), capture_output=True, text=True)
    assert as_script.returncode != 0, "run as a script, main() must parse its arguments"
