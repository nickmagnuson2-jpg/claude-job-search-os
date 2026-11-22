"""Tests for tools/memory_usage.py: when did a memory file's text last enter a session?

Spec: output/memory-hygiene/112226-state-matrix.md, rows "Kind" and "Freshness"; method and
its gaps: output/analysis/112226-memory-transcript-analysis.md. The script reads Claude Code
session transcripts and records content entries only: a Read tool call, a Bash print command
with output, a grep-style content line, a `file` attachment. Names in listings, prompts,
system reminders and always-loaded files are not entries. Writes are not entries.

Every fixture here is synthetic: invented rule names in a temporary folder. No test reads or
writes the live memory folder.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import memory_usage as mu  # noqa: E402

STEMS = {"feedback_alpha_rule", "reference_beta_fact", "project_gamma_note", "user_delta_pref"}


# ---------------------------------------------------------------------------- fixtures

def _mem(tmp_path: Path) -> Path:
    mem = tmp_path / "proj" / "memory"
    mem.mkdir(parents=True)
    for s in STEMS:
        (mem / f"{s}.md").write_text("---\nname: x\n---\nbody\n")
    (mem / "MEMORY.md").write_text("index\n")
    (mem / "notes.md").write_text("not a rule file\n")
    return mem


def _tool_use(tid, name, inp, ts="2026-11-20T18:00:00Z"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"model": "m", "content": [{"type": "tool_use", "id": tid, "name": name,
                                                   "input": inp}]}}


def _tool_result(tid, text, ts="2026-11-20T18:00:01Z", error=False):
    block = {"type": "tool_result", "tool_use_id": tid, "content": text}
    if error:
        block["is_error"] = True
    return {"type": "user", "timestamp": ts, "message": {"content": [block]}}


def _write(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _scan(tmp_path, records, session="s1"):
    mem = _mem(tmp_path)
    _write(mem.parent / f"{session}.jsonl", records)
    return mu.collect(mem.parent, mu.memory_stems(mem))


def _chans(events):
    return sorted((e.stem, e.channel) for e in events)


MP = "/x/proj/memory/"


# ---------------------------------------------------------------------------- stems

def test_memory_stems_are_the_four_rule_prefixes_only(tmp_path):
    assert mu.memory_stems(_mem(tmp_path)) == STEMS


# ---------------------------------------------------------------------------- Read channel

def test_a_read_with_a_result_is_an_entry(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Read", {"file_path": MP + "feedback_alpha_rule.md"}),
                           _tool_result("t1", "1\t---\n2\tname: x")])
    assert _chans(res.events) == [("feedback_alpha_rule", "read")]


def test_a_failed_read_is_not_an_entry(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Read", {"file_path": MP + "feedback_alpha_rule.md"}),
                           _tool_result("t1", "File does not exist.", error=True)])
    assert res.events == []


def test_a_read_outside_a_memory_folder_is_not_an_entry(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Read", {"file_path": "/x/output/feedback_alpha_rule.md"}),
                           _tool_result("t1", "text")])
    assert res.events == []


def test_a_read_of_an_unknown_stem_is_not_an_entry(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Read", {"file_path": MP + "feedback_gone_rule.md"}),
                           _tool_result("t1", "text")])
    assert res.events == []


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_a_write_tool_is_not_an_entry(tmp_path, tool):
    res = _scan(tmp_path, [_tool_use("t1", tool, {"file_path": MP + "feedback_alpha_rule.md"}),
                           _tool_result("t1", "ok")])
    assert res.events == []


# ---------------------------------------------------------------------------- Bash channel

@pytest.mark.parametrize("cmd", [
    f"cat {MP}feedback_alpha_rule.md",
    f"head -20 {MP}feedback_alpha_rule.md",
    f"cd {MP} && sed -n 1,10p feedback_alpha_rule.md",
    f"FOO=1 tail {MP}feedback_alpha_rule.md | wc -l",
    f"grep -n promoted {MP}feedback_alpha_rule.md",
])
def test_a_bash_print_with_output_is_an_entry(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "some text")])
    assert _chans(res.events) == [("feedback_alpha_rule", "bash_print")]


def test_a_bash_print_with_no_output_is_not_an_entry(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": f"cat {MP}feedback_alpha_rule.md"}),
                           _tool_result("t1", "   ")])
    assert res.events == []


def test_a_failed_bash_is_not_an_entry(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": f"cat {MP}feedback_alpha_rule.md"}),
                           _tool_result("t1", "No such file", error=True)])
    assert res.events == []


def test_a_failed_bash_whose_output_carries_the_files_frontmatter_is_an_entry(tmp_path):
    """Real case, 2026-11-22: `cat x.md; false` exits 1 and the file's text still entered."""
    out = "Exit code 1\n---\nname: feedback_alpha_rule\ndescription: d\n"
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": f"cat {MP}feedback_alpha_rule.md; false"}),
                           _tool_result("t1", out, error=True)])
    assert _chans(res.events) == [("feedback_alpha_rule", "bash_print")]


def test_grep_lines_in_a_failed_result_are_entries(tmp_path):
    """Real case, 2026-11-22: a checker exits 1 and prints `<stem>.md: <text>` lines."""
    out = "Exit code 1\nfeedback_alpha_rule.md:4:the rule text"
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": "python3 tools/x.py"}),
                           _tool_result("t1", out, error=True)])
    assert _chans(res.events) == [("feedback_alpha_rule", "grep_line")]


def test_a_failed_read_with_grep_like_text_is_still_not_an_entry(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Read", {"file_path": MP + "feedback_alpha_rule.md"}),
                           _tool_result("t1", "feedback_alpha_rule.md:1:x", error=True)])
    assert res.events == []


@pytest.mark.parametrize("cmd", [
    f"sed -i '' 's/a/b/' {MP}feedback_alpha_rule.md",
    f"echo x > {MP}feedback_alpha_rule.md",
    f"cp {MP}feedback_alpha_rule.md /tmp/x",
    f"ls {MP} | grep alpha",
    f"ls {MP}feedback_alpha_rule.md",
])
def test_bash_writes_and_listings_are_not_entries(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "x")])
    assert [e for e in res.events if e.channel == "bash_print"] == []


def test_a_python_heredoc_naming_a_file_is_not_an_entry_but_is_counted_as_a_gap(tmp_path):
    cmd = f"python3 - <<'EOF'\nprint(open('{MP}feedback_alpha_rule.md').read())\nEOF"
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "body")])
    assert res.events == []
    assert res.gaps["heredoc_refs"] == 1


# ---------------------------------------------------------------------------- grep content lines

def test_grep_style_content_lines_from_any_tool_are_entries(tmp_path):
    out = "feedback_alpha_rule.md:12:the rule text\nreference_beta_fact.md-3-context line"
    res = _scan(tmp_path, [_tool_use("t1", "Grep", {"pattern": "x"}), _tool_result("t1", out)])
    assert _chans(res.events) == [("feedback_alpha_rule", "grep_line"),
                                  ("reference_beta_fact", "grep_line")]


@pytest.mark.parametrize("out", [
    "feedback_alpha_rule.md:3",            # grep -c
    "feedback_alpha_rule.md",              # grep -l
    "/x/proj/memory/feedback_alpha_rule.md\nproject_gamma_note.md",
])
def test_counts_and_file_lists_are_not_content(tmp_path, out):
    res = _scan(tmp_path, [_tool_use("t1", "Grep", {"pattern": "x"}), _tool_result("t1", out)])
    assert res.events == []


# ---------------------------------------------------------------------------- attachments and text

def test_a_file_attachment_is_an_entry(tmp_path):
    rec = {"type": "attachment", "timestamp": "2026-11-20T18:00:00Z",
           "attachment": {"type": "file", "filename": MP + "user_delta_pref.md"}}
    res = _scan(tmp_path, [rec])
    assert _chans(res.events) == [("user_delta_pref", "file_attachment")]


@pytest.mark.parametrize("rec", [
    {"type": "attachment", "timestamp": "2026-11-20T18:00:00Z",
     "attachment": {"type": "instructions",
                    "files": [{"path": MP + "MEMORY.md", "content": "see feedback_alpha_rule"}]}},
    {"type": "user", "timestamp": "2026-11-20T18:00:00Z",
     "message": {"content": "<system-reminder>feedback_alpha_rule.md was edited</system-reminder>"}},
    {"type": "user", "timestamp": "2026-11-20T18:00:00Z",
     "message": {"content": "please look at feedback_alpha_rule"}},
    {"type": "attachment", "timestamp": "2026-11-20T18:00:00Z",
     "attachment": {"type": "edited_text_file", "filename": MP + "feedback_alpha_rule.md"}},
])
def test_names_in_text_and_always_loaded_files_are_not_entries(tmp_path, rec):
    assert _scan(tmp_path, [rec]).events == []


def test_records_of_other_types_are_skipped(tmp_path):
    rec = {"type": "file-history-snapshot", "timestamp": "2026-11-20T18:00:00Z",
           "attachment": {"type": "file", "filename": MP + "user_delta_pref.md"}}
    assert _scan(tmp_path, [rec]).events == []


def test_a_corrupt_line_is_skipped_not_fatal(tmp_path):
    mem = _mem(tmp_path)
    p = mem.parent / "s1.jsonl"
    p.write_text("{not json\n" + json.dumps(_tool_use("t1", "Read", {"file_path": MP + "feedback_alpha_rule.md"}))
                 + "\n" + json.dumps(_tool_result("t1", "x")) + "\n")
    res = mu.collect(mem.parent, mu.memory_stems(mem))
    assert _chans(res.events) == [("feedback_alpha_rule", "read")]


# ---------------------------------------------------------------------------- sessions and dedup

def test_a_resumed_session_copying_a_tool_call_counts_it_once(tmp_path):
    mem = _mem(tmp_path)
    recs = [_tool_use("t1", "Read", {"file_path": MP + "feedback_alpha_rule.md"}), _tool_result("t1", "x")]
    _write(mem.parent / "s1.jsonl", recs)
    _write(mem.parent / "s2.jsonl", recs)
    res = mu.collect(mem.parent, mu.memory_stems(mem))
    assert len(res.events) == 1


def test_a_subagent_transcript_belongs_to_its_parent_session(tmp_path):
    mem = _mem(tmp_path)
    _write(mem.parent / "parent1.jsonl", [{"type": "user", "timestamp": "2026-11-20T17:00:00Z",
                                           "message": {"content": "hi"}}])
    _write(mem.parent / "parent1" / "subagents" / "workflows" / "a1.jsonl",
           [_tool_use("t9", "Read", {"file_path": MP + "reference_beta_fact.md"}), _tool_result("t9", "x")])
    res = mu.collect(mem.parent, mu.memory_stems(mem))
    assert [(e.session, e.stem) for e in res.events] == [("parent1", "reference_beta_fact")]


def test_excluded_sessions_are_not_scanned(tmp_path):
    mem = _mem(tmp_path)
    _write(mem.parent / "s1.jsonl", [_tool_use("t1", "Read", {"file_path": MP + "feedback_alpha_rule.md"}),
                                     _tool_result("t1", "x")])
    res = mu.collect(mem.parent, mu.memory_stems(mem), exclude_sessions={"s1"})
    assert res.events == []


def test_entry_dates_are_local_pacific_dates(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Read", {"file_path": MP + "feedback_alpha_rule.md"},
                                     ts="2026-11-20T03:00:00Z"),
                           _tool_result("t1", "x", ts="2026-11-20T03:00:01Z")])
    assert res.events[0].day == date(2026, 11, 19)


def test_the_window_is_the_first_and_last_timestamp_of_any_record(tmp_path):
    res = _scan(tmp_path, [{"type": "user", "timestamp": "2026-09-01T18:00:00Z", "message": {"content": "a"}},
                           {"type": "user", "timestamp": "2026-11-20T18:00:00Z", "message": {"content": "b"}}])
    assert (res.window_start, res.window_end) == (date(2026, 9, 1), date(2026, 11, 20))


def test_sibling_project_folders_are_reported_as_a_gap(tmp_path):
    mem = _mem(tmp_path)
    sib = tmp_path / "proj-worktree-x"
    _write(sib / "z.jsonl", [{"type": "user", "timestamp": "2026-11-20T18:00:00Z", "message": {"content": "a"}}])
    res = mu.collect(mem.parent, mu.memory_stems(mem), sibling_glob=str(tmp_path / "proj-*"))
    assert res.gaps["sibling_transcripts_not_scanned"] == 1


# ---------------------------------------------------------------------------- breadth

def test_session_breadth_counts_distinct_files_per_session():
    ev = [mu.Event("s1", "a", "read", date(2026, 11, 1)), mu.Event("s1", "a", "bash_print", date(2026, 11, 2)),
          mu.Event("s1", "b", "read", date(2026, 11, 2)), mu.Event("s2", "a", "read", date(2026, 11, 3))]
    assert mu.session_breadth(ev) == {"s1": 2, "s2": 1}


# ---------------------------------------------------------------------------- per-file summary

def _ev(session, stem, d, ch="read"):
    return mu.Event(session, stem, ch, d)


def test_summary_last_entry_ignores_broad_sessions_but_keeps_their_events():
    broad = [_ev("audit", f"f{i}", date(2026, 11, 10)) for i in range(25)]
    ordinary = [_ev("work", "f0", date(2026, 11, 1))]
    s = mu.summarize(broad + ordinary, reconfirms=lambda n: n < 20)
    assert s["f0"]["last_context_entry"] == date(2026, 11, 1)
    assert s["f0"]["last_entry_any_session"] == date(2026, 11, 10)
    assert s["f0"]["broad_session_entries"] == 1
    assert s["f1"]["last_context_entry"] is None
    assert s["f1"]["last_entry_any_session"] == date(2026, 11, 10)


def test_summary_counts_channels_sessions_and_use_days():
    ev = [_ev("s1", "a", date(2026, 11, 1), "read"), _ev("s1", "a", date(2026, 11, 1), "grep_line"),
          _ev("s2", "a", date(2026, 11, 5), "bash_print")]
    s = mu.summarize(ev, reconfirms=lambda n: True)["a"]
    assert s["channels"] == {"read": 1, "grep_line": 1, "bash_print": 1}
    assert s["sessions"] == 2
    assert s["use_days"] == [date(2026, 11, 1), date(2026, 11, 5)]


def test_use_gaps_are_days_between_consecutive_use_days():
    assert mu.use_gaps([date(2026, 11, 1), date(2026, 11, 5), date(2026, 11, 6)]) == [4, 1]
    assert mu.use_gaps([date(2026, 11, 1)]) == []
    assert mu.use_gaps([]) == []


# ---------------------------------------------------------------------------- malformed input (mutation run 1)

def test_records_without_timestamps_neither_crash_nor_move_the_window(tmp_path):
    res = _scan(tmp_path, [{"type": "user", "timestamp": "2026-11-20T18:00:00Z", "message": {"content": "a"}},
                           {"type": "user", "message": {"content": "no ts"}}])
    assert (res.window_start, res.window_end) == (date(2026, 11, 20), date(2026, 11, 20))


def test_records_of_other_types_do_not_move_the_window(tmp_path):
    res = _scan(tmp_path, [{"type": "user", "timestamp": "2026-11-20T18:00:00Z", "message": {"content": "a"}},
                           {"type": "file-history-snapshot", "timestamp": "2026-11-25T18:00:00Z"}])
    assert res.window_end == date(2026, 11, 20)


@pytest.mark.parametrize("rec", [
    [1, 2],
    {"type": "assistant", "timestamp": "2026-11-20T18:00:00Z", "message": {"content": ["plain string"]}},
    {"type": "user", "timestamp": "2026-11-20T18:00:00Z", "message": {"content": ["plain string"]}},
    {"type": "user", "timestamp": "2026-11-20T18:00:00Z",
     "message": {"content": [{"type": "text", "text": "feedback_alpha_rule.md:3:looks like grep"}]}},
])
def test_odd_shapes_are_skipped_without_events(tmp_path, rec):
    assert _scan(tmp_path, [rec]).events == []


def test_a_tool_result_given_as_a_list_of_text_blocks_is_read(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": f"cat {MP}feedback_alpha_rule.md"}),
                           {"type": "user", "timestamp": "2026-11-20T18:00:01Z",
                            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                                     "content": [{"type": "text", "text": "body"}, "junk"]}]}}])
    assert _chans(res.events) == [("feedback_alpha_rule", "bash_print")]


@pytest.mark.parametrize("cmd", [f"cd {MP}feedback_alpha_rule.md", "X=feedback_alpha_rule"])
def test_a_segment_left_empty_after_cd_or_assignments_is_skipped(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "x")])
    assert res.events == []


def test_grep_lines_shown_by_reading_another_file_count(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Read", {"file_path": "/x/output/report.md"}),
                           _tool_result("t1", "12\tfeedback_alpha_rule.md:3:the rule text")])
    assert _chans(res.events) == [("feedback_alpha_rule", "grep_line")]


def test_reading_a_memory_file_counts_once_as_a_read(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Read", {"file_path": MP + "feedback_alpha_rule.md"}),
                           _tool_result("t1", "1\treference_beta_fact.md:2:cross reference text")])
    assert _chans(res.events) == [("feedback_alpha_rule", "read")]


def test_a_file_attachment_outside_memory_is_not_an_entry(tmp_path):
    rec = {"type": "attachment", "timestamp": "2026-11-20T18:00:00Z",
           "attachment": {"type": "file", "filename": "/x/output/notes.md"}}
    assert _scan(tmp_path, [rec]).events == []


def test_last_context_entry_is_the_latest_day_whatever_the_event_order():
    ev = [_ev("s1", "a", date(2026, 11, 9)), _ev("s2", "a", date(2026, 11, 2)), _ev("s3", "a", date(2026, 11, 5))]
    s = mu.summarize(ev, reconfirms=lambda n: True)["a"]
    assert s["last_context_entry"] == date(2026, 11, 9)
    assert s["last_entry_any_session"] == date(2026, 11, 9)
