---
name: analyze
description: Analyze an external artifact (research paper, GitHub repo, or article) through the "can this help my systems or wisdom?" lens. Triages relevance cheaply first, extracts a common ExtractionBlock (thesis/method/evidence for a paper, what-it-does/architecture/maturity for a repo), then runs a mandatory applicability pass against Nick's actual architecture and goals - with a non-empty "where it does NOT fit" carve-out so it never just cheerleads. Use when Nick shares a URL and asks whether it is worth adopting, what it means for his systems, or "is this useful for me?"
argument-hint: <url> [focus or question]
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Glob(output/**), Grep(*), Write(output/**), Bash, Task, mcp__exa__web_search_exa, mcp__exa__web_fetch_exa, WebFetch, WebSearch
---

# /analyze - Artifact Analysis Through the "Does This Help Me?" Lens

Takes a URL (research paper, GitHub repo, or article) and answers one question: **does this help Nick develop his systems or his wisdom?** Not a summary. A critical teardown plus a concrete, honest mapping to his actual architecture and goals.

Origin: 2026-06-08, after an ad-hoc analysis of "Compiling Agentic Workflows into LLM Weights" (Dennis et al.). That analysis is the exemplar for the output shape: thesis in one breath, an honest "the technique does not fit your scale but the principle does," explicit cost calibration, and a "does NOT fit because" carve-out. See `output/analysis/` for prior runs.

## Argument grammar

`/analyze <url> [focus or question]`

- `<url>` (required) - arxiv/paper URL, `github.com/...` repo, or any article/blog URL.
- `[focus]` (optional) - a steer, e.g. "focus on the memory architecture angle" or "is this worth adopting for /research-company?". If omitted, analyze broadly against systems + wisdom. If the focus carries a framing ("feels important as costs increase"), classify the framing rather than adopt it (see Calibrate, do not agree).

## Hard rules (this skill exists to enforce these)

- **Anti-cheerleading. The "Where it does NOT fit" section is MANDATORY and non-empty.** Every artifact has limits, scale mismatches, or transfer failures. If you cannot find one, you have not analyzed hard enough - re-read. Most papers/repos are a *principle* Nick can use wrapped in a *technique* that does not fit his single-user, judgment-heavy, low-volume context. Name both, separately. An empty "does NOT fit" is a skill failure.
- **No fabrication.** Paper results, sample sizes, benchmark numbers, repo stats - extract them verbatim from the source, never estimate or recall. If a number is not in the source, say "not stated," do not invent it. Composes with the verification-umbrella + `feedback_verify_quantitative_claims_before_memorization`.
- **Confidence-tag every applicability claim** *(strong / lean / unsure)* per the Discussion Discipline block in CLAUDE.md. A claim you cannot tag, you should not make.
- **Calibrate, do not agree.** If Nick frames the artifact a certain way in `[focus]`, classify it: is the framing right at the principle level but wrong at his scale? Say so. Sycophantic agreement is a failure of this skill.
- **No em dashes** in the saved doc or chat summary (global voice rule). Use commas, periods, or hyphens. Long-form generation bypasses hard rules, so grep the doc for em dashes before writing it, per `feedback_long_form_generation_bypasses_hard_rules`.
- **Route fetching through Exa** by default (`web_fetch_exa` / `web_search_exa`), per `feedback_route_research_subagents_through_exa`. WebFetch is a fallback only.
- **Privacy.** The skill reads Nick's local files only for the applicability judgment. Nothing of his is included in any external fetch or query.

## Pipeline (the data flow)

The skill is a pipeline of bounded steps joined by one fixed contract. Every step runs inline in v1.

```
detect(url)              -> {source_type, slug, canonical_url}        # deterministic
triage(url, type)        -> {decision: go|skip, cheap_summary, bail_reason?}
extract(url, type)       -> ExtractionBlock                            # per-type, ONE shape
applicability(block)     -> {verdict, systems, wisdom, does_not_fit, next_actions}
write(...)               -> output/analysis/MMDDYY-<slug>.md + chat summary
route(...)               -> propose todos / /remember (never auto-write)
```

The **`ExtractionBlock` is the load-bearing interface.** Everything downstream reads it, so how it is produced (inline today, a script or subagent later) can change without touching the applicability or output logic. Keep the steps separable.

## Step 1: Detect + triage

**Detect** (deterministic, trivial):
- `arxiv.org`, `*.pdf`, DOI links, journal/proceedings domains -> `paper`.
- `github.com/<owner>/<repo>` -> `repo`.
- YouTube / video URLs -> `video`: fetch the transcript via `PYTHONIOENCODING=utf-8 python3 tools/fetch_transcript.py <url> --repo-root .` (built 2026-06-09). It returns a JSON ExtractionBlock to stdout (`title`, `slug`, `text`, `segments`, `is_auto_generated`, `cached_path`) and caches `data/source-transcripts/<video_id>.md`. Use `text` as the source content and `slug` for the output filename, then run triage + extract + applicability exactly as for an `article` (core claim + evidence offered). If the JSON is `ok:false`, report the `reason_code` plainly (`no_captions` = no usable captions, ask Nick to paste a transcript; `private`/`age_restricted` = can't access; `dep_missing` = run the pip install the error names; `ip_blocked` = YouTube is rate-limiting, retry later) and do NOT fabricate around it. Flag auto-generated captions (`is_auto_generated: true`) as a transcription-quality caveat: speaker names, jargon, and numbers may be mis-rendered, so verbatim-number extraction (Step 2 evidence rule) is lower-confidence from auto-captions.
- everything else -> `article`.
- Derive a `slug` (lowercase-hyphens) from the title or repo name for the output filename.

**Triage** (the cost control - do this BEFORE any full extract, for every type):
- Cheap first pass ONLY: abstract (paper) / README + `gh` metadata (repo) / page head or first screen (article).
- Quick relevance read against `CLAUDE.md` + `data/goals.md` (the architecture and the current search direction).
- If it clears the relevance bar -> proceed to Step 2 (full extract).
- If it is a dud -> **bail in chat with the specific reason** ("not relevant because X / too low-maturity because Y / out of scope because Z"), write NO doc, and offer "want the full teardown anyway?" The bail must state why, so a wrong bail is visible and Nick can override it.

## Step 2: Extract (build the ExtractionBlock)

Build the factual layer before any synthesis. Every later claim must trace to it. Produce ONE common shape regardless of type so Step 3 is type-agnostic:

- **Paper:** thesis (one sentence); method; **evidence quality** (sample sizes, baselines/controls, effect sizes, p-values, who/when, judge or benchmark used, peer-reviewed vs preprint); headline results captured *verbatim* (numbers exactly as stated); stated limitations/threats.
- **Repo:** what it does; approach/architecture; **maturity signals** (stars, last push, archived flag, test presence, release cadence, license, dependency weight, single-maintainer vs team); how it actually works; what adopting it would require.
- **Article:** core claim + any evidence offered.

**Fetching (route by type, Exa by default):**

*Paper:* `mcp__exa__web_fetch_exa` on the URL (use the `/html/NNNN` arxiv form when handed `/abs/` or `/pdf/`). Papers are large: if the fetch is persisted to a tool-result file rather than returned inline, read it in pages or extract sections (`json.load` the tool-result, slice by char range or regex on section headers). Never dump the entire paper text inline.

*Repo (adaptive depth):*
- **Pass 1 (always, cheap):** README + metadata via `gh`:
  ```bash
  gh repo view <owner>/<repo>
  gh api repos/<owner>/<repo> --jq '{stars:.stargazers_count, forks:.forks_count, open_issues:.open_issues_count, pushed:.pushed_at, archived:.archived, license:.license.spdx_id}'
  gh api repos/<owner>/<repo>/git/trees/HEAD?recursive=1 --jq '.tree[].path'
  ```
  This alone answers most relevance calls.
- **Pass 2 (only if Pass 1 clears the bar):** shallow-clone and read the load-bearing code:
  ```bash
  git clone --depth 1 https://github.com/<owner>/<repo> /tmp/analyze-<slug> 2>&1 | tail -2
  ```
  Read entry points, core modules, tests. For a large repo, dispatch an `Explore` subagent (Task tool) to read the code so it does not flood the main context; ask it for the architecture, how it actually works, and adoption cost. **Delete the temp clone when done** (`rm -rf /tmp/analyze-<slug>`).
- If Pass 1 already showed the repo is irrelevant/abandoned/low-maturity, STOP - do not clone (this is the triage bail for repos).

*Article:* `mcp__exa__web_fetch_exa`; capture the core claim and any evidence.

If a fetch fails or is paywalled: report it, try WebFetch as a fallback, then ask Nick to paste the content. Never fabricate around a failed fetch.

## Step 3: Applicability pass (THE CORE - judgment, stays prose)

Read Nick's context first: `CLAUDE.md` always (the architecture: skills, hooks, scripts, memory tiers, data model), plus as relevant `data/goals.md`, `data/professional-identity.md`, and the `MEMORY.md` index. Then produce, from the `ExtractionBlock`, three co-equal reads:

- **Systems fit:** does this map to something he could build or change in his OS - a skill, a hook, a script, the memory/context architecture, a research/CV/interview workflow? Name the specific file or skill it would touch and a rough effort. Confidence-tag it.
- **Wisdom fit:** is there a mental model, principle, or framing that transfers even if the artifact itself does not - something that sharpens how he thinks about his system, his search, or his decisions? Confidence-tag it.
- **Where it does NOT fit (MANDATORY, non-empty):** scale mismatch (production-volume technique vs single-user interactive use), judgment-vs-procedure mismatch (his generative skills need frontier reasoning; fixed-procedure techniques do not apply), maturity/risk, dependency cost, or plain irrelevance. Be specific about *why*.

Systems and wisdom are co-equal; neither is subordinate. The verdict leads.

## Step 4: Write the analysis + route

**Write** to `output/analysis/MMDDYY-<slug>.md` (the dir exists; if somehow absent, create it). Use this shape (the Dennis-paper analysis is the exemplar). All eight elements are required:

1. **Verdict** (leads): one-line "does this help, and the single most useful takeaway," plus a fit read (systems / wisdom / neither).
2. **What it is:** thesis or what-it-does, in a breath.
3. **Evidence / maturity check:** the quality signals from Step 2; flag hype vs substance.
4. **Applicability to my systems:** concrete, with file/skill targets + rough effort + confidence tag.
5. **Applicability to my wisdom:** transferable principles, confidence-tagged.
6. **Where it does NOT fit:** the mandatory, non-empty carve-out.
7. **Next actions (if any):** specific builds/todos, or an explicit "nothing to build, useful frame only." Never manufacture a build to look productive.
8. Footer: `Source: <url>` and `Analyzed: YYYY-MM-DD`.

Before saving, grep the drafted text for em dashes and replace them (hard rule above).

**Chat summary:** mirror the doc tightly - verdict + the systems/wisdom/does-not-fit triad + next actions.

**Route** (never auto-write to data files):
- If a concrete build emerged and is in-session tractable, propose building it directly. Do NOT offer a park/leave-it menu for a fix Nick would clearly want, per `feedback_dont_offer_deferral_for_user_flagged_pain`. If it is not in-session tractable, propose a `data/job-todos.md` entry (added via `tools/todo_write.py`, the atomic write path - do not hand-edit that file).
- If the analysis produced a durable strategic insight (not a build), offer to capture it via `/remember` to `data/decisions.md`. Do not auto-write.
- Offer a PDF only if Nick asks for paper output.

## Evolvability seams (keep re-architecture cheap)

The fixed `ExtractionBlock` plus bounded steps make each future change localized. Do not collapse the steps into one blob; the seams are the point.

| Future change | What it touches | Cost |
|---|---|---|
| Add other video hosts (Vimeo etc.) | extend `tools/fetch_transcript.py` (currently YouTube-only) | localized; YouTube path already shipped 2026-06-09 |
| Promote to a script (Approach 2) | move `detect` (and optionally repo-metadata normalize) into `tools/analyze_detect.py`; SKILL.md calls it; `ExtractionBlock` unchanged | one-step swap |
| Promote to subagents (Approach 3) | wrap per-type `extract` in `Task` calls; `applicability` already consumes `ExtractionBlock`, untouched | localized |

Promotion trigger for the script split (per the skill-rebuild methodology): invoked >= 20x AND a recurring categorization decision AND currently inconsistent. v1 does not meet this, so no script is built now.

## Error handling and edge cases

- Fetch fails or paywalled: report it, try WebFetch fallback, then ask Nick to paste the content.
- Huge paper persisted to a tool-result file: page or section-extract, never dump all the text inline.
- Private or 404 repo: report it and stop.
- `video` URL: fetch via `tools/fetch_transcript.py` (Step 1). On `ok:false`, report the `reason_code` and ask Nick to paste a transcript; never fabricate around a failed fetch. Auto-generated captions are lower-confidence for verbatim numbers.
- Missing local data file (e.g. `data/goals.md` absent): graceful-degrade - run the applicability pass from `CLAUDE.md` plus whatever is present, and note which context was unavailable. Never fail on a missing optional data file.
- The "Where it does NOT fit" section must be non-empty; an empty one is a skill failure - re-read the artifact.

## Routing notes

- This is a single-source deep-read, NOT multi-source research. For "what is the landscape on X across many sources," use `/deep-research`. For a company/industry, use `/research-company` / `/research-industry`.
- The applicability pass is judgment, not a script - it is the whole point. Do not mechanize it away.
- Default bias for Nick's context: most external agent/LLM-tooling artifacts are a useful *principle* wrapped in a *technique built for a scale he does not have*. Lead with that distinction.

## Out of scope for v1

- Non-YouTube video hosts (Vimeo, recorded talks). YouTube transcript ingestion shipped 2026-06-09 via `tools/fetch_transcript.py` (reusable by `/reflect`, `/remember` when those add a video path; only `/analyze` is wired today).
- Social threads (X), recorded talks.
- Multi-source synthesis (that is `/deep-research`).
- The orchestrator-script split and subagent fan-out (documented seams above, built only when the promotion trigger fires).
