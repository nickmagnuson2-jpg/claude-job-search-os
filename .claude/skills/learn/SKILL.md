---
name: learn
description: Produce a deep, validated educational briefing on any topic. Fans out parallel live-web research, synthesizes a BLUF-and-bullets learning document, adds a comprehension + interview quiz, then runs an adversarial claim-validation pass that independently refutes each load-bearing claim against live sources, auto-corrects what is wrong, and attaches real source URLs. Use when the user says "/learn <topic>", "teach me X", "deep briefing on Y", "I want to understand Z well enough to talk about it", or wants a refresher they can trust and cite.
---

# /learn: Validated Deep-Briefing Generator

Turns a topic into a learning document you can trust, because the claims are independently verified, not assumed. Built from the pattern: **research deeply, then prove it before you rely on it.** Sibling to `/analyze` (which evaluates one artifact) and `/deep-research` (which answers one question). `/learn` is for *understanding a whole subject area* well enough to teach or interview on it.

The heavy lifting runs as a multi-agent Workflow (`learn-workflow.js` in this skill directory). This SKILL.md is the conductor: gather config, launch the workflow, then assemble the outputs.

## When to use

- "Teach me about X" / "I need a refresher on Y" / "deep briefing on Z."
- Prepping to speak credibly on a subject (interviews, a new domain, a board topic).
- Any time the user wants depth AND wants to be sure it is factually solid and citable.

Not for: evaluating a single paper/repo/article (use `/analyze`), or answering one narrow factual question (use `/deep-research`).

## Defaults (set when the skill was created; all toggleable per run)

- **Validation: always on, auto-fix.** Every run validates load-bearing claims and auto-corrects anything flagged wrong or overstated before presenting. This is the point of the skill, do not skip it.
- **Depth: deep** (~6,000-8,000 words). Override with `--focused` (~3-4k) or `--comprehensive` (~10-12k).
- **Sections on by default:** apply-to-your-work tie-in (only if repos/context named), quiz (comprehension + interview), rendered PDF, deployment-strategist framing.
- **House rule: no em dashes** in any output.

## Step 1: Parse the request and scope it

Read `$ARGUMENTS` for the topic and any flags. Recognized toggles:

- `--focused` / `--comprehensive` — depth (default deep).
- `--repos <path1,path2>` or `--apply <path>` — local repos/context files to mine for the "how this applies to your work" section. Accept natural phrasing too ("tie it to my side-project repo").
- `--no-quiz`, `--no-pdf`, `--no-personal`, `--neutral` (turns off deployment-strategist framing).
- `--audience "<desc>"` — override the default audience.

**If the topic is underspecified** (too broad to research well, e.g. "AI" or "business"), ask 2-3 sharp clarifying questions to narrow scope before launching. If it is specific enough, proceed.

**Grey-area pass (per project CLAUDE.md):** if any scoping choice is genuinely ambiguous (depth, whether to mine personal repos, framing), surface it with a lean and let the user resolve before spending the workflow. When the defaults clearly fit, state what you are running and proceed.

## Step 2: Launch the validated-briefing workflow

Build the `args` object and launch the workflow by path:

```
Workflow({
  scriptPath: "<ABSOLUTE path to this skill dir>/learn-workflow.js",
  args: {
    topic: "<the topic, fully stated>",
    depth: "focused" | "deep" | "comprehensive",
    repos: ["<abs path>", ...],            // [] if none / --no-personal
    sections: { personal: true, quiz: true, deploymentFraming: true },  // flip per flags
    audience: "<optional override>"
  }
})
```

The workflow runs six phases: **Scope** (anti-anchored agent builds the coverage checklist + atomic research slices), **Research** (one agent per slice on live web via Exa, plus an optional repo-miner), **Assemble** (fresh-context writer, URLs required), **Quiz** (optional), **Verify** (extract 25-35 load-bearing claims, adversarial agents try to refute each against live sources), **Finalize** (auto-correct flagged claims, attach real URLs, write a validation report).

It returns `{ briefing, quiz, validationReport, tally, totalClaims }`.

Launch in the background and let it run; you will be notified on completion. It is token-heavy by design (often 1M+ tokens across ~15-20 agents). That is the cost of a validated document.

## Step 3: Assemble the outputs

When the workflow completes, parse its JSON result file with a small Python snippet (do not hand-read the whole thing into context):

1. Write the briefing + quiz to `output/<MMDDYY>-<topic-slug>-briefing.md` (entity-less one-off, date-prefixed per repo convention). Concatenate `briefing`, then a `---`, then `quiz` if present.
2. Write the validation report to `output/<MMDDYY>-<topic-slug>-validation.md`.
3. **Verify the no-em-dash rule held:** `grep -c "—"` both files; if any slipped in, replace ` — ` with ` - ` and bare `—` with `-`.
4. If PDF is on, render: `PYTHONIOENCODING=utf-8 python3 tools/md_to_pdf_doc.py output/<MMDDYY>-<topic-slug>-briefing.md`.
5. Confirm the briefing already reflects the auto-corrections (the workflow's Finalize phase applies them). If for any reason it did not, apply the corrections from the validation report before presenting.

## Step 4: Present

Give the user:

- The file paths (md + pdf) and the section map.
- **The validation scoreboard up front:** "X of N load-bearing claims independently confirmed against live sources (Y%); Z corrected; W still unverified." Be straight, not reassuring. If anything is still unverified or was contradicted, say so plainly.
- Any myth-vs-reality flags the research surfaced (these are credibility gold).
- Offer to run the quiz interactively (user answers Part A cold, you grade and reconcile gaps).

## Notes

- **Public repo:** this skill and its examples must use generic placeholders, never real contacts or pipeline-target companies (per the project PII gate). The skill takes personal repos as a runtime parameter and never hardcodes them.
- **URLs are mandatory.** The known failure mode (observed on the first run) is the assembler stripping URLs down to bare author-year. The workflow now requires clickable links and the validation pass re-attaches any that slip. If a presented briefing has zero URLs, something broke, flag it.
- **Newly created skills are not session-registered.** A fresh `/learn` goes live as a slash command only in a new session. To run it the same session it was built, execute these SKILL.md steps manually (launch the workflow by scriptPath).
