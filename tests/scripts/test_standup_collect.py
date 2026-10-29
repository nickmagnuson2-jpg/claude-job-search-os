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
    monkeypatch.setattr(sc, "commands", lambda d, read_only=False: {
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
    monkeypatch.setattr(sc, "commands", lambda d, read_only=False: {"noisy": ["tools/noisy.py"]})
    out = sc.collect(root, "2026-01-02")
    assert out["results"]["noisy"]["data"] == {"n": 1}


def test_a_hung_script_times_out_instead_of_stalling_the_brief(tmp_path, monkeypatch):
    root = _fake_repo(tmp_path, {"slow.py": "import time; time.sleep(5)"})
    monkeypatch.setattr(sc, "TIMEOUT", 0.5)
    monkeypatch.setattr(sc, "commands", lambda d, read_only=False: {"slow": ["tools/slow.py"]})
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
    monkeypatch.setattr(sc, "commands", lambda d, read_only=False: {"probes": ["tools/probes.py"], "dead": ["tools/dead.py"]})
    out = sc.collect(root, "2026-01-02")
    assert out["partial"] == ["probes"] and out["failed"] == ["dead"] and out["ok"] == []
    assert out["results"]["probes"]["data"] == {"error": 1}
    assert out["results"]["probes"]["stderr"] == "probe x failed"


def test_exit_0_with_no_output_is_a_failure(tmp_path, monkeypatch):
    """591.F3: a script that exits 0 and prints nothing has no usable output."""
    root = _fake_repo(tmp_path, {"silent.py": "pass"})
    monkeypatch.setattr(sc, "commands", lambda d, read_only=False: {"silent": ["tools/silent.py"]})
    assert sc.collect(root, "2026-01-02")["failed"] == ["silent"]


# --- read-only mode (S4): the morning page renders from it every 15 minutes -------------

def test_read_only_drops_the_two_writes_and_changes_nothing_else():
    rw, ro = sc.commands("2026-01-02"), sc.commands("2026-01-02", read_only=True)
    assert "--render" in rw["workstream_state"] and "--render" not in ro["workstream_state"]
    assert "--no-state" not in rw["sent_artifacts"] and "--no-state" in ro["sent_artifacts"]
    for name in rw:
        if name not in ("workstream_state", "sent_artifacts"):
            assert rw[name] == ro[name], name


def _snapshot(root: Path) -> dict:
    """Every file, __pycache__ included: a bytecode write is a write (S4 review F1)."""
    import hashlib
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (hashlib.sha256(p.read_bytes()).hexdigest(),
                                             p.stat().st_mtime_ns)
    return out


def _copied_repo(tmp_path: Path) -> Path:
    """A copy of the real tools/ plus a workstream whose STATE-BLOCK a render rewrites."""
    import shutil
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "tools", root / "tools",
                    ignore=shutil.ignore_patterns("__pycache__", ".sent-artifact-state.json"))
    ws_dir = root / "data" / "workstreams"
    ws_dir.mkdir(parents=True)
    probe = json.dumps([sys.executable, "-c", "print('{\"value\": 7}')"])
    (ws_dir / "example.md").write_text(
        f"# example\n\n| **Status** | ACTIVE |\n\n<!-- PROBE: {probe} -->\n", encoding="utf-8")
    for name in ("job-todos.md", "job-pipeline.md", "networking.md"):
        (root / "data" / name).write_text(f"# {name}\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("read_only", [True, False])
def test_read_only_run_on_a_copied_repo_changes_no_file(tmp_path, monkeypatch, read_only):
    """The default mode is the control: it must change something here, or the read-only
    assertion would pass on a fixture nothing could write to."""
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    root = _copied_repo(tmp_path)
    before = _snapshot(root)
    out = sc.collect(root, "2026-01-02", read_only=read_only)
    assert out["read_only"] is read_only
    after = _snapshot(root)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if read_only:
        assert changed == [], f"read-only mode wrote: {changed}"
    else:
        # The sent-check state write needs a Gmail token, which no fixture has; its
        # --no-state flag is pinned by the argv test above.
        assert "data/workstreams/example.md" in changed, changed
        assert any("__pycache__" in k for k in changed), "control: imports write bytecode"


def test_the_cli_passes_read_only_through(tmp_path):
    root = _fake_repo(tmp_path, {})
    r = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "standup_collect.py"),
                        "--repo-root", str(root), "--target-date", "2026-01-02", "--read-only"],
                       capture_output=True, text=True, timeout=120)
    assert json.loads(r.stdout)["read_only"] is True
