"""The hook that keeps placeholder text out of shippable prep/synthesis artifacts.

WHY IT HAD TO BE THIS ONE. check_no_confabulation.py is wired in .claude/settings.json on
Write|Edit|MultiEdit|NotebookEdit and had no test file of its own. Its failure mode is silent
non-enforcement: if a regex stops matching or the scope list empties, nothing errors -- the
placeholders simply start shipping into cheat sheets, prep packages and coached answers.

WHAT IT GUARDS. A narrow path scope (output/**/*prep*|*cheat-sheet*|*soft-answers*|
*discovery-goals*.md, data/projects/*.md, coaching/coached-answers/*.md) is scanned for
unmistakable placeholder tokens -- "x measurable outcomes", "[N] workstreams", "[insert ...]",
"and all of that stuff", "(specific number)", "several other things", a line-leading "TODO:".
Frontmatter, structured status lines, and explicit TODO / Open Questions sections are exempt
so an in-progress doc is never blocked mid-authoring.

TESTED THROUGH THE REAL ENTRY POINT, not the helpers. Per tools/HOOK_AUTHORING.md: a 53-test
suite once stayed green while the shipped hook was broken, because every test called the
helper directly. Every case below pipes JSON into the CLI over stdin and reads its exit code.

THE FALSE-POSITIVE HALF CARRIES EQUAL WEIGHT. This is a CONTENT hook, so its false-positive
surface is path scope and near-miss prose (HOOK_AUTHORING, "Two kinds of hook"). A hook that
blocks ordinary writing gets CONFAB_OVERRIDE=1 set permanently, which drops the guard just as
completely as a regex that never fires.

Where a test asserts behaviour that looks wrong, it says so and pins the CURRENT behaviour --
these are documented findings for the caller, not endorsements.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "tools" / "check_no_confabulation.py"

BLOCK, ALLOW = 2, 0

# A path inside the guarded scope, used wherever the test is about content, not scope.
PREP = "output/acme-corp/090226-prep.md"


def run(file_path: str, content: str = "", *, new_string: str | None = None,
        env_override: bool = False, raw: str | None = None) -> subprocess.CompletedProcess:
    """Invoke the shipped CLI exactly as Claude Code does: JSON on stdin, exit code out."""
    tool_input: dict = {"file_path": file_path}
    if new_string is not None:
        tool_input["new_string"] = new_string
    else:
        tool_input["content"] = content
    payload = raw if raw is not None else json.dumps({"tool_input": tool_input})
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"}
    if env_override:
        env["CONFAB_OVERRIDE"] = "1"
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, env=env)


# --- the vacuity guard: this must fail first --------------------------------

def test_the_hook_exists_and_its_pattern_and_scope_lists_are_non_empty():
    """Every "not blocked" assertion in this file is also satisfied by a hook that never
    blocks anything -- an empty PLACEHOLDER_PATTERNS, an empty IN_SCOPE_PATTERNS, or a file
    that moved. This test fails before any of them can pass vacuously."""
    assert HOOK.is_file(), f"hook not found at {HOOK}"
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import check_no_confabulation as mod
    assert len(mod.PLACEHOLDER_PATTERNS) >= 7, "placeholder list shrank; blocks may be vacuous"
    assert len(mod.IN_SCOPE_PATTERNS) >= 6, "scope list shrank; the hook judges fewer files"
    assert mod.SKIP_LINE_PREFIXES, "exemption list empty; the FP surface is unguarded"


# --- the guarantee: each placeholder pattern actually blocks -----------------

@pytest.mark.parametrize("body", [
    "We delivered x measurable outcomes for the client.",
    "The engagement ran y key workstreams in parallel.",
    "I owned [N] workstreams across two regions.",
    "Sign the note as [your name] before sending.",
    "Cite [insert the metric here] in the opener.",
    "Migrated the billing stack and all of that stuff.",
    "Grew ARR by (specific number) over four quarters.",
    "We also covered several other topics in the review.",
    "TODO: confirm the headcount before the call",
], ids=["x-measurable", "y-key-workstreams", "bracket-n", "your-name", "insert",
        "all-of-that-stuff", "specific-number", "several-other-topics", "line-leading-todo"])
def test_each_placeholder_pattern_blocks_when_it_reaches_a_scoped_file(body):
    """Every pattern must independently fire. A regex that silently stops matching removes
    one class of confabulation from the guard without failing any other test."""
    assert run(PREP, body).returncode == BLOCK, body


def test_the_block_uses_exit_2_not_1():
    """PreToolUse only blocks on exit 2. On exit 1 Claude Code treats it as an infrastructure
    error and the write proceeds -- the hook would look wired and enforce nothing."""
    assert run(PREP, "We delivered x measurable outcomes.").returncode == 2


def test_the_block_message_names_the_file_the_match_and_the_way_out():
    """A block that does not say what matched trains the reader to reach for the override
    rather than fix the placeholder, which disables the guard permanently."""
    err = run(PREP, "We delivered x measurable outcomes.").stderr
    assert "BLOCKED" in err
    assert PREP in err, "the offending file must be named"
    assert "x measurable outcomes" in err, "the matched text must be quoted back"
    assert "Line 1" in err, "the location must be given"
    assert "## Open Questions" in err and "CONFAB_OVERRIDE=1" in err, "remedies must be listed"


def test_the_reported_line_number_points_at_the_violation_not_the_top():
    """A guard that always says "Line 1" is useless on a 400-line prep doc, and the offset
    arithmetic is the easiest thing here to get wrong."""
    body = "clean intro\n\nmore clean prose\n\nWe delivered x measurable outcomes.\n"
    assert "Line 5" in run(PREP, body).stderr


def test_every_violation_is_reported_not_only_the_first():
    """Reporting one at a time turns a single fix cycle into several, and the author gives up
    and overrides."""
    err = run(PREP, "We delivered x measurable outcomes.\nSign it as [your name].\n").stderr
    assert "x measurable outcomes" in err
    assert "[your name]" in err


def test_an_edit_is_covered_not_just_a_write():
    """Edit sends new_string, not content. Reading only `content` would leave every Edit
    unguarded -- and editing a prep doc is more common than writing one from scratch."""
    assert run(PREP, new_string="We delivered x measurable outcomes.").returncode == BLOCK


# --- path scope: in ---------------------------------------------------------

@pytest.mark.parametrize("path", [
    "output/acme-corp/090226-prep.md",
    "output/acme-corp/090226-cheat-sheet.md",
    "output/acme-corp/090226-soft-answers.md",
    "output/acme-corp/090226-discovery-goals.md",
    "data/projects/billing-platform.md",
    "coaching/coached-answers/why-did-you-leave.md",
    "OUTPUT/ACME/090226-PREP.MD",
], ids=["prep", "cheat-sheet", "soft-answers", "discovery-goals", "projects",
        "coached-answers", "uppercase"])
def test_each_documented_in_scope_path_is_judged(path):
    """The docstring advertises six shapes. If one drops out of IN_SCOPE_PATTERNS the hook
    goes silent for that whole artifact class with nothing to notice."""
    assert run(path, "We delivered x measurable outcomes.").returncode == BLOCK, path


def test_a_windows_style_path_is_normalised_before_matching():
    """extract/normalise runs on the raw payload; a backslash path must not slip the scope."""
    assert run(r"output\acme-corp\090226-prep.md",
               "We delivered x measurable outcomes.").returncode == BLOCK


# --- path scope: out (the false-positive half) ------------------------------

@pytest.mark.parametrize("path", [
    "output/acme-corp/090226-notes.md",
    "output/acme-corp/acme-corp.md",
    "data/reflections/2026-09-02-thinking.md",
    "data/company-notes/acme-corp.md",
    "memory/feedback_something.md",
    "README.md",
], ids=["other-output-doc", "research-dossier", "reflection", "company-notes",
        "memory-rule", "repo-root"])
def test_out_of_scope_files_are_never_judged(path):
    """The hook is deliberately narrow: reflections are voice-pure and dossiers have their own
    evidence rules. Widening the scope silently is how a content hook starts blocking the
    user's own writing."""
    assert run(path, "We delivered x measurable outcomes.").returncode == ALLOW, path


def test_a_nested_projects_path_does_not_inherit_the_data_projects_exemption_or_scope():
    """data/projects/ is matched as direct children only ([^/]+\\.md). A nested file is a
    different artifact class and is currently NOT judged -- pinned so a widening is visible."""
    assert run("data/projects/archive/old.md",
               "We delivered x measurable outcomes.").returncode == ALLOW


def test_a_coached_answers_lookalike_directory_is_not_in_scope():
    """coaching/coached-answers/ is the spoken-STAR store. A sibling directory with a similar
    name is not it and must not be judged by these rules."""
    assert run("coaching/coached-answers-archive/why-leave.md",
               "We delivered x measurable outcomes.").returncode == ALLOW


# --- content exemptions: an in-progress doc must stay writable ---------------

def test_frontmatter_may_contain_placeholder_tokens():
    """Blocking on frontmatter would make a stubbed-out doc impossible to create, so the
    author could never reach the valid state the hook wants."""
    body = "---\nstatus: TODO: draft\ntype: prep\n---\n\nThe body is clean.\n"
    assert run(PREP, body).returncode == ALLOW


@pytest.mark.parametrize("heading", ["## TODO", "### TODO", "## Open Questions"],
                         ids=["h2-todo", "h3-todo", "open-questions"])
def test_placeholders_inside_an_explicit_todo_section_are_allowed(heading):
    """The block message tells the author to move unresolved items under exactly these
    headings. If the exemption did not work, following the instructions would block again."""
    body = f"Clean opener.\n\n{heading}\n\nTODO: get the number\nSign as [your name]\n"
    assert run(PREP, body).returncode == ALLOW


def test_the_todo_exemption_ends_at_the_next_heading():
    """An exemption that runs to end-of-file would let one `## TODO` heading disable the hook
    for the entire rest of the document."""
    body = "## TODO\n\nTODO: get the number\n\n## Answer\n\nWe delivered x measurable outcomes.\n"
    assert run(PREP, body).returncode == BLOCK


@pytest.mark.parametrize("line", ["status: TBD", "captured: TODO: later", "- [ ] TODO: draft it"],
                         ids=["status-field", "captured-field", "checkbox"])
def test_structured_status_lines_and_checkboxes_are_exempt(line):
    """These are tracking scaffolding, not shipped prose. Blocking them makes the hook fire on
    routine bookkeeping."""
    assert run(PREP, f"Clean opener.\n{line}\n").returncode == ALLOW


# --- near-miss prose: ordinary writing must survive -------------------------

@pytest.mark.parametrize("body", [
    "The team raised several other risks I had not considered.",
    "We ran y workstreams is a phrase I would never write, but the shape needs a qualifier.",
    "Revenue grew 40% year over year (source: their 2025 annual report).",
    "The x axis measured outcomes over time.",
    "I handled the migration and all of that was documented afterwards.",
    "Confidence on this number is [Confidence: Med, as of 2025-11].",
    "We should insert the metric once finance confirms it.",
    "Left a note to myself: TODO items live at the bottom of this doc.",
    "The retro covered three areas and several other people joined late.",
    "Status on the comp band is TBD until the recruiter calls back.",
], ids=["several-other-risks", "bare-y-workstreams", "cited-percentage", "x-axis",
        "all-of-that-documented", "confidence-tag", "insert-as-a-verb", "todo-as-a-noun",
        "several-other-people", "tbd-prose"])
def test_realistic_near_miss_prose_is_not_blocked(body):
    """These all contain a trigger word without being a placeholder. A content hook that
    blocks ordinary writing gets CONFAB_OVERRIDE=1 set permanently, which is the same outcome
    as having no hook. Note "TBD" is advertised in the docstring but implemented by no
    pattern -- this pins the shipped behaviour, not the documented one."""
    assert run(PREP, body).returncode == ALLOW, body


def test_a_todo_appearing_mid_sentence_is_not_blocked():
    """The TODO pattern is line-anchored on purpose: prose that merely mentions the word is
    not an unresolved stub."""
    assert run(PREP, "The prep doc still has a TODO: item somewhere below.").returncode == ALLOW


def test_a_clean_shippable_prep_doc_passes_end_to_end():
    """The realistic control. If a fully-cited, placeholder-free artifact does not pass, the
    hook is unusable regardless of how well it catches violations."""
    body = (
        "---\ntype: prep\nstatus: ready\n---\n\n"
        "# Acme Corp - Interview Prep\n\n"
        "## Why now\n\nThey closed a Series C in March 2025 (source: company blog).\n\n"
        "## My story\n\nI led four workstreams across billing and provisioning, cutting\n"
        "invoice errors by 31% (per the 2025 close report).\n"
    )
    assert run(PREP, body).returncode == ALLOW


# --- override and fail-open -------------------------------------------------

def test_the_documented_override_bypasses_the_hook():
    """CONFAB_OVERRIDE=1 is the sanctioned escape hatch for shipping a known stub. If it
    stopped working the only recourse would be to unwire the hook."""
    assert run(PREP, "We delivered x measurable outcomes.", env_override=True).returncode == ALLOW


def test_a_non_one_value_of_the_override_env_var_does_not_bypass():
    """The check is `== "1"`. A stale `CONFAB_OVERRIDE=0` in the environment must not read as
    "on", or the guard is off for the whole session without anyone noticing."""
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin", "CONFAB_OVERRIDE": "0"}
    payload = json.dumps({"tool_input": {"file_path": PREP,
                                         "content": "We delivered x measurable outcomes."}})
    r = subprocess.run([sys.executable, str(HOOK)], input=payload,
                       capture_output=True, text=True, env=env)
    assert r.returncode == BLOCK


def test_malformed_stdin_fails_open():
    """Fail-open is deliberate: a hook-internal problem must never block real work. The cost
    is that a crash is silent, which is exactly why the vacuity guard above exists."""
    assert run("", raw="{not json").returncode == ALLOW


def test_empty_stdin_fails_open():
    assert run("", raw="").returncode == ALLOW


def test_KNOWN_DEFECT_non_object_json_tracebacks_instead_of_failing_open():
    """The `except Exception` guards json.load() only; `data.get(...)` then runs on whatever
    was parsed. Valid-but-non-object JSON (a list, a bare string, `null`) raises
    AttributeError and the hook exits 1. It does not BLOCK, so the write still proceeds --
    but every such call prints a traceback into the session. Pinned, not endorsed."""
    r = run("", raw="[1, 2, 3]")
    assert r.returncode == 1, "defect fixed -- change this to assert ALLOW"
    assert r.returncode != BLOCK, "a malformed payload must never block real work"
    assert "AttributeError" in r.stderr


@pytest.mark.parametrize("payload", [
    {"tool_input": {}},
    {"tool_input": {"file_path": PREP}},
    {"tool_input": {"content": "We delivered x measurable outcomes."}},
    {"tool_input": None},
    {},
], ids=["no-fields", "path-only", "content-only", "null-tool-input", "no-tool-input"])
def test_incomplete_payloads_do_not_block(payload):
    """No path or no content means there is nothing to judge. Blocking here would fire the
    hook on unrelated tool calls that happen to carry a different input shape."""
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert r.returncode == ALLOW, r.stderr


# --- known defects, pinned so a fix is visible ------------------------------

def test_KNOWN_DEFECT_a_markdown_link_starting_with_the_is_blocked_as_a_placeholder():
    """FALSE POSITIVE. `[the ...]` matches any markdown link text beginning with "the", so
    "See [the full list](notes.md)" is blocked in every scoped file. This is the highest-
    traffic FP surface in the hook. Pinned, not endorsed -- if this starts passing, the
    regex was tightened and this test should be inverted."""
    assert run(PREP, "See [the full list](notes.md) for the breakdown.").returncode == BLOCK


def test_KNOWN_DEFECT_the_hook_judges_its_own_test_fixtures():
    """HOOK_AUTHORING is explicit that a content hook must exclude tests/ and any analysis
    directory that quotes the pattern as prose. This one excludes neither, so a fixture or a
    spec that contains a placeholder ON PURPOSE cannot be written."""
    assert run("tests/fixtures/output/acme-corp/090226-prep.md",
               "We delivered x measurable outcomes.").returncode == BLOCK
    assert run("output/analysis/090226-prep-format-spec.md",
               "Bad example: we delivered x measurable outcomes.").returncode == BLOCK


def test_KNOWN_DEFECT_a_leading_horizontal_rule_makes_the_body_unscannable():
    """strip_frontmatter() treats any leading `---` as frontmatter and returns everything
    after the SECOND `---`. A doc that opens with a horizontal rule therefore has all content
    up to the next rule discarded, and placeholders in it ship silently."""
    body = "---\n\nWe delivered x measurable outcomes.\n\n---\n\nTail is clean.\n"
    assert run(PREP, body).returncode == ALLOW, "defect fixed -- invert this test"
