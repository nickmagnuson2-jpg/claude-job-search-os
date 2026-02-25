# Usage Guide

Practical how-to for using the framework. For how the system works internally, see [methodology.md](methodology.md). For extending it with plugins and custom formats, see [customization.md](customization.md).

## Getting Started

### First-time setup

1. Import your existing CV:

```
/import-cv path/to/your-cv.pdf
```

This extracts structured data into `data/` files (projects, skills, certifications, education, profile). Run it multiple times to merge data from different CVs.

2. Optionally, discover your professional identity:

```
/extract-identity
```

A guided coaching conversation that produces `data/professional-identity.md` with your strengths, growth edges, values, and narrative patterns. This informs how CVs get written and how coaching sessions evaluate your answers.

3. Review and fill in the generated project files in `data/projects/`. Many will have TODO sections marking areas that need more detail from your memory.

### Want to try it first?

Copy the example data to explore features with a fictional profile before importing your own:

```bash
cp -r examples/data/* data/
cp -r examples/output/* output/
```

See [examples/README.md](../examples/README.md) for example invocations.

---

## Interview Coaching

Provide a job description and ask for a coaching session. You can use a real job ad, paste a description, or ask the model to invent a fictional role.

### Session types

**Recruiter screening** -- practise surviving the recruiter filter:
```
I want to practice a recruiter screening for this role: https://example.com/jobs/cloud-engineer
```

**Hiring manager mock interview** -- practise depth and differentiation:
```
Start a mock interview for a Java developer position. Come up with a fictional job profile for it.
```

**Full simulation** -- uninterrupted conversation, coaching comes after:
```
Run a full simulation for a Senior Infrastructure Engineer screening.
Invent a realistic job profile at a large e-commerce company.
```

Each session type offers **normal** and **tough** difficulty. Tough mode targets your weak points, follows up on gaps, and includes compensation pushback.

### During a session: IC and OOC

Coaching sessions alternate between **in-character (IC)** exchanges with the interviewer and **out-of-character (OOC)** coaching discussion.

**Use `ic:` and `ooc:` prefixes** to make it clear who you're talking to:

- **`ic:`** -- said to the interviewer. They hear it and react to it.
- **`ooc:`** -- said to the coach. The interviewer does NOT hear this.

```
ic: I have about six years of hands-on experience with Kubernetes in production.
```

```
ooc: I'm not sure if I should mention the Azure project here -- it was mostly advisory.
```

Without a prefix, the coach infers from context. But explicit prefixes help when your message mixes an answer with a coaching question, or when it's ambiguous whether you're rehearsing a line or asking for advice.

**Other session conventions:**
- Say **"go on"** to resume the interview after coaching feedback
- Say **`ooc: please wrap up the session`** at any point to end the interview early and move to the debrief/progress logging phase
- OOC is a safe space: admitting gaps or doubts to the coach is intelligence, not a performance failure
- The interviewer only reacts to what you said IC, never to the coach's suggested rewrites

### Voice simulation

For spoken practice using the Claude App (voice mode):

1. **Generate a voice prompt:**
   ```
   /voice-export output/20260210-cloud-engineer.md https://example.com/jobs/cloud-engineer
   ```
   Produces a self-contained recruiter simulation prompt. Copy it into the Claude App.

2. **Practise by speaking.** No typing, no coaching. A realistic 15-20 minute call.

3. **Debrief the transcript:**
   ```
   /debrief output/20260210-cloud-engineer.md
   ```
   Paste the transcript from the voice session. The debrief analyzes your answers against coached versions, identifies anti-patterns, and logs the session.

---

## Generating a CV

Provide a job description and ask for a targeted resume. Three ways to specify the role:

**From a job ad URL:**
```
Generate a CV targeting this role: https://example.com/jobs/senior-devops-engineer
```

**From a pasted job description:**
```
Generate a CV for this role:

Senior Cloud Engineer - FinServ Corp
Requirements: 5+ years AWS, Kubernetes, Terraform, CI/CD pipelines.
```

**From a fictional job profile:**
```
Generate a CV targeting a Senior Platform Engineer role at a mid-size SaaS company.
Focus on Kubernetes, GitOps, and developer experience. Remote, contract, 6 months.
```

The framework matches your experience against the role requirements, selects the most relevant projects, tailors the summary, and outputs a clean markdown CV in `output/`.

### Reviewing a CV

Two review skills catch problems before the CV goes out:

- **`/review-cv <filename>`** -- fast quality gate: structural checks, keyword matching, claim audits, self-sabotage detection. Pass/fail in 30 seconds.
- **`/review-cv-deep <filename> <job-ad-url>`** -- six parallel reviewers (recruiter, hiring manager, competitor analyst, skeptic, copy editor, source data auditor) each assess the CV. Produces severity-rated issues, suggested rewrites, and top 10 probing interview questions.

The filename is just the name (e.g. `20260210-cloud-engineer.md`), read from the `output/` folder.

---

## Skills Reference

Skills are slash commands that trigger multi-step workflows.

| Skill | Arguments | What it does |
|---|---|---|
| `/import-cv` | `<path-to-cv>` (or paste text) | Extract structured data from a CV into `data/` files. Additive -- can run repeatedly. |
| `/extract-identity` | *(none -- interactive)* | Guided conversation to discover professional identity, strengths, values. Produces `data/professional-identity.md`. |
| `/review-cv` | `<cv-filename> [job-ad-url]` | Fast quality-gate review. Structural checks, keyword matching, claim audits. |
| `/review-cv-deep` | `<cv-filename> <job-ad-url> [intermediary]` | Six-perspective deep review. Severity-rated issues, rewrites, and 10 probing interview questions. |
| `/voice-export` | `<cv-path> <job-ad-url>` | Generate a self-contained recruiter simulation prompt for the Claude App (voice mode). |
| `/debrief` | `<cv-path>` | Analyze a voice simulation transcript. Rates answers, identifies anti-patterns, logs to progress tracker. |
| `/scan-jobs` | `<portal> [search terms]` | Scan a job portal for matching roles. Assess fit, deduplicate, output a ranked table. |

CV filenames for `/review-cv` and `/review-cv-deep` are just the filename (e.g. `20260210-draft.md`), read from the `output/` folder. Other skills that take a CV path expect the full relative path (e.g. `output/20260210-draft.md`).

---

## Other Tasks

**Adding new experience:** Create a project file in `data/projects/` (copy `framework/templates/project.md`), add an entry to `data/project-index.md`, and update `data/skills.md` if new skills were used.

**Scanning job boards:**
```
/scan-jobs upwork.com kubernetes devops
```

**Updating professional identity:** After coaching sessions or career reflection, update `data/professional-identity.md` with new insights, or re-run `/extract-identity`.

**PDF generation** (requires Python setup):
```bash
python tools/md_to_pdf.py output/20260210-cloud-engineer.md
```
