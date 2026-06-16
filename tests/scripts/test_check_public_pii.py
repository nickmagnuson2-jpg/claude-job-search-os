"""Tests for tools/check_public_pii.py — the public-repo PII PreToolUse hook.

The hook blocks (exit 2) when a denylisted real name / company appears in a public
artifact (tests/, .claude/skills/, framework/, docs/, tools/*.py, top-level *.md),
and stays clean (exit 0) for generic placeholders, private paths (data/**), out-of-
scope paths, and tokens that only appear as substrings of larger words.

The denylist is a per-test fixture so these tests never depend on (or contain) real
PII themselves.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_public_pii.py"

# Fixture denylist — INVENTED tokens standing in for the gitignored production list.
# Deliberately synthetic (no real person/company) so this public test leaks nothing.
FIXTURE_DENYLIST = "Zorptech\nMirvelo\nPat Zorp\nRobin Mirvel\n"


def _run(tmp_path, file_path, content, tool_name="Write", with_denylist=True):
    """Run the hook with cwd=tmp_path. file_path is repo-relative (resolved against cwd)."""
    if with_denylist:
        dl = tmp_path / "tools" / ".pii-denylist.txt"
        dl.parent.mkdir(parents=True, exist_ok=True)
        dl.write_text("# fixture\n" + FIXTURE_DENYLIST, encoding="utf-8")

    if tool_name == "Edit":
        tool_input = {"file_path": file_path, "new_string": content}
    else:
        tool_input = {"file_path": file_path, "content": content}

    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True,
                       cwd=str(tmp_path))
    return r.returncode, r.stderr


# --- should BLOCK (exit 2) --------------------------------------------------

def test_blocks_real_company_in_test_file(tmp_path):
    code, err = _run(tmp_path, "tests/scripts/test_x.py", "assert 'Zorptech' in result")
    assert code == 2
    assert "Zorptech" in err


def test_blocks_real_full_name_in_skill(tmp_path):
    code, _ = _run(tmp_path, ".claude/skills/foo/SKILL.md", "Origin: Pat Zorp call.")
    assert code == 2


def test_blocks_on_edit_new_string(tmp_path):
    code, _ = _run(tmp_path, "framework/bar.md", "e.g. Robin Mirvel replied", tool_name="Edit")
    assert code == 2


def test_blocks_in_tool_python_comment(tmp_path):
    code, _ = _run(tmp_path, "tools/some_tool.py", "# origin: Mirvelo standup bug")
    assert code == 2


def test_blocks_top_level_markdown(tmp_path):
    code, _ = _run(tmp_path, "README.md", "Example pipeline: Zorptech")
    assert code == 2


# --- should ALLOW (exit 0) --------------------------------------------------

def test_allows_generic_placeholders(tmp_path):
    code, _ = _run(tmp_path, "tests/scripts/test_x.py",
                   "assert 'ClosedCo' in result  # Casey Doe placeholder")
    assert code == 0


def test_allows_private_data_path(tmp_path):
    # data/** is private (gitignored); real names are expected there.
    code, _ = _run(tmp_path, "data/networking.md", "Pat Zorp | Mirvelo | ...")
    assert code == 0


def test_allows_out_of_scope_path(tmp_path):
    # output/ is not a public-skill/test path for this hook's purposes.
    code, _ = _run(tmp_path, "output/zorptech/dossier.md", "Zorptech and Mirvelo")
    assert code == 0


def test_token_substring_not_matched(tmp_path):
    # "Zorptech" must not match inside "Zorptechnology".
    code, _ = _run(tmp_path, "tests/scripts/test_x.py", "the Zorptechnology renderer")
    assert code == 0


def test_allows_when_no_denylist(tmp_path):
    # Fail open: no denylist file → nothing to enforce.
    code, _ = _run(tmp_path, "tests/scripts/test_x.py", "Zorptech", with_denylist=False)
    assert code == 0


def test_ignores_non_write_edit_tool(tmp_path):
    payload = json.dumps({"tool_name": "Read",
                          "tool_input": {"file_path": "tests/x.py"}})
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0


def test_malformed_json_fails_open(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input="not json", capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0
