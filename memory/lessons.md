# Lessons — Active Rules

> Managed by the self-improvement loop defined in `CLAUDE.md`.
> Section 1 = general corrections (CV, coaching, skills). Section 2 = email/outreach corrections.
>
> **Loop for Section 2:** When Nick edits, rewrites, or rejects an email/LinkedIn draft, capture the
> delta as a new row (Occurrences = 1, Promoted = No). On a second occurrence of the same pattern,
> find the existing row and increment Occurrences. When Occurrences ≥ 2 AND Promoted = No:
> add the rule to `framework/style-guidelines.md` under the relevant "Nick's Voice" subsection,
> then set Promoted = Yes here.

---

## Section 1 — General Corrections

| # | Pattern (what went wrong) | Rule (what to do instead) | Date |
|---|--------------------------|---------------------------|------|
| 1 | Cross-reference matching used first name only | Always match full name string as substring — "Alex" ≠ "Alex Mullin", "Amae" ≠ "Amae Health" | 2026-02-24 |
| 2 | Display examples in skills written without checking actual data | Trace matching logic against actual data files before finalizing any example in a skill | 2026-02-24 |
| 3 | Skills failed hard when a cross-referenced file was missing | Skills that read cross-referenced files must skip silently if the file doesn't exist — never throw an error | 2026-02-24 |
| 4 | `allowed-tools` listed both `Read(*)` and specific read paths | `Read(*)` already covers all read paths — listing specific paths too is redundant (harmless but noisy) | 2026-02-24 |
| 5 | Used Edit tool on data/job-todos.md | Always Write data/job-todos.md atomically — a linter actively reverts Edit changes on long cells | 2026-02-25 |
| 6 | Subagent WebFetch used on corporate careers pages | Most corporate careers pages are access-blocked in subagents; only ATS pages (Greenhouse, Lever) tend to work — mark blocked pages as "access blocked; check manually" and keep Pending | 2026-02-25 |
| 7 | Profile guard only checked profile.md | Profile guard must check BOTH profile.md AND goals.md — both must exist and contain non-TODO content before any generative skill runs | 2026-02-25 |
| 8 | "Odyssey" used without disambiguation | "Odyssey" is ambiguous — Odyssey PBC (psilocybin, Bend OR) vs. Odyssey ML (AI lab, Santa Clara) — always confirm which one before researching or writing | 2026-02-25 |
| 9 | `allowed-tools` glob used single `*` for output subdirectories | Must use `**` depth for subdirectory writes — `Write(output/**)` not `Write(output/*)` | 2026-02-25 |
| 10 | `Glob` call used `output/*` to discover dossiers | Must use `Glob(output/**)` to find files in nested subfolders — `output/*` only matches top-level entries | 2026-02-25 |
| 11 | Shared employer framed as personal working relationship | Shared employer ≠ personal connection — don't overstate familiarity. "We both did time at Altman Solon" not "we worked together" | 2026-02-25 |
| 12 | Stated "she's the second person to mention HR" without verifying — no prior mention existed in any data file | Never assert a pattern (recurring suggestion, repeated theme, second occurrence) without first searching the data files to confirm it. If no corroborating evidence is found, don't state it. | 2026-02-26 |
| 13 | Added "possible operations/community role" to Lisa referral — Nick only said client connection, nothing professional | Never infer the nature or purpose of a connection beyond what was explicitly stated. Record only what was said; do not extrapolate intent or capacity. | 2026-02-26 |
| 14 | CHANGELOG not updated after a session that created `/scan-contacts`, `todo_write.py`, and several file changes — Nick had to ask explicitly | Always update `docs/CHANGELOG.md` as the final step of any session that creates or modifies skills, tools, or framework files. Don't wait to be asked. | 2026-02-28 |
| 15 | Multi-agent comparison plans risk anchoring when the independent agent sees the original plan | When building a clean-room comparison agent (e.g., Agent 6 in `/critique-plan`), pass ONLY the stated goal — never the existing plan steps. Anchoring defeats the purpose of the independent view. | 2026-02-28 |

---

## Section 2 — Email / Outreach Corrections

| Pattern | Rule | Occurrences | Promoted | Date |
|---------|------|-------------|----------|------|
| *(no entries yet)* | | | | |
