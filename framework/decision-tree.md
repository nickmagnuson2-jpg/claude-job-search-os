# Decision tree: what am I trying to do?

Last updated: 2026-10-26

The whole-repo router. `/standup` Step 0 reads it and states the branch for the day's work in one
line. Branch 6 (build) is defined once, in `framework/artifact-workflow.md`; this file only routes
to it.

```
WHAT AM I TRYING TO DO?

1. Open or close the day ................ /standup (morning) · /checkout (evening). Capture anything mid-day with /remember or /wispr.

2. Find companies or roles .............. /scan-companies · /scan-jobs <portal> · /discover-companies. A posting screenshot: /ss.
                                          Pull the JD from the ATS API, never WebFetch. Posting age: read published_at from the same
                                          API response by hand. The scanners store that field; none of the three skills gates on it.

3. Understand a company, industry, person /research-company <name> · /research-industry · /scan-contacts "<company>".
                                          A dossier restates; before any claim leaves the dossier, re-check the primary source.

4. Apply to a posted role ............... /apply "<url>". It covers dossier, the person, the Outreach Brief, your spine, the CV, form answers,
                                          submit confirmation, pipeline. Then ask by hand (no /apply step asks it yet): will the reader
                                          also get something to open? If yes, go to 6.

5. Write to a person .................... First contact: /cold-outreach. Existing thread: /follow-up. Thank-you, intro, status: /draft-email.
                                          Several people at one company: /outreach-batch. The hook stack gates drafts written by tool call;
                                          a draft typed inline in chat bypasses it, so invoke the skill.
                                          Body carries one finding and one question the reader can answer in a sentence. A link is support.
                                          The Brief records value_item, prediction, send_date and artifact: none, so a text send has a record.

6. Build something a reader will open ... Has the reader said, in words you can quote with a source line, what they need? A job posting,
                                          press quote or company page is the company's words, not a stated need. If no: branch 5, not a build.
                                          If yes: answer Q0-Q4 of the artifact workflow (framework/artifact-workflow.md section 1) and open
                                          output/<slug>/BUILD-LOG.md (template: framework/templates/build-log.md) before any build file.
                                          Similar to a past build? Copy its Method notes for reuse; re-fetch every source;
                                          write adapted_from in the log. Neither? Set the variant line to new and record every step.

7. Interview ............................ Booked: /prep-interview <company> "<round, date>". Confirm the round format first.
                                          Just happened: transcript in data/voice-corpus/granola/, then /debrief <transcript path>; the
                                          stage (networking for a mutual call, drill for practice) is set at its Step 1. Practice: /voice-export,
                                          then /debrief. Loop result (offer or no): no skill; the precedent is the newest coaching/progress/*-outcome.md.

8. Idea practice or product test ........ The idea-practice repo (a sibling repo, not this workflow). Runs after today's outreach is sent,
                                          never instead of it.

9. Fix or verify the system ............. Second opinion or finished document: /verify. Before any push: /audit-pii.
                                          A correction from the user: self-improvement loop Step 1 (CLAUDE.md). Cross-session decision: data/workstreams/.

10. Personal ............................ /personal-todo · /reflect. A family or friend document uses the build core and stays out of the
                                          job-search proof count.
```
