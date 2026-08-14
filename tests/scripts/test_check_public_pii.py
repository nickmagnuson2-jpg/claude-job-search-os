"""Tests for tools/check_public_pii.py — the public-repo PII PreToolUse hook.

The hook blocks (exit 2) when a denylisted real name / company appears in a public
artifact (tests/, .claude/skills/, framework/, docs/, tools/*.py, top-level *.md),
and stays clean (exit 0) for generic placeholders, private paths (data/**), out-of-
scope paths, and tokens that only appear as substrings of larger words.

The denylist is a per-test fixture so these tests never depend on (or contain) real
PII themselves.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_public_pii.py"

# Fixture denylist — INVENTED tokens standing in for the gitignored production list.
# Deliberately synthetic (no real person/company) so this public test leaks nothing.
FIXTURE_DENYLIST = "Zorptech\nMirvelo\nPat Zorp\nRobin Mirvel\n"


def _run(tmp_path, file_path, content, tool_name="Write", with_denylist=True, cwd=None):
    """Run the hook against a fixture repo at tmp_path. The repo root is injected via
    PII_REPO_ROOT (the production seam that replaced the old cwd() derivation, fable-audit
    #3), so `cwd` can be pointed anywhere to prove root no longer follows the process cwd."""
    if with_denylist:
        dl = tmp_path / "tools" / ".pii-denylist.txt"
        dl.parent.mkdir(parents=True, exist_ok=True)
        dl.write_text("# fixture\n" + FIXTURE_DENYLIST, encoding="utf-8")

    if tool_name == "Edit":
        tool_input = {"file_path": file_path, "new_string": content}
    else:
        tool_input = {"file_path": file_path, "content": content}

    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    env = {**os.environ, "PII_REPO_ROOT": str(tmp_path)}
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True,
                       cwd=str(cwd) if cwd else str(tmp_path), env=env)
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


def test_root_independent_of_cwd(tmp_path, tmp_path_factory):
    """Regression for fable-audit 2026-07-07 #3: repo root must come from
    PII_REPO_ROOT/__file__, NOT the process cwd. Simulate a session launched
    OUTSIDE the repo — cwd is an unrelated dir and the edited file is passed as an
    ABSOLUTE path inside the fixture repo (as Claude passes them). Under the old
    root=Path.cwd() logic this file resolved as '..'-prefixed and the guard was
    silently skipped; now it must still block."""
    elsewhere = tmp_path_factory.mktemp("outside_repo")
    abs_file = tmp_path / "tests" / "scripts" / "test_x.py"
    code, err = _run(tmp_path, str(abs_file), "assert 'Zorptech' in result", cwd=elsewhere)
    assert code == 2
    assert "Zorptech" in err


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


# --- examples/ + plugins/ scope + binary skip (fable-audit #10/#17/#18) ---------

def test_blocks_in_examples_dir(tmp_path):
    # examples/ is tracked public surface — must be in scope now.
    code, err = _run(tmp_path, "examples/data/sample.md", "Contact: Pat Zorp")
    assert code == 2
    assert "Pat Zorp" in err


def test_blocks_in_plugins_dir(tmp_path):
    code, _ = _run(tmp_path, "plugins/README.md", "e.g. Zorptech integration")
    assert code == 2


def test_skips_binary_pdf_in_examples(tmp_path):
    # A PDF's byte stream can contain a short denylist brand-token as a substring;
    # binary files must never be text-scanned or every push would be blocked.
    code, _ = _run(tmp_path, "examples/output/sample-cv.pdf", "Zorptech Pat Zorp")
    assert code == 0


# --- direct unit checks of the new predicates -----------------------------------

sys.path.insert(0, str(SCRIPT.parent))
import check_public_pii as cpp  # noqa: E402


def test_is_public_path_covers_examples_and_plugins():
    assert cpp.is_public_path("examples/data/x.md")
    assert cpp.is_public_path("plugins/README.md")


def test_is_public_path_covers_claude_workflows():
    """Regression, 2026-08-13.

    .claude/workflows/ is tracked and public and carried three .js files that the
    always-on hook had NEVER scanned, because the directory was missing from
    PUBLIC_PREFIXES. Anything committed there was ungated.

    The gap surfaced only because the tool refuses to report a clean sweep over zero
    files. A scanner that answered "clean" for an empty scope would have concealed it
    indefinitely -- which is why that refusal is a feature and must stay.
    """
    assert cpp.is_public_path(".claude/workflows/plan-hardening.js")
    assert cpp.is_public_path(".claude/workflows/anything.js")
    # sibling public surface must not regress
    assert cpp.is_public_path(".claude/skills/foo/SKILL.md")


def test_empty_scope_is_never_reported_as_clean():
    """An empty sweep is an error, not a pass.

    This is the property that exposed the workflows gap above. If a future change
    makes a zero-file scan return clean, coverage holes become invisible again.
    """
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--scan",
         "data/definitely-gitignored-not-public.md"],
        capture_output=True, text=True, cwd=str(SCRIPT.parents[1]),
    )
    payload = json.loads(r.stdout)
    assert payload.get("clean") is not True, payload
    assert payload.get("scanned") == 0
    assert "error" in payload


def test_is_binary_flags_pdf_and_images_not_text():
    assert cpp.is_binary("examples/output/sample-cv.pdf")
    assert cpp.is_binary("docs/diagram.png")
    assert not cpp.is_binary("examples/data/notes.md")
    assert not cpp.is_binary("tools/foo.py")
