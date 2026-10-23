Last updated: 2026-10-23

# Getting Started

This is a guided walkthrough from a fresh clone to your first real piece of output. It is meant to be read top to bottom the first time. Once you are running, [usage.md](usage.md) is the per-skill reference and [workflows.md](workflows.md) is the end-to-end flows.

The whole path below takes about thirty to sixty minutes (the identity step is a conversation you can take at your own pace), and you will see the system working on a demo profile before you put in any of your own data.

## Prerequisites

- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** installed. A Max subscription is recommended; the parallel-agent research and deep CV reviews are token-intensive.
- **Python 3.10+** for the atomic write scripts, the CV pipeline, and the hooks. (All scripts run with `python3`, not `python`.) `pyyaml` is required; the other packages in `requirements.txt` are optional.
- **RenderCV** (optional, only for CV PDFs): the CV skills render formatted PDFs via [RenderCV](https://github.com/rendercv/rendercv). Without it, the CV YAML still generates, but there is no PDF, no markdown copy for `/review-cv`, and the layout check is skipped.
- That is all you need for the core. There is nothing to compile. The skills are markdown files Claude Code reads and executes.

### Optional integrations

The system runs without these, but several skills unlock more when they are connected. Each is optional and set up on your side:

- **Wispr Flow** (voice-dictation desktop app): powers the `/wispr` capture path, so a voice dump routes into the right files. Install the app; `/wispr` reads its recent transcripts.
- **Granola** (meeting-notes app): powers the transcript sourcing in `/debrief` and `/follow-up` and the auto-debrief background job. `/granola-pull` is an optional global skill that is not in this repo. Needs the Granola app and its local auth.
- **Exa**: the primary retrieval source for the research skills. `/research-company` and `/research-industry` send five of their six agents to Exa and keep one plain-web-search agent as an independent cross-check. `/discover-companies` (Exa Agent API) and `/scan-contacts` (Exa search) cannot run without `EXA_API_KEY`. Put `EXA_API_KEY` in `.env` at the repo root for `/scan-contacts` and `/discover-companies`. Add the Exa MCP server to Claude Code for `/research-company` and `/research-industry`.

---

## Step 1: Clone and install

```bash
git clone https://github.com/nickmagnuson2-jpg/claude-job-search-os.git
cd claude-job-search-os
python3 -m pip install -r requirements.txt   # pyyaml is required; the rest is optional
```

Open the repo in Claude Code. The session loads `CLAUDE.md` (the project's operating rules). Claude Code also loads its auto-memory `MEMORY.md` from `~/.claude/projects/<slug>/memory/` once you have one. As your memory corpus grows, `MEMORY.md` can become a small router pointing to topic-shard files (`index-<topic>.md` in the same directory) instead of holding every entry directly. See `docs/methodology.md` for the pattern.

> **You should see:** Claude Code start a session in the repo with no errors. If you skipped `pip install`, install at least `pyyaml` before running the CV pipeline or company discovery; the optional packages only add PDF rendering and integrations.

---

## Step 2: See it work on the demo profile first

Before importing your own CV, load the fictional demo data so you can explore real features with nothing at stake:

```bash
mkdir -p data output
cp -r examples/data/. data/
cp -r examples/output/. output/
```

This populates the data layer with a complete fictional candidate, so the generative skills have something to read.

> **You should see:** `data/` fill with profile, goals, projects, and pipeline files, and `output/` gain a few sample dossiers.

---

## Step 3: Your first run

Now ask the system to do something. Start with the morning briefing, which reads the whole data layer and synthesizes it:

```
/standup
```

> **You should see:** a single briefing built from the demo profile, pipeline health, suggested actions, and pending outreach, instead of a generic response. This is the system reading the data layer and reasoning over it.

Then explore what the system produces. A sample company dossier already ships with the demo at `output/larkspur-data/larkspur-data.md`. It is a hand-written sample of what `/research-company` produces (six parallel research agents, conversation starters, a ranked list of similar companies). The research skills (`/research-company`, `/research-industry`, `/discover-companies`) use Exa for primary retrieval, so set up an Exa key (see [Optional integrations](#optional-integrations)) before running them yourself.

Spend a few minutes running skills against the demo data. When you are ready to make it yours, clear the demo data first, because `/import-cv` is additive and would otherwise merge Priya's history into yours (see the cleanup commands in [`examples/README.md`](../examples/README.md)). Then import your own in the next step.

---

## Step 4: Import your own data

Three setup skills turn the system from a demo into yours. Run them in order.

```
/import-cv path/to/your-cv.pdf
```

Extracts your career history, skills, education, and projects into structured `data/` files. It is additive, so you can run it again from a second CV to fill gaps. Expect some files in `data/projects/` to have TODO markers where the import could not extract detail; fill those in, because the richer the project data, the better everything downstream performs.

```
/extract-identity
```

A guided coaching conversation that produces `data/professional-identity.md`: your strengths, growth edges, work style, values, and narrative patterns. This shapes how CVs get written and how coaching evaluates your answers.

```
/setup-goals
```

Reads your identity doc, derives what it can (target industries, non-negotiables), then asks only the fields it cannot infer: compensation, company stage, geography, current phase, and search thesis. Writes `data/goals.md`.

> **You should see:** `data/profile.md`, `data/professional-identity.md`, and `data/goals.md` all populated with real content. These three are the foundation. Generative skills check that profile and goals exist and will stop and send you back here if either is missing or still all TODOs.

---

## Step 5: Your first real output

With your data in place, generate something you would actually use. Find a job posting you care about and run:

```
/apply https://jobs.lever.co/company/role-id
```

This runs the full application campaign: a company dossier (needs Exa; or pass `--skip-research` with an existing dossier under 14 days old), the hiring-manager contact and an outreach brief, then a tailored CV with a companion interview cheat sheet, all under `output/<company-slug>/`, and a pipeline entry. Add `--cover-letter` if the application form requires one.

> **You should see:** research, an outreach brief that waits for your spine, and a role-specific CV. If you have not submitted yet, the pipeline entry is set to "To Apply," not "Applied," and only flips to Applied after you confirm you actually submitted.

That is the loop. From here, one habit matters most: capture context as it arrives with `/wispr` and `/remember`, and run `/standup` to start each day. The more context you add to the data layer, the more specific every output gets.

---

## Where to go next

| You want to | Read |
|---|---|
| Understand the value and the design bet | [why.md](why.md) |
| See full end-to-end flows | [workflows.md](workflows.md) |
| Look up any skill's exact syntax | [usage.md](usage.md) |
| Know what stays private | [privacy.md](privacy.md) |
| Get a quick answer | [faq.md](faq.md) |
| Extend it with plugins or new formats | [customization.md](customization.md) |
