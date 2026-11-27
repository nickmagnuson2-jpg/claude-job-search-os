"""MERGED item 7: friction_log's writing commands take the ledger's own lock.

Every writing command is a read-modify-write of the ledger, run by two auto-logging hooks
as well as by hand. Without a lock, 20 parallel appends kept 18, 14 and 15 rows on
2026-11-27 while all 20 printed ok. Scratch ledgers only, never the live one.
"""
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
SCRIPT = TOOLS / "friction_log.py"
sys.path.insert(0, str(TOOLS))
from inbox_lock import file_lock  # noqa: E402

HEADER = ("# Friction Log\n\n## Entries\n\n"
          "| Date | Surface | Nature | Fix | Occurrences | Promotion | Kind |\n"
          "|---|---|---|---|---|---|---|\n")


def _env(led):
    return dict(os.environ, FRICTION_LEDGER=str(led), PYTHONIOENCODING="utf-8")


def _append_cmd(i):
    return [sys.executable, str(SCRIPT), "append", f"surface{i}.py",
            f"distinct failure number {i} zeta{i}", "--fix", "x"]


def _rows(led):
    return [l for l in led.read_text(encoding="utf-8").splitlines() if re.match(r"\| 20\d\d-", l)]


def test_twenty_parallel_appends_keep_every_row(tmp_path):
    led = tmp_path / "friction-log.md"
    led.write_text(HEADER, encoding="utf-8")
    procs = [subprocess.Popen(_append_cmd(i), env=_env(led), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for i in range(20)]
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(json.loads(o.strip().splitlines()[-1])["status"] == "ok" for o, _ in outs), outs
    assert len(_rows(led)) == 20


def test_append_waits_for_a_held_lock_and_writes_nothing_on_timeout(tmp_path):
    led = tmp_path / "friction-log.md"
    led.write_text(HEADER, encoding="utf-8")
    held, release = threading.Event(), threading.Event()

    def holder():
        with file_lock(led, timeout=5):
            held.set()
            release.wait(30)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert held.wait(5)
        r = subprocess.run(_append_cmd(1), env=_env(led), capture_output=True, text=True,
                           timeout=30)
    finally:
        release.set()
        t.join()
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert r.returncode == 1 and out["status"] == "error" and "locked" in out["message"]
    assert led.read_text(encoding="utf-8") == HEADER


def test_append_keeps_the_ledger_mode(tmp_path):
    led = tmp_path / "friction-log.md"
    led.write_text(HEADER, encoding="utf-8")
    led.chmod(0o644)
    r = subprocess.run(_append_cmd(1), env=_env(led), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(_rows(led)) == 1
    assert led.stat().st_mode & 0o777 == 0o644


def test_read_only_commands_take_no_lock(tmp_path):
    """Preservation test, not a regression test: it passes before the fix too (the old
    code took no lock at all). It pins that list and query stay lock-free (Codex r1 F3)."""
    led = tmp_path / "friction-log.md"
    led.write_text(HEADER, encoding="utf-8")
    with file_lock(led, timeout=5):
        r = subprocess.run([sys.executable, str(SCRIPT), "list"], env=_env(led),
                           capture_output=True, text=True, timeout=4)
    assert r.returncode == 0, r.stdout + r.stderr
