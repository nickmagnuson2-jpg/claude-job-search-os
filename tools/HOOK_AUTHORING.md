# Authoring a `check_*.py` Bash/Write hook

Scaffold every new PreToolUse hook from this. It bakes in the blind-spot class
that bit `check_bare_python.py` twice on 2026-06-02 (command-position-not-substring
family — see `memory/feedback_command_hook_match_position_not_substring.md`). Copy
the snippets, fill the TODOs, and do NOT skip the smoke step.

## The failure class this prevents

A command-detection hook scans the **raw command string** — which includes
arguments, quoted strings, grep patterns, commit messages, and heredoc bodies. A
naive `\btoken\b` match fires on the token wherever it appears, not only where
it's actually *invoked*. The cost of a false-positive BLOCK is high: it blocks
your own correct work (a grep, a commit) with no recourse but to reword.

Three blind spots, each found one shape at a time:
1. **Substring** — `python-dateutil`, `python.md` (fixed by a command-position anchor + negative lookahead).
2. **Quoted literal** — `grep "Bash(python "` (the `(` looks like shell grouping but is inside a string; fixed by stripping quoted spans).
3. **Heredoc body** — `git commit -F - <<'EOF' … python … EOF` message bodies (FIXED 2026-06-05: `tools/hook_command_lint.py` strips heredoc bodies — quoted-delimiter heredocs fully, unquoted ones only when they hold no `$()`/backtick so a real substitution is still caught).

**All three live in one shared module now: `tools/hook_command_lint.py` (`strip_literals`).** Do NOT re-implement the strip logic per hook — that drift is what caused this family's 4th/5th fires (the fix landed in one hook's copy, not the other's). Import the shared function.

## Two kinds of hook — classify BEFORE you scaffold

Everything above is about **command hooks** (PreToolUse on `Bash`), whose false-positive
surface is *command position*. A **content hook** (PreToolUse on `Write|Edit`) has a
completely different false-positive surface: **path scope**. Scaffolding a content hook
from the command-hook checklist gets you a correct matcher pointed at the wrong files.

| | Command hook (`Bash`) | Content hook (`Write|Edit`) |
|---|---|---|
| Scans | the raw command string | the file content + target path |
| FP surface | token appears but is not invoked | **path glob catches files it must not judge** |
| Canonical trap | `grep "python "` | its own test fixtures |

**A content hook MUST exclude its own fixtures, or it makes the suite unrunnable.** A hook
scoped to `output/<slug>/*prep*.md` also matches `tests/fixtures/w4/output/acme-corp/081026-prep.md`
— a file that contains the broken pattern *on purpose* — and `output/analysis/` specs that
quote the format as prose. Without those exclusions the W4 suite would not have run at all.
Origin: 2026-08-12, `check_prep_doc_format.py`; see
`memory/feedback_hook_fp_surface_depends_on_hook_type.md`.

**Fire only when the content is complete enough to judge.** A content hook that trips
mid-authoring blocks the author from reaching a valid state. `check_prep_doc_format.py`
fires only when BOTH proof lines are present, so a half-written doc is never blocked.

## Step 0 — probe the capability before designing around it

Before writing a hook that depends on a tool flag, an exit code, or an output field,
run the thing and read what it actually emits. A capability assumed from a docstring is
the same class of error as a claim asserted from memory: the docstring of
`check_no_confabulation.py` still advertises an uncited-number warn that **no code
implements**, and a reader would reasonably conclude the extension had shipped.
Per `memory/feedback_guard_must_hard_abort_on_empty_input.md`.

## Checklist

- [ ] **Classify the hook first** — command-scanner or content-scanner? Use the matching
      FP discipline (command position vs path scope). Content hooks: exclude `tests/` and
      any spec/analysis directory that quotes the pattern as prose.
- [ ] **Content hooks: test the real entry point, not just the helper.** A guarantee the
      CLI advertises needs a test that RUNS the CLI and content-scans its output. A 53-test
      suite stayed green while a mutant dropped the output filter in `main()`, because every
      test called the helper directly. Never emit a hardcoded "guarantees" block — that is
      self-attestation, not evidence. (`feedback_guard_tests_must_cover_shipped_surface.md`)
- [ ] **Run one mutation pass per advertised guarantee.** Break the behavior on purpose and
      confirm a test fails. Two of eighteen mutants survived a 124-test suite because every
      assertion checked internal consistency rather than coverage of known-present items.
- [ ] **Extractors: assert that named known items ARE captured, and near-misses are not.**
      Self-consistency is not coverage — a detector can be perfectly consistent about the
      39 lines it sees and silently miss the 201 it does not.
- [ ] **Anchor the token to command position** — start, or after a separator (`|`, `;`, `&`, `(`, newline), with optional leading `VAR=val`. Never a bare substring.
- [ ] **Strip quoted literals before matching** so a boundary char inside a string isn't read as shell syntax.
- [ ] **Negative lookahead** for adjacent word chars / `.` / `-` so prefixed names (`python3`, `python-dateutil`, `python.md`) stay clean.
- [ ] **Exclude backtick as a boundary** — `` `token` `` in prose/markdown is inline code, not command substitution; `$(...)` is already covered by `(`.
- [ ] **Fail open** on bad JSON / empty command (exit 0).
- [ ] **BLOCK (exit 2) only for unambiguous, single-correction violations** (per `feedback_warn_vs_block_hook_design.md`). PreToolUse WARN-via-exit-0+stderr is NOT surfaced by Claude Code — a true "warn" is invisible, so a real warning must be exit 2 with a corrective message, reserved for cases that always fail anyway.
- [ ] **Distinguishable block message** — make the BLOCKED line specific enough that a transcript scan can tell a true catch from a false-positive.
- [ ] **Log false-positives manually — this is the ONLY telemetry path for them.** Auto-capture cannot see or classify a hook block: a block is intent-ambiguous (a correct catch vs a wrong block look identical, and only the agent in-context knows which). The PostToolUse auto-logger is blind to PreToolUse blocks entirely; the transcript-scan backfill miscounts a false-positive as another occurrence of the thing being blocked. So if a hook blocks work you believe is legitimate, log it *at that moment*: `PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append <hook>.py "FP: <what got wrongly blocked>"`. A distinct nature keeps it separate from the true-positive row. Origin: 2026-06-02 #4 investigation. **PARKED — auto-logger PreToolUse-block blindness. REOPEN gate: if a buggy hook is discovered late because its false-positives were invisible/miscounted in the ledger, build a stderr FP-logging hint for BLOCK-tier hooks.**
- [ ] **Write the test file** with BOTH block cases AND clean cases — including token-in-quoted-pattern and token-in-heredoc.
- [ ] **Smoke it live** before trusting it: pipe a real payload through, and run the actual command you expect to be clean (e.g. the grep you'll use to verify the sweep). Unit tests use inputs you chose; smoke surfaces the input shapes you didn't imagine (`feedback_smoke_test_catches_unit_test_blind_spots.md`).

## Canonical detection snippet

```python
import os
import re
import sys

# Shared literal-context stripping (quoted spans + heredoc bodies). Single source
# of truth — never re-implement per hook (that drift caused the 4th/5th fires).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_command_lint import strip_literals  # noqa: E402

# Command boundary = start | newline | separator. Backtick EXCLUDED (inline code).
TOKEN = re.compile(r"(?:^|[\n;&|(])\s*(?:\w+=\S+\s+)*TODO_TOKEN(?![\w.\-])")

# match on:  TOKEN.search(strip_literals(command))
```

## Canonical test parametrize

```python
@pytest.mark.parametrize("command", [
    "TODO_token foo",                 # command position
    "ls; TODO_token x",               # after separator
    'echo "$(TODO_token x)"',         # cmd-subst inside double quotes (still real)
])
def test_blocks(command): assert _run(command) == 2

@pytest.mark.parametrize("command", [
    'git commit -m "fix TODO_token bug"',   # word in commit message
    "grep TODO_token f",                    # word as grep pattern
    'grep "X(TODO_token " dir/',            # boundary char INSIDE a quoted pattern
    "rg '(TODO_token' src/",                # boundary char inside single quotes
    "git commit -F - <<'EOF'\nTODO_token in a message body\nEOF",  # heredoc body
])
def test_allows(command): assert _run(command) == 0
```

## Exemplar

`tools/check_bare_python.py` + `tests/scripts/test_check_bare_python.py` are the
live reference — command-position regex, the shared `strip_literals()` import, and
the full clean/block parametrize including the quoted-pattern and heredoc-body
cases. The strip logic itself lives in `tools/hook_command_lint.py` with its own
unit tests (`tests/scripts/test_hook_command_lint.py`). Copy from there.
