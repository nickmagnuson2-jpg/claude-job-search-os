// Test driver for .claude/workflows/plan-hardening.js
//
// Executes the real workflow script with the workflow DSL globals stubbed, so the
// invariants in that file are exercised for real rather than asserted about by regex.
// Each scenario deliberately breaks ONE invariant; the Python suite asserts the run
// FAILS. A driver that only ran the happy path would prove nothing.
//
// Usage: node plan_hardening_driver.mjs <scenario>   -> prints JSON to stdout

import { readFile } from 'node:fs/promises'
import path from 'node:path'

const scenario = process.argv[2]
const WF = path.resolve(process.cwd(), '.claude/workflows/plan-hardening.js')

const GOOD_PLAN = '# A Plan\n' + ('This is a real plan line with enough substance to clear the floor.\n'.repeat(20))

const calls = []

function goal(tag) {
  return { goal: `goal-${tag}`, success_observation: 'x improves', failure_observation: 'x does not improve', beneficiary: 'the operator' }
}

// Per-scenario overrides of what each labelled agent returns.
const S = {
  happy_no_holes: {},
  payload_floor: { planText: 'too short' },
  blindness: { contextIsPlan: true },
  premise_open: { premise: { delta: 'material', delta_explanation: 'aimed elsewhere', goal_is_right: 'no', premise_status: 'open', findings: [{ claim: 'wrong aim', failure_scenario: 'ships the wrong thing' }] } },
  untypeable: { plan_type: 'untypeable' },
  reason_required: { withHoles: true, dispositions: (h) => h.map(x => ({ id: x.id, status: 'accepted', note: 'ok' })) },
  // one hole's agent dies -> that id has no disposition -> INV4 must abort
  id_coverage: { withHoles: true, dispositions: (h) => h.slice(1).map(x => ({ id: x.id, status: 'fixed', note: 'changed the thing' })) },
  fix_payload_floor: { withHoles: true, revisedPlan: 'tiny' },
  full_retest: { withHoles: true },
  // REGRESSION for the measured 2026-08-21 failure: 25 holes killed the single-agent fix
  // stage at E8. With S4a one-agent-per-hole this must complete.
  many_holes: { withHoles: true, holeCount: 25 },
  persist_both: { withHoles: true, registerPath: 'out/reg.json', outPath: 'out/plan.md' },
  persist_none: { withHoles: true },
  // the write agent dies: the register must NOT claim the result is durable
  persist_fails: { withHoles: true, registerPath: 'out/reg.json', persistFails: true },
  // 25 real fixes legitimately grow the plan. Retention stays high; the scaled budget must allow it.
  // Regression for the 2026-08-21 false positive: 2.30x at 84% retention was aborted by a flat 1.15x.
  many_holes_grown: { withHoles: true, holeCount: 25, grownPlan: true },
  // one hole's agent fails once then succeeds: the run must recover, not abort
  flaky_hole: { withHoles: true, flakyHole: 'E1' },
  // the reviser blows the budget once, then complies after the violation is fed back
  flaky_revise: { withHoles: true, flakyRevise: true },
  // INVARIANT 6 — the reviser must EDIT the plan, not re-author it.
  // reconstructed: same ballpark length, but none of the original's lines survive
  // (the measured v1 run-2 shape: plan silently renamed/paraphrased).
  reconstructed_plan: { withHoles: true, revisedPlan: '# A Plan\n' + Array.from({ length: 20 }, (_, i) => `Wholly rewritten replacement sentence number ${i} bearing no resemblance.`).join('\n') },
  // bloated: every original line survives, but the reviser padded past the growth budget.
  bloated_plan: { withHoles: true, revisedPlan: null },
}[scenario]

if (!S) { console.log(JSON.stringify({ ok: false, error: `unknown scenario ${scenario}` })); process.exit(0) }

const planText = S.planText !== undefined ? S.planText : GOOD_PLAN

if (scenario === 'bloated_plan') S.revisedPlan = GOOD_PLAN.repeat(6)  // 6x, past even the fix-scaled budget

const HOLES = S.holeCount
  ? Array.from({ length: S.holeCount }, (_, i) => ({ title: `h${i}`, severity: i % 2 ? 'major' : 'blocking', problem: `p${i}`, failure_scenario: `f${i}`, suggested_fix: `s${i}` }))
  : [
      { title: 'h1', severity: 'blocking', problem: 'p1', failure_scenario: 'f1', suggested_fix: 's1' },
      { title: 'h2', severity: 'major', problem: 'p2', failure_scenario: 'f2', suggested_fix: 's2' },
      { title: 'ignored', severity: 'minor', problem: 'p3', failure_scenario: 'f3', suggested_fix: 's3' },
    ]

globalThis.args = {
  planText,
  context: S.contextIsPlan ? planText : 'A domain context paragraph for the test.',
  minPlanChars: 500,
  registerPath: S.registerPath,
  outPath: S.outPath,
}

globalThis.phase = (t) => calls.push({ kind: 'phase', t })
globalThis.log = (m) => calls.push({ kind: 'log', m })

globalThis.parallel = async (thunks) => {
  const out = []
  for (const t of thunks) { try { out.push(await t()) } catch { out.push(null) } }
  return out
}

globalThis.agent = async (prompt, opts = {}) => {
  const label = opts.label || ''
  calls.push({ kind: 'agent', label })
  if (label.startsWith('goal:')) return goal(label)
  if (label === 'premise-gate') {
    return S.premise || { delta: 'immaterial', delta_explanation: 'same', goal_is_right: 'yes', premise_status: 'resolved', findings: [] }
  }
  if (label === 'target-generation') {
    return {
      plan_type: S.plan_type || 'guard',
      plan_type_reason: 'because',
      targets: [{ key: 't1', generator: 'stated-constraints', question: 'q', what_counts_as_a_hole: 'w' }],
    }
  }
  if (label.startsWith('probe:')) {
    return { target: label.slice(6), holes: S.withHoles && label === 'probe:t1' ? HOLES : [] }
  }
  // S4a: one agent PER HOLE. The driver mirrors the real ID assignment (blocking/major only,
  // numbered in probe order) so scenarios can target a specific hole's disposition.
  if (label.startsWith('fix:')) {
    const id = label.slice(4).replace(/:retry$/, '')
    if (S.flakyHole === id && !label.endsWith(':retry')) return null
    const withIds = HOLES.filter(h => h.severity !== 'minor').map((h, i) => ({ ...h, id: `E${i + 1}` }))
    if (S.dispositions) {
      const d = S.dispositions(withIds).find(x => x.id === id)
      return d ? { status: d.status, note: d.note, edit_instruction: 'replace X with Y' } : null
    }
    return { status: 'fixed', note: 'changed the thing materially', edit_instruction: 'replace X with Y' }
  }
  // S4b: the reviser applies edits and reviews nothing.
  if (label === 'revise' || label === 'revise:retry') {
    if (S.flakyRevise) {
      return label === 'revise'
        ? { revised_plan: GOOD_PLAN.repeat(6), applied: ['E1'] }
        : { revised_plan: GOOD_PLAN + '\nA compliant fix was applied here.\n', applied: ['E1', 'E2'] }
    }
    // a faithful edit that ABSORBS many fixes: every original line kept, substantive text added
    const grown = GOOD_PLAN + Array.from({ length: 30 }, (_, i) => `Added substantive remediation sentence ${i} applying a commissioned fix.`).join('\n')
    return {
      revised_plan: S.revisedPlan !== undefined ? S.revisedPlan : (S.grownPlan ? grown : GOOD_PLAN + '\nA fix was applied here.\n'),
      applied: ['E1', 'E2'],
    }
  }
  if (label.startsWith('persist:')) return S.persistFails ? null : `wrote file, verified byte count`
  if (label.startsWith('retest:new')) return { new_holes: [] }
  if (label.startsWith('retest:')) return { verdict: 'closed', evidence: 'quoted text' }
  if (label === 'validate') return { verdict: 'CONFIRMED', evidence: 'e', consequence: 'c' }
  return 'unhandled'
}

// The workflow script uses TOP-LEVEL RETURN, which the real harness supports by wrapping
// the source in an async function. An ES `import()` cannot: it throws "Illegal return
// statement" before a single invariant runs. So the driver wraps it the same way the
// harness does — otherwise this suite would pass vacuously on a syntax error.
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const src = (await readFile(WF, 'utf8')).replace(/^export const meta/m, 'const meta')

try {
  const fn = new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', src)
  const result = await fn(globalThis.args, globalThis.agent, globalThis.parallel, globalThis.phase, globalThis.log)
  console.log(JSON.stringify({ ok: true, result, calls: calls.filter(c => c.kind === 'agent').map(c => c.label) }))
} catch (e) {
  console.log(JSON.stringify({ ok: false, error: String(e && e.message || e), calls: calls.filter(c => c.kind === 'agent').map(c => c.label) }))
}
