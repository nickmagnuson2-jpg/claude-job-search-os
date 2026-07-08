Last updated: 2026-06-15

# Claude Code Job-Search OS

An end-to-end job search operating system built on [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Built by Nick Magnuson during a 2026 job search.

35 slash-command skills, a structured data layer, a verification hook stack, and a library of methodology documents. Runs against an Obsidian markdown vault. No code required to use, skills are defined in markdown and executed by Claude.

**New here?** Start with [why this exists](docs/why.md) for the value and design, [getting started](docs/getting-started.md) to run it from zero, or [workflows](docs/workflows.md) to see the end-to-end flows.

---

## Daily Drivers

Five skills account for 75% of invocations (Apr-May 2026 usage audit):

| Skill | What it does | Invocations |
|---|---|---|
| `/wispr` | Pull Wispr Flow voice dictations into the session; route to reflections, inbox, or active workflow | 43 |
| `/remember` | Route a mid-session note to the right data file, contact, pipeline, company notes, inbox | 28 |
| `/draft-email` | Draft outgoing email with voice-anchored tone matching, Substance-Provenance Audit, and Gmail compose integration | 20 |
| `/standup` | Morning briefing: pipeline health, today's top 3 actions (scheduled interviews pinned first), pending outreach, suggested priority | 14 |
| `/checkout` | End-of-day close-out: daily log, tomorrow's top 3, velocity snapshot, accomplishment candidates, and an automatic private-repo data backup | 14 |

---

## Full Skill Catalog

35 skills total. See [docs/usage.md](docs/usage.md) for full argument syntax and worked examples.

### Daily Operations
`/standup` `/checkout` `/todo` `/personal-todo` `/weekly-review` `/act` `/pipe` `/dashboard`

### Capture and Memory
`/wispr` `/remember`

### Research and Analysis
`/research-industry` `/research-company` `/discover-companies` `/scan-companies` `/scan-jobs` `/analyze` `/learn`

### Application Materials
`/generate-cv` `/apply` `/cover-letter` `/review-cv` `/review-cv-deep` `/scan-contacts`

### Outreach and Networking
`/cold-outreach` `/follow-up` `/draft-email` `/networking`

### Interview Coaching and Preparation
`/prep-interview` `/voice-export` `/debrief` `/critique-plan`

### Setup (one-time)
`/import-cv` `/extract-identity` `/setup-goals`

### Maintenance
`/audit-pii` (pre-commit public-repo PII gate)

### Personal Commands (global)

Two personal orientation/processing commands live in `~/.claude/commands/` (global, not part of the 35 project skills): `/reflect` (in-the-moment processing: six questions to a dated reflection) and `/my-world` (daily orientation plus a gated longitudinal three-axis synthesis over your reflections, written to `data/reflections/_longitudinal.md`).

---

## Architecture

Four layers. The hook layer is what makes this distinct from other personal-OS setups.

### 1. Data layer (`data/`)

Structured markdown files that are the source of truth for everything generative skills produce. Core files: `profile.md`, `goals.md`, `professional-identity.md`, `job-pipeline.md`, `job-todos.md`, `networking.md`, `outreach-log.md`. Append-only chronological logs (newest first): `decisions.md` (strategic search decisions) and `accomplishments.md` (process wins). Supporting files: `data/projects/*.md` (one per role or engagement), `data/company-notes/<slug>.md`, `data/people/<slug>.md` (per-person relationship dossiers for active relationships), `data/reflections/` (dated reflection files plus `_longitudinal.md`, the Claude-voice longitudinal synthesis).

Data files accumulate over time. A file like `data/role-shape-engagement-lead.md` starts as an interview hypothesis, gets updated after each call, and becomes the living spec that every downstream skill reads from. The value compounds because the files persist across sessions.

### 2. Skill layer (`.claude/skills/`)

35 slash commands defined as markdown files Claude Code reads and executes. Each skill specifies: steps, context-loading (which data files to read), allowed-tools (what write operations are permitted), and failure conditions. Skills are the orchestration surface, they read data, call tools, and write outputs following the methodology docs.

### 3. Hook layer (`tools/check_*.py`)

PreToolUse and PostToolUse hooks wired into `.claude/settings.json`. They enforce rules structurally on every relevant tool call, no manual invocation, no behavioral discipline required.

| Hook | Trigger | What it enforces |
|---|---|---|
| `check_voice_pure.py` | PreToolUse on Write/Edit | Blocks Claude-voice prose in dated reflection files |
| `check_email_via_skill.py` | PreToolUse on Write/Edit | Blocks email-shaped content written outside the skill flow |
| `check_no_confabulation.py` | PreToolUse on Write/Edit | Blocks placeholder tokens in shippable artifacts |
| `check_data_projects.py` | PreToolUse on Write/Edit to `data/projects/` | Surfaces solo-agency overclaim verbs in Key Achievements |
| `check_living_log_purity.py` | PreToolUse on Write/Edit/MultiEdit | Blocks direct writes to living-log files, only the atomic script may write these |
| `check_draft_voice.py` | PreToolUse on Bash `open_draft.py` | Scans draft for voice anti-patterns before Gmail compose opens |
| `check_todo_write_kwargs.py` | PreToolUse on Bash | Blocks kwarg-style invocation of the atomic to-do writer |
| `check_edit_after_mutation.py` | PreToolUse on Edit/MultiEdit | Read-state guard: warns when a file changed on disk since last read, or was never read this session |
| `check_edit_safety.py` | PostToolUse on Edit | Warns on long-row markdown tables; hard-stops on write-only files |
| `check_plan_partner_critique.py` | PostToolUse on Write/Edit | Reminds to run a McKinsey-critical-advisor critique on large plan docs |
| `check_script_error_logged.py` | PostToolUse on Bash | Auto-appends to friction log when a script returns a JSON error, or when any `python3` invocation crashes with a traceback |
| `check_bare_python.py` | PreToolUse on Bash | Blocks a bare `python` in command position, requiring `python3` (anchored to command position so it never trips on the token inside strings or filenames) |
| `check_changelog_currency.py` | Stop | Warns once per HEAD when commits since the last `docs/CHANGELOG.md` edit touched `tools/`, `.claude/skills/`, `framework/`, settings, or `requirements.txt` without a changelog update |

Friction capture runs on a dedicated three-hook chain rather than a single hook (PostToolUse hooks never fire on tool errors, a documented Claude Code limitation). `check_script_error_logged.py` (PostToolUse on Bash) catches `tools/*.py` scripts that return a JSON error, plus any `python3` invocation (inline heredocs and skill helpers included) that crashes with a traceback. `log_tool_failure.py` (PostToolUseFailure on Bash/Edit/Write/MultiEdit) is the primary capture for outright tool-call failures. `scan_transcript_failures.py` (Stop hook) scans the session transcript at turn-end for harness-level errors the other two cannot see, for example "file not read" Edit rejections. All three append to `memory/friction-log.md`; entries that fire 3+ times trigger a structural patch.

### 4. Framework layer (`framework/`)

Methodology docs that skills reference instead of embedding rules inline. Key files: `application-workflow.md` (16-point CV quality checklist, tailoring rules), `interview-workflow.md` (coaching session protocol), `outreach-guide.md` (email frameworks, channel limits, quality gate), `voice-reference.md` (empirical voice patterns from a 37-email corpus), `style-guidelines.md` (tone, language, CV format), `answering-strategies/` (6 files covering recruiter call techniques), `problem-solving-mckinsey.md` (7-step structured problem-solving), `slide-craft-mckinsey.md` (visual synthesis and communication craft).

Skills reference framework docs by path. When a rule needs updating, you edit one framework file and all skills that reference it pick up the change.

---

## Verification Mechanics: The Substance-Provenance Audit

Every outgoing email draft (`/draft-email`, `/follow-up`, `/cold-outreach`) labels each substantive sentence before presenting it:

- **N**, Nick-dictated (verbatim from this session's voice or text input)
- **C**, Nick-corpus (drawn from prior sent messages or `voice-reference.md`)
- **I**, Claude-inferred (synthesized from cited research)
- **G**, Claude-generated (no Nick source, pure model output)

**G in self-positioning, bridge, or story slots halts the draft.** The skill stops and asks for the spine before continuing. This is a structural check on the tool-call surface, it cannot be bypassed by drafting in chat.

---

## What Got Pruned

Of the 31 skills in the catalog at the time of the Apr-May 2026 usage audit, 20 saw regular use. 11 were low-invocation. (The catalog has since grown to 35.) That's not a failure of design, the system was built broadly and the daily-driver skills emerged from actual use, not from planning.

Three low-invocation skills are legitimate one-shot tools: `/import-cv`, `/setup-goals`, and `/extract-identity` run once at setup and rarely again. The rest are skills that made sense in theory and didn't get pulled in practice: `/scan-contacts` (LinkedIn scanner, useful but rarely needed at the daily cadence), `/critique-plan` (six-agent plan critique, valuable when needed, infrequently needed), `/dashboard` (Textual terminal UI for the pipeline, replaced by `/pipe` for quick checks), `/act` (autonomous task execution, useful in theory, in practice the manual cadence worked better), `/review-cv-deep` (six-reviewer parallel CV analysis, pulled when actively applying, not in between).

The system is opinionated. The five daily drivers reflect what a job search actually runs on: capturing context as it comes in, surfacing the right information each morning, and writing outbound communication that sounds like you.

---

## Adjacent Use Cases

The architecture separates job-search context from personal-OS context by design. The job-search vault (`job-search/data/`) and the personal vault (`personal/data/`) have identical internal shapes, both have `data/`, `data/reflections/`, `data/inbox.md`, and the same routing patterns. Content routes by synthesis destination, not by topic.

The patterns in this repo, skill catalog, hook stack, atomic write scripts, friction log, framework docs, apply to any domain where you want a Claude-driven knowledge system that compounds over time. The examples here are job-search-specific. The architecture is not.

If you are building a personal-OS on top of Claude Code (not job-search-specific), the framework files in `framework/` and the hook stack in `tools/check_*.py` are the most transferable parts.

---

## Getting Started

> For a guided walkthrough with a demo profile and a first worked example, see [docs/getting-started.md](docs/getting-started.md). The quick version:

**Prerequisites:** [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed (Max subscription recommended, parallel-agent research and deep CV reviews are token-intensive). Python 3.8+ for PDF features and atomic write scripts.

### Step 1, Clone and install

```bash
git clone https://github.com/nickmagnuson2-jpg/claude-job-search-os.git
cd claude-job-search-os
pip install -r requirements.txt  # optional, only needed for PDF generation
```

Open the repo in Claude Code.

### Step 2, Try it first (optional)

The repo ships with a fictional demo profile in `examples/` so you can explore features before importing your own data:

```bash
cp -r examples/data/* data/
cp -r examples/output/* output/
```

### Step 3, Import your data

```
/import-cv path/to/your-cv.pdf
/extract-identity
/setup-goals
```

`/import-cv` extracts structured data into `data/` files. Run it multiple times from different CVs, it's additive. `/extract-identity` runs a guided coaching conversation to produce `data/professional-identity.md`. `/setup-goals` reads your identity doc and writes `data/goals.md`. Both are required before generative skills run, skills will stop and tell you to complete setup if either file is missing.

### Step 4, Start using it

```
/standup                          # morning briefing
/research-company "Acme Corp"     # company dossier before a call
/draft-email "thank-you to Jamie Torres after coffee chat"
```

Run `/standup` each morning. When you have a target company, run `/research-company` before reaching out or before a call. Use `/draft-email`, `/cold-outreach`, or `/follow-up` for all outbound, email written outside these skills bypasses the voice and provenance hooks.

---

## Repository Layout

```
CLAUDE.md              Project instructions, loaded every Claude Code session
framework/             Methodology docs: workflows, coaching, outreach, voice, style, templates
data/                  Your professional data and search ops data (private once filled)
  ├─ company-notes/    Free-form notes on target companies, newest first
  ├─ projects/         One .md per role or engagement, source-of-truth for CV generation
  ├─ people/           Per-person relationship dossiers for active relationships
  ├─ reflections/      Dated reflection files (frozen) + _longitudinal.md (Claude-voice synthesis)
  ├─ workbooks/        Reusable frameworks, updated over time
  ├─ decisions.md      Append-only log of strategic search decisions, newest first
  └─ accomplishments.md  Append-only log of job-search process wins, newest first
coaching/              Coaching session outputs and progress tracking
.claude/skills/        35 slash-command skill definitions
tools/                 Python scripts: atomic writes, PDF generation, background automation
output/                Generated CVs, dossiers, cover letters, outreach archives
  └─ <company-slug>/   One folder per named entity; all related artifacts inside
memory/                MEMORY.md (auto-loaded router + critical context), index-<topic>.md shards, friction-log.md, lessons.md
docs/                  Usage guide, changelog, navigation index
examples/              Fictional demo profile for trying features before importing your own
```

---

## What Is Not In This Repo

Personal data files are gitignored. When you fill in your data, none of it is checked in:

- `data/profile.md`, facts: career history, education, skills, availability
- `data/goals.md`, search thesis, target criteria, compensation, phase, weekly focus
- `data/professional-identity.md`, strengths, growth edges, work style, values, narrative patterns
- `data/decisions.md` and `data/accomplishments.md`, chronological strategic and process logs
- `data/project-background/`, sensitive captures, never in any external-facing artifact
- `output/`, all generated artifacts (CVs, dossiers, cover letters)
- `memory/MEMORY.md` and `memory/friction-log.md`, session-level context
- `coaching/`, coaching session outputs and progress tracking

What you see in the repo is the system. Your data is separate.

### The two-repo pattern (keep the system public, your data private)

This repo holds the *system*. Your *data* belongs somewhere only you can see. Two complementary layers make that work:

- **Gitignore** keeps personal files out of this public repo. Nothing in `data/`, `output/`, `coaching/`, or your memory is ever committed here.
- **A separate private repository** is where those same files actually live and stay versioned. An end-of-day step (wired into `/checkout`) commits your data and pushes it to that private repo, so your search history is durable without ever touching the public one.

The public repo also guards itself against leaks. A PreToolUse hook (`tools/check_public_pii.py`) blocks real names and target-company names from landing in any public file (tests, skills, docs), and the `/audit-pii` skill runs a deeper semantic check before you commit. Forking it? Point the backup at your own private repo and you get the same split: share the system, keep the search to yourself.

---

## Pointer Block

| Doc | What it covers |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | Zero-to-first-output walkthrough, with a demo profile to try first |
| [docs/why.md](docs/why.md) | The value and the design bet: what it does for you, who it is for, what it is not |
| [docs/workflows.md](docs/workflows.md) | End-to-end flows: the daily loop, applying, interviewing, outreach |
| [docs/usage.md](docs/usage.md) | Full how-to: all 35 skills with argument syntax, worked examples, PDF pipeline, hook override flags |
| [docs/faq.md](docs/faq.md) | Quick answers: setup, privacy, day-to-day, voice, forking, troubleshooting |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Dated history: what was built, what was fixed, what was learned |
| [docs/README.md](docs/README.md) | Navigation index: all docs organized by audience (Claude, new users, design) |
| [CLAUDE.md](CLAUDE.md) | Project instructions and hard rules loaded every session |

---

## License

- **Code** (`tools/`, `.claude/skills/`, `CLAUDE.md`): [MIT](LICENSE)
- **Written methodology** (`framework/`, coaching templates): [CC BY 4.0](LICENSE)

Your personal data in `data/` is yours. The licenses cover only the framework and tooling.

Originally created by [Raphael Otten](https://www.linkedin.com/in/raphaelotten/) as an AI interview coaching toolkit. Extended into a full job search OS by [Nick Magnuson](https://www.linkedin.com/in/nickmagnuson/).
