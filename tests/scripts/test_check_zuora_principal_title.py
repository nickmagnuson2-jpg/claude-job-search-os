#!/usr/bin/env python3
"""Tests for check_zuora_principal_title.py (PreToolUse Write|Edit|MultiEdit|Bash).

Structure mirrors tests/scripts/test_check_bare_python.py: every case pipes a real
hook JSON payload through the script as a subprocess and asserts the exit code.

The load-bearing case is `test_blocks_origin_input`: the verbatim shape of the
2026-06-11 Otterbrook CV that created the rule, and the corrected version of the
same file that must stay clean.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pytest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "tools", "check_zuora_principal_title.py")

# The Bash branch imports extract_write_targets/split_command_segments from the
# sibling check_public_pii.py. In the repo they sit in the same directory; from the
# staging dir we point PYTHONPATH at tools/ so the same import resolves.
REPO_TOOLS = os.environ.get(
    "ZUORA_HOOK_SIBLING_DIR",
    "/Users/mag/Documents/Obsidian/30-projects/job-search/tools",
)


def _proc(payload: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = REPO_TOOLS + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def _run(payload: dict) -> int:
    return _proc(payload).returncode


def _write(path: str, content: str) -> int:
    return _run({"tool_name": "Write",
                 "tool_input": {"file_path": path, "content": content}})


def _bash(command: str) -> int:
    return _run({"tool_name": "Bash", "tool_input": {"command": command}})


# --------------------------------------------------------------------------
# ORIGIN INPUT — 2026-06-11 Otterbrook/Harrison CV, built on the fractional-ai
# baseline, which rendered the stale title in 4 places. The clean variant is the
# file's current on-disk text, with "Chief Product and Technology Officer".
# --------------------------------------------------------------------------
ORIGIN_PATH = "output/otterbrook/061126-magnuson.content.yaml"

ORIGIN_INPUT = """\
sections:
  SUMMARY:
    - "McKinsey-trained operator who brings structure and clarity to ambiguous, \
cross-functional work. Most recently Chief of Staff to the Head of Product and \
Technology at Zuora, owning cross-functional execution and the operating rhythm \
across a 600-person organization."
  EXPERIENCE:
    - company: ZUORA
      position: Chief of Staff to the Head of Product and Technology
      highlights:
        - Acted as primary execution partner to the Head of Product and Technology \
for a 600-person organization, running discovery across engineering, finance, and \
go-to-market.
        - Prototyped an AI-assisted executive-intelligence workflow (Gemini, \
NotebookLM, Glean Agents), beta-tested with the Head of Product and Technology.
"""

CLEAN_VARIANT = ORIGIN_INPUT.replace(
    "Head of Product and Technology", "Chief Product and Technology Officer"
)


def test_blocks_origin_input():
    assert _write(ORIGIN_PATH, ORIGIN_INPUT) == 2


def test_allows_corrected_origin_input():
    assert _write(ORIGIN_PATH, CLEAN_VARIANT) == 0


# --------------------------------------------------------------------------
# BLOCK cases — the stale title asserted in a new artifact
# --------------------------------------------------------------------------
@pytest.mark.parametrize("content", [
    "*Chief of Staff to Head of Product and Technology*",
    "Chief of Staff to the Head of Product & Technology at Zuora",
    "position: Chief of Staff to Head of Product and Tech",
    "I'm Chief of Staff to Zuora's Head of Product and Technology, running planning.",
    "- Partner with the Head of Product/Technology on FY planning",
    "> \"I finished as Chief of Staff to the head of product and tech at Zuora.\"",
    "HEAD OF PRODUCT AND TECHNOLOGY",
    "Chief of Staff to the Head of Product",          # bare, but CoS-linked
    "Reported to Zuora's head of product.",           # possessive, Zuora-linked
])
def test_blocks_stale_title(content):
    assert _write("output/acme-corp/091026-magnuson.md", content) == 2


# --------------------------------------------------------------------------
# CLEAN cases — the false-positive surface this check must not touch
# --------------------------------------------------------------------------
@pytest.mark.parametrize("content", [
    # canonical rendering
    "Chief of Staff to the Chief Product and Technology Officer",
    "position: Chief of Staff to the Chief Product and Technology Officer",
    # OTHER companies' real heads of product (rule: Jupiter/Feltsense untouched)
    "| Michelle Fechtor | Airsignal | Head of Product (startup) | peer |",
    "Build named personas - Hiring Manager, CEO, Head of Product, Recruiter.",
    "They just hired a Head of Product from Stripe.",
    # self-correcting line: documents the error alongside the canonical title
    "Baseline inherited \"Head of Product and Technology\" (wrong; CPTO).",
    "Do NOT write Head of Product & Technology; it is Chief Product and "
    "Technology Officer.",
    "",
])
def test_allows_clean_content(content):
    assert _write("output/acme-corp/091026-magnuson.md", content) == 0


@pytest.mark.parametrize("path", [
    "memory/feedback_zuora_principal_title_is_cpto.md",
    "memory/canonical-sidecar/archive-2026-07.md",
    "/Users/x/.claude/projects/p/memory/index-outreach.md",
    "inbox/_archive-2026-06-backfill/20260218-t22-reaching-out.md",
    "data/networking.md",
    "data/job-todos.md",
    "coaching/pressure-points.md",
    "tests/scripts/test_check_zuora_principal_title.py",
    "tests/fixtures/cv/stale-title.md",
])
def test_exempt_paths_allow_the_stale_string(path):
    """Frozen records, the rule's own file, and fixtures stay writable."""
    assert _write(path, "Chief of Staff to the Head of Product & Technology") == 0


def test_non_exempt_sibling_of_an_exempt_file_still_blocks():
    """data/networking.md is exempt; data/profile.md is not."""
    assert _write("data/profile.md",
                  "Role: Chief of Staff to Head of Product and Technology") == 2


# --------------------------------------------------------------------------
# Other write surfaces
# --------------------------------------------------------------------------
def test_edit_blocks():
    assert _run({"tool_name": "Edit", "tool_input": {
        "file_path": "output/acme-corp/cover-letter.md",
        "old_string": "x",
        "new_string": "Chief of Staff to Zuora's Head of Product and Technology",
    }}) == 2


def test_edit_ignores_old_string():
    """Removing the stale title must not be blocked."""
    assert _run({"tool_name": "Edit", "tool_input": {
        "file_path": "output/acme-corp/cover-letter.md",
        "old_string": "Chief of Staff to Head of Product and Technology",
        "new_string": "Chief of Staff to the Chief Product and Technology Officer",
    }}) == 0


def test_multiedit_blocks():
    assert _run({"tool_name": "MultiEdit", "tool_input": {
        "file_path": "output/acme-corp/cover-letter.md",
        "edits": [
            {"old_string": "a", "new_string": "clean line"},
            {"old_string": "b",
             "new_string": "Chief of Staff to the Head of Product & Technology"},
        ],
    }}) == 2


# --------------------------------------------------------------------------
# Bash: heredoc / redirect writes are gated; reads and greps are not
# --------------------------------------------------------------------------
@pytest.mark.parametrize("command", [
    "cat > output/acme-corp/cv.md <<'EOF'\n"
    "*Chief of Staff to Head of Product and Technology*\nEOF",
    "echo 'position: Chief of Staff to the Head of Product and Technology' "
    ">> output/acme-corp/cv.yaml",
    "printf '%s\\n' \"Chief of Staff to Zuora's Head of Product & Technology\" "
    "| tee output/acme-corp/cv.md",
])
def test_bash_write_blocks(command):
    assert _bash(command) == 2


@pytest.mark.parametrize("command", [
    # the audit sweep you run to VERIFY this rule must stay clean
    "grep -rin 'head of product and technology' output/ data/",
    "/usr/bin/grep -rn \"Chief of Staff to the Head of Product\" .",
    "rg 'head of product & technology' --glob '*.md'",
    "ls output/otterbrook/",
    # writes with the canonical title
    "cat > output/acme-corp/cv.md <<'EOF'\n"
    "*Chief of Staff to the Chief Product and Technology Officer*\nEOF",
    # write to an exempt path
    "echo 'Chief of Staff to Head of Product and Technology' >> data/networking.md",
])
def test_bash_allows(command):
    assert _bash(command) == 0


# --------------------------------------------------------------------------
# Fail-open
# --------------------------------------------------------------------------
def test_malformed_json_fails_open():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, HOOK], input="not json{",
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0


def test_unknown_tool_fails_open():
    assert _run({"tool_name": "Read",
                 "tool_input": {"file_path": "output/x/y.md"}}) == 0


# --------------------------------------------------------------------------
# The BLOCK message itself. Exit 2 with no explanation is a hook that stops a
# write and tells nobody why — the reason the print() is load-bearing.
# --------------------------------------------------------------------------
def test_block_prints_the_offending_line_and_the_canonical_fix():
    # kills DROP_CALL on the print(...) in judge() (line 178)
    proc = _proc({"tool_name": "Write", "tool_input": {
        "file_path": "output/acme-corp/091026-magnuson.md",
        "content": "position: Chief of Staff to Head of Product and Technology",
    }})
    assert proc.returncode == 2
    err = proc.stderr
    assert "BLOCKED (check_zuora_principal_title.py)" in err
    assert "output/acme-corp/091026-magnuson.md" in err
    # the offending line is echoed back
    assert "position: Chief of Staff to Head of Product and Technology" in err
    # and the canonical title is named as the fix
    assert "Chief Product and Technology Officer" in err
    assert "memory/feedback_zuora_principal_title_is_cpto.md" in err


# --------------------------------------------------------------------------
# NotebookEdit is a fourth write surface and must be judged like the others.
# --------------------------------------------------------------------------
def test_notebook_edit_blocks():
    # kills IF_FALSE on `elif tool_name == "NotebookEdit"` (219) and
    # DROP_CALL on the judge(...) inside it (220)
    assert _run({"tool_name": "NotebookEdit", "tool_input": {
        "notebook_path": "output/acme-corp/analysis.ipynb",
        "new_source": "# Chief of Staff to the Head of Product and Technology",
    }}) == 2


def test_notebook_edit_allows_canonical_title():
    assert _run({"tool_name": "NotebookEdit", "tool_input": {
        "notebook_path": "output/acme-corp/analysis.ipynb",
        "new_source": "# Chief of Staff to the Chief Product and Technology Officer",
    }}) == 0


def test_notebook_edit_reads_new_source_not_the_path():
    """An exempt notebook path stays writable even with the stale string."""
    assert _run({"tool_name": "NotebookEdit", "tool_input": {
        "notebook_path": "memory/scratch.ipynb",
        "new_source": "Chief of Staff to the Head of Product and Technology",
    }}) == 0


# --------------------------------------------------------------------------
# Dispatch is exact-match on tool_name. A tool this hook is NOT registered for
# must not be routed through the Bash branch just because it carries a
# `command` field (MCP shells, SlashCommand, and friends all do).
# --------------------------------------------------------------------------
def test_unregistered_tool_with_a_command_field_is_not_judged_as_bash():
    # kills IF_TRUE on `elif tool_name == "Bash"` (221)
    assert _run({"tool_name": "mcp__shell__exec", "tool_input": {
        "command": "echo 'Chief of Staff to Head of Product and Technology' "
                   "> output/acme-corp/cv.md",
    }}) == 0


# --------------------------------------------------------------------------
# Sibling import. The Bash branch is only alive because the hook puts its own
# directory on sys.path before importing check_public_pii — it must not depend
# on the caller's PYTHONPATH or on Python adding the script dir for it.
# --------------------------------------------------------------------------
BASH_STALE_WRITE = {"tool_name": "Bash", "tool_input": {
    "command": "echo 'Chief of Staff to Head of Product and Technology' "
               "> output/acme-corp/cv.md",
}}


def _run_isolated(script: str, payload: dict) -> subprocess.CompletedProcess:
    """Run `script` with no PYTHONPATH and no implicit script-dir on sys.path."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONPATH", None)
    env["PYTHONSAFEPATH"] = "1"
    return subprocess.run(
        [sys.executable, script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def test_bash_branch_works_without_pythonpath():
    # kills DROP_CALL on sys.path.insert(...) (line 105): without it the
    # check_public_pii import fails, split_command_segments is None, and every
    # heredoc/redirect write silently stops being gated.
    proc = _run_isolated(HOOK, BASH_STALE_WRITE)
    assert proc.returncode == 2
    assert "BLOCKED (check_zuora_principal_title.py)" in proc.stderr


def test_missing_sibling_degrades_silently(tmp_path):
    # kills IF_FALSE on `if not command or split_command_segments is None:` (223):
    # with the guard gone, a missing sibling makes split_command_segments(None-call)
    # raise, and the outer handler prints "error (allowing through)" on every Bash
    # call. Degrading must be silent, not noise on every command.
    solo = tmp_path / "check_zuora_principal_title.py"
    shutil.copy(HOOK, solo)
    proc = _run_isolated(str(solo), BASH_STALE_WRITE)
    assert proc.returncode == 0
    assert proc.stderr == ""


# --------------------------------------------------------------------------
# Importing the hook must be inert: it is a module other tools can import for
# is_exempt/offending_lines, and importing it must not consume stdin or exit.
# --------------------------------------------------------------------------
def test_import_does_not_run_main():
    # kills IF_TRUE on `if __name__ == "__main__":` (line 232)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = REPO_TOOLS + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", "import check_zuora_principal_title"],
        input=json.dumps({"tool_name": "Write", "tool_input": {
            "file_path": "output/acme-corp/cv.md",
            "content": "Chief of Staff to Head of Product and Technology",
        }}),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0
    assert "BLOCKED" not in proc.stderr


# --------------------------------------------------------------------------
# Fail-open on an internal error must SAY SO on stderr. A hook that swallows its
# own crash silently is indistinguishable from a hook that ran and passed.
# --------------------------------------------------------------------------
def test_internal_error_fails_open_loudly():
    # kills DROP_CALL on the print(...) in the module-level except (line 238)
    proc = _proc({"tool_name": "Write", "tool_input": ["not-a-dict"]})
    assert proc.returncode == 0
    assert "check_zuora_principal_title.py error (allowing through)" in proc.stderr


# --------------------------------------------------------------------------
# is_exempt as a unit: the test_-prefixed basename rule, and the bool contract.
# --------------------------------------------------------------------------
def _load_module():
    spec = importlib.util.spec_from_file_location("_zuora_hook_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, REPO_TOOLS)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(REPO_TOOLS)
    return mod


def test_test_prefixed_file_outside_tests_dir_is_exempt():
    # kills IF_FALSE on `if os.path.basename(p).startswith("test_")` (155) and
    # RETURN_NONE on its `return True` (156): a fixture named test_*.md living
    # outside tests/ would start being blocked.
    assert _write("output/acme-corp/test_stale_title_fixture.md",
                  "Chief of Staff to the Head of Product & Technology") == 0


def test_is_exempt_returns_real_booleans():
    # kills RETURN_NONE on `return False` at 148 (empty path) and at 157 (fallthrough)
    mod = _load_module()
    assert mod.is_exempt("") is False
    assert mod.is_exempt(None) is False
    assert mod.is_exempt("output/acme-corp/091026-magnuson.md") is False
    assert mod.is_exempt("memory/index-outreach.md") is True
    assert mod.is_exempt("data/networking.md") is True
    assert mod.is_exempt("output/acme-corp/test_fixture.md") is True
