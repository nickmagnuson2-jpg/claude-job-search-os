"""Tests for tools/check_banned_phrase.py — the banned-phrase Write|Edit content hook.

The hook blocks (exit 2) when the load/bearing compound appears in content being
written to a prose surface Nick reads, and stays clean (exit 0) for fixtures, for
writes that document the ban, for code files, and for the `old_string` half of an
Edit (so an existing violation can be edited away).

The ORIGIN case is pinned below: the verbatim heading from
output/acme/050526-partner-call-prep.md, one of the Sunday-Monday prep docs Nick was
reading on 2026-05-25 when he banned the phrase.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_banned_phrase.py"

# Import the hook as a module so the two pure functions can be asserted directly.
# Importing it must NOT execute main(): main() reads stdin, so an import-time run
# would blow up collection. That is exactly what pins the __main__ guard.
_spec = importlib.util.spec_from_file_location("check_banned_phrase", SCRIPT)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)  # kills IF_TRUE on `if __name__ == "__main__":` (L187)

# Verbatim line from an origin-era prep doc (output/acme/050526-partner-call-prep.md).
ORIGIN_LINE = (
    "## The two answers that need sharpening (load-bearing — see "
    "[050526-casey-prep-soft-answers.md](050526-casey-prep-soft-answers.md) for full prep)"
)
ORIGIN_PATH = "output/acme/050526-partner-call-prep.md"


def _run(tool_name: str, tool_input: dict):
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True)
    return r.returncode, r.stderr


def _write(path: str, content: str):
    return _run("Write", {"file_path": path, "content": content})


# --- the blocking verdict, asserted with named expectations ------------------
# Deliberately FIRST in the file. The mutation harness runs pytest with `-x`, so the
# first failing test is the one that classifies the kill, and a bare `assert code == 2`
# renders under `--tb=line` as `file:52: assert 0 == 2` — the word "AssertionError"
# never appears, so a real assertion kill is reported as a weak (crash) kill. Carrying
# a message on each assert makes the failure say WHICH property of the verdict broke.

def test_blocking_verdict_reports_exit_code_count_line_and_reason():
    code, err = _write("output/acme/082526-prep.md",
                       "intro line\nthe load-bearing claim\n")
    assert code == 2, f"expected BLOCK (exit 2), got exit {code}; stderr={err!r}"
    assert "BLOCKED (banned phrase)" in err, f"verdict header missing from stderr: {err!r}"
    assert "1 occurrence(s)" in err, f"occurrence count wrong in stderr: {err!r}"
    assert "line 2:" in err, f"line number of the hit missing from stderr: {err!r}"
    assert "'load-bearing'" in err, f"matched text missing from stderr: {err!r}"
    assert "banned LLM-tell metaphor" in err, f"reason missing from stderr: {err!r}"
    assert "output/acme/082526-prep.md" in err, f"target path missing from stderr: {err!r}"


def test_clean_prose_produces_no_verdict_at_all():
    code, err = _write("output/acme/082526-prep.md",
                       "intro line\nthe claim that matters is pricing\n")
    assert code == 0, f"expected CLEAN (exit 0), got exit {code}; stderr={err!r}"
    assert err == "", f"a clean write must print nothing to stderr, got {err!r}"


# --- ORIGIN: the incident that created the rule -----------------------------

def test_blocks_the_origin_prep_doc_line():
    code, err = _write(ORIGIN_PATH, "# Partner call prep\n\n" + ORIGIN_LINE + "\n")
    assert code == 2
    assert "BLOCKED (banned phrase)" in err
    assert "the thing that matters" in err


def test_allows_the_origin_line_rewritten():
    clean = ORIGIN_LINE.replace("(load-bearing — see", "(the two that matter — see")
    code, _ = _write(ORIGIN_PATH, "# Partner call prep\n\n" + clean + "\n")
    assert code == 0


# --- should BLOCK (exit 2) --------------------------------------------------

@pytest.mark.parametrize("content", [
    'The load-bearing claim is the pricing one.',
    'This is a load bearing assumption.',          # space form (Nick's spelling)
    'That paragraph is Load-Bearing for the case.',  # casing
    'the load_bearing constraint',                 # underscore
    'a loadbearing risk',                          # no separator
    'X is load-bearing.',                          # predicate form
    'ok line\nanother ok line\nthe load-bearing insight is here',  # not first line
])
def test_blocks_banned_compound(content):
    code, err = _write("output/acme/082526-prep.md", content)
    assert code == 2, err


def test_blocks_memory_write():
    code, _ = _write(
        "/Users/x/.claude/projects/p/memory/reference_thing.md",
        "The load-bearing part of the argument is section 3.",
    )
    assert code == 2


def test_blocks_edit_new_string():
    code, _ = _run("Edit", {
        "file_path": "framework/analysis-method.md",
        "old_string": "the key insight",
        "new_string": "the load-bearing insight",
    })
    assert code == 2


def test_blocks_multiedit_second_edit():
    code, _ = _run("MultiEdit", {
        "file_path": "docs/usage.md",
        "edits": [
            {"old_string": "a", "new_string": "clean text"},
            {"old_string": "b", "new_string": "the load-bearing step"},
        ],
    })
    assert code == 2


def test_reports_every_occurrence_and_line_numbers():
    code, err = _write(
        "output/acme/082526-prep.md",
        "the load-bearing claim\nfine line\nthe load-bearing risk\n",
    )
    assert code == 2
    assert "2 occurrence(s)" in err
    assert "line 1:" in err and "line 3:" in err


# --- should stay CLEAN (exit 0) ---------------------------------------------

@pytest.mark.parametrize("path,content", [
    # the rewrite the rule prescribes
    ("output/acme/082526-prep.md", "The claim that matters is the pricing one."),
    ("output/acme/082526-prep.md", "The binding constraint is headcount."),
    # near-miss words must not trip a substring matcher
    ("output/acme/082526-prep.md", "download bearings for the rig"),
    ("output/acme/082526-prep.md", "overload bearingless designs"),
    ("output/acme/082526-prep.md", "the load balancer bearing the traffic"),
    # non-prose surfaces are judged elsewhere
    ("tools/check_draft_voice.py", 'r"\\bload[\\s-]?bearing\\b"'),
    ("data/pipeline.json", '{"note": "load-bearing"}'),
    # fixtures carry the bad pattern on purpose
    ("tests/fixtures/prep/bad.md", "the load-bearing claim"),
    ("tests/scripts/test_check_draft_voice_t10.py.md", "load-bearing"),
    # the rule file itself
    ("memory/feedback_no_load_bearing_vocabulary.md", "load-bearing anywhere"),
])
def test_allows(path, content):
    code, err = _write(path, content)
    assert code == 0, err


@pytest.mark.parametrize("content", [
    'Do not use "load-bearing" — it is an LLM-tell.',
    '"load-bearing" is banned across the vocabulary.',
    "Never use load-bearing in prose Nick reads.",
])
def test_allows_lines_documenting_the_ban(content):
    code, err = _write("memory/index-outreach.md", content)
    assert code == 0, err


def test_allows_whole_file_that_cites_the_rule():
    code, err = _write(
        "output/analysis/082526-voice-audit.md",
        "Per feedback_no_load_bearing_vocabulary, the sweep found "
        "'load-bearing' in 3 prep docs and 'load-bearing' in one handoff.",
    )
    assert code == 0, err


def test_allows_edit_that_removes_an_existing_violation():
    code, err = _run("Edit", {
        "file_path": "output/acme/082526-prep.md",
        "old_string": "the load-bearing insight",
        "new_string": "the key insight",
    })
    assert code == 0, err


# --- fail-open surfaces -----------------------------------------------------

@pytest.mark.parametrize("payload", [
    "not json at all",
    "[]",
    '{"tool_name": "Write", "tool_input": {}}',
    '{"tool_name": "Write", "tool_input": {"file_path": "a.md"}}',
    '{"tool_name": "Bash", "tool_input": {"command": "grep load-bearing ."}}',
    '{"tool_name": "Write", "tool_input": "notadict"}',
])
def test_fails_open(payload):
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       input=payload, capture_output=True, text=True)
    assert r.returncode == 0


# --- which tools contribute content -----------------------------------------

def test_unhandled_tool_contributes_no_content():
    """A tool the hook does not handle yields the empty string, never None."""
    # kills RETURN_NONE on `return ""` (new_content L117): None would not equal "".
    assert hook.new_content("Bash", {"command": 'echo "load-bearing"'}) == ""


def test_edits_payload_from_a_non_multiedit_tool_is_ignored():
    """Only MultiEdit's edits list is read; another tool carrying `edits` is not."""
    # kills IF_TRUE on `if tool_name == "MultiEdit":` (new_content L114).
    payload = {"file_path": "notes.md",
               "edits": [{"old_string": "a", "new_string": "the load-bearing cell"}]}
    assert hook.new_content("NotebookEdit", payload) == ""
    code, err = _run("NotebookEdit", payload)
    assert code == 0, err


# --- violations() returns a LIST on every clean path ------------------------
# Each of these reaches a different early `return []`. `None == []` is False, so
# asserting the empty list (not just falsiness) kills RETURN_NONE on that line —
# main() does `len(hits)` on this value, so the list type is the real contract.

def test_violations_empty_inputs_return_empty_list():
    # kills RETURN_NONE on the empty path/content guard (violations L123).
    assert hook.violations("", "the load-bearing claim") == []
    assert hook.violations("output/acme/082526-prep.md", "") == []


def test_violations_non_text_suffix_returns_empty_list():
    # kills RETURN_NONE on the suffix guard (violations L125).
    assert hook.violations("tools/check_draft_voice.py", "the load-bearing claim") == []


def test_violations_exempt_path_returns_empty_list():
    # kills RETURN_NONE on the EXEMPT_PATH guard (violations L127).
    assert hook.violations("tests/fixtures/prep/bad.md", "the load-bearing claim") == []


def test_violations_ban_documentation_file_returns_empty_list():
    # kills RETURN_NONE on the FILE_MARKER guard (violations L129).
    content = "check_banned_phrase.py flags 'load-bearing' wherever it appears."
    assert hook.violations("output/analysis/082526-audit.md", content) == []


def test_violations_reports_line_number_matched_text_and_reason():
    """The blocking path returns one (line_no, matched_text, why) triple per hit."""
    hits = hook.violations("output/acme/082526-prep.md",
                           "clean line\nthe load-bearing claim\n")
    assert hits == [(2, "load-bearing", '"load-bearing" — banned LLM-tell metaphor')]


# --- the >10 truncation footer ----------------------------------------------

def test_truncates_after_ten_hits_and_counts_the_remainder():
    """11 hits: 10 are listed, and the footer names the 1 that was not."""
    # kills NEGATE_CMP on `len(hits) <= 10` (main L162) — flipped, the footer vanishes.
    code, err = _write("output/acme/082526-prep.md",
                       "".join(f"hit {i} is load-bearing\n" for i in range(1, 12)))
    assert code == 2
    assert "11 occurrence(s)" in err
    assert "line 10:" in err
    assert "line 11:" not in err
    assert "... and 1 more" in err


def test_no_truncation_footer_at_exactly_ten_hits():
    """10 hits is the boundary: every hit is listed and no footer is printed."""
    # kills NEGATE_CMP on `len(hits) <= 10` (main L162) — flipped, "and -0 more" appears.
    code, err = _write("output/acme/082526-prep.md",
                       "".join(f"hit {i} is load-bearing\n" for i in range(1, 11)))
    assert code == 2
    assert "10 occurrence(s)" in err
    assert "line 10:" in err
    assert "more\n" not in err.split("Rewrite, do not reword")[0]
