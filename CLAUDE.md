<!-- This file is the orchestration brain — Claude reads it on every session.
     Personal details live in data/profile.md (gitignored).
     Everything else works as-is. -->

# AI Job Search System

End-to-end job search OS. See [README.md](README.md) for description, `docs/usage.md` for skill reference.

## Hard Rules

- **Profile guard.** Before any generative or research skill, verify `data/profile.md` and `data/goals.md` exist with real content (not TODOs). If either is missing or all TODOs, stop and tell the user to run `/import-cv` (profile) or fill `data/goals.md` from `framework/templates/goals.md`. Never fall back to generic candidate context.
- **No em dashes** (—) in any output Nick will send. Use commas, periods, or hyphens.
- **Never draft email bodies inline in chat.** Any outgoing email — subject + greeting + body + signoff, or any subset that constitutes a draftable message — MUST go through `/draft-email`, `/cold-outreach`, or `/follow-up`. The hook stack (`check_email_via_skill.py` + `check_draft_voice.py` + `.pending-draft.source` marker) gates the *tool-call* surface (Write/Edit/Bash on draft files); inline chat drafts bypass that surface entirely. Skill-tier voice anchoring (matched exemplar from `framework/voice-reference.md`) cannot fire when no skill runs. If Nick says "draft something quickly" or "send a quick note," the answer is to invoke the skill — not to skip it. Origin: 2026-05-20 inline-draft incident; tier escalation per `memory/feedback_llm_self_policing_fails.md`.
- **`data/project-background/` is sealed.** Contents never appear in CVs, cover letters, recruiter prep, voice exports, networking notes, or any external-facing artifact.
- **Personal facts** live in gitignored files (`data/profile.md`, `data/goals.md`, `data/professional-identity.md`). Public-fork-safe.
- **Never infer or upgrade Nick's role titles or scope from thin context.** His McKinsey title is **management consultant** (marketplace + customer-engagement transformations) — NOT "Engagement Manager" or any implied seniority level. His ESPN role was **digital analytics manager/analyst within product analytics** (product + social/content performance) — NOT "SEO." Cite what he DID, never a level or function he didn't hold. Fired 3x on the same shape (2026-06-22 at a target company: "ran SEO at ESPN"; 2026-06-25 at another + 2026-07-07 fit-assess: both "I am not an EM") before this rule existed — one bad inference propagates into a dossier, a prep doc, and a live interview opener before Nick catches it. See `memory/reference_nick_mckinsey_role.md` and `memory/reference_nick_espn_role_is_product_analytics.md`.
- **Never assert absence without running the check this session and naming the scope.** "X doesn't exist" / "Y didn't happen" / "not found" / "no result" claims about codebase state, file contents, data records, or memory entries MUST be backed by a grep/ls/Read in the current session AND the assertion must state what was checked (e.g., "no `check_skill_shadow.py` in `tools/` per `ls`" — not bare "the hook doesn't exist"). Memory and prior-session knowledge are not sufficient — they decay. Asserting absence from memory creates ghost facts: a future audit cycle treats Claude's "not built" as authoritative and skips the verification, compounding the original error. Origin: `feedback_llm_verification_system` Rule #1 + Rule #13 (scope-limited query). Family-N extends to data-state absence claims (e.g., "you haven't applied yet" — see `feedback_pipeline_applied_status_must_be_user_confirmed`, 5/28 ghost-row incident).
- **Never make a durability, backup, sync, version-control, or coverage claim without running the function-first tooling sweep and naming it as the scope.** No git command answers "is this data backed up." `git rev-parse`, `git remote -v`, `git check-ignore`, and `find -name .git` are **repo-shape** checks; they answer "what does git see from here," and the real mechanism here is an **overlay `GIT_DIR` outside the working tree** that is invisible to every one of them. Required before any such claim: `ls tools/ | grep -iE "backup|sync|push|mirror|archive"` and `grep -rl "git push\|rsync\|remote add" tools/ .claude/skills/`. `/data/` and `/output/` are gitignored **by design** (public repo) and are backed up by `tools/backup-data.sh` to a **private** repo, run nightly by `/checkout`; a retired decoy clone elsewhere on disk looks like evidence of absence and is not. **The concrete repo name, account, overlay path, decoy path, and the `gh api ... --jq '{pushed_at}'` verification command live in the gitignored `memory/reference_private_data_backup_mechanism.md`** — read it before making the claim; it is deliberately not in this public file. **Fired 3x on the same shape** (7/8, 7/30, 7/31 — the last two asserted false absence and the 7/31 fire also misdirected Nick to verify Obsidian Sync instead of surfacing that the real backup was a day stale). See `memory/feedback_prefer_extending_existing_infra_over_new_build.md` and `memory/reference_private_data_backup_mechanism.md`.
- **`grep` here skips gitignored trees. Name `data/` and `output/` explicitly, or the search is blind.** The shell's `grep` resolves to a ripgrep-backed wrapper that honors `.gitignore`. `/data/` and `/output/` are gitignored by design (public repo) and hold nearly all the real content: dossiers, reflections, generated CVs, pipeline artifacts. **A repo-root `grep -ri "<term>" .` cannot see any of it and returns an empty result indistinguishable from a true negative.** Adding `--exclude-dir=.git` makes it look deliberately scoped while changing nothing. Any search establishing presence or absence must either name the trees (`grep -ri "<term>" data/ output/`) or bypass the wrapper entirely: `find . -path ./.git -prune -o -name '*.md' -print | xargs /usr/bin/grep -l "<term>"`. **Fired 2x** (2026-08-07: a contact search returned nothing while a dossier and three output dirs existed; 2026-08-12: a sweep for a corrected-away CV overclaim returned nothing while **43 files** carried it, on a PII-relevant back-propagation task). This is the concrete meaning of "name the scope" in the absence-assertion rule above. See `memory/feedback_grep_is_ripgrep_gitignore_blindspot.md`.
- **Investigate before answering when factual claims about code, files, or data are involved.** Never speculate about a file, function, configuration, or data record you have not opened in this session. If the user references a specific file/skill/hook/data row, read it before answering. Memory and prior-session knowledge can be stale — the source is canonical, memory is a pointer. Composes with the absence-assertion rule above: stating "X exists" and "X doesn't exist" both require this-session evidence. Origin: Anthropic prompting guide (2026-05-28) + 13+ verify-* family rules. See [[verification-umbrella]] (`framework/verification-umbrella.md`, the Family L composite, built 2026-06-01).
- **Run a grey-area pass before proposing any multi-step plan.** Trigger phrases: "let's plan X" / "what's your approach" / "propose a plan for Y" / "before we start" / phase boundaries inside ongoing sessions / about to invoke `EnterPlanMode` or any planning skill (`/gsd-plan-phase`, `/gsd-explore`, `/gsd-discuss-phase`). The pass = (1) enumerate grey areas with leans + alternatives + cost-of-default, (2) ask explicitly "are there others I'm missing?", (3) WAIT for responses, (4) draft the plan only after grey areas resolve. The pass is mandatory even when context feels fresh and the plan feels obvious — "feels obvious" is the LLM intuition this rule exists to override. Cost: ~2 min. Cost of skipping: a wrong-direction plan compounds across every downstream commit. Origin: 2026-05-22 multi-edit cascade + 2026-05-28 standing-protocol elevation. See `memory/feedback_push_back_on_grey_areas_before_starting.md`.

## Tool Usage Conventions

- **Parallel tool calling is the default for independent operations.** If you intend to call multiple tools and there are no dependencies between them, make ALL independent tool calls in the same response — do not serialize them. This is not optional for read-only investigation (grep, ls, Read, glob) — sequencing read operations one-per-response wastes tokens and turns. Reserve sequential calls for genuine data dependencies (output of A feeds input of B).
- **Subagent overuse guard.** Use subagents (Task tool) when tasks can run in parallel, require isolated context, or involve independent workstreams. For simple tasks, sequential operations, single-file edits, or quick greps — work directly. The cost of spawning a subagent (context restart, isolation overhead, summary loss) is real; don't pay it for work you can do in 1-2 tool calls. Origin: Anthropic prompting guide (2026-05-28).
- **Git hooks are not version-controlled.** The pre-push PII gate lives in `tools/hooks/` (tracked) and must be installed into `.git/hooks/` on every clone: `bash tools/hooks/install.sh` (`--check` detects drift). Before this existed the guard script was tracked but the hook that called it was not, so a fresh clone silently had **no push-time gate at all**. Origin 2026-08-12.
- **Public-repo PII gate.** This repo is PUBLIC; public artifacts (`tests/`, `.claude/skills/`, `framework/`, `docs/`, `tools/*.{py,md,sh}`, top-level `*.md`) must use generic placeholder examples, never real contacts/pipeline-target companies. Two layers: (1) `tools/check_public_pii.py` PreToolUse hook (always-on, BLOCKs known real tokens from the gitignored `tools/.pii-denylist.txt`); (2) `/audit-pii` skill — run before committing/pushing any public-file change for the semantic subagent pass that catches new names the denylist misses. Regenerate the denylist via `tools/gen_pii_denylist.py` after adding contacts/pipeline rows. Origin: 2026-06-11, `feedback_generalize_examples_in_public_artifacts`.
- **Data tools are verified on REAL data, not fixtures; duplicated domain logic gets ONE source of truth.** Any `tools/*.py` that reads/parses/aggregates a real data file (pipeline, networking, outreach, todos) is not "done" on a green fixture suite — run it against the live file and inspect the output; when two tools must agree, run both and diff. A green suite is an artifact of rigor that hides real-data divergence (fable-audit Theme 2: 29 closed companies mis-counted as "active," undetected by a passing suite). When the same domain rule (stage classification, schema parsing) appears in a 2nd tool, consolidate it into one module + a single-source-of-truth guard test + a cross-tool parity test — never patch the copies in parallel. Exemplar: `tools/stage_vocab.py` + `tests/scripts/test_stage_classification_consistency.py`. Full rule: [[verification-umbrella]] `verify_data_tool_on_real_data_not_fixtures` + `memory/feedback_consolidate_duplicated_domain_logic_and_verify_on_real_data.md`.

## Self-Improvement Loop

After any user correction, run the **full tiered protocol** — not just a lessons.md row. The tier ladder (per [[feedback_llm_self_policing_fails]]: memory < skill < hook < independent reviewer) determines *where* the rule lives; the protocol below determines *how* it gets there.

### Step 1 — Capture at memory tier (always)

1. **Create a dedicated auto-memory file** at `~/.claude/projects/-Users-mag-Documents-Obsidian-30-projects-job-search/memory/feedback_<kebab-case-slug>.md` with the frontmatter below — **all six `metadata` keys are mandatory for `type: feedback`** — and a body containing:

   ```yaml
   ---
   name: feedback_<kebab-case-slug>
   description: <one line, used for recall relevance>
   metadata:
     node_type: memory
     type: feedback
     occurrences: 1        # bump on EVERY repeat fire, or the rule can never trip its own gate
     promoted: no          # or "yes — <tier>, <date>" once wired into a skill/hook/principle
     reopen_gate: "<trigger + the concrete structural target it promotes to>"
     last_cited: YYYY-MM-DD
   ---
   ```

   **`occurrences` / `promoted` / `reopen_gate` / `last_cited` are the ONLY keys `tools/scan_promotion_candidates.py` reads** — the detector behind both `/memory-refresh` and the weekly `memory-promotion-scan` job. A memory file without them is **invisible** to it: the rule accumulates fires while reporting as a first-timer, and no promotion ever surfaces. Prose alone does not register. Origin: this instruction previously specified only `name`/`description`/`metadata.type`, disagreeing with the `lessons-learned` skill, so **384 of 403 `feedback_*.md` files (95%) are invisible to the scanner** — 69 of them written in the month *after* the gap was first discovered. Fixed 2026-08-13; see `output/analysis/081326-memory-hygiene-project.md`.
   - **Rule:** the new rule, plainly stated
   - **Why:** the underlying principle
   - **If the rule is about absence-assertion, verification-before-claim, or hook/skill effectiveness**, default to skill or hook tier (not memory tier), and check the family-N count across [[verification-umbrella]] — these failures compose silently and memory-tier capture alone has a documented track record of not preventing recurrence.
   - **Origin:** the specific incident that triggered the correction (date + concrete trace)
   - **How to apply:** the operational test ("when X happens, do Y")
   - **Connections:** `[[wikilinks]]` to related memory rules — link liberally even if the target doesn't exist yet
   - **Tier ladder:** name the next promotion target (skill / hook / framework) and the trigger condition
2. **If the correction refines an existing rule**, update that file instead of creating a duplicate. Add a dated supplement section.
3. **Add a one-line index pointer** (`- [Title](feedback_*.md) — terse hook`). Since the 2026-07-08 7-shard restructure, `~/.claude/projects/.../memory/MEMORY.md` itself holds ONLY Critical Context (facts that must never depend on recall — employment status, family contacts, active hard-rule-DUE items) plus the Topic Shards router; it is NOT the index anymore. Check MEMORY.md's Topic Shards router table and add the pointer to the matching `memory/index-<topic>.md` shard (outreach / coaching / research / tools / system / personal / projects). Only add directly to MEMORY.md's Critical Context block if the fact itself belongs there. Keep each shard under ~24KB; archive on overflow per [[project_memory_directory_structure]].
4. **For email/outreach corrections specifically:** also add a row to `memory/lessons.md` Section 2 (Occurrences=1, Promoted=No). Recurring → increment Occurrences. If Occurrences ≥ 2 and Promoted=No → update `framework/style-guidelines.md` "Nick's Voice" section, set Promoted=Yes. This is the email-specific promotion path; doesn't replace the auto-memory file.

### Step 2 — Plan promotion via REOPEN gate (when build cost > 5 min)

If the structural fix (skill edit / hook addition / framework rewrite) takes more than a few minutes and you're not building it now, **add a parked todo with an explicit REOPEN gate** (per [[feedback_defer_todos_as_reopen_gates]]):

- **Format:** `"PARKED — <skill or system> improvement: <change>. REOPEN gate: <occurrence-count trigger or named decision criterion>. Per [[feedback_*]]."`
- **Priority:** Low
- **Due:** `—`
- **The REOPEN gate is mandatory.** "Build by [date]" is the failure mode; "REOPEN when this fires a 2nd time" is the correct shape.

### Step 3 — Promote when gate trips

When the REOPEN gate condition is met (2nd occurrence, threshold crossed, etc.):
- **Trace to source before building a guard (mandatory).** Before adding a hook or any forward-guard, ask: *what artifact or instruction generated this bad input?* If a doc, skill, template, or printed-help string prescribes the failing pattern, fix THAT in the same pass. A forward-guard over a poisoned source leaves the recurrence intact — the guard just trips on every run while the source keeps emitting the bad pattern. Origin: 2026-06-02 — `check_bare_python.py` was built to block bare `python`, but 13 skill docs + tool docstrings still *prescribed* it; the hook tripped on every skill run until the docs were swept. "Patch the script" was misread as "build a guard" instead of "fix what emits the input."
- **Back-propagate to existing artifacts (mandatory for greppable rules).** A newly-learned rule is enforced FORWARD by default (memory/hook catches future violations). The artifacts that ALREADY violate it must be swept in the same pass. When the rule has a greppable violation signature, run `PYTHONIOENCODING=utf-8 python3 tools/audit_rule_violations.py --pattern '<signature>'` and sweep or surface every existing hit. Skipping this is how a rule sits "captured" for weeks while broken artifacts accumulate (origin: the python3 rule existed while 13 docs violated it; `audit_rule_violations.py` built 2026-06-02 to close this gap).
- **Skill-tier promotion:** edit the relevant skill SKILL.md to make the rule structurally enforced (mandatory step, not implicit context).
- **Hook-tier promotion:** add a check_*.py hook in `tools/` and wire it into `.claude/settings.json` (defaults to WARN exit 0; reserve BLOCK for unambiguous violations per [[feedback_warn_vs_block_hook_design]]). Scaffold from `tools/HOOK_AUTHORING.md` (command-position regex + quote-stripping + clean/block tests + mandatory live smoke) so the new hook doesn't repeat the command-position-not-substring blind-spot class.
- **Framework-tier promotion:** for tonal/voice rules with Occurrences ≥ 2, update `framework/style-guidelines.md` "Nick's Voice" section (email/outreach path above).
- **Hard-rule promotion:** for project-tier rules unmissable in any session, add a bullet to the `## Hard Rules` section of this file. Reserve for rules that meet the bar: structural, unambiguous, and bypassable by skill-tier defects (cf. the 2026-05-20 "never draft email bodies inline" rule).

### Step 4 — Scan on relevant sessions

Scan `memory/MEMORY.md` (auto-loaded every session — Critical Context + the Topic Shards router) and `memory/lessons.md` (skill-edit / data-ops / CV-gen sessions) at the start of relevant work. For anything beyond Critical Context, load the relevant `memory/index-<topic>.md` shard(s) named in the router table — that's where the actual index entries live now, not in MEMORY.md itself. Full memory files load on demand via recall.

### Anti-patterns this loop is designed against

- **Single-tier capture.** Dropping a row in lessons.md and moving on — without an auto-memory file, the rule is unfindable next session.
- **Build now, no gate.** Adding a "Build skill update by 5/25" todo with no occurrence trigger — drifts indefinitely or fires prematurely.
- **Skip the wikilinks.** A memory file with no `[[connections]]` doesn't surface during recall on related topics; isolates the rule.
- **Tier confusion.** Treating a tonal voice nudge as a hard-rule candidate, or a project-tier safety rule as a memory-only note. The tier ladder belongs in the file itself so the next promotion is obvious.
- **Symptom-guard over source-fix.** Building a hook to block a bad pattern without fixing the doc/skill/template that prescribes it. The guard then trips forever on a problem you could have deleted. Always trace to source first (Step 3).
- **Forward-guard, no backward-sweep.** Capturing/enforcing a rule for the future while leaving existing violations in place. A rule isn't landed until the artifacts that already break it are swept — run `audit_rule_violations.py` for greppable rules.

## Repository Structure

```
framework/         Workflows, methodologies, style guides, templates
coaching/          coached-answers/, pressure-points/, anti-pattern-tracker.md, progress-recruiter/
data/              Owner data (profile.md, goals.md, professional-identity.md gitignored)
  ├─ company-notes/, industry-notes/, projects/
  ├─ people/               Per-person relationship dossiers (active relationships only; no date prefix)
  ├─ project-background/   Sensitive — never in output
  ├─ reflections/          Snapshots of Nick's processing (date-prefixed)
  └─ workbooks/            Reusable frameworks (no date prefix)
.claude/skills/    38 slash-command skill definitions
memory/            MEMORY.md (auto-loaded router + Critical Context), index-<topic>.md shards (7), lessons.md, archives
tools/             Python scripts (PDF, preprocessing, atomic writes, launchd schedules)
output/            Generated outputs — company-first hierarchy
```

## Output Conventions

**Company-first hierarchy.** Every named entity gets `output/<slug>/` (slug = lowercase-hyphens). Dossier matches folder name (`output/<slug>/<slug>.md`, no date). All other files date-prefixed `MMDDYY-[descriptor].md`. Flat `output/MMDDYY-*.md` only for entity-less one-offs.

`data/company-notes/<slug>.md` and `data/industry-notes/<slug>.md` hold free-form personal context. Append `## YYYY-MM-DD | [context]` entries. Generative skills read these automatically.

### Per-Person Relationship Dossiers (`data/people/<slug>.md`)

The judgment layer for an active relationship, distinct from the flat roster. Sharp boundary:

- **`data/networking.md`** = the roster + raw interaction log. Every contact, full message content, append-only, transactional. Mutated via `networking_write.py`.
- **`data/people/<slug>.md`** = the synthesized dossier for the ~active relationships only: Where This Stands, Pressure Points, Commitments (what they committed to), What I Owe, Touchpoints (pointers, not pastes), Next Move. NOT a duplicate of the interaction log; it is the "where does this relationship stand and what's my play."

slug = person name lowercased, accents folded, spaces→hyphens (matches the `output/<slug>` convention, e.g. `first-last`). Created on demand via `/networking promote` (or `person_write.py create`), never auto-created for every contact: anti-sprawl by recruitment. The structured sections (Commitments / What I Owe / Touchpoints) are atomic dated appends via `person_write.py add-entry`; the freeform sections are edited via re-read + Write. Read-consumers: `/networking`, `/follow-up`, `/cold-outreach`, `/draft-email`, `/prep-interview` load `data/people/<slug>.md` when present. Template: `framework/templates/person.md`. Exemplar: `data/people/<slug>.md`. Origin: comparison against an external personal-OS system, E2 (built 2026-06-01).

## Data Files

### Write-Only Files (use Write or atomic scripts, not Edit)

Edit silently fails on rows >500 chars. Mutate via:

- `data/job-todos.md` → `tools/todo_write.py`
- `data/job-pipeline.md` → `tools/pipe_write.py`
- All `output/**/*.md` dossiers → re-read and Write full content

PostToolUse hook warns when Edit hits an affected file.

### Decisions & Accomplishments Logs (chronological, newest-first)

Two append-only logs at top-level `data/` (gitignored, personal), written newest-first under `## YYYY-MM-DD` headers:

- **`data/decisions.md`**: strategic search decisions. The chronological companion to `goals.md` (goals.md = current direction; decisions.md = how, when, and why I got there). Each entry: what I decided, what drove it, what changes if it's wrong. **Boundary:** strategic search decisions only, not every small choice. Distinct from MEMORY.md auto-memories (Claude's operational memory) and from reflections (Nick's in-the-moment processing); a decision may also appear in MEMORY.md, but decisions.md is the human-readable chronological record.
- **`data/accomplishments.md`**: job-search-PROCESS wins (an onsite landed, a dossier shipped, a networking ladder built). **Boundary:** NOT career-history bullets (those stay in `data/projects/<name>.md`), and milestone-level only, not every daily task (daily granular work stays in the `/checkout` log). Substrate for retros and LinkedIn posts.

Written via `/remember` (routes `decision`/`accomplishment` captures here and prompts for the structured fields) or appended directly. Programmatic appends go through `tools/remember_apply.py` (atomic, newest-first). `/weekly-review` and `/standup` read them.

### Personal Exploration — Four Kinds

Snapshots get `YYYY-MM-DD-` prefixes; living docs don't. Dividing question: *does this update over time?*

| Kind | Lifecycle | Location |
|---|---|---|
| Source-of-truth identity docs | Living, single canonical version | Top-level `data/` (`profile.md`, `professional-identity.md`, `goals.md`, future `conviction.md`) |
| Sensitive captures (therapy, family, mental health) | Frozen at capture; never in any output | `data/project-background/` |
| Reflections (Nick's own processing) | Frozen at writing; new session = new file | `data/reflections/` |
| Workbooks / frameworks | Updated over time | `data/workbooks/` |

Files in `data/project-background/` open with a boundary header forbidding use in any external-facing artifact. Reflections inform guidance but are never source-of-truth — they capture how Nick was thinking on a date, not what's currently true.

**Therapy docs use a two-tier pattern, sealed in personal vault.** Per-session files (`YYYY-MM-DD-therapy-{therapist}-transcript.md`) are frozen and contain transcript + session-specific themes. The undated aggregate (`therapy-themes-job-search.md`) is the living cross-session synthesis. Both live at `<personal-vault>/data/therapy/` (relocated 2026-05-04 from this project's `data/project-background/` per the personal-vs-job-OS architecture; see `framework/personal-vs-job-os-architecture.md`). Same sealed-file rule applies: never appears in any external-facing artifact. When a new session adds themes that recur, refine, or contradict prior themes, promote them into the aggregate as a new dated supplement section; session-specific themes that don't generalize stay in the per-session file only.

**Reflections use a two-voice pattern.** Dated files (`data/reflections/YYYY-MM-DD.md`) are Nick's voice, frozen at writing — never edit them with Claude-voice synthesis. The companion `data/reflections/_themes.md` is Claude's voice — co-authored reflections (theme, gap, reframe) appended after each `/reflect` session, newest first. Same two-tier logic as therapy docs: frozen per-session + living cross-session. Skills that surface longitudinal patterns (`/standup`, `/weekly-review`, `/my-world`, `/checkout`) read `_themes.md` for cross-reflection patterns once enough entries exist (~4+). Single-reflection content stays in the dated file only.

### Three Identity Docs — Sharp Boundaries

Three layers, not three versions. Different consumers, different cadences. Drift is a problem; collapse would be worse (every weekly goals tweak would touch the file voice/positioning skills read).

| Doc | Holds | Update cadence |
|---|---|---|
| `profile.md` | **Facts.** Career history, education, skills, availability. | Rarely (when a fact changes). |
| `professional-identity.md` | **Self-understanding.** Strengths, growth edges, work style, values, narrative patterns, conditions for thriving. | Occasionally (after major reflection). |
| `goals.md` | **Direction.** Thesis, target criteria, comp, phase, weekly focus, success metrics. | Frequently (weekly review, search shifts). |

**Boundary rules:**
- Comp facts (floor, target, equity) → `goals.md` only.
- Work style and conditions for thriving → `professional-identity.md` only.
- Target industries and role types → `goals.md` only (they shift). `professional-identity.md` describes *what kind of work Nick is drawn to*, not which sectors are on the list this month.

### Workbook Outputs Update Existing Docs

Outputs from `data/workbooks/*.md` update existing source-of-truth structures. The workbook is the instrument; the existing docs are the canon.

| Workbook output | Destination |
|---|---|
| Conviction doc (3 paragraphs) | New top-level `data/conviction.md` (only genuinely new artifact) |
| Sharpened achievement bullets (Part 4 STAR factual content) | `Key Achievements` in `data/projects/<name>.md` |
| Spoken STAR delivery (Part 4) | `coaching/coached-answers/<question-type>.md` |
| One-sentence value statement | `goals.md` thesis or `professional-identity.md` summary |
| Conditions Statement (Part 2) | `professional-identity.md` Work Style |
| Green/red flags + screen questions (Part 3) | `goals.md` Non-Negotiables |
| Two-sentence Zuora chapter | `professional-identity.md` Career Direction + `coaching/coached-answers/why-did-you-leave.md` |
| "What worked / didn't / learned" doc (Part 1) | New dated file in `data/reflections/` |

### Resume Bullets vs Spoken STAR Stories — Different Artifacts

Same underlying experience, different forms. Do not conflate.

- **Resume bullets** → `data/projects/<name>.md` → `Key Achievements`. Written, scannable. Used by `/generate-cv`, `/apply`, `/cover-letter`. Optimized for the 6-second resume scan.
- **Spoken STAR stories** → `coaching/coached-answers/<question-type>.md`. Conversational, practiced. Used by `/prep-interview`, `/voice-export`, `/debrief`. Optimized for spoken delivery — pacing, hedging-removal, emotional arc.

Never use a CV bullet as an interview answer or a spoken story as a CV bullet.

### Projects

`data/projects/*.md` follows `framework/templates/project.md`: Period, Role, Client, Industry, Location, Type, Description, Responsibilities, Key Achievements, Technologies, Tags.

Type values: `flagship` | `consulting` | `contract` | `employment` | `co-founded` | `internship` | `side-project`.

## Research Dossiers

**Two-speed reading design:**
1. **Executive Summary** — thesis, opportunity rating, top reasons/risks, next action. Scan in 2 min.
2. **Full dossier** — every section opens with bold **BLUF** sentence. Scan all BLUFs in 60 sec.

**Evidence rules:**
- Source tiers: A (primary/official), B (reputable secondary), C (aggregator/crowd — flag).
- High-impact claims tagged `[Confidence: High|Med|Low, as of YYYY-MM]`.
- Contradictions: show both sources, mark `[Needs verification]`.
- Self-reported metrics: always qualify ("they report" / "self-reported"). Never present as independently verified.
- Both `/research-company` and `/research-industry` include Evidence Summary Table and contradiction audit.

**Refresh:** Fresh dossier (<14 days) — offer "view existing" or "refresh." On refresh, include `## What Changed`. Flow: `/research-industry` → `/research-company` → `/cold-outreach` or `/follow-up`.

## Resume Generation & Interview Training

- Resume standards (tailoring, 16-point checklist, cheat sheet) → `framework/application-workflow.md`. Used by `/generate-cv`, `/apply`, `/cover-letter`.
- Interview workflow, coaching rules, progress logging → `framework/interview-workflow.md`.
- Six answering strategies in `framework/answering-strategies/` (blank-mind, gap reframing, pressure defense, question-back, anti-patterns, direct answer structure).
- Voice simulation: `/voice-export` (generate prompt) → practice in Claude App → `/debrief` (analyze).

## Problem Solving & Communication Craft

Two foundational frameworks from Nick's McKinsey training. Sealed raw materials in `data/project-background/mckinsey/`; the extracted concepts in framework docs are public knowledge.

- **Problem solving** → `framework/problem-solving-mckinsey.md`. The 7-step method (Define → Structure → Prioritize → Plan → Conduct → Synthesize → Recommend), Problem Statement Worksheet, MECE issue trees, hypothesis-driven workplans, pyramid synthesis tests. Used by `/prep-interview`, `/debrief`, `/research-company`, `/research-industry`, and any synthesis work where structure matters.
- **SMB decision analysis** → `framework/smb-decision-analysis.md`. The method for irreversible small-business calls (takeover, lease, capital): fact base → structured problem → multi-lens → assumptions register → adversarial verdicts → gates → per-audience artifact. Its three survival disciplines (canonical spine, superseded banners that state *what survives*, blind-run reconciliation) apply to any analysis that outlives its own premises.
- **Slide / communication craft** → `framework/slide-craft-mckinsey.md`. Ledes (insight vs. process), 30-second test, page anatomy (4 corners), 11-point quality checklist, 8 common feedback patterns, "kill empty verbiage" rules. Used when producing prep PDFs, dossiers, cover letters, and any artifact where the audience scans first.

## Tools & Environment

Python 3.10+ (several `tools/*.py` use PEP 604 `X | None` annotations evaluated at def time — e.g. `networking_read.py`, `networking_followup.py`, `pipe_read.py`, `pipeline_staleness.py`). `pip install -r requirements.txt` for PDF features. **All `tools/*.py` scripts require `PYTHONIOENCODING=utf-8` prefix or they crash on Unicode.**

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

**Multi-agent workflows.** Reusable orchestration templates live in `.claude/workflows/*.js`, invocable as skills. Patterns documented in `framework/multi-agent-workflows.md`: agents supply judgment, the script supplies deterministic control flow (fan-out, loops, convergence gates).

| Workflow | Pattern | Use when |
|---|---|---|
| `/plan-hardening` | Adversarial panel over N rounds, stopping when a round surfaces no NEW blocking hole (delta rule, not a certificate). Returns `residual_risks[]` + `unverified_claims[]` — **there is deliberately no `airtight` boolean** | Before executing an expensive or irreversible plan |
| `/extract-verify` | Extract records, then re-derive each in a fresh context (anti-anchoring) | Turning a messy corpus into a labeled dataset with provenance |
| `/research-audit` | Fan-out research per angle + adversarial claim validation | Deciding whether existing systems are stale and what to change |

Args are passed as a **JSON object**, not a bare string — `{"planPath": "...", "context": "...", "maxRounds": 3, "lenses": [...]}`. A bare string fails at parse time before any agent runs.

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

**CV PDFs use RenderCV** (`~/.local/bin/rendercv render <yaml>`) — see `/generate-cv` and `/apply` SKILL.md for the full pipeline. Reference YAML: `output/example-ventures/042826-cos-example.yaml`. Theme: `framework/cv-themes/tuck-mbb.yaml`.

`todo_write.py` accepts `--repo-root` anywhere; `pipe_write.py` and `networking_write.py` require it before the subcommand.

**Email drafts.** Use `tools/open_draft.py` (Google MCP lacks draft-creation permissions). Write `tools/.pending-draft.txt`:

```
TO: recipient@example.com
CC: optional@example.com
SUBJECT: Subject line
BODY:
Email body here
```

`CC:` is optional (omit the line entirely if unused). Then `PYTHONIOENCODING=utf-8 python3 tools/open_draft.py` opens Gmail compose pre-filled.

**Post-interview workflow:**
1. `data/company-notes/<slug>.md` — call intel, newest at top
2. `pipe_write.py` — stage, next-action, notes
3. `networking_write.py log` — interaction
4. `todo_write.py add` — follow-up task
5. `tools/open_draft.py` — thank-you email
6. If a mock session preceded the call: update `coaching/progress-recruiter/`

**Gotchas:**
- Filter separator-row noise from script output: `[e for e in entries if e.get("task") != "---"]`.
- Edit-safety hook (`.claude/settings.json`) runs `tools/check_edit_safety.py` on every `.md` Edit.
- **Never create, truncate, or `touch` a file in `tools/launchd/logs/` from a Claude Code Bash call.** Files created that way inherit a `com.apple.provenance` xattr that launchd cannot open, so the job dies at setup with `EX_CONFIG` (78) **before the script runs** — nothing is logged to explain it. All 8 jobs sat dead for ~18 hours this way on 2026-08-11. Fix: `rm` the log and let launchd recreate it; `xattr -d` reports success without removing the tag, and `ls -l@` is the only reliable check. Log rotation must delete, never truncate. Diagnose with `launchctl print gui/$(id -u)/<label> | grep -i "last exit code"` — a `runs` count far below what `StartInterval` implies is the tell. Full trace: `memory/reference_launchd_ex_config_provenance_xattr.md`.

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

The (now-historical) n8n setup (`tools/run_n8n.bat`, dashboard at localhost:5678) was replaced by launchd ~2026-04-28; n8n binaries may still exist but no jobs run there.

## Memory Hygiene

**MEMORY.md is loaded every conversation — keep it to Critical Context + the Topic Shards router only** (2026-07-08 7-shard restructure; currently ~27 lines / 4KB). It is a router, not the index — do not append general index entries directly to it.

**Keep in MEMORY.md's Critical Context block (small, always-visible, never depend on recall):** employment status, family contacts, active hard-rule-DUE items. Nothing else belongs here — active search context, architectural patterns, unfixed bugs, and user preferences all route to the matching `memory/index-<topic>.md` shard (outreach / coaching / research / tools / system / personal / projects) instead.

**Archive to `memory/archive-YYYY-MM.md`** (from whichever shard the entry lives in) when:
- Skill change / bug fix / migration is completed and merged (codebase is source of truth)
- Search lead resolved (move with outcome)
- "New feature" note has been stable >2 weeks
- Session-specific reminders past their date

**Before deleting the source file on archive, update citing artifacts.** `grep -rl <entry-slug> .claude/skills/ CLAUDE.md docs/` — if anything still cites it, either repoint the citation to the archive rollup or leave a one-line stub file that points to `[[archive-YYYY-MM]]`. A deleted memory file with live citations is a recall dead-end (fable-audit 2026-07-07 #1: 6 skill citations stranded this way).

Keep each shard under ~24KB; if one overflows, split it further rather than letting it grow past the read budget again.

## Style

See `framework/style-guidelines.md` for tone, language, CV format.
