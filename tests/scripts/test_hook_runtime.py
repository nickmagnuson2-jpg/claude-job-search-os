"""Tests for tools/hook_runtime.py — the shared hook payload intake.

WHY THESE TESTS MATTER MORE THAN USUAL. This module will be imported by every
`check_*.py` hook. A defect here is a defect in all of them at once, which is the
trade the extraction makes: one place to get wrong instead of 33, so that one place
has to be pinned hard. Per CLAUDE.md, a green test is not evidence -- each test below
is written to FAIL if the behaviour it names is removed, and the module is expected to
be mutation-verified before any hook is switched over.

The three bounds (deadline, volume ceiling, no-unbounded-fallback) are behavioural and
cannot be checked by reading the source, so they are exercised against a REAL child
process with a REAL pipe. `test_a_retained_open_stdin_does_not_stall_past_the_deadline`
is the direct regression for the measured 300-second live hang.
"""
import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
import hook_runtime as hr  # noqa: E402


def _drive(payload, extra="", timeout=30):
    """Run read_payload() in a child with `payload` on stdin. Returns (rc, stdout, secs).

    A child process with a real pipe is the only honest instrument here: monkeypatching
    sys.stdin gives you a StringIO with no fileno, which silently exercises the F4
    fallback arm instead of the code that actually runs in production.
    """
    script = textwrap.dedent(f"""
        import sys, json
        sys.path.insert(0, {os.path.join(REPO_ROOT, "tools")!r})
        from hook_runtime import read_payload
        p = read_payload({extra})
        out = {{"ok": p.ok, "error": p.error, "raw_len": len(p.raw)}}
        if p.ok:
            # Reading a field off a not-ok payload RAISES by design, so the harness
            # applies the same contract every consumer must: check .ok first.
            out.update({{"tool_name": p.tool_name, "file_path": p.file_path,
                        "command": p.command, "content": p.content,
                        "stop": p.stop_hook_active}})
        print(json.dumps(out))
    """)
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, "-c", script], input=payload,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, time.monotonic() - t0


# --------------------------------------------------------------------------- parsing

def test_a_well_formed_payload_parses_and_exposes_its_fields():
    rc, out, _ = _drive(json.dumps({
        "tool_name": "Write",
        "tool_input": {"file_path": "/x/y.md", "content": "hello"},
    }))
    assert rc == 0
    got = json.loads(out)
    assert got["ok"] is True
    assert got["tool_name"] == "Write"
    assert got["file_path"] == "/x/y.md"
    assert got["content"] == "hello"


def test_an_edit_exposes_new_string_as_content():
    """The `content or new_string` chain appeared independently in 5 hooks.

    Without it an Edit payload yields an empty content and every content hook silently
    passes on edits -- a false-negative that no exit code would reveal.
    """
    rc, out, _ = _drive(json.dumps({
        "tool_name": "Edit",
        "tool_input": {"file_path": "/x/y.md", "new_string": "replacement"},
    }))
    assert json.loads(out)["content"] == "replacement"


def test_path_key_is_accepted_as_an_alias_for_file_path():
    rc, out, _ = _drive(json.dumps({"tool_input": {"path": "/a/b.txt"}}))
    assert json.loads(out)["file_path"] == "/a/b.txt"


def test_malformed_json_is_not_ok_and_does_not_raise():
    rc, out, _ = _drive("{not json at all")
    assert rc == 0
    got = json.loads(out)
    assert got["ok"] is False
    assert "malformed" in got["error"]


def test_empty_stdin_is_not_ok():
    rc, out, _ = _drive("")
    assert rc == 0, "an unreadable payload must not crash the caller"
    got = json.loads(out)
    assert got["ok"] is False
    assert got["error"] == "empty stdin"


@pytest.mark.parametrize("body", ["[]", '"a string"', "42", "null"])
def test_a_non_object_toplevel_is_rejected_rather_than_treated_as_a_dict(body):
    """Several of the 33 copies assumed `json.load` returns a dict.

    A bare `[]` then reached `.get` and raised AttributeError INSIDE the hook, which is
    a crash, not a fail-open -- the opposite of what those hooks documented.
    """
    rc, out, _ = _drive(body)
    assert rc == 0, "a non-object payload must fail open, not raise inside the hook"
    got = json.loads(out)
    assert got["ok"] is False
    assert "not an object" in got["error"]


def test_a_missing_tool_input_yields_an_empty_dict_not_none():
    rc, out, _ = _drive(json.dumps({"tool_name": "Bash"}))
    got = json.loads(out)
    assert got["ok"] is True
    assert got["command"] == ""
    assert got["file_path"] == ""


def test_a_null_tool_input_yields_an_empty_dict():
    """`data.get("tool_input", {})` returns None when the key is present and null.

    That is why the copies that used the two-arg .get without an `or {}` were wrong,
    and it is the single most common variation among the 33.
    """
    rc, out, _ = _drive(json.dumps({"tool_input": None}))
    got = json.loads(out)
    assert got["ok"] is True
    assert got["command"] == ""


def test_stop_hook_active_is_reported_and_is_strictly_boolean_true():
    assert json.loads(_drive(json.dumps({"stop_hook_active": True}))[1])["stop"] is True
    assert json.loads(_drive(json.dumps({"stop_hook_active": "yes"}))[1])["stop"] is False
    assert json.loads(_drive(json.dumps({}))[1])["stop"] is False


# ---------------------------------------------------------------------- the bounds

def test_a_retained_open_stdin_does_not_stall_past_the_deadline():
    """THE 300-SECOND HANG REGRESSION (codex F1, 2026-09-07).

    A producer that writes, flushes, and RETAINS the pipe never sends EOF. `sys.stdin.
    read()` waits for it forever. This test holds the pipe open and asserts the reader
    returns on schedule. If the bounded read is replaced by a plain read, this test
    hangs until its own timeout and fails -- which is the point.
    """
    script = textwrap.dedent(f"""
        import sys, time, json
        sys.path.insert(0, {os.path.join(REPO_ROOT, "tools")!r})
        from hook_runtime import read_payload
        t0 = time.monotonic()
        p = read_payload(deadline_s=0.5)
        print(json.dumps({{"secs": time.monotonic() - t0, "ok": p.ok}}))
    """)
    proc = subprocess.Popen([sys.executable, "-c", script],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    proc.stdin.write('{"tool_name": "Bash"}')
    proc.stdin.flush()
    # Deliberately do NOT close stdin: that is the failure mode. `communicate()` would
    # close it and quietly turn this into an ordinary EOF read -- which it did, until
    # mutation testing showed the test passing against a reader with its bounds removed.
    got = _finish(proc, budget=15)
    assert got["secs"] < 5.0, "reader did not honour its deadline"
    assert got["ok"] is True, "a complete payload arriving before the deadline must parse"


def test_eof_returns_immediately_rather_than_waiting_out_the_deadline():
    """The deadline is a ceiling, not a sleep. A closed pipe must return at once."""
    _, _, secs = _drive(json.dumps({"tool_name": "Bash"}))
    assert secs < 2.0, "normal EOF path paid the full deadline"


def test_the_volume_ceiling_is_enforced_at_the_request_not_audited_afterwards():
    """codex F3: reproduced at 1,048,577 bytes before the fix.

    Asking for a full 64 KiB and checking the total afterwards lets a later read cross
    the ceiling by up to 65,535 bytes. We ask for a 4 KiB cap and assert the reader
    never returns more than that, which a post-hoc audit cannot satisfy.
    """
    payload = json.dumps({"tool_input": {"content": "x" * 200_000}})
    script = textwrap.dedent(f"""
        import sys, json
        sys.path.insert(0, {os.path.join(REPO_ROOT, "tools")!r})
        from hook_runtime import read_stdin_bounded
        raw = read_stdin_bounded(deadline_s=5.0, max_bytes=4096)
        print(json.dumps({{"n": len(raw)}}))
    """)
    r = subprocess.run([sys.executable, "-c", script], input=payload,
                       capture_output=True, text=True, timeout=30)
    assert json.loads(r.stdout)["n"] <= 4096


def test_an_oversized_payload_fails_closed_as_unparseable_rather_than_half_parsed():
    """Truncation must not yield a plausible-looking partial object.

    A hook that acted on a half-read payload would judge content it never saw.
    """
    payload = json.dumps({"tool_input": {"content": "x" * 200_000}})
    script = textwrap.dedent(f"""
        import sys, json
        sys.path.insert(0, {os.path.join(REPO_ROOT, "tools")!r})
        from hook_runtime import read_payload
        p = read_payload(deadline_s=5.0, max_bytes=4096)
        print(json.dumps({{"ok": p.ok, "error": p.error, "truncated": p.truncated}}))
    """)
    r = subprocess.run([sys.executable, "-c", script], input=payload,
                       capture_output=True, text=True, timeout=30)
    got = json.loads(r.stdout)
    assert got["ok"] is False
    # AMENDED 2026-09-14 (cross-model finding F1). This previously asserted the error
    # said "malformed", i.e. that a truncated payload was indistinguishable from
    # garbage input. That conflation WAS the defect: every consumer treats malformed
    # as fail-open, so an oversized payload walked through BLOCK-tier guards unread.
    # The contract now separates the two, and this test asserts the separation.
    assert got["truncated"] is True, "an oversized payload must be reported as truncated"
    assert "TRUNCATED" in got["error"]


def test_there_is_no_unbounded_fallback_when_stdin_cannot_be_polled(monkeypatch):
    """codex F4. A non-pollable stdin must fail open on nothing, never plain-read.

    Asserted through the real function with a fileno() that raises, because the whole
    point of F4 is that this arm must NOT reach sys.stdin.read().
    """
    class NoFileno:
        def fileno(self):
            raise OSError("not a real fd")

        def read(self, *a):  # pragma: no cover - reaching this IS the failure
            raise AssertionError("F4 violated: fell back to an unbounded read")

    monkeypatch.setattr(hr.sys, "stdin", NoFileno())
    assert hr.read_stdin_bounded(deadline_s=5.0) == ""


# ------------------------------------------------------- the mechanism/policy line

def test_read_payload_never_exits_on_bad_input():
    """The module must not decide for the caller.

    If read_payload ever calls sys.exit, every hook that documents fail-CONSERVATIVE
    (check_draft_voice.py) silently flips to fail-open. The child below prints a
    sentinel AFTER the call; an exit inside the module means the sentinel never lands.
    """
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {os.path.join(REPO_ROOT, "tools")!r})
        from hook_runtime import read_payload
        p = read_payload()
        print("REACHED", p.ok)
    """)
    r = subprocess.run([sys.executable, "-c", script], input="{bad",
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert r.stdout.strip() == "REACHED False"


def test_read_payload_writes_nothing_to_stdout_or_stderr():
    """A PreToolUse hook's stdout/stderr is a user-facing channel.

    Chatter from a shared module would appear in 33 hooks at once.
    """
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {os.path.join(REPO_ROOT, "tools")!r})
        from hook_runtime import read_payload
        read_payload()
    """)
    r = subprocess.run([sys.executable, "-c", script], input="{bad",
                       capture_output=True, text=True, timeout=30)
    assert r.stdout == ""
    assert r.stderr == ""


def test_payload_is_frozen_so_a_hook_cannot_mutate_what_a_later_check_reads():
    p = hr.Payload(ok=True, data={"tool_name": "Write"})
    with pytest.raises(Exception):
        p.ok = False  # type: ignore[misc]


# ------------------------------------------------- bounds, pinned against a live pipe
#
# The four tests below exist because mutation testing said the earlier ones did not
# reach the loop's exit conditions: `if total >= max_bytes` could be inverted, forced
# true, or removed and every assertion still passed. The distinguishing observable is
# not the byte count alone -- it is the byte count AND the elapsed time together.

def _reader(deadline_s, max_bytes):
    """Spawn read_stdin_bounded in a child. Returns the Popen with stdin as a pipe."""
    script = textwrap.dedent(f"""
        import sys, time, json
        sys.path.insert(0, {os.path.join(REPO_ROOT, "tools")!r})
        from hook_runtime import read_stdin_bounded
        t0 = time.monotonic()
        raw = read_stdin_bounded(deadline_s={deadline_s}, max_bytes={max_bytes})
        print(json.dumps({{"n": len(raw), "secs": time.monotonic() - t0}}))
    """)
    return subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)


def _finish(proc, budget):
    """Wait for the child WITHOUT closing its stdin, then read what it printed.

    `communicate()` cannot be used for any test whose premise is a retained pipe: it
    closes the child's stdin, which sends the EOF the test is supposed to withhold.
    Mutation testing caught exactly this -- three "we hold the pipe open" tests here
    were sending EOF and passing against a deliberately broken reader. Same pattern as
    test_check_status_query_verification.test_a_retained_open_stdin_...
    """
    try:
        proc.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise AssertionError(
            f"child did not exit within {budget}s on a retained open stdin -- "
            "the read is unbounded")
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
    return json.loads(proc.stdout.read())


def test_a_trickling_producer_is_read_across_iterations_up_to_exactly_the_ceiling():
    """Kills `if total >= max_bytes` forced-true and inverted.

    A single read that fills the cap cannot tell those mutants apart -- every variant
    returns the same count. Small writes force the loop to iterate, so "stop at the
    ceiling" becomes distinguishable from "stop after the first chunk" (forced true)
    and from "stop before reaching it" (inverted comparison).
    """
    proc = _reader(deadline_s=5.0, max_bytes=250)
    for _ in range(10):
        try:
            proc.stdin.write("x" * 100)
            proc.stdin.flush()
        except BrokenPipeError:
            # The child stopped at the ceiling and exited while we were still
            # writing. That is the behaviour under test, not an error.
            break
        time.sleep(0.02)
    got = _finish(proc, budget=20)
    assert got["n"] == 250, f"expected to stop exactly at the ceiling, got {got['n']}"


def test_reaching_the_ceiling_returns_at_once_rather_than_waiting_out_the_deadline():
    """Kills `if total >= max_bytes` removed.

    Without that break the reader loops back into `select` and sits there for the whole
    remaining deadline, because the producer is still holding the pipe. The byte count
    is identical either way; only the clock separates them. This is also the honest
    reason the break is NOT redundant with the request-time clamp.
    """
    proc = _reader(deadline_s=3.0, max_bytes=4096)
    proc.stdin.write("x" * 4096)
    proc.stdin.flush()
    # Hold the pipe open: no EOF is ever sent, and _finish must not send one.
    got = _finish(proc, budget=20)
    assert got["n"] == 4096
    assert got["secs"] < 1.0, (
        f"hit the ceiling but still waited {got['secs']:.2f}s of a 3s deadline")


def test_a_quiet_but_open_pipe_costs_the_deadline_and_never_more():
    """Kills `if not ready` removed, and pins the cost of adopting this reader.

    THE CONTRACT IS NOT "returns promptly". A producer that writes, flushes and holds
    the pipe is indistinguishable from one that is about to write again, so `select`
    waits out the remaining allowance and the read costs the FULL deadline. That is
    the price of the bound and it is worth stating plainly: hooks switching to this
    module pay up to `STDIN_DEADLINE_S` in this case, where today they hang forever.
    The normal EOF path is unaffected and returns at once
    (test_eof_returns_immediately_rather_than_waiting_out_the_deadline).

    Without the `not ready` break the next `os.read` blocks on an open, empty pipe and
    the child never exits at all -- `_finish` then fails on its budget, which is how
    this test kills that mutant.
    """
    proc = _reader(deadline_s=3.0, max_bytes=hr.MAX_STDIN_BYTES)
    proc.stdin.write("x" * 50)
    proc.stdin.flush()
    got = _finish(proc, budget=20)
    assert got["n"] == 50
    assert got["secs"] >= 2.5, (
        f"returned in {got['secs']:.2f}s -- a quiet open pipe cannot be distinguished "
        "from a pause, so anything much under the deadline means the read is not "
        "actually waiting on select")
    assert got["secs"] < 6.0, f"overran a 3s deadline at {got['secs']:.2f}s"


def test_cwd_is_exposed_and_defaults_to_empty():
    """check_table_integrity.py reads `cwd` off the payload to locate the repo root.

    Untested, this property could return None and that hook would build a Path(None).
    """
    assert hr.Payload(ok=True, data={"cwd": "/repo"}).cwd == "/repo"
    assert hr.Payload(ok=True, data={}).cwd == ""
    assert hr.Payload(ok=True, data={"cwd": None}).cwd == ""


# --------------------------------------- the .ok check must change behaviour

def test_reading_any_field_off_an_unreadable_payload_raises():
    """Skipping the `.ok` check must fail loudly, not quietly judge an empty payload.

    This is what makes a consumer's `if not p.ok: sys.exit(0)` observable. Without it,
    deleting that line from a hook changed nothing any test could see -- proven by
    differential run against all 7 pilot adopters on 2026-09-07 -- so the guard scored
    as a surviving mutant everywhere and the honest options were ~100 allowlist entries
    or an implicit fail-open.
    """
    p = hr.Payload(ok=False, error="malformed JSON: whatever")
    for attr in ("tool_name", "tool_input", "stop_hook_active",
                 "file_path", "command", "content", "cwd"):
        with pytest.raises(ValueError, match="check `.ok`"):
            getattr(p, attr)


def test_data_raw_and_error_stay_readable_on_a_bad_payload():
    """A caller must be able to see WHAT went wrong; only derived fields are gated."""
    p = hr.Payload(ok=False, raw="{bad", error="malformed JSON: x")
    assert p.raw == "{bad"
    assert "malformed" in p.error
    assert p.data == {}


def test_a_good_payload_is_unaffected_by_the_gate():
    p = hr.Payload(ok=True, data={"tool_name": "Bash",
                                  "tool_input": {"command": "ls"}, "cwd": "/r"})
    assert (p.tool_name, p.command, p.cwd) == ("Bash", "ls", "/r")
    assert p.stop_hook_active is False


# --- F1 regression: truncation must be distinguishable from malformed input -------
# Origin: 2026-09-14 cross-model verification. Before the payload-intake extraction,
# 32 of 33 hooks used an unbounded json.load(sys.stdin). The shared reader added a
# 1 MiB cap; a truncated payload then failed to parse, every consumer read that as
# ordinary malformed input, and allowed it. A large file write could therefore walk
# straight through the public-repo PII gate without being read.

def test_truncated_payload_is_flagged_not_merely_unparseable(tmp_path, monkeypatch):
    """A payload cut off at the volume ceiling reports truncated=True.

    Without the flag this is indistinguishable from garbage input, and the whole
    enforcement layer fails open on it.
    """
    import hook_runtime
    # Stay under the OS pipe buffer (~64 KiB) or os.write blocks forever with no
    # reader draining it. 8 KiB of payload against a 1 KiB ceiling truncates just
    # as well as a megabyte would, and the test terminates.
    big = ('{"tool_name":"Write","tool_input":{"content":"'
           + "x" * 8192 + '"}}')
    r, w = os.pipe()
    os.write(w, big.encode())
    os.close(w)
    monkeypatch.setattr(sys, "stdin", os.fdopen(r, "r"))
    p = hook_runtime.read_payload(max_bytes=1024)
    assert p.truncated is True, "truncation must be reported, not silently swallowed"
    assert p.ok is False
    assert "TRUNCATED" in (p.error or ""), "the error must name truncation specifically"


def test_clean_small_payload_is_not_flagged_truncated(tmp_path, monkeypatch):
    """The flag must not fire on an ordinary payload, or it means nothing."""
    import hook_runtime
    r, w = os.pipe()
    os.write(w, b'{"tool_name":"Write","tool_input":{"file_path":"a.md"}}')
    os.close(w)
    monkeypatch.setattr(sys, "stdin", os.fdopen(r, "r"))
    p = hook_runtime.read_payload()
    assert p.ok is True
    assert p.truncated is False


def test_malformed_but_complete_payload_is_not_flagged_truncated(monkeypatch):
    """Genuine garbage stays fail-open; only truncation escalates."""
    import hook_runtime
    r, w = os.pipe()
    os.write(w, b'{not json at all')
    os.close(w)
    monkeypatch.setattr(sys, "stdin", os.fdopen(r, "r"))
    p = hook_runtime.read_payload()
    assert p.ok is False
    assert p.truncated is False, "a complete-but-garbage payload is NOT truncation"
