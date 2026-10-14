"""check_owner_only_actions.py: the agent cannot accept a parked P0, waive the gate, or
write the cross-model ledger directly. Those are Nick's decisions (2026-10-14).

Block cases and clean cases both, because a guard that blocks the agent's own grep or
commit message gets routed around, and one that misses `env X=... git push` is not a
guard.
"""
import json
import os
import subprocess
import sys

import pytest

from conftest import TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
import check_owner_only_actions as h  # noqa: E402

SCRIPT = TOOLS_DIR / "check_owner_only_actions.py"


def _run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                          capture_output=True, text=True,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _bash(command: str) -> int:
    return _run({"tool_name": "Bash", "tool_input": {"command": command}}).returncode


ACCEPT = "PYTHONIOENCODING=utf-8 python3 tools/finding_write.py accept 335.F1 --why ok"


@pytest.mark.parametrize("command, kind", [
    (ACCEPT, "accept"),
    ("python3 tools/finding_write.py --repo-root . accept 1.F1 --why x", "accept"),
    ("python3 -m tools.finding_write accept 1.F1 --why x", "accept"),
    ("cd /repo && " + ACCEPT, "accept"),
    ('echo "$(' + ACCEPT + ')"', "accept"),
    ("python3 -c \"import finding_write as fw; fw.accept(p, '1.F1', 'x')\"", "accept"),
    ("python3 - <<'EOF'\nimport finding_write\nfinding_write.accept(r, a, 'w')\nEOF",
     "accept"),
    ("CODEX_VERIFY_WAIVE='why' git push", "waive"),
    ("FOO=1 CODEX_VERIFY_WAIVE=x git push", "waive"),
    ("export CODEX_VERIFY_WAIVE=x; git push", "waive"),
    ("env CODEX_VERIFY_WAIVE=x git push", "waive"),
    ("python3 -c \"import os; os.environ['CODEX_VERIFY_WAIVE']='x'\"", "waive"),
    ("echo '{}' >> tools/.cross-model-ledger.jsonl", "ledger"),
    ("cat x > tools/.cross-model-ledger.jsonl", "ledger"),
    ("tee -a tools/.cross-model-ledger.jsonl < row.json", "ledger"),
    ("sed -i '' 's/a/b/' tools/.cross-model-ledger.jsonl", "ledger"),
    ("cp /tmp/forged tools/.cross-model-ledger.jsonl", "ledger"),
    ("dd if=/tmp/x of=tools/.cross-model-ledger.jsonl", "ledger"),
    ("mv -f /tmp/forged tools/.cross-model-ledger.jsonl", "ledger"),
    ("python3 -m pytest -q; CODEX_VERIFY_WAIVE=x git push", "waive"),
    ('echo "$(date)"; CODEX_VERIFY_WAIVE=x git push', "waive"),     # after a subst
    ("python3 -c \"import cross_model_gate as g; g.append_row(r, {})\"", "ledger"),
    ("python3 -c \"import cross_model_gate as g; g.record_waiver(r, [], 'x')\"", "ledger"),
])
def test_blocks(command, kind):
    assert h.find_violation(command) == kind
    assert _bash(command) == 2


@pytest.mark.parametrize("command", [
    # recording a disposition is the agent's job; only accepting is not
    "PYTHONIOENCODING=utf-8 python3 tools/finding_write.py set 1.F1 --disposition parked "
    "--why 'accept later'",
    "python3 tools/finding_write.py list --parked",
    "python3 tools/other.py accept 1.F1",
    "python3 tools/finding_write.py accept --help",
    "python3 tools/finding_write.py accept -h",
    # the words as text, not as acts
    'git commit -m "let Nick accept parked P0s; CODEX_VERIFY_WAIVE is owner-only"',
    "git commit -F - <<'EOF'\nfinding_write.py accept 1.F1\nCODEX_VERIFY_WAIVE=x git push\nEOF",
    "grep -n CODEX_VERIFY_WAIVE tools/cross_model_gate.py",
    "grep -rn 'finding_write.py accept' tools/",
    "echo 'CODEX_VERIFY_WAIVE=x git push'",
    # reading the ledger
    "tail -2 tools/.cross-model-ledger.jsonl",
    "wc -l < tools/.cross-model-ledger.jsonl",
    "python3 -c \"print(open('tools/.cross-model-ledger.jsonl').read()[:10])\"",
    "python3 -c \"import finding_write as fw; print(fw.collect(p, parked_only=True))\"",
    # copying the ledger OUT is a backup, not a write to it
    "cp tools/.cross-model-ledger.jsonl /tmp/ledger-before.jsonl",
    "sed -n 1,5p tools/.cross-model-ledger.jsonl",
    "sed 's/a/b/' tools/.cross-model-ledger.jsonl > /tmp/x",
    # python that EDITS these tools' source names everything without doing it
    # (replay, 2026-10-14: 33 such commands in 18,791)
    "python3 - <<'PY'\np = Path('tools/finding_write.py'); s = p.read_text()\n"
    "s = s.replace('x', 'result = accept(root, args.addr, args.why)')\np.write_text(s)\nPY",
    "python3 - <<'PY'\np = Path('tools/cross_model_gate.py'); s = p.read_text()\n"
    "s = s.replace('Or waive:  CODEX_VERIFY_WAIVE', 'Or Nick waives:  CODEX_VERIFY_WAIVE')\n"
    "p.write_text(s)\nPY",
    "python3 - <<'PY'\np = Path('tools/backup-data.sh'); s = open(p).read()\n"
    "s = s.replace('a', '\"$WORK_TREE/tools/.cross-model-ledger.jsonl\"')\n"
    "open(p, 'w').write(s)\nPY",
    # a heredoc fed to a SCRIPT is that script's stdin data, not a python program
    "python3 tools/x.py <<'EOF'\nimport finding_write\nfinding_write.accept(r, a, w)\nEOF",
    # stdin from a file has no body to read
    "python3 - < /tmp/prog.py",
    # an assignment alone, and a missing subcommand
    "X=1",
    "python3 tools/finding_write.py --repo-root /r",
    "python3 tools/finding_write.py --repo-root=/r list",
    # a plain push, and tests
    "git push",
    "python3 -m pytest tests/scripts/test_finding_write.py -q",
])
def test_allows(command):
    assert h.find_violation(command) is None
    assert _bash(command) == 0


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_a_direct_ledger_edit_is_blocked(tool):
    r = _run({"tool_name": tool,
              "tool_input": {"file_path": "/repo/tools/.cross-model-ledger.jsonl",
                             "content": "{}"}})
    assert r.returncode == 2 and "owner-only" in r.stderr


def test_other_writes_are_allowed():
    r = _run({"tool_name": "Write",
              "tool_input": {"file_path": "/repo/tools/finding_write.py", "content": "x"}})
    assert r.returncode == 0


def test_the_block_message_names_the_owner_command():
    r = _run({"tool_name": "Bash", "tool_input": {"command": ACCEPT}})
    assert "! PYTHONIOENCODING=utf-8 python3 tools/finding_write.py accept" in r.stderr
    assert "There is no override" in r.stderr


def test_an_unreadable_payload_fails_open():
    r = subprocess.run([sys.executable, str(SCRIPT)], input="not json",
                       capture_output=True, text=True)
    assert r.returncode == 0


def test_accept_subcommand_is_strictly_boolean():
    assert h._accept_subcommand(["--repo-root", "/r"]) is False
    assert h._accept_subcommand(["--repo-root=/r", "accept"]) is True
    assert h._accept_subcommand(["--repo-root", "/r", "accept"]) is True
    assert h._accept_subcommand(["accept"]) is True
    assert h._accept_subcommand([]) is False
    assert h._accept_subcommand(["--repo-root=/r"]) is False


def test_a_read_of_the_ledger_with_another_tool_is_allowed():
    r = _run({"tool_name": "Read",
              "tool_input": {"file_path": "/repo/tools/.cross-model-ledger.jsonl"}})
    assert r.returncode == 0


def test_the_documented_python_file_io_gap_is_still_a_gap():
    """Not a wish: the docstring says plain file I/O on the ledger from python is not
    seen, because every rule that tried produced false positives on source edits. If
    this starts failing, the gap closed: update the docstring."""
    cmd = "python3 -c \"open('tools/.cross-model-ledger.jsonl','a').write('x')\""
    assert h.find_violation(cmd) is None
