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


# ---------------------------------------------------------------------------
# Public-path predicate: the allowlist is a fast path, gitignore is the authority.
# Two fires of the same defect, both regression-guarded here.
#   2026-08-13: .claude/workflows/*.js tracked+public but absent from PUBLIC_PREFIXES
#   2026-08-18: .claude/settings.json (tracked) and a stray .bak beside it (untracked,
#               NOT gitignored) both reported "not a public artifact"
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import check_public_pii as cpp  # noqa: E402


def _repo(tmp_path):
    """A throwaway git repo whose .gitignore mirrors the real one's shape."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("data/\noutput/\nmemory/\nscratchpad/\n", encoding="utf-8")
    for d in ("data", "output", ".claude", ".claude/workflows", "tools", "docs"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_allowlisted_prefixes_still_public_without_git(tmp_path):
    """The fast path must not depend on git being answerable."""
    for rel in ("tests/x.py", ".claude/skills/s/SKILL.md", "framework/f.md",
                "docs/d.md", "tools/t.py", "CLAUDE.md"):
        assert cpp.is_public_path(rel) is True, rel


def test_data_is_never_public_even_if_git_says_otherwise(tmp_path):
    root = _repo(tmp_path)
    assert cpp.is_public_path("data/goals.md", root) is False


def test_gitignored_trees_are_not_public(tmp_path):
    root = _repo(tmp_path)
    for rel in ("output/analysis/x.md", "memory/feedback_x.md", "scratchpad/tmp.py"):
        assert cpp.is_public_path(rel, root) is False, rel


def test_regression_2026_08_18_claude_settings_json_is_public(tmp_path):
    """settings.json is tracked and ships publicly; the allowlist did not cover it."""
    root = _repo(tmp_path)
    (root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    assert cpp.is_public_path(".claude/settings.json", root) is True


def test_regression_2026_08_18_stray_untracked_backup_is_public(tmp_path):
    """An untracked file that is NOT gitignored is one `git add -A` from shipping."""
    root = _repo(tmp_path)
    (root / ".claude" / "settings.json.bak-081726").write_text("{}", encoding="utf-8")
    assert cpp.is_public_path(".claude/settings.json.bak-081726", root) is True


def test_regression_2026_08_13_claude_workflows_is_public(tmp_path):
    root = _repo(tmp_path)
    assert cpp.is_public_path(".claude/workflows/plan-hardening.js", root) is True


def test_unknown_git_state_does_not_widen_the_surface(tmp_path):
    """No git repo -> the fallback must NOT claim an unlisted path is public.

    Fail-open here is deliberate: this hook BLOCKs at exit 2, so a fallback that
    said 'public' whenever git could not answer would block every write in any
    directory git cannot resolve.
    """
    not_a_repo = tmp_path / "bare"
    not_a_repo.mkdir()
    assert cpp.git_ignore_state(not_a_repo, "whatever.json") == "unknown"
    assert cpp.is_public_path("whatever.json", not_a_repo) is False


def test_git_ignore_state_distinguishes_all_three(tmp_path):
    root = _repo(tmp_path)
    assert cpp.git_ignore_state(root, "data/goals.md") == "ignored"
    assert cpp.git_ignore_state(root, "docs/readme.md") == "not-ignored"
    assert cpp.git_ignore_state(tmp_path / "nope", "x.md") == "unknown"


# ===========================================================================
# Bash branch (added 2026-08-18)
#
# Until this date the hook was wired on `Write|Edit` only, so `cat > docs/x.md
# <<'EOF'` -- the most common way an agent writes a file -- reached the public
# repo unscanned. These cover the write-target parser and the end-to-end block.
#
# Note the inverted FP discipline vs a normal command hook: we do NOT strip
# quoted spans or heredoc bodies, because the heredoc body is exactly the
# content about to land in a public file.
# ===========================================================================

extract_write_targets = cpp.extract_write_targets  # module imported above (line ~160)


def _run_bash(tmp_path, command, with_denylist=True, cwd=None):
    """Run the hook against a Bash payload in a fixture repo at tmp_path."""
    if with_denylist:
        dl = tmp_path / "tools" / ".pii-denylist.txt"
        dl.parent.mkdir(parents=True, exist_ok=True)
        dl.write_text("# fixture\n" + FIXTURE_DENYLIST, encoding="utf-8")

    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    env = {**os.environ, "PII_REPO_ROOT": str(tmp_path)}
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True,
                       cwd=str(cwd) if cwd else str(tmp_path), env=env)
    return r.returncode, r.stderr


# --- should BLOCK (exit 2) --------------------------------------------------

def test_bash_heredoc_to_public_doc_blocks(tmp_path):
    cmd = "cat > docs/notes.md <<'EOF'\nMet with Pat Zorp today.\nEOF"
    code, err = _run_bash(tmp_path, cmd)
    assert code == 2
    assert "Pat Zorp" in err


def test_bash_append_redirect_to_top_level_md_blocks(tmp_path):
    code, err = _run_bash(tmp_path, 'echo "Zorptech is hiring" >> README.md')
    assert code == 2
    assert "Zorptech" in err


def test_bash_tee_to_tests_blocks(tmp_path):
    code, _ = _run_bash(tmp_path, 'echo "Robin Mirvel" | tee tests/fixture.py')
    assert code == 2


def test_bash_tee_append_flag_blocks(tmp_path):
    code, _ = _run_bash(tmp_path, 'echo "Robin Mirvel" | tee -a tests/fixture.py')
    assert code == 2


def test_bash_sed_inplace_macos_form_blocks(tmp_path):
    code, _ = _run_bash(tmp_path, "sed -i '' 's/placeholder/Mirvelo/' framework/style.md")
    assert code == 2


def test_bash_numbered_fd_redirect_blocks(tmp_path):
    code, _ = _run_bash(tmp_path, 'echo "Zorptech" 1> docs/out.md')
    assert code == 2


# --- should ALLOW (exit 0) --------------------------------------------------

def test_bash_write_to_private_data_allowed(tmp_path):
    code, _ = _run_bash(tmp_path, "cat > data/networking.md <<'EOF'\nPat Zorp\nEOF")
    assert code == 0


def test_bash_dev_null_is_not_a_file_target(tmp_path):
    code, _ = _run_bash(tmp_path, 'echo "Pat Zorp" > /dev/null')
    assert code == 0


def test_bash_stderr_dup_is_not_a_file_target(tmp_path):
    """`2>&1` duplicates a descriptor; it must never be read as a filename."""
    code, _ = _run_bash(tmp_path, 'echo "Pat Zorp" 2>&1')
    assert code == 0


def test_bash_read_only_command_with_token_allowed(tmp_path):
    """A grep FOR a real name is legitimate work -- no write, no block."""
    code, _ = _run_bash(tmp_path, 'grep -r "Pat Zorp" data/')
    assert code == 0


def test_bash_target_outside_repo_allowed(tmp_path):
    code, _ = _run_bash(tmp_path, 'echo "Pat Zorp" > /tmp/scratch.md')
    assert code == 0


def test_bash_text_written_to_a_binary_extension_still_blocks(tmp_path):
    """CONTRACT REVERSED 2026-08-19. The binary skip exists because a PDF's BYTE STREAM
    false-positives on short tokens. On the Bash path the scanned text is the COMMAND,
    not the file, so the extension is irrelevant -- and skipping let a plain-text real
    name be written into a tracked `.pdf` unchecked. The Write/Edit skip below is
    unchanged, because there the payload really is the file's bytes."""
    code, _ = _run_bash(tmp_path, 'echo "Pat Zorp" > docs/report.pdf')
    assert code == 2


def test_write_tool_to_binary_path_is_still_skipped(tmp_path):
    """The original false-positive protection must survive the reversal above."""
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    code, _ = _run(tmp_path, str(tmp_path / "docs" / "report.pdf"), "Pat Zorp")
    assert code == 0


def test_bash_no_denylist_fails_open(tmp_path):
    code, _ = _run_bash(tmp_path, "cat > docs/x.md <<'EOF'\nPat Zorp\nEOF",
                        with_denylist=False)
    assert code == 0


def test_bash_clean_content_to_public_path_allowed(tmp_path):
    code, _ = _run_bash(tmp_path, "cat > docs/x.md <<'EOF'\nCasey Doe at Acme\nEOF")
    assert code == 0


# --- extract_write_targets unit tests ---------------------------------------

def test_extract_simple_redirect():
    assert extract_write_targets("echo hi > docs/a.md") == ["docs/a.md"]


def test_extract_append_and_numbered_fd():
    assert extract_write_targets("echo hi >> docs/a.md") == ["docs/a.md"]
    assert extract_write_targets("echo hi 2> docs/err.log") == ["docs/err.log"]


def test_extract_skips_descriptor_dup():
    assert extract_write_targets("cmd 2>&1") == []


def test_extract_skips_dev_null():
    assert extract_write_targets("cmd > /dev/null") == []


def test_extract_quoted_target_is_unquoted():
    assert extract_write_targets('echo hi > "docs/my file.md"') == ["docs/my file.md"]


def test_extract_tee_variants():
    assert extract_write_targets("echo hi | tee docs/a.md") == ["docs/a.md"]
    assert extract_write_targets("echo hi | tee -a docs/a.md") == ["docs/a.md"]


def test_extract_dd_of():
    assert extract_write_targets("dd if=/dev/zero of=docs/blob.bin") == ["docs/blob.bin"]


def test_extract_sed_inplace_both_forms():
    assert extract_write_targets("sed -i 's/a/b/' docs/a.md") == ["docs/a.md"]
    assert extract_write_targets("sed -i '' 's/a/b/' docs/a.md") == ["docs/a.md"]


def test_extract_sed_inplace_quoted_expression_with_spaces_is_one_token():
    """2026-09-28 false positive: the expression was split on whitespace, so words
    inside it ('operating', "Lake's") became phantom public targets and a write to a
    gitignored output/ file was blocked. Only the bare file token is a target."""
    cmd = """sed -i '' "s/the owner's operating partner/Acme's operating partner/" output/x/cv.yaml"""
    assert extract_write_targets(cmd) == ["output/x/cv.yaml"]
    cmd = "sed -i 's/a b c/d e/' docs/a.md docs/b.md"
    assert extract_write_targets(cmd) == ["docs/a.md", "docs/b.md"]
    cmd = """sed -i.bak -e 's/x y/z/' -e "s/p q/r/" docs/a.md"""
    assert extract_write_targets(cmd) == ["docs/a.md"]
    # a BARE -e expression is the script, not a file
    assert extract_write_targets("sed -i -e s/a/b/ docs/a.md") == ["docs/a.md"]
    assert extract_write_targets("sed -i -f edits.sed docs/a.md") == ["docs/a.md"]
    # flags before -i are not targets either
    assert extract_write_targets("sed -E -i 's/a/b/' docs/a.md") == ["docs/a.md"]


@pytest.mark.parametrize("cmd", [
    # adjacent quoted + bare spans are ONE shell word: the expression is "s/a/-e"
    "sed -i 's/a/'-e docs/public.md",
    # after --, "-e" is a filename, not an option
    "sed -i 's/a/b/' -- -e docs/public.md",
    # '|' inside a quoted expression is not a pipeline
    "sed -i 's/a|b/c/' docs/public.md",
    "sed -i 's/a;b/c/' docs/public.md",
    # long-form in-place flag
    "sed --in-place 's/a/b/' docs/public.md",
    "sed --in-place=.bak 's/a/b/' docs/public.md",
    # options after the operands (GNU permutes)
    "sed 's/a/b/' docs/public.md -i",
    # clustered short options
    "sed -Ei 's/a/b/' docs/public.md",
    "sed -ne 's/a/b/p' -i docs/public.md",
])
def test_extract_sed_inplace_never_drops_the_public_file(cmd):
    """Codex review of c0a2a61 (2026-09-28): the -e/-f skip and a quote-unaware
    command regex each dropped a real public target. False negatives here leak."""
    assert "docs/public.md" in extract_write_targets(cmd)


def test_extract_sed_inplace_escaped_quotes_stay_in_the_expression():
    cmd = 'sed -i "s/a/\\"hello world\\"/" docs/a.md'
    assert extract_write_targets(cmd) == ["docs/a.md"]


@pytest.mark.parametrize("cmd,expected", [
    # script given by option: EVERY operand is a file
    ("sed --expression 's/a/b/' --in-place docs/a.md docs/b.md", ["docs/a.md", "docs/b.md"]),
    ("sed --expression='s/a/b/' -i docs/a.md docs/b.md", ["docs/a.md", "docs/b.md"]),
    ("sed --file edits.sed -i docs/a.md", ["docs/a.md"]),
    ("sed -fedits.sed -i docs/a.md", ["docs/a.md"]),
    ("sed -es/a/b/ -i docs/a.md", ["docs/a.md"]),
    # options that take an argument must consume it, not turn it into the script
    ("sed -l 5 -i 's/a/b/' docs/a.md", ["docs/a.md"]),
    ("sed -l5 -i 's/a/b/' docs/a.md", ["docs/a.md"]),
    ("sed --line-length 5 -i 's/a/b/' docs/a.md", ["docs/a.md"]),
    ("sed --line-length=5 -i 's/a/b/' docs/a.md", ["docs/a.md"]),
    # a long option containing the letter i is not -i
    ("sed --posix 's/a/b/' docs/a.md", []),
    ("sed --debug -E 's/a/b/' docs/a.md", []),
])
def test_extract_sed_option_forms(cmd, expected):
    assert extract_write_targets(cmd) == expected


def test_sed_unbalanced_quote_is_not_a_write():
    """bash refuses to run a line with an unclosed quote ("unexpected EOF"), so it
    writes nothing. The tokenizer reads the rest of the line as one word, which
    leaves sed with no file operand. (The earlier over-extracting fallback existed
    only because shlex raised on this input.)"""
    assert extract_write_targets("sed -i 's/a/b docs/public.md") == []


@pytest.mark.parametrize("args,dialect,expected", [
    # BSD: -l is a flag, so -i after it is still in-place (Codex c6c6ce5 F1)
    ("-l -i '' 's/a/b/' docs/public.md", "bsd", ["docs/public.md"]),
    # BSD: -i always takes the next word as the suffix, empty or not (F2)
    ("-i '.bak' 's/a b/c/' docs/public.md", "bsd", ["docs/public.md"]),
    ("-i '' 's/a b/c/' docs/public.md", "bsd", ["docs/public.md"]),
    ("-I '.bak' 's/a/b/' docs/public.md", "bsd", ["docs/public.md"]),
    ("-Ei '' 's/a/b/' docs/public.md", "bsd", ["docs/public.md"]),
    # GNU: -l takes an argument; -i takes no separate word, except an empty one
    ("-l 5 -i 's/a/b/' docs/public.md", "gnu", ["docs/public.md"]),
    ("-i 's/a/b/' docs/public.md", "gnu", ["docs/public.md"]),
    ("-i '' 's/a/b/' docs/public.md", "gnu", ["docs/public.md"]),
    ("-Ei 's/a/b/' docs/public.md", "gnu", ["docs/public.md"]),
    ("-I 's/a/b/' docs/public.md", "gnu", []),
])
def test_sed_dialect_rules(args, dialect, expected):
    import shlex
    from check_public_pii import _parse_sed_args
    assert _parse_sed_args(shlex.split(args), dialect) == expected


@pytest.mark.parametrize("cmd,expected", [
    # each file is written under only ONE dialect's reading; the union must keep it
    ("sed -l -i '' 's/a/b/' docs/public.md", ["docs/public.md"]),               # BSD only
    ("sed -i 's/a b/c/' docs/a.md docs/public.md", ["docs/a.md", "docs/public.md"]),  # GNU keeps a.md
    # path-qualified sed is still sed (Codex HEAD review F1, P0)
    ("/usr/bin/sed -i '' 's/a/b/' docs/public.md", ["docs/public.md"]),
    ("./bin/sed -i 's/a/b/' docs/public.md", ["docs/public.md"]),
    ("echo x; /opt/homebrew/bin/sed -i '' 's/a/b/' docs/public.md", ["docs/public.md"]),
    # redirections are not sed operands (F2)
    ("sed -i '' 's/a/b/' docs/public.md 2>/dev/null", ["docs/public.md"]),
    ("sed -i '' 's/a/b/' docs/public.md < input.txt", ["docs/public.md"]),
    # err.log IS a write target, found by the redirect parser, not by sed's
    ("sed -i '' 's/a/b/' docs/public.md 2> err.log", ["err.log", "docs/public.md"]),
    # a redirection in the MIDDLE must drop exactly operator + target, no more
    ("sed -i '' 's/a/b/' < input.txt docs/public.md", ["docs/public.md"]),
    ("sed -i '' 's/a/b/' 2>/dev/null docs/public.md", ["docs/public.md"]),
    # GNU accepts unique long-option abbreviations
    ("sed --fi=edits.sed -i docs/a.md docs/public.md", ["docs/a.md", "docs/public.md"]),
    ("sed --in 's/a/b/' docs/public.md", ["docs/public.md"]),
    ("sed --expr='s/a/b/' --in-pl docs/a.md docs/public.md", ["docs/a.md", "docs/public.md"]),
])
def test_sed_command_forms(cmd, expected):
    assert extract_write_targets(cmd) == expected


@pytest.mark.parametrize("cmd", [
    "echo x | /usr/bin/tee docs/public.md",
    "echo x | ./tee -a docs/public.md",
    "/bin/dd if=a of=docs/public.md",
])
def test_path_qualified_writers_are_recognised(cmd):
    """Same prefix assumption as sed (Codex HEAD review F1): a path before the
    command name hid tee and dd too."""
    assert extract_write_targets(cmd) == ["docs/public.md"]


def test_writers_not_matched_inside_a_longer_word():
    assert extract_write_targets("echo x | mytee docs/public.md") == []
    assert extract_write_targets("add if=a of=docs/public.md") == []


@pytest.mark.parametrize("cmd,expected", [
    # Codex review of 2af4a29, F1 (P0): a descriptor redirect before the operand
    ("sed -i 's/a/b/' 2>&1 docs/public.md", ["docs/public.md"]),
    ("sed -i 's/a/b/' 3<&0 docs/public.md", ["docs/public.md"]),
    ("echo x | tee 2>&1 docs/public.md", ["docs/public.md"]),
    ("dd 2>&1 if=a of=docs/public.md", ["docs/public.md"]),
    ("echo x | tee &>err.log docs/public.md", ["err.log", "docs/public.md"]),
    # F2 (P1): quoted or escaped executable path
    (""""/usr/bin/sed" -i 's/a/b/' docs/public.md""", ["docs/public.md"]),
    ("""echo x | "/usr/bin/tee" docs/public.md""", ["docs/public.md"]),
    ("""'/bin/dd' if=a of=docs/public.md""", ["docs/public.md"]),
    ("""\\sed -i '' 's/a/b/' docs/public.md""", ["docs/public.md"]),
    # writes inside a shell string are still writes
    ('bash -c "sed -i \'\' \'s/a/b/\' docs/public.md"', ["docs/public.md"]),
    ("""sh -lc 'echo x > docs/public.md'""", ["docs/public.md"]),
    ('eval "echo x | tee docs/public.md"', ["docs/public.md"]),
    # prefixes and wrappers
    ("LC_ALL=C sed -i '' 's/a/b/' docs/public.md", ["docs/public.md"]),
    ("sudo sed -i '' 's/a/b/' docs/public.md", ["docs/public.md"]),
    ("xargs sed -i '' 's/a/b/' docs/public.md", ["docs/public.md"]),
    # bash >& FILE writes both streams to FILE; >&N and >&- are descriptor moves
    ("echo hi >& docs/public.md", ["docs/public.md"]),
    ("echo hi >&2", []),
    ("echo hi 2>&-", []),
    ("echo hi <> docs/public.md", ["docs/public.md"]),
    # reads are not writes
    ("sed -n p < docs/public.md", []),
    ("cat <<< docs/public.md", []),
])
def test_writer_extraction_shell_forms(cmd, expected):
    assert extract_write_targets(cmd) == expected


@pytest.mark.parametrize("cmd,expected", [
    # a QUOTED writer counts only in command position, which is found past
    # assignments and wrappers (and their options)
    ("""A=1 "/usr/bin/sed" -i '' 's/a/b/' docs/p.md""", ["docs/p.md"]),
    ("""sudo -n "/usr/bin/tee" docs/p.md""", ["docs/p.md"]),
    ("""env A=1 "tee" docs/p.md""", ["docs/p.md"]),
    ("""timeout 5 "tee" docs/p.md""", ["docs/p.md"]),
    ("""A=1 B=2 "tee" docs/p.md""", ["docs/p.md"]),
    ("""sudo env A=1 "tee" docs/p.md""", ["docs/p.md"]),
    # ...and a quoted writer name as an ARGUMENT is just text
    ("""echo "tee" docs/p.md""", []),
    ("""grep -n "sed" docs/p.md""", []),
    # tee: after --, a dash-word is a file
    ("echo x | tee -- -a docs/p.md", ["-a", "docs/p.md"]),
    # a shell runs a command string only with -c (alone or in a cluster)
    ("""bash -x 'echo x > docs/p.md'""", []),
    ("""bash -xc 'echo x > docs/p.md'""", ["docs/p.md"]),
    ("bash -c", []),
])
def test_writer_extraction_command_position(cmd, expected):
    assert extract_write_targets(cmd) == expected


@pytest.mark.parametrize("cmd,expected", [
    # Codex review of fd12aa3, F1 (P0): writers inside command substitution run
    ("x=$(tee docs/public.md)", ["docs/public.md"]),
    ("x=$(sed -i 's/a/b/' docs/public.md)", ["docs/public.md"]),
    ("x=$(dd if=/dev/null of=docs/public.md)", ["docs/public.md"]),
    ("x=`tee docs/public.md`", ["docs/public.md"]),
    ('echo "$(echo x > docs/public.md)"', ["docs/public.md"]),
    ("echo $(echo $(tee docs/public.md))", ["docs/public.md"]),
    # F2 (P0): -c -- COMMAND, and options that take a value before it
    ("sh -c -- 'echo x > docs/public.md'", ["docs/public.md"]),
    ("bash -c -- 'tee docs/public.md'", ["docs/public.md"]),
    ("bash -o pipefail -c 'tee docs/public.md'", ["docs/public.md"]),
    ("bash -c -e 'tee docs/public.md'", ["docs/public.md"]),
    ("bash --norc -c 'tee docs/public.md'", ["docs/public.md"]),
    # after --, the command string may itself start with dashes
    ("bash -c -- '--x; tee docs/public.md'", ["docs/public.md"]),
    # nesting deeper than any fixed cap is still scanned
    ("echo $(echo $(echo $(echo $(echo $(tee docs/public.md)))))", ["docs/public.md"]),
    # F3 (P0): wrapper options with values; NAME+=value
    ('timeout 5s "tee" docs/public.md', ["docs/public.md"]),
    ('sudo -u nobody "/usr/bin/tee" docs/public.md', ["docs/public.md"]),
    ('env -u X "tee" docs/public.md', ["docs/public.md"]),
    ('stdbuf -o L "tee" docs/public.md', ["docs/public.md"]),
    ('PATH+=:/tmp "/usr/bin/tee" docs/public.md', ["docs/public.md"]),
    # F4 (P1): digits before &> are an argument, not a descriptor
    ("echo x | tee 2&>err.log", ["err.log", "2"]),
])
def test_writer_extraction_nested_and_wrapped(cmd, expected):
    assert extract_write_targets(cmd) == expected


@pytest.mark.parametrize("cmd,expected", [
    # eval / sh -c run their string only where the SHELL runs them. An argument that
    # happens to be the word "eval" is not eval (real-data replay, 2026-09-30:
    # `agent-browser eval "x=>y"` read JavaScript arrows as redirects).
    ("agent-browser eval \"new Promise(r=>setTimeout(()=>r('ok'),1200))\"", []),
    ("grep -n bash -c 'echo x > docs/p.md'", []),
    ("sudo sh -c 'echo x > docs/p.md'", ["docs/p.md"]),
    ("find . -name x -exec sh -c 'tee docs/p.md' \;", ["docs/p.md"]),
    ("find . -exec \"tee\" docs/p.md \;", ["docs/p.md", ";"]),
])
def test_nested_command_strings_only_where_the_shell_runs_them(cmd, expected):
    assert extract_write_targets(cmd) == expected


def test_sed_not_matched_inside_a_longer_word():
    assert extract_write_targets("unsed -i 's/a/b/' docs/public.md") == []
    assert extract_write_targets("mysed -i 's/a/b/' docs/public.md") == []


def test_sed_common_macos_form_has_no_phantom():
    """`-i ''` is the everyday macOS form; neither reading may turn the
    expression into a target (the original false positive, 2026-09-28)."""
    from check_public_pii import _sed_inplace_files
    assert _sed_inplace_files("-i '' 's/a b/Real Name/' output/x.md") == ["output/x.md"]


def test_sed_quote_opened_before_the_filename():
    """Codex c6c6ce5 F3's input. The quote never closes, so bash would not run it;
    the tokenizer still keeps the text as a word rather than dropping it."""
    from check_public_pii import _sed_inplace_files
    assert "docs/public.md" in _sed_inplace_files("-i s/a/b/ 'docs/public.md")


def test_extract_sed_without_inplace_writes_nothing():
    assert extract_write_targets("sed 's/a/b/' docs/a.md") == []
    assert extract_write_targets("sed -n -e 's/a/b/p' docs/a.md") == []


def test_extract_skips_unresolvable_variable_target():
    assert extract_write_targets('echo hi > "$OUT"') == []
    assert extract_write_targets("echo hi > $OUT") == []


def test_extract_documents_cp_mv_gap():
    """cp/mv are a KNOWN, documented gap: the bytes they move are not in the
    command string, so there is nothing for a text scan to match. Asserted so the
    gap stays deliberate rather than drifting into an accidental regression."""
    assert extract_write_targets("cp data/secret.md docs/public.md") == []
    assert extract_write_targets("mv data/secret.md docs/public.md") == []


def test_extract_multiple_targets_in_one_command():
    got = extract_write_targets("echo a > docs/a.md; echo b >> docs/b.md")
    assert got == ["docs/a.md", "docs/b.md"]


# ===========================================================================
# Per-segment evaluation (added 2026-08-18, from a live-smoke false positive)
#
# The first live smoke of the Bash branch blocked a compound command that wrote
# CLEAN content to docs/ in one segment and a denylisted token to a PRIVATE path
# in another. Scanning the command as one string conflated the two. Compound
# commands are the normal working shape here, so that FP would have made the
# hook unusable -- and an unusable guard gets disabled, which is strictly worse
# than no guard because it still reads as protection.
# ===========================================================================

split_command_segments = cpp.split_command_segments


def test_compound_clean_public_write_plus_private_token_write_allowed(tmp_path):
    """THE REGRESSION TEST for the 2026-08-18 live-smoke FP."""
    cmd = ("cat > docs/clean.md <<'EOF'\nCasey Doe at Acme.\nEOF\n"
           "cat > data/private.md <<'EOF'\nPat Zorp notes.\nEOF")
    code, err = _run_bash(tmp_path, cmd)
    assert code == 0, err


def test_compound_token_reaching_public_still_blocks(tmp_path):
    """The mirror image must still fire -- the fix must not disarm detection."""
    cmd = ("cat > data/private.md <<'EOF'\nCasey Doe.\nEOF\n"
           "cat > docs/leak.md <<'EOF'\nPat Zorp notes.\nEOF")
    code, _ = _run_bash(tmp_path, cmd)
    assert code == 2


def test_and_chained_private_token_then_public_clean_allowed(tmp_path):
    cmd = 'echo "Pat Zorp" > data/n.md && echo "Casey Doe" > docs/n.md'
    code, _ = _run_bash(tmp_path, cmd)
    assert code == 0


def test_pipe_is_not_split_so_tee_leak_still_caught(tmp_path):
    """`echo BODY | tee docs/a.md` puts content and target on opposite sides of the
    pipe. Splitting there would hide the leak, so `|` must NOT be a segment break."""
    code, _ = _run_bash(tmp_path, 'echo "Pat Zorp" | tee docs/a.md')
    assert code == 2


def test_heredoc_body_containing_semicolons_stays_with_its_redirect(tmp_path):
    cmd = "cat > docs/x.md <<'EOF'\nline one; line two;\nPat Zorp\nEOF"
    code, _ = _run_bash(tmp_path, cmd)
    assert code == 2


# --- split_command_segments unit tests --------------------------------------


@pytest.mark.parametrize("cmd", [
    # an escaped quote does not end a double-quoted string (real-data replay,
    # 2026-09-30: the split put a name and its public target in different segments,
    # so neither segment had both and the write was allowed)
    'echo "a \\"x; y\\"" > docs/f.md',
    'echo a\\; b > docs/f.md',
    'echo `a; b` > docs/f.md',
    'echo "`a; b`" > docs/f.md',
])
def test_split_does_not_cut_inside_escapes_or_backticks(cmd):
    assert len(split_command_segments(cmd)) == 1


def test_escaped_quote_leak_to_public_file_STILL_BLOCKS(tmp_path):
    code, err = _run_bash(tmp_path, 'echo "Pat Zorp \\"x; y\\"" > docs/notes.md')
    assert code == 2
    assert "Pat Zorp" in err

def test_split_keeps_escape_text_exactly():
    assert split_command_segments('echo a\; b > f') == ['echo a\; b > f']
    assert split_command_segments('echo "a \\"x; y\\"" > f') == ['echo "a \\"x; y\\"" > f']


@pytest.mark.parametrize("cmd,expected", [
    ("{ echo a; echo b; } > f; echo c", ["{ echo a; echo b; } > f", " echo c"]),
    ("(echo a; echo b) > f; echo c", ["(echo a; echo b) > f", " echo c"]),
    ("cat <<EOF > f\na; b\nEOF\necho c", ["cat <<EOF > f\na; b\nEOF", "echo c"]),
    ("cat <<EOF > f\nxEOF; y\nEOF\necho c", ["cat <<EOF > f\nxEOF; y\nEOF", "echo c"]),
    # a body line that merely ENDS with the delimiter must not close the heredoc
    ("cat <<EOF > f\nxEOF\na; b\nEOF\necho c", ["cat <<EOF > f\nxEOF\na; b\nEOF", "echo c"]),
])
def test_split_keeps_groups_and_heredocs_whole(cmd, expected):
    assert split_command_segments(cmd) == expected


@pytest.mark.parametrize("cmd", [
    "{ echo Pat Zorp; } > docs/notes.md",
    "(echo Pat Zorp; true) > docs/notes.md",
])
def test_group_leak_to_public_file_STILL_BLOCKS(tmp_path, cmd):
    code, err = _run_bash(tmp_path, cmd)
    assert code == 2
    assert "Pat Zorp" in err



def test_split_on_semicolon_and_newline():
    assert split_command_segments("a > x; b > y") == ["a > x", " b > y"]
    assert split_command_segments("a > x\nb > y") == ["a > x", "b > y"]


def test_split_on_and_or_operators():
    assert split_command_segments("a && b") == ["a ", " b"]
    assert split_command_segments("a || b") == ["a ", " b"]


def test_split_does_not_break_on_single_pipe():
    assert split_command_segments("echo x | tee f") == ["echo x | tee f"]


def test_split_keeps_heredoc_body_intact():
    got = split_command_segments("cat > f <<'EOF'\na; b\nEOF\necho done")
    assert len(got) == 2
    assert "a; b" in got[0]
    assert got[1] == "echo done"


def test_split_ignores_separators_inside_quotes():
    assert split_command_segments('echo "a; b" > f') == ['echo "a; b" > f']


# --- heredoc-body masking (2026-08-24, 5 fires) -----------------------------
# A heredoc body is CONTENT, never syntax. Before mask_heredoc_bodies(), any `>` inside a
# body was read as a redirect and the next token captured as a filename. Those bogus names
# matched no public prefix and had no "/", so is_public_path fell through to the gitignore
# fallback -- where `git check-ignore` exits 1 on a nonexistent path, reading as "not
# ignored" and therefore "public". A write whose REAL target was gitignored got scanned as
# a public artifact and BLOCKED.
#
# The safety-critical pair is the last two tests: masking must kill the FALSE POSITIVE
# without weakening the leak gate. A denylisted token in a heredoc body bound for a public
# file must still block, because only TARGET EXTRACTION is masked -- judge() still receives
# the raw segment.

def test_mask_heredoc_body_blockquote_yields_no_target():
    """Fires 1-4: markdown blockquote lines read as redirects."""
    cmd = "cat > coaching/x.md <<'EOF'" + "\n" + '> **Casey:** "hello"' + "\n" + "EOF"
    targets = extract_write_targets(cmd)
    assert targets == ["coaching/x.md"]


def test_mask_heredoc_body_comparison_operator_yields_no_target():
    """Fire 5: a Python comparison inside a heredoc, NOT a blockquote. This is why
    'markdown blockquote' was too narrow a description of the bug."""
    cmd = "python3 - <<'PY'" + "\n" + "if nw >= 300:" + "\n" + "    pass" + "\n" + "PY"
    assert extract_write_targets(cmd) == []


def test_mask_heredoc_body_hides_redirect_and_tee():
    cmd = "cat > output/a.md <<'EOF'" + "\n" + "run cmd >> other.md" + "\n" + "echo x | tee docs/nope.md" + "\n" + "EOF"
    targets = extract_write_targets(cmd)
    assert targets == ["output/a.md"]


def test_mask_preserves_redirect_on_the_heredoc_opening_line():
    """The body starts after the newline ending the heredoc's own line, so a redirect
    written AFTER `<<EOF` on that same line is real and must survive."""
    cmd = "cat <<'EOF' > docs/y.md" + "\n" + "body" + "\n" + "EOF"
    assert "docs/y.md" in extract_write_targets(cmd)


def test_mask_preserves_redirect_after_heredoc_closes():
    cmd = "cat <<'EOF'" + "\n" + "> quoted" + "\n" + "EOF" + "\n" + "echo z > docs/after.md"
    targets = extract_write_targets(cmd)
    assert "docs/after.md" in targets
    assert "quoted" not in targets


def test_heredoc_body_leak_to_public_file_STILL_BLOCKS(tmp_path):
    """SAFETY-CRITICAL. Masking must not weaken the gate: the body is still scanned for
    tokens, only target mining is masked. If this ever goes green-to-red the fix broke the
    hook's entire reason for existing."""
    cmd = ("cat > docs/notes.md <<'EOF'" + "\n"
           "> **Pat Zorp:** said the thing" + "\n"
           "EOF")
    code, err = _run_bash(tmp_path, cmd)
    assert code == 2
    assert "Pat Zorp" in err


def test_heredoc_body_with_token_to_GITIGNORED_path_does_not_block(tmp_path):
    """The false positive itself, end to end. /coaching/ is gitignored, so a real name in
    the body is private content and must pass. This is the exact write that was blocked on
    2026-08-24."""
    (tmp_path / ".gitignore").write_text("/coaching/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "coaching").mkdir(exist_ok=True)
    cmd = ("cat > coaching/debrief.md <<'EOF'" + "\n"
           "> **Pat Zorp:** said the thing" + "\n"
           "EOF")
    code, err = _run_bash(tmp_path, cmd)
    assert code == 0, f"gitignored target should pass, got {code}: {err}"


def test_mask_does_not_treat_a_QUOTED_heredoc_marker_as_a_heredoc():
    """Kills the quote-tracking mutants in mask_heredoc_bodies (IF_FALSE/IF_TRUE/NEGATE_CMP
    on the quote state machine).

    `<<EOF` inside a quoted string is text, not a heredoc opener. If quote tracking breaks,
    the masker believes a heredoc opened on line 1, treats line 2 as body, and swallows a
    REAL redirect -- which would silently drop a public write target and let a leak through.
    That is the dangerous direction, so it gets a test rather than an allowlist entry."""
    cmd = 'echo "a <<EOF b"' + "\n" + "echo hi > docs/real.md"
    assert extract_write_targets(cmd) == ["docs/real.md"]


def test_mask_handles_single_quoted_heredoc_marker_too():
    cmd = "echo 'x <<EOF y'" + "\n" + "echo hi > docs/real2.md"
    assert extract_write_targets(cmd) == ["docs/real2.md"]


def test_mask_quoted_redirect_inside_a_heredoc_body_is_still_masked():
    """A quoted redirect inside a body is still body content, not syntax."""
    cmd = ("cat > output/c.md <<'EOF'" + "\n"
           'echo "leak > docs/nope.md"' + "\n"
           "EOF")
    assert extract_write_targets(cmd) == ["output/c.md"]


def test_mask_does_not_close_the_heredoc_mid_line_on_a_delimiter_lookalike():
    """Kills IF_TRUE on the `ch == chr(10)` body test.

    Forcing that branch true makes every character take the newline path, so the delimiter
    lookahead runs from mid-line positions. A body line like `xEOF` then matches the
    delimiter from offset 1, the masker believes the heredoc closed early, and every later
    body line is exposed -- so a `>` further down becomes a phantom write target and the
    original false positive returns."""
    cmd = ("cat > output/d.md <<'EOF'" + "\n"
           "xEOF" + "\n"
           "> phantom.md" + "\n"
           "EOF")
    targets = extract_write_targets(cmd)
    assert targets == ["output/d.md"]
    assert "phantom.md" not in targets


def test_mask_closes_quotes_so_a_later_heredoc_is_still_detected():
    """Kills IF_FALSE on `ch == quote` (the quote-closing test).

    If a quote never closes, every later character is treated as quoted and emitted
    verbatim, so the heredoc opener is never recognised and its body is never masked -- a
    `>` inside the body becomes a phantom target again."""
    cmd = ('echo "hi"' + "\n"
           "cat > output/e.md <<'EOF'" + "\n"
           "> phantom2.md" + "\n"
           "EOF")
    targets = extract_write_targets(cmd)
    assert "output/e.md" in targets
    assert "phantom2.md" not in targets


# --- an unrecognised argument must ERROR, never fall into the hook path ----------
# Origin 2026-09-06: `check_public_pii.py --paths <files>` (the flag is --scan) fell
# through to json.load(sys.stdin) and, with no stdin, blocked for 3 hours 53 minutes at
# 0.00% CPU. The background task holding it reported its earlier steps and then simply
# stopped, so the PII sweep silently never ran and nothing said so. A hook that hangs on
# a typo is worse than one that rejects it: no exit code, no message, no reason to look.

def _run_argv(*args, stdin: str = ""):
    """Deliberately NOT named _run: this file already has one with a different
    signature, and shadowing it broke three pre-existing tests when these were first
    appended. SCRIPT is the module-level path the rest of the file already uses."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin, capture_output=True, text=True, timeout=20,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})


@pytest.mark.parametrize("bad", ["--paths", "--files", "-s", "scan", "--Scan"])
def test_an_unknown_argument_exits_2_instead_of_blocking(bad):
    """timeout=20 is the assertion that matters: before the guard this call never
    returned at all, and the test would fail by timing out rather than by asserting."""
    proc = _run_argv(bad, "tools/finding_write.py")
    assert proc.returncode == 2
    assert "unknown argument" in proc.stderr
    assert "--scan" in proc.stderr


def test_the_usage_message_names_BOTH_real_modes(bad=None):
    """A usage line that only mentions the sweep leaves the reader guessing why the
    same script also works with no arguments at all."""
    err = _run_argv("--nope").stderr
    assert "--scan --stdin-paths" in err
    assert "PreToolUse hook" in err


def test_no_arguments_still_reads_the_hook_payload():
    """The guard must not break the path that actually matters: 34 wired invocations
    a session pass zero arguments and a JSON payload on stdin."""
    proc = _run_argv(stdin='{"tool_name":"Write","tool_input":'
                      '{"file_path":"tools/x.py","content":"nothing sensitive"}}')
    assert proc.returncode == 0


def test_scan_mode_is_untouched():
    proc = _run_argv("--scan", "tools/finding_write.py")
    assert proc.returncode == 0
