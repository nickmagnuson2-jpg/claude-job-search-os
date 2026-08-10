---
name: trim-context-file
description: Shrink an always-loaded context file (CLAUDE.md, MEMORY.md, a large SKILL.md) by moving reference-shaped sections out to on-demand docs and leaving a router pointer behind. Measures first, proposes KEEP/MOVE per section, never deletes, never acts without approval.
argument-hint: <path> (defaults to CLAUDE.md)
user-invocable: false  # DISABLED 2026-08-10 — see Safety gate below. Do not flip to true until the capability probe passes.
allowed-tools: Bash(PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py:*), Bash(grep:*), Bash(wc:*), Bash(ls:*), Read(*), Write(*), Edit(*)
---

# Trim Context File

## ⛔ SAFETY GATE — this skill is DISABLED

**Do not run this skill until `tools/context_file_audit.py` advertises `--rules`,
`--emit-blocks`, and fence-aware section splitting, verified by exit code from a
`--capabilities` probe — not by reading help text.**

Why, found by adversarial review 2026-08-10 before first use: the verification steps
below invoke script flags **that do not exist yet**. `--rules --json > rules_before.json`
writes a **zero-byte file**; the Step 5 check "any normative line present before and
absent after is a hard abort" then iterates an empty before-set, finds zero missing
lines, and reports **PASS**. Identically, `--emit-blocks` errors out leaving no blocks,
so the per-block `diff` loop iterates zero blocks and also reports PASS.

Both guards **fail open, silently**, while destinations exist, pointers resolve, and
line accounting balances — so the run looks clean. This is exactly the failure class
this repo has documented twice (a check that passes because its input mirrors the
defect), applied to the file that holds every hard rule.

**A guard with no failing mode is the same bug wearing a safety vest.** Empty artifacts
must be a hard abort, never a pass: `rules_before.json` must be non-empty with a rule
count > 0, and the emitted block count must equal the approved block count exactly,
both checked *before* any edit.

Re-enable by: landing the script capabilities, adding the Step 0 capability gate, then
flipping `user-invocable` back to `true` in the frontmatter.

Some files are loaded into **every session**: `CLAUDE.md`, `memory/MEMORY.md`, and any
SKILL.md the model reads on each invocation. Their size is a per-session tax paid
forever. This skill reduces that tax **without losing the content** by moving
reference-shaped material to on-demand files and leaving an explicit pointer behind.

**The precedent this generalizes:** the 2026-07-08 MEMORY.md restructure, which turned
a bloated always-loaded index into a Critical Context block plus a 7-shard router. That
worked because the router table is explicit about what lives where. This skill applies
the same pattern to any context file.

## The governing distinction

| Tier | Content | Where it belongs |
|---|---|---|
| **Always-loaded** | Rules that must never depend on recall. Prohibitions, hard invariants, tier ladders, "before doing X, do Y." | Stays resident |
| **On-demand** | Material consulted *while doing a specific task*: tool tables, file-format conventions, schema references, worked examples. | Moves out, pointer stays |

The test for a section: **"If Claude never read this section, what breaks?"** If the
answer is "it would violate a rule without knowing," it stays. If the answer is "it
would look up the wrong column name when editing that specific file," it moves.

## Anti-goal

This is **not** the additive-by-default rule being violated. That rule governs *data*
(the user's writings, logs, reflections). This skill governs *infrastructure*, which
CLAUDE.md explicitly carves out for bounded periodic pruning with each decision
surfaced for approval. **Nothing is ever deleted — only relocated.**

## Steps

### Step 1 — Measure (deterministic, never skipped)

```bash
PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py <path>
```

Reports per section: bytes, share of total, `rule_density`, `lookup_density`, and an
advisory KEEP/MOVE/REVIEW.

**Bytes, not lines, are the unit.** A 13-line section of enormous paragraphs can outweigh
a 91-line table. Never propose trims from line counts alone.

The script's suggestion is **advisory input, not a verdict.** Read the actual section
before agreeing with it. A high `lookup_density` section can still be load-bearing if
its table encodes a rule (e.g. a "these files are write-only, use these scripts" table).

### Step 2 — Propose per-section, with the destination named

Present every section above ~3% share as a row:

```
### <Section> — <bytes> (<share>%)
Signals: rule <n> · reference <n> · script says <SUGGESTION>
Proposal: KEEP | MOVE -> <destination path>
Because: <what breaks if Claude never reads this>
Pointer that would remain: "<the exact one-line router entry>"
```

Group into KEEP / MOVE / REVIEW. **Wait for explicit approval per group.** Do not
proceed on silence, and do not batch-apply a group the user only partly approved.

### Step 3 — Move, and leave a real pointer

For each approved MOVE:

1. Create the destination file with a header naming its origin and the date.
2. Move the section **verbatim**. Do not summarize, reword, or "improve" while moving —
   that silently changes rules under cover of a formatting task.
3. Replace the section in the source with a **router entry** that says what lives there
   and *when to read it*. A pointer with no trigger condition is a pointer nobody follows:

   > **Tools & scripts reference** → `docs/tools-reference.md`. Read before invoking any
   > `tools/*.py` script or when you need argument order and flag placement.

4. If the file already has a router table (as MEMORY.md does), add the row there instead
   of scattering pointers through the body.

### Step 4 — Back-propagate references (mandatory)

Moving a section breaks every artifact that cited it by heading or line.

```bash
grep -rn "<distinctive phrase from the moved section>" .claude/skills/ docs/ framework/ tools/ *.md
```

Repoint every hit at the new location in the same pass. A dangling citation is a recall
dead-end, the same failure the memory-archive rule guards against.

### Step 5 — Verify

1. Re-run Step 1 and report before/after: total bytes, and the reduction.
2. Confirm every new pointer's destination file exists and is non-empty.
3. Confirm no moved content was lost: the sum of moved bytes should roughly match the
   source's reduction (allow for the pointer lines added back).
4. If the file is `CLAUDE.md` or a SKILL.md, run the repo's test suite — hooks and tests
   assert against some of these conventions.

### Step 6 — Record

Add a CHANGELOG entry naming what moved and where. A future session that cannot find a
rule needs to be able to trace it.

## Failure modes this skill is built against

- **Trimming by line count.** Understates paragraph-heavy rule sections and overstates tables.
- **Summarizing while moving.** The single most dangerous failure: a rule quietly weakens
  during what was billed as a formatting change. Move verbatim, always.
- **Pointers without triggers.** "See `docs/x.md`" gets ignored. "Read before doing X" gets followed.
- **Forgetting back-propagation.** The rule survives; every citation of it dies.
- **Trimming the wrong tier.** Hard rules are the one thing that must never be paged out,
  no matter how many bytes they cost. High byte-share is not by itself a reason to move.
- **Acting on the script's suggestion without reading the section.** The heuristics are
  regex over line shapes. They cannot tell a load-bearing table from a lookup table.

## Notes

- Read-and-propose by default. It never edits without per-group approval.
- Works on any markdown file with `## ` sections; it is not CLAUDE.md-specific.
- Companion script: `tools/context_file_audit.py` (measure/classify only, never edits).
