# Documentation Index

Navigation for all project documentation. The reader-facing docs are organized by the four kinds of documentation (the [Diátaxis](https://diataxis.fr/) lens): a tutorial to learn by doing, how-to guides to accomplish goals, reference to look things up, and explanation to understand the design.

## For Claude (auto-loaded or referenced by skills)

| File | Purpose |
|------|---------|
| [CLAUDE.md](../CLAUDE.md) | Project instructions, loaded every session |
| [memory/MEMORY.md](../memory/MEMORY.md) | Loaded every session — critical context + a router to the topic shards below |
| memory/index-\<topic\>.md | Full memory index, split into topic shards (outreach, coaching, research, tools, system, personal, projects); loaded on demand per the router |
| [memory/lessons.md](../memory/lessons.md) | Correction tracking, reviewed before skill edits, data ops, CV generation |

---

## Start here (tutorial)

Learning-oriented. Read it once, top to bottom, to get running.

| File | Purpose |
|------|---------|
| [getting-started.md](getting-started.md) | Zero to your first real output, with a demo profile to try first |

## How-to guides (goals)

Task-oriented. Read these when you are trying to get a specific thing done.

| File | Purpose |
|------|---------|
| [workflows.md](workflows.md) | The four end-to-end flows: the daily loop, applying, interviewing, the outreach ladder |
| [customization.md](customization.md) | Extend it: plugins, CV format rules, coaching behavior |
| [privacy.md](privacy.md) | Keep data safe: gitignore layout, public-vs-private repos, pre-push checklist |

## Reference (information)

Look-it-up material. Precise, scannable, not meant to be read front to back.

| File | Purpose |
|------|---------|
| [usage.md](usage.md) | Every skill with argument syntax, worked examples, the PDF pipeline, hook override flags |
| [faq.md](faq.md) | Quick answers: setup, privacy, day-to-day, voice, forking, troubleshooting |

## Explanation (understanding)

Discursive background. Read these to understand why the system is shaped the way it is.

| File | Purpose |
|------|---------|
| [why.md](why.md) | The value and the design bet: what it does for you, who it is for, what it is not |
| [methodology.md](methodology.md) | System architecture and capability areas |
| [self-improving-data-framework.md](self-improving-data-framework.md) | How global CLAUDE.md patterns map to project implementations |
| [global-claude-md-snippet.md](global-claude-md-snippet.md) | Project-agnostic behavioral defaults (copy to `~/.claude/CLAUDE.md`) |
| [CHANGELOG.md](CHANGELOG.md) | Version history and feature log |

---

## Framework Files

The methodology docs skills reference instead of embedding rules inline.

> **Note for forks:** a few framework docs are author-private and gitignored in the source repo, so they are not in your clone. They ship as copy-into-place **starter stubs** under [`examples/framework/`](../examples/framework/) — copy them into `framework/` (gitignored) and adapt. Any skill that references a missing framework doc degrades gracefully.

### Application & CV

| File | Purpose |
|------|---------|
| [application-workflow.md](../framework/application-workflow.md) | Tailoring rules, 16-point quality checklist, cheat sheet generation |
| [style-guidelines.md](../framework/style-guidelines.md) | Tone, language conventions, CV format options, Nick's Voice patterns |
| [voice-reference.md](../examples/framework/voice-reference.md) _(starter stub → copy to `framework/`)_ | Voice rules + verbatim exemplars for the email-drafting skills; fill from your own sent-email corpus |

### Interview & Coaching

| File | Purpose |
|------|---------|
| [interview-workflow.md](../framework/interview-workflow.md) | Session workflow, coaching rules, progress logging |
| [framework/answering-strategies/](../framework/answering-strategies/) | Six files covering recruiter call techniques (blank mind, gap reframing, pressure defense, etc.) |

### Problem Solving & Communication Craft

The McKinsey-derived problem-solving (7-step method) and slide-craft methodology docs are author-private and not shipped in the public repo. The skills that reference them (`/prep-interview`, `/debrief`, `/research-company`, `/research-industry`) degrade gracefully without them; the underlying methods are widely-documented public knowledge you can bring your own version of.

### Data & Voice Preservation

| File | Purpose |
|------|---------|
| [two-tier-capture.md](../examples/framework/two-tier-capture.md) _(starter stub → copy to `framework/`)_ | Principle for preserving raw voice separately from synthesized artifacts; referenced by `/debrief`, `/follow-up`, `/cold-outreach`, `/generate-cv`, `/cover-letter`, `/draft-email` |
| [voice-pure-dictation.md](../examples/framework/voice-pure-dictation.md) _(starter stub → copy to `framework/`)_ | The dictation discipline rules |

### Outreach & Networking

| File | Purpose |
|------|---------|
| [outreach-guide.md](../examples/framework/outreach-guide.md) _(starter stub → copy to `framework/`)_ | Email frameworks, personalization logic, channel constraints, anti-patterns |

### Templates

| File | Purpose |
|------|---------|
| [framework/templates/](../framework/templates/) | Starter templates for projects, goals, people, and data files |

---

## Archived

Superseded docs and completed session working-notes live in [archive/](archive/). This includes the dated lessons-audits, build queues, and handoff docs from past development sessions, kept for history but out of the active index.
