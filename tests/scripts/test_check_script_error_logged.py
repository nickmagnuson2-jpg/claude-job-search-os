"""The PostToolUse hook that auto-logs script friction to the ledger.

WHY IT HAD TO BE THIS ONE. check_script_error_logged.py is wired in .claude/settings.json
as a PostToolUse hook on `Bash`, runs on every single Bash call, is ~300 lines of regex
branching, and had no test file at all. Its failure mode is silent non-enforcement: the
friction ledger simply stops accruing rows, and the 1st-fire/2nd-fire/3rd-fire promotion
ladder that CLAUDE.md hangs off those occurrence counts quietly stops advancing. Nothing
errors. Nobody notices.

WHAT IT GUARDS. It is not a blocker -- it is a *recorder*. It watches the output of Bash
calls for two failure signatures and mechanically calls `tools/friction_log.py append` so
Claude never has to remember to log:
  Branch A -- a `tools/<name>.py` invoked via python whose output carries the
              {"status": "error"} JSON contract (minus a graceful-empty carve-out for
              scripts that use status:error to mean "nothing to do").
  Branch B -- any python invocation whose output carries a Python traceback (inline
              heredocs and skill helpers outside tools/, which have no JSON contract).

EXIT CODES IN THE PostToolUse POSITION. The tool call has already run, so nothing can be
blocked. Exit 2 in PostToolUse is not a block -- it feeds stderr back to Claude as a
message; exit 0 is the quiet path. This hook is WARN-tier by design and returns 0 on
*every* path, firing and clean alike. That means the exit code carries almost no signal
here, and a suite that only asserted `returncode == 0` would pass against a hook whose
body had been deleted. So every test below asserts exit 0 AND asserts the observable
effect: whether friction_log.py was invoked, with which argv, and what went to stderr.

WHY friction_log.py IS INTERCEPTED, NOT MOCKED AWAY. The hook shells out with a literal
`subprocess.run(["python3", FRICTION_LOG_PY, "append", ...])`, and friction_log.py appends
a row to the REAL memory/friction-log.md. A test suite that let that run would write
garbage rows into Nick's live ledger and inflate the very occurrence counts the promotion
ladder reads. Because the hook resolves `python3` through PATH (not sys.executable), the
tests put a recording shim named `python3` first on PATH: the real hook file, the real CLI
entry point, the real subprocess call -- and the callee replaced by a script that records
argv and prints a synthetic friction_log JSON response. We assert the invocation, never a
side effect on the real ledger.

TESTED THROUGH THE REAL ENTRY POINT, not the helpers. Per tools/HOOK_AUTHORING.md: a 53-test
suite once stayed green while the shipped hook was broken, because every test called the
helper directly. Everything here feeds JSON on stdin to the actual file at its actual path.
The one exception is the dedup group, which needs a fabricated ledger and therefore runs a
verbatim copy of the hook inside a tmp repo -- flagged where it happens.

BOTH DIRECTIONS CARRY EQUAL WEIGHT. A false negative stops the ledger accruing. A false
positive is just as bad in the other direction: it fabricates friction rows for successful
commands, inflates occurrence counts, and trips promotion gates that demand Nick's time.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "tools" / "check_script_error_logged.py"

# PostToolUse: 0 = quiet, 2 = surface stderr to Claude. This hook is exit-0-always.
QUIET = 0

ERROR_JSON = '{"status": "error", "message": "Row not found: acme-corp"}'
OK_JSON = '{"status": "ok", "message": "Updated 1 row"}'

TRACEBACK = (
    'Traceback (most recent call last):\n'
    '  File "/Users/x/.claude/skills/ss/ss_log_append.py", line 12, in <module>\n'
    '    main()\n'
    'FileNotFoundError: [Errno 2] No such file or directory: \'/tmp/shot.png\'\n'
)

SHIM = (
    "#!/bin/sh\n"
    'for a in "$@"; do printf "%s\\n" "$a" >> "$FRICTION_RECORD"; done\n'
    'printf "%s\\n" "$FRICTION_STDOUT"\n'
)


class Result:
    def __init__(self, proc: subprocess.CompletedProcess, record: Path):
        self.returncode = proc.returncode
        self.stderr = proc.stderr
        self._record = record

    @property
    def logged(self) -> bool:
        """True iff the hook actually shelled out to friction_log.py append."""
        return self._record.exists()

    @property
    def argv(self) -> list[str]:
        if not self._record.exists():
            return []
        return self._record.read_text(encoding="utf-8").splitlines()

    @property
    def surface(self) -> str:
        # argv = [friction_log.py, "append", <surface>, <nature>, "--fix", <text>]
        return self.argv[2]

    @property
    def nature(self) -> str:
        return self.argv[3]


@pytest.fixture
def hook_run(tmp_path):
    """Run the real hook with a recording `python3` shim ahead of the real one on PATH.

    The shim is what stops this suite from appending to the live friction ledger; see the
    module docstring. It records every argument the hook passes and answers with whatever
    JSON the test asked for, so the promotion-message branches are reachable without
    friction_log.py ever running.
    """
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "python3"
    shim.write_text(SHIM, encoding="utf-8")
    shim.chmod(0o755)
    record = tmp_path / "friction_log_argv.txt"

    def _run(command: str = "", stdout: str = "", stderr: str = "", *,
             tool_name: str = "Bash", raw: str | None = None,
             hook: Path = HOOK, promotion: str = "none", occurrences: int = 1,
             shim_on_path: bool = True) -> Result:
        payload = raw if raw is not None else json.dumps({
            "tool_name": tool_name,
            "tool_input": {"command": command},
            "tool_response": {"stdout": stdout, "stderr": stderr},
        })
        path = f"{shim_dir}:/usr/bin:/bin" if shim_on_path else str(tmp_path / "empty")
        env = {
            "PATH": path,
            "PYTHONIOENCODING": "utf-8",
            "FRICTION_RECORD": str(record),
            "FRICTION_STDOUT": json.dumps(
                {"status": "ok", "occurrences": occurrences,
                 "promotion_action": promotion}),
        }
        proc = subprocess.run([sys.executable, str(hook)], input=payload,
                              capture_output=True, text=True, env=env)
        return Result(proc, record)

    return _run


# --- Branch A: the tools/*.py JSON error contract ---------------------------

def test_a_tools_script_json_error_is_auto_logged(hook_run):
    """The core guarantee. If this stops firing, the ledger stops growing and every
    occurrence-count-driven promotion gate silently freezes."""
    r = hook_run("PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py update acme",
                 stdout=ERROR_JSON)
    assert r.returncode == QUIET
    assert r.logged, "friction_log.py was never invoked"


def test_the_logged_surface_is_the_script_that_failed(hook_run):
    """The ledger dedupes and counts on (surface, nature). A wrong surface splits one
    recurring friction into two rows that each look like a first fire."""
    r = hook_run("python3 tools/pipe_write.py update acme", stdout=ERROR_JSON)
    assert r.surface == "pipe_write.py"


def test_the_logged_nature_carries_the_json_message_and_the_auto_tag(hook_run):
    """The [auto] tag is how a hook-written row is told apart from one Claude wrote by
    hand. Losing it makes the ledger's provenance unreadable."""
    r = hook_run("python3 tools/pipe_write.py update acme", stdout=ERROR_JSON)
    assert r.nature == "[auto] Row not found: acme-corp"


def test_the_error_signal_is_read_from_stderr_as_well_as_stdout(hook_run):
    """Several tools print the JSON error to stderr. Reading only stdout would miss them
    entirely -- a whole class of friction that never reaches the ledger."""
    r = hook_run("python3 tools/pipe_write.py update acme", stderr=ERROR_JSON)
    assert r.logged and r.surface == "pipe_write.py"


def test_a_pipe_in_the_message_is_neutralised_before_it_reaches_the_ledger(hook_run):
    """The ledger is a markdown table. An unescaped | shifts every later cell one column
    left, so surface/nature/occurrences silently desynchronise for that row onward."""
    r = hook_run("python3 tools/pipe_write.py update acme",
                 stdout='{"status": "error", "message": "bad col a|b|c"}')
    assert "|" not in r.nature
    assert r.nature == "[auto] bad col a/b/c"


def test_a_long_message_is_truncated_so_one_row_cannot_swamp_the_table(hook_run):
    """A multi-line Usage dump pasted whole into a table cell makes the ledger unreadable
    and breaks the 40-char dedup key's usefulness."""
    r = hook_run("python3 tools/pipe_write.py update acme",
                 stdout='{"status": "error", "message": "%s"}' % ("x" * 200))
    assert len(r.nature) <= len("[auto] ") + 120


def test_a_message_over_the_extractor_limit_still_produces_a_bounded_nature(hook_run):
    """Documented as found, not as designed: the message regex only matches up to 300
    chars, so a longer message falls through to the raw-first-line path and lands a
    200-char nature that includes the JSON envelope. Ugly, but still bounded and still
    logged -- the failure mode to guard against is an unbounded or empty cell."""
    r = hook_run("python3 tools/pipe_write.py update acme",
                 stdout='{"status": "error", "message": "%s"}' % ("x" * 400))
    assert r.logged
    assert len(r.nature) <= len("[auto] ") + 200


def test_an_error_with_no_json_message_falls_back_to_the_first_output_line(hook_run):
    """A script can emit {"status":"error"} with no message field. Falling through to no
    nature at all would drop the row; a generic nature at least preserves the count."""
    r = hook_run("python3 tools/pipe_write.py update acme",
                 stdout='ledger write failed unexpectedly\n{"status":"error"}')
    assert r.logged
    assert r.nature.startswith("[auto] ")
    assert r.nature != "[auto] "


def test_the_fix_field_is_filled_with_a_placeholder_not_left_empty(hook_run):
    """friction_log.py rejects an empty justification, and an empty --fix would make the
    auto-append fail at exactly the moment the friction happened."""
    r = hook_run("python3 tools/pipe_write.py update acme", stdout=ERROR_JSON)
    assert r.argv[4] == "--fix"
    assert r.argv[5].strip(), "--fix passed empty"


# --- Branch A: the graceful-empty carve-out ---------------------------------

@pytest.mark.parametrize("message", [
    "No task found matching: prep acme",
    "No matches for that query",
    "Nothing to close",
    "Already closed",
    "File not found",
], ids=["no-task", "no-matches", "nothing-to", "already-closed", "not-found"])
def test_a_nothing_to_do_result_is_not_logged_as_friction(hook_run, message):
    """These scripts use status:error to mean "no work here" -- sweeps, idempotent done,
    query-no-match. Logging them fabricates friction from ordinary success and inflates
    occurrence counts toward promotion gates that cost Nick real time. Caught live on
    2026-05-21 when a /checkout sweep auto-logged an absent prep todo."""
    r = hook_run("python3 tools/todo_write.py done prep",
                 stdout='{"status": "error", "message": "%s"}' % message)
    assert r.returncode == QUIET
    assert not r.logged, f"graceful-empty logged as friction: {message}"


def test_a_real_error_that_merely_mentions_a_graceful_word_is_still_logged(hook_run):
    """The carve-out is anchored to the message field's opening. A genuine failure whose
    text happens to contain "not found" mid-sentence must not inherit the exemption."""
    r = hook_run("python3 tools/pipe_write.py update acme",
                 stdout='{"status": "error", "message": "schema column not found in row"}')
    assert r.logged


# --- Branch A: self-referential exclusions ----------------------------------

@pytest.mark.parametrize("script", ["friction_log.py", "check_script_error_logged.py"])
def test_the_logger_and_the_hook_itself_never_log_themselves(hook_run, script):
    """Without this the ledger feeds itself: a failing friction_log.py call logs a
    friction, which invokes friction_log.py, which fails again."""
    r = hook_run(f"python3 tools/{script} append x y", stdout=ERROR_JSON)
    assert r.returncode == QUIET
    assert not r.logged


# --- clean cases that must pass untouched -----------------------------------

def test_a_successful_script_run_logs_nothing(hook_run):
    """The overwhelmingly common case -- this hook runs on EVERY Bash call."""
    r = hook_run("python3 tools/pipe_write.py update acme", stdout=OK_JSON)
    assert r.returncode == QUIET
    assert not r.logged


def test_the_word_error_in_successful_output_does_not_trigger_a_log(hook_run):
    """The original substring match fired on the word "error" inside successful output
    (todo_daily_metrics.py, 2026-05-21). Only the literal "status": "error" contract counts."""
    r = hook_run("python3 tools/todo_daily_metrics.py",
                 stdout='{"status": "ok", "error_count": 0, "notes": "no errors today"}')
    assert not r.logged


def test_a_help_invocation_is_never_logged(hook_run):
    """Usage output is intentionally an error exit. Logging it would file a friction row
    every time anyone read a script's help."""
    r = hook_run("python3 tools/pipe_write.py --help", stdout=ERROR_JSON)
    assert r.returncode == QUIET
    assert not r.logged


def test_a_non_bash_tool_event_is_ignored(hook_run):
    """PostToolUse payloads for other tools have no command to judge; the settings matcher
    is Bash but the hook must not depend on the matcher being right."""
    r = hook_run("python3 tools/pipe_write.py update acme", stdout=ERROR_JSON,
                 tool_name="Write")
    assert r.returncode == QUIET
    assert not r.logged


def test_an_empty_command_is_ignored(hook_run):
    r = hook_run("", stdout=ERROR_JSON)
    assert r.returncode == QUIET
    assert not r.logged


def test_a_json_error_from_something_that_is_not_a_python_tools_script_is_ignored(hook_run):
    """The hook only claims the tools/*.py contract. An arbitrary CLI printing similar
    JSON has no surface name the ledger could dedupe on."""
    r = hook_run("curl -s https://example.com/api", stdout=ERROR_JSON)
    assert not r.logged


def test_a_tools_path_quoted_inside_an_echo_is_not_treated_as_an_invocation(hook_run):
    """Command strings carry grep patterns and commit messages. Matching a bare path
    would file friction against a script that never ran."""
    r = hook_run('grep -n "tools/pipe_write.py" docs/tools-reference.md',
                 stdout=ERROR_JSON)
    assert not r.logged


def test_a_missing_tool_response_is_ignored(hook_run):
    """Not every PostToolUse payload carries stdout/stderr. Treating absent output as
    matchable would crash the hook on a routine call."""
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": "python3 tools/pipe_write.py x"}})
    r = hook_run(raw=payload)
    assert r.returncode == QUIET
    assert not r.logged


# --- Branch B: python tracebacks outside the JSON contract ------------------

def test_an_inline_python_heredoc_traceback_is_auto_logged(hook_run):
    """Branch B exists because the ss-skill U+202F FileNotFoundError fired twice while
    invisible to Branch A: an inline heredoc importing a skill helper never emits the
    tools/*.py JSON contract."""
    r = hook_run("python3 - <<'EOF'\nimport ss_log_append\nEOF", stderr=TRACEBACK)
    assert r.returncode == QUIET
    assert r.logged


def test_a_traceback_surface_falls_back_to_the_skill_named_in_the_command(hook_run):
    r = hook_run("python3 /Users/x/.claude/skills/ss/ss_log_append.py shot.png",
                 stderr=TRACEBACK)
    assert r.surface == "ss skill (inline)"


def test_a_traceback_surface_prefers_a_tools_script_when_the_command_names_one(hook_run):
    """A tools script that crashes instead of emitting JSON must land on the same ledger
    surface as its JSON-contract failures, or one recurring defect splits into two rows."""
    r = hook_run("python3 tools/pipe_write.py update acme", stderr=TRACEBACK)
    assert r.surface == "pipe_write.py"


def test_a_traceback_surface_falls_back_to_the_deepest_real_frame(hook_run):
    """With no tools script and no skill path in the command, the traceback's own frames
    are the only naming signal left."""
    r = hook_run("python3 - <<'EOF'\nrun()\nEOF", stderr=TRACEBACK)
    assert r.surface == "ss_log_append.py (inline)"


def test_the_traceback_nature_is_the_final_exception_line(hook_run):
    """The exception class plus message is what makes two fires recognisable as the same
    friction; a generic "python crashed" would never dedupe."""
    r = hook_run("python3 - <<'EOF'\nrun()\nEOF", stderr=TRACEBACK)
    assert r.nature.startswith("[auto] FileNotFoundError:")


def test_a_traceback_from_the_logger_itself_is_excluded(hook_run):
    """Same infinite-loop guard as Branch A, on the branch that has no graceful-empty
    carve-out to stop it."""
    r = hook_run("python3 tools/friction_log.py append a b", stderr=TRACEBACK)
    assert r.returncode == QUIET
    assert not r.logged


def test_a_traceback_in_output_with_no_python_invocation_is_not_logged(hook_run):
    """Reading a log file full of old tracebacks, or grepping for one, is not a crash.
    Firing here would file friction against a command that succeeded."""
    r = hook_run("cat tools/launchd/logs/nightly.err", stdout=TRACEBACK)
    assert r.returncode == QUIET
    assert not r.logged


def test_a_python_command_that_succeeded_is_not_logged(hook_run):
    r = hook_run("python3 - <<'EOF'\nprint('ok')\nEOF", stdout="ok\n")
    assert not r.logged


# --- what the hook tells Claude on stderr -----------------------------------

def test_a_first_fire_reports_the_occurrence_count(hook_run):
    """stderr is the only channel back to Claude here. If it goes quiet, the auto-log is
    invisible and Claude cannot update the row's --fix once resolved."""
    r = hook_run("python3 tools/pipe_write.py x", stdout=ERROR_JSON, occurrences=1)
    assert "pipe_write.py" in r.stderr
    assert "1" in r.stderr


def test_a_second_fire_surfaces_the_memory_tier_promotion_instruction(hook_run):
    """2nd fire = write feedback_<slug>.md. This message IS the enforcement of the ladder;
    without it the ladder is prose nobody reads."""
    r = hook_run("python3 tools/pipe_write.py x", stdout=ERROR_JSON,
                 promotion="memory", occurrences=2)
    assert "PROMOTION TRIGGERED" in r.stderr


def test_a_third_fire_surfaces_the_mandatory_script_patch_instruction(hook_run):
    r = hook_run("python3 tools/pipe_write.py x", stdout=ERROR_JSON,
                 promotion="script-patch", occurrences=3)
    assert "MANDATORY SCRIPT PATCH" in r.stderr


def test_an_unrecognised_promotion_value_still_reports_rather_than_going_silent(hook_run):
    """A future friction_log.py value must degrade to a visible line, not to nothing."""
    r = hook_run("python3 tools/pipe_write.py x", stdout=ERROR_JSON, promotion="escalate")
    assert "escalate" in r.stderr


def test_unparseable_logger_output_does_not_suppress_the_stderr_notice(hook_run, tmp_path):
    """If friction_log.py's response shape changes, Claude must still learn a row was
    written -- silently swallowing it is how the ledger drifts out of sync unnoticed."""
    r = hook_run("python3 tools/pipe_write.py x", stdout=ERROR_JSON,
                 promotion="none", occurrences=1)
    assert r.returncode == QUIET
    assert "friction-log" in r.stderr


def test_a_failing_logger_invocation_never_breaks_the_bash_call(hook_run):
    """PATH without python3 makes the shell-out raise. A recorder that crashed the
    PostToolUse chain would be worse than one that records nothing."""
    r = hook_run("python3 tools/pipe_write.py x", stdout=ERROR_JSON, shim_on_path=False)
    assert r.returncode == QUIET
    assert "auto-log failed" in r.stderr


# --- dedup: the same friction twice in a row is one row ---------------------

@pytest.fixture
def sandbox_hook(tmp_path):
    """A verbatim copy of the hook inside a throwaway repo.

    The dedup path reads REPO_ROOT/memory/friction-log.md, and REPO_ROOT is derived from
    the hook's own __file__. Exercising it against the real ledger would either depend on
    Nick's live rows (nondeterministic) or require writing to them (forbidden). Copying
    the unmodified file relocates only the paths it computes, not its logic.
    """
    tools = tmp_path / "repo" / "tools"
    tools.mkdir(parents=True)
    (tmp_path / "repo" / "memory").mkdir()
    dest = tools / HOOK.name
    shutil.copyfile(HOOK, dest)
    return dest


def _write_ledger(hook_path: Path, rows: list[str]) -> None:
    body = ("# Friction Log\n\n## Entries\n\n"
            "| Date | Surface | Nature | Fix | Occurrences | Promotion |\n"
            "|---|---|---|---|---|---|\n" + "".join(rows))
    (hook_path.parents[1] / "memory" / "friction-log.md").write_text(body, encoding="utf-8")


def test_the_same_surface_and_nature_in_the_recent_rows_is_not_logged_twice(
        hook_run, sandbox_hook):
    """A retry loop would otherwise file the identical friction five times and vault it
    straight past the 2nd- and 3rd-fire promotion gates on a single defect."""
    _write_ledger(sandbox_hook,
                  ["| 2026-09-01 | pipe_write.py | [auto] Row not found: acme-corp | x | 1 | none |\n"])
    r = hook_run("python3 tools/pipe_write.py update acme", stdout=ERROR_JSON,
                 hook=sandbox_hook)
    assert r.returncode == QUIET
    assert not r.logged
    assert "duplicate" in r.stderr


def test_a_different_nature_on_the_same_surface_is_still_logged(hook_run, sandbox_hook):
    """Dedup must key on the friction, not the script. Suppressing every repeat from one
    surface would hide genuinely new defects behind an old row."""
    _write_ledger(sandbox_hook,
                  ["| 2026-09-01 | pipe_write.py | [auto] something else entirely | x | 1 | none |\n"])
    r = hook_run("python3 tools/pipe_write.py update acme", stdout=ERROR_JSON,
                 hook=sandbox_hook)
    assert r.logged


def test_an_older_matching_row_beyond_the_lookback_window_does_not_suppress(
        hook_run, sandbox_hook):
    """The window is deliberately short: the same friction recurring next week IS a
    second occurrence and must be counted, or the ladder never advances."""
    filler = ["| 2026-09-0%d | other_tool.py | [auto] noise %d | x | 1 | none |\n" % (i, i)
              for i in range(1, 7)]
    _write_ledger(sandbox_hook,
                  filler + ["| 2026-08-01 | pipe_write.py | [auto] Row not found: acme-corp | x | 1 | none |\n"])
    r = hook_run("python3 tools/pipe_write.py update acme", stdout=ERROR_JSON,
                 hook=sandbox_hook)
    assert r.logged


def test_an_absent_ledger_does_not_suppress_the_first_ever_row(hook_run, sandbox_hook):
    """First run on a fresh clone. Treating "no ledger" as "already logged" would mean
    the ledger could never be created."""
    r = hook_run("python3 tools/pipe_write.py update acme", stdout=ERROR_JSON,
                 hook=sandbox_hook)
    assert r.logged


def test_a_ledger_with_no_entries_section_does_not_suppress(hook_run, sandbox_hook):
    (sandbox_hook.parents[1] / "memory" / "friction-log.md").write_text(
        "# Friction Log\n\nnotes only\n", encoding="utf-8")
    r = hook_run("python3 tools/pipe_write.py update acme", stdout=ERROR_JSON,
                 hook=sandbox_hook)
    assert r.logged


# --- malformed input --------------------------------------------------------

def test_malformed_stdin_fails_open(hook_run):
    """Fail-open is deliberate: a hook-internal problem must never disturb a Bash call
    that already ran. The cost is that a crash is silent -- which is why every test above
    asserts an observable effect rather than just the exit code."""
    r = hook_run(raw="{not json")
    assert r.returncode == QUIET
    assert not r.logged


def test_empty_stdin_fails_open(hook_run):
    r = hook_run(raw="")
    assert r.returncode == QUIET
    assert not r.logged


@pytest.mark.parametrize("payload", [
    {},
    {"tool_name": "Bash"},
    {"tool_name": "Bash", "tool_input": {}},
    {"tool_name": "Bash", "tool_input": {"command": None}, "tool_response": None},
    {"tool_name": None, "tool_input": {"command": "python3 tools/pipe_write.py x"}},
], ids=["empty", "name-only", "no-command", "null-fields", "null-tool-name"])
def test_incomplete_payloads_are_ignored_without_logging(hook_run, payload):
    """PostToolUse payload shapes vary by tool and by Claude Code version. Any of these
    crashing the hook would put a traceback into every Bash call's hook chain."""
    r = hook_run(raw=json.dumps(payload))
    assert r.returncode == QUIET
    assert not r.logged


# --- the assertion that has to fail first -----------------------------------

def test_the_hook_exists_and_its_pattern_tables_are_populated():
    """Nearly every assertion above is a "did not log" assertion, and all of them are
    satisfied by a hook that never logs anything -- or by a file that no longer exists.
    If the file moved, or a pattern list went empty, or the dedup window collapsed to
    zero, this fails before any of them can pass vacuously."""
    assert HOOK.is_file(), f"hook not found at {HOOK}"
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import check_script_error_logged as mod

    assert len(mod.GRACEFUL_EMPTY_PATTERNS) >= 5, "graceful-empty carve-out emptied"
    assert {"friction_log.py", "check_script_error_logged.py"} <= mod.EXCLUDE_SCRIPTS
    assert mod.DEDUP_LOOKBACK_ROWS >= 1, "a zero window disables dedup entirely"
    assert mod.SCRIPT_RE.search("python3 tools/pipe_write.py x")
    assert mod.ERROR_MARKER_RE.search(ERROR_JSON)
    assert mod.TRACEBACK_RE.search(TRACEBACK)
    assert mod.PY_INVOKE_RE.search("python3 - <<'EOF'")
    assert mod.FRICTION_LOG_PY.name == "friction_log.py"
    assert mod.LEDGER.name == "friction-log.md"


def test_the_hook_is_still_wired_as_a_posttooluse_bash_hook():
    """The suite measures a hook that only matters while settings.json runs it. An
    unwired hook passes every test above and enforces nothing -- the exact silent
    non-enforcement this file was written to detect."""
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    wired = [h["command"]
             for entry in settings["hooks"]["PostToolUse"]
             if entry.get("matcher") == "Bash"
             for h in entry.get("hooks", [])]
    assert any("check_script_error_logged.py" in c for c in wired), wired
