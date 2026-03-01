# Changelog

All notable changes to this job search system are recorded here.
Format: newest entries at the top.

---

## 2026-02-28 — /critique-plan skill, /scan-contacts skill, todo_write.py, PDF + style fixes

### Added
- **`/critique-plan` skill** — six-agent plan critique + hybrid plan synthesis. Inserts a structured review step between Codex plan generation and Claude execution. Five analytical agents (completeness, risk/safety, codebase alignment, simplicity/scope, sequencing) run in parallel against the Codex plan; a sixth independent Claude planner receives only the stated goal (no Codex steps — no anchoring) to generate a clean-room plan. Synthesizes a diff table (Codex vs Claude) and an enhanced hybrid plan with all blockers resolved, gaps filled, and order corrected. Inline output only — no file written. Agents 1/2/3/6 use sonnet; agents 4/5 use haiku.
- **`/scan-contacts` skill** — LinkedIn contact scanner for a target company. Runs `tools/linkedin-scanner/scan.py` to fetch profiles, then ranks each contact on four dimensions: role proximity (hiring decision authority), education overlap, network connectedness, and industry fit. Outputs a ranked table and adds top contacts to `data/networking.md`.
- **`tools/todo_write.py`** — atomic mutation tool for `data/job-todos.md`. Handles `add`, `done`, `clear`, and `sync` without loading the full file into Claude's context. Outputs JSON. The `sync` command fast-paths out immediately if the pipeline Archived section is empty.

### Changed
- **`/todo` skill** — all mutation commands (add, done, clear, sync) now delegate to `tools/todo_write.py` via Bash. Direct file manipulation removed. Pipeline sync step rewritten to call `todo_write.py sync` instead of reading and rewriting the file manually.
- **`tools/md_to_pdf.py`** — major rewrite for 1-page CV output: switched from Helvetica to Calibri (registered via ReportLab TTFont from `C:/Windows/Fonts/`), tightened page margins (8mm/13mm), reduced line-height to 1.1, reduced body font-size to 8.5pt, tightened section spacing throughout.
- **`framework/style-guidelines.md`** — added Nick's CV formatting preferences: no em dashes or en dashes (use hyphens everywhere), comma separators for skills lists (not dots or bullets).
- **`CLAUDE.md`** — added `todo_write.py` to repo structure listing; updated Write-Only Files section to specify that mutations must use `todo_write.py`; added `todo_write.py` usage examples to Tools & Environment section.
- **`.claude/settings.local.json`** — added pre-approved WebFetch domains (luma.com, oceantechhackathon.org, sofarocean.com, propellervc.com, aquatic-labs.com) and pre-approved Bash patterns (`git add:*`, `PYTHONIOENCODING=utf-8 python:*`).

### Tests added
- `tests/scripts/test_linkedin_scanner_parser.py` — unit tests for LinkedIn profile parser
- `tests/scripts/test_linkedin_scanner_scan.py` — integration tests for scan workflow
- `tests/scripts/test_linkedin_scanner_unit.py` — unit tests for scanner core
- `tests/skills/SCAN_CONTACTS_TESTING.md` — manual testing guide for `/scan-contacts`

### Files changed
- `.claude/skills/critique-plan/SKILL.md` — new
- `.claude/skills/scan-contacts/SKILL.md` — new
- `tools/todo_write.py` — new
- `tests/scripts/test_linkedin_scanner_parser.py`, `test_linkedin_scanner_scan.py`, `test_linkedin_scanner_unit.py` — new
- `tests/skills/SCAN_CONTACTS_TESTING.md` — new
- `.claude/skills/todo/SKILL.md` — mutation commands rewired to todo_write.py
- `tools/md_to_pdf.py` — major rewrite (Calibri, tight spacing)
- `framework/style-guidelines.md` — Nick's CV formatting preferences added
- `CLAUDE.md` — todo_write.py docs added; Write-Only Files section updated
- `.claude/settings.local.json` — domain + bash permissions added

---

## 2026-02-26 — Edit tool safety: Write-only enforcement + PostToolUse hook

### Root cause
The Edit tool silently fails (returns success, no change) when `old_string` spans lines >~500 characters in markdown table files. No external linter is involved — this is intrinsic Edit tool behavior. Affected files: `data/job-todos.md` (543 chars max), `data/job-pipeline.md` (524 chars max), all `output/**/*.md` dossiers (up to 1,677 chars).

### Changed
- **7 skill `allowed-tools` fixes** — removed `Edit()` on risky files, keeping only `Write()`:
  - `todo/SKILL.md` — removed `Edit(data/job-todos.md)`
  - `pipe/SKILL.md` — removed `Edit(data/job-pipeline.md)`
  - `apply/SKILL.md` — removed `Edit(data/job-pipeline.md)`
  - `cover-letter/SKILL.md` — removed `Edit(data/job-pipeline.md)`
  - `generate-cv/SKILL.md` — removed `Edit(data/job-pipeline.md)`, `Edit(output/**)`
  - `remember/SKILL.md` — removed `Edit(data/job-pipeline.md)`, `Edit(output/**)`
  - `act/SKILL.md` — removed `Edit(data/job-todos.md)`, `Edit(data/job-pipeline.md)`, `Edit(output/**)`

### Added
- **`tools/check_edit_safety.py`** — PostToolUse hook script; warns when Edit is used on markdown files with rows >500 chars; hard-stops on known write-only files (`job-todos.md`, `job-pipeline.md`)
- **`.claude/settings.json`** — PostToolUse hook registration; triggers `check_edit_safety.py` after every Edit call on `.md` files

### Documented
- **`CLAUDE.md` Write-Only Files section** — lists the two affected data files and all output dossiers; explains the root cause; points to the hook

### Files changed
- `.claude/skills/todo/SKILL.md`, `pipe/SKILL.md`, `apply/SKILL.md`, `cover-letter/SKILL.md`, `generate-cv/SKILL.md`, `remember/SKILL.md`, `act/SKILL.md` — allowed-tools updated
- `tools/check_edit_safety.py` — new
- `.claude/settings.json` — new
- `CLAUDE.md` — Write-Only Files section added

---

## 2026-02-26 — /checkout + /apply skills, preprocessing scripts, token optimization

### Added
- **`/checkout` skill** — end-of-day close-out, bookend to `/standup`. Absorbs `/todo daily` entirely. Runs `todo_daily_metrics.py` to build today's snapshot, writes daily log entry, calculates streak/velocity, surfaces tomorrow's top 3 cross-referenced against the weekly review's Top 5 priorities.
- **`/apply` skill** — one-command apply bundle: fetches JD, runs CV generation logic, runs cover letter logic, and adds/updates the pipeline entry. Eliminates the 3-command apply flow.
- **5 preprocessing scripts** in `tools/` — each accepts `--target-date` and `--repo-root`, outputs JSON to stdout:
  - `todo_daily_metrics.py` — todos, daily log, pipeline snapshot, outreach, research, changelog (~2,300 tokens saved per `/checkout` run)
  - `pipeline_staleness.py` — per-stage staleness thresholds (Researching=7d, Applied=5d, Screening=5d, Interview=7d, Offer=3d)
  - `dossier_freshness.py` — detects dossiers by filename==parent pattern, classifies by freshness
  - `outreach_pending.py` — awaiting/overdue outreach, response rate calculation
  - `networking_followup.py` — infers follow-up due dates from free-text (next week, 3–5 biz days, explicit dates, default 14d)
- **28 pytest tests** in `tests/scripts/` — full coverage of all 5 scripts; `conftest.py` sets `PYTHONIOENCODING=utf-8` for Windows compatibility

### Changed
- **`/standup`** — Step 1 now runs `pipeline_staleness.py`, `outreach_pending.py`, `networking_followup.py` instead of reading 6 files and parsing manually; `allowed-tools` simplified to `Read(*)`
- **`/weekly-review`** — Steps 2/3/5 now use script JSON instead of manual file parsing; Step 1 calls 3 scripts + reads only 2 files; velocity (Step 4) reads from daily log (not raw todos); edge case note updated from `/todo daily` to `/checkout`
- **`/scan-jobs`** — Step 7b added: after scoring, surfaces shortlisted roles (≥80%) not already in pipeline and prompts to add them
- **`/todo`** — `daily` command removed; replaced with one-line redirect to `/checkout`
- **`prep-interview`, `generate-cv`, `cold-outreach`, `follow-up`** — inline stale dossier warning (>30 days old) with refresh suggestion; never blocks execution

### Files changed
- `.claude/skills/checkout/SKILL.md` — new
- `.claude/skills/apply/SKILL.md` — new
- `tools/todo_daily_metrics.py`, `pipeline_staleness.py`, `dossier_freshness.py`, `outreach_pending.py`, `networking_followup.py` — new
- `tests/scripts/conftest.py` + 5 test files — new
- `tests/skills/TESTING_CHECKLIST.md` — new
- `.claude/skills/standup/SKILL.md` — Step 1 rewritten, Step 2 analysis sections replaced with JSON refs
- `.claude/skills/weekly-review/SKILL.md` — Steps 1/2/3/5 rewritten; allowed-tools updated
- `.claude/skills/scan-jobs/SKILL.md` — Step 7b added
- `.claude/skills/todo/SKILL.md` — daily command removed
- `.claude/skills/prep-interview/SKILL.md`, `generate-cv/SKILL.md`, `cold-outreach/SKILL.md`, `follow-up/SKILL.md` — stale dossier warning added

---

## 2026-02-25 — Response tracking + lessons loop auto-promotion

### Added
- **Response tracking in `/follow-up`** — Step 1b inserted between Step 1 and Step 2 in Named Contact Mode. Before drafting, `/follow-up` now scans `data/outreach-log.md` for Drafted/Sent rows for this contact, asks "Did they reply?", and updates the Status column to `Replied` or `No reply`. The reply status flows into Step 3 to ensure the correct follow-up type is selected (e.g., a Nudge can't be chosen if they already replied).
- **Lessons loop auto-promotion in all outreach skills** — Step 0 added to `/cold-outreach`, `/follow-up`, and `/draft-email`. Before drafting, each skill scans `memory/lessons.md` Section 2 for patterns with Occurrences ≥ 2 AND Promoted = No, then prompts the user to promote them to `framework/style-guidelines.md`. Prevents Nick's Voice rules from stagnating in lessons.md indefinitely.
- **Outreach reply routing in `/remember`** — New classification type: "Outreach reply" detected when a note mentions a person's name alongside reply-indicating words ("replied", "got back to me", "heard back from", etc.). Routes to `data/outreach-log.md` — updates the most recent Drafted/Sent row to `Replied`. Falls back to networking.md if no matching row found. Reply notes that also contain contact info write to both files.

### Files changed
- `.claude/skills/follow-up/SKILL.md` — added Step 1b (reply status check) + Step 3 reply_status routing note
- `.claude/skills/cold-outreach/SKILL.md` — added Step 0 (lessons promotion check)
- `.claude/skills/draft-email/SKILL.md` — added Step 0 (lessons promotion check)
- `.claude/skills/remember/SKILL.md` — added Outreach reply classification + Step 3 handler + two new examples

---

## 2026-02-25 — Self-improvement loop repairs + email tone clarity

### Bugs fixed
- **`memory/lessons.md` didn't exist** — the self-improvement loop defined in `CLAUDE.md` wrote corrections to this file, but it was never created. Any email/outreach edits since the loop was added had nowhere to land. File created with the correct two-section table structure (Section 1: general corrections; Section 2: email/outreach corrections with Occurrences + Promoted tracking).
- **`/draft-email` silently ignored Nick's Voice** — `/cold-outreach` and `/follow-up` both loaded `framework/style-guidelines.md` for Nick's voice patterns, but `/draft-email` Step 3 only loaded `outreach-guide.md`. Thank-you notes, status updates, and intro requests were drafted without the learned phrasing rules. Added `framework/style-guidelines.md` to `/draft-email` context loading.

### Improved
- **Disambiguation between the two tone sources** — both `framework/outreach-guide.md` and `framework/style-guidelines.md` contained tone guidance with no stated relationship. Added scope notes to each:
  - `outreach-guide.md` Tone Matching Protocol: marks it as HOW to calibrate tone from prior messages; directs agents to style-guidelines for WHAT Nick sounds like.
  - `style-guidelines.md` Nick's Voice: marked as the canonical source, precedence over outreach-guide when they conflict, fed by the lessons loop from `memory/lessons.md`.

### Files changed
- `memory/lessons.md` — created
- `.claude/skills/draft-email/SKILL.md` — added `framework/style-guidelines.md` to Step 3
- `framework/style-guidelines.md` — disambiguation header on Nick's Voice section
- `framework/outreach-guide.md` — scope note on Tone Matching Protocol

---

## 2026-02-25 — Nick's Voice guidelines + outreach skill wiring

- Added "Nick's Voice — Outreach & Email" section to `framework/style-guidelines.md` with specific greetings, closings, phrasing patterns, and sentence-level rules derived from actual sent messages
- Wired Nick's Voice into `/cold-outreach` and `/follow-up` skills

---

## 2026-02-25 — Company notes convention

- Added `data/company-notes/<slug>.md` as the standard location for personal company context (recruiter calls, video notes, call prep, observations)
- Wired into all generative skills: `/generate-cv`, `/cover-letter`, `/prep-interview`, `/cold-outreach`
- Added convention to `/remember` and `/act` so new observations are routed there automatically

---

## 2026-02-25 — Self-improvement loop

- Added Self-Improvement Loop section to `CLAUDE.md`: after any correction, open `memory/lessons.md`, add/update a row, promote to `framework/style-guidelines.md` when pattern hits 2+ occurrences

---

## 2026-02-25 — Output hierarchy migration

- Adopted company-first output structure: every named entity gets `output/<slug>/` subfolder
- Dossier file: `output/<slug>/<slug>.md` (no date prefix — canonical, in-place versioned)
- All other artifacts inside the folder use `MMDDYY` date prefix
- Updated `/generate-cv`, `/cover-letter`, `/prep-interview`, `/cold-outreach`, `/follow-up`, `/draft-email`, `/research-industry`
- Removed legacy `data/company-research/` and `data/industry-research/` references from all skills

---

## 2026-02-25 — Skill audit fixes (8 bugs)

- `allowed-tools` glob depth: switched from `*` to `**` for subdirectory writes across all output-writing skills
- `Edit(data/job-todos.md)` removed from 7 skills — linter reverts Edit changes on this file; only `Write` works
- Dossier read path standardized to `output/<slug>/<slug>.md` across all skills
- `/prep-interview` missing `Write(data/job-todos.md)` added
- `/import-cv` Step 5 had wrong command name (`/onboard` → `/import-cv`)

---

## 2026-02-24 — New skills: `/setup-goals`, `/cover-letter`

- `/setup-goals` — identity-aware goals bootstrapper; reads `professional-identity.md`, derives what it can, asks only the missing fields, writes `data/goals.md`
- `/cover-letter` — 3-paragraph cover letter (hook → value bridge → close with ask); saves to `output/<company-slug>/MMDDYY-cover-letter.md`, syncs pipeline
- `framework/templates/goals.md` slimmed: removed Priority Stack, Industries, Non-Negotiables (those live in `professional-identity.md`)

---

## 2026-02-24 — Profile guard

- Added hard prerequisite check before all generative and research skills: both `data/profile.md` and `data/goals.md` must exist and contain real content before proceeding
- Skills affected: `/generate-cv`, `/research-company`, `/research-industry`, `/prep-interview`, `/cold-outreach`, `/follow-up`, `/draft-email`, `/voice-export`, `/extract-identity`, `/review-cv`, `/review-cv-deep`, `/weekly-review`, `/scan-jobs`, `/standup`

---

## 2026-02-24 — Research quality standards

- Added Executive Summary + BLUF-per-section to all research dossiers
- Added evidence quality tiers (A/B/C), confidence tags, contradiction handling, and freshness rules
- Added Evidence Summary Table and contradiction audit as mandatory output sections in `/research-company` and `/research-industry`
- Added refresh behavior: if dossier exists and is fresh, offer "view existing" or "refresh"

---

## 2026-02-23 — `/standup` skill

- Morning briefing: reads goals/pipeline/todos/outreach/networking in parallel, outputs daily brief + one suggested action

---

## 2026-02-23 — Scope expansion

- Expanded `CLAUDE.md` from interview coach to full job search operating system
- Added pipeline tracking, outreach, networking, weekly reviews, and research workflows
- Added `/generate-cv`, `/prep-interview`, `/weekly-review` skills
