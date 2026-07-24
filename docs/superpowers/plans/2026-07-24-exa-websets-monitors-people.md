# Exa Websets — Monitors, Enrichments, Async, People Discovery — Implementation Plan

> **SUPERSEDED 2026-07-24** by `2026-07-24-exa-agent-discovery.md`. Websets 401'd for Nick's team and is deprecating; the build pivoted to the Exa Agent API. Kept for the decision trail — do not execute this plan.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify live Exa Websets access, then extend the discovery stack with configurable enrichments, async create/collect, server-side Monitors that drip new results into `data/inbox.md`, and a person entity for cross-company people discovery.

**Architecture:** Extract a shared `webset_core.py` engine from today's monolithic `webset_discover.py`; keep thin entrypoints (`webset_discover.py` on-demand, `webset_monitor.py` lifecycle, `webset_collect.py` launchd collector). Company results score via the existing `company_scorer`; person results surface with enrichment fields and route to a contact-style inbox proposal. A weekly launchd job runs the collector.

**Tech Stack:** Python 3.10+, `exa_py` SDK (Websets API), `pyyaml`, pytest, macOS launchd. All scripts run with `PYTHONIOENCODING=utf-8`.

**Design spec:** `docs/superpowers/specs/2026-07-24-exa-websets-monitors-people-design.md`

**Repo conventions (must follow):**
- Every `tools/*.py` invocation is prefixed `PYTHONIOENCODING=utf-8 python3 ...` (scripts crash on Unicode otherwise).
- Public-repo PII gate: tests/tool code use only generic placeholder examples — never real contacts or real pipeline-target companies.
- Verify data tools on REAL data, not just fixtures (Phase 0 and per-phase live runs are mandatory gates).
- `.webset_monitors.json` holds account-specific ids → add to `.gitignore`.

---

## File Structure

- **Create** `tools/webset_core.py` — shared engine (client, entity-aware payloads, `normalize_item`, enrichment resolution, dedup, sync/async runners).
- **Modify** `tools/webset_discover.py` — refactor onto core; add `--entity`, `--async`, `--collect`.
- **Create** `tools/webset_monitor.py` — `create` / `list` / `delete` monitors; state in `tools/.webset_monitors.json`.
- **Create** `tools/webset_collect.py` — launchd collector: new items since cursor → inbox proposals.
- **Modify** `data/discover-presets.yaml` — per-preset `entity_type`, `enrichments`, `monitor`; add a `deployment-leads` person preset.
- **Modify** `tools/test_webset_discover.py` — repoint imports to core; keep existing coverage green.
- **Create** `tools/test_webset_core.py` — unit tests for the extracted core (both entities).
- **Create** `tools/test_webset_collect.py` — cursor advancement + routing tests.
- **Create** `tools/launchd/com.nickmagnuson.jobsearch.webset-monitor-collect.plist` — weekly collector schedule.
- **Modify** `.gitignore` — add `tools/.webset_monitors.json`.
- **Modify** `CLAUDE.md` (Tools table + launchd table) — document the three new scripts + the plist.

---

## Phase 0 — Live access gate (verify before building)

### Task 0: Confirm live Websets entitlement for both entity types

**Files:** none (throwaway verification).

- [ ] **Step 1: Company smoke test (small count, short timeout)**

Run:
```bash
PYTHONIOENCODING=utf-8 python3 tools/webset_discover.py --query "AI companies deploying into enterprises" --count 3 --timeout 180
```
Expected: JSON with `"candidate_count"` ≥ 1 and real company names in `candidates[].name`. `"error": null`.

- [ ] **Step 2: Interpret the result**

- If items return → entitlement confirmed for company entity. Proceed.
- If `"error"` contains auth/entitlement/403/plan language → **STOP.** Report to Nick; do not write extension code until access is sorted.

- [ ] **Step 3: Person smoke test (raw SDK, count 3)**

Run:
```bash
PYTHONIOENCODING=utf-8 python3 - <<'PY'
import os, pathlib
env = pathlib.Path("/Users/mag/Documents/Obsidian/30-projects/job-search/.env").read_text()
for line in env.splitlines():
    if line.startswith("EXA_API_KEY"):
        os.environ["EXA_API_KEY"] = line.split("=",1)[1].strip()
from exa_py import Exa
from exa_py.websets.types import (CreateWebsetParameters, CreateWebsetParametersSearch,
    CreateCriterionParameters)
c = Exa(os.environ["EXA_API_KEY"])
search = CreateWebsetParametersSearch(
    query="deployment lead / forward-deployed engineer",
    count=3, entity={"type": "person"},
    criteria=[CreateCriterionParameters(description="Series A company"),
              CreateCriterionParameters(description="based in San Francisco")])
ws = c.websets.create(CreateWebsetParameters(search=search))
c.websets.wait_until_idle(ws.id, timeout=180)
items = list(c.websets.items.list_all(ws.id))
print("PERSON ITEMS:", len(items))
for it in items[:3]:
    print(it.model_dump().get("properties", {}))
PY
```
Expected: `PERSON ITEMS: N` with N ≥ 1 and person-shaped properties. This locks the person-entity property shape used in Task 3's `normalize_item`.

- [ ] **Step 4: Record the person property shape**

Copy the printed `properties` structure into a comment at the top of `tools/webset_core.py` when it's created (Task 1) so the person `normalize_item` maps real field names, not guessed ones.

---

## Phase 1 — Core extraction + enrichments + async

### Task 1: Extract `webset_core.py` and repoint `webset_discover.py`

**Files:**
- Create: `tools/webset_core.py`
- Modify: `tools/webset_discover.py`
- Create: `tools/test_webset_core.py`
- Modify: `tools/test_webset_discover.py:1-12` (imports)

- [ ] **Step 1: Write failing core test for enrichment resolution**

Create `tools/test_webset_core.py`:
```python
from tools.webset_core import resolve_enrichments, normalize_item
from unittest.mock import MagicMock


def test_resolve_enrichments_from_preset_list():
    preset = {"enrichments": [
        {"description": "Funding stage"},
        {"description": "Hiring for deployment roles?"},
    ]}
    out = resolve_enrichments(preset)
    assert len(out) == 2
    assert out[0].description == "Funding stage"


def test_resolve_enrichments_defaults_to_funding_stage_when_absent():
    out = resolve_enrichments({})
    assert len(out) == 1
    assert "funding" in out[0].description.lower()
```

- [ ] **Step 2: Run it, verify failure**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_webset_core.py -v`
Expected: FAIL — `ModuleNotFoundError: tools.webset_core`.

- [ ] **Step 3: Create `webset_core.py` by moving the deterministic functions**

Create `tools/webset_core.py`. Move verbatim from `webset_discover.py`: `_load_dotenv`, `load_preset`, `normalize_item`, `_norm_name`, `filter_known`, `guess_slug`, `load_known_names`, `run_webset`. Add the entity-aware builder and enrichment resolver:

```python
def resolve_enrichments(preset: dict):
    """Return a list of CreateEnrichmentParameters from preset['enrichments'].

    Each entry is a dict {description, format?, options?}. Falls back to a single
    funding-stage enrichment when the preset omits the key (back-compat)."""
    from exa_py.websets.types import CreateEnrichmentParameters
    specs = preset.get("enrichments")
    if not specs:
        return [CreateEnrichmentParameters(
            description=("The company's most recent funding stage or round, e.g. "
                         "Seed, Series A, Series B, Series C, Series D, public, acquired."))]
    out = []
    for s in specs[:5]:
        kwargs = {"description": s["description"]}
        if s.get("format"):
            kwargs["format"] = s["format"]
        if s.get("options"):
            kwargs["options"] = s["options"]
        out.append(CreateEnrichmentParameters(**kwargs))
    return out


def build_search_payload(preset: dict):
    """CreateWebsetParameters for company OR person entity, with resolved enrichments."""
    from exa_py.websets.types import (
        CreateWebsetParameters, CreateWebsetParametersSearch,
        CreateCriterionParameters, WebsetCompanyEntity,
    )
    entity_type = preset.get("entity_type", "company")
    criteria = [CreateCriterionParameters(description=c)
                for c in (preset.get("criteria") or [])[:5]]
    entity = (WebsetCompanyEntity(type="company") if entity_type == "company"
              else {"type": "person"})
    search = CreateWebsetParametersSearch(
        query=preset["query"], count=int(preset.get("count", 25)),
        entity=entity, criteria=criteria or None)
    return CreateWebsetParameters(search=search, enrichments=resolve_enrichments(preset))


def create_async(client, payload):
    """Create a webset without waiting; return its id."""
    return client.websets.create(payload).id


def collect_by_id(client, webset_id: str, entity_type: str = "company"):
    """List + normalize all items for an existing webset id."""
    items = list(client.websets.items.list_all(webset_id))
    return [normalize_item(it, entity_type) for it in items]
```

Update `normalize_item` to take `entity_type` and branch on person vs company using the property shape recorded in Task 0 Step 4. Company branch = today's logic. Person branch maps: `name`, `title`/`role`, current company, `location`, plus `enrichments`.

- [ ] **Step 4: Run core test, verify pass**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_webset_core.py -v`
Expected: PASS.

- [ ] **Step 5: Repoint `webset_discover.py` and its test to import from core**

In `webset_discover.py`, delete the moved functions and import them: `from tools.webset_core import (_load_dotenv, load_preset, build_search_payload, normalize_item, filter_known, guess_slug, load_known_names, run_webset, create_async, collect_by_id, resolve_enrichments)`. In `tools/test_webset_discover.py:1-12`, change the import source from `tools.webset_discover` to `tools.webset_core` for the moved names.

- [ ] **Step 6: Run the full existing suite, verify still green**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_webset_discover.py tools/test_webset_core.py -v`
Expected: PASS (no behavior change — pure refactor).

- [ ] **Step 7: Commit**

```bash
git add tools/webset_core.py tools/webset_discover.py tools/test_webset_core.py tools/test_webset_discover.py
git commit -m "refactor(websets): extract webset_core; configurable enrichments"
```

### Task 2: Add `--entity`, `--async`, `--collect` to `webset_discover.py`

**Files:** Modify: `tools/webset_discover.py`

- [ ] **Step 1: Write failing test for async id passthrough**

Add to `tools/test_webset_core.py`:
```python
def test_create_async_returns_id():
    client = MagicMock()
    client.websets.create.return_value.id = "ws_123"
    from tools.webset_core import create_async
    assert create_async(client, object()) == "ws_123"
```
Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_webset_core.py::test_create_async_returns_id -v`
Expected: PASS (function exists from Task 1) — this documents the contract the CLI relies on.

- [ ] **Step 2: Add argparse flags**

In `webset_discover.py::main`, add:
```python
p.add_argument("--entity", choices=["company", "person"], default=None,
               help="Override entity type (else preset's entity_type)")
p.add_argument("--async", dest="async_mode", action="store_true",
               help="Create the webset and print its id without waiting")
p.add_argument("--collect", default=None, help="Fetch results for an existing webset id")
```

- [ ] **Step 3: Branch on the new flags**

After resolving `preset` and before scoring, apply `--entity` override (`preset["entity_type"] = args.entity` when set). Then:
```python
from exa_py import Exa
client = Exa(api_key)
entity_type = preset.get("entity_type", "company")
if args.collect:
    candidates = collect_by_id(client, args.collect, entity_type)
elif args.async_mode:
    payload = build_search_payload(preset)
    ws_id = create_async(client, payload)
    print(json.dumps({"webset_id": ws_id, "status": "created",
                      "collect_with": f"--collect {ws_id}", "error": None}))
    return
else:
    payload = build_search_payload(preset)
    candidates, partial = run_webset(client, payload, timeout=args.timeout)
```
Guard scoring so person entities skip `company_scorer` (surface only): `if entity_type == "company": <existing score loop> else: <attach slug_guess only, no score>`.

- [ ] **Step 4: Run suite**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_webset_discover.py tools/test_webset_core.py -v`
Expected: PASS.

- [ ] **Step 5: Live async round-trip (real-data gate)**

Run:
```bash
PYTHONIOENCODING=utf-8 python3 tools/webset_discover.py --query "AI for enterprises" --count 3 --async
# copy the printed webset_id, wait ~60s, then:
PYTHONIOENCODING=utf-8 python3 tools/webset_discover.py --collect <webset_id> --count 3
```
Expected: async prints an id immediately (no wait); `--collect` returns scored candidates. Confirms the split works on the live API.

- [ ] **Step 6: Commit**

```bash
git add tools/webset_discover.py tools/test_webset_core.py
git commit -m "feat(websets): --entity, --async, --collect on webset_discover"
```

---

## Phase 2 — Monitors + collector + schedule

### Task 3: `webset_monitor.py` — create / list / delete

**Files:**
- Create: `tools/webset_monitor.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing state round-trip test**

Create `tools/test_webset_collect.py` (shared by Tasks 3-4):
```python
import json
from tools.webset_monitor import read_state, write_state, upsert_monitor


def test_upsert_and_read_state(tmp_path):
    sf = tmp_path / ".webset_monitors.json"
    upsert_monitor(sf, {"preset": "lane-a", "entity": "company",
                        "webset_id": "ws_1", "monitor_id": "mon_1",
                        "cadence": "weekly", "created": "2026-07-24", "last_seen": None})
    state = read_state(sf)
    assert state["monitors"][0]["webset_id"] == "ws_1"
```

- [ ] **Step 2: Run it, verify failure**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_webset_collect.py::test_upsert_and_read_state -v`
Expected: FAIL — `ModuleNotFoundError: tools.webset_monitor`.

- [ ] **Step 3: Implement state + create/list/delete**

Create `tools/webset_monitor.py` with:
```python
def read_state(path):
    import json, pathlib
    p = pathlib.Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"monitors": []}

def write_state(path, state):
    import json, pathlib, os, tempfile
    p = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, p)

def upsert_monitor(path, record):
    state = read_state(path)
    state["monitors"] = [m for m in state["monitors"]
                         if m["preset"] != record["preset"]]
    state["monitors"].append(record)
    write_state(path, state)
```
`cmd_create(preset_name, cadence)`: load preset via `webset_core.load_preset`, `build_search_payload`, `client.websets.create(payload)`, then attach a monitor:
```python
from exa_py.websets.types import CreateMonitorParameters
mon = client.websets.monitors.create(CreateMonitorParameters(
    webset_id=ws.id, cadence=cadence,
    behavior={"type": "search"}))
upsert_monitor(state_path, {"preset": preset_name, "entity": preset.get("entity_type","company"),
    "webset_id": ws.id, "monitor_id": mon.id, "cadence": cadence,
    "created": <pass date in via arg — no Date.now in this repo's tooling>, "last_seen": None})
```
Note: the exact `cadence`/`behavior` object shape must be confirmed against the SDK during Task 0 (introspect `MonitorCadence` and `MonitorBehaviorSearch`); use whatever concrete shape the SDK requires. `list` prints `read_state`; `delete <preset>` calls `client.websets.monitors.delete(monitor_id)` and removes the record.

- [ ] **Step 4: Run state test, verify pass**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_webset_collect.py::test_upsert_and_read_state -v`
Expected: PASS.

- [ ] **Step 5: Add state file to `.gitignore`**

Append `tools/.webset_monitors.json` to `.gitignore`.

- [ ] **Step 6: Live monitor create (real-data gate)**

Run:
```bash
PYTHONIOENCODING=utf-8 python3 tools/webset_monitor.py create --preset lane-a --cadence weekly
PYTHONIOENCODING=utf-8 python3 tools/webset_monitor.py list
```
Expected: a monitor id is returned and stored; `list` shows one record. If the SDK rejects the cadence/behavior shape, fix per its introspected types before continuing.

- [ ] **Step 7: Commit**

```bash
git add tools/webset_monitor.py tools/test_webset_collect.py .gitignore
git commit -m "feat(websets): monitor lifecycle (create/list/delete) + state"
```

### Task 4: `webset_collect.py` — new items since cursor → inbox proposals

**Files:**
- Create: `tools/webset_collect.py`
- Modify: `tools/test_webset_collect.py`

- [ ] **Step 1: Write failing cursor test**

Add to `tools/test_webset_collect.py`:
```python
from tools.webset_collect import new_items_since


def test_new_items_since_filters_by_cursor():
    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    fresh, cursor = new_items_since(items, last_seen="a")
    assert [i["id"] for i in fresh] == ["b", "c"]
    assert cursor == "c"


def test_new_items_since_none_cursor_returns_all():
    items = [{"id": "a"}, {"id": "b"}]
    fresh, cursor = new_items_since(items, last_seen=None)
    assert len(fresh) == 2
    assert cursor == "b"
```

- [ ] **Step 2: Run it, verify failure**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_webset_collect.py -k new_items_since -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement collector**

Create `tools/webset_collect.py`:
```python
def new_items_since(items, last_seen):
    """Return (fresh_items, new_cursor). Items are assumed API-ordered oldest->newest.
    fresh = everything after the item whose id == last_seen; all if last_seen is None."""
    if last_seen is None:
        fresh = list(items)
    else:
        idx = next((i for i, it in enumerate(items) if it.get("id") == last_seen), None)
        fresh = list(items) if idx is None else items[idx + 1:]
    cursor = items[-1]["id"] if items else last_seen
    return fresh, cursor
```
`main()`: `_load_dotenv`, build `Exa` client, `read_state`. For each monitor record: `raw = list(client.websets.items.list_all(webset_id))`; convert each to `{"id": it.id, **normalize_item(it, entity)}`; `fresh, cursor = new_items_since(...)`; dedup via `filter_known(load_known_names([...targets, pipeline...]))`; route (Step 4 helper); update `record["last_seen"] = cursor`; `write_state`. Print a JSON summary `{preset: {"new": n, "written": w}}`.

- [ ] **Step 4: Add routing helper + test**

Add to `webset_collect.py`:
```python
def render_inbox_block(record, candidates, today):
    """Return a markdown proposal block for data/inbox.md (review-gated, not auto-applied)."""
    entity = record["entity"]
    lines = [f"\n## {today} | Webset drip: {record['preset']} ({entity})",
             "<!-- review-gated: accept via /act or /networking -->"]
    for c in candidates:
        if entity == "company":
            lines.append(f"- **{c.get('name')}** — {c.get('description','')[:120]} "
                         f"[{c.get('url','')}] (score {c.get('score','-')})")
        else:
            lines.append(f"- **{c.get('name')}** — {c.get('role','')} @ "
                         f"{c.get('company','')} · {c.get('location','')}")
    return "\n".join(lines) + "\n"
```
Add test:
```python
from tools.webset_collect import render_inbox_block

def test_render_inbox_block_person():
    rec = {"preset": "deployment-leads", "entity": "person"}
    block = render_inbox_block(rec, [{"name": "A. Person", "role": "Deployment Lead",
        "company": "Acme", "location": "SF"}], "2026-07-24")
    assert "Deployment Lead" in block and "review-gated" in block
```
The collector appends this block to `data/inbox.md` (create-if-missing, atomic write). It never calls `act_apply` directly — routing is Nick's review step, consistent with `alirohde-triage`.

- [ ] **Step 5: Run collector tests, verify pass**

Run: `PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_webset_collect.py -v`
Expected: PASS.

- [ ] **Step 6: Live collector run (real-data gate)**

Run: `PYTHONIOENCODING=utf-8 python3 tools/webset_collect.py`
Expected: JSON summary; if the lane-a monitor has produced items, a review-gated block appears at the top-relevant section of `data/inbox.md`. Inspect the block for real company names + scores. Re-run immediately → second run reports `new: 0` (cursor advanced; no duplicates).

- [ ] **Step 7: Commit**

```bash
git add tools/webset_collect.py tools/test_webset_collect.py
git commit -m "feat(websets): collector drips new monitored items to inbox"
```

### Task 5: launchd schedule for the collector

**Files:** Create: `tools/launchd/com.nickmagnuson.jobsearch.webset-monitor-collect.plist`

- [ ] **Step 1: Create the plist (weekly, mirrors career-scan)**

Copy `career-scan.plist`, change `Label` to `com.nickmagnuson.jobsearch.webset-monitor-collect`, `ProgramArguments` to run `tools/webset_collect.py` (use `/opt/homebrew/bin/python3` + absolute script path, keep `PYTHONIOENCODING=utf-8` env + `WorkingDirectory`), set `StartCalendarInterval` to Weekday 1 / Hour 8 / Minute 45, and log paths to `tools/launchd/logs/webset-monitor-collect.log`.

- [ ] **Step 2: Install + verify loaded**

Run:
```bash
bash tools/launchd/install.sh install
bash tools/launchd/install.sh status | grep webset-monitor-collect
```
Expected: label listed as loaded.

- [ ] **Step 3: Commit**

```bash
git add tools/launchd/com.nickmagnuson.jobsearch.webset-monitor-collect.plist
git commit -m "feat(websets): weekly launchd collector schedule"
```

---

## Phase 3 — People preset + docs

### Task 6: Add the `deployment-leads` person preset and verify end-to-end

**Files:** Modify: `data/discover-presets.yaml`

- [ ] **Step 1: Add the person preset**

Add under `presets:`:
```yaml
  deployment-leads:
    entity_type: person
    query: "deployment lead, forward-deployed engineer, or implementation lead at an early-stage AI company"
    criteria:
      - "Works at a Series A company"
      - "Role is in person in San Francisco"
    enrichments:
      - description: "Current job title"
      - description: "Current company and its most recent funding stage"
      - description: "Whether the role is based in person in San Francisco"
    count: 15
    monitor:
      cadence: weekly
```

- [ ] **Step 2: Live on-demand person discovery (real-data gate)**

Run: `PYTHONIOENCODING=utf-8 python3 tools/webset_discover.py --preset deployment-leads --count 5`
Expected: JSON `candidates` with person names, titles, company, location from enrichments; no `score` key (person path skips scoring). Inspect for the "deployment lead at a Series A, in person in SF" shape you asked for.

- [ ] **Step 3: Commit**

```bash
git add data/discover-presets.yaml
git commit -m "feat(websets): deployment-leads person preset"
```

### Task 7: Document the new tools

**Files:** Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Tools & atomic-scripts table**

Add rows for `webset_monitor.py` (create/list/delete monitored websets) and `webset_collect.py` (launchd collector → inbox). Note `webset_discover.py` now supports `--entity`, `--async`, `--collect`.

- [ ] **Step 2: Update the launchd table**

Add a row: `webset-monitor-collect | Weekly (Mon 08:45) | webset_collect.py → new monitored companies/people to data/inbox.md`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(websets): document monitor/collect tools + schedule"
```

---

## Self-Review (completed at write time)

- **Spec coverage:** verify-first gate → Task 0; enrichments → Task 1; async → Task 2; monitors → Task 3; collector + inbox → Task 4; schedule → Task 5; people entity → Tasks 1/2 (entity plumbing) + Task 6 (preset); cost defaults → encoded in presets (count/cadence) and Task 6. All spec sections mapped.
- **Placeholder scan:** the two intentional "confirm against the live SDK" notes (Monitor cadence/behavior shape, person property shape) are gated in Task 0 and flagged where used — they are verification steps, not unresolved placeholders, because the concrete shape can only come from the live SDK introspection Nick now has access to.
- **Type consistency:** `resolve_enrichments`, `build_search_payload`, `create_async`, `collect_by_id`, `normalize_item(item, entity_type)`, `new_items_since`, `read_state/write_state/upsert_monitor`, `render_inbox_block` are named identically across defining and consuming tasks.

## Open risks

- Monitor `cadence` / `behavior` object shape is SDK-version-specific — Task 0 introspection resolves it before Task 3 hard-codes it.
- Person-entity property names come from live output (Task 0 Step 4), not documentation — the person `normalize_item` branch must be written against that recorded shape.
- `list_all` ordering assumption in `new_items_since` (oldest→newest) must be confirmed in Task 4 Step 6; if the API returns newest-first, reverse before cursoring.
