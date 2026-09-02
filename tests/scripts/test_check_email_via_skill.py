"""The hook enforcing the never-draft-email-inline Hard Rule.

WHY IT HAD TO BE THIS ONE. check_email_via_skill was wired in settings.json, live on every
Write and Edit, and had no suite of its own. Its 23/23 mutation survival came from unrelated
test files that merely mention the module, so it read as "tests that catch nothing" when the
truth was "no tests at all". A guard whose failure mode is silent non-enforcement is the
worst kind to leave unmeasured: nothing errors, drafts simply stop being caught.

WHAT IT GUARDS. Drafting an email inline bypasses voice-reference matching, the email
corpus, and check_draft_voice.py on the staging file. The rule fired from a real 2026-05-20
incident. The hook's job is to make the bypass impossible at the tool-call surface.

TESTED THROUGH THE REAL ENTRY POINT, not the helpers. Per tools/HOOK_AUTHORING.md: a
guarantee the CLI advertises needs a test that RUNS the CLI. A 53-test suite once passed
while the shipped hook was broken because every test called the helper directly.

The two directions are not symmetric. A false negative silently drops the guard; a false
positive blocks legitimate writes and trains the reader to set the override, which drops the
guard too. Both are covered below.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "tools" / "check_email_via_skill.py"

BLOCK, ALLOW = 2, 0

EMAIL = ("Subject: Following up on our conversation\n\n"
         "Hi Jordan,\n\n"
         "Wanted to circle back on the role we discussed.\n\n"
         "Best,\nNick\n")


def run(file_path: str, content: str = "", *, new_string: str | None = None,
        env_override: bool = False, raw: str | None = None) -> subprocess.CompletedProcess:
    tool_input: dict = {"file_path": file_path}
    if new_string is not None:
        tool_input["new_string"] = new_string
    else:
        tool_input["content"] = content
    payload = raw if raw is not None else json.dumps({"tool_input": tool_input})
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"}
    if env_override:
        env["EMAIL_VIA_SKILL_OVERRIDE"] = "1"
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, env=env)


# --- the guarantee: email shape outside the allowlist is BLOCKED ------------

def test_an_email_written_to_an_arbitrary_file_is_blocked():
    r = run("/repo/notes/scratch.md", EMAIL)
    assert r.returncode == BLOCK, r.stderr


def test_the_block_uses_exit_2_not_1():
    """PreToolUse only blocks on exit 2. Exit 1 is an infrastructure error and the tool
    call proceeds -- the guard would look wired and enforce nothing."""
    assert run("/repo/notes/scratch.md", EMAIL).returncode == 2


def test_the_block_message_names_what_matched_and_where_to_go():
    """A block that does not say why trains the reader to reach for the override."""
    err = run("/repo/notes/scratch.md", EMAIL).stderr
    assert "BLOCKED" in err
    assert "/draft-email" in err
    assert "Subject: header" in err


def test_an_edit_is_covered_not_just_a_write():
    """Edit sends new_string, not content. Reading only `content` leaves every Edit
    unguarded, which is the more common way a file gets modified."""
    assert run("/repo/notes/scratch.md", new_string=EMAIL).returncode == BLOCK


# --- the threshold: one signal is not an email ------------------------------

def test_a_single_signal_is_not_enough_to_block():
    """A doc quoting one Subject: line is not a draft. Blocking it is the false positive
    that gets the whole hook disabled."""
    assert run("/repo/notes/doc.md", "Subject: a talk I attended\n\nnotes\n").returncode == ALLOW


def test_two_signals_block():
    assert run("/repo/notes/doc.md",
               "Subject: hello\nTo: someone@example.com\n").returncode == BLOCK


@pytest.mark.parametrize("second", [
    "To: jordan@example.com\n",
    "Hi Jordan,\n",
    "\nBest,\nNick\n",
    "BODY:\n",
], ids=["to-header", "hi-opener", "signature", "body-marker"])
def test_each_shape_signal_counts_toward_the_threshold(second):
    """Every pattern must actually contribute. A regex that silently stops matching drops
    the effective threshold and the guard weakens without failing."""
    assert run("/repo/notes/doc.md", "Subject: hello\n" + second).returncode == BLOCK


# --- the allowlist: legitimate skill destinations must pass -----------------

@pytest.mark.parametrize("path", [
    "/repo/tools/.pending-draft.txt",
    "/repo/output/acme/090226-draft-email-jordan.md",
    "/repo/output/acme/090226-follow-up-jordan.md",
    "/repo/output/acme/090226-cold-outreach-jordan.md",
    "/repo/output/acme/090226-cover-letter-jordan.md",
    "/repo/data/networking.md",
    "/repo/data/outreach-log.md",
    "/repo/inbox/2026-09/mail.md",
    "/repo/framework/voice-reference.md",
    "/repo/data/voice-corpus/exemplar.md",
])
def test_allowlisted_destinations_are_not_blocked(path):
    """These are the skill's own staging, archive and corpus paths. Blocking them would
    make the sanctioned flow impossible, which is how a guard gets removed."""
    assert run(path, EMAIL).returncode == ALLOW, path


def test_a_lookalike_output_path_is_still_blocked():
    """The archive pattern is specific on purpose: a wrong-shaped filename under output/
    is not the skill's archive and must not inherit its exemption."""
    assert run("/repo/output/acme/notes-about-jordan.md", EMAIL).returncode == BLOCK


def test_a_networking_lookalike_elsewhere_is_still_blocked():
    assert run("/repo/output/acme/networking.md", EMAIL).returncode == BLOCK


# --- override and fail-open -------------------------------------------------

def test_the_documented_override_bypasses_the_hook():
    assert run("/repo/notes/scratch.md", EMAIL, env_override=True).returncode == ALLOW


def test_malformed_stdin_fails_open():
    """Fail-open is deliberate: a hook-internal problem must never block real work. The
    cost is that a crash is silent, which is exactly why the tests above exist."""
    assert run("", raw="{not json").returncode == ALLOW


def test_empty_stdin_fails_open():
    assert run("", raw="").returncode == ALLOW


@pytest.mark.parametrize("payload", [
    {"tool_input": {}},
    {"tool_input": {"file_path": "/repo/x.md"}},
    {"tool_input": {"content": "Subject: x\nTo: a@b.c\n"}},
    {},
], ids=["no-fields", "path-only", "content-only", "no-tool-input"])
def test_incomplete_payloads_do_not_block(payload):
    """No path or no content means nothing to judge; blocking on it would fire on
    unrelated tool calls."""
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert r.returncode == ALLOW


# --- false positives on ordinary prose --------------------------------------

@pytest.mark.parametrize("content", [
    "# Notes\n\nWe discussed the role. Best regards to the team.\n",
    "The subject: of the talk was distributed systems.\n",
    "Reach them at hello@example.com if needed.\n",
    "",
], ids=["prose-with-best", "inline-subject", "bare-address", "empty"])
def test_ordinary_prose_is_not_blocked(content):
    assert run("/repo/data/company-notes/acme.md", content).returncode == ALLOW


# --- the assertion that has to fail first -----------------------------------

def test_the_hook_exists_and_the_threshold_is_meaningful():
    """Every allow-assertion above is satisfied by a hook that never blocks anything.
    If the file moved or the threshold went to zero, this fails first."""
    assert HOOK.is_file(), f"hook not found at {HOOK}"
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import check_email_via_skill as mod
    assert mod.THRESHOLD >= 2, "a threshold below 2 blocks any doc quoting one email line"
    assert len(mod.EMAIL_SHAPE_PATTERNS) >= mod.THRESHOLD
    assert len(mod.ALLOWLIST_PATTERNS) >= 5
