# AI Job Search OS

Turn [Claude Code](https://docs.anthropic.com/en/docs/claude-code) into a complete end-to-end job search operating system. From identifying target companies to accepting an offer — pipeline tracking, market research, outreach drafting, tailored CV generation, and interview coaching. No code, just markdown.

## What it does

**Job search operations**
- **Morning briefing** with `/standup` — pipeline health, today's top 3 actions, pending outreach, suggested priority
- **Pipeline tracking** with `/pipe` — stage-by-stage with staleness alerts and per-stage action prompts
- **Task management** with `/todo` — cross-referenced to pipeline and contacts, with daily velocity tracking
- **Weekly retrospective** with `/weekly-review` — pipeline health, outreach response rates, task velocity trend, and Top 5 priorities
- **Autonomous execution** with `/act` — runs careers checks, article reads, and research tasks in parallel

**Market intelligence**
- **Industry research** with `/research-industry` — parallel-agent sector dossiers with executive summary, source tiers, and ranked target company list
- **Company research** with `/research-company` — deep dossiers with conversation starters calibrated to your context (coffee chat, interview, networking)

**Application materials**
- **Tailored CVs** with `/generate-cv` — 11-step workflow, role-specific every time, saved with a companion cheat sheet
- **Cover letters** with `/cover-letter` — hook anchored to company mission, value bridge, close with ask
- **CV review** with `/review-cv` and `/review-cv-deep` (six parallel AI reviewers)

**Outreach & networking**
- **Cold outreach** with `/cold-outreach` — framework-selected, quality-gated, auto-logged to contacts and outreach history
- **Follow-up sequences** with `/follow-up` — sequence-aware, tone-matched, new value each time
- **Contact tracking** with `/networking` — full interaction logs, stale-contact alerts, auto-generated follow-up to-dos

**Interview coaching & preparation**
- **Recruiter screening** and **hiring manager mock** with real-time AI coaching that catches anti-patterns after every answer
- **Full simulation** — uninterrupted call, debrief at the end
- **One-command prep package** with `/prep-interview` — question mapping, company context digest, tactics & logistics
- **Voice simulation** — practice out loud in the Claude mobile app, debrief the transcript back here

## What coaching looks like

All examples from a tough-mode interview with the fictional example profile (Alex Chen). Questions, pressure points, and coached answers are generated automatically from the job ad and project data. Nothing is scripted.

**The coaching loop:** you answer, the coach catches anti-patterns and provides the strongest version:

> **Recruiter:** "The role is heavily Go — it's our primary language for platform services. I see three years on your CV. The engineers on this team have been writing Go for seven, eight years. How would you handle that gap in experience?"
>
> **You:** "Probably not as good as them *laughs*. But I'm a quick learner."
>
> **Coach:** That answer just killed your candidacy. Let me be specific about why:
>
> "Probably not as good as them" — she didn't say you're worse than the team. You said that. She asked how you'd handle the difference. You confirmed yourself as the weakest engineer on the team before you've even interviewed.
>
> "I'm a quick learner" — every single candidate says this. It means nothing.
>
> Anti-patterns triggered:
> - **Confirming the recruiter's concern** — she floated the gap. You confirmed it and made it worse.
> - **Volunteering negatives** — you volunteered that you'd be the weakest.
> - **Generic self-description** — "quick learner" is not a differentiator.
> - **Missing quantitative checkboxes** — you have 3 years of Go. You didn't even state the number or what you built with it.
>
> **Strongest version:** "Three years of production Go — I built the microservice layer at NordPay for their payment platform, and I wrote Go tooling at ShopStream for the internal developer platform. My angle isn't competing on pure language years — it's that I write Go AND design the infrastructure it runs on. Most Go engineers don't think about the platform layer. I do both. Is the team looking for a pure Go specialist, or someone who bridges Go development and infrastructure?"

**Session debrief:** structured feedback and a concrete focus for next time:

> **Dominant patterns:** Volunteering negatives (5 of 8 answers) and not answering the actual question (4 of 8).
>
> **Focus for next session:**
> 1. Write and memorise 15-second recruiter pitch
> 2. Practise gap reframing on 3 common gaps
> 3. Practise direct answer structure — answer first, brief context, stop

## Quick start

### Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and configured (Max subscription recommended — coaching sessions, parallel-agent research, and deep CV reviews are token-intensive)
- Git
- Python 3.8+ (optional, only needed for PDF generation)

### 1. Clone and try the demo

```bash
git clone https://github.com/nickmagnuson2-jpg/claude-job-search-os.git
cd claude-interview-coach
```

The repo includes a fictional profile so you can try features before importing your own data:

```bash
cp -r examples/data/* data/
cp -r examples/output/* output/
```

Then open Claude Code in the repo and try:
- `/standup` for a morning briefing from the example data
- `/review-cv-deep output/sample-cv-cloud-engineer.md` for a six-perspective deep review
- `I want to practice a recruiter screening for a Senior Cloud Engineer role`

### 2. Import your own data

```
/import-cv path/to/your-cv.pdf
```

Extracts structured data into `data/` files (projects, skills, certifications, education, profile). Run it multiple times to merge from different CVs — it's additive.

### 3. Set up your foundation

```
/extract-identity    # discover your strengths, values, and narrative patterns
/setup-goals         # set your search thesis, target criteria, and phase
```

Both are required before generative skills run. `/setup-goals` reads your identity doc, derives what it can, and asks only the fields it can't infer.

### 4. Use it

The typical daily flow:
```
/standup             # morning briefing
/todo                # check open tasks
/pipe                # check pipeline staleness
/weekly-review       # Friday retrospective
```

For applications:
```
/research-company "Company Name"
/generate-cv https://jobs.lever.co/company/role
/cover-letter "Company Name" "Role Title"
/prep-interview "Company Name" "Role Title"
```

## Available commands

| Command | What it does |
|---|---|
| `/import-cv <path>` | Import a CV into structured data files (additive merge) |
| `/extract-identity` | Guided conversation to discover your professional identity |
| `/setup-goals` | Identity-aware goals setup — asks only what it can't infer |
| `/standup` | Morning briefing — pipeline, todos, outreach, suggested priority |
| `/todo [add/done/daily]` | Task manager with pipeline and contact cross-references |
| `/pipe [add/update]` | Application pipeline with staleness alerts |
| `/weekly-review` | Weekly retrospective with pipeline health and outreach metrics |
| `/act` | Autonomous execution of Bucket A to-dos (careers checks, research, articles) |
| `/research-industry "name"` | Parallel-agent industry dossier with target company list |
| `/research-company "name"` | Parallel-agent company dossier with conversation starters |
| `/scan-jobs <portal> [query]` | Scan a job portal for matching roles with fit scoring |
| `/generate-cv <url-or-jd>` | 11-step tailored CV + cheat sheet |
| `/cover-letter <role>` | 3-paragraph cover letter (hook → value bridge → close) |
| `/review-cv <cv-path>` | Fast quality-gate review |
| `/review-cv-deep <cv-path>` | Six-perspective deep review + Top 10 probing questions |
| `/cold-outreach "name" "company"` | Framework-selected first-contact draft, quality-gated, auto-logged |
| `/follow-up "name"` | Sequence-aware follow-up, tone-matched to prior messages |
| `/draft-email [context]` | General email drafting — thank-you, status update, intro request |
| `/networking add/log/view` | Contact tracker with interaction logs and stale-contact alerts |
| `/prep-interview "company"` | 3-agent prep package — questions, company context, tactics |
| `/voice-export <cv> <url>` | Recruiter simulation prompt for the Claude mobile app (voice mode) |
| `/debrief <cv>` | Transcript analysis — rates answers, logs anti-patterns, updates tracker |
| `/remember <note>` | Route a mid-session note to the right data file |

## Repository structure

```
CLAUDE.md              ← Orchestration: tells Claude where everything is
framework/             ← Reusable methodology (workflows, coaching, strategies, outreach guide)
data/                  ← Professional data + search ops data (private once filled)
coaching/              ← Coaching outputs and progress tracking (private once used)
examples/              ← Fictional example data to try features before importing
.claude/skills/        ← 23 Claude Code skill definitions
tools/                 ← PDF conversion utilities
output/                ← Generated CVs, dossiers, cover letters, outreach archives
docs/                  ← Framework documentation
```

## How it works

Four layers:

1. **Data layer** (`data/`): your professional experience and search ops data in structured markdown — projects, skills, goals, pipeline, contacts, todos, outreach log
2. **Methodology layer** (`framework/`): workflows, coaching methodologies, answering strategies, outreach frameworks, research standards
3. **Automation layer** (`CLAUDE.md` + `.claude/skills/`): 23 skills that read and write the data layer following the methodology layer
4. **Output layer** (`output/`): company-first hierarchy — every named entity gets its own subfolder with all related artifacts inside

Claude Code reads `CLAUDE.md` at the start of every session. The `.claude/skills/` folder defines the slash commands. Together, they make the framework work without any code.

Full documentation: **[docs/methodology.md](docs/methodology.md)** | **[docs/usage.md](docs/usage.md)**

## Adapting to your market

Works out of the box for any market and career type. The coaching frameworks (gap reframing, pin-down defense, direct answer structure) are universally applicable.

- Edit `framework/style-guidelines.md` for your market's CV conventions (US, UK, DACH, and international formats included)
- Add answering strategies in `framework/answering-strategies/` for pressure points specific to your domain
- `/scan-jobs` and research skills work with any job portal or company

## License

Dual-licensed:
- **Code** (tools/, .claude/skills/, CLAUDE.md): [MIT](LICENSE)
- **Written methodology** (framework/, coaching/ templates): [CC BY 4.0](LICENSE)

Your personal data in `data/` and `coaching/` is yours. The licenses cover only the framework and tooling.

If this helped, consider starring the repo.

## Credits

Originally created by [Raphael Otten](https://www.linkedin.com/in/raphaelotten/) as an AI interview coaching toolkit.

Extended into a full job search operating system (pipeline tracking, market research, outreach, daily operations) by [Nick Magnuson](https://www.linkedin.com/in/nickmagnuson/).
