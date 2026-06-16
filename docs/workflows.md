Last updated: 2026-06-14

# Workflows

This is the how-to layer: the system's skills chained into the end-to-end journeys an actual search runs on. Each flow opens with the situation it is for, walks the steps in order, and tells you what you should see after each one. For per-skill argument syntax, see [usage.md](usage.md). For why the system is built this way, see [why.md](why.md).

A note on naming: these flows are organized around what you are trying to accomplish, not around how the skills are filed. A skill can appear in more than one flow.

**If you just cloned the repo,** these flows assume you have already done first-time setup. Start with [getting-started.md](getting-started.md) (import your CV, identity, and goals), then come back here — the daily loop below is where you end up *after* setup, not where you begin.

## Contents

- [Flow 1: The daily loop](#flow-1-the-daily-loop)
- [Flow 2: Apply to a role, end to end](#flow-2-apply-to-a-role-end-to-end)
- [Flow 3: Win the interview: research to debrief](#flow-3-win-the-interview-research-to-debrief)
- [Flow 4: The outreach and networking ladder](#flow-4-the-outreach-and-networking-ladder)
- [Evaluating what to adopt](#evaluating-what-to-adopt)
- [The capture habit that feeds all four](#the-capture-habit-that-feeds-all-four)

---

## Flow 1: The daily loop

> **When I sit down at the start of the day,** I want to know the three things that actually move my search, and at the end of it I want today's progress and tomorrow's priorities captured, so I am running the search instead of reacting to my inbox.

This is the spine of the system. These are the skills a search touches most days, because a search is mostly a daily rhythm rather than a sequence of big events.

### Morning

```
/standup
```

Reads your goals, pipeline, todos, outreach log, and networking contacts in parallel and returns one briefing: pipeline health with staleness alerts, today's top three actions cross-referenced to the relevant companies and contacts, pending outreach with follow-up sequence positions, and one suggested priority. An interview, screen, or call scheduled within the next three days is pinned to the top automatically, even when it lives in your pipeline's Next-Action text instead of a dated to-do, so a next-day interview can't hide.

> **You should see:** a single screen that replaces manually opening five files. If a contact replied but the outreach log still says "Sent," standup catches the mismatch. Outreach on threads you have already closed or settled is filtered out, so standup stops flagging resolved conversations as still awaiting a reply.

### During the day, as things land

```
/wispr                 # pull in voice dictations
/remember <note>       # route a typed note to the right file
```

Context arrives all day: a recruiter mentions a second open role, you have a thought on the commute, a call goes well. Capture it the moment it happens. `/wispr` pulls your voice dictations into the session; `/remember` routes a note to its correct home (a contact note to networking, a company note to its dossier, a strategic decision to `data/decisions.md`). You are not filing; you are dropping, and the system routes. You do not have to watch your inbox either: a background fetch pulls job-tagged email every fifteen minutes and sends one batched desktop notification when new ones land, prompting you to run `/act` to triage them.

> **You should see:** notes landing in the right files automatically, so nothing you learned today is lost by tomorrow.

### Acting on the list

```
/act          # optional: auto-execute the runnable to-dos
/todo         # or work the list manually
```

`/act` previews your to-do list, splits it into items it can run (career-page checks, article reads, company research) and items only you can do (calls, in-person), and on your confirmation runs the first set in parallel.

### End of day

```
/checkout
```

The bookend to standup. Builds today's progress snapshot (completion rate, streak, velocity trend), logs it, and surfaces tomorrow's top three cross-referenced against the weekly review's priorities, with any interview or screen scheduled in the next three days pinned ahead of every to-do. It proposes any calls from today that have not been debriefed yet, and proposes at most one or two milestone-level wins from the day's real artifacts for you to confirm into your accomplishments log (a strict bar, so most days produce none, and nothing is ever auto-logged). It runs a silent-failure probe that asks whether anything produced today looked right but rested on a wrong assumption, then, as its closing action, pushes an automatic backup of your private data, so the end-of-day snapshot is captured without a manual step.

> **You should see:** a clean close. Tomorrow's standup starts from today's real state, not from memory.

**Weekly:** run `/weekly-review` once a week for pipeline health by stage, outreach response rates, task velocity, and the coming week's top five.

---

## Flow 2: Apply to a role, end to end

> **When I find a role worth pursuing,** I want to go from job posting to a submitted-quality application without starting from a blank page, so I can apply while the role is fresh and the tailoring is real, not generic.

### Step 0: Find the target (when you need new ones)

```
/discover-companies "vertical SaaS for the trades"
/scan-companies
```

Most of the time you start this flow from a role you already found. When you need fresh targets, `/discover-companies` uses Exa Websets to surface companies you are not yet tracking, scores them against your thesis (geography as a hard gate, then stage, sector, and lane-keyword fit), and proposes the survivors to your inbox; `/scan-companies` then checks the career pages of your configured targets for live roles. (`/discover-companies` needs an Exa Pro key — see [getting-started.md](getting-started.md#optional-integrations); `/scan-companies` works on the standard setup.) Discovery finds the company, the scan finds the role.

> **You should see:** new, thesis-fit companies and roles waiting in `data/inbox.md` to triage, instead of a blank page when you go looking for where to apply next.

### Step 1: Know the ground (optional but recommended)

```
/research-company "Meridian Health" "https://meridian.com" "CoS role, applying this week"
```

Five parallel agents produce a dossier: overview, funding, people and culture, news and strategy, competitive landscape. Output includes a ranked list of similar companies, which is often a better source of next targets than the role you started from.

> **You should see:** `output/meridian-health/meridian-health.md`, plus conversation starters calibrated to your context.

### Step 2: Generate the application bundle

```
/apply https://jobs.lever.co/meridian/cos-role-id "context notes"
```

One command runs the eleven-step CV workflow, writes a problem-solution cover letter that leads with the company's challenge rather than your background, and adds the entry to your pipeline. Use `/generate-cv` alone if you want just the CV.

> **You should see:** a tailored CV, a companion cheat sheet mapping your coached answers to each must-have requirement, a cover letter, and a new pipeline row, all under `output/meridian-health/`. The pipeline entry is marked "Draft Generated," not "Applied." It only flips to Applied when you confirm you actually submitted.

### Step 3: Quality-gate before you send

```
/review-cv output/meridian-health/MMDDYY-cos.md https://jobs.lever.co/meridian/cos-role-id
```

A fast check on keyword coverage, claim integrity, formatting, and self-sabotage language. For a high-stakes application, `/review-cv-deep` runs six reviewers (recruiter, hiring manager, competitor, skeptic, copy editor, source-data auditor) and surfaces the top ten probing questions the CV would trigger, which doubles as interview prep.

> **You should see:** a severity-rated issue list with specific rewrites. Fix, re-render, send.

### Step 4: Record reality

After you actually submit, mark it:

```
/pipe update "Meridian Health" Applied
```

> **You should see:** an accurate pipeline. The system never marks something Applied on your behalf, because a phantom Applied row corrupts every downstream view of where your search really stands.

---

## Flow 3: Win the interview: research to debrief

> **When an interview is coming,** I want to walk in with the company's context and my strongest answers ready, then turn what actually happened into sharper answers for next time, so each conversation makes the next one better.

### Step 1: One-command prep package

```
/prep-interview "Meridian Health" "Chief of Staff" "coffee chat with Jamie Torres, MBA alum"
```

Three parallel agents produce a single document: ten to twelve likely questions mapped to your coached answers with gaps flagged, a company context digest, and tactics plus logistics including questions to ask them.

> **You should see:** `output/meridian-health/MMDDYY-prep.md` and an automatically created debrief to-do. Keep the prep doc open during the call.

### Step 2: Rehearse out loud (optional)

```
/voice-export output/meridian-health/MMDDYY-cos.md https://jobs.lever.co/meridian/cos-role-id
```

Produces a self-contained recruiter-simulation prompt you paste into a voice-capable Claude app, then practice a realistic fifteen-to-twenty-minute screen by speaking, no typing, no coaching mid-call.

> **You should see:** a transcript you can carry into the debrief.

### Step 3: Debrief the real thing

```
/debrief output/meridian-health/MMDDYY-cos.md
```

Paste the transcript (from the real call or the voice rehearsal). The debrief parses it into question-and-answer pairs, rates each answer with its trust and credibility impact, compares against your coached answers, flags every anti-pattern you triggered, and logs the session to your progress tracker.

> **You should see:** your coaching files getting smarter. Refined phrasings flow back into `coaching/coached-answers/`, and your anti-pattern trends update, so the next prep package is built on a more honest picture of your weak spots.

**Why this is a loop, not a line:** the coached answers and anti-pattern tracker that Step 1 reads from are exactly what Step 3 updates. Five interviews in, prep is drawing on five debriefs' worth of calibration.

**Then close the loop outward:** after a real interview, the debrief offers to hand off to `/follow-up`, which pulls the same call transcript to ground the thank-you in what was actually said: the specific callback, the moment that resonated, a concern you can now answer. It sources content from the transcript only, never tone, so the note still reads in your email voice. The full chain is `/granola-pull → /debrief → /follow-up`.

---

## Flow 4: The outreach and networking ladder

> **When I want to reach someone,** I want a first message that earns a reply and a follow-up cadence I will not drop, and I want every interaction remembered, so relationships move forward instead of stalling in my head.

### Step 1: Reach out, informed

```
/cold-outreach "Jordan Kim" "Verdant Foods" "CoS role, MBA alum connection"
```

Selects the right framework for the context, respects channel limits (75 to 125 words for email, under 300 characters for a LinkedIn connect), and runs a three-question quality gate: why you, why now, why me. It auto-logs to your networking file, creates a follow-up to-do, and archives the message.

> **You should see:** a draft that passes the gate, plus the relationship already recorded and the next touch already scheduled as a to-do. Every outgoing message also runs the Substance-Provenance Audit, so a model-generated self-positioning line stops the draft and asks you for the real one.

### Step 2: Follow up without nagging

```
/follow-up "Jordan Kim"        # or no argument for the stale-contact dashboard
```

Checks the message history, determines where you are in the sequence (first through fifth-plus touch), and drafts a tone-matched follow-up that adds new value rather than "just checking in." With no argument, it shows every contact with a pending follow-up, ordered by urgency. For a follow-up right after a real interview or call, it also reads that conversation's transcript and sources the specific callback from what was actually said, while still drawing your writing voice from your email corpus, not the spoken transcript.

> **You should see:** follow-ups that each carry a new reason to reply, and a dashboard so no warm contact slips through.

### Step 3: Promote the relationships that matter

```
/networking log "Jordan Kim" "call 2026-06-04, discussed the CoS mandate"
/networking promote "Jordan Kim"
```

Logging an interaction with reply-signal keywords automatically updates the outreach log from "Sent" to "Replied." When a contact becomes an active relationship, promote them to a per-person dossier (`data/people/<slug>.md`) that tracks where the relationship stands, what each of you committed to, and the next move. This is the judgment layer, distinct from the raw interaction log. Adding a contact also guards against duplicates: a spelling variant of someone already in your roster is blocked with the likely match surfaced, so the same person doesn't fork into two records.

> **You should see:** the handful of relationships actually driving your search getting a dedicated, synthesized record, while the long tail stays in the lightweight roster. The dossier is read by `/follow-up`, `/cold-outreach`, `/draft-email`, and `/prep-interview` whenever that person is involved.

**General drafts** (thank-you notes, status updates, intro requests) go through `/draft-email`, which auto-detects the message type and matches the tone of your prior messages to that recipient. All outbound email runs through these skills on purpose: writing email by hand bypasses the voice and provenance guards.

---

## Evaluating what to adopt

> **When I come across a research paper, a GitHub repo, or a sharp article,** I want to know fast whether it actually helps me, and how, instead of bookmarking it to never read again.

This one is not a search journey. It is the tool for everything adjacent to the search: the steady stream of "should I pay attention to this?" that a curious operator runs into while building and reading.

```
/analyze https://arxiv.org/abs/2506.12345
/analyze https://github.com/owner/repo "worth adopting for /research-company?"
```

`/analyze` takes a URL and answers one question: does this help your systems or your wisdom? It triages relevance cheaply first and bails with a stated reason on a dud, then gives a critical teardown mapped to your actual architecture and goals, with a mandatory "where it does NOT fit" section so it never just cheerleads. Numbers are quoted verbatim or marked "not stated," and every applicability claim is confidence-tagged.

> **You should see:** `output/analysis/MMDDYY-<slug>.md` plus a chat summary, with any build or capture it suggests proposed for your call, never written for you.

When you need to *understand* a topic rather than triage one artifact someone handed you, `/learn <topic>` produces a validated briefing: it fans out parallel live-web research, synthesizes a BLUF-and-bullets document with a comprehension and interview quiz, then runs an adversarial pass that independently refutes each load-bearing claim against live sources, auto-corrects what is wrong, and attaches real source URLs. It is the sibling of `/analyze` (triage what you were handed) and `/deep-research` (a full cited report) for going into an interview able to speak on a subject with confidence.

---

## The capture habit that feeds all four

Every flow above is only as good as the data underneath it, and that data comes from a single cheap habit: capture context the moment it arrives.

```
/wispr                 # voice, all day
/remember <note>       # typed, anytime
```

A recruiter mentions a second role, a call reshapes your read on a company, you have a strategic realization on a walk. Drop it. `/remember` routes each note to its correct destination (contact, company dossier, pipeline, decisions log, accomplishments log, or the inbox for later sorting), and `/wispr` pulls your dictations in and applies or routes them based on what you are working on.

This is the flywheel. The research you ran, the calls you logged, the decisions you captured all become the substrate the next CV, the next prep package, and the next outreach read from automatically. Skip the capture and every flow falls back to generic. Keep it, and the search compounds.
