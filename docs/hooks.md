Last updated: 2026-10-23

# Hooks

Hooks are Python scripts that Claude Code runs before or after a tool call (`PreToolUse`, `PostToolUse`) or at the end of a turn (`Stop`). They are wired in [`.claude/settings.json`](../.claude/settings.json) and live in `tools/check_*.py`.

Most hooks block the tool call outright (exit code 2), and Claude sees the reason and corrects course. A few are warn-only, marked below. The usual path to a new hook is a failure that recurs: it is logged, then written up as a rule, then turned into a check that blocks it.

| Hook | Trigger | What it enforces |
|---|---|---|
| `check_voice_pure.py` | PreToolUse on Write/Edit/MultiEdit/NotebookEdit | Blocks model-written prose in dated reflection files, which hold only the user's own words, including a `## Notes` or `## Analysis` section of Claude synthesis. Reads Write and Edit payloads only; a MultiEdit or NotebookEdit passes unchecked |
| `check_email_via_skill.py` | PreToolUse on Write/Edit/MultiEdit/NotebookEdit | Blocks email-shaped content written outside the skill flow |
| `check_no_confabulation.py` | PreToolUse on Write/Edit/MultiEdit/NotebookEdit | Blocks placeholder tokens in shippable artifacts |
| `check_data_projects.py` | PreToolUse on Write/Edit/MultiEdit/NotebookEdit to `data/projects/` | Warn-only (the warning is not shown to Claude): flags verbs that claim sole credit ("single-handedly built") in a project's Key Achievements |
| `check_living_log_purity.py` | PreToolUse on Write/Edit/MultiEdit | Blocks direct writes to append-only log files; only the dedicated append script may write them |
| `check_draft_voice.py` | PreToolUse on Bash `open_draft.py` | Scans draft for voice anti-patterns before Gmail compose opens, including lines that tell the recipient how to do their own job ("please make sure", "send it my way") |
| `check_todo_write_kwargs.py` | PreToolUse on Bash | Blocks kwarg-style invocation of the atomic to-do writer |
| `check_edit_after_mutation.py` | PreToolUse on Edit/MultiEdit (PostToolUse on Read/Write/Edit/MultiEdit/Bash records what was read) | Warn-only: flags an edit to a file that changed on disk since it was last read, or was never read this session |
| `check_edit_safety.py` | PostToolUse on Edit | Warns after an Edit on long-row markdown tables, and more strongly on files that must be rewritten whole (`job-todos.md`, `job-pipeline.md`) |
| `check_plan_partner_critique.py` | Not currently wired in `.claude/settings.json` (script present) | Reminds to run a McKinsey-critical-advisor critique on large plan docs |
| `check_script_error_logged.py` | PostToolUse on Bash | Auto-appends to friction log when a script returns a JSON error, or when any `python3` invocation crashes with a traceback |
| `check_bare_python.py` | PreToolUse on Bash | Blocks a bare `python` in command position, requiring `python3` (anchored to command position so it never trips on the token inside strings or filenames) |
| `check_prep_doc_format.py` | PreToolUse on Write/Edit/MultiEdit/NotebookEdit to `output/<slug>/*prep*.md` | Blocks a malformed status stamp in an interview prep doc. Only checks that need no context live here; the rest stay in the `/prep-interview` skill. Reads Write content and an Edit's `new_string`; a MultiEdit's `edits[]` is not checked |
| `check_changelog_currency.py` | Stop | Warns once per HEAD when commits since the last `docs/CHANGELOG.md` edit touched `tools/`, `.claude/skills/`, `framework/`, settings, or `requirements.txt` without a changelog update |
| `check_banned_phrase.py` | PreToolUse on Write/Edit/MultiEdit | Blocks the phrases enumerated in `tools/mannered-phrases.txt`. A listed-phrase gate, not a prose-quality detector |
| `check_zuora_principal_title.py` | PreToolUse on Write/Edit/MultiEdit/NotebookEdit/Bash | Blocks an outdated version of a former manager's job title |
| `check_handoff_producer.py` | PreToolUse on Write/Edit/MultiEdit/NotebookEdit/Bash | Blocks creating a new handoff-shaped file under `output/`; cross-session state goes to the workstreams registry |
| `check_guard_edit_approval.py` | PreToolUse on Write/Edit/MultiEdit and Bash | Blocks edits to guard infrastructure (`tools/check_*.py`, the hook libraries, `.claude/settings.json`) unless approved. A Python script that names a guard is judged by where its writes go, so reading, hashing or running a guard is not blocked |
| `check_new_toplevel_dir_protected.py` | PreToolUse on Write | Blocks a file under a new top-level directory that is neither gitignored nor covered by the data backup |
| `check_sealed_read.py` | PreToolUse on Read/Grep/Glob/Bash | Blocks reads of a private notes directory kept outside the repo |
| `check_gmail_read_mcp.py` | PreToolUse on the Gmail read MCP tools | Blocks MCP reads, including the label list, and points to `tools/gmail_fetch.py --search` or `--list-labels` instead |
| `check_draft_pleasantry.py` | PreToolUse on Bash `open_draft.py` | Blocks a weekend or holiday-adjacent send whose body has no warm open or close |
| `check_draft_weekday.py` | PreToolUse on Bash `open_draft.py` | Blocks a draft that pairs a weekday with a date falling on a different weekday ("Monday, Mar 16" when Mar 16 is a Tuesday). The same parser makes the to-do and follow-up writers refuse a wrong pair. Known gap: a spaced or comma weekday list before a date range ("Wed / Mon 3/16-17") can false-block |
| `check_public_pii.py` | PreToolUse on Write/Edit/MultiEdit/NotebookEdit and Bash | Blocks a real contact or target-company name from the gitignored denylist landing in a tracked (public) file. High precision by design; `/audit-pii` is the semantic pass at commit time |
| `check_replace_all_safety.py` | PreToolUse on Edit/MultiEdit | Blocks `replace_all` on a short token that also appears inside longer words in the file, where a character-level replace would corrupt them |
| `check_table_integrity.py` | PostToolUse on Bash/Write/Edit/MultiEdit | Flags a markdown-table row whose field count differs from the rest of its table, the sign of an unescaped `\|` that shifts every later column. As a command-line scanner it rejects an unknown flag (exit 2) instead of scanning nothing |
| `check_pipe_close_via_update.py` | PreToolUse on Bash | Blocks closing a pipeline row with `pipe_write.py update`; closing is `remove --stage` |
| `check_pipeline_exit_status.py` | PreToolUse on Bash | Blocks reading a verdict from a shell pipeline whose last stage is a formatter, which hides the tested command's exit status |
| `check_scanner_examined_something.py` | PreToolUse on Bash | Blocks invoking a two-interface checker through the wrong entry point, where it exits 0 having scanned nothing |
| `check_heredoc_quoting.py` | PreToolUse on Bash | Fires on an unquoted heredoc whose body contains a backtick, which the shell would execute |
| `check_unquoted_glob.py` | PreToolUse on Bash | Fires on an unquoted glob passed to `--include=`/`--exclude=`, which zsh expands before the command runs |
| `check_screenshot_path.py` | PreToolUse on Bash | Fires on a hand-typed macOS screenshot filename with a regular space before AM/PM (the real name uses U+202F) |
| `check_workflow_scriptpath.py` | PreToolUse on Workflow | Guards against running a cached snapshot of a workflow script instead of the file on disk |
| `check_module_imports.py` | PostToolUse on Write/Edit/MultiEdit | Imports a Python module right after it is edited and warns if the import raises |
| `check_save_claims.py` | Stop | Catches a reply that says a file was written when no write happened, and a reply that says a file is missing or does not exist when a file by that name is on disk (in the repo, a sibling repo, `.claude/skills/`, or the memory folders). Conditionals and questions are skipped |
| `check_stale_file_claim.py` | Stop | Catches a reply that says a named file was not touched this session when the filesystem says it was |
| `check_derived_ratio.py` | Stop | Blocks a reply that states a ratio or percentage worked out in chat ("12 of 40 (30%)") when no tool printed that result; the fix is to compute it in a script and quote the output |

## Friction capture

Friction capture runs on a dedicated three-hook chain rather than a single hook (PostToolUse hooks never fire on tool errors, a documented Claude Code limitation). `check_script_error_logged.py` (PostToolUse on Bash) catches `tools/*.py` scripts that return a JSON error, plus any `python3` invocation (inline heredocs and skill helpers included) that crashes with a traceback. `log_tool_failure.py` (PostToolUseFailure on Bash/Edit/Write/MultiEdit) is the primary capture for outright tool-call failures. `scan_transcript_failures.py` (Stop hook) scans the session transcript at turn-end for harness-level errors the other two cannot see, for example "file not read" Edit rejections. All three append to `memory/friction-log.md`; an entry that recurs 3 or more times is flagged for a code fix.
