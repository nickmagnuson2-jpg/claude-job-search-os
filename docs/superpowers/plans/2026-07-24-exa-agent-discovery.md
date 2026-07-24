# Exa Agent — Company & People Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover target companies and specific people via the Exa **Agent** API with structured output, score/dedup them, and drip new results into `data/inbox.md` on a weekly launchd cadence — replacing the blocked/deprecating Websets approach.

**Architecture:** A shared `agent_core.py` wraps the Agent API through its raw request layer (isolating an SDK v2.13.0 float bug), exposing sync + async run helpers. Thin entrypoints: `agent_discover.py` (on-demand) and `agent_collect.py` (launchd). Company results score via the existing `company_scorer`; people surface with fields + citations. Since Agent is stateless, "keep finding new" = re-run + local seen-set diff.

**Tech Stack:** Python 3.10+, `exa_py` 2.13.0 (Agent API, beta flag `agent-2026-05-07`), `pyyaml`, pytest, macOS launchd. All invocations prefixed `PYTHONIOENCODING=utf-8`.

**Design spec:** `docs/superpowers/specs/2026-07-24-exa-agent-discovery-design.md`

**Repo conventions:** `PYTHONIOENCODING=utf-8` prefix on every tool call; public-repo PII gate (generic placeholders only — no real names/companies in tool code or tests); verify data tools on REAL data (per-phase live runs are gates); gitignore state files.

---

## File Structure

- **Create** `tools/agent_core.py` — Agent engine (client, `run_agent`, `create_run_async`, `get_run`, dedup helpers).
- **Create** `tools/agent_discover.py` — on-demand entrypoint (`--entity`, `--effort`, `--async`, `--collect`).
- **Create** `tools/agent_collect.py` — launchd collector (re-run + seen-diff → inbox).
- **Create** `tools/test_agent_core.py` — unit tests (mocked Agent).
- **Create** `tools/test_agent_collect.py` — seen-diff + inbox-render tests.
- **Modify** `data/discover-presets.yaml` — add `output_schema`, `effort`, `monitor` per preset; add `deployment-leads` person preset.
- **Modify** `.gitignore` — add `tools/.agent_seen.json`.
- **Create** `tools/launchd/com.nickmagnuson.jobsearch.agent-discover-collect.plist` — weekly.
- **Modify** `CLAUDE.md` — Tools table + launchd table.

The Websets scripts (`webset_discover.py`, `webset_core.py` if present) are left in place but unwired; not deleted in this plan.

---

## Phase 0 — Live Agent access gate

### Task 0: Confirm Agent entitlement (company + person)

**Files:** none (verification; already proven 2026-07-24, re-confirm).

- [ ] **Step 1: Company run via raw layer**

Run:
```bash
PYTHONIOENCODING=utf-8 python3 - <<'PY'
import os, pathlib, json, time
env=pathlib.Path(".env").read_text()
for l in env.splitlines():
    if l.startswith("EXA_API_KEY"): os.environ["EXA_API_KEY"]=l.split("=",1)[1].strip()
from exa_py import Exa
c=Exa(os.environ["EXA_API_KEY"]); B=["agent-2026-05-07"]
schema={"type":"object","properties":{"companies":{"type":"array","items":{"type":"object","properties":{"name":{"type":"string"},"funding_stage":{"type":"string"},"hq":{"type":"string"}},"required":["name"]}}}}
run=c.beta.agent.runs.request("", betas=B, method="POST", data={"query":"Find 3 Series A AI deployment companies HQ in San Francisco.","outputSchema":schema,"effort":"low"})
rid=run["id"]
for _ in range(60):
    time.sleep(3); cur=c.beta.agent.runs.request(f"/{rid}", betas=B, method="GET")
    if cur["status"] in ("completed","failed","canceled"): break
print("STATUS", cur["status"]); print(json.dumps(cur.get("output",{}).get("structured"), default=str)[:400])
PY
```
Expected: `STATUS completed` and a `companies` array with real names. If 401/403 → STOP, access regressed.

- [ ] **Step 2: Person run** — same script, swap query to "Find deployment leads at Series A AI companies working in person in San Francisco" and schema root key to `people` with `title`/`company`/`location`. Expected: `completed` + a `people` array.

---

## Phase 1 — Core + on-demand discovery

### Task 1: `agent_core.py` — the Agent engine

**Files:** Create `tools/agent_core.py`, `tools/test_agent_core.py`

- [ ] **Step 1: Failing test for `run_agent` shape (mocked)**

Create `tools/test_agent_core.py`:
```python
from unittest.mock import MagicMock
from tools.agent_core import run_agent, BETAS


def test_run_agent_returns_structured(monkeypatch):
    client = MagicMock()
    # create returns running, then get returns completed
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
```

- [ ] **Step 2: Run it, verify failure**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_agent_core.py -v`
Expected: FAIL — `ModuleNotFoundError: tools.agent_core`.

- [ ] **Step 3: Implement `agent_core.py`**

Create `tools/agent_core.py`:
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_core.py - Exa Agent engine (raw-request layer to dodge exa_py 2.13.0 float bug)."""
import os, re, time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
BETAS = ["agent-2026-05-07"]


def load_dotenv():
    p = _REPO_ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.replace("export ", "").strip(), v.strip())


def make_client():
    from exa_py import Exa
    key = os.environ.get("EXA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("EXA_API_KEY not set in .env")
    return Exa(key)


def _post(client, query, output_schema, effort):
    data = {"query": query, "effort": effort}
    if output_schema:
        data["outputSchema"] = output_schema
    return client.beta.agent.runs.request("", betas=BETAS, method="POST", data=data)


def create_run_async(client, query, output_schema=None, effort="low"):
    """Create a run; return its id without polling."""
    return _post(client, query, output_schema, effort)["id"]


def get_run(client, run_id):
    """Raw-dict fetch of a run's current state."""
    return client.beta.agent.runs.request(f"/{run_id}", betas=BETAS, method="GET")


def _shape(cur):
    out = cur.get("output") or {}
    return {"status": cur.get("status"), "text": out.get("text"),
            "structured": out.get("structured"), "grounding": out.get("grounding"),
            "usage": cur.get("usage"), "cost": cur.get("cost"),
            "run_id": cur.get("id")}


def run_agent(client, query, output_schema=None, effort="low",
              timeout_s=600, poll_interval_s=3):
    """Create + poll until terminal; return a plain dict (isolates the SDK float bug)."""
    run = _post(client, query, output_schema, effort)
    rid = run["id"]
    deadline = poll_interval_s * (timeout_s // max(poll_interval_s, 1))
    waited = 0
    cur = run
    while waited <= timeout_s:
        cur = get_run(client, rid)
        if cur.get("status") in ("completed", "failed", "canceled"):
            return _shape(cur)
        if poll_interval_s:
            time.sleep(poll_interval_s)
        waited += poll_interval_s or 1
    return {**_shape(cur), "status": "timeout", "run_id": rid}


# --- dedup helpers (moved verbatim from webset_discover.py) ---
def _norm_name(name):
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def filter_known(candidates, known_names):
    return [c for c in candidates if _norm_name(c.get("name", "")) not in known_names]


def guess_slug(name):
    s = re.sub(r"[.,&]", " ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def load_known_names(paths):
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
            for line in text.splitlines():
                if line.strip().startswith("|"):
                    cells = [x.strip() for x in line.strip().strip("|").split("|")]
                    if cells and cells[0] and cells[0].lower() not in ("company", "---"):
                        known.add(_norm_name(cells[0]))
    return known
```

- [ ] **Step 4: Run test, verify pass**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_agent_core.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/agent_core.py tools/test_agent_core.py
git commit -m "feat(agent): agent_core engine over Exa Agent raw-request layer"
```

### Task 2: `agent_discover.py` — on-demand entrypoint

**Files:** Create `tools/agent_discover.py`; Modify `tools/test_agent_core.py`

- [ ] **Step 1: Failing test for structured→scorer key mapping**

Add to `tools/test_agent_core.py`:
```python
def test_company_struct_to_candidate_maps_keys():
    from tools.agent_discover import struct_to_candidates
    structured = {"companies": [{"name": "Acme", "funding_stage": "Series A",
                                 "hq": "San Francisco", "description": "AI infra"}]}
    cands = struct_to_candidates(structured, "company")
    assert cands[0]["name"] == "Acme"
    assert cands[0]["location"] == "San Francisco"   # hq -> location for scorer
    assert cands[0]["stage_text"] == "Series A"      # funding_stage -> stage_text
```

- [ ] **Step 2: Run it, verify failure**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_agent_core.py::test_company_struct_to_candidate_maps_keys -v`
Expected: FAIL — `ModuleNotFoundError: tools.agent_discover`.

- [ ] **Step 3: Implement `agent_discover.py`**

Create `tools/agent_discover.py`. Key pieces:
```python
import argparse, json, sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from tools.agent_core import (load_dotenv, make_client, run_agent, create_run_async,
                              get_run, _shape, filter_known, load_known_names, guess_slug)


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
    weights = data.get("scoring_weights", {"stage": 0.50, "sector": 0.30, "keyword": 0.20})
    return presets[name], weights


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
    schema = preset.get("output_schema")
    if not query:
        _fail("no query: pass --preset or --query")

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

    candidates = filter_known(struct_to_candidates(result["structured"], entity),
                              load_known_names(args.exclude_names_from))
    if entity == "company":
        from tools.career_scanner.scorer import load_scoring_context
        from tools.career_scanner.company_scorer import score_company
        ctx = load_scoring_context(_REPO_ROOT)
        kw = preset.get("keywords") or []
        for c in candidates:
            sc = score_company(c, ctx, weights=weights, keywords=kw)
            c.update({"score": sc["score"], "excluded": sc["excluded"],
                      "geo_flag": sc["geo_flag"]})
        candidates = [c for c in candidates if not c["excluded"]]
        candidates.sort(key=lambda c: c["score"], reverse=True)

    print(json.dumps({"entity": entity, "query": query, "effort": effort,
                      "candidate_count": len(candidates), "cost": result.get("cost"),
                      "grounding": result.get("grounding"),
                      "candidates": candidates, "error": None},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test, verify pass**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_agent_core.py -v`
Expected: PASS.

- [ ] **Step 5: Live on-demand run (real-data gate)**

Run:
```bash
PYTHONIOENCODING=utf-8 python3 tools/agent_discover.py --query "Find 3 Series A AI deployment companies in San Francisco" --entity company --effort low
```
Expected: JSON with scored `candidates`, a `cost` field, `grounding` citations. Then test `--async` + `--collect` round-trip.

- [ ] **Step 6: Commit**

```bash
git add tools/agent_discover.py tools/test_agent_core.py
git commit -m "feat(agent): on-demand agent_discover with structured output + async/collect"
```

---

## Phase 2 — Collector + schedule

### Task 3: `agent_collect.py` — re-run + seen-diff → inbox

**Files:** Create `tools/agent_collect.py`, `tools/test_agent_collect.py`; Modify `.gitignore`

- [ ] **Step 1: Failing tests for seen-diff + inbox render**

Create `tools/test_agent_collect.py`:
```python
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
```

- [ ] **Step 2: Run, verify failure**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_agent_collect.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `agent_collect.py`**

Create `tools/agent_collect.py`:
```python
import json, sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from tools.agent_core import (load_dotenv, make_client, run_agent, _norm_name,
                              filter_known, load_known_names)
from tools.agent_discover import struct_to_candidates, load_preset

SEEN_PATH = _REPO_ROOT / "tools" / ".agent_seen.json"
INBOX = _REPO_ROOT / "data" / "inbox.md"


def read_seen():
    return json.loads(SEEN_PATH.read_text(encoding="utf-8")) if SEEN_PATH.is_file() else {}


def write_seen(state):
    import os, tempfile
    fd, tmp = tempfile.mkstemp(dir=SEEN_PATH.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, SEEN_PATH)


def diff_unseen(candidates, seen_set):
    fresh, updated = [], set(seen_set)
    for c in candidates:
        k = _norm_name(c.get("name", ""))
        if k and k not in updated:
            fresh.append(c); updated.add(k)
    return fresh, updated


def render_inbox_block(record, candidates, today):
    entity = record["entity"]
    lines = [f"\n## {today} | Agent drip: {record['preset']} ({entity})",
             "<!-- review-gated: accept via /act or /networking -->"]
    for c in candidates:
        if entity == "company":
            lines.append(f"- **{c.get('name')}** - {(c.get('description') or '')[:120]} "
                         f"(score {c.get('score','-')})")
        else:
            lines.append(f"- **{c.get('name')}** - {c.get('role','')} @ "
                         f"{c.get('company','')} · {c.get('location','')}")
    return "\n".join(lines) + "\n"


def append_inbox(block):
    prior = INBOX.read_text(encoding="utf-8") if INBOX.is_file() else "# Inbox\n"
    INBOX.write_text(prior + block, encoding="utf-8")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", required=True, help="YYYY-MM-DD (Date.now unavailable in tools)")
    ap.add_argument("--presets-file",
        default=str(_REPO_ROOT / "data" / "discover-presets.yaml"))
    args = ap.parse_args()
    load_dotenv(); client = make_client()
    import yaml
    data = yaml.safe_load(Path(args.presets_file).read_text(encoding="utf-8")) or {}
    seen = read_seen(); summary = {}
    known = load_known_names([str(_REPO_ROOT / "data" / "scan-targets.yaml"),
                              str(_REPO_ROOT / "data" / "job-pipeline.md")])
    for name, preset in (data.get("presets") or {}).items():
        if not preset.get("monitor"):
            continue
        entity = preset.get("entity_type", "company")
        res = run_agent(client, preset["query"], preset.get("output_schema"),
                        preset.get("effort", "low"))
        if res["status"] != "completed":
            summary[name] = {"status": res["status"]}; continue
        cands = filter_known(struct_to_candidates(res["structured"], entity), known)
        fresh, updated = diff_unseen(cands, set(seen.get(name, [])))
        if fresh:
            append_inbox(render_inbox_block({"preset": name, "entity": entity},
                                            fresh, args.today))
        seen[name] = sorted(updated)
        summary[name] = {"new": len(fresh), "cost": res.get("cost")}
    write_seen(seen)
    print(json.dumps({"summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_agent_collect.py -v`
Expected: PASS.

- [ ] **Step 5: Add seen-state to `.gitignore`**

Append `tools/.agent_seen.json` to `.gitignore`.

- [ ] **Step 6: Live collector run (real-data gate)**

Run: `PYTHONIOENCODING=utf-8 python3 tools/agent_collect.py --today 2026-07-24`
Expected: JSON summary; a review-gated block appended to `data/inbox.md` with real names. Re-run → same preset reports `new: 0` (seen-set advanced).

- [ ] **Step 7: Commit**

```bash
git add tools/agent_collect.py tools/test_agent_collect.py .gitignore
git commit -m "feat(agent): weekly collector re-runs agent + seen-diff to inbox"
```

### Task 4: launchd schedule

**Files:** Create `tools/launchd/com.nickmagnuson.jobsearch.agent-discover-collect.plist`

- [ ] **Step 1: Create the plist**

Copy `career-scan.plist`; set `Label` to `com.nickmagnuson.jobsearch.agent-discover-collect`; `ProgramArguments` = `/opt/homebrew/bin/python3 <repo>/tools/agent_collect.py --today` — note: launchd can't compute the date, so wrap in a tiny shell: set `ProgramArguments` to `/bin/sh -c 'PYTHONIOENCODING=utf-8 /opt/homebrew/bin/python3 <repo>/tools/agent_collect.py --today "$(date +%%Y-%%m-%%d)"'`. Keep `PYTHONIOENCODING` env + `WorkingDirectory`; `StartCalendarInterval` Weekday 1 / Hour 8 / Minute 45; logs to `tools/launchd/logs/agent-discover-collect.log`.

- [ ] **Step 2: Install + verify**

Run: `bash tools/launchd/install.sh install && bash tools/launchd/install.sh status | grep agent-discover-collect`
Expected: label loaded.

- [ ] **Step 3: Commit**

```bash
git add tools/launchd/com.nickmagnuson.jobsearch.agent-discover-collect.plist
git commit -m "feat(agent): weekly launchd schedule for agent collector"
```

---

## Phase 3 — People preset + docs

### Task 5: Add `deployment-leads` preset + verify

**Files:** Modify `data/discover-presets.yaml`

- [ ] **Step 1: Add the person preset** — the `deployment-leads` block from the spec (entity person, effort medium, `output_schema` with `people[]`, `monitor.cadence weekly`).

- [ ] **Step 2: Live person discovery (real-data gate)**

Run: `PYTHONIOENCODING=utf-8 python3 tools/agent_discover.py --preset deployment-leads`
Expected: JSON `candidates` with person name/title/company/location; no `score` key.

- [ ] **Step 3: Commit**

```bash
git add data/discover-presets.yaml
git commit -m "feat(agent): deployment-leads person preset"
```

### Task 6: Docs

**Files:** Modify `CLAUDE.md`

- [ ] **Step 1: Tools table** — add `agent_discover.py` (on-demand Agent discovery) and `agent_collect.py` (launchd collector → inbox); note Websets scripts are retired.
- [ ] **Step 2: launchd table** — add `agent-discover-collect | Weekly (Mon 08:45) | agent_collect.py → new companies/people to data/inbox.md`.
- [ ] **Step 3: Commit** — `git commit -m "docs(agent): document agent discovery tools + schedule"`.

---

## Self-Review (at write time)

- **Spec coverage:** Phase 0 gate → Task 0; structured output + on-demand + async → Tasks 1-2; scheduled drip + seen-diff → Tasks 3-4; people entity → Tasks 1-2 (path) + Task 5 (preset); cost visibility → `cost` in outputs + collector summary; SDK float-bug isolation → `agent_core` raw layer (Task 1). All spec sections mapped.
- **Placeholder scan:** none; the one "date via shell" note (launchd can't call `Date.now`) is a concrete `$(date)` wrapper, not a TODO.
- **Type consistency:** `run_agent`, `create_run_async`, `get_run`, `_shape`, `struct_to_candidates`, `load_preset`, `diff_unseen`, `render_inbox_block`, `read_seen/write_seen` named identically across defining and consuming tasks. `struct_to_candidates` maps `hq→location`/`funding_stage→stage_text` consistently with `company_scorer`'s expected keys (`location`, `stage_text`).

## Open risks

- Agent latency: person runs have historically taken ~10 min; `run_agent` default timeout is 600s. If a live run times out, use `--async` + `--collect` (Task 2) rather than raising the timeout unboundedly.
- Structured-output reliability: Agent may occasionally return prose without populating the schema; `struct_to_candidates` returns `[]` and the caller logs it (spec error-handling). Confirm on the Task 2/5 live runs.
- Beta flag `agent-2026-05-07` and the float-bug workaround are `exa_py` 2.13.0-specific; a future SDK bump should re-check both (isolated to `agent_core`).
