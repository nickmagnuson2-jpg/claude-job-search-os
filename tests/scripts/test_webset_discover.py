import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock
from tools.webset_discover import (
    load_preset,
    build_search_payload,
    filter_known,
    guess_slug,
    normalize_item,
    run_webset,
    load_known_names,
)


def test_load_preset_reads_named_preset(tmp_path):
    p = tmp_path / "presets.yaml"
    p.write_text(
        "scoring_weights:\n  stage: 0.5\n  sector: 0.3\n  keyword: 0.2\n"
        "presets:\n"
        "  lane-a:\n"
        "    query: \"q\"\n"
        "    entity_type: company\n"
        "    criteria: [\"c1\", \"c2\"]\n"
        "    keywords: [\"k1\"]\n"
        "    count: 25\n",
        encoding="utf-8",
    )
    preset, weights = load_preset(str(p), "lane-a")
    assert preset["query"] == "q"
    assert preset["criteria"] == ["c1", "c2"]
    assert preset["keywords"] == ["k1"]
    assert weights == {"stage": 0.5, "sector": 0.3, "keyword": 0.2}


def test_load_preset_unknown_name_raises(tmp_path):
    p = tmp_path / "presets.yaml"
    p.write_text("presets:\n  lane-a:\n    query: q\n", encoding="utf-8")
    with pytest.raises(KeyError):
        load_preset(str(p), "nope")


def test_build_search_payload_shape():
    preset = {"query": "q", "entity_type": "company",
              "criteria": ["c1", "c2"], "count": 25}
    payload = build_search_payload(preset)
    assert payload.search.query == "q"
    assert payload.search.count == 25
    assert len(payload.search.criteria) == 2
    assert payload.enrichments and len(payload.enrichments) == 1


def test_build_search_payload_caps_criteria_at_5():
    preset = {"query": "q", "entity_type": "company",
              "criteria": ["c1", "c2", "c3", "c4", "c5", "c6"], "count": 10}
    payload = build_search_payload(preset)
    assert len(payload.search.criteria) == 5


def test_filter_known_removes_matching_names():
    candidates = [{"name": "Northwind"}, {"name": "Brand New Co"}, {"name": "contoso"}]
    known = {"northwind", "contoso", "acme-ai"}
    out = filter_known(candidates, known)
    assert [c["name"] for c in out] == ["Brand New Co"]


def test_guess_slug():
    assert guess_slug("Brand New Co.") == "brand-new-co"
    assert guess_slug("AI & Robotics, Inc") == "ai-robotics-inc"


def _fake_item(name, location, url, stage_result, description="AI co"):
    item = MagicMock()
    item.properties.url = url
    item.properties.description = description
    item.properties.company.name = name
    item.properties.company.location = location
    item.properties.company.industry = "AI"
    item.properties.company.employees = 50
    item.properties.company.about = description
    enr = MagicMock()
    enr.result = [stage_result]
    enr.reasoning = "from filing"
    item.enrichments = [enr]
    return item


def test_run_webset_creates_waits_and_lists():
    fake_client = MagicMock()
    created = MagicMock()
    created.id = "ws_123"
    fake_client.websets.create.return_value = created
    fake_client.websets.items.list_all.return_value = iter([
        _fake_item("Acme AI", "San Francisco, CA", "https://acme.ai", "Series C"),
        _fake_item("Beta AI", "Austin, TX", "https://beta.ai", "Seed"),
    ])

    payload = MagicMock()
    items, partial = run_webset(fake_client, payload, timeout=10)

    fake_client.websets.create.assert_called_once_with(payload)
    fake_client.websets.wait_until_idle.assert_called_once_with("ws_123", timeout=10)
    fake_client.websets.items.list_all.assert_called_once_with("ws_123")
    assert partial is False
    assert len(items) == 2
    assert items[0]["name"] == "Acme AI"
    assert items[0]["stage_text"] == "Series C"


def test_run_webset_timeout_returns_partial_not_crash():
    # On wait_until_idle TimeoutError, still fetch completed items, partial=True.
    fake_client = MagicMock()
    created = MagicMock()
    created.id = "ws_to"
    fake_client.websets.create.return_value = created
    fake_client.websets.wait_until_idle.side_effect = TimeoutError("idle timeout")
    fake_client.websets.items.list_all.return_value = iter([
        _fake_item("Partial Co", "San Francisco, CA", "https://p.co", "Series B"),
    ])

    items, partial = run_webset(fake_client, MagicMock(), timeout=5)
    assert partial is True
    assert len(items) == 1
    assert items[0]["name"] == "Partial Co"


def test_load_known_names_reads_yaml_and_md(tmp_path):
    targets = tmp_path / "scan-targets.yaml"
    targets.write_text("companies:\n  - name: Northwind\n  - name: Contoso\n", encoding="utf-8")
    pipe = tmp_path / "job-pipeline.md"
    pipe.write_text("| Company | Stage |\n| Acme AI | Applied |\n", encoding="utf-8")
    names = load_known_names([str(targets), str(pipe)])
    assert "northwind" in names
    assert "contoso" in names
    assert any("acme ai" in n for n in names)


def test_load_known_names_missing_files_skip_silently(tmp_path):
    names = load_known_names([str(tmp_path / "nope.yaml")])
    assert names == set()
