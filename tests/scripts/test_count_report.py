"""Tests for tools/count_report.py: the report line, conservation, and the files CLI reach."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import count_report as cr  # noqa: E402

SCRIPT = REPO_ROOT / "tools" / "count_report.py"


# --- library ------------------------------------------------------------------------

def test_report_line_shape_and_return():
    buf = io.StringIO()
    line = cr.report("rows in example.md", 10, 7, {"header": 1, "withdrawn": 2}, file=buf)
    assert line == ("scope: rows in example.md | population 10 | included 7"
                    " | excluded: header 1, withdrawn 2")
    assert buf.getvalue() == line + "\n"


def test_report_defaults_to_stdout(capsys):
    cr.report("x", 1, 1)
    assert capsys.readouterr().out == "scope: x | population 1 | included 1 | excluded: none\n"


def test_conservation_mismatch_raises():
    with pytest.raises(ValueError, match="do not conserve"):
        cr.format_line("rows", 10, 7, {"header": 1})
    with pytest.raises(ValueError, match="do not conserve"):
        cr.format_line("rows", 10, 11)


@pytest.mark.parametrize("scope", ["", "   ", None])
def test_empty_scope_raises(scope):
    with pytest.raises(ValueError, match="scope is empty"):
        cr.format_line(scope, 0, 0)


def test_scope_is_stripped():
    assert cr.format_line("  a  ", 0, 0).startswith("scope: a |")


@pytest.mark.parametrize("pop,inc,exc", [
    (-1, -1, {}), (1, 1.0, {}), (True, 1, {}), (2, 1, {"r": -1 + 2 - 1}),
    (2, 1, {"r": "1"}),
])
def test_bad_counts_raise(pop, inc, exc):
    with pytest.raises(ValueError):
        cr.format_line("s", pop, inc, exc)


def test_empty_reason_raises():
    with pytest.raises(ValueError, match="reason is empty"):
        cr.format_line("s", 2, 1, {" ": 1})


def test_zero_exclusions_are_printed():
    assert cr.format_line("s", 1, 1, {"old": 0}).endswith("excluded: old 0")


# --- files CLI ----------------------------------------------------------------------

def _w(p: Path, text: str = "x", age_days: float = 0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(p, (t, t))
    return p


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_cli_sees_gitignored_directory(tmp_path):
    (tmp_path / ".gitignore").write_text("data/\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _w(tmp_path / "data" / "secret.md", "needle")
    _w(tmp_path / "public.md", "needle")
    r = _run("files", tmp_path, "--glob", "*.md", "--grep", "needle", "--list")
    assert r.returncode == 0, r.stderr
    assert f"scope: files under {tmp_path} [glob *.md] [regex needle] |" in r.stdout
    assert "| included 2 |" in r.stdout
    assert str(tmp_path / "data" / "secret.md") in r.stdout.splitlines()


def test_cli_prunes_backup_trees_as_named_exclusion(tmp_path):
    _w(tmp_path / "memory" / "live.md")
    _w(tmp_path / "memory" / "canonical-sidecar" / "a.md")
    _w(tmp_path / "memory" / "canonical-sidecar" / "sub" / "b.md")
    _w(tmp_path / "memory" / "global-claude-mirror" / "c.md")
    _w(tmp_path / ".git" / "HEAD")
    _w(tmp_path / "other" / "canonical-sidecar" / "kept.md")
    r = _run("files", tmp_path, "--list")
    assert r.returncode == 0, r.stderr
    first = r.stdout.splitlines()[0]
    assert first.endswith("| population 6 | included 2 | excluded: pruned .git/backup trees 4,"
                          " unreadable 0")
    listed = r.stdout.splitlines()[2:]
    assert sorted(listed) == sorted([str(tmp_path / "memory" / "live.md"),
                                     str(tmp_path / "other" / "canonical-sidecar" / "kept.md")])


def test_cli_newer_than_excluded_count(tmp_path):
    _w(tmp_path / "new.md")
    _w(tmp_path / "old1.md", age_days=3)
    _w(tmp_path / "old2.md", age_days=10)
    r = _run("files", tmp_path, "--newer-than-days", "2")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0].endswith(
        "[newer than days 2] | population 3 | included 1"
        " | excluded: pruned .git/backup trees 0, older than 2 days 2, unreadable 0")


def test_filters_apply_in_order_glob_age_regex(tmp_path):
    _w(tmp_path / "a.txt", "needle")
    _w(tmp_path / "old.md", "needle", age_days=5)
    _w(tmp_path / "miss.md", "hay")
    _w(tmp_path / "hit.md", "needle")
    res = cr.count_files(str(tmp_path), glob="*.md", newer_than_days=1.5, grep="needle")
    assert res["excluded"] == {cr.R_PRUNED: 0, cr.R_GLOB: 1, "older than 1.5 days": 1,
                               cr.R_NOMATCH: 1, cr.R_UNREADABLE: 0}
    assert res["included"] == [str(tmp_path / "hit.md")]
    assert res["population"] == 4


def test_glob_with_slash_matches_relative_path(tmp_path):
    _w(tmp_path / "x" / "a.md")
    _w(tmp_path / "y" / "a.md")
    res = cr.count_files(str(tmp_path), glob="x/*.md")
    assert res["included"] == [str(tmp_path / "x" / "a.md")]


def test_unreadable_file_is_counted(tmp_path):
    p = _w(tmp_path / "locked.md", "needle")
    p.chmod(0)
    try:
        if os.access(p, os.R_OK):
            pytest.skip("running as a user that can read mode-000 files")
        res = cr.count_files(str(tmp_path), grep="needle")
    finally:
        p.chmod(0o644)
    assert res["excluded"][cr.R_UNREADABLE] == 1
    assert res["included"] == []


def test_multiple_roots_per_root_lines_and_totals(tmp_path):
    _w(tmp_path / "r1" / "a.md")
    _w(tmp_path / "r2" / "b.md")
    _w(tmp_path / "r2" / "c.txt")
    r = _run("files", tmp_path / "r1", tmp_path / "r2", "--glob", "*.md")
    lines = r.stdout.splitlines()
    assert lines[0].startswith(f"scope: files under {tmp_path / 'r1'}, {tmp_path / 'r2'} [glob *.md]")
    assert "| population 3 | included 2 | excluded: pruned .git/backup trees 0, glob mismatch 1" in lines[0]
    assert lines[1] == f"  root {tmp_path / 'r1'}: population 1 | included 1"
    assert lines[2] == f"  root {tmp_path / 'r2'}: population 2 | included 1"
    assert len(lines) == 3      # no paths without --list


def test_missing_root_exits_2(tmp_path):
    r = _run("files", tmp_path / "absent")
    assert r.returncode == 2
    assert "root not found" in r.stderr
    assert r.stdout == ""


def test_main_returns_zero_on_empty_result(tmp_path, capsys):
    assert cr.main(["files", str(tmp_path), "--grep", "nothing"]) == 0
    assert "| population 0 | included 0 |" in capsys.readouterr().out
