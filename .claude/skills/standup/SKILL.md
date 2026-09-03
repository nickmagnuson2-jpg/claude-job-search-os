---
name: standup
description: Morning briefing — pipeline health, today's top 3 actions, pending outreach, corpus state, and a momentum read of the search state
argument-hint: [none]
user-invocable: true
allowed-tools: Read(*), Glob(inbox/*), Glob(data/reflections/*), Glob(data/workbooks/*), Bash(PYTHONIOENCODING=utf-8 python3 tools/pipeline_staleness.py:*), Bash(PYTHONIOENCODING=utf-8 python3 tools/outreach_pending.py:*), Bash(PYTHONIOENCODING=utf-8 python3 tools/networking_followup.py:*), Bash(PYTHONIOENCODING=utf-8 python3 tools/todos_summary.py:*), Bash(PYTHONIOENCODING=utf-8 python3 tools/check_automation_health.py:*), Bash(PYTHONIOENCODING=utf-8 python3 tools/attention.py:*), Bash(PYTHONIOENCODING=utf-8 python3 tools/conversations_metric.py:*), Bash(PYTHONIOENCODING=utf-8 python3 tools/fetch_source_email.py:*), Bash(ls:*), Bash(stat:*)
---

# Standup — Morning Briefing

Reads five data files and generates a focused daily brief. Output is in-chat only — nothing is written to disk.

## Instructions

### Step 1: Load Data

**Run preprocessing scripts (parallel):**
```bash
PYTHONIOENCODING=utf-8 python3 tools/pipeline_staleness.py --target-date $(date +%Y-%m-%d)
PYTHONIOENCODING=utf-8 python3 tools/outreach_pending.py --target-date $(date +%Y-%m-%d)
PYTHONIOENCODING=utf-8 python3 tools/networking_followup.py --target-date $(date +%Y-%m-%d)
PYTHONIOENCODING=utf-8 python3 tools/todos_summary.py --target-date $(date +%Y-%m-%d) --top-n 6
PYTHONIOENCODING=utf-8 python3 tools/check_automation_health.py --repo-root .
PYTHONIOENCODING=utf-8 python3 tools/attention.py --repo-root . --json
PYTHONIOENCODING=utf-8 python3 tools/conversations_metric.py --repo-root . --target-date $(date +%Y-%m-%d)
```
Parse JSON output from each script. If a script returns empty results (missing data file), continue — never fail.

**OUTCOME METRIC (from `conversations_metric.py`) — surface THIRD, and it is the headline number of the brief.**

Nick changed the primary metric on 2026-09-01: *"My metric that I first is outreach: number of outreach
sent. I need to start thinking about interviews and conversations had. I need to actually get those
outcomes up and then determine how I schedule my day based off of how I can get those things."*
**Outreach sent is an INPUT. Conversations and interviews had is the OUTCOME.** Lead with the outcome.

Render exactly one line, immediately under Queues:

```
🎯 **Conversations & interviews had:** 7d **N** · 30d **N** · 90d **N**   (outreach sent: 7d N · 30d N · 90d N)
```

Four rules, all mandatory:

- **Never render a `null` count as `0`.** If `complete: false`, append
  `⚠️ metric incomplete — unreadable: <unreadable_sources>`. A source that silently zeroes turns
  "no conversations" into a lie, which is the one failure this metric exists to prevent.
- **Never compute or state a conversion rate.** The script deliberately does not return one, because
  conversations in a window are not caused by that window's outreach (lag, plus interviews also arrive
  via applications). Show the two counts side by side and let the gap speak.
- **`interviews.untagged[]` is surfaced when non-empty**, as
  `(N progress file(s) have no stage: tag and were not counted)`. Uncounted is not zero.
- **`outcome_counts` are NOT conversations.** They are loop results (offer/rejection news). Mention
  them only if non-zero in 7d, as a separate clause.

**New roles (from `role_queue_read.py`) — surface directly under the outcome metric.**

```bash
PYTHONIOENCODING=utf-8 python3 tools/role_queue_read.py --repo-root . --top 5
```

This is THE drain for the nightly career scan. From 2026-08-11 to 2026-09-02 the
scanner ran daily, scored ~30 roles a night, and **nobody ever saw one**: it wrote
`data/inbox.md` while this skill globbed the `inbox/` DIRECTORY for `*career-scan*`
files that have never existed. Producer healthy, consumer pointing elsewhere, no error
anywhere, for three weeks. Pinned by `tests/scripts/test_role_queue_path_contract.py`.

Render, newest-posted first, only when `new_count` is non-zero:

```
🆕 **New roles:** [N] new · [M] standing
- [7/10] Deployment Strategist — Acme AI (SF) · posted 6d ago · `/apply <url>`
```

Four rules, all mandatory:

- **`exists: false` is NOT zero.** A missing queue means the scan has not run; render
  `⚠️ career scan has not produced a queue — check the job`, never "0 new roles". The
  same null-vs-zero discipline as the Queues block.
- **`is_stale: true` is surfaced** as `⚠️ scan is [N]h old`. A daily job silent for 36h
  is broken, not quiet.
- **`fetch_failures > 0` is surfaced always**, as `⚠️ [N] board(s) failed to fetch:
  [companies]`. Every ATS parser returns `[]` on HTTP error, so without this a scan in
  which every board 404s reports a clean zero. A scan that examined nothing must never
  look like a scan that found nothing.
- **Every role carries its literal `/apply <url>`.** The handoff is the point: surfaced
  → dossier → hiring manager → outreach brief → CV is one copy-paste, with no
  re-derivation of what the scan already knew.
- **`pending_overflow: true` is surfaced** as `⚠️ [N] roles pending acknowledgement`.
  It means the queue is accumulating faster than it is being read, i.e. this step has
  been skipped or failing.

**ACKNOWLEDGE, and only AFTER the roles are rendered above.** The queue is a pending
log: reading does not consume it. If this step is skipped the same roles re-appear
tomorrow (mild noise, deliberate); if it were done BEFORE rendering, a crash mid-render
would lose them for good.

```bash
# Pass the ack_keys from the read above, one --ack per key. Never --ack-all unless
# new_count <= the --top you rendered.
PYTHONIOENCODING=utf-8 python3 tools/role_queue_read.py --repo-root . --ack '<key>'
```

Ack **by key, never by count**: keys identify what was actually put in front of Nick,
so a role a scan inserted between the read and the ack is not silently consumed.

**Cross-model findings (from `cross_model_gate.py`) — surface whenever non-empty.**

```bash
PYTHONIOENCODING=utf-8 python3 -c "
import sys; sys.path.insert(0,'.')
from pathlib import Path
from tools import cross_model_gate as g
print(g.summary(Path('.')))"
```

This is the consumer half of `/codex-verify`. Built 2026-09-03 with the drain still
fresh: a Codex report written to `output/analysis/` and read by nobody is the
career-scan defect in a new costume, and the whole reason findings carry a
`disposition` is so an unresolved one cannot be quietly filed away.

- **Open findings are listed**, most severe first, as
  `🔍 [P0] <summary> — from <report>`. A finding stays open until it is dispositioned
  (fixed / rejected with a reason / parked with a reason).
- **`waivers` is surfaced when non-zero**, as `⚠️ [N] cross-model waiver(s) recorded`.
  Nick's condition for this not being theatre was that skipping is a conscious act; a
  waiver count nobody sees is how it stops being one. A rising count is the signal that
  the gate is being routed around rather than used.
- **Zero open findings and zero waivers renders nothing at all** — same rule as the
  role queue. A daily "0 findings" line trains the reader to skip the section.

Empty queue with `new_count: 0` renders **nothing at all** — a daily "0 new roles" line
trains the reader to skip the section, which is how the original defect stayed invisible.

**This metric drives Today's Top 3 (mandatory).** After computing it, ask: *what would produce a
conversation this week?* If the **7d conversations count is 0 or 1**, Today's Top 3 must be shaped
toward creating conversations — follow-ups on live threads, scheduling asks, warm intros, recruiter
replies — and **not** toward research, tooling, or corpus work, whatever `top_n` ranked. Say so
explicitly in one line: `Top 3 is conversation-shaped: 7d conversations = N.` If 7d is 2+, rank
normally. Origin: 2026-09-01, Nick's metric change, wired here rather than `/weekly-review` because
he does not run that skill.

**Queue depth (from `attention.py`) — surface SECOND, right below automation health.** This is the only place the promotion backlog is ever seen: the weekly scan writes it to `memory/promotion-backlog.md` and a Low-priority todo, and neither is read. Render a single compact block, never a duplicate of the Pipeline/Inbox sections below:

```
📥 **Queues:** inbox N · todos N overdue (oldest Nd) · promotion N (N partial) · pipeline N stale — **total N**
```

Three rules for this block:
- **`complete: false` is surfaced, always.** Append `⚠️ N of 4 queues unreadable: <names>`. A queue that silently drops out turns "nothing needs attention" into a lie — a skipped queue reports `count: null`, never `0`, and you must not render a null as a zero.
- **Every count keeps its denominator** where the JSON provides one (`157 / 160`). A bare count is not a finding.
- **`partial` is called out separately** from the promotion total. A half-landed rule needs its enforcement finished; an untouched one needs a tier chosen. Different work, so don't merge the numbers.

If `total_open` is `null`, say `Queues: UNREADABLE` rather than omitting the line — the absence is the signal.

**Automation health (from `check_automation_health.py`) — surface FIRST if anything is wrong.** This is the independent watchdog: it lives outside the launchd jobs so a scheduler failure is caught even when the jobs (and their own in-process alerts) are dead. If `warnings[]` is non-empty, prepend a block at the very top of the brief, above the date header:
```
⚙️ **Automation health:** [each warning on its own line]
```
The two failure modes it catches: (1) **Gmail fetch stalled** (`last_refresh` older than the threshold) — your email-derived pipeline/inbox data is going stale; (2) **a launchd job is broken** (non-zero last exit). If `warnings[]` is empty, surface nothing (don't add a "healthy" line — keep the brief clean). Origin: 2026-06-15 — all 8 launchd jobs silently died for ~2 weeks (stale `com.apple.macl` xattr on log files after a macOS TCC update → `EX_CONFIG`); the in-job Gmail alert couldn't fire because the job never ran. Fix for a poisoned log is to delete it so launchd recreates it fresh.

**Critical: do NOT bypass `todos_summary.py` by reading `job-todos.md` yourself.** The file is long (often 500+ lines) and silent truncation by Read produces a confidently-wrong Top 3 (origin: 2026-05-13 standup miss — see `memory/feedback_todos_full_file_read_required.md`). The script is the source of truth for ranking; Read `data/job-todos.md` only for cross-checking specific tasks you found in the JSON output.

**Morning-starter check (built into `todos_summary.py`):**
The script also returns `morning_starter.{path, exists, content_preview}`. If `exists: true`, the file at `data/workbooks/_morning-starter-YYYY-MM-DD.md` is the canonical source of today's chain. Read it in full, and use its **Sequence** section to construct Today's Top 3 — overriding the script's `top_n` ranking for the Top 3 itself (the chain is the day's shape; the rest of `top_n` is supporting context). Origin: `memory/feedback_morning_starter_doc_pattern.md`.

**Close the morning-starter read todo (mandatory if morning_starter.exists == true):**
After reading the morning-starter file, close its companion `READ FIRST: data/workbooks/_morning-starter-YYYY-MM-DD.md` todo. `/standup` is the closure point for that todo — nothing else closes it.

```bash
PYTHONIOENCODING=utf-8 python3 tools/todo_write.py done "READ FIRST: data/workbooks/_morning-starter-$(date +%Y-%m-%d).md"
```

Parse the result:
- `status: ok` → report inline at the top of the brief: `✓ Morning-starter read todo closed.`
- `status: error, "No task found"` → skip silently (todo may have been closed in a prior /standup run today).
- `status: error, "Multiple matches"` → display matches; ask Nick which to close.

Origin: 2026-05-14 audit. Skill workflows that consume trigger todos must close them, not just read them.

**Also read in parallel:**
1. `data/goals.md` — current phase, this week's focus, search thesis
2. `data/job-todos-daily-log.md` — daily progress log (for checkout nudge check)
3. `data/decisions.md` (most recent 2-3 entries) + `data/accomplishments.md` (most recent entry) — for the momentum read in Step 2: the latest strategic decision is current-direction context, the latest win is momentum fuel. Skip silently if missing.
4. `data/reflections/_longitudinal.md` (if present) — read the TOP entry only (newest). Surface 1-2 headline lines from its three axes (thinking patterns / decision biases / communication tendencies) as the longitudinal read in the Momentum Read. This is the `/my-world` deep-pass output; skip silently if the file does not exist.

Also check `inbox/` for any captured items: `Glob(inbox/*)` — list filenames only.

**Corpus state check (run in parallel with the above):**
```bash
ls -lt data/reflections/*.md 2>/dev/null | head -20
ls -lt data/projects/zuora.md data/professional-identity.md data/conviction.md 2>/dev/null
ls -lt coaching/coached-answers/*.md 2>/dev/null | head -5
```
Use to compute "unsharpened reflection count" — see Step 2 corpus-state block.

**Checkout nudge check (run after loading daily log):**
Check if an entry exists for **yesterday's date** (look for `### YYYY-MM-DD` header matching yesterday). If no entry for yesterday is found, prepend this one-line nudge at the very top of the brief output (before the date header):

```
💡 Yesterday's checkout wasn't logged — run `/checkout` when wrapping up today.
```

If yesterday's entry exists, skip silently.

### Step 1c: Overnight run (read last night, arm tonight)

The nightly mutation sweep is DETERMINISTIC measurement; the fixing is agent work. This step
closes the loop between them: read what last night measured, then arm tonight's run. Doing
both here is what makes the loop self-perpetuating — a morning that only reads leaves the
sweep to no-op, because it resumes by skipping tools already banked.

```bash
PYTHONIOENCODING=utf-8 python3 tools/mutation_trend.py record --note "overnight <date>"
PYTHONIOENCODING=utf-8 python3 tools/mutation_trend.py show
PYTHONIOENCODING=utf-8 python3 tools/mutation_report.py
```

**Read `record`'s status.** `skipped` means the baseline has not changed since the last
recorded point — the sweep did not run, or ran and banked nothing. That is a finding, not a
no-op: say so in the brief rather than reporting the old number as if it were fresh.

**Arming tonight** (only when the sweep completed — `mutation_report` shows full coverage):

```bash
cd output/analysis/082626-mutation-baseline
cp baseline.jsonl baseline.pre-$(date +%m%d%y).jsonl     # snapshot, never overwrite
cd - && PYTHONIOENCODING=utf-8 python3 tools/mutation_sweep.py --targets
: > output/analysis/082626-mutation-baseline/baseline.jsonl
```

`--targets` is not optional on a rebuild: it is what re-derives the tool list (a tool only
enters the sweep once it has a collected suite, so ported tests ADD targets — 110 → 125 on
2026-09-02) and what populates the `own` field.

**Do NOT arm while a sweep is in flight** (`pgrep -f mutation_check`). Clearing the results
file under a running sweep loses the night.

### Step 1b: Read Manifest (mandatory before synthesis)

Before producing the brief in Step 3, emit a Read manifest as an internal-check step:

```
## Read manifest
**Files Read in full this session:**
- [each file path actually called via Read tool]

**Files referenced by grep/snippet/filename only (NOT Read in full):**
- [list, or "(none)"]
```

**Rule:** Do not make content claims about any file in the second list. If the brief requires content from a not-yet-read file, Read it first. Partial reads (head, offset+limit, grep snippets) count as "snippet only," not "full Read."

This manifest is internal-check; do not surface it in the user-facing brief unless the user asks. But if any content claim in the brief cites a file from the second list, the brief must be revised before output.

Origin: [[feedback_no_confabulation_in_corpus_synthesis]] (Conviction Workbook Part 1 audit 2026-05-12). The manifest is the structural check that prevents filename/snippet inference from masquerading as content analysis.

### Step 2: Analyze Each Data Source

**From goals.md:**
- Extract: current phase (Exploring / Active / Interviewing / Negotiating)
- Extract: this week's top 3 focus items
- Extract: search thesis (one-sentence version)
- If goals.md is all TODOs or missing: flag "⚠️ goals.md not populated — run `/standup` after filling in `data/goals.md`" and skip goals-dependent sections

**From pipeline_staleness.py JSON:**
- `stalled_entries[]` — each entry has: `name`, `role`, `stage`, `days_since_update`. Use for the "Attention Needed" list.
- Suggested action per stage: Researching → "run `/research-company` or move on"; Applied → "follow up or check status"; Screening → "send thank-you / follow up"; Interview → "follow up on timeline"; Offer → "respond or negotiate"
- `stage_distribution{}` — use for the pipeline snapshot count (N per stage)
- `metrics.total_active` — total active entries

**From `todos_summary.py` output:**
- `upcoming_scheduled[]` — **pin these FIRST, before any todo ranking.** Interviews/screens/calls parsed from the pipeline Next-Action column within the next 3 days; they live in free text, not dated todos, so `top_n` is blind to them. **Any entry with `is_event: true` is a fixed commitment that takes Today's Top 3 slot #1** (earliest date first), even ahead of the morning-starter chain when it falls today. Show as `[Company] [role] — [event] ([date], [days_until]d)`. Empty list → skip. Origin 2026-06-11: a next-day interview in the pipeline Next-Action was missed by standup — see `memory/feedback_surface_scheduled_events_in_ranking.md`.
- `top_n[]` is already sorted by (priority asc, distance-from-today asc, overdue-tiebreak). Use it directly, **below any `upcoming_scheduled` pins.**
- If `morning_starter.exists` → Read the file in full and use its Sequence section as Today's Top 3 (the chain is the source of truth). The `top_n` items are supporting context shown below the chain. (An `is_event: true` item dated today still pins above the chain — a real interview beats the planned chain.)
- Else → take the first 3 entries of `top_n` for "Today's Top 3"
- `total_pending`, `overdue_count`, `high_priority_count` go in the footer line.

**Staleness sanity check on Today's Top 3 (mandatory, cheap):** before presenting, fuzzy-match each of the (up to) 3 chosen task strings against the entries already loaded for the Momentum Read — the 2-3 most recent `data/decisions.md` entries and the most recent `data/accomplishments.md` entry (Step 1, item 3) — plus any filenames seen in the corpus-state `ls` output (Step 1 corpus-state check). A likely match is a shared distinctive noun/entity/deliverable name, not topic-level resemblance (e.g. a todo saying "ship N projects" against an accomplishments entry naming that same project as shipped). If a candidate looks done, do not present it as fact in Today's Top 3 — either drop it in favor of the next-ranked `top_n` item, or present it flagged: `⚠️ [task] — this may already be done (per [source]); confirm before treating as open`. This is a read-only flag; `/standup` never writes closures itself (`/checkout`'s Step 4c#5 owns the actual close). Origin: 2026-07-09 — two already-completed High-priority todos (one done weeks earlier, one the day before) were presented as live Top 3 items and Nick had to correct them live.

**From outreach_pending.py JSON:**
- `awaiting_response_overdue[]` + `awaiting_response[]` — sorted by `days_since_sent` descending (oldest first)
- Each entry has: `name`, `company`, `channel`, `days_since_sent`
- Show overdue entries first, then non-overdue awaiting entries
- **Do NOT surface the response-rate metric** (`recent_outreach.response_rate_percent`). Nick doesn't track it (decided 2026-06-15) — it added noise without changing behavior, and the underlying formula had a history of being wrong (the `replied/(sent−replied)` bug reported 93% for a true 48%, 2026-06-02). Use `awaiting_response*[]` for the nudge lists; ignore the rate. Raw `sent`/`replied` counts are fine if a momentum sentence needs volume, but never compute or state a percentage.

**From networking_followup.py JSON:**
- `followup_overdue[]` — contacts with overdue follow-ups (show first)
- `followup_due[]` — contacts with follow-ups due within 7 days (show after overdue)
- Each entry has: `name`, `company`, `follow_up_action`
- `suppressed_closed[]` — nudges withheld because the contact's company reached a
  terminal pipeline stage AND the contact has not been touched since that close. Each
  carries `close_date` and `suppression_reason`. **Report the count as a single line
  under Follow-Up Due, never the full list:**
  ```
  🔇 N nudge(s) suppressed — company closed, no contact since. Run `/networking` to review.
  ```
  Do not silently drop them: a suppressed nudge is still a row someone may want to
  retire properly. Suppression is display-only and writes nothing.

  **A contact touched *after* the close is never suppressed** — that touch is deliberate
  relationship work (the "close the loop, buy you a beer" text) and outlives the
  opportunity. Origin: 2026-05-13 (4 ghost rows in one standup, parked 3 months) and
  2026-08-14, when a recruiting coordinator surfaced as due-today with prep
  instructions for a loop whose company had closed four days earlier.

**From inbox/:**

The `inbox/` folder is fed by launchd automation (gmail-fetch, gmail-fetch-personal, alirohde-triage, granola-auto-debrief) PLUS ad-hoc captures. Surface them by category so the feeds actually surface — don't just count. (The dossier-freshness and follow-up/weekly-review nudge jobs were retired; those category patterns below now only match hand-dropped files, so they may stay empty.)

Run:
```bash
ls inbox/ 2>/dev/null | grep -v "^README"
```

Categorize each filename by pattern:
- `*dossier-freshness-alert*` → **Dossier freshness** category
- `*follow-up-nudge*` → **Follow-up nudges** category
- `*weekly-review-reminder*` → **Weekly review reminders** category
- `GMAIL-AUTH-FAILURE*` or `*-AUTH-FAILURE*` → **System alerts** category (always surface)
- Anything else → **Captures** category (raw notes, emails, ad-hoc inputs)

For the **two most recent** files in each non-empty category (excluding System alerts — always read all of those), peek at content with `head -20` and extract the key signal:
- Dossier freshness: which companies are stale
- Follow-up nudges: which contacts are due
- Weekly review: confirm date
- Career-scan: which roles surfaced

Skip categories with zero items.

**Corpus state — sharpening loop check:**

This section enforces `memory/feedback_volume_as_cope_yellow_flag.md` (2026-05-08 refinement: corpus-with-sharpening-loop is OK; cold corpus when loop is dormant is the actual risk).

1. **Find the most recent sharpening trigger.** Take max of these mtimes (skip if file missing):
   - `data/projects/zuora.md`
   - `data/professional-identity.md`
   - `data/conviction.md` (if exists)
   - Newest file in `coaching/coached-answers/*.md`
   - Newest file in `output/**/cheat-sheet*.md` (most recent prep-interview output)
   Call this `last_sharpening_at`.

2. **Count unsharpened reflections.** Files in `data/reflections/*.md` **excluding any underscore-prefixed file** (e.g. `_themes.md`, `_longitudinal.md`, `_stoic-prompts.md`) with mtime > `last_sharpening_at`. Call this `unsharpened_count`.

3. **Find next interview/outreach in 7 days.** From pipeline next-actions and networking_followup output, identify any interview, recruiter call, or scheduled outreach within 7 days from today. Call this `next_use`.

4. **Decide what to surface:**
   - `unsharpened_count == 0` → "Corpus is current — last sharpening pass was N days ago." (1-line, healthy)
   - `unsharpened_count >= 1 and unsharpened_count <= 2` → "Corpus has N new reflection(s) since last sharpening. Light load." (informational, no action)
   - `unsharpened_count >= 3` AND `next_use` exists → recommend a sharpening pass tied to that use: "N unsharpened reflections + [next_use] in [days] days → run `/prep-interview [company]` or `/voice-export [role]` to pull fresh material in."
   - `unsharpened_count >= 3` AND no `next_use` within 7 days → soft prompt: "N unsharpened reflections accumulated. No upcoming use to anchor to — corpus is fine to keep growing, but flag for next weekly review if it crosses 5+ entries."

5. **List the unsharpened reflection filenames** (just the dated stems, max 5) so Nick can see what's queued for the next sharpening pass.

### Step: Reflection staleness + commonplace intake

Two cheap checks. Both are **nudges, never questions** — standup does not ask reflection questions
inline. Reflection needs its own space; the only job here is to stop it going invisible.

**a) Days since the last reflection.**

```bash
ls data/reflections/[0-9]*.md | sort -r | head -1
```

Take the leading `YYYY-MM-DD` from the basename (NOT the mtime — files get edited later, which would
reset the clock and hide a real gap). Compute days since. **Surface a line only when it is 5+ days.**
Under 5, print nothing at all: a daily "you reflected 2 days ago" is noise that trains him to skip the
whole block. Ignore `_`-prefixed files (`_themes.md`, `_stoic-prompts.md` and friends are not
reflections). If the glob matches nothing, skip silently.

**b) Unreacted commonplace intake.**

```bash
PYTHONIOENCODING=utf-8 python3 tools/fetch_source_email.py --label "Interesting thoughts" --list --json --repo-root . --max 40
```

Read-only: it enumerates the Gmail label and diffs it against `data/source-emails/` by Message-ID.
Writes nothing. Parse the JSON and use `unpulled_count`.

- This makes a network call. **On any failure, non-zero exit, or unparseable output, skip the block
  silently** and continue standup. Never fail the brief on it.
- If `source_dir_exists` is false, say so instead of reporting the count: every message reads as
  unpulled when the directory is missing, which is a false alarm, not a backlog.
- **Report the count, do not list the subjects.** The brief stays scannable, and the subjects are
  real correspondents.

**Why this is here at all:** the intake path existed since 2026-06-13 and went dormant after one day,
because the tool required `--match` and there was no way to ask what was sitting unread. This
reverses the earlier deliberate "manual-first, do not automate surfacing" decision in
`memory/project_commonplace_book_system.md`. Nick called it on 2026-09-02 with the dormancy as the
evidence. **Surfacing only — never auto-pull, never auto-write a take.** The take is voice-pure and
stays his (`memory/feedback_commonplace_take_is_voice_pure.md`).

### Step: Daily Stoic prompt

1. Archive any new meditation and read state:

   Run: `PYTHONIOENCODING=utf-8 python3 tools/daily_stoic.py --sync --repo-root .`

   Parse the JSON. If `newest_id` is null, skip this whole step silently (no email yet, or tool error - graceful degradation; never fail standup on this step).

2. If `already_prompted` is true, skip silently (today's meditation already got a prompt this cycle).

3. Otherwise read the archive file at `newest_path` (relative to repo root). Inside the `<email-content>` block, the meditation is mixed with promotional header/footer text (tour, ticket links, bare URLs). Extract ONLY the meditation: the reflective prose paragraphs. Ignore ticket/tour/CTA lines and URLs. Distill Ryan's core point into ONE sentence.

4. Craft a **prompt for Nick**:
   - One line: Ryan's theme, plainly stated.
   - One pointed question. **Framing: follow what's alive for Nick.** Read `data/job-pipeline.md`, the two most recent dated `data/reflections/*.md` files, and the top of `data/reflections/_themes.md` to sense what is current. Usually the question connects to the search; when a search angle would be forced (a meditation about mortality, family, friendship), let it be a pure life/personal prompt. NEVER manufacture a job-search connection on a meditation that is not about work.
   - No em dashes. Beats, not a polished script (per `feedback_give_nick_beats_not_a_polished_script`). Keep it short.

5. Surface it in the briefing under a distinct **"Stoic prompt"** sub-head, placed near the Momentum Read (not competing with the top-3 actions). Show the catch-up note ONLY when BOTH conditions hold: `new_since_last_prompt > 1` AND `had_prior_prompted_id` is true (from the tool's JSON). When `had_prior_prompted_id` is false (first-ever prompt, after backfill), suppress the catch-up count entirely -- the backlog is a backfill artifact, not a real gap.

6. Append the prompt to `data/reflections/_stoic-prompts.md` (newest first; the file already has a header). Entry format:

   ```markdown
   ## YYYY-MM-DD -- "<meditation subject>"

   **Theme (Ryan):** <one sentence>
   **Prompt for you:** <one pointed question>
   Source: [[<archive-file-basename-without-extension>]]
   ```

7. Record that the prompt fired:

   Run: `PYTHONIOENCODING=utf-8 python3 tools/daily_stoic.py --mark-prompted <newest_id> --repo-root .`

**Error handling:**
- Steps 1-6 (before anything is shown to Nick): if any sub-step errors, skip the entire stoic block silently and continue standup.
- Step 7 (`--mark-prompted`, runs AFTER the prompt is already surfaced and logged): a failure here must NOT be silent. Add an inline warning in the briefing: "Note: stoic prompt shown but state update failed; it may reappear next standup." Nick needs to know the state is out of sync.

### Step 3: Generate the Brief

Output the brief in this exact format:

```markdown
## Morning Brief — [Today's date, spelled out: e.g., Tuesday, February 25]

**Search thesis:** [one sentence from goals.md, or "— not set" if missing]
**Current phase:** [phase from goals.md, or "— not set"]

📥 **Queues:** inbox [N / N] · todos [N] overdue (oldest [N]d) · promotion [N] ([N] partial) · pipeline [N] stale — **total [N]**
[If complete=false: ⚠️ [N] of 4 queues unreadable: [names]]
[If total_open=null: **Queues: UNREADABLE** — say this, never omit the line]

🎯 **Conversations & interviews had:** 7d **[N]** · 30d **[N]** · 90d **[N]**   (outreach sent: 7d [N] · 30d [N] · 90d [N])
[If complete=false: ⚠️ metric incomplete — unreadable: [sources]]
[If interviews.untagged non-empty: ([N] progress file(s) untagged, not counted)]

---

### This Week's Focus
[Numbered list from goals.md weekly focus section, or "— goals.md not populated" if missing]

---

### Pipeline — Attention Needed
[For each stale item:]
- **[Company]** — [stage] for [N] days → [suggested action]

[If no stale items:]
> Pipeline looks healthy — no items overdue for action.

**Snapshot:** [N] Researching · [N] Applied · [N] Screening · [N] Interviewing · [N] Offer

---

### Today's Top 3
[If 7d conversations <= 1, first line is: **Top 3 is conversation-shaped: 7d conversations = [N].**]
1. [Highest priority todo with due date if set]
2. [Second priority]
3. [Third priority]

> [N] total pending to-dos — run `/todo` to see the full list.

---

### Reflection & Reading

[Only render this section if at least one of the two lines below fires. If both are silent, omit the
whole section including its heading -- an empty section is a daily reminder that nothing is wrong.]

[If days since the newest dated reflection is 5+:]
> 🪞 Last reflection was [N] days ago. `/reflect` when you want it.

[If unpulled_count >= 1:]
> 📚 **[N] unread in "Interesting thoughts"** — `/analyze` or pull one into `data/source-emails/`. Each needs your own take before it counts.

[If source_dir_exists is false:]
> ⚠️ `data/source-emails/` not found — the unread count is unreliable (everything reads as unpulled).

---

### Corpus State

[If `unsharpened_count == 0`:]
> Corpus is current — last sharpening pass [N] days ago.

[If `unsharpened_count == 1` or `2`:]
> [N] new reflection(s) since last sharpening pass. Light load.

[If `unsharpened_count >= 3` AND a `next_use` exists within 7 days:]
> ⚡ **[N] unsharpened reflections** + [interview/call name] in [N] days → recommend running `/prep-interview [company]` or `/voice-export [role]` to pull fresh material in.
> Queued reflections: [list 5 most recent dated stems]

[If `unsharpened_count >= 3` AND no `next_use` within 7 days:]
> [N] unsharpened reflections accumulated, no upcoming use to anchor to. Fine to keep growing — flag for next weekly review if it crosses 5+.
> Queued: [list dated stems]

---

### Overnight Run

[If the sweep completed and the trend advanced:]
> **Guard coverage: [pct]% of decisions unprotected** ([delta] since [last date]).
> [N] tools measured, [K] fully clean, [M] with no verdict.

[If any tool's survivor count ROSE — this is the only line here that should interrupt the day:]
> ⚠️ **Survivors rose in [N] tool(s):** [tool] [old]→[new]. A rise means a behaviour lost its
> test, not that the tool got worse. Check before anything else.

[If `mutation_trend record` returned `skipped`:]
> ⚠️ **The sweep did not run last night** — the baseline is unchanged since [date]. Check
> `launchctl print gui/$(id -u)/com.nickmagnuson.jobsearch.mutation-sweep | grep "last exit"`;
> exit 78 means a log-file provenance problem, not a code problem.

[Open decisions, from the two gate allowlists — this is the queue Nick answers:]
> **Needs your call:** [N] wired hook(s) with no suite (`KNOWN_MISSING`), [M] orphaned test
> file(s) (`KNOWN_ORPHANS`). Both empty = the backlog is drained.

---

### Awaiting Response
[For each outreach awaiting reply:]
- **[Name]** @ **[Company]** — sent [N] days ago ([channel: LinkedIn/email])

[If nothing pending:]
> No outreach awaiting response (last 30 days).

---

### Follow-Up Due
[For each contact with overdue follow-up:]
- **[Name]** ([Company]) — [what to do]

[If nothing due:]
> No follow-ups overdue.

---

### Inbox

[If categorized items exist, surface them by category — don't just count. Format:]

**[Total N] items in inbox/** — categorized below.

[For each non-empty category, output a labeled bullet group:]

- **🔔 Follow-up nudges (N)** — most recent contacts due:
  - [Contact 1]: [reason / context from peek]
  - [Contact 2]: [reason / context from peek]
- **📊 Dossier freshness (N)** — companies with stale research:
  - [Company A, B, C — list up to 5 names, comma-separated]
- **📅 Weekly review reminders (N)** — most recent: [date / week]
- **⚠️ System alerts (N)** — [always list all in full, e.g., "GMAIL-AUTH-FAILURE 2026-05-10"]
- **📝 Captures (N)** — [count only; route via `/act` or `/remember`]

[If total exceeds 15, add this line:]
> Inbox is heavy. Consider a triage pass — `/act` to route, or bulk-delete stale system nudges.

[If inbox is empty:]
> Inbox clear.

---

### Momentum Read
[1–2 honest sentences assessing search momentum based on the data above.
Be direct — if momentum is stalling, say so. If it's strong, say that.
Base it on: pipeline activity, outreach cadence (are sends going out, are replies coming back), todo completion pace. Do not cite a response-rate percentage.

**Lead the momentum read with the outcome metric, not with activity.** The first sentence names
conversations-and-interviews-had over 7d and 30d and whether that is moving. Outreach volume, todo
pace, and pipeline motion are supporting detail *behind* that number, never a substitute for it. If
7d conversations is 0, say that plainly in the first sentence — a busy week with zero conversations is
a stalled week, and reporting the busyness first is how that gets obscured.

**Engine vs output (mandatory — classify the last 7 days before writing the sentences).**
Split the last 7 days of completed work into two columns internally; print the split only when
the output column is thin, otherwise let it shape the momentum sentences.

| Output — moved toward a job | Engine — improved the system |
|---|---|
| applications sent, calls taken, prep done, outreach sent, an artifact Nick can *speak from* | skills, hooks, tools, docs refactors, memory hygiene, dashboards |

Three calls this has to get right, or the classification is worse than not doing it:

- **Package-to-speak is OUTPUT, refactor-to-organize is ENGINE, and they feel identical from the
  inside.** Articulating principles so Nick can use them in an interview is output. Cleaning up the
  OS docs that hold those principles is engine. The tell is the phrase *"codify / organize /
  refactor my OS."*
- **Clear-mind activity counts as output.** Walking Phil, yoga, Peloton — when they bring his best
  self they are not the absence of work. Never score them as slack.
- **Engine weeks are not automatically bad.** `goals.md` Search Principle 4 arbitrates, in its
  stated order: (1) did it displace the morning foundational window? (2) was a judgment or ship rep
  available and avoided? (3) otherwise it compounds legitimately, and the energy is a signal, not a
  guilt trigger. The crowding-out test wins ties.

**If the 7-day output column is thin, say so plainly and make Today's Top 3 output-shaped.** Nick
asked to be held to this. This lives here, on the daily surface, because the identical check at
`/weekly-review` Step 5c is anchored to a skill he does not run reliably — per
[[user_nick_invokes_standup_not_weekly_review]] and [[feedback_verify_the_surface_fires_before_anchoring_to_it]].
Rolling 7 days rather than a calendar week so a daily invocation needs no stored state.
Examples:
- "Pipeline is active with 4 companies in motion, but 2 applications are stale — outreach would unblock both."
- "Search has slowed — no new applications this week and 3 todos are overdue. Worth a focused session today."
- "Strong week — pipeline moving at all stages and outreach follow-ups are current."]

---

### Stoic Prompt
[Include this section only when the Daily Stoic step fires (newest_id is non-null, already_prompted is false).]

**Theme (Ryan):** [one sentence distilling the meditation's core point]
**Prompt for you:** [one pointed question, grounded in what's alive for Nick]

[If new_since_last_prompt > 1 AND had_prior_prompted_id is true (from tool JSON):]
> [N] new meditations archived since last standup; prompting on the newest.

[Otherwise -- including when had_prior_prompted_id is false (first-ever prompt) -- omit the catch-up note.]
```

### Step 4: Suggest One Action

After the brief, add a single suggested action — the one thing most likely to move the search forward today based on what you found:

```
**One thing:** [Specific, actionable — e.g., "Follow up with Acme AI (applied 6 days ago) — run `/follow-up 'Acme'`" or "Process inbox items before anything else — run `/act`"]
```

## Edge Cases

- **goals.md all TODOs:** Proceed with other sections; note goals.md is unpopulated at the top of the brief and skip the thesis/phase/focus sections.
- **job-pipeline.md missing:** Skip pipeline section, note "pipeline.md not found — run `/pipe add` to start tracking."
- **job-todos.md missing or empty:** Show "No active to-dos — run `/todo add` to capture actions."
- **outreach-log.md missing:** Skip awaiting-response section, note "No outreach log found."
- **networking.md missing:** Skip follow-ups section.
- **All files missing:** Display "⚠️ No data files found. Start with `/import-cv` to populate your profile, then `/pipe add` to track your first application."
- **No stale pipeline items:** Show the healthy message.
- **No todos:** Note it and suggest adding some.
