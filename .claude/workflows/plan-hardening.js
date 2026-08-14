export const meta = {
  name: 'plan-hardening',
  description: 'Adversarial panel pokes holes in a plan over multiple rounds, stopping when no NEW blocking hole appears; returns a residual risk register, not a pass/fail certificate',
  whenToUse: 'Before executing an expensive/irreversible plan you want stress-tested. Pattern #3 (iterative refinement with a DELTA convergence gate). Returns residual_risks + unverified_claims — there is deliberately no airtight boolean; read the register. See framework/multi-agent-workflows.md.',
  phases: [
    { title: 'Critique', detail: 'N independent adversarial lenses per round' },
    { title: 'Revise', detail: 'fold surviving holes into the plan' },
    { title: 'Judge', detail: 'fresh judge: residual risk register + unverified repo-state claims' },
    { title: 'Validate', detail: 'independent model checks every claim against real repo state', model: 'fable' },
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

// Bound by framework/review-findings-protocol.md — the canonical rules for verifying, re-rating,
// and dispositioning review findings. This file was fixed 2026-08-13, a day before the protocol
// was written, so it is the one surface whose compliance predates the doc; if the two ever
// disagree, the protocol wins.
//
// The judge returns a RESIDUAL RISK REGISTER, not a pass/fail certificate.
//
// WHY (promoted from [[feedback_adversarial_panel_needs_delta_stopping_rule]], 2 fires):
// `airtight: true` is a claim the panel makes about itself, and it has been wrong twice.
// On 2026-08-12 a run returned airtight:true on a 470-line spec; executing it surfaced 7
// real defects, TWO of which the panel had flagged as blocking in round 1 and silently
// dropped in round 2. A boolean invites the reader to stop thinking. A register of named
// residual risks, each explicitly accepted or mitigated, is always achievable and tells
// the executing agent where to look.
const JUDGE_SCHEMA = {
  type: 'object',
  properties: {
    residual_risks: {
      type: 'array',
      description: 'Every blocking/major risk that remains. Empty ONLY if genuinely none remain.',
      items: {
        type: 'object',
        properties: {
          risk: { type: 'string' },
          severity: {
            type: 'string',
            enum: ['blocking', 'major'],
            description: "THE JUDGE'S OWN rating, derived from the plan text — not the critic's rating relayed.",
          },
          critic_severity: {
            type: 'string',
            enum: ['blocking', 'major', 'minor', 'none'],
            description: "What the critic who raised it called it. 'none' if the judge raised it independently.",
          },
          severity_disagreement: {
            type: 'string',
            description: "One line on why the judge's rating differs from the critic's, or 'agrees'. Makes inheritance visible: a register where every row reads 'agrees' is a judge that did not judge.",
          },
          status: { type: 'string', enum: ['mitigated', 'accepted', 'open'] },
          why: { type: 'string', description: 'Why it is mitigated/acceptable, or why it is still open. Note here if the risk attacks machinery the panel itself added rather than the original plan.' },
          where_to_verify: { type: 'string', description: 'What the executing agent should check to confirm this in the real repo' },
        },
        required: ['risk', 'severity', 'critic_severity', 'severity_disagreement', 'status', 'why', 'where_to_verify'],
      },
    },
    unverified_claims: {
      type: 'array',
      description: 'Repo-state claims the plan asserts that were NOT verified against the actual repo this round. The executing agent must treat each as work.',
      items: { type: 'string' },
    },
    remaining_blocking: { type: 'array', items: { type: 'string' } },
    reason: { type: 'string' },
  },
  required: ['residual_risks', 'unverified_claims', 'remaining_blocking', 'reason'],
}

// The Judge emits `unverified_claims` and `where_to_verify` and then NOTHING checks
// them. That is the gap this schema closes. A panel reasoning from a false premise
// about repo state produces confident, well-argued, wrong risks — and the register
// reads identically whether the premises were true or not.
//
// THREE STATES, and UNVERIFIABLE is never a pass. Same discipline as the frame gate:
// a claim nobody could check is not a confirmed claim, and collapsing the two is how
// "we verified it" comes to mean "we looked at it."
const VALIDATION_SCHEMA = {
  type: 'object',
  properties: {
    verdict: {
      type: 'string',
      enum: ['CONFIRMED', 'REFUTED', 'UNVERIFIABLE'],
      description: 'CONFIRMED = evidence found proving it true. REFUTED = evidence found proving it false. UNVERIFIABLE = could not establish either way. Default to UNVERIFIABLE over guessing.',
    },
    evidence: {
      type: 'string',
      description: 'The concrete artifact: file:line, the command run and its output, or the specific absence checked and the scope of that check. A verdict with no evidence is not a verdict.',
    },
    consequence: {
      type: 'string',
      description: 'If REFUTED: what in the plan or the risk register rests on this false premise and must change. Otherwise a short note.',
    },
  },
  required: ['verdict', 'evidence', 'consequence'],
}

function validatePrompt(claim, kind) {
  return `You are an independent verifier. A different model produced the claim below while stress-testing a plan. NOBODY has checked it against the actual repository. Your only job is to establish whether it is true.

DOMAIN CONTEXT: ${CONTEXT}

CLAIM TO VERIFY (${kind}):
${claim}

METHOD — this is not optional:
- Actually run commands and open files. A verdict from reasoning alone is worthless here.
- Cite concrete evidence: file:line, or the command you ran and what it returned.
- To assert something does NOT exist, you must state the scope you searched. "Not found" without a named scope is not evidence of absence.
- REPO TRAP, read this before searching: the shell's \`grep\` is ripgrep-backed and honours .gitignore. Gitignored trees are INVISIBLE to a repo-root grep and return empty results indistinguishable from a true negative. Name candidate directories explicitly, or bypass with: find . -path ./.git -prune -o -name '*.md' -print | xargs /usr/bin/grep -l "<term>"

BIAS INSTRUCTION: you are here to REFUTE. The claim arrived with confidence attached and no evidence. Try to prove it wrong first. If you cannot establish it either way, return UNVERIFIABLE — do not upgrade a plausible-sounding claim to CONFIRMED because it seems reasonable.

Return ONLY the structured object.`
}

// Lexical (NOT semantic) dedup signature for a hole. Deterministic and agent-free:
// lowercase, strip non-alphanumerics, collapse whitespace, take a bounded prefix of
// title+problem. Two rounds producing the "same" hole in slightly different words will
// usually collide; genuinely new holes will not. Honest about being lexical — a
// reworded restatement can slip through, which is why the stopping rule needs K
// consecutive quiet rounds rather than one.
function holeSignature(hole) {
  const raw = `${hole.title || ''} ${hole.problem || ''}`
  return raw.toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ').trim().slice(0, 140)
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

function revisePrompt(planText, holes, beforeChars) {
  const budget = Math.round(beforeChars * MAX_GROWTH)
  return `You are the plan reviser. Fix every blocking and major hole you AGREE with, and explicitly REJECT (with reason) any hole you think is wrong or out of scope — do not cargo-cult fixes. Keep the plan concrete and executable; do not bloat it.

HARD LENGTH BUDGET: the revised plan must be at most ~${budget} characters (the current plan is ${beforeChars}). **Prefer REPLACING weak text over APPENDING caveats.** Every round of accretion adds attack surface for the next round of critics, which is how this loop fails to converge. If a fix cannot fit in the budget, cut something that earns less.

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

Your job is NOT to certify the plan. It is to produce a RESIDUAL RISK REGISTER: what could still go wrong, and whether that is acceptable.

Independently re-examine the plan text — do NOT trust that the listed holes were actually fixed. A hole marked "fixed" in the changelog but absent from the plan text is an OPEN risk, and this is the single most common failure of this loop: a blocking hole gets flagged in one round and silently dropped in the next.

DERIVE SEVERITY YOURSELF. DO NOT INHERIT IT.

The holes below carry a severity assigned by the critic who raised it. That rating is an INPUT you are re-deciding, never a verdict you are relaying. For every risk you keep, judge its severity from the plan text and the domain context on your own, then record:
- severity: YOUR rating.
- critic_severity: what the critic called it (or "none" if you raised it yourself).
- severity_disagreement: if yours differs, one line on why. If they match, "agrees".

Escalating a critic's "major" to blocking, and demoting a critic's "blocking" to major, are both expected outcomes and neither needs justifying beyond that line. A judge that returns the critics' ratings unchanged across every risk has not judged.

Return:
- residual_risks: every risk that remains AT YOUR OWN SEVERITY, each with a status (mitigated = the plan genuinely handles it; accepted = it remains but is a reasonable trade-off; open = unhandled) and where_to_verify — the concrete thing the executing agent should check in the real repo to confirm it.
- unverified_claims: every assertion the plan makes about repo state (a file exists, a path is public, a format is X) that was NOT checked against the actual repository. Be thorough here. The executing agent will treat each as work, and an unverified claim silently inherited is how this loop has produced wrong plans before.
- remaining_blocking: titles of the risks YOU rate blocking.

An empty residual_risks is a strong claim — return it only if you genuinely cannot name a way this plan fails.

ALSO CHECK WHAT THE PANEL BUILT. This loop revises the plan between rounds, so some risks may attack machinery the panel itself introduced rather than anything the author proposed. Where you can tell, say so in the risk's `why` — "this attacks an addition from round N, not the original plan" — because the cheapest fix for those is often to drop the addition.

ALL OF THIS ROUND'S HOLES (every severity, JSON — you are re-rating them, so you get the full set, not a pre-filtered one):
${JSON.stringify(holes).slice(0, 12000)}

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
const seenBlocking = new Set()   // lexical signatures of every blocking hole ever raised
let round = 0
let quietRounds = 0              // consecutive rounds yielding no NEW blocking hole
let lastVerdict = null

// DELTA STOPPING RULE. The old gate was "loop until the judge says airtight", which is
// anti-convergent by construction: every fix adds prose, new prose is new attack
// surface, and N fresh critics primed to attack will essentially always find something.
// Stop instead when a round surfaces no NEW blocking hole (K consecutive quiet rounds),
// which is always reachable. Origin: 2026-08-10 (74->70->70 holes, never converged) and
// 2026-08-12 (airtight:true certified over 7 residual defects).
const QUIET_ROUNDS_TO_STOP = cfg.quietRoundsToStop || 1
const MAX_GROWTH = cfg.maxGrowthPerRound || 1.10   // reviser may not bloat the plan

while (round < MAX_ROUNDS && quietRounds < QUIET_ROUNDS_TO_STOP) {
  round += 1
  phase('Critique')
  log(`Round ${round}: ${LENSES.length} adversarial lenses attacking the plan...`)
  const critiques = await parallel(
    LENSES.map((lens) => () =>
      agent(critiquePrompt(lens, planText, round), { label: `critique:${lens.key}:r${round}`, phase: 'Critique', schema: HOLES_SCHEMA }),
    ),
  )
  const holes = critiques.filter(Boolean).flatMap((c) => c.holes.map((h) => ({ ...h, lens: c.lens })))
  const blockingHoles = holes.filter((h) => h.severity === 'blocking')
  const major = holes.filter((h) => h.severity === 'major').length

  // Only holes never raised before count toward convergence.
  const fresh = blockingHoles.filter((h) => !seenBlocking.has(holeSignature(h)))
  fresh.forEach((h) => seenBlocking.add(holeSignature(h)))
  quietRounds = fresh.length === 0 ? quietRounds + 1 : 0

  log(`Round ${round}: ${holes.length} holes (${blockingHoles.length} blocking, ${fresh.length} NEW, ${major} major). Revising...`)

  phase('Revise')
  const beforeChars = planText.length
  const revision = await agent(revisePrompt(planText, holes, beforeChars), { label: `revise:r${round}`, phase: 'Revise', schema: REVISION_SCHEMA })
  const grew = revision.revised_plan.length / Math.max(beforeChars, 1)
  if (grew > MAX_GROWTH) {
    log(`Round ${round}: plan grew ${Math.round((grew - 1) * 100)}% (cap ${Math.round((MAX_GROWTH - 1) * 100)}%) — accretion, not revision. Flagged in history.`)
  }
  planText = revision.revised_plan

  phase('Judge')
  lastVerdict = await agent(judgePrompt(planText, holes, round), { label: `judge:r${round}`, phase: 'Judge', schema: JUDGE_SCHEMA })
  history.push({
    round, holes: holes.length, blocking: blockingHoles.length, newBlocking: fresh.length, major,
    growth: Number(grew.toFixed(3)), overGrowthCap: grew > MAX_GROWTH,
    changelog: revision.changelog, verdict: lastVerdict,
  })
  const openRisks = (lastVerdict.residual_risks || []).filter((r) => r.status === 'open').length
  log(`Round ${round}: ${fresh.length} new blocking, ${openRisks} open residual risk(s), ${(lastVerdict.unverified_claims || []).length} unverified claim(s).`)
}

const stoppedBecause = quietRounds >= QUIET_ROUNDS_TO_STOP
  ? `no new blocking holes for ${quietRounds} round(s)`
  : `hit maxRounds (${MAX_ROUNDS}) with new blocking holes still arriving — treat this plan as UNCONVERGED`

// ---------------------------------------------------------------------------
// VALIDATE — the step that stops the register resting on unchecked premises.
//
// Runs on a DIFFERENT model to the panel, deliberately. Same-model verification
// inherits the same blind spots that produced the claim; an independent model is
// the cheapest real independence available. Skip with args.skipValidation.
// ---------------------------------------------------------------------------
phase('Validate')

const claimsToCheck = [
  ...(lastVerdict?.unverified_claims || []).map((c) => ({ claim: c, kind: 'unverified repo-state claim' })),
  ...(lastVerdict?.residual_risks || [])
    .filter((r) => r.where_to_verify)
    .map((r) => ({ claim: `RISK: ${r.risk}\nTO VERIFY: ${r.where_to_verify}`, kind: 'residual risk premise' })),
]

let validations = []
if (cfg.skipValidation) {
  log('Validation SKIPPED by caller — every claim below is unchecked.')
} else if (claimsToCheck.length === 0) {
  log('Validation: the judge emitted no checkable claims. That is itself worth noticing.')
} else {
  log(`Validating ${claimsToCheck.length} claim(s) against real repo state on an independent model...`)
  validations = (await parallel(claimsToCheck.map((c, i) => () =>
    agent(validatePrompt(c.claim, c.kind), {
      label: `validate:${i + 1}`,
      phase: 'Validate',
      model: 'fable',
      schema: VALIDATION_SCHEMA,
    }).then((v) => (v ? { ...v, claim: c.claim, kind: c.kind } : null)),
  ))).filter(Boolean)

  const refuted = validations.filter((v) => v.verdict === 'REFUTED')
  const unver = validations.filter((v) => v.verdict === 'UNVERIFIABLE')
  log(`Validation: ${validations.filter((v) => v.verdict === 'CONFIRMED').length} confirmed, ${refuted.length} REFUTED, ${unver.length} unverifiable.`)
  if (refuted.length) {
    log(`${refuted.length} claim(s) were FALSE. Any risk resting on them is unsound and must be re-read, not just noted.`)
  }
}

// The register is persisted WITH the plan, not only returned. Before 2026-08-14 this wrote the
// hardened plan alone, so the artifact surviving on disk was a clean-looking final plan with the
// register naming where it still fails stripped off — and a reader opening it a week later has no
// way to tell it was ever stress-tested. Returning the register to a caller that may discard it is
// not persistence. Per framework/review-findings-protocol.md: findings outlive the session that
// produced them, or they are not findings.
const outPath = cfg.outPath || `scratchpad/plan-hardened.md`
const registerMd = [
  `## Residual Risk Register`,
  ``,
  `Rounds: ${round}. Stopped because: ${stoppedBecause}.`,
  `**There is deliberately no \`airtight\` verdict. Read the register and decide.**`,
  ``,
  `| Risk | My severity | Critic severity | Disagreement | Status | Why | Where to verify |`,
  `|---|---|---|---|---|---|---|`,
  ...(lastVerdict?.residual_risks || []).map(
    (r) => `| ${r.risk} | ${r.severity} | ${r.critic_severity} | ${r.severity_disagreement} | ${r.status} | ${r.why} | ${r.where_to_verify} |`,
  ),
  ``,
  `**A register where every Disagreement reads "agrees" is a judge that did not judge.**`,
  ``,
  `### Unverified repo-state claims`,
  ``,
  ...((lastVerdict?.unverified_claims || []).length
    ? (lastVerdict.unverified_claims || []).map((c) => `- ${c}`)
    : ['- none recorded']),
  ``,
  `The executing agent must treat each as work, not as background.`,
].join('\n')

await agent(
  `Write the following to ${outPath} exactly as given (it is final), the plan first and the register immediately after it under a horizontal rule. Then return a 3-sentence summary of its final shape.\n\nPLAN:\n${planText.slice(0, 60000)}\n\n---\n\n${registerMd}`,
  { label: 'persist:final-plan', phase: 'Judge' },
)

// NO `airtight` FIELD, deliberately. Callers that want a green light must read the
// register and decide for themselves. See JUDGE_SCHEMA above for the two fires that
// removed it.
return {
  rounds: round,
  stoppedBecause,
  converged: stoppedBecause.startsWith('no new blocking'),
  residual_risks: lastVerdict?.residual_risks || [],
  unverified_claims: lastVerdict?.unverified_claims || [],
  open_blocking: (lastVerdict?.residual_risks || []).filter((r) => r.status === 'open' && r.severity === 'blocking').map((r) => r.risk),

  // Validation results. READ `refuted` FIRST — a refuted claim means the panel
  // reasoned from a false premise, so the risk built on it is unsound rather than
  // merely open. `unverifiable` is NOT a pass; those claims remain unchecked work.
  validation: {
    ran: !cfg.skipValidation && claimsToCheck.length > 0,
    checked: validations.length,
    refuted: validations.filter((v) => v.verdict === 'REFUTED'),
    unverifiable: validations.filter((v) => v.verdict === 'UNVERIFIABLE'),
    confirmed: validations.filter((v) => v.verdict === 'CONFIRMED'),
  },

  outPath,
  history,
  finalPlanChars: planText.length,
}
