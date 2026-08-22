export const meta = {
  name: 'plan-hardening',
  description: 'Two-layer plan hardening: a premise gate that HARD STOPS before execution critique, then scoped probes with per-hole regression retest. Returns a residual risk register.',
  whenToUse: 'Before executing an expensive or irreversible plan. Implements framework/plan-hardening-v2-spec.md. Bounded questions converge; "attack this plan" does not — v1 ran 5 rounds x ~26 new blocking holes and never converged. ARGS MUST BE AN OBJECT, never a prose string: {planText OR planPath (required), context (required), outPath?, registerPath?, minPlanChars?, minRetention?}. Passing prose dies with a JSON parse error in ~15ms before any agent runs.',
  phases: [
    { title: 'Goal', detail: 'two independent goal statements, one blind to the plan' },
    { title: 'Premise', detail: 'is G the right goal — HARD STOP if open' },
    { title: 'Targets', detail: 'type the plan, derive targets from four generators' },
    { title: 'Probe', detail: 'one agent per target, plus one unscoped' },
    { title: 'Fix', detail: 'one agent per hole decides it, then one reviser applies the edits' },
    { title: 'Retest', detail: 'per-hole: does this specific hole still fire?' },
    { title: 'Validate', detail: 'repo-grounded check of factual claims', model: 'fable' },
    { title: 'Persist', detail: 'write the register and revised plan to disk' },
  ],
}

// Implements framework/plan-hardening-v2-spec.md. Read that before editing this file.
//
// WHAT CHANGED FROM v1 AND WHY (both measured, same plan, 2026-08-21):
//   run 1: 88 agents, 5.24M tokens, 5 rounds, UNCONVERGED (82/84/78/75/76 holes,
//          ~26 NEW blocking every round). Its final reviser received a 3-character
//          payload and RECONSTRUCTED the plan from critics' paraphrases.
//   run 2: 46 agents, 2.94M tokens, 2 rounds, UNCONVERGED. Plan silently renamed.
//
// Root cause: "attack this plan" is an UNBOUNDED question, so fresh critics re-derive
// it forever and a delta gate can never fire. Every stage below asks a question with a
// decidable answer. Second cause: premise findings were reported as peers of execution
// findings and got buried; they are upstream and now gate them.

const cfg = typeof args === 'string' ? JSON.parse(args) : (args || {})
const CONTEXT = cfg.context || 'No domain context supplied.'
const MIN_PLAN_CHARS = cfg.minPlanChars || 500
const MAX_GROWTH = 1.15
const MIN_RETENTION = cfg.minRetention || 0.6
// Absolute allowance per commissioned fix. A flat growth ratio encodes "a few small fixes"
// and silently becomes a blocker at scale — the same defect class as the single-agent fix
// stage that could not disposition 25 holes. Budget must scale with work commissioned.
const PER_FIX_CHARS = cfg.perFixChars || 800
// Hard ceiling regardless of hole count. Without it the per-fix allowance dominates on a short
// plan and the accretion guard goes toothless (measured: a 1.3KB plan with 25 fixes permitted
// 12.66x). A plan that triples is being re-authored in substance even if its lines survive.
const MAX_TOTAL_GROWTH = cfg.maxTotalGrowth || 3

const PLAN_TYPES = {
  diagnostic: 'success = the question is answered, INCLUDING "no". Failure mode: can only confirm, never falsify.',
  intervention: 'success = a pre-registered before/after delta. Failure mode: no baseline, so no attribution.',
  guard: 'success = fires on real violations AND stays silent on near-misses. Failure mode: activation measured, restraint never.',
  deliverable: 'success = acceptance criteria met AND someone uses it. Failure mode: ships to a surface nobody runs.',
}

// ---------------------------------------------------------------- schemas

const GOAL_SCHEMA = {
  type: 'object',
  properties: {
    goal: { type: 'string', description: 'One or two sentences. The outcome, not the activities.' },
    success_observation: { type: 'string', description: 'What you would observe, after execution, that means this worked.' },
    failure_observation: { type: 'string', description: 'What you would observe that means it FAILED. If you cannot name one, say so explicitly — an unfalsifiable goal is the finding.' },
    beneficiary: { type: 'string', description: 'Who is better off, and how you would know.' },
  },
  required: ['goal', 'success_observation', 'failure_observation', 'beneficiary'],
}

const PREMISE_SCHEMA = {
  type: 'object',
  properties: {
    delta: { type: 'string', enum: ['material', 'immaterial'], description: 'material = the two goal statements describe different outcomes, different success conditions, or different beneficiaries.' },
    delta_explanation: { type: 'string' },
    goal_is_right: { type: 'string', enum: ['yes', 'no', 'unclear'], description: 'Independent of the delta: is the problem-derived goal itself the right goal, or is the problem framed wrongly upstream of both?' },
    premise_status: { type: 'string', enum: ['open', 'resolved'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          claim: { type: 'string' },
          failure_scenario: { type: 'string', description: 'Concretely: what goes wrong downstream if this premise defect is not fixed.' },
        },
        required: ['claim', 'failure_scenario'],
      },
    },
  },
  required: ['delta', 'delta_explanation', 'goal_is_right', 'premise_status', 'findings'],
}

const TARGET_SCHEMA = {
  type: 'object',
  properties: {
    plan_type: { type: 'string', enum: ['diagnostic', 'intervention', 'guard', 'deliverable', 'untypeable'] },
    plan_type_reason: { type: 'string' },
    targets: {
      type: 'array',
      description: '5 to 9 targets. Fewer suggests a shallow read; more reproduces the volume problem this design exists to fix.',
      items: {
        type: 'object',
        properties: {
          key: { type: 'string', description: 'short kebab-case slug' },
          generator: { type: 'string', enum: ['stated-constraints', 'irreversible-steps', 'cost-drivers', 'load-bearing-assumptions', 'type-failure-mode'] },
          question: { type: 'string', description: 'The bounded question this target asks of the plan.' },
          what_counts_as_a_hole: { type: 'string' },
        },
        required: ['key', 'generator', 'question', 'what_counts_as_a_hole'],
      },
    },
  },
  required: ['plan_type', 'plan_type_reason', 'targets'],
}

const HOLES_SCHEMA = {
  type: 'object',
  properties: {
    target: { type: 'string' },
    holes: {
      type: 'array',
      description: 'FEW and HIGH-CONFIDENCE. Volume is a failure mode here, not thoroughness.',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          severity: { type: 'string', enum: ['blocking', 'major', 'minor'] },
          problem: { type: 'string' },
          failure_scenario: { type: 'string', description: 'Concrete inputs or circumstances -> the wrong outcome. A hole with no failure scenario is not a hole.' },
          suggested_fix: { type: 'string' },
        },
        required: ['title', 'severity', 'problem', 'failure_scenario', 'suggested_fix'],
      },
    },
  },
  required: ['target', 'holes'],
}

const DISPOSITION_SCHEMA = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['fixed', 'accepted'] },
    note: { type: 'string', description: 'If fixed: what must change, concretely. If accepted: WHY the risk is acceptable. An empty or hand-waving reason on an accepted finding FAILS THE RUN.' },
    edit_instruction: { type: 'string', description: 'Only when status=fixed. The precise edit: which section, what text to replace, and the replacement. A reviser who has not seen this hole must be able to apply it verbatim.' },
  },
  required: ['status', 'note'],
}

const REVISE_SCHEMA = {
  type: 'object',
  properties: {
    revised_plan: { type: 'string', description: 'The FULL revised plan markdown, with every supplied edit applied.' },
    applied: { type: 'array', items: { type: 'string' }, description: 'The hole IDs whose edits you actually applied.' },
  },
  required: ['revised_plan', 'applied'],
}

const FIX_SCHEMA = {
  type: 'object',
  properties: {
    revised_plan: { type: 'string', description: 'The FULL revised plan markdown.' },
    dispositions: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string', description: 'The hole ID exactly as given. Every hole must appear exactly once.' },
          status: { type: 'string', enum: ['fixed', 'accepted'] },
          note: { type: 'string', description: 'If fixed: what changed. If accepted: WHY it is acceptable. An empty reason on an accepted finding fails the run.' },
        },
        required: ['id', 'status', 'note'],
      },
    },
  },
  required: ['revised_plan', 'dispositions'],
}

const RETEST_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['closed', 'still-fires', 'fix-introduced-new'] },
    evidence: { type: 'string', description: 'Quote the plan text that closes it, or show where it still fires. A verdict with no quoted text is not a verdict.' },
  },
  required: ['verdict', 'evidence'],
}

const NEWHOLES_SCHEMA = {
  type: 'object',
  properties: {
    new_holes: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          severity: { type: 'string', enum: ['blocking', 'major', 'minor'] },
          problem: { type: 'string' },
          failure_scenario: { type: 'string' },
        },
        required: ['title', 'severity', 'problem', 'failure_scenario'],
      },
    },
  },
  required: ['new_holes'],
}

const VALIDATION_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['CONFIRMED', 'REFUTED', 'UNVERIFIABLE'] },
    evidence: { type: 'string', description: 'file:line, or the command run and its output, or the specific absence checked AND why that scope would have contained the thing.' },
    consequence: { type: 'string' },
  },
  required: ['verdict', 'evidence', 'consequence'],
}

// ---------------------------------------------------------------- prompts

const REPO_TRAP = `REPO TRAP: the shell's \`grep\` is ripgrep-backed and honours .gitignore, so gitignored trees are INVISIBLE to a repo-root grep and return empty results indistinguishable from a true negative. Name candidate directories explicitly or bypass with /usr/bin/grep. CONFIG TRAP: settings can live in BOTH a project-local and a global file — checking one and concluding absence is a known false-refutation in this harness's own history.`

function goalFromPlanPrompt(planText) {
  return `Read the plan below and state what it is actually trying to achieve.

DOMAIN CONTEXT: ${CONTEXT}

PLAN:
${planText}

State the OUTCOME, not the activities. "Re-key 488 descriptions" is an activity; the outcome is whatever that activity is supposed to cause. Name what you would observe if it worked and what you would observe if it failed. If you cannot name a failure observation, say so explicitly — an unfalsifiable goal is itself the finding.

Return ONLY the structured object.`
}

function goalFromProblemPrompt() {
  return `You are given a PROBLEM SITUATION and no plan. Nobody has shown you any proposed solution, and you must not speculate about what one might contain.

PROBLEM SITUATION: ${CONTEXT}

State what a plan in this situation SHOULD achieve. What is the outcome worth having? What would you observe if it worked, and what would you observe if it failed? Who is better off?

Answer from the problem alone. Return ONLY the structured object.`
}

function premisePrompt(fromPlan, fromProblem) {
  return `You are the premise gate. You have NOT been shown the plan, deliberately.

DOMAIN CONTEXT: ${CONTEXT}

GOAL AS DERIVED FROM THE PLAN:
${JSON.stringify(fromPlan, null, 1)}

GOAL AS DERIVED FROM THE PROBLEM ALONE (author never saw the plan):
${JSON.stringify(fromProblem, null, 1)}

Two bounded questions.

1. Is the delta between these MATERIAL or IMMATERIAL? Material means they describe different outcomes, different success conditions, or different beneficiaries. Wording differences are immaterial. A plan aimed at something adjacent to the real problem is material even if it sounds similar.

2. Independent of the delta: is the PROBLEM-derived goal itself right, or is the problem framed wrongly upstream of both?

Set premise_status to "open" if the delta is material OR goal_is_right is "no"/"unclear". Otherwise "resolved".

premise_status: open HARD STOPS the whole run before any execution critique. That is intended and it is a successful outcome, not a failure — the premise finding is the deliverable. Do not soften a material delta to let the run continue, and do not manufacture one to look rigorous.

Return ONLY the structured object.`
}

function targetPrompt(planText) {
  const types = Object.entries(PLAN_TYPES).map(([k, v]) => `- ${k}: ${v}`).join('\n')
  return `You generate the target list for a plan-hardening pass. Targets are DERIVED, never freeformed.

DOMAIN CONTEXT: ${CONTEXT}

STEP 1 — type the plan as EXACTLY ONE of:
${types}

If the plan genuinely resists typing, it is doing more than one thing and should be split. Return plan_type "untypeable" and explain — that stops the run, which is correct.

STEP 2 — run these four generators over the plan and produce 5-9 targets total:
1. STATED CONSTRAINTS — the plan's own declared rules. Self-violation is the highest-yield target class and the cheapest to check.
2. IRREVERSIBLE STEPS — anything that cannot be undone, plus anything whose damage PROPAGATES (backups, mirrors, published artifacts, sent messages).
3. COST DRIVERS — the two or three steps consuming most of the budget. Underestimates here dominate real cost.
4. LOAD-BEARING ASSUMPTIONS — claims that, if false, collapse an item. Especially claims about system state that were asserted rather than measured.

ALWAYS include one target for the failure mode of the type you assigned.

Each target needs a BOUNDED question and an explicit "what counts as a hole here."

PLAN:
${planText}

Return ONLY the structured object.`
}

function probePrompt(target, planText) {
  return `You are an adversarial reviewer with ONE narrow target. Ignore everything outside it.

DOMAIN CONTEXT: ${CONTEXT}

YOUR TARGET: ${target.key}
QUESTION: ${target.question}
WHAT COUNTS AS A HOLE HERE: ${target.what_counts_as_a_hole}

Return FEW, HIGH-CONFIDENCE holes. Volume is a failure mode of this harness, not a sign of thoroughness — a prior version produced 82 holes in one round and none of them could be acted on. If the plan handles your target well, say so and return an empty list. Do not manufacture holes to look useful.

Every hole needs a CONCRETE failure scenario: specific inputs or circumstances leading to a specific wrong outcome. A hole with no failure scenario is not a hole and will be discarded.

${REPO_TRAP}

PLAN:
${planText}

Return ONLY the structured object.`
}

function unscopedPrompt(targets, planText) {
  return `Every other reviewer on this pass was given a narrow target. You are the completeness critic and your ONLY question is: WHAT DID THE TARGET LIST MISS?

DOMAIN CONTEXT: ${CONTEXT}

TARGETS ALREADY COVERED (do NOT re-raise anything inside these):
${targets.map(t => `- ${t.key}: ${t.question}`).join('\n')}

You exist because scoping structurally cannot find the hole nobody thought to look for. In a prior run the single best finding — an irreversible step whose damage propagated through a nightly backup mirror, with no rollback path — came from an angle no generated target list contained.

Find what falls between or outside those targets. Few, high-confidence, each with a concrete failure scenario.

${REPO_TRAP}

PLAN:
${planText}

Return ONLY the structured object with target set to "unscoped".`
}

function dispositionPrompt(hole, planText) {
  return `You decide the fate of ONE specific hole in a plan. This is a bounded question with a decidable answer. Do not review the rest of the plan; other agents own the other holes.

DOMAIN CONTEXT: ${CONTEXT}

THE HOLE (${hole.id}, severity ${hole.severity}, from target "${hole.target}"):
title: ${hole.title}
problem: ${hole.problem}
failure scenario: ${hole.failure_scenario}
suggested fix: ${hole.suggested_fix || '(none offered)'}

THE PLAN:
${planText}

TWO DISPOSITIONS ONLY:
- "fixed" — the plan should change. Supply an edit_instruction precise enough that a reviser who has NOT seen this hole can apply it verbatim: name the section, quote the text to replace, give the replacement.
- "accepted" — deliberately not fixed. The note must say WHY the risk is acceptable and what is being knowingly carried. An empty or hand-waving reason FAILS THE RUN, the same contract as a mutation allowlist: an exception without a justification is how a gate decays into "we looked at it."

PREFER REPLACING weak text over APPENDING caveats. Accretion is how this loop failed before: appended prose becomes attack surface for the next round. Do not rename, renumber, or restructure the plan's items — a prior run silently renamed every item and made retest impossible.

Return ONLY the structured object.`
}

function revisePrompt(planText, decided) {
  // Must match the INVARIANT 6b arithmetic exactly. A reviser told one limit while the gate
  // enforces another is a guard lying to the thing it guards.
  const budget = Math.round(Math.min(planText.length * MAX_GROWTH + decided.length * PER_FIX_CHARS, planText.length * MAX_TOTAL_GROWTH))
  return `You are the plan reviser. You are NOT a reviewer: every decision below has already been made by another agent. Your only job is to APPLY the supplied edits to the plan faithfully.

DOMAIN CONTEXT: ${CONTEXT}

HARD RULES:
- Apply every edit listed. Do not re-litigate, soften, or skip one because you disagree.
- Do NOT re-author the plan. Keep every untouched section VERBATIM, character for character. A run is ABORTED automatically if too little of the original survives or the plan grows past ~${budget} characters (current: ${planText.length}). Two prior runs died exactly here, one by rebuilding the plan from paraphrase and one by silently renaming every item.
- Do not rename, renumber, or restructure items. Identifiers stay stable so each hole can be retested.

EDITS TO APPLY (JSON):
${JSON.stringify(decided).slice(0, 24000)}

CURRENT PLAN:
${planText}

Return the FULL revised plan and the list of hole IDs you applied. Return ONLY the structured object.`
}

function persistPrompt(path, content, what) {
  return `Write a file. This is a mechanical persistence task, not an authoring task.

TARGET PATH (relative to the repo root): ${path}

Write EXACTLY the content below to that path, byte for byte. Do not summarize it, reformat it, pretty-print it differently, add commentary, add a header, or truncate it. Create parent directories if needed. If the file exists, overwrite it.

After writing, verify by reading the file back and confirming its byte length matches ${content.length}. Report the path and the byte count you observed.

CONTENT (${what}, ${content.length} bytes):
${content}`
}

function retestPrompt(hole, revisedPlan) {
  return `You are a regression tester for a single, specific defect. You did not write this plan or the fix.

DOMAIN CONTEXT: ${CONTEXT}

THE ORIGINAL HOLE (${hole.id}):
title: ${hole.title}
problem: ${hole.problem}
failure_scenario: ${hole.failure_scenario}

ONE BOUNDED QUESTION: against the revised plan below, does THIS SPECIFIC hole still fire?

- "closed" — the revised plan makes this failure scenario no longer reachable. Quote the text that does it.
- "still-fires" — the fix is cosmetic, or addresses something adjacent, or the failure scenario still runs. Show where.
- "fix-introduced-new" — this hole is closed but the fix created a different problem. Describe it.

Do NOT look for other holes. Do NOT re-critique the plan. Answer only about ${hole.id}. A verdict without quoted plan text is not a verdict.

REVISED PLAN:
${revisedPlan}

Return ONLY the structured object.`
}

function newHolesPrompt(diff) {
  return `A plan was just revised. You are shown ONLY the changes, not the whole plan, so that you cannot re-derive the findings that motivated them.

DOMAIN CONTEXT: ${CONTEXT}

ONE BOUNDED QUESTION: do any of these CHANGES introduce a NEW blocking or major problem that did not exist before?

Not "is the plan good." Not "what else is wrong." Only: did these edits break something. Fixes frequently introduce new defects — a constraint added in one section can contradict a step in another, and a deferral can strand a dependency. Empty list is the expected answer and is fine.

CHANGES:
${diff.slice(0, 20000)}

Return ONLY the structured object.`
}

function validatePrompt(claim, kind) {
  return `You are an independent verifier. A different model produced the claim below while stress-testing a plan. NOBODY has checked it against the actual repository.

DOMAIN CONTEXT: ${CONTEXT}

CLAIM TO VERIFY (${kind}):
${claim}

METHOD:
- Actually run commands and open files. A verdict from reasoning alone is worthless.
- Cite concrete evidence: file:line, or the command and its output.
- To assert something does NOT exist you must state the scope searched AND why that scope would have contained it if it existed.
- NAME THE SCOPE IN THE SAME SENTENCE AS THE VERDICT. Two false refutations in this harness's own history came from comparing different denominators and from checking one config file of two.

${REPO_TRAP}

BIAS INSTRUCTION: try to REFUTE first. If you cannot establish it either way, return UNVERIFIABLE — never upgrade a plausible-sounding claim to CONFIRMED.

Return ONLY the structured object.`
}

// ---------------------------------------------------------------- helpers

function lineDiff(before, after) {
  const b = new Set(before.split('\n'))
  const added = after.split('\n').filter(l => l.trim() && !b.has(l))
  const a = new Set(after.split('\n'))
  const removed = before.split('\n').filter(l => l.trim() && !a.has(l))
  return `--- ADDED (${added.length} lines) ---\n${added.join('\n')}\n\n--- REMOVED (${removed.length} lines) ---\n${removed.join('\n')}`
}

// The workflow DSL has NO filesystem access, so persistence goes through an agent with
// Write tools — the same way planPath is already READ by a bootstrap agent. Before this
// existed, `registerPath` and `outPath` were advertised in the meta and silently ignored:
// the argument contract documented a behavior the code did not have, which is the exact
// "written down and followed" tier this harness exists to refuse.
async function register(obj) {
  const out = JSON.parse(JSON.stringify(obj))
  const jobs = []
  if (cfg.registerPath) {
    const body = JSON.stringify(out, null, 2)
    jobs.push(() => agent(persistPrompt(cfg.registerPath, body, 'the residual risk register, JSON'),
      { label: 'persist:register', phase: 'Persist' }))
  }
  if (cfg.outPath && out.revised_plan) {
    jobs.push(() => agent(persistPrompt(cfg.outPath, out.revised_plan, 'the revised plan, markdown'),
      { label: 'persist:plan', phase: 'Persist' }))
  }
  if (jobs.length) {
    phase('Persist')
    const done = await parallel(jobs)
    out.persisted = {
      register: cfg.registerPath || null,
      plan: (cfg.outPath && out.revised_plan) ? cfg.outPath : null,
      // A null here means the write agent died. The register still returns, but the caller
      // must not report it as durable — an unverified write is how a result quietly evaporates.
      confirmations: done.map(d => (typeof d === 'string' ? d.slice(0, 200) : d)),
      all_writes_returned: done.every(Boolean),
    }
  }
  return out
}

// ---------------------------------------------------------------- run

phase('Goal')

let planText = cfg.planText || ''
if (!planText && cfg.planPath) {
  const boot = await agent(
    `Read the file at ${cfg.planPath} and return its ENTIRE contents verbatim as plain text. No summary, no commentary, no truncation.`,
    { label: 'read-plan', phase: 'Goal' })
  planText = typeof boot === 'string' ? boot : String(boot || '')
}

// INVARIANT 1 — payload floor. v1 received a 3-character payload and RECONSTRUCTED the
// plan from critics' paraphrases, producing a hardened version of an artifact nobody read.
// A short payload aborts. It is never reconstructed, never inferred, never worked around.
if (planText.trim().length < MIN_PLAN_CHARS) {
  throw new Error(
    `INVARIANT 1 (payload floor) FAILED: plan text is ${planText.trim().length} chars, ` +
    `floor is ${MIN_PLAN_CHARS}. Aborting rather than hardening a truncated or empty plan. ` +
    `Check planPath (${cfg.planPath || 'none'}) and that the bootstrap agent returned file contents.`)
}
log(`plan loaded: ${planText.length} chars`)

// INVARIANT 2 — blindness. The problem-derived goal is the oracle arm; it only works if
// that agent never sees the plan. Asserted structurally, not by instruction, because an
// agent told not to anchor still anchors.
const blindPrompt = goalFromProblemPrompt()
const planProbe = planText.trim().slice(0, 200)
if (planProbe && blindPrompt.includes(planProbe)) {
  throw new Error('INVARIANT 2 (blindness) FAILED: plan text leaked into the plan-blind goal prompt.')
}

const [goalFromPlan, goalFromProblem] = await parallel([
  () => agent(goalFromPlanPrompt(planText), { label: 'goal:from-plan', phase: 'Goal', schema: GOAL_SCHEMA }),
  () => agent(blindPrompt, { label: 'goal:from-problem (plan-blind)', phase: 'Goal', schema: GOAL_SCHEMA }),
])
if (!goalFromPlan || !goalFromProblem) throw new Error('goal extraction returned nothing; aborting')

phase('Premise')
const premise = await agent(premisePrompt(goalFromPlan, goalFromProblem),
  { label: 'premise-gate', phase: 'Premise', schema: PREMISE_SCHEMA })
if (!premise) throw new Error('premise gate returned nothing; aborting')

const premiseFindings = (premise.findings || []).map((f, i) => ({
  id: `P${i + 1}`, layer: 'premise', ...f, status: 'open',
}))

// INVARIANT 3 — the gate. Execution critique is UNREACHABLE while the premise is open.
// This is the expensive choice and it is deliberate: the observed failure was five rounds
// of execution critique polishing a plan whose aim had never been checked.
if (premise.premise_status === 'open') {
  log(`PREMISE OPEN — hard stop. delta=${premise.delta}, goal_is_right=${premise.goal_is_right}`)
  phase('Validate')
  const checked = await parallel(premiseFindings.map(f => () =>
    agent(validatePrompt(f.claim, 'premise finding'), { label: `validate:${f.id}`, phase: 'Validate', model: 'fable', schema: VALIDATION_SCHEMA })
      .then(v => ({ ...f, validation: v }))))
  return await register({
    terminal_state: 'PREMISE-OPEN',
    spec: 'framework/plan-hardening-v2-spec.md',
    note: 'Execution critique did NOT run. This is the gate working as designed, not a failed run. The premise finding is the deliverable.',
    goal_from_plan: goalFromPlan,
    goal_from_problem: goalFromProblem,
    goal_delta: premise.delta,
    delta_explanation: premise.delta_explanation,
    goal_is_right: premise.goal_is_right,
    premise_status: 'open',
    findings: checked.filter(Boolean),
    agents_used: 'S0-S1 + validation only',
  })
}

phase('Targets')
const targeting = await agent(targetPrompt(planText),
  { label: 'target-generation', phase: 'Targets', schema: TARGET_SCHEMA })
if (!targeting) throw new Error('target generation returned nothing; aborting')

if (targeting.plan_type === 'untypeable') {
  return await register({
    terminal_state: 'PREMISE-OPEN',
    spec: 'framework/plan-hardening-v2-spec.md',
    note: 'Plan could not be typed, which means it is doing more than one thing and should be split. Emitted as a premise finding per the spec.',
    goal_from_plan: goalFromPlan,
    goal_from_problem: goalFromProblem,
    premise_status: 'open',
    findings: [...premiseFindings, {
      id: `P${premiseFindings.length + 1}`, layer: 'premise', status: 'open',
      claim: 'Plan is untypeable and should be split',
      failure_scenario: targeting.plan_type_reason,
    }],
  })
}

const targets = cfg.targets || targeting.targets || []
log(`plan typed as ${targeting.plan_type}; ${targets.length} targets`)

phase('Probe')
const probeResults = await parallel([
  ...targets.map(t => () => agent(probePrompt(t, planText), { label: `probe:${t.key}`, phase: 'Probe', schema: HOLES_SCHEMA })),
  () => agent(unscopedPrompt(targets, planText), { label: 'probe:unscoped', phase: 'Probe', schema: HOLES_SCHEMA }),
])

// INVARIANT 4 — ID stability. Assigned once, deterministically, by probe order. Retest
// verdicts key to IDs, never to positions or titles.
let n = 0
const holes = probeResults.filter(Boolean).flatMap(r =>
  (r.holes || [])
    .filter(h => h.severity !== 'minor')
    .map(h => ({ id: `E${++n}`, layer: 'execution', target: r.target, ...h })))
log(`${holes.length} blocking/major holes across ${targets.length + 1} probes`)

if (holes.length === 0) {
  return await register({
    terminal_state: 'CLOSED',
    spec: 'framework/plan-hardening-v2-spec.md',
    plan_type: targeting.plan_type,
    goal_from_plan: goalFromPlan, goal_from_problem: goalFromProblem,
    premise_status: 'resolved', targets, findings: premiseFindings,
    note: 'No blocking or major execution holes found.',
  })
}

phase('Fix')

// S4 IS SPLIT IN TWO, and the split is load-bearing rather than cosmetic.
// Measured 2026-08-21 (run wf_a440ab49-405): a single agent asked to disposition 25 holes AND
// rewrite the plan in one response returned dispositions for E1-E8 and stopped. INVARIANT 4
// caught it and aborted, which is correct, but the stage could not complete on any plan
// yielding more than ~8 holes. "Disposition 25 holes" is an unbounded ask in the same family
// as "attack this plan"; "what happens to hole E9" is bounded and terminates.
//   S4a — one agent per hole, deciding ONLY that hole. ID coverage becomes structural: one
//         agent in, one disposition out, so there is no way to silently drop the tail.
//   S4b — one reviser that APPLIES the recorded edits and reviews nothing.
// One retry per hole. Measured across three runs: an invariant violation from a SINGLE agent
// discarded the entire downstream (retest, validate, persist) twice, ~2.6M tokens, to learn
// two things a bounded retry catches in-loop. The guarantee is unchanged — INVARIANT 4 below
// still aborts if a hole ends with no disposition — but one flaky agent no longer costs a run.
const decideOne = async (h) => {
  for (let attempt = 0; attempt < 2; attempt++) {
    const d = await agent(dispositionPrompt(h, planText),
      { label: attempt ? `fix:${h.id}:retry` : `fix:${h.id}`, phase: 'Fix', schema: DISPOSITION_SCHEMA })
    if (d && d.status) return { id: h.id, ...d }
  }
  return null
}
const decisions = await parallel(holes.map(h => () => decideOne(h)))

// INVARIANT 4 — ID coverage. A null here means that hole's agent died; the run must not
// proceed as though the hole were resolved. Silence is the failure mode being guarded.
const byId = new Map(decisions.filter(Boolean).map(d => [d.id, d]))
const missing = holes.filter(h => !byId.has(h.id)).map(h => h.id)
if (missing.length) throw new Error(`INVARIANT 4 (ID coverage) FAILED: no disposition for ${missing.join(', ')}`)

// INVARIANT 5 — an accepted finding without a written reason fails the run.
const unreasoned = [...byId.values()]
  .filter(d => d.status === 'accepted' && (!d.note || d.note.trim().length < 20))
  .map(d => d.id)
if (unreasoned.length) {
  throw new Error(`INVARIANT 5 (reason required) FAILED: accepted without a reason: ${unreasoned.join(', ')}`)
}

const toApply = [...byId.values()].filter(d => d.status === 'fixed')
  .map(d => ({ id: d.id, edit_instruction: d.edit_instruction || d.note }))
log(`${toApply.length} fixed, ${byId.size - toApply.length} accepted with reasons`)

// The reviser gets ONE bounded retry with the specific violation quoted back. A 2.30x plan is
// a correctable output, not a reason to discard 40 agents of upstream work — and the retry is
// bounded, so this cannot become v1's unbounded revise loop. The invariants still decide.
const checkMutation = (revised) => {
  const sig = (t) => t.split('\n').map(l => l.trim()).filter(l => l.length >= 25)
  const before = sig(planText)
  const after = new Set(sig(revised))
  const kept = before.filter(l => after.has(l)).length
  const retention = before.length ? kept / before.length : 1
  const budget = Math.round(Math.min(planText.length * MAX_GROWTH + toApply.length * PER_FIX_CHARS, planText.length * MAX_TOTAL_GROWTH))
  return { growth: revised.length / planText.length, retention, budget, length: revised.length,
           ok: retention >= MIN_RETENTION && revised.length <= budget && revised.trim().length >= MIN_PLAN_CHARS }
}
let revision = null
if (toApply.length) {
  let feedback = ''
  for (let attempt = 0; attempt < 2; attempt++) {
    const r = await agent(revisePrompt(planText, toApply) + feedback,
      { label: attempt ? 'revise:retry' : 'revise', phase: 'Fix', schema: REVISE_SCHEMA })
    if (r && r.revised_plan) {
      const m = checkMutation(r.revised_plan)
      revision = r
      if (m.ok) break
      feedback = `\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED BY THE GATE. It was ${m.length} chars ` +
        `against a budget of ${m.budget}, with ${(m.retention * 100).toFixed(0)}% of the original's ` +
        `substantive lines surviving verbatim (floor ${(MIN_RETENTION * 100).toFixed(0)}%). ` +
        `REPLACE weak text instead of APPENDING to it, and keep untouched sections byte-identical. ` +
        `Apply the same edits again, within budget.`
      log(`revise attempt ${attempt + 1} rejected (${m.growth.toFixed(2)}x, ${(m.retention * 100).toFixed(0)}% retention); retrying once`)
    }
  }
} else {
  revision = { revised_plan: planText, applied: [] }
}
if (!revision || !revision.revised_plan) throw new Error('revise stage returned no revised plan; aborting')
const fix = { revised_plan: revision.revised_plan, dispositions: [...byId.values()] }

// INVARIANT 1 again — the revised plan is a payload too, and this is exactly where v1 died.
if (fix.revised_plan.trim().length < MIN_PLAN_CHARS) {
  throw new Error(`INVARIANT 1 FAILED at fix stage: revised plan is ${fix.revised_plan.trim().length} chars.`)
}

// INVARIANT 6 — the revised plan must be a BOUNDED MUTATION of the original, not a
// reconstruction. Two checks that were originally conflated under one name, and separated
// on 2026-08-21 after the first live fire proved they measure different things:
//
//   (a) RETENTION — the anti-reconstruction guard, and the real one. Independent of how many
//       fixes were commissioned. Catches run-2's shape: a reviser paraphrases the plan, lands
//       inside any length budget, and the retest then judges an artifact nobody reviewed.
//   (b) ACCRETION — a budget on growth. Measured 2026-08-21: 25 commissioned fixes produced a
//       2.30x plan at 84% retention with identical section headers — a faithful edit, not a
//       re-authoring, yet a flat 1.15x cap aborted it. A ratio alone cannot tell "padded" from
//       "absorbed 25 real fixes," so the budget scales with the work actually commissioned.
//       It still aborts, because the spec names accretion as how v1 died: appended prose
//       becomes the next round's attack surface.
const inv6 = (() => {
  const revised = fix.revised_plan
  const sig = (t) => t.split('\n').map(l => l.trim()).filter(l => l.length >= 25)
  const before = sig(planText)
  const after = new Set(sig(revised))
  const kept = before.filter(l => after.has(l)).length
  const retention = before.length ? kept / before.length : 1
  const budget = Math.round(Math.min(planText.length * MAX_GROWTH + toApply.length * PER_FIX_CHARS, planText.length * MAX_TOTAL_GROWTH))
  return { growth: revised.length / planText.length, retention, budget, length: revised.length }
})()

if (inv6.retention < MIN_RETENTION) {
  throw new Error(
    `INVARIANT 6a (reconstruction) FAILED: only ${(inv6.retention * 100).toFixed(0)}% of the ` +
    `original plan's substantive lines survive verbatim (floor ${(MIN_RETENTION * 100).toFixed(0)}%). ` +
    `The reviser re-authored the plan rather than editing it; the retest would be run against ` +
    `an artifact nobody reviewed.`)
}
if (inv6.length > inv6.budget) {
  throw new Error(
    `INVARIANT 6b (accretion) FAILED: revised plan is ${inv6.length} chars against a budget of ` +
    `${inv6.budget} (${planText.length} original x ${MAX_GROWTH} + ${toApply.length} fixes x ` +
    `${PER_FIX_CHARS}). Retention was ${(inv6.retention * 100).toFixed(0)}%, so this is padding, ` +
    `not reconstruction. Replace weak text instead of appending caveats.`)
}
log(`INV6 ok: ${inv6.growth.toFixed(2)}x growth (budget ${(inv6.budget / planText.length).toFixed(2)}x), ${(inv6.retention * 100).toFixed(0)}% retention`)

const revised = fix.revised_plan
const diff = lineDiff(planText, revised)

phase('Retest')
const toRetest = holes.filter(h => byId.get(h.id).status === 'fixed')
const [retests, newHoles] = await parallel([
  () => parallel(toRetest.map(h => () =>
    agent(retestPrompt(h, revised), { label: `retest:${h.id}`, phase: 'Retest', schema: RETEST_SCHEMA })
      .then(v => ({ id: h.id, ...v })))),
  () => agent(newHolesPrompt(diff), { label: 'retest:new-holes-only', phase: 'Retest', schema: NEWHOLES_SCHEMA }),
])

const retestById = new Map((retests || []).filter(Boolean).map(r => [r.id, r]))
const findings = [
  ...premiseFindings.map(f => ({ ...f, status: 'resolved-at-gate' })),
  ...holes.map(h => {
    const d = byId.get(h.id)
    const r = retestById.get(h.id)
    return {
      id: h.id, layer: 'execution', target: h.target, severity: h.severity,
      claim: h.title, problem: h.problem, failure_scenario: h.failure_scenario,
      status: d.status, reason: d.note,
      retest: r ? r.verdict : 'not-yet-run', retest_evidence: r ? r.evidence : null,
    }
  }),
]

phase('Validate')
const claims = [
  ...findings.filter(f => f.layer === 'execution' && f.retest === 'still-fires').map(f => ({ text: f.claim + ' :: ' + f.failure_scenario, kind: 'unclosed hole' })),
  ...(newHoles && newHoles.new_holes ? newHoles.new_holes.filter(h => h.severity === 'blocking').map(h => ({ text: h.title + ' :: ' + h.problem, kind: 'new hole from the fix' })) : []),
].slice(0, 12)

const validations = await parallel(claims.map(c => () =>
  agent(validatePrompt(c.text, c.kind), { label: 'validate', phase: 'Validate', model: 'fable', schema: VALIDATION_SCHEMA })
    .then(v => ({ claim: c.text, kind: c.kind, ...v }))))

const stillFires = findings.filter(f => f.retest === 'still-fires')
const introduced = findings.filter(f => f.retest === 'fix-introduced-new')
const accepted = findings.filter(f => f.status === 'accepted')
const newBlocking = (newHoles && newHoles.new_holes ? newHoles.new_holes : []).filter(h => h.severity === 'blocking')

const terminal = (stillFires.length === 0 && introduced.length === 0 && newBlocking.length === 0)
  ? (accepted.length ? 'RESIDUAL' : 'CLOSED')
  : 'OPEN'

return await register({
  terminal_state: terminal,
  spec: 'framework/plan-hardening-v2-spec.md',
  plan_type: targeting.plan_type,
  plan_type_reason: targeting.plan_type_reason,
  goal_from_plan: goalFromPlan,
  goal_from_problem: goalFromProblem,
  goal_delta: premise.delta,
  premise_status: 'resolved',
  targets: targets.map(t => ({ key: t.key, generator: t.generator, question: t.question })),
  counts: {
    holes: holes.length, fixed: toRetest.length, accepted: accepted.length,
    closed: findings.filter(f => f.retest === 'closed').length,
    still_fires: stillFires.length, fix_introduced_new: introduced.length,
    new_blocking_from_fix: newBlocking.length,
  },
  findings,
  bounded_mutation: inv6,
  new_holes_from_fix: newHoles ? newHoles.new_holes : [],
  validations: validations.filter(Boolean),
  revised_plan: revised,
  outPath: cfg.outPath || null,
  harness_self_check: {
    premise_findings_at_gate: premiseFindings.length,
    premise_findings_late: 0,
    note: 'Healthy = premise findings surface at the gate. If premise-shaped findings only appear from the unscoped probe, goal extraction is too weak and is being rescued by luck.',
  },
})
