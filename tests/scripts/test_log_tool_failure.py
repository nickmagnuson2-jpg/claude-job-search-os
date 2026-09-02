"""The PostToolUseFailure hook that feeds the friction ledger.

WHY IT HAD TO BE THIS ONE. log_tool_failure.py is wired in .claude/settings.json on
`Bash|Edit|Write|MultiEdit` and had no suite of its own. It is the PRIMARY capture path for
memory/friction-log.md (scan_transcript_failures.py is the belt-and-suspenders Stop hook
behind it). Its failure mode is silent non-recording: nothing errors, failures simply stop
being written down. When that happens the 1st-fire/2nd-fire/3rd-fire promotion ladder in
CLAUDE.md quietly stops counting -- a recurring tooling defect never reaches "memory (do
now)" or "script-patch (mandatory)", and no one is told. The ledger looks healthy because a
ledger that records nothing is indistinguishable from a week with no friction.

WHAT IT GUARDS. Every tool-call failure is converted into (surface, nature, exit code) and
appended through friction_log.py, which owns dedup and the promotion ladder. The hook's own
job is attribution and suppression: name the right surface (the mis-called script, not
`bash:cd`), and drop the four classes of non-friction that would otherwise drown the real
rows -- help-call exits, its own infrastructure scripts (infinite-loop guard), graceful-empty
"nothing to do" returns, and masked benign nonzeros from chained shell commands.

TESTED THROUGH THE REAL ENTRY POINT, not the helpers. Per tools/HOOK_AUTHORING.md: a 53-test
suite once stayed green while the shipped hook was broken, because every test called the
helper directly. Every test below spawns the CLI and feeds it JSON on stdin.

THE WRITE SEAM. The hook shells out to friction_log.py with `os.environ.copy()`, and
friction_log.py resolves its ledger from `$FRICTION_LEDGER`. Tests therefore point that at a
tmp_path ledger and assert on its rows. Every fixture error string carries a sentinel token,
and an autouse guard fails the run if that token ever appears in the real
memory/friction-log.md.

BOTH DIRECTIONS MATTER, ASYMMETRICALLY. A false negative loses a real friction row forever
(the ladder undercounts). A false positive fills the ledger with benign stdout, which is how
the 2026-07-08 audit found ten rows reading "On branch main". Recording nothing and recording
everything both end with the ledger being ignored.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "tools" / "log_tool_failure.py"
REAL_LEDGER = REPO_ROOT / "memory" / "friction-log.md"

# The hook is capture-only. PostToolUseFailure runs AFTER the tool already failed, so a
# nonzero exit here cannot undo anything -- it only injects hook noise back into the session
# on top of the failure the user is already reading. 0 is the only correct code on every
# path, including the ones where the hook decides not to record.
OK = 0

LEDGER_SEED = (
    "# Friction Log\n\n"
    "## Entries\n\n"
    "| Date | Surface | Nature | Fix | Occurrences | Promotion |\n"
    "|---|---|---|---|---|---|\n"
)


# --- the real ledger must never be touched ----------------------------------

# Embedded in every fixture error string. If a fixture row ever reaches the live ledger this
# token is the fingerprint that proves it. Content-based rather than a whole-file hash on
# purpose: the live ledger is written by real hooks in other sessions while this suite runs,
# so a hash comparison reports concurrency as a leak.
SENTINEL = "ltf-fixture-sentinel"


@pytest.fixture(autouse=True)
def real_ledger_untouched():
    """Hard stop if a test ever writes a fixture row into the live friction ledger.

    The seam is one env var away from not being set. Without this, a regression in the
    redirect would silently pollute Nick's real ledger with fixture rows -- and those rows
    would then count toward the promotion ladder, which is the very mechanism this hook
    exists to feed."""
    yield
    if REAL_LEDGER.exists():
        assert SENTINEL not in REAL_LEDGER.read_text(encoding="utf-8"), (
            "a fixture row reached the real memory/friction-log.md; the FRICTION_LEDGER "
            "redirect is not holding"
        )


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    p = tmp_path / "friction-log.md"
    p.write_text(LEDGER_SEED, encoding="utf-8")
    return p


# --- driving the real CLI ---------------------------------------------------

def run(payload, ledger: Path | None = None, *, raw: str | None = None
        ) -> subprocess.CompletedProcess:
    """Spawn the hook exactly as settings.json does: JSON on stdin, nothing else."""
    env = {
        "PYTHONIOENCODING": "utf-8",
        # The hook shells out to bare `python3`, so the interpreter must be on PATH.
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
    }
    if ledger is not None:
        env["FRICTION_LEDGER"] = str(ledger)
    stdin = raw if raw is not None else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=stdin,
                          capture_output=True, text=True, env=env)


def bash_failure(command: str, *, stderr: str = "Traceback (most recent call last):\n"
                 '  File "tools/x.py", line 1, in <module>\nTypeError: bad call',
                 exit_code: int | None = 1) -> dict:
    fd: dict = {"stderr": f"{stderr} {SENTINEL}"}
    if exit_code is not None:
        fd["exit_code"] = exit_code
    return {
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "failure_details": fd,
    }


def rows(ledger: Path) -> list[dict]:
    """Data rows of the tmp ledger, parsed into cells."""
    out = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or cells[0] in ("Date", "---"):
            continue
        out.append(dict(zip(
            ["date", "surface", "nature", "fix", "occurrences", "promotion"], cells)))
    return out


# --- the assertion that has to fail first -----------------------------------

def test_the_hook_exists_and_its_suppression_lists_are_populated():
    """Most tests below assert that something was NOT recorded. Every one of those is also
    satisfied by a hook that records nothing at all, or by a file that has moved. If the
    exclusion list emptied or the graceful-empty patterns were dropped, the suppression tests
    would keep passing for the wrong reason -- this fails first instead."""
    assert HOOK.is_file(), f"hook not found at {HOOK}"
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import log_tool_failure as mod

    assert mod.EXCLUDE_SCRIPTS, "empty exclusion list = the hook can log its own failures"
    assert "log_tool_failure.py" in mod.EXCLUDE_SCRIPTS
    assert "friction_log.py" in mod.EXCLUDE_SCRIPTS
    assert len(mod.GRACEFUL_EMPTY_PATTERNS) >= 5
    assert mod.HELP_RE.search("tools/x.py --help")
    # The seam this whole suite depends on. If friction_log stops honouring the env var,
    # every "was recorded" assertion below would be reading an empty tmp ledger.
    import friction_log
    assert "FRICTION_LEDGER" in (REPO_ROOT / "tools" / "friction_log.py").read_text()
    assert friction_log.LEDGER  # resolved, not None


# --- a real failure is recorded, attributed to the right surface ------------

def test_a_failing_tools_script_is_recorded_under_that_script_as_the_surface(ledger):
    """The whole point of the ledger is per-surface counting. Attributing a pipe_write.py
    failure to `bash:python3` would scatter its occurrences and it would never trip the
    ladder."""
    run(bash_failure("PYTHONIOENCODING=utf-8 python3 tools/pipe_write.py add --bad"), ledger)
    r = rows(ledger)
    assert len(r) == 1
    assert r[0]["surface"] == "pipe_write.py"


def test_the_recorded_nature_is_the_exception_line_not_the_traceback_header(ledger):
    """"Traceback (most recent call last):" is identical for every Python failure ever. If
    that became the nature, dedup would merge unrelated defects into one inflated row and
    promote the wrong thing."""
    run(bash_failure("python3 tools/pipe_write.py add"), ledger)
    assert rows(ledger)[0]["nature"] == f"[auto] TypeError: bad call {SENTINEL}"


def test_the_nature_carries_the_auto_provenance_tag(ledger):
    """The [auto] tag is how a hook-captured row is told apart from one Nick logged by hand
    with real context. dedup's false-positive sweep keys off it."""
    run(bash_failure("python3 tools/pipe_write.py add"), ledger)
    assert rows(ledger)[0]["nature"].startswith("[auto]")


def test_the_exit_code_is_preserved_in_the_fix_cell(ledger):
    """exit=? versus exit=2 is what later distinguishes a real failure from a masked benign
    nonzero during the retroactive dedup pass."""
    run(bash_failure("python3 tools/pipe_write.py add", exit_code=2), ledger)
    assert "exit=2" in rows(ledger)[0]["fix"]


def test_a_non_bash_tool_failure_is_recorded_under_a_tool_prefixed_surface(ledger):
    """The matcher covers Edit|Write|MultiEdit too. Those have no command to parse, so if the
    Bash-shaped path were the only one, every Edit and Write failure would go unrecorded."""
    run({"tool_name": "Edit", "tool_input": {"file_path": "/repo/x.md"},
         "failure_details": {"exit_code": 1,
                             "error_type": "Error: File has not been read yet"}}, ledger)
    assert rows(ledger)[0]["surface"] == "tool:Edit"


@pytest.mark.parametrize("command,error,expected", [
    ("git commit -m x",
     "[python3 /repo/tools/check_x.py]: BLOCKED: todo_write.py invoked with --priority",
     "todo_write.py"),
    ('python3 -c "import networking_write; networking_write.go()"',
     'Traceback (most recent call last):\n  File "<string>", line 1\nNameError: nope',
     "networking_write.py"),
    ("ls /nope",
     "ls: /nope: No such file or directory",
     "bash:ls"),
], ids=["pretooluse-block", "inline-python", "plain-bash"])
def test_each_failure_shape_lands_on_its_own_surface(command, error, expected, ledger):
    """Three shapes that all used to collapse into `bash:<first token>`. A PreToolUse BLOCK
    on a git commit is a todo_write.py defect, not a git defect; collapsing them makes the
    row unpromotable because nobody can tell what to fix."""
    run(bash_failure(command, stderr=error), ledger)
    assert rows(ledger)[0]["surface"] == expected


def test_a_pipe_in_the_error_text_does_not_break_the_ledger_table(ledger):
    """The ledger is a markdown table. An unescaped `|` in an error message would split the
    row into the wrong cells and silently corrupt every subsequent parse of the file."""
    run(bash_failure("python3 tools/pipe_write.py add",
                     stderr="ValueError: bad | value | here"), ledger)
    r = rows(ledger)
    assert len(r) == 1
    assert "|" not in r[0]["nature"]
    assert "bad / value / here" in r[0]["nature"]


@pytest.mark.parametrize("payload_key", ["failure_details", "error", "tool_response"])
def test_every_documented_failure_payload_shape_is_read(payload_key, ledger):
    """Claude Code's failure payload is undocumented (issue #19372) and the hook guesses at
    three shapes. If the live harness switches to a shape the hook does not read, capture goes
    to zero with no error -- exactly the silent stop this suite exists to catch."""
    base = {"tool_name": "Bash", "tool_input": {"command": "python3 tools/pipe_write.py x"},
            "exit_code": 1}
    text = f"RuntimeError: boom {SENTINEL}"
    if payload_key == "failure_details":
        base["failure_details"] = {"stderr": text}
    elif payload_key == "error":
        base["error"] = text
    else:
        base["tool_response"] = {"stderr": text}
    run(base, ledger)
    assert rows(ledger)[0]["nature"] == f"[auto] {text}"


# --- the promotion ladder: repeat fires must COUNT, not duplicate -----------

def test_a_repeat_of_the_same_failure_increments_the_count_instead_of_adding_a_row(ledger):
    """This is the stake. The ladder is 1st fire logs, 2nd writes a feedback file, 3rd forces
    a script patch. If repeats appended new rows instead of incrementing, every defect would
    sit at occurrence 1 forever and nothing would ever be promoted."""
    payload = bash_failure("python3 tools/pipe_write.py add")
    run(payload, ledger)
    run(payload, ledger)
    r = rows(ledger)
    assert len(r) == 1
    assert r[0]["occurrences"] == "2"
    assert r[0]["promotion"] == "memory (do now)"


def test_a_third_fire_escalates_to_the_mandatory_script_patch_rung(ledger):
    payload = bash_failure("python3 tools/pipe_write.py add")
    for _ in range(3):
        run(payload, ledger)
    r = rows(ledger)
    assert len(r) == 1
    assert r[0]["occurrences"] == "3"
    assert "script-patch" in r[0]["promotion"]


def test_two_different_surfaces_stay_two_rows(ledger):
    """The mirror of dedup: over-merging is as bad as under-counting, because it promotes a
    surface that is not actually failing."""
    run(bash_failure("python3 tools/pipe_write.py add"), ledger)
    run(bash_failure("python3 tools/todo_write.py add",
                     stderr="KeyError: 'due'"), ledger)
    assert {row["surface"] for row in rows(ledger)} == {"pipe_write.py", "todo_write.py"}


def test_the_occurrence_and_promotion_are_reported_on_stderr(ledger):
    """The only in-session signal that capture happened. If it goes quiet, the hook has
    stopped recording and nobody finds out until the ledger is audited."""
    err = run(bash_failure("python3 tools/pipe_write.py add"), ledger).stderr
    assert "friction-log auto-logged" in err
    assert "pipe_write.py" in err
    assert "occurrence 1" in err


# --- suppression: what must NOT reach the ledger ----------------------------

@pytest.mark.parametrize("command", [
    "python3 tools/pipe_write.py --help",
    "python3 tools/pipe_write.py -h",
], ids=["long-flag", "short-flag"])
def test_a_help_invocation_is_not_recorded(command, ledger):
    """`--help` exits nonzero by design in several of these scripts. Logging it would make
    reading the docs look like a recurring defect and push a healthy script up the ladder."""
    r = run(bash_failure(command), ledger)
    assert r.returncode == OK
    assert rows(ledger) == []


@pytest.mark.parametrize("script", ["friction_log.py", "log_tool_failure.py",
                                    "scan_transcript_failures.py"])
def test_the_ledgers_own_infrastructure_is_never_logged_as_a_surface(script, ledger):
    """Infinite-loop guard. If friction_log.py failing were itself logged via friction_log.py,
    each failure would spawn another failure."""
    run(bash_failure(f"python3 tools/{script} append a b"), ledger)
    assert rows(ledger) == []


def test_a_command_naming_both_an_excluded_and_a_real_script_is_still_recorded(ledger):
    """The exclusion is `all(...)`, not `any(...)`, on purpose: a compound command that
    happens to mention friction_log.py must not become a blanket amnesty for the real script
    that failed alongside it."""
    run(bash_failure("python3 tools/friction_log.py list && python3 tools/pipe_write.py add"),
        ledger)
    assert [row["surface"] for row in rows(ledger)] == ["pipe_write.py"]


@pytest.mark.parametrize("message", [
    '{"status":"error","message":"No task found matching that text"}',
    '{"status":"error","message":"No matches for the query"}',
    '{"status":"error","message":"Nothing to do"}',
    '{"status":"error","message":"Already closed"}',
    '{"status":"error","message":"File not found in pipeline"}',
], ids=["no-task", "no-matches", "nothing-to-do", "already-closed", "not-found"])
def test_graceful_empty_returns_are_not_recorded(message, ledger):
    """The atomic scripts use status:error as a "nothing to do" signal. Each of these five
    patterns is a distinct real-world shape; if one regex silently stops matching, that shape
    starts flooding the ledger and drowns the genuine rows."""
    run(bash_failure("python3 tools/todo_write.py done x", stderr=message), ledger)
    assert rows(ledger) == []


def test_a_masked_benign_nonzero_with_no_error_signal_is_not_recorded(ledger):
    """`git status && grep -q x` returns nonzero because grep found nothing, and the only
    capturable text is the earlier stage's happy stdout. The 2026-07-08 audit found ten rows
    reading "On branch main" logged this way. No exit code plus no error signal means drop."""
    run(bash_failure("git status && grep -q nothing f", stderr="On branch main",
                     exit_code=None), ledger)
    assert rows(ledger) == []


def test_a_missing_exit_code_still_records_when_the_text_does_look_like_an_error(ledger):
    """The mirror of the case above. The suppression is gated on BOTH conditions; if it were
    gated on the unresolved exit code alone it would swallow real failures, which is a silent
    loss rather than visible noise."""
    run(bash_failure("python3 tools/pipe_write.py add",
                     stderr="FileNotFoundError: no such file", exit_code=None), ledger)
    assert len(rows(ledger)) == 1


PLACEHOLDER = "tool failed (no parseable error text)"


def test_a_payload_with_no_readable_error_text_still_writes_a_placeholder_row(ledger):
    """FINDING, asserted as observed behaviour rather than as a desired one.

    With no exit code and no readable text the hook is supposed to drop the event (the
    masked-benign guard: unresolved exit AND no error signal). It does not, because the
    fallback string it substitutes for the missing text is "tool failed (no parseable error
    text)" -- and `looks_like_real_error` matches the literal word "failed" in the hook's own
    placeholder. The guard can therefore never fire on a text-less payload.

    Consequence: every unreadable payload writes an identical row, and because the natures are
    byte-identical they all dedup-merge into ONE row whose occurrence count climbs toward
    "script-patch (mandatory)" against a surface nobody can act on. Locked in here so the
    behaviour is visible; the fix belongs in the hook, not in this test."""
    r = run({"tool_name": "Bash", "tool_input": {"command": "ls"}}, ledger)
    assert r.returncode == OK
    assert [row["nature"] for row in rows(ledger)] == [f"[auto] {PLACEHOLDER}"]
    assert "exit=?" in rows(ledger)[0]["fix"]


# --- malformed and incomplete input -----------------------------------------

def test_malformed_stdin_exits_zero_and_records_nothing(ledger):
    """Fail-open is deliberate: a hook-internal problem must never add noise on top of the
    failure the user is already looking at. The cost is that a crash here is invisible, which
    is precisely why this suite exists."""
    r = run(None, ledger, raw="{not json")
    assert r.returncode == OK
    assert rows(ledger) == []


def test_empty_stdin_exits_zero_but_still_writes_a_placeholder_row(ledger):
    """FINDING. Malformed JSON returns early and writes nothing; EMPTY stdin does not. `{}`
    is substituted for the missing payload and falls straight through to the placeholder row
    described above. The two shapes are both "the harness told us nothing" and should behave
    the same; only one of them keeps the ledger clean."""
    r = run(None, ledger, raw="")
    assert r.returncode == OK
    assert [row["nature"] for row in rows(ledger)] == [f"[auto] {PLACEHOLDER}"]


@pytest.mark.parametrize("payload", [
    {},
    {"tool_name": "Bash"},
    {"tool_name": "Bash", "tool_input": {}},
    {"tool_name": "", "failure_details": {}},
    {"tool_name": "Bash", "tool_input": {"command": None}, "failure_details": None},
], ids=["empty", "name-only", "no-command", "blank-name", "null-fields"])
def test_incomplete_payloads_never_crash_and_never_invent_a_surface(payload, ledger):
    """Real hook payloads vary by tool and by Claude Code version. A KeyError or TypeError on
    an unexpected shape would take the capture path down for every subsequent failure in the
    session, silently -- so exit 0 is the load-bearing assertion.

    The second assertion is the one that matters for ledger quality: whatever gets written
    must be the undifferentiated placeholder, never a confident attribution to a real script.
    A wrongly-attributed row is worse than a missing one, because it sends the promotion
    ladder after a tool that is working fine."""
    r = run(payload, ledger)
    assert r.returncode == OK, r.stderr
    assert all(row["nature"] == f"[auto] {PLACEHOLDER}" for row in rows(ledger)), rows(ledger)


@pytest.mark.parametrize("payload", [
    "raw:{not json",
    "recorded",
    "help-skip",
    "graceful-skip",
], ids=["malformed", "recorded", "skipped-help", "skipped-graceful"])
def test_the_hook_exits_zero_on_every_path(payload, ledger):
    """PostToolUseFailure fires after the tool already failed. A nonzero exit cannot prevent
    anything -- it only stacks hook noise onto a failure the user is already reading. Recording,
    declining to record, and crashing on bad input must all be exit 0."""
    if payload == "raw:{not json":
        r = run(None, ledger, raw="{not json")
    elif payload == "recorded":
        r = run(bash_failure("python3 tools/pipe_write.py add"), ledger)
    elif payload == "help-skip":
        r = run(bash_failure("python3 tools/pipe_write.py --help"), ledger)
    else:
        r = run(bash_failure("python3 tools/todo_write.py done x",
                             stderr='{"message":"No task found"}'), ledger)
    assert r.returncode == OK, r.stderr


def test_the_ledger_destination_is_redirectable_and_the_real_one_is_untouched(tmp_path):
    """Named explicitly rather than left to the autouse guard: the hook has no --repo-root and
    no ledger flag of its own, so FRICTION_LEDGER inherited through os.environ.copy() is the
    ONLY seam that keeps a test run out of Nick's real ledger. If it disappears, this test is
    where that gets noticed."""
    alt = tmp_path / "elsewhere.md"
    alt.write_text(LEDGER_SEED, encoding="utf-8")
    run(bash_failure("python3 tools/pipe_write.py add"), alt)
    assert len(rows(alt)) == 1
