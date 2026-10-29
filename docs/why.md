Last updated: 2026-10-23

# Why This Exists

Most job-search tools optimize a single artifact: a resume builder, a cold-email generator, an interview-question bank. They tend to start fresh each time you open them, and they do not carry forward what you decided yesterday or how you actually write.

This system is built around a different assumption: that a job search runs for months, accumulates context, and benefits from tooling that remembers. The value is less in any single output than in the data underneath them: a structured record of who you are, what you are targeting, and the conversations you have had, that every skill reads before it writes.

Stated plainly: the outputs are short-lived, and the data layer keeps growing and stays useful. A tailored CV is useful the day you send it. The `data/` files that produced it stay useful as the search continues.

---

## What it actually does for you

Five properties separate this from a folder of prompts. Each is stated as the benefit, not the mechanism. The mechanics are in [methodology.md](methodology.md) and [usage.md](usage.md).

### 1. It accumulates context instead of resetting

Every dossier, decision, and interaction is written to a file that persists. A role-shape spec, for instance, can start as a rough interview hypothesis, get updated after each call, and over a few weeks become the working document that research, follow-up and debrief read from. You stop re-explaining yourself to the tool, and the search keeps a record of what happened.

### 2. It sounds like you, not like a model

Outbound communication runs through a voice system rather than a generic LLM. A corpus of your own sent email (`framework/voice-reference.md`, which you create from the starter in [`examples/framework/voice-reference.md`](../examples/framework/voice-reference.md); it is gitignored, so your sent mail stays local) sets the tone; raw dictation is preserved verbatim before any polish; and every draft produced by the drafting skills is labeled sentence-by-sentence by provenance before you see it ([Substance-Provenance Audit](usage.md#substance-provenance-audit)). Pure model-generated text in a self-positioning or bridge sentence halts the draft and asks you for the real main point. The aim is outreach a recipient reads as a person, not a template. (On a fresh install with no corpus yet, it falls back to neutral prose; the voice sharpens as you add your own sent mail.)

### 3. Recurring errors become guardrails

This is the part most setups skip. When you flag a tooling failure, it is logged to a friction ledger (`tools/friction_log.py`). A weekly background job (`tools/scan_promotion_candidates.py`, also runnable by hand) lists recurring memory rules that have not yet been turned into a skill step or hook, and you decide which become one. The intent is a system that becomes more reliable the more it is used, because its recurring failures get converted into skill steps and hooks.

### 4. It enforces structurally, not by willpower

Several rules are hooks on the tool-call surface: the no-confabulation hook blocks placeholder tokens in prep docs, cheat sheets and project files, and the draft-voice hook blocks a draft containing an em dash until it is fixed. Each hook covers the file paths and tools it is wired to, not every action. Within that scope, a tired late-night session is held to the same standard as a fresh one.

### 5. It keeps your thinking yours

There is a clear separation between what humans write and what agents produce. `data/` holds your source facts and notes, plus logs the skills update through atomic scripts. `output/` holds generated documents. The Obsidian vault excludes `output/` entirely, so generated documents stay out of the place you take notes.

---

## The jobs it gets hired for

A feature list tells you what the system has. These describe the situations it is meant to handle.

> **When I am staring at a job posting late at night,** I want a tailored CV and an outreach brief for the hiring manager (cover letter with `--cover-letter`) without starting from a blank page, so I can apply while the role is still fresh. → `/apply`

> **When a recruiter screen lands tomorrow morning,** I want the company's context and my strongest answers mapped to their likely questions in one page, so I walk in prepared instead of cramming. → `/prep-interview`

> **When I just finished a coffee chat,** I want a thank-you that sounds like me and a follow-up I will not forget to send, so I do not lose touch with the person. → `/draft-email` + `/networking`

> **When I sit down at the start of the day,** I want to know the few things that actually advance my search, so I am not just reacting to whatever hit my inbox. → `/standup`

> **When I have been talking to a company across several rounds,** I want the intel I have already gathered to still be there next time, so I am building on the last conversation instead of repeating it. → `/research-company` (additive refresh) + `data/people/<slug>.md`

The flows that chain these together end to end are in [workflows.md](workflows.md).

---

## Who it is for

The same system serves three readers, because the same features serve all three and only the framing differs.

- **If you want to fork it:** this is a working example of a Claude Code knowledge system that keeps context across sessions, with a skill catalog, a hook stack, atomic write scripts, and framework docs you can adapt to your own domain. The job-search specifics are examples; the architecture applies to other domains. Start with [Getting Started](getting-started.md).
- **If you are looking at it as a work sample:** the system reflects how its author approaches an open-ended operational problem, from separating human input from generated output to enforcing rules with hooks on tool calls. Treat it as one data point alongside the usual ones.
- **If you are running your own search with it:** this is your operations manual. Where things live, what reads what, and how to keep the accumulated data intact are documented, so you can pick the search back up mid-stream without re-deriving the system.

---

## What it is deliberately not

Stated plainly so you do not evaluate it for a job it will not do:

- **Not an autopilot.** It drafts, researches, and organizes. It does not apply to jobs for you, send email without your review, or decide your strategy. Human judgment is the point of the human layer.
- **Not a one-click resume mill.** Tailoring reads your real project data and a real job description. If the data layer is thin, the output is thin. Quality is bounded by what you put in.
- **Not a CRM or an ATS.** The pipeline tracking is lightweight and markdown-based, designed for one searcher, not a recruiting team.
- **Not a voice simulator.** It sets tone from a corpus of your actual writing. With no corpus and no dictation, it falls back to careful neutral prose rather than an imitation of you.

---

## Where to go next

| You want to | Read |
|---|---|
| Get it running from zero | [getting-started.md](getting-started.md) |
| See full end-to-end flows | [workflows.md](workflows.md) |
| Look up a specific skill | [usage.md](usage.md) |
| Understand the internals | [methodology.md](methodology.md) |
| Get a quick answer | [faq.md](faq.md) |
