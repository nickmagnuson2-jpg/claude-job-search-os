# Build log template

Copy everything inside the fence to `output/<slug>/BUILD-LOG.md` at hour 0, before any build file.
The rules for each field are in `framework/artifact-workflow.md` section 2. Append-only below the
header; every Entry carries its own `duration` and `who`.

`tools/check_build_log_present.py` reads `reader_ask:` (a quoted value, not the placeholder) and
blocks build files in the folder until it is filled. Only Nick writes the alternative line,
`waived: nick YYYY-MM-DD (<reason>)`, for a build that is not for a reader.

```markdown
# Build log: <artifact, one line>

Last updated: YYYY-MM-DD
Variant: V<n> | new   adapted_from: <output/<slug>/BUILD-LOG.md or appendix row | none>

## Phase 0 (required for this variant's tier; blank = no verdict)
filled_by: nick | claude   route: Q0 yes (<source:line>) · Q1 <yes/no> · Q2 <yes/no: why not in text> · Q3 <yes/no, count> · Q4 <adapt/blank>
reader: <name, role, owns; theirs to act on: yes/no>   channel: <reader-named>   address_source: <inbound | verified record | guess: <how delivery is confirmed>>
reader_names: <First Last, First Last; every person a send from this build may go to; omit for one reader>
reader_ask: "<verbatim>" (<transcript file:line | sources/<snapshot> | gmail id, date>)   answers_ask: yes/no (re-answered at ship)
external_stake: <the real thing touched | none (then this is a demo)>
time_box: signal "<quoted>" | need_expires YYYY-MM-DD | research <h> | design <h> | review loops <n> | send_date YYYY-MM-DD
value_item: <one sentence the reader can use even if they never reply>   proof_purpose: <stated separately>
not_a_copy (V1): tells them something they lack and the first screen shows it: yes/no   company-facts grep: <0 hits | lines>
prior_outcomes: <variant: sends N, written replies N, conversations N; source: outreach_status query, date>
prediction: <reply yes/no by YYYY-MM-DD; what would change the method>
lessons_printed: <N [artifact] rows in memory/lessons.md Section 2; 0 = stop, command, date>   rows_that_change_this_build: <AR ids | none>
frame: <output/<slug>/frame.yaml | none>
verdict: build | reshape (<change>) | don't build (<what would have to be true>)   nick_confirmed: YYYY-MM-DD | pending

## Entries
### YYYY-MM-DD HH:MM | Step <n>: <what> | duration <h:mm> | who: nick | claude
- Done: ...   Tools: ...   Output: <file>   Failed or cut: ...

## Ship
reviewed_text: <file, Entry id>   shipped_text: <file, Entry id>   diff: <0 lines | reviewed again YYYY-MM-DD>
review_dispositions: <N fixed, N signed off: nick YYYY-MM-DD | refusal logged, Entry id>
phone_check: <screenshots>   deploy_verify: <curl results>   analytics: on/off   access: <steps before first useful sentence; password reason | none>
message: <capture id>   finding: "<...>"   question: "<...>"   proof: <named | n/a (warm)>   meeting_ask: <one | none>   cost_of_yes: <minutes | data | access>
recipient_equals_reader: yes   defaults_applied: <D ids>
freeze (V2 presented): <YYYY-MM-DD HH:MM>   reps_after: <N, transcript paths>   reopened: <Entry ids | none>   cheat_sheet: <path; opens with governor + say-back line: yes/no>
sent: data/outreach-log.md:<line>

## After send
opened: <N visits by YYYY-MM-DD | unknown>   reply: data/networking.md:<line> | none by YYYY-MM-DD   outcome: data/job-pipeline.md:<line> | none by YYYY-MM-DD
parallel_signals: <...>   n_note: one silence is n=1

## Method notes for reuse
Transferred unchanged: ...   Had to change: ...   Do differently: ...

## Drain
ledger_rows: <added N, incremented <AR ids>>   workflow_edit_todo: <todo fragment | none>   drained: YYYY-MM-DD
```
