# Exa Agent — Company & People Discovery, Enrichment, Async, Scheduled Drip

**Date:** 2026-07-24
**Status:** Design approved (pivot from Websets), pending spec review
**Author:** Nick Magnuson (with Claude)
**Supersedes:** `2026-07-24-exa-websets-monitors-people-design.md` (Websets pivot — see "Why the pivot")

## Problem

Nick wants continuous, low-effort discovery of (a) target companies and (b) specific people ("Find a deployment lead at a Series A company that works in person in San Francisco"), scored against his thesis and dripped into `data/inbox.md` for review — no manual waiting, and it should keep finding new results automatically.

The original design targeted the Exa **Websets** API. Live verification on 2026-07-24 proved Websets is unusable for Nick:

- `POST /v0/websets` returns **401** — the account's team does not have Websets API access. An Exa-side contact granted a temporary (~2 week) Websets toggle, but it is not effective for the team at the API layer.
- Websets is on a **deprecation path** at Exa ("people are generally using Agent instead").

## Why the pivot — what actually works

Empirical entitlement check on Nick's one API key (2026-07-24):

| Exa API | Status | Evidence |
|---|---|---|
| Search | works | `exa_search.py` returned 6 real results |
| Answer | works | `client.answer(...)` returned a cited answer |
| **Agent** (`client.beta.agent.runs`) | **works** | live run completed: structured output + per-field citations, $0.025 |
| Websets | 401 | blocked, deprecating |
| Research | 410 | retired for everyone |
| Search Monitors | requires webhook | unusable locally (no inbound endpoint) |

The **Agent API** is not just the only live option — it is a better fit than Websets for this use case:

- **Structured output** via `output_schema` → companies/people with exactly the fields we define (replaces Websets enrichments).
- **Per-field grounding citations** (source URLs) → maps directly onto the dossier evidence rules.
- **One primitive for both entities** → company vs person is just a different query + schema, so the deferred people search comes nearly for free.
- **`effort` knob** → cost/depth control (~$0.025/run at low effort).
- **Conversational continuation** (`previous_run_id`) available if useful later.

Corroboration: Nick's own Agent dashboard shows **completed past runs of his exact use cases** — "Find AI native companies hiring deployment strategists based in San Francisco" (Jul 14, 46 sources; Jun 12, 21 sources) and "Find AI infrastructure companies hiring founding designers" (Jul 24). Output quality for his searches is already proven by his manual runs.

## Scope

In scope:

1. Verify live Agent access (company + person) — already proven; re-run as the Phase 0 gate.
2. On-demand discovery on the Agent API with structured output + `effort`, for company OR person.
3. Async run + collect (don't-make-me-wait) — Agent runs take minutes, so this matters.
4. Scheduled drip: a launchd collector re-runs each preset's Agent query on a cadence, dedups locally, writes new results to `data/inbox.md`.
5. People discovery (the deployment-lead search) as a first-class preset, routed to contacts/networking.

Out of scope:

- Websets (blocked/deprecating).
- Native Search Monitors (require a webhook endpoint Nick won't host).
- Webhooks (rejected earlier — no inbound endpoint).
- Replacing `contact_finder.py` (per-company lookup stays; this adds cross-company people *discovery*).

## Known SDK constraint (must handle)

`exa_py` v2.13.0 has a deserialization bug: a completed Agent run returns `usage.agentComputeUnits: 0.1` (a float), but the SDK's `AgentRun` model requires an integer, so the typed `poll_until_finished` raises `ValidationError` on an otherwise-successful run. **Workaround (proven):** call through the raw request layer — `client.beta.agent.runs.request(endpoint, betas=[...], method=...)` — which returns plain dicts and skips the model validation. All Agent calls in this build go through a thin wrapper over that raw layer, isolating the bug in one place so a future SDK bump is a one-line change.

Beta flag required on every Agent call: `betas=["agent-2026-05-07"]`.

## Architecture

Shared core + thin entrypoints (mirrors the repo's `stage_vocab.py` single-source-of-truth pattern).

### Modules

- **`tools/agent_core.py`** (new — the engine)
  - `.env` load + `Exa` client factory.
  - `run_agent(client, query, output_schema=None, effort="low", system_prompt=None, timeout_s=600)` — creates a run via the raw request layer, polls until `completed`/`failed`/`canceled`, returns a plain dict: `{status, text, structured, grounding, usage, cost}`. Single choke point for the SDK float-bug workaround and the beta flag.
  - `create_run_async(client, query, ...)` → returns `run_id` without polling.
  - `get_run(client, run_id)` → raw-dict fetch of a run's current state/result.
  - Dedup helpers (`_norm_name`, `filter_known`, `load_known_names`) — moved from today's `webset_discover.py` (unchanged logic; still needed).
  - `guess_slug` — moved (unchanged).

- **`tools/agent_discover.py`** (new — on-demand entrypoint; the Websets `webset_discover.py` is retired/left in place but no longer wired)
  - Flags: `--preset`, `--query`, `--entity company|person`, `--effort low|medium|high`, `--count` (hint injected into query), `--async`, `--collect <run_id>`, `--exclude-names-from`.
  - Resolves preset (query + `output_schema` + effort + keywords) from `data/discover-presets.yaml`.
  - Company path: run Agent → take `structured.companies` → map to scorer input keys → `company_scorer.score_company` → sort → JSON to stdout (same shape `/discover-companies` already consumes).
  - Person path: run Agent → take `structured.people` → surface with fields + grounding (no scoring in v1).
  - `--async` prints `{run_id, collect_with}`; `--collect` fetches + scores an existing run.

- **`tools/agent_collect.py`** (new — launchd-driven)
  - For each preset with a `monitor:` block: run the Agent query, normalize `structured`, dedup against known targets (`load_known_names`) **and** a per-preset seen-set (`tools/.agent_seen.json`, keyed by normalized name), score/route the remainder.
  - Company → pipeline-style inbox proposal (with citations). Person → contact-style inbox proposal (reuses the `act_apply.py` contact-add destination on Nick's later acceptance).
  - Append seen names; emit a JSON summary.
  - Note: Agent is stateless (no server-side accumulation), so "keep finding new" = re-run + local diff. Same net effect as a monitor; the seen-set is what makes it incremental.

### Config — `data/discover-presets.yaml`

Per-preset schema (extended):

```yaml
presets:
  lane-a:
    entity_type: company
    query: "AI companies deploying into incumbent enterprises (banks, insurers, mortgage servicers, hospital systems)"
    effort: low
    output_schema:
      type: object
      properties:
        companies:
          type: array
          items:
            type: object
            properties:
              name: {type: string}
              funding_stage: {type: string}
              hq: {type: string}
              description: {type: string}
            required: [name]
    keywords: [...]
    monitor:
      cadence: weekly
  deployment-leads:            # person preset
    entity_type: person
    query: "Find people in deployment lead / forward-deployed engineer / implementation lead roles at Series A AI companies, working in person in San Francisco"
    effort: medium
    output_schema:
      type: object
      properties:
        people:
          type: array
          items:
            type: object
            properties:
              name: {type: string}
              title: {type: string}
              company: {type: string}
              funding_stage: {type: string}
              location: {type: string}
            required: [name]
    monitor:
      cadence: weekly
```

### State files

- `tools/.agent_seen.json` — per-preset list of normalized names already surfaced (prevents re-proposing). Gitignored.

### Scheduling

- `tools/launchd/com.nickmagnuson.jobsearch.agent-discover-collect.plist` — weekly, mirrors `career-scan`. Runs `agent_collect.py`. Logs under `tools/launchd/logs/`.

## Data flow

**On-demand:** `agent_discover.py --preset X [--entity …] [--effort …] [--async]` → `agent_core.run_agent` (or async create) → structured output → (company: score) → JSON to stdout → `/discover-companies` presents. `--collect <run_id>` fetches a prior async run.

**Scheduled:** weekly launchd → `agent_collect.py` → per preset: run Agent → normalize → dedup vs known + seen → score/route → append new proposals to `data/inbox.md`, advance seen-set → Nick reviews inbox; `/act` or `/networking` routes accepted items.

## Error handling

- Missing `EXA_API_KEY` → clean JSON error, exit 1.
- Agent run `failed`/`canceled` → surfaced in JSON with the run's error; collector logs and continues other presets (graceful degradation).
- SDK float bug → never hit, because all calls use the raw request layer (documented above).
- Run timeout (default 600s; person runs have historically taken ~10 min) → return partial/`{status: timeout, run_id}` so `--collect` can retrieve later; collector logs and moves on.
- Empty `structured` (Agent returned prose only) → fall back to `text`; log that the schema wasn't populated.

## Cost

Agent bills per run by effort + searches. Observed: low-effort company run = **$0.025** (3 searches). Person runs at higher effort with many sources cost more (a manual "deployment strategists" run used 46 sources). Defaults: **low effort for company lanes, medium for people**, weekly cadence, small result counts. `effort` and `cadence` are per-preset knobs. The collector logs per-run cost from the `cost` field so spend stays visible.

## Testing & verification

Per the repo rule "data tools are verified on REAL data, not fixtures":

- **Phase 0 (gate):** live Agent run, company + person, structured output — already proven 2026-07-24; re-run to confirm before building.
- Unit tests (fixtures) for the deterministic pieces: structured→scorer key mapping, dedup, seen-set advancement, inbox-block rendering. Agent calls are mocked in unit tests.
- Live end-to-end run of `agent_discover.py` (both entities) and `agent_collect.py` before declaring each phase done — not just green fixtures.
- Cross-path check: `--collect <id>` and the collector normalize an Agent result identically (shared `agent_core`).

## Implementation phases

- **Phase 0** — live Agent access gate (company + person). Proven; re-confirm.
- **Phase 1** — `agent_core.py` (raw-layer wrapper, SDK-bug isolation) + `agent_discover.py` with structured output, `--entity`, `--effort`, `--async`/`--collect`. Verify on-demand live.
- **Phase 2** — `agent_collect.py` (re-run + local seen-diff) + launchd plist. Verify scheduled drip to inbox live.
- **Phase 3** — `deployment-leads` person preset + docs (CLAUDE.md tool/launchd tables). Verify the deployment-lead search live.

## Open questions / deferred

- Person-candidate ranking (deferred; v1 surfaces with fields + citations).
- `previous_run_id` conversational refinement (deferred; not needed for v1).
- Migrating `/discover-companies` skill text from Websets to Agent wording (Phase 3 docs).
