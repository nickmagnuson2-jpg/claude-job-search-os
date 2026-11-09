"""check_draft_voice.py: the helpers and the main() decision path, observed directly.

Before this file the content patterns were well tested (t10, hedged_time_ask) but the
code that decides WHETHER they run was not: the 2026-10-05 sweep recorded 68 of 84
mutants surviving, almost all in read_hook_command, check_provenance, extract_body,
load_personal_recipients, check_stacked_really and main(). A mutant there can turn the
gate into a no-op (skip provenance, swallow the BLOCKED message, exit 0 on a bad draft)
while every pattern test stays green.

Every path a test writes is under tmp_path. The hook's real tools/.pending-draft.txt is
never touched: module constants are monkeypatched in-process, and the one subprocess test
runs a COPY of the hook from a temp directory, where Path(__file__).parent is that
directory.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
SCRIPT = TOOLS / "check_draft_voice.py"
_spec = importlib.util.spec_from_file_location("check_draft_voice_main_t", SCRIPT)
cdv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdv)

INVOKE = "PYTHONIOENCODING=utf-8 python3 tools/open_draft.py"


class _FakePayload:
    def __init__(self, ok, data=None):
        self.ok = ok
        self.data = data if data is not None else {}


# --- read_hook_command ---------------------------------------------------------------

def test_unreadable_payload_reads_as_none(monkeypatch):
    monkeypatch.setattr(cdv, "read_payload", lambda: _FakePayload(False))
    assert cdv.read_hook_command() is None


def test_payload_command_is_returned(monkeypatch):
    monkeypatch.setattr(cdv, "read_payload",
                        lambda: _FakePayload(True, {"tool_input": {"command": INVOKE}}))
    assert cdv.read_hook_command() == INVOKE


@pytest.mark.parametrize("data", [{}, {"tool_input": None}, {"tool_input": {"command": None}}])
def test_payload_without_a_command_reads_as_empty_string(monkeypatch, data):
    monkeypatch.setattr(cdv, "read_payload", lambda: _FakePayload(True, data))
    assert cdv.read_hook_command() == ""


def test_unknown_command_is_treated_as_an_invocation():
    # Fail conservative: when the command cannot be read, the check runs.
    assert cdv.is_open_draft_invocation(None) is True
    assert cdv.is_open_draft_invocation("") is False


# --- check_stacked_really ------------------------------------------------------------

@pytest.mark.parametrize("body", ["", "No filler here.", "I really liked it."])
def test_one_really_or_none_is_allowed(body):
    assert cdv.check_stacked_really(body) == []


def test_two_reallys_are_flagged_with_the_count():
    out = cdv.check_stacked_really("Really good. I really liked it. Really.")
    assert len(out) == 1
    assert '"really" appears 3 times' in out[0]


# --- extract_body ------------------------------------------------------------------

def test_body_is_the_text_after_the_marker_stripped():
    content = "TO: a@example.com\nSUBJECT: hi\nBODY:\n\n  Hello there.  \n\n"
    assert cdv.extract_body(content) == "Hello there."


def test_no_marker_returns_the_whole_content_unchanged():
    content = "  no headers at all  \n"
    assert cdv.extract_body(content) == content


def test_marker_at_the_very_start_is_found():
    assert cdv.extract_body("BODY:hello") == "hello"


# --- load_personal_recipients ------------------------------------------------------

def test_missing_allowlist_file_is_an_empty_set(monkeypatch, tmp_path):
    monkeypatch.setattr(cdv, "PERSONAL_RECIPIENTS_FILE", tmp_path / "absent.txt")
    got = cdv.load_personal_recipients()
    assert isinstance(got, set) and got == set()


def test_unreadable_allowlist_file_is_an_empty_set(monkeypatch, tmp_path):
    d = tmp_path / "is-a-directory.txt"
    d.mkdir()
    monkeypatch.setattr(cdv, "PERSONAL_RECIPIENTS_FILE", d)
    got = cdv.load_personal_recipients()
    assert isinstance(got, set) and got == set()


def test_allowlist_file_is_lowercased_without_comments_or_blanks(monkeypatch, tmp_path):
    f = tmp_path / "personal.txt"
    f.write_text("# family\n\n  Spouse@Example.com \nfriend@example.com\n   # note\n",
                 encoding="utf-8")
    monkeypatch.setattr(cdv, "PERSONAL_RECIPIENTS_FILE", f)
    assert cdv.load_personal_recipients() == {"spouse@example.com", "friend@example.com"}


# --- check_provenance ----------------------------------------------------------------

def _marker(tmp_path, text="follow-up\n", age=0.0):
    src = tmp_path / ".pending-draft.source"
    src.write_text(text, encoding="utf-8")
    if age:
        t = time.time() - age
        os.utime(src, (t, t))
    return src


def test_missing_marker_names_the_skills(monkeypatch, tmp_path):
    monkeypatch.setattr(cdv, "SOURCE_FILE", tmp_path / ".pending-draft.source")
    out = cdv.check_provenance(INVOKE)
    assert len(out) == 1
    assert out[0].startswith("No skill provenance marker")


@pytest.mark.parametrize("skill", sorted(cdv.ALLOWED_SKILLS))
def test_fresh_marker_for_an_allowed_skill_is_clean(monkeypatch, tmp_path, skill):
    monkeypatch.setattr(cdv, "SOURCE_FILE", _marker(tmp_path, skill + "\nextra line\n"))
    assert cdv.check_provenance() == []


def test_marker_a_little_under_the_age_limit_is_clean(monkeypatch, tmp_path):
    monkeypatch.setattr(cdv, "SOURCE_FILE",
                        _marker(tmp_path, age=cdv.SOURCE_MAX_AGE_SECONDS - 30))
    assert cdv.check_provenance() == []


def test_marker_past_the_age_limit_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(cdv, "SOURCE_FILE",
                        _marker(tmp_path, age=cdv.SOURCE_MAX_AGE_SECONDS + 60))
    out = cdv.check_provenance()
    assert len(out) == 1 and out[0].startswith("Stale provenance marker")


def test_marker_slightly_in_the_future_is_within_the_grace(monkeypatch, tmp_path):
    monkeypatch.setattr(cdv, "SOURCE_FILE", _marker(tmp_path, age=-5))
    assert cdv.check_provenance() == []


def test_marker_far_in_the_future_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(cdv, "SOURCE_FILE", _marker(tmp_path, age=-120))
    out = cdv.check_provenance()
    assert len(out) == 1 and "future timestamp" in out[0]


def test_empty_marker_is_unreadable(monkeypatch, tmp_path):
    monkeypatch.setattr(cdv, "SOURCE_FILE", _marker(tmp_path, ""))
    assert cdv.check_provenance() == [
        "Unreadable provenance marker. Re-invoke the drafting skill."]


def test_marker_naming_an_unknown_skill_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(cdv, "SOURCE_FILE", _marker(tmp_path, "inline-chat\n"))
    out = cdv.check_provenance()
    assert len(out) == 1 and 'unknown skill "inline-chat"' in out[0]


# --- main() ------------------------------------------------------------------------

def _setup(monkeypatch, tmp_path, body="Thanks for the note.", to="casey@example.com",
           marker="follow-up\n", command=INVOKE, personal=()):
    draft = tmp_path / ".pending-draft.txt"
    draft.write_text(f"TO: {to}\nSUBJECT: Re: hello\nBODY:\n{body}\n", encoding="utf-8")
    source = tmp_path / ".pending-draft.source"
    if marker is not None:
        source.write_text(marker, encoding="utf-8")
    allow = tmp_path / ".personal-recipients.txt"
    allow.write_text("".join(a + "\n" for a in personal), encoding="utf-8")
    monkeypatch.setattr(cdv, "DRAFT_FILE", draft)
    monkeypatch.setattr(cdv, "SOURCE_FILE", source)
    monkeypatch.setattr(cdv, "PERSONAL_RECIPIENTS_FILE", allow)
    monkeypatch.setattr(cdv, "read_hook_command", lambda: command)
    monkeypatch.delenv("DRAFT_OVERRIDE", raising=False)
    return draft


def _main(capsys):
    with pytest.raises(SystemExit) as exc:
        cdv.main()
    return exc.value.code, capsys.readouterr().err


def test_clean_draft_exits_zero_explicitly(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    code, err = _main(capsys)
    assert code == 0 and err == ""


def test_voice_violation_blocks_with_line_match_and_fix(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, body="First line.\nI genuinely enjoyed it.")
    code, err = _main(capsys)
    assert code == 2
    assert err.startswith("BLOCKED: tools/.pending-draft.txt contains voice anti-patterns.")
    assert "Line 2:" in err and '"genuinely"' in err
    assert "Match:" in err and "Fix:" in err


def test_stacked_really_blocks_end_to_end(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, body="Really good call. I really liked the plan.")
    code, err = _main(capsys)
    assert code == 2
    assert '"really" appears 2 times' in err


def test_override_bypasses_a_blocking_draft(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, body="I genuinely enjoyed it.", marker=None)
    monkeypatch.setenv("DRAFT_OVERRIDE", "1")
    code, _ = _main(capsys)
    assert code == 0


def test_override_other_than_one_does_not_bypass(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, body="I genuinely enjoyed it.")
    monkeypatch.setenv("DRAFT_OVERRIDE", "yes")
    code, _ = _main(capsys)
    assert code == 2


def test_unrelated_bash_command_is_not_checked(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, body="I genuinely enjoyed it.", marker=None,
           command="ls tools/")
    code, err = _main(capsys)
    assert code == 0 and err == ""


def test_no_draft_file_fails_open(monkeypatch, tmp_path, capsys):
    draft = _setup(monkeypatch, tmp_path, marker=None)
    draft.unlink()
    code, err = _main(capsys)
    assert code == 0 and err == ""


def test_unreadable_draft_fails_open(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, marker=None)
    d = tmp_path / "draft-is-a-directory"
    d.mkdir()
    monkeypatch.setattr(cdv, "DRAFT_FILE", d)
    code, err = _main(capsys)
    assert code == 0 and err == ""


def test_missing_provenance_blocks_before_content(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, body="I genuinely enjoyed it.", marker=None)
    code, err = _main(capsys)
    assert code == 2
    assert err.startswith(
        "BLOCKED: tools/.pending-draft.txt was created outside an approved drafting skill.")
    assert "No skill provenance marker" in err
    assert "voice anti-patterns" not in err


def test_personal_recipient_skips_provenance_but_not_voice(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, to="spouse@example.com", marker=None,
           personal=("spouse@example.com",))
    code, err = _main(capsys)
    assert code == 0, err
    _setup(monkeypatch, tmp_path, to="spouse@example.com", marker=None,
           personal=("spouse@example.com",), body="See you at six; bring the map.")
    code, err = _main(capsys)
    assert code == 2 and "semicolon" in err


def test_mixed_recipients_stay_gated(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, to="spouse@example.com, casey@example.com", marker=None,
           personal=("spouse@example.com",))
    code, err = _main(capsys)
    assert code == 2 and "No skill provenance marker" in err


def test_empty_body_exits_zero(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, body="   \n  ")
    code, err = _main(capsys)
    assert code == 0 and err == ""


# --- the script entry point ----------------------------------------------------------

def test_running_the_script_blocks_a_bad_draft(tmp_path):
    """`python3 tools/check_draft_voice.py` must reach main(). Runs a COPY from tmp_path,
    so its DRAFT_FILE and SOURCE_FILE are the temp ones, never the real pending draft."""
    for name in ("check_draft_voice.py", "hook_command_lint.py", "hook_runtime.py"):
        shutil.copy(TOOLS / name, tmp_path / name)
    (tmp_path / ".pending-draft.txt").write_text(
        "TO: casey@example.com\nSUBJECT: hi\nBODY:\nI genuinely enjoyed it.\n",
        encoding="utf-8")
    (tmp_path / ".pending-draft.source").write_text("follow-up\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "DRAFT_OVERRIDE"}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": INVOKE}})
    r = subprocess.run([sys.executable, str(tmp_path / "check_draft_voice.py")],
                       input=payload, capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 2, r.stderr
    assert "voice anti-patterns" in r.stderr
