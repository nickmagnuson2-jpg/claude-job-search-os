# Data File Conventions

**Origin:** moved verbatim out of `CLAUDE.md` on 2026-08-14 to hold that always-loaded
file under its 40 KB budget. Nothing was reworded, summarized, or dropped in the move.

## When to read this

- Before writing to `data/decisions.md` or `data/accomplishments.md`
- When routing a `/remember` capture between those two logs
- Before creating a reflection, workbook, therapy doc, or identity file
- When deciding whether a new file gets a `YYYY-MM-DD-` prefix

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
