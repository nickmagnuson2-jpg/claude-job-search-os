export const meta = {
  name: 'learn-briefing',
  description: 'Produce a deep, validated educational briefing on any topic: scope, parallel research, synthesis, quiz, then an adversarial claim-validation pass that auto-corrects and attaches real source URLs. Driven entirely by args.',
  phases: [
    { title: 'Scope', detail: 'Anti-anchored agent builds coverage checklist + atomic research slices' },
    { title: 'Research', detail: 'One agent per slice on live web (Exa) + optional repo-miner' },
    { title: 'Assemble', detail: 'Fresh-context writer drafts the briefing (URLs required)' },
    { title: 'Quiz', detail: 'Optional comprehension + interview self-test' },
    { title: 'Verify', detail: 'Extract load-bearing claims, adversarially refute each against live web' },
    { title: 'Finalize', detail: 'Auto-correct flagged claims, attach real URLs, write validation report' },
  ],
}

// ---------- config from args (the skill fills these in) ----------
const cfg = args || {}
const TOPIC = cfg.topic || 'UNSPECIFIED TOPIC'
const DEPTH = cfg.depth || 'deep' // 'focused' | 'deep' | 'comprehensive'
const REPOS = Array.isArray(cfg.repos) ? cfg.repos : [] // optional paths/context to mine
const SECTIONS = Object.assign(
  { personal: true, quiz: true, deploymentFraming: true },
  cfg.sections || {}
)
const AUDIENCE = cfg.audience ||
  'A sharp, non-technical reader (ex-strategy-consultant operator) who needs to deeply understand and confidently talk about this topic, not implement it.'

const DEPTH_SPEC = {
  focused: { words: '3,000-4,000', slices: '4 to 5' },
  deep: { words: '6,000-8,000', slices: '6 to 7' },
  comprehensive: { words: '10,000-12,000', slices: '8' },
}[DEPTH] || { words: '6,000-8,000', slices: '6 to 7' }

const GOAL = `
TOPIC: ${TOPIC}

AUDIENCE: ${AUDIENCE}

DELIVERABLE: A single readable learning document, target ${DEPTH_SPEC.words} words. Style: every major section opens with a bold one-sentence BLUF (bottom-line-up-front), then bullets that carry real content (named examples, dates, mechanisms), never vague. Depth over fluff, no buzzword soup, no filler. Non-technical language but intellectually serious. Explain jargon in plain language the first time it appears. NO em dashes anywhere (use commas, periods, or hyphens). This is a hard rule.
${SECTIONS.deploymentFraming ? 'FRAMING: include a "why an operator / deployment strategist should care" lens where natural.\n' : ''}EVIDENCE RULES: prioritize sources from the last ~12 months for anything current. Every load-bearing or surprising claim must carry a real, clickable source URL plus a confidence tag for contested claims. Never fabricate. Flag myth-vs-reality and contested points explicitly rather than repeating folklore as fact.
`

// ---------- Phase 1: Scope (anti-anchored, goal only) ----------
phase('Scope')
const SCOPE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['coverage_checklist', 'pitfalls', 'research_slices'],
  properties: {
    coverage_checklist: { type: 'string', description: 'Markdown checklist of specific sub-topics, named episodes/cases, and distinctions an excellent version MUST contain' },
    pitfalls: { type: 'string', description: 'Common ways such a document goes shallow or wrong; easy-to-confuse distinctions' },
    research_slices: {
      type: 'array', description: `${DEPTH_SPEC.slices} atomic, non-overlapping research assignments`,
      items: {
        type: 'object', additionalProperties: false,
        required: ['key', 'brief'],
        properties: {
          key: { type: 'string', description: 'short kebab-case label' },
          brief: { type: 'string', description: 'A focused research brief: exactly what this agent must dig up, including named cases/dates/contested points to resolve' },
        },
      },
    },
  },
}
const scope = await agent(
  `You are an independent research-design validator. You are given ONLY the goal below, by design, so your coverage map is uncontaminated by anyone's draft plan.

GOAL:
${GOAL}

Produce: (1) a rigorous COVERAGE CHECKLIST of the specific sub-topics, named historical episodes, named cases/organizations, statistics, and conceptual distinctions a genuinely deep version must contain; (2) the PITFALLS that make such a document shallow or wrong, plus the distinctions that are easy to conflate; (3) ${DEPTH_SPEC.slices} ATOMIC, non-overlapping RESEARCH SLICES that together cover the checklist, each with a focused brief naming what to dig up. Return via StructuredOutput.`,
  { label: 'scope:checklist+slices', phase: 'Scope', schema: SCOPE_SCHEMA }
)
const checklist = scope?.coverage_checklist || ''
const slices = (scope?.research_slices || []).slice(0, 8)
log(`Scope: ${slices.length} research slices`)

// ---------- Phase 2: Research (parallel barrier; assembler needs all) ----------
phase('Research')
const EXA_PREAMBLE = `You are a research agent. Use the live web for primary research: first run ToolSearch with query "select:mcp__exa__web_search_exa,mcp__exa__web_fetch_exa" to load Exa, then run several targeted searches (5-12) and FETCH the most authoritative full pages. Prefer primary/official sources and the last ~12 months for current topics.

Return a dense RESEARCH DOSSIER (markdown) for your slice: organized by sub-topic, each claim followed by a REAL source (publication, year, and the actual URL you opened) and a confidence tag [High/Med/Low] for contested or high-impact claims. Concrete named cases, dates, specifics, NOT generalities. Flag any myth-vs-reality or contested point explicitly. Do NOT write final prose; produce raw researched material a writer can quote, and ALWAYS keep the URL with each fact.

Master coverage checklist this work must help satisfy (cover the parts relevant to YOUR slice):
${checklist}
`
const researchThunks = slices.map(s => () => agent(
  `${EXA_PREAMBLE}\n\nYOUR SLICE [${s.key}]:\n${s.brief}`,
  { label: `research:${s.key}`, phase: 'Research' }
))

if (SECTIONS.personal && REPOS.length) {
  researchThunks.push(() => agent(
    `You are a codebase/context analyst. Read the following local repositories or context paths the user named, and map THIS TOPIC ("${TOPIC}") to their actual work. Read real artifacts (README, CLAUDE.md, planning docs, key source/config) and quote them.

PATHS:
${REPOS.map(p => `- ${p}`).join('\n')}

Return a markdown dossier: for each major theme of the topic, give 1-3 CONCRETE examples from the user's own work that illustrate or contrast it, with quoted artifact names/paths. End with 3-4 "talking points" the user could say in an interview ("In my own project I practice X, which is Y, and here is what I learned"). Do not fabricate; cite only what you actually read. If a path is unreadable or absent, say so.`,
    { label: 'research:apply-to-work', phase: 'Research' }
  ))
}
const dossiers = (await parallel(researchThunks)).filter(Boolean)
log(`Research complete: ${dossiers.length} dossiers`)

// ---------- Phase 3: Assemble ----------
phase('Assemble')
const assembled = await agent(
  `You are an expert explanatory writer. Write a single cohesive LEARNING BRIEFING from the dossiers below. Fresh context: rely on the dossiers, not prior assumptions.

${GOAL}

STRUCTURE: open with an Executive Summary (BLUF + a one-paragraph map of the territory). Then one section per major theme of the topic. Include a compact markdown comparison table wherever the topic has competing options/approaches.${SECTIONS.personal && REPOS.length ? ' Include a "How this connects to your work" section built from the apply-to-work dossier, concrete and specific.' : ''} Close with an "implications / where this is going" section${SECTIONS.deploymentFraming ? ' framed for an operator / deployment strategist' : ''}, and a "talk about it like a pro" cheat section of crisp one-liners.

HARD RULES:
- Target ${DEPTH_SPEC.words} words. Depth and substance, not padding.
- Every major section starts with a bold **BLUF:** one-sentence takeaway.
- Bullets must carry real content (named examples, dates, mechanisms).
- Non-technical but serious; explain jargon plainly on first use.
- NO em dashes. Use commas, periods, or hyphens.
- CITATIONS ARE MANDATORY AND MUST BE CLICKABLE: every load-bearing or surprising claim keeps a real markdown-link URL from the dossiers, e.g. ([Source, 2025](https://...)). Do NOT strip URLs down to bare author-year. Carry confidence tags on contested claims. If a dossier gave a fact with no URL, mark it [unverified] rather than dropping the caveat.
- Honor myth-vs-reality flags from the research.
- Do NOT include a quiz.

Return ONLY the final markdown document body, starting with a top-level # title.

=== COVERAGE CHECKLIST TO SATISFY ===
${checklist}

=== RESEARCH DOSSIERS ===
${dossiers.map((d, i) => `\n----- DOSSIER ${i + 1} -----\n${d}`).join('\n')}`,
  { label: 'assemble:briefing', phase: 'Assemble' }
)

// ---------- Phase 4: Quiz (optional) ----------
let quiz = ''
if (SECTIONS.quiz) {
  phase('Quiz')
  quiz = await agent(
    `You are an assessment designer. Read the briefing below and design a quiz confirming a smart non-technical reader genuinely comprehended it.

PART A, COMPREHENSION (8 questions): mix of multiple-choice (4 options) and short-answer, covering the load-bearing distinctions. Then a clearly separated "## Answer Key" with the correct answer and a one-sentence why for each.
PART B, INTERVIEW REHEARSAL (5 questions): questions a sharp interviewer might ask to probe real fluency, each with a strong MODEL ANSWER (4-6 sentences) in a confident, plain-spoken, non-buzzwordy voice.${SECTIONS.personal && REPOS.length ? ' Where natural, model answers reference how the reader\'s own work illustrates the point.' : ''}

NO em dashes. Return ONLY markdown, starting with "# Quiz: Check Your Understanding".

=== THE BRIEFING ===
${assembled}`,
    { label: 'quiz:design', phase: 'Quiz' }
  )
}

// ---------- Phase 5: Verify (extract -> adversarial refute) ----------
phase('Verify')
const CLAIM_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['claims'],
  properties: { claims: { type: 'array', items: {
    type: 'object', additionalProperties: false,
    required: ['id', 'claim', 'section', 'attribution_in_doc'],
    properties: {
      id: { type: 'integer' },
      claim: { type: 'string', description: 'exact self-contained factual assertion' },
      section: { type: 'string' },
      attribution_in_doc: { type: 'string', description: 'the source the doc credits, or "none"' },
    },
  } } },
}
const extracted = await agent(
  `Read this briefing and extract the LOAD-BEARING, INDEPENDENTLY-CHECKABLE factual claims (whose truth a reader's credibility depends on). Prioritize contested "myth vs reality" claims, named episodes with dates, statistics, attributed quotes, and confidence-tagged claims. Skip opinion, framing, generic definitions. Aim for 25 to 35 of the highest-stakes claims, each self-contained. Return via StructuredOutput.\n\n=== BRIEFING ===\n${assembled}`,
  { label: 'verify:extract', phase: 'Verify', schema: CLAIM_SCHEMA }
)
const claims = extracted?.claims || []
const batches = []
for (let i = 0; i < claims.length; i += 3) batches.push(claims.slice(i, i + 3))

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['results'],
  properties: { results: { type: 'array', items: {
    type: 'object', additionalProperties: false,
    required: ['id', 'verdict', 'real_source_url', 'note'],
    properties: {
      id: { type: 'integer' },
      verdict: { type: 'string', enum: ['SUPPORTED', 'PARTIAL', 'UNVERIFIED', 'CONTRADICTED'] },
      real_source_url: { type: 'string', description: 'a real URL you actually opened, or "NONE FOUND"' },
      note: { type: 'string', description: 'what the live source says + any correction' },
    },
  } } },
}
const verdicts = (await parallel(batches.map((batch, bi) => () => agent(
  `You are an adversarial fact-checker. Try to REFUTE each claim, not confirm it. Default to skepticism: if you cannot find a real, credible live source supporting it, the verdict is UNVERIFIED, not SUPPORTED. Use the live web: run ToolSearch with query "select:mcp__exa__web_search_exa,mcp__exa__web_fetch_exa" to load Exa, then search and FETCH real pages. You are deliberately NOT told the document's own confidence rating.

Verdicts: SUPPORTED (a credible source directly confirms), PARTIAL (mostly right but the doc overstates/misdates/misattributes a detail), UNVERIFIED (no credible support found), CONTRADICTED (a credible source says otherwise). Return a REAL url you opened and a one-to-two-sentence note with any correction.

CLAIMS:
${batch.map(c => `[id ${c.id}] ${c.claim}\n  doc credits: ${c.attribution_in_doc}`).join('\n\n')}

Return via StructuredOutput.`,
  { label: `verify:batch-${bi + 1}`, phase: 'Verify', schema: VERDICT_SCHEMA }
))))
  .filter(Boolean).flatMap(r => r.results || [])

const byId = Object.fromEntries(verdicts.map(v => [v.id, v]))
const tally = verdicts.reduce((m, v) => { m[v.verdict] = (m[v.verdict] || 0) + 1; return m }, {})
const merged = claims.map(c => ({ ...c, ...(byId[c.id] || { verdict: 'NO-VERDICT', real_source_url: 'NONE', note: 'verifier did not return' }) }))
log(`Verdicts: ${JSON.stringify(tally)}`)

// ---------- Phase 6: Finalize (auto-correct + report) ----------
phase('Finalize')
const needsFix = merged.filter(m => ['PARTIAL', 'UNVERIFIED', 'CONTRADICTED', 'NO-VERDICT'].includes(m.verdict))
let finalBriefing = assembled
if (needsFix.length) {
  finalBriefing = await agent(
    `Apply these verification corrections to the briefing and return the FULL corrected markdown. Rules:
- CONTRADICTED: fix the claim to match what the live source says (or remove it if unsalvageable), and cite the real URL.
- PARTIAL: correct the overstated/misdated/misattributed detail; cite the real URL.
- UNVERIFIED / NO-VERDICT: do not delete, but append a visible " [unverified]" marker right after the claim so the reader knows it is not independently confirmed.
- For SUPPORTED claims where the doc lacked a clickable URL, attach the verified URL as a markdown link.
- Change NOTHING else. Preserve structure, voice, BLUFs, and all other content. NO em dashes.

CORRECTIONS (apply each to its claim):
${needsFix.map(m => `- [${m.verdict}] claim: "${m.claim}"\n  fix: ${m.note}\n  url: ${m.real_source_url}`).join('\n')}

 also attach URLs for these SUPPORTED-but-maybe-bare claims:
${merged.filter(m => m.verdict === 'SUPPORTED').map(m => `- "${m.claim}" -> ${m.real_source_url}`).join('\n')}

=== BRIEFING TO CORRECT ===
${assembled}

Return ONLY the corrected markdown.`,
    { label: 'finalize:auto-correct', phase: 'Finalize' }
  )
}

const report = await agent(
  `Write a VALIDATION REPORT (markdown) telling the reader honestly how trustworthy the briefing is now. No em dashes. Be straight, not reassuring.
Include: (1) BLUF one-liner on overall trustworthiness; (2) a scoreboard with counts of SUPPORTED / PARTIAL / UNVERIFIED / CONTRADICTED out of ${claims.length} and the headline percentage independently confirmed; (3) a "Corrections applied" table for every PARTIAL/UNVERIFIED/CONTRADICTED claim (claim, verdict, what the live source says, fix applied); (4) a "Confirmed with live source" table of SUPPORTED claims with their real URLs; (5) a blunt caveats section (claims not extracted, interpretive framing not checked, anything still unverified).

VERIFICATION RESULTS:
${merged.map(m => `[id ${m.id}] ${m.verdict} | ${m.section}\n  claim: ${m.claim}\n  live source: ${m.real_source_url}\n  note: ${m.note}`).join('\n\n')}

Return ONLY the markdown report.`,
  { label: 'finalize:report', phase: 'Finalize' }
)

return { briefing: finalBriefing, quiz, validationReport: report, tally, totalClaims: claims.length }
