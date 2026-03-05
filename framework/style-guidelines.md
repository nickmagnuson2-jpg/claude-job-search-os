# Style Guidelines

## Tone & Voice

Derive the CV's tone from the candidate's data files — don't assume a default persona.

Before writing, read:
- `data/professional-identity.md` — identity statements, strengths, and narrative patterns define how the candidate should sound (e.g. hands-on builder vs. strategic advisor vs. consultative leader)
- `data/profile.md` — location and language context inform spelling variant and market conventions

Then apply these universal principles:
- **Professional but not stiff** — match the energy of the candidate's identity, not a generic corporate template
- **Use the narrative patterns table** — if `professional-identity.md` contains reframes, apply them. The candidate's default framing is usually weaker than the coached version
- **Use concrete numbers** where the source data provides them (scale, duration, team size, impact metrics)
- **Language variant:** Infer from `data/profile.md` location or match the job posting's language. Maintain consistency within a single CV. Match the formality level expected in the target market's language conventions (e.g. German resumes use formal professional German)
- **Never impose a work-style assumption** (e.g. "hands-on", "building and operating", "consulting on") — let the projects and identity data speak for themselves

## Nick's Voice — Outreach & Email

**This is the canonical source for Nick's personal voice in all outreach.** Patterns are extracted from actual sent messages and promoted here from `memory/lessons.md` when a correction pattern reaches 2+ occurrences.

**Precedence:** These patterns override generic guidance in `framework/outreach-guide.md` when they conflict. Nick's Voice is specific and observed; outreach-guide is universal and strategic — they are complementary, not duplicative.

**When adding new rules** (via the self-improvement loop): add to the most specific subsection below. If no subsection fits, create one.

### Greetings & Closings
- Open with **"Hi [Name],"** for warm contacts — not just the name alone, not "Hello"
- Close with **"Thanks!"** — warm, casual, peer-level. Not "Best," not "Sincerely," not just the name

### Phrasing Patterns
- **"Wanted to follow up here"** — not "Wanted to bump this up" (natural, not pushy)
- **"Exploring my next move"** — not "job search" or "pointing my job search" (soft framing)
- **Specific ask, not generic:** "Would Wrecking Ball work?" not just "grab coffee" — concrete venue or format
- Drop filler adverbs: no "genuinely," "truly," "really" — cleaner without them

### Sentence-Level Rules
- Short sentences. No filler. No hedge language.
- No "I believe I could," "I think I might," "hoping to" — if it weakens the line, cut it
- No essay structure — answer first, context second, stop
- No em dashes in outreach — replace with a comma (e.g., "last week, wanted to follow up" not "last week — wanted to follow up"). Scan the full draft before presenting, not just the first occurrence.

### When to Apply
Use these patterns in: `/cold-outreach`, `/follow-up`, `/draft-email`, and any outreach drafted inline during sessions. Read 2–3 entries from `data/networking.md` Interaction Log for additional tone-matching context if available.

---

## CV Formats

> The format to use is determined by the target market from the job posting or `data/profile.md`. Regional formats below cover market-specific conventions. When in doubt, use the International format.

### US Resume Format (default)

For US-based roles and US job boards:
- Professional summary at the top (tailored, concise)
- No photo, no date of birth, no personal details beyond name/contact/LinkedIn
- Experience section with accomplishment-driven bullets (lead with impact metrics)
- Education at the bottom (unless recent graduate)
- Typically 1 page (senior roles may extend to 2)
- No availability or rate in the document - discuss in conversation

### International CV Format 

For international roles, direct applications, or when no regional format matches:
- Professional summary at the top (tailored)
- If the candidate has an anchor project (type: `flagship`), highlight it prominently. Otherwise, lead with the 2-3 most relevant projects for the target role
- Skills grouped thematically, not just listed
- Projects can be reordered by relevance rather than strictly chronological
- Education and certifications can be positioned for maximum impact

#### Nick's CV Formatting Preferences
- **No em dashes or en dashes** - use hyphens (`-`) everywhere. In bullet text: ` - ` (with spaces). In date ranges: `2022-2024` (no spaces).
- **Skills separators:** use commas, not dots/bullets. Example: `SQL, Tableau, Looker, R, Excel` not `SQL · Tableau · Looker`
- **Job header format:** Use H3 with pipe-separated parts — `### Company | Title | Location | Date`. The PDF generator renders this as: **Company bold** + date right-aligned (row 1), *title italic* + location italic right (row 2). 3-part format `### Company | Title | Date` omits the location row.
- **Education header format:** Same H3 pipe format — `### School | Degree | Date`. Renders as: **School bold** + date right (row 1), *degree italic* below (row 2).
- **Do not use the old `### Title — Company` H3 format** — it bypasses the transform and renders as a plain bold line with no right-aligned date.

### UK CV Format

For UK-based roles:
- Personal statement / professional summary at the top
- No photo (unless creative industry)
- Experience section with achievement-oriented bullets
- Education section (include A-levels/GCSEs only for junior candidates)
- Typically 2 pages
- "References available on request" is optional and increasingly omitted

### DACH Freelancer Profil

For the DACH freelance market (platforms like freelance.de, GULP, Hays, etc.):
- Header: Name, title, contact, photo placeholder, availability, daily/hourly rate
- Certificates section near the top
- Skills as a table (skill + years)
- Projects listed reverse-chronologically with: period, role title, client/industry, bullet points, technologies
- Education at the bottom
- No professional summary (or very brief)
- Date and location at the bottom ("[City], [date]")
