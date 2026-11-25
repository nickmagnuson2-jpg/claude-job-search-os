import argparse, json, sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from tools.agent_core import (load_dotenv, make_client, run_agent, create_run_async,
                              get_run, _shape, filter_known, load_known_names, guess_slug)
from tools import company_queue

# Default output schemas so an ad-hoc --query (no preset) still yields structured
# output. A preset's own output_schema takes precedence when present.
DEFAULT_SCHEMAS = {
    "company": {"type": "object", "properties": {"companies": {"type": "array", "items": {
        "type": "object", "properties": {
            "name": {"type": "string"}, "funding_stage": {"type": "string"},
            "hq": {"type": "string"}, "description": {"type": "string"}},
        "required": ["name"]}}}},
    "person": {"type": "object", "properties": {"people": {"type": "array", "items": {
        "type": "object", "properties": {
            "name": {"type": "string"}, "title": {"type": "string"},
            "company": {"type": "string"}, "funding_stage": {"type": "string"},
            "location": {"type": "string"}},
        "required": ["name"]}}}},
}


def resolve_schema(preset, entity):
    """Preset's output_schema if set, else the entity default (so --query works)."""
    return preset.get("output_schema") or DEFAULT_SCHEMAS.get(entity)


def struct_to_candidates(structured, entity):
    """Map Agent structured output to candidate dicts.
    company: hq->location, funding_stage->stage_text (keys company_scorer expects)."""
    if not structured:
        return []
    if entity == "company":
        rows = structured.get("companies") or []
        return [{"name": r.get("name"), "location": r.get("hq"),
                 "description": r.get("description", ""), "industry": "",
                 "about": "", "stage_text": r.get("funding_stage", ""),
                 "url": r.get("url"), "slug_guess": guess_slug(r.get("name", ""))}
                for r in rows if r.get("name")]
    rows = structured.get("people") or []
    return [{"name": r.get("name"), "role": r.get("title", ""),
             "company": r.get("company", ""), "location": r.get("location", ""),
             "stage_text": r.get("funding_stage", "")}
            for r in rows if r.get("name")]


def load_preset(presets_path, name):
    import yaml
    data = yaml.safe_load(Path(presets_path).read_text(encoding="utf-8")) or {}
    presets = data.get("presets", {})
    if name not in presets:
        raise KeyError(f"preset '{name}' not in {presets_path}")
    # Global weights, overridable per preset. Lane B needs this: the 2026-08-06
    # decision retired stage as a Lane B criterion ("stage is no longer a criterion;
    # customer shape is"), but the global block weights stage at 0.50 — so Lane B
    # candidates were being marked down on the exact axis that decision removed.
    weights = dict(data.get("scoring_weights",
                            {"stage": 0.50, "sector": 0.30, "keyword": 0.20}))
    weights.update(presets[name].get("scoring_weights") or {})
    return presets[name], weights


def score_company_candidates(candidates, weights=None, keywords=None):
    """Score + geo-exclude + sort company candidates via the shared company_scorer.
    Used by both the on-demand CLI and the collector so scoring is identical."""
    from tools.career_scanner.scorer import load_scoring_context
    from tools.career_scanner.company_scorer import score_company
    ctx = load_scoring_context(_REPO_ROOT)
    for c in candidates:
        sc = score_company(c, ctx, weights=weights, keywords=keywords or [])
        c.update({"score": sc["score"], "excluded": sc["excluded"],
                  "geo_flag": sc["geo_flag"], "geo_band": sc.get("geo_band")})
    kept = [c for c in candidates if not c["excluded"]]
    kept.sort(key=lambda c: c["score"], reverse=True)
    return kept


def known_names(paths, entity, repo_root=_REPO_ROOT):
    """Names a discovery run must not propose again: the --exclude-names-from files, plus,
    for a company run, every company already in the company queue, pending or decided.

    The queue is read through company_queue.load (its own reader; this file never writes
    it). Without this, 5 of 8 lane-a results on 2026-11-24 were companies already waiting
    there. Matching is filter_known's: an exact normalized name is dropped, a looser key
    match is kept and tagged possible_duplicate (company-key-1121 ruling). People are not
    matched against the queue: its person rows are keyed by person+company, not by name."""
    known = load_known_names(paths)
    if entity == "company":
        data = company_queue.load(repo_root)
        for e in data["pending"] + data["decided"]:
            if isinstance(e, dict) and e.get("entity") != "person" and e.get("name"):
                known.add_name(e["name"])
    return known


def _fail(msg):
    print(json.dumps({"candidates": [], "error": msg})); sys.exit(1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--preset"); p.add_argument("--presets-file",
        default=str(_REPO_ROOT / "data" / "discover-presets.yaml"))
    p.add_argument("--query"); p.add_argument("--entity", choices=["company", "person"])
    p.add_argument("--effort", default=None)
    p.add_argument("--async", dest="async_mode", action="store_true")
    p.add_argument("--collect", default=None)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--exclude-names-from", nargs="*", default=[])
    args = p.parse_args()

    load_dotenv()
    try:
        client = make_client()
    except RuntimeError as e:
        _fail(str(e))

    preset, weights = ({}, {"stage": 0.50, "sector": 0.30, "keyword": 0.20})
    if args.preset:
        try:
            preset, weights = load_preset(args.presets_file, args.preset)
        except (OSError, KeyError) as e:
            _fail(f"preset error: {e}")
    entity = args.entity or preset.get("entity_type", "company")
    query = args.query or preset.get("query")
    effort = args.effort or preset.get("effort", "low")
    schema = resolve_schema(preset, entity)
    if not query:
        _fail("no query: pass --preset or --query")
    # Before the paid agent run: an unreadable queue stops here, not after the spend.
    try:
        known = known_names(args.exclude_names_from, entity, _REPO_ROOT)
    except (OSError, ValueError) as e:
        _fail(f"company queue unreadable ({company_queue.QUEUE_NAME}): {e}")

    if args.collect:
        result = _shape(get_run(client, args.collect))
    elif args.async_mode:
        rid = create_run_async(client, query, schema, effort)
        print(json.dumps({"run_id": rid, "collect_with": f"--collect {rid}",
                          "error": None})); return
    else:
        result = run_agent(client, query, schema, effort, timeout_s=args.timeout)

    if result["status"] not in ("completed",):
        _fail(f"agent run {result['status']} (run_id {result.get('run_id')})")

    candidates = filter_known(struct_to_candidates(result["structured"], entity), known)
    if entity == "company":
        candidates = score_company_candidates(candidates, weights, preset.get("keywords"))

    print(json.dumps({"entity": entity, "query": query, "effort": effort,
                      "candidate_count": len(candidates), "cost": result.get("cost"),
                      "grounding": result.get("grounding"),
                      "candidates": candidates, "error": None},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
