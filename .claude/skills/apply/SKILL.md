---
name: apply
description: Research a role end to end and produce the outreach that actually gets sent - dossier, then the people, then a cold-outreach brief for the hiring manager, then a CV seeded with all of it. Cover letter only when the application demands one.
---

# Apply — Research First, Outreach Second, CV Last

Run a complete application campaign for one role. **The cold outreach email to a named human is the
deliverable, and the CV is of equal importance.** Neither supports the other; both get sent, both get read,
both get the full quality bar. What the order below buys is **context flow**: the CV is generated last so it
can be seeded with everything the research and the outreach positioning surfaced, and so it says the same
thing the email says.

## Arguments

- **`<job-url-or-jd>`** (required) — URL to the job posting, or pasted job description text.
- **`[context]`** (optional) — extra instructions, e.g. `"emphasize the McKinsey work"`, `"warm intro via a mutual contact"`. Passed through to every delegate verbatim.

Flags, all optional and all off by default:
- **`--cover-letter`** — the application form requires one. Runs Step 7. Skipped otherwise.
- **`--no-deep-review`** — passes through to `/generate-cv` for fast iteration. The deep review is otherwise always on.
- **`--skip-research`** — reuse an existing dossier without refreshing. Only valid when `output/<slug>/<slug>.md` exists and is under 14 days old; otherwise ignored with a note.

```
Usage: /apply <job-url-or-jd> [context] [--cover-letter] [--no-deep-review] [--skip-research]
```

## Why this order

Rewritten 2026-09-01 after a run that produced the bundle in the old order (CV first, cover letter, research
never). Three things went wrong and all three trace to sequencing:

1. **The CV was written from the job description**, because that was the only context available at Step 5. It
   claimed things the record did not support, and read as a paraphrase of the posting - which one review
   agent scored as a *strength*.
2. **The cover letter was generated, audited, and never used.** What Nick actually sent was an email to the
   hiring manager with the CV attached. The bundle spent its effort on the one artifact nobody read. The
   cover letter is the thing to cut under time pressure - never the quality of either co-equal artifact.
3. **The research ran afterwards, by hand, and immediately made everything better** - it found the hiring
   manager, her standing public invitation to be messaged, the company's own published deployment doctrine,
   and a dated convergence with Nick's own prior work that became the strongest line in the email.

The dossier is the seed. Everything downstream is better for having it, and nothing downstream is cheap to
redo once it is wrong.

## Instructions

### Step 1: Parse arguments and fetch the JD

1. Strip and record the flags above. Leave everything else in `[context]` untouched for pass-through.
2. If the first remaining token is a URL, fetch it. **Ashby, Greenhouse and Lever postings are
   JavaScript-rendered and WebFetch returns a shell.** For Ashby, hit the GraphQL API directly:
   ```bash
   curl -s "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting" -H 'content-type: application/json' \
     -d '{"operationName":"ApiJobPosting","variables":{"organizationHostedJobsPageName":"<org>","jobPostingId":"<id>"},"query":"query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { id title departmentName locationName employmentType descriptionHtml } }"}'
   ```
   Both values come from the posting URL. Strip the HTML. If the fetch fails, ask for a paste; do not
   reconstruct a JD from the URL slug.
3. Derive the company slug and role slug. Record the **department** (it tells you which org the seat sits in,
   which matters for Step 3).

### Step 2: Profile guard

Verify `data/profile.md` and `data/goals.md` exist with real content, not TODOs. If either is missing or all
TODOs, stop and say to run `/import-cv` or fill `data/goals.md` from `framework/templates/goals.md`. Never
fall back to generic candidate context.

### Step 3: Company dossier — FIRST, and DELEGATED to `/research-company`

**Invoke `/research-company`. Do not do an inline search pass instead.**

```
/research-company "<Company>" <company URL if known> "applying for <role>; <[context]>"
```

Pass the specific questions this role raises, not just the company name. At minimum ask for: funding and
runway, real customer traction versus self-reported claims, the org shape around this seat and who it
reports to, competitive set, and **anything published in the company's own voice about how they work** -
engineering blogs, deployment write-ups, founder interviews. That last one is consistently the highest-value
input to Step 5 and the easiest to miss.

**If a dossier already exists** at `output/<slug>/<slug>.md`: under 14 days old and `--skip-research` was
passed, reuse it. Otherwise refresh. Never proceed on a dossier older than 30 days without saying so.

**Carry forward:** opportunity rating, the two or three facts that would change whether Nick wants this, the
company's own language for what it does, and any risk Nick should decide about before he spends more time.

**Surface the risks to Nick now, not in the final summary.** If the research turns up something that could
change his mind about applying at all - ownership structure, runway, political exposure, a founder story that
does not hold up - say it here, plainly, before the outreach work begins. He decides whether to continue.

### Step 4: Find the person — who does this actually go to?

The dossier's people section is the starting point, not the answer. Determine specifically:

1. **Who is the hiring manager for THIS req?** Search for the role title plus the company; hiring managers
   frequently post their own openings and say "message me." That post is the single best outreach signal
   available and it expires.
2. **Who else is a legitimate door?** A vertical lead, a functional peer already in the seat, someone with a
   shared institution. Rank by whether they can actually decide, not by how reachable they are.
3. **Check `data/networking.md` and `data/people/` for existing ties** before treating anyone as cold. Use an
   explicit grep; a matcher's negative is an absence assertion.
4. **Verify every person fact against a primary source.** Agent-drip entries in `data/inbox.md` are
   unreviewed and have been wrong: on 2026-08-17 a scan stamped the company HQ onto two people who were
   actually eight time zones away, and both sat unquestioned until checked.
5. **Find the contact route.** If email, establish the domain pattern from a public source rather than
   guessing blind, and tell Nick the confidence and the fallback. If the person named a channel themselves
   ("message me"), that channel wins.

**If no named person can be found**, say so plainly and ask Nick whether to submit cold or hold. Do not
default to submitting into a portal because the research was inconclusive.

### Step 5: Cold outreach — DELEGATED to `/cold-outreach`

**This is the deliverable, co-equal with the CV. Invoke `/cold-outreach`.**

```
/cold-outreach "<Name>" "<Company>" "<Role>" channel:<email|linkedin|inmail> "<everything from Steps 3 and 4 that matters: the why-now, the verified facts about this person, the company's own words, the open screens, [context]>"
```

Pass the dossier findings in the argument. The delegate will do its own research pass, but it should not have
to rediscover what Step 3 already established.

**Brief mode is the default and stays the default.** `/cold-outreach` produces an Outreach Brief - the
why-now, verified recipient facts, sourced proofs, positioning, the hook, hard don'ts - and **stops**. Nick
writes the sentences. It escalates to a full draft only on an explicit ask ("draft it," "write it"). Urgency
is not that ask. Per Nick 2026-08-26 and the authenticity non-negotiable behind it.

**Carry forward into Step 6:** the positioning spine, the proofs the brief selected, and the hook. **These
are the seed for the CV.** A CV written after the positioning is settled says the same thing as the outreach;
a CV written before it says whatever the job description said.

### Step 6: CV — DELEGATED to `/generate-cv`, seeded with everything above

**Invoke `/generate-cv`. Do not select projects, write CV YAML, or apply CV quality rules here.**

```
/generate-cv <JD from Step 1> "<[context]>. Seeded by /apply: positioning spine is <spine from Step 5>; the proofs the outreach leads with are <proofs>; the company's own framing is <their language>. Do NOT update the pipeline, /apply owns that step. <pass --no-deep-review through if set>"
```

`/generate-cv` owns the CV end to end: project selection and factual stubs, the source-corrections
reconciliation gate, the tailored YAML, its inline quality checks, theme merge, render, the mandatory
render-and-verify pass, the companion cheat sheet, and the always-on six-perspective deep review, including
auto-applying that review's mechanical fixes.

**Why this is a delegation and not a copy.** These rules used to live in both skills and they drifted. On
2026-08-25 `/generate-cv` gained content rules `/apply` never received - "do not lead with a credential-first
opener," "the Building entry lives in ADDITIONAL INFORMATION and NEVER in EXPERIENCE," "name the AI products
by capability, never by listing internal primitives," a ban on JD-mirroring competency phrases in Skills, an
always-on deep review, and a render-and-verify gate. On 2026-09-01 an `/apply` run reproduced every one and
Nick corrected each by hand against rules that already existed six days earlier. **One source of truth.** If a
CV rule needs to change, change it in `/generate-cv`.

**Carry forward:** CV paths, cheat sheet path, selected projects and rationale, the deep review verdicts, and
any finding the delegate deliberately left as a judgment call.

**Then surface the judgment calls to Nick before anything ships.** Not a list of mechanical edits - the
delegate already applied those - but the claim-level and voice-level decisions he has to stand behind.

### Step 7: Cover letter — ONLY if `--cover-letter` was passed

Skipped by default. Most applications do not read one, and on 2026-09-01 a fully generated and audited cover
letter went unused because the real artifact was an email.

When the form does require one, follow the Problem-Solution structure in
`framework/application-workflow.md`: lead with their challenge, prove you have solved something like it,
bridge to what you would do for them. 250-350 words, hard ceiling 400.

**Then run the Substance-Provenance Audit.** Apply `framework/writing-discipline.md`, which is canonical for
the `N` / `C` / `I` / `G` labels and the invariant that `G` is blocked in any slot carrying a claim about who
Nick is, what he brings, what he wants, or what he has done.

| Slot | `G` allowed? | If `G` found |
|---|---|---|
| Thesis / opening claim | **No** | STOP. Ask Nick to dictate it. |
| Their-problem statement | **No** | STOP. Either `I` with a citable source, or it is speculation. |
| Proof-point framing | **No** | STOP. Facts come from `data/projects/*.md`; framing must be `N` or `C`. |
| Self-positioning | **No** | STOP. Ask Nick to dictate that slot. |
| Bridge | **No** | STOP. Ask for the link or extract from corpus. |
| Closing CTA / logistics | Yes | Proceed. |
| Salutation / sign-off | Yes | Proceed. |

**Expect this to block, and do not treat that as a failure.** A cover letter is self-positioning nearly end
to end. If most substantive sentences come back `G`, the finding is that the letter should not be drafted
yet - ask Nick for the spine.

Save to `output/<company-slug>/MMDDYY-cover-letter.md`. This is the only filename `/apply` owns; CV and cheat
sheet names belong to the delegate.

### Step 8: Confirm submission status (mandatory — do NOT skip)

`/apply` produces artifacts. Submission is a separate human action. Before any pipeline write that would set
`Applied`, ask:

> Did you submit this application to **[Company]**? (Y/N)

- **Y** → `pipeline_stage = "Applied"`.
- **N** → `pipeline_stage = "To Apply"`, next action `Submit when ready - bundle generated [today]`.

**Why:** auto-flipping to `Applied` on artifact generation creates ghost rows. Origin 2026-05-28: `/apply`
produced artifacts on 2026-05-08 and the row read `Applied 5/8` for three weeks while Nick never submitted.
See `memory/feedback_pipeline_applied_status_must_be_user_confirmed`.

**The same discipline applies to the outreach.** Do not log a drafted message into `data/networking.md` as
though it were sent. Log it only once Nick confirms it went, and log **his** text, not the draft - he edits
on send, and a log entry is indistinguishable from a record of what happened. Fired twice: 2026-08-19 and
2026-09-01.

### Step 9: Fit-reason capture (mandatory prompt, optional answer)

> One-line fit read for the calibration ledger - why is this in your lane, or where is the reservation?

Capture verbatim as `fit_reason`, plus `fit_verdict` ∈ {fit, not-fit, neutral, unknown} if he names one.
Accept a skip; never block on it. Sanitize any `|` to `/` so the table row survives.

Rationale: this is the source-coverage input the calibration loop needs. The blind machine re-run abstained
on 9 of 52 of Nick's fit calls because his reasoning lived only in his head
(`output/analysis/071526-machine-vs-human-agreement.md`).

### Step 10: Update the pipeline

Use `tools/pipe_write.py` (never Edit — rows exceed the Edit-safe length).

- **Not in the pipeline:** `add` with `--stage <pipeline_stage>`, `--url`, and the fit-reason flags.
- **Already present:** `update`. Set CV Used, next action, notes. **Never regress a stage** - if the row is
  already `Applied` or later, leave it and note that in the summary.
- **`--notes` REPLACES the Notes cell**, so it drops any existing `[fit-reason ...]` tag. Re-apply the
  fit-reason in a separate `update` call after setting notes, or compose both in one call.

### Step 11: Display summary

```markdown
## [Role] at [Company] — [Applied / bundle ready]

### The person
[Name, role, why them, the channel, and the confidence on the contact route]

### Outreach
[Brief delivered / draft sent. If sent: date, channel, attachment.]

### Research
**Opportunity rating:** [High/Med/Low] — [one line]
**Worth knowing before you go further:** [the one or two facts that could change his mind, or "none"]
**Dossier:** `output/<slug>/<slug>.md`

### CV
- **PDF:** `output/<slug>/MMDDYY-magnuson.pdf`
- **Deep review:** `output/<slug>/MMDDYY-magnuson-DEEP-REVIEW.md`
- **Recruiter / Hiring Manager verdicts:** [from the delegate]
- **Judgment calls left for you:** [claim and voice decisions only, not mechanical edits]

### Pipeline
[stage, and the follow-up date]

### Open
[Anything unresolved, stated plainly. Guessed email addresses, unverified facts, untested changes.]
```

## Edge cases

- **JD fetch fails:** ask for a paste. Never reconstruct from a URL slug.
- **No dossier possible** (stealth company, no public footprint): say so, proceed, and mark every downstream
  claim about the company as unverified.
- **Company name collides with a better-known company:** check this explicitly in Step 3 and warn Nick. On
  2026-09-01 a target company was roughly 100x less indexed than an unrelated firm with nearly the same name,
  which made plain web search return up to 100% wrong-company results.
- **Hiring manager unreachable:** offer the peer path or the portal, and say which you recommend.
- **Existing pipeline entry at `Applied` or later:** never regress; update CV Used only and surface a note.
- **Research surfaces a genuine reason not to apply:** stop and say so. Finishing the bundle is not the goal.
