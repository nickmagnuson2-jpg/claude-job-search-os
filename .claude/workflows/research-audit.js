export const meta = {
  name: 'research-audit',
  description: 'Audit N systems against current best practices: fan-out Exa research per angle, adversarially validate every claim, synthesize per-system verdicts + redesign',
  whenToUse: 'Deciding whether existing systems/approaches are stale and what to change. Patterns #1 (fan-out) + #2 (adversarial verify). See framework/multi-agent-workflows.md.',
  phases: [
    { title: 'Research', detail: 'one agent per (system, angle) runs live web/Exa research' },
    { title: 'Validate', detail: 'adversarial refute-first validation of each angle\'s load-bearing claims', model: 'fable' },
    { title: 'Synthesize', detail: 'per-system verdict + redesign, then a final assembled doc' },
    { title: 'Cross-model verify', detail: '/verify on the final doc: reader review (Codex, Fable, Grok) + a Codex claim check, findings dispositioned into the doc' },
  ],
}

// REUSABLE TEMPLATE — no personal/subject data hardcoded; supply it via `args`. args = {
//   subject:  string   // 1-paragraph "who/what this serves" context every agent needs
//   systems:  [{ name, currentState, angles: [string] }]   // systems to audit + research angles
//   date:     string   // for the output filename
//   outDir?:  string   // default output/analysis
//   crossCutting?: { name, currentState, angles:[string] }  // optional Nth "system" spanning all
//   reader?:  string   // who reads the final doc, for /verify's reader review (default: derived from subject)
//   purpose?: string   // what that reader needs from it (default: decide keep / change / remove per system)
//   knownErrors?: string  // mistakes already known in the inputs (e.g. how currentState was written); passed to /verify
//   protect?: string   // what must not leak; omitted -> the reader review skips its confidentiality question and says so
//   skipVerify?: boolean  // true skips the cross-model phase. Logged loudly and recorded in the doc's Coverage; never silent
// }

const cfg = typeof args === 'string' ? JSON.parse(args) : (args || {})
const SUBJECT = cfg.subject || 'No subject context supplied.'
const SYSTEMS = (cfg.systems || []).concat(cfg.crossCutting ? [cfg.crossCutting] : [])
if (!SYSTEMS.length) throw new Error('research-audit: args.systems is required (fail loud).')
// `date` is interpolated into the output filename and has NO default. Workflow scripts cannot call
// new Date() (it throws, so runs stay resumable), so it can only come from args. Without this guard
// the run completes and writes `undefined-best-practices-audit.md` — a silent success with a
// corrupt name, which is worse than an abort. Found by audit 2026-08-21.
if (!cfg.date) throw new Error('research-audit: args.date is required (it lands in the output filename; fail loud).')

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

// Bound by framework/review-findings-protocol.md. Two rules bite here:
// `uncertain` is never a pass, and `dropped` is not a disposition. Before 2026-08-14 the
// synthesizer was told "refuted claims dropped" and the evidence table was "confirmed/corrected
// only", so a refuted claim and an unverifiable one both vanished — and a vanished claim is
// indistinguishable from one that was never raised. Every checked claim now carries a
// disposition forward into the ledger, including the ones that did not survive.
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
          verdict: {
            type: 'string',
            enum: ['confirmed', 'refuted', 'uncertain'],
            description: 'confirmed = a source checked THIS run proves it. refuted = a source checked THIS run disproves it. uncertain = could not establish either way. Default to uncertain over guessing; uncertain is NEVER treated as confirmed.',
          },
          researcher_confidence: {
            type: 'string',
            enum: ['High', 'Med', 'Low', 'none'],
            description: "What the researching agent rated it. 'none' if you raised the claim yourself. This is an input you are re-deciding, not a rating you inherit.",
          },
          confidence_disagreement: {
            type: 'string',
            description: "One line on why your verdict diverges from the researcher's confidence, or 'agrees'. A set where every row reads 'agrees' is a validator that did not validate.",
          },
          disposition: {
            type: 'string',
            enum: ['carried', 'corrected', 'rejected', 'rejected - could not verify'],
            description: 'What happens to it downstream. Every claim gets one. There is no silent drop: a refuted claim is `rejected` and an uncertain one is `rejected - could not verify`, and both still appear in the ledger.',
          },
          reason: { type: 'string' },
          corrected_claim: { type: 'string' },
          sources: { type: 'array', items: { type: 'string' } },
        },
        required: ['claim', 'verdict', 'researcher_confidence', 'confidence_disagreement', 'disposition', 'reason'],
      },
    },
    surviving_recommendation: { type: 'string' },
  },
  required: ['system', 'checked', 'surviving_recommendation'],
}

function short(s) { return s.slice(0, 32).replace(/[^a-zA-Z0-9]+/g, '-') }

// NO SILENT CAPS. Until 2026-10-11 the three hand-offs below were cut with bare .slice(): the
// validator got the first 6,000 chars of research that ran 23,000-34,000 (about 3 of each
// angle's 8-10 findings), and the synthesizer got the first 14,000 of a 50,000-75,000 payload
// in which the first validator verdict began after char 23,000. Measured on run wf_8056843a-238:
// 53 claims were validated and NONE reached a synthesizer, and 5 confident sections were
// written from half of one angle's raw research. The cap is now far above any real payload,
// and if it ever bites it says so in the log, in the prompt, and in the doc's Coverage section.
const CAP = 400000
const truncations = []
function capped(text, what) {
  if (text.length <= CAP) return text
  truncations.push(what + ': ' + CAP + ' of ' + text.length + ' chars passed on')
  log('WARNING: TRUNCATED ' + what + ': only ' + CAP + ' of ' + text.length + ' chars passed on.')
  return text.slice(0, CAP) + '\n[TRUNCATED HERE: ' + CAP + ' of ' + text.length + ' chars shown. Say so in your output; do not treat this as complete.]'
}

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
FINDINGS IN THIS RESEARCH: ${((research && research.findings) || []).length}. Every one of them gets a row in 'checked'; a finding with no row is a silent drop. Claims you raise yourself are additional rows.

RESEARCH TO VALIDATE (JSON): ${capped(JSON.stringify(research), 'research -> validator, ' + item.system + ' / ' + short(item.angle))}

For each claim: verdict (confirmed/refuted/uncertain), a reason grounded in a source you checked THIS run, and (if not confirmed) a corrected_claim.

You also re-rate confidence rather than inheriting it. Record researcher_confidence (what the research agent claimed, or 'none' if you raised the claim yourself) and confidence_disagreement (one line on why yours differs, or 'agrees').

Every claim gets a disposition and NONE are dropped: carried, corrected, rejected, or 'rejected - could not verify'. Do not silently omit a claim you refuted or could not check — a claim that disappears here is indistinguishable from one nobody raised, and only one of those is a decision. uncertain is never a pass; default to it over guessing.

Then give surviving_recommendation built from the carried and corrected claims only. Return ONLY the structured object.`
}

function synthPrompt(system, items) {
  // validation precedes research in each entry: if anything is ever cut, it is raw research, not the verdicts
  const payload = items.map((v) => ({ angle: v.item.angle, validation: v.verdict, research: v.research }))
  const nChecked = items.reduce((n, v) => n + (((v.verdict && v.verdict.checked) || []).length), 0)
  const nFindings = items.reduce((n, v) => n + (((v.research && v.research.findings) || []).length), 0)
  return `You are the synthesizer for ONE system. Build your recommendation from the claims dispositioned 'carried' or 'corrected' ONLY — but do not let the others vanish, they belong in the Findings Ledger below. Write a self-contained markdown SECTION (start with '## ${system.name}').

SUBJECT: ${SUBJECT}
SYSTEM: ${system.name}
CURRENT IMPLEMENTATION: ${system.currentState}
YOU ARE RECEIVING ${items.length} angle(s), ${nFindings} research finding(s) and ${nChecked} validator-checked claim(s). If what you can read below is fewer than that, STOP and say so as the first line of your section instead of writing a verdict.

VALIDATED RESEARCH (JSON): ${capped(JSON.stringify(payload), 'validated research -> synthesizer, ' + system.name)}

Subsections: **BLUF** (is it stale + the single most important change); **Verdict: KEEP/EVOLVE/REPLACE** (with justification tied to scale); **Evidence table** (Claim | Approach | Confidence | Source URL, carried/corrected only, real URLs); **Proposed redesign** (concrete, right-sized); **Where it does NOT fit / risks** (non-empty); **Migration steps** (ordered, each independently shippable); then **Findings Ledger** (see below). Be decisive. Return ONLY the markdown section.

The Findings Ledger is mandatory and the section is not done without it. One row per checked claim, INCLUDING every rejected one, per framework/review-findings-protocol.md:

| Claim | Researcher confidence | Validator verdict | Disposition | Why |

Count the rows against the number of claims checked; if the ledger is shorter, something was dropped and you must go back and add it. A ledger where confidence and verdict agree on every row, or one with zero rejections, means no independent validation happened and you should say so in the BLUF rather than present the section as validated.`
}

function finalPrompt(sectionsText, path, coverage) {
  return `Assemble the final recommendations document and save it with Write to ${path}.

Structure: '# Best-Practices Audit & Proposed Redesign'; a 'Last updated' line; '## Executive Summary' (a System | Verdict | Headline change | Effort table + 3-5 cross-cutting bullets); '## How to read this'; '## Coverage' (see below); the system sections VERBATIM below; '## Open questions' (the decisions the owner must make); '## Sources' (deduplicated real URLs).

The '## Coverage' section states what this audit did NOT cover, in the same sentence as any claim about what it did: the angles that returned nothing and the systems skipped for lack of surviving research, both listed below. If both are empty, say "all N angles across M systems returned research" with the real numbers. A document that reports only what it found reads as complete regardless of how much it missed.

COVERAGE FACTS (use verbatim, do not soften):
${coverage}

SYSTEM SECTIONS (verbatim, in order):
${sectionsText}

After writing, return a 4-6 sentence executive summary (plain text). Return that summary only.`
}

// ---- Run -------------------------------------------------------------------
phase('Research')
log(`Auditing ${SYSTEMS.length} systems across ${ANGLES.length} research angles (research -> validate -> synthesize).`)

const validated = await pipeline(
  ANGLES,
  (item) => agent(researchPrompt(item), { label: `research:${item.system}:${short(item.angle)}`, phase: 'Research', schema: FINDINGS_SCHEMA }),
  (research, item) =>
    agent(validatePrompt(research, item), { label: `verify:${item.system}:${item.idx}`, phase: 'Validate', model: 'fable', schema: VERDICT_SCHEMA })
      .then((verdict) => ({ research, verdict, item })),
)

// A pipeline item that throws resolves to null, so `.filter(Boolean)` used to make a dead
// angle indistinguishable from one that was never requested — the same silent-drop defect the
// validator schema closes, one layer up. Name the casualties; a count alone reads as coverage.
const live = validated.filter(Boolean)
const lostAngles = ANGLES.filter((_, i) => !validated[i]).map((a) => `${a.system}: ${a.angle}`)
if (lostAngles.length) {
  log(`⚠️ ${lostAngles.length} of ${ANGLES.length} angle(s) returned nothing and are NOT covered below:`)
  for (const a of lostAngles) log(`   - ${a}`)
}

// A validator that returns fewer rows than the research had findings has dropped some, whatever
// the reason. Name each shortfall; it goes into the doc's Coverage section verbatim.
const shortfalls = live
  .map((r) => ({ r, f: ((r.research && r.research.findings) || []).length, c: ((r.verdict && r.verdict.checked) || []).length }))
  .filter((x) => x.c < x.f)
  .map((x) => `${x.r.item.system} / ${short(x.r.item.angle)}: ${x.c} of ${x.f} findings validated`)
if (shortfalls.length) {
  log(`WARNING: ${shortfalls.length} angle(s) had findings the validator did not check:`)
  for (const s of shortfalls) log(`   - ${s}`)
}

const bySystem = {}
for (const r of live) { (bySystem[r.item.system] || (bySystem[r.item.system] = [])).push(r) }

phase('Synthesize')
// A system whose angles all failed has NO research behind it. Synthesizing it anyway produces a
// confident section built from nothing, which is worse than a missing section because it reads
// identically to a real one. Skip it and say so.
const starved = SYSTEMS.filter((s) => !(bySystem[s.name] || []).length).map((s) => s.name)
if (starved.length) log(`⚠️ No surviving research for: ${starved.join(', ')} — these are SKIPPED, not synthesized.`)
const researched = SYSTEMS.filter((s) => (bySystem[s.name] || []).length)

const rawSections = await parallel(
  researched.map((s) => () => agent(synthPrompt(s, bySystem[s.name]), { label: `synth:${s.name}`, phase: 'Synthesize' })),
)
const sections = rawSections.filter(Boolean)
// Third instance of the same class: a synthesizer that dies takes its whole section with it, and
// the assembled doc would simply not mention that system. Researched-but-unsynthesized is the
// most misleading of the three, because the research exists and the reader never learns it.
const unsynthesized = researched.filter((_, i) => !rawSections[i]).map((s) => s.name)
if (unsynthesized.length) log(`⚠️ Research succeeded but synthesis FAILED for: ${unsynthesized.join(', ')}`)

const outPath = `${cfg.outDir || 'output/analysis'}/${cfg.date}-best-practices-audit.md`
const sectionsText = capped(sections.join('\n\n---\n\n'), 'system sections -> final doc')
const coverage = [
  `Angles requested: ${ANGLES.length}. Angles that returned research: ${live.length}.`,
  lostAngles.length ? `Angles that returned NOTHING (not covered): ${lostAngles.join('; ')}` : 'No angles were lost.',
  starved.length ? `Systems SKIPPED for lack of surviving research: ${starved.join(', ')}` : 'No systems were skipped.',
  unsynthesized.length
    ? `Systems where research succeeded but SYNTHESIS FAILED (no section below, despite research existing): ${unsynthesized.join(', ')}`
    : 'Every researched system was synthesized.',
  shortfalls.length ? `Findings the validator did NOT check: ${shortfalls.join('; ')}` : 'Every research finding has a validator row.',
  truncations.length ? `Hand-offs that were TRUNCATED: ${truncations.join('; ')}` : 'No hand-off between agents was truncated.',
  cfg.skipVerify ? 'Cross-model verification (/verify) was SKIPPED by args.skipVerify.' : 'Cross-model verification (/verify) is attempted after this document is written. If no section headed "Cross-model verification" follows the Sources, it did not run and nothing here was checked by a second model.',
].join('\n')
const execSummary = await agent(finalPrompt(sectionsText, outPath, coverage), { label: 'synthesize:final-doc', phase: 'Synthesize' })

// ---- Cross-model verify ------------------------------------------------------
// Added 2026-10-11 at the owner's direction: /verify did not exist when this workflow was
// written. Everything above is one model family checking itself. This phase runs the two
// /verify tools against the finished document and lands every finding IN the document with a
// disposition, because a report nothing reads is the defect /verify exists to prevent.
const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    ran_reader_review: { type: 'boolean' },
    ran_claim_check: { type: 'boolean' },
    failure: { type: 'string', description: 'Exact error text if either tool did not run or did not finish; empty string otherwise' },
    reports: { type: 'array', items: { type: 'string' }, description: 'Paths of the report files the tools wrote' },
    findings_total: { type: 'number' },
    findings_confirmed: { type: 'number' },
    findings_rejected: { type: 'number' },
    contested_verdicts: { type: 'array', items: { type: 'string' }, description: 'System verdicts a second model disputes, one line each, or empty' },
    appended_section: { type: 'boolean', description: 'true only if the Cross-model verification section was written into the document' },
    summary: { type: 'string' },
  },
  required: ['ran_reader_review', 'ran_claim_check', 'failure', 'reports', 'findings_total', 'findings_confirmed', 'findings_rejected', 'contested_verdicts', 'appended_section', 'summary'],
}

function verifyPhasePrompt(path) {
  const reader = cfg.reader || ('The owner of the systems audited. ' + SUBJECT.slice(0, 400))
  const purpose = cfg.purpose || 'Decide, for each audited system, whether to keep it, change it or remove parts of it, and which change to make first.'
  return `Run the project's /verify tooling against a finished audit document, then record the results inside that document. The document is ${path}. Work from the repository root.

STEP 1. Read ${path} in full. From its Executive Summary table write one numbered claim per system, in the form "<system>: verdict <KEEP|EVOLVE|REPLACE>; headline change: <change>". Add up to five more claims for the factual statements the recommendations most depend on (figures about the owner's own records, and the strongest external-evidence claims).

STEP 2. Reader review (Codex, Fable and Grok in parallel). Run, with the maximum Bash timeout (600000 ms):
  PYTHONIOENCODING=utf-8 python3 tools/reader_review.py ${path} --reader "<READER>" --purpose "<PURPOSE>"${cfg.protect ? ' --protect "<PROTECT>"' : ''}${cfg.knownErrors ? ' --known-errors "<KNOWN ERRORS>"' : ''}
READER: ${reader}
PURPOSE: ${purpose}${cfg.protect ? '\nPROTECT: ' + cfg.protect : ''}${cfg.knownErrors ? '\nKNOWN ERRORS: ' + cfg.knownErrors : ''}
Pass each value as ONE shell argument: replace any double quote inside a value with a single quote before quoting it. Run with --dry-run first to confirm the command parses, then run it for real.

STEP 3. Claim check (Codex). Run, with the maximum Bash timeout:
  PYTHONIOENCODING=utf-8 python3 tools/codex_verify.py --target "best-practices audit ${cfg.date}: per-system verdicts and the evidence they rest on" --paths ${path} --claim "<claim 1>" --claim "<claim 2>" ...${cfg.knownErrors ? ' --known-errors "<KNOWN ERRORS>"' : ''} --question "What is now redundant, and what shape would let some of this be deleted? What is the most upstream cause of each problem named, and would fixing it there remove the downstream fixes?"

If a command times out, do not assume it failed and do not assume it succeeded: look for the report file it names (reports land in output/analysis/), and if it is absent re-run once in the background and wait for the file. If a tool cannot run at all (not installed, not authenticated, network refused), STOP that step, set its ran_ flag to false, and put the exact error text in failure. Never invent a finding and never describe a report you did not read.

STEP 4. Read every report the tools wrote. Apply framework/review-findings-protocol.md to each finding before it goes anywhere: open ${path}, quote the line the finding is about, and give it one verdict (CONFIRMED / REFUTED / COULD NOT VERIFY) and your own severity. COULD NOT VERIFY is never CONFIRMED. Discount a second model's claims about this machine's environment (scheduled jobs, network, installed tools); do not discount its reasoning about the document.

STEP 5. Append ONE section to the end of ${path} with the Edit tool (append only; change nothing else in the document, and do not rewrite any verdict):

## Cross-model verification
- Which tools ran, on what date argument (${cfg.date}), and the report paths. If a tool did not run, say so here in the first line.
- **Contested verdicts**: each system verdict a second model disputes, with its reason and your verdict on that dispute. Write "None" if none.
- **Findings Ledger**: | # | Model | Finding | Its severity | My severity | Verdict | Disposition | Why | with one row per finding, INCLUDING every rejected one. Count the rows against the findings in the reports.
- **What a reader should do differently because of this section**: at most five lines.

Return ONLY the structured object. appended_section is true only if you wrote the section and re-read the end of the file to confirm it is there.`
}

let verify = null
if (cfg.skipVerify) {
  log('WARNING: Cross-model verify SKIPPED (args.skipVerify). The document says so in its Coverage section.')
} else {
  phase('Cross-model verify')
  verify = await agent(verifyPhasePrompt(outPath), { label: 'verify:cross-model', phase: 'Cross-model verify', schema: VERIFY_SCHEMA })
  if (!verify) log('WARNING: the cross-model verify agent returned nothing. The document has NO Cross-model verification section.')
  else if (!verify.ran_reader_review || !verify.ran_claim_check || !verify.appended_section) {
    log(`WARNING: cross-model verify is INCOMPLETE: reader review ran=${verify.ran_reader_review}, claim check ran=${verify.ran_claim_check}, section appended=${verify.appended_section}. ${verify.failure}`)
  }
}

return {
  outPath,
  angles: ANGLES.length,
  validated: live.length,
  sections: sections.length,
  lostAngles,
  skippedSystems: starved,
  validatorShortfalls: shortfalls,
  truncations,
  execSummary,
  verify: cfg.skipVerify ? 'SKIPPED by args.skipVerify' : (verify || 'FAILED: verify agent returned nothing'),
}
