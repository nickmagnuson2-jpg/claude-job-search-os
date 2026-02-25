# Interview Training Workflow

When asked to practice for an interview or prepare for a role:

1. **Analyse the role** — extract key requirements, technologies, seniority level, industry, and market (same as `/generate-cv` Step 3)
2. **Match from data** — scan `data/project-index.md` and select relevant projects, skills, certifications
3. **Load coaching material** — read `coaching/pressure-points.md` for known pressure points to probe, `coaching/anti-pattern-tracker.md` for current anti-pattern status (which are resolved, which to watch for), `coaching/coached-answers.md` for high-risk question answers, `data/professional-identity.md` for deeper context on strengths, growth edges, underselling patterns, and values, and the answering strategy frameworks in `framework/answering-strategies/` for evaluating whether answers follow the coached patterns
4. **Load plugins** — check `data/plugin-activation.md` for activation config (if the file is missing, all plugins are active). Scan `plugins/*/plugin.md` for enabled plugins whose scope includes `coaching` and whose activation criteria match the current role, industry, and session type. If found, load their questions, anti-patterns, answering strategies, and session behavior alongside core content. Content (questions, anti-patterns, strategies) is additive. If a plugin defines Session Behavior for the interviewer or coach, those become the persona instructions for the session (the session type file's default tone is skipped). If `plugins/` is empty or missing, skip this step.
5. **Choose session type:**
  - **Recruiter screening** → load `framework/recruiter-screening.md`, adopt recruiter persona
  - **Mock interview** → load `framework/mock-interview.md`, adopt hiring manager persona
  - **Full simulation** → load `framework/full-simulation.md`, adopt chosen persona, run complete conversation without coaching breaks
6. **Run the session** — ask questions one at a time, wait for answers, coach after each response.
7. **Reference actual data** — when giving "stronger versions" of answers, pull from the real project files and summary variants, not generic advice
8. **Deliver Takeaway** — when the session wraps up, deliver a 3-4 sentence executive summary to the candidate in chat: what happened, dominant patterns, what went well, and the single most important thing to fix
9. **Log progress** — after the session, create a session file in the relevant progress folder (`coaching/progress-recruiter/` or `coaching/progress-interview/`) and update its `_summary.md` with anti-pattern counts and coaching takeaways
10. **Update anti-pattern tracker** — update `coaching/anti-pattern-tracker.md` with pattern status changes, new patterns, and an Update Log entry
11. **Data enrichment** — check if the session surfaced new information (project details, achievements, technologies, skills) that should be captured in the data files. Follow the procedure in `framework/data-enrichment.md`.

## Coaching Rules

These are the **default** tone rules. If a plugin was loaded in step 4 with Session Behavior, the plugin's interviewer and coach instructions are used instead -- skip the first bullet.

- Be direct — don't sugarcoat weak answers
- Focus on the session type's specific risks (recruiter: surviving the filter; hiring manager: differentiation)
- Always reference the candidate's actual experience when suggesting stronger answers
- After the session, offer to save any new pitch variants or coaching insights back to `data/`
- After the session, always update the progress tracker — this is mandatory, not optional
