"""A failed write reports a JSON error, never a traceback; write_atomic still raises.

Origin 2026-11-26 (Codex P1 on s6-writers-1126, parked as 1495.F1): a valid update in a
read-only data/ dir crashed with a PermissionError traceback instead of the JSON error
every writer CLI promises on stdout.

The fix is NOT inside write_atomic. Two callers depend on its exception:
networking_write undoes a capture link in `except OSError`, and remember_apply records a
per-note error and continues its batch. A SystemExit from inside write_atomic would skip
both. So write_atomic keeps raising (and now removes its .tmp on failure), and CLI
writers with no handler of their own call write_atomic_or_exit.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import md_table as mt  # noqa: E402

needs_permissions = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory modes")


@pytest.fixture
def read_only(tmp_path):
    """A directory the test makes read-only, restored so tmp_path can be cleaned."""
    made = []

    def make(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o555)
        made.append(path)
        return path
    yield make
    for p in made:
        p.chmod(0o755)


def _run(script: str, *args, stdin=None):
    r = subprocess.run([sys.executable, str(TOOLS / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8", input=stdin,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r


def _assert_write_failed(r, path: Path):
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["status"] == "error"
    assert out["code"] == "write_failed"
    assert out["path"] == str(path)


# --- md_table ---------------------------------------------------------------

def test_write_atomic_still_raises_for_its_handling_callers(tmp_path):
    target = tmp_path / "is-a-dir"
    target.mkdir()
    with pytest.raises(OSError):
        mt.write_atomic(target, "x")


def test_write_atomic_removes_its_temp_file_when_the_rename_fails(tmp_path):
    """The temp file is written, then os.replace onto a directory fails."""
    target = tmp_path / "is-a-dir"
    target.mkdir()
    with pytest.raises(OSError):
        mt.write_atomic(target, "x")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["is-a-dir"]


def test_write_atomic_or_exit_writes_like_write_atomic(tmp_path):
    target = tmp_path / "sub" / "f.md"
    mt.write_atomic_or_exit(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


@needs_permissions
def test_write_atomic_or_exit_is_a_json_error_in_a_read_only_dir(tmp_path, read_only, capsys):
    target = read_only(tmp_path / "data") / "f.md"
    with pytest.raises(SystemExit) as e:
        mt.write_atomic_or_exit(target, "x")
    assert e.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert (out["status"], out["code"], out["path"]) == ("error", "write_failed", str(target))
    assert not target.exists()


# --- the three switched callers --------------------------------------------

@needs_permissions
def test_personal_todo_write_reports_write_failed(tmp_path, read_only):
    todos = tmp_path / "todos" / "personal-todos.md"
    todos.parent.mkdir()
    todos.write_text("# Personal\n\n## Active\n\n| Task | Priority | Due | Status | Notes |\n"
                     "| --- | --- | --- | --- | --- |\n", encoding="utf-8")
    read_only(todos.parent)
    _assert_write_failed(_run("personal_todo_write.py", "--file", todos, "add", "call"), todos)


@needs_permissions
def test_granola_save_reports_write_failed(tmp_path, read_only):
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"meeting_id": "x", "title": "t", "captured": "2026-08-25 10:00",
                               "transcript": "Me: hi\nThem: hello", "summary": "s",
                               "type": "recruiter", "session_desc": "d"}), encoding="utf-8")
    out = read_only(tmp_path / "out") / "o.md"
    _assert_write_failed(_run("granola_save.py", "write", "--output", out, "--input", inp), out)


@needs_permissions
def test_my_world_synthesis_reports_write_failed(tmp_path, read_only):
    refl = read_only(tmp_path / "data" / "reflections")
    _assert_write_failed(
        _run("my_world_synthesis.py", "--repo-root", tmp_path, "append",
             "--covered-through", "2026-11-01", stdin="## block\ntext\n"),
        refl / "_longitudinal.md")


# --- Codex 1.F1 / 1.F2 on 55ec0274 (worktree ledger) -------------------------

def test_write_atomic_or_exit_wording_is_neutral_and_extra_fields_pass_through(
        tmp_path, read_only, capsys):
    """1.F1: the wrapper cannot know whether an earlier file was saved, so it must not
    claim "nothing was saved"; the caller says what was written via an extra field."""
    target = read_only(tmp_path / "ro") / "f.md"
    with pytest.raises(SystemExit):
        mt.write_atomic_or_exit(target, "x", written=["earlier.md"])
    out = json.loads(capsys.readouterr().out.strip())
    assert "nothing was saved" not in out["message"]
    assert out["message"].startswith(f"{target} could not be written")
    assert out["written"] == ["earlier.md"]


def _granola_input(tmp_path: Path) -> Path:
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"meeting_id": "x", "title": "t", "captured": "2026-08-25 10:00",
                               "transcript": "Me: hi\nThem: hello", "summary": "s",
                               "type": "recruiter", "session_desc": "d"}), encoding="utf-8")
    return inp


def test_granola_save_names_the_transcript_it_wrote_when_the_summary_fails(tmp_path):
    """1.F1: the transcript lands, the summary write fails; the error must say the
    transcript was written instead of claiming nothing was saved."""
    out = tmp_path / "out" / "o.md"
    summary = out.with_name("o-summary.md")
    summary.mkdir(parents=True)  # os.replace cannot put a file over a directory
    r = _run("granola_save.py", "write", "--output", out, "--input", _granola_input(tmp_path))
    _assert_write_failed(r, summary)
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert out.is_file()
    assert res["written"] == [str(out)]
    assert "nothing was saved" not in res["message"]


@needs_permissions
def test_granola_save_reports_write_failed_when_the_output_dir_cannot_be_made(
        tmp_path, read_only):
    """1.F2: the output-dir mkdir ran outside the wrapper and crashed with a traceback."""
    parent = read_only(tmp_path / "ro") / "sub"
    out = parent / "o.md"
    r = _run("granola_save.py", "write", "--output", out, "--input", _granola_input(tmp_path))
    _assert_write_failed(r, parent)
    assert json.loads(r.stdout.strip().splitlines()[-1])["written"] == []
