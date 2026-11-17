"""hook_telemetry.py: the hooks workstream's speed + block-rate PROBE (RULING Q8, hooks.md).

What must hold:
  - the value is BANDED, so /standup surfaces it on a real shift, not daily noise;
  - one Bash call = the PreToolUse:Bash finish lines up to the call's TERMINATOR, the
    `[Stall] tool_dispatch_start tool=Bash` line (the call ran) or `Bash tool permission
    denied` (it did not); its wall is the SLOWEST hook (hooks run in parallel), never the sum;
  - calls and blocks are counted from terminators and `Hook denied tool use for Bash`, so
    overlapping calls cannot inflate or mix them; a segment that holds more than one call
    is left out of wall and hooks/call, never split into invented calls (Codex r2 F1);
  - a hook denial whose only status-2 lines are "can't open file" crashes of THAT exact
    file (basename equality, Codex r2 F2) is a crash, not a block;
  - a reading that cannot be taken is exit 2 with {"error": ...}, never a 0.
Fixtures are synthetic debug-log lines in the real format.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import hook_telemetry as ht  # noqa: E402

NOW = 1_794_900_000.0  # fixed clock (2026-11-16, UTC)


def ts(offset_s: float) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(NOW + offset_s, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def line(offset_s, ms, status=0, hook="check_a.py"):
    return (f"{ts(offset_s)} [DEBUG] Hooks: PreToolUse:Bash [PYTHONIOENCODING=utf-8 python3 "
            f"$CLAUDE_PROJECT_DIR/tools/{hook}] finished with status {status} ({ms}ms)\n")


def start(offset_s, uid="toolu_X"):
    return f"{ts(offset_s)} [INFO] [Stall] tool_dispatch_start tool=Bash toolUseId={uid} permissionDecisionMs=5\n"


def denied(offset_s, by_hook=True):
    out = [f"{ts(offset_s)} [DEBUG] Hook denied tool use for Bash\n"] if by_hook else []
    return out + [f"{ts(offset_s)} [DEBUG] Bash tool permission denied\n"]


def call(offset_s, walls, status=None):
    """One Bash call: one finish line per hook, then its terminator (denied when a hook
    returned 2, else dispatched), as the real debug log writes it."""
    out = []
    for i, ms in enumerate(walls):
        st = status[i] if status else 0
        out.append(line(offset_s + i * 0.01, ms, st, f"check_{i}.py"))
    return out + (denied(offset_s + 0.5) if status and 2 in status else [start(offset_s + 0.5)])


def write_log(tmp_path, name, lines, mtime=NOW):
    import os
    p = tmp_path / name
    p.write_text("".join(lines), encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def run(tmp_path, capsys, *args):
    code = ht.main(["--logs-glob", str(tmp_path / "cc-debug-*.log"), "--now", str(NOW), *args])
    return code, json.loads(capsys.readouterr().out)


def test_wall_is_the_slowest_hook_in_a_call_not_the_sum(tmp_path, capsys):
    write_log(tmp_path, "cc-debug-1.log", call(-100, [30, 140, 60]))
    code, out = run(tmp_path, capsys, "--raw")
    assert code == 0
    assert "bash_wall_p50=140ms" in out["value"]


def test_each_terminator_closes_one_call(tmp_path, capsys):
    lines = call(-100, [100, 100]) + call(-90, [300, 300]) + call(-80, [300, 300])
    write_log(tmp_path, "cc-debug-1.log", lines)
    code, out = run(tmp_path, capsys, "--raw")
    assert "calls=3" in out["value"]
    assert "bash_wall_p50=300ms" in out["value"]
    assert "hooks/call=2" in out["value"]


@pytest.mark.parametrize("wall,band", [(99, "0-99ms"), (100, "100-199ms"), (244, "200-299ms"), (300, "300-399ms")])
def test_value_is_banded_to_100ms(tmp_path, capsys, wall, band):
    write_log(tmp_path, "cc-debug-1.log", call(-100, [wall]))
    code, out = run(tmp_path, capsys)
    assert code == 0
    assert out["value"].startswith(f"bash_wall={band} |")


def test_block_rate_is_per_100_calls_and_banded(tmp_path, capsys):
    lines = []
    for k in range(50):
        lines += call(-1000 + k * 10, [50, 50], status=[2, 0] if k < 2 else None)
    write_log(tmp_path, "cc-debug-1.log", lines)
    code, out = run(tmp_path, capsys, "--raw")
    assert "blocks_per_100=4.00" in out["value"]
    code, out = run(tmp_path, capsys)
    assert "blocks=4.0-4.9/100" in out["value"]


def test_a_crash_is_not_counted_as_a_block(tmp_path, capsys):
    crash = [line(-100, 40, 2), f"{ts(-100)} [ERROR] stderr: Python: can't open file '/x/tools/check_a.py'\n"]  # the crashed hook itself
    write_log(tmp_path, "cc-debug-1.log", crash + denied(-99) + call(-50, [40]))
    code, out = run(tmp_path, capsys, "--raw")
    assert "blocks_per_100=0.00" in out["value"]


def test_lines_older_than_the_window_are_ignored_even_in_a_recent_file(tmp_path, capsys):
    write_log(tmp_path, "cc-debug-1.log", call(-10 * 86400, [900]) + call(-100, [50]))
    code, out = run(tmp_path, capsys, "--raw", "--days", "7")
    assert "bash_wall_p50=50ms" in out["value"] and "calls=1" in out["value"]


def test_files_untouched_since_the_window_are_not_read(tmp_path, capsys):
    write_log(tmp_path, "cc-debug-old.log", call(-100, [900]), mtime=NOW - 30 * 86400)
    write_log(tmp_path, "cc-debug-new.log", call(-100, [50]))
    code, out = run(tmp_path, capsys, "--raw")
    assert "logs=1" in out["value"] and "bash_wall_p50=50ms" in out["value"]


def test_no_reading_is_exit_2_with_an_error_never_a_zero(tmp_path, capsys):
    code, out = run(tmp_path, capsys)
    assert code == 2
    assert "error" in out and "value" not in out


def test_non_bash_and_unrelated_lines_are_ignored(tmp_path, capsys):
    other = [f"{ts(-100)} [DEBUG] Hooks: PreToolUse:Edit [python3 tools/check_x.py] finished with status 2 (999ms)\n",
             f"{ts(-100)} [DEBUG] something else\n"]
    write_log(tmp_path, "cc-debug-1.log", other + call(-50, [70]))
    code, out = run(tmp_path, capsys, "--raw")
    assert "bash_wall_p50=70ms" in out["value"] and "blocks_per_100=0.00" in out["value"]


def test_log_stamps_are_utc_whatever_the_local_timezone(tmp_path, capsys, monkeypatch):
    """The logs stamp lines in UTC ('...Z'). The 2026-11-16 prototype parsed them as naive
    LOCAL time, which on a Pacific machine shifted the window 8 h and counted 41,106 lines
    where the UTC-correct count was 22,368. One hour either side of the window edge must
    land on the right side regardless of TZ."""
    import time as _time
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    _time.tzset()
    try:
        week = 7 * 86400
        write_log(tmp_path, "cc-debug-1.log",
                  call(-week - 3600, [900]) + call(-week + 3600, [50]))
        code, out = run(tmp_path, capsys, "--raw", "--days", "7")
        assert "calls=1" in out["value"] and "bash_wall_p50=50ms" in out["value"]
    finally:
        monkeypatch.delenv("TZ")
        _time.tzset()


def test_hooks_per_call_is_the_median_over_every_call(tmp_path, capsys):
    """Each burst's hook count must be recorded, not only the last one in the file."""
    lines = call(-300, [10]) + call(-200, [10]) + call(-100, [10, 10, 10])
    write_log(tmp_path, "cc-debug-1.log", lines)
    code, out = run(tmp_path, capsys, "--raw")
    assert "hooks/call=1" in out["value"] and "calls=3" in out["value"]


def test_a_recent_log_with_no_bash_timing_is_read_without_error(tmp_path, capsys):
    write_log(tmp_path, "cc-debug-a.log", [f"{ts(-100)} [DEBUG] nothing here\n"])
    write_log(tmp_path, "cc-debug-b.log", call(-50, [60]))
    code, out = run(tmp_path, capsys, "--raw")
    assert code == 0 and "logs=2" in out["value"] and "calls=1" in out["value"]


def test_the_command_line_entry_point_runs_and_keeps_the_exit_2_contract(tmp_path):
    import subprocess
    script = REPO / "tools" / "hook_telemetry.py"
    r = subprocess.run([sys.executable, str(script), "--logs-glob", str(tmp_path / "none-*.log")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "error" in json.loads(r.stdout)
    write_log(tmp_path, "cc-debug-1.log", call(-50, [60]))
    r = subprocess.run([sys.executable, str(script), "--logs-glob", str(tmp_path / "cc-debug-*.log"),
                        "--now", str(NOW)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout)["value"].startswith("bash_wall=0-99ms")


def test_two_calls_within_three_seconds_are_two_calls(tmp_path, capsys):
    """Codex P0 on fc56db23: a new call starts when a hook repeats, not only after 3 s."""
    write_log(tmp_path, "cc-debug-1.log", call(-100, [50, 60]) + call(-99.5, [300, 300]))
    code, out = run(tmp_path, capsys, "--raw")
    assert "calls=2" in out["value"]


def test_a_call_blocked_by_two_hooks_is_one_blocked_call(tmp_path, capsys):
    """Codex P0 on fc56db23: the rate counts blocked CALLS, so it can never exceed 100."""
    write_log(tmp_path, "cc-debug-1.log", call(-100, [50, 50], status=[2, 2]))
    code, out = run(tmp_path, capsys, "--raw")
    assert "blocks_per_100=100.00" in out["value"]


def test_a_crash_line_only_cancels_the_hook_it_names(tmp_path, capsys):
    """Codex P0 on fc56db23: a parallel hook's crash must not erase a real block."""
    lines = [line(-100, 40, 2, "check_0.py"),
             f"{ts(-100)} [ERROR] Python: can't open file '/x/tools/check_9.py'\n"] + denied(-99)
    write_log(tmp_path, "cc-debug-1.log", lines)
    code, out = run(tmp_path, capsys, "--raw")
    assert "blocks_per_100=100.00" in out["value"]


def test_an_unreadable_log_is_exit_2_json(tmp_path, capsys, monkeypatch):
    """Codex P1 on fc56db23: a read failure keeps the exit-2 JSON contract."""
    write_log(tmp_path, "cc-debug-1.log", call(-100, [50]))
    real_open = open
    def boom(path, *a, **k):
        if str(path).endswith(".log"):
            raise OSError("Operation not permitted")
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", boom)
    code, out = run(tmp_path, capsys)
    assert code == 2 and "error" in out


# --- Codex r2 (8f72d6b6) ---------------------------------------------------------------

def test_interleaved_overlapping_calls_are_never_split_into_mixed_calls(tmp_path, capsys):
    """r2 F1: call 1 a, call 2 a, call 1 b, call 2 b, then both terminators. That segment
    holds two calls whose hooks cannot be told apart: it counts as 2 calls, contributes no
    wall or hooks/call sample, and is reported as ambiguous. No 3-call mixed reading."""
    lines = [line(-100, 10, 0, "a.py"), line(-100, 100, 0, "a.py"),
             line(-100, 200, 0, "b.py"), line(-100, 20, 0, "b.py"),
             start(-99, "toolu_1"), start(-99, "toolu_2")] + call(-50, [60, 60])
    write_log(tmp_path, "cc-debug-1.log", lines)
    code, out = run(tmp_path, capsys, "--raw")
    v = out["value"]
    assert "calls=3" in v and "ambiguous=1" in v
    assert "bash_wall_p50=60ms" in v and "hooks/call=2" in v


def test_a_partial_interleave_with_one_terminator_is_ambiguous_not_a_call_sample(tmp_path, capsys):
    """Call 2's first hook finishes before call 1's terminator: a repeated hook inside a
    one-terminator segment. It still counts as one call, but gives no wall sample."""
    lines = [line(-100, 10, 0, "a.py"), line(-100, 500, 0, "a.py"), start(-99, "toolu_1"),
             line(-99, 30, 0, "b.py"), start(-98, "toolu_2")]
    write_log(tmp_path, "cc-debug-1.log", lines)
    code, out = run(tmp_path, capsys, "--raw")
    v = out["value"]
    assert "calls=2" in v and "ambiguous=1" in v and "bash_wall_p50=30ms" in v


def test_a_block_inside_overlapping_calls_counts_once_per_denied_call(tmp_path, capsys):
    lines = [line(-100, 10, 2, "a.py"), line(-100, 10, 0, "a.py"),
             line(-100, 10, 0, "b.py"), line(-100, 10, 0, "b.py")] + denied(-99) + [start(-99)]
    write_log(tmp_path, "cc-debug-1.log", lines + call(-50, [60]))
    code, out = run(tmp_path, capsys, "--raw")
    v = out["value"]
    assert "calls=3" in v and "blocks_per_100=33.33" in v


def test_a_denial_that_is_not_from_a_hook_is_a_call_but_not_a_block(tmp_path, capsys):
    """48 of 243 Bash denials in the 30 days to 2026-11-16 had no 'Hook denied' line
    (classifier or user). They count as calls, never as hook blocks."""
    lines = call(-100, [40]) [:-1] + denied(-99, by_hook=False) + call(-50, [40])
    write_log(tmp_path, "cc-debug-1.log", lines)
    code, out = run(tmp_path, capsys, "--raw")
    assert "calls=2" in out["value"] and "blocks_per_100=0.00" in out["value"]


def test_a_crash_of_a_file_whose_name_contains_the_hook_name_does_not_erase_the_block(tmp_path, capsys):
    """r2 F2: 'check.py' blocked; 'precheck.py' could not be opened. Substring matching
    erased the real block; basename equality keeps it."""
    lines = [line(-100, 40, 2, "check.py"),
             f"{ts(-100)} [ERROR] Python: can't open file '/x/tools/precheck.py'\n"] + denied(-99)
    write_log(tmp_path, "cc-debug-1.log", lines)
    code, out = run(tmp_path, capsys, "--raw")
    assert "blocks_per_100=100.00" in out["value"]


def test_hook_lines_with_no_terminator_are_not_a_call(tmp_path, capsys):
    """A session that ends mid-call leaves finish lines with no terminator: not a call."""
    write_log(tmp_path, "cc-debug-1.log", call(-100, [50]) + [line(-50, 900)])
    code, out = run(tmp_path, capsys, "--raw")
    assert "calls=1" in out["value"] and "bash_wall_p50=50ms" in out["value"]


def test_a_call_is_kept_when_a_later_line_is_stamped_after_now(tmp_path, capsys):
    """A live log gains lines after the probe takes `now`. The in-window call before them
    must still give its wall sample, not be dropped with the out-of-window line."""
    write_log(tmp_path, "cc-debug-1.log", call(-100, [70]) + call(+50, [900]))
    code, out = run(tmp_path, capsys, "--raw")
    assert "calls=1" in out["value"] and "bash_wall_p50=70ms" in out["value"]


# --- reach line (tools/count_report.py), 2026-11-16 -------------------------------------
# A reading names the logs it was taken from: how many matched the glob, how many were
# read, and how many were skipped as untouched since the window. The banded `value` is
# unchanged, so workstream_state (which lifts `value` only) does not surface a change.

def _reach(tmp_path, population, included, days=7):
    return (f"scope: debug logs matching {tmp_path / 'cc-debug-*.log'} | population {population}"
            f" | included {included} | excluded: modified before the {days}-day window"
            f" {population - included}")


@pytest.mark.parametrize("args", [(), ("--raw",)])
def test_a_reading_carries_its_reach_line(tmp_path, capsys, args):
    write_log(tmp_path, "cc-debug-old.log", call(-100, [900]), mtime=NOW - 30 * 86400)
    write_log(tmp_path, "cc-debug-new.log", call(-100, [50]))
    code, out = run(tmp_path, capsys, *args)
    assert code == 0 and out["reach"] == _reach(tmp_path, 2, 1)


def test_the_reach_line_uses_the_window_given(tmp_path, capsys):
    write_log(tmp_path, "cc-debug-old.log", call(-100, [900]), mtime=NOW - 3 * 86400)
    write_log(tmp_path, "cc-debug-new.log", call(-100, [50]))
    code, out = run(tmp_path, capsys, "--days", "2")
    assert code == 0 and out["reach"] == _reach(tmp_path, 2, 1, days=2)


def test_no_reading_states_the_reach_of_the_logs_it_looked_at(tmp_path, capsys):
    write_log(tmp_path, "cc-debug-old.log", call(-100, [900]), mtime=NOW - 30 * 86400)
    write_log(tmp_path, "cc-debug-new.log", [f"{ts(-100)} [DEBUG] nothing here\n"])
    code, out = run(tmp_path, capsys)
    assert code == 2 and "value" not in out
    assert out["reach"] == _reach(tmp_path, 2, 1)
    assert "no PreToolUse:Bash timing" in out["error"] and out["reach"] in out["error"]
