"""Tests for tools/check_ats_raw_fetch.py (PreToolUse Bash hook).

Block cases are the shapes found in the 2026-11-18 restraint replay (149 of 347
historical ATS-host commands; 149 after round 2): curl one-liners, for-loops over slugs, python -c and
python heredocs with urllib. Clean cases are the shapes that mention a host without
fetching it: greps, notes, doc edits, commit messages, and use of the project parser.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "tools" / "check_ats_raw_fetch.py"
sys.path.insert(0, str(REPO / "tools"))

import check_ats_raw_fetch as hook  # noqa: E402

ASHBY = "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
LEVER = "https://api.lever.co/v0/postings/acme?mode=json"


def _run(command: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30,
                          env={**os.environ, "PYTHONPATH": str(HOOK.parent)})


BLOCK = [
    f'curl -s "{ASHBY}" | python3 -c "import json,sys; print(json.load(sys.stdin))"',
    f'cd /some/repo && curl -s -m 25 "{GREENHOUSE}"',
    f'/usr/bin/curl -s "{LEVER}" -o /dev/null -w "%{{http_code}}"',
    f'wget -qO- "{ASHBY}"',
    'for s in acme beta; do\n  curl -s "https://api.ashbyhq.com/posting-api/job-board/$s"\ndone',
    'for u in "https://api.lever.co/v0/postings/acme"; do curl -s "$u"; done',
    f"python3 - <<'PY'\nimport json, urllib.request\nd=json.load(urllib.request.urlopen('{ASHBY}'))\nPY",
    f"PYTHONIOENCODING=utf-8 python3 -c \"import urllib.request; urllib.request.urlopen('{GREENHOUSE}')\"",
    f"python3 - <<'EOF'\nimport requests\nrequests.get('{LEVER}')\nEOF",
    f"sed -n 1,5p notes.md; python3 - <<'PY'\nimport httpx\nhttpx.get('{ASHBY}')\nPY",
    f"S=/tmp/x && mkdir -p $S && curl -s '{ASHBY}' -o $S/a.json",
    f"ls tools/ | xargs curl -s '{ASHBY}'",
]

CLEAN = [
    # mentions of the host with no fetch
    'grep -rn "api.ashbyhq.com/posting-api" tools/',
    'grep -n "curl.*api.lever.co/v0/postings" docs/notes.md',
    f'echo "{ASHBY}"',
    f'git commit -m "note: curl {ASHBY} returns 403 without a user agent"',
    "git commit -F - <<'EOF'\nfix: stop curl to https://api.ashbyhq.com/posting-api\nEOF",
    # python that only edits a file containing the URL (no HTTP call)
    f"python3 - <<'PY'\np='notes.md'; s=open(p).read().replace('x','{ASHBY}'); open(p,'w').write(s)\nPY",
    # an HTTP call in a python program with no ATS host
    "python3 -c \"import urllib.request; urllib.request.urlopen('https://example.com')\"",
    # the supported path
    "PYTHONIOENCODING=utf-8 python3 tools/ats_board.py ashby acme --comp",
    "python3 -c \"from tools.career_scanner.ashby import fetch_ashby; print(len(fetch_ashby('acme', [])))\"",
    # single-posting GraphQL: no project parser wraps it, deliberately not covered
    'curl -s "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting" -d "{}"',
    # a curl to an unrelated host
    'curl -s https://example.com',
    "",
]


@pytest.mark.parametrize("command", BLOCK)
def test_blocks_raw_fetch(command):
    r = _run(command)
    assert r.returncode == 2, r.stderr
    assert "BLOCKED [check_ats_raw_fetch]" in r.stderr
    assert "tools/ats_board.py" in r.stderr


@pytest.mark.parametrize("command", CLEAN)
def test_allows_non_fetch(command):
    r = _run(command)
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""


@pytest.mark.parametrize("prefix", [
    'ATS_RAW_FETCH_OK="parser has no field for X" ',
    "ATS_RAW_FETCH_OK='parser has no field for X' ",
    "ATS_RAW_FETCH_OK=parser-lacks-x ",
])
def test_override_with_reason_allows(prefix):
    r = _run(prefix + f'curl -s "{ASHBY}"')
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("prefix", ['ATS_RAW_FETCH_OK="" ', "ATS_RAW_FETCH_OK= ", "ATS_RAW_FETCH_OK='  ' "])
def test_override_without_reason_still_blocks(prefix):
    r = _run(prefix + f'curl -s "{ASHBY}"')
    assert r.returncode == 2


def test_override_text_inside_a_quoted_note_does_not_count():
    r = _run(f'echo "ATS_RAW_FETCH_OK=x"; curl -s "{ASHBY}"')
    assert r.returncode == 2


def test_unreadable_payload_fails_open():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True, timeout=30,
                       env={**os.environ, "PYTHONPATH": str(HOOK.parent)})
    assert r.returncode == 0


def test_non_bash_payload_with_no_command_is_clean():
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "x", "content": ASHBY}})
    r = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True,
                       text=True, timeout=30, env={**os.environ, "PYTHONPATH": str(HOOK.parent)})
    assert r.returncode == 0


# --- unit level: each detection branch alone --------------------------------

def test_host_alone_is_not_a_fetch():
    assert hook.is_raw_ats_fetch(f"cat file # {ASHBY}") is False


def test_shell_fetch_branch():
    assert hook.is_raw_ats_fetch(f"curl {ASHBY}") is True
    assert hook.is_raw_ats_fetch(f'grep "curl {ASHBY}" f') is False


def test_python_branch_needs_both_interpreter_and_http_call():
    assert hook.is_raw_ats_fetch(f"python3 -c \"urlopen('{ASHBY}')\"") is True
    assert hook.is_raw_ats_fetch(f"python3 -c \"print('{ASHBY}')\"") is False
    assert hook.is_raw_ats_fetch(f"grep urlopen f; echo {ASHBY}") is False


@pytest.mark.parametrize("host", [ASHBY, GREENHOUSE, LEVER,
                                  "https://api.greenhouse.io/v1/boards/acme/jobs"])
def test_every_host_is_covered(host):
    assert hook.is_raw_ats_fetch(f"curl -s {host}") is True


def test_has_override_requires_reason():
    assert hook.has_override(f'ATS_RAW_FETCH_OK="why" curl {ASHBY}') is True
    assert hook.has_override(f'ATS_RAW_FETCH_OK="" curl {ASHBY}') is False
    assert hook.has_override(f"curl {ASHBY}") is False
    assert hook.has_override("curl https://example.com") is False


# --- review round 1 (Codex/Grok 2026-11-18) -----------------------------------

@pytest.mark.parametrize("command", [
    # F1: host in one command, a fetch of another URL in a different command
    f'grep -rn "{ASHBY}" docs/; curl -s https://example.com',
    f'echo "{GREENHOUSE}" > note.txt && wget -qO- https://example.com/x',
    f"python3 tools/ats_board.py ashby acme  # was {ASHBY}\ncurl -s https://example.com",
    f"grep -n urlopen tools/x.py; python3 -c \"print('{LEVER}')\"",
])
def test_host_and_fetch_in_different_commands_is_clean(command):
    r = _run(command)
    assert r.returncode == 0, r.stderr


def test_override_on_another_command_does_not_cover_the_fetch():
    # F2 (Codex): a standalone assignment earlier in the line authorizes nothing
    r = _run(f'export ATS_RAW_FETCH_OK="x"; curl -s "{ASHBY}"')
    assert r.returncode == 2
    r = _run(f'ATS_RAW_FETCH_OK="x" true; curl -s "{ASHBY}"')
    assert r.returncode == 2


def test_override_must_cover_every_fetch_in_the_line():
    r = _run(f'ATS_RAW_FETCH_OK="why" curl -s "{ASHBY}"; curl -s "{LEVER}"')
    assert r.returncode == 2


@pytest.mark.parametrize("command", [
    'S="https://api.ashbyhq.com/posting-api/job-board/acme"; curl -s "$S"',
    'U=https://api.lever.co/v0/postings/acme curl -s "$U"',
    'for u in "https://boards-api.greenhouse.io/v1/boards/a/jobs"; do curl -s "$u"; done',
    'x=$(curl -s "https://api.ashbyhq.com/posting-api/job-board/acme")',
])
def test_host_in_variable_or_substitution_still_blocks(command):
    r = _run(command)
    assert r.returncode == 2, r.stderr


def test_variable_fetch_without_a_held_host_is_clean():
    r = _run(f'grep "{ASHBY}" f; U=https://example.com; curl -s "$U"')
    assert r.returncode == 0


def test_no_host_returns_false_not_none():
    assert hook.is_raw_ats_fetch("curl -s https://example.com/jobs") is False


@pytest.mark.parametrize("command", [
    # a host held in a variable does not taint a fetch whose argument is a literal URL
    'S="https://api.ashbyhq.com/posting-api/job-board/acme"; curl -s https://example.com',
    'for u in "https://api.lever.co/v0/postings/a"; do echo "$u"; done; curl -s https://example.com',
])
def test_literal_url_fetch_is_not_tainted_by_a_held_host(command):
    r = _run(command)
    assert r.returncode == 0, r.stderr


# --- review round 2 (Codex/Grok 2026-11-18) -----------------------------------

@pytest.mark.parametrize("command", [
    # r2 F1: a fetch through a variable that does NOT hold the host
    'S="https://api.ashbyhq.com/posting-api/job-board/acme"; U="https://example.com"; curl -s "$U"',
    'S="https://api.lever.co/v0/postings/acme"; curl -s https://example.com -o "$OUT"',
    'S="https://api.lever.co/v0/postings/acme"; curl -s --max-time $((N)) https://example.com',
])
def test_unrelated_variable_is_not_tainted(command):
    r = _run(command)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("prefix", ['ATS_RAW_FETCH_OK="$reason" ', "ATS_RAW_FETCH_OK=$R ",
                                    'ATS_RAW_FETCH_OK="`date`" '])
def test_override_must_be_a_literal(prefix):
    # r2 F2 (Codex): an unset variable expands to empty at run time
    r = _run("reason=; " + prefix + f'curl -s "{ASHBY}"')
    assert r.returncode == 2


# --- review round 3 (Codex/Grok 2026-11-18): order-aware variable tracking ------

@pytest.mark.parametrize("command", [
    'S="https://api.ashbyhq.com/posting-api/job-board/acme"; S="https://example.com"; curl -s "$S"',
    'curl -s "$U"; U="https://api.lever.co/v0/postings/acme"',
    'SITE=example.com/careers; curl "$SITE" -o out.html; SITE="https://api.lever.co/v0/postings/a"; echo "$SITE"',
])
def test_variable_state_is_order_aware(command):
    r = _run(command)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("command", [
    'export S="https://api.ashbyhq.com/posting-api/job-board/acme"; curl -s "$S"',
    'timeout 25 curl -s "https://api.lever.co/v0/postings/acme"',
    'timeout -k 5 30s curl -s "https://boards-api.greenhouse.io/v1/boards/a/jobs"',
])
def test_export_and_timeout_shapes_block(command):
    r = _run(command)
    assert r.returncode == 2, r.stderr


# --- review round 4 (Codex F1, Grok note) -------------------------------------

@pytest.mark.parametrize("builtin", ["declare", "typeset", "local", "readonly", "declare -x"])
def test_builtin_reassignment_clears_the_held_name(builtin):
    r = _run(f'S="{LEVER}"; {builtin} S=file:///dev/null; curl -s "$S"')
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("command", [
    f'timeout --kill-after 5 30s curl -s "{LEVER}"',
    f'timeout --signal KILL 25 curl -s "{ASHBY}"',
    f'timeout --kill-after=5 30s curl -s "{LEVER}"',
])
def test_timeout_long_options_block(command):
    r = _run(command)
    assert r.returncode == 2, r.stderr


@pytest.mark.parametrize("command", [
    # r5 (Codex/Grok): a flag-only option before a value-taking one
    f'timeout --foreground --kill-after 5 30s curl -s "{LEVER}"',
    f'timeout --foreground --signal KILL 25 curl -s "{ASHBY}"',
])
def test_timeout_flag_then_value_option_blocks(command):
    r = _run(command)
    assert r.returncode == 2, r.stderr


@pytest.mark.parametrize("duration", ["1e2", "1.5E1s", "inf", "infinity", "0.5m"])
def test_timeout_strtod_durations_block(duration):
    # r6 (Codex): every GNU timeout duration form is skipped as a duration
    r = _run(f'timeout {duration} curl -s "{LEVER}"')
    assert r.returncode == 2, r.stderr
