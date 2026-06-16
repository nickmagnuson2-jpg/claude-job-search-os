#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""webset_discover.py - deterministic company discovery via Exa Websets.

Reads criteria from data/discover-presets.yaml (or --query/--criteria overrides),
runs an async Webset, dedups against known targets, scores each company with the
deterministic company_scorer, and emits sorted JSON candidates to stdout.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/webset_discover.py --preset lane-a \
      --exclude-names-from data/scan-targets.yaml data/job-pipeline.md
  PYTHONIOENCODING=utf-8 python3 tools/webset_discover.py \
      --query "AI for restaurants" --criteria "Bay Area" --criteria "Series A-C"

Exit codes: 0 ok, 1 config/runtime error (JSON error body still printed).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

# Repo root on sys.path so `tools.career_scanner.*` imports resolve when this
# script is invoked directly (python3 tools/webset_discover.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_dotenv() -> None:
    env_path = _REPO_ROOT / ".env"
    if env_path.is_file():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.replace("export ", "").strip(), v.strip())


def load_preset(presets_path: str, name: str):
    """Return (preset_dict, weights_dict) for a named preset."""
    import yaml
    with open(presets_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    presets = data.get("presets", {})
    if name not in presets:
        raise KeyError(f"preset '{name}' not in {presets_path}")
    weights = data.get("scoring_weights", {"stage": 0.50, "sector": 0.30, "keyword": 0.20})
    return presets[name], weights


def build_search_payload(preset: dict):
    """Build a CreateWebsetParameters object from a preset/override dict.

    Requests a single funding-stage enrichment (read as enrichments[0] later).
    """
    from exa_py.websets.types import (
        CreateWebsetParameters, CreateWebsetParametersSearch,
        CreateCriterionParameters, CreateEnrichmentParameters, WebsetCompanyEntity,
    )
    criteria = [CreateCriterionParameters(description=c)
                for c in (preset.get("criteria") or [])[:5]]
    search = CreateWebsetParametersSearch(
        query=preset["query"],
        count=int(preset.get("count", 25)),
        entity=WebsetCompanyEntity(type="company"),
        criteria=criteria or None,
    )
    enrichments = [CreateEnrichmentParameters(
        description=("The company's most recent funding stage or round, e.g. "
                     "Seed, Series A, Series B, Series C, Series D, public, acquired.")
    )]
    return CreateWebsetParameters(search=search, enrichments=enrichments)


def normalize_item(item) -> dict:
    """Flatten a WebsetItem (pydantic) to a plain candidate dict."""
    props = item.properties
    company = getattr(props, "company", None)
    enrichments = []
    for e in (getattr(item, "enrichments", None) or []):
        enrichments.append({
            "result": getattr(e, "result", None),
            "reasoning": getattr(e, "reasoning", None),
        })
    # Stage text = first enrichment result joined (we request exactly one).
    stage_text = ""
    if enrichments and enrichments[0]["result"]:
        stage_text = " ".join(enrichments[0]["result"])
    return {
        "name": getattr(company, "name", None) if company else None,
        "url": str(props.url) if getattr(props, "url", None) else None,
        "description": getattr(props, "description", "") or "",
        "location": getattr(company, "location", None) if company else None,
        "industry": getattr(company, "industry", None) if company else None,
        "employees": getattr(company, "employees", None) if company else None,
        "about": getattr(company, "about", None) if company else None,
        "stage_text": stage_text,
        "enrichments": enrichments,
    }


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def filter_known(candidates: list, known_names: set) -> list:
    """Drop candidates whose normalized name is in known_names (already normalized)."""
    return [c for c in candidates if _norm_name(c.get("name", "")) not in known_names]


def guess_slug(name: str) -> str:
    """Lowercase-hyphenate a company name into a best-effort ATS slug."""
    s = re.sub(r"[.,&]", " ", (name or "").lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def run_webset(client, payload, timeout: int = 1800):
    """Create a webset, wait until idle, return (candidates, partial).

    On timeout we do NOT discard work: we still fetch whatever items completed
    and return them with partial=True (spec: timeout -> partial result, not a
    crash). Other create/list errors propagate to the caller.
    """
    webset = client.websets.create(payload)
    partial = False
    try:
        client.websets.wait_until_idle(webset.id, timeout=timeout)
    except TimeoutError:
        partial = True
    items = list(client.websets.items.list_all(webset.id))
    return [normalize_item(it) for it in items], partial


def load_known_names(paths: list) -> set:
    """Collect normalized company names to exclude, from scan-targets.yaml
    (companies[].name) and job-pipeline.md (best-effort). Missing files skipped."""
    import yaml
    known = set()
    for path in paths or []:
        p = Path(path)
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        if p.suffix in (".yaml", ".yml"):
            try:
                data = yaml.safe_load(text) or {}
            except yaml.YAMLError:
                continue
            for c in (data.get("companies") or []):
                if isinstance(c, dict) and c.get("name"):
                    known.add(_norm_name(c["name"]))
        else:
            # Markdown table: take the first cell of each row as a candidate name.
            for line in text.splitlines():
                if line.strip().startswith("|"):
                    cells = [x.strip() for x in line.strip().strip("|").split("|")]
                    if cells and cells[0] and cells[0].lower() not in ("company", "---"):
                        known.add(_norm_name(cells[0]))
    return known


def _fail(msg: str) -> None:
    print(json.dumps({"candidates": [], "candidate_count": 0, "error": msg}))
    sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser(description="Discover companies via Exa Websets")
    p.add_argument("--preset", default=None, help="Preset name in the presets file")
    p.add_argument("--presets-file", default=str(_REPO_ROOT / "data" / "discover-presets.yaml"))
    p.add_argument("--query", default=None, help="Override query (skips preset query)")
    p.add_argument("--criteria", action="append", default=None, help="Override criterion (repeatable, max 5)")
    p.add_argument("--keywords", action="append", default=None, help="Override lane keywords (repeatable)")
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--exclude-names-from", nargs="*", default=[])
    args = p.parse_args()

    _load_dotenv()
    api_key = os.environ.get("EXA_API_KEY", "").strip()
    if not api_key:
        _fail("EXA_API_KEY not set in .env")

    # Resolve preset + weights + keywords.
    weights = {"stage": 0.50, "sector": 0.30, "keyword": 0.20}
    preset = {"query": None, "entity_type": "company", "criteria": [], "keywords": [], "count": 25}
    if args.preset:
        try:
            preset, weights = load_preset(args.presets_file, args.preset)
        except (OSError, KeyError) as e:
            _fail(f"preset error: {e}")
    if args.query:
        preset = dict(preset); preset["query"] = args.query
    if args.criteria is not None:
        preset["criteria"] = args.criteria
    if args.keywords is not None:
        preset["keywords"] = args.keywords
    if args.count is not None:
        preset["count"] = args.count
    if not preset.get("query"):
        _fail("no query: pass --preset NAME or --query STR")

    keywords = preset.get("keywords") or []

    # Load scoring context (target_industries from goals.md).
    from tools.career_scanner.scorer import load_scoring_context
    context = load_scoring_context(_REPO_ROOT)

    # Run the webset.
    try:
        from exa_py import Exa
        client = Exa(api_key)
        payload = build_search_payload(preset)
        candidates, partial = run_webset(client, payload, timeout=args.timeout)
    except Exception as e:  # noqa: BLE001  (network/SDK error -> clean JSON)
        _fail(f"webset error: {type(e).__name__}: {e}")

    # Dedup (drop nameless non-company items first), score, enrich with slug.
    candidates = [c for c in candidates if c.get("name")]
    known = load_known_names(args.exclude_names_from)
    candidates = filter_known(candidates, known)
    from tools.career_scanner.company_scorer import score_company
    for c in candidates:
        sc = score_company(c, context, weights=weights, keywords=keywords)
        c.update({
            "score": sc["score"], "score_breakdown": sc["breakdown"],
            "geo_flag": sc["geo_flag"], "excluded": sc["excluded"],
            "slug_guess": guess_slug(c.get("name", "")),
            "careers_url": c.get("url"),
            "ats": "unknown",
        })
    # Keep excluded out of the proposal but report the count.
    excluded_count = sum(1 for c in candidates if c["excluded"])
    kept = [c for c in candidates if not c["excluded"]]
    kept.sort(key=lambda c: c["score"], reverse=True)

    print(json.dumps({
        "preset": args.preset,
        "query": preset.get("query"),
        "candidate_count": len(kept),
        "excluded_count": excluded_count,
        "partial": partial,
        "weights": weights,
        "candidates": kept,
        "error": None,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
