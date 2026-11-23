"""Tests for tools/run_metrics.py: per-session timings, Nick-minutes, time-to-land and
failure catchers rebuilt from Claude Code session transcripts for one overnight run.

Spec: output/overnight-112126/DECISIONS.md row C7 and brief output/overnight-112226/brief.md S6.
Every fixture here is synthetic: invented session names (proj-a1, proj-b2, proj-org) in a
temporary folder and a throwaway git repo. No test reads the live transcript folder.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import run_metrics as rm  # noqa: E402

T0 = "2026-01-10T10:00:00.000Z"


def ts(minute: float) -> str:
    """ISO timestamp `minute` minutes after T0 (10:00Z)."""
    base = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc).timestamp()
    return datetime.fromtimestamp(base + minute * 60, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def human(minute, text):
    return {"type": "user", "timestamp": ts(minute), "origin": {"kind": "human"},
            "message": {"role": "user", "content": text}}


def peer(minute, from_name, body):
    content = ("Another Claude session sent a message:\n"
               f'<cross-session-message from="uds:/tmp/x.sock" from-name="{from_name}" from-mode="prompting">\n'
               f"{body}\n</cross-session-message>")
    return {"type": "user", "timestamp": ts(minute), "origin": {"kind": "peer"}, "isMeta": True,
            "message": {"role": "user", "content": content}}


def assistant(minute, text="ok", tool=None):
    content = [{"type": "text", "text": text}]
    if tool:
        content.append(tool)
    return {"type": "assistant", "timestamp": ts(minute), "message": {"role": "assistant", "content": content}}


def send(minute, to, message):
    return assistant(minute, "sending", {"type": "tool_use", "id": f"tu{minute}", "name": "SendMessage",
                                         "input": {"to": to, "message": message}})


def tool_error(minute, text):
    return {"type": "user", "timestamp": ts(minute),
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "x", "is_error": True, "content": text}]}}


def write_session(folder: Path, sid: str, rows, branch=None):
    p = folder / f"{sid}.jsonl"
    with p.open("w") as f:
        for r in rows:
            r = dict(r, sessionId=sid)
            if branch:
                r["gitBranch"] = branch
            f.write(json.dumps(r) + "\n")
    return p


WINDOW = (ts(-60), ts(600))


# ---------------------------------------------------------------- timings

def test_active_minutes_excludes_gaps_over_the_idle_cap():
    stamps = [rm.parse_ts(ts(m)) for m in (0, 5, 9, 60, 62)]
    # 0->5->9 = 9 min, 9->60 is idle (>10), 60->62 = 2 min
    assert rm.active_minutes(stamps, idle_cap_min=10) == pytest.approx(11.0)


def test_active_minutes_counts_a_gap_exactly_at_the_cap():
    stamps = [rm.parse_ts(ts(m)) for m in (0, 10)]
    assert rm.active_minutes(stamps, idle_cap_min=10) == pytest.approx(10.0)


def test_session_first_and_last_are_clipped_to_the_window(tmp_path):
    write_session(tmp_path, "s1", [assistant(-120), human(0, "go"), assistant(1), assistant(700)])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.first == rm.parse_ts(ts(0))
    assert s.last == rm.parse_ts(ts(1))


def test_session_with_no_line_in_the_window_is_none(tmp_path):
    write_session(tmp_path, "s1", [assistant(-300), assistant(-200)])
    assert rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW)) is None


# ---------------------------------------------------------------- Nick-minutes

def test_nick_minutes_counts_only_human_prompts_capped_per_prompt(tmp_path):
    write_session(tmp_path, "s1", [
        assistant(0), human(3, "yes"),                     # 3 min after output
        assistant(4), human(40, "what next"),              # 36 min gap, capped at 10
        assistant(41), peer(42, "proj-org", "do X"),       # peer, not Nick
    ])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.nick_prompts == 2
    assert s.nick_minutes(cap_min=10) == pytest.approx(13.0)


def test_nick_prompt_with_no_prior_output_counts_zero_minutes(tmp_path):
    write_session(tmp_path, "s1", [human(0, "start"), assistant(1)])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.nick_prompts == 1
    assert s.nick_minutes(cap_min=10) == 0.0


def test_meta_and_tool_results_are_not_nick(tmp_path):
    write_session(tmp_path, "s1", [assistant(0), tool_error(1, "boom"),
                                   {"type": "user", "timestamp": ts(2), "isMeta": True,
                                    "message": {"role": "user", "content": "<system-reminder>x</system-reminder>"}}])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.nick_prompts == 0


# ---------------------------------------------------------------- names

def test_names_resolve_by_matching_sent_and_received_bodies(tmp_path):
    write_session(tmp_path, "aaa", [assistant(0), send(1, "proj-org", "proj-a1 check-in: started S9")])
    write_session(tmp_path, "ooo", [assistant(0), peer(1.1, "proj-a1", "proj-a1 check-in: started S9")])
    w = list(map(rm.parse_ts, WINDOW))
    sessions = [rm.load_session(tmp_path / f"{s}.jsonl", *w) for s in ("aaa", "ooo")]
    names = rm.resolve_names(sessions)
    assert names["aaa"] == "proj-a1"
    assert names["ooo"] is None  # ooo never sent a message anyone received


def test_unresolved_name_falls_back_to_session_prefix(tmp_path):
    write_session(tmp_path, "abcdef123", [assistant(0)])
    s = rm.load_session(tmp_path / "abcdef123.jsonl", *map(rm.parse_ts, WINDOW))
    assert rm.display_name(s, {"abcdef123": None}) == "sid:abcdef12"


# ---------------------------------------------------------------- failures and catchers

def test_hook_block_is_caught_by_the_named_hook(tmp_path):
    write_session(tmp_path, "s1", [assistant(0), tool_error(
        1, "PreToolUse:Bash hook error: [PYTHONIOENCODING=utf-8 python3 $CLAUDE_PROJECT_DIR/tools/check_alpha.py]: BLOCKED: no")])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    f = rm.failures(s, names={"s1": "proj-a1"}, org_names={"proj-org"})
    assert [(x["catcher"], x["catcher_class"]) for x in f] == [("hook:check_alpha.py", "hook")]


def test_plain_tool_error_is_not_a_caught_failure(tmp_path):
    write_session(tmp_path, "s1", [assistant(0), tool_error(1, "File not found")])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert rm.failures(s, names={}, org_names=set()) == []


def test_nick_correction_is_caught_by_nick(tmp_path):
    write_session(tmp_path, "s1", [assistant(0), human(1, "that's wrong, the board is stale")])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    f = rm.failures(s, names={}, org_names=set())
    assert [x["catcher_class"] for x in f] == ["nick"]


def test_nick_prompt_without_a_correction_word_is_not_a_failure(tmp_path):
    write_session(tmp_path, "s1", [assistant(0), human(1, "yes go ahead")])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert rm.failures(s, names={}, org_names=set()) == []


def test_peer_correction_is_classed_org_or_peer_by_sender(tmp_path):
    write_session(tmp_path, "s1", [assistant(0),
                                   peer(1, "proj-org", "correction: your count was wrong"),
                                   peer(2, "proj-b2", "you overwrote my file")])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    f = rm.failures(s, names={}, org_names={"proj-org"})
    assert [(x["catcher"], x["catcher_class"]) for x in f] == [("proj-org", "org"), ("proj-b2", "peer")]


def test_self_correction_in_a_sent_message_is_caught_by_self(tmp_path):
    write_session(tmp_path, "s1", [assistant(0), send(1, "proj-org", "Correction to my last message: it was 12, not 13")])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    f = rm.failures(s, names={"s1": "proj-a1"}, org_names=set())
    assert [(x["catcher"], x["catcher_class"]) for x in f] == [("proj-a1", "self")]


def test_failure_excerpt_is_bounded(tmp_path):
    write_session(tmp_path, "s1", [assistant(0), human(1, "wrong " + "x" * 2000)])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    f = rm.failures(s, names={}, org_names=set())
    assert len(f[0]["excerpt"]) <= rm.EXCERPT_CHARS


# ---------------------------------------------------------------- time-to-land

def _git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def _commit(repo, msg, when):
    import os
    env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    (repo / f"{msg}.txt").write_text(msg)
    _git(repo, "add", ".", env=env)
    _git(repo, "commit", "-q", "-m", msg, env=env)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _commit(r, "base", "2026-01-10T09:00:00Z")
    return r


def test_unlanded_branch_reports_first_commit_and_no_land(repo):
    _git(repo, "checkout", "-q", "-b", "feat-x")
    _commit(repo, "c1", "2026-01-10T10:05:00Z")
    _commit(repo, "c2", "2026-01-10T10:20:00Z")
    b = rm.branch_timing(repo, "feat-x", main_ref="main")
    assert b["first_commit"] == "2026-01-10T10:05:00Z"
    assert b["landed"] is None
    assert b["time_to_land_min"] is None


def test_merged_branch_reports_time_from_first_commit_to_merge(repo):
    _git(repo, "checkout", "-q", "-b", "feat-y")
    _commit(repo, "c1", "2026-01-10T10:05:00Z")
    _git(repo, "checkout", "-q", "main")
    import os
    env = dict(os.environ, GIT_AUTHOR_DATE="2026-01-10T12:05:00Z", GIT_COMMITTER_DATE="2026-01-10T12:05:00Z",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feat-y", env=env)
    b = rm.branch_timing(repo, "feat-y", main_ref="main")
    assert b["first_commit"] == "2026-01-10T10:05:00Z"
    assert b["landed"] == "2026-01-10T12:05:00Z"
    assert b["time_to_land_min"] == pytest.approx(120.0)


def test_fast_forwarded_branch_lands_at_its_own_tip(repo):
    _git(repo, "checkout", "-q", "-b", "feat-z")
    _commit(repo, "c1", "2026-01-10T10:05:00Z")
    _commit(repo, "c2", "2026-01-10T10:35:00Z")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "feat-z")
    b = rm.branch_timing(repo, "feat-z", main_ref="main")
    assert b["landed"] == "2026-01-10T10:35:00Z"
    assert b["time_to_land_min"] == pytest.approx(30.0)


def test_missing_branch_is_reported_not_raised(repo):
    b = rm.branch_timing(repo, "no-such-branch", main_ref="main")
    assert b["error"] == "branch not found"


# ---------------------------------------------------------------- report and output guard

def test_report_carries_every_run_field(tmp_path, repo):
    tdir = tmp_path / "t"
    tdir.mkdir()
    write_session(tdir, "aaa", [assistant(0), send(1, "proj-org", "proj-a1 check-in"), human(2, "wrong")], branch="feat-q")
    write_session(tdir, "ooo", [assistant(0), peer(1.1, "proj-a1", "proj-a1 check-in"),
                                send(3, "proj-a1", "go")])
    write_session(tdir, "zzz", [assistant(3.5), peer(3.2, "proj-org", "go")])
    rep = rm.build_report(tdir, repo, *WINDOW, org_names={"proj-org"}, run_id="r-test", main_ref="main")
    assert set(rm.RUN_FIELDS) <= set(rep)
    assert rep["run_id"] == "r-test"
    assert rep["sessions_count"] == 3
    names = {s["name"] for s in rep["sessions"]}
    assert "proj-a1" in names and "proj-org" in names
    assert all(set(rm.SESSION_FIELDS) <= set(s) for s in rep["sessions"])
    assert rep["org_messages_sent"] == 1
    assert rep["candidates_by_catcher_class"] == {"nick": 1}
    assert [b["branch"] for b in rep["branches"]] == ["feat-q"]


def test_main_branch_is_not_reported_as_a_branch(tmp_path, repo):
    tdir = tmp_path / "t"
    tdir.mkdir()
    write_session(tdir, "aaa", [assistant(0)], branch="main")
    rep = rm.build_report(tdir, repo, *WINDOW, org_names=set(), run_id="r", main_ref="main")
    assert rep["branches"] == []


def test_out_path_must_not_be_tracked(tmp_path, repo):
    (repo / "tracked.json").write_text("{}")
    _git(repo, "add", "tracked.json")
    with pytest.raises(SystemExit):
        rm.check_out_path(repo / "tracked.json", repo)


def test_out_path_inside_repo_must_be_gitignored(tmp_path, repo):
    (repo / ".gitignore").write_text("output/\n")
    rm.check_out_path(repo / "output" / "m.json", repo)  # ignored: fine
    with pytest.raises(SystemExit):
        rm.check_out_path(repo / "loose.json", repo)  # not ignored


def test_out_path_outside_repo_is_allowed(tmp_path, repo):
    rm.check_out_path(tmp_path / "elsewhere.json", repo)


def test_cli_writes_json_report(tmp_path, repo):
    tdir = tmp_path / "t"
    tdir.mkdir()
    write_session(tdir, "aaa", [assistant(0)])
    out = tmp_path / "out.json"
    rc = rm.main(["--transcripts", str(tdir), "--repo", str(repo), "--start", WINDOW[0], "--end", WINDOW[1],
                  "--run-id", "r1", "--main-ref", "main", "--out", str(out)])
    assert rc == 0
    assert json.loads(out.read_text())["run_id"] == "r1"


# ---------------------------------------------------------------- busy-session receipt and worktrees

def queued(minute, from_name, body):
    content = (f'<cross-session-message from="uds:/tmp/x.sock" from-name="{from_name}" from-mode="prompting">\n'
               f"{body}\n</cross-session-message>")
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": ts(minute), "content": content}


def test_a_queued_peer_message_counts_as_received_but_not_as_activity(tmp_path):
    write_session(tmp_path, "ooo", [assistant(0), queued(5, "proj-a1", "proj-a1 ready")])
    s = rm.load_session(tmp_path / "ooo.jsonl", *map(rm.parse_ts, WINDOW))
    assert [(n, b.strip()) for _, n, b in s.received] == [("proj-a1", "proj-a1 ready")]
    assert s.last == rm.parse_ts(ts(0))


def test_the_same_peer_message_seen_twice_is_received_once(tmp_path):
    write_session(tmp_path, "ooo", [assistant(0), queued(5, "proj-a1", "hello"), peer(6, "proj-a1", "hello")])
    s = rm.load_session(tmp_path / "ooo.jsonl", *map(rm.parse_ts, WINDOW))
    assert len(s.received) == 1


def test_names_resolve_through_a_queued_message(tmp_path):
    write_session(tmp_path, "aaa", [assistant(0), send(1, "proj-org", "proj-a1 lessons ready")])
    write_session(tmp_path, "ooo", [assistant(0), queued(1.1, "proj-a1", "proj-a1 lessons ready")])
    w = list(map(rm.parse_ts, WINDOW))
    names = rm.resolve_names([rm.load_session(tmp_path / f"{s}.jsonl", *w) for s in ("aaa", "ooo")])
    assert names["aaa"] == "proj-a1"


def bash(minute, command):
    return assistant(minute, "run", {"type": "tool_use", "id": f"b{minute}", "name": "Bash",
                                     "input": {"command": command}})


def test_worktree_dirs_named_in_tool_inputs_are_collected(tmp_path):
    write_session(tmp_path, "s1", [bash(0, "cd /Users/x/Library/Caches/claude-worktrees/feat-w1 && git status"),
                                   bash(1, "git -C /r/.claude/worktrees/agent-abc log -1")])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.worktrees == {"feat-w1", "agent-abc"}


def test_worktree_dirs_map_to_branches_through_git_worktree_list(tmp_path, repo):
    wt = tmp_path / "claude-worktrees" / "feat-w2"
    _git(repo, "worktree", "add", "-q", "-b", "feat-w2-branch", str(wt))
    m = rm.worktree_branches(repo)
    assert m["feat-w2"] == "feat-w2-branch"
    assert m[repo.name] == "main"


def test_report_attributes_a_branch_reached_through_a_worktree(tmp_path, repo):
    wt = tmp_path / "claude-worktrees" / "feat-w3"
    _git(repo, "worktree", "add", "-q", "-b", "feat-w3", str(wt))
    tdir = tmp_path / "t"
    tdir.mkdir()
    write_session(tdir, "aaa", [bash(0, f"cd {wt} && pytest")], branch="main")
    rep = rm.build_report(tdir, repo, *WINDOW, org_names=set(), run_id="r", main_ref="main")
    assert rep["sessions"][0]["branches"] == ["feat-w3"]
    assert [b["branch"] for b in rep["branches"]] == ["feat-w3"]


# ---------------------------------------------------------------- landing from the pushed ref's reflog

def _env(when):
    import os
    return dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when, GIT_AUTHOR_NAME="t",
                GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")


def test_landing_time_comes_from_the_land_ref_reflog_not_the_merge_commit(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "-m", "update by push", "refs/remotes/origin/main", base, env=_env("2026-01-10T09:30:00Z"))
    _git(repo, "checkout", "-q", "-b", "feat-p")
    _commit(repo, "c1", "2026-01-10T10:00:00Z")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feat-p", env=_env("2026-01-10T10:30:00Z"))
    _git(repo, "update-ref", "-m", "update by push", "refs/remotes/origin/main", "main", env=_env("2026-01-10T13:00:00Z"))
    b = rm.branch_timing(repo, "feat-p", main_ref="main", land_ref="refs/remotes/origin/main")
    assert b["landed"] == "2026-01-10T13:00:00Z"
    assert b["land_source"] == "reflog:refs/remotes/origin/main"
    assert b["time_to_land_min"] == pytest.approx(180.0)


def test_landing_falls_back_to_commit_time_without_a_land_ref(repo):
    _git(repo, "checkout", "-q", "-b", "feat-q")
    _commit(repo, "c1", "2026-01-10T10:00:00Z")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feat-q", env=_env("2026-01-10T10:30:00Z"))
    b = rm.branch_timing(repo, "feat-q", main_ref="main", land_ref="refs/remotes/origin/main")
    assert b["landed"] == "2026-01-10T10:30:00Z"
    assert b["land_source"] == "commit-time"


def test_branch_commits_start_at_the_branch_creation_point(repo):
    _commit(repo, "m1", "2026-01-10T09:10:00Z")
    _git(repo, "checkout", "-q", "-b", "feat-r")
    _commit(repo, "r1", "2026-01-10T10:00:00Z")
    _commit(repo, "r2", "2026-01-10T10:10:00Z")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "feat-r")
    b = rm.branch_timing(repo, "feat-r", main_ref="main")
    assert b["first_commit"] == "2026-01-10T10:00:00Z"   # not m1 or base
    assert b["commits"] == 2


def test_branch_with_no_commits_of_its_own_reports_zero_commits(repo):
    _git(repo, "branch", "feat-empty")
    b = rm.branch_timing(repo, "feat-empty", main_ref="main")
    assert b["commits"] == 0
    assert b["first_commit"] is None
    assert b["time_to_land_min"] is None


# ---------------------------------------------------------------- Nick-catch shapes and run-level dedupe

@pytest.mark.parametrize("text", [
    "why arent you going down the backlog ranked by value",
    "are you making sure every session is checking in",
    "make sure that all the sessions are on the board",
    "did you check the old run?",
    "again, the goal is speed",
])
def test_nick_question_and_directive_shapes_are_catches(tmp_path, text):
    write_session(tmp_path, "s1", [assistant(0), human(1, text)])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert [x["catcher_class"] for x in rm.failures(s, names={}, org_names=set())] == ["nick"]


@pytest.mark.parametrize("text", ["keep going", "I am going to go to dinner", "yes do both builds", "what's the status"])
def test_plain_nick_instructions_are_not_catches(tmp_path, text):
    write_session(tmp_path, "s1", [assistant(0), human(1, text)])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert rm.failures(s, names={}, org_names=set()) == []


def test_a_self_correction_is_counted_once_not_again_at_the_receiver(tmp_path, repo):
    tdir = tmp_path / "t"
    tdir.mkdir()
    body = "proj-a1 correction to my last message: the tip is abc"
    write_session(tdir, "aaa", [assistant(0), send(1, "proj-org", body)])
    write_session(tdir, "ooo", [assistant(0), peer(1.1, "proj-a1", body)])
    rep = rm.build_report(tdir, repo, *WINDOW, org_names={"proj-org"}, run_id="r", main_ref="main")
    assert [(f["catcher"], f["catcher_class"]) for f in rep["failure_candidates"]] == [("proj-a1", "self")]


def test_one_peer_correction_received_by_two_sessions_counts_once(tmp_path, repo):
    tdir = tmp_path / "t"
    tdir.mkdir()
    body = "you overwrote the shared file"
    write_session(tdir, "b1", [assistant(0), peer(1, "proj-org", body)])
    write_session(tdir, "b2", [assistant(0), peer(1.2, "proj-org", body)])
    rep = rm.build_report(tdir, repo, *WINDOW, org_names={"proj-org"}, run_id="r", main_ref="main")
    assert len(rep["failure_candidates"]) == 1
    assert rep["candidates_by_catcher_class"] == {"org": 1}


# ---------------------------------------------------------------- Codex r1: window boundary, hook dedupe, attachments

def test_a_prompt_just_inside_the_window_counts_the_gap_from_output_just_before_it(tmp_path):
    # Codex r1 F1: assistant 1 min before the window, Nick 1 min after it -> 2 minutes, not 0.
    start, end = rm.parse_ts(ts(0)), rm.parse_ts(ts(600))
    write_session(tmp_path, "s1", [assistant(-1), human(1, "yes")])
    s = rm.load_session(tmp_path / "s1.jsonl", start, end)
    assert s.nick_minutes(cap_min=10) == pytest.approx(2.0)
    assert s.first == rm.parse_ts(ts(1))  # the pre-window line is still not activity


def test_two_different_blocks_from_one_hook_at_one_timestamp_are_both_kept():
    # Codex r1 F2
    e1 = {"ts": "2026-01-10T10:00:00Z", "session": "a", "catcher": "hook:check_a.py", "catcher_class": "hook",
          "kind": "hook_block", "excerpt": "check_a.py BLOCKED first"}
    e2 = dict(e1, excerpt="check_a.py BLOCKED second")
    assert len(rm.dedupe_failures([e1, e2])) == 2


def test_the_same_hook_block_seen_twice_is_kept_twice_when_it_fired_twice():
    e1 = {"ts": "2026-01-10T10:00:00Z", "session": "a", "catcher": "hook:check_a.py", "catcher_class": "hook",
          "kind": "hook_block", "excerpt": "check_a.py BLOCKED same"}
    e2 = dict(e1, ts="2026-01-10T10:05:00Z")
    assert len(rm.dedupe_failures([e1, e2])) == 2


def test_a_peer_message_delivered_as_an_attachment_is_received_and_not_activity(tmp_path):
    body = ('<cross-session-message from="uds:/tmp/x.sock" from-name="proj-a1" from-mode="prompting">\n'
            "proj-a1 via attachment\n</cross-session-message>")
    write_session(tmp_path, "ooo", [assistant(0), {"type": "attachment", "timestamp": ts(5),
                                                   "attachment": {"type": "queued_command", "prompt": body}}])
    s = rm.load_session(tmp_path / "ooo.jsonl", *map(rm.parse_ts, WINDOW))
    assert [n for _, n, _ in s.received] == ["proj-a1"]
    assert s.last == rm.parse_ts(ts(0))


# ---------------------------------------------------------------- mutation r1 survivors (7e6abaa2): record shapes

def test_iso_of_none_is_none():
    assert rm.iso(None) is None


def test_a_human_prompt_given_as_text_blocks_is_read(tmp_path):
    write_session(tmp_path, "s1", [assistant(0), {"type": "user", "timestamp": ts(1), "origin": {"kind": "human"},
                                                  "message": {"role": "user", "content": [
                                                      {"type": "text", "text": "that's wrong"}, "stray-string"]}}])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.human[0][1] == "that's wrong"


def test_a_peer_message_inside_a_tool_result_list_is_received(tmp_path):
    inner = ('<cross-session-message from="uds:/tmp/x.sock" from-name="proj-a1" from-mode="prompting">\n'
             "nested\n</cross-session-message>")
    write_session(tmp_path, "s1", [assistant(0), {"type": "user", "timestamp": ts(1), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": [{"type": "text", "text": inner}]}]}}])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert [n for _, n, _ in s.received] == ["proj-a1"]


def test_a_peer_message_nested_in_an_attachment_list_is_received(tmp_path):
    body = ('<cross-session-message from="uds:/tmp/x.sock" from-name="proj-a1" from-mode="prompting">\n'
            "deep\n</cross-session-message>")
    write_session(tmp_path, "s1", [assistant(0), {"type": "attachment", "timestamp": ts(2),
                                                  "attachment": {"items": [{"prompt": body}]}}])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert [n for _, n, _ in s.received] == ["proj-a1"]


def test_unusable_lines_are_skipped(tmp_path):
    p = tmp_path / "s1.jsonl"
    rows = [assistant(0),
            dict(assistant(1), isSidechain=True),
            {"type": "assistant", "message": {"content": []}},            # no timestamp
            dict(assistant(2), timestamp="not-a-time"),
            {"type": "system", "timestamp": ts(3)},
            assistant(700)]                                               # after the window
    with p.open("w") as f:
        f.write("{broken json\n")
        for r in rows:
            f.write(json.dumps(dict(r, sessionId="s1")) + "\n")
    s = rm.load_session(p, *map(rm.parse_ts, WINDOW))
    assert s.stamps == [rm.parse_ts(ts(0))]


def test_a_user_line_before_the_window_is_not_look_behind(tmp_path):
    start, end = rm.parse_ts(ts(0)), rm.parse_ts(ts(600))
    write_session(tmp_path, "s1", [human(-1, "earlier"), human(1, "yes")])
    s = rm.load_session(tmp_path / "s1.jsonl", start, end)
    assert s.nick_minutes(cap_min=10) == 0.0


def test_a_cross_session_text_in_a_system_line_is_not_received(tmp_path):
    body = ('<cross-session-message from="uds:/tmp/x.sock" from-name="proj-a1" from-mode="prompting">\n'
            "x\n</cross-session-message>")
    write_session(tmp_path, "s1", [assistant(0), {"type": "system", "timestamp": ts(1), "content": body}])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.received == []


def test_a_worktree_path_in_assistant_prose_is_not_collected(tmp_path):
    write_session(tmp_path, "s1", [assistant(0, "see ~/Library/Caches/claude-worktrees/feat-prose for details")])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.worktrees == set()


def test_only_sendmessage_with_a_body_counts_as_sent(tmp_path):
    other = {"type": "tool_use", "id": "o", "name": "Notify", "input": {"to": "x", "message": "hello"}}
    subscribe = {"type": "tool_use", "id": "q", "name": "SendMessage", "input": {"to": "x", "notify_when_idle": True}}
    write_session(tmp_path, "s1", [assistant(0, "a", other), assistant(1, "b", subscribe)])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.sent == []


def test_a_successful_tool_result_naming_a_hook_is_not_a_block(tmp_path):
    write_session(tmp_path, "s1", [assistant(0), {"type": "user", "timestamp": ts(1), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "ran tools/check_alpha.py: BLOCKED count 0"}]}}])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.hook_errors == []


def test_a_name_resolves_from_a_later_sent_message_when_an_earlier_one_was_never_received(tmp_path):
    write_session(tmp_path, "aaa", [assistant(0), send(1, "x", "never delivered"), send(2, "proj-org", "proj-a1 here")])
    write_session(tmp_path, "ooo", [assistant(0), peer(2.1, "proj-a1", "proj-a1 here")])
    w = list(map(rm.parse_ts, WINDOW))
    names = rm.resolve_names([rm.load_session(tmp_path / f"{s}.jsonl", *w) for s in ("aaa", "ooo")])
    assert names["aaa"] == "proj-a1"


# ---------------------------------------------------------------- mutation r1 survivors: git

def test_a_missing_main_ref_is_reported(repo):
    _git(repo, "branch", "feat-m")
    b = rm.branch_timing(repo, "feat-m", main_ref="no-such-main")
    assert b["error"] == "main ref not found"
    assert b["first_commit"] is None


def test_a_branch_reset_onto_a_later_main_counts_only_its_own_commits(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feat-s")
    _commit(repo, "side", "2026-01-10T09:20:00Z")            # creation-era commit, later abandoned
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "m1", "2026-01-10T09:30:00Z")
    _git(repo, "checkout", "-q", "feat-s")
    _git(repo, "reset", "-q", "--hard", "main")
    _commit(repo, "c1", "2026-01-10T10:00:00Z")
    _git(repo, "checkout", "-q", "main")
    b = rm.branch_timing(repo, "feat-s", main_ref="main")
    # base..tip would hold m1 (main's) and c1; only c1 was made on the branch and is still in it.
    assert base
    assert b["commits"] == 1 and b["first_commit"] == "2026-01-10T10:00:00Z"


def test_an_abandoned_creation_point_off_the_tip_history_is_not_counted(repo):
    _git(repo, "checkout", "-q", "--orphan", "island")
    _commit(repo, "island", "2026-01-10T09:15:00Z")
    _git(repo, "branch", "feat-t")                             # feat-t created on the island commit
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "m1", "2026-01-10T09:30:00Z")
    _git(repo, "branch", "-f", "feat-t", "main")               # moved onto main's line
    _git(repo, "checkout", "-q", "feat-t")
    _commit(repo, "c1", "2026-01-10T10:00:00Z")
    _git(repo, "checkout", "-q", "main")
    b = rm.branch_timing(repo, "feat-t", main_ref="main")
    assert b["commits"] == 1
    assert b["first_commit"] == "2026-01-10T10:00:00Z"


def test_no_land_ref_falls_back_to_commit_time(repo):
    _git(repo, "checkout", "-q", "-b", "feat-u")
    _commit(repo, "c1", "2026-01-10T10:00:00Z")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feat-u", env=_env("2026-01-10T10:40:00Z"))
    b = rm.branch_timing(repo, "feat-u", main_ref="main", land_ref=None)
    assert b["landed"] == "2026-01-10T10:40:00Z" and b["land_source"] == "commit-time"


# ---------------------------------------------------------------- mutation r1 survivors: report and CLI

def test_report_skips_a_transcript_with_no_line_in_the_window(tmp_path, repo):
    tdir = tmp_path / "t"
    tdir.mkdir()
    write_session(tdir, "aaa", [assistant(0)])
    write_session(tdir, "old", [assistant(-500)])
    rep = rm.build_report(tdir, repo, *WINDOW, org_names=set(), run_id="r", main_ref="main")
    assert rep["sessions_count"] == 1


def test_org_messages_count_only_org_sessions(tmp_path, repo):
    tdir = tmp_path / "t"
    tdir.mkdir()
    write_session(tdir, "org", [assistant(0), send(1, "proj-a1", "org one"), send(2, "proj-a1", "org two")])
    write_session(tdir, "aaa", [assistant(0), peer(1.1, "proj-org", "org one"), send(3, "proj-org", "a1 says hi")])
    write_session(tdir, "o2", [assistant(0), peer(3.1, "proj-a1", "a1 says hi")])
    rep = rm.build_report(tdir, repo, *WINDOW, org_names={"proj-org"}, run_id="r", main_ref="main")
    assert rep["org_messages_sent"] == 2


def test_cli_without_out_prints_the_report(tmp_path, repo, capsys):
    tdir = tmp_path / "t"
    tdir.mkdir()
    write_session(tdir, "aaa", [assistant(0)])
    rc = rm.main(["--transcripts", str(tdir), "--repo", str(repo), "--start", WINDOW[0], "--end", WINDOW[1],
                  "--run-id", "r2", "--main-ref", "main"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == "r2"


def test_cli_creates_the_out_directory(tmp_path, repo):
    tdir = tmp_path / "t"
    tdir.mkdir()
    write_session(tdir, "aaa", [assistant(0)])
    out = tmp_path / "new" / "dir" / "m.json"
    assert rm.main(["--transcripts", str(tdir), "--repo", str(repo), "--start", WINDOW[0], "--end", WINDOW[1],
                    "--run-id", "r3", "--main-ref", "main", "--out", str(out)]) == 0
    assert out.exists()


def test_cli_refuses_a_tracked_out_path_before_writing(tmp_path, repo):
    tdir = tmp_path / "t"
    tdir.mkdir()
    write_session(tdir, "aaa", [assistant(0)])
    (repo / "tracked.json").write_text("{}")
    _git(repo, "add", "tracked.json")
    with pytest.raises(SystemExit):
        rm.main(["--transcripts", str(tdir), "--repo", str(repo), "--start", WINDOW[0], "--end", WINDOW[1],
                 "--run-id", "r4", "--main-ref", "main", "--out", str(repo / "tracked.json")])
    assert (repo / "tracked.json").read_text() == "{}"


def test_reset_entries_are_counted_so_a_late_first_commit_is_visible(repo):
    _git(repo, "checkout", "-q", "-b", "feat-v")
    _commit(repo, "elsewhere", "2026-01-10T09:40:00Z")
    elsewhere = _git(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "-q", "--hard", "main")
    _git(repo, "reset", "-q", "--hard", elsewhere)
    _commit(repo, "c1", "2026-01-10T10:00:00Z")
    _git(repo, "checkout", "-q", "main")
    b = rm.branch_timing(repo, "feat-v", main_ref="main")
    assert b["reset_entries"] == 2


def test_a_branch_without_a_reflog_counts_main_to_tip(repo):
    _git(repo, "checkout", "-q", "-b", "feat-w")
    _commit(repo, "w1", "2026-01-10T10:00:00Z")
    _commit(repo, "w2", "2026-01-10T10:10:00Z")
    _git(repo, "checkout", "-q", "main")
    (repo / ".git" / "logs" / "refs" / "heads" / "feat-w").unlink()
    b = rm.branch_timing(repo, "feat-w", main_ref="main")
    assert b["commits"] == 2 and b["first_commit"] == "2026-01-10T10:00:00Z"
    assert b["reset_entries"] == 0


def test_a_rebased_branch_counts_every_rebased_commit(repo):
    # Codex r2 F1: one 'rebase (finish)' reflog entry stands for all the rebased commits.
    _git(repo, "checkout", "-q", "-b", "feat-rb")
    _commit(repo, "a", "2026-01-10T10:00:00Z")
    _commit(repo, "b", "2026-01-10T10:10:00Z")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "m1", "2026-01-10T10:20:00Z")
    _git(repo, "checkout", "-q", "feat-rb")
    _git(repo, "rebase", "-q", "main", env=_env("2026-01-10T10:30:00Z"))
    _git(repo, "checkout", "-q", "main")
    b = rm.branch_timing(repo, "feat-rb", main_ref="main")
    assert b["commits"] == 2
    assert b["first_commit"] == "2026-01-10T10:00:00Z"   # author time survives the rebase


# ---------------------------------------------------------------- mutation r2 survivors (a8d68ba0)

XS = ('<cross-session-message from="uds:/tmp/x.sock" from-name="proj-a1" from-mode="prompting">\n'
      "quoted\n</cross-session-message>")


def test_an_assistant_line_quoting_a_peer_message_is_not_a_receipt(tmp_path):
    write_session(tmp_path, "s1", [assistant(0, "earlier I got: " + XS)])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.received == []


def test_a_peer_message_nick_pasted_into_a_prompt_is_not_a_receipt(tmp_path):
    write_session(tmp_path, "s1", [assistant(0), human(1, "look at this: " + XS)])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.received == []
    assert s.nick_prompts == 1


def test_a_non_text_block_adds_nothing_to_the_prompt_text(tmp_path):
    write_session(tmp_path, "s1", [assistant(0), {"type": "user", "timestamp": ts(1), "origin": {"kind": "human"},
                                                  "message": {"role": "user", "content": [
                                                      {"type": "text", "text": "see image"},
                                                      {"type": "image", "source": {}}]}}])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.human[0][1] == "see image"


# ---------------------------------------------------------------- PII scrub: the transcript folder is derived, not typed

def test_the_transcript_folder_is_claude_codes_own_name_for_the_repo(tmp_path, monkeypatch):
    # Grok r2 F1: Claude Code turns only "/" into "-" and keeps dots and spaces.
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = Path("/srv/some user/my.repo")
    assert rm.transcripts_dir_for(repo) == tmp_path / "home" / ".claude" / "projects" / "-srv-some user-my.repo"


def test_the_transcript_folder_follows_claude_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert rm.transcripts_dir_for(Path("/srv/r")) == tmp_path / "cfg" / "projects" / "-srv-r"


def test_a_missing_transcript_folder_is_an_error_not_zero_sessions(tmp_path, repo, capsys):
    rc = rm.main(["--transcripts", str(tmp_path / "nope"), "--repo", str(repo), "--start", WINDOW[0],
                  "--end", WINDOW[1], "--run-id", "r", "--main-ref", "main"])
    assert rc == 2
    assert "no transcript folder" in capsys.readouterr().err


def test_no_home_directory_name_is_typed_into_the_tool():
    src = (Path(__file__).resolve().parents[2] / "tools" / "run_metrics.py").read_text()
    assert not re.search(r'"-Users-|/Users/', src)


def test_cli_defaults_to_the_derived_folder_for_the_repo(tmp_path, monkeypatch, repo):
    seen = {}
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    rm.transcripts_dir_for(repo).mkdir(parents=True)
    monkeypatch.setattr(rm, "build_report", lambda transcripts, *a, **k: seen.setdefault("t", transcripts) and {"run_id": "r"})
    rm.main(["--repo", str(repo), "--start", WINDOW[0], "--end", WINDOW[1], "--run-id", "r", "--main-ref", "main"])
    assert seen["t"] == rm.transcripts_dir_for(repo)
