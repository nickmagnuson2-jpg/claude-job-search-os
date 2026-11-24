#!/usr/bin/env python3
"""tools/check_workstream_heading_time.py: block a dated workstream heading whose time was
typed into the tool call; allow a time produced at run time.

Origin: 2026-11-21 18:47, "## 2026-11-21 18:12 ..." was written into data/workstreams/hooks.md
(rule feedback_log_timestamps_from_date_not_estimates, fire 8). The BLOCK cases below include
the three literal-time shapes found in the 2026-11-21..23 replay; the ALLOW cases include the
two shapes that carried 200 of the 203 real writes.
"""
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "check_workstream_heading_time.py"
WS = "data/workstreams/hooks.md"


def _run(tool: str, tool_input: dict):
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload, capture_output=True,
                       text=True)
    return r.returncode, r.stderr


def _bash(command: str):
    return _run("Bash", {"command": command})


@pytest.fixture
def mod():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        return importlib.import_module("check_workstream_heading_time")
    finally:
        sys.path.remove(str(ROOT / "tools"))


# --- BLOCK: a typed time in a heading that is written into a workstream file ---------------

@pytest.mark.parametrize("command", [
    # fire 8, the real shape: a literal time in a cat >> heredoc
    f"cat >> {WS} <<'EOF'\n\n## 2026-11-21 18:12 by `date` | gate-fixes-1121\n- x\nEOF",
    # unquoted heredoc, other workstream file
    "cat >> data/workstreams/orchestration.md <<EOF\n## 2026-11-22 16:27 by `date` | t\nEOF",
    # estimate shapes from the rule's fires
    f"cat >> {WS} <<'EOF'\n## 2026-11-21 21:1x | org\nEOF",
    f"cat >> {WS} <<'EOF'\n## 2026-11-06 ~15:20 | estimate\nEOF",
    f"cat >> {WS} <<'EOF'\n## 2026-11-22 08:2? | q\nEOF",
    f"cat >> {WS} <<'EOF'\n## 2026-11-22 9:05 | one-digit hour\nEOF",
    f"cat >> {WS} <<'EOF'\n  ## 2026-11-22 09:05 | indented\nEOF",
    # echo / printf
    f'echo "## 2026-11-21 18:12 | x" >> {WS}',
    f"printf '## 2026-11-21 18:12 | x\\n' >> {WS}",
    f'echo -e "\\n## 2026-11-21 18:12 | x" >> {WS}',
    # tee, sed -i, interpreter
    f"tee -a {WS} <<'EOF'\n## 2026-11-21 18:12 | x\nEOF",
    f"printf 'x' | tee {WS} >/dev/null <<'EOF'\n## 2026-11-21 18:12 | x\nEOF",
    f"sed -i '' '$a\\\n## 2026-11-21 18:12 | x' {WS}",
    f"python3 - <<'EOF'\nopen('{WS}','a').write('''\n## 2026-11-21 18:12 | x\n''')\nEOF",
    # a literal assigned earlier in the SAME command (org condition 2)
    f"T=18:12; cat >> {WS} <<EOF\n## 2026-11-21 $T | x\nEOF",
    f"T='18:12'; cat >> {WS} <<EOF\n## 2026-11-21 ${{T}} | x\nEOF",
    f'D="2026-11-21 18:12"; cat >> {WS} <<EOF\n## $D by `date` | x\nEOF',
    f"export T=18:12 && cat >> {WS} <<EOF\n## 2026-11-21 $T | x\nEOF",
    # the target in a variable assigned in the same command
    f"F={WS}; cat >> \"$F\" <<'EOF'\n## 2026-11-21 18:12 | x\nEOF",
    # absolute path and a quoted target
    f"cat >> \"/Users/x/repo/{WS}\" <<'EOF'\n## 2026-11-21 18:12 | x\nEOF",
    # cd then a relative write is still the workstream path text
    f"cd /repo && cat >> {WS} <<'EOF'\n## 2026-11-21 18:12 | x\nEOF",
])
def test_blocks_a_typed_time(command):
    code, err = _bash(command)
    assert code == 2, command
    assert "BLOCKED (check_workstream_heading_time)" in err


def test_the_block_message_names_both_fixes_and_the_target():
    code, err = _bash(f"cat >> {WS} <<'EOF'\n## 2026-11-21 18:12 by `date` | x\nEOF")
    assert code == 2
    assert f"workstream_write.py add-section {WS}" in err
    assert "$(date '+%Y-%m-%d %H:%M')" in err
    assert "'18:12' is typed into the tool call" in err
    assert "## 2026-11-21 18:12 by `date` | x" in err
    assert "Write or Edit tool call can only carry a literal time" in err


def test_the_block_message_names_a_typed_variable():
    code, err = _bash(f"T=18:12; cat >> {WS} <<EOF\n## 2026-11-21 $T | x\nEOF")
    assert code == 2
    assert "$T is assigned a typed time earlier in this command" in err


# --- ALLOW: the time is produced at run time, or nothing is written to a workstream ---------

@pytest.mark.parametrize("command", [
    # the 200-of-203 shapes
    f"cat >> {WS} <<EOF\n## 2026-11-21 $(date +%H:%M) by `date` | x\nEOF",
    f"T=$(date '+%Y-%m-%d %H:%M') && cat >> {WS} <<EOF\n## $T by `date` | x\nEOF",
    f"D=$(date '+%Y-%m-%d %H:%M'); cat >> {WS} <<EOF\n## ${{D}} by `date` | x\nEOF",
    f"T=`date +%H:%M`; cat >> {WS} <<EOF\n## 2026-11-21 $T | x\nEOF",
    f"cat >> {WS} <<EOF\n## 2026-11-21 `date +%H:%M` | x\nEOF",
    f"echo \"## $(date '+%Y-%m-%d %H:%M') by \\`date\\` | x\" >> {WS}",
    # a variable this command does not assign (tripwire limit, documented)
    f"cat >> {WS} <<EOF\n## 2026-11-21 $T | x\nEOF",
    # no time in the heading at all
    f"cat >> {WS} <<'EOF'\n## 2026-11-21 | context entry\nEOF",
    f"cat >> {WS} <<'EOF'\n## Notes\n- 18:12 in the body is fine\nEOF",
    # a typed time in the BODY, not a heading
    f"cat >> {WS} <<'EOF'\n## 2026-11-21 $(date +%H:%M) | x\n- 18:12 heading was wrong\nEOF",
    # the sanctioned writer
    f"PYTHONIOENCODING=utf-8 python3 tools/workstream_write.py add-section {WS} "
    "--title \"S5: x\" --body-file - <<'EOF'\n- body\nEOF",
    # reading a workstream: grep / sed -n / cat with a quoted heading pattern
    f'grep -n "## 2026-11-21 18:12" {WS}',
    f"grep -n '^## 2026-11-21 18:1' {WS} > /tmp/out.txt",
    f"sed -n '/## 2026-11-21 18:12/,$p' {WS}",
    f"cat {WS} | tail -5",
    # writing somewhere else entirely
    "cat >> output/lessons-1122/S5-x.md <<'EOF'\n## 2026-11-21 18:12 | x\nEOF",
    "cat >> data/company-notes/acme.md <<'EOF'\n## 2026-11-21 18:12 | x\nEOF",
    # a workstream path only mentioned, write goes elsewhere
    f"cat >> output/notes.md <<'EOF'\n## 2026-11-21 18:12 | see {WS}\nEOF",
    # a commit message quoting the heading
    f"git commit -q -F - <<'EOF'\nfix {WS}\n## 2026-11-21 18:12 heading\nEOF",
    f"git log -1 2>/dev/null; grep -c '## 2026' {WS}",
])
def test_allows(command):
    code, err = _bash(command)
    assert code == 0, (command, err)
    assert err == "", err  # allowed cleanly, not by the fail-open error path


# --- Write / Edit / MultiEdit ---------------------------------------------------------------

def test_write_adding_a_timed_heading_is_blocked(tmp_path):
    f = tmp_path / "data" / "workstreams" / "x.md"
    f.parent.mkdir(parents=True)
    f.write_text("# X\n## 2026-11-20 10:00 by `date` | old\n", encoding="utf-8")
    code, err = _run("Write", {"file_path": str(f), "content": f.read_text() +
                               "\n## 2026-11-21 18:12 by `date` | new\n"})
    assert code == 2
    assert "a Write tool call adds it" in err
    assert "## 2026-11-21 18:12 by `date` | new" in err


def test_write_keeping_existing_timed_headings_is_allowed(tmp_path):
    f = tmp_path / "data" / "workstreams" / "x.md"
    f.parent.mkdir(parents=True)
    text = "# X\n## 2026-11-20 10:00 by `date` | old\n"
    f.write_text(text, encoding="utf-8")
    assert _run("Write", {"file_path": str(f), "content": text + "- more body\n"})[0] == 0


def test_write_to_a_new_workstream_with_a_timed_heading_is_blocked(tmp_path):
    f = tmp_path / "data" / "workstreams" / "new.md"
    code, _ = _run("Write", {"file_path": str(f), "content": "## 2026-11-21 18:12 | x\n"})
    assert code == 2


def test_edit_adding_a_timed_heading_is_blocked():
    code, err = _run("Edit", {"file_path": f"/r/{WS}", "old_string": "- last line",
                              "new_string": "- last line\n\n## 2026-11-21 18:12 | x"})
    assert code == 2 and "a Edit tool call adds it" in err


def test_edit_that_keeps_a_heading_unchanged_is_allowed():
    h = "## 2026-11-21 18:12 | x"
    assert _run("Edit", {"file_path": f"/r/{WS}", "old_string": f"{h}\n- a",
                         "new_string": f"{h}\n- b"})[0] == 0


def test_multiedit_adding_a_timed_heading_is_blocked():
    code, _ = _run("MultiEdit", {"file_path": f"/r/{WS}", "edits": [
        {"old_string": "a", "new_string": "b"},
        {"old_string": "c", "new_string": "c\n## 2026-11-21 18:12 | x"}]})
    assert code == 2


def test_multiedit_without_one_is_allowed_and_bad_edits_are_skipped():
    assert _run("MultiEdit", {"file_path": f"/r/{WS}", "edits": [
        "not a dict", {"old_string": "a", "new_string": "b"}]})[0] == 0


@pytest.mark.parametrize("path", ["/r/output/x.md", "/r/data/workstreams/x.json",
                                  "/r/data/workstreams/sub/x.md", "/r/data/x.md"])
def test_write_outside_workstream_files_is_allowed(path):
    assert _run("Write", {"file_path": path,
                          "content": "## 2026-11-21 18:12 | x\n"})[0] == 0


def test_write_with_a_windows_separator_is_still_a_workstream():
    code, _ = _run("Write", {"file_path": "C:\\r\\data\\workstreams\\x.md",
                             "content": "## 2026-11-21 18:12 | x\n"})
    assert code == 2


def test_other_tools_and_bad_payloads_pass():
    assert _run("Read", {"file_path": f"/r/{WS}"})[0] == 0
    for raw in ["", "not json", "[]", json.dumps({"tool_name": "Bash", "tool_input": "x"})]:
        r = subprocess.run([sys.executable, str(SCRIPT)], input=raw, capture_output=True,
                           text=True)
        assert r.returncode == 0, raw


# --- helpers, directly ------------------------------------------------------------------

@pytest.mark.parametrize("value,typed", [
    ("18:12", True), ("'18:12'", True), ('"2026-11-21 18:12"', True), ("~8", True),
    ("$(date +%H:%M)", False), ("`date`", False), ('"$X 18:12"', False), ("abc", False),
])
def test_literal_time_value(mod, value, typed):
    assert mod.literal_time_value(value) is typed


@pytest.mark.parametrize("rest,why", [
    ("2026-11-21 18:12 | x", "the time '18:12' is typed into the tool call"),
    ("2026-11-21 $(date +%H:%M) | x", None),
    ("2026-11-21 | x", None),
    ("Notes", None),
    ("$T by x", None),
])
def test_typed_time_in_rest(mod, rest, why):
    assert mod.typed_time_in_rest(rest, {}) == why


def test_writes_workstream_names_the_target(mod):
    assert mod.writes_workstream(f"cat >> {WS} <<'EOF'\nx\nEOF") == WS
    assert mod.writes_workstream(f"tee -a {WS}") == WS
    assert mod.writes_workstream(f"sed -i '' 's/a/b/' {WS}") == WS
    assert mod.writes_workstream(f"F={WS}; echo x >> $F") == WS
    assert mod.writes_workstream(f"python3 x.py {WS}") == WS
    assert mod.writes_workstream(f"cat {WS}") is None
    assert mod.writes_workstream("echo x >> output/a.md") is None
    assert mod.writes_workstream(f"F=output/a.md; echo x >> $F; cat {WS}") is None


def test_writes_workstream_without_any_workstream_path_is_none(mod):
    assert mod.writes_workstream("python3 - <<'EOF'\n## 2026-11-21 18:12 | x\nEOF") is None


def test_a_tee_to_another_file_is_not_a_workstream_write(mod):
    assert mod.writes_workstream(f"echo x | tee /tmp/a.txt; cat {WS}") is None


def test_unknown_tools_are_not_judged_even_with_edit_shaped_input(mod):
    inp = {"file_path": f"/r/{WS}", "content": "## 2026-11-21 18:12 | x",
           "edits": [{"old_string": "", "new_string": "## 2026-11-21 18:12 | x"}]}
    assert mod.evaluate("NotebookEdit", inp) is None
    assert mod.evaluate("MultiEdit", inp) is not None


def test_an_internal_error_fails_open_and_says_so(mod, monkeypatch, capsys):
    def boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(mod, "main", boom)
    assert mod.run() == 0
    assert "check_workstream_heading_time.py error (allowing through): kaboom" in \
        capsys.readouterr().err


def test_run_returns_mains_verdict(mod, monkeypatch):
    monkeypatch.setattr(mod, "main", lambda: 2)
    assert mod.run() == 2


# ============================================================ stale next block (codify #5)
# Origin (Nick, 2026-11-23, via org f1): S3's 07:59 suite failed because a dated section was
# written while the header's newest json-next item was older than it. The guard blocks that
# write and names add-next. Replay since 11-21: 24 of 210 heading writes, 7 (file, date) pairs.

from datetime import date as _date, timedelta as _td

TODAY = _date.today().isoformat()
YESTERDAY = (_date.today() - _td(days=1)).isoformat()


def _ws_file(tmp_path, newest):
    d = tmp_path / "data" / "workstreams"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "demo.md"
    f.write_text(
        "# Workstream: demo\n\n| Field | Value |\n|---|---|\n| **Status** | ACTIVE |\n\n"
        "```json next\n[\n  {\n    \"date\": \"" + newest + "\",\n"
        "    \"action\": \"An item long enough to be valid here\",\n"
        "    \"done_when\": \"a test reads the newest date\"\n  }\n]\n```\n\n"
        "## 2026-11-01 10:00 by `date` | old\n", encoding="utf-8")
    return f


def _bash_in(tmp_path, command):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                          "cwd": str(tmp_path)})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload, capture_output=True,
                       text=True)
    return r.returncode, r.stderr


STALE_DYN = "cat >> data/workstreams/demo.md <<EOF\n## $(date '+%Y-%m-%d %H:%M') by `date` | x\nEOF"


def test_a_section_newer_than_the_newest_next_item_is_blocked(tmp_path):
    _ws_file(tmp_path, YESTERDAY)
    code, err = _bash_in(tmp_path, STALE_DYN)
    assert code == 2
    assert "BLOCKED (check_workstream_heading_time): stale next block" in err
    assert f"newest next item is dated {YESTERDAY}" in err
    assert f"a section dated {TODAY}" in err
    assert ("run `tools/workstream_write.py add-next data/workstreams/demo.md --action ... "
            "--done-when ...` first") in err


def test_a_section_on_the_newest_next_date_passes(tmp_path):
    _ws_file(tmp_path, TODAY)
    code, err = _bash_in(tmp_path, STALE_DYN)
    assert code == 0 and err == ""


@pytest.mark.parametrize("heading", [
    "## $T by `date` | x",                      # T from $(date) in the same command
    "## ${T} by `date` | x",
])
def test_a_run_time_date_variable_is_dated_today(tmp_path, heading):
    _ws_file(tmp_path, YESTERDAY)
    cmd = (f"T=$(date '+%Y-%m-%d %H:%M'); cat >> data/workstreams/demo.md <<EOF\n{heading}\n"
           "EOF")
    assert _bash_in(tmp_path, cmd)[0] == 2
    _ws_file(tmp_path, TODAY)
    assert _bash_in(tmp_path, cmd)[0] == 0


def test_a_literal_date_older_than_the_next_item_passes(tmp_path):
    _ws_file(tmp_path, TODAY)
    cmd = ("cat >> data/workstreams/demo.md <<'EOF'\n## " + YESTERDAY +
           " | a back-dated note, no time\nEOF")
    assert _bash_in(tmp_path, cmd)[0] == 0


def test_a_literal_future_date_is_blocked_as_stale(tmp_path):
    _ws_file(tmp_path, YESTERDAY)
    cmd = "cat >> data/workstreams/demo.md <<'EOF'\n## " + TODAY + " | dated, no time\nEOF"
    code, err = _bash_in(tmp_path, cmd)
    assert code == 2 and "stale next block" in err


def test_the_typed_time_block_is_reported_first(tmp_path):
    _ws_file(tmp_path, YESTERDAY)
    cmd = "cat >> data/workstreams/demo.md <<'EOF'\n## " + TODAY + " 18:12 | x\nEOF"
    code, err = _bash_in(tmp_path, cmd)
    assert code == 2 and "TYPED time" in err and "stale next block" not in err


@pytest.mark.parametrize("body", [
    "# Workstream: demo\n\nno table at all\n",                                  # not judged
    "# W\n\n| Field | Value |\n|---|---|\n| **Status** | ACTIVE |\n\n## 2026-11-01 | x\n",
])
def test_files_without_a_valid_next_block_are_not_judged(tmp_path, body):
    d = tmp_path / "data" / "workstreams"
    d.mkdir(parents=True)
    (d / "demo.md").write_text(body, encoding="utf-8")
    assert _bash_in(tmp_path, STALE_DYN)[0] == 0


def test_a_missing_target_file_is_not_judged(tmp_path):
    assert _bash_in(tmp_path, STALE_DYN)[0] == 0


def test_an_absolute_target_path_is_read(tmp_path):
    f = _ws_file(tmp_path, YESTERDAY)
    cmd = STALE_DYN.replace("data/workstreams/demo.md", str(f))
    assert _bash_in(tmp_path / "elsewhere", cmd)[0] == 2


def test_the_project_dir_is_the_fallback_base(tmp_path):
    _ws_file(tmp_path, YESTERDAY)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": STALE_DYN}})
    r = subprocess.run([sys.executable, str(SCRIPT)], input=payload, capture_output=True,
                       text=True, env={**__import__("os").environ,
                                       "CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert r.returncode == 2


def test_write_adding_a_section_without_bumping_next_is_blocked(tmp_path):
    f = _ws_file(tmp_path, YESTERDAY)
    new = f.read_text() + f"\n## {TODAY} | new section, no time\n"
    code, err = _run("Write", {"file_path": str(f), "content": new})
    assert code == 2 and "stale next block" in err


def test_write_that_bumps_next_in_the_same_content_passes(tmp_path):
    f = _ws_file(tmp_path, YESTERDAY)
    new = f.read_text().replace(YESTERDAY, TODAY) + f"\n## {TODAY} | new section\n"
    assert _run("Write", {"file_path": str(f), "content": new})[0] == 0


def test_edit_adding_a_section_without_bumping_next_is_blocked(tmp_path):
    f = _ws_file(tmp_path, YESTERDAY)
    code, err = _run("Edit", {"file_path": str(f), "old_string": "| old\n",
                              "new_string": f"| old\n\n## {TODAY} | new\n"})
    assert code == 2 and "stale next block" in err


def test_edit_whose_old_string_is_absent_is_judged_on_the_current_file(tmp_path):
    f = _ws_file(tmp_path, TODAY)
    assert _run("Edit", {"file_path": str(f), "old_string": "not in file",
                         "new_string": f"## {TODAY} | x"})[0] == 0


def test_multiedit_is_judged_after_all_edits(tmp_path):
    f = _ws_file(tmp_path, YESTERDAY)
    ok = _run("MultiEdit", {"file_path": str(f), "edits": [
        {"old_string": YESTERDAY, "new_string": TODAY},
        {"old_string": "| old\n", "new_string": f"| old\n## {TODAY} | new\n"}]})
    assert ok[0] == 0
    f2 = _ws_file(tmp_path, YESTERDAY)
    bad = _run("MultiEdit", {"file_path": str(f2), "edits": [
        {"old_string": "| old\n", "new_string": f"| old\n## {TODAY} | new\n"}]})
    assert bad[0] == 2


def test_heading_date_resolution(mod):
    assert mod.heading_date("2026-11-21 18:12 | x", {}) == "2026-11-21"
    assert mod.heading_date("$(date '+%Y-%m-%d') | x", {}) == TODAY
    assert mod.heading_date("`date +%F` | x", {}) == TODAY
    assert mod.heading_date("$T by x", {"T": ["$(date)"]}) == TODAY
    assert mod.heading_date('$D by x', {"D": ['"2026-11-20 09:00"']}) == "2026-11-20"
    assert mod.heading_date("$U by x", {}) is None   # not assigned here: not judged
    assert mod.heading_date("Notes", {}) is None


# ---- mutation r1 survivors (guard-next)

def test_a_workstream_write_with_no_dated_heading_allows_cleanly(tmp_path):
    _ws_file(tmp_path, YESTERDAY)
    code, err = _bash_in(tmp_path, "cat >> data/workstreams/demo.md <<'EOF'\n- a line\nEOF")
    assert code == 0 and err == ""


def test_write_creating_a_new_workstream_is_judged_on_its_content(tmp_path):
    d = tmp_path / "data" / "workstreams"
    d.mkdir(parents=True)
    # no typed time in the old heading, so only the stale-next rule can fire
    src = _ws_file(tmp_path / "src", YESTERDAY).read_text().replace(" 10:00 by `date`", "")
    new = d / "fresh.md"
    code, err = _run("Write", {"file_path": str(new),
                               "content": src + f"\n## {TODAY} | first section\n"})
    assert code == 2 and "stale next block" in err


def test_an_edit_with_an_empty_old_string_is_not_an_insertion(tmp_path):
    f = _ws_file(tmp_path, YESTERDAY)
    # Prepended text with a second next block would make the file unjudgeable; an empty
    # old_string replaces nothing, so the file is judged as it stands and the section blocks.
    prefix = ("```json next\n[]\n```\n")
    code, err = _run("Edit", {"file_path": str(f), "old_string": "",
                              "new_string": prefix + f"## {TODAY} | x\n"})
    assert code == 2 and "stale next block" in err


def test_an_edit_adding_no_dated_heading_allows_cleanly(tmp_path):
    f = _ws_file(tmp_path, YESTERDAY)
    code, err = _run("Edit", {"file_path": str(f), "old_string": "| old\n",
                              "new_string": "| old\n- one more line\n"})
    assert code == 0 and err == ""
