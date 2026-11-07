"""check_public_pii.py must finish quickly on every command, because a PreToolUse hook
that hangs stalls the session's next tool call until the harness times it out.

Two loops in the hook only terminate because of one line each, and on 2026-11-06 a
whole-file mutation run (733 mutants) left exactly those four mutants UNMEASURED: each
hit the 300-second per-mutant timeout instead of failing a test.

  - _command_positions dedups its work list with `seen`. Without the dedup, every
    one-letter wrapper option queues two states (it may or may not take a value), so a
    wrapper followed by n flags is re-walked about fib(n) times: 40 flags is ~10^8.
  - cd_parts strips leading `cd` options with `rest.pop(0)`; without the pop the
    `while` never advances.

Each call runs in a child process with a short timeout, so THIS file fails in seconds
on such a regression when run alone. It does not stop the mutants from timing out under
mutation_check: measured 2026-11-07 (diff scope, 57 mutants), the same four still hit the
300 s limit, because earlier mapped test files (alphabetical order, e.g.
test_check_public_pii.py) feed the hook inputs that hang first. Turning those timeouts
into kills needs a change in mutation_check (a per-file timeout, or the target's own test
files first), not another test.
"""
import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"

FORTY_FLAGS = "sudo " + " ".join("-" + c for c in
                                 "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN") + " ls"


def _call(expr: str, timeout: float = 20.0):
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); "
            "import check_public_pii as m; "
            f"print(json.dumps({expr}))")
    r = subprocess.run([sys.executable, "-c", code, str(TOOLS)],
                       capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_command_positions_finishes_on_a_wrapper_with_forty_flags():
    words = FORTY_FLAGS.split()
    got = _call(f"sorted(m._command_positions({words!r}))")
    # Position 0 is sudo; the last word is the command. Each flag may take a value,
    # so the flag words are not command positions.
    assert got == [0, len(words) - 1]


def test_cd_parts_finishes_and_strips_leading_options():
    assert _call("m.cd_parts('cd -P -L -- /tmp/x')") == [["-P", "-L"], "/tmp/x"]
    assert _call("m.cd_parts('cd -P /tmp/x')") == [["-P"], "/tmp/x"]
