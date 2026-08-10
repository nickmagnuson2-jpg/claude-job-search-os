export const meta = {
  name: 'plan-hardening',
  description: 'Adversarial panel pokes holes in a plan over multiple rounds until a fresh judge finds no blocking holes',
  whenToUse: 'Before executing an expensive/irreversible plan you want stress-tested. Pattern #3 (iterative refinement with a convergence gate). See framework/multi-agent-workflows.md.',
  phases: [
    { title: 'Critique', detail: 'N independent adversarial lenses per round' },
    { title: 'Revise', detail: 'fold surviving holes into the plan' },
    { title: 'Judge', detail: 'fresh convergence judge: airtight or another round' },
  ],
}

// REUSABLE TEMPLATE — no personal/subject data hardcoded. Everything specific comes
// from `args` at run time (this repo is public). Copy + adapt prompts for your domain.
//
// args = {
//   planPath?:  string   // path to a markdown plan the bootstrap agent reads
//   planText?:  string   // OR the plan text inline (one of planPath/planText required)
//   context:    string   // 1-paragraph domain context every critic needs
//   maxRounds?: number   // default 3
//   lenses?:    [{key, prompt}]   // optional override of the default critic lenses
//   outPath?:   string   // where to persist the hardened plan (default scratchpad)
// }

// args may arrive as a JSON string depending on the caller; normalize defensively.
const cfg = typeof args === 'string' ? JSON.parse(args) : (args || {})

const MAX_ROUNDS = cfg.maxRounds || 3
const CONTEXT = cfg.context || 'No domain context supplied.'

// Default lenses — a broad, reusable panel. Override via args.lenses for a domain.
const DEFAULT_LENSES = [
  { key: 'correctness', prompt: 'Attack whether the plan actually produces correct results. Where are the assumptions wrong, the edge cases unhandled, the logic flawed?' },
  { key: 'methodology', prompt: 'Attack the method/statistics/rigor. Are the techniques valid at this scale? Are thresholds and metrics defensible? Any measurement that would mislead?' },
  { key: 'reliability', prompt: 'Attack execution reliability. Failure modes, silent errors, things that break under real inputs, steps that can produce garbage that looks fine.' },
  { key: 'architecture', prompt: 'Attack the structure/design. Coupling, ordering, races, cost/complexity blowup, a simpler decomposition that would be more robust.' },
  { key: 'safety-privacy', prompt: 'Attack data-handling and safety. Sensitive/sealed content that must not leak, PII in outputs, irreversible or destructive steps, missing guards.' },
  { key: 'scope-yagni', prompt: 'Attack scope. Is this over-engineered? What is the simplest version that still meets the goal? Which outputs are load-bearing vs nice-to-have?' },
  { key: 'bias-validity', prompt: 'Attack causal/decision validity. Selection/survivorship bias, feedback loops that entrench the status quo, whether conclusions generalize beyond the sample.' },
  { key: 'red-team-fatal', prompt: 'Assume the finished result is worthless. Name the SINGLE most likely reason and what the plan must add to de-risk it. Be ruthless; do not hedge.' },
]
const LENSES = cfg.lenses || DEFAULT_LENSES

const HOLES_SCHEMA = {
  type: 'object',
  properties: {
    lens: { type: 'string' },
    holes: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          severity: { type: 'string', enum: ['blocking', 'major', 'minor'] },
          problem: { type: 'string' },
          suggested_fix: { type: 'string' },
        },
        required: ['title', 'severity', 'problem', 'suggested_fix'],
      },
    },
    overall_read: { type: 'string' },
  },
  required: ['lens', 'holes', 'overall_read'],
}

const REVISION_SCHEMA = {
  type: 'object',
  properties: {
    revised_plan: { type: 'string', description: 'The FULL revised plan markdown' },
    changelog: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          hole: { type: 'string' },
          action: { type: 'string', enum: ['fixed', 'rejected', 'deferred'] },
          note: { type: 'string' },
        },
        required: ['hole', 'action', 'note'],
      },
    },
  },
  required: ['revised_plan', 'changelog'],
}

const JUDGE_SCHEMA = {
  type: 'object',
  properties: {
    airtight: { type: 'boolean' },
    remaining_blocking: { type: 'array', items: { type: 'string' } },
    reason: { type: 'string' },
  },
  required: ['airtight', 'remaining_blocking', 'reason'],
}

function critiquePrompt(lens, planText, round) {
  return `You are an adversarial reviewer on a plan-hardening panel (round ${round}).

DOMAIN CONTEXT: ${CONTEXT}

YOUR LENS: ${lens.prompt}

Critique ONLY through your lens. Find real holes, not style nits. For each: a severity (blocking = the plan produces wrong/worthless results or violates a hard constraint; major = materially degrades quality; minor = worth fixing), the concrete problem, and a specific suggested fix. If the plan already handles your lens well, say so and return few/no holes — do not manufacture holes.

PLAN UNDER REVIEW:
${planText}

Return ONLY the structured object.`
}

function revisePrompt(planText, holes) {
  return `You are the plan reviser. Fix every blocking and major hole you AGREE with, and explicitly REJECT (with reason) any hole you think is wrong or out of scope — do not cargo-cult fixes. Keep the plan concrete and executable; do not bloat it.

DOMAIN CONTEXT: ${CONTEXT}

CURRENT PLAN:
${planText}

PANEL HOLES (JSON):
${JSON.stringify(holes).slice(0, 18000)}

Return the FULL revised plan markdown in revised_plan, plus a changelog (fixed/rejected/deferred + note) for every hole. Return ONLY the structured object.`
}

function judgePrompt(planText, holes, round) {
  return `You are a FRESH convergence judge (round ${round}) — you did not write or revise this plan.

DOMAIN CONTEXT: ${CONTEXT}

Decide whether the plan below is AIRTIGHT: no remaining BLOCKING holes (blocking = produces wrong/worthless results or violates a hard constraint). Major/minor do NOT block. Independently re-examine the plan text — do not just trust that listed holes were fixed. List any blocking holes that remain.

THIS ROUND'S BLOCKING HOLES (reference, JSON):
${JSON.stringify(holes.filter((h) => h.severity === 'blocking')).slice(0, 8000)}

REVISED PLAN:
${planText}

Return ONLY the structured object.`
}

// ---- Run -------------------------------------------------------------------
let planText = cfg.planText
if (!planText && cfg.planPath) {
  log('Reading plan...')
  planText = await agent(`Read the file ${cfg.planPath} and return its FULL contents verbatim (markdown only, nothing else).`, { label: 'bootstrap:read-plan', phase: 'Critique' })
}
if (!planText) throw new Error('plan-hardening: neither cfg.planText nor a readable cfg.planPath was provided (fail loud, do not critique a blank plan).')

const history = []
let airtight = false
let round = 0

while (round < MAX_ROUNDS && !airtight) {
  round += 1
  phase('Critique')
  log(`Round ${round}: ${LENSES.length} adversarial lenses attacking the plan...`)
  const critiques = await parallel(
    LENSES.map((lens) => () =>
      agent(critiquePrompt(lens, planText, round), { label: `critique:${lens.key}:r${round}`, phase: 'Critique', schema: HOLES_SCHEMA }),
    ),
  )
  const holes = critiques.filter(Boolean).flatMap((c) => c.holes.map((h) => ({ ...h, lens: c.lens })))
  const blocking = holes.filter((h) => h.severity === 'blocking').length
  const major = holes.filter((h) => h.severity === 'major').length
  log(`Round ${round}: ${holes.length} holes (${blocking} blocking, ${major} major). Revising...`)

  phase('Revise')
  const revision = await agent(revisePrompt(planText, holes), { label: `revise:r${round}`, phase: 'Revise', schema: REVISION_SCHEMA })
  planText = revision.revised_plan

  phase('Judge')
  const verdict = await agent(judgePrompt(planText, holes, round), { label: `judge:r${round}`, phase: 'Judge', schema: JUDGE_SCHEMA })
  airtight = verdict.airtight
  history.push({ round, holes: holes.length, blocking, major, changelog: revision.changelog, verdict })
  log(`Round ${round}: judge says ${airtight ? 'AIRTIGHT' : 'not yet (' + verdict.remaining_blocking.length + ' blocking remain)'}.`)
}

const outPath = cfg.outPath || `scratchpad/plan-hardened.md`
await agent(
  `Write the following hardened plan to ${outPath} exactly as given (it is final). Then return a 3-sentence summary of its final shape.\n\nPLAN:\n${planText.slice(0, 60000)}`,
  { label: 'persist:final-plan', phase: 'Judge' },
)

return { airtight, rounds: round, outPath, history, finalPlanChars: planText.length }
