# Tools Reference

Lookup tables moved out of `CLAUDE.md` on 2026-08-13, verbatim, when that file was 1.38x over
its 40KB always-loaded budget. Nothing here was reworded in the move.

**Read this before invoking any `tools/*.py` script**, before touching `tools/launchd/`, or when
a gitignored private config file appears to be missing.

**What deliberately did NOT move, and still lives in `CLAUDE.md`:** the `PYTHONIOENCODING=utf-8`
requirement on every script, and the prohibition on creating/truncating/`touch`ing files in
`tools/launchd/logs/` from a Claude Code Bash call. Those are prohibitions with destructive
failure modes, not lookups, so they stay in the always-loaded tier even though they sit beside
these tables.

## Atomic write scripts

**Atomic write scripts** (return JSON):

| Script | Purpose |
|---|---|
| `todo_write.py` | add/done/withdraw/supersede/clear/sync `data/job-todos.md` (`supersede <prefix>` withdraws all open rows matching a prefix — keeps one live follow-up per contact) |
| `pipe_write.py` | add/update/remove `data/job-pipeline.md` (`--repo-root .` before subcommand). `remove --stage Withdrawn\|Rejected\|Accepted` archives under the REAL terminal stage; default `Withdrawn` records a rejection as a withdrawal, which inverts the fact |
| `networking_write.py` | add/**update**/log/remove `data/networking.md`. `add` takes `--email`; `update <name>` changes only the fields passed (never `Added`) and rewrites the Interaction Log header when `--company` changes, so use it instead of remove+re-add, which leaves an `[ARCHIVED]` stub. `log` auto-detects a **received** reply (recipient → Nick) and flips the matching `data/outreach-log.md` row to `Replied`. Outbound phrasing no longer false-flips; override with `--reply-received` / `--no-reply-flip` |
| `remember_apply.py` | route notes to 11 destinations |
| `daily_stoic.py` | `--sync`/`--backfill` archive Daily Stoic meditations to `data/source-emails/daily-stoic/` (promo/digest filtered, 28 kept / 15 dropped on the seed corpus); `--mark-prompted <id>` records standup prompts. State: `tools/.daily_stoic_state.json`. Read-only Gmail; reuses gmail_fetch auth + sanitizer. |
| `act_apply.py` | pipeline-add / contact-add / notes-add / company-note-add / **target-add** / **target-reject** for inbox routing (`--repo-root`/`--dry-run` go BEFORE the subcommand). The target-* pair writes `data/scan-targets.yaml` at the TEXT level (never re-serialises, which would strip its hand-written comments) and validates the result, rolling back on corruption. `target-reject` records declines so `agent_collect.py` stops re-proposing them |
| `person_write.py` | `create` (scaffold a `data/people/<slug>.md` dossier, idempotent) / `add-entry` (atomic newest-first dated append to commitments/owed/touchpoints) / `list`. `--repo-root` before subcommand. See `/networking promote`. |
| `projects_to_yaml.py` | `data/projects/*.md` → RenderCV experience YAML stubs (source-of-truth for CV experience entries) |
| `cv_merge_theme.py` | compose CV content YAML with `framework/cv-themes/tuck-mbb.yaml` → render-ready, standalone CV YAML |
| `md_to_pdf_doc.py` | prep-doc PDFs (cheat sheets, dossiers, prep packages) via weasyprint — Georgia, multi-page |
| `convert_pdfs.py` | extract text from PDFs in `files/` |
| `fetch_transcript.py` | YouTube transcript + oEmbed metadata → ExtractionBlock JSON; caches markdown to `data/source-transcripts/<id>.md`. Used by `/analyze` video branch. Dep: `youtube-transcript-api`. |
| `agent_discover.py` | on-demand company/people discovery via the **Exa Agent API** (structured `output_schema` + citations). `--preset`/`--query`, `--entity company\|person`, `--effort`, `--async`/`--collect`. Reads `data/discover-presets.yaml`; company results scored by `company_scorer`. Engine: `agent_core.py`. (Replaces the retired Websets path `webset_discover.py` — Websets 401s for this account + is deprecating.) |
| `agent_collect.py` | launchd collector: re-runs each `monitor:`-flagged preset's Agent query, dedups vs known targets + a per-preset seen-set (`tools/.agent_seen.json`), writes new review-gated proposals to `data/inbox.md`. Takes `--today YYYY-MM-DD`. |
| `inbox_census.py` | Comment-span-aware census of `data/inbox.md`. The ORACLE every later inbox step asserts against, and the only figure that does not come from the code under test. A naive `## ` scan miscounts: a 372-line HTML comment hides headers. `--write` pins the result plus a source sha256. Aborts on unbalanced comment markers or a zero-header parse |
| `inbox_triage.py` | Read-only extraction of the non-machine blocks of `data/inbox.md` into a grouped review doc (open-loop / coaching / system-design / personal-vault / saved-read / idea-todo / reflection / unclassified). Never mutates the inbox; a test asserts the source hash is unchanged. Explicit `#personal` tags beat inferred signatures |
| `inbox_lock.py` | Advisory lock around `data/inbox.md` so concurrent writers (launchd collectors + interactive skills) cannot interleave a partial block. |
| `ledger_diff.py` | Diffs a meeting's commitment ledger against the prior call: what was kept, dropped, or newly promised. Consumed by `/meeting`. |
| `hook_trace.py` | Shared rotating trace-log writer for the auto-capture hooks (`log_tool_failure.py`, `scan_transcript_failures.py`). Caps `tools/.hook-trace.log` at 256KB + one rotated generation. Library, not an entry point. |
| `vault_paths.py` | The ONLY place the personal-vault root resolves (env `PERSONAL_VAULT_ROOT` or gitignored `tools/.personal-vault.conf`) + named accessors (therapy dir, personal inbox, voice corpus, todos, living logs). Never hardcode the root; a regression test fails the build if it reappears in a tracked public file. Unconfigured raises `VaultRootMissing` — no fallback, since the vault holds sealed material. Library, not an entry point. |
| `context_file_audit.py` | Measures an always-loaded context file section by section (bytes, rule/lookup density, advisory KEEP/MOVE) and backs `/trim-context-file`. Exit codes are the contract: 4 = zero rules (refuses an empty baseline), 5 = zero blocks, 6 = unsafe output dir, 7 = block count != `--expect-blocks`. Fence-aware splitting; `--emit-blocks` writes verbatim per-section blocks + a sha256 manifest. |
| `trim_context_gate.sh` | Step 0 gate for `/trim-context-file`. Exercises `--rules`/`--emit-blocks` on the real target and independently re-derives byte conservation from the source. Gates on exit status and parsed content, never on file existence or size (`cmd > f` truncates before `cmd` runs). |
| `backfill_memory_schema.py` | One-shot Phase 1 backfill that made the legacy memory corpus visible to `scan_promotion_candidates.py` (383 files, 4.7% -> 100% feedback coverage, 2026-08-13). Stamps `occurrences: 1` / `promoted: no` / `reopen_gate` / `needs_review: true` and **deliberately omits `last_cited`** — the PostToolUse hook stamps that on a genuine Read, so no value in the corpus is fabricated. `occurrences: 1` from this script is a **floor, not a count**; `needs_review: true` is what says so. Dry run by default; `--apply` writes atomically; refuses a zero-file scope (exit 2); conservation-checked per file before each write; idempotent. |
| `sweep.py` | **The one way to run an ad-hoc "does this token appear anywhere" scan.** Takes an EXPLICIT path list (never a glob computed in a prior shell call — shell state does not persist between Bash calls), **raises/exits 2 on an empty scope** (an empty sweep is an error, never a negative result), and every result carries its own denominator (`scanned` / `matched` / `paths`) because a verdict without one is not a verdict. **Word-boundary matching by default**; `--substring` is opt-in and labelled in the output. `--control <token>` is a positive control: a known-present token that must match, or `clean` is withheld and it exits 3 — a negative result is only trustworthy once the mechanism is proven to have read the files. Library (`from sweep import sweep`) + CLI. Promoted 2026-08-13 from two memory rules at 4 and 3 fires; `tests/scripts/test_sweep_signature_guard.py` bans the three shell shapes it replaces. |
| `source_corrections.py` | Surfaces the dated corrections pinned to source-project bullets as HTML comments, so drafting external-facing prose stops depending on noticing them. **The comment sits with the claim, which is exactly why it is invisible while you paraphrase that claim** — this fired twice (2026-07-08 CV, 2026-08-07 application answer) with the file already read in full in the same session, and both times only an after-the-fact review agent caught it. Reports EVERY HTML comment, not a marker whitelist, and splits unfilled template `TODO:` scaffolding into its own count rather than dropping it. Empty scope exits 2 (same contract as `sweep.py`: a scan that read nothing must never look like a scan that found nothing). Wired into `/generate-cv` Step 6a-corrections, `/cover-letter` 2b, `/apply` 4b. |
| `attention.py` | **One read-only view of every review queue that dead-ends.** Aggregates inbox (via `inbox_census`, the oracle), overdue todos, promotion candidates, and stale pipeline rows. Built 2026-08-13 after measuring that four producers write review-gated items and none is consumed on a cadence. **A missing source is a loud SKIP carrying `count: null`, never `0`** — a silently-omitted queue turns "nothing needs attention" into a lie — and `complete: false` flags the report. Every count carries its denominator. **Read-only by construction: takes no lock, so it is safe to run while another session drains `data/inbox.md`.** `--memory-dir` is explicit because the corpus lives outside the repo and `--repo-root` does not scope it. |
| `calibration_review.py` | Reviews logged prediction-vs-outcome pairs from `data/calibration/` (gitignored). |
| `backup-data.sh` | Nightly private-data backup driver, run by `/checkout`. **Reads the private remote + overlay git-dir from the gitignored `tools/.private-backup.conf`** — the public repo never names them. Missing config is a hard failure, never a silent skip. |
| `outreach_status.py` | Derives send-state for a recipient from `data/outreach-log.md`. **`sent` and `delivered` are separate, and `delivered` is tri-state and computed ONLY over rows matching a named `--artifact`** — a recipient-level query can never return `delivered:true`, because they may have replied on an unrelated thread. `--stamp` emits the frozen v2 provenance comment that `/prep-interview` pastes into Logistics; `--set-status` records a `Bounced`/`Delivered` on one row addressed by (recipient ∧ date ∧ artifact), writing nothing if it matches 0 or ≥2. Ambiguous recipient exits 2 and never guesses. Origin: a prep doc claimed "CV already sent, do not re-offer" about a CV that had bounced. |
| `artifact_vocab.py` | Single source of truth for artifact tokens (`cv`, `cover-letter`, `deck`, `portfolio`, `writeup`, `link`) + aliases. **Two functions, and the distinction is load-bearing:** `find_in_text` = does this sentence NAME the artifact (right question for a suppressive sentence); `find_transmitted_in_text` = does it say the artifact was actually SENT (right question for receipt). On the live log, 8 of 16 artifact-bearing rows name a CV without sending one, so scoping receipt on mentions returns a false `delivered:true`. Library + CLI. |
| `proof_domains.py` | Closed canonical enum of prep-doc proof domains + aliases. Exists so `customer-experience` and `customer-ops` cannot pose as different domains — a "reserve" proof in the primary's domain dies to the same exclusion sentence. Library + CLI (`--list`, `--canonicalize`). |
| `prep_doc_parse.py` | Shared prep-doc locator (`find_prep_doc`, newest by `MMDDYY` prefix) + the **frozen Primary/Reserve proof-line regex**, imported by `/prep-interview`, `/follow-up` Step 3e and `check_prep_doc.py` so the three cannot disagree about where a prep doc is or what it bound. `parse_proofs_text` checks in-flight content for the hook. Library + CLI. |
| `check_prep_doc.py` | Six checks on a prep doc; **run by `/prep-interview` Step 6a, which blocks the PDF render on failure.** 1-3: a primary AND reserve proof exist with canonically DIFFERENT domains. 4-6: suppressive phrasing ("do not re-offer", "already sent") must name its artifact and be backed by a stamp for that artifact reading `delivered=true` — `delivered=unknown` and `artifact=none` both fail, and a malformed/v1 stamp fails rather than being ignored. `SUPPRESSIVE_PATTERNS` is versioned and enumerated; extend it only with a fixture. Locates the Logistics block at ANY heading level (the live doc uses `#`, the skill template `##`). |
| `check_prep_doc_format.py` | **PreToolUse hook** (`Write\|Edit`) — the context-free subset of the above: a malformed/non-v2 stamp, and proof domains that are invalid or collapse. Fires only when BOTH proof lines are present, so it never blocks mid-authoring. Scoped to `output/<slug>/*prep*.md`, excluding `tests/` and `output/analysis/` (a hook that blocks its own fixtures makes the suite unrunnable). Missing reserves and unbacked suppression stay at skill tier — contextual, would over-fire. |
| `transcript_exclusions.py` | Scans a Granola transcript for sentences where the **counterpart** ruled a domain out of scope. `--speaker Them` by default: Nick uses "never" constructions himself, and surfacing his own line as an interviewer exclusion would block a valid proof. Body is bounded by the next `##`/`---` so a trailing `## Granola Private Notes` (Nick's TYPED notes) is never reported as spoken. Every output carries a `coverage` string — **`hit_count: 0` is not a clearance**; `--wide` adds low-precision `candidates[]`, never merged into `hits[]`. Used by `/follow-up` Step 3e and `/debrief` Step 1c. |
| `check_doc_precedence.py` | Catches a phrase `content-rules` BANS while `voice-reference` still advertises it (precedence: content-rules wins). Bidirectional template subsumption over the `banned_phrases`/`preferred_phrases` registry, so a construction ban collides with a literal ADD. **All three source files are gitignored**, so it SKIPS loudly from a clean clone and its real validation is Tier 2 local. `rather than` is registered only as a construction — as a bare literal it fires on ~10 benign prose uses. |


## Multi-agent workflows

**Multi-agent workflows.** Reusable orchestration templates live in `.claude/workflows/*.js`, invocable as skills. Patterns documented in `framework/multi-agent-workflows.md`: agents supply judgment, the script supplies deterministic control flow (fan-out, loops, convergence gates).

| Workflow | Pattern | Use when |
|---|---|---|
| `/plan-hardening` | Adversarial panel over N rounds, stopping when a round surfaces no NEW blocking hole (delta rule, not a certificate). Returns `residual_risks[]` + `unverified_claims[]` — **there is deliberately no `airtight` boolean** | Before executing an expensive or irreversible plan |
| `/extract-verify` | Extract records, then re-derive each in a fresh context (anti-anchoring) | Turning a messy corpus into a labeled dataset with provenance |
| `/research-audit` | Fan-out research per angle + adversarial claim validation | Deciding whether existing systems are stale and what to change |

Args are passed as a **JSON object**, not a bare string — `{"planPath": "...", "context": "...", "maxRounds": 3, "lenses": [...]}`. A bare string fails at parse time before any agent runs.


## Private local config

**Private local config (gitignored, required for full function).** The public repo carries no real identities or infra names; these files supply them locally and each degrades gracefully or fails loudly when absent:

| File | Supplies | Absent behavior |
|---|---|---|
| `tools/.private-backup.conf` | Private backup remote + overlay git-dir | `backup-data.sh` exits non-zero with instructions (never a silent no-op) |
| `tools/.personal-vault.conf` | Personal Obsidian vault root (one line; `~` expanded). Read only via `tools/vault_paths.py` — never hardcode the path. Env `PERSONAL_VAULT_ROOT` overrides. | `VaultRootMissing` with setup instructions. **No fallback by design:** the vault hosts sealed therapy material, so a guessed destination is worse than stopping. Consumers: `granola_auto_debrief.py`, `granola_save.py`, `living_log_append.py`, `personal_todo_write.py` |
| `tools/.owner-identity.txt` | Owner email(s) for external-attendee detection | Falls back to the public name alone |
| `tools/.therapy-classifier.txt` | Real therapist identities | Title-keyword classification only |
| `tools/.personal-projects.txt` | Personal-OS project routing allowlist | Two-way split instead of four-way |
| `tools/.pii-denylist.txt` | Generated PII tokens, harvested from `networking.md` + `job-pipeline.md` + `scan-targets.yaml` | Regenerate with `gen_pii_denylist.py`. **A real entity living in any other file is invisible to the deterministic hook**, so run `/audit-pii` (semantic pass) before any push |
| `framework/content-rules.{md,yaml}` | Voice/content rule corpus + exemplars | Skills skip the Content-Rules Pass |
| `tools/.local-validators.json` | Real-name / real-path / verbatim-quote expectations for the Tier 2 `--validate-local` runs of `outreach_status.py`, `transcript_exclusions.py`, `check_prep_doc.py`, `check_doc_precedence.py`. Four keys (`w1`-`w4`). The committed tests use placeholders only, so this file is where the real historical regressions actually get proven | Each validator prints `{"status":"SKIPPED","reason":"tools/.local-validators.json absent"}` and exits 0 — **loudly SKIPPED, never a silent pass** |


## --repo-root placement

`todo_write.py` accepts `--repo-root` anywhere; `pipe_write.py` and `networking_write.py` require it before the subcommand.


## launchd automation

**Background automation (launchd, macOS-native).** Schedules live as plists in `tools/launchd/`. Install/manage with `bash tools/launchd/install.sh {install|uninstall|status}`. Logs at `tools/launchd/logs/`.

| Plist label | Schedule | Effect |
|---|---|---|
| `gmail-fetch` | Every 15 min | `gmail_fetch.py` → `inbox/` |
| `gmail-fetch-personal` | Every 15 min | `gmail_fetch.py` (personal Gmail label) → the personal vault's `inbox/` |
| `career-scan` | Daily | scans target company career pages for new matches |
| `alirohde-triage` | Daily 9:15 | `alirohde_nudge.py` cheap-check: no-op unless a new "Ali Rohde Jobs" Substack edition landed in `inbox/`; then writes `inbox/YYYYMMDD-alirohde-edition-NNN-triage.md` (review-gated → run `/scan-jobs <url>`). State: `tools/.alirohde_state.json`. |
| `granola-auto-debrief` | Every 3 hrs at :20 (00:20, 03:20, …) | `granola_auto_debrief.py` → persists transcript+summary pair via `granola_save.py` (sealed-aware), AND posts a `<!-- voice: cloud-generated -->` snippet to an inbox. **Four-way routing:** `therapy` → sealed vault, no inbox; `personal` → personal vault corpus + `personal/data/inbox.md`, tagged with a project slug, CTA points at `/meeting`; `networking` → this repo's corpus + `data/inbox.md`, CTA points at `/debrief`; `unknown` → fail-closed, persisted nowhere. Personal-OS routing is driven by the gitignored `tools/.personal-projects.txt` allowlist (project → attendee/name/title rules); with no allowlist the behavior is identical to the pre-2026-08-06 two-way split. Therapy always outranks personal. |
| `memory-promotion-scan` | Weekly (Mon 07:00) | `scan_promotion_candidates.py` → surfaces memory rules due for promotion/demotion (feeds `/memory-refresh`) |
| `agent-discover-collect` | Weekly (Mon 09:45) | `agent_collect.py` → runs each monitored Exa Agent preset, drips new companies/people to `data/inbox.md` (review-gated). **Measured cost: $0.15 per run** ($0.025 lane-a + $0.10 lane-b + $0.025 deployment-leads; ~$7.80/yr weekly) — real but negligible. Corrected 2026-08-11 from a stale "$0.05 / 2 presets / ~$2.60/yr" claim: a 3rd preset was added and lane-b costs 4x the others. Verified against a live run producing 34 new companies (lane-a 2, lane-b 31, deployment-leads 1). Re-measure whenever a preset is added — the cost scales per preset, not per job. ⚠️ **`install.sh` installs this job unconditionally** along with the other 7 — there is no opt-out flag, so any `bash tools/launchd/install.sh install` restarts it. The doc previously said "NOT installed by default," which was false. **Currently ON** (verified loaded 2026-08-10, Mon 09:45) — briefly unloaded that day, then restored once the cost was measured at ~$2.60/yr against a live lead source. Disable: `launchctl bootout gui/$(id -u)/com.nickmagnuson.jobsearch.agent-discover-collect && rm ~/Library/LaunchAgents/com.nickmagnuson.jobsearch.agent-discover-collect.plist`. Check state: `launchctl list \| grep agent-discover-collect`. |


