# Top-of-Funnel Wiring — Design Spec (rev 4)

**Date:** 2026-08-10
**Status:** Revised after third adversarial panel review
**Goal:** Close the gap between a working discovery layer and near-zero cold outreach volume.

---

## Problem

Every component of the acquisition funnel is built. None of them are connected.

**Producing:**
- `agent_collect.py` runs weekly via launchd, ~$0.05/run, seen-set holds 12 companies + 13 people.
- The career scanner runs daily; wrote 91 role blocks to `data/inbox.md` on 2026-08-10 alone.

**Consuming:** nothing.

Three breaks:

1. **`data/inbox.md` has no drain.** Fed by `/remember`, `agent_collect.py`, and the career scanner. No skill reads it. `/act` consumes the `inbox/` *directory* via `act_classify.py`; it never opens the `.md` file. Drip blocks carry `<!-- review-gated: accept via /act or /networking -->`, pointing at a workflow that does not exist. 4,765 lines, append-only.
2. **Nothing writes to `data/scan-targets.yaml`.** `agent_collect.py:79` reads it only as a dedup exclusion list; `/scan-companies` says edit it by hand.
3. **`/outreach-batch` has never run.** Written 2026-07-28, uncommitted. `output/outreach-queue/` does not exist on disk.

**Result.** `data/outreach-log.md`: 106 logged touches to 59 distinct people against a 99-row roster.

| Type | Count |
|---|---|
| `follow-up` | 77 |
| `draft-email` | 14 |
| **`cold-outreach`** | **8** |
| other | 7 |

**The nurture engine works. The acquisition engine does not.** Cold first-touch is 8 lifetime. This spec targets the top of the funnel only.

### The meta-lesson

Each break has the same shape: **a producer with no named drain.** Any component added here must name its consumer, or it becomes the next `data/inbox.md`.

**Corollary added in rev 4:** a *guard* with no failing mode is the same bug wearing a safety vest. Any control that can degrade to a silent no-op (empty denylist, zero-row parse, vacuous assertion) must be given a floor and made to fail loudly below it. See G8.

---

## Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Extend `/act` to drain `data/inbox.md` | One consumer for both inboxes; reuses classify → preview → approve → route. |
| D2 | `/act` claims only machine-marked blocks | Human `/remember` captures carry no marker, so automation structurally cannot route the user's own writing. |
| D3 | Weekly launchd drafter, pre-drafted queue, **review-capacity backpressure** | Queue is never auto-sent and never grows past what review can absorb (ADR-6). |
| D4 | Drafter draws only from an approved pool | User approves a company in ~30s via `/act` before research budget is spent. |
| D5 | `format` is a **logged covariate, not an experiment** | ADR-2. Also confounded with `hook_type` by construction. |
| D6 | Migrate to JSONL before building the funnel | ADR-1. Avoids writing the drafter twice. |
| D7 | **Cold-outreach target is a measured ramp, not a fixed 5/day** | ADR-3. |
| D8 | Lane B weighted for cold outreach | Lane A arrives via recruiters; Lane B produces zero conversations without deliberate outbound. |
| D9 | **Unattended drafting is restricted to affiliation-anchored contacts** | ADR-4. Machine-verifiable hooks only. |
| D10 | **Follow-up ladder stays long, but cold follow-ups are batched and cheapened** | ADR-5. |
| D11 | **No relationship is inferred at migration.** `pipeline_id` is minted forward-only, never backfilled | ADR-1 §"Identity". The only available backfill key is the exact matcher that caused the 18-withdrawal incident. |
| D12 | **Prose columns stay free text.** `stage` and `status` get an *advisory* classifier, never an enum validator | ADR-1 §"Validation". Verified live: 31 distinct Stage values, most of them sentences. An enum would force a normalization pass that rewrites closed-ness. |
| D13 | **Every repaired row records its evidence basis** (`history` vs `user_judgment`) | ADR-1 §"Repair". Ground truth for the pre-reflow rows exists in the private backup repo; recall is not the adjudication input. |

---

## ADR-1: JSONL over markdown tables — and how to migrate without repeating 2026-06-08

**Context.** Four data files are parsed positionally (`cols[0]`..`cols[N]`). Verified this session: **39 `split("|")` sites across 25 files** — not "24 call sites." Row counts: `job-todos.md` 926 table lines, `outreach-log.md` 108, `networking.md` 99, `job-pipeline.md` 112.

**The readability argument is empirically void for these files.** `.obsidian/app.json` `userIgnoreFilters` already excludes pipeline, todos, todos-daily-log, networking, outreach-log, outreach-tracker, scan-targets, inbox, weekly-review-log. They render in no vault.

**Three data-integrity incidents:**

| Date | Incident | Cost |
|---|---|---|
| 2026-06-08 | Staleness parser read the wrong column index after the live header drifted. `days_since_update` returned null for every row for an unknown duration. **Unit tests stayed green because fixtures encoded the same stale header.** | Produced `schema_guard.py` |
| 2026-07-08 | Writer embedded a literal `\|` inside a Notes cell, drifting the Completed table's column count. **A repair script (`migrate_todos_column_drift.py`) already ran a heuristic reflow over it.** | One-time repair migration |
| 2026-08-10 | Corrupt rows found across files. | Repaired by this spec |

**Decision.** JSONL as source of truth — one record per line. Appends stay atomic and single-line; diffs stay one-row-per-line. No generated markdown view for daily use (Obsidian-excluded), but `jsonl_to_markdown` is built and kept permanently as a verification and re-derivation tool.

### The failure mode this migration must actually defeat

06-08 was **recoverable** — ground truth stayed in the `.md`. Once JSONL is source of truth, a wrong header→field mapping is written as an authoritative field *name*, and every `table_io` guarantee is satisfied by a perfectly-formed JSONL whose `notes` field holds a URL. Reader and file agree on the wrong thing. **Most obvious gates are blind to it:**

- A round-trip through a shared registry is a permutation composed with its inverse — identity. Blind.
- Golden-output parity is invariant to any *consistent* mislabel. Blind.
- Row counts are blind.
- A field-set / unknown-field check is blind (the names are all present; only their contents are wrong).

Worse, the four files' most load-bearing columns are **mutually indistinguishable free text**: pipeline `Next Action` / `Notes` / `Role`, todos `Task` / `Notes`, outreach `Subject / Summary`. A swap of `next_action` and `notes` would feed a stale next-action string to the drafter as company context and put it in a cold email to a stranger.

**And the registry itself is circular.** G1 asserts every live header column maps to exactly one registry entry — but the registry's mapping was authored by a human reading that same header line. G1 verifies transcription completeness, never correctness. That is 06-08's tautology restated. **The only way out is an oracle external to the file being migrated: G7.**

**Eight mandatory migration gates.** Phase 1 does not exit until all eight are green.

---

**G1 — Schema registry, checked in first.** `tools/schemas/{pipeline,todos,networking,outreach_log}.json`. One entry per column: exact header text → canonical snake_case field → type → enum/regex/`free_text`. The converter reads *only* from the registry; it never infers a name from header text. Two tests: (a) every column of every live `.md` header maps to exactly one registry entry; (b) AST scan — no ported tool subscripts a `table_io` result with a key absent from the registry.

**Stated limitation, in code and here: G1 is a completeness check, not a correctness check, and is circular with respect to the header line.** Correctness evidence is G5, G7, and G5b.

**G2 — Round-trip cell conservation.** `jsonl_to_markdown(convert(md))` reproduces the original `.md` **cell-for-cell** (whitespace normalized). **G2 cannot detect mislabeling** — converter and renderer share the registry, so any bijective name permutation round-trips clean. G2 catches dropped, truncated, mangled, and split cells, and is the gate that catches the variable-arity Notes hazard below.

**G3 — Golden parity on live data, against a PRE-DECLARED exception list.** *Before* touching anything, run every read/report tool against the LIVE files and freeze stdout as `tests/golden/pre-migration/*.json`. Phase 0a's integrity scan enumerates every non-conformant row; for each, the expected post-migration delta is written down **before goldens are captured** and checked in as `tests/golden/expected-diffs.json`. G3 asserts: post-migration diff set **== declared exception set, exactly.** An undeclared diff fails; a declared diff that fails to appear also fails.

**Stated limitation: G3 detects porting infidelity only.** It is invariant to consistent mislabeling.

G3's tool set is **not a hand-written list** — it is every tool marked `IN_SCOPE` in the inventory (below). Explicit additional assertions:

- `pipe_read.py` returns identical `active_entries`, `archived_count`, per-stage counts.
- **Every counting tool's integer outputs are identical** (`todo_daily_metrics.py`, `todos_summary.py`, `outreach_pending.py`, `dashboard/pipeline_data.py`). This is the G3 clause that catches the RecordSet hazard below.
- **`stage_vocab.is_terminal_stage(stage)` is frozen per pipeline row** pre-migration and asserted bit-identical post-migration. Any stage normalization that flips closed-ness fails loudly here.
- Mutating paths `todo_write.py {supersede,clear,withdraw,sync}` run against a scratch copy pre- and post-migration.
- **Every golden carries a non-emptiness assertion.** A tool that finds zero `|` lines and returns `[]` must fail, not pass.

**G4 — Row AND cell conservation, per table AND per section.**
- `len(jsonl_records) == count_of_data_rows_in_md`, asserted **per table**.
- **Per-section row counts** asserted separately: pipeline active vs archived, todos active vs completed. (Section is unrecoverable from any column; it needs its own count.)
- `sum(len(cells)) per table == sum(non-null fields + rejoined-notes segments)`.
- No source row containing a literal `|` inside a cell converts without that content appearing verbatim in some field's value.

*Why the cell-level half exists.* `todo_write.py` treats Notes as variable-arity: lines 281/334/446/541 use `" | ".join(cols[4:])` while `cmd_supersede:407` uses bare `cols[4]` — **a live intra-tool disagreement, verified.** Resolution: the registry declares `notes` a single free-text field, the converter rejoins `cols[4:]` with `' | '`, and **`cmd_supersede`'s `cols[4]` is fixed in the same commit.**

**G5 — Per-row tuple fingerprint (registry-independent).** *Revised in rev 4: the rev-3 per-column multiset was blind to two swaps live in this data.* Multiset equality is invariant to a swap between columns whose value multisets coincide (todos Due/Status, pipeline `Next Action`/`CV Used` — both dominated by `—`/`none`/`-`), and it is **completely invariant to section mislabeling**, because `## Active Pipeline` (line 6) and `## Archived` (line 114) carry *identical 8-column headers*.

Replacement: for each source table, a throwaway parser written **from the header line alone** hashes the ordered tuple of each row's cells. Assert the multiset of row-hashes equals the multiset of hashes recomputed from the JSONL records **restricted to that (table, section)**, in registry field order. A row-tuple hash is not invariant to a column swap even when column multisets coincide, and per-section computation makes section assignment a first-class part of the assertion.

**G5b — Human sample confirmation, non-skippable and stratified.** *Revised in rev 4: 10 uniform samples out of ~450 rows detects a single-table swap at roughly coin-flip odds and an Archived-only swap essentially never — and printing JSONL fields alone asks the user to judge prose rather than a mapping.*

- Each sampled record prints its **`_raw` source line immediately above** the `field: value` block.
- Sampling is **stratified, with mandatory inclusions**: (a) 3 rows per table where two or more free-text fields are simultaneously non-empty and >40 chars; (b) **every** row containing a literal `|` inside a cell; (c) 2 rows from each section/table, including Archived and Completed; (d) the first and last data row of every table.
- Capped at ~25 records total (~10 minutes).
- Confirmation is **per-FIELD, not per-record**: the user names which fields they actually checked. An unchecked field is recorded as unchecked, not as passed.

**G6 — Cross-tool parity.** Verified live: `networking_read.py:145-161` tracks `in_archived` from headings and skips archived rows, while `outreach_pending.py:122-140` `parse_pipeline_stages` iterates **every line** of `job-pipeline.md` with no heading awareness and is last-wins on duplicates — so an Archived row silently overrides its Active row when deciding closed-ness. Same file, same question, two answers, in production right now.

G6 asserts `networking_read`, `outreach_pending`, `pipe_read`, and `dashboard/pipeline_data` return the **identical set of open/active companies**. Run **pre-migration** to document the divergence, resolve with the user (near-certainly: section-aware, Archived excluded), then consolidate section/terminal logic into `stage_vocab` and have all four import it.

**G7 — Cross-file semantic oracle (NEW in rev 4; the only gate whose evidence lives outside the migrated file).** Run over 100% of records, deterministic, header-independent:

| Field | External oracle |
|---|---|
| `outreach_log.recipient` | appears in the networking Contacts name set |
| `outreach_log.company`, `pipeline.company` | appears in pipeline companies ∪ scan-targets |
| `pipeline.cv_used` | dash/empty, or a filename existing under `output/**` |
| `pipeline.url` | `^https?://` or `^\[.*\]\(https?://` or dash |
| `pipeline.date_updated`, `todos.due` | full-match ISO-8601 |
| networking contact fields | same, per registry type |

**Thresholds are measured, not asserted.** A hardcoded "≥95%" is another arbitrary constant that either trips on legitimate noise or passes vacuously. Instead: Phase 0c computes each field's hit **rate** against the live pre-migration markdown and freezes it as the baseline; post-migration G7 asserts no field's rate falls more than 2 percentage points below its own baseline. A whole-column swap drops the rate toward zero and fails regardless of what the baseline turned out to be.

This pins six of pipeline's eight columns to external reality. Only `role` / `next_action` / `notes` remain genuinely unpinnable — a tractable human-review surface for G5b instead of an open-ended one.

**G8 — Safety-control non-degradation (NEW in rev 4; blocking).** *The migration's worst failure is not wrong data — it is a safety control that silently becomes a no-op with a green build.*

Verified live: `gen_pii_denylist.py:parse_networking_names` (line ~94) and `:parse_pipeline_companies` (line ~110) both gate on `if line.startswith("|")` over `data/networking.md` and `data/job-pipeline.md`. Post-cutover no line starts with `|`, both return empty sets, and the regenerated `tools/.pii-denylist.txt` is empty. `check_public_pii.py` is an always-on PreToolUse hook (`.claude/settings.json:34`) that BLOCKs on denylist tokens. An empty denylist does not error, does not raise, and fails no gate in rev 3 — it just stops blocking, on a **PUBLIC repo**, against a CLAUDE.md hard constraint. It is not a read/report tool anyone would think to golden and it does not use `table_io`, so the "raises on non-JSON" backstop never fires.

G8 requires, in **Phase 1 step 7** (not Phase 4), and as a cutover-window exit condition:

1. `gen_pii_denylist.py` ported to `table_io`, added to the G3 golden set with **floor assertions**: `len(networking_names)` and `len(pipeline_companies)` at or above floors derived from the live files in Phase 0c (measured baseline: 99 networking table rows, 112 pipeline table rows).
2. `gen_pii_denylist.py` **exits non-zero** when either parse falls below its floor. Silence is not success.
3. `check_public_pii.py` **BLOCKs** (does not pass) when `.pii-denylist.txt` is shorter than its floor or older than the mtime of any source JSONL. A stale or empty denylist is loud.
4. The same audit is applied to every other control that pattern-matches these files as markdown: `check_edit_safety.py`, `check_edit_after_mutation.py`, `check_living_log_purity.py`, `check_script_error_logged.py`. Each gets an explicit post-cutover behavior decision (port, retarget, or retire) recorded in the inventory — no hook may be left matching a format that no longer exists.

---

### Scope inventory replaces the call-site count

Verified: 39 `split("|")` sites across 25 files. A per-file checklist is wrong in both directions.

- **Multi-target parsers** (one file, two in-scope data files — checking the file off after one site loses the other): `scorer_eval.py:150` pipeline + `:271` outreach-log; `gen_pii_denylist.py:94` networking + `:110` pipeline; `remember_classify.py:149/:172` networking + pipeline; `act_classify.py:220/:300` including job-todos.
- **Same-signature, out-of-scope parsers** the sweep will visit and must not break: `personal_todo_write.py` (`data/personal-todos.md`, **same schema as job-todos** — a shared `table_io` port would point it at the wrong registry), `friction_log.py`, `dossier_freshness.py`, `check_script_error_logged.py` (a hook), `migrate_todos_column_drift.py` (dead one-shot), `agent_core.py`, `webset_discover.py`.

**Therefore `audit_rule_violations.py` on `split("|")` is not the scope oracle.** Phase 0 generates `tests/migration-inventory.json`: one row per `(file, line, resolved target data file, IN_SCOPE|OUT_OF_SCOPE, reason)`, produced mechanically by resolving the path each site reads. A test regenerates it and **fails if any site is unclassified**. That artifact is what makes Phase 1 step 7 completable.

- Every `IN_SCOPE` tool needs either a G3 golden with a non-empty assertion, or a written waiver checked in beside the inventory.
- Every `OUT_OF_SCOPE` tool gets a regression test asserting it *still parses markdown correctly* after the sweep.
- `migrate_todos_column_drift.py` is deleted (dead one-shot; its history is preserved in git and cited in the repair record below).

### Validation is value-driven — but prose columns are free text, not enums

Flagging on cell count alone misses this spec's own centerpiece: `job-todos.md:459` is `| --help | Med | 2026-04-30 | Completed 2026-04-30 |` and `:463` is `| --task | Med | Completed 2026-04-30 | 2026-04-30 — blocking |`. Both are 4 cells against a 4-column header — perfectly conformant, semantically swapped.

**But rev 3's proposed rules were themselves wrong against this corpus.** Verified by `awk` over the live `Stage` column: **31 distinct values**, most free prose — *"Closed - rejected (7/14): HM passed after the 7/7 screen; nearing end of process with another candidate (relayed by the recruiter)"*, *"Founder screen booked (Casey Doe, Mon 8/10 3:00 PM PT)"*, *"Applied 2026-07-08 via Jordan Sample (recruiter platform) - CV + fit profile emailed..."*. The file's doc header claims a 7-value ladder; the live column obeys none of it, and `stage_vocab.py` classifies by regex keyword search *precisely because there is no enum*. A closed-enum rule flags a large fraction of ~110 rows; and since Phase 2 is gated on `withheld == 0`, the schedule-pressure fix would be to normalize Stage into a real enum — which silently rewrites the input to `is_terminal_stage`, flips rows between active and closed, and surfaces as **a cold email to a company that already rejected him.** Rule #3 has the same problem: `Notes`, `Next Action`, and `Stage` routinely embed ISO dates adjacent to a real `Date Updated` column.

**Revised rules. `_repair_needed` fires when ANY of:**

1. Cell count mismatches the enclosing table's header.
2. A **registry-typed** field fails its regex/enum — where typed means ISO-8601 dates, slug regex, booleans on `outreach`/`active`, and the closed enums `lane` / `format` / `section` / `table` **only**. `stage` and `status` are declared **free text**; `stage_vocab` classifies them **advisorily** and can never set `_repair_needed`.
3. **Swap signature, narrowed:** a typed field's value **full-matches** (anchored, e.g. `^\d{4}-\d{2}-\d{2}$` — not a substring search) another field's type in the same record. **Excluded whenever both fields are registry-declared free text**, because there prose and swap are indistinguishable by construction.

**Rule calibration is an output of Phase 0a, not an assumption.** Phase 0a dry-runs each rule over the live files and prints the flagged-row count **per rule per table**. **If any rule flags more than 5% of a table, the rule is wrong and gets narrowed — the data does not get normalized to satisfy it.** The rev-2 budget of "a handful of rows, a two-minute conversation" is withdrawn.

`schema_guard.py` is retired in the same commit — grep confirms exactly two importers; markdown header-drift detection is meaningless with no markdown headers. Update `pipeline_staleness.py`, `todo_daily_metrics.py`, `test_schema_guard.py`, and the citing memory entry.

### Repair: adjudicate against history, never against recall

Two things rev 3 did not know.

1. **`tools/migrate_todos_column_drift.py` already ran a repair** over the job-todos Completed table. Its `reflow()` heuristic takes `cols[0]`/`cols[1]` as task/priority, then picks the completed-date cell by "prefer one starting with Completed/Withdrawn; else the first bare `YYYY-MM-DD`." The ~13 arity-conformant swapped rows are exactly the shape that heuristic produces when it guesses wrong. So "adjudicate every `_repair_needed` row with the user" is adjudicating **a machine guess, from memory, months later**, with no `_raw` preserved from the first migration — and the user's pick then gets frozen into an authoritative field name. 06-08's mechanism with a human rubber stamp attached.
2. **Ground truth is recoverable.** `data/` is gitignored in the public repo but `tools/backup-data.sh` commits it nightly to the overlay `GIT_DIR` at `$PRIVATE_GIT_DIR` (private repo `the private backup repo`) — **verified present this session.** The pre-reflow bytes of `data/job-todos.md` are in that history.

**Therefore, before any adjudication (Phase 0a):**

```
git --git-dir=$PRIVATE_GIT_DIR --work-tree=. log --oneline -- \
  data/job-todos.md data/job-pipeline.md data/outreach-log.md data/networking.md
```

Recover the pre-`migrate_todos_column_drift` version of each flagged row. For each `_repair_needed` row, present **the historical original alongside the current corrupt form**; the user adjudicates against evidence, not recall.

- Record the recovered original in `_raw_original` with its commit sha.
- Record `_repair_basis: "history" | "user_judgment"` on **every** repaired record.
- A `_repair_basis: user_judgment` row is never silently indistinguishable from clean data: `table_io` exposes it, and `/weekly-review` prints the standing count.

### Ordering: migrate first, adjudicate second, never silently

`data/outreach-log.md` has 108 table lines at a 7-column header, one 6-cell row and one 8-cell row. The 6-cell row (line 75) is genuinely ambiguous — Company-omitted and Status-omitted are both consistent, and they assign different `Status` to a real cold touch, one of only **8 lifetime**. Repairing before migrating commits a guess with no marker.

1. Converter emits flagged rows with `_raw`, `_raw_original` (+ sha, where history has it), `_repair_needed: true`, `_ambiguity`.
2. All flagged rows surfaced for explicit adjudication.
3. `table_io` refuses to serve `_repair_needed` records to the drafter, `/weekly-review`, or any metric — and reports the count.

### Withheld records are counted out loud, and adjudication cannot be deferred

Silent withholding on a corpus this small is a measurement error large enough to move a decision: dropping line 75 takes cold sends from 8 to 7 invisibly, and ADR-3's ramp is calibrated against those counts.

- Every consumer renders it: `106 rows read, 2 withheld pending adjudication`.
- **Phase 2 is blocked on `withheld == 0` across all four files** — with the rule-calibration escape valve above, so the gate can never be satisfied by normalizing prose.

### `table_io.read()` returns a RecordSet, not a tuple

Rev 3 specified `read() -> (records, withheld_ids)`. That is the exact failure the user's own global rule names: **changing a return type to a tuple silently breaks every caller.** Twenty-plus ported sites do `rows = parse(...)` then `len(rows)`, `if rows:`, `for r in rows`. `len(table_io.read(path))` would return **2** — no exception, a plausible small integer — and it would land in precisely the counting tools (`todo_daily_metrics.py` has six sites, `todos_summary`, `pipe_read.archived_count`, `outreach_pending`) whose numbers calibrate the ADR-3 ramp and the "8 cold sends lifetime" baseline. `if rows:` would be True on an empty file.

**Fix (structural, not grep-based):** `read()` returns a `RecordSet` — a `collections.abc.Sequence` subclass over the *served* records, carrying `.withheld` (list) and `.withheld_ids`. `len()`, truthiness, iteration, and indexing keep their prior meanings; tuple-unpacking (`a, b = read(...)`) raises with a message pointing at `.withheld`. Two tests: `len(RecordSet)` can never equal the tuple arity by construction, and the G3 counting-tool integer assertions above.

### Section membership is structural state, not a column

`pipe_read.py:82-84` derives `in_archived` from `## Archived` / `## Withdrawn` / `## Rejected` headings; line 118 counts archived as `is_terminal_stage(stage) OR in_archived`. That second disjunct is unrecoverable from any column. Verified: `job-pipeline.md` has `## Active Pipeline` (line 6) and `## Archived` (line 114) with **identical 8-column headers**; `job-todos.md` Active is 5 columns (line 9), Completed is 4 (line 250).

**Every record carries an explicit section field** from the enclosing heading: pipeline `section: "active" | "archived"`; todos `table: "active" | "completed"`. G4 asserts per-section counts; G5 hashes per section.

`cmd_sync` currently scopes its terminal scan with a `## Archived` regex. Lose the heading and its blast radius widens across the whole file — which is why the FK rewrite and the section field must both land before the sync ever runs against a sectionless file.

### Identity: `pipeline_id` is minted forward-only and never backfilled

Verified: `todo_write.py cmd_sync:520-528` builds `full_text = " ".join(cols).lower()` and withdraws any to-do where a terminal company name appears **anywhere in the concatenation of every column, including Notes** — which is why 3 unrelated to-dos were swept alongside the real 15.

That matcher is the **only** candidate key for backfilling `pipeline_id`. Backfilling would write the incident's false positives into the FK as observed fact, after which the bug is unfalsifiable. And a synthetic test that "seeds 3 to-dos with no `pipeline_id`" passes trivially while asserting null FKs on exactly the rows the converter would have populated — 06-08 verbatim.

- Converter mints `id` on every record (`<slug>-YYYYMMDD-nn`; ULID rejected as an unnecessary dependency).
- Converter emits **`pipeline_id: null` on every migrated to-do.** No inference.
- `pipeline_id` is populated only at creation time going forward.
- `cmd_sync` is rewritten to **withdraw only where `pipeline_id` is non-null**, never on name. On legacy rows it correctly withdraws nothing — the safe direction.
- **Verification that bites (Phase 0d, live data):** run the current name-matcher and the new FK-matcher over the real `job-todos.md` + `job-pipeline.md`, diff the withdrawal SETS, print the symmetric difference row by row for user adjudication. A synthetic fixture is not a substitute.

### Rollback is re-derivation, not reversion

Mislabeling surfaces late; reverting on day 25 discards 25 days of pipeline updates, outreach rows, granola-driven networking logs, and career-scan output. Nobody would pull that trigger, so the snapshot would be decoration.

- `jsonl_to_markdown` is a permanent, tested tool.
- For 30 days post-cutover a weekly launchd job re-renders JSONL to markdown, re-runs G1/G4/G5/G6/G7/G8 and the validator, and writes to the automation-health log.
- A discovered mislabel is fixed by **editing the registry and remapping the field in place** — no data loss. `data/_pre-jsonl-snapshot/` (gitignored) is the reference for verifying that remap, kept until the 30-day cadence completes clean.

### Scope boundary — `networking.md` is two documents

Live: 2,552 lines. Lines 10-111 are the `## Contacts` table (7 cols). Line 112 onward is `## Interaction Log` — ~30 `### Name — Company` sections of freeform prose with full message bodies, parsed by a separate loop at `networking_read.py:154`.

**Migration covers the Contacts TABLE only.** Split: `data/networking.jsonl` (roster) + `data/networking-log.md` (prose, byte-identical). Update `networking_read.py`'s two loops and `networking_write.py log`. Assertion: prose region byte count unchanged. Note for G8: `gen_pii_denylist.py` also harvests names from the `### Name — Company` headers via regex — that path survives the split and must be repointed at `networking-log.md`.

### The separator regex, and the rows it hides

`todo_write.py:499` skips lines matching `^\|\s*:?-+`, which also matches `| --help` and `| --task` — invisible to the current writer. A converter reusing it silently drops them; one that does not imports garbage. G4 exists for this. The regex is tightened to require ≥3 dashes and no alphabetic characters.

### The consumer set is larger than the python call sites

~12 `SKILL.md` files instruct the model in markdown-row shapes, plus the hooks named in G8. Post-cutover, a model following an un-swept SKILL.md appends a `| ... |` line into a `.jsonl` — CLAUDE.md's named *forward-guard, no backward-sweep* anti-pattern.

Phase 1 runs `PYTHONIOENCODING=utf-8 python3 tools/audit_rule_violations.py` over `tools/`, `.claude/skills/`, `.claude/settings.json` for the greppable signatures, sweeping every hit — **cross-checked against the inventory**, which is the authority on what is in scope. `table_io.read` raises loudly on any non-JSON line.

### Cutover is a single quiesced window, not a week

Eight launchd jobs write during Phase 1.

1. Build `table_io`, registry, converter, goldens, and all ports **against a copy**, over as many days as needed. Nothing in `data/` changes.
2. Cutover in **one session, under an hour**: `bash tools/launchd/install.sh uninstall` → verify `launchctl list | grep jobsearch` empty → write `data/.migration-lock` → run converter → run G1-G8 + G5b → land ports → regenerate the PII denylist and confirm it clears its floor → smoke `/standup` → unlock → reinstall launchd.
3. No manual `/cold-outreach` and no data mutation during the window.
4. Every writer tool refuses to run while `.migration-lock` exists.
5. Post-cutover, diff the frozen snapshot against a fresh `jsonl_to_markdown` render to confirm no writes were stranded.

---

## ADR-2: the format comparison is a logged covariate, not an experiment

Rev 1 specified stratified randomization of two draft formats. That does not survive the numbers or the confounds.

**Format is confounded with hook strength by construction.** ADR-4 permits both formats only when there is a cited affiliation overlap; a research-hook contact gets paragraph only. So **every `bullet` record comes from the strongest-tie stratum** while `paragraph` mixes strong and weak ties. Draft quality also varies by whoever edits the queue. And ADR-3's honest forecast does not deliver N≈20/arm in two weeks.

**We are not un-confounding this by loosening ADR-4.** Permitting bullet format on research-hook contacts trades real send quality for the internal validity of a comparison we have already declined to run.

**Revised:**
- `outreach-log.jsonl` keeps `format` (`bullet` | `paragraph` | `null`), `hiring_status`, `warm_tie`, and **`hook_type` (`affiliation` | `research` | `manual`) as a REQUIRED field.**
- Drop the stratification machinery and the randomizer. The drafter alternates format deterministically by contact index within the affiliation stratum.
- `/weekly-review` reports replies **grouped by `hook_type` first, then format**, labeled: *descriptive counts, not a comparison; format is confounded with hook type by design.*
- No rate is reported below N ≥ 50 per format, and even then it is read for reply *character*, not rate.

---

## ADR-3: 5/day is a ceiling, not a commitment — the ramp is gated on measured pool depth

1. Discovery yields 3-5 new companies per run, **weekly**, and that rate *decays* as the seen-set exhausts a finite SF-anchored market. 12 companies to date.
2. Lane B companies are small. A 30-person startup structurally does not have 6 people worth cold-emailing. The 6-contacts/company assumption is the least defensible number in the spec.
3. Baseline is 8 cold sends **lifetime**. A 13x step change in week one is a wish.

**Measure before committing:**

- **Phase 2 exit gate (pool-depth probe).** Run `contact_finder.py` against the 12 already-discovered Lane B companies with `--num 8` and count contacts clearing the evidence-span employment gate. Yields the real contacts-per-company number `k` in one afternoon, before any drafter is built.
- Sustainable weekly volume = `k × (new companies/week) + backlog drawdown`. If `k` is 2-3, the honest steady state is **8-15 cold sends/week (~1.5-3/day)**, not 25.
- **Ramp:** week 1 target 5/week, week 2 10/week, then hold at what measured `k` supports.
- **If measured volume falls short, the lever is more presets and more sectors, never more contacts per company.**
- **Decay tripwire:** if new-companies-per-run falls below 2 for three consecutive runs, the ramp step is **held flat** while presets are expanded.
- `/standup` reports actual sends against the *current ramp step*, never against 5/day.

**Geography.** Queries stay SF-anchored; the geo gate penalizes and flags rather than hard-dropping Bay Area results. Bay Area is acceptable for Lane B (confirmed 2026-08-10) because pool depth is the binding constraint.

---

## ADR-4: what an unattended job is allowed to draft

An unattended weekly job cannot exercise taste. It can check facts.

| Hook available | Drafter output | `hook_type` |
|---|---|---|
| **Affiliation overlap** (shared school with class year, or shared past employer), with a cited evidence span | Full draft, both formats permitted | `affiliation` |
| Citable company-specific research hook (funding round, product launch, public post) with a quoted span | Full draft, **paragraph format only** | `research` |
| Neither | `NEEDS-RESEARCH` stub: company, contact, evidence gap. **No body text.** | — |

**Never drafts on a headline alone.** A stub that costs 30 seconds to reject beats a plausible-sounding email that costs a relationship.

**Affiliation layer.** `data/affiliations.jsonl` (gitignored) declares the user's own institutions. Used three ways: input to `warm_tie` ranking, preferred drafting hook, discovery preset.

**Overlap must be cited, not asserted.** The drafter records the evidence span proving the affiliation, under the same quote-or-drop rule as the employment gate. A drafted email whose affiliation claim has no span is auto-downgraded to `NEEDS-RESEARCH`.

**Drafter reads only clean records.** It refuses any record with `_repair_needed` **or** `_repair_basis: user_judgment` on a field it uses as email context — a guessed repair is not company intel.

**Quality tripwire.** For the first four weeks every queue file records `approved_as_is` / `edited` / `discarded` on review. If `discarded + heavily-edited` exceeds 50% over two consecutive weeks, the drafter is disabled and diagnosed before re-enabling.

---

## ADR-5: follow-up ladder economics at cold volume

`/cold-outreach` auto-creates a dated follow-up to-do per send, and the guide prescribes a 5-touch ladder (+2-3d, +5-8d, +10-15d, +20-28d, +35d). At 25 sends/week that is ~125 touches/week and ~25 new dated to-dos/week. **The warm follow-up queue is already 27 days overdue.**

- **Keep the long ladder.** Touches 4 and 5 are not cut.
- **Late touches are cheap.** Touches 3-5 are 2-sentence bumps, explicitly templated as such.
- **Soft-out at touch #3** ("if this isn't the right time, no need to reply — I'll stop here").
- **Cold follow-ups create no per-send dated to-dos.** One weekly sweep to-do ("cold follow-up sweep — N due"); the drafter emits due bumps into the same `output/outreach-queue/` file. Per-contact state lives in the outreach log.
- **Warm ranks above cold in the morning brief, always.**

**The 14-day no-reply flag is a MEASUREMENT state only. It is never a stop-sending signal.** A real thread in this corpus replied on touch #4 after ~3 months of silence; the silence was a paused search, not disinterest. The timeout exists so `Sent` stops being a permanent unknown (7 of 8 cold sends sit there today) and so metrics have a denominator. The state is named `no-reply-14d`, not `dead`/`closed`/`lost`, and no code path may branch on it to suppress a scheduled touch. A test asserts the follow-up scheduler's output is identical with and without the flag set.

---

## ADR-6: review capacity is the real cap on queue depth

Backpressure keyed to "2× weekly target" is circular — it sizes the queue against the number the queue is supposed to test. The binding constraint is human review minutes.

- **Budget:** ~4 minutes per full draft (read, verify the cited span, edit, send), ~30 seconds per `NEEDS-RESEARCH` stub.
- **One standing 45-minute weekly review appointment.** ~10 full drafts plus stubs — which brackets the ADR-3 measured range and independently confirms the ramp is capacity-feasible.
- **Drafter cap = min(current ramp step, review-minute budget ÷ 4 min).**
- **Backpressure:** if unreviewed queue depth exceeds one week's review budget, the drafter writes nothing that week and logs the reason.
- `/standup` shows queue depth **in review-minutes**, plus oldest unreviewed draft age.

---

## Architecture

### Target flow

```
agent_discover / agent_collect  (weekly, Exa Agent API)
        |
        v
data/inbox.md  (review-gated blocks)
        |
        v  /act  <-- NEW: parses machine blocks only          [D1, D2]
        |
        +--> company --> scan-targets.jsonl (lane, outreach)   [NEW writer]
        +--> person  --> networking.jsonl
        +--> role    --> job-pipeline.jsonl  or discard
                              |
                              v
        launchd drafter (weekly)  <-- lane:b + outreach:true   [D3, D4, D9]
          |  affiliation-anchored -> full draft (hook_type: affiliation)
          |  citable hook         -> paragraph draft (hook_type: research)
          |  neither              -> NEEDS-RESEARCH stub
          |  review budget spent  -> writes nothing, logs backpressure
                              |
                              v
        output/outreach-queue/MMDDYY-<slug>.md   (never auto-sent)
                              |
                              v  /standup surfaces depth-in-minutes + oldest age   <-- DRAIN
                              |
                              v
        human review --> send --> outreach-log.jsonl (format, hook_type, covariates)
                              |
                              v
        no-reply-14d flag (measurement only) --> /weekly-review descriptive read
                              |
                              v
        weekly cold follow-up sweep --> back into the queue   [D10]
```

Every producer has a named drain. Every guard has a floor below which it fails. Those are the invariants.

### Component: `tools/table_io.py`

- Reads return dicts keyed by **canonical field name** from `tools/schemas/*.json`, never positional index.
- Writes validate against the registry: field set, required fields, types, enums, date regexes, narrowed swap signature.
- Appends single-line and atomic.
- Unknown fields on read raise. Any non-JSON line raises.
- `read()` returns a **`RecordSet`** (Sequence over served records) with `.withheld` / `.withheld_ids`; tuple-unpacking raises. `_repair_needed` records are withheld from metric and drafter consumers and counted.
- Exposes `_repair_basis` so guessed repairs stay visible downstream.
- Honors `data/.migration-lock`.

### Component: `/act` inbox drain

| Block type | Marker | Destination |
|---|---|---|
| Agent drip (company) | `review-gated` | `scan-targets.jsonl` + optional pipeline |
| Agent drip (person) | `review-gated` | `networking.jsonl` |
| Career-scan roles | career-scan header | pipeline, or discard |

Unmarked blocks are invisible to `/act`.

### Component: target pool schema

```json
{"id":"acme-20260810-01","name":"<company>","lane":"b","outreach":true,"ats":"ashby","slug":"<slug>","active":true,"role_filters":["operations"]}
```

**Outreach eligibility is independent of hiring status — a first-class case.** For Lane B it is the *primary* case.

| `ats` | `outreach` | Meaning |
|---|---|---|
| set | `true` | Role-scanned **and** cold-outreach eligible |
| set | `false` | Role-scan only |
| null | `true` | **Outreach only — not hiring, still a target** |
| null | `false` | Inactive |

The scanner skips records lacking `ats` rather than erroring; the drafter must **not** filter on open roles or `ats` presence; **outreach-only companies do not create pipeline rows.**

### Component: weekly drafter

- Draws `lane == "b" && outreach == true && !_repair_needed`.
- Sized to `min(ramp step, review-minute budget)` per ADR-6.
- Pipeline: `contact_finder.py` → evidence-span employment gate → rank (affiliation as `warm_tie` input) → draft per ADR-4.
- Reuses `/scan-contacts` and `/cold-outreach` logic; reimplements neither.
- Writes to `output/outreach-queue/`. Never opens a mail client. Never sends.

### Component: measurement

- `outreach-log.jsonl` gains `format`, **required `hook_type`**, `hiring_status`, `warm_tie`, review outcome.
- `no-reply-14d` per ADR-5 — measurement state, never a send gate.
- `/weekly-review`: counts grouped by `hook_type` then format, labeled descriptive; withheld count and `user_judgment`-repair count printed.
- `/standup`: queue depth in minutes, oldest unreviewed draft age, sends this week vs current ramp step, warm follow-ups first, cold sweep last, withheld count.

---

## Discovery widening

| Preset | Change |
|---|---|
| `lane-a` | Stage range extended down to Series A. Count 25 → 40. |
| `lane-b` | Gains `monitor: {cadence: weekly}`. Count 25 → 40. |
| `deployment-leads` | Count 15 → 30. |
| `lane-b-leads` *(new)* | People preset for the Lane B sector. |
| `alumni-overlap` *(new, Phase 4)* | Alumni of the user's schools / past employers in relevant roles. |

**Depth.** `/outreach-batch` default rises `draft:3` → `draft:6`, with `contact_finder --num` raised to match — but 6 is a *cap*, not an expectation; measured `k` governs. Decay tripwire per ADR-3.

---

## Backlog cleanup

**`data/inbox.md`, 150 blocks:** ~8 machine review-gated, ~34 career-scan role blocks, ~108 human `/remember` captures spanning March-August. The 108 get a bucketing script (by age and type) with **batch approval by the user**. Nothing routed unreviewed.

**Corrupt rows:** counted by the Phase 0a value-driven scan, per table per rule, and reported before the converter is written. Known minimum: 1 six-cell + 1 eight-cell in outreach-log, 1 nine-cell in job-pipeline, ≥13 arity-conformant swapped-field rows in job-todos Completed (themselves the residue of `migrate_todos_column_drift.py`). All adjudicated against private-repo history under `_repair_needed` + `_repair_basis`.

---

## Build order

**Phase 0 — Pre-migration capture (no data changes)**
0a. Per-table integrity scan: **first** dry-run each proposed rule and print flagged counts per rule per table; narrow any rule flagging >5% of a table. Then triage every remaining hit, recovering pre-reflow originals from `$PRIVATE_GIT_DIR` and recording `_raw_original` + sha.
0b. Generate `tests/migration-inventory.json` (39 sites / 25 files → in/out of scope + reason) and the test that fails on any unclassified site.
0c. Freeze golden outputs of every `IN_SCOPE` tool (incl. `gen_pii_denylist.py` and the mutating paths against a scratch copy) with **non-emptiness assertions**; freeze `stage_vocab.is_terminal_stage` per pipeline row; measure and freeze **G7 baseline hit rates** and **G8 denylist floor counts**.
0d. Write `tests/golden/expected-diffs.json` from 0a — **before** goldens are relied on.
0e. **Live sync differential:** current name-matcher vs proposed FK-matcher over real `job-todos.md` + `job-pipeline.md`; print the symmetric difference row by row; adjudicate.
0f. Run G6 cross-tool parity pre-migration to document the archived-row divergence; resolve with the user.
0g. Copy the four files to `data/_pre-jsonl-snapshot/`.

**Phase 1 — Substrate (built against a copy; cutover in one quiesced window)**
1. `tools/schemas/*.json` registry (prose columns declared `free_text`) + G1 tests.
2. `tools/table_io.py` with narrowed value validation, `RecordSet` return, `_repair_basis` passthrough.
3. Converter: section fields, minted `id`, **`pipeline_id: null` always**, `_repair_needed`/`_ambiguity`/`_raw`/`_raw_original`, `notes` rejoined from `cols[4:]`.
4. `jsonl_to_markdown` renderer (permanent tool) + G2 cell-conservation differential.
5. G5 per-row tuple fingerprint (per table, per section) + G7 cross-file oracle + G5b stratified human sample.
6. Split `networking.md` → `networking.jsonl` + `networking-log.md`; assert prose bytes unchanged.
7. Port every `IN_SCOPE` site from the inventory (all sites per file, not one per file); consolidate section/terminal logic into `stage_vocab`; land G6 parity test; retire `schema_guard.py` and `migrate_todos_column_drift.py`. **Land G8: port `gen_pii_denylist.py`, add floor-based hard failure, make `check_public_pii.py` BLOCK on a short/stale denylist, and decide port/retarget/retire for every markdown-matching hook.** Add markdown-still-parses regression tests for every `OUT_OF_SCOPE` tool.
8. Fix `todo_write.py cmd_supersede` `cols[4]` → full notes; fix `cmd_sync` to withdraw only on non-null `pipeline_id`. **Both land before the sync ever runs against a sectionless file.**
9. `audit_rule_violations.py` sweep across `tools/`, `.claude/skills/`, `.claude/settings.json`, cross-checked against the inventory.
10. **Cutover window:** uninstall launchd → `.migration-lock` → convert → G1-G8 + G5b → land ports → regenerate denylist and confirm floor → smoke `/standup` → unlock → reinstall launchd → stranded-write diff.
11. Adjudicate every `_repair_needed` row against history until `withheld == 0`.
12. Schedule the 30-day weekly re-verification job (re-render + G1/G4/G5/G6/G7/G8 + validator → automation-health log).

**Phase 2 — Wiring** *(gated on `withheld == 0` and G8 green)*
13. `lane` / `outreach` fields, optional `ats`, in the target pool; scanner guard for missing `ats`.
14. Target-pool writer in `act_apply.py`; `/act` parses machine blocks in `data/inbox.md`.
15. Preset widening: Series A, counts, `lane-b` monitor, `lane-b-leads`.
16. `data/affiliations.jsonl` + affiliation as `warm_tie` input.
17. **Pool-depth probe (Phase 2 exit gate):** measure `k` against the 12 known Lane B companies; set the ramp before any drafter is built.

**Phase 3 — Outreach**
18. `format` / **required `hook_type`** / `hiring_status` / `warm_tie` / review-outcome fields; `no-reply-14d` + scheduler-invariance test.
19. `/outreach-batch` depth cap 6, affiliation-preferred hook selection, `NEEDS-RESEARCH` stubs.
20. Cold follow-up ladder per ADR-5.
21. Weekly launchd drafter → `output/outreach-queue/`, with ADR-6 backpressure and the 4-week quality tripwire.
22. `/standup` surfaces queue depth in minutes, oldest draft age, sends vs current ramp step, withheld count.

**Phase 4 — Cleanup**
23. Capture-triage script + batch approval for the 108.
24. `alumni-overlap` discovery preset.

**Interim.** Manual `/cold-outreach` against already-discovered companies is unblocked **except during the cutover window** and is the path to volume meanwhile.

---

## Risks

| Risk | Mitigation |
|---|---|
| **PII hook silently degrades to a no-op post-cutover on a PUBLIC repo** | G8: `gen_pii_denylist.py` ported + floor-based non-zero exit; `check_public_pii.py` BLOCKs on a short or stale denylist; landed in Phase 1 step 7 and a cutover exit condition. |
| Validator flags most of a prose column, forcing a normalization that flips closed-ness | Prose columns declared free text (D12); swap signature anchored and free-text/free-text excluded; Phase 0a rule dry-run with a 5%-of-table ceiling; `is_terminal_stage` frozen per row in G3. |
| Multi-target parser marked "ported" after one of two sites; sweep breaks an out-of-scope tool | Mechanically generated `tests/migration-inventory.json` keyed on `(file, line, target)`; test fails on any unclassified site; regression tests for out-of-scope parsers. |
| Adjudicating a prior machine guess from memory, months later | Recover pre-reflow originals from `$PRIVATE_GIT_DIR`; present history alongside corruption; `_repair_basis` recorded and surfaced forever; drafter refuses `user_judgment` context. |
| Registry authored from and tested against the same header (circularity) | G7 cross-file semantic oracle with measured, frozen baseline rates — evidence external to the migrated file. G1's limitation stated in code. |
| Column swap survives because multisets coincide, or a section is mislabeled | G5 replaced with per-row ordered-tuple hashes computed per (table, section); per-section row counts in G4. |
| 10 uniform samples miss a single-table or Archived-only swap | G5b stratified with mandatory inclusions, `_raw` printed above each record, per-FIELD confirmation, ~25 records. |
| `read()` tuple silently makes `len()` return 2 in the counting tools that calibrate the ramp | `RecordSet` Sequence return preserving `len()`/truthiness/iteration; unpacking raises; G3 asserts every counting tool's integers unchanged. |
| Converter mislabels a free-text column; unfalsifiable after cutover | G5 + G7 + G5b. G2/G3/G4 explicitly declared blind to this class. |
| G3 freezes an existing bug as the acceptance criterion | Exception list pre-declared in `expected-diffs.json`; G3 asserts exact set equality. |
| Variable-arity Notes truncated at conversion | G4 cell-level conservation + literal-pipe assertion; `notes` free-text; `cmd_supersede` fixed same commit. |
| 18-withdrawal incident laundered into authoritative FK data | No backfill; `pipeline_id: null`; FK-only sync; live symmetric-difference adjudication in Phase 0e. |
| Two tools silently disagree about archived rows | G6, pre- and post-migration; logic consolidated into `stage_vocab`. |
| "Rollback" is a data-loss event nobody will trigger | Rollback = re-derivation: permanent `jsonl_to_markdown`, in-place registry remap, 30-day scheduled re-verification with an artifact. |
| Withheld rows silently move the ramp denominator | `RecordSet.withheld`; every consumer prints the count; Phase 2 blocked on zero. |
| Skills/hooks still emit markdown rows post-cutover | `audit_rule_violations.py` sweep cross-checked against the inventory; `table_io.read` raises on non-JSON. |
| Background jobs write mid-cutover | One-hour quiesced window, launchd uninstalled, `.migration-lock`, stranded-write diff. |
| Pool depth cannot sustain the volume goal | Measured ramp; Phase 2 probe sets it; decay tripwire holds the step flat; lever is more presets, never more contacts per company. |
| Unattended drafts read generic | Affiliation-anchored only for full drafts; `NEEDS-RESEARCH` stub otherwise; 4-week discard-rate tripwire at >50%. |
| Review capacity, not queue logic, is the real cap | ADR-6: 4 min/draft budget, standing 45-min appointment, drafter cap = min(ramp, budget), depth in minutes. |
| Cold follow-ups bury the 27-day-overdue warm queue | One weekly sweep to-do, cheap late touches, warm ranked above cold. |
| 14-day flag misread as a stop signal | Named `no-reply-14d`; scheduler-invariance test. |
| Format comparison produces a false result | Confounded with `hook_type` by construction; descriptive counts only; no rate below N=50/format. |

---

## Out of scope

- Auto-sending. The human gate between generation and outbox is a hard invariant.
- Migrating non-tabular `data/` files, including `networking-log.md` and `data/personal-todos.md` (same schema as job-todos, deliberately not migrated — see the inventory).
- Formal statistical significance testing on the format comparison (ADR-2).
- Loosening ADR-4's format restriction to un-confound ADR-2. Explicitly rejected: send quality outranks the internal validity of a comparison we are not running.
- Normalizing the `Stage` column into an enum. Explicitly rejected (D12): it would rewrite the input to closed-ness detection to satisfy a validator we chose.
