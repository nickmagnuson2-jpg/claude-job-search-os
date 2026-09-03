# Data File Conventions

**Origin:** moved verbatim out of `CLAUDE.md` on 2026-08-14 to hold that always-loaded
file under its 40 KB budget. Nothing was reworded, summarized, or dropped in the move.

## When to read this

- Before writing to `data/decisions.md` or `data/accomplishments.md`
- When routing a `/remember` capture between those two logs
- Before creating a reflection, workbook, therapy doc, or identity file
- When deciding whether a new file gets a `YYYY-MM-DD-` prefix
- Before writing to `data/profile.md`, `data/professional-identity.md`, or `data/goals.md`
- When routing the output of a `data/workbooks/*.md` exercise
- Before creating or editing a file in `data/projects/`

## What is NOT here

The sealed-folder prohibition still lives in the `CLAUDE.md` Hard Rules and stays
always-loaded. What appears below is its elaboration, never the rule itself.

---

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


---

**Origin, second move:** the three sections below were moved verbatim out of `CLAUDE.md`
on 2026-09-03, same reason as the 2026-08-14 move above: that file had reached its 40 KB
always-loaded budget. Nothing was reworded, summarized, or dropped. The prohibition
*"Never use a CV bullet as an interview answer or a spoken story as a CV bullet"* was
deliberately LEFT in `CLAUDE.md` — it is phrased as a rule, so it stays always-loaded.

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

### Projects

`data/projects/*.md` follows `framework/templates/project.md`: Period, Role, Client, Industry, Location, Type, Description, Responsibilities, Key Achievements, Technologies, Tags.

Type values: `flagship` | `consulting` | `contract` | `employment` | `co-founded` | `internship` | `side-project`.

