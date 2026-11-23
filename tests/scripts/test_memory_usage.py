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
import re
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
    f"FOO=1 tail {MP}feedback_alpha_rule.md | head -5",
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
    """A grep that printed the rule's lines and then failed still put them in context."""
    out = "Exit code 1\nfeedback_alpha_rule.md:4:the rule text"
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": f"cd {MP} && grep -n rule *.md; false"}),
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
    """Folders whose name embeds the project's (worktrees, scratchpads) hold transcripts
    this scan does not read, including their subagent transcripts."""
    mem = _mem(tmp_path)
    rec = [{"type": "user", "timestamp": "2026-11-20T18:00:00Z", "message": {"content": "a"}}]
    _write(tmp_path / "proj-worktree-x" / "z.jsonl", rec)
    _write(tmp_path / "-tmp-scratch--proj-y" / "z.jsonl", rec)
    _write(tmp_path / "-tmp-scratch--proj-y" / "z" / "subagents" / "a.jsonl", rec)
    _write(tmp_path / "other" / "z.jsonl", rec)
    assert mu.sibling_dirs(mem.parent) == sorted([tmp_path / "-tmp-scratch--proj-y", tmp_path / "proj-worktree-x"])
    res = mu.collect(mem.parent, mu.memory_stems(mem), sibling_dirs=mu.sibling_dirs(mem.parent))
    assert res.gaps["sibling_transcripts_not_scanned"] == 3
    assert mu.collect(mem.parent, mu.memory_stems(mem)).gaps["sibling_transcripts_not_scanned"] == 0


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


def test_grep_like_lines_inside_another_file_read_are_not_entries(tmp_path):
    """Real data, 2026-11-22: 1,233 such lines were citations in docs (`x.md:89,269`), not
    rule text. Reverted from 35c44a3e."""
    for text in ("12\tfeedback_alpha_rule.md:3:the rule text",
                 "/x/proj/memory/feedback_alpha_rule.md:3:saved grep output in a report"):
        res = _scan(tmp_path / text[:2].strip("/\t"), [
            _tool_use("t1", "Read", {"file_path": "/x/output/report.md"}), _tool_result("t1", text)])
        assert res.events == []


@pytest.mark.parametrize("out", [
    "see `feedback_alpha_rule.md:89,269`; and more",
    "feedback_alpha_rule.md:12, 40",
])
def test_a_line_citation_is_not_a_grep_content_line(tmp_path, out):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": "cat output/doc.md"}), _tool_result("t1", out)])
    assert res.events == []


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


def test_only_the_read_tool_counts_as_a_read_even_if_another_tool_names_a_file_path(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "NotebookRead", {"file_path": MP + "feedback_alpha_rule.md"}),
                           _tool_result("t1", "cell text")])
    assert res.events == []


# ---------------------------------------------------------------------------- Codex r1 (2026-11-22)

def test_a_resumed_transcript_joins_its_original_session_for_breadth(tmp_path):
    """r1 F1 (P0): a resume copies the old tool calls into a new file. The two files are one
    logical session, so an audit split across them still counts as one broad session."""
    mem = tmp_path / "proj" / "memory"
    mem.mkdir(parents=True)
    stems = [f"feedback_r{i:02d}" for i in range(25)]
    for s in stems:
        (mem / f"{s}.md").write_text("x")
    def reads(ids):
        out = []
        for i in ids:
            out += [_tool_use(f"t{i}", "Read", {"file_path": MP + f"{stems[i]}.md"}), _tool_result(f"t{i}", "x")]
        return out
    _write(mem.parent / "s1.jsonl", reads(range(15)))
    _write(mem.parent / "s2.jsonl", reads(range(25)))          # resume: 15 copied + 10 new
    res = mu.collect(mem.parent, mu.memory_stems(mem))
    assert mu.session_breadth(res.events) == {"s1": 25}
    summary = mu.summarize(res.events, reconfirms=lambda n: n < 20)
    assert all(v["last_context_entry"] is None for v in summary.values())
    assert len(summary) == 25


def test_sessions_that_share_no_tool_call_stay_apart(tmp_path):
    mem = _mem(tmp_path)
    _write(mem.parent / "s1.jsonl", [_tool_use("t1", "Read", {"file_path": MP + "feedback_alpha_rule.md"}),
                                     _tool_result("t1", "x")])
    _write(mem.parent / "s2.jsonl", [_tool_use("t2", "Read", {"file_path": MP + "reference_beta_fact.md"}),
                                     _tool_result("t2", "x")])
    res = mu.collect(mem.parent, mu.memory_stems(mem))
    assert mu.session_breadth(res.events) == {"s1": 1, "s2": 1}


@pytest.mark.parametrize("cmd", [
    "cat /tmp/feedback_alpha_rule.md",
    "head output/analysis/feedback_alpha_rule.md",
    "grep -rn '[[feedback_alpha_rule' output/",               # searching for the name
])
def test_a_print_of_a_same_named_file_outside_memory_is_not_an_entry(tmp_path, cmd):
    """r1 F2 (P1): a path that names a folder other than memory/ is another file."""
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "text")])
    assert res.events == []


@pytest.mark.parametrize("cmd", [
    "cat memory/feedback_alpha_rule.md",
    "cat $MEM/feedback_alpha_rule.md",
    "cat \"${MEM}/feedback_alpha_rule.md\"",
    "cd ~/x/memory && cat feedback_alpha_rule.md",
    "cat \"$M\"/feedback_alpha_rule.md",
    "cat 'memory'/feedback_alpha_rule.md",
    "cat memory/canonical-sidecar/feedback_alpha_rule.md",   # backup copy of the same text
])
def test_a_print_through_memory_or_a_variable_or_a_bare_name_is_an_entry(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "text")])
    assert _chans(res.events) == [("feedback_alpha_rule", "bash_print")]


@pytest.mark.parametrize("content", ["", [], [{"type": "text", "text": ""}]])
def test_an_empty_read_result_is_not_an_entry(tmp_path, content):
    """r1 F4 (P1): no text entered context."""
    res = _scan(tmp_path, [_tool_use("t1", "Read", {"file_path": MP + "feedback_alpha_rule.md"}),
                           {"type": "user", "timestamp": "2026-11-20T18:00:01Z",
                            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                                     "content": content}]}}])
    assert res.events == []


def test_tool_calls_without_an_id_never_join_two_sessions(tmp_path):
    mem = _mem(tmp_path)
    for s, stem in (("s1", "feedback_alpha_rule"), ("s2", "reference_beta_fact")):
        _write(mem.parent / f"{s}.jsonl", [
            {"type": "assistant", "timestamp": "2026-11-20T18:00:00Z",
             "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "true"}}]}},
            _tool_use(f"{s}-t", "Read", {"file_path": MP + f"{stem}.md"}), _tool_result(f"{s}-t", "x")])
    res = mu.collect(mem.parent, mu.memory_stems(mem))
    assert mu.session_breadth(res.events) == {"s1": 1, "s2": 1}


# ---------------------------------------------------------------------------- Grok r1 (2026-11-22)

@pytest.mark.parametrize("out", [
    "output/foo.md:12:see feedback_alpha_rule.md:3:quoted text",       # a doc quoting grep output
    "notes: feedback_alpha_rule.md:3:the rule text",                     # not at line start
    "/x/output/analysis/feedback_alpha_rule.md:3:same-named file elsewhere",
    "/tmp/feedback_alpha_rule.md-3-context line",
])
def test_a_grep_line_counts_only_as_the_lines_own_memory_file_path(tmp_path, out):
    """Grok r1 F1 (P1): grep output starts with the matched file's path; that path must be
    a memory file by the same rule as a Bash print."""
    res = _scan(tmp_path, [_tool_use("t1", "Grep", {"pattern": "x"}), _tool_result("t1", out)])
    assert res.events == []


@pytest.mark.parametrize("out", [
    "/x/proj/memory/feedback_alpha_rule.md:3:the rule text",
    "memory/canonical-sidecar/feedback_alpha_rule.md:3:the rule text",
    "./feedback_alpha_rule.md:3:the rule text",
    "  feedback_alpha_rule.md-4-context",
    "$MEM/feedback_alpha_rule.md:3:text",
])
def test_a_grep_line_through_memory_a_variable_or_a_bare_name_counts(tmp_path, out):
    res = _scan(tmp_path, [_tool_use("t1", "Grep", {"pattern": "x"}), _tool_result("t1", out)])
    assert _chans(res.events) == [("feedback_alpha_rule", "grep_line")]


def test_a_dot_slash_print_after_cd_counts(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": "cd memory && cat ./feedback_alpha_rule.md"}),
                           _tool_result("t1", "body")])
    assert _chans(res.events) == [("feedback_alpha_rule", "bash_print")]


# ---------------------------------------------------------------------------- Codex r2 (2026-11-22)

@pytest.mark.parametrize("out", [
    "memory/notes.md:12:feedback_alpha_rule.md:3:saved grep output",
    "memory/notes.md-12-feedback_alpha_rule.md-3-saved context line",
    "/x/proj/memory/MEMORY.md:4:feedback_alpha_rule.md:2:quoted",
])
def test_a_rule_cited_inside_another_memory_files_grep_line_does_not_count(tmp_path, out):
    """Codex r2 F1 (P0): the opening path is the matched file; a rule name after it is text."""
    res = _scan(tmp_path, [_tool_use("t1", "Grep", {"pattern": "x"}), _tool_result("t1", out)])
    assert res.events == []


def test_a_print_naming_a_rule_after_another_memory_path_does_not_count(tmp_path):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": "cat memory/notes.md-feedback_alpha_rule.md"}),
                           _tool_result("t1", "x")])
    assert res.events == []


# ---------------------------------------------------------------------------- Grok r2 (2026-11-22)

@pytest.mark.parametrize("cmd", [
    "grep -rn feedback_alpha_rule output/",
    "grep -rn 'feedback_alpha_rule' output/",
    "grep -rl feedback_alpha_rule .",
    "rg feedback_alpha_rule memory/",
    "grep -rn feedback_alpha_rule.md memory/",
    "grep -e feedback_alpha_rule -rn output/",
    f"grep -l promoted {MP}feedback_alpha_rule.md",
    f"grep -c promoted {MP}feedback_alpha_rule.md",
    f"rg --files-with-matches promoted {MP}feedback_alpha_rule.md",
    f"grep -q promoted {MP}feedback_alpha_rule.md",
])
def test_a_grep_for_a_rule_name_or_one_that_prints_no_content_is_not_a_print(tmp_path, cmd):
    """Grok r2 F1 (P0): the pattern is a search for the name, not the file; -l, -c and -q
    print names or counts, not the file's text."""
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "output/x.md:3:hit")])
    assert [e for e in res.events if e.channel == "bash_print"] == []


@pytest.mark.parametrize("cmd", [
    f"grep -n promoted {MP}feedback_alpha_rule.md",
    f"grep -e promoted -n {MP}feedback_alpha_rule.md",
    f"rg -n 'reopen' {MP}feedback_alpha_rule.md",
    "cd memory && grep -n promoted feedback_alpha_rule.md",
    f"grep -A3 -n promoted {MP}feedback_alpha_rule.md",
])
def test_a_grep_of_a_rule_file_with_content_output_is_a_print(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "3:promoted: no")])
    assert _chans(res.events) == [("feedback_alpha_rule", "bash_print")]


@pytest.mark.parametrize("seg,files", [
    ("grep -n promoted memory/a.md", ["memory/a.md"]),
    ("grep -n -- -weird memory/a.md", ["memory/a.md"]),            # after --, first operand is the pattern
    ("grep -e x -- -odd.md", ["-odd.md"]),
    ("grep --color=never -n promoted memory/a.md", ["memory/a.md"]),
    ("grep --regexp=promoted -n memory/a.md", ["memory/a.md"]),
    ("grep --regexp promoted memory/a.md", ["memory/a.md"]),
    ("grep --include '*.md' -rn promoted memory/", ["memory/"]),
    ("grep --max-count=1 promoted memory/a.md", ["memory/a.md"]),
    ("grep -A 3 promoted memory/a.md", ["memory/a.md"]),
    ("grep -A3 promoted memory/a.md", ["memory/a.md"]),
    ("grep -nA 3 promoted memory/a.md", ["memory/a.md"]),
    ("grep -nA3 promoted memory/a.md", ["memory/a.md"]),
    ("grep -e x -e y memory/a.md memory/b.md", ["memory/a.md", "memory/b.md"]),
    ("rg --files-without-match x memory/", None),
    ("grep -rnl x memory/", None),
    ("grep 'unbalanced memory/a.md", ["memory/a.md"]),             # shlex error: plain split
])
def test_search_file_args_finds_only_the_file_operands(seg, files):
    assert mu._search_file_args(seg) == files


# ---------------------------------------------------------------------------- Codex r3 (2026-11-22)

@pytest.mark.parametrize("seg,files", [
    ("grep -o feedback_alpha_rule memory/a.md", None),                  # F1: only-matching prints the match
    ("rg --only-matching x memory/a.md", None),
    ("rg --encoding utf-8 memory/a.md output/", ["output/"]),            # F2: value flags consume values
    ("grep --exclude-from excludes memory/a.md output/", ["output/"]),
    ("grep -d skip memory/a.md output/", ["output/"]),
    ("grep --binary-files text memory/a.md output/", ["output/"]),
    ("grep --label x memory/a.md output/", None),                      # confirm F2: --label fakes the path
    ("rg -j 4 memory/a.md output/", ["output/"]),
    ("rg -r X memory/a.md output/", ["output/"]),                       # rg -r is --replace (a value)
    ("grep -r memory/a.md output/", ["output/"]),                       # grep -r is recursive (no value)
    ("rg --sort path memory/a.md output/", ["output/"]),
    ("rg --engine pcre2 memory/a.md output/", ["output/"]),
    ("grep --some-new-flag memory/a.md output/", None),                 # unknown: fail safe, count nothing
    ("grep -K memory/a.md output/", None),
    ("rg --some-new-flag=1 promoted memory/a.md", ["memory/a.md"]),     # '=' keeps the value inside
    ("grep --line-number --ignore-case promoted memory/a.md", ["memory/a.md"]),
    ("rg --no-heading --hidden -n promoted memory/a.md", ["memory/a.md"]),
])
def test_search_file_args_reads_each_verbs_own_option_grammar(seg, files):
    assert mu._search_file_args(seg) == files


@pytest.mark.parametrize("cmd", [
    f"env -i grep -n promoted {MP}feedback_alpha_rule.md",
    f"env -u GREP_OPTIONS grep -n promoted {MP}feedback_alpha_rule.md",
    f"env -i X=1 cat {MP}feedback_alpha_rule.md",
    f"time -p cat {MP}feedback_alpha_rule.md",
])
def test_env_options_do_not_hide_the_real_command(tmp_path, cmd):
    """Codex r3 F3 (P1)."""
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "3:promoted: no")])
    assert _chans(res.events) == [("feedback_alpha_rule", "bash_print")]


@pytest.mark.parametrize("seg", ["", "   ", "cat memory/a.md", "egrep x memory/a.md"])
def test_search_file_args_is_none_for_an_empty_or_non_search_segment(seg):
    assert mu._search_file_args(seg) is None


# ---------------------------------------------------------------------------- Grok r3 (2026-11-22)

@pytest.mark.parametrize("seg", [
    "rg -h promoted memory/a.md",          # rg -h is --help
    "rg -V promoted memory/a.md",          # --version
    "rg -ih promoted memory/a.md",
    "rg -5 promoted memory/a.md",          # rg has no -NUM context
    "grep -V promoted memory/a.md",        # grep -V is --version
])
def test_help_and_version_shorts_are_outside_the_grammar(seg):
    """Grok r3 F1 (P1): these print help or a version, not the file."""
    assert mu._search_file_args(seg) is None


def test_grep_h_still_counts_it_is_no_filename_there():
    assert mu._search_file_args("grep -h promoted memory/a.md") == ["memory/a.md"]
    assert mu._search_file_args("grep -5 promoted memory/a.md") == ["memory/a.md"]


@pytest.mark.parametrize("cmd", [
    f"cd -C /tmp cat {MP}feedback_alpha_rule.md",
    f"FOO=1 -C /tmp cat {MP}feedback_alpha_rule.md",
])
def test_env_option_skipping_applies_only_after_env_or_time(cmd):
    """Grok r3 F2: kills classify_bash::IF_TRUE::fcb00f22, which was wrongly allowlisted."""
    assert mu.classify_bash(cmd, {"feedback_alpha_rule"})[0] == set()


# ---------------------------------------------------------------------------- B4 confirmation (2026-11-22)
# Codex and Grok confirmation reviews of 2ca32856: four ways an entry was counted while the
# rule's text never reached the tool result.

@pytest.mark.parametrize("cmd", [
    f"FOO=1 tail {MP}feedback_alpha_rule.md | wc -l",               # Codex F1, Grok F2: a count
    f"cat {MP}feedback_alpha_rule.md | md5",
    f"cat {MP}feedback_alpha_rule.md | grep -c promoted",
    f"cat {MP}feedback_alpha_rule.md | python3 -c 'print(1)'",
    f"cat {MP}feedback_alpha_rule.md >/dev/null; echo done",
    f"cat {MP}feedback_alpha_rule.md > /tmp/copy.txt && echo ok",
    f"cat {MP}feedback_alpha_rule.md | head -3 > /tmp/copy.txt; echo ok",
    f"cat {MP}feedback_alpha_rule.md &> /tmp/copy.txt; echo ok",
    f"cat {MP}feedback_alpha_rule.md 1>/tmp/copy.txt; echo ok",
])
def test_a_print_whose_stdout_does_not_reach_the_result_is_not_an_entry(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "17")])
    assert [e for e in res.events if e.channel == "bash_print"] == []


@pytest.mark.parametrize("cmd", [
    f"cat {MP}feedback_alpha_rule.md 2>/dev/null",
    f"cat {MP}feedback_alpha_rule.md 2>&1 | head -40",
    f"sed -n 1,30p {MP}feedback_alpha_rule.md | cut -c1-300",
    f"cat {MP}feedback_alpha_rule.md | tail -30 | cut -c1-300",
    f"head -9 {MP}feedback_alpha_rule.md | grep -E 'occurrences|promoted'",
    f"cat {MP}feedback_alpha_rule.md | sed -n '1,60p'",
    f"cat {MP}feedback_alpha_rule.md | nl | cat",
    f"cat {MP}feedback_alpha_rule.md >&2",
])
def test_a_print_through_pass_through_filters_is_an_entry(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "promoted: no")])
    assert _chans(res.events) == [("feedback_alpha_rule", "bash_print")]


@pytest.mark.parametrize("cmd", [
    "cd output/drain && cat feedback_alpha_rule.triage.json",       # found while measuring
    "cat $D/feedback_alpha_rule.verify.json | head -c 1500",
    "cat memory/feedback_alpha_rule.mdx",
    "cat memory/feedback_alpha_rule",
])
def test_a_print_counts_only_for_the_rules_own_md_file(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "x")])
    assert res.events == []


@pytest.mark.parametrize("cmd,out", [
    ("printf 'feedback_alpha_rule.md:3:not the rule text\\n'",          # Codex F2
     "feedback_alpha_rule.md:3:not the rule text"),
    ("grep --label feedback_alpha_rule.md -H x /tmp/other",
     "feedback_alpha_rule.md:1:unrelated text"),
    ("PYTHONIOENCODING=utf-8 python3 tools/promotion_schema.py --memory-dir $M",  # a checker message
     "feedback_alpha_rule.md: I4 reopen_gate names no number"),
    ("cat /x/output/report.md",                                       # Grok F3: a report quoting grep output
     "/x/proj/memory/feedback_alpha_rule.md:3:saved grep output in a report"),
    ("cd output/drain; cat x.triage.json",
     "feedback_alpha_rule.md: 'a quoted clause'"),
    ("rg -o promoted memory/",                                        # Grok F1: only-matching
     "memory/feedback_alpha_rule.md:promoted"),
    ("grep -oH feedback_alpha_rule memory/feedback_alpha_rule.md",
     "memory/feedback_alpha_rule.md:feedback_alpha_rule"),
    ("grep -rno promoted memory/",
     "memory/feedback_alpha_rule.md:12:promoted"),
])
def test_a_grep_shaped_line_counts_only_from_a_grep_that_prints_lines(tmp_path, cmd, out):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert [e for e in res.events if e.channel == "grep_line"] == []


@pytest.mark.parametrize("cmd", [
    "grep -rn promoted memory/",
    "M=~/m/memory; /usr/bin/grep -n promoted $M/*.md | head -20",
    "cd memory && rg -n promoted",
    "env -i grep -rn promoted memory/",
])
def test_a_grep_line_from_a_line_printing_grep_is_an_entry(tmp_path, cmd):
    out = "memory/feedback_alpha_rule.md:12:promoted: no"
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert _chans(res.events) == [("feedback_alpha_rule", "grep_line")]


@pytest.mark.parametrize("tool", ["Agent", "Task", "mcp__x__y", "WebFetch"])
def test_a_grep_shaped_line_from_a_non_search_tool_is_not_an_entry(tmp_path, tool):
    res = _scan(tmp_path, [_tool_use("t1", tool, {"prompt": "x"}),
                           _tool_result("t1", "feedback_alpha_rule.md:3:quoted by a subagent")])
    assert res.events == []


@pytest.mark.parametrize("cmd", [
    f"grep -E 'occurrences|promoted' {MP}feedback_alpha_rule.md",       # quoted | is the pattern
    f"grep -n \"a;b\" {MP}feedback_alpha_rule.md",
    f"grep -n 'a&&b' {MP}feedback_alpha_rule.md | head -3",
])
def test_separators_inside_quotes_do_not_split_the_command(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "3:promoted: no")])
    assert _chans(res.events) == [("feedback_alpha_rule", "bash_print")]


@pytest.mark.parametrize("cmd", [
    f"cat notes.txt it's; ls {MP}feedback_alpha_rule.md",              # unclosed quote: no joined stage
    f"head -3 don't.txt | cat; ls {MP}feedback_alpha_rule.md",
])
def test_an_unclosed_quote_does_not_join_later_commands_into_a_print(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "x")])
    assert res.events == []


@pytest.mark.parametrize("text,commands", [
    ("a | b && c; d\ne || f", [["a ", " b "], [" c"], [" d"], ["e "], [" f"]]),
    ("grep 'x|y' f | head", [["grep 'x|y' f ", " head"]]),
    ('grep "x;y" f', [['grep "x;y" f']]),
    ("echo it's | cat; ls", [["echo it's ", " cat"], [" ls"]]),
    ("a|b", [["a", "b"]]),
    ('echo \\"x; grep "a|b" f', [['echo \\"x'], [' grep "a|b" f']]),   # an escaped quote opens nothing
    ('grep "a\\"|b" f | head', [['grep "a\\"|b" f ', ' head']]),
    ("grep 'a\\' f | head", [["grep 'a\\' f ", " head"]]),        # no escapes inside single quotes
])
def test_split_commands(text, commands):
    assert mu._split_commands(text) == commands


@pytest.mark.parametrize("cmd", [
    'cd memory; for f in feedback_alpha_rule.md; do echo "$f: $(grep -m3 -E \'^ *promoted:\' $f)"; done',
    "grep -l x $M/feedback_*.md | head -8 | xargs -I{} /usr/bin/grep -H 'promoted:' {}",
    "ls memory/*.md | xargs -n 1 -P 4 grep -H promoted",
])
def test_a_grep_run_by_xargs_or_inside_a_substitution_prints_lines(tmp_path, cmd):
    out = "memory/feedback_alpha_rule.md:   promoted: no"
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert _chans(res.events) == [("feedback_alpha_rule", "grep_line")]


@pytest.mark.parametrize("cmd", [
    'echo "$(grep -c x memory/feedback_alpha_rule.md)"',
    "ls memory | xargs -I{} grep -l promoted {}",
    "ls memory | xargs -0 echo",
    "xargs",
])
def test_a_substitution_or_xargs_without_a_line_printing_grep_is_not_a_source(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}),
                           _tool_result("t1", "memory/feedback_alpha_rule.md:3:x")])
    assert res.events == []


@pytest.mark.parametrize("text,bodies", [
    ("echo $(a) $(b)", ["a", "b"]),
    ("echo \"$(grep -E '^ *(occurrences|promoted):' f | tr x y)\"",
     ["grep -E '^ *(occurrences|promoted):' f | tr x y"]),
    ("x $(a $(b) c)", ["a $(b) c", "b"]),
    ('x $(echo ")" ; grep y f)', ['echo ")" ; grep y f']),
    ("x $(echo \\) y)", ["echo \\) y"]),
    ("x $(unclosed", []),
    ("no substitution (here)", []),
])
def test_substitutions(text, bodies):
    assert mu._substitutions(text) == bodies



# ---------------------------------------------------------------------------- Nick ruling (2026-11-22)
# "yeah I want to fix that", then "measure it where you need to to make the better decision": inside a
# compound command another command's output makes a non-empty result no proof that a grep matched.
# A grep there counts only when a result line matches its own pattern (measured on the snapshot:
# removes 36 of 48 no-match overcounts and loses 7 of 358 real reads).

R = f"{MP}feedback_alpha_rule.md"
B = f"{MP}reference_beta_fact.md"


@pytest.mark.parametrize("cmd,out", [
    (f"grep -n X {R}; echo ---; cat other.txt", "---\nother file text"),             # no match
    (f"grep X {R}; echo ---", "---"),
    (f"grep -n X {R}; grep -n Y notes.txt", "4:Y in notes"),                       # the other grep's line
    (f"grep -n X {R}; cat -n other.txt", "1\tother"),
    (f"grep -H X {R}; echo ---", "---"),
    (f"echo start; grep -n X {R} | head -3", "start"),
    (f"grep -n ALPHA {R}; echo ---", "3:alpha here\n---"),                         # case matters without -i
    (f"grep -nF 'a.b' {R}; echo ---", "1:axb\n---"),                               # -F is literal
    (f"grep -n 'a\\|b' {R}; echo ---", "1:c\n---"),
    (f"grep -nE 'q(x|y)' {R}; echo ---", "1:qz\n---"),
    (f"rg -n 'q+' {R}; echo ---", "1:x\n---"),
    (f"grep -n zzz {R}; grep -n alpha {B}", "3:alpha here"),                       # line belongs to beta's grep
])
def test_a_grep_in_a_compound_command_whose_pattern_matches_no_result_line_is_not_a_print(tmp_path, cmd, out):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert ("feedback_alpha_rule", "bash_print") not in _chans(res.events)


@pytest.mark.parametrize("cmd,out", [
    (f"grep -n X {R}; echo ---; cat other.txt", "12:X here\n---\nother"),
    (f"echo start; grep -n X {R} | head -3", "start\n3:X"),
    (f"grep X {R}; echo ---", "X matched line\n---"),                              # plain single-file grep
    (f"grep -H X {R}; echo ---", f"{R}:X here\n---"),
    (f"grep -in ALPHA {R}; echo ---", "3:alpha here\n---"),
    (f"grep -n --ignore-case ALPHA {R}; echo ---", "3:alpha here\n---"),
    (f"rg -nS alpha {R}; echo ---", "3:ALPHA here\n---"),                         # rg smart case
    (f"grep -nF 'a.b' {R}; echo ---", "1:a.b\n---"),
    (f"grep -n 'a\\|b' {R}; echo ---", "1:b\n---"),                               # BRE alternation
    (f"grep -n 'a+' {R}; echo ---", "1:a+\n---"),                                  # BRE + is literal
    (f"grep -nE 'q(x|y)' {R}; echo ---", "1:qy\n---"),
    (f"grep -n -e zzz -e alpha {R}; echo ---", "3:alpha\n---"),
    (f"grep -n alpha {R}; grep -n zzz {B}", "3:alpha here"),
    (f"grep -n \"$k\" {R}; echo ---", "---"),                                       # unknown pattern: counts
    (f"grep -nv alpha {R}; echo ---", "---"),                                       # -v: counts
    (f"grep -n -f pats.txt {R}; echo ---", "---"),
    (f"grep -n '[[:space:]]x' {R}; echo ---", "---"),                               # Python cannot read it: counts
    (f"grep -n X {R}", "anything"),                                                # standalone: unchanged
    (f"M={MP}; grep X $M/feedback_alpha_rule.md", "X line"),                       # assignments are not commands
    (f"cat {R}; echo ---", "---"),                                                 # cat: unchanged
])
def test_a_grep_in_a_compound_command_whose_pattern_matches_a_line_or_cannot_be_read_is_a_print(tmp_path, cmd, out):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert ("feedback_alpha_rule", "bash_print") in _chans(res.events)



# ---------------------------------------------------------------------------- mutation (7a6b20df)

@pytest.mark.parametrize("cmd,pattern", [
    ("grep -n -- -x f", "(?:-x)"),                      # after --, the first word is the pattern
    ("grep -e a -- f", "(?:a)"),                         # -e gave it: the word after -- is a file
    ("grep --regexp=a f", "(?:a)"),
    ("grep --regexp a f", "(?:a)"),
    ("grep --max-count 3 pat f", "(?:pat)"),             # a long option's value is not the pattern
    ("grep --max-count=3 pat f", "(?:pat)"),
    ("grep -A 2 pat f", "(?:pat)"),                      # nor a short option's value
    ("grep -A2 pat f", "(?:pat)"),
    ("grep -nA2 pat f", "(?:pat)"),
    ("grep -epat f", "(?:pat)"),
    ("grep -n -e pat f", "(?:pat)"),
    ("grep -ne pat f", "(?:pat)"),
    ("grep -e a -e b f", "(?:a)|(?:b)"),
    ("grep 'a+b' f", "(?:a\\+b)"),                       # basic regex: + is literal
    ("grep 'a\\+b' f", "(?:a+b)"),
    ("grep -E 'a+b' f", "(?:a+b)"),
    ("grep -F 'a.b' f", "(?:a\\.b)"),
])
def test_search_pattern_reads_the_pattern(cmd, pattern):
    assert mu._search_pattern(cmd.split()).pattern == pattern   # words as _stage_tokens gives them, quotes kept


@pytest.mark.parametrize("toks", [
    ["grep", "'unbalanced", "f"],                        # shell quoting it cannot parse
    ["grep", "-E", "(", "f"],                            # Python cannot compile it
    ["grep", "--"],                                      # no pattern
])
def test_search_pattern_it_cannot_evaluate_counts_anyway(toks):
    assert mu._search_pattern(toks) is mu.COUNTS_ANYWAY


@pytest.mark.parametrize("toks", [["grep", "-n", "-e"], ["grep", "--regexp"]])
def test_an_option_missing_its_pattern_gives_an_empty_pattern(toks):
    assert mu._search_pattern(toks).pattern == "(?:)"


def test_search_pattern_ignores_case_only_when_asked():
    assert mu._search_pattern(["grep", "-i", "a", "f"]).flags & re.I
    assert not mu._search_pattern(["grep", "a", "f"]).flags & re.I
    assert not mu._search_pattern(["rg", "-S", "A", "f"]).flags & re.I   # smart case: an upper letter


STEMS_ALPHA = {"feedback_alpha_rule"}


@pytest.mark.parametrize("cmd,text,found", [
    (f"grep -n alpha {R}", "3:alpha", set()),                                 # whole command: not this rule's job
    (f"grep alpha {R} > /dev/null; echo alpha", "alpha", set()),              # its stdout never reached the result
    (f"grep alpha {R} | wc -l; echo alpha", "1\nalpha", set()),
    (f"cat {R}; echo alpha", "alpha", set()),                                 # not a search
    (f"grep -n alpha {R}; echo ---", "3:alpha\n---", STEMS_ALPHA),
    (f"grep -n alpha {MP}notes.md; echo ---", "3:alpha\n---", set()),        # not a rule file
])
def test_evidenced_search_prints(cmd, text, found):
    assert mu.evidenced_search_prints(cmd, text, set(STEMS)) == found


@pytest.mark.parametrize("cmd,i,reaches", [
    (f"cat {R} > out.txt", 0, False),
    (f"cat {R} | wc -l", 0, False),
    (f"cat {R} | head -3", 0, True),
    (f"cat {R} | sed -n 1,3p", 0, True),
    (f"cat {R} | sed -i '' 's/a/b/' other.txt", 0, False),                # sed -i writes, prints nothing
    (f"cat {R} | grep -c a", 0, False),
    (f"cat {R} | grep a", 0, True),
    (f"cat {R} | tr '\\n' ' '", 0, True),                                  # real case: a loop joining lines
])
def test_reaches_result(cmd, i, reaches):
    assert mu._reaches_result(mu._pipelines(cmd)[0], i) is reaches


@pytest.mark.parametrize("text,bodies", [
    ("x $(a \\)) y", ["a \\)"]),                        # an escaped ) does not close the body
    ('x $(echo "a)b")', ['echo "a)b"']),               # nor a ) inside quotes
])
def test_substitutions_escapes_and_quotes(text, bodies):
    assert mu._substitutions(text) == bodies



# ---------------------------------------------------------------------------- final confirmation (2026-11-22)
# Codex F1 (P1): grep-shaped lines need a source search whose output reaches the result; in a
# compound command the line must match that search's pattern, as a compound grep print must.

FAKE = "memory/feedback_alpha_rule.md:3:not rule text"


@pytest.mark.parametrize("cmd", [
    f"grep -n alpha {R} >/dev/null; printf '{FAKE}\\n'",
    f"grep -n alpha {R} | wc -l; printf '{FAKE}\\n'",
    f"ls memory | xargs grep -n alpha > /tmp/o; printf '{FAKE}\\n'",
    f"x=$(grep -rn alpha memory/) > /dev/null; printf '{FAKE}\\n'",
    f"grep -rn zzz memory/; printf '{FAKE}\\n'",                    # the grep's pattern is not in the line
    f"grep -rn rule memory/ >/dev/null; printf '{FAKE}\\n'",        # its pattern matches, but its output is gone
    "grep -rn rule memory/ > /tmp/out.txt",                          # a whole command whose output is gone
])
def test_a_grep_line_needs_a_reaching_search_whose_pattern_it_matches(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", FAKE)])
    assert [e for e in res.events if e.channel == "grep_line"] == []


@pytest.mark.parametrize("cmd,out", [
    ("grep -rn rule memory/; echo ---", f"{FAKE}\n---"),                     # matches 'rule'
    ("grep -rn zzz memory/", FAKE),                                          # whole command: any line
    ("echo ---; ls memory | xargs grep -Hn rule", f"---\n{FAKE}"),
    ('echo "$(grep -rn rule memory/)"; echo ---', f"{FAKE}\n---"),
    ("grep -rn -e zzz -e rule memory/ | head -5; echo ---", f"{FAKE}\n---"),
    ('grep -rn "$k" memory/; echo ---', f"{FAKE}\n---"),                     # pattern it cannot read
    ("for f in memory/*.md; do echo \"$f: $(grep -m3 -E '^ *(promoted|rule):' $f | tr '\\n' ' ')\"; done; ls",
     "memory/feedback_alpha_rule.md:   rule: x"),                            # real case 2026-11-22
])
def test_a_grep_line_from_a_reaching_search_counts(tmp_path, cmd, out):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert ("feedback_alpha_rule", "grep_line") in _chans(res.events)



@pytest.mark.parametrize("cmd,out", [
    # real case: the pattern needs the line's leading spaces
    (f"python3 x.py; grep -E '^  (occurrences|rule)' {MP}feedback_alpha_rule.md {B}",
     "ok\nmemory/feedback_alpha_rule.md:  rule: x"),
    # real case: cut can cut the matched text off the line
    ("grep -rn PIPESTATUS memory/ | cut -c1-40; echo ---",
     "memory/feedback_alpha_rule.md:29:Related\n---"),
    ("grep -rn rule memory/ | tr a-z A-Z; echo ---", "memory/feedback_alpha_rule.md:29:RULE\n---"),
])
def test_a_grep_line_altered_after_the_search_or_needing_its_spaces_counts(tmp_path, cmd, out):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert ("feedback_alpha_rule", "grep_line") in _chans(res.events)


@pytest.mark.parametrize("cmd,out", [
    (f"grep -n PIPESTATUS {R} | cut -c1-6; echo ---", "29:Rel\n---"),
    (f"grep -n rule {R} | tr a-z A-Z; echo ---", "3:RULE\n---"),
])
def test_a_compound_grep_print_altered_after_the_search_counts(tmp_path, cmd, out):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert ("feedback_alpha_rule", "bash_print") in _chans(res.events)


def test_head_after_a_search_keeps_the_pattern_check(tmp_path):
    cmd = f"grep -n zzz {R} | head -3; echo ---"
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "3:other\n---")])
    assert ("feedback_alpha_rule", "bash_print") not in _chans(res.events)



@pytest.mark.parametrize("stage,words", [
    ("grep -E '^  (a|b)' f", ["grep", "-E", "'^  (a|b)'", "f"]),
    ('echo "a  b" c', ["echo", '"a  b"', "c"]),
    ("a\\ b  c", ["a\\ b", "c"]),
    ("  x   y  ", ["x", "y"]),
    ("", []),
    ("it's open", ["it's open"]),
])
def test_words(stage, words):
    assert mu._words(stage) == words



@pytest.mark.parametrize("cmd,out,counts", [
    (f"grep -n PIPESTATUS {R} | cut -c1-6; echo ---", "29:Rel\n---", True),          # 6 chars: may be cut
    (f"grep -n PIPESTATUS {R} | cut -c1-60; echo ---", "29:Rel\n---", False),        # short: not cut, no match
    (f"grep -n PIPESTATUS {R} | cut -c 1-6; echo ---", "29:Rel\n---", True),
    (f"grep -n PIPESTATUS {R} | cut -c-6; echo ---", "29:Rel\n---", True),
    (f"grep -n PIPESTATUS {R} | cut -d: -f1; echo ---", "29\n---", False),         # fields: no evidence
    (f"grep -n PIPESTATUS {R} | cut -c1-60 | cut -c1-6; echo ---", "29:Rel\n---", True),   # narrowest cut
])
def test_a_cut_after_a_compound_grep(tmp_path, cmd, out, counts):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert (("feedback_alpha_rule", "bash_print") in _chans(res.events)) is counts



@pytest.mark.parametrize("out", ["  rule: x\n---", "3:  rule: x\n---"])
def test_a_single_file_grep_pattern_keeps_the_lines_leading_spaces(tmp_path, out):
    """Real case: `grep -E '^  (occurrences|promoted)' rule.md` prints indented frontmatter lines."""
    cmd = f"grep -nE '^  (occurrences|rule)' {R}; echo ---"
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert ("feedback_alpha_rule", "bash_print") in _chans(res.events)



# ---------------------------------------------------------------------------- final confirmation, Grok F1 (2026-11-22)
# After tr, sed or cut the line test must still look for the pattern: accepting any line let a
# no-match grep count from another command's output.

FAKE46 = "memory/feedback_alpha_rule.md:3:not rule text"


@pytest.mark.parametrize("cmd,out", [
    (f"grep -n zzz {R} | tr a-z A-Z; echo ---", "---"),
    (f"grep -n zzz {R} | sed 's/a/b/'; echo ---", "---"),
    (f"grep -n zzz {R} | cut -d: -f1; echo ---", "---"),
    (f"grep -n zzz {R} | cut -c1-5; echo ---", "---"),                              # shorter than the cut
    (f"grep -n zzz {R} | cut -c1-3; echo ---", "-----"),                            # longer than the cut
    (f"grep -n rule {R} | sed 's/rule/x/'; echo ---", "3:x\n---"),                  # rewritten match: undercount
])
def test_a_compound_grep_print_through_tr_sed_or_cut_needs_its_pattern(tmp_path, cmd, out):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert ("feedback_alpha_rule", "bash_print") not in _chans(res.events)


@pytest.mark.parametrize("cmd", [
    f"grep -n zzz {R} | tr a-z A-Z; printf '{FAKE46}\\n'",
    f"grep -rn zzz memory/ | cut -c1-40; printf '{FAKE46}\\n'",                     # 46 chars after a 40 cut
    f"grep -rn zzz memory/ | sed 's/a/b/'; printf '{FAKE46}\\n'",
])
def test_a_grep_line_through_tr_sed_or_cut_needs_its_pattern(tmp_path, cmd):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", FAKE46)])
    assert [e for e in res.events if e.channel == "grep_line"] == []


def test_a_cut_line_of_exactly_the_cut_width_counts(tmp_path):
    line = "memory/feedback_alpha_rule.md:29:Related to PIPESTATUS"[:40]
    cmd = "grep -rn PIPESTATUS memory/ | cut -c1-40; echo ---"
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", line + "\n---")])
    assert ("feedback_alpha_rule", "grep_line") in _chans(res.events)



def test_only_cut_sets_a_line_width(tmp_path):
    """`head -c -6` looks like a cut range but cuts no line: a 6-character line is not evidence."""
    cmd = f"grep -n zzz {R} | head -c -6; echo abcdef"
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "abcdef")])
    assert ("feedback_alpha_rule", "bash_print") not in _chans(res.events)



# Codex confirm3 F1: only a case-converting tr makes the pattern check ignore case.
@pytest.mark.parametrize("cmd,out,counts", [
    (f"grep -n zzz {R} | tr x y; echo ZZZ", "ZZZ", False),
    (f"grep -n zzz {R} | tr -d x; echo ZZZ", "ZZZ", False),
    (f"grep -n zzz {R} | tr a-z A-Z; echo ---", "3:ZZZ\n---", True),
    (f"grep -n ZZZ {R} | tr A-Z a-z; echo ---", "3:zzz\n---", True),
    (f"grep -n zzz {R} | tr '[:lower:]' '[:upper:]'; echo ---", "3:ZZZ\n---", True),
])
def test_only_a_case_converting_tr_ignores_case(tmp_path, cmd, out, counts):
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", out)])
    assert (("feedback_alpha_rule", "bash_print") in _chans(res.events)) is counts


def test_a_grep_line_after_a_tr_that_keeps_case_stays_case_sensitive(tmp_path):
    cmd = "grep -rn zzz memory/ | tr x y; printf 'memory/feedback_alpha_rule.md:3:ZZZ\\n'"
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}),
                           _tool_result("t1", "memory/feedback_alpha_rule.md:3:ZZZ")])
    assert [e for e in res.events if e.channel == "grep_line"] == []
