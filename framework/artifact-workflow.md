# The artifact workflow

Last updated: 2026-10-26

```
IF THE READER HAS NOT SPOKEN, STOP HERE.
A job posting, a press quote or a company page is the company's words, not a stated need.
With no quotable ask from one named person: /cold-outreach (a text first touch). No build, no build log.
The Brief records value_item, prediction, send_date, artifact: none. Done.
Otherwise: answer Q0-Q4 below, then open output/<slug>/BUILD-LOG.md before any build file.
```

One workflow for every reader-facing thing built during the search. Core steps run in the same order every time; a variant row in section 4 names the steps it skips, and nothing else changes.

**How to read this file.** This file holds rules, checks and cites only. Every outcome, reader quote and company name lives in the private appendix (`output/artifact-workflow/appendix.md`, gitignored), which maps a cite like `B13:33` to line 33 of a mined build file. `AR3` is a row of the artifact ledger: the rows tagged `[artifact]` in `memory/lessons.md` Section 2. `S1:155` cites a prior-work source the same way. A cite proves that a line exists and was read; it does not prove the line says what the sentence claims. The review record for this file is private (`output/codify-102326/07-redteam-log.md`).

**Enforcement tier, stated plainly (2026-10-26).** Four mechanisms read this workflow, and one of them can stop a build:
- `tools/check_build_log_present.py` (PreToolUse, BLOCK) stops the first HTML, code or deck file under `output/<slug>/` until that folder has a BUILD-LOG with `reader_ask` filled and a dossier. It cannot see Markdown or PDF builds.
- `tools/check_draft_artifact.py` (PreToolUse, BLOCK) stops `tools/open_draft.py` on a staged draft that carries an attachment or a non-bio link without an `ARTIFACT:` header.
- `tools/sent_artifact_check.py` (a `/standup` probe and a daily job) finds an attachment or link in Gmail Sent that no BUILD-LOG, CV or acknowledgement explains. It detects after the send; nothing prevents a file attached by hand in Gmail, and LinkedIn is not covered.
- `tools/build_check.py` (a `/standup` probe) checks the logs that exist and raises the flags in section 6.

Every step below marked SCRIPT names a check a command can make; where no command makes it yet, the label reads **SCRIPT (unbuilt)**, and until it is built that step is prose, the tier at which a build's own written stop rule did not stop that build (B09:97-98).

**Size.** The three parts this file has that the earlier fast path (`S04:289-314`) did not are a record written before any build file, an outcome record after the send, and a drain. Whether they pay for themselves is unmeasured until builds run on this file and the appendix records their outcomes.

---

## 1. Decision tree (branch 6 of the router in `framework/decision-tree.md`)

Answer Q0 to Q3 in order and stop at the first variant instruction; then Q4. Write the answers and their sources into the Phase 0 `route` field; a route with no recorded answers has no verdict.

| # | Question | If yes | If no |
|---|---|---|---|
| Q0 | Has one named reader said, in words you can quote with a source line (a transcript line, a saved post, a fetched message), what they need now? **A job description, press quote or company page is the company's words, not a stated need**; it can supply the topic of a text first touch, never the license to build (B01:66, B13:137). A reply that names a need re-enters here as a stated ask. | Q1. | **No build.** `/cold-outreach` or `/follow-up`; the value item is text in the body. The Brief record line (section 6) is the record. The record behind this row is appendix 8.2 (AR1, AR14). |
| Q1 | Did the reader set the task (a take-home, a case, a requested document)? | **V2 (assigned exercise)**, or **V3** when the request is a document. Phase 0 runs in its V2 form (section 2). Then Q4. | Q2. |
| Q2 | Can the stated need be answered in text, inside one afternoon, from sources read in full? Write one line on why not when the answer is no. | **V4 (text, in thread) or V3 (written note)**. The record for this shape is appendix 8.2, with its denominator. Then Q4. | Q3. |
| Q3 | Does the need require a page, demo or tool, and does `prior_outcomes` (section 2) show this type has drawn a reply from a reader who had asked for nothing? If the reader has already answered and named the need, use the warm rows, not the cold count. | **V1**, after Phase 0 with the lookup result written in. Then Q4. | Send the smallest text answer first (V4), log it, then decide whether a page adds anything the reader asked for. |
| Q4 | Is there an earlier build of the same variant in the record (`output/**/BUILD-LOG.md`, or appendix 8.1 for builds before the template)? | **Adapt it.** Copy its Method notes for reuse and its appendix row into the new log as `adapted_from`; re-fetch every source; keep the structure, change the first screen; never copy its outputs (B06:77, B17:71). A Phase 0 header is copied only once a past log has one. | **Start from blank.** Set the header `Variant:` line to `new` and log every step (AR16). At drain, propose a variant row. |

Not this workflow: interview prep (`/prep-interview`, `/debrief`; AR30), idea validation reps (B15:124), the standard application bundle (`/apply`, B17:11), a requested readout page for a partner (`framework/linkedin-post-readout-runbook.md`; run it, do not redesign, B12:56). Live delivery of an assigned exercise is `/prep-interview`'s scope, with one rehearsal item named in V2.

---

## 2. Phase 0: the gate record (required fields, tiered by variant)

Written as the header of `output/<slug>/BUILD-LOG.md` (template: `framework/templates/build-log.md`) **before** any draft or design file exists. The lookups the fields need are the Phase 0 work. A blank field has no verdict. `CHECK`: **SCRIPT** (a command can check it; unbuilt is marked), **FIELD** (a non-empty value a script can see), **JUDGMENT** (no script can decide it; the reason is given).

**Tiers.** V1 fills every row. V2 fills every row except `not_a_copy`, and adds the brief saved verbatim as the first file, the format question's answer, and the single comparison. V3 fills the seven core rows. V4 opens no BUILD-LOG: its record is the `/cold-outreach` Brief record line (section 6), with `value_item`, `prediction`, `send_date` and `artifact`. A timed V2 under two hours fills `reader`, `reader_ask` (the brief), `time_box` and `prediction` only.

| Field | What goes in it | CHECK | Evidence | Default (ID; counts in the appendix) |
|---|---|---|---|---|
| `filled_by` and `route` | `nick` or `claude`; the Q0-Q4 answers, each with its source. A `verdict: build` written by a session is a proposal until Nick writes the same word. | FIELD; SCRIPT (`tools/build_check.py`): both present | Author overrides of reviewers decided the recorded failures (B09:98, B07:95). | none |
| `reader` | One person: name, role, what they own, whether the recommendation is theirs to act on; the channel the reader named; `address_source: inbound message / verified record / guess` (a guess needs one line on how delivery will be confirmed). | FIELD; JUDGMENT on 'theirs to act on' | AR21 (B09:17, B09:101, B17:79, B05:89). | D1 in-thread when a thread exists (B03:49). |
| `reader_ask` | The reader's own words, quoted, with source and line; `answers_ask: yes/no`, re-answered at ship against the shipped text. For V2 the brief itself, saved verbatim as the first file. | FIELD, read by `tools/check_build_log_present.py`; SCRIPT (unbuilt): the quoted string is found in the cited source, which is a transcript, a snapshot under `output/<slug>/sources/`, or a fetched message, never a file this log wrote; JUDGMENT: `answers_ask` (B09:128) | AR1 (B09:90, B06:63). AR10 (B07:87, B04:71). | none |
| `external_stake` | The real thing touched: the reader's product, data, document or a real call. `none` means a demo; say so or redesign. If the artifact holds test data the author wrote, both test and answer are the author's: cut that part (AR12, B14:50). | FIELD | AR2. | none |
| `time_box` | One line: the reader's time signal quoted; `need_expires` (a signal of 'asap' or 'urgent' sets it to the post date plus 1 day); research hours; design hours; review loops; `send_date`. For V2 the reader's named budget is binding; an overrun needs a new `send_date` written before the overrun, never a reason written after. | FIELD; SCRIPT (`tools/build_check.py`): `send_date <= need_expires`; hours and loop counts summed from the Entries' `duration` and `who` lines against the budgets (never from file mtimes) | AR2 (B06:63, B09:75, B07:72). AR8 (B13:33, B09:73, B12:51). AR23. | D2 design: one critic round from a template, a second only if the first moved the score, stop at the first flat round (B13:33, B12:68). D3 review loops: 2; a loop stops when no P0 is open (B09:73, B12:51). |
| `value_item` | One sentence: the single thing the reader can use even if they never reply or hire. Then `proof_purpose`, separately; it never leads. | JUDGMENT; FIELD that both lines exist | AR3 (B13:137, B10:102). | D4 first screen = the value item, as the strongest sourced fact (B13:139). |
| `not_a_copy` (V1 only) | One yes/no line: does the artifact tell the reader something about their own use case they do not already have, and does the first screen show it? Plus the grep result for the company's own price, dates and homepage sentences. | JUDGMENT; SCRIPT (unbuilt) for the grep | AR4 (B13:139, B09:102, B14:51). | none |
| `prior_outcomes` | For this variant: sends, written replies, conversations, with the command and date, read from the `Artifact` column of `data/outreach-log.md` (`tools/outreach_status.py`). | SCRIPT | AR14 (B14:53, B17:82, B08:17). | none |
| `prediction` | One line before any send: reply yes/no, by when, what would change the method. | FIELD, stamped before `send_date` | AR17 (B07:117, B08:70). | none |

**Stop rules (the same for every build, so they live here, not in a field):** a flat design round; a P0 against the title or headline claim means reshape or stop, never a qualifier lower on the page (B09:98); `need_expires` passed; a change of framing or corpus counts against `time_box` and is logged as an Entry, it does not reset this record; no new tool, gate or script is built between Phase 0 and the send.

**Lessons.** Step 1 prints the `[artifact]` rows of `memory/lessons.md` Section 2 for this variant into the log (until a script does it, a grep for the `[artifact]` tag over that file). A count of 0 is a failure, not an empty pass. The log lists only the rows that change this build. No per-row hand table.

**When `frame.yaml` exists** for the build (written by `tools/frame_write.py`, its only sanctioned writer), `inputs`, decisions and status point at it and the BUILD-LOG holds only the fields the frame lacks.

**Defaults have stable IDs (D1-D6).** A build that applies one writes `defaults_applied: D2, D3` in its Ship block. Run counts and outcomes per default are derived from those lines and never typed; the hand-frozen table in the appendix is history as of 2026-10-24 and marks every default with no run as `unrun`.

**Verdict** after the fields: `build | reshape (name the change) | don't build (name what would have to be true)`. Filled G1-G8 gate records under `output/<slug>/` are superseded by this section: each is re-answered with Q0-Q4 and the result written into the BUILD-LOG, with a field-by-field comparison of its G7 (time box, access) against Phase 0 and every conflict listed for Nick. The old record stays in place with a one-line pointer.

---

## 3. Core steps (eight; V3 and V4 skip the steps their section-4 rows name)

### Step 1. Open the log and print the record (hour 0)

Write `output/<slug>/BUILD-LOG.md` from `framework/templates/build-log.md` and print into it: prior art (`output/<slug>/`, the contact's block in `data/networking.md`, `data/company-notes/<slug>.md`, `coaching/progress/*<slug>*`, the nearest build of the same variant), `prior_outcomes` for the variant, and the `[artifact]` ledger rows. Then fill the Phase 0 fields and save the reader's ask or the brief **verbatim** as the first file. The dossier `output/<slug>/<slug>.md` exists first (`/research-company`): `tools/check_build_log_present.py` blocks a build file without it.

- SCRIPT (`tools/build_check.py --open <log>`): the log exists; the Phase 0 fields for the variant's tier are non-empty. SCRIPT (unbuilt): the brief file's own `saved:` timestamp precedes the first Entry that names a draft.
- Why: AR10 (B07:87, B04:71, B06:68), AR25 (B16:55, B14:42), AR16.

### Step 2. Inputs: real, fetched, dated, tiered, staged

Primary documents on the company's own domain and its customers' domains first; fetch each, record date and tier (A/B/C or assumption), log what could not be opened, re-fetch the target's own site even when a brief quotes it. Save every source under `output/<slug>/sources/` so the reviewer in step 6 can cite file and line. A stop-rule count states its tier floor.

- SCRIPT (unbuilt): every claim row has a tier and a source path; `output/<slug>/sources/` is non-empty before step 6.
- JUDGMENT: which three claims an insider checks first.
- Why: AR12 (B11:76, B10:59, B14:52, B13:50).

### Step 3. Test the frame before the draft (V1 and V2 only)

(a) Red team the outline with a blind agent that opens every cited source, or run `/verify` on the proposal; (b) test any headline claim on the live product or data, pulling both classes; (c) define each criterion in one line and check that no input sits in two.

- SCRIPT (unbuilt): the red-team file's Entry precedes the first draft Entry; the headline-claim row has `check` and `sample` filled.
- JUDGMENT: whether criteria are mutually exclusive; whether a claim is a floor or a differentiator.
- Why: AR11 (B13:51, B02:56, B04:53, B07:95, B14:95).

### Step 4. Draft the smallest passing version, text first

Nick states the spine and owns the prose; Claude structures and pressure-tests (AR19, B02:63). The log names the lines only the author could have written. Checks on the draft:

| Rule | CHECK | Evidence |
|---|---|---|
| First screen = the value item as the strongest sourced fact; nothing restates the product. | JUDGMENT; SCRIPT (unbuilt) for homepage phrases | AR4 |
| Every number is generated by one tested function and traced to a fact; never typed. | SCRIPT | AR13 (B09:92, B07:177) |
| Every quoted string appears verbatim in a staged source. | SCRIPT (the pattern at B09:93) | AR13, L130 |
| One name per role; the company's words for things; never its customer-facing second person. | SCRIPT (unbuilt): term list | AR26 (B13:56) |
| No line tells the company its own facts, argues with a founder's public quote, or gives GTM, pricing or competitor advice to its CEO. The plan is checked against the company's own public claims. | SCRIPT partial; JUDGMENT | AR4, AR5 (B13:74) |
| Every sentence about the author's own record is greppable in `data/projects/`, `data/profile.md` or `data/professional-identity.md`, or cut. | SCRIPT | AR20 |
| Plain words; no em dashes; a term the reader would look up is restated with the concrete case. | SCRIPT (`tools/check_banned_phrase.py` pattern) | B02:62, B08:60 |
| Uncertainty as the author's own notes (Assumed / I'd ask), not warnings; a guess is sent as a guess with 'where is this wrong?'. | FIELD: an assumptions block exists | AR22 |
| Methods shown as methods; former clients and employers not named as their source. | SCRIPT: denylist grep | L243 (B09:96, B10:40) |

### Step 5. Design (V1; V2 only when the deliverable is a deck)

Design has its own budget (D2). Render the ugly page and look at it before any copy review (AR24). Start from the nearest template; elicit taste in Nick's words before any brief; one critic round with screenshot-only critics; phone-width check by eye at 400px for every scroll state; stop at the first flat round.

- SCRIPT (unbuilt): 400px screenshots exist for each state before Nick sees the page; design round count <= budget.
- Why: AR24 (B07:91, B12:68, B13:63), AR27 (B13:33, B12:110).

### Step 6. Reader review, with a loop budget

Run `tools/reader_review.py --gate output/<slug>/BUILD-LOG.md` on the rendered artifact; `--gate` reads `reader` and `value_item` from Phase 0 and refuses a log with either blank. Add `--protect` and `--source-dir output/<slug>/sources`. The terminal run sees only the brief and the artifact, never a claim list. Attachments go through the same review.

- **Open P0 = 0 is not enough to send.** Every P0 dispositioned as accepted-risk or won't-fix, and any reviewer verdict of 'do not send', needs a dated `signoff: nick YYYY-MM-DD` line a script can read; a written waiver without that line is not a sign-off. `tools/build_check.py` reports an open P0 or an undated sign-off as a failed check on the `/standup` `Builds:` line; nothing stops the send itself, so read that line before sending (B09:98, B07:95).
- **If the review tool refuses to run** (`tools/codex_verify.py` refuses a path it cannot show the reviewer), log the refusal as an Entry; the send waits for one completed review or a Nick sign-off line (B13:149).
- **V2 brief-term coverage:** pull the product and reader nouns from the saved brief, grep the artifact's visible text, list the misses for disposition before the send (B07:89, B07:198).
- SCRIPT (`tools/build_check.py`): review Entry after the last edit Entry; every P0 disposition either fixed or signed off; loop count <= D3.
- Why: AR11, AR8 (B11:95, B10:62, B12:51).

### Step 7. Ship

Each item is a line in the Ship block.

1. **Diff the shipped rendered text against the last reviewed text**; anything new or changed is reviewed again or cut. SCRIPT (unbuilt, the highest-yield check in the replay). Why: AR9 (B13:70, B07:89, B04:59, B01:35).
2. **Phone check by eye**, both scroll states. SCRIPT for the screenshots; JUDGMENT for the look (AR24).
3. **Deploy and verify**: `noindex`, analytics present, the curl sequence if a gate exists. No password on a cold link without the one-line reason recorded in `access:` (AR7). SCRIPT.
4. **Message body**: one finding from the artifact and one question from its own question list (for a text touch, from the dossier's conversation starters), answerable in a sentence; for a cold first touch, one matching proof from Nick's own record (named in the `/cold-outreach` Brief record line); at most one meeting ask stating what yes costs the reader; the link on a word. The staged draft carries `ARTIFACT: output/<slug>/BUILD-LOG.md`, which `tools/check_draft_artifact.py` reads. SCRIPT (unbuilt): flag a body over the channel's word cap (200 for a cold first touch), with an em dash, with more than half its characters in a URL, or with no question mark. JUDGMENT: which finding. Why: AR6 (B13:137, B09:99, B06:120, B08:107).
5. **Recipient equals `reader`.** The send goes to the Phase 0 reader on the channel recorded; a second recipient needs its own Phase 0 (AR21).
6. **Stamp `prediction`** before sending. FIELD (AR17).
7. **Log the send at send time** through the one row writer, `tools/outreach_status.py --append`, with the `Artifact` cell; the BUILD-LOG `sent:` line is the pointer it prints, `data/outreach-log.md:<line>`, never a retyped copy. Why: AR17 (B07:117, B13:90, B05:88).

### Step 8. After send, and drain

One clock: the step runs within a day of a terminal outcome, or on day 14 after `send_date` when nothing has arrived, whichever is first. `tools/build_check.py` flags `drain due` on either trigger and keeps flagging until the log carries a `drained: YYYY-MM-DD` line.

1. **After-send fields**, each a pointer or a source, never typed from memory (AR17): `opened` (hosting analytics, read before any silence is read; `unknown` if no counter), `reply: data/networking.md:<line> | none by <date>` (from a Gmail all-mail search pasted into the networking block, `tools/gmail_fetch.py`), `outcome: data/job-pipeline.md:<line>`, `parallel_signals` (anything else that explains a silence), `n_note: one silence is n=1`. The outreach-log row is the one authoritative outcome row; the BUILD-LOG never holds a second copy.
2. **Postmortem order**: Nick's cold read, then Claude's annotation from the record, then reconcile; the record wins on fact, Nick on judgment (B07:125). Read the brief before diagnosing (AR10, B07:115).
3. **Method notes for reuse**: transferred unchanged / had to change / do differently (the shape at `S07:71-94`).
4. **Ledger rows**: each note becomes one `[artifact]` row in `memory/lessons.md` Section 2, or increments an existing row's Occurrences; the drain never touches a memory file's `occurrences`, which counts Nick corrections. SCRIPT: row count before and after.
5. **A repeated lesson changes this file**: a row whose Occurrences a drain increments to 2 or more, with Promoted = No, produces a proposed edit filed as a parked todo with a REOPEN gate through `tools/todo_write.py`, pointed to from the row's Promoted cell. Rows already encoded in this file carry `Promoted: Encoded (<step or field>)`, so nothing is due until a drain increments a `No` row.
6. **`drained: YYYY-MM-DD`** closes the log. Stores: the build log, the ledger, the outreach-log row. Nothing else (AR18).

---

## 4. Variants (what differs from the core)

Each row says only what changes; outcomes per variant are in appendix 8.2.

| Variant | What differs | Steps skipped |
|---|---|---|
| **V1. Built page or demo** on the company's own use case, product or domain | `not_a_copy` is the binding field (AR4). Design budget separate (D2). Hosted with analytics; no password without a recorded reason (AR7). For a demo: frozen blind labels saved read-only, reflections in a sibling file; pre-registered re-runs; one tested stats function before any copy cites a number; the behaviours the company asked about go first (AR29, B09:91-95, B09:103). For a tool built for an owner: a recorded yes in `reader_ask` or a stated discovery-only cap; the show trigger and fallback decided before the meeting (AR31, B16:113). | none |
| **V2. Assigned exercise** (take-home, case, deck with a live defense) | The brief verbatim is the first file and the input to the terminal check (AR10). The reader's named budget and format are binding (AR23). Ask 'deck or conversation, and what is the one thing you are grading?' before the clock (B06:67). Decide the single comparison first; never present a model you cannot redo on a whiteboard (B06:64). No visuals in a timed case (B06:66). Keep a copy before access is revoked (B06:69). If presented: each criterion's definition on the slide that introduces it; printed metric equals spoken metric (SCRIPT: grep deck, talk track, cut list); freeze the frame once rehearsal begins, and a new number from the interviewer is the one external trigger that reopens it, said aloud first (AR28, B04:58-63). Rehearse that one item through `/prep-interview`. Post-defense follow-ups deliver the answer each interviewer asked for and did not get, under 120 words (L250, B04:70). | 5 unless a deck |
| **V3. Written note or doc** (post-call research note, first-day plan, advisory document) | The reader's problem is a quoted line with a transcript line number (B11:14). Facts into a numbered claims file; sources staged; reader review with `--source-dir`; Nick cuts credentials (B11:47-50). Stage the draft once (B11:97). Vendor estimates attributed with their range (B11:96). At scale: a script-built claim register with two independent checks and a different-model check (B10:92); a sanitization step with a denylist count of 0 before a copy travels to a second reader (B10:94). A document with no question the reader can answer in a sentence is incomplete (B10:109). | 3 and 5 |
| **V4. Text email** (value-add, learnings, extension of the reader's own words) | No BUILD-LOG. The `/cold-outreach` or `/follow-up` Brief record line carries `value_item`, `prediction`, `send_date`, `artifact: none` (section 6). One callback, one example, one question (L231); extend the recipient's own stated idea (L227); every 'you said' checked against the verbatim transcript (L130, B08:61); in-thread when a thread exists (D1); after two unanswered emails, shrink the ask and offer a second path (B08:53). A finished sample of the exact template the reader asked for, within two hours, is V3 with an attachment (B12:63). | 1, 2, 3, 5, 6; step 7 items 4-7 and step 8 apply through the outreach log |

Router lines, not variants: a requested readout page (run `framework/linkedin-post-readout-runbook.md`, B12:91-93); interview-loop prep apparatus (AR30); idea reps (B15:124); the application bundle (`/apply`).

---

## 5. BUILD-LOG template

The template is `framework/templates/build-log.md`. Save it as `output/<slug>/BUILD-LOG.md` at hour 0. Append-only below the header. Every Entry carries its own `duration` and `who`; the checks read these lines, never file mtimes.

---

## 6. What reads this file

| Piece | What it does | Tier |
|---|---|---|
| `tools/check_build_log_present.py` | Blocks the first HTML, code or deck file under `output/<slug>/` until the folder has a BUILD-LOG with `reader_ask` filled (or Nick's `waived:` line) and the dossier `output/<slug>/<slug>.md`. Cannot see Markdown or PDF builds. | a check a gate reads (PreToolUse) |
| `tools/check_draft_artifact.py` | Blocks `tools/open_draft.py` on a staged draft with an `ATTACH:` line or a link other than the bio URLs, unless the draft carries `ARTIFACT: output/<slug>/BUILD-LOG.md` (a log with `reader_ask` filled or waived) or `ARTIFACT: none (<reason>)`. A rendered CV passes. | a check a gate reads (PreToolUse) |
| `tools/sent_artifact_check.py` | Lists every attachment and link in Gmail Sent and flags any that no BUILD-LOG `sent:` line, rendered CV, or line in `data/sent-artifact-ack.md` explains. After the send; Gmail only. | a `/standup` probe and a daily job |
| `tools/build_check.py` | Checks every `output/**/BUILD-LOG.md` and raises `incomplete`, `expired`, `check analytics and Gmail` (sent 3 days, no `opened`/`reply`), `drain due` (terminal outcome or day 14, no `drained:`), `workflow edit due` (a drain increment to 2 or more on a Promoted = No row). Zero logs is a warning, never `0 builds`. | a record check over logs that exist |
| `/standup` Step 0 | The router (`framework/decision-tree.md`) as one `Branch:` line, plus the `Builds:` line from `tools/build_check.py`. A morning summary, not the gate. | written and followed |
| `/cold-outreach` Brief item 8 | The record line for every send: `value_item`, `prediction`, `send_date`, `artifact: none | <BUILD-LOG path>`. The record for every Q0-no and V4 send. | a field a skill step demands |
| `/verify` reader review | Read Phase 0 first and pass `--gate <BUILD-LOG>`; no log is the first finding. | blocking only inside a run Nick invokes |
| `data/outreach-log.md` `Artifact` column | The eighth column, validated by `tools/artifact_vocab.py`; the source of `prior_outcomes`. | a field a schema demands |

`/apply` gets a step only after two more applications have run on this file (a parked to-do with that gate).

---

## 7. Public and private

This file and `framework/templates/build-log.md` are public: rules and cites only. The appendix (`output/artifact-workflow/appendix.md`) is private and holds the build codes, the reaction table, the defaults ledger and the outcome of every build. Before any change to a public file here: `tools/gen_pii_denylist.py`, then `/audit-pii`. Never copy an outcome, a reader quote or a company name into this file.
