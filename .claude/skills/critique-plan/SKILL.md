---
name: critique-plan
description: Critique a Codex implementation plan — six-agent analysis (completeness, risk, codebase alignment, simplicity, sequencing, independent Claude plan) — then synthesize an enhanced hybrid plan
argument-hint: <path-to-codex-plan>
user-invocable: true
allowed-tools: Read(*), Glob(*), Grep(*), Task, WebFetch
---

# Critique Plan — Six-Agent Analysis + Enhanced Hybrid Plan

Inserts a structured critique step between Codex plan generation and Claude execution. Dissects the Codex plan across five analytical dimensions, runs an independent Claude plan in isolation (no anchoring), then synthesizes a superior hybrid plan that combines Codex's structure with Claude's codebase awareness.

**Workflow position:**
```
Codex creates plan → saves to .cursor/plans/<slug>.plan.md
         ↓
/critique-plan <path>   ← this skill
         ↓
Inline: critique report + enhanced hybrid plan
         ↓
User approves → Claude executes enhanced plan
```

## Arguments

- `$ARGUMENTS` (required): Path to the Codex plan file.
  - Usually under `.cursor/plans/<slug>.plan.md`
  - Absolute or repo-relative path both accepted
  - Example: `/critique-plan .cursor/plans/scan-contacts-improvement.plan.md`

If no argument is provided, display:
```
Usage: /critique-plan <path-to-codex-plan>

Example:
  /critique-plan .cursor/plans/my-feature.plan.md
```

## Instructions

### Step 1 — Load Plan

Resolve the provided path to an absolute path (relative to the repo root if not absolute).

Read the plan file. If the file does not exist, stop with:
```
⚠️ Plan file not found: <path>
Check the path and try again. Plans are typically saved under .cursor/plans/.
```

From the plan, extract:
- **Stated goal** — the primary objective the plan aims to achieve
- **Phases/steps** — the implementation sequence
- **Files to be modified or created** — any paths mentioned explicitly
- **Stated constraints** — any rules, requirements, or non-negotiables listed

### Step 2 — Load Supporting Context

Read the following in parallel:
1. `CLAUDE.md` — project conventions, data file rules, output conventions, tool safety rules
2. Any project-level `README.md` if it exists in the repo root

This context is passed to all agents. Agents do their own targeted codebase reads as needed (see Agent 3 and Agent 6).

### Step 3 — Launch Six Parallel Critique Agents

Use the Task tool to launch **six parallel subagents** simultaneously.

- **Agents 1, 2, 3, 6:** `subagent_type: "general-purpose"`, `model: "sonnet"`
  - Agents 1, 2, 3: `max_turns: 10`
  - Agent 6: `max_turns: 12` (deeper codebase reads + full plan generation)
- **Agents 4, 5:** `subagent_type: "general-purpose"`, `model: "haiku"`, `max_turns: 6`

**Every agent except Agent 6** receives:
- The full Codex plan text (verbatim)
- The `CLAUDE.md` contents
- Their specific lens and output format instructions (below)

**Agent 6 receives ONLY:**
- The stated goal (extracted in Step 1 — plain text, no implementation steps)
- The `CLAUDE.md` contents
- Its instructions (below)
- **Critical isolation rule: Do NOT give Agent 6 any of Codex's implementation steps. Agent 6 must plan from first principles only. Passing steps to Agent 6 defeats the purpose of the independent plan.**

---

#### Agent 1 — Completeness Analyst
**Lens:** What does the plan miss?

You are a software engineer reviewing an implementation plan for completeness. You have been given the plan text and the project's CLAUDE.md conventions file.

Evaluate:
- Does every step directly map to the stated goal? Flag any steps whose connection to the goal is unclear.
- What edge cases aren't handled? Think about: empty input, missing files, partial failures, concurrent runs, encoding issues, OS-specific behavior.
- What error and failure paths are unaddressed? What happens when a step fails midway?
- What success criteria are vague or unverifiable? "It works" is not a success criterion — flag anything like this.
- What prerequisites are assumed but unstated? (e.g., "assumes Python 3.8+ installed", "assumes file exists at X path")
- What follow-up steps after completion are missing? (e.g., updating an index, notifying downstream consumers, updating documentation)

**Output:** Numbered list of gaps. Tag each: `[gap]`
Include a severity estimate for each gap: HIGH (breaks execution), MEDIUM (causes silent failure or incomplete result), LOW (omission that reduces quality).

---

#### Agent 2 — Risk & Safety Auditor
**Lens:** What could go wrong? What is irreversible?

You are a security and reliability engineer auditing an implementation plan. You have been given the plan text and the project's CLAUDE.md conventions file.

CLAUDE.md contains critical rules about:
- Files that must use Write (not Edit) — e.g., data/job-todos.md, data/job-pipeline.md, large markdown tables
- Content exclusions for CVs (data/project-background/ must never appear in CVs)
- Destructive operations that require confirmation
- Encoding requirements for Windows (PYTHONIOENCODING=utf-8)

Evaluate:
- **Irreversible operations:** Does the plan delete, overwrite, truncate, or drop anything? Are backups or rollback steps present before these ops?
- **High blast-radius changes:** Does the plan touch many files simultaneously? Could a bug at step N break steps N+1 through N+10?
- **Security vulnerabilities:** Any command injection risk (unsanitized user input passed to shell)? Any secrets or credentials in paths or logs? Any unvalidated external input?
- **Write-vs-Edit safety:** Does the plan use Edit on any file that CLAUDE.md says must use Write? (Check CLAUDE.md's "Write-Only Files" section.)
- **Encoding and OS assumptions:** Any paths with hardcoded separators? Any script invocations missing `PYTHONIOENCODING=utf-8` on Windows?
- **Missing guards at system boundaries:** User input, external API calls, file reads — are they validated?
- **Missing checkpoints:** Before any destructive step, is there a verification or dry-run step?
- **Fragile environmental assumptions:** OS-specific paths, tools assumed to be installed, version assumptions.

**Output:** Numbered list of risks. Tag each:
- `[blocker]` — must fix before execution; plan is unsafe as written
- `[warning]` — should fix; risk of silent failure or data loss
- `[note]` — worth knowing; low probability but worth a comment

---

#### Agent 3 — Codebase Alignment Scout
**Lens:** Does the plan fit the existing codebase?

You are a senior engineer who knows this codebase well. You have been given the plan text and the project's CLAUDE.md conventions file.

Do the following targeted reads to ground your analysis:
- `Glob tools/*.py` — find existing utility scripts the plan might reinvent
- `Glob .claude/skills/**/*.md` — find existing skill patterns and conventions
- Read any files the plan explicitly proposes to create or modify (so you can reason about what's already there vs. what the plan assumes)
- Read `data/project-index.md` if it exists (lightweight map of existing projects)

Then evaluate:
- **Reinvention:** Does the plan propose building something that already exists in `tools/`? (e.g., a new parser for a file that `tools/todo_write.py` already handles)
- **Naming conventions:** Do proposed file names and paths match the patterns in CLAUDE.md and existing `output/`, `data/`, `tools/` directories?
- **Path conflicts:** Will any proposed new files conflict with existing paths?
- **Missing reads:** Are there framework files, data files, or skill files the plan's execution will need but doesn't mention reading? (e.g., a skill that writes to `data/job-pipeline.md` but doesn't plan to read it first)
- **Skill pattern violations:** If the plan creates or modifies a skill, does it follow the YAML frontmatter conventions (name, description, argument-hint, user-invocable, allowed-tools)?
- **Convention violations:** Does the plan output files using the wrong naming pattern? (e.g., flat `output/file.md` instead of `output/<slug>/MMDDYY-file.md`)
- **Reuse opportunities:** What existing utilities or patterns could simplify the plan?

**Output:** Numbered list of alignment issues and reuse opportunities. Tag each:
- `[reuse]` — existing tool/pattern the plan should use instead of rebuilding
- `[conflict]` — path or name conflict with existing code
- `[convention]` — plan deviates from established project conventions

---

#### Agent 4 — Simplicity & Scope Auditor
**Lens:** Is the plan the minimum necessary? (Uses haiku — structural check, no codebase reads needed)

You are a code reviewer applying the principle of minimum necessary complexity. You have been given the plan text and the project's CLAUDE.md conventions file.

Evaluate:
- **Scope creep:** Does the plan implement features not present in the stated goal? Flag anything extra.
- **Over-engineering:** Abstractions (base classes, interfaces, factory patterns, plugin systems) for a task that will have exactly one implementation?
- **Premature generalization:** Code structured for hypothetical future requirements ("we might want to support X later")?
- **Unnecessary helpers:** Utility functions or classes that wrap trivial single operations?
- **Backwards-compatibility hacks:** Renaming variables to `_old_foo`, re-exporting removed types, keeping deprecated code paths alongside new ones — when the right move is to just change the code?
- **Excessive validation:** Input validation for scenarios that can't happen (internal functions validating types that are already type-safe, framework-guaranteed properties)?
- **Configuration bloat:** Feature flags, environment-variable toggles, or configurability for things that should just be hardcoded for this use case?

**Output:** Numbered list of simplification opportunities. Tag each:
- `[scope-creep]` — feature not in the stated goal
- `[over-engineering]` — unnecessary complexity for the actual use case

---

#### Agent 5 — Execution Sequencer
**Lens:** Is the order and structure correct? (Uses haiku — structural check, no codebase reads needed)

You are an execution planner reviewing the implementation order of a plan. You have been given the plan text and the project's CLAUDE.md conventions file.

Evaluate:
- **Dependency order:** Do any steps use the output of a later step? List specific step pairs where this occurs.
- **Unstated dependencies:** What implicit dependencies exist between steps that aren't called out? (e.g., step 3 assumes step 1 created a file, but step 2 could fail first)
- **Parallelization opportunities:** Which sequential steps have no dependency on each other and could safely run in parallel?
- **Verification placement:** Are test/verify steps placed AFTER the thing they verify? Flag any that appear too early.
- **Missing checkpoints:** Before any high-risk operation (overwrite, delete, publish), is there a "verify the previous step succeeded" checkpoint?
- **Rollback position:** If the plan has irreversible steps, is a rollback/backup step positioned BEFORE (not after) the risky operation?
- **Missing cleanup steps:** If the plan creates temp files or intermediate artifacts, is there a cleanup step?

**Output:** Reordering suggestions and dependency flags. Tag each:
- `[ordering]` — step sequence is wrong or suboptimal
- `[parallelizable]` — steps that could run concurrently
- `[checkpoint]` — a verification/gate step should be inserted here

---

#### Agent 6 — Independent Claude Planner
**Lens:** What would Claude build from scratch for the same goal?

**CRITICAL ISOLATION RULE: You have NOT been shown any implementation steps from the Codex plan. You are planning from first principles only. Do not ask about steps — they were intentionally withheld to prevent anchoring.**

You are an experienced engineer implementing the following goal in this codebase. You have been given the project's CLAUDE.md conventions file. Read the codebase to understand what already exists before planning.

Do the following targeted reads first:
- `Glob tools/*.py` — existing utilities
- `Glob .claude/skills/**/*.md` — existing skill patterns
- Any other files in the repo that are directly relevant to achieving the stated goal (use your judgment — check what the goal touches)

Then produce a **complete, independent implementation plan:**
- Numbered steps, each with: what to do, which files to touch, and how to verify success
- State any assumptions explicitly (what you're assuming about the current state of the codebase)
- State your rationale: why these steps, in this order?
- If you considered multiple approaches, briefly note the alternatives and why you chose this path

Tag every step `[claude-only]` to distinguish your steps during synthesis.

**Goal to implement:**
```
<STATED_GOAL>
```

**CLAUDE.md conventions:**
```
<CLAUDE_MD_CONTENTS>
```

---

### Step 4 — Synthesize and Output

After all six agents return, produce the full critique report and enhanced hybrid plan inline (do not write to a file).

#### 4a. Build Issue Table (from Agents 1–5)

Merge all critique findings from Agents 1–5 into a single unified issue table. Deduplication rules:
- If multiple agents flag the same underlying issue, merge into one row listing all flagging agents
- Use the highest severity assigned by any agent for the merged row
- Combine the detail from all flagging agents for a richer description

Assign severity based on these definitions:
- **BLOCKER** — must fix before execution; plan is unsafe or unexecutable as written. Maps to: any `[blocker]` from Agent 2, or any `[gap]` rated HIGH by Agent 1, or any `[ordering]` that makes a step unrunnable.
- **WARNING** — should fix; risk of silent failure, data loss, or significantly degraded result. Maps to: Agent 2 `[warning]`, Agent 1 `[gap]` rated MEDIUM, Agent 3 `[conflict]`, Agent 5 `[ordering]` that is suboptimal but not unrunnable.
- **SUGGESTION** — nice to fix; improves quality or efficiency without blocking execution. Maps to: Agent 4 findings, Agent 3 `[reuse]` and `[convention]`, Agent 1 `[gap]` rated LOW, Agent 5 `[parallelizable]` and `[checkpoint]`.

#### 4b. Diff Codex vs Claude (Agent 6)

Compare Codex's steps (from Step 1) against Agent 6's independent plan step-by-step. For each step or cluster of related steps, classify:

- `[both]` — Codex and Claude agree on this step (high confidence — keep as-is in hybrid)
- `[codex-only]` — Codex included this, Claude didn't. Cross-check against Agents 1–5: if no agent flagged a problem with it → keep; if an agent flagged it → flag for review with the issue
- `[claude-only]` — Claude planned this, Codex didn't. These are the primary gap-fills — insert into the hybrid plan
- `[diverge]` — Both planned a step for the same goal but chose different approaches. Show both approaches and recommend one with rationale (prefer the simpler one unless there is a clear reason not to)

#### 4c. Produce Inline Output

```
## Plan Critique: <plan filename>

### What Codex Intends
[2-3 sentence summary of the plan's stated goal and overall approach]

---

### Critique Report (Agents 1–5)

| # | Dimension | Severity | Tag | Issue | Fix |
|---|-----------|----------|-----|-------|-----|
| 1 | Risk | BLOCKER | [blocker] | ... | ... |
| 2 | Completeness | WARNING | [gap] | ... | ... |
| 3 | Alignment | WARNING | [conflict] | ... | ... |
| 4 | Scope | SUGGESTION | [scope-creep] | ... | ... |
| ... | | | | | |

**Summary:** N blockers · N warnings · N suggestions

---

### Codex vs Claude Diff (Agent 6)

| Step | Codex | Claude | Classification | Recommendation |
|------|-------|--------|----------------|----------------|
| Goal: [sub-goal] | [Codex approach] | [Claude approach] | [both] | Keep |
| Goal: [sub-goal] | [Codex approach] | — | [codex-only] | Keep (no issues flagged) |
| Goal: [sub-goal] | [Codex approach] | — | [codex-only] ⚠️ | Flag — Issue #N applies |
| Goal: [sub-goal] | — | [Claude approach] | [claude-only] | Add to hybrid |
| Goal: [sub-goal] | [Codex alt A] | [Claude alt B] | [diverge] | Prefer B because... |

---

## Enhanced Hybrid Plan

> Codex's structure · Claude's judgment · all blockers resolved

### Goal
[Restate the goal, sharpened based on critique findings]

### Prerequisites
[Any unstated prerequisites surfaced by agents — e.g., "Python 3.8+ required", "PYTHONIOENCODING=utf-8 must be set", "File X must exist"]

### Steps

[Numbered steps built from the diff:
- [both] steps kept as-is
- [claude-only] steps inserted at the correct sequence position
- [diverge] steps resolved to the recommended approach with a note
- Safety checkpoints from Agent 2 inserted before any high-risk operations
- Reordering applied per Agent 5 findings
- Simplifications from Agent 4 applied inline
- Each step: action + files touched + how to verify success]

1. **[Step title]**
   - Action: [what to do]
   - Files: [files touched]
   - Verify: [observable outcome or command that confirms success]

2. ...

### Verification
[Overall: how to confirm the full plan succeeded — specific commands or observable outcomes]

### Rollback
[Only include this section if the plan contains destructive operations. Describe how to undo each destructive step.]
```

---

## Edge Cases

- **No argument provided:** Display usage message and stop.
- **File not found:** Error with `⚠️ Plan file not found: <path>` and stop.
- **Plan has no explicit steps:** Work from the stated goal and any prose description. Note in the critique that the plan lacks structured steps — this is itself a BLOCKER (Agent 1 gap).
- **Agent returns thin results:** Proceed with remaining agents' output. Note which dimension returned thin results in the critique report. Do not fail the full operation.
- **Agent 6 returns very similar steps to Codex:** This is a good signal — mark everything `[both]` and note in the diff that both approaches converged (high confidence). The hybrid plan will closely resemble the Codex plan, with gaps and risks addressed.
- **Agent 6 returns a radically different plan:** Surface all `[diverge]` entries clearly. Recommend the simpler or more codebase-aligned approach. Let the user decide.
- **No blockers found:** Still produce the full output — a clean bill of health is useful signal. Note "No blockers found — plan is safe to execute."
- **Plan is not in `.cursor/plans/`:** Accept any valid path — the plan could be anywhere. No restriction on location.
