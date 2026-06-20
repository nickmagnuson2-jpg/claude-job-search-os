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
- **Never assert absence without running the check this session and naming the scope.** "X doesn't exist" / "Y didn't happen" / "not found" / "no result" claims about codebase state, file contents, data records, or memory entries MUST be backed by a grep/ls/Read in the current session AND the assertion must state what was checked (e.g., "no `check_skill_shadow.py` in `tools/` per `ls`" — not bare "the hook doesn't exist"). Memory and prior-session knowledge are not sufficient — they decay. Asserting absence from memory creates ghost facts: a future audit cycle treats Claude's "not built" as authoritative and skips the verification, compounding the original error. Origin: `feedback_llm_verification_system` Rule #1 + Rule #13 (scope-limited query). Family-N extends to data-state absence claims (e.g., "you haven't applied yet" — see `feedback_pipeline_applied_status_must_be_user_confirmed`, 5/28 ghost-row incident).
- **Investigate before answering when factual claims about code, files, or data are involved.** Never speculate about a file, function, configuration, or data record you have not opened in this session. If the user references a specific file/skill/hook/data row, read it before answering. Memory and prior-session knowledge can be stale — the source is canonical, memory is a pointer. Composes with the absence-assertion rule above: stating "X exists" and "X doesn't exist" both require this-session evidence. Origin: Anthropic prompting guide (2026-05-28) + 13+ verify-* family rules. See [[verification-umbrella]] (`framework/verification-umbrella.md`, the Family L composite, built 2026-06-01).
- **Run a grey-area pass before proposing any multi-step plan.** Trigger phrases: "let's plan X" / "what's your approach" / "propose a plan for Y" / "before we start" / phase boundaries inside ongoing sessions / about to invoke `EnterPlanMode` or any planning skill (`/gsd-plan-phase`, `/gsd-explore`, `/gsd-discuss-phase`). The pass = (1) enumerate grey areas with leans + alternatives + cost-of-default, (2) ask explicitly "are there others I'm missing?", (3) WAIT for responses, (4) draft the plan only after grey areas resolve. The pass is mandatory even when context feels fresh and the plan feels obvious — "feels obvious" is the LLM intuition this rule exists to override. Cost: ~2 min. Cost of skipping: a wrong-direction plan compounds across every downstream commit. Origin: 2026-05-22 multi-edit cascade + 2026-05-28 standing-protocol elevation. See `memory/feedback_push_back_on_grey_areas_before_starting.md`.

## Tool Usage Conventions

- **Parallel tool calling is the default for independent operations.** If you intend to call multiple tools and there are no dependencies between them, make ALL independent tool calls in the same response — do not serialize them. This is not optional for read-only investigation (grep, ls, Read, glob) — sequencing read operations one-per-response wastes tokens and turns. Reserve sequential calls for genuine data dependencies (output of A feeds input of B).
- **Subagent overuse guard.** Use subagents (Task tool) when tasks can run in parallel, require isolated context, or involve independent workstreams. For simple tasks, sequential operations, single-file edits, or quick greps — work directly. The cost of spawning a subagent (context restart, isolation overhead, summary loss) is real; don't pay it for work you can do in 1-2 tool calls. Origin: Anthropic prompting guide (2026-05-28).
- **Public-repo PII gate.** This repo is PUBLIC; public artifacts (`tests/`, `.claude/skills/`, `framework/`, `docs/`, `tools/*.{py,md,sh}`, top-level `*.md`) must use generic placeholder examples, never real contacts/pipeline-target companies. Two layers: (1) `tools/check_public_pii.py` PreToolUse hook (always-on, BLOCKs known real tokens from the gitignored `tools/.pii-denylist.txt`); (2) `/audit-pii` skill — run before committing/pushing any public-file change for the semantic subagent pass that catches new names the denylist misses. Regenerate the denylist via `tools/gen_pii_denylist.py` after adding contacts/pipeline rows. Origin: 2026-06-11, `feedback_generalize_examples_in_public_artifacts`.

## Self-Improvement Loop

After any user correction, run the **full tiered protocol** — not just a lessons.md row. The tier ladder (per [[feedback_llm_self_policing_fails]]: memory < skill < hook < independent reviewer) determines *where* the rule lives; the protocol below determines *how* it gets there.

### Step 1 — Capture at memory tier (always)

1. **Create a dedicated auto-memory file** at `~/.claude/projects/-Users-mag-Documents-Obsidian-30-projects-job-search/memory/feedback_<kebab-case-slug>.md` with frontmatter (`name`, `description`, `metadata.type: feedback`) and a body containing:
   - **Rule:** the new rule, plainly stated
   - **Why:** the underlying principle
   - **If the rule is about absence-assertion, verification-before-claim, or hook/skill effectiveness**, default to skill or hook tier (not memory tier), and check the family-N count across [[verification-umbrella]] — these failures compose silently and memory-tier capture alone has a documented track record of not preventing recurrence.
   - **Origin:** the specific incident that triggered the correction (date + concrete trace)
   - **How to apply:** the operational test ("when X happens, do Y")
   - **Connections:** `[[wikilinks]]` to related memory rules — link liberally even if the target doesn't exist yet
   - **Tier ladder:** name the next promotion target (skill / hook / framework) and the trigger condition
2. **If the correction refines an existing rule**, update that file instead of creating a duplicate. Add a dated supplement section.
3. **Update `~/.claude/projects/.../memory/MEMORY.md`** with a one-line index pointer (`- [Title](feedback_*.md) — terse hook`). Keep MEMORY.md under ~24KB; archive on overflow per [[project_memory_directory_structure]].
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

Scan `memory/MEMORY.md` (auto-loaded every session) and `memory/lessons.md` (skill-edit / data-ops / CV-gen sessions) at the start of relevant work. The MEMORY.md index is the primary loaded surface; full memory files load on demand via recall.

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
.claude/skills/    35 slash-command skill definitions
memory/            MEMORY.md (auto-loaded, <100 lines), lessons.md, archives
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

slug = person name lowercased, accents folded, spaces→hyphens (matches the `output/<slug>` convention, e.g. `first-last`). Created on demand via `/networking promote` (or `person_write.py create`), never auto-created for every contact: anti-sprawl by recruitment. The structured sections (Commitments / What I Owe / Touchpoints) are atomic dated appends via `person_write.py add-entry`; the freeform sections are edited via re-read + Write. Read-consumers: `/networking`, `/follow-up`, `/cold-outreach`, `/draft-email`, `/prep-interview` load `data/people/<slug>.md` when present. Template: `framework/templates/person.md`. Exemplar: `data/people/<slug>.md`. Origin: KAOS comparison E2 (built 2026-06-01).

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

**Therapy docs use a two-tier pattern, sealed in personal vault.** Per-session files (`YYYY-MM-DD-therapy-{therapist}-transcript.md`) are frozen and contain transcript + session-specific themes. The undated aggregate (`therapy-themes-job-search.md`) is the living cross-session synthesis. Both live at `~/Documents/Obsidian/30-projects/personal/data/therapy/` (relocated 2026-05-04 from this project's `data/project-background/` per the personal-vs-job-OS architecture; see `framework/personal-vs-job-os-architecture.md`). Same sealed-file rule applies: never appears in any external-facing artifact. When a new session adds themes that recur, refine, or contradict prior themes, promote them into the aggregate as a new dated supplement section; session-specific themes that don't generalize stay in the per-session file only.

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
- **Slide / communication craft** → `framework/slide-craft-mckinsey.md`. Ledes (insight vs. process), 30-second test, page anatomy (4 corners), 11-point quality checklist, 8 common feedback patterns, "kill empty verbiage" rules. Used when producing prep PDFs, dossiers, cover letters, and any artifact where the audience scans first.

## Tools & Environment

Python 3.8+. `pip install -r requirements.txt` for PDF features. **All `tools/*.py` scripts require `PYTHONIOENCODING=utf-8` prefix or they crash on Unicode.**

**Atomic write scripts** (return JSON):

| Script | Purpose |
|---|---|
| `todo_write.py` | add/done/withdraw/supersede/clear/sync `data/job-todos.md` (`supersede <prefix>` withdraws all open rows matching a prefix — keeps one live follow-up per contact) |
| `pipe_write.py` | add/update/remove `data/job-pipeline.md` (`--repo-root .` before subcommand) |
| `networking_write.py` | add/log/remove `data/networking.md`; `log` auto-detects a **received** reply (recipient → Nick) and flips the matching `data/outreach-log.md` row to `Replied`. Outbound phrasing ("Replied to her intro") no longer false-flips; override with `--reply-received` / `--no-reply-flip` |
| `remember_apply.py` | route notes to 8 destinations |
| `daily_stoic.py` | `--sync`/`--backfill` archive Daily Stoic meditations to `data/source-emails/daily-stoic/` (promo/digest filtered, 28 kept / 15 dropped on the seed corpus); `--mark-prompted <id>` records standup prompts. State: `tools/.daily_stoic_state.json`. Read-only Gmail; reuses gmail_fetch auth + sanitizer. |
| `act_apply.py` | pipeline-add / contact-add / notes-add for inbox routing |
| `person_write.py` | `create` (scaffold a `data/people/<slug>.md` dossier, idempotent) / `add-entry` (atomic newest-first dated append to commitments/owed/touchpoints) / `list`. `--repo-root` before subcommand. See `/networking promote`. |
| `projects_to_yaml.py` | `data/projects/*.md` → RenderCV experience YAML stubs (source-of-truth for CV experience entries) |
| `cv_merge_theme.py` | compose CV content YAML with `framework/cv-themes/tuck-mbb.yaml` → render-ready, standalone CV YAML |
| `md_to_pdf_doc.py` | prep-doc PDFs (cheat sheets, dossiers, prep packages) via weasyprint — Georgia, multi-page |
| `convert_pdfs.py` | extract text from PDFs in `files/` |
| `fetch_transcript.py` | YouTube transcript + oEmbed metadata → ExtractionBlock JSON; caches markdown to `data/source-transcripts/<id>.md`. Used by `/analyze` video branch. Dep: `youtube-transcript-api`. |

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

**Background automation (launchd, macOS-native).** Schedules live as plists in `tools/launchd/`. Install/manage with `bash tools/launchd/install.sh {install|uninstall|status}`. Logs at `tools/launchd/logs/`.

| Plist label | Schedule | Effect |
|---|---|---|
| `gmail-fetch` | Every 15 min | `gmail_fetch.py` → `inbox/` |
| `gmail-fetch-personal` | Every 15 min | `gmail_fetch.py` (personal Gmail label) → the personal vault's `inbox/` |
| `career-scan` | Daily | scans target company career pages for new matches |
| `alirohde-triage` | Daily 9:15 | `alirohde_nudge.py` cheap-check: no-op unless a new "Ali Rohde Jobs" Substack edition landed in `inbox/`; then writes `inbox/YYYYMMDD-alirohde-edition-NNN-triage.md` (review-gated → run `/scan-jobs <url>`). State: `tools/.alirohde_state.json`. |
| `granola-auto-debrief` | Every 3 hrs | `granola_auto_debrief.py` → persists transcript+summary pair via `granola_save.py` (sealed-aware), AND posts a `<!-- voice: cloud-generated -->` debrief snippet to `data/inbox.md` (skipped for therapy-classified calls) |

The (now-historical) n8n setup (`tools/run_n8n.bat`, dashboard at localhost:5678) was replaced by launchd ~2026-04-28; n8n binaries may still exist but no jobs run there.

## Memory Hygiene

MEMORY.md is loaded every conversation — keep under 100 lines.

**Archive to `memory/archive-YYYY-MM.md`** when:
- Skill change / bug fix / migration is completed and merged (codebase is source of truth)
- Search lead resolved (move with outcome)
- "New feature" note has been stable >2 weeks
- Session-specific reminders past their date

**Keep in MEMORY.md:** active search context, stable architectural patterns, known unfixed bugs, user preferences, critical personal context (employment status, framing rules).

## Style

See `framework/style-guidelines.md` for tone, language, CV format.
