# Research Skills Upgrade Summary

Purpose: provide a single, implementation-ready reference for improving `/research-company` and `/research-industry`, and a clean source document for updating `CLAUDE.md`.

---

## 1) What this upgrade solves

- Improve reliability of research skill execution and refresh behavior.
- Improve evidence quality (traceability, confidence, contradiction handling).
- Reduce output bloat while increasing decision usefulness.
- Standardize ranking and handoff quality across both research skills.

---

## 2) Unified design principle

Both research outputs should use a two-layer format:

- **Layer 1: Decision Brief** (fast scan, 2-3 minutes)
- **Layer 2: Evidence Pack** (audit trail and deeper context)

This keeps reports actionable for quick decisions while preserving rigor.

---

## 3) Canonical dossier format (company research)

Use this section order in `output/<company-slug>/<company-slug>.md`:

1. **Decision Snapshot**
   - One-line thesis
   - Opportunity rating: `High | Medium | Low`
   - Top 3 reasons to target
   - Top 3 risks/red flags
   - Recommended next action

2. **Company Fundamentals**
   - Mission, products, business model, stage, size
   - What changed in the last 6-12 months

3. **Competitive Position**
   - Direct competitors + substitutes
   - Positioning: why this company wins / where it is vulnerable

4. **Financial & Growth Signals**
   - Funding, growth proxies, hiring signals, expansion signals
   - Contradiction notes where sources differ

5. **Leadership & Operating Context**
   - Leadership changes
   - Culture/work model and execution implications

6. **Candidate Fit**
   - Strong overlaps, key gaps, strongest entry angle
   - 2-3 tailored conversation hooks

7. **Action Plan**
   - Next 3 actions (7-day and 30-day windows)
   - Watch triggers for refresh

8. **Evidence Ledger (appendix)**
   - Claim | Source | Source Tier | Date | Confidence
   - Full raw agent output only in optional audit mode

---

## 4) Canonical dossier format (industry research)

Use this section order in `output/<industry-slug>/<industry-slug>.md`:

1. **Industry Snapshot**
   - Attractiveness verdict
   - Secular vs cyclical read
   - Best 3 segments now
   - Biggest structural risk

2. **Industry Structure**
   - Five Forces summary
   - Profit pools (where margins concentrate)

3. **Market Economics**
   - TAM/SAM/SOM (or equivalent practical estimate)
   - Growth drivers and constraints

4. **Segment & Ecosystem Map**
   - Segment taxonomy
   - Leaders, breakout players, emerging players

5. **Talent Market**
   - In-demand roles, skills, compensation norms, hiring hotspots

6. **Regulation, Technology, and Capital Flows**
   - Changes most likely to affect opportunity in 24-36 months

7. **Target Company Prioritization**
   - Ranked list with score breakdown

8. **Candidate Entry Strategy**
   - Positioning narrative
   - Bridge roles
   - 30-day learning and networking plan

9. **Evidence Ledger (appendix)**
   - Claim | Source | Source Tier | Date | Confidence

---

## 5) Shared quality rules for both skills

### Source quality tiers

- **Tier A (primary):** company filings, official website, regulator, earnings materials, first-party press releases.
- **Tier B (reputable secondary):** high-quality business/industry publications and analysts.
- **Tier C (aggregators/crowd):** only with explicit caveat and lower default confidence.

### Confidence language

For high-impact claims (market size, growth rate, funding, hiring signal, risk events), include:

- **Confidence:** `High | Medium | Low`
- **As-of date:** `YYYY-MM-DD`

### Contradiction protocol

If credible sources disagree:

- show both values,
- cite both sources,
- label as `Needs verification`,
- avoid silently averaging or choosing one without explanation.

### Freshness protocol

- Prioritize sources from the last 12 months for trend/news claims.
- If older sources are used, state why they are still relevant.

### Output size control

- Decision Brief should fit roughly one screen before deep detail.
- Avoid mandatory large raw dumps in default mode.

---

## 6) Shared ranking model (for target companies)

Score each target on 1-5 for:

1. Candidate fit
2. Hiring signal strength
3. Geography/remote fit
4. Stage/culture fit
5. Risk-adjusted upside

Then include:

- Total score
- One-line `Why now` trigger
- One key risk

---

## 7) Workflow and integration improvements

- Add deterministic refresh behavior: `view existing` vs `refresh`.
- Add `what changed since last update` section on refresh.
- Add to-do dedupe rules to avoid repeated tasks from reruns.
- Standardize handoffs:
  - `/research-industry` -> top 3 deep dives for `/research-company`
  - `/research-company` -> top outreach actions for `/cold-outreach` and `/networking`

---

## 8) Implementation priorities

### P0 (first)

- Fix tool invocation and failure fallback paths in both research skills.
- Enforce evidence ledger + confidence + source-tier rules.
- Enforce contradiction and freshness handling.

### P1 (second)

- Adopt the two-layer dossier structure.
- Implement shared ranking model and score display.

### P2 (third)

- Improve refresh delta view.
- Add robust task dedupe and handoff polish.

---

## 9) Success criteria

- 100% of high-impact claims include source, date, and confidence.
- Consistent section order across both dossier types.
- Default dossier length reduced materially while preserving decision quality.
- Each run produces clear next actions with minimal duplicate to-dos.

---

## 10) CLAUDE.md update-ready block

Use or adapt this block in `CLAUDE.md` under the research skills section:

```markdown
### Research Dossier Standard (Applies to `/research-company` and `/research-industry`)

All research outputs must follow a two-layer structure:
1) **Decision Brief** (fast-scan summary for action)
2) **Evidence Pack** (traceable support and deep detail)

#### Mandatory Evidence Rules
- For all high-impact claims (market size, growth, funding, hiring signals, major risks):
  - include source URL
  - include as-of date
  - include confidence (`High`, `Medium`, `Low`)
- Use source tiers:
  - Tier A: primary/official
  - Tier B: reputable secondary
  - Tier C: aggregator/crowd (with caveat)
- If two sources conflict, show both and mark `Needs verification`.

#### Output Design Rules
- Decision Brief appears first and is concise.
- Keep deep detail in an appendix/evidence section.
- Do not include full raw subagent dumps by default; include only in explicit audit mode.

#### Ranking Standard
- Ranked target companies must include scored factors:
  1. Candidate fit
  2. Hiring signal
  3. Geography/remote fit
  4. Stage/culture fit
  5. Risk-adjusted upside
- Include total score, one `Why now` trigger, and one key risk for each ranked target.

#### Refresh Standard
- If a dossier exists and is fresh, offer `view existing` or `refresh`.
- On refresh, include a `What changed since last update` section.
```

---

## 11) Files this summary should inform next

- `CLAUDE.md`
- `.claude/skills/research-company/SKILL.md`
- `.claude/skills/research-industry/SKILL.md`
- optional compatibility pass: `.claude/skills/todo/SKILL.md` (for dedupe conventions)

