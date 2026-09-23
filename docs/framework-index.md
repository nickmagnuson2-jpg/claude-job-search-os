# Framework Index

Moved verbatim out of `CLAUDE.md` on 2026-09-14 by `/trim-context-file`, when that file sat at
40,934 bytes against its 40,960-byte always-loaded budget — 26 bytes of headroom, so the next
addition of any substance would have breached it.

**Both sections below measured a rule density of 0.00.** They are descriptions of what lives inside
other files, consulted when you need the METHOD rather than the name. Nothing here is a rule, which
is why it could leave the always-loaded tier; nothing here was reworded in the move.

**Read this when:** you need the actual method inside one of these frameworks — the 7 steps, the
page anatomy, the 16-point checklist, the answering strategies — not merely that the framework exists.

---

## Problem Solving & Communication Craft

Two foundational frameworks from Nick's McKinsey training. Sealed raw materials in `data/project-background/mckinsey/`; the extracted concepts in framework docs are public knowledge.

- **Problem solving** → `framework/problem-solving-mckinsey.md`. The 7-step method (Define → Structure → Prioritize → Plan → Conduct → Synthesize → Recommend), Problem Statement Worksheet, MECE issue trees, hypothesis-driven workplans, pyramid synthesis tests. Used by `/prep-interview`, `/debrief`, `/research-company`, `/research-industry`, and any synthesis work where structure matters.
- **SMB decision analysis** → `framework/smb-decision-analysis.md`. The method for irreversible small-business calls (takeover, lease, capital): fact base → structured problem → multi-lens → assumptions register → adversarial verdicts → gates → per-audience artifact. Its three survival disciplines (canonical spine, superseded banners that state *what survives*, blind-run reconciliation) apply to any analysis that outlives its own premises.
- **Slide / communication craft** → `framework/slide-craft-mckinsey.md`. Ledes (insight vs. process), 30-second test, page anatomy (4 corners), 11-point quality checklist, 8 common feedback patterns, "kill empty verbiage" rules. Used when producing prep PDFs, dossiers, cover letters, and any artifact where the audience scans first.
- **THE DECK GATE** → **`framework/deck-rubric.md`**. Read it before building any deck, and score against it before sending one. Three layers, do not collapse them: `slide-craft-mckinsey.md` holds the PRINCIPLES, `deck-rubric.md` is the enumerated GATE (sections A-E score craft, F scores the frame), and the `mckinsey-slides` skill is the EXECUTION layer that builds the artifact. **Nothing ships until E1, E7 and F all pass.** Deterministic subset of F: `tools/check_frame_integrity.py`.
  - **This pointer exists because the link was one-way and the gate never fired.** The rubric named `mckinsey-slides` as its execution layer from the day it was promoted (2026-08-13) while nothing pointed back. Measured 2026-09-17: zero repo files matched "deck-rubric", and a deck built minutes after loading the skill carried three C11 dash violations and no C7 takeaway box. There is also a STEP 0 probe in the global skill, but that file lives at `~/.claude/skills/` **outside this repo**, so it is not versioned here and not covered by the nightly backup. **This line is the durable half; the skill probe is the convenient half.** Trace: `output/analysis/091726-deck-rubric-gap-register.md`, `memory/rederivation-log.md`.

## Resume Generation & Interview Training

- Resume standards (tailoring, 16-point checklist, cheat sheet) → `framework/application-workflow.md`. Used by `/generate-cv`, `/apply`, `/cover-letter`.
- Interview workflow, coaching rules, progress logging → `framework/interview-workflow.md`.
- Six answering strategies in `framework/answering-strategies/` (blank-mind, gap reframing, pressure defense, question-back, anti-patterns, direct answer structure).
- Voice simulation: `/voice-export` (generate prompt) → practice in Claude App → `/debrief` (analyze).

