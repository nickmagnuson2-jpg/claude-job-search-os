Last updated: 2026-06-14

# FAQ

Quick answers to questions that come up in real use. For step-by-step setup see [getting-started.md](getting-started.md), for per-skill syntax see [usage.md](usage.md), and for the design rationale see [why.md](why.md).

## Contents

- [Setup and first run](#setup-and-first-run)
- [Privacy and your data](#privacy-and-your-data)
- [Using it day to day](#using-it-day-to-day)
- [Voice and email](#voice-and-email)
- [Forking and extending](#forking-and-extending)
- [Troubleshooting](#troubleshooting)

---

## Setup and first run

**Do I need a Claude Max subscription?**
It is recommended, not required. The parallel-agent research skills and the deep CV reviews fan out into many sub-agents and are token-intensive. On a smaller plan they still run, you will just hit usage limits faster.

**Do I have to install Python?**
Only for PDF generation and the atomic write scripts. The skills themselves are markdown and run without it. Run `pip install -r requirements.txt` when you want PDF output. All scripts use `python3`.

**Can I try it before importing my own CV?**
Yes. Copy the demo profile (`cp -r examples/data/* data/` and `cp -r examples/output/* output/`) and run skills against a complete fictional candidate. See [Step 2 of Getting Started](getting-started.md#step-2-see-it-work-on-the-demo-profile-first).

**Why did a skill stop and tell me to run `/import-cv` or `/setup-goals`?**
That is the profile guard. Generative skills check that `data/profile.md` and `data/goals.md` exist with real content before they run, so they never fall back to a generic candidate. Complete the [setup steps](getting-started.md#step-4-import-your-own-data) and rerun.

**Do I need Exa, Granola, or Wispr Flow to use this?**
No. The core — CV tailoring, cover letters, pipeline, todos, interview prep, the daily loop — runs on Claude Code alone. The optional integrations add reach: **Exa** powers the deep-research skills (`/research-company`, `/research-industry`, `/discover-companies`) and is effectively required for those specific skills; **Granola** pulls meeting transcripts for `/granola-pull` and the debrief/follow-up chain (otherwise just paste a transcript); **Wispr Flow** feeds voice dictation to `/wispr` (otherwise type your notes); **LinkedIn contact-scan** (`/scan-contacts`) needs a scraper. None are required to start. See [Optional integrations](getting-started.md#optional-integrations).

**What actually works on day one with empty data?**
After `/import-cv` and `/setup-goals` (about ten minutes), the generative core works: `/generate-cv`, `/apply`, `/cover-letter`, `/prep-interview`, `/standup`, and the todo/pipeline skills. Research and outreach get sharper as you add company notes and a sent-email corpus, but they run from the start. The honest rule: thin data in, thin output out — the system rewards the data layer you feed it.

**What does it cost beyond my Claude plan?**
The skills themselves are covered by your Claude subscription. Optional third-party services are separately priced: Exa (deep research) and a LinkedIn scraper (contact scan). Skip them and the core search loop still runs.

**Does it run on Linux or Windows?**
The skills, scripts, and hooks are cross-platform (Python + markdown). The *background automation* (gmail-fetch, career-scan, granola auto-debrief) uses macOS `launchd`; on Linux or Windows the interactive skills all work, but you would reimplement scheduling with cron or Task Scheduler.

---

## Privacy and your data

**Is my personal data safe to commit?**
By default, yes. `data/`, `coaching/`, `output/`, and `files/` are gitignored. A fork or clone gets the framework, not your data. Details and a pre-push checklist are in [privacy.md](privacy.md).

**Is there anything that stops me accidentally committing a real name into a public file?**
Yes. An always-on hook (`check_public_pii.py`) blocks a write to any public file (skills, docs, framework, tests, tool code) that matches a real contact or pipeline-target company on your gitignored denylist. Before pushing public-file changes, run `/audit-pii` for a deeper semantic pass that also catches names the denylist hasn't learned yet. See [privacy.md](privacy.md).

**Is my data backed up?**
`/checkout` runs `tools/backup-data.sh` at end of day, pushing your gitignored `data/` to a private backup repo you configure once. After that it is automatic and non-blocking — a backup failure never aborts checkout, and it never touches the public repo. Set the private backup target first; see [privacy.md](privacy.md).

**I want git history for my own data. How?**
Use a private repo and remove the `data/` and `coaching/` lines from `.gitignore` (the file documents exactly which lines). Verify the repo is private first. A public repo with those lines removed exposes everything.

**I committed data before the gitignore was in place. Am I exposed?**
Gitignore only prevents future commits. Anything already committed stays in history. Audit with `git log --all --name-only` and, if needed, rewrite history with `git filter-repo` before going public. See [privacy.md](privacy.md#forking-and-cloning-safely).

**What does Claude actually see during a session?**
Only the files the skills instruct it to read, routed by `CLAUDE.md`. That data is sent to Anthropic's API for processing and is subject to Anthropic's usage and retention policies, which differ by plan type. See [privacy.md](privacy.md#what-claude-sees-during-a-session).

---

## Using it day to day

**What is the difference between `/generate-cv` and `/apply`?**
`/generate-cv` produces just the tailored CV plus a cheat sheet. `/apply` is the full bundle: CV, problem-solution cover letter, and a new pipeline entry, in one command. Use `/apply` when you are ready to apply, `/generate-cv` when you only want the CV.

**What is the difference between `/todo` and `/personal-todo`?**
`/todo` is the job-search list, cross-referenced against your pipeline and contacts. `/personal-todo` is the same idea scoped to your personal vault (household, admin, errands) with no pipeline sync. They are siblings, deliberately separate.

**Why didn't it mark my application as Applied?**
By design. New artifact generation sets the pipeline to "Draft Generated," never "Applied." It only flips to Applied when you confirm you actually submitted, because a phantom Applied row quietly corrupts every view of where your search stands. Run `/pipe update "<company>" Applied` after you submit.

**`/review-cv` or `/review-cv-deep`?**
`/review-cv` is a fast quality gate (keywords, claim integrity, formatting). `/review-cv-deep` runs six reviewers from different perspectives and also surfaces the top ten probing questions the CV would trigger. Use the fast one routinely, the deep one for high-stakes applications.

**How do I keep everything I have learned about a company across several rounds?**
Refresh its dossier additively with `/research-company` (it preserves hand-added interview intel rather than overwriting), and promote the key person to a dossier at `data/people/<slug>.md` via `/networking promote`. See [Flow 4 in workflows.md](workflows.md#flow-4-the-outreach-and-networking-ladder).

---

## Voice and email

**Why did my email draft get blocked or halted?**
One of two guards fired. Either you tried to write email-shaped content outside an approved drafting skill (the system gates email to `/draft-email`, `/follow-up`, `/cold-outreach`), or the Substance-Provenance Audit found pure model-generated text in a self-positioning, bridge, or story sentence and stopped to ask you for the real spine.

**Why can't I just write an email in chat?**
Because that bypasses the voice anchoring and the provenance audit entirely. Email written outside the skills does not get tone-matched to your corpus or checked sentence-by-sentence, which is exactly the value those skills add. Use the skill.

**It keeps removing my em dashes.**
That is intentional. No em dashes appear in anything outgoing; the draft-voice hook scrubs them. Use commas, periods, or hyphens.

**How does it learn my voice?**
Three layers: a corpus of your actual sent email anchors tone (`framework/voice-reference.md`), raw dictation is preserved verbatim before any polish, and every draft is labeled by provenance so model-generated prose cannot silently stand in for your own. With no corpus and no dictation, it falls back to careful neutral prose rather than guessing at your voice.

---

## Forking and extending

**Can I use this for something other than a job search?**
Yes. The job-search content is the example; the architecture is the transferable part, the data-versus-output separation, the hook stack, the atomic write scripts, the framework docs, and the friction log. Adapt those to any domain where you want a Claude-driven system that compounds. [why.md](why.md#who-it-is-for) covers this.

**How do I add domain-specific behavior (a new industry, a stress-test interviewer)?**
Plugins. Drop a self-contained directory in `plugins/` with a `plugin.md` manifest; the framework discovers and loads it with no registration. See [customization.md](customization.md#plugins).

**How do I add a new regional CV format?**
Add a `### Format Name` section to `framework/style-guidelines.md` following the existing International, US, UK, and DACH patterns. Domain-specific CV rules belong in a plugin, not the global style guide. See [customization.md](customization.md#cv-format-rules).

---

## Troubleshooting

**A script crashed with a Unicode error.**
Prefix it with `PYTHONIOENCODING=utf-8`. All `tools/*.py` scripts require it or they crash on Unicode.

**`command not found: python`**
macOS has no `python` binary. Use `python3`.

**My edit failed with "file has been modified since read."**
A script or earlier write changed the file on disk after you last read it. Re-read the file, then edit. This happens most often right after an atomic script (like `networking_write.py`) mutated a file you had open.

**My edit to `job-todos.md` or `job-pipeline.md` silently reverted.**
Those are write-only files; direct edits to long table rows can silently fail. Mutate them through their atomic scripts (`tools/todo_write.py`, `tools/pipe_write.py`) and re-read before any manual edit. A hook warns you when an Edit hits an affected file.

**A hook is blocking me and I am sure it is a false positive.**
The voice, confabulation, and email hooks have override environment flags for rare legitimate bypasses (for example `CONFAB_OVERRIDE=1`, `EMAIL_VIA_SKILL_OVERRIDE=1`). The full list is in [usage.md](usage.md#verification-hook-stack). Use them sparingly; the default is that the hook is right.

**My CV did not render to PDF.**
Install the PDF dependencies: `pip install -r requirements.txt`. The CV pipeline uses RenderCV; the prep-doc and dossier PDFs use weasyprint.
