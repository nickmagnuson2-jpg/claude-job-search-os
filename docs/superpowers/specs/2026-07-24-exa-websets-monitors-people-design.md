# Exa Websets — Monitors, Enrichments, Async, and People Discovery

> **SUPERSEDED 2026-07-24** by `2026-07-24-exa-agent-discovery-design.md`. Live verification found the Websets API returns 401 for Nick's team (no entitlement) and is deprecating in favor of the Exa **Agent** API, which works on his key. The design pivoted to Agent (same scoring/dedup/inbox/launchd plumbing). Kept for the decision trail.

**Date:** 2026-07-24
**Status:** Superseded (Websets unreachable — pivoted to Agent)
**Author:** Nick Magnuson (with Claude)

## Problem

The Exa Websets API is already wired into `tools/webset_discover.py` (powers `/discover-companies`), but it was never verified against the live API — Nick lacked Websets entitlement on his account until it was granted via the org. The code has only a fixture test (`tools/test_webset_discover.py`), so live behavior is unproven.

On top of verifying the base, Nick wants three capabilities:

1. **"Keep finding new companies automatically"** — continuous, hands-off discovery.
2. **"Don't make me wait"** — no blocking for up to 30 minutes on an on-demand run.
3. **Cross-company people discovery** — e.g. "Find a deployment lead at a Series A company that works in person in San Francisco."

Webhooks were considered and **rejected**: they require a public HTTP endpoint for Exa to call back, which a local CLI on Nick's Mac does not have. The underlying wants map onto native Websets features instead — **Monitors** (server-side scheduled re-runs) and **async create/collect**.

## Scope

In scope for this build (company-side + people-side, one spec):

1. Verify live Websets access (company and person entities).
2. Configurable, expanded enrichments.
3. Async on-demand discovery (kick off + collect later).
4. Monitors + a launchd collector that drips new results into `data/inbox.md`.
5. People entity — cross-company people discovery, routed to contacts/networking.

Explicitly **out of scope**:

- Webhooks (rejected — no inbound endpoint).
- Replacing `contact_finder.py` (per-company people lookup stays as-is; this adds *cross-company* people *discovery*, a different capability).
- Heavy people-candidate scoring (v1 surfaces people with enrichment fields; ranking is a fast-follow once real output is seen).

## Architecture

Current `webset_discover.py` does everything in one file. Rather than grow it into a 600-line grab-bag, extract a shared core and keep thin entrypoints. This follows the repo rule: consolidate duplicated domain logic into one source of truth (cf. `tools/stage_vocab.py`).

### Modules

- **`tools/webset_core.py`** (new — extracted from `webset_discover.py`)
  The shared engine:
  - `.env` load + `Exa` client factory.
  - Entity-aware payload building (`company` | `person`): query, up to 5 criteria, entity type, enrichment list.
  - `normalize_item(item, entity)` — flattens a `WebsetItem` to a plain dict; company path reads `properties.company.*`, person path reads person properties (name, title/role, current company, location, plus enrichment results).
  - Enrichment resolution from a preset's `enrichments:` list (supports `description`, optional `format`, optional `options`).
  - Dedup helpers (`_norm_name`, `filter_known`, `load_known_names`) — moved verbatim.
  - `run_webset(client, payload, timeout)` — synchronous create → `wait_until_idle` → `items.list_all`, with partial-on-timeout preserved.
  - `create_async(client, payload)` — create only, return webset id, no wait.
  - `collect_by_id(client, webset_id)` — list items for an existing webset id, normalize.

- **`tools/webset_discover.py`** (refactor — calls core)
  On-demand entrypoint. New flags:
  - `--entity company|person` (default `company`).
  - `--async` — create the webset, print the id, exit without waiting.
  - `--collect <webset_id>` — fetch + normalize + score an already-created webset.
  - Company results scored by `company_scorer` (unchanged). Person results surfaced without heavy scoring (v1).

- **`tools/webset_monitor.py`** (new)
  Monitor lifecycle:
  - `create --preset <name> --cadence <weekly|daily|...>` — creates a persistent webset from the preset, attaches a Monitor (`CreateMonitorParameters`, `behavior=search` to find new items), stores `{preset, entity, webset_id, monitor_id, cadence, created}` in `tools/.webset_monitors.json`.
  - `list` — show monitored websets from state.
  - `delete <preset|monitor_id>` — delete the monitor (and optionally the webset), remove from state.

- **`tools/webset_collect.py`** (new — launchd-driven)
  For each entry in `.webset_monitors.json`:
  - List items added since a per-webset cursor (tracked in `.webset_monitors.json` as `last_seen` — item id or timestamp).
  - Normalize, dedup against known targets (`load_known_names`) **and** already-proposed items (cursor prevents re-proposing).
  - Route by entity:
    - `company` → score with `company_scorer` → pipeline-style proposal block in `data/inbox.md`.
    - `person` → contact-style proposal block in `data/inbox.md` (reuse `act_apply.py` contact-add path), surfacing role / company / stage / location / in-person signal.
  - Advance the cursor. Emit a JSON summary (counts per preset).

### Config — `data/discover-presets.yaml`

Extended per-preset schema:

```yaml
presets:
  lane-a:
    entity_type: company        # company | person
    query: "..."
    criteria: ["Bay Area", "Series A-C"]
    enrichments:                # NEW — configurable list
      - description: "Most recent funding stage/round"
      - description: "Is the company hiring for deployment/implementation/forward-deployed roles?"
    keywords: [...]
    count: 25
    monitor:                    # NEW — optional
      cadence: weekly
  deployment-leads:             # NEW example — person preset
    entity_type: person
    query: "deployment lead / forward-deployed engineer / implementation lead"
    criteria: ["Series A company", "works in person", "San Francisco"]
    enrichments:
      - description: "Current job title"
      - description: "Current company and its funding stage"
      - description: "Whether the role is in-person in San Francisco"
    count: 15
```

### State files

- `tools/.webset_monitors.json` — one record per monitored webset: preset, entity, webset_id, monitor_id, cadence, created, `last_seen` cursor. Gitignored (contains account-specific ids).

### Scheduling

- `tools/launchd/com.nickmagnuson.jobsearch.webset-monitor-collect.plist` — weekly, mirrors the `career-scan` pattern. Runs `webset_collect.py`. Logs to `tools/launchd/logs/`. Installed via `tools/launchd/install.sh`.

## Data flow

**On-demand (company or person):**
`webset_discover.py --preset X [--entity …] [--async]`
→ core builds payload → create → (sync: wait+list) or (async: print id; later `--collect id`)
→ normalize → (company: score) → JSON to stdout → `/discover-companies` presents.

**Continuous (monitored):**
`webset_monitor.py create --preset X --cadence weekly` (once)
→ Exa re-runs server-side on cadence, accumulates new items
→ weekly launchd → `webset_collect.py` → new items since cursor → score/dedup/route → `data/inbox.md`
→ Nick reviews inbox; `/act` or `/networking` routes accepted items. Nick never waits.

## Error handling

- Missing `EXA_API_KEY` → clean JSON error, exit 1 (existing pattern).
- SDK/network error → caught, `{"error": "...", "candidates": []}`, exit 1 (existing pattern).
- **Entitlement/auth failure on live call** (the new risk) → surfaced explicitly by the Phase 0 smoke test; build halts until resolved.
- Monitor create failure → not written to state (no orphan cursor).
- Collector: a single webset failing is logged and skipped; other monitored websets still process (graceful degradation).
- Timeout on sync run → partial results returned with `partial=True` (existing behavior, preserved in core).

## Cost

Monitors bill server-side per run × items × enrichments. Because access is new, conservative defaults:

- **Weekly** cadence (not daily).
- **Small counts** (~25 company, ~15 person).
- **2–3 enrichments** max per preset.

All tunable per preset. `webset_monitor.py list` shows active monitors so cost surface stays visible; `delete` stops billing.

## Testing & verification

Per the repo rule "data tools are verified on REAL data, not fixtures":

- **Phase 0 (gate):** live smoke test — `count=3` company webset on `lane-a` and one person webset on the deployment-lead query. Confirms entitlement + both entity paths return items. **If this fails, stop and fix access before writing extension code.**
- Fixture/unit tests for the deterministic pieces: `normalize_item` (company + person), enrichment resolution, dedup, cursor advancement in the collector, monitor-state read/write.
- A live end-to-end run of the collector against a real monitored webset before declaring done (not just green fixtures).
- Cross-path check: on-demand `--collect <id>` and the collector should normalize the same webset identically (shared core → one source of truth).

## Implementation phases

Staged so each slice is independently verifiable:

- **Phase 0** — live access smoke test (company + person). Gate.
- **Phase 1** — extract `webset_core.py`; refactor `webset_discover.py` onto it; add configurable enrichments + `--async`/`--collect`. Verify on-demand still works live.
- **Phase 2** — `webset_monitor.py` + `webset_collect.py` + launchd plist. Verify continuous drip to inbox live.
- **Phase 3** — person entity: preset, normalize path, contact-style inbox routing. Verify the deployment-lead query live.

## Open questions / deferred

- People-candidate ranking (deferred to a fast-follow once real output is reviewed).
- Replacing/merging `contact_finder.py` (out of scope; revisit if the person-webset path proves it can subsume per-company lookup).
