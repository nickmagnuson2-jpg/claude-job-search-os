---
name: voice-export
description: Generate a recruiter simulation prompt for the Claude App (voice mode) from a CV and job ad URL
argument-hint: <path-to-cv> <job-ad-url>
user-invocable: true
allowed-tools: Read(*), Glob(*), Grep(*), WebFetch
---

# Voice Export — Generate Recruiter Simulation Prompt

Generate a self-contained recruiter screening simulation prompt that can be pasted into the Claude App (voice mode). The recruiter stays in character for the entire call — no coaching, no interruptions. Coaching happens afterwards in Claude Code.

## Arguments

- `$ARGUMENTS` (required): Two arguments separated by space:
  1. Path to the CV file (e.g. `output/20260210-target-role-slug.md`)
  2. Job ad URL (e.g. `https://www.upwork.com/project/...`)

## Instructions

### Step 1: Parse Arguments

Extract the CV path and job ad URL from `$ARGUMENTS`. If only one argument is provided, ask the user for the missing one.

### Step 2: Load Sources

1. Read the CV file.
2. Fetch the job ad from the URL using WebFetch.
3. Auto-detect a deep review file: take the CV filename, append `-DEEP-REVIEW` before the extension.
   - Example: CV `output/20260210-target-role-slug.md` → look for `output/20260210-target-role-slug-DEEP-REVIEW.md`
   - If the file exists, read it. If not, skip — the question pool will rely on gap analysis only.
4. Auto-detect a cheat sheet file: take the CV filename, append `-cheatsheet` before the extension.
   - Example: CV `output/20260210-target-role-slug.md` → look for `output/20260210-target-role-slug-cheatsheet.md`
   - If the file exists, read **only the header section above the first `---`** for recruiter persona enrichment (name, company context, intermediary vs. end client). Do NOT read below the first `---` — that contains coached answers and candidate prep.
5. Read `framework/voice-export.md` for the export prompt structure and quality rules.

### Step 3: Detect Language

Determine the CV language by scanning section headers and body text. Common header patterns:
- English: "Summary", "Skills", "Certifications", "Projects" → **EN**
- German: "Kurzprofil", "Fachkenntnisse", "Zertifizierungen" → **DE**
- French: "Compétences", "Expérience", "Formation" → **FR**
- Dutch: "Vaardigheden", "Werkervaring", "Opleiding" → **NL**
- Spanish: "Habilidades", "Experiencia", "Formación" → **ES**
- Other languages → infer from content
- If uncertain → default to **EN**

All generated prompt text must match this language.

### Step 3b: Set the four persona axes (mandatory — do NOT default to a warm recruiter)

A warm-recruiter sim cannot rehearse the muscle a cold adversarial deep-dive tests. The most valuable reps recreate the pressure of the *actual next interviewer*. Before building the persona, read the pipeline **Next-Action** and the interviewer's `data/people/<slug>.md` dossier if one exists, then set all four axes explicitly:

| Axis | Options | How to set it |
|---|---|---|
| **1. Temperature** | adversarial / cold / skeptical ⟷ warm gatekeeper | From the round. Ask "adversarial or warm?" **only if genuinely ambiguous** — don't ask when the round type already answers it. |
| **2. Interviewer persona** | a named person, not a generic recruiter | Build from `data/people/<slug>.md`: background, fluency, what they screen for. An ex-consulting deep-dive interviewer behaves nothing like a warm phone screen. |
| **3. Round type** | recruiter screen / behavioral deep-dive / case / founder vibe-check | Drives question shape AND pacing, not just content. |
| **4. Scenario hold** | required whenever the sim carries a scenario | See Step 6 §8 below. |

**Seed the persona with current-week company facts** from the freshest dossier, so the sim interrogates from accurate present state rather than stale context.

**Length variant — offer it, don't wait to be asked:**
- **Full sim** (~25 min) — the complete loop.
- **Focused rep** (~10-12 min) — names the must-hit beats up front and **guarantees the run reaches them**. Use before a real call. This exists because an open-ended full sim ran out of time before the highest-value beat: the live-need bridge (hypothesis H4), which is the recurring miss. A focused rep that reaches the bridge beats a longer unfocused one.

### Step 4: Extract Recruiter Persona

Build from the Step 3b axes, the job ad, and the cheat sheet header if available:
- **Company name** and brief context (1-2 sentences) — cheat sheet may clarify intermediary vs. end client
- **Role title** / project description
- **Start date, duration, utilisation, remote/onsite**
- **Interviewer name and background** — prefer the real named interviewer from `data/people/<slug>.md`. Only generate a plausible recruiter name matching the job ad's market/language when no real interviewer is known.
- **Temperature and round-type behaviors** from Step 3b, written into the persona as concrete instructions ("you are skeptical and press for numbers; you do not offer encouragement"), not as an adjective.

### Step 5: Build Question Pool

Assemble the question pool from three sources:

#### A. Deep Review Questions (if file exists)
- Extract the "Top 10 Probing Interview Questions" section from the deep review file
- Include all questions verbatim — these are the highest-value probes

#### B. Gap-Derived Questions
- Compare job ad requirements against the CV
- Generate 3-5 questions targeting: technical gaps, experience depth mismatches, role-fit concerns
- Do NOT duplicate topics already covered by deep review questions

#### C. Standard Recruiter Topics
Always include these topics (the recruiter weaves them in naturally):
- Compensation expectations and flexibility (rate for freelance/contract roles, salary for permanent — derive from the CV and job ad context)
- Availability, notice period, or earliest start date
- Remote/onsite preferences and travel willingness
- Motivation for this specific role
- Current employment or engagement status
- Invoicing entity / contracting setup (for freelance/contract roles only, if relevant based on CV)

### Step 6: Assemble the Prompt

Build the prompt following this exact section order (use `##` headers):

1. **System Instruction** — Role assignment: realistic recruiter, no coaching, stay in character
2. **Recruiter Persona** — From Step 4
3. **Candidate CV** — Full CV text inlined (the recruiter "has it on their desk")
4. **Question Pool** — From Step 5. Mark this section as internal to the recruiter: "These are topics and questions for you to draw from during the call. Weave them into natural conversation — do not read them as a list."
5. **Call Flow Guidelines** — Natural pacing: intro → candidate pitch → technical/experience questions → compensation/logistics → closing with next steps. Target 15-20 minutes for a full sim, 10-12 for a focused rep. Go deeper on fewer questions rather than rushing through all.

   **For a focused rep**, replace the open flow with the named must-hit beats and instruct the persona to reach every one before closing — e.g. force a substance-first open, demand the numbers, and GUARANTEE the run reaches the live-need moment. A rep that ends before its target beat produced nothing.
6. **Session Rules** — Language match, stay in character, natural behaviour, ending instruction ("End of simulation. Take this conversation to Claude Code for a full debrief with your coaching files.")
7. **Start Instruction** — match CV language. Provide the start instruction in the detected language. Examples — EN: `Say "Start" to begin the call.` / DE: `Sag "Start" um das Gespräch zu beginnen.` / For other languages, translate accordingly.

8. **Scenario Hold Clause** — **MANDATORY whenever the sim carries a scenario, case, prompt, or exercise.** "Open with Scenario 1" is not sufficient. A persona told only to open with a scenario will follow the candidate's first interesting tangent and never return to it, and the rep silently tests nothing. Write the recovery behavior explicitly:

   > *You are running Scenario X. If the candidate opens somewhere else or drifts, acknowledge briefly, then bring the conversation back to Scenario X before moving on. Do not proceed to any other topic until Scenario X has been worked. If the candidate resists twice, name it directly and return to it a third time.*

   Omit this section only when the sim genuinely has no scenario.

### Step 7: Quality Check

1. **Count words.** If > 8,000, apply compression strategies from `framework/voice-export.md` in priority order.
2. **Scan for file references.** If any path-like string (`data/...`, `coaching/...`, `output/...`) appears in the prompt, remove it — everything must be inline.
3. **Check language consistency.** No mixing DE/EN within the prompt.
4. **Verify no coached answers leaked.** The prompt must NOT contain any prepared candidate answers.

### Step 8: Output

Output the assembled prompt inside a single fenced code block (` ```markdown ... ``` `) so the candidate can copy it directly into the Claude App.

Before the code block, print a short summary:
- Role name
- Language (DE/EN)
- Word count
- Deep review questions included? (Yes/No) — if No, add: `💡 Run /review-cv-deep <cv-path> first for more substantial probing questions in the simulation.`
