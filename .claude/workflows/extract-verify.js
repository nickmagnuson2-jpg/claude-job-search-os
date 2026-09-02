export const meta = {
  name: 'extract-verify',
  description: 'Extract structured records from a corpus, each independently re-derived by a fresh-context verifier (anti-anchoring)',
  whenToUse: 'Turning a messy corpus (notes/logs/docs) into a clean labeled dataset with provenance. Pattern #4 (extract -> verify). See framework/multi-agent-workflows.md.',
  phases: [
    { title: 'Preferences', detail: 'optional: extract global context from preference sources' },
    { title: 'Extract', detail: 'one agent per batch reads each entity\'s sources -> structured record' },
    { title: 'Verify', detail: 'a FRESH agent re-derives each label from sources (never sees extractor reasoning)' },
    { title: 'Synthesize', detail: 'merge verified records -> ledger + summary files' },
  ],
}

// REUSABLE TEMPLATE — no personal/subject data hardcoded; everything domain-specific
// arrives via `args`. See framework/multi-agent-workflows.md. args = {
//   manifestPath: string   // JSON array of entities; each has sources to read
//   count: number          // number of entities (batching)
//   batchSize?: number      // default 8
//   taxonomy: string        // label set + definitions the extractor/verifier apply
//   denylist: string        // sealed/off-limits sources, carried verbatim into every prompt
//   extractionGuidance: string  // domain rules (citation format, contradiction rule, etc.)
//   recordShape: string     // prose description of the fields to extract per entity
//   globals?: object        // {name: path} of shared context files agents may read
//   preferenceSources?: [{label, instruction}]  // optional Phase-Preferences agents
//   outDir: string          // where synthesis writes outputs
// }
// NOTE: a `date` arg was documented here until 2026-08-21 and was never read by this script
// (outputs are fixed filenames). Removed rather than implemented — an advertised arg the code
// ignores is documentation posing as a contract.

const cfg = typeof args === 'string' ? JSON.parse(args) : (args || {})
const BATCH = cfg.batchSize || 8
const COUNT = cfg.count || 0
const DENY = cfg.denylist || 'None specified.'
const GUIDE = cfg.extractionGuidance || ''
const TAX = cfg.taxonomy || ''
const GLOBALS = cfg.globals || {}

if (!cfg.manifestPath || !COUNT) {
  throw new Error('extract-verify: manifestPath and count are required (fail loud, per pre-flight guard).')
}

const RECORD_SCHEMA = {
  type: 'object',
  properties: {
    records: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          entity: { type: 'string' },
          label: { type: 'string', description: 'One of the taxonomy labels' },
          justification: { type: 'string' },
          citations: { type: 'array', items: { type: 'object', properties: { quote: { type: 'string' }, source: { type: 'string' } }, required: ['quote', 'source'] } },
          engagement_events: { type: 'array', items: { type: 'object', properties: { event: { type: 'string' }, date: { type: 'string' }, source: { type: 'string' } }, required: ['event', 'source'] } },
          interest_level: { type: 'string', enum: ['high', 'medium', 'low', 'none', 'unknown'] },
          interest_evidence: { type: 'string', description: 'STATED interest in the subject\'s words, not effort/dossier existence' },
          nick_reasoning: { type: 'string' },
          fit_features: { type: 'object', properties: { sector: { type: 'string' }, stage: { type: 'string' }, role_shape: { type: 'string' }, thesis_fit_note: { type: 'string' } } },
          declined_after_engagement: { type: 'boolean' },
          confidence: { type: 'string', enum: ['High', 'Med', 'Low'] },
          sources_read: { type: 'array', items: { type: 'string' } },
          needs_review: { type: 'boolean', description: 'true if a claim lacked a quotable source (write as TODO not assertion)' },
        },
        required: ['entity', 'label', 'justification', 'confidence'],
      },
    },
  },
  required: ['records'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          entity: { type: 'string' },
          agrees: { type: 'boolean' },
          verifier_label: { type: 'string' },
          reason: { type: 'string' },
          citation: { type: 'string', description: 'A verbatim quoted span supporting the verifier label' },
        },
        required: ['entity', 'agrees', 'verifier_label', 'reason'],
      },
    },
  },
  required: ['verdicts'],
}

const DENY_BLOCK = `SEALED-SOURCE DENY-LIST (hard rule): ${DENY}
You may read ONLY the file paths enumerated for your entities in the manifest, plus the named global context files. NEVER follow wikilinks ([[...]]) or references to any path not explicitly listed. If a source is on the deny-list or not enumerated, do not read it and do not include its content.`

const GLOBALS_BLOCK = Object.keys(GLOBALS).length
  ? `Global context files you MAY read: ${Object.entries(GLOBALS).map(([k, v]) => `${k}=${v}`).join(', ')}.`
  : ''

function extractPrompt(start, end) {
  return `You extract a structured record per entity from its enumerated sources. ${DENY_BLOCK}

Read the manifest JSON at ${cfg.manifestPath}. Process ONLY entities at array indices [${start}, ${end}). For each entity, read its listed sources (company_note, dossier_dir, debriefs) and the global context files if relevant. ${GLOBALS_BLOCK}

TAXONOMY (assign exactly one label): ${TAX}

WHAT TO EXTRACT PER ENTITY: ${cfg.recordShape || 'the taxonomy label + justification + evidence.'}

RULES: ${GUIDE}

Every engagement event and interest claim needs a VERBATIM quoted span + its source path. If you cannot quote a source for a claim, set needs_review=true and write it as a TODO, do NOT assert it. Scope: entities whose only source is a bare pipeline stage (no note/dossier/debrief) get a deterministic record from the stage alone — do not invent detail. Return ONLY the structured object with a record for every entity in your range.`
}

function verifyPrompt(start, end, proposed) {
  return `You are a FRESH verifier. You did NOT extract these records and must NOT be shown the extractor's reasoning. ${DENY_BLOCK}

Read the manifest JSON at ${cfg.manifestPath}, entities at indices [${start}, ${end}). For each, INDEPENDENTLY derive its outcome label from its sources, applying:
TAXONOMY: ${TAX}
RULES: ${GUIDE}

Then compare to the extractor's PROPOSED label (given below as entity->label, with NO reasoning). Report agrees (true/false), your own verifier_label, a reason, and a verbatim citation. Do not just ratify — re-derive first, then compare.

PROPOSED LABELS: ${JSON.stringify(proposed).slice(0, 4000)}

Return ONLY the structured object.`
}

// ---- Run -------------------------------------------------------------------
const NBATCH = Math.ceil(COUNT / BATCH)
const batches = []
for (let i = 0; i < NBATCH; i++) batches.push({ i, start: i * BATCH, end: Math.min((i + 1) * BATCH, COUNT) })

// Phase Preferences (optional, parallel) — global revealed-preference context.
let preferences = []
if (cfg.preferenceSources && cfg.preferenceSources.length) {
  phase('Preferences')
  log(`Extracting revealed preferences from ${cfg.preferenceSources.length} source groups...`)
  preferences = (await parallel(
    cfg.preferenceSources.map((ps) => () =>
      agent(`${DENY_BLOCK}\n\n${ps.instruction}\n\nReturn concise prose findings (drawn-to signals, dealbreakers, self-decline patterns, thesis evolution) with a verbatim quote + source per claim.`,
        { label: `preferences:${ps.label}`, phase: 'Preferences' }),
    ),
  )).filter(Boolean)
}

// Phase Extract -> Verify (pipeline, no barrier).
phase('Extract')
log(`Extracting + verifying ${COUNT} entities across ${NBATCH} batches...`)
const verified = await pipeline(
  batches,
  (b) => agent(extractPrompt(b.start, b.end), { label: `extract:b${b.i}`, phase: 'Extract', schema: RECORD_SCHEMA }),
  (extracted, b) => {
    const records = (extracted && extracted.records) || []
    const proposed = {}
    records.forEach((r) => { proposed[r.entity] = r.label })
    return agent(verifyPrompt(b.start, b.end, proposed), { label: `verify:b${b.i}`, phase: 'Verify', schema: VERIFY_SCHEMA })
      .then((v) => ({ records, verdicts: (v && v.verdicts) || [] }))
  },
)

// Merge extractor + verifier (JS, deterministic). Verifier is authoritative on disagreement; flag it.
const merged = []
for (const batch of verified.filter(Boolean)) {
  const vByEntity = {}
  batch.verdicts.forEach((v) => { vByEntity[v.entity] = v })
  for (const rec of batch.records) {
    const v = vByEntity[rec.entity]
    const disagreement = v ? !v.agrees : false
    merged.push({
      ...rec,
      final_label: v && !v.agrees ? v.verifier_label : rec.label,
      verifier_label: v ? v.verifier_label : null,
      verifier_reason: v ? v.reason : null,
      disagreement,
    })
  }
}

// Phase Synthesize — write the outputs.
// NOTE: the returned `merged` array is the AUTHORITATIVE output — the caller should
// persist it to JSON and regenerate any tabular ledger deterministically from it.
// The synthesized .md below is a convenience summary; on large corpora the inline
// record payload is capped (below) and the .md may cover only a prefix. Do not treat
// the .md as complete for >~20 rich records — regenerate from the JSON instead.
phase('Synthesize')
const outDir = cfg.outDir || 'data/calibration'
const ledgerPath = `${outDir}/calibration-ledger.md`
const prefsPath = `${outDir}/revealed-preferences.md`
const RECORD_CAP = 120000
const recordsJson = JSON.stringify(merged)
if (recordsJson.length > RECORD_CAP) {
  log(`WARNING: ${merged.length} records (${recordsJson.length} chars) exceed the ${RECORD_CAP}-char synth cap; the .md ledger will cover a prefix only. Regenerate the full ledger deterministically from the returned records.`)
}

await agent(
  `Write two files for a scoring-calibration ledger.

1) ${prefsPath} — synthesize these revealed-preference findings into a tight markdown doc (drawn-to signals, dealbreakers, self-decline patterns, thesis evolution), each claim keeping its source citation:
${JSON.stringify(preferences).slice(0, 20000)}

2) ${ledgerPath} — a human-readable calibration ledger: a summary table (label distribution, engaged/rejected/declined/unlabeled counts), the list of disagreements (extractor vs verifier) for review, a "never-pursued but researched" negative-space list (entities labeled unlabeled that still have a dossier/high interest — the scorer's potential misses), a coverage note (how many entities had narrative sources vs stage-only), and a short "how this feeds the continuous-learning loop with an exploration budget" section. Use ONLY the merged records below.
MERGED RECORDS (JSON): ${recordsJson.slice(0, RECORD_CAP)}

Write both files exactly. Then return a 4-sentence summary of the ledger.`,
  { label: 'synthesize:ledger', phase: 'Synthesize' },
)

return { entities: COUNT, batches: NBATCH, merged, disagreements: merged.filter((m) => m.disagreement).length, ledgerPath, prefsPath }
