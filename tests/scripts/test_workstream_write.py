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


def test_error_messages_are_the_message_not_the_args_tuple(ww, ws, capsys):
    j = ws.parent / "x.txt"
    j.write_text("x", encoding="utf-8")
    code, out = _call(ww, capsys, "add-section", str(j), "--title", "t", "--body", "b")
    assert out["message"] == f"{j} is not a .md file"


def test_the_file_descriptor_is_closed_after_each_write(ww, ws, capsys):
    def open_fds():
        return len(os.listdir("/dev/fd"))
    _call(ww, capsys, "add-section", str(ws), "--title", "warm", "--body", "b")
    before = open_fds()
    for i in range(40):
        _call(ww, capsys, "add-section", str(ws), "--title", f"t{i}", "--body", "b")
    assert open_fds() - before < 5


def test_a_short_write_is_reported_not_claimed_ok(ww, ws, capsys, monkeypatch):
    real = os.write
    monkeypatch.setattr(ww.os, "write", lambda fd, data: real(fd, data[:5]))
    code, out = _call(ww, capsys, "add-section", str(ws), "--title", "t", "--body", "b")
    monkeypatch.undo()
    assert code == 1 and out["code"] == "short_write"
    assert out["message"].startswith("wrote 5 of ")


# ============================================================================ add-next
# Origin (2026-11-23, org f1 / Nick): S3's suite failed at 07:59 on a header Next date that had
# gone stale, and the repair was a hand-written python edit: a typed date and no lock. add-next
# appends one item to the ```json next block with today's date from the clock, under
# inbox_lock.atomic_update, parsed by workstream_state's own reader so the two cannot disagree.

WS_TEXT = """# Workstream: demo

| Field | Value |
|---|---|
| **Status** | ACTIVE |
| **Owner** | s5 |
| **Value** | V2 |
| **Size** | S |
| **Blocker** | none |
| **Files** | `new:tools/x.py` |

```json next
[
  {
    "date": "2026-11-09",
    "action": "An old grandfathered item with no done_when at all"
  },
  {
    "date": "2026-11-20",
    "action": "Second item, the newest before this test runs",
    "done_when": "a dated section says it is done"
  }
]
```

## 2026-11-21 10:00 by `date` | a section newer than every next item
- body
"""


@pytest.fixture
def wsn(tmp_path):
    d = tmp_path / "repo" / "data" / "workstreams"
    d.mkdir(parents=True)
    f = d / "demo.md"
    f.write_text(WS_TEXT, encoding="utf-8")
    return f


def _state():
    sys.path.insert(0, str(TOOLS))
    try:
        return importlib.import_module("workstream_state")
    finally:
        sys.path.remove(str(TOOLS))


def _check(path):
    st = _state()
    return st.check_workstream("demo", path.read_text(encoding="utf-8"), today=FIXED.date(),
                               root=path.parents[2], question_state=lambda q: None,
                               branch_state=lambda b: None)


def _items(path):
    st = _state()
    text = path.read_text(encoding="utf-8")
    items, reasons = st._read_next(st._visible_lines(text), st.read_header(text)["end"],
                                   FIXED.date())
    assert reasons == []
    return items


NEW = {"action": "Rebase the writer branch on main and rerun the suite",
       "done_when": "a PASS row for the new tip is in tools/.suite-ledger.jsonl"}


def _add(ww, capsys, path, **kw):
    a = {**NEW, **kw}
    return _call(ww, capsys, "add-next", str(path), "--action", a["action"],
                 "--done-when", a["done_when"])


def test_add_next_appends_one_item_dated_today_from_the_clock(ww, wsn, capsys):
    before = _items(wsn)
    code, out = _add(ww, capsys, wsn)
    assert code == 0, out
    after = _items(wsn)
    assert after == before + [{"date": "2026-11-21", **NEW}]
    assert out["status"] == "ok"
    assert out["item"] == {"date": "2026-11-21", **NEW}
    assert out["file"] == str(wsn.resolve())


def test_add_next_never_touches_an_existing_byte(ww, wsn, capsys):
    original = wsn.read_text(encoding="utf-8")
    _add(ww, capsys, wsn)
    new = wsn.read_text(encoding="utf-8")
    inserted = (',\n  {\n    "date": "2026-11-21",\n    "action": "' + NEW["action"] +
                '",\n    "done_when": "' + NEW["done_when"] + '"\n  }')
    assert inserted in new
    assert new.replace(inserted, "", 1) == original


def test_the_written_file_passes_check_workstream(ww, wsn, capsys):
    # Before: the newest next item (11-20) is older than the newest dated section (11-21),
    # the stale-header shape that failed S3's suite. After add-next: no reasons.
    assert any("older than the newest dated section" in r for r in _check(wsn)["reasons"])
    _add(ww, capsys, wsn)
    assert _check(wsn)["reasons"] == []


def test_quotes_backslashes_and_unicode_round_trip(ww, wsn, capsys):
    action = 'Fix the "stale" header \\ in café notes, then rerun'
    code, out = _add(ww, capsys, wsn, action=action)
    assert code == 0, out
    assert _items(wsn)[-1]["action"] == action


def test_a_concurrent_cat_append_during_add_next_survives(ww, wsn, capsys, monkeypatch):
    real_now, fired = ww._now, []

    def now_with_interleaved_append():
        if not fired:
            fired.append(1)
            with wsn.open("a", encoding="utf-8") as f:
                f.write("\n## 2026-11-21 18:46 by `date` | other session via cat >>\n- theirs\n")
        return real_now()

    monkeypatch.setattr(ww, "_now", now_with_interleaved_append)
    code, out = _add(ww, capsys, wsn)
    assert code == 0, out
    text = wsn.read_text(encoding="utf-8")
    assert "- theirs" in text
    assert _items(wsn)[-1]["action"] == NEW["action"]
    assert out["attempts"] == 2


def test_a_writer_that_keeps_changing_the_file_is_refused_by_name(ww, wsn, capsys, monkeypatch):
    real_now = ww._now

    def always_interleave():
        with wsn.open("a", encoding="utf-8") as f:
            f.write("- another lock-unaware line\n")
        return real_now()

    monkeypatch.setattr(ww, "_now", always_interleave)
    code, out = _add(ww, capsys, wsn)
    assert code == 1 and out["code"] == "concurrent_modification"
    assert NEW["action"] not in wsn.read_text(encoding="utf-8")


@pytest.mark.parametrize("mutate,code_,fragment", [
    (lambda t: t.replace("| Field | Value |", "| F | V |"), "no_header_table", "header table"),
    (lambda t: t.replace("```json next", "```json"), "next_block_invalid", "missing next block"),
    (lambda t: t.replace('"Second item', '"Second item\\x'), "next_block_invalid",
     "not strict JSON"),
    (lambda t: t.replace('"2026-11-20"', '"2026-12-31"'), "next_block_invalid",
     "is after today"),
])
def test_a_broken_block_is_refused_and_left_untouched(ww, wsn, capsys, mutate, code_, fragment):
    wsn.write_text(mutate(WS_TEXT), encoding="utf-8")
    before = wsn.read_bytes()
    code, out = _add(ww, capsys, wsn)
    assert code == 1 and out["code"] == code_
    assert fragment in out["message"]
    assert wsn.read_bytes() == before


@pytest.mark.parametrize("field,value", [("action", "too short"), ("done_when", "short"),
                                         ("action", "   " + "x" * 3 + "   ")])
def test_a_short_action_or_done_when_is_refused(ww, wsn, capsys, field, value):
    before = wsn.read_bytes()
    code, out = _add(ww, capsys, wsn, **{field: value})
    assert code == 1 and out["code"] == f"short_{field}"
    assert "15" in out["message"]
    assert wsn.read_bytes() == before


def test_add_next_targets_only_existing_workstream_files(ww, wsn, capsys, tmp_path):
    code, out = _add(ww, capsys, wsn.parent / "missing.md")
    assert code == 1 and out["code"] == "no_such_workstream"
    other = tmp_path / "elsewhere.md"
    other.write_text(WS_TEXT, encoding="utf-8")
    code, out = _add(ww, capsys, other)
    assert code == 1 and out["code"] == "not_a_workstream"
    assert other.read_text(encoding="utf-8") == WS_TEXT


def test_add_next_has_no_date_argument_and_requires_done_when(ww, wsn):
    with pytest.raises(SystemExit) as e:
        ww.main(["add-next", str(wsn), "--action", NEW["action"], "--done-when",
                 NEW["done_when"], "--date", "2026-11-21"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        ww.main(["add-next", str(wsn), "--action", NEW["action"]])
    assert e.value.code == 2


def test_every_workstream_state_name_the_writer_imports_still_exists(ww):
    # The writer reuses workstream_state's own parser (private names, by org f1's ruling) so
    # the two cannot disagree. A rename there must fail HERE, by name, not at runtime.
    st = _state()
    assert ww.STATE_NAMES == ("_visible_lines", "read_header", "_read_next", "NEXT_FENCE",
                              "MIN_ACTION")
    missing = [n for n in ww.STATE_NAMES if not hasattr(st, n)]
    assert not missing, f"workstream_state no longer defines {missing}; workstream_write.py " \
                        "imports them for add-next"


def test_add_next_cli_end_to_end_uses_the_real_date(wsn, tmp_path):
    env = {**os.environ, "JOBSEARCH_LOCK_DIR": str(tmp_path / "locks"),
           "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, str(TOOL), "add-next", str(wsn), "--action",
                        NEW["action"], "--done-when", NEW["done_when"]],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    from datetime import date
    assert json.loads(r.stdout)["item"]["date"] == date.today().isoformat()


def test_only_the_done_when_message_asks_for_the_observable_result(ww, wsn, capsys):
    _, out = _add(ww, capsys, wsn, done_when="short")
    assert out["message"] == ("--done-when must be at least 15 characters stating the "
                              "observable result that means done")
    _, out = _add(ww, capsys, wsn, action="short")
    assert out["message"] == "--action must be at least 15 characters"


def test_an_insert_that_does_not_reparse_to_old_plus_new_is_refused(ww, wsn, capsys,
                                                                     monkeypatch):
    # The verification after the text insertion is the last line of defence: if the
    # re-parse ever disagrees with old items + the new one, nothing is written.
    real, calls = ww._ws._read_next, []

    def drops_the_new_item(lines, end, today):
        calls.append(1)
        items, reasons = real(lines, end, today)
        return (items[:-1] if len(calls) == 2 else items), reasons

    monkeypatch.setattr(ww._ws, "_read_next", drops_the_new_item)
    before = wsn.read_bytes()
    code, out = _add(ww, capsys, wsn)
    assert code == 1 and out["code"] == "insert_unverified"
    assert "nothing was written" in out["message"]
    assert wsn.read_bytes() == before


# ============================================================ add-section: stale next block
# Origin (Nick, 2026-11-23, codify #5 via org f1): a dated section written while the header's
# newest json-next item is older than it is the shape that failed S3's 07:59 suite. The guard
# blocks it for cat >>; add-section refuses it itself, since its heading never appears in a
# command for the guard to read.

def test_add_section_refuses_when_the_newest_next_item_is_older_than_today(ww, wsn, capsys):
    before = wsn.read_bytes()  # newest next item 2026-11-20, clock 2026-11-21
    code, out = _call(ww, capsys, "add-section", str(wsn), "--title", "S5: x", "--body", "b")
    assert code == 1 and out["code"] == "stale_next"
    assert out["message"] == (
        f"the newest next item in {wsn.resolve()} is dated 2026-11-20, older than today "
        f"(2026-11-21): run `tools/workstream_write.py add-next {wsn.resolve()} --action ... "
        "--done-when ...` first")
    assert wsn.read_bytes() == before


def test_add_section_passes_after_add_next(ww, wsn, capsys):
    assert _add(ww, capsys, wsn)[0] == 0
    code, out = _call(ww, capsys, "add-section", str(wsn), "--title", "S5: x", "--body", "b")
    assert code == 0, out
    assert _check(wsn)["reasons"] == []


def test_add_section_with_a_next_item_dated_today_passes(ww, wsn, capsys):
    wsn.write_text(WS_TEXT.replace('"2026-11-20"', '"2026-11-21"'), encoding="utf-8")
    assert _call(ww, capsys, "add-section", str(wsn), "--title", "t", "--body", "b")[0] == 0


@pytest.mark.parametrize("mutate", [
    lambda t: t.replace("```json next", "```json"),           # no next block (exempt files)
    lambda t: t.replace("| Field | Value |", "| F | V |"),    # no header table
    lambda t: t.replace('"Second item', '"Second item\\x'),   # broken JSON: not ours to judge
])
def test_add_section_does_not_judge_a_file_without_a_valid_next_block(ww, wsn, capsys, mutate):
    wsn.write_text(mutate(WS_TEXT), encoding="utf-8")
    assert _call(ww, capsys, "add-section", str(wsn), "--title", "t", "--body", "b")[0] == 0


def test_newest_next_date_reads_workstream_state(ww):
    assert ww.newest_next_date(WS_TEXT, FIXED.date()) == "2026-11-20"
    assert ww.newest_next_date(WS_TEXT.replace("```json next", "```json"), FIXED.date()) is None
    assert ww.newest_next_date("no table", FIXED.date()) is None
