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

# The SHIPPED table, not a fixture: these tests assert against the policy that
# actually runs. A fixture table here would let the real one rot unnoticed.
TABLE = hook.load_table(hook.TABLE_PATH)

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
    assert "the thing that matters" in err, f"replacement missing from stderr: {err!r}"
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
    assert hook.violations("", "the load-bearing claim", TABLE) == []
    assert hook.violations("output/acme/082526-prep.md", "", TABLE) == []


def test_violations_non_text_suffix_returns_empty_list():
    # kills RETURN_NONE on the suffix guard (violations L125).
    assert hook.violations("tools/check_draft_voice.py", "the load-bearing claim", TABLE) == []


def test_violations_exempt_path_returns_empty_list():
    # kills RETURN_NONE on the EXEMPT_PATH guard (violations L127).
    assert hook.violations("tests/fixtures/prep/bad.md", "the load-bearing claim", TABLE) == []


def test_violations_ban_documentation_file_returns_empty_list():
    # kills RETURN_NONE on the FILE_MARKER guard (violations L129).
    content = "check_banned_phrase.py flags 'load-bearing' wherever it appears."
    assert hook.violations("output/analysis/082526-audit.md", content, TABLE) == []


def test_violations_reports_line_number_matched_text_and_reason():
    """The blocking path returns one (line_no, matched_text, why) triple per hit."""
    hits = hook.violations("output/acme/082526-prep.md",
                           "clean line\nthe load-bearing claim\n", TABLE)
    assert hits == [(2, "load-bearing", 'the thing that matters')]


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


# =============================================================================
# The 2026-09-03 generalization: a scoped table, not one hardcoded phrase.
#
# Every test below names the finding it pins. They came out of a cross-model
# review (output/analysis/090326-codex-*.md) that marked six of eight claims in
# the build plan wrong. A test here that stops failing when its behavior breaks
# is worse than no test, so each asserts the SHIPPED CLI exit code, not a helper.
# =============================================================================

MANNERED_PATH = "output/acme/090326-prep.md"


def test_mannered_phrase_blocks_on_an_authored_surface():
    """A listed mannered phrase in output/ blocks, and names its replacement."""
    code, err = _write(MANNERED_PATH, "The connective tissue here is obvious.\n")
    assert code == 2, f"expected BLOCK, got exit {code}; stderr={err!r}"
    assert "connective tissue" in err, f"matched text missing: {err!r}"
    assert "what actually connects them" in err, f"replacement missing: {err!r}"


def test_north_star_is_deliberately_not_banned():
    """Nick kept 'north star' on 2026-09-03 (94 corpus hits). Pins that decision.

    If someone adds it to the table, this fails and they must re-ask Nick.
    """
    code, err = _write(MANNERED_PATH, "Our north star metric is retention.\n")
    assert code == 0, f"expected clean, got exit {code}; stderr={err!r}"


def test_scope_all_is_not_narrowed_by_the_authored_exemptions():
    """F1: generalizing must not shrink the older load/bearing rule.

    data/voice-corpus/ is exempt for mannered prose. The load/bearing row carries
    scope=all and must still fire there. If a future edit drops per-row scope and
    applies one shared exemption list, this is the test that dies.
    """
    code, err = _write("data/voice-corpus/granola/2026-09-03-call.md",
                       "He said the claim was load-bearing.\n")
    assert code == 2, f"scope=all was narrowed; got exit {code}; stderr={err!r}"


def test_mannered_phrase_is_exempt_on_a_verbatim_transcript():
    """The same path is clean for a scope=authored row: it is someone else's words."""
    code, err = _write("data/voice-corpus/granola/2026-09-03-call.md",
                       "He said it was table stakes for the role.\n")
    assert code == 0, f"expected clean, got exit {code}; stderr={err!r}"


def test_captures_tree_is_exempt_for_authored_scope():
    """captures/ holds other people's verbatim words; a scope=authored row must not fire.

    Origin 2026-09-03: freezing a third party's ChatGPT-written call summary into
    a sibling repo's captures/ tree was blocked on 'force multiplier' inside that person's
    own text. Paraphrasing to satisfy the gate would corrupt the capture, which is
    what a verbatim tree exists to prevent. If a future edit drops the captures/
    alternative from AUTHORED_EXEMPT, this test dies.
    """
    code, err = _write("captures/2026-09-03-call-summary.md",
                       "She called it a long-term growth force multiplier.\n")
    assert code == 0, f"expected clean, got exit {code}; stderr={err!r}"


def test_captures_tree_does_not_narrow_scope_all():
    """captures/ is exempt for mannered prose only. scope=all must still fire there."""
    code, err = _write("captures/2026-09-03-call-summary.md",
                       "He said the assumption was load-bearing.\n")
    assert code == 2, f"scope=all was narrowed by the captures exemption; exit {code}; stderr={err!r}"


def test_captures_must_be_a_path_segment_not_a_substring():
    """A file merely named '...captures...' is not a verbatim tree and stays gated."""
    code, err = _write("output/analysis/what-captures-attention.md",
                       "It was table stakes for the role.\n")
    assert code == 2, f"substring match leaked an exemption; exit {code}; stderr={err!r}"


def test_claude_authored_synthesis_inside_coaching_is_gated():
    """F3: coaching/progress/ holds debriefs Claude writes, so it is NOT exempt.

    Exempting the whole tree would let mannered prose land in a debrief and be
    copied into a gated prep artifact later.
    """
    code, err = _write("coaching/progress/2026-09-03-1000-acme-debrief.md",
                       "Clearing the bar is table stakes.\n")
    assert code == 2, f"laundering path is open; got exit {code}; stderr={err!r}"


def test_synthesized_reflection_is_gated_but_nicks_dated_one_is_not():
    """The underscore prefix is the Claude-voice marker in data/reflections/.

    _longitudinal.md carries `voice: cloud-generated`; a dated file is Nick's.
    """
    synth, err_s = _write("data/reflections/_longitudinal.md", "It moves the needle.\n")
    assert synth == 2, f"_-prefixed synthesis not gated; exit {synth}; stderr={err_s!r}"
    dated, err_d = _write("data/reflections/2026-09-01.md", "It moves the needle for me.\n")
    assert dated == 0, f"Nick's own reflection was gated; exit {dated}; stderr={err_d!r}"


def test_synthesized_person_dossier_is_gated():
    """data/people/ holds synthesized relationship judgments, not quoted words."""
    code, err = _write("data/people/jane-doe.md", "Trust here is table stakes.\n")
    assert code == 2, f"expected BLOCK, got exit {code}; stderr={err!r}"


def test_interaction_log_and_nicks_own_files_are_exempt():
    """networking.md quotes correspondence in full; goals.md is Nick's own writing."""
    for path in ("data/networking.md", "data/goals.md", "data/job-pipeline.md"):
        code, err = _write(path, "She called it table stakes.\n")
        assert code == 0, f"{path} was gated; exit {code}; stderr={err!r}"


def test_the_table_itself_can_be_edited():
    """The denylist must be writable, or the phrases cannot be maintained."""
    code, err = _write("tools/mannered-phrases.txt", "table stakes\tauthored\tthe minimum\n")
    assert code == 0, f"the table blocked its own edit; exit {code}; stderr={err!r}"


def test_block_message_disclaims_semantic_detection():
    """F7: the overclaim must be refused on the operational surface, not only in a docstring.

    A reader who sees a clean exit must not conclude the prose is good.
    """
    _code, err = _write(MANNERED_PATH, "The connective tissue here is obvious.\n")
    assert "NOT a mannered-prose" in err, f"scope disclaimer missing: {err!r}"
    assert "no regex can certify" in err, f"semantic disclaimer missing: {err!r}"


# --- the failure contract (F6): a bad table BLOCKS, never passes, never exits 1 --

def _run_with_table(tmp_path, table_text):
    """Copy the hook beside a controlled table so the shipped one is untouched.

    TABLE_PATH resolves next to the script, which is the injection seam. The
    shipped CLI path stays non-overridable from the payload.
    """
    script = tmp_path / "check_banned_phrase.py"
    script.write_bytes(SCRIPT.read_bytes())
    if table_text is not None:
        (tmp_path / "mannered-phrases.txt").write_bytes(table_text)
    payload = json.dumps({"tool_name": "Write",
                          "tool_input": {"file_path": MANNERED_PATH,
                                         "content": "ordinary sentence\n"}})
    r = subprocess.run([sys.executable, str(script)],
                       input=payload, capture_output=True, text=True)
    return r.returncode, r.stderr


@pytest.mark.parametrize("label,table", [
    ("missing file", None),
    ("empty file", b""),
    ("comments only", b"# nothing but a comment\n"),
    ("no tab separator", b"foo bar baz\n"),
    ("too many fields", b"foo\tall\tbar\tbaz\n"),
    ("unknown scope", b"foo\tsideways\tbar\n"),
    ("empty pattern", b"\tall\tbar\n"),
    ("empty replacement", b"foo\tall\t\n"),
    ("invalid regex", b"foo(\tall\tbar\n"),
    ("invalid utf-8", b"\xff\xfe\x00bad\n"),
])
def test_unusable_table_blocks_with_exit_2(tmp_path, label, table):
    """F6: every load failure is a deliberate exit 2, not 0 and not an uncaught 1.

    Exit 1 is what an uncaught FileNotFoundError or re.error would produce, and it
    is not the documented blocking verdict. Exit 0 would mean a gate that passes
    every write the moment its own policy file breaks.
    """
    code, err = _run_with_table(tmp_path, table)
    assert code == 2, f"{label}: expected exit 2, got {code}; stderr={err!r}"
    assert "mannered-phrases.txt" in err, f"{label}: error does not name the table: {err!r}"


def test_unusable_table_error_does_not_dump_the_table_contents(tmp_path):
    """Name the file, not its rows: the diagnostic must not become a phrase leak."""
    code, err = _run_with_table(tmp_path, b"seekrit-phrase-xyz\tall\tbar\nfoo(\tall\tbar\n")
    assert code == 2
    assert "seekrit-phrase-xyz" not in err, f"table contents leaked into stderr: {err!r}"


def test_a_valid_injected_table_is_actually_used(tmp_path):
    """Pins that the failure tests above prove something.

    Without this, every parametrized case could pass because the copied script is
    broken for an unrelated reason. A clean table must produce a clean exit, and
    its own rows must fire.
    """
    ok, _ = _run_with_table(tmp_path, b"zzz-unlikely-token\tall\treplacement text\n")
    assert ok == 0, "a valid injected table should let an ordinary write through"

    script = tmp_path / "check_banned_phrase.py"
    payload = json.dumps({"tool_name": "Write",
                          "tool_input": {"file_path": MANNERED_PATH,
                                         "content": "contains zzz-unlikely-token here\n"}})
    r = subprocess.run([sys.executable, str(script)],
                       input=payload, capture_output=True, text=True)
    assert r.returncode == 2, "the injected table's own row did not fire"
    assert "replacement text" in r.stderr


def test_shipped_table_parses_and_carries_both_scopes():
    """The real table must load, and must still contain the load/bearing row.

    A table that lost scope=all entirely would leave the older rule unenforced
    while every mannered-prose test still passed.
    """
    scopes = {scope for _pat, scope, _rep in TABLE}
    assert scopes == {"all", "authored"}, f"shipped table scopes are {scopes}"
    assert any(pat.search("this is load-bearing") for pat, scope, _ in TABLE if scope == "all"), \
        "the load/bearing row is gone from the shipped table"
