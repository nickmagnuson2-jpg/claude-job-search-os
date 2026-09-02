# Example Data — Priya Anand (Fictional)

This directory contains a complete, fictional candidate profile so you can try the framework's features before importing your own data.

**Priya Anand** is a fictional San Francisco professional with an unusual arc: Stanford CS, three years as a software engineer, two years as a management consultant at BCG, an HBS MBA, and three years as a senior product manager. She is now pivoting into **enterprise sales**, using her technical depth and product background as the wedge into a quota-carrying seat. The persona is deliberately a *career-pivot* search, so the examples show how the framework handles positioning a non-linear story.

## How to use

Copy the example data into the repo's working directories:

```bash
cp -r examples/data/* data/
cp -r examples/output/* output/
```

Then try any of the features below.

## Example Invocations

### Interview Coaching

**Recruiter screening with a specific job ad:**

```
I want to practice a recruiter screening for this role: https://example.com/jobs/enterprise-account-executive
```

**Recruiter screening with a pasted job description:**

```
Let's do a recruiter screening practice. Here's the job:

Enterprise Account Executive at a Series C developer-tools company (SF)
Must have: 3+ years closing experience, technical product, $1M+ quota
Nice to have: engineering or product background, MEDDPICC
```

**Recruiter screening with a fictional job profile:**

```
Start a recruiter screening for an Enterprise Account Executive position
at an AI-native data infrastructure startup. Come up with a fictional job profile for it.
```

```
Practice a recruiter call for a Sales Engineer role at a Series B developer-tools company.
Make up a realistic job description.
```

**Mock interview (sales hiring manager, harder on the pivot):**

```
Start a mock interview for an Enterprise AE role.
Come up with a fictional job profile, and play a skeptical VP of Sales who
keeps pushing on the fact that I've never personally carried a quota.
```

The model creates a complete job profile on the fly and then drops straight into character as the hiring manager:

> **Enterprise Account Executive**
> Company: Meridian Data, Inc. -- San Francisco, CA
> Industry: Data Infrastructure / AI | Team: ~12 AEs, founder-led sales
> Comp: $150k base / $300k OTE + equity
>
> Meridian sells a real-time data platform to engineering and data teams at
> mid-market and enterprise accounts. The sales motion is technical and
> consultative, often multi-threaded across a VP of Engineering, a data lead,
> and a CFO. Average deal size $120k ACV, 3-6 month cycle.
>
> **Must have:** 3+ years quota-carrying SaaS sales, comfort selling to technical
> buyers, structured discovery (MEDDPICC or similar), track record above plan
>
> **Nice to have:** engineering or product background, experience selling
> developer tools or data infrastructure

After presenting the profile, the interviewer begins the session immediately. You just answer as yourself.

```
I want to do a mock interview with a hiring manager for a Sales Engineer role.
Create a fictional job profile for an AI workflow-automation company.
```

```
Start a hiring manager mock interview. The role is Enterprise AE at a Series B
fintech infrastructure startup. Make up the details and probe my product-to-sales pivot.
```

**Full simulation (uninterrupted conversation, debrief after):**

```
Run a full simulation for an Enterprise Account Executive screening.
Invent a realistic job profile at a growth-stage B2B SaaS company.
```

### Voice Simulation (for Claude App)

**Generate a voice-mode prompt:**

```
/voice-export output/sample-cv-priya-anand.pdf https://example.com/jobs/enterprise-account-executive
```

After practising in the Claude App, paste the transcript back and debrief:

```
/debrief output/sample-cv-priya-anand.pdf
```

### Generate a tailored CV

The example ships with a ready-made CV (`output/sample-cv-priya-anand.yaml` plus the rendered `sample-cv-priya-anand.pdf`). To regenerate or re-render it with the current pipeline:

```bash
rendercv render output/sample-cv-priya-anand.yaml
```

Or tailor a fresh CV to a specific role:

```
/generate-cv https://example.com/jobs/enterprise-account-executive
```

### Other Features

**Import your own CV (can run multiple times, data merges):**

```
/import-cv path/to/your-cv.pdf
```

**Discover your professional identity:**

```
/extract-identity
```

**Scan a job portal for matching roles:**

```
/scan-jobs builtinsf.com enterprise account executive
```

## Tips

- **No job ad? No problem.** You can always ask the model to invent a fictional job profile. Just describe the role, seniority, industry, and any specifics you want to practise against.
- **Mix and match.** Combine a real CV with a fictional job, or a fictional job with specific constraints ("make the VP of Sales skeptical about a candidate with no quota history").
- **Pivot stories are the hard case.** Priya's profile is a career change on purpose. Use it to practise the objection you'll actually get: "Why should I bet on someone who hasn't done this exact job before?"
- **Plugins change the vibe.** Drop a plugin into `plugins/` to modify interview tone, add industry-specific questions, or adjust coaching style. See `examples/plugins/` for a working example.

## How to remove

When you're ready to use your own data, delete the example files:

```bash
rm -rf data/projects/*
rm -f data/profile.md data/skills.md data/certifications.md data/education.md data/project-index.md data/professional-identity.md
rm -f data/goals.md data/job-pipeline.md data/networking.md
rm -rf output/*
```

Delete these before importing your own data — `/import-cv` is additive and would otherwise merge Priya's history into yours.

Then run `/import-cv path/to/your-cv.pdf` -- one command turns your CV into the same structured data files.
