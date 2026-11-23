#!/usr/bin/env python3
"""tools/workstream_write.py add-section: a dated workstream section whose time comes from the
clock, appended without rewriting the file.

Origin (C3, output/overnight-112126/DECISIONS.md): on 2026-11-21 a hand-typed heading read
"18:12" when the clock read 18:47 (data/workstreams/hooks.md), and two sessions racing on one
workstream file could drop each other's sections. The writer takes no time argument at all.
"""
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
TOOL = TOOLS / "workstream_write.py"
FIXED = datetime(2026, 11, 21, 18, 47, tzinfo=timezone(timedelta(hours=-8)))


@pytest.fixture
def ww(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(tmp_path / "locks"))
    sys.path.insert(0, str(TOOLS))
    try:
        mod = importlib.import_module("workstream_write")
    finally:
        sys.path.remove(str(TOOLS))
    monkeypatch.setattr(mod, "_now", lambda: FIXED)
    return mod


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "repo" / "data" / "workstreams"
    d.mkdir(parents=True)
    f = d / "hooks.md"
    f.write_text("# Hooks\n\n## 2026-11-20 10:00 by `date` | earlier\n- old entry\n",
                 encoding="utf-8")
    return f


def _call(ww, capsys, *argv):
    code = ww.main(list(argv))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_appends_a_clock_stamped_section_and_keeps_every_prior_byte(ww, ws, capsys):
    before = ws.read_bytes()
    code, out = _call(ww, capsys, "add-section", str(ws), "--title", "S5: C2 committed",
                      "--body", "- Changed: x\n- Next: y")
    assert code == 0, out
    after = ws.read_bytes()
    assert after.startswith(before)
    added = after[len(before):].decode("utf-8")
    assert added == "\n## 2026-11-21 18:47 by `date` | S5: C2 committed\n- Changed: x\n- Next: y\n"
    assert out["status"] == "ok"
    assert out["heading"] == "## 2026-11-21 18:47 by `date` | S5: C2 committed"
    assert out["bytes"] == len(added.encode("utf-8"))
    assert out["file"] == str(ws.resolve())


def test_the_heading_matches_the_org_cron_reader_shape(ww, ws, capsys):
    _call(ww, capsys, "add-section", str(ws), "--title", "S5: start", "--body", "x")
    last = [ln for ln in ws.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")][-1]
    import re
    assert re.fullmatch(r"## \d{4}-\d{2}-\d{2} \d{2}:\d{2} by `date` \| S5: .+", last)


def test_a_file_without_a_final_newline_gets_one_before_the_section(ww, ws, capsys):
    ws.write_text("# Hooks\n- no newline at end", encoding="utf-8")
    _call(ww, capsys, "add-section", str(ws), "--title", "t", "--body", "b")
    assert ws.read_text(encoding="utf-8") == (
        "# Hooks\n- no newline at end\n\n## 2026-11-21 18:47 by `date` | t\nb\n")


def test_an_empty_file_gets_the_section_without_a_stray_newline_first(ww, ws, capsys):
    ws.write_text("", encoding="utf-8")
    _call(ww, capsys, "add-section", str(ws), "--title", "t", "--body", "b")
    assert ws.read_text(encoding="utf-8") == "\n## 2026-11-21 18:47 by `date` | t\nb\n"


def test_an_empty_body_writes_the_heading_only(ww, ws, capsys):
    before = ws.read_text(encoding="utf-8")
    code, _ = _call(ww, capsys, "add-section", str(ws), "--title", "t", "--body", "")
    assert code == 0
    assert ws.read_text(encoding="utf-8") == before + "\n## 2026-11-21 18:47 by `date` | t\n"


def test_body_file_and_stdin(ww, ws, capsys, tmp_path, monkeypatch):
    bf = tmp_path / "body.md"
    bf.write_text("- from a file\n", encoding="utf-8")
    assert _call(ww, capsys, "add-section", str(ws), "--title", "a", "--body-file", str(bf))[0] == 0
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("- from stdin\n"))
    assert _call(ww, capsys, "add-section", str(ws), "--title", "b", "--body-file", "-")[0] == 0
    text = ws.read_text(encoding="utf-8")
    assert text.endswith("| a\n- from a file\n\n## 2026-11-21 18:47 by `date` | b\n- from stdin\n")


def test_an_unreadable_body_file_is_named(ww, ws, capsys, tmp_path):
    code, out = _call(ww, capsys, "add-section", str(ws), "--title", "a",
                      "--body-file", str(tmp_path / "missing.md"))
    assert code == 1 and out["code"] == "body_unreadable"
    assert "missing.md" in out["message"]


@pytest.mark.parametrize("title", ["2026-11-21 18:12 S5: x", "18:12 S5: x", "~18:1x S5",
                                   "  9:05 thing"])
def test_a_title_that_starts_with_a_typed_stamp_is_refused(ww, ws, capsys, title):
    before = ws.read_bytes()
    code, out = _call(ww, capsys, "add-section", str(ws), "--title", title, "--body", "b")
    assert code == 1 and out["code"] == "typed_stamp_in_title"
    assert "clock" in out["message"]
    assert ws.read_bytes() == before


def test_a_title_with_a_time_later_in_it_is_allowed(ww, ws, capsys):
    code, _ = _call(ww, capsys, "add-section", str(ws), "--title", "S5: reply to the 07:36 ping",
                    "--body", "b")
    assert code == 0


@pytest.mark.parametrize("title,code_", [("   ", "empty_title"), ("a\nb", "multiline_title"),
                                          ("a\rb", "multiline_title")])
def test_bad_titles_are_named(ww, ws, capsys, title, code_):
    code, out = _call(ww, capsys, "add-section", str(ws), "--title", title, "--body", "b")
    assert code == 1 and out["code"] == code_


@pytest.mark.parametrize("body", ["## 2026-11-21 18:12 by `date` | smuggled",
                                  "- a\n  ## 2026-11-21 | indented dated heading",
                                  "x\n##\t2026-01-01 tab"])
def test_a_dated_heading_inside_the_body_is_refused(ww, ws, capsys, body):
    before = ws.read_bytes()
    code, out = _call(ww, capsys, "add-section", str(ws), "--title", "t", "--body", body)
    assert code == 1 and out["code"] == "dated_heading_in_body"
    assert ws.read_bytes() == before


def test_undated_subheadings_in_the_body_are_fine(ww, ws, capsys):
    code, _ = _call(ww, capsys, "add-section", str(ws), "--title", "t",
                    "--body", "### Detail\n## Not dated\n- see 2026-11-21 18:12 in hooks.md")
    assert code == 0


def test_only_files_in_data_workstreams_are_accepted(ww, tmp_path, capsys):
    other = tmp_path / "repo" / "output" / "workstreams"
    other.mkdir(parents=True)
    f = other / "x.md"
    f.write_text("x\n", encoding="utf-8")
    code, out = _call(ww, capsys, "add-section", str(f), "--title", "t", "--body", "b")
    assert code == 1 and out["code"] == "not_a_workstream"
    (tmp_path / "repo" / "data").mkdir()
    g = tmp_path / "repo" / "data" / "notes.md"
    g.write_text("x\n", encoding="utf-8")
    code, out = _call(ww, capsys, "add-section", str(g), "--title", "t", "--body", "b")
    assert code == 1 and out["code"] == "not_a_workstream"
    assert f.read_text(encoding="utf-8") == "x\n" and g.read_text(encoding="utf-8") == "x\n"


def test_a_missing_workstream_is_not_created(ww, ws, capsys):
    new = ws.parent / "new-stream.md"
    code, out = _call(ww, capsys, "add-section", str(new), "--title", "t", "--body", "b")
    assert code == 1 and out["code"] == "no_such_workstream"
    assert "registry" in out["message"]
    assert not new.exists()


def test_a_non_markdown_file_is_refused(ww, ws, capsys):
    j = ws.parent / "backlog-exempt.json"
    j.write_text("{}", encoding="utf-8")
    code, out = _call(ww, capsys, "add-section", str(j), "--title", "t", "--body", "b")
    assert code == 1 and out["code"] == "not_markdown"
    assert j.read_text(encoding="utf-8") == "{}"


def test_there_is_no_time_argument(ww, ws):
    with pytest.raises(SystemExit) as e:
        ww.main(["add-section", str(ws), "--title", "t", "--body", "b", "--time", "18:12"])
    assert e.value.code == 2


def test_body_and_body_file_are_exclusive_and_one_is_required(ww, ws):
    with pytest.raises(SystemExit):
        ww.main(["add-section", str(ws), "--title", "t"])
    with pytest.raises(SystemExit):
        ww.main(["add-section", str(ws), "--title", "t", "--body", "b", "--body-file", "-"])


def test_a_held_lock_times_out_with_a_named_error_and_writes_nothing(ww, ws, capsys,
                                                                     monkeypatch):
    monkeypatch.setattr(ww, "LOCK_TIMEOUT_S", 0.2)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time; sys.path.insert(0, %r)\n"
         "from inbox_lock import file_lock\n"
         "with file_lock(%r, timeout=0):\n"
         "    print('held', flush=True); time.sleep(30)\n" % (str(TOOLS), str(ws.resolve()))],
        stdout=subprocess.PIPE, text=True, env={**os.environ})
    try:
        assert holder.stdout.readline().strip() == "held"
        before = ws.read_bytes()
        code, out = _call(ww, capsys, "add-section", str(ws), "--title", "t", "--body", "b")
    finally:
        holder.kill()
        holder.wait()
    assert code == 1 and out["code"] == "lock_timeout"
    assert ws.read_bytes() == before


def test_a_concurrent_lock_unaware_append_is_never_lost(ww, ws, capsys, monkeypatch):
    # A `cat >>` landing between the stamp and the write must survive: the writer appends,
    # it never rewrites the file from a copy it read earlier.
    real_now = ww._now

    def now_with_interleaved_append():
        with ws.open("a", encoding="utf-8") as f:
            f.write("\n## 2026-11-21 18:46 by `date` | other session via cat >>\n- theirs\n")
        return real_now()

    monkeypatch.setattr(ww, "_now", now_with_interleaved_append)
    _call(ww, capsys, "add-section", str(ws), "--title", "mine", "--body", "- mine")
    text = ws.read_text(encoding="utf-8")
    assert "- theirs" in text and "- mine" in text
    assert text.index("- theirs") < text.index("- mine")


def test_two_writer_processes_both_land(ws, tmp_path):
    env = {**os.environ, "JOBSEARCH_LOCK_DIR": str(tmp_path / "locks"),
           "PYTHONIOENCODING": "utf-8"}
    procs = [subprocess.Popen([sys.executable, str(TOOL), "add-section", str(ws), "--title",
                               f"writer {i}", "--body", f"- body {i}"],
                              stdout=subprocess.PIPE, text=True, env=env) for i in range(6)]
    outs = [json.loads(p.communicate()[0]) for p in procs]
    assert all(o["status"] == "ok" for o in outs)
    text = ws.read_text(encoding="utf-8")
    for i in range(6):
        assert f"| writer {i}\n- body {i}\n" in text


def test_cli_end_to_end_stamps_the_real_clock(ws, tmp_path):
    env = {**os.environ, "JOBSEARCH_LOCK_DIR": str(tmp_path / "locks"),
           "PYTHONIOENCODING": "utf-8"}
    lo = datetime.now().astimezone().replace(second=0, microsecond=0)
    r = subprocess.run([sys.executable, str(TOOL), "add-section", str(ws), "--title", "live",
                        "--body", "b"], capture_output=True, text=True, env=env)
    hi = datetime.now().astimezone()
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    stamp = datetime.strptime(out["heading"][3:19], "%Y-%m-%d %H:%M").astimezone()
    assert lo <= stamp <= hi
