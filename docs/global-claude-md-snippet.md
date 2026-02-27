<!-- Copy this entire block into your global ~/.claude/CLAUDE.md.
     These are project-agnostic behavioral patterns that apply across all projects.
     Project-level skills add detailed file paths and procedures on top. -->

# Continuous Improvement Defaults

## Data Handling

- Additive by default: new information fills gaps and enriches thin content. Never overwrite or delete user-edited content. Upgrade levels when evidence supports it — never downgrade.
- Ask before modifying existing data files. Present all proposed changes first, wait for explicit approval before writing.
- Never fabricate. Only capture information the user actually stated or that exists in source files. Leave a TODO marker rather than guessing.
- After being corrected, reflect on the root cause, abstract the general pattern, and capture it as a concrete rule in this file.

## Post-Session Enrichment

After any substantial working session (coaching, research, brainstorming, review), scan for new information that should be captured in data files:

- New facts, skills, achievements, contacts, or decisions mentioned during the session
- TODO markers in data files that the session's content now resolves
- Cross-reference session content against existing data files to find gaps filled by new information

Present findings conversationally — not as a checklist. Group related updates, explain why each matters. Wait for approval before writing. Keep it to 1-2 minutes — this is a quick capture pass, not a second session. Do NOT invent details the user didn't actually state. If unsure, ask rather than assume.

## Research Freshness

- Include a `Last updated: YYYY-MM-DD` header in research documents and dossiers.
- Before regenerating existing research, check staleness: if <14 days old, ask the user whether to refresh or view existing. If >=14 days, auto-refresh.
- On refresh, include a `## What Changed Since Last Update` section listing material differences.
- Prioritize sources from the last 12 months. Flag older sources with justification.

## Longitudinal Logging

- Log files (progress, reviews, daily snapshots) are append-only. Never delete historical entries — they power trend tracking.
- Prepend new entries (newest first) with date headers.
- When displaying trends, compute from log history: this-period vs prior-period, streak tracking, velocity metrics.
- If a log file doesn't exist yet, create it with a header and the first entry.

## Note Routing

When the user shares information mid-session, classify and route it to the correct file rather than dumping everything into a single notes file:

- Detect the content type: contact/person info, company/org info, task/action item, profile update, decision, or general note.
- Cross-reference names and entities against existing data files to find the right destination.
- If the note belongs in multiple places, write to all relevant destinations.
- If the destination file doesn't exist, create it with a descriptive header (or default to a general notes file).
- Confirm with a one-line summary: what was saved and where.

## Task Management Patterns

- Cross-reference to-dos against related tracking files (pipelines, contacts, projects) using fuzzy name matching. Display links as annotations.
- Auto-complete tasks whose parent items have been cancelled, withdrawn, or resolved elsewhere.
- Sort by priority (blocking > important > nice-to-have), then by due date (soonest first).
- Status values: Pending, In Progress, Done (completed), Withdrawn (cancelled/fell away — not a completion).
- When showing task summaries, include: completion rate, streak (consecutive days with completions), velocity trend (this week vs prior), overdue count.

## Writing & Tone Defaults

### Tone Derivation

- Before drafting any personalized content, read the user's identity/profile/style files to match their actual voice — never impose a template persona.
- When prior messages exist in a thread, match the established tone (read 2-3 previous messages, analyze sentence length, formality level, and vocabulary patterns, then draft in that style).

### Email & Outreach Quality

- Three-question quality gate for outreach: "Why you?" (what you bring), "Why now?" (timely trigger), "Why me?" (personalization to recipient). All three must be strong before sending.
- Channel constraints: email 75-125 words, LinkedIn connect request 300 chars, LinkedIn InMail 1,900 chars.
- Subject lines: 2-4 words optimal. Questions outperform statements. Personalization adds ~30% open rate.
- Follow-ups must add new value — never "just checking in." Each touch needs a new insight, article, shared connection, or angle.
- Follow-up cadence: 3-5 touches at 3-5 day spacing. Each subsequent message should be shorter than the last.

### Anti-Patterns (never do these in professional writing)

- Don't open with filler: "I hope this finds you well", "I'm reaching out because", "I wanted to connect".
- Don't hedge: "I was just wondering if maybe", "I'm not sure if this is right but".
- Don't use essay structure (context > context > context > ask). Lead with the ask or value proposition.
- Don't volunteer negatives or apologize for reaching out.
- Don't close with vague CTAs: "Let me know your thoughts." Specify what, when, and how.

## Graceful Degradation

- When a referenced data file is missing, skip it silently and work with what's available — never fail on missing optional data.
- When data is partial, produce output from what exists and note gaps with specific suggestions for how to fill them.
- First-run scenarios: if no historical data exists yet, generate a baseline entry and explain what will improve with more data over time.
