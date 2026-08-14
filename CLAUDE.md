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
- **Name the scope in the same sentence as the conclusion, or do not state the conclusion.** Every ratio, percentage, or "N of M" is reported with its denominator AND how that denominator was obtained. Three shapes, one defect: (1) **scope narrower than the claim** — scanning 64 of 883 transcript files and reporting a corpus-wide percentage; (2) **premise never checked** — asserting the mechanism ("recall injection is how the corpus is consulted") then reasoning confidently from it, when it measured 2 against 290; (3) **cause inferred from a count** — "promoted rules are read 69% vs 38%," true and almost entirely an artifact of having read those files *in order to* promote them the same day (controlled: 11% vs 5%). This is worse than being obviously wrong: the number is real and the sentence is confident, so it propagates into files a future session trusts. Before any ratio, check the glob's own coverage (`find | wc -l` against the directory you believe you searched). **When a new number contradicts a number you already have, the contradiction IS the finding — reconcile before reporting either.** **Fired 5x on 2026-08-13 alone**, the last two hours after three sibling rules about it were written and still sitting `promoted: no`. See `memory/feedback_name_the_scope_before_stating_the_conclusion.md`.
- **Investigate before answering when factual claims about code, files, or data are involved.** Never speculate about a file, function, configuration, or data record you have not opened in this session. If the user references a specific file/skill/hook/data row, read it before answering. Memory and prior-session knowledge can be stale — the source is canonical, memory is a pointer. Composes with the absence-assertion rule above: stating "X exists" and "X doesn't exist" both require this-session evidence. Origin: Anthropic prompting guide (2026-05-28) + 13+ verify-* family rules. See [[verification-umbrella]] (`framework/verification-umbrella.md`, the Family L composite, built 2026-06-01).
- **Run a grey-area pass before proposing any multi-step plan.** Trigger phrases: "let's plan X" / "what's your approach" / "propose a plan for Y" / "before we start" / phase boundaries inside ongoing sessions / about to invoke `EnterPlanMode` or any planning skill (`/gsd-plan-phase`, `/gsd-explore`, `/gsd-discuss-phase`). The pass = (1) enumerate grey areas with leans + alternatives + cost-of-default, (2) ask explicitly "are there others I'm missing?", (3) WAIT for responses, (4) draft the plan only after grey areas resolve. **(0) For any plan costing >2 hours of Nick's time, first name which memory rules govern the domain and check the plan against each: "Rule X says Y. My plan does Z. Does Z respect Y?"** A codified rule does not bind unless it is explicitly checked against the next plan in the same domain — that check is what makes the corpus load-bearing rather than decorative. Per `memory/feedback_precheck_plans_against_memory_rules.md`. The pass is mandatory even when context feels fresh and the plan feels obvious — "feels obvious" is the LLM intuition this rule exists to override. Cost: ~2 min. Cost of skipping: a wrong-direction plan compounds across every downstream commit. Origin: 2026-05-22 multi-edit cascade + 2026-05-28 standing-protocol elevation. See `memory/feedback_push_back_on_grey_areas_before_starting.md`.

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
3. **Add a one-line index pointer** (`- [Title](feedback_*.md) — terse hook`). Since the 2026-07-08 restructure (re-split to 11 shards 2026-08-13), `~/.claude/projects/.../memory/MEMORY.md` itself holds ONLY Critical Context (facts that must never depend on recall — employment status, family contacts, active hard-rule-DUE items) plus the Topic Shards router; it is NOT the index anymore. Check MEMORY.md's Topic Shards router table and add the pointer to the matching `memory/index-<topic>.md` shard (outreach / coaching / research / tools / hooks / repo-ops / agents / verification / system / personal / projects — 11 shards since the 2026-08-13 split). Only add directly to MEMORY.md's Critical Context block if the fact itself belongs there. Keep each shard under ~24KB; archive on overflow per [[project_memory_directory_structure]].
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
- **Name the target surface AND when Nick last ran it (mandatory before any skill-tier promotion).** A rule anchored to a surface that never fires is **worse than an unpromoted rule**: the promotion flips `promoted: yes`, the rule leaves the backlog, and the detector stops surfacing it. Rank surfaces by whether they need Nick at all: **launchd job > hook > CLAUDE.md (auto-loaded) > `/standup` > a skill he invokes occasionally > a skill he rarely invokes**. `/standup` yes; `/weekly-review` and `/memory-refresh` no. If the only sensible home is a rarely-run skill, land it there but say so plainly and do NOT treat it as closed. Origin: 2026-08-13, two promotions landed on `/weekly-review` Step 5c and `/memory-refresh` Step 3a hours before Nick said *"I dont run weekly review reliabely"* — correct content, dead surface. See `memory/feedback_verify_the_surface_fires_before_anchoring_to_it.md` and `memory/user_nick_invokes_standup_not_weekly_review.md`.
- **Skill-tier promotion:** edit the relevant skill SKILL.md to make the rule structurally enforced (mandatory step, not implicit context).
- **Hook-tier promotion:** add a check_*.py hook in `tools/` and wire it into `.claude/settings.json`. **PreToolUse: BLOCK (exit 2) or don't build it** — an exit-0 WARN goes to stderr, which Claude Code never surfaces. Too soft for BLOCK? Use a `Stop` hook, pre-commit, or a skill step. Per [[feedback_warn_vs_block_hook_design]] (its "default to WARN" form is **superseded**). Scaffold from `tools/HOOK_AUTHORING.md` (command-position regex + quote-stripping + clean/block tests + mandatory live smoke) so the new hook doesn't repeat the command-position-not-substring blind-spot class.
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

### Decisions, accomplishments, and personal-exploration files

> **Moved 2026-08-14 → [`docs/data-file-conventions.md`](docs/data-file-conventions.md)**
> (verbatim; relocated to hold this file under its 40 KB always-loaded budget).
>
> **Read it before:** writing to `data/decisions.md` or `data/accomplishments.md` ·
> routing a `/remember` capture between those logs · creating a reflection, workbook,
> therapy doc, or identity file · deciding whether a new file takes a `YYYY-MM-DD-` prefix.
>
> **It covers:** the two append-only logs and their boundaries; the four kinds of personal
> exploration and which get date prefixes; the therapy two-tier pattern; the reflections
> two-voice pattern.

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

**Tool tables** → `docs/tools-reference.md`. Read **before invoking any `tools/*.py` script** (argument order, flag placement, `--repo-root` position), before touching `tools/launchd/`, or when a gitignored private config file appears to be missing. Contains: atomic write scripts, launchd background jobs, private local config, multi-agent workflow templates.

**CV PDFs use RenderCV** (`~/.local/bin/rendercv render <yaml>`) — see `/generate-cv` and `/apply` SKILL.md for the full pipeline. Reference YAML: `output/example-ventures/042826-cos-example.yaml`. Theme: `framework/cv-themes/tuck-mbb.yaml`.

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

The (now-historical) n8n setup (`tools/run_n8n.bat`, dashboard at localhost:5678) was replaced by launchd ~2026-04-28; n8n binaries may still exist but no jobs run there.

## Memory Hygiene

**MEMORY.md is loaded every conversation — keep it to Critical Context + the Topic Shards router only** (2026-07-08 restructure, re-split to 11 shards 2026-08-13; currently ~8.5KB). It is a router, not the index — do not append general index entries directly to it.

**Keep in MEMORY.md's Critical Context block (small, always-visible, never depend on recall):** employment status, family contacts, active hard-rule-DUE items. Nothing else belongs here — active search context, architectural patterns, unfixed bugs, and user preferences all route to the matching `memory/index-<topic>.md` shard (outreach / coaching / research / tools / system / personal / projects) instead.

**Archive to `memory/archive-YYYY-MM.md`** (from whichever shard the entry lives in) when:
- Skill change / bug fix / migration is completed and merged (codebase is source of truth)
- Search lead resolved (move with outcome)
- "New feature" note has been stable >2 weeks
- Session-specific reminders past their date

**Check CLASS before archiving, not just the ARCHIVE flag.** A file flagged `canonical` is safe to archive ONLY if it is pure MACHINERY (canon target `-`). If it is HYBRID or PRINCIPLE, the flag means only the *machinery half* shipped — the portable kernel is still in the file and archiving strands it. Join each candidate against `output/analysis/061226-memory-sort-results.md` (`FILE | CLASS | CANON | ARCHIVE`); route HYBRID/PRINCIPLE to "hold for principles promotion." **A file absent from that dataset HOLDS** — it classified 330 files on 2026-06-12 and the corpus is larger now, so a lookup miss means *unclassified*, never *safe*. Promotion and archival are separate decisions: never flip `promoted:` and archive in the same breath, which is the coupling that caused this. Fail-safe direction: an un-archived file costs shard bytes, a wrongly-archived hybrid costs the kernel. This guard lives here, not only in `/memory-refresh` Step 3a, because archiving happens in any hygiene pass and that skill is rarely invoked. Full rule: `memory/feedback_dont_archive_hybrid_before_kernel_promoted.md`.

**Before deleting the source file on archive, update citing artifacts.** `grep -rl <entry-slug> .claude/skills/ CLAUDE.md docs/` — if anything still cites it, either repoint the citation to the archive rollup or leave a one-line stub file that points to `[[archive-YYYY-MM]]`. A deleted memory file with live citations is a recall dead-end (fable-audit 2026-07-07 #1: 6 skill citations stranded this way).

Keep each shard under ~24KB; if one overflows, split it further rather than letting it grow past the read budget again.

## Style

See `framework/style-guidelines.md` for tone, language, CV format.
