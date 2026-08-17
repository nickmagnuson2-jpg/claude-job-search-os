# Changelog

All notable changes to this job search system are recorded here.
Format: newest entries at the top.

## 2026-08-17: a note that broke a parser, and the invariant that will catch the next one

Three defects in `data/job-todos.md` tooling, found in sequence, each exposed by fixing the one
before it. The last is the only one that generalises.

**1. Eighteen rows were unreachable.** The 2026-08-05 bad `sync` withdrew 18 todos; the
restore-from-backup put them back under `## Completed` with their status still `Pending`. `done`
and `update` search the Active section only, so those rows were invisible to both — and to anyone
reading the Active list — while `todo_daily_metrics.py` kept counting them Pending. They had been
inflating the overdue count on every `/standup` since early August. The producer was already fixed
on 8/10 (verified in code, not assumed); this was the backward sweep that fix skipped.

Built: an `audit` subcommand (read-only, reports section/status disagreement with line numbers), a
`done` fallback that repairs a misfiled row in place and reports `repaired_misfiled`, an `update`
refusal that names the section instead of the bare "No ACTIVE task found" that had sent an earlier
investigation chasing match-string variants, and `withdraw` scrub + disambiguation.

Disposition of the 18 was 5 Completed / 10 Withdrawn / 3 back to Active — deliberately not a bulk
close, because three were live parked work that a mechanical sweep would have destroyed. Pending
253 → 245, overdue 131 → 116.

**2. Two defects the fixture suite could not see.** Both surfaced only on the real file. A misfiled
row is in ACTIVE column format inside a COMPLETED-format section, so its status word lands where
that schema reads `notes` — `withdraw` set the date correctly and left the literal string "Pending"
in ten rows. And because `withdraw` searches Active first, running it after re-adding a recovered
row withdraws the *new* row, leaving two same-named Completed rows and a permanent "Multiple
matches" bail.

**3. A Notes cell impersonated a section header, and every check missed it.** A recovery note
written during (1) contained the literal text `## Completed`. `todo_daily_metrics.py` found sections
with an *unanchored* regex, so it matched inside the table row and collapsed the Completed section
from 744 rows to 2. **`completed_today` reported 0 while five rows in the file read
"Completed 2026-08-17".** No error, no warning, a plausible number — flowing straight into the daily
log, the streak, and velocity.

Every check the writer had passed, because `todo_write.py` walks lines and was never vulnerable: the
writer's checks and the writer's bug were on the same side of the boundary. It was caught only
because a number failed to reconcile.

**The fix is two-layered.** All four section regexes are now `^`-anchored with `MULTILINE` (a table
row starts with `|`, so `^##` can only match a real header), guarded by a test that greps the source
for the unanchored form — which immediately caught two more call sites that had been missed. And
`save_lines`, the single choke point all eleven mutation paths funnel through, now **verifies every
write with the downstream consumer**: it re-reads the file via `todo_daily_metrics.section_row_counts`
and rolls back if the reader sees different counts or a missing section. Proven against the live
file: a forced divergence returned writer `(252, 746)` vs reader `(1, 1)`, rejected, file
byte-identical.

The generalisable lesson is not about markdown in cells. It is that **verifying a mutation with the
tool that made it proves nothing about the tool that reads it** — the same shape as the green fixture
suite in (2) and the unswept damage in (1). The invariant is in the write path because a rule about
what may be typed into a note converts at zero.

Suite 1854 → 1869.

## 2026-08-14: the ECONNRESET drops are upstream, proved by TTL, after two wrong answers of my own

Four days of investigation had narrowed the recurring `API Error: Connection dropped (ECONNRESET)`
to a hard ~180s cliff but could not identify who owned the timer: home router NAT, ISP CGNAT, or
the server edge. A packet capture settled it.

**The answer: Anthropic's edge resets live connections.** Of 37 connections captured, **17 were
reset by the server with no client FIN first** (one took 13 consecutive RSTs). A further 8 were
benign client-close-then-server-RST pool teardowns, which is the trap — counting those as failures
inflates the result, so each RST was paired against its own connection's prior events.

**The discriminator was TTL.** Every packet from the API IP — the RSTs, the bare option-less ones,
and the ordinary data packets — arrives at `ttl=57`. An RST injected by a middlebox on our side of
the path would carry a different hop count. **Home router and ISP are both eliminated.** Nothing on
this machine fixes it; the keepalive sysctl applied earlier was reverted, having targeted a local
path timer that does not exist.

**Two of my own conclusions had to be retracted first, and both failures were measurement, not
reasoning:**

1. **An investigation contaminates its own corpus.** Counting transcript lines containing
   `ECONNRESET` returned 89 records; only **18** were real (`isApiErrorMessage: true`). The other
   80% were us *discussing* the problem — prose, changelog quotes, and recursively the tool result
   from reading the investigation doc. The count rises the more you investigate, in the direction
   that makes the problem look urgent. It nearly shipped a "16x worse after the fix" finding.
2. **A sleep-resume theory I called "conclusive" was refuted an hour later.** Debug logs showed
   errors 0.5s after resume-from-idle, which looked decisive. But on clean data **0 of 18 real
   errors follow a ≥10min idle**, while 72% land in the 150–230s band. Two distinct phenomena had
   been conflated: transient dead-pool sockets that self-heal via retry and never surface, versus
   the user-visible ~180s failures.

**The contaminated data had also produced the original investigation's wrong refutation.** It
dismissed keep-alive idle reuse because "73% of resets occur within 15s of the previous entry" —
that 73% was entirely discussion records, which naturally sit seconds apart mid-conversation. On
real errors it is **0%**. The contamination was hiding the cliff, not creating it.

**Method note:** the capture nearly produced a false negative twice. A 0-byte pcap was write
buffering, and a `wc -l` of 0 was a failed `sudo` producing no output — neither was a measurement.
A positive control (open/close a TCP connection and confirm it was recorded) is what separated
"no events" from "instrument not working."

Evidence preserved at `output/analysis/evidence/081426-econnreset-rst-capture.pcap`; full writeup
in the RESOLVED banner atop `output/analysis/081426-econnreset-investigation.md` (both gitignored,
private-backup only).

## 2026-08-14: the PII ambiguous-tier problem dissolved by renaming, not by a smarter guard

A real pipeline-target company had a single-word name that is also an ordinary English word.
`gen_pii_denylist.py` builds the denylist from the pipeline company column, so that word became a
token — but it appears 396 times as ordinary prose across the private trees and 76 times across 32
public tracked files. The generator therefore routed it to the ambiguous WARN tier, which exits 0
and is not surfaced, so it protected nothing.

**What did not work:** designing a discriminator. A `/plan-hardening` pass (47 agents, ~2.9M tokens)
returned UNCONVERGED and correctly concluded the design could not work — public files are
overwhelmingly markdown, capitalization does not separate the cases, and the hook sees only
`new_string` on an Edit so surrounding context is unavailable.

**What worked:** Nick renamed the pipeline cell to the company's domain-style form, which is not an
English word. Measured before/after — ambiguous tier 1 → 0, BLOCK list 0 → 1 (plus the slug form);
an ordinary-prose public write now exits 0 unflagged, and a public write containing the company
token exits 2 and blocks. A three-byte edit where the alternative was unshippable. The dossier and
JD were renamed to match and the output directory moved to the `output/<slug>/<slug>.md` convention;
the company's own product terminology and the append-only daily log were left verbatim.

**This entry is itself evidence the fix works.** The first draft named the company in prose and
`check_public_pii.py` BLOCKED the write at exit 2 — the token is now on the deterministic denylist
instead of the invisible WARN tier, and it caught a real public-artifact leak minutes after shipping.

**The lesson, captured as a rule:** when a guard cannot separate a true positive from a false
positive, the ambiguity is upstream in the data it reads, not in the detector. CLAUDE.md Step 3
already mandates tracing to source before building a guard; the miss was skipping grey-area step (0),
which is itself a Hard Rule and exists to make rules like that bind. A user-named process
("run /plan-hardening first") occupied the slot where the pre-check belongs. Parked a Low todo for
the structural gate with a 4th-fire REOPEN trigger.

**Also fixed:** two docs indexed (`tools-reference.md` was never listed either), three stale counts
corrected (7 → 11 memory shards in two places, 35 → 38 skills), and the memory-hygiene handoff
regenerated to rev 5 from live detector output — backlog 40/432, and rev 4's "start with the two at
5 fires" advice replaced, having been tested and found wrong.

**Known and unpatched, logged to `memory/friction-log.md` at 1 fire:** `check_save_claims.py:184`
and `check_stale_file_claim.py:234` both prune directories starting with `.`, so nothing under
`.claude/` is findable by bare basename — all 38 skills, every workflow, `settings.json`. Confirmed
live. Convention is log at 1 fire, patch at 3.

## 2026-08-14: plan-hardening workflow was unrunnable — unescaped backticks in a prompt

`.claude/workflows/plan-hardening.js` failed to parse when invoked, aborting before any agent
ran. Line 257 sits inside a template literal and contained an unescaped backtick pair around the
word `why`, which breaks out of the string mid-prompt.

**It passed `node --check`.** The two backticks cancel each other, so overall parity stays balanced
and a syntax check reports clean — but the Workflow runtime's parser rejected it, and more
importantly the prompt text was wrong regardless: inside a template literal, a backtick must be
escaped to be literal text. A file can be parseable and still say the wrong thing.

Introduced by `516098a` ("the judge derives severity instead of inheriting it"), which added the
"ALSO CHECK WHAT THE PANEL BUILT" paragraph. Shipped and pushed; the workflow had been unrunnable
since. Fixed by escaping both backticks. A parity scan over the whole file confirms this was the
only occurrence.

Found because the workflow was invoked for real. A prompt-only change inside a template literal is
invisible to the test suite and to `node --check`, so nothing but an actual run would have caught it.

## 2026-08-14: check_stale_file_claim.py Stop hook, and the WARN-tier correction chain

Backfilled — these six commits shipped before this entry existed. See the note at the end.

**`tools/check_stale_file_claim.py` (new, Stop hook, exit 2)** — catches telling the user a named
file "hasn't had a pass this session" when mtime says otherwise. Built first as a process guard
("did an `ls` run on this path?"), which passed 35 unit tests and then scored ~zero true positives
against 4,085 real assistant messages: the dominant false positive was the legitimate self-report
("`data/goals.md` untouched", meaning *I chose not to edit it*), which carries the same evidence
signature as the defect. Redesigned to check mtime instead — 6 hits (0.15%), two of them the
original 2026-07-31 incident recovered verbatim, 0 spurious blocks across 60 real transcripts.
37 tests. Commit `8e80024`.

**The WARN-tier chain (`9e71d52`, `dde8231`, `506a19a`, `ad96350`).** Writing that hook put a real
pipeline-company name into two public files. It was caught by hand at staging, not by the always-on
PII hook — which had detected it correctly and emitted an exit-0 stderr WARN that Claude Code does
not surface. Tracing that to source found a recall dead-end:
`memory/feedback_warn_vs_block_hook_design.md` had been archived and deleted in the 2026-06-13 pass
while 13 `tools/*.py`, `CLAUDE.md`, `HOOK_AUTHORING.md` and this changelog still cited it by name.
Its 2026-05-28 correction — *a PreToolUse exit-0 WARN is non-functional; default to BLOCK, or don't
build the hook* — was therefore unreachable, and `gen_pii_denylist.py` had implemented the rule's
superseded "default to WARN" form. Worse, `CLAUDE.md`'s own hook-tier bullet prescribed that
superseded form to every session.

Fixed at the source, not just the symptom: memory file restored (3 fires); `CLAUDE.md` hook-tier
bullet corrected to "PreToolUse: BLOCK (exit 2) or don't build it"; `gen_pii_denylist.py` docstring
and generated header corrected; `.claude/skills/audit-pii/SKILL.md` gained the second deterministic
blind spot (pipeline membership does not imply BLOCK-tier coverage, because dictionary-word company
names are routed to the invisible WARN tier); `HOOK_SPEC_status_query_verification.md` clarified
that its always-exit-0 design is correct for `UserPromptSubmit`, where stdout injection *is*
delivery, and must not be generalized to PreToolUse. No hook behavior changed.

**Why this entry is a backfill.** Six pushes went out before any changelog entry was written.
`tools/check_changelog_currency.py` exists to prevent exactly that and did not fire: run directly,
it exits 0 with no stdout and no stderr, and every return path in it is `return 0`. It is a third
instance of the defect above — a guard that cannot reach the agent it is guarding. Nick supplied
the correction the hook could not.

## 2026-08-14: CLAUDE.md 41.1KB -> 37.2KB, data-file conventions moved to docs/data-file-conventions.md

CLAUDE.md went 142 bytes over its 40KB always-loaded budget when a Hard-Rules bullet was
corrected (the hook-tier bullet had been prescribing the superseded "default to WARN exit 0",
which is how a WARN tier shipped that warns into a void). The file had **24 bytes of headroom**
before that edit, so any correction longer than the wrong line it replaced would trip the
budget. Scraping 142 bytes would have tripped again on the next fix.

Moved verbatim to `docs/data-file-conventions.md` (3,710 bytes, two contiguous subsections):
**Decisions & Accomplishments Logs** and **Personal Exploration — Four Kinds**. A pointer block
stays behind carrying explicit triggers ("read before writing to data/decisions.md or
data/accomplishments.md, routing a /remember capture between them, or creating a reflection,
workbook, therapy doc, or identity file") rather than a bare see-also.

**What deliberately did NOT move**, confirming the 2026-08-13 entry's warning about this exact
section: `### Write-Only Files` stayed resident. The audit script scores it reference-shaped,
but its table encodes a rule — `Edit` silently fails on rows >500 chars, so a session that never
read it would corrupt `data/job-todos.md` without an error. Also held back for a later pass:
Three Identity Docs (boundary rules whose violation is silent), Resume Bullets vs Spoken STAR
(contains a real prohibition), and Tools & Environment (one undifferentiated block mixing two
load-bearing rules with pure reference — needs splitting, not relocating).

The sealed-folder prohibition stays in Hard Rules; only its elaboration moved, verified against
line 14 before the move.

Verification: Step 0 gate passed (72-rule baseline, 13 blocks reconstructing all 41,102 bytes);
byte accounting balances exactly (41,102 = 38,099 trimmed + 3,710 moved - 707 pointer); rule
conservation checked against the UNION of source and destination with 0 missing; suite 1650
passed; `oversized_context_files` back to 0.

Also this session: `feedback_warn_vs_block_hook_design.md` restored to the memory corpus after
being archived in June while 13 tools/*.py, CLAUDE.md, HOOK_AUTHORING.md and this changelog
still cited it by name — a recall dead-end that let a build implement the rule's superseded form.

## 2026-08-13: CLAUDE.md trimmed 56.5KB -> 37.5KB, tool tables moved to docs/tools-reference.md

CLAUDE.md was 1.38x over its 40KB always-loaded budget — a size paid as a tax on every
session, forever. Four pure-lookup tables moved out verbatim to `docs/tools-reference.md`:
atomic write scripts (37 rows), launchd background jobs (9), private local config (10),
multi-agent workflow templates (5). A router pointer with a trigger condition stays behind
("read before invoking any tools/*.py script"), because a pointer with no trigger is a
pointer nobody follows.

**What deliberately did NOT move**, against the audit script's own advice: the script scored
Tools & Environment as MOVE wholesale and Data Files as MOVE. Both are wrong in part, because
rule-shaped lines sit inside reference-shaped sections. The `PYTHONIOENCODING=utf-8`
requirement and the prohibition on creating/truncating files in `tools/launchd/logs/` (which
killed 8 launchd jobs for ~18 hours) stay resident — they are prohibitions with destructive
failure modes, not lookups, even though they sit beside the tables that left. Data Files stays
whole: its "write-only files, use these scripts not Edit" table encodes a rule (Edit silently
fails on rows >500 chars). Hard Rules and the Self-Improvement Loop stay regardless of byte
share.

Verified per the trim-context-file protocol: Step 0 gate (90-rule baseline, 13 blocks
reconstructing 56,487 bytes exactly); rule conservation against the UNION of trimmed source
and destination, 0 of 90 missing; byte conservation with all 61 moved table rows
byte-identical to their Step 0 block; 1590 tests green; 0 hits against 414 PII denylist
tokens.

Back-propagated: `feedback_ship_isnt_done_until_doc_surfaces_swept` named "the CLAUDE.md tools
table" as its future hook's enforcement target in four places. Repointed. Unswept, that hook
would have been built to check a table no longer there — and would have passed every commit
silently, which is worse than no hook, because it reports green.


## 2026-08-13: memory shards re-split 7 -> 11, and the promotion backlog worked down

**Shards.** `index-tools` (42KB, 1.72x) and `index-system` (38KB, 1.53x) were over the ~24KB
read budget five weeks after the 7-shard restructure. Split by artifact type into four new
shards -- `index-hooks`, `index-repo-ops`, `index-agents`, `index-verification` -- per the
CLAUDE.md rule to split rather than let a shard grow past budget again. Every shard is now
under; the largest is `index-outreach` at 24.3KB.

Destinations are topical, not parent-derived: a verification rule filed under tools moved to
`index-verification` regardless of origin. 80 of 248 entries (32%) were ambiguous to the keyword
classifier and were resolved by reading each file's own `description` frontmatter instead of
guessing. One duplicate index entry (`blind_sighted_pair_for_self_audit`, listed in two shards)
was collapsed. Conservation asserted on line count and line multiset before writing.

**Archival was measured and rejected as the lever:** under the memory-refresh CLASS guard only
7 of 318 entries were archivable (646 bytes), because 242 are unpromoted live rules. Splitting
was the only thing that could work.

**Promotion backlog 22 -> 12.** New `terminal:` frontmatter key retires rules no artifact can
enforce (12 marked, each with a stated reason, fails open). Five rules promoted to skill or
hard-rule tier; `tools/source_corrections.py` added; five broken or missing reopen gates
repaired. Four audit verdicts from the 2026-08-13 corpus mining were overturned on inspection.


---

## 2026-08-13: Two repeat-fire rules promoted out of memory tier

Both surfaced by `/memory-refresh` — but only after fixing the reason it could not see
them. **The promotion detector reads an `occurrences:` frontmatter key; `/lessons-learned`
writes a `## Promotion criterion` prose section. 288 memory files had the prose, 4 had the
key**, so a scan returning "0 promotion candidates" was an empty scan reading as an
all-clear. Two rules at 2 and 3 fires — one with its structural fix already designed and
unbuilt — had never surfaced. The global `/lessons-learned` skill now emits both forms and
increments `occurrences:` on update.

**`plan-hardening` no longer returns a pass/fail certificate.** `airtight: true` is a
claim the panel makes about itself, and it was wrong twice — most recently certifying a
470-line spec that produced 7 real defects on execution, two of which the panel had
flagged as blocking in round 1 and silently dropped in round 2. The judge now returns
`residual_risks[]` (status mitigated/accepted/open, each with a `where_to_verify`) plus
`unverified_claims[]` — repo-state assertions the plan makes that were never checked,
which the executing agent must treat as work. The loop stops on a **delta rule** (no NEW
blocking hole for K rounds) rather than waiting for a verdict that is anti-convergent by
construction, and the reviser gets a hard length budget so rounds replace rather than
accrete.

**`check_public_pii.py --scan`, and `/audit-pii` uses it.** This rule was *already* at
skill tier and still failed: Step 0b has always said to use word boundaries, and a
pre-push sweep hand-rolled `grep -qiF` anyway, producing ~190 false hits from short tokens
matching inside unrelated words. The root cause was ergonomic — the correct matcher was
reachable only through a per-file synthetic stdin payload, so auditing a tree meant
reaching for grep. `--scan` makes the correct sweep one command over a path list, with the
same public/gitignored/binary filters. Exit 1 when it scans zero files, because an empty
sweep is not a pass. Step 1 now forbids hand-rolled greps and names the `--replace-text`
corruption risk.

Docs brought current in the same pass: the 8 tools from the 8/12 build were missing from
the CLAUDE.md tools table entirely, and both `/plan-hardening` descriptions (CLAUDE.md +
`framework/multi-agent-workflows.md` §3) still described the removed certificate.

---

## 2026-08-12: Drafting-integrity hardening — send-state, reserve proofs, exclusion scans

Four workstreams closing defects from the 8/10-8/11 sessions. Each is independently
landable; build order W1 → W4 → W3 → W2.

**A prep doc can no longer tell you to withhold something that never arrived.** The old
chain was "a draft file exists under `output/`" → *"CV already sent 8/4, do not re-offer
it."* The CV had bounced. `outreach_status.py` derives send-state from
`data/outreach-log.md` and splits *transmission* from *receipt*: only `delivered=true`,
for the named artifact, unlocks suppressive phrasing. A recipient-level query can never
report `delivered:true`, because the recipient may have replied on an unrelated thread.
`--set-status` makes a bounce recordable; the log's status vocabulary gains `Bounced` /
`Delivered` / `Delivery unknown` (header only, nothing backfilled).

**Mentioning an artifact is not sending it.** Found by running the mandated real-data
validator, not by the fixture suite: 8 of 16 artifact-bearing log rows name a CV without
sending one — resume merely offered, CV promised but not yet sent, an explicit "not
attached", a thread whose subject contains the word — and several carry `Replied`.
Scoping receipt on mentions returned `delivered:true` for a CV that was
never sent — the original defect, reproduced by its own fix.
`artifact_vocab.find_transmitted_in_text()` gates it, nearest-marker-wins, deliberately
asymmetric: a missed send is harmless, a false send unlocks suppression.

**Prep docs bind a primary AND a reserve proof, in canonically different domains.**
Binding one proof is a single point of failure — an interviewer excluded that domain
20 minutes into a call and there was no fallback. `proof_domains.py` is a closed enum, so
`customer-experience` and `customer-ops` cannot pose as a fallback for each other.
`check_prep_doc.py` runs six checks and now gates `/prep-interview` Step 6a, **before**
the PDF render: the PDF is what gets read on paper, so a defect that reaches it has
already done its work.

**The transcript gets scanned for domain exclusions before a bound proof is deployed.**
`transcript_exclusions.py`, scoped to the counterpart by default (Nick uses "never"
constructions himself; surfacing his own line would block a valid proof). `hit_count: 0`
is explicitly not a clearance — every output carries a `coverage` string saying
paraphrases are not detected. `/follow-up` Step 3e runs on every follow-up type and emits
`[exclusion scan: NOT RUN — no transcript]` rather than passing silently. `/debrief`
Step 1c scores an undeployed proof as "interviewer excluded the domain", not as a miss.

**`content-rules` beats `voice-reference` on conflict, and a checker proves it.**
`X rather than Y` sat in voice-reference's "Patterns to ADD" as validated (n=1) while
content-rules B7 banned it by name; a drafting pass took the one labelled validated.
Precedence is now declared in all three files, all 69 rules carry a phrase registry
mirrored into the human index the skills actually load, and `check_doc_precedence.py`
compares them by bidirectional template subsumption. `rather than` is registered *only*
as a construction — as a bare literal it fires on ~10 benign prose uses, and a checker
that cries wolf gets ignored.

**One new hook, deliberately narrow.** `check_prep_doc_format.py` (PreToolUse,
`Write|Edit`) blocks only the two prep-doc defects decidable from the text alone: a
malformed or non-v2 `outreach_status` stamp, and proof domains that are invalid or
collapse to the same tag. Missing reserves, suppressive phrasing and stale stamps stay at
skill tier — they need context, and a PreToolUse "warn" is invisible in Claude Code, so
every hook is block-or-nothing.

103 new tests (1428 total). Spec, build log and reconciliation audit under
`output/analysis/` (gitignored).

---

## 2026-08-12: Data-integrity fixes, a discovery feedback loop, and four closed traps

**`todo_write.py sync` was destroying to-dos.** Two independent defects: the company name
was substring-matched against all columns joined, so a to-do that merely mentioned a
company in its Notes was withdrawn; and nothing checked whether that company also held a
live row in Active Pipeline. A single stale Archived row had mass-withdrawn 18 to-dos, 3
unrelated. Now matches the task column only, on a word boundary, and skips any company
with a non-terminal row. Verified against a copy of the live files: pre-fix withdrew 2
that day, post-fix withdrew 0.

**`pipe_write.py remove` gains `--stage`.** It hardcoded `Withdrawn`, so a loop that ended
because the company passed was recorded as a withdrawal. Opposite facts, and every
conversion metric computed off the file inherited the error.

**Discovery output now routes, and rejections compound.** `act_apply.py` gains
`target-add` / `target-reject`; `scan-targets.yaml` gains outreach-only rows (no `ats`,
skipped by the nightly scan); and `agent_core.load_known_names` reads `rejected:` as well
as `companies:`, so a declined company stops returning to the inbox every week. 44 scored
candidates triaged: 24 kept, 20 rejected. Collector dedup set 11 to 55.

**Inbox tooling.** `inbox_census.py` (comment-span-aware oracle; a 372-line HTML comment
was hiding headers, so the real count was 147/31 not 150/34) and `inbox_triage.py`
(read-only extraction of 108 non-machine blocks, grouped by destination).

**PII: the denylist had a blind spot over the entire target pool.** `gen_pii_denylist.py`
read only `networking.md` and `job-pipeline.md`, never `scan-targets.yaml`. A real target
company reached a public test file as a fixture name and was **not** blocked; the semantic
subagent pass caught it, the deterministic hook did not. Fixed at source; 332 to 414
tokens.

**Four standing traps closed.** The suite runs clean with **no `--ignore` flags** (1325
passing): `scan.py` called `tqdm.pandas()` at import time, hard-requiring a scanner-only
dependency and breaking collection for the whole suite, while enabling a feature the
package never uses. That had been hiding a genuinely failing test (`KeyError:
'target_role'` from a stale template test; production was correct). The pre-push PII gate
is now tracked in `tools/hooks/` with an installer, closing a gap where a fresh clone had
**no push-time gate at all**. And the internal-only naming rule, cited by reflections for
months, is finally written into `framework/style-guidelines.md`.

**Known and deliberate:** `output/deployment-strategist/071626-magnuson.*` carries a
GenAI-workshop overclaim corrected on 2026-07-08. Decision 2026-08-12: **leave as is**, it
is the record of what was sent. The canonical reference CV was fixed so it stops
propagating.

---

## 2026-08-12: Sleep-killed API connections, eight silently-dead launchd jobs, and a rebuilt trim guard

**API errors: macOS idle-sleep, not the API.** Recurring `ECONNRESET` / "connection closed
mid-response" traced to the machine sleeping every 60 seconds on battery *and* on AC
(`pmset sleep 1` on both), tearing down pooled TCP connections between requests. 2,044
sleep/wake events in one day. Raised to 15 min (battery) / 30 min (AC). Proxy, DNS, MTU,
IPv6 and API reachability were each ruled out with evidence first.

**All 8 launchd jobs were dead for ~18 hours, silently.** `EX_CONFIG` (78) is a *job setup*
failure: launchd opens `StandardOutPath` itself, and the log files carried a
`com.apple.provenance` xattr that denied it. The script never ran, so nothing was logged to
explain why. Isolated by cloning the plist and changing one variable at a time — identical
job, only the log-file identity differed. **Files created from a Claude Code Bash call
inherit the tag**, so recreating a log reproduces the bug and `xattr -d` reports success
without removing it. Fix: delete the log, let launchd recreate it. Logs archived, all 8
verified exit 0 and firing on schedule.

**Gmail token corruption.** `token_path.write_text()` truncates in place; a death mid-write
(ample opportunity, given the sleeping) left an empty token and a `JSONDecodeError` on every
later run. Now atomic via temp + `os.replace`, with a corrupt token reporting how to re-auth
instead of a traceback.

**career-scan was silently scanning nothing** for two companies: both had migrated Greenhouse
→ Ashby and the config was never updated (one is a pipeline company with 73 open roles). A
third target has no reachable ATS at all and was deactivated. Nothing is cached — targets are
read fresh from `scan-targets.yaml` each run; the config had simply aged out.

**`/trim-context-file` re-enabled behind a real gate.** The skill relocates sections out of
`CLAUDE.md`, which holds every hard rule, and had been disabled since 2026-08-10 because its
verification invoked flags that did not exist — producing an empty before-set that made the
"any normative line missing is a hard abort" check pass vacuously. `context_file_audit.py`
gained `--capabilities`/`--require`, `--rules`, `--emit-blocks`, `--expect-blocks`, and
fence-aware splitting (the old splitter produced phantom sections in 32 of 113 tracked
markdown files), each with a loud failing mode. New `tools/trim_context_gate.sh` is a
mandatory Step 0.

Built builder → tester → auditor, and **each round found a defect the previous one
introduced**: the guard against stale blocks became a file-deleter that destroyed a folder of
numbered user notes at exit 0 (fixed by requiring `manifest.json` as provenance — filename
shape is a *guess* about provenance, not evidence); the guard against empty baselines left a
*thin* baseline that missed the em-dash hard rule entirely (fixed with a structural
bolded-bullet rule, coverage 16% → 29%). The auditor wrote its spec before reading the code,
then mutation-tested: 15 of 18 mutants caught, and the two survivors were rule-detector
coverage — no test asserted a *known* rule was captured, so the detector could silently
narrow while the suite stayed green. Closed with fixtures where each keyword is the sole
marker on its line. 184 tests.

Two corrections that generalize: **byte conservation is the load-bearing guarantee, the rules
diff is corroborating** (both sides share a detector, so a systematic omission cancels out);
and a gate must check **exit status**, never file existence or size, because `cmd > f`
truncates `f` before `cmd` runs.

**Personal-vault path swept.** The vault root was hardcoded in 17 places across 7 public
files, so scrubbing any one was cosmetic. Now resolved solely via `tools/vault_paths.py`
(env var or gitignored `tools/.personal-vault.conf`). No fallback by design — the vault holds
sealed therapy routing, so a guessed destination is worse than stopping. Accessors are
functions, not constants, so an unconfigured vault fails at *use* rather than making the
module unimportable. Regression test greps tracked public files and fails if the root returns.

**A guard that passed on staging state.** The regression test added to lock the vault sweep
spelled the root literally as its `git grep` needle, putting the string in a tracked public
file so the guard matched its own source. It went green only because `git grep` searches
*tracked* files and the test was still untracked when written — a pass that depended entirely
on staging state. Once committed it failed. Needle now built from parts; a guard that flags
itself gets deleted rather than fixed.

**Doc drift corrected:** `agent-discover-collect` was documented at $0.05/run from a two-preset
measurement; a third preset was added and lane-b costs 4x the others, so it is $0.15 (~$7.80/yr).
Skill count 35 → 38. An external system's codename removed from two `Origin:` notes.

---

## 2026-08-10: Public-repo privacy hardening, portable hook paths, and a silent-failure test suite

**Privacy.** A full `/audit-pii` pass (deterministic denylist scan + four independent semantic reviewers over 63 changed public files) surfaced leaks the denylist structurally could not catch.

**Root cause, corrected.** An earlier draft of this entry said `gen_pii_denylist.py` "emits person names only." That is false — verified: it parses 105 companies and 70 reach the denylist. The real mechanism is narrower and was a *deliberate* design choice: `is_distinctive_single()` drops single-token company names that are ordinary English words, because matching them would false-positive against prose. 35 companies are dropped this way. A live pipeline company whose name is an everyday noun was therefore absent from the denylist while all three of its interviewers were present — and its name reached six public files. **Fix: a second WARN-only tier** (`tools/.pii-denylist-ambiguous.txt`, gitignored, 29 tokens) that surfaces these without blocking, per `feedback_warn_vs_block_hook_design`. Verified: ambiguous token → WARN at exit 0; hard denylist token → BLOCK at exit 2; clean content → silent.

Also fixed in this pass:

- Real contact/company names scrubbed from two hook scripts, five test files, and one skill doc (45 replacements).
- A new leak class documented: test fixtures whose *names* were placeholder-ized while **verbatim sealed-transcript content** was copied across unchanged. A name denylist can never catch this. Fixtures derived from real transcripts must be paraphrased, not just renamed.
- Consulting-case material moved out of the repo into the gitignored archive.
- `framework/content-rules.{md,yaml}` made private (gitignored) — the exemplar corpus held a real compensation figure and a valuation/headcount/stage triple identifying one company. Paths unchanged, so all five referencing skills keep working and degrade gracefully.
- Private backup remote, GitHub account, and overlay git-dir removed from `CLAUDE.md`, `/checkout`, `docs/usage.md`, and `backup-data.sh`; they now load from the gitignored `tools/.private-backup.conf`. Missing config is a **hard failure**, never a silent skip.
- Owner email removed from `granola_auto_debrief.py`, now loaded from gitignored `tools/.owner-identity.txt`, falling back to the public name so forks work unchanged.

**Portability.** All 25 hook commands in `.claude/settings.json` migrated from hardcoded absolute paths to `$CLAUDE_PROJECT_DIR`. The repo is now forkable without editing settings.

**Silent-failure test suite** (`tests/scripts/test_no_silent_failures.py`, 9 tests). Guards the failure class this repo actually suffers from — things that quietly stop running. Asserts: settings.json parses; every hook command resolves to an existing, compiling script; no command hardcodes a home path; `$CLAUDE_PROJECT_DIR` resolves; one representative hook executes end-to-end; every `check_*.py` is registered or explicitly exempt; every launchd plist points at a real script and has a schedule. **Each assertion was mutation-tested** — verified to fail when the invariant is broken — rather than trusted because it passed on first write.

Also: `backup-data.sh` now backs up the newly-private config files, and derives the Claude project-memory path instead of hardcoding it. Full suite green at 1024 tests.

---

## 2026-07-08: MEMORY.md restructured into a 7-shard router + memory-overhaul promotion backlog closed out

### Changed
- **`memory/MEMORY.md` is now a router, not the index.** It holds only a small always-visible Critical Context block (facts that must never depend on recall — employment status, family contacts, active hard-rule-DUE items) plus a Topic Shards table pointing to 7 `memory/index-<topic>.md` files (outreach, coaching, research, tools, system, personal, projects). All ~267 prior index entries were classified by topic and migrated verbatim, with one-line cross-link pointers for entries spanning two shards. MEMORY.md itself dropped from 279 lines / 66KB to 27 lines / 4KB, well under the 24.4KB auto-load cap that had been silently truncating it. This supersedes the earlier 2026-06-04 restructure prep (family-consolidation approach, never built) with a simpler topic-shard split.
- **`.claude/skills/{weekly-review,wispr,memory-refresh,analyze}/SKILL.md`** and **`CLAUDE.md`** updated to route index writes/reads to the correct shard instead of assuming a flat `MEMORY.md`.
- **`~/.claude/skills/lessons-learned/SKILL.md`** (global, cross-project) now detects the router pattern and routes new lesson captures to the right shard when present, falling back to flat-file behavior for projects that don't use it.
- **`tools/todo_daily_metrics.py`** and **`tools/scan_promotion_candidates.py`**: exclude `index-*.md` shard files from memory-file counts/scans so they aren't miscounted as individual memory entries.

### Fixed (bug fixes shipped same day, unrelated to the restructure but part of the same session)
- `todo_write.py`: new `list` subcommand + a `_safe_cell()` guard that neutralizes literal `|` characters before they're written into a table row (root cause of a job-todos.md column-drift bug); 366 drifted rows migrated back to canonical 4 columns.
- `pipe_write.py`: reject `update`/`remove` on a pipeline row with the wrong column count instead of silently truncating Notes/URL.
- `remember_classify.py` / `remember_apply.py`: new `source_article` and `deferred_idea` routing branches.
- `gen_pii_denylist.py`: emit slug-forms alongside name-forms so path-embedded PII doesn't slip the denylist.
- `friction_log.py`: fixed an inverted `--unpromoted` filter.
- `career_scanner/cli.py`, `gmail_fetch.py`, `scan_transcript_failures.py`: import-path fix, label-scoped history-sync gap, transcript flush-race hardening respectively.

### Promoted (memory-overhaul promotion backlog worked to completion)
- 8 memory rules promoted into skill/hook/hard-rule tier: `follow-up`/`cold-outreach`/`draft-email` SKILL.md (voice-pure dictation mode, reply-mode source grounding, spine-first trigger), `audit-pii` SKILL.md (anti-rationalization subagent instruction, cross-check requirement, full-tree+history+remote-ref sweep step), and a new CLAUDE.md Hard Rule banning role-title inference (fired 3x).
- 2 more confirmed already-landed on inspection (`/wispr` topic-match gate, `/ss` source-read pass).
- ~34 correctly re-verified against their own stated promotion gates and left parked with dated notes in `memory/promotion-backlog-2026-07.md`.

### Origin
- Multi-session memory-overhaul project (handoff: `output/analysis/070826-memory-overhaul-handoff.md`). This entry closes it out.

---

## 2026-06-18: Atomic-write hardenings — todo supersede, reply-direction fix, draft CC, traceback friction-log

### Added
- **`todo_write.py supersede <prefix>`**: withdraws every open Active row whose task starts with the given prefix, keeping a single live item per logical group. Superseded rows archive to Completed marked `Withdrawn <today>` (a cancellation, not a completion). Built so `networking_write.py log --followup` can supersede a contact's prior auto-generated follow-up before adding a fresh one, instead of stacking duplicate `Follow up: <name> — ...` rows on repeated logging. The `— ` prefix match targets only the auto format, leaving a manually-curated variant untouched. New tests in `tests/scripts/test_todo_write_withdraw.py`.
- **`open_draft.py` `CC:` field**: `tools/.pending-draft.txt` now accepts an optional `CC:` line (placed between `TO:` and `SUBJECT:`); the address is threaded into the Gmail compose URL. Omit the line entirely when unused.
- **`check_script_error_logged.py` Branch B (traceback capture)**: the friction-log hook now also catches Python crashes that do **not** follow the `tools/*.py` `{"status":"error"}` JSON contract — inline `python3 - <<heredoc` scripts and skill helpers outside `tools/` that raise a traceback. A traceback is an unambiguous crash signal (no graceful-empty carve-out); surface name is derived from the command's tools script, then skill name, then deepest real `.py` frame. The two self-referential scripts (`friction_log.py`, `check_script_error_logged.py`) are excluded to avoid logging loops. Origin: the `/ss` U+202F hand-typed-path `FileNotFoundError` (2nd fire 2026-06-18) was invisible to the old JSON-contract-only path.

### Fixed
- **`networking_write.py log` reply-direction false-flip**: logging Nick's *own* outbound reply ("Replied to her intro") previously flipped the matching `outreach-log.md` row to `Replied`, which means the *recipient* replied. The new `reply_received()` heuristic only flips on an inbound subject ("she replied", "heard back", "reply from"); an outbound marker ("replied to", "my reply", "I replied") suppresses the flip. Explicit `--reply-received` / `--no-reply-flip` flags override the heuristic. New tests in `tests/scripts/test_networking_write.py`.
- **`/learn` `args`-binding guard**: some runtimes deliver the workflow `args` parameter as a JSON-encoded *string* rather than an object, so `cfg.topic` read undefined and the run silently fell back to the `'UNSPECIFIED TOPIC'` placeholder (observed 2026-06-15, ~700k tokens wasted on agents that correctly refused to fabricate). `learn-workflow.js` now self-parses the string case (`string → JSON.parse → config`); SKILL.md documents the failure signature.

### Origin
- 2026-06-18 networking/todo session: blind follow-up logging stacked duplicate `Follow up:` todos (264/265 dup), and an outbound "Replied to her intro" log false-flipped a Sent row to Replied. Both traced to the atomic-write layer and fixed at the script tier with regression tests.

---

## 2026-06-14: /checkout proposes milestone accomplishment candidates (propose-only, anti-inflation)

### Added
- **`/checkout`**: new **Step 4e** ("Surface accomplishment candidates") scans the day's already-computed artifacts — substantive work (outputs, memories, reflections, inbox), the day's System Changes, and any win-shaped reflection — and proposes 0-2 milestone-level wins for `data/accomplishments.md`. Reuses Step 1 inputs (no new reads). Candidates must clear a strict three-part bar to qualify: **milestone, not maintenance** (a landed onsite, shipped dossier, compounding capability — not a log entry or hook fix); **process win, not career-history** (about the search, not a resume bullet); and **stands on its own a week from now**. Default output is **zero candidates** — anti-inflation is the explicit design intent, and a heavy build day is still usually maintenance. De-dupes against the top of `accomplishments.md`. Never auto-writes: it surfaces candidates in a new **Step 6 `#### Accomplishments check`** block (omitted entirely when nothing clears the bar) and only appends on Nick's explicit confirmation, via `tools/remember_apply.py` (`accomplishment` type), honoring any decline or edit verbatim.
- Docs sync: this CHANGELOG backfilled for 2026-06-11 (afternoon) through today; `docs/usage.md` hook-stack table updated with `check_public_pii.py` + `check_replace_all_safety.py`, skill catalog updated with `/learn` and `/audit-pii`, and the `/standup` + `/checkout` one-liners refreshed.

### Origin
- 2026-06-13: the commonplace-book launch was a genuine compounding win that only got captured because Nick volunteered it after the silent-failure probe — faulty recall, not faulty system. This step makes that capture structural without turning every build into an "accomplishment." Deliberately **evidence-grounded** (propose from real artifacts) rather than open-ended: distinct from the silent-failure probe in valence (wins, not gaps) and method (grounded proposal, not open prompt). Composes with the `accomplishments.md` boundary (milestone-level process wins only) and the CLAUDE.md "never inflate" rule.

---

## 2026-06-13: Fail-closed Granola meeting classifier (sealed-therapy leak fix) + replace_all substring-collision hook

### Added
- **`tools/check_replace_all_safety.py`** (+ `tests/scripts/test_check_replace_all_safety.py`, 14 tests): a PreToolUse hook on `Edit`/`MultiEdit` that BLOCKs (exit 2) a `replace_all: true` call when `old_string` is a short single token (no whitespace, ≤12 chars) that appears glued inside a longer word in the target file — the substring-corruption class where `"Ann"` → `"Anne"` silently expands an existing `"Anne"` to `"Annee"` everywhere. Word-char (`\w`) adjacency is the only danger signal, so punctuation-bounded matches (`color` inside `background-color`) don't fire; the block message surfaces the exact longer words it would alter. Fail-open on unreadable target / bad stdin / internal error; `REPLACE_ALL_OVERRIDE=1` bypasses for confirmed-intentional cases. Wired into the `PreToolUse` array in `.claude/settings.json`. Hook-tier promotion of the Edit-`replace_all` surface of `feedback_replace_all_substring_check` from behavioral to enforced — **supersedes the 2026-06-04 parked note** that flagged this as the one genuinely hook-able should-promote held back from the memory restructure. Commit `37cfb41`.

### Fixed
- **Closed the sealed-therapy-to-inbox leak in the Granola cron.** A name-less / mis-titled therapy session (no attendees, innocuous title) previously defaulted to `"networking"` and got posted to `data/inbox.md` + the voice corpus. The title-keyword-only classifier is replaced by **`classify_meeting(meeting, config)`**, a three-way, fail-closed classifier returning `therapy | networking | unknown`: an attendee on the therapist allowlist or a generic therapy title keyword → `therapy` (sealed to the personal vault, never inbox); the no-attendee branch with a therapist name in the transcript → `therapy`; an external (non-Nick, non-therapist) attendee → `networking` (the only class that reaches inbox + voice corpus); everything else → **`unknown`, which FAILS CLOSED — persisted nowhere and flagged for manual `/granola-pull`**. The orchestrator fetches the full note for attendees and classifies once, threading the resolved type into persistence so routing and storage can't disagree; the transcript-name scan is confined to the no-attendee branch so a real call that merely mentions a therapist's name is never mis-sealed. Wired into both the cron (`granola_auto_debrief.py`) and the manual path (`granola_cli.py`, which now exits 2 on `unknown` asking for an explicit `--type`). 14 new tests (`tests/scripts/test_granola_classify.py`); full suite 471 passing. Commit `6dcf357`.

### Changed
- **Therapist PII scrubbed from public code.** Real therapist identities (names + emails) moved to the gitignored `tools/.therapy-classifier.txt` (new `.gitignore` entry, loaded at runtime); public code carries only generic keywords. Genericized stray therapist names in `granola_split_existing.py` and `granola_save.py` example paths/docstrings. Commit `6dcf357`.

### Origin
- The leak case is the exact mis-titled, attendee-less in-person therapy session that `feedback_granola_autotitle_unreliable_for_classification` warned about — auto-titles can't be trusted for meeting-type classification, so the cron now classifies by attendee/transcript signal and fails closed when it can't. The hook closes the REOPEN gate parked in the 2026-06-04 memory-restructure entry (`feedback_replace_all_substring_check`, the `Reif`→`Reiff`→`Reifff` corruption class).

---

## 2026-06-12: Public-repo PII commit-gate, fuzzy contact-dedup guard, /learn skill, transcript-aware follow-up

### Added
- **`tools/check_public_pii.py`** (+ `tests/scripts/test_check_public_pii.py`, 115 lines): an always-on `PreToolUse` hook on `Write|Edit`, wired into `.claude/settings.json`, that **BLOCKs** (exit 2) writing a real contact name or pipeline-target company into a public-repo artifact (`tests/`, `.claude/skills/`, `framework/`, `docs/`, `tools/*.{py,md,sh}`, top-level `*.md`). Deterministic, high-precision: case-sensitive whole-word/phrase match against the gitignored denylist. Acts only on tracked, non-gitignored public paths; private `data/**`, the denylist, caches, and the in-repo `./memory` copy are skipped. Fails OPEN on any error.
- **`tools/gen_pii_denylist.py`** (178 lines) + gitignored **`tools/.pii-denylist.txt`**: regenerates the distinctive-token denylist (full names + brand-name companies) from current contacts/pipeline rows, so newly-added entities are covered by the deterministic layer immediately.
- **`/audit-pii` skill** (`.claude/skills/audit-pii/SKILL.md`): the commit-time gate. Refreshes the denylist, runs the deterministic scan over changed public files, then dispatches a **fresh, anti-anchored subagent** (given the file contents and the KEEP/SCRUB boundary but NOT the denylist) for a high-recall semantic pass that catches new names, ambiguous brand-words, emails, and phone numbers the denylist misses. Reads and proposes generic-placeholder fixes; never commits. The second of two layers with the always-on hook.
- **`tools/name_dedup.py`** (63 lines, stdlib `difflib`+`unicodedata`) + **`tests/scripts/test_networking_dedup.py`** (10 cases): a fuzzy name-duplicate matcher (normalize → accent-fold → `SequenceMatcher`, default threshold 0.84) that catches spelling variants, nickname-vs-full-name, and accent-only differences exact matching misses.
- **`/learn` skill** (`.claude/skills/learn/SKILL.md` + `learn-workflow.js`): a validated deep-briefing generator. Six-phase multi-agent workflow — scope (anti-anchored coverage checklist) → parallel live-web research → synthesis (BLUF-and-bullets, URLs required) → comprehension/interview quiz → adversarial verification (load-bearing claims independently refuted against live sources) → finalize (auto-correct flagged claims, attach real source URLs, write a validation report). Sibling to `/analyze` (one artifact) and `/deep-research` (one question); `/learn` is for understanding a whole subject well enough to teach or interview on it.

### Changed
- **`/follow-up` Step 3b — transcript-aware post-interview drafting**: for a post-meeting follow-up off a real call, auto-detects the contact's recent Granola transcript (`data/voice-corpus/granola/`, name-token + ~7-day match) plus any companion AI summary and debrief file, and sources the specific callback from it. **Content only, never tone**: a new Step 5 guard makes a transcript an invalid tone source (spoken, mixed-voice), so email voice stays anchored on the corpus; transcript-sourced callbacks are cited (provenance `I`), not invented. Graceful degradation when no transcript exists.
- **`/debrief` Step 12**: after a real-interview debrief, hands off to the transcript-aware `/follow-up` (offered, not auto-invoked), completing the chain `/granola-pull → /debrief → /follow-up`. Skipped for drills.
- **`networking_write.py add`** now BLOCKs on a fuzzy name match to an existing contact (`code: possible_duplicate`, `--force` to override) via `name_dedup.py`, on top of the existing exact-match check — closing the silent variant-spelling duplicate-row gap.

### Fixed
- **`outreach_pending.py` suppresses resolved threads from "awaiting response"**: outreach tied to a closed/passed pipeline company or whose subject is a thank-you / graceful close is still counted in sent/replied stats but no longer flagged as an overdue open loop, so `/standup` stops nagging on outreach that is already settled. New pipeline-stage cross-reference against `job-pipeline.md`; covered by expanded `tests/scripts/test_outreach_pending.py`.

### Origin
- 2026-06-11/12 — the PII gate promotes `feedback_generalize_examples_in_public_artifacts` to hook + skill tier after real names reached a public fixture; the dedup guard and closed-thread suppression are tier promotions of the dedup-before-append and `feedback_reconcile_stale_gate_against_specific_intel` rules; the transcript-aware follow-up grounds post-interview notes in what was actually said. Full suite: **457 passed, 3 skipped**; `/audit-pii` clean over all changed public files.

---

## 2026-06-11: Scheduled-interview surfacing in standup/checkout, batched email pings, auto-backup, public-tree tidy

### Added
- **Scheduled events from the pipeline Next-Action now surface in `/standup` and `/checkout`** (`tools/todo_daily_metrics.py`, `tools/todos_summary.py`). Both rankers built Top-3 from todo due-dates + pipeline stage only, so an interview written into the pipeline **Next-Action** free text (not filed as a dated todo) was invisible to both — a next-day interview got missed. New `parse_upcoming_scheduled()` parses ISO and bare `M/D` dates out of the Next-Action column, filters to a 3-day window, flags true events (interview/screen/call/onsite/panel) via `is_event`, and handles the Dec→Jan year rollover. `todos_summary.py` (standup's script) imports the same parser so both consumers can't drift. `/checkout` Step 5a and `/standup` Step 2 pin any `is_event: true` entry to Top-3 slot #1. New `/checkout` Step 5b also reconciles outbound "reach out / apply" todos against recent networking touches to suppress redundant actions. 7 new tests in `tests/scripts/test_todo_daily_metrics.py`. Commit `b3c6371`.
- **Batched desktop ping for new job emails** (`tools/gmail_fetch.py`). Fires one macOS notification per fetch cycle when new job-search-tagged emails land, summarizing senders and prompting to run `/act`. Counts only emails newly written that cycle (not the `inbox/` backlog), so untriaged items don't re-nag every 15 minutes; quiet hours 21:00–08:00 suppress the ping while files still land. Best-effort, never raises. Commit `757a704`.
- **`/checkout` auto-backs-up private data at end of day** (new Step 7). Runs `tools/backup-data.sh` as a closing, non-blocking action so the private `nick-job-search-data` snapshot pushes automatically each evening (via the `~/.nick-private-git` overlay; never touches the public repo). Surfaces a `Private backup: pushed/up-to-date/skipped` line and degrades gracefully on network/auth failure — a backup failure never aborts checkout. Replaces the prior manual-only backup. Commit `24abb04`.

### Changed
- **Housekeeping for the public tree** (no PII): moved stale/internal dev artifacts and doc snapshots out of the public repo into the private archive (`docs/archive/`, `framework/archive/resume-workflow.md`); genericized the LinkedIn-scanner fallback profile + rubric examples in `tools/linkedin-scanner/` (real profile still loads from gitignored `data/profile.md`); and dropped retired `notion_write.py` references from `CLAUDE.md`, `docs/CHANGELOG.md`, `docs/methodology.md`. Commits `4e0bb02`, `25ad7e0`.

### Origin
- 2026-06-11: a next-day interview sat in the pipeline Next-Action and was missed by both `/standup` and `/checkout` (`feedback_surface_scheduled_events_in_ranking`); a separate reach-out was re-surfaced after it had already been logged the day prior (`feedback_reconcile_stale_gate_against_specific_intel`). The backup auto-wire follows the 2026-06-11 private-repo reconcile making `~/.nick-private-git` canonical; the tidy/genericize commits were prep for the public git-history sanitization pass.

---

## 2026-06-11: CV first-draft quality defaults promoted into the framework + /generate-cv

### Changed
- **`framework/application-workflow.md`**: new CV Quality Standards subsections — **Summary Discipline** (opener must be standable, not fluff, not metric-stuffed; no location in summary), **Skills Section Discipline** (sentence case not Title Case; strongest-first; cut generic-buzzword filler even when evidenced), **Length & One-Page Verification** (one-page default for Nick; cut vague source-unbacked filler bullets, header-only entries OK). Added a **Render & verify** step to the CV Output Pipeline (render a PNG and Read it; count pages; rendercv layout gotchas: bare-year→"Jan YYYY", and the close/reopen-emphasis trick for stacked italic titles). Strengthened check #12 (skills format/filler), added checks **#19** (one page + PNG layout verification) and **#20** (summary + skills discipline) — now a 20-point checklist. Added a baseline-propagation Tailoring Rule (re-verify titles/labels/numbers against source, not a prior CV).
- **`.claude/skills/generate-cv/SKILL.md`**: Step 9b now renders a PNG; new **Step 9b-verify** (mandatory PNG layout + page-count check before presenting); Step 10b now **auto-applies the deep review's high-confidence objective fixes** (filler, skills, layout, one-page) and re-verifies, leaving the user only voice/judgment calls.

### Origin
- 2026-06-11 a recruiter-channel deployment CV took ~10+ user rounds, almost all mechanical cleanup the deep review had already flagged (IBM filler, skills grouping/case, redundant summary location line, two-page length) plus layout guessed from markdown instead of a render. Nick: "put it into the system so we don't have to do that again." Memories: `feedback_cv_first_draft_quality_defaults`, `feedback_cv_summary_must_be_standable`, `reference_rendercv_layout_tricks`, `feedback_zuora_principal_title_is_cpto` (CPTO title fix + source canonicalization).

---

## 2026-06-10: YouTube transcript fetcher, /analyze video branch, /prep-interview pipeline-advance, example persona refresh

### Added
- **`tools/fetch_transcript.py`** (+ `tests/scripts/test_fetch_transcript.py`): pulls a YouTube transcript plus title/author (oEmbed, no API key) and emits the `ExtractionBlock` JSON contract, caching a markdown copy to `data/source-transcripts/<video_id>.md`. Reusable by `/analyze`, `/reflect`, `/remember`. Optional dep `youtube-transcript-api>=1.2.0` (PEP 668: install `--user --break-system-packages`).

### Changed
- **`/analyze`**: the `video` source type is now live, wired to `fetch_transcript.py` (previously a documented stub).
- **`/prep-interview`**: on a confirmed, dated round it now advances the `data/job-pipeline.md` stage via `pipe_write.py` (new Step 7b), closing the gap where a pipeline read-consumer left the stage stale after prep. Origin: 2026-06-09 executive screen with a target company.
- **Example data** (`examples/`): replaced the original DevOps-consultant demo persona ("Alex Chen", Berlin) with a San Francisco product-to-sales pivot persona ("Priya Anand"); regenerated the sample CV with the current RenderCV pipeline (`examples/output/sample-cv-priya-anand.yaml` + rendered PDF).

### Docs
- Swept `docs/methodology.md` architecture section: corrected the unit-test count (176 → 424), replaced the retired n8n automation description with the launchd job set, and refreshed the skill tree (27 → 33) and tools tree.
- Reconciled `README.md` (skill-count note), `docs/usage.md` (analyze video + prep-interview pipeline-advance), `docs/privacy.md` (gitignore table: `inbox/`, `memory/`), and `CLAUDE.md` (launchd + tool tables).

---

## 2026-06-08: /analyze skill, Daily Stoic pipeline, pipeline-staleness + friction-scan fixes

### Added
- **`/analyze` skill** (`.claude/skills/analyze/SKILL.md`): a user-invocable skill that takes a URL (research paper, GitHub repo, or article) and answers one question, "does this help Nick's systems or his wisdom?" It is a critical teardown, not a summary. Pipeline: cheap triage with a stated bail reason before any full read; a common `ExtractionBlock` contract; an adaptive repo path (README + `gh` metadata first, shallow-clone + Explore-subagent code read only if it clears the relevance bar); and a mandatory non-empty "where it does NOT fit" carve-out so it never just cheerleads. Output to `output/analysis/MMDDYY-<slug>.md` plus a tight chat summary; routing is proposed, never auto-written. The `video` source type is a documented stub (parked transcript-fetcher todo). Built to `docs/superpowers/specs/2026-06-08-analyze-skill-design.md` via brainstorm to writing-plans to a subagent-driven build with spec + quality review; smoke-tested on one real paper, one real repo, and a triage-bail dud. Commits `5f1d8c4`, `80a4340`, merge `44ec711`.
- **Daily Stoic pipeline** (`tools/daily_stoic.py` + a `/standup` step): `--sync` / `--backfill` archive Daily Stoic meditation emails to `data/source-emails/daily-stoic/` (promo and digest subjects filtered out, a data-derived 28-kept / 15-dropped split on the seed corpus); `--mark-prompted` records which meditation `/standup` surfaced. `/standup` now distills a daily Stoic prompt from the latest meditation and logs it to `data/reflections/_stoic-prompts.md`; answering it in your own voice creates a normal dated reflection. Read-only Gmail, reusing the launchd `gmail_fetch` token and sanitizer; `sanitize_body` gained a backward-compatible `max_chars` kwarg. State in `tools/.daily_stoic_state.json` (gitignored). Merge `a5d1a4b`.
- **`/todo withdraw <item>` command**: marks an Active to-do as `Withdrawn <date>` (a withdrawal is not a completion), or corrects a row already mis-marked Completed. Pairs with the metrics fix below. Commit `9eb0b67`.

### Changed
- **`/debrief` now advances the job-pipeline stage** (new Step 9b). A real-interview debrief means the call happened, so it updates the `data/job-pipeline.md` row via `pipe_write.py` instead of leaving it reading "round scheduled." This was the source of phantom "prep needed" items in `/standup`: the debrief closed the prep todos but the pipeline row stayed stale, so standup re-surfaced it. Skipped for drills. Commit `0fbc2b1`.
- **`pipeline_staleness.py` parses the canonical `pipe_write.py` column schema** (Date Updated | Next Action | CV Used | Notes | URL) and now gates staleness flagging to active-pursuit stages by keyword, so backlog and closed rows (To Evaluate, To Apply, Deprioritized, Watch) are still counted in the distribution but never nagged. Commit `0fbc2b1`.

### Fixed
- **Stop-hook transcript scan now catches masked SHELL failures**, not just masked python tracebacks. A zsh `no matches found` (unmatched glob) or a BSD/GNU bad-flag error (`ls: illegal option`) whose non-zero exit was swallowed by a pipe (`| awk`) or an `|| echo` fallback read as exit 0, so it evaded both `PostToolUseFailure` and the python-masked path. New `is_masked_shell_failure` + `masked_shell_error_line` + a line-anchored `MASKED_SHELL_RE` in `tools/scan_transcript_failures.py`; the nature is the error line, not the whole output. Line-anchoring plus `EXCLUDE_SCRIPTS` keep the friction log's own table rows (which start with `|`) from false-positiving. 9 new tests + an end-to-end `scan()` smoke; full suite 401 passed / 3 skipped. Commit `77e51ef`.
- **`todo_daily_metrics.py` excludes Withdrawn rows** from completion-rate, streak, and velocity counts (a withdrawal is not a completion). Commit `0fbc2b1`.

### Docs
- Drift sweep across the user-facing docs: skill count 31 to 33 in README.md, CLAUDE.md, and usage.md (current-truth instances only; the Apr-May 2026 usage-audit line keeps its historical 31). Added `/analyze` and `/discover-companies` to the README Full Skill Catalog and the usage.md Skills Reference (both were shipped but undocumented in the guides). Documented `/todo withdraw`, the `/debrief` pipeline-advance, and the Daily Stoic standup prompt. Added `check_bare_python.py` and `check_changelog_currency.py` to the hook tables in README.md and usage.md (both were wired in `.claude/settings.json` but missing from the docs).

### Learned
- A defensively-written shell one-liner (`| awk`, `|| echo`, `2>/dev/null`) masks its own non-zero exit, so the friction auto-capture, which keys off a failure signal, never sees it. The structural fix mirrors the existing python-masked path: detect the error text by a line-anchored signature regardless of exit code. Three such errors this session were caught only by a manual friction-log append before the fix landed.

---

## 2026-06-08: todo_write positional-placeholder fix

### Fixed
- **`todo_write.py` rejected its own documented `--` placeholder.** `cmd_add` accepts a bare `--`/`-`/`""` as a "use default" sentinel for an omitted priority/due/notes, but the upfront flag guard rejected ANY `--`-prefixed token — so the `/todo` SKILL.md instruction to pass `--` always failed with `Unknown flag(s)`. Narrowed the guard to reject only `--word` kwarg-style flags (`len > 2`), leaving the bare `--`/`-` placeholder valid (resolving the cmd_add-vs-guard contradiction at the source); updated `/todo` SKILL.md to use a bare `-` placeholder; added `tests/scripts/test_todo_write_guard.py` (5 cases). Full suite 364 pass / 3 skip.

---

## 2026-06-05: Friction-log masked-failure auto-capture + heredoc-aware hook linting + changelog-currency hook + Gmail search CLI

### Added
- **`tools/check_changelog_currency.py`** (+ test): a Stop hook that WARNs when commits since the last `docs/CHANGELOG.md` edit have touched system surfaces (`tools/`, `.claude/skills/`, `framework/`, `.claude/settings.json`, `requirements.txt`) without a changelog update. Cursor-deduped to warn at most once per HEAD (not every turn); fail-open, never blocks. This is the structural promotion of the changelog-drift rule (REOPEN gate tripped after a 2nd same-day fire). Wired into the `Stop` hook array alongside `scan_transcript_failures.py`.
- **Masked-failure auto-capture in the friction-log Stop scan**: the scan only inspected `tool_results` with `is_error:true`, so a `python3 -c "..." 2>&1 | head` crash whose non-zero exit was swallowed by the pipe was never logged (the traceback was in the output but the exit code read 0). New `is_masked_python_failure` + `looks_like_python_crash` in `tools/scan_transcript_failures.py` catch `is_error:false` Bash results that ran python in command position and ended with an exception line; new `python_invoked` + non-stdlib import attribution in `tools/friction_surface.py` fix the surface (was junk `inline:os`) and `derive_nature` now strips the `Shell cwd was reset` harness footer. Smoke-validated on real transcripts (caught 5 genuine masked crashes while rejecting a 6-case false-positive class of commands that merely print a traceback as data). 72 friction tests pass. Commit `3f3b853`.
- **`tools/hook_command_lint.py`** (+ test): single shared source of `strip_literals` for command-detection hooks, now including heredoc-body stripping. Quoted-delimiter heredocs (`<<'EOF'`/`<<"EOF"`) stripped fully; unquoted (`<<EOF`) stripped only when the body holds no `$()`/backtick so a genuine `$(python …)` invocation is still caught. Commit `cfcb28e`.
- **Gmail read-only `--search` CLI**: arbitrary Gmail search (defaulting to the Job Search label) via the existing read-only launchd token. Commit `e19865b`.

### Changed
- **`check_bare_python.py` + `check_todo_write_kwargs.py`** now import the shared `strip_literals` and drop their duplicated copies — the duplication was the root cause of this hook family's 4th/5th false-positive fires (a fix landing in one copy, not the other). This closes the parked "2nd heredoc fire" REOPEN gate, which tripped when a `git commit -F - <<'EOF' … python … EOF` message body was wrongly blocked. `HOOK_AUTHORING.md` snippet updated to import the shared module (it had prescribed heredoc stripping in prose while its code did not implement it). Commit `cfcb28e`.

### Fixed
- **Pre-existing test-suite drift swept** (full suite now 359 passed / 3 skipped / 0 failed). Three tests were asserting old behavior against intentionally-evolved code: `test_remember_apply` (decisions now route to `data/decisions.md`, not a `## Decisions` section in notes.md — also fixed the stale routing docstring in `remember_apply.py`), `test_dossier_freshness` (staleness now defaults to pipeline-targeted dossiers; test opts into `--all-stale` for the date-threshold check), and `test_todo_daily_metrics` (`pipeline_snapshot` changed from a flat list to a categorized dict). The 3 linkedin-scanner tests now `pytest.importorskip("termcolor")` so a missing optional sub-tool dep skips cleanly instead of erroring collection.

### Learned
- The auto-capture net had a structural hole (pipe-masked exits) independent of the behavioral log-before-fix rule — surfaced when an inline-fixed `/ss` error went unlogged and the user had to ask twice. Smoke-testing on real transcripts, not unit reasoning, caught the masked-failure detector's false-positive class.
- The three "data drift" test failures were each actually a stale test against an intentional behavior change (return-type/routing/default change) — classified per-case rather than force-greened, confirming the code was correct and the tests lagged.

---

## 2026-06-04: Memory restructure prep (MEMORY.md overflow fix, planned not built)

### Added
- **MEMORY.md restructure plan + family-assignment tooling (prep; build parked to a dedicated session)**: `MEMORY.md` had grown to 235 lines / 88.7KB against the ~100-line / 24.4KB load budget, so the harness was silently truncating it. Diagnosed the root cause (a one-line-per-file index grows monotonically; the dir now holds 282 memory files) and locked the architecture: a curated sub-100-line hot set in `MEMORY.md`, with the 223 feedback rules consolidated into 13 mechanism-family framework docs (the `verification-umbrella.md` pattern, new docs under `framework/rule-families/`) plus thin per-type indexes for project/reference/user. Built three reusable scripts: `tools/memory_index_partition.py` (partition by type + classify each feedback rule's promotion tier from its `Current tier:` line), `tools/memory_family_assign.py` (first-pass keyword assignment to the 13 families), `tools/memory_family_finalize.py` (authoritative manual overrides + invariant check, all 223 files assigned). Cold-pickup handoff at `docs/memory-restructure-plan-2026-06-04.md`, final roster at `docs/memory-restructure-family-rosters.json`, rollback snapshot at `memory/MEMORY.backup-2026-06-04.md`. Commit `c3b9a93`.

### Learned
- The promotion backlog was essentially already clear: of 87 plain-behavioral rules, only about 2 had a genuine un-built tripped gate. The lever to get `MEMORY.md` under 100 lines is demote-already-promoted plus consolidate-families, not a promotion sweep.
- Parked the one genuinely hook-able should-promote (`check_replace_all_safety.py`, blocks substring-corrupting short `replace_all` Edits) as its own standalone to-do, independent of the restructure.

---

## 2026-06-03: /discover-companies skill (Exa Websets discovery layer) + Diataxis docs reorg

### Added
- **`/discover-companies` skill**: a new Exa Websets-backed discovery layer that surfaces net-new target companies and feeds `/scan-companies`. Scores each discovered company against the thesis with a geography hard-gate plus weighted stage / sector / keyword dimensions, lane-a/lane-b keyword presets derived from role-shape + goals, dedup, and slug generation. Built spec to plan to TDD (9 tasks). New `.claude/skills/discover-companies/SKILL.md`, `tools/webset_discover.py` (+ test), `tools/career_scanner/company_scorer.py` (+ test), version-controlled `data/discover-presets.yaml`, and the `exa-py` dependency in `requirements.txt`. Commits `ae895a8`, `d149018`, `8b3d128`, `709019c`, `2d90dd4`, `f94a033`, `4d98ac0`, `8170d96`, `de8efcb`, `a38b612`, `4680c35`, `737028c`.
- **Diataxis docs reorg**: added value / flows / getting-started / FAQ docs and reorganized `docs/` navigation around the Diataxis model. Commit `dd7e0d5`.

### Changed
- **`/scan-jobs` cache**: updated a target company's comp to the $200-320K EM band per recruiter. Commit `ab4c1ef`.

---

## 2026-06-02: bare-python source fix + hook-authoring template, friction-log attribution, vault rename, /act dedup

### Added
- **`tools/HOOK_AUTHORING.md`**: a command-detection hook template (command-position regex, quote-stripping, clean/block tests, mandatory live smoke) so new hooks stop repeating the substring-vs-command-position blind spot.
- **`tools/audit_rule_violations.py`** (+ test): a greppable-rule violation auditor for back-propagating a newly-learned rule across existing artifacts.
- **`tools/check_bare_python.py`** (+ test): a PreToolUse hook blocking bare `python` at command position (not as a substring). Commits `50f261d`, `6dcf8f9`, `ca3c3f1`.

### Changed
- **Bare-python killed at the source**: swept the ~13 skill docs + tool docstrings that *prescribed* bare `python`, instead of only guarding the symptom with the hook. Commits `6dcf8f9`, `ca3c3f1`, `0abd757`.
- **Vault rename**: `00-inbox` to `00-voice-corpus-archive` across the spec and all references. Commit `a71f3f4`.
- **`/act` + dossier-freshness**: dedup already-categorized Gmail and scope dossier-freshness nudges to the active pipeline only. Commit `296bbe3`.
- **Docs archive**: moved completed handoff/audit docs to `docs/archive/`. Commit `14824d1`.

### Fixed
- **Outreach response-rate math + friction-log dedup**: corrected the outreach-pending calculation (was reporting ~93% for a true ~48%) and sharpened friction-log surface-keying so distinct frictions stop merging into a noise bucket. Commits `50f261d`, `e63f030`.
- **`check_todo_write_kwargs.py` quote-blindness**: command-position fix so the kwarg guard stops matching the token inside quoted strings. Commit `e63f030`.

---

## 2026-06-01: Longitudinal synthesis, relationship dossiers, checkout cascade, decisions/accomplishments logs

### Added
- **`/my-world` longitudinal three-axis synthesis**: daily orientation plus a gated synthesis pass over reflections, written to `data/reflections/_longitudinal.md`. New `tools/my_world_synthesis.py` (status + atomic append), gating logic with malformed-frontmatter warnings and marker insertion, and read-wiring into `/standup`, `/weekly-review`, and `/checkout` (T5). `/my-world deep` forces the synthesis pass. `/my-world` and `/reflect` are global commands in `~/.claude/commands/`, not project skills. Commits `ace6d7e`, `5331dd2`, `46a0366`, `2f68148`, `192407c`.
- **Per-person relationship dossiers (E2)**: `tools/person_write.py` plus a `data/people/<slug>.md` dossier format for active relationships. `/networking promote <name>` creates a dossier; the `/networking` view now shows dossier indicators and a promote suggestion for active contacts. Five read-consumers wired. Commit `5a20e9a`.
- **`/checkout` Granola debrief cascade (E3) + silent-failure probe (E6)**: checkout Step 4d surfaces un-debriefed calls from the day (propose-only, never auto-debriefs) and runs a probe at close to catch tracked items left in an unexpected state. Commit `dc3bddd`.
- **Decisions + accomplishments logs (E1/E4)**: append-only chronological logs `data/decisions.md` (strategic search decisions) and `data/accomplishments.md` (process wins), newest first. `/remember` routes `decision` and `accomplishment` captures to them; `/weekly-review` and `/standup` read them. Commit `c6e3422`.

### Changed
- **Research routing, Exa default sweep (E1)**: research subagents now default to Exa MCP (`web_search_exa` / `web_fetch_exa`) as primary retrieval, with web as a scoped cross-check. Commit `c6e3422`.
- **Additive-vs-pruning carve-out (E5)** and **MEMORY.md trim** (243 to 195 lines).
- **`fetch_source_email.py`**: untruncated source-email body fetch. Commit `d83c64e`.

---

## 2026-06-01: Verification framework, friction-capture system, McKinsey craft docs, skill hardenings

### Added
- **`framework/verification-umbrella.md` (M1)**: the Family L composite operating manual covering 20 verification-family rules across 5 mechanism clusters, the iron law (never assert state without this-session evidence), and the family-N tier-promotion procedure. Commit `9096706`.
- **Interview-prep-discipline canon + `/prep-interview` Family V wiring (M3)**: interview-prep methodology codified and wired into the prep skill. Commit `f43b71f`.
- **`framework/voice-pure-dictation.md` (M4)**: Family B + C8 mode triad for voice-pure dictation handling. Commit `b320f62`.
- **Read-state hook (M2)**: `tools/check_edit_after_mutation.py`, a PreToolUse guard on Edit/MultiEdit that warns when a file changed on disk since last read this session or was never read. Also friction-log dedup tooling and Phase-E queue reconciliation. Commit `3c00234`.
- **Friction-log transcript-capture system**: `tools/log_tool_failure.py` (PostToolUseFailure logger) + `tools/scan_transcript_failures.py` (Stop-hook transcript scan) + backfill, closing the gap where standard PostToolUse hooks never fire on tool errors. Commit `547da9b`.
- **McKinsey craft docs**: `framework/problem-solving-mckinsey.md` (7-step method), `framework/slide-craft-mckinsey.md` (visual synthesis), and an MBB case-flow doc. Commit `37c5290`.
- **PDF print-reassembly footer** on prep-doc PDFs, plus a mckinsey-slides pptx-export spec. Commit `d2c3ce9`.

### Changed
- **`/wispr` Step 4.7 topic-match gate**: explicit token-count gate before routing dictation to a topic. Commit `e44d48b`.
- **`/debrief` hardening**: Step 6b Signal Analysis (Tier-1 non-negotiable screen with gap surface) and Step 0.5 raw-Granola precondition hard gate. Commits `925c1c1`, `060e98d`.
- **`/apply` submission gate**: post-generation submission confirmation now gates the pipeline stage flip, so artifacts in `output/` never silently mark an application as Applied. Commit `108085c`.
- **Hook hardenings**: `check_todo_write_kwargs.py` PreToolUse hook blocks kwarg-style invocation of the atomic to-do writer (`718f5e1`); `check_email_via_skill.py` allowlist extended to `/follow-up`, `/cold-outreach`, `/cover-letter` (`f5f15a6`); friction-log chokepoint drops test-output false positives plus voice-anchor BLOCK patterns (`bc9e5fb`).

### Fixed
- **Voice reference**: added 5/21 phrasings from a founder and a 6/1 Exemplar 5 rule. Commit `e5c3a3d`.

---

## 2026-05-28 — networking_followup respects explicit close markers

### Fixed
- **`tools/networking_followup.py`** — when the most-recent interaction entry's `**Follow-up:**` line is `—` or starts with `Closed`/`Resolved`, the contact is now marked closed instead of falling through to stale older entries. Caught a contact still surfacing as overdue 8 days after a 5/20 closeout. Total overdue follow-ups dropped from 17 → 7 (filtered five other contacts in addition to that one). Commit `6f8b1a7`.

---

## 2026-05-21 — Friction-log capture system + email Substance-Provenance Audit + weekly-review integration

### Added
- **`tools/friction_log.py` + `memory/friction-log.md`** — append-only ledger for small recurring errors that burn turns (commit `29019b0`). Auto-invoked from a PostToolUse Bash hook on script errors (commit `eae17cc`) plus a Stop hook scanning transcript JSONL at turn-end (Build B, commit `82306b7`). Occurrence ≥2 → auto-memory; ≥3 → structural patch. First production day captured 61 friction events that would otherwise have rotted.
- **`/draft-email`, `/follow-up`, `/cold-outreach` — Substance-Provenance Audit (Step 6b)** — every substantive sentence in a draft is labeled N (Nick-dictated) / C (Nick-corpus) / I (Claude-inferred from cited research) / G (Claude-generated). G-blocks fire in self-positioning / new-value-add / bridge / story / opener-referencing-recipient-work slots — drafting halts until Nick supplies the spine. Catches voice corruption before it reaches Nick as a fait accompli. Build A from the 5/21 lessons audit. Commit `55dc358`.
- **`/weekly-review` Step 5b** — audits friction-log for unpromoted entries during the weekly retro. Commit `6d40499`.

### Fixed
- **`tools/todo_write.py`** — rejects unknown `--flags` (e.g., `--priority`) at the arg parser instead of silently slot-shifting them into positional arguments. Promotion #3 after the rule fired three times in a session. Commit `744df6b`.

---

## 2026-05-19 — Exa promoted to primary retrieval, web as cross-check

### Changed
- **`/research-industry`, `/research-company`, multi-agent research patterns** — Exa MCP (`mcp__exa__web_search_exa`, `mcp__exa__web_fetch_exa`) is now primary retrieval for the 5 parallel research agents; WebSearch/WebFetch is preserved as a sixth dedicated cross-check agent. Pattern locked after A/B test on a target company's AI-native case research where Exa decisively beat web on primary-source reach (TechFundingNews, BusinessWire, Crunchbase exclusives) and paywall avoidance. Commit `5e8af4d`.
- **`.claude/settings.json`** — allowlisted Exa MCP tools for sub-agents so the parallel-research pattern works without permission prompts. Commit `a0a70da`.

---

## 2026-05-18 — Interview Live-Need Bridge (H4) + living-log voice-purity hook + Substack-triage automation + CEO/founder voice exemplar

### Added
- **Live-Need Bridge (H4) answering strategy** — `framework/answering-strategies/live-need-bridge.md` codifies the muscle of bridging an interviewer's stated live-need to Nick's wedge in a 2-sentence move (a + b + c clauses). Wired into `/prep-interview` Step N as a mandatory drill before any onsite. Commits `c544dba`, `755b8e7`.
- **`/debrief` H4 binary** — Live-Need Bridge is now a binary scored field in every debrief, feeding the cross-call interview knowledge system M layer. Commit `755b8e7`.
- **`tools/check_living_log_purity.py` + PreToolUse hook** — blocks Write/Edit/MultiEdit on `data/garden-log.md`, `data/practice-log.md`, `data/coffee-log.md`, `data/farmers-market-log.md`. The sanctioned writer is `tools/living_log_append.py` only — preserves verbatim voice-purity, no agent paraphrasing. Commit `9ece82c`.
- **`tools/alirohde_nudge.py` + state file `tools/.alirohde_state.json` + launchd schedule** — daily 9:15 AM cheap-check on "Ali Rohde Jobs" Substack inbox; emits review-gated triage prompt when a new edition lands (otherwise no-op). Commit `0657c5a`.
- **`framework/voice-reference.md` Exemplar 5 — CEO/founder post-call thank-you** — canonical exemplar with 5 encoded anti-patterns (never grade their strategy back to them; no AI abstraction-preamble; peer-not-student callback; deferential close ≠ hedgy; subject = plain-warm not clever-hook). Sourced from 5 Nick-directed passes on a founder thank-you 5/18. Commit `545924e`.

---

## 2026-05-14 — Verification hook stack + Granola/voice/Notion tooling + framework docs for personal-vs-job-OS architecture

### Added
- **Verification hook stack** (`tools/check_*.py`) — `check_edit_safety.py`, `check_email_via_skill.py`, `check_draft_voice.py`, and others — PreToolUse hooks that block bad writes structurally instead of relying on behavioral discipline. Commit `6503a59`.
- **Granola REST integration** — `tools/granola_cli.py` + `tools/granola_fetch.py` + `tools/granola_save.py` + launchd `granola-auto-debrief` plist (every 3 hrs). Auto-persists transcripts AND posts `<!-- voice: cloud-generated -->` debrief snippets to `data/inbox.md`. Therapy-classified calls skipped. Commit `2c365a4`.
- **`/research-company` Agent 6 (Exa neural search)** — sixth research agent dedicated to Exa neural search, separate from the five Haiku agents (early pattern that became the 5/19 promotion). Commit `51cc569`.
- **`/wispr` skill (project-local override)** — Wispr Flow voice-dictation ingestion with the four-step date+framing anchor check (Step 4.5) for dated reflection files. Commit `7951114`.
- **Framework architecture docs** — `framework/personal-vs-job-os-architecture.md` (the personal vault relocation decision from 2026-05-04), `framework/two-tier-capture.md` (voice-pure tier + synthesis tier), `framework/overnight-queue-design.md` (sealed-aware background processing). Commit `7951114`.

### Changed
- **Skill rewires** — multiple skills rewired around the verification hook stack and the two-tier capture pattern. Commit `7951114`.

---

## 2026-05-12 — CV generation migrated to RenderCV pipeline

### Changed
- **`tools/projects_to_yaml.py` + `tools/cv_merge_theme.py` + `framework/cv-themes/tuck-mbb.yaml`** — CV generation migrated from `xhtml2pdf` (lossy, brittle on Unicode) to **RenderCV** (LaTeX-based, professional typography, theme system). `data/projects/*.md` is now the source-of-truth for experience entries — `projects_to_yaml.py` converts to RenderCV YAML stubs, `cv_merge_theme.py` composes content + theme into render-ready standalone YAML, `~/.local/bin/rendercv render` produces the PDF. Tuck/MBB theme is the project default. Reference YAML: `output/example-ventures/042826-cos-example.yaml`. Commit `09d3602`.

### Fixed
- **GMAIL-AUTH-FAILURE alert** — cleared stale auth alert + documented the RenderCV pipeline in CLAUDE.md. Commit `93d480b`.

---

## 2026-05-07 — Interview Knowledge System (M layer) + voice-reference reconciliation

### Added
- **Interview Knowledge System (M layer)** — `/debrief` and `/prep-interview` rewired around a cross-call knowledge layer: themes, hypotheses, anti-patterns, and signals propagate from one debrief into the prep doc for the next call. Spec at `docs/interview-knowledge-system-spec.md`. Plan at `docs/interview-knowledge-system-plan.md`. Commits `99224c0`, `738bf2e`, `9dd0e3c`.

### Changed
- **`framework/voice-reference.md`** — reconciled legacy `framework/style-guidelines.md` "Nick's Voice" rules into voice-reference.md Section 3. KEEP rules became canonical (corpus-validated). DROP rules were factually contradicted by the corpus and removed (e.g., "close with Thanks not Best" was wrong — corpus is 59% `Best,\nNick`). Commit `d64892e`.

---

## 2026-05-04 — `/personal-todo` skill

### Added
- **`/personal-todo` skill + `tools/personal_todo_write.py`** — lightweight todo list for personal life (admin, household, family, finances, errands) scoped outside the job search. Sibling to `/todo` (job-search-scoped). Atomic write script. Commit `728d08f`.

---

## 2026-04-09 — Phase 03: Pipeline Dashboard (TUI) — `/dashboard` skill

### Added
- **`/dashboard` skill + Textual-based pipeline TUI** — stage-grouped tables with staleness flags, conversion funnel visualization, search/filter, sort toggles, keyboard navigation. Launches via `/dashboard` for a visual cross-section of the job pipeline outside the markdown surface. Commits `bc8c804`, `4f741c6`, `c2fc81d`.

### Fixed
- **Async handler races** — awaited `_rebuild_tables` in `on_mount` and action handlers; capped funnel rates at 100%. Commits `a792c3b`, `2413a9a`.

---

## 2026-04-09 — Phase 02: Browser-automation job discovery — `/scan-companies` skill

### Added
- **`/scan-companies` skill** — orchestrator that scans 45+ company career portals + job boards, parses postings via three ATS API parsers (Greenhouse, Lever, Ashby) + Playwright generic fallback, scores roles against profile/goals on four dimensions, dedups against pipeline. Daily launchd schedule `career-scan` writes new matches to `inbox/`. Commits `88775c3`, `13b284f`, `dddb0b0`, `30fbfd3`, `1ce8415`, `b50190d`, `07d34ed`.

---

## 2026-04-08 — Phase 01: Granola MCP integration + auto-debrief orchestrator

### Added
- **`/debrief` Granola MCP integration** — `/debrief` can now fetch transcripts directly from Granola via MCP, append intel to `data/company-notes/<slug>.md`, and run enhanced filler-tracking. Commit `3ce94ca`.
- **Auto-debrief orchestrator** — `tools/granola_fetch.py` (REST API client) + `tools/call_analyzer.py` (transcript analysis engine) + macOS n8n launcher script. Polls Granola every N minutes, auto-debriefs new transcripts, routes by meeting type. Commits `bf43a50`, `d3eafcd`, `78cf0a6`.

### Changed
- **`tools/granola_save.py`** — handles Granola's `Me:/Them:` transcript format correctly. Commit `ae441f5`.

---

## 2026-03-12 — Documentation cleanup and navigation index

### Added
- **`docs/README.md`** — navigation index mapping all documentation by audience (Claude, new users, design) and framework files by domain (application, interview, outreach, coaching, templates). The missing "which file do I read for X?" entry point.
- **`Last updated` headers** on 11 docs/framework files — enables the staleness-check logic in the global CLAUDE.md snippet (14-day threshold).

### Changed
- **`CLAUDE.md`** — trimmed Purpose section from 10-line lifecycle list to one-line pointer to README.md (saves ~200 tokens/session of duplicated content). Updated structure tree: added `coaching/` subdirectories (coached-answers, pressure-points, anti-pattern-tracker, progress-recruiter), changed `docs/` comment to point to new index.
- **`docs/privacy.md`** — added cross-reference to CLAUDE.md Profile Guard section under "What stays local."
- **`framework/outreach-guide.md`** — added precedence note: Nick-specific voice in `style-guidelines.md` overrides generic guidance here.
- **`docs/global-claude-md-snippet.md`** — added scope note: these are project-agnostic patterns; project-specific overrides live in `style-guidelines.md` and `CLAUDE.md`.

### Archived
- **`docs/phase2-handoff.md`** → `docs/archive/` — Phase 2 complete (commit `9791808`). No skills reference it.
- **`docs/research-skills-upgrade-summary.md`** → `docs/archive/` — Proposal doc; adopted parts already in CLAUDE.md research standards. Unadopted parts would contradict current standards.

---

## 2026-03-04 — Opportunity evaluation generators

### Added
- A private side-business analysis script — financial model generator for an acquisition thesis with computed economics (not hardcoded); COGS reconciled to 10–18% EBITDA industry benchmarks.
- A private side-business analysis script — investor pitch deck generator for the acquisition thesis.
- A private financial-model generator script (original pattern; gitignored).
- A private investor-pitch-deck generator script (gitignored).
- **`framework/opportunity-evaluation-playbook.md`** — end-to-end process for evaluating acquisition/investment/business opportunities, from initial research through financial model and pitch deck. Gitignored (private financial analysis).

---

## 2026-03-10 — Fix date logic bugs in standup preprocessing scripts

### Fixed
- **`networking_followup.py` — column mismatch bug:** Script read `cols[5]` as "Follow-Up Action" but the real Contacts table has 7 columns (`Name | Company | Role | Relationship | Added | Last Interaction | Email`) where `cols[5]` is Last Interaction (a date). This caused every contact's last interaction date to be parsed as a follow-up note — `infer_followup_date()` found a YYYY-MM-DD pattern and treated it as the due date, making contacts appear "overdue" the day after any interaction. **Symptom:** a contact showed "1 day overdue" on 2026-03-10 despite being emailed 2026-03-09 with a follow-up target of ~2026-03-16.
- **`networking_followup.py` — wrong data source:** Follow-up information lives in the Interaction Log section (`**Follow-up:**` lines under `### Name — Company`), not in the Contacts table. The script now reads follow-ups from the Interaction Log and uses the entry's `#### YYYY-MM-DD` date for relative inference (e.g., "next week" = entry_date + 7d). Contacts with no interaction log, `—` follow-ups, or "None required" follow-ups are skipped.
- **`outreach_pending.py` — no cross-reference for replies:** Script only read `outreach-log.md`. When a reply was logged via `networking_write.py log`, the outreach entry still showed "Sent" because the two data files were independent. Now cross-references `networking.md` Interaction Log: if a contact has an interaction dated *after* their outreach, the outreach is treated as "Replied". **Symptom:** a contact still showed "awaiting response" after their reply was logged.

### Added
- **`networking_write.py` — auto-update outreach-log.md on reply detection:** When `log` is called and the summary contains reply-signal keywords (replied, responded, call scheduled, meeting set, etc.), the most recent "Sent" entry for that contact in `outreach-log.md` is updated to "Replied". This is write-path reconciliation complementing the read-path cross-reference in `outreach_pending.py`.
- **4 new tests in `test_networking_followup.py`:** `test_most_recent_followup_used`, `test_none_required_followup_skipped`, `test_marisa_bug_regression`, `test_dash_followup_skipped`
- **3 new tests in `test_outreach_pending.py`:** `test_cross_reference_networking_reply`, `test_cross_reference_no_later_interaction`, `test_cross_reference_no_networking_file`

### Changed
- **`test_networking_followup.py` rewritten:** All fixtures updated from the incorrect 6-column format (`Name | Company | Role | Relationship | Last Interaction | Follow-Up Action`) to the real 7-column format + Interaction Log sections. 6 tests → 9 tests.
- **Test count:** 165 → 176 (11 net new tests)

### Root Cause
Two scripts (`networking_followup.py` and `outreach_pending.py`) and the data model (`networking.md` Contacts table vs Interaction Log) drifted out of sync. The Contacts table schema changed when `networking_write.py` was built (Added + Email columns replaced Follow-Up Action), but the read scripts were never updated. The outreach-log.md ↔ networking.md data silo existed from inception — both were designed as independent stores with no cross-referencing.

---

## 2026-03-09 — Context trimming + memory hygiene

### Changed
- **`CLAUDE.md`** — trimmed from 396 → 196 lines (51% reduction, ~5,350 tokens saved per conversation):
  - Repository structure tree collapsed from 112 lines to 15 (top-level with brief descriptions)
  - "Working With This Repo" section (50 lines of user-facing docs) removed — `docs/usage.md` covers this
  - Answering Strategies expanded list collapsed to one sentence
  - Output file examples reduced from 7 to 3
  - Tools section condensed — kept gotchas and atomic write scripts, dropped per-script descriptions
- **`memory/MEMORY.md`** — trimmed from 157 → 66 lines (57% reduction, ~1,375 tokens saved per conversation). Archived resolved sections to `memory/archive-2026-03.md`.
- **`README.md`** — fixed "23 skills" → "27 skills" in architecture description; updated `tools/` description from "PDF conversion utilities" to "Python scripts: PDF, preprocessing, atomic writes, n8n automation"
- **`docs/methodology.md`** — added 5 missing preprocessing scripts to tools tree (`todo_daily_metrics.py`, `pipeline_staleness.py`, `outreach_pending.py`, `networking_followup.py`, `dossier_freshness.py`); added `memory/` directory to architecture tree; updated test count 137 → 165

### Added
- **`CLAUDE.md` "Memory Hygiene" section** — rules for when to archive vs keep entries in MEMORY.md (archive when: codebase is source of truth, leads resolved, features stable >2 weeks, reminders past date)
- **`memory/archive-2026-03.md`** — archived resolved sections: completed skill additions, output hierarchy migration, skills audit fixes, CV quality check sync, skill updates, a target company's research briefs, stale outreach notes, resolved search leads

### Removed (from MEMORY.md)
- n8n API key (security concern — should not be stored in auto-loaded memory)
- Stale "NEW" labels on established skills
- Completed migration notes (output hierarchy, skills audit fixes)
- Resolved lead context (two companies in the pipeline)

### Notes
- Combined token savings: ~6,725 tokens per conversation (~$0.10-0.20 saved per session at Opus rates)
- All information removed from CLAUDE.md either exists in `docs/usage.md` (user guide), is discoverable via Glob (file structure), or was redundant with section content that follows the tree
- Methodology test count updated from 137 (Phase 2 baseline) to 165 (current)

---

## 2026-03-04 — Extract application workflow framework

### Added
- **`framework/application-workflow.md`** — single source of truth for shared application standards. Contains 6 sections: Candidate Context Loading (with per-output-type table), Company Dossier Staleness Check, Tailoring Rules (incl. Keyword Pragmatism), CV Quality Standards (5 subsections), 16-point CV Quality Checks, and Cheat Sheet Structure (contents, quality rules, markdown template).

### Changed
- **`/generate-cv`** — replaced ~165 lines of inline rules with references to `framework/application-workflow.md`. Deleted Tailoring Rules section, CV Quality Standards section, Cheat Sheet Format template, and the "Future: Application Workflow Framework" TODO.
- **`/apply`** — replaced ~65 lines of inline rules with framework references. **Fixed two bugs:** (1) broken reference to `framework/style-guidelines.md` for Tailoring Rules/Quality Standards (that file doesn't contain those rules), now correctly points to `framework/application-workflow.md`; (2) candidate context loading was missing `data/goals.md` in the numbered list (now uses framework superset).
- **`/cover-letter`** — replaced candidate context loading and dossier staleness check with framework references (~15 lines saved). Cover letter quality gates remain inline (unique to this skill).
- **`CLAUDE.md`** — added `application-workflow.md` to framework/ listing; updated Resume Generation section description.

### Notes
- `/review-cv` and `/review-cv-deep` intentionally NOT modified — they operate from a reviewer perspective and don't share the same generation rules.
- This extraction was triggered by the TODO in generate-cv (line 349): "if the same rule needs to be updated in more than one skill at the same time" — which happened twice in the 2026-03-04 session (quality checks 9-16 sync and cover letter format sync).

---

## 2026-03-04 — Cover letter rewrite + /apply quality sync

### Changed
- **`/cover-letter` skill rewritten** — replaced generic 3-paragraph hook/value/close with research-backed **Problem-Solution format** (4 sections: Hook → Proof → Bridge → Close). Based on meta-analysis of 80+ cover letter studies showing Problem-Solution outperforms traditional formats. Key changes: leads with company's specific challenge, uniqueness test quality gate, resume separation test, ATS keyword weaving (3-5 terms), 250-350 word target.
- **`/apply` Step 7 updated** — cover letter section now uses the same Problem-Solution format as standalone `/cover-letter` skill.
- **`/apply` Step 6b synced with `/generate-cv`** — added quality checks 9-16 that were missing: date math validation, month-level dates for short/recent tenures, causal attribution check, skills evidence check, metric specificity, client engagement disambiguation, role progression in titles, jargon translation.

### Notes
- Cover letter research sources: Interview Guys meta-analysis (80+ studies), HBR 2025, Ask a Manager, Jobscan, Resume Genius (625 managers), MyPerfectResume (1,000+ seekers). Key stat: 94% of hiring managers say cover letters influence interview decisions; 90% of generic letters rejected.
- Both `/cover-letter` and `/apply` now enforce the same Problem-Solution structure and quality gates.

---

## 2026-03-01 — n8n background automation (4 workflows)

### Added
- **4 n8n workflows** built and active at http://localhost:5678 (`n8n start` via `tools/run_n8n.bat`):
  - **Gmail Fetch** (every 15 min) — runs `gmail_fetch.py --label-id Label_7175134973725917628`; replaces Windows Task Scheduler task
  - **Standup Cache Warm** (weekdays 8am) — runs `act_classify.py` + `pipeline_staleness.py` in parallel; writes pre-computed JSON to `tools/.cache/`
  - **Follow-up Nudge + Dossier Freshness** (daily 9am) — runs `n8n_outreach_nudge.py` + `n8n_dossier_nudge.py` in parallel; writes inbox items when overdue follow-ups or stale dossiers are found
  - **Weekly Review Reminder** (Friday 4pm) — runs `n8n_weekly_reminder.py`; writes `inbox/YYYYMMDD-weekly-review-reminder.md`
- **`tools/n8n_outreach_nudge.py`** — delegates to `outreach_pending.py`; writes inbox nudge if `awaiting_response_overdue` is non-empty
- **`tools/n8n_dossier_nudge.py`** — delegates to `dossier_freshness.py`; writes inbox nudge if `staleness_alerts` is non-empty
- **`tools/n8n_weekly_reminder.py`** — writes weekly review reminder to `inbox/`
- **`tools/run_n8n.bat`** — n8n startup script; sets `NODES_EXCLUDE=[]` to re-enable the Execute Command node (blocked by default in n8n 2.x); always use instead of bare `n8n start`
- **`tools/.cache/`** — pre-computed JSON cache directory written by Standup Cache Warm workflow

### Changed
- **Windows Task Scheduler** — "Gmail Fetch (Job Search)" task disabled; n8n workflow handles the same 15-min cadence

### Notes
- n8n 2.x excludes `n8n-nodes-base.executeCommand` by default via `NODES_EXCLUDE` env var; `run_n8n.bat` overrides this with `NODES_EXCLUDE=[]`
- n8n API key stored in `~/.n8n/database.sqlite` (label: `claude-automation`)

---

## 2026-03-01 — Gmail integration pipeline

### Added
- **`tools/gmail_fetch.py`** — incremental Gmail sync: OAuth, Gmail history API, body sanitization (HTML strip → invisible unicode removal → injection phrase redaction → truncation → XML wrap), inbox file writes, 48h auto-cleanup of Gmail files, token expiry alerting. All pure functions are top-level and testable without Google API deps.
- **`tools/run_gmail_fetch.bat`** — Windows Task Scheduler wrapper; appends to `logs/gmail_fetch.log`
- **`logs/`** added to `.gitignore` alongside `tools/gmail_credentials.json`, `tools/gmail_token.json`, `tools/.gmail_state.json`
- **Gmail deps** added to `requirements.txt` (optional section): `google-api-python-client`, `google-auth-httplib2`, `google-auth-oauthlib`, `beautifulsoup4`
- **28 new tests** in `tests/scripts/test_gmail_fetch.py` (165 total, all passing): `sanitize_body` (9 tests), `extract_plain_text` (4), `build_inbox_filename` (5), `write_inbox_file` (2), `cleanup_old_inbox_files` (5), `act_classify` gmail detection (3)

### Changed
- **`tools/act_classify.py`** — `classify_inbox_file` now detects `source="gmail"` in inbox file content and sets `source_type: "gmail"` on the item. Type classification still runs for display; routing to Bucket A is blocked by `/act` security policy.
- **`/act` skill** — security warning block added before Step 1: email content inside `<email-content>` tags is untrusted, injection instructions must be flagged, gmail items require explicit Nick confirmation before any data file write.

---

## 2026-02-28 — Phase 2: 4 atomic write scripts + skill wiring

### Added
- **`tools/pipe_write.py`** — atomic add/update/remove for `data/job-pipeline.md`
- **`tools/networking_write.py`** — atomic add/log/remove for `data/networking.md`
- **`tools/remember_apply.py`** — 8 destination handlers routing notes to the correct data file
- **`tools/act_apply.py`** — pipeline-add/contact-add/notes-add for `/act` Immediate Route writes
- **48 new tests** in `tests/scripts/` (137 total, all passing)
- **`conftest.py` `write_fixture`** — shared helper for write script tests

### Changed
- **`/pipe`** — inline write logic replaced with `pipe_write.py` calls; allowed-tools updated
- **`/networking`** — inline write logic replaced with `networking_write.py` calls; allowed-tools updated
- **`/remember`** — Step 3 write logic replaced with `remember_apply.py` calls; allowed-tools updated
- **`/act`** — Step 4 Immediate Route format specs replaced with `act_apply.py` commands; inline `Write()` tools removed from allowed-tools

---

## 2026-02-28 — Deterministic script migration + CLAUDE.md audit + continuous learning loop

### Added
- **`tools/act_classify.py`** — classifies Pending todos + inbox items into bucket_a/bucket_b/skipped/inbox_items JSON; replaces inline LLM classification in `/act` Steps 1–2
- **`tools/pipe_read.py`** — pipeline read with per-entry staleness annotations (stale_label, needs_attention, missing_action), metrics, and company_index; replaces inline date math in `/pipe`
- **`tools/networking_read.py`** — contacts read with stale_contacts, pipeline_connections, interaction counts, and metrics; replaces inline stale detection in `/networking`
- **`tools/remember_classify.py`** — 8-priority rule engine: classifies note text into typed destinations[] with entity matching against networking/pipeline/dossier slugs; replaces classification table in `/remember`
- **24 new tests** in `tests/scripts/` covering all 4 new scripts (89 total, all passing)

### Changed
- **`/act`** — Steps 1–2 (75 lines of inline classification tables) replaced with `act_classify.py` call + JSON parse
- **`/pipe`** — Show command inline staleness math replaced with `pipe_read.py` JSON fields
- **`/networking`** — Show command inline stale detection + pipeline scan replaced with `networking_read.py`; `Edit(data/networking.md)` removed from allowed-tools
- **`/remember`** — Step 1 classification table replaced with `remember_classify.py` call
- **`CLAUDE.md`** — skill count corrected (20→27); LEGACY dirs removed from tree (deleted 2026-02-25); `/standup` + `/checkout` added to Ongoing Tracking; `/apply` added to Applications; all 9 preprocessing scripts listed in tools section
- **`memory/lessons.md`** — Section 2 back-populated with 8 Nick's Voice patterns (all Promoted=Yes); closes the email correction loop
- **`docs/self-improving-data-framework.md`** — stale Note Routing and Longitudinal Logging entries updated to reflect `remember_classify.py`, `act_classify.py`, and `/checkout`
- **`docs/methodology.md`** — `/todo daily` replaced with `/checkout` in Daily Operations section

---

## 2026-02-28 — /critique-plan skill, /scan-contacts skill, todo_write.py, PDF + style fixes

### Added
- **`/critique-plan` skill** — six-agent plan critique + hybrid plan synthesis. Inserts a structured review step between Codex plan generation and Claude execution. Five analytical agents (completeness, risk/safety, codebase alignment, simplicity/scope, sequencing) run in parallel against the Codex plan; a sixth independent Claude planner receives only the stated goal (no Codex steps — no anchoring) to generate a clean-room plan. Synthesizes a diff table (Codex vs Claude) and an enhanced hybrid plan with all blockers resolved, gaps filled, and order corrected. Inline output only — no file written. Agents 1/2/3/6 use sonnet; agents 4/5 use haiku.
- **`/scan-contacts` skill** — LinkedIn contact scanner for a target company. Runs `tools/linkedin-scanner/scan.py` to fetch profiles, then ranks each contact on four dimensions: role proximity (hiring decision authority), education overlap, network connectedness, and industry fit. Outputs a ranked table and adds top contacts to `data/networking.md`.
- **`tools/todo_write.py`** — atomic mutation tool for `data/job-todos.md`. Handles `add`, `done`, `clear`, and `sync` without loading the full file into Claude's context. Outputs JSON. The `sync` command fast-paths out immediately if the pipeline Archived section is empty.

### Changed
- **`/todo` skill** — all mutation commands (add, done, clear, sync) now delegate to `tools/todo_write.py` via Bash. Direct file manipulation removed. Pipeline sync step rewritten to call `todo_write.py sync` instead of reading and rewriting the file manually.
- **`tools/md_to_pdf.py`** — major rewrite for 1-page CV output: switched from Helvetica to Calibri (registered via ReportLab TTFont from `C:/Windows/Fonts/`), tightened page margins (8mm/13mm), reduced line-height to 1.1, reduced body font-size to 8.5pt, tightened section spacing throughout.
- **`framework/style-guidelines.md`** — added Nick's CV formatting preferences: no em dashes or en dashes (use hyphens everywhere), comma separators for skills lists (not dots or bullets).
- **`CLAUDE.md`** — added `todo_write.py` to repo structure listing; updated Write-Only Files section to specify that mutations must use `todo_write.py`; added `todo_write.py` usage examples to Tools & Environment section.
- **`.claude/settings.local.json`** — added pre-approved WebFetch domains (luma.com, oceantechhackathon.org, sofarocean.com, propellervc.com, aquatic-labs.com) and pre-approved Bash patterns (`git add:*`, `PYTHONIOENCODING=utf-8 python:*`).

### Tests added
- `tests/scripts/test_linkedin_scanner_parser.py` — unit tests for LinkedIn profile parser
- `tests/scripts/test_linkedin_scanner_scan.py` — integration tests for scan workflow
- `tests/scripts/test_linkedin_scanner_unit.py` — unit tests for scanner core
- `tests/skills/SCAN_CONTACTS_TESTING.md` — manual testing guide for `/scan-contacts`

### Files changed
- `.claude/skills/critique-plan/SKILL.md` — new
- `.claude/skills/scan-contacts/SKILL.md` — new
- `tools/todo_write.py` — new
- `tests/scripts/test_linkedin_scanner_parser.py`, `test_linkedin_scanner_scan.py`, `test_linkedin_scanner_unit.py` — new
- `tests/skills/SCAN_CONTACTS_TESTING.md` — new
- `.claude/skills/todo/SKILL.md` — mutation commands rewired to todo_write.py
- `tools/md_to_pdf.py` — major rewrite (Calibri, tight spacing)
- `framework/style-guidelines.md` — Nick's CV formatting preferences added
- `CLAUDE.md` — todo_write.py docs added; Write-Only Files section updated
- `.claude/settings.local.json` — domain + bash permissions added

---

## 2026-02-26 — Edit tool safety: Write-only enforcement + PostToolUse hook

### Root cause
The Edit tool silently fails (returns success, no change) when `old_string` spans lines >~500 characters in markdown table files. No external linter is involved — this is intrinsic Edit tool behavior. Affected files: `data/job-todos.md` (543 chars max), `data/job-pipeline.md` (524 chars max), all `output/**/*.md` dossiers (up to 1,677 chars).

### Changed
- **7 skill `allowed-tools` fixes** — removed `Edit()` on risky files, keeping only `Write()`:
  - `todo/SKILL.md` — removed `Edit(data/job-todos.md)`
  - `pipe/SKILL.md` — removed `Edit(data/job-pipeline.md)`
  - `apply/SKILL.md` — removed `Edit(data/job-pipeline.md)`
  - `cover-letter/SKILL.md` — removed `Edit(data/job-pipeline.md)`
  - `generate-cv/SKILL.md` — removed `Edit(data/job-pipeline.md)`, `Edit(output/**)`
  - `remember/SKILL.md` — removed `Edit(data/job-pipeline.md)`, `Edit(output/**)`
  - `act/SKILL.md` — removed `Edit(data/job-todos.md)`, `Edit(data/job-pipeline.md)`, `Edit(output/**)`

### Added
- **`tools/check_edit_safety.py`** — PostToolUse hook script; warns when Edit is used on markdown files with rows >500 chars; hard-stops on known write-only files (`job-todos.md`, `job-pipeline.md`)
- **`.claude/settings.json`** — PostToolUse hook registration; triggers `check_edit_safety.py` after every Edit call on `.md` files

### Documented
- **`CLAUDE.md` Write-Only Files section** — lists the two affected data files and all output dossiers; explains the root cause; points to the hook

### Files changed
- `.claude/skills/todo/SKILL.md`, `pipe/SKILL.md`, `apply/SKILL.md`, `cover-letter/SKILL.md`, `generate-cv/SKILL.md`, `remember/SKILL.md`, `act/SKILL.md` — allowed-tools updated
- `tools/check_edit_safety.py` — new
- `.claude/settings.json` — new
- `CLAUDE.md` — Write-Only Files section added

---

## 2026-02-26 — /checkout + /apply skills, preprocessing scripts, token optimization

### Added
- **`/checkout` skill** — end-of-day close-out, bookend to `/standup`. Absorbs `/todo daily` entirely. Runs `todo_daily_metrics.py` to build today's snapshot, writes daily log entry, calculates streak/velocity, surfaces tomorrow's top 3 cross-referenced against the weekly review's Top 5 priorities.
- **`/apply` skill** — one-command apply bundle: fetches JD, runs CV generation logic, runs cover letter logic, and adds/updates the pipeline entry. Eliminates the 3-command apply flow.
- **5 preprocessing scripts** in `tools/` — each accepts `--target-date` and `--repo-root`, outputs JSON to stdout:
  - `todo_daily_metrics.py` — todos, daily log, pipeline snapshot, outreach, research, changelog (~2,300 tokens saved per `/checkout` run)
  - `pipeline_staleness.py` — per-stage staleness thresholds (Researching=7d, Applied=5d, Screening=5d, Interview=7d, Offer=3d)
  - `dossier_freshness.py` — detects dossiers by filename==parent pattern, classifies by freshness
  - `outreach_pending.py` — awaiting/overdue outreach, response rate calculation
  - `networking_followup.py` — infers follow-up due dates from free-text (next week, 3–5 biz days, explicit dates, default 14d)
- **28 pytest tests** in `tests/scripts/` — full coverage of all 5 scripts; `conftest.py` sets `PYTHONIOENCODING=utf-8` for Windows compatibility

### Changed
- **`/standup`** — Step 1 now runs `pipeline_staleness.py`, `outreach_pending.py`, `networking_followup.py` instead of reading 6 files and parsing manually; `allowed-tools` simplified to `Read(*)`
- **`/weekly-review`** — Steps 2/3/5 now use script JSON instead of manual file parsing; Step 1 calls 3 scripts + reads only 2 files; velocity (Step 4) reads from daily log (not raw todos); edge case note updated from `/todo daily` to `/checkout`
- **`/scan-jobs`** — Step 7b added: after scoring, surfaces shortlisted roles (≥80%) not already in pipeline and prompts to add them
- **`/todo`** — `daily` command removed; replaced with one-line redirect to `/checkout`
- **`prep-interview`, `generate-cv`, `cold-outreach`, `follow-up`** — inline stale dossier warning (>30 days old) with refresh suggestion; never blocks execution

### Files changed
- `.claude/skills/checkout/SKILL.md` — new
- `.claude/skills/apply/SKILL.md` — new
- `tools/todo_daily_metrics.py`, `pipeline_staleness.py`, `dossier_freshness.py`, `outreach_pending.py`, `networking_followup.py` — new
- `tests/scripts/conftest.py` + 5 test files — new
- `tests/skills/TESTING_CHECKLIST.md` — new
- `.claude/skills/standup/SKILL.md` — Step 1 rewritten, Step 2 analysis sections replaced with JSON refs
- `.claude/skills/weekly-review/SKILL.md` — Steps 1/2/3/5 rewritten; allowed-tools updated
- `.claude/skills/scan-jobs/SKILL.md` — Step 7b added
- `.claude/skills/todo/SKILL.md` — daily command removed
- `.claude/skills/prep-interview/SKILL.md`, `generate-cv/SKILL.md`, `cold-outreach/SKILL.md`, `follow-up/SKILL.md` — stale dossier warning added

---

## 2026-02-25 — Response tracking + lessons loop auto-promotion

### Added
- **Response tracking in `/follow-up`** — Step 1b inserted between Step 1 and Step 2 in Named Contact Mode. Before drafting, `/follow-up` now scans `data/outreach-log.md` for Drafted/Sent rows for this contact, asks "Did they reply?", and updates the Status column to `Replied` or `No reply`. The reply status flows into Step 3 to ensure the correct follow-up type is selected (e.g., a Nudge can't be chosen if they already replied).
- **Lessons loop auto-promotion in all outreach skills** — Step 0 added to `/cold-outreach`, `/follow-up`, and `/draft-email`. Before drafting, each skill scans `memory/lessons.md` Section 2 for patterns with Occurrences ≥ 2 AND Promoted = No, then prompts the user to promote them to `framework/style-guidelines.md`. Prevents Nick's Voice rules from stagnating in lessons.md indefinitely.
- **Outreach reply routing in `/remember`** — New classification type: "Outreach reply" detected when a note mentions a person's name alongside reply-indicating words ("replied", "got back to me", "heard back from", etc.). Routes to `data/outreach-log.md` — updates the most recent Drafted/Sent row to `Replied`. Falls back to networking.md if no matching row found. Reply notes that also contain contact info write to both files.

### Files changed
- `.claude/skills/follow-up/SKILL.md` — added Step 1b (reply status check) + Step 3 reply_status routing note
- `.claude/skills/cold-outreach/SKILL.md` — added Step 0 (lessons promotion check)
- `.claude/skills/draft-email/SKILL.md` — added Step 0 (lessons promotion check)
- `.claude/skills/remember/SKILL.md` — added Outreach reply classification + Step 3 handler + two new examples

---

## 2026-02-25 — Self-improvement loop repairs + email tone clarity

### Bugs fixed
- **`memory/lessons.md` didn't exist** — the self-improvement loop defined in `CLAUDE.md` wrote corrections to this file, but it was never created. Any email/outreach edits since the loop was added had nowhere to land. File created with the correct two-section table structure (Section 1: general corrections; Section 2: email/outreach corrections with Occurrences + Promoted tracking).
- **`/draft-email` silently ignored Nick's Voice** — `/cold-outreach` and `/follow-up` both loaded `framework/style-guidelines.md` for Nick's voice patterns, but `/draft-email` Step 3 only loaded `outreach-guide.md`. Thank-you notes, status updates, and intro requests were drafted without the learned phrasing rules. Added `framework/style-guidelines.md` to `/draft-email` context loading.

### Improved
- **Disambiguation between the two tone sources** — both `framework/outreach-guide.md` and `framework/style-guidelines.md` contained tone guidance with no stated relationship. Added scope notes to each:
  - `outreach-guide.md` Tone Matching Protocol: marks it as HOW to calibrate tone from prior messages; directs agents to style-guidelines for WHAT Nick sounds like.
  - `style-guidelines.md` Nick's Voice: marked as the canonical source, precedence over outreach-guide when they conflict, fed by the lessons loop from `memory/lessons.md`.

### Files changed
- `memory/lessons.md` — created
- `.claude/skills/draft-email/SKILL.md` — added `framework/style-guidelines.md` to Step 3
- `framework/style-guidelines.md` — disambiguation header on Nick's Voice section
- `framework/outreach-guide.md` — scope note on Tone Matching Protocol

---

## 2026-02-25 — Nick's Voice guidelines + outreach skill wiring

- Added "Nick's Voice — Outreach & Email" section to `framework/style-guidelines.md` with specific greetings, closings, phrasing patterns, and sentence-level rules derived from actual sent messages
- Wired Nick's Voice into `/cold-outreach` and `/follow-up` skills

---

## 2026-02-25 — Company notes convention

- Added `data/company-notes/<slug>.md` as the standard location for personal company context (recruiter calls, video notes, call prep, observations)
- Wired into all generative skills: `/generate-cv`, `/cover-letter`, `/prep-interview`, `/cold-outreach`
- Added convention to `/remember` and `/act` so new observations are routed there automatically

---

## 2026-02-25 — Self-improvement loop

- Added Self-Improvement Loop section to `CLAUDE.md`: after any correction, open `memory/lessons.md`, add/update a row, promote to `framework/style-guidelines.md` when pattern hits 2+ occurrences

---

## 2026-02-25 — Output hierarchy migration

- Adopted company-first output structure: every named entity gets `output/<slug>/` subfolder
- Dossier file: `output/<slug>/<slug>.md` (no date prefix — canonical, in-place versioned)
- All other artifacts inside the folder use `MMDDYY` date prefix
- Updated `/generate-cv`, `/cover-letter`, `/prep-interview`, `/cold-outreach`, `/follow-up`, `/draft-email`, `/research-industry`
- Removed legacy `data/company-research/` and `data/industry-research/` references from all skills

---

## 2026-02-25 — Skill audit fixes (8 bugs)

- `allowed-tools` glob depth: switched from `*` to `**` for subdirectory writes across all output-writing skills
- `Edit(data/job-todos.md)` removed from 7 skills — linter reverts Edit changes on this file; only `Write` works
- Dossier read path standardized to `output/<slug>/<slug>.md` across all skills
- `/prep-interview` missing `Write(data/job-todos.md)` added
- `/import-cv` Step 5 had wrong command name (`/onboard` → `/import-cv`)

---

## 2026-02-24 — New skills: `/setup-goals`, `/cover-letter`

- `/setup-goals` — identity-aware goals bootstrapper; reads `professional-identity.md`, derives what it can, asks only the missing fields, writes `data/goals.md`
- `/cover-letter` — 3-paragraph cover letter (hook → value bridge → close with ask); saves to `output/<company-slug>/MMDDYY-cover-letter.md`, syncs pipeline
- `framework/templates/goals.md` slimmed: removed Priority Stack, Industries, Non-Negotiables (those live in `professional-identity.md`)

---

## 2026-02-24 — Profile guard

- Added hard prerequisite check before all generative and research skills: both `data/profile.md` and `data/goals.md` must exist and contain real content before proceeding
- Skills affected: `/generate-cv`, `/research-company`, `/research-industry`, `/prep-interview`, `/cold-outreach`, `/follow-up`, `/draft-email`, `/voice-export`, `/extract-identity`, `/review-cv`, `/review-cv-deep`, `/weekly-review`, `/scan-jobs`, `/standup`

---

## 2026-02-24 — Research quality standards

- Added Executive Summary + BLUF-per-section to all research dossiers
- Added evidence quality tiers (A/B/C), confidence tags, contradiction handling, and freshness rules
- Added Evidence Summary Table and contradiction audit as mandatory output sections in `/research-company` and `/research-industry`
- Added refresh behavior: if dossier exists and is fresh, offer "view existing" or "refresh"

---

## 2026-02-23 — `/standup` skill

- Morning briefing: reads goals/pipeline/todos/outreach/networking in parallel, outputs daily brief + one suggested action

---

## 2026-02-23 — Scope expansion

- Expanded `CLAUDE.md` from interview coach to full job search operating system
- Added pipeline tracking, outreach, networking, weekly reviews, and research workflows
- Added `/generate-cv`, `/prep-interview`, `/weekly-review` skills
