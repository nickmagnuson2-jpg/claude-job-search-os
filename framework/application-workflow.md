Last updated: 2026-06-11

# Application Workflow Framework

> Single source of truth for `/generate-cv`, `/apply`, and `/cover-letter`. Each skill references sections here instead of duplicating rules. Skill-specific workflow steps, argument parsing, output logic, and edge cases stay inline in the skill files.

## Candidate Context Loading

Read the following files in parallel — skip any that don't exist, never fail. Not every output type needs every file; the table below shows which are required per skill.

| # | File | CV | Cover Letter | Notes |
|---|------|----|-------------|-------|
| 1 | `data/profile.md` | ✅ | ✅ | Name, contact, compensation, availability |
| 2 | `data/professional-identity.md` | ✅ | ✅ | Strengths, narrative patterns, reframes, values |
| 3 | `data/goals.md` | ✅ | ✅ | Search thesis, role type preferences |
| 4 | `data/education.md` | ✅ | — | Degrees, qualifications |
| 5 | `data/skills.md` | ✅ | — | Skill inventory with levels |
| 6 | `data/certifications.md` | ✅ | — | Certifications with status |
| 7 | `data/project-index.md` | ✅ | ✅ | Lightweight project index for relevance scanning |
| 8 | `data/companies.md` | ✅ | — | Own companies (if applicable) |
| 9 | `framework/style-guidelines.md` | ✅ | ✅ | Tone conventions, Nick's Voice section |
| 9b | `framework/voice-reference.md` | — | ✅ | **EMPIRICAL voice reference (corpus-validated rules + verbatim exemplars).** MUST read both rules AND exemplars sections — research finding: rules alone underperform; rules + 2-3 exemplars beats both. |
| 10 | `data/company-notes/<company-slug>.md` | ✅ | ✅ | Personal notes, call context, observations |
| 11 | `data/networking.md` | — | ✅ | Check for contacts at this company (informs hook) |
| 12 | `coaching/coached-answers.md` | cheat sheet | — | Cross-reference for cheat sheet |
| 13 | `coaching/anti-pattern-tracker.md` | cheat sheet | — | "Do NOT say" warnings for cheat sheet |
| 14 | `framework/answering-strategies/anti-patterns.md` | cheat sheet | — | Pre-call warnings for cheat sheet |

**If `data/profile.md` is missing:** warn "Profile not found — run `/import-cv` first for best results. Proceeding with available data."

**Plugins:** If `data/plugin-activation.md` exists, read it. Glob `plugins/*/plugin.md` and check for any plugin with `scope: cv` or `scope: all`. If found, read those plugin files and apply any instructions they contain for CV generation.

## Company Dossier Staleness Check

After determining the company slug, read the company dossier at `output/<company-slug>/<company-slug>.md`. If found, grep for `Last updated:` in the first 10 lines:

- If the dossier is **more than 30 days old** (or no `Last updated:` line exists), display this inline warning — then continue, **never block**:
  > ⚠️ Company dossier is [N] days old (last updated YYYY-MM-DD). Consider refreshing: `/research-company "[Company]"`

## CV Output Pipeline (RenderCV)

CVs are generated as **YAML, not markdown**, and rendered to PDF by RenderCV (Typst backend). The legacy `tools/md_to_pdf.py` + xhtml2pdf flow was retired 2026-05-12. Pipeline:

1. **Source experience entries.** `/generate-cv` and `/apply` call `tools/projects_to_yaml.py --include <slug1>,<slug2>,...` to generate factual EXPERIENCE stubs from `data/projects/*.md`. The stub becomes the source-of-truth baseline for company, position, date, location, highlights. Per lesson #54, every quantified claim on the final CV must trace to this stub or directly to `data/projects/<slug>.md`. Tailored phrasing is fine; invented numbers are not.

2. **Compose content YAML.** Claude tailors the stubs for the JD, writes summary/skills/education sections, emits `output/<slug>/MMDDYY-magnuson.content.yaml`. MUST NOT contain a `design:` block.

3. **Merge theme.** `tools/cv_merge_theme.py` composes the content YAML with `framework/cv-themes/tuck-mbb.yaml` (the single source of design truth — Times New Roman, ALL CAPS small-caps section titles, COMPANY-over-italic-POSITION experience template, hairline rules, year-only dates) into `output/<slug>/MMDDYY-magnuson.yaml`. This file is design+content baked together — reproducible standalone months later even if the shared theme changes.

4. **Render.** `rendercv render` produces:
   - `output/<slug>/MMDDYY-magnuson.pdf` — the artifact sent
   - `output/<slug>/MMDDYY-magnuson.md` — rendercv-emitted markdown, used by `/review-cv` and `/review-cv-deep`

**Reference visual:** `output/example-ventures/042826-cos-example.pdf` (a sent CV, validated Tuck/MBB aesthetic).

**Filenames** standardise on `MMDDYY-magnuson.*` for all CV artifacts (yaml, content.yaml, pdf, md, DEEP-REVIEW.md). Cheat sheets keep their role-slug since they are role-specific (`MMDDYY-[role-slug]-cheatsheet.md`). Collision on same-day same-company multi-app → `-v2`, `-v3` suffix on the four CV files.

**To re-render after edits** (no skill needed):
```bash
rendercv render output/<slug>/MMDDYY-magnuson.yaml \
  --pdf-path MMDDYY-magnuson.pdf \
  --markdown-path MMDDYY-magnuson.md \
  --output-folder output/<slug>/rendercv_output \
  --dont-generate-html --dont-generate-png
rm -rf output/<slug>/rendercv_output/
```

### Render & verify (do this before presenting any CV)

Do not reason about layout from the markdown alone — rendercv quirks are only visible in the actual render.

1. **Render a PNG and `Read` it.** Drop `--dont-generate-png`, then open the PNG (`rendercv_output/*_1.png`) with the Read tool to visually confirm italics, line breaks, spacing, and page fill. This catches broken emphasis, stray asterisks, and awkward wraps that the markdown hides.
2. **Count pages.** `PYTHONIOENCODING=utf-8 python3 -c "from pypdf import PdfReader; print(len(PdfReader('output/<slug>/MMDDYY-magnuson.pdf').pages))"`. For Nick this must be 1; trim per Length & One-Page Verification if over.
3. **Clean up** `rm -rf rendercv_output/` once verified.

**RenderCV layout gotchas (learned 2026-06-11):**
- **Bare year renders as "Jan YYYY".** A quoted `date: '2024'` renders with a spurious month and reads as a one-month/open-ended tenure. Use an unquoted integer `date: 2024` for a clean year, or a literal range string `date: 2022 – 2024`.
- **Two stacked role titles, both italic.** rendercv wraps `position` in `*...*` emphasis; a bare `\n` inside breaks the emphasis and leaves literal asterisks on the page. To stack two titles on separate lines with *both* italic, close and reopen the emphasis around the break: `position: "Title A (dates)*\n*Title B (dates)"`.

See [[reference_rendercv_layout_tricks]].

## Tailoring Rules

- Never fabricate experience or inflate skill levels.
- **Project selection:** Choose 3-6 projects by relevance to the target role. If one project is marked type: `flagship`, it may deserve inclusion for depth — but it competes on relevance like any other project. If no single standout exists, choose 2-3 of equal weight that best demonstrate depth.
- **Project framing:** Adapt client descriptions to what's most impressive for the target role (e.g. parent company name for enterprise credibility, or technical characteristics for architecture roles). Full project context is in the project file.
- **Role-type emphasis:** Match what you lead with to what the job posting values most. Scan `data/project-index.md` tags and `data/skills.md` categories to find the candidate's strongest overlap with the role's focus area — then lead with those projects and skills. Don't assume which technologies or domains the candidate is strongest in; derive it from the data.
- For **entrepreneurial / startup roles:** include co-founded companies and side businesses from `data/companies.md`.
- For **consulting / advisory roles:** include relevant early-career experience, professional qualifications, and degree focus areas.
- For **roles where the JD mentions technical background, engineering, CS/EE/ML, or "builds products":** scan side-projects in `data/project-index.md` regardless of role seniority and include the strongest one in a compact Selected Projects section.
- Early-career experience (internships, student jobs, apprenticeships, bootcamps, first roles) is usually omitted unless specifically relevant to the target role.
- **No parroting company marketing language.** The summary may reference the company's mission or stage, but must not copy distinctive phrases from the company's own marketing or the JD verbatim (e.g. "capital-efficient approach", "frontier AI"). Paraphrase in the candidate's language.
- **Token de-duplication.** Any distinctive phrase or scale number should appear at most twice across the CV (ideally once in summary and once in the most relevant bullet). Watch for the same headcount, dollar figure, or descriptor repeating three or more times.
- **One page is the default for Nick** (US market, ~10 years experience, recruiter-forward). Two pages only with an explicit reason. After rendering, verify the page count and trim to one page *before presenting* (see CV Output Pipeline → Render & verify). DACH/international may run 2-4 pages with extensive project history.
- **When building from a prior CV as a baseline, re-verify every title, label, and number against `data/projects/<slug>.md` — not the prior CV.** Baselines silently propagate errors forward (an inherited wrong job title, a redundant summary line, stale phrasing). Treat the prior CV as a hypothesis and the source files as truth. Per [[feedback_zuora_principal_title_is_cpto]] + lesson #54. Origin: 2026-06-11 a recruiter-channel CV inherited "Head of Product and Technology" (wrong; CPTO) and a redundant location line from the prior-role CV baseline.
- Daily rate and availability are only included if explicitly requested.

### Keyword Pragmatism

When the candidate's source data uses accurate but different terminology from a job posting's buzzwords, find honest bridge language rather than fabricating experience:
- Use qualifying adjectives that signal intent/direction without claiming full adoption (e.g. "-oriented", "-driven", "-based")
- Never invent patterns, tools, or practices the candidate hasn't actually used
- If a keyword gap is too wide to bridge honestly, omit it — don't stretch

## CV Quality Standards

### Keyword Discipline

- **Match the job posting's exact terminology.** If the posting says "CRM", the word "CRM" must appear in the CV. Don't rely on synonyms or related terms. ATS systems and speed-scanning recruiters won't make the connection.
- **When a technology has sub-products, always specify which one.** E.g. "Salesforce" could mean Sales Cloud, Service Cloud, or Marketing Cloud; "AWS" could mean any of 200+ services. Always label the specific product.
- **Extract the top 10 keywords from the job posting** before writing. After drafting, verify each appears at least once in the CV. Missing a primary keyword is a critical defect.

### Honest Scoping

- **Only count projects where the candidate worked *inside* the technology**, not just alongside it. Consuming a product's API from the outside is integration experience, not experience with that product. Make this distinction explicit in the CV.
- **Role titles must reflect how the candidate was engaged.** If hired in one role and later absorbed broader duties, frame it as progression (e.g., "Developer, expanding to Architecture & Team Lead"), not as the starting role.
- **Quantifiers must survive scrutiny.** "Across two projects" must mean two projects with genuine depth. "Three continents" must mean three production deployments, not one production + one pilot + one evaluation. When in doubt, use the more conservative framing.
- **Certification status must be current.** Check `data/certifications.md` for renewal status. Never list a certification as active if it is expired or renewal-pending without noting the status.

### Avoid Self-Sabotage

- **Never include weakness admissions.** Phrases like "currently expanding my X experience", "basic knowledge of Y", or "evaluated but not used in production" tell the reader what the candidate *can't* do. If the skill isn't strong enough to state positively, omit it entirely.
- **Explain concurrent engagements (if any overlap exists).** If any selected projects or roles overlap in time, the CV must acknowledge how concurrent work was managed. Without explanation, reviewers assume a timeline error or exaggeration. Add a brief explanation like "[Engagement A] maintained part-time alongside [Engagement B]" where applicable. Skip this check entirely if no timelines overlap.
- **Include team-fit signals.** Always include at least 2-3 references to collaboration across the CV: code reviews, knowledge transfer, team onboarding, training, sprint participation, coordination with client departments. Candidates who appear to only work solo raise red flags for team-based roles.
- **Apply `data/professional-identity.md` narrative reframes.** If the narrative patterns table shows weaker default framings alongside stronger coached versions, use the coached versions.

### Summary Discipline

- **The opener must be a line the candidate can stand behind — true and ownable, not abstract assertion.** Avoid hollow consultant-fluff ("turns ambiguous business problems into shipped solutions", "results-driven leader who delivers impact"). Ground it in who the candidate actually is and what they actually do. The test: could Nick say this sentence out loud, in his own voice, without flinching?
- **Do NOT stuff the summary with metrics to "prove" the opener.** When the candidate says "back it up," that means *make it true and ownable*, not add a dollar figure. Numbers live in the bullets; the summary establishes identity and positioning. Origin: 2026-06-11 a recruiter-channel CV — an abstract opener was over-corrected by jamming in a $10M figure the candidate did not want to lead with. See [[feedback_cv_summary_must_be_standable]] + [[feedback_give_nick_beats_not_a_polished_script]].
- **No location in the summary.** Location lives in the header. A trailing "San Francisco, in-person or hybrid" in the summary is redundant — cut it.
- Keep to ~3 sentences (identity, current role, one differentiator). Watch for a single word ("cross-functional", "operator") repeating across the sentences.

### Skills Section Discipline

- **Sentence case, not Title Case.** Capitalize only proper nouns, tools, and acronyms (Claude API, SQL, Looker, OKRs). Do NOT Title-Case every term ("Structured Problem-Solving, Customer & Stakeholder Management") — it reads as a keyword dump.
- **Lead with the concrete and differentiated** (domain craft, tools, technical/AI skills actually used). Strongest, least-generic items first.
- **Cut generic competency buzzwords even when technically evidenced.** "Cross-functional delivery", "structured problem-solving", "stakeholder management", "requirements gathering" are filler — already demonstrated in the bullets, adding nothing in a skills line. Check #12 (skills evidence) is necessary but not sufficient: a skill can be evidenced and still be fluff.
- Group lightly with a semicolon when there are distinct clusters (e.g. operator-craft; then AI/technical), but only if it does not cost a page line.

### Length & One-Page Verification

- After rendering, render a PNG and count pages (see CV Output Pipeline → Render & verify). For Nick the target is exactly **one page**. If it spills over, trim in this order: tighten the summary, merge or cut the weakest/oldest bullets, shorten the side-project line, drop vague filler — before touching design/margins.
- **Cut vague, source-unbacked filler bullets.** An older early-career role may stay as a **header-only entry** (company, title, dates, location) with NO bullet, rather than carry a generic line like "Delivered data-driven analytics and strategy recommendations to enterprise clients." A filler bullet invites doubt and costs a line. Origin: 2026-06-11 the IBM bullet (source file had only TODO placeholders) was retained until the candidate cut it.

### Structural Consistency

- **Project headers must follow one pattern throughout.** Use either `Role — Description` or `Description — Role` for all project entries. Never mix. No prefixes like "Flagship:".
- **All bullets within a section must follow the same format.** If most bullets have bold labels (e.g., `**Architecture:**`), every bullet in that section must. No exceptions.
- **Every project and engagement must have dates.** No "second engagement" or "later period" without a time range. Even approximate dates (e.g., "Q2 2023") are better than nothing.
- **Include availability and location context** in the header, when the target market convention expects it or if the candidate is based in a different region from the role. Add a line like "Available: [date] · Remote ([timezone]) · Travel to [region] on request".
- **Sentence completeness:** Every bullet point must contain at least one verb. Sentence fragments without verbs are defects.

### Language Precision

- **No native-language calques.** Check `data/profile.md` for the candidate's native language, then watch for false friends and literal translations (e.g. German: "reconception" → redesign; French: "resume" → summary; Spanish: "actually" → currently; Dutch: "eventually" → possibly).
- **British/American English consistency** — match the target market convention or the job posting's language. Don't mix within a single CV.
- **Tense must match engagement status.** Present tense for ongoing engagements, past tense for completed ones.
- **Use standard modern compound forms** (e.g. "subcontractors" not "sub-contractors", "freelancer" not "free-lancer").

## CV Quality Checks (20-point checklist)

Run all 20 checks against the CV. Fix any issues found **in place** — rewrite the CV, don't just flag problems. Checks #1-#18 and #20 run at draft time (pre-render); check #19 runs post-render (see CV Output Pipeline → Render & verify).

**1. Keyword coverage:**
- Take the 10 ATS keywords from the role analysis. For each, verify it appears at least once (case-insensitive, but exact product names must match — "React Native" ≠ "React").
- If a keyword is missing, find a natural place to insert it. If genuinely not addable (candidate lacks the skill), note as a gap.

**2. Product specificity:**
- For every technology or platform named, verify the specific sub-product is labelled (not just the parent brand). E.g. "Salesforce" → which cloud? "AWS" → which services? Fix any that are too generic.

**3. Claim integrity:**
- Scan all quantified claims (years of experience, scale numbers, "across N projects").
- **Open each source project file referenced in the CV and verify each claim line-by-line.** Fix or soften any that don't match source data.
- Check certifications against `data/certifications.md` — fix or note status for any expired ones.

**4. No weakness admissions:**
- Search for hedging qualifiers: "currently expanding", "basic knowledge", "evaluated but not used", "learning", "aspiring", "exposure to", "introductory", "some experience". Remove or rewrite any found with confident, specific language.

**5. Concurrent engagement explanation:**
- If any selected projects overlap in time, confirm the CV explains how concurrent work was managed. Skip entirely if no timelines overlap.

**6. Team-fit signals:**
- Confirm at least 2-3 collaboration references exist (code reviews, onboarding, cross-functional coordination, sprint participation). Add naturally if missing.

**7. Structural consistency:**
- **Header pattern**: all project headers follow one format throughout (`Role — Description` or `Description — Role`). No "Flagship:" prefixes. Fix any that don't conform.
- **Bullet format**: all bullets within each section follow the same format (all with bold labels or all without). Fix inconsistencies.
- **Dates**: every project and engagement has a date range. Add approximate dates (e.g. "Q2 2023") if missing.
- **Reverse-chronological order**: list every adjacent pair of roles with their end dates; if any role's end date is earlier than the next role's end date below it, the order is broken — fix by sorting strictly by end date (most recent first). This is a blocker.
- **Availability**: header includes availability/location/remote context if market convention expects it or if candidate is in a different region from the role.
- **Sentence completeness**: every bullet point contains at least one verb. Fix fragments.

**8. Language and tense:**
- Spelling variant consistent throughout (all British OR all American English — match the job posting's variant).
- Tense: present tense for current/ongoing roles, past tense for completed ones.
- No native-language calques — check `data/profile.md` for candidate's native language, then scan for false friends or unusual phrasing.

**9. Date math validation:**
- Any claim like "X+ years at [Company]" or "X years of [skill]" in the summary must be verified against actual date ranges in the source files. If dates show 2022-2024, that's 2 years, not 3+. Fix or soften claims that fail arithmetic.

**10. Month-level dates for short/recent tenures:**
- Roles under 2 years duration OR within the last 3 years must include month-level dates (e.g., "Jan 2024-Dec 2024"), not just year ranges. Current roles must show month + "Present" (e.g., "Jan 2025-Present"). Year-only dates on short recent roles create ambiguity that hurts recruiter screening.

**11. Causal attribution check:**
- Large-scale outcome metrics (e.g., "400% growth", "$2M revenue improvement") must use appropriately scoped language. At IC/analyst level, use "supported", "contributed to", "informed strategies behind". At manager/lead level, "led initiatives that drove" is acceptable. Never claim sole credit for org-wide outcomes unless the candidate was demonstrably the sole driver. Watch for "enabling", "driving", "delivering" applied to outcomes far above the candidate's scope.

**12. Skills evidence + quality check:**
- Every skill listed in the Skills section must appear substantively in at least one experience bullet. Remove any skill that cannot be evidenced in the experience section. "Substantively" means used as a tool/method in a described activity — not just name-dropped.
- **Beyond evidence, apply Skills Section Discipline:** sentence case (proper nouns/tools only, no Title Case keyword dump); strongest/most-concrete items first; cut generic competency buzzwords even when evidenced ("cross-functional delivery", "structured problem-solving", "stakeholder management" are filler). A skill can pass the evidence test and still be fluff.

**13. Metric specificity:**
- Percentage-based claims must include the underlying metric being measured (e.g., "daily active user engagement by 25%" not just "engagement by 25%"). Include a timeframe or baseline where available from source data. Bare percentages without context are vague and invite skepticism.
- Dollar figures over $1M must specify what the number represents (opex, revenue, ARR, budget, contract value, TCV). Bare "$130M+" without a qualifier is ambiguous and invites the "$130M of what?" probe.

**14. Client engagement disambiguation (consulting firms):**
- At consulting firms or agencies, bullets from different client engagements must be clearly attributed to separate clients. Do not bundle bullets from 3 different clients under one employer header without distinguishing which client each bullet refers to. Use descriptors like "for an ecommerce marketplace" vs "for an online retailer" to disambiguate.

**15. Role progression in titles:**
- When a candidate held multiple titles at one company (e.g., promoted or transitioned roles), show the progression explicitly in the header (e.g., "Digital Growth Manager (2018-2020) / Analyst, Product Analytics (2017-2018)"). Do not collapse multiple roles into the final title only.

**16. Jargon translation:**
- Replace casual or overly informal language with professional equivalents. Examples: "stood up" → "established", "tiger team" → "cross-functional task force". Standard strategy/ops terms like "rhythm-of-business", "operating cadence", and "OKRs" are fine — only translate slang or company-internal shorthand that an outside reader wouldn't recognize.

**17. Employment gap detection:**
- List each role's start and end date in chronological order. Compute the delta between each role's end date and the next role's start date.
- Any gap greater than 3 months that is not covered by an Education entry in that window must be addressed on the CV or cover letter. Add a one-line framing (e.g., "Sabbatical", "Independent consulting", "Career transition") on the CV, or pre-empt in the cover letter.
- Also flag: any role under 6 months with a single bullet. This reads as a layoff signal. Either expand to 2–3 source-backed bullets or add a one-line framing of why it was short.

**18. JD-keyword-to-source contradiction check (anti-hallucination):**
- For every bullet that uses a distinctive JD keyword or phrase (e.g., "pricing and packaging", "financial modeling", "competitive analysis", "M&A"), open the source project file and verify that keyword or a clear synonym actually appears in the source file's description of that engagement.
- If the JD keyword is NOT supported by the source — the generator has lifted language from the JD and attached it to an engagement that was really about something else. This is keyword drift and must be fixed by either (a) rewriting the bullet to match what the source actually says, or (b) moving the claim to a different engagement whose source does substantiate it.
- Also verify every skill in the Skills section against at least one experience bullet that evidences it substantively (not just name-drops it). Remove skills that cannot be evidenced.

**19. One page + layout verification (post-render):**
- Render a PNG and `Read` it (CV Output Pipeline → Render & verify). Confirm: exactly one page for Nick; italics, line breaks, and spacing render cleanly (no stray asterisks, no broken emphasis, no awkward title wraps); page is well-filled but not overflowing.
- If over one page, trim per **Length & One-Page Verification** before presenting. Never hand the candidate a 2-page CV to "edit down" when the trims are mechanical.

**20. Summary + skills discipline:**
- **Summary:** opener is a standable, true line (no hollow assertion); no metrics stuffed in to "prove" it; no location (it's in the header); ≤3 sentences; no key word repeated across sentences. Per **Summary Discipline**.
- **Skills:** sentence case (proper nouns/tools only); strongest/most-concrete first; no generic competency buzzwords even if evidenced. Per **Skills Section Discipline**.

### QC Summary Template

After all fixes are applied, record a QC summary. **For each of the 18 checks, cite the specific CV line(s) or section inspected and the outcome.** A bare "clean" without line citations is not acceptable — it means the check was not actually run.

Template:

- **#1 Keyword coverage:** X/10 matched. [List each keyword and where it appears, or mark as gap.]
- **#2 Product specificity:** [List every technology mention and its specific sub-product, or "n/a — no parent-brand ambiguity".]
- **#3 Claim integrity:** [For each quantified claim, cite the CV line and the source file line that substantiates it. List corrections made.]
- **#7 Structural consistency:** [Reverse-chron order verified — list roles with end dates confirming order. Header pattern, bullets, dates, completeness confirmed.]
- **#8 Language and tense:** [Spelling variant, tense per role, calques checked.]
- **#11 Causal attribution:** [List each large-scale metric (>$1M, >100%, >100-person) and its scoped verb.]
- **#12 Skills evidence:** [List every skill in Skills section and the bullet that evidences it. Flag any removed for lack of evidence.]
- **#13 Metric specificity:** [List every percentage and dollar figure with its qualifier.]
- **#17 Employment gap:** [List end-to-start deltas between adjacent roles. Flag any >3 months not covered by education or pre-empted on CV.]
- **#18 JD-keyword-to-source:** [For each JD keyword appearing in a bullet, cite the source file line that substantiates it. List any rewrites.]
- **#19 One page + layout:** page count (must be 1 for Nick), and the PNG layout confirmation (italics/line-breaks/spacing clean).
- **#20 Summary + skills discipline:** confirm the summary is standable + location-free + ≤3 sentences, and the skills line is sentence-case, strongest-first, filler-free.
- Other checks (#4, #5, #6, #9, #10, #14, #15, #16): confirm each ran and list any fixes.

If a check genuinely has nothing to flag, write "n/a — [one-line reason]", not just "clean".

## Application Answers (portal / recruiter-form questions)

**A distinct artifact from the CV, with its own failure modes.** The CV standards above do not cover it, and in the one case where it was the only thing sent, the CV standards could not have helped. Origin: 2026-08, a client-facing strategy role sourced through a recruiter marketplace. See [[feedback_hard_filter_needs_demonstrating_artifact_confirmed_sent]] and [[feedback_overridden_dissent_needs_owner_and_reread_trigger]].

### Hard-filter demonstration gate (BLOCKING)

Identify the JD's **hard filter**: the one explicit experience bar it states as a requirement (e.g. "N+ years in a client-facing consulting role, working directly with enterprise customers"). Then, before send, name all three:

1. **Which artifact demonstrates it** (a specific answer, or the CV).
2. **Which passage inside that artifact** demonstrates it — quote the line.
3. **Confirmation that artifact is in the outbound package.**

**Any hard filter that cannot name all three BLOCKS the send.** Two specific rejections:

- **Assertion is not demonstration.** A clause claiming the experience does not satisfy the filter; a story showing it does. If the question-clause checklist row reads `ASSERTED, NOT DEMONSTRATED` or `PARTIAL` against the hard filter, that is a block, not a note. In the origin incident that exact row shipped, and the company came back citing that exact gap.
- **"Optional" on the form is not optional to the case.** When the answers do not demonstrate the filter, the CV becomes mandatory regardless of what the form says. In the origin incident the answers left the filter "resting entirely on the resume" by the document's own words, and no resume was sent.

### Slot assignment: sort by filter, not by recency

When choosing which story fills a question, the sort key is **relevance to the hard filter**, not recency and not which story is best rehearsed. A more recent story that structurally cannot evidence the filter is the wrong choice even when it is the better-told story. In the origin incident the third answer was drafted against the consulting engagement (the only story hitting all three legs of the question), then pivoted to a more recent internal story, and the resulting answer could not evidence enterprise-client work because no version of it contains an external client.

**Corollary for the CV.** Reverse-chron buries the filter when the qualifying work is not the top role. If the hard filter lives in job three, the summary must carry it explicitly into the six-second scan.

### Do not volunteer the disqualifying frame

The "Avoid Self-Sabotage" rules above apply to answers, and one addition specific to them. Scope-honesty guardrails written for interview accuracy become disqualifiers when pre-emptively stated in a screening artifact:

- "My lane was strategy; [someone else] owned delivery" reads as *no delivery experience*.
- "This was internal to [employer]" reads as *no client experience*.
- Flagging a target as not-yet-a-result reads as *no result*.

All three are correct and must stay true under a direct question in an interview. **None of them needs to be volunteered in a screening artifact.** Precision about what the candidate did is not the same as narrating what he did not do. Rewriting a negation into a positive assignment ("X owned delivery" instead of "I did not do delivery") does not fix this: it is the same information and a filtering screener reads it the same way.

### Accepted costs get a to-do, not a paragraph

If the answers document records an "accepted cost," "live consequence," "recorded rather than resolved," or a "reversal path if wanted," that residual gets a dated row via `tools/todo_write.py` **at the moment it is written**, naming the owner and the mitigating artifact. A residual filed as prose inside the document that recorded it has no owner and does not resurface. In the origin incident the residual was recorded accurately, in a section with that literal heading, and was still paid in full.

## Cheat Sheet Structure

Generate a pre-interview cheat sheet alongside each CV. Contents:

**1. 15-second recruiter pitch** — tailored to this role. Format: "[Identity hook] with [X years / key credential]. Most relevant experience: [2 projects]. What I'm looking for: [role type] at [company type]. Interested in [Company] because [specific reason]."

**2. Must-have requirements coverage** — for each required skill or qualification from the JD:
- 2-3 specific bullet points from the selected projects that demonstrate it
- Direct quote-ready: "When asked about [requirement], cite [Project] where I [specific action/result]"

**3. Compensation, availability, start date** — pulled from `data/profile.md` (skip if not present)

**4. Coached answers to cross-reference** — read `coaching/coached-answers.md` if it exists. Flag any coached answers that directly apply to likely questions for this role. List: "Existing coached answer for: [topic]."

**5. Do NOT say warnings** — read `coaching/anti-pattern-tracker.md` and `framework/answering-strategies/anti-patterns.md`. Include the most relevant 5-7 warnings for this specific role/context.

**6. Keyword cheat** — list all 10 ATS keywords with a one-line reminder of which project to cite for each.

### Cheat Sheet Quality Rules

- For collaboration/teamwork questions, use the best **peer-work project** as the primary example — not projects where the candidate was sole decision-maker.
- Include rate pushback defense if the rate is above market average for the role.
- Include a short closing with interest statement + max 1-2 questions for the recruiter (save detailed/technical questions for the client interview).
- For each answer where a known anti-pattern could fire, add a bold **"Do NOT say:"** warning with the specific trap to avoid.

### Cheat Sheet Markdown Template

```markdown
# Interview Cheat Sheet — [Role] at [Company]
> Generated [date] from CV: output/<company-slug>/MMDDYY-magnuson.pdf

## 15-Second Pitch
[Tailored pitch for this role]

## Must-Have Requirements — Coverage Map

### [Requirement 1]
- **Project [Name]**: [specific bullet, outcome-focused]
- **Project [Name]**: [specific bullet]
- _Cite when asked:_ "[Trigger phrase]"

### [Requirement 2]
...

## Compensation & Availability
- **Target comp:** [from profile.md]
- **Availability:** [from profile.md]
- **Start date:** [from profile.md]

## Existing Coached Answers (cross-reference coaching/)
- [topic] → see coached-answers.md: "[answer title/section]"
- [topic] → see coached-answers.md: "[answer title/section]"
(omit section if coaching/coached-answers.md not found)

## Do NOT Say — Pre-Call Warnings
1. [Warning from anti-patterns relevant to this role]
2. ...

## ATS Keyword Cheat
| Keyword | Cite This Project |
|---------|------------------|
| [keyword] | [Project name] |
```
