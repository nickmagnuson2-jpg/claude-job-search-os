"""Tests for tools/standup_collect.py, the one call that runs every /standup Step 1 script.

It replaced a hand-typed zsh loop that passed "script.py --flag" as one argument and failed
every script five mornings out of a month. The tests pin the three things that loop got
wrong: each command is an argv list naming a real script, one failure never hides the
others, and stderr never reaches the JSON.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
import standup_collect as sc  # noqa: E402


def test_every_command_is_an_argv_list_naming_a_script_that_exists():
    cmds = sc.commands("2026-01-02")
    assert len(cmds) == 12
    for name, argv in cmds.items():
        assert isinstance(argv, list) and all(isinstance(a, str) and " " not in a for a in argv), name
        assert (REPO_ROOT / argv[0]).is_file(), f"{name}: {argv[0]} does not exist"


def test_the_target_date_reaches_every_dated_script():
    cmds = sc.commands("2026-01-02")
    for name in ("pipeline_staleness", "outreach_pending", "networking_followup",
                 "todos_summary", "conversations_metric", "source_check"):
        argv = cmds[name]
        assert argv[argv.index("--target-date") + 1] == "2026-01-02", name


def test_build_check_runs_over_the_repo_as_json():
    assert sc.commands("2026-01-02")["build_check"] == [
        "tools/build_check.py", "--repo-root", ".", "--json"]


def _fake_repo(tmp_path, scripts: dict) -> Path:
    (tmp_path / "tools").mkdir()
    for name, body in scripts.items():
        (tmp_path / "tools" / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_a_failing_script_is_reported_and_the_others_still_run(tmp_path, monkeypatch):
    root = _fake_repo(tmp_path, {
        "good.py": "import json, sys; print(json.dumps({'args': sys.argv[1:]}))",
        "bad.py": "import sys; sys.stderr.write('boom on stderr'); sys.exit(3)",
        "plain.py": "print('not json')",
    })
    monkeypatch.setattr(sc, "commands", lambda d: {
        "good": ["tools/good.py", "--target-date", d],
        "bad": ["tools/bad.py"],
        "plain": ["tools/plain.py"],
    })
    out = sc.collect(root, "2026-01-02")
    assert out["failed"] == ["bad"] and out["ok"] == ["good", "plain"]
    r = out["results"]
    assert r["good"]["data"] == {"args": ["--target-date", "2026-01-02"]}
    assert "stderr" not in r["good"], "stderr is kept only for a failure"
    assert r["bad"]["rc"] == 3 and r["bad"]["stderr"] == "boom on stderr"
    assert r["plain"]["data"] is None and r["plain"]["text"] == "not json\n"


def test_stderr_never_corrupts_the_parsed_json(tmp_path, monkeypatch):
    root = _fake_repo(tmp_path, {
        "noisy.py": "import json, sys; sys.stderr.write('progress...\\n'); print(json.dumps({'n': 1}))",
    })
    monkeypatch.setattr(sc, "commands", lambda d: {"noisy": ["tools/noisy.py"]})
    out = sc.collect(root, "2026-01-02")
    assert out["results"]["noisy"]["data"] == {"n": 1}


def test_a_hung_script_times_out_instead_of_stalling_the_brief(tmp_path, monkeypatch):
    root = _fake_repo(tmp_path, {"slow.py": "import time; time.sleep(5)"})
    monkeypatch.setattr(sc, "TIMEOUT", 0.5)
    monkeypatch.setattr(sc, "commands", lambda d: {"slow": ["tools/slow.py"]})
    out = sc.collect(root, "2026-01-02")
    assert out["failed"] == ["slow"] and "timed out" in out["results"]["slow"]["stderr"]


def test_the_cli_exits_1_when_any_script_failed(tmp_path):
    root = _fake_repo(tmp_path, {})
    r = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "standup_collect.py"),
                        "--repo-root", str(root), "--target-date", "2026-01-02"],
                       capture_output=True, text=True, timeout=120)
    out = json.loads(r.stdout)
    assert r.returncode == 1 and len(out["failed"]) == 12, "a repo with no scripts fails all twelve"


def test_a_script_that_cannot_start_is_reported_not_raised(tmp_path, monkeypatch):
    def cannot(*a, **k):
        raise PermissionError("not executable")
    monkeypatch.setattr(sc.subprocess, "run", cannot)
    r = sc.run_one(["tools/x.py"], tmp_path)
    assert r == {"rc": -1, "data": None, "text": "", "stderr": "could not start: not executable"}


def test_a_nonzero_exit_with_data_is_partial_not_failed(tmp_path, monkeypatch):
    """588.F5: workstream_state exits 1 when a probe errored but still prints its JSON.
    Calling that failed told /standup to drop a source it actually had."""
    root = _fake_repo(tmp_path, {
        "probes.py": "import json, sys; print(json.dumps({'error': 1})); sys.stderr.write('probe x failed'); sys.exit(1)",
        "dead.py": "import sys; sys.exit(1)",
    })
    monkeypatch.setattr(sc, "commands", lambda d: {"probes": ["tools/probes.py"], "dead": ["tools/dead.py"]})
    out = sc.collect(root, "2026-01-02")
    assert out["partial"] == ["probes"] and out["failed"] == ["dead"] and out["ok"] == []
    assert out["results"]["probes"]["data"] == {"error": 1}
    assert out["results"]["probes"]["stderr"] == "probe x failed"


def test_exit_0_with_no_output_is_a_failure(tmp_path, monkeypatch):
    """591.F3: a script that exits 0 and prints nothing has no usable output."""
    root = _fake_repo(tmp_path, {"silent.py": "pass"})
    monkeypatch.setattr(sc, "commands", lambda d: {"silent": ["tools/silent.py"]})
    assert sc.collect(root, "2026-01-02")["failed"] == ["silent"]
