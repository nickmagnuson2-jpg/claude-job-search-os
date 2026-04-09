# Phase 1: Granola MCP Integration - Call Analysis & Coaching Loop - Research

**Researched:** 2026-04-08
**Domain:** MCP integration, transcript analysis, coaching automation
**Confidence:** MEDIUM

## Summary

This phase extends the existing `/debrief` skill to fetch transcripts from Granola's official MCP server, analyze them for anti-patterns and coaching improvements, and (later) automate the loop via n8n. The Granola MCP server at `https://mcp.granola.ai/mcp` exposes tools for listing meetings, fetching transcripts, and searching notes. Authentication uses browser-based OAuth 2.1 with PKCE - no API keys needed.

The critical discovery: **Granola MCP is not yet configured in the project.** CONTEXT.md references `.claude.json` but no MCP configuration file exists. The first task must be adding Granola as an MCP server via `claude mcp add`. Additionally, **n8n is not installed** on the current Mac machine (only a Windows `.bat` launcher exists), so D-09 through D-11 (scheduled automation) require n8n installation as a prerequisite.

**Primary recommendation:** Split work into two waves: (1) MCP setup + `/debrief` extension with Granola fetch (D-01 through D-08), then (2) n8n installation and automation workflow (D-09 through D-12). Wave 1 delivers immediate value without n8n dependency.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Extend the existing `/debrief` skill to support Granola as an optional input source. Do NOT create a separate skill.
- **D-02:** Two input modes: (1) pasted transcript (existing behavior, unchanged) or (2) Granola fetch via MCP. If no transcript is pasted, default to Granola picker.
- **D-03:** Interactive picker showing recent Granola meetings. User selects which call to debrief.
- **D-04:** The picker should show meeting title, date, and duration so the user can identify the right call.
- **D-05:** Full debrief depth - same as current `/debrief` skill: filler word counts, coached-answer comparison, Q&A pair grading, anti-pattern detection, session logging.
- **D-06:** Auto-extract filler counts for tracked words: "really", "kind of", "definitely", "to be honest with you", "absolutely", "pretty".
- **D-07:** Compare what was said against cheat sheet / coached answers for the relevant company.
- **D-08:** Update `coaching/anti-pattern-tracker.md` with per-call data and trend tracking.
- **D-09:** n8n workflow checks Granola periodically for new unprocessed calls.
- **D-10:** Process ALL calls - no filtering by pipeline match. Cast wide net, review from inbox.
- **D-11:** Auto-debrief results written to `data/inbox.md` for review, not directly to coaching files.
- **D-12:** DEPENDENCY: n8n is not installed on current machine. n8n setup is prerequisite for D-09 through D-11.

### Claude's Discretion
- Granola MCP API discovery - tool names, data model, authentication flow
- Transcript parsing format (how to split into Q&A pairs from Granola's raw format)
- n8n workflow scheduling interval (suggest every 2-4 hours)
- How to handle calls where no cheat sheet exists (general coaching analysis without coached-answer comparison)

### Deferred Ideas (OUT OF SCOPE)
- Auto-generate practice scripts from call analysis
- Voice tone analysis (confidence, pace, energy)
- Cross-call trend dashboard (visual chart of filler counts over time)

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| R1.1 | Discover Granola MCP API surface | Official MCP tools documented: 5 tools including list_meetings, get_meeting_transcript, query_granola_meetings. See Standard Stack section. |
| R1.2 | Build `/debrief-call` skill that pulls Granola transcript | Decision D-01 says extend existing `/debrief`, not build new skill. MCP tool `get_meeting_transcript` returns speaker-labeled segments. |
| R1.3 | Auto-extract filler word counts from transcripts | Transcript segments have `text` field - simple regex/string matching against D-06 word list. See Code Examples. |
| R1.4 | Auto-extract STAR story usage | Requires Q&A pair parsing from transcript segments, then matching against coached-answers.md stories. See Architecture Patterns. |
| R1.5 | Compare transcript against coached answers | Load cheat sheet + coached-answers.md, fuzzy-match Q&A pairs against coached phrasings. Existing debrief Step 3C already does this. |
| R1.6 | Append call analysis to company-notes | Existing convention: `data/company-notes/<slug>.md` with `## YYYY-MM-DD` headers. Append structured intel section. |
| R1.7 | Update anti-pattern tracker with per-call data | Existing schema in `coaching/anti-pattern-tracker.md` - update Occurrences, Last Seen, Trend columns + Update Log. |
| R1.8 | Generate post-call improvement recommendations | Existing debrief Step 4 "Focus for Next Session" already does this. Extend with trend-aware recommendations. |

</phase_requirements>

## Standard Stack

### Core

| Component | Version/Details | Purpose | Why Standard |
|-----------|----------------|---------|--------------|
| Granola Official MCP | `https://mcp.granola.ai/mcp` | Meeting transcript source | Official first-party server, OAuth built-in, no API key management [CITED: docs.granola.ai/help-center/sharing/integrations/mcp] |
| Claude Code MCP | Built-in | MCP client for tool calls | Already the execution environment |
| Python 3.14 | Installed (verified) | Script runtime for analysis tools | Project standard - all tools/*.py use Python |

### Granola MCP Tools (Official Server)

| Tool | Purpose | Notes |
|------|---------|-------|
| `list_meetings` | Browse meeting metadata with filtering | Returns title, date, duration. Use for D-03/D-04 picker. [CITED: docs.granola.ai] |
| `get_meeting_transcript` | Fetch verbatim transcript with speaker labels | Paid plans only. Returns segments with speaker source + text. [CITED: docs.granola.ai] |
| `get_meetings` | Search meeting content including transcripts and notes | Batch retrieval by document IDs [CITED: docs.granola.ai] |
| `query_granola_meetings` | Natural language search across meeting history | Conversational interface with citations [CITED: docs.granola.ai] |
| `list_meeting_folders` | Enumerate accessible folders | Paid plans only [CITED: docs.granola.ai] |

### Granola REST API (for n8n automation)

| Endpoint | Purpose | Notes |
|----------|---------|-------|
| `GET https://public-api.granola.ai/v1/notes` | List notes with pagination | Supports `created_after` param for incremental fetch [CITED: docs.granola.ai/introduction] |
| `GET https://public-api.granola.ai/v1/notes/{id}?include=transcript` | Get note + transcript | Returns transcript array with speaker objects [CITED: docs.granola.ai/introduction] |

**Rate limits:** MCP ~100 requests/minute. REST API: 25 requests/5 seconds burst, 300/minute sustained. [CITED: docs.granola.ai]

### Supporting

| Tool | Version | Purpose | When to Use |
|------|---------|---------|-------------|
| n8n | Not installed | Workflow automation for scheduled polling | D-09 through D-11 only. Install via `npm install -g n8n` |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Official Granola MCP | Community MCP servers (btn0s, mishkinf, pedramamini) | Official server is maintained by Granola, has OAuth built-in, and matches docs. Community servers use local Supabase credentials scraping - fragile. |
| MCP for n8n automation | REST API with Personal API Key | MCP requires an interactive Claude session. n8n workflows need REST API with an API key for headless operation. Use both: MCP for interactive debrief, REST for automation. |

## Architecture Patterns

### Recommended Approach: Two-Path Architecture

```
Interactive path (D-01 through D-08):
  User invokes /debrief [cv-path]
  → Skill checks: transcript pasted? 
    → YES: existing flow (unchanged)
    → NO: Granola MCP picker
      → list_meetings → show picker → user selects
      → get_meeting_transcript → parse into Q&A pairs
      → existing debrief analysis pipeline (Steps 2-6)

Automated path (D-09 through D-11, requires n8n):
  n8n cron → tools/granola_fetch.py (REST API)
  → fetch new meetings since last check
  → tools/call_analyzer.py → lightweight analysis
  → append summary to data/inbox.md
  → user reviews in /standup or /act
```

### Pattern 1: Extending the Debrief Skill

**What:** Add a new "Step 0: Acquire Transcript" before the existing Step 1 in SKILL.md
**When to use:** All Granola integration work (D-01 through D-04)

The existing `/debrief` skill flow is:
1. Load Context (CV, cheat sheet, coached answers, anti-pattern tracker)
2. Parse Transcript (from conversation context)
3. Analyze Each Answer
4. Generate Debrief Report
5. Discuss with User
6. Log Session

Extension point: Insert before Step 1. If no transcript is in conversation context, call Granola MCP to fetch one. The rest of the pipeline stays identical.

**Key detail:** The skill's `allowed-tools` field needs updating to include MCP tool permissions (or MCP tools may already be available globally - verify during implementation).

### Pattern 2: Transcript Segment to Q&A Pair Parsing

**What:** Convert Granola's speaker-labeled segments into the Q&A pair format the debrief expects.
**When to use:** R1.2, R1.4

Granola transcript format (from REST API / MCP):
```json
{
  "transcript": [
    {"speaker": {"source": "speaker"}, "text": "Tell me about your experience..."},
    {"speaker": {"source": "microphone"}, "text": "Sure, at McKinsey I led..."},
    {"speaker": {"source": "speaker"}, "text": "How did you handle..."},
    {"speaker": {"source": "microphone"}, "text": "I would start by..."}
  ]
}
```
[CITED: docs.granola.ai/introduction]

- `source: "microphone"` = the user (Nick)
- `source: "speaker"` = the other participant (interviewer/recruiter)

Parsing logic:
1. Merge consecutive segments from same speaker (Granola splits on pauses)
2. Pair each "speaker" block with the following "microphone" block = one Q&A pair
3. Handle edge cases: multiple questions before an answer, monologues, opening pleasantries

This is custom logic but straightforward. No library needed - just sequential processing.

### Pattern 3: Filler Word Extraction

**What:** Count occurrences of tracked filler words/phrases in microphone segments
**When to use:** R1.3, D-06

Tracked fillers (from D-06): "really", "kind of", "definitely", "to be honest with you", "absolutely", "pretty"

Implementation: case-insensitive regex matching on each microphone segment. Must handle:
- Word boundaries ("pretty" as filler vs "pretty good" vs "a pretty building")
- Multi-word phrases ("to be honest with you", "kind of")
- Contractions ("kinda" = "kind of")

[ASSUMED] "Pretty" as a filler typically precedes adjectives ("pretty good", "pretty much") vs. standalone use. May need heuristic or just count all occurrences with context snippets for manual review.

### Pattern 4: No-Cheat-Sheet Fallback (Claude's Discretion)

**What:** When no cheat sheet exists for the company being debriefed, run general coaching analysis.
**Recommendation:** Skip the coached-answer comparison (Step 3C) but still run all other analysis: filler counts, anti-pattern detection, Q&A grading, trust impact assessment. Flag in the report: "No cheat sheet found for [company]. Coached-answer comparison skipped. Consider running /prep-interview to generate one."

### Anti-Patterns to Avoid
- **Creating a separate `/debrief-call` skill:** D-01 explicitly says extend `/debrief`, not create new.
- **Scraping local Supabase credentials:** Community MCP servers do this. Use the official MCP server with proper OAuth instead.
- **Overwriting anti-pattern tracker:** Must append/update existing rows, never rebuild the table. Read-then-write with existing data preserved.
- **Blocking on n8n for Wave 1:** The interactive debrief (D-01 through D-08) has zero dependency on n8n. Don't couple them.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| MCP communication | Custom HTTP client for MCP protocol | Claude Code's built-in MCP client | MCP protocol handles auth, transport, tool discovery automatically |
| OAuth token management | Token refresh logic | Granola official MCP server handles OAuth | Official server manages the full OAuth 2.1 + PKCE flow |
| Meeting list display | Custom API polling + formatting | `list_meetings` MCP tool | Returns structured data; skill formats for display |
| Transcript retrieval | Direct API calls from Python | `get_meeting_transcript` MCP tool (interactive) / REST API (automated) | MCP for interactive, REST for headless n8n |

**Key insight:** The interactive path (D-01 through D-08) should use MCP tools called from within the skill instructions, not Python scripts. The skill runs inside Claude Code which IS the MCP client. Python scripts are only needed for the headless n8n automation path (D-09 through D-11).

## Common Pitfalls

### Pitfall 1: Granola MCP Not Configured
**What goes wrong:** Skill tries to call Granola MCP tools but they're not available.
**Why it happens:** No `.mcp.json` or MCP configuration exists in the project. CONTEXT.md mentions `.claude.json` but it doesn't exist.
**How to avoid:** First task must be `claude mcp add granola --transport http https://mcp.granola.ai/mcp` and complete OAuth flow.
**Warning signs:** MCP tool calls return "tool not found" errors.

### Pitfall 2: Transcript Access Requires Paid Plan
**What goes wrong:** `get_meeting_transcript` returns empty or 404.
**Why it happens:** Granola free tier doesn't include transcript access via MCP. Only notes from last 30 days on free tier.
**How to avoid:** Verify Nick has a Business or Enterprise Granola plan before implementation. If on free tier, the entire phase is blocked.
**Warning signs:** Can list meetings but can't fetch transcripts. [CITED: docs.granola.ai/help-center/sharing/integrations/mcp]

### Pitfall 3: Speaker Attribution Ambiguity
**What goes wrong:** Can't distinguish interviewer questions from candidate answers.
**Why it happens:** Granola uses `source: "microphone"` (local device) vs `source: "speaker"` (remote audio). Works for 1:1 calls. Panel interviews with multiple remote speakers all show as "speaker".
**How to avoid:** For panel interviews, the Q&A parsing must handle multiple consecutive "speaker" segments as different questions. Flag panel calls in the debrief output.
**Warning signs:** All remote speakers appear as same "speaker" source.

### Pitfall 4: Consecutive Segment Merging
**What goes wrong:** Q&A pairs are fragmented - one answer split into 5+ segments.
**Why it happens:** Granola splits transcript on pauses, not on speaker turns. A single answer with pauses becomes multiple segments.
**How to avoid:** Merge consecutive segments from the same speaker source before Q&A pairing.
**Warning signs:** Dozens of very short Q&A pairs for a 30-minute call.

### Pitfall 5: Edit Tool on Anti-Pattern Tracker
**What goes wrong:** Edit silently fails on the anti-pattern tracker table (long rows).
**Why it happens:** Project CLAUDE.md documents this: "Edit tool silently fails on markdown files with table rows >500 characters."
**How to avoid:** Use Write (read-then-write full file) for `coaching/anti-pattern-tracker.md`. The skill already has `Write(coaching/**)` permission.
**Warning signs:** The `check_edit_safety.py` PostToolUse hook will warn, but better to use Write from the start.

### Pitfall 6: n8n REST API vs MCP Authentication Mismatch
**What goes wrong:** n8n automation can't use MCP tools (MCP requires interactive browser OAuth).
**Why it happens:** MCP authentication is session-based with browser OAuth. n8n runs headless.
**How to avoid:** n8n automation (D-09 through D-11) must use the REST API (`public-api.granola.ai`) with a Personal API Key, not MCP. Two separate auth mechanisms.
**Warning signs:** Trying to call MCP from a Python script outside Claude Code.

## Code Examples

### Filler Word Counter (for call_analyzer.py)

```python
# Source: Project-specific logic based on D-06 requirements
import re
from collections import Counter

FILLER_PATTERNS = {
    "really": r'\breally\b',
    "kind of": r'\bkind\s+of\b|\bkinda\b',
    "definitely": r'\bdefinitely\b',
    "to be honest with you": r'\bto\s+be\s+honest\s+with\s+you\b',
    "absolutely": r'\babsolutely\b',
    "pretty": r'\bpretty\s+(?:much|good|well|big|bad|sure|clear|easy|hard|tough|close|far)',
}

def count_fillers(text: str) -> dict:
    """Count filler words in candidate's speech segments."""
    text_lower = text.lower()
    counts = {}
    for filler, pattern in FILLER_PATTERNS.items():
        matches = re.findall(pattern, text_lower)
        if matches:
            counts[filler] = len(matches)
    return counts
```

### Transcript Q&A Pair Parser

```python
# Source: Based on Granola transcript structure [CITED: docs.granola.ai]
def parse_qa_pairs(transcript_segments: list[dict]) -> list[dict]:
    """
    Convert Granola transcript segments into Q&A pairs.
    Merges consecutive same-speaker segments first.
    """
    # Step 1: Merge consecutive segments from same speaker
    merged = []
    for seg in transcript_segments:
        source = seg["speaker"]["source"]
        text = seg["text"].strip()
        if merged and merged[-1]["source"] == source:
            merged[-1]["text"] += " " + text
        else:
            merged.append({"source": source, "text": text})
    
    # Step 2: Pair speaker (interviewer) with microphone (candidate)
    pairs = []
    current_question = None
    for block in merged:
        if block["source"] == "speaker":
            if current_question and not pairs:
                # Opening remarks from interviewer, no answer yet
                pass
            current_question = block["text"]
        elif block["source"] == "microphone" and current_question:
            pairs.append({
                "question": current_question,
                "answer": block["text"],
            })
            current_question = None
        elif block["source"] == "microphone":
            # Candidate speaking without a preceding question (e.g., intro)
            pairs.append({
                "question": "(unprompted / opening)",
                "answer": block["text"],
            })
    return pairs
```

### Granola MCP Picker (Skill Instructions Pattern)

```markdown
<!-- For inclusion in the /debrief SKILL.md Step 0 -->
If no transcript is in the conversation context:

1. Call `list_meetings` to get recent meetings
2. Present a numbered list to the user:
   | # | Title | Date | Duration |
   |---|-------|------|----------|
   | 1 | Dusty Robotics - Phil CTO | Mar 17, 2026 | 45 min |
   | 2 | Amae Health - Recruiter Screen | Mar 10, 2026 | 30 min |
3. Ask: "Which call would you like to debrief? (Enter number)"
4. Call `get_meeting_transcript` with the selected meeting ID
5. Continue to Step 1 (Load Context) with the fetched transcript
```

### Anti-Pattern Tracker Update Pattern

```python
# Source: Existing schema in coaching/anti-pattern-tracker.md
# Pattern for updating the tracker after a debrief

# Read existing tracker
# Find row matching anti-pattern name
# Update: Occurrences += 1, Last Seen = today, Trend = compute from history
# Append to Update Log table:
# | 2026-04-08 | Company - Role (REAL/voice sim) | Patterns flagged | Notes |
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Paste transcript manually | Granola MCP auto-fetch | Feb 2025 (MCP launch) | Eliminates copy-paste friction |
| Community MCP servers (scrape local Supabase creds) | Official MCP server with OAuth 2.1 | Early 2026 | Proper auth, maintained by Granola |
| Single API | Dual API: MCP (interactive) + REST (headless) | 2026 | MCP for Claude sessions, REST for automation |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | "pretty" as filler can be detected by checking if followed by adjective/adverb | Code Examples | May over-count or under-count; LOW risk - counts are for coaching trends, not precision metrics |
| A2 | Granola MCP `list_meetings` returns title, date, and duration fields | Standard Stack | If missing duration, picker shows title + date only - trivial fallback |
| A3 | Nick has a paid Granola plan with transcript access | Pitfall 2 | HIGH risk - if free tier, transcript fetch is blocked entirely |
| A4 | Panel interviews (3+ participants) are rare enough that simple speaker/microphone parsing suffices | Architecture Patterns | MEDIUM risk - if panel calls are common, need more sophisticated speaker tracking |
| A5 | n8n can be installed via npm on macOS without issues | Environment Availability | LOW risk - standard npm global install, well-documented |

## Open Questions

1. **Granola Plan Tier**
   - What we know: Transcript access requires Business or Enterprise plan
   - What's unclear: Which plan Nick is on
   - Recommendation: Verify before starting implementation. If free tier, escalate immediately - this blocks R1.2 through R1.8.

2. **MCP Tool Parameter Schema**
   - What we know: Official docs list 5 tools but don't fully document parameters
   - What's unclear: Exact parameters for `list_meetings` (date range? limit? pagination?) and `get_meeting_transcript` (meeting ID format?)
   - Recommendation: First implementation task should be MCP discovery - call each tool and document actual parameter schemas.

3. **REST API Key for n8n Path**
   - What we know: REST API requires Personal API Key from Granola Settings > API
   - What's unclear: Whether Nick has already created one, and whether the Business plan includes API access
   - Recommendation: Defer to Wave 2 (n8n automation). Not needed for interactive debrief.

4. **Transcript Processing Time**
   - What we know: API only returns notes with generated AI summary and transcript
   - What's unclear: How long after a call ends before the transcript is available via API/MCP
   - Recommendation: Document the lag during discovery. If >1 hour, affects n8n polling interval.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | Analysis scripts | Yes | 3.14.3 | - |
| Node.js | n8n runtime | Yes | 25.9.0 | - |
| Granola MCP | Interactive transcript fetch | Not configured | - | Must run `claude mcp add granola` |
| Granola app | Meeting recording | Installed (verified local data) | Unknown | - |
| n8n | Scheduled automation (D-09-D-11) | Not installed | - | `npm install -g n8n` |
| Granola paid plan | Transcript access | Unknown | - | None - blocks phase if free tier |

**Missing dependencies with no fallback:**
- Granola MCP configuration (must add before any MCP tool calls work)
- Granola paid plan verification (blocks transcript access if free tier)

**Missing dependencies with fallback:**
- n8n (only needed for D-09-D-11; install via npm when ready for Wave 2)

## Project Constraints (from CLAUDE.md)

- **Write tool for coaching files:** Use Write (read-then-write), never Edit, for anti-pattern-tracker.md and similar files with long table rows
- **PostToolUse hook:** `check_edit_safety.py` runs after every Edit on `.md` files - will catch violations
- **PYTHONIOENCODING=utf-8:** Required prefix for all Python script invocations
- **Company-notes convention:** `data/company-notes/<slug>.md` with `## YYYY-MM-DD | [context]` headers
- **Session log convention:** `coaching/progress-recruiter/YYYY-MM-DD-HHMM-<company>-<type>.md`
- **No em dashes:** Hard rule - not in any generated output
- **Atomic write scripts:** Use existing `todo_write.py`, `pipe_write.py`, `networking_write.py` for data file mutations
- **Profile guard:** `/debrief` must check `data/profile.md` and `data/goals.md` exist before running
- **Skill permissions:** `/debrief` has `Write(coaching/**)` and `Edit(coaching/**)` - may need expansion for `data/company-notes/` writes

## Sources

### Primary (HIGH confidence)
- [Granola API Documentation](https://docs.granola.ai/introduction) - REST API endpoints, transcript structure, authentication
- [Granola MCP Documentation](https://docs.granola.ai/help-center/sharing/integrations/mcp) - Official MCP server tools, OAuth setup, Claude Code configuration
- Existing codebase: `.claude/skills/debrief/SKILL.md`, `coaching/anti-pattern-tracker.md`, `coaching/progress-recruiter/_summary.md`

### Secondary (MEDIUM confidence)
- [Granola MCP Blog Post](https://www.granola.ai/blog/granola-mcp) - MCP announcement and use cases
- [PulseMCP Granola Server Page](https://www.pulsemcp.com/servers/granola) - OAuth details, transport protocol
- [Glama MCP Registry](https://glama.ai/mcp/servers/@btn0s/granola-mcp) - Community MCP tool list (7 tools)
- [GitHub chrisguillory/granola-mcp](https://github.com/chrisguillory/granola-mcp) - Transcript segment structure, download tool patterns

### Tertiary (LOW confidence)
- [MCPmarket Granola page](https://mcpmarket.com/server/granola-1) - Aggregated info, unverified details

## Metadata

**Confidence breakdown:**
- Standard stack: MEDIUM - Official MCP tools documented but exact parameter schemas unverified. REST API well-documented.
- Architecture: HIGH - Extension pattern is clear from existing skill structure. Two-path approach (MCP interactive + REST headless) is sound.
- Pitfalls: HIGH - Well-documented from official docs (plan requirements, auth differences) and project conventions (Edit safety, file conventions).

**Research date:** 2026-04-08
**Valid until:** 2026-05-08 (Granola MCP is evolving rapidly - tools may change)
