# Phase 2 Handoff — Start Here Next Session

## Status: Phase 2 is 95% complete. Commit: edd8f29

### Done (137 tests passing)
- tools/pipe_write.py + 11 tests
- tools/networking_write.py + 12 tests
- tools/remember_apply.py + 14 tests
- tools/act_apply.py + 11 tests
- /pipe skill wired (allowed-tools updated, inline writes → pipe_write.py)
- /networking skill wired (allowed-tools updated, inline writes → networking_write.py)
- /remember skill wired (allowed-tools updated, Step 3 → remember_apply.py)
- conftest.py write_fixture shared helper

### Remaining (do these first)

1. **Wire /act skill** — `.claude/skills/act/SKILL.md`
   - allowed-tools: REMOVE `Write(data/job-pipeline.md)`, `Write(data/networking.md)`, `Edit(data/networking.md)`, `Write(data/notes.md)`, `Edit(data/notes.md)`
   - allowed-tools: ADD `Bash(PYTHONIOENCODING=utf-8 python tools/act_apply.py:*)`
   - In Step 4 "Immediate Route writes" section: replace the inline prose format specs with:
     - Job ad: `act_apply.py pipeline-add "<company>" --role ROLE --url URL --source-file FILENAME --repo-root .`
     - Contact: `act_apply.py contact-add "<name>" --company CO --role ROLE --content TEXT --source-file FILENAME --repo-root .`
     - Unclassifiable: `act_apply.py notes-add --content TEXT [--company-slug SLUG] --source-file FILENAME --repo-root .`
   - Keep `Bash(rm inbox/*)` and `Bash(PYTHONIOENCODING=utf-8 python tools/todo_write.py:*)` in allowed-tools
   - Inbox delete stays gated on status:ok from act_apply.py

2. **docs/CHANGELOG.md** — prepend new entry above the 2026-02-28 entry:
   ```
   ## 2026-02-28 — Phase 2: 4 atomic write scripts + skill wiring
   ### Added
   - tools/pipe_write.py — add/update/remove for job-pipeline.md
   - tools/networking_write.py — add/log/remove for networking.md
   - tools/remember_apply.py — 8 destination handlers
   - tools/act_apply.py — pipeline-add/contact-add/notes-add
   - 48 new tests (137 total)
   ### Changed
   - /pipe, /networking, /remember, /act skills wired to write scripts
   - conftest.py: shared write_fixture helper
   - settings.local.json: allow-all wildcard for build session
   ```

3. **docs/methodology.md** — add "Deterministic Write Layer" section under Tools

4. **CLAUDE.md** — add pipe_write, networking_write, remember_apply, act_apply to Tools section

5. **Run git commit** for the 5 modified files (CLAUDE.md, CHANGELOG, methodology, self-improving-data-framework, lessons)

6. **/code-review:code-review** (Nick requested)

7. **/claude-md-management:revise-claude-md** (Nick requested)

---

## NEW RESEARCH TASK (do after Phase 2 wrap-up)

Nick wants extensive research saved to inbox/:

**Topic 1: Gmail integration without full API access**
- Best practices for pulling Gmail emails with minimal permissions
- Preventing prompt injection from email content
- Creating gaps/sanitization layers between raw email and LLM
- Specific: OAuth scopes (gmail.readonly vs gmail.modify), PubSub push vs polling

**Topic 2: Best automation tools for this job search system**
- N8N vs Make (formerly Integromat) vs Zapier vs others
- Self-hosted vs cloud, cost, complexity, job-search-specific use cases
- How to integrate with this repo's skill system without reinventing the wheel
- "As simple as possible, as fast as possible, without losing quality"

**Format required by Nick:**
1. Extensive web research (use WebSearch + WebFetch)
2. Create implementation plans
3. Panel of experts critique the plans with critical questions
4. Answer those questions, improve plans using pros/cons
5. Design principle: "Continuously ask why and back up with evidence"
6. Save final output to inbox/ for morning review

**Use a general-purpose agent with max_turns: 30 for the research**

