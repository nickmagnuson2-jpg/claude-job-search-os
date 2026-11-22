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
    ("grep --label x memory/a.md output/", ["output/"]),
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
])
def test_env_options_do_not_hide_the_real_command(tmp_path, cmd):
    """Codex r3 F3 (P1)."""
    res = _scan(tmp_path, [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "3:promoted: no")])
    assert _chans(res.events) == [("feedback_alpha_rule", "bash_print")]


@pytest.mark.parametrize("seg", ["", "   ", "cat memory/a.md", "egrep x memory/a.md"])
def test_search_file_args_is_none_for_an_empty_or_non_search_segment(seg):
    assert mu._search_file_args(seg) is None
