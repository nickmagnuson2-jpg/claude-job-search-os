export const meta = {
  name: 'research-audit',
  description: 'Audit N systems against current best practices: fan-out Exa research per angle, adversarially validate every claim, synthesize per-system verdicts + redesign',
  whenToUse: 'Deciding whether existing systems/approaches are stale and what to change. Patterns #1 (fan-out) + #2 (adversarial verify). See framework/multi-agent-workflows.md.',
  phases: [
    { title: 'Research', detail: 'one agent per (system, angle) runs live web/Exa research' },
    { title: 'Validate', detail: 'adversarial refute-first validation of each angle\'s load-bearing claims' },
    { title: 'Synthesize', detail: 'per-system verdict + redesign, then a final assembled doc' },
  ],
}

// REUSABLE TEMPLATE — no personal/subject data hardcoded; supply it via `args`. args = {
//   subject:  string   // 1-paragraph "who/what this serves" context every agent needs
//   systems:  [{ name, currentState, angles: [string] }]   // systems to audit + research angles
//   date:     string   // for the output filename
//   outDir?:  string   // default output/analysis
//   crossCutting?: { name, currentState, angles:[string] }  // optional Nth "system" spanning all
// }

const cfg = typeof args === 'string' ? JSON.parse(args) : (args || {})
const SUBJECT = cfg.subject || 'No subject context supplied.'
const SYSTEMS = (cfg.systems || []).concat(cfg.crossCutting ? [cfg.crossCutting] : [])
if (!SYSTEMS.length) throw new Error('research-audit: args.systems is required (fail loud).')

const ANGLES = SYSTEMS.flatMap((s) =>
  (s.angles || []).map((a, i) => ({ system: s.name, currentState: s.currentState, angle: a, idx: i })),
)

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    system: { type: 'string' },
    angle: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          claim: { type: 'string' },
          approach: { type: 'string' },
          evidence: { type: 'string' },
          sources: { type: 'array', items: { type: 'string' }, description: 'Real URLs, prefer last 24 months' },
          confidence: { type: 'string', enum: ['High', 'Med', 'Low'] },
          applicability: { type: 'string', description: 'How it applies at THIS subject\'s scale' },
          where_it_does_not_fit: { type: 'string', description: 'Non-empty carve-out: where this does NOT fit' },
        },
        required: ['claim', 'evidence', 'sources', 'confidence', 'applicability', 'where_it_does_not_fit'],
      },
    },
    angle_recommendation: { type: 'string' },
  },
  required: ['system', 'angle', 'findings', 'angle_recommendation'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    system: { type: 'string' },
    angle: { type: 'string' },
    checked: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          claim: { type: 'string' },
          verdict: { type: 'string', enum: ['confirmed', 'refuted', 'uncertain'] },
          reason: { type: 'string' },
          corrected_claim: { type: 'string' },
          sources: { type: 'array', items: { type: 'string' } },
        },
        required: ['claim', 'verdict', 'reason'],
      },
    },
    surviving_recommendation: { type: 'string' },
  },
  required: ['system', 'checked', 'surviving_recommendation'],
}

function short(s) { return s.slice(0, 32).replace(/[^a-zA-Z0-9]+/g, '-') }

function researchPrompt(item) {
  return `You are researching CURRENT (last ~24 months) best practices to decide whether one system is stale and what to change. Use live web research (load Exa via ToolSearch: mcp__exa__web_search_exa and mcp__exa__web_fetch_exa, or WebSearch/WebFetch). Do NOT answer from memory — cite real URLs.

SUBJECT (who/what this serves): ${SUBJECT}

SYSTEM UNDER REVIEW (${item.system}) — current implementation:
${item.currentState}

YOUR RESEARCH ANGLE:
${item.angle}

Rules: every finding needs a real source URL, a confidence, an applicability note AT THIS SUBJECT'S SCALE, and a NON-EMPTY "where it does NOT fit" carve-out (do not just cheerlead the fanciest approach). Weigh implementation cost/complexity for the actual scale, not an enterprise. End with a concrete angle_recommendation. Return ONLY the structured object.`
}

function validatePrompt(research, item) {
  return `You are an adversarial validator. Independently try to REFUTE the load-bearing claims below against LIVE sources (Exa via ToolSearch, or WebSearch/WebFetch). Default to skepticism: if a claim is overstated, dated, or unsupported, mark it refuted/uncertain and give the corrected version. This protects the decision from shipping on a stale or hallucinated "best practice."

SYSTEM: ${item.system}
ANGLE: ${item.angle}
RESEARCH TO VALIDATE (JSON): ${JSON.stringify(research).slice(0, 6000)}

For each claim: verdict (confirmed/refuted/uncertain), a reason grounded in a source you checked THIS run, and (if not confirmed) a corrected_claim. Then give surviving_recommendation with refuted claims removed. Return ONLY the structured object.`
}

function synthPrompt(system, items) {
  const payload = items.map((v) => ({ angle: v.item.angle, research: v.research, validation: v.verdict }))
  return `You are the synthesizer for ONE system. Using ONLY validated findings (refuted claims dropped), write a self-contained markdown SECTION (start with '## ${system.name}').

SUBJECT: ${SUBJECT}
SYSTEM: ${system.name}
CURRENT IMPLEMENTATION: ${system.currentState}
VALIDATED RESEARCH (JSON): ${JSON.stringify(payload).slice(0, 14000)}

Subsections: **BLUF** (is it stale + the single most important change); **Verdict: KEEP/EVOLVE/REPLACE** (with justification tied to scale); **Evidence table** (Claim | Approach | Confidence | Source URL, confirmed/corrected only, real URLs); **Proposed redesign** (concrete, right-sized); **Where it does NOT fit / risks** (non-empty); **Migration steps** (ordered, each independently shippable). Be decisive. Return ONLY the markdown section.`
}

function finalPrompt(sections, path) {
  return `Assemble the final recommendations document and save it with Write to ${path}.

Structure: '# Best-Practices Audit & Proposed Redesign'; a 'Last updated' line; '## Executive Summary' (a System | Verdict | Headline change | Effort table + 3-5 cross-cutting bullets); '## How to read this'; the system sections VERBATIM below; '## Open questions' (the decisions the owner must make); '## Sources' (deduplicated real URLs).

SYSTEM SECTIONS (verbatim, in order):
${sections.join('\n\n---\n\n').slice(0, 60000)}

After writing, return a 4-6 sentence executive summary (plain text). Return that summary only.`
}

// ---- Run -------------------------------------------------------------------
phase('Research')
log(`Auditing ${SYSTEMS.length} systems across ${ANGLES.length} research angles (research -> validate -> synthesize).`)

const validated = await pipeline(
  ANGLES,
  (item) => agent(researchPrompt(item), { label: `research:${item.system}:${short(item.angle)}`, phase: 'Research', schema: FINDINGS_SCHEMA }),
  (research, item) =>
    agent(validatePrompt(research, item), { label: `verify:${item.system}:${item.idx}`, phase: 'Validate', schema: VERDICT_SCHEMA })
      .then((verdict) => ({ research, verdict, item })),
)

const live = validated.filter(Boolean)
const bySystem = {}
for (const r of live) { (bySystem[r.item.system] || (bySystem[r.item.system] = [])).push(r) }

phase('Synthesize')
const sections = (await parallel(
  SYSTEMS.map((s) => () => agent(synthPrompt(s, bySystem[s.name] || []), { label: `synth:${s.name}`, phase: 'Synthesize' })),
)).filter(Boolean)

const outPath = `${cfg.outDir || 'output/analysis'}/${cfg.date}-best-practices-audit.md`
const execSummary = await agent(finalPrompt(sections, outPath), { label: 'synthesize:final-doc', phase: 'Synthesize' })

return { outPath, angles: ANGLES.length, validated: live.length, sections: sections.length, execSummary }
