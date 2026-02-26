# Changelog

All notable changes to this job search system are recorded here.
Format: newest entries at the top.

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
