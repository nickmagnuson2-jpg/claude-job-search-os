"""The concurrent-session commit guard: a commit may carry only paths this session named.

WHY THIS FILE EXISTS
--------------------
More than one Claude Code session works in this tree at once. A broad stage
(`git add -A`, `git add .`, `git commit -a`) or a plain `git commit` over an index
another session already staged into puts that session's uncommitted work in a commit
whose message does not describe it. On a PUBLIC repo that work has carried a PII leak.

The guard runs from the tracked pre-commit hook. It reads the committing session's own
transcript and refuses a commit whose staged set holds a path no tool call of that
session ever named.

Every test builds a throwaway git repo and a throwaway transcript directory. Nothing
here reads the real repo's index or a real transcript.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools import concurrent_commit_guard as g  # noqa: E402

GUARD = REPO_ROOT / "tools" / "concurrent_commit_guard.py"
HOOK = REPO_ROOT / "tools" / "hooks" / "pre-commit"
SID = "00000000-aaaa-bbbb-cccc-000000000001"


# --------------------------------------------------------------------------- fixtures

def _git(repo, *args, env=None, check=True):
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("GIT_") and k not in (g.SESSION_ENV, g.WAIVE_ENV)}
    e.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
              "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})
    e.update(env or {})
    r = subprocess.run(["git", *args], cwd=repo, env=e, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stderr
    return r


@pytest.fixture
def repo(tmp_path):
    """A repo with one commit and the tracked pre-commit hook installed in ITS hooks dir."""
    root = tmp_path / "repo"
    (root / "tools" / "hooks").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "tools" / "concurrent_commit_guard.py").write_bytes(GUARD.read_bytes())
    (root / "seed.txt").write_text("seed\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed", "--no-verify")
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(HOOK.read_bytes())
    hook.chmod(0o755)
    return root


@pytest.fixture
def cfg(tmp_path):
    """A stand-in for the Claude config dir, holding one session's transcripts."""
    d = tmp_path / "cfg"
    (d / "projects" / "-placeholder-project" / SID / "subagents").mkdir(parents=True)
    return d


def _record(name, inp):
    return json.dumps({"type": "assistant", "sessionId": SID, "message": {
        "role": "assistant",
        "content": [{"type": "tool_use", "name": name, "input": inp}]}})


def _main_transcript(cfg):
    return cfg / "projects" / "-placeholder-project" / f"{SID}.jsonl"


def _write_main(cfg, *records):
    _main_transcript(cfg).write_text("\n".join(records) + "\n")


def _env(cfg, **extra):
    return {"CLAUDE_CONFIG_DIR": str(cfg), g.SESSION_ENV: SID, **extra}


def _committed(repo):
    out = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    return sorted(x for x in out.splitlines() if x)


# ------------------------------------------------------- the failure the rule records

def test_broad_add_sweeping_another_sessions_file_is_refused(repo, cfg):
    """`git add -A` names no path, so the other session's file has no attribution."""
    (repo / "mine.py").write_text("x = 1\n")
    (repo / "theirs.py").write_text("y = 2\n")
    _write_main(cfg,
                _record("Write", {"file_path": str(repo / "mine.py"), "content": "x = 1\n"}),
                _record("Bash", {"command": "git add -A && git commit -m 'add mine'"}))
    _git(repo, "add", "-A")
    r = _git(repo, "commit", "-m", "add mine", env=_env(cfg), check=False)
    assert r.returncode != 0
    assert "theirs.py" in r.stderr
    assert "mine.py" not in r.stderr.replace("theirs.py", "")
    assert g.WAIVE_ENV in r.stderr, "the refusal must print the exact override"
    assert _committed(repo) == ["seed.txt", "tools/concurrent_commit_guard.py"]


def test_plain_commit_over_an_index_someone_else_staged_is_refused(repo, cfg):
    """Fire 6: a path-scoped `git add` followed by a plain `git commit` ships the
    whole index, including what another session staged before this one started."""
    (repo / "theirs.py").write_text("y = 2\n")
    _git(repo, "add", "theirs.py")                      # the other session
    (repo / "mine.py").write_text("x = 1\n")
    _write_main(cfg,
                _record("Edit", {"file_path": str(repo / "mine.py"),
                                 "old_string": "", "new_string": "x = 1\n"}),
                _record("Bash", {"command": "git add mine.py && git commit -m m"}))
    _git(repo, "add", "mine.py")
    r = _git(repo, "commit", "-m", "m", env=_env(cfg), check=False)
    assert r.returncode != 0
    assert "theirs.py" in r.stderr


# ------------------------------------------------------------------------- restraint

def test_explicit_pathspec_commit_passes_and_leaves_the_other_work_staged(repo, cfg):
    """The technique the rule prescribes. It must pass untouched, and the other
    session's staged file must not be in the commit."""
    (repo / "theirs.py").write_text("y = 2\n")
    _git(repo, "add", "theirs.py")
    (repo / "mine.py").write_text("x = 1\n")
    _git(repo, "add", "mine.py")
    _write_main(cfg, _record("Bash", {"command": "git commit -m 'mine' -- mine.py"}))
    r = _git(repo, "commit", "-m", "mine", "--", "mine.py", env=_env(cfg), check=False)
    assert r.returncode == 0, r.stderr
    assert _committed(repo) == ["mine.py"]
    assert "theirs.py" in _git(repo, "diff", "--cached", "--name-only").stdout


def test_ordinary_single_session_commit_of_edited_files_passes(repo, cfg):
    (repo / "docs").mkdir()
    (repo / "docs" / "note one.md").write_text("a\n")
    (repo / "mine.py").write_text("x = 1\n")
    _write_main(cfg,
                _record("Write", {"file_path": str(repo / "docs" / "note one.md"),
                                  "content": "a\n"}),
                _record("Write", {"file_path": "mine.py", "content": "x = 1\n"}))
    _git(repo, "add", "-A")
    r = _git(repo, "commit", "-m", "both mine", env=_env(cfg), check=False)
    assert r.returncode == 0, r.stderr
    assert _committed(repo) == ["docs/note one.md", "mine.py"]


def test_a_subagents_edit_counts_as_the_sessions_own(repo, cfg):
    """A session delegates edits; the parent commits them. Same session, same work."""
    (repo / "built.py").write_text("z = 3\n")
    _write_main(cfg, _record("Bash", {"command": "git add -A && git commit -m built"}))
    sub = _main_transcript(cfg).parent / SID / "subagents" / "agent-placeholder.jsonl"
    sub.write_text(_record("Write", {"file_path": str(repo / "built.py"),
                                     "content": "z = 3\n"}) + "\n")
    _git(repo, "add", "-A")
    r = _git(repo, "commit", "-m", "built", env=_env(cfg), check=False)
    assert r.returncode == 0, r.stderr


def test_commit_outside_any_claude_session_is_not_judged(repo, cfg):
    """No session id means a person at a terminal. There is no transcript to compare
    against, and the guard does not guess."""
    (repo / "by_hand.py").write_text("h = 1\n")
    _git(repo, "add", "-A")
    r = _git(repo, "commit", "-m", "by hand", env={"CLAUDE_CONFIG_DIR": str(cfg)},
             check=False)
    assert r.returncode == 0, r.stderr


# -------------------------------------------------------------------------- override

def test_waiver_with_a_reason_passes_and_is_recorded(repo, cfg):
    (repo / "theirs.py").write_text("y = 2\n")
    _write_main(cfg, _record("Bash", {"command": "git add -A"}))
    _git(repo, "add", "-A")
    r = _git(repo, "commit", "-m", "theirs, on purpose",
             env=_env(cfg, **{g.WAIVE_ENV: "peer asked me to land it"}), check=False)
    assert r.returncode == 0, r.stderr
    log = repo / ".git" / g.WAIVER_LOG
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert rows[-1]["reason"] == "peer asked me to land it"
    assert rows[-1]["paths"] == ["theirs.py"]


def test_blank_waiver_is_not_a_waiver(repo, cfg):
    (repo / "theirs.py").write_text("y = 2\n")
    _write_main(cfg, _record("Bash", {"command": "git add -A"}))
    _git(repo, "add", "-A")
    r = _git(repo, "commit", "-m", "m", env=_env(cfg, **{g.WAIVE_ENV: "   "}),
             check=False)
    assert r.returncode != 0


# ------------------------------------------------------------- attribution, unit level

def test_reading_a_file_does_not_make_it_yours(tmp_path, cfg):
    _write_main(cfg, _record("Read", {"file_path": str(tmp_path / "theirs.py")}),
                _record("Grep", {"pattern": "y", "path": "theirs.py"}))
    named = g.session_claims(g.transcript_files(cfg, SID))
    assert g.unattributed(["theirs.py"], named, tmp_path) == ["theirs.py"]


def test_a_longer_or_differently_rooted_path_does_not_attribute(tmp_path, cfg):
    """`tools/a.py.bak` and `other/tools/a.py` are not `tools/a.py`."""
    _write_main(cfg, _record("Bash", {"command": "cp tools/a.py.bak /x/other/tools/a.py"}),
                _record("Write", {"file_path": "/elsewhere/tools/a.py", "content": ""}))
    named = g.session_claims(g.transcript_files(cfg, SID))
    assert g.unattributed(["tools/a.py"], named, tmp_path) == ["tools/a.py"]


def test_absolute_and_relative_spellings_in_a_command_both_attribute(tmp_path, cfg):
    _write_main(cfg, _record("Bash", {"command": f"printf x > '{tmp_path}/a b.md'; "
                                                 "git rm -q tools/old.py"}))
    named = g.session_claims(g.transcript_files(cfg, SID))
    assert g.unattributed(["a b.md", "tools/old.py"], named, tmp_path) == []


def test_malformed_transcript_lines_are_skipped_not_fatal(tmp_path, cfg):
    _main_transcript(cfg).write_text(
        '{"type":"assistant","message":{"content":[{"type":"tool_use"\n'
        + _record("Write", {"file_path": "ok.py", "content": ""}) + "\n\n")
    named = g.session_claims(g.transcript_files(cfg, SID))
    assert g.unattributed(["ok.py"], named, tmp_path) == []


# ------------------------------------------------- which transcripts belong to a session

def test_only_the_named_sessions_transcripts_are_read(cfg):
    """Another session's transcript in the same project is that session's evidence."""
    other = "00000000-aaaa-bbbb-cccc-000000000002"
    proj = _main_transcript(cfg).parent
    _write_main(cfg, _record("Write", {"file_path": "mine.py", "content": ""}))
    sub = proj / SID / "subagents" / "agent-placeholder.jsonl"
    sub.write_text("")
    (proj / f"{other}.jsonl").write_text("")
    (proj / "notes.txt").write_text("")
    assert g.transcript_files(cfg, SID) == [_main_transcript(cfg), sub]


def test_a_session_with_only_agent_transcripts_or_only_a_main_one(cfg):
    proj = _main_transcript(cfg).parent
    sub = proj / SID / "subagents" / "agent-placeholder.jsonl"
    sub.write_text("")
    assert g.transcript_files(cfg, SID) == [sub]
    sub.unlink()
    (proj / SID / "subagents").rmdir()
    (proj / SID).rmdir()
    _write_main(cfg)
    assert g.transcript_files(cfg, SID) == [_main_transcript(cfg)]


def test_no_session_id_or_no_projects_directory_yields_no_transcripts(tmp_path, cfg):
    """An empty id must not match every transcript in the project directory."""
    _write_main(cfg, _record("Write", {"file_path": "mine.py", "content": ""}))
    assert g.transcript_files(cfg, "") == []
    assert g.transcript_files(tmp_path / "no-such-config", SID) == []


# --------------------------------------------------- what counts as naming a path

def test_an_unreadable_transcript_does_not_hide_the_readable_ones(tmp_path, cfg):
    _write_main(cfg, _record("Write", {"file_path": "ok.py", "content": ""}))
    gone = tmp_path / "deleted-since-listing.jsonl"
    named = g.session_claims([gone, _main_transcript(cfg), gone])
    assert g.unattributed(["ok.py", "theirs.py"], named, tmp_path) == ["theirs.py"]


def test_records_of_an_unexpected_shape_are_skipped_not_fatal(tmp_path, cfg):
    """Each line mentions "tool_use" and none of them is a well-formed tool call."""
    lines = [
        json.dumps(["tool_use"]),
        json.dumps({"tool_use": 1, "message": None}),
        json.dumps({"tool_use": 1, "message": {"content": None}}),
        json.dumps({"tool_use": 1, "message": {"content": 7}}),
        json.dumps({"message": {"content": ["tool_use", 7, None]}}),
        json.dumps({"message": {"content": [{"type": "tool_use", "name": "Write"}]}}),
        json.dumps({"message": {"content": [
            {"type": "tool_use", "name": "Write", "input": "theirs.py"}]}}),
        _record("Write", {"file_path": "ok.py", "content": ""}),
    ]
    _main_transcript(cfg).write_text("\n".join(lines) + "\n")
    named = g.session_claims(g.transcript_files(cfg, SID))
    assert g.unattributed(["ok.py", "theirs.py"], named, tmp_path) == ["theirs.py"]


def test_a_tool_result_that_quotes_a_write_call_does_not_attribute(tmp_path, cfg):
    """Only a tool_use block is a call this session made. A block of another type
    carrying the same fields (a quoted call inside a tool result) names nothing."""
    quoted = json.dumps({"tool_use": 1, "message": {"content": [
        {"type": "tool_result", "name": "Write", "input": {"file_path": "theirs.py"}},
        {"type": "text", "name": "Bash", "input": {"command": "git add theirs.py"}}]}})
    _main_transcript(cfg).write_text(quoted + "\n")
    named = g.session_claims(g.transcript_files(cfg, SID))
    assert g.unattributed(["theirs.py"], named, tmp_path) == ["theirs.py"]


def test_only_bash_commands_attribute_not_other_tools_with_a_command_field(tmp_path, cfg):
    _write_main(cfg,
                _record("mcp__placeholder__run", {"command": "cat theirs.py"}),
                _record("Bash", {"command": "git add mine.py"}))
    named = g.session_claims(g.transcript_files(cfg, SID))
    assert g.unattributed(["mine.py", "theirs.py"], named, tmp_path) == ["theirs.py"]


def test_empty_and_non_string_paths_and_commands_are_ignored(tmp_path, cfg):
    _write_main(cfg,
                _record("Write", {"file_path": "", "content": ""}),
                _record("Write", {"file_path": 7, "content": ""}),
                _record("Edit", {"file_path": None}),
                _record("Bash", {"command": None}),
                _record("Bash", {"command": ""}),
                _record("Bash", {"command": 7}),
                _record("NotebookEdit", {"notebook_path": "nb.ipynb"}),
                _record("Bash", {"command": "git add mine.py"}))
    named = g.session_claims(g.transcript_files(cfg, SID))
    assert named.edited == {"nb.ipynb"}
    assert named.commands == ["git add mine.py"]
    assert g.unattributed(["mine.py", "nb.ipynb", "theirs.py"], named, tmp_path) \
        == ["theirs.py"]


def test_an_edit_in_a_sibling_checkout_does_not_attribute_the_same_name_here(tmp_path):
    """`/x/rep0/a.py` and `/x/repo2/a.py` are not this repo's `a.py`. The first has a
    root of the same length, the second has this root as a prefix of its own."""
    root = Path(os.path.realpath(tmp_path)) / "repo"
    for other in ("rep0", "repo2"):
        claims = g.Claims(edited={str(root.parent / other / "a.py")})
        assert g.unattributed(["a.py"], claims, root) == ["a.py"], other
    assert g.unattributed(["a.py"], g.Claims(edited={str(root / "a.py")}), root) == []


# ------------------------------------------------ no transcript: the narrower guard

def test_no_transcript_and_a_live_peer_session_refuses(tmp_path):
    v = g.decide(["a.py"], session_id=SID, transcripts=[], peers=[4242], root=tmp_path)
    assert v.blocked and v.paths == ["a.py"]


def test_no_transcript_and_no_peer_session_passes(tmp_path):
    v = g.decide(["a.py"], session_id=SID, transcripts=[], peers=[], root=tmp_path)
    assert not v.blocked


def test_peer_detection_excludes_this_sessions_own_process_chain(tmp_path):
    """Rows are (pid, ppid, command name, cwd). 10 is this session; 11 is its shell."""
    rows = [(10, 1, "claude", str(tmp_path)),
            (11, 10, "zsh", str(tmp_path)),
            (12, 11, "claude", str(tmp_path)),          # spawned by this session
            (20, 1, "claude", str(tmp_path / "sub")),   # a peer, in a subdirectory
            (30, 1, "claude", "/somewhere/else"),       # a session in another project
            (40, 1, "vim", str(tmp_path))]
    assert g.peer_sessions(rows, own_pid=11, root=tmp_path) == [20]


def test_peer_detection_excludes_everything_this_session_spawned(tmp_path):
    """14 is a `claude -p` started from a different shell of this same session. It is
    under the session process (10) and not under the committing shell (11)."""
    here = str(tmp_path)
    rows = [(10, 1, "claude", here),
            (11, 10, "zsh", here),          # the shell running the commit
            (13, 10, "zsh", here),          # another shell of the same session
            (14, 13, "claude", here),
            (20, 1, "/usr/local/bin/claude", here)]
    assert g.peer_sessions(rows, own_pid=11, root=tmp_path) == [20]


def test_peer_detection_excludes_an_outer_session_that_launched_this_one(tmp_path):
    here = str(tmp_path)
    rows = [(5, 1, "claude", here),         # outer session
            (6, 5, "zsh", here),
            (10, 6, "claude", here),        # this session, started by the outer one
            (11, 10, "zsh", here),
            (20, 1, "claude", here)]
    assert g.peer_sessions(rows, own_pid=11, root=tmp_path) == [20]


def test_peer_detection_ends_on_a_process_table_that_loops(tmp_path):
    """macOS lists pid 0 as its own parent, so every ancestry walk reaches a loop."""
    here = str(tmp_path)
    rows = [(0, 0, "kernel_task", ""),
            (1, 0, "launchd", ""),
            (10, 1, "claude", here),
            (11, 10, "zsh", here),
            (20, 1, "claude", here),
            (21, 20, "claude", here)]       # spawned by the peer: also not ours
    assert g.peer_sessions(rows, own_pid=11, root=tmp_path) == [20, 21]


def test_a_commit_from_outside_any_claude_process_tree_sees_every_session_as_a_peer(
        tmp_path):
    rows = [(0, 0, "kernel_task", ""), (1, 0, "launchd", ""),
            (11, 1, "zsh", str(tmp_path)), (20, 1, "claude.exe", str(tmp_path))]
    assert g.peer_sessions(rows, own_pid=11, root=tmp_path) == [20]


class _FakeProcesses:
    """Stands in for subprocess.run inside the guard: canned `ps` and `lsof` output."""

    def __init__(self, ps_out, cwds, ps_error=None, lsof_error_for=()):
        self.ps_out, self.cwds = ps_out, cwds
        self.ps_error, self.lsof_error_for = ps_error, set(lsof_error_for)
        self.lsof_pids = []

    def __call__(self, cmd, **_kw):
        if cmd[0] == "ps":
            if self.ps_error:
                raise self.ps_error
            return subprocess.CompletedProcess(cmd, 0, stdout=self.ps_out, stderr="")
        assert cmd[0] == "lsof", cmd
        pid = int(cmd[cmd.index("-p") + 1])
        self.lsof_pids.append(pid)
        if pid in self.lsof_error_for:
            raise subprocess.TimeoutExpired(cmd, 10)
        out = f"p{pid}\nfcwd\nn{self.cwds[pid]}\n" if pid in self.cwds else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def test_process_table_is_parsed_and_only_claude_rows_get_a_working_directory(
        monkeypatch):
    ps_out = ("    1     0 /sbin/launchd\n"
              "   10     1 claude\n"
              "   11    10 /bin/zsh\n"
              "   20     1 /Applications/Acme Tools/claude\n"
              "   30     1 claude\n"
              "   31     1 claude\n"
              "garbage line\n"
              "  PID  PPID COMM\n"
              "   40 notanumber vim\n"
              "   41     1\n"
              "\n")
    fake = _FakeProcesses(ps_out, {10: "/work/acme", 11: "/work/acme",
                                   20: "/work/acme/sub dir", 1: "/"},
                          lsof_error_for=[31])
    monkeypatch.setattr(g.subprocess, "run", fake)
    assert g.live_process_rows() == [
        (1, 0, "/sbin/launchd", ""),
        (10, 1, "claude", "/work/acme"),
        (11, 10, "/bin/zsh", ""),
        (20, 1, "/Applications/Acme Tools/claude", "/work/acme/sub dir"),
        (30, 1, "claude", ""),              # lsof printed nothing for it
        (31, 1, "claude", "")]              # lsof timed out
    assert fake.lsof_pids == [10, 20, 30, 31]


def test_a_failing_ps_yields_an_empty_process_table_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(g.subprocess, "run",
                        _FakeProcesses("", {}, ps_error=OSError("no ps")))
    rows = g.live_process_rows()
    assert rows == []
    assert g.peer_sessions(rows, own_pid=11, root=tmp_path) == []


# ------------------------------------------------------------- the verdict, unit level

def test_nothing_staged_is_never_blocked_even_with_a_live_peer(tmp_path):
    v = g.decide([], session_id=SID, transcripts=[], peers=[4242], root=tmp_path)
    assert v.blocked is False and v.paths == [] and v.mode == "nothing-staged"


def test_no_session_id_is_never_blocked_even_with_a_live_peer(tmp_path):
    v = g.decide(["a.py"], session_id="", transcripts=[], peers=[4242], root=tmp_path)
    assert v.blocked is False and v.paths == [] and v.mode == "not-a-session"


# ------------------------------------------- the command itself: exit code and message

@pytest.fixture
def staged_repo(tmp_path, monkeypatch):
    """A repo with theirs.py and mine.py staged, and an environment with no session,
    no waiver and no live processes. Tests add what they need."""
    for k in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
    monkeypatch.delenv(g.SESSION_ENV, raising=False)
    monkeypatch.delenv(g.WAIVE_ENV, raising=False)
    monkeypatch.setattr(g, "live_process_rows", lambda: [])
    root = tmp_path / "staged"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "theirs.py").write_text("y = 2\n")
    (root / "mine.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)        # the guard must not depend on the caller's cwd
    return root


def _session(monkeypatch, cfg, *records):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv(g.SESSION_ENV, SID)
    if records:
        _write_main(cfg, *records)


def _peer_rows(root, pid=4242):
    return [(os.getpid(), 1, "python3", ""), (pid, 1, "claude", str(root))]


def test_main_passes_when_every_staged_path_was_named(staged_repo, cfg, monkeypatch,
                                                      capsys):
    _session(monkeypatch, cfg, _record("Bash", {"command": "git add mine.py theirs.py"}))
    assert g.main(["--repo-root", str(staged_repo)]) == 0
    assert capsys.readouterr().err == ""


def test_main_refusal_lists_the_paths_and_the_live_peer(staged_repo, cfg, monkeypatch,
                                                        capsys):
    _session(monkeypatch, cfg, _record("Bash", {"command": "git add -A"}))
    monkeypatch.setattr(g, "live_process_rows", lambda: _peer_rows(staged_repo))
    assert g.main(["--repo-root", str(staged_repo)]) == 1
    err = capsys.readouterr().err
    assert "BLOCKED" in err
    assert "2 staged path(s) were never named by this session" in err
    assert "    mine.py\n    theirs.py\n" in err
    assert "1 other live claude process(es)" in err and "(pid 4242)" in err
    assert "No other live claude process" not in err
    assert "transcript was not found" not in err


def test_main_refusal_says_so_when_no_peer_is_live(staged_repo, cfg, monkeypatch, capsys):
    _session(monkeypatch, cfg, _record("Bash", {"command": "git add mine.py"}))
    assert g.main(["--repo-root", str(staged_repo)]) == 1
    err = capsys.readouterr().err
    assert "1 staged path(s) were never named by this session" in err
    assert "    theirs.py\n" in err and "    mine.py" not in err
    assert "No other live claude process has this repo as working directory" in err
    assert "other live claude process(es)" not in err


def test_main_without_a_transcript_refuses_on_a_live_peer_and_names_it(
        staged_repo, cfg, monkeypatch, capsys):
    """The message must name the process that caused the refusal, so the process table
    is the one the verdict was reached on. The second read here returns nothing."""
    _session(monkeypatch, cfg)
    reads = iter([_peer_rows(staged_repo), []])
    monkeypatch.setattr(g, "live_process_rows", lambda: next(reads, []))
    assert g.main(["--repo-root", str(staged_repo)]) == 1
    err = capsys.readouterr().err
    assert "transcript was not found" in err
    assert "never named by this session" not in err
    assert "(pid 4242)" in err
    assert "    mine.py\n    theirs.py\n" in err


def test_main_without_a_transcript_and_without_a_peer_passes(staged_repo, cfg,
                                                             monkeypatch, capsys):
    _session(monkeypatch, cfg)
    assert g.main(["--repo-root", str(staged_repo)]) == 0
    assert capsys.readouterr().err == ""


def test_main_waiver_passes_reports_and_records_under_the_repo(staged_repo, cfg,
                                                               monkeypatch, capsys):
    """Run from another directory: the record belongs in this repo's git dir."""
    _session(monkeypatch, cfg, _record("Bash", {"command": "git add mine.py"}))
    monkeypatch.setenv(g.WAIVE_ENV, "  Pat Zorp asked me to land it  ")
    assert g.main(["--repo-root", str(staged_repo)]) == 0
    err = capsys.readouterr().err
    log = staged_repo.resolve() / ".git" / g.WAIVER_LOG
    assert "WAIVED: Pat Zorp asked me to land it\n" in err
    assert f"1 path(s) recorded in {log}" in err
    assert "BLOCKED" not in err
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["reason"] == "Pat Zorp asked me to land it"
    assert rows[0]["paths"] == ["theirs.py"]
    assert rows[0]["mode"] == "attribution"
    assert rows[0]["session"] == SID
    assert rows[0]["ts"]


def test_waiver_in_a_linked_worktree_is_recorded_where_git_says(staged_repo):
    """In a linked worktree git answers with an absolute path outside the worktree."""
    _git(staged_repo, "commit", "-q", "-m", "seed", "--no-verify")
    wt = staged_repo.parent / "linked"
    _git(staged_repo, "worktree", "add", "-q", str(wt))
    log = g.record_waiver(wt, ["a.py"], "why", "attribution")
    assert log.is_absolute() and log.is_file()
    assert log.name == g.WAIVER_LOG and wt.resolve() not in log.resolve().parents
    assert json.loads(log.read_text())["paths"] == ["a.py"]


def test_main_refuses_when_git_cannot_be_read(tmp_path, cfg, monkeypatch, capsys,
                                              staged_repo):
    """A guard that cannot run must refuse and say why, not pass in silence."""
    not_a_repo = tmp_path / "plain-directory"
    not_a_repo.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    _session(monkeypatch, cfg)
    with pytest.raises(RuntimeError, match="git diff --cached"):
        g.staged_paths(not_a_repo)
    assert g.main(["--repo-root", str(not_a_repo)]) == 1
    err = capsys.readouterr().err
    assert "BLOCKED: concurrent-session commit guard could not run" in err
    assert "git diff --cached" in err
    assert f"{g.WAIVE_ENV}='<why>' git commit" in err


def test_main_error_with_a_waiver_passes_and_says_it_errored(tmp_path, cfg, monkeypatch,
                                                             capsys, staged_repo):
    not_a_repo = tmp_path / "plain-directory"
    not_a_repo.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv(g.WAIVE_ENV, "index is being repaired")
    assert g.main(["--repo-root", str(not_a_repo)]) == 0
    err = capsys.readouterr().err
    assert "errored" in err and "git diff --cached" in err
    assert "waived: index is being repaired" in err
    assert "BLOCKED" not in err


# ------------------------------------------------------------------------ the wiring

def test_tracked_pre_commit_hook_calls_the_guard():
    body = HOOK.read_text(encoding="utf-8")
    assert "concurrent_commit_guard.py" in body
    assert os.access(HOOK, os.X_OK), "git ignores a hook that is not executable"


def test_installer_check_reports_the_pre_commit_hook(tmp_path):
    """install.sh --check must cover pre-commit. Run against a throwaway repo: it
    reports MISSING before install and ok after, and never touches the real hooks dir."""
    root = tmp_path / "r"
    (root / "tools").mkdir(parents=True)
    _git(root, "init", "-q")
    subprocess.run(["cp", "-R", str(REPO_ROOT / "tools" / "hooks"), str(root / "tools")],
                   check=True)
    inst = str(root / "tools" / "hooks" / "install.sh")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    before = subprocess.run(["bash", inst, "--check"], cwd=root, env=env,
                            capture_output=True, text=True)
    assert before.returncode == 1 and "MISSING: pre-commit" in before.stdout
    subprocess.run(["bash", inst], cwd=root, env=env, check=True, capture_output=True)
    after = subprocess.run(["bash", inst, "--check"], cwd=root, env=env,
                           capture_output=True, text=True)
    assert after.returncode == 0 and "ok: pre-commit" in after.stdout


# ------------------------------------------- the commit's own command line, 2026-10-13
# The transcript is not written promptly. A session's file on disk held nothing from
# its last several hours of tool calls, and `git commit -- <paths>` was refused twice
# for paths the session had created minutes earlier. The older pathspec test above
# wrote the commit command into the transcript first, which is what hid this.

def test_explicit_pathspec_passes_when_the_transcript_never_heard_of_the_file(repo, cfg):
    (repo / "theirs.py").write_text("y = 2\n")
    _git(repo, "add", "theirs.py")
    (repo / "mine.py").write_text("x = 1\n")
    _git(repo, "add", "mine.py")
    _write_main(cfg, _record("Bash", {"command": "ls"}))        # a stale transcript
    r = _git(repo, "commit", "-m", "mine", "--", "mine.py", env=_env(cfg), check=False)
    assert r.returncode == 0, r.stderr
    assert _committed(repo) == ["mine.py"]


def test_explicit_pathspec_passes_with_no_transcript_at_all(repo, cfg):
    (repo / "mine.py").write_text("x = 1\n")
    _git(repo, "add", "mine.py")
    r = _git(repo, "commit", "-m", "mine", "--", "mine.py", env=_env(cfg), check=False)
    assert r.returncode == 0, r.stderr


def test_a_broad_commit_is_still_refused_when_the_transcript_is_stale(repo, cfg):
    """`git commit -m` names no path on its command line, so nothing changes for it."""
    (repo / "mine.py").write_text("x = 1\n")
    (repo / "theirs.py").write_text("y = 2\n")
    _write_main(cfg, _record("Bash", {"command": "ls"}))
    _git(repo, "add", "-A")
    r = _git(repo, "commit", "-m", "both", env=_env(cfg), check=False)
    assert r.returncode != 0
    assert "mine.py" in r.stderr and "theirs.py" in r.stderr
    assert "not after `--` on this commit's own command line" in r.stderr


def test_a_pathspec_names_only_what_it_names(tmp_path, cfg):
    v = g.decide(["mine.py", "theirs.py"], session_id=SID, transcripts=[], peers=[7],
                 root=tmp_path, commit_command="mine.py")
    assert (v.blocked, v.mode) == (True, "no-transcript")
    _write_main(cfg, _record("Bash", {"command": "ls"}))
    v = g.decide(["mine.py", "theirs.py"], session_id=SID,
                 transcripts=[_main_transcript(cfg)], peers=[], root=tmp_path,
                 commit_command="mine.py")
    assert (v.blocked, v.paths, v.mode) == (True, ["theirs.py"], "attribution")
    v = g.decide(["mine.py"], session_id=SID, transcripts=[], peers=[7], root=tmp_path,
                 commit_command="mine.py")
    assert (v.blocked, v.mode) == (False, "attribution")
    v = g.decide(["mine.py"], session_id=SID, transcripts=[], peers=[7], root=tmp_path)
    assert v.blocked is True, "no command line and no transcript: the peer check decides"


class _Ps:
    """A process table for invoking_commit_command: {pid: (ppid, args)}."""
    def __init__(self, table):
        self.table, self.asked = table, []

    def __call__(self, argv, **kw):
        pid = int(argv[-1])
        self.asked.append(pid)
        row = self.table.get(pid)
        out = f"{row[0]:>5} {row[1]}\n" if row else ""
        return subprocess.CompletedProcess(argv, 0 if row else 1, stdout=out, stderr="")


def test_the_commit_command_is_found_above_the_hook_script():
    ps = _Ps({30: (20, "python3 tools/concurrent_commit_guard.py --repo-root /r"),
              20: (10, "/bin/bash .git/hooks/pre-commit"),
              10: (5, "/usr/bin/git commit -q -F msg.txt -- tools/a.py docs/b.md"),
              5: (1, "zsh")})
    assert g.invoking_commit_command(30, run=ps) == (
        "/usr/bin/git commit -q -F msg.txt -- tools/a.py docs/b.md")
    assert ps.asked == [30, 20, 10], "it stops at the commit and reads no further up"


def test_a_git_process_that_is_not_a_commit_is_not_the_commit():
    ps = _Ps({30: (20, "bash hook"), 20: (10, "git rebase main"),
              10: (5, "git commit -m x -- a.py"), 5: (1, "zsh")})
    assert g.invoking_commit_command(30, run=ps) == "git commit -m x -- a.py"
    ps = _Ps({30: (20, "bash hook"), 20: (1, "mygit commit -- a.py"), 1: (0, "launchd")})
    assert g.invoking_commit_command(30, run=ps) == ""


def test_no_commit_in_the_chain_or_an_unreadable_table_yields_nothing():
    assert g.invoking_commit_command(30, run=_Ps({30: (1, "bash"), 1: (0, "launchd")})) == ""
    assert g.invoking_commit_command(30, run=_Ps({})) == ""
    loop = _Ps({30: (30, "bash")})
    assert g.invoking_commit_command(30, run=loop) == "" and len(loop.asked) == 8

    def boom(argv, **kw):
        raise OSError("no ps")
    assert g.invoking_commit_command(30, run=boom) == ""

    def garbage(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, stdout="not-a-pid args\n", stderr="")
    assert g.invoking_commit_command(30, run=garbage) == ""


# --- Cross-model review 307: a file named in the MESSAGE is not a pathspec ---

def test_a_commit_message_that_names_a_file_does_not_attribute_it(repo, cfg):
    """`git add -A; git commit -m "update mine.py and theirs.py"` passed for one
    afternoon: the whole command line was searched, message included."""
    (repo / "mine.py").write_text("x = 1\n")
    (repo / "theirs.py").write_text("y = 2\n")
    _write_main(cfg, _record("Bash", {"command": "ls"}))
    _git(repo, "add", "-A")
    r = _git(repo, "commit", "-m", "update mine.py and theirs.py", env=_env(cfg), check=False)
    assert r.returncode != 0
    assert "mine.py" in r.stderr and "theirs.py" in r.stderr


def test_a_broad_pathspec_does_not_attribute_a_file_the_message_names(repo, cfg):
    """`git commit -m "... theirs.py" -- .` has a pathspec index, and the only word
    after `--` is the dot."""
    (repo / "seed.txt").write_text("changed by another session\n")
    _write_main(cfg, _record("Bash", {"command": "ls"}))
    r = _git(repo, "commit", "-m", "touch seed.txt", "--", ".", env=_env(cfg), check=False)
    assert r.returncode != 0 and "seed.txt" in r.stderr


@pytest.mark.parametrize("command,index,expected", [
    ("git commit -m msg -- a.py docs/b.md", ".git/next-index-123.lock", "a.py docs/b.md"),
    ("/usr/bin/git commit -q -F m.txt -- a.py", "/r/.git/next-index-9.lock", "a.py"),
    # the message is before the separator and is dropped, even when it holds one
    ("git commit -m update a.py -- theirs.py -- mine.py", ".git/next-index-1.lock", "mine.py"),
    # not a pathspec commit: the plain index, the -a / -i lock, or nothing exported
    ("git commit -m msg -- a.py", ".git/index", ""),
    ("git commit -a -m msg -- a.py", ".git/index.lock", ""),
    ("git commit -m msg -- a.py", "", ""),
    ("git commit -m msg -- a.py", ".git/my-next-index-1.lock", ""),
    # a pathspec index and no separator: nothing can be told apart from the message
    ("git commit -m update a.py a.py", ".git/next-index-1.lock", ""),
    ("git commit -m msg --", ".git/next-index-1.lock", ""),
    ("", ".git/next-index-1.lock", ""),
])
def test_commit_pathspecs(command, index, expected):
    assert g.commit_pathspecs(command, index) == expected
