"""Suite for tools/scan_transcript_failures.py, the Stop hook feeding the friction ledger.

WHY THIS MATTERS MORE THAN ITS TIER SUGGESTS. The hook is WARN-tier and always exits 0, so
every failure mode it has is SILENT. It reads the session transcript at end of turn, finds
errored tool_results, and appends friction rows; if it breaks, friction simply stops being
recorded and the 1st-fire/2nd-fire/3rd-fire ladder in CLAUDE.md quietly stops counting.
Nothing surfaces. Before this file it had no suite of its own -- its 135/177 survival rate
was computed against unrelated test files that merely mention the module.

Scope is deliberately the pure decision logic plus `scan`: the classifiers that decide what
counts as a failure, the cursor arithmetic that decides what has already been seen, and the
dedup that keeps the overlap re-scan from double-logging. Not covered here: `wait_for_flush`
timing, `resolve_transcript_path` worktree fallback, and `main`'s stdin protocol.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import scan_transcript_failures as st  # noqa: E402

REAL_CRASH = (
    'Traceback (most recent call last):\n'
    '  File "tools/x.py", line 3, in <module>\n'
    '    boom()\n'
    'NameError: name \'boom\' is not defined\n')
# Same traceback, but the command went on producing output -- a log being displayed.
DISPLAYED_CRASH = REAL_CRASH + "| 2026-06-05 | bash:grep | some ledger row |\n"


# --- looks_like_python_crash ------------------------------------------------

def test_real_crash_is_a_crash():
    assert st.looks_like_python_crash(REAL_CRASH) is True


def test_a_traceback_that_is_merely_displayed_is_not_a_crash():
    """The 2026-06-05 smoke finding: bare traceback-matching false-positived on ~6
    display commands (showing the friction log, dumping captured output)."""
    assert st.looks_like_python_crash(DISPLAYED_CRASH) is False


def test_text_without_a_traceback_is_not_a_crash():
    assert st.looks_like_python_crash("all fine here") is False


def test_empty_text_is_not_a_crash():
    assert st.looks_like_python_crash("") is False


def test_harness_footers_are_stripped_before_looking_at_the_last_line():
    assert st.looks_like_python_crash(REAL_CRASH + "Shell cwd was reset to /tmp\n") is True


def test_a_traceback_with_nothing_after_the_header_is_not_a_crash():
    assert st.looks_like_python_crash("Traceback (most recent call last):\n") is False


# --- is_masked_python_failure ----------------------------------------------

def test_masked_python_failure_detected_when_exit_code_was_swallowed():
    assert st.is_masked_python_failure("Bash", "python3 tools/x.py | head", REAL_CRASH, False)


def test_a_real_error_is_left_to_the_normal_path():
    """is_error true is already handled upstream; flagging it here double-logs."""
    assert not st.is_masked_python_failure("Bash", "python3 x.py", REAL_CRASH, True)


def test_non_bash_tool_is_not_a_masked_python_failure():
    assert not st.is_masked_python_failure("Read", "python3 x.py", REAL_CRASH, False)


def test_a_non_python_command_displaying_a_traceback_is_not_flagged():
    assert not st.is_masked_python_failure("Bash", "cat friction-log.md", REAL_CRASH, False)


def test_a_python_command_printing_a_traceback_as_data_is_not_flagged():
    assert not st.is_masked_python_failure(
        "Bash", "python3 tools/show_log.py", DISPLAYED_CRASH, False)


# --- masked shell failures --------------------------------------------------

@pytest.mark.parametrize("text", [
    "zsh: no matches found: *.md",
    "(eval):1: no matches found: tools/*.py",
    "ls: illegal option -- z",
    "grep: unrecognized option '--foo'",
])
def test_masked_shell_signatures_are_recognised(text):
    assert st.masked_shell_error_line(text) is not None


def test_masked_shell_line_is_returned_stripped_and_alone():
    out = st.masked_shell_error_line("ok\nzsh: no matches found: *.md  \nmore output\n")
    assert out == "zsh: no matches found: *.md", (
        "the offending LINE only, stripped -- passing the whole output would make the "
        "derived nature the entire successful-looking stdout")


def test_an_indented_signature_does_not_match():
    """Column-0 anchoring is the false-positive defense, not an accident: any line that
    is indented is quoted output, a table cell, or a grep prefix -- never the shell's
    own error. Asserted so a later `re.MULTILINE` tweak that relaxes the anchor fails."""
    assert st.masked_shell_error_line("  zsh: no matches found: *.md\n") is None
    assert st.masked_shell_error_line("42:zsh: no matches found: *.md\n") is None


def test_the_signature_is_line_anchored_so_a_table_cell_cannot_match():
    """The dominant false positive: grepping the friction log itself, whose rows start
    with '| ' and never at column 0."""
    assert st.masked_shell_error_line(
        "| 2026-06-08 | bash:ls | ls: illegal option -- z |\n") is None


def test_clean_output_has_no_masked_shell_line():
    assert st.masked_shell_error_line("everything worked\n") is None


def test_empty_text_has_no_masked_shell_line():
    assert st.masked_shell_error_line("") is None


def test_a_python_traceback_is_not_double_flagged_as_a_shell_failure():
    assert not st.is_masked_shell_failure("Bash", "python3 x.py", REAL_CRASH, False)


def test_masked_shell_failure_detected_on_a_bash_result():
    assert st.is_masked_shell_failure("Bash", "ls -z | awk '{print}'",
                                      "ls: illegal option -- z\n", False)


def test_real_shell_error_is_left_to_the_normal_path():
    assert not st.is_masked_shell_failure("Bash", "ls -z", "ls: illegal option -- z", True)


# --- extract_text -----------------------------------------------------------

def test_extract_text_passes_a_string_through():
    assert st.extract_text("boom") == "boom"


def test_extract_text_joins_text_blocks():
    assert st.extract_text([{"type": "text", "text": "a"},
                            {"type": "text", "text": "b"}]) == "a b"


def test_extract_text_survives_a_non_dict_block():
    assert "7" in st.extract_text([7, {"type": "text", "text": "x"}])


def test_extract_text_stringifies_anything_else():
    assert st.extract_text(None) == "None"


# --- prune_cursor -----------------------------------------------------------

def test_cursor_under_the_cap_is_returned_untouched():
    state = {f"s{i}": {"last_seen_ts": f"2026-01-{i:02d}"} for i in range(1, 5)}
    assert st.prune_cursor(state) == state


def test_cursor_over_the_cap_keeps_the_newest_sessions():
    state = {f"s{i}": {"last_seen_ts": f"2026-01-{i:02d}T00:00:00"}
             for i in range(1, st.MAX_CURSOR_SESSIONS + 6)}
    pruned = st.prune_cursor(state)
    assert len(pruned) == st.MAX_CURSOR_SESSIONS
    newest = f"s{st.MAX_CURSOR_SESSIONS + 5}"
    assert newest in pruned, "the most recent session must survive pruning"
    assert "s1" not in pruned, "the oldest session must be dropped"


def test_legacy_rows_without_a_timestamp_are_dropped_first():
    state = {"legacy": {}}
    state.update({f"s{i}": {"last_seen_ts": f"2026-01-{i:02d}T00:00:00"}
                  for i in range(1, st.MAX_CURSOR_SESSIONS + 1)})
    assert "legacy" not in st.prune_cursor(state)


# --- is_excluded_bash -------------------------------------------------------

def test_help_invocations_are_excluded():
    assert st.is_excluded_bash({"input": {"command": "python3 tools/friction_log.py --help"}})


def test_an_ordinary_command_is_not_excluded():
    assert not st.is_excluded_bash({"input": {"command": "python3 tools/pipe_write.py add"}})


def test_a_missing_input_block_does_not_explode():
    assert st.is_excluded_bash({}) is False


# --- scan -------------------------------------------------------------------

def _transcript(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "session.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return p


def _use(tu_id: str, command: str) -> dict:
    return {"message": {"content": [
        {"type": "tool_use", "id": tu_id, "name": "Bash", "input": {"command": command}}]}}


def _result(tu_id: str, text: str, is_error: bool = True) -> dict:
    return {"message": {"content": [
        {"type": "tool_result", "tool_use_id": tu_id, "content": text,
         "is_error": is_error}]}}


@pytest.fixture
def captured(monkeypatch):
    """append_friction shells out to friction_log.py; capture instead of writing."""
    calls = []
    monkeypatch.setattr(st, "append_friction",
                        lambda surface, nature, exit_hint="": calls.append((surface, nature)))
    return calls


def test_scan_logs_an_errored_tool_result(tmp_path, captured):
    t = _transcript(tmp_path, [_use("t1", "python3 tools/pipe_write.py"),
                               _result("t1", "boom: something failed")])
    line, logged, ids = st.scan(t, 0)
    assert logged == 1
    assert len(captured) == 1
    assert "t1" in ids
    assert line == 2


def test_scan_ignores_results_at_or_before_the_cursor(tmp_path, captured):
    t = _transcript(tmp_path, [_use("t1", "python3 tools/pipe_write.py"),
                               _result("t1", "boom")])
    _, logged, _ = st.scan(t, start_line=2)
    assert logged == 0
    assert captured == []


def test_scan_does_not_relog_an_id_already_logged(tmp_path, captured):
    """What makes the deliberate overlap re-scan safe: without this, every rescan
    re-appends the same error and inflates friction_log's occurrence count."""
    t = _transcript(tmp_path, [_use("t1", "python3 tools/pipe_write.py"),
                               _result("t1", "boom")])
    _, logged, _ = st.scan(t, 0, logged_ids={"t1"})
    assert logged == 0
    assert captured == []


def test_scan_skips_successful_results(tmp_path, captured):
    t = _transcript(tmp_path, [_use("t1", "echo hi"),
                               _result("t1", "hi", is_error=False)])
    _, logged, _ = st.scan(t, 0)
    assert logged == 0


def test_scan_catches_a_masked_python_crash_that_reported_success(tmp_path, captured):
    """The whole reason the masked-failure classifiers exist: is_error is falsy."""
    t = _transcript(tmp_path, [_use("t1", "python3 tools/x.py | head -5"),
                               _result("t1", REAL_CRASH, is_error=False)])
    _, logged, _ = st.scan(t, 0)
    assert logged == 1


def test_scan_skips_friction_infrastructure_commands(tmp_path, captured):
    t = _transcript(tmp_path, [_use("t1", "python3 tools/friction_log.py --help"),
                               _result("t1", "boom")])
    _, logged, _ = st.scan(t, 0)
    assert logged == 0


def test_scan_tolerates_malformed_jsonl_lines(tmp_path, captured):
    p = tmp_path / "s.jsonl"
    p.write_text("not json\n" + json.dumps(_use("t1", "python3 tools/x.py")) + "\n"
                 + json.dumps(_result("t1", "boom")) + "\n", encoding="utf-8")
    _, logged, _ = st.scan(p, 0)
    assert logged == 1, "one bad line must not abort the whole scan"


def test_scan_on_a_missing_file_returns_the_cursor_unmoved(tmp_path, captured):
    line, logged, ids = st.scan(tmp_path / "nope.jsonl", 7)
    assert (line, logged) == (7, 0), "a read failure must not advance the cursor"


# --- the assertion that has to fail first -----------------------------------

def test_the_module_under_test_is_the_real_one():
    assert Path(st.__file__).resolve() == (TOOLS / "scan_transcript_failures.py").resolve()
    assert st.OVERLAP_LINES > 0, "overlap re-scan disabled means flush-race misses return"
    assert st.MAX_CURSOR_SESSIONS > 0


# --- scan: malformed transcript shapes must be skipped, never crash ---------

@pytest.mark.parametrize("entry", [
    {"message": "a string, not a dict"},
    {"message": {"content": "a string, not a list"}},
    {"message": {"content": ["a bare string, not a block"]}},
    {"message": {}},
    {"no_message_key": 1},
], ids=["msg-not-dict", "content-not-list", "block-not-dict", "empty-msg", "no-msg"])
def test_scan_skips_malformed_entries_without_logging_or_crashing(tmp_path, captured, entry):
    """Each guard clause here is a real transcript shape. Without a test per shape, the
    isinstance checks can all be inverted and the suite stays green."""
    t = _transcript(tmp_path, [entry,
                               _use("t1", "python3 tools/x.py"),
                               _result("t1", "boom")])
    _, logged, _ = st.scan(t, 0)
    assert logged == 1, "the malformed entry must be skipped, the real error still logged"
    assert len(captured) == 1


def test_a_tool_use_without_an_id_is_not_indexed(tmp_path, captured):
    """`if tu_id:` — a tool_use with no id must not be stored under a None key, or the
    next tool_result with a missing id would pair with the wrong command."""
    t = _transcript(tmp_path, [
        {"message": {"content": [{"type": "tool_use", "name": "Bash",
                                  "input": {"command": "python3 tools/ghost.py"}}]}},
        _use("t1", "python3 tools/real.py"),
        _result("t1", "boom")])
    _, logged, _ = st.scan(t, 0)
    assert logged == 1
    assert "ghost" not in captured[0][0], "must not attribute the error to the id-less use"


# --- scan: the skip rules ---------------------------------------------------

@pytest.mark.parametrize("text", [
    '{"status": "ok", "message": "No task found matching that id"}',
    '{"status": "ok", "message": "No matches"}',
    '{"status": "ok", "message": "Nothing to do"}',
    '{"status": "ok", "message": "Already closed"}',
    '{"status": "ok", "message": "File not found"}',
])
def test_graceful_empty_results_are_not_friction(tmp_path, captured, text):
    """A tool politely reporting 'nothing matched' is not a tooling failure; logging it
    would flood the ledger and inflate every occurrence count."""
    t = _transcript(tmp_path, [_use("t1", "python3 tools/todo_write.py done x"),
                               _result("t1", text)])
    _, logged, _ = st.scan(t, 0)
    assert logged == 0
    assert captured == []


def test_an_unattributable_error_is_skipped_rather_than_logged_as_bare_bash(tmp_path,
                                                                            captured):
    """`if not surface or surface == "bash:"` — a row with no identifiable surface is
    useless in the ledger and cannot be deduped against anything."""
    t = _transcript(tmp_path, [_use("t1", ""), _result("t1", "something went wrong")])
    _, logged, _ = st.scan(t, 0)
    assert all(s and s != "bash:" for s, _ in captured), \
        "no row may be logged with an empty or bare 'bash:' surface"
    assert logged == len(captured)


def test_a_masked_shell_failure_derives_its_nature_from_the_error_line(tmp_path, captured):
    """For a masked SHELL failure the output looks successful, so passing the whole text
    to derive_nature would describe the SUCCESS. Only the offending line is informative."""
    out = ("total 12\nfile-a.md\nfile-b.md\n"
           "zsh: no matches found: tools/*.xyz\nfile-c.md\n")
    t = _transcript(tmp_path, [_use("t1", "ls tools/*.xyz | awk '{print}'"),
                               _result("t1", out, is_error=False)])
    _, logged, _ = st.scan(t, 0)
    assert logged == 1
    nature = captured[0][1]
    assert "no matches found" in nature, f"nature should carry the shell error, got {nature!r}"
    assert "file-a.md" not in nature, "nature must not be the whole successful-looking output"


def test_a_python_crash_keeps_its_own_nature_not_the_shell_path(tmp_path, captured):
    """Line 421's second condition: a traceback must not be routed through the
    shell-line branch even if a shell signature also appears in the output."""
    t = _transcript(tmp_path, [_use("t1", "python3 tools/x.py | head"),
                               _result("t1", REAL_CRASH, is_error=False)])
    _, logged, _ = st.scan(t, 0)
    assert logged == 1
    assert "NameError" in captured[0][1] or "boom" in captured[0][1]


# --- resolve_transcript_path (issue #44450 worktree fallback) ----------------

def test_the_payload_path_is_used_when_it_exists(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("", encoding="utf-8")
    assert st.resolve_transcript_path({"transcript_path": str(p)}) == p


def test_a_tilde_in_the_payload_path_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "t.jsonl").write_text("", encoding="utf-8")
    assert st.resolve_transcript_path({"transcript_path": "~/t.jsonl"}) is not None


def test_a_missing_payload_path_falls_back_to_the_constructed_one(tmp_path, monkeypatch):
    """Issue #44450: in a git worktree the payload path can point at a file that does
    not exist. Without the fallback the hook silently does nothing, forever, in every
    worktree -- no error, just no friction ever recorded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project = "/some/project/dir"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", project)
    built = tmp_path / ".claude" / "projects" / project.replace("/", "-")
    built.mkdir(parents=True)
    (built / "sess-1.jsonl").write_text("", encoding="utf-8")
    got = st.resolve_transcript_path(
        {"transcript_path": str(tmp_path / "gone.jsonl"), "session_id": "sess-1"})
    assert got == built / "sess-1.jsonl"


def test_the_fallback_is_tried_even_with_no_payload_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/p")
    built = tmp_path / ".claude" / "projects" / "-p"
    built.mkdir(parents=True)
    (built / "s.jsonl").write_text("", encoding="utf-8")
    assert st.resolve_transcript_path({"session_id": "s"}) == built / "s.jsonl"


def test_both_paths_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/p")
    assert st.resolve_transcript_path(
        {"transcript_path": str(tmp_path / "gone.jsonl"), "session_id": "s"}) is None


# --- wait_for_flush (issue #15813 write race) -------------------------------

def test_wait_for_flush_returns_on_a_stable_file(tmp_path):
    import time as _t
    p = tmp_path / "t.jsonl"
    p.write_text("a\nb\n", encoding="utf-8")
    start = _t.monotonic()
    st.wait_for_flush(p, max_wait=2.0)
    assert _t.monotonic() - start < 2.0, "a stable file must not burn the whole budget"


def test_wait_for_flush_returns_immediately_on_a_missing_file(tmp_path):
    import time as _t
    start = _t.monotonic()
    st.wait_for_flush(tmp_path / "nope.jsonl", max_wait=3.0)
    assert _t.monotonic() - start < 1.0, "an unreadable file must return, not poll to max_wait"


def test_wait_for_flush_is_bounded_by_max_wait(tmp_path):
    """The Stop hook runs on every turn; an unbounded wait would stall each one."""
    import time as _t
    p = tmp_path / "t.jsonl"
    p.write_text("a\n", encoding="utf-8")
    start = _t.monotonic()
    st.wait_for_flush(p, max_wait=0.3)
    assert _t.monotonic() - start < 3.0


# --- main -------------------------------------------------------------------

@pytest.fixture
def hook(tmp_path, monkeypatch):
    """Run main() against an isolated cursor file with a real transcript."""
    import io

    cursor = tmp_path / "cursor.json"
    monkeypatch.setattr(st, "CURSOR_FILE", cursor)
    calls = []
    monkeypatch.setattr(st, "append_friction",
                        lambda s, n, exit_hint="": calls.append((s, n)))
    monkeypatch.setattr(st, "wait_for_flush", lambda *a, **k: None)

    def run(payload: dict):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        return st.main()

    return type("H", (), {"run": staticmethod(run), "cursor": cursor,
                          "calls": calls, "tmp": tmp_path})


def test_main_always_exits_zero_on_empty_stdin(hook, monkeypatch):
    """WARN tier: this hook must never block the stop, whatever it is handed."""
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert st.main() == 0


def test_main_exits_zero_on_malformed_stdin(hook, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    assert st.main() == 0


def test_the_recursion_guard_stops_before_any_scan(hook, monkeypatch):
    """Our own friction-log write triggers another Stop; without this guard the hook
    re-enters itself."""
    called = []
    monkeypatch.setattr(st, "resolve_transcript_path",
                        lambda d: called.append(1) or None)
    assert hook.run({"stop_hook_active": True, "session_id": "s"}) == 0
    assert called == [], "recursion guard must return before resolving anything"


def test_main_exits_zero_when_no_transcript_resolves(hook, monkeypatch):
    monkeypatch.setattr(st, "resolve_transcript_path", lambda d: None)
    assert hook.run({"session_id": "s"}) == 0


def _write_transcript(p: Path, n_errors: int = 1) -> Path:
    rows = []
    for i in range(n_errors):
        rows.append(_use(f"t{i}", "python3 tools/pipe_write.py"))
        rows.append(_result(f"t{i}", "boom"))
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def test_main_logs_errors_and_persists_a_cursor(hook):
    t = _write_transcript(hook.tmp / "s.jsonl")
    assert hook.run({"transcript_path": str(t), "session_id": "sess"}) == 0
    assert len(hook.calls) == 1
    state = json.loads(hook.cursor.read_text(encoding="utf-8"))
    assert state["sess"]["last_line"] == 2
    assert "t0" in state["sess"]["logged_ids"]
    assert state["sess"]["last_seen_ts"]


def test_a_second_run_does_not_relog_the_same_error(hook):
    """The overlap re-scan deliberately rewinds; dedup by tool_use_id is what keeps
    that from re-appending every error on every single turn."""
    t = _write_transcript(hook.tmp / "s.jsonl")
    hook.run({"transcript_path": str(t), "session_id": "sess"})
    hook.run({"transcript_path": str(t), "session_id": "sess"})
    assert len(hook.calls) == 1


def test_the_persisted_cursor_never_moves_backwards(hook):
    """`new_line = max(new_line, last_line)` — the overlap start is a re-read window,
    not a regression of the saved position."""
    t = _write_transcript(hook.tmp / "s.jsonl")
    hook.cursor.write_text(json.dumps(
        {"sess": {"last_line": 999, "last_seen_ts": "2026-01-01", "logged_ids": []}}),
        encoding="utf-8")
    hook.run({"transcript_path": str(t), "session_id": "sess"})
    assert json.loads(hook.cursor.read_text(encoding="utf-8"))["sess"]["last_line"] == 999


def test_the_session_id_falls_back_to_the_transcript_stem(hook):
    t = _write_transcript(hook.tmp / "fallback-sess.jsonl")
    hook.run({"transcript_path": str(t)})
    assert "fallback-sess" in json.loads(hook.cursor.read_text(encoding="utf-8"))


def test_logged_ids_are_bounded(hook, monkeypatch):
    """Unbounded, this grows forever inside a file rewritten on every single turn."""
    monkeypatch.setattr(st, "MAX_LOGGED_IDS", 5)
    t = _write_transcript(hook.tmp / "s.jsonl", n_errors=12)
    hook.run({"transcript_path": str(t), "session_id": "sess"})
    ids = json.loads(hook.cursor.read_text(encoding="utf-8"))["sess"]["logged_ids"]
    assert len(ids) == 5


def test_a_corrupt_cursor_file_does_not_crash_the_hook(hook):
    hook.cursor.write_text("{not json", encoding="utf-8")
    t = _write_transcript(hook.tmp / "s.jsonl")
    assert hook.run({"transcript_path": str(t), "session_id": "sess"}) == 0
