#!/usr/bin/env python3
"""Tests for the write-target half of tools/check_save_claims.py (redesign, 2026-11-16).

WHY. A debug-log audit found 0 true positives in 21 real blocks (2026-08-31..11-09). Most
false blocks were claims about files that WERE written, but where the prose path did not
resolve on disk: a sibling repo reached by `cd`, a path elided with `...`, a tokenizer cut,
or a write made in an earlier turn of the session. Nick's ruling (Q2, option a): check the
session's ACTUAL Write/Edit/Bash write targets, not only the prose path.

Contract under test:
  - A claimed path that is not on disk PASSES when any successful write in the session
    targeted it: same path, the claim is a path-suffix of the target, or the claim is a
    fragment (8+ chars) of the written file's name.
  - A failed write (tool_result is_error) is not a write.
  - The true case still BLOCKS: "Saved to X", X not on disk, and nothing in the session
    wrote X, even when other files were written.
  - A "- *File:* x.md" proposal label ends the claim window (narrow fix, ruled same day).

Every fixture is a generic replica of a real false block; no real names (public repo).
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "tools" / "check_save_claims.py"

spec = importlib.util.spec_from_file_location("csc_writes", HOOK)
csc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csc)


# ---------- transcript builders ----------

def _assistant_tool(tid, name, inp):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _result(tid, is_error=False, text="ok"):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": is_error, "content": text}]}}


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _say(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": text}]}}


def _transcript(tmp_path, records, cwd=None):
    tpath = tmp_path / "t.jsonl"
    with tpath.open("w", encoding="utf-8") as fh:
        for r in records:
            if cwd:
                r = dict(r, cwd=str(cwd))
            fh.write(json.dumps(r) + "\n")
    return tpath


def _run(tpath, root):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"transcript_path": str(tpath)}),
        capture_output=True, text=True, timeout=30,
        env={"PYTHONIOENCODING": "utf-8", "CLAUDE_PROJECT_DIR": str(root),
             "HOME": str(root.parent), "PATH": "/usr/bin:/bin"},
    )


def _repo(tmp_path):
    root = tmp_path / "work" / "main-repo"
    root.mkdir(parents=True)
    return root


# ---------- replays of real false blocks: must be CLEAN ----------

def test_a_bash_write_into_a_sibling_repo_clears_the_claim(tmp_path):
    """Real shape: `cd` into a sibling repo, heredoc write, then 'Teardown
    written to output/<client>/<file>.md'. The prose path resolves under neither root."""
    root = _repo(tmp_path)
    peer = root.parent / "peer-repo"
    (peer / "output" / "client-a").mkdir(parents=True)
    cmd = ("cd ../peer-repo && cat > output/client-a/teardown.md <<'EOF'\n"
           "# Teardown\nEOF")
    (peer / "output" / "client-a" / "teardown.md").write_text("# Teardown\n")
    t = _transcript(tmp_path, [
        _user("tear it down"),
        _assistant_tool("t1", "Bash", {"command": cmd}), _result("t1"),
        _say("Teardown written to `output/client-a/teardown.md` (+ PDF)."),
    ], cwd=root)
    r = _run(t, root)
    assert r.returncode == 0, r.stderr


def test_a_write_in_an_earlier_turn_clears_the_claim(tmp_path):
    """Real shape (2026-11-09): a file written earlier in the session, outside this repo,
    and cited in a later turn by its repo-relative tail."""
    root = _repo(tmp_path)
    elsewhere = tmp_path / "other-checkout" / "output" / "ws-1107"
    elsewhere.mkdir(parents=True)
    target = elsewhere / "ws-1107-pr-body.md"
    t = _transcript(tmp_path, [
        _user("draft the PR body"),
        _assistant_tool("w1", "Write", {"file_path": str(target), "content": "x"}), _result("w1"),
        _say("Drafted."),
        _user("where did it go?"),
        _say("A peer session copied the draft to `output/ws-1107/ws-1107-pr-body.md`."),
    ], cwd=root)
    assert _run(t, root).returncode == 0


def test_an_elided_path_matches_the_full_write_target(tmp_path):
    """Real shape (2026-09-04): the reply shortened the path with '...'."""
    root = _repo(tmp_path)
    target = tmp_path / "scratch-a1" / "scratchpad" / "prompt.txt"
    t = _transcript(tmp_path, [
        _user("save the prompt"),
        _assistant_tool("w1", "Write", {"file_path": str(target), "content": "x"}), _result("w1"),
        _say("Saved to `.../scratchpad/prompt.txt`."),
    ], cwd=root)
    assert _run(t, root).returncode == 0


def test_an_edit_counts_as_a_write(tmp_path):
    root = _repo(tmp_path)
    target = tmp_path / "peer-repo" / "memory" / "README.md"
    t = _transcript(tmp_path, [
        _user("document the command"),
        _assistant_tool("e1", "Edit", {"file_path": str(target), "old_string": "a",
                                       "new_string": "b"}), _result("e1"),
        _say("That command is written into `peer-repo/memory/README.md` so it is findable."),
    ], cwd=root)
    assert _run(t, root).returncode == 0


def test_a_cp_destination_counts_as_a_write(tmp_path):
    root = _repo(tmp_path)
    t = _transcript(tmp_path, [
        _user("copy it over"),
        _assistant_tool("b1", "Bash", {"command": f"cp /tmp/src.md {tmp_path}/far/output/x/brief.md"}),
        _result("b1"),
        _say("Copied to `output/x/brief.md`."),
    ], cwd=root)
    assert _run(t, root).returncode == 0


def test_a_tokenizer_fragment_of_a_written_name_clears_the_claim(tmp_path):
    """Real shape (2026-09-01): the claimed token was the tail of a written file's name."""
    assert csc.claim_matches_write(
        "_share_links.md", ["/x/feedback_js_rendered_share_links.md"])


def test_a_truncated_dotted_name_matches_its_write(tmp_path):
    """Real shape (2026-10-26): 'job-todos.pre-cleanup-102526.md' read as 'job-todos.p'."""
    assert csc.claim_matches_write(
        "output/table-audit/job-todos.p",
        ["/r/output/table-audit/job-todos.pre-cleanup-102526.md"])


def test_a_proposal_file_label_is_not_a_claim():
    """Real shape (2026-10-21, /lessons-learned): 'I copied ...' above a '- *File:* x.md'
    proposal line. The file correctly does not exist yet."""
    text = ("I copied the trace into the rule.\n"
            "- *File:* `feedback_measure_cases_first.md`.\n")
    assert csc.extract_claimed_paths(text) == []


# ---------- the case the hook exists for: must BLOCK ----------

def test_a_claimed_save_with_no_write_anywhere_blocks(tmp_path):
    """Origin: a prompt printed to chat and 'Saved to output/<co>/<file>.md', no Write."""
    root = _repo(tmp_path)
    t = _transcript(tmp_path, [
        _user("make the sim prompt"),
        _say("Here is the prompt.\n\nSaved to `output/acme/111626-sim-prompt.md`."),
    ], cwd=root)
    r = _run(t, root)
    assert r.returncode == 2
    assert "output/acme/111626-sim-prompt.md" in r.stderr


def test_other_real_writes_do_not_clear_an_unwritten_claim(tmp_path):
    """The origin session wrote other files genuinely; this one was only asserted."""
    root = _repo(tmp_path)
    t = _transcript(tmp_path, [
        _user("build the deck and the prompt"),
        _assistant_tool("w1", "Write", {"file_path": str(root / "output/acme/deck.html"),
                                        "content": "x"}), _result("w1"),
        _say("Deck done. Saved to `output/acme/111626-sim-prompt.md`."),
    ], cwd=root)
    (root / "output" / "acme").mkdir(parents=True)
    (root / "output" / "acme" / "deck.html").write_text("x")
    assert _run(t, root).returncode == 2


def test_a_failed_write_does_not_clear_the_claim(tmp_path):
    root = _repo(tmp_path)
    t = _transcript(tmp_path, [
        _user("save it"),
        _assistant_tool("w1", "Write", {"file_path": str(root / "output/acme/brief.md"),
                                        "content": "x"}),
        _result("w1", is_error=True, text="File has not been read yet"),
        _say("Saved to `output/acme/brief.md`."),
    ], cwd=root)
    assert _run(t, root).returncode == 2


def test_a_short_fragment_does_not_match():
    """Under 8 characters a fragment matches too much ('a.md' is inside 'data.md')."""
    assert not csc.claim_matches_write("a.md", ["/r/data.md"])


def test_a_different_file_in_the_same_directory_does_not_match():
    assert not csc.claim_matches_write("output/acme/brief.md", ["/r/output/acme/deck.html"])


def test_a_write_whose_name_merely_contains_a_short_claim_does_not_match():
    assert not csc.claim_matches_write("notes.md", ["/r/meeting-notes.md.bak"])


# ---------- session_write_targets ----------

def test_session_write_targets_reads_write_edit_and_bash(tmp_path):
    root = _repo(tmp_path)
    t = _transcript(tmp_path, [
        _assistant_tool("w1", "Write", {"file_path": "/abs/a.md", "content": "x"}), _result("w1"),
        _assistant_tool("e1", "Edit", {"file_path": "/abs/b.md", "old_string": "a",
                                       "new_string": "b"}), _result("e1"),
        _assistant_tool("b1", "Bash", {"command": "echo hi > out/c.md"}), _result("b1"),
        _assistant_tool("w2", "Write", {"file_path": "/abs/failed.md", "content": "x"}),
        _result("w2", is_error=True),
    ], cwd=root)
    got = csc.session_write_targets(str(t), root)
    assert "/abs/a.md" in got and "/abs/b.md" in got
    assert str(root / "out" / "c.md") in got
    assert "/abs/failed.md" not in got


def test_session_write_targets_on_a_missing_transcript_is_empty(tmp_path):
    assert csc.session_write_targets(str(tmp_path / "nope.jsonl"), tmp_path) == []


# ---------- robustness and contract details (mutation round 1 survivors) ----------

def test_session_write_targets_skips_malformed_records(tmp_path):
    """A transcript is read line by line from another program's output: a non-JSON line, a
    message with no content list, and a non-dict content block are skipped, not fatal."""
    root = _repo(tmp_path)
    tpath = tmp_path / "t.jsonl"
    lines = [
        "not json at all",
        json.dumps({"type": "user", "message": {"role": "user", "content": None}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                       "content": ["a bare string block"]}}),
        json.dumps(_assistant_tool("w1", "Write", {"file_path": "/abs/kept.md", "content": "x"})),
        json.dumps(_result("w1")),
    ]
    tpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert csc.session_write_targets(str(tpath), root) == ["/abs/kept.md"]


def test_a_write_call_without_a_path_and_a_non_write_tool_add_nothing(tmp_path):
    root = _repo(tmp_path)
    t = _transcript(tmp_path, [
        _assistant_tool("w1", "Write", {"content": "no path given"}), _result("w1"),
        _assistant_tool("r1", "Read", {"file_path": "/abs/only-read.md"}), _result("r1"),
    ], cwd=root)
    assert csc.session_write_targets(str(t), root) == []


def test_claim_matches_write_returns_a_bool():
    assert csc.claim_matches_write("output/acme/brief.md", ["/r/output/acme/deck.html"]) is False
    assert csc.claim_matches_write("output/acme/brief.md", ["/r/output/acme/brief.md"]) is True


def test_a_fragment_in_the_wrong_directory_does_not_match():
    assert csc.claim_matches_write(
        "output/acme/job-todos.p", ["/r/output/other/job-todos.pre-cleanup.md"]) is False


def _main_with(monkeypatch, root, tpath, writes_fn):
    """Run main() in process with a real Payload, so the test reaches the claim logic
    (a StringIO stdin has no fd, and read_payload would fail open before it)."""
    import hook_runtime
    monkeypatch.setattr(csc, "session_write_targets", writes_fn)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setattr(csc, "read_payload", lambda: hook_runtime.Payload(
        ok=True, data={"transcript_path": str(tpath)}, raw="{}"))
    return csc.main()


def test_a_turn_with_no_missing_claim_never_reads_the_session_writes(tmp_path, monkeypatch):
    """The transcript walk runs only when a claim is missing; a clean turn pays nothing."""
    root = _repo(tmp_path)
    (root / "notes.md").write_text("x")
    t = _transcript(tmp_path, [_user("hi"), _say("Saved to `notes.md`.")], cwd=root)

    def boom(*a, **k):
        raise AssertionError("session_write_targets read on a clean turn")
    assert _main_with(monkeypatch, root, t, boom) == 0


def test_a_missing_claim_does_read_the_session_writes(tmp_path, monkeypatch):
    """The companion: proves the in-process harness reaches the write lookup at all."""
    root = _repo(tmp_path)
    t = _transcript(tmp_path, [_user("hi"), _say("Saved to `output/x/gone.md`.")], cwd=root)
    calls = []

    def record(*a, **k):
        calls.append(a)
        return ["/elsewhere/output/x/gone.md"]
    assert _main_with(monkeypatch, root, t, record) == 0
    assert len(calls) == 1


def test_a_non_bash_tool_with_a_command_field_adds_no_target(tmp_path):
    """Only Bash commands are parsed for writes; another tool's `command` input is not run
    by a shell."""
    root = _repo(tmp_path)
    t = _transcript(tmp_path, [
        _assistant_tool("m1", "Monitor", {"command": "echo x > /abs/never.md"}), _result("m1"),
    ], cwd=root)
    assert csc.session_write_targets(str(t), root) == []
