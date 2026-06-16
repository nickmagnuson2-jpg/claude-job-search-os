---
name: audit-pii
description: Pre-commit PII gate for the public repo. Regenerates the denylist, runs the deterministic scan over changed public files, then dispatches a fresh subagent for a semantic PII pass that catches real names/companies/emails the denylist misses. Run before committing or pushing public artifacts (tests, skills, framework, docs, tool code).
argument-hint: "(no args — scans staged + unstaged changes to public files)"
user-invocable: true
allowed-tools: Read(*), Glob(*), Grep(*), Bash(PYTHONIOENCODING=utf-8 python3 tools/gen_pii_denylist.py:*), Bash(PYTHONIOENCODING=utf-8 python3 tools/check_public_pii.py:*), Bash(git status:*), Bash(git diff:*), Bash(git ls-files:*), Bash(git check-ignore:*), Task
---

# Audit PII — Pre-Commit Public-Repo Leak Gate

The job-search OS repo (`claude-job-search-os`) is **public**. This skill is the
commit-time gate that stops a real contact, pipeline/target company, email, or phone
number from shipping into a public artifact (tests, `.claude/skills/`, `framework/`,
`docs/`, tool code/comments, top-level `*.md`).

Two layers, by design (per `feedback_generalize_examples_in_public_artifacts`):

- **Deterministic** (`tools/check_public_pii.py`, always-on PreToolUse hook + re-run here):
  high-precision exact match against the gitignored denylist. Catches known tokens.
- **Semantic** (this skill's subagent): high-recall judgment pass. Catches *new* names
  not yet on the denylist, ambiguous brand-words the denylist deliberately skips
  (e.g. real companies that are also dictionary words), emails, phone numbers, and
  any other identifying detail. This is the layer the deterministic hook cannot do.

Run this **before every commit or push that touches public files.** It is the
commit-flow step — there is no separate git pre-commit hook.

## Boundary (KEEP vs SCRUB)

Per `memory/feedback_nick_pii_redaction_boundary`:

- **KEEP (public-safe):** real employers/schools (Zuora, McKinsey, Tuck, Duke, Yahoo,
  ESPN), public-figure authors (Ali Rohde, Ryan Holiday, Molly Graham), generic
  products (Notion, Stripe, Anthropic, GitHub), Nick's own handle/name where already
  public, Raphael attribution.
- **SCRUB (leak):** pipeline/target companies, recruiter and contact names, real
  emails/phones, side-venture names, donor/WPI work, anything sealed.
- **Ambiguous → flag, don't auto-edit.** Surface it and let Nick decide.

## Steps

### Step 1 — Refresh the denylist and run the deterministic scan

```bash
PYTHONIOENCODING=utf-8 python3 tools/gen_pii_denylist.py --repo-root .
```

Then collect the changed **public, non-gitignored** files:

```bash
git status --porcelain
```

For each changed/untracked path, keep it only if BOTH:
- it is a public artifact: under `tests/`, `.claude/skills/`, `framework/`, `docs/`,
  or is `tools/*.{py,md,sh}`, or a top-level `*.md`; AND
- `git check-ignore -q <path>` returns non-zero (NOT ignored).

Skip everything else (private `data/**`, gitignored caches, `output/**`, the denylist).
If the public-file set is empty, report "No public files changed — nothing to audit" and stop.

Run the deterministic hook against each candidate file's current content (pipe a
synthetic `Write` payload) to surface any known-token hits up front:

```bash
echo '{"tool_name":"Write","tool_input":{"file_path":"<path>","content":<file-json>}}' \
  | PYTHONIOENCODING=utf-8 python3 tools/check_public_pii.py; echo "exit=$?"
```

Collect the exact-match hits (exit 2) into the findings list.

### Step 2 — Dispatch a FRESH subagent for the semantic pass

Spawn one subagent (Task tool, `general-purpose`) over the candidate files. Give it
ONLY the file list, the file contents, and the KEEP/SCRUB boundary above — do **not**
give it the denylist (you want an independent judgment pass that catches what the
denylist missed, per the anti-anchoring rule in CLAUDE.md / Multi-Agent Design).

Subagent instructions:
> You are a PII reviewer for a PUBLIC code repository. Below are changed files. Find
> every piece of real personal/company identifying information that should not be in a
> public repo: real person names, pipeline/target company names, email addresses, phone
> numbers, physical addresses, side-venture names. Apply this boundary: KEEP real
> employers/schools/public-author names/generic products [list]; SCRUB everything else.
> For each finding return: file, line, exact text, type, KEEP/SCRUB/AMBIGUOUS, and a
> suggested generic placeholder that preserves the example's intent. Return structured
> findings only. If a file is clean, say so. Do not edit anything.

### Step 3 — Merge, present, and gate

Merge deterministic hits (Step 1) with the subagent's SCRUB + AMBIGUOUS findings.
De-duplicate by (file, text). Present grouped by file:

```
## PII Audit — N file(s) scanned

### <path>
- ⛔ SCRUB  L<line>  "<text>"  (<type>) → suggest: "<placeholder>"
- ⚠️ AMBIGUOUS  L<line>  "<text>" → your call (likely KEEP because ...)

[If clean:] ✅ <path> — clean
```

- **Any SCRUB finding = do not commit yet.** Offer to apply the generic-placeholder
  fixes (re-read + Write/Edit each file), then re-run the audit to confirm green.
- **AMBIGUOUS findings** are surfaced for Nick's decision, never auto-edited.
- **All clean** → report `✅ PII audit clean — safe to commit.` and stop.

### Step 4 — On fix-and-recommit

If Nick approves fixes, apply them, re-run Steps 1–3 once, and confirm a fully green
pass before handing back to the commit flow. The deterministic hook will also fire on
each Write/Edit as a backstop.

## Notes

- This skill READS and proposes; it does not commit or push.
- The denylist is regenerated each run so newly-added contacts/pipeline rows are covered
  by the deterministic layer immediately.
- Origin: 2026-06-11, hook+subagent promotion of `feedback_generalize_examples_in_public_artifacts`.
  Pairs with `tools/check_public_pii.py` (the always-on reflex) and
  `tools/gen_pii_denylist.py` (the token source).
