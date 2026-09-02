"""The hook that surfaces solo-agency overclaim in data/projects/*.md.

WHY IT HAD TO BE THIS ONE. check_data_projects is wired in .claude/settings.json on
Write|Edit|MultiEdit|NotebookEdit and had no suite of its own. `mutation_sweep` skips any
tool that maps to zero test files, so it never entered the baseline at all -- absent, not
failing, which is the quiet half of the problem. Its failure mode is silent
non-enforcement: nothing errors, overclaimed bullets simply stop being surfaced and
propagate into CVs, cover letters and STAR stories.

WHAT IT ACTUALLY DOES -- AND THE ONE THING THAT MUST NOT BE ASSUMED. This hook is
WARN-ONLY BY DESIGN. It exits 0 on every input, including the ones it flags, and prints
the finding to stderr. So the exit code is NOT the signal here: a suite that only asserted
exit codes would pass against a hook whose entire warning body had been deleted. Every
positive case below asserts the stderr WARNING; every negative case asserts its absence.
The docstring's own line "Exit 2 only on infrastructure errors" is not implemented -- see
the exit-code test for what the shipped code does.

TESTED THROUGH THE REAL ENTRY POINT, not the helpers. Per tools/HOOK_AUTHORING.md: a
guarantee the CLI advertises needs a test that RUNS the CLI. A 53-test suite once passed
while the shipped hook was broken because every test called the helper directly.

The two directions are not symmetric. A false negative silently drops the guard; a false
positive trains the reader to set PROJECTS_OVERRIDE=1, which drops the guard too, and for
a warn-only hook noise is the whole cost -- there is nothing else it can do wrong. Both
are covered below, and the realistic clean shapes (softened verbs, prose, non-project
paths, other sections) carry the same weight as the flagged ones.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "tools" / "check_data_projects.py"

# This hook never blocks. PROCEED is the only exit code it is allowed to produce.
PROCEED = 0
WARN_MARKER = "WARNING (write proceeds)"

PROJECT_PATH = "/repo/data/projects/marketplace-transformation.md"

DIRTY = (
    "# Marketplace Transformation\n\n"
    "## Description\n\n"
    "A two-year programme.\n\n"
    "## Key Achievements\n\n"
    "- Built the pricing model that shipped to 40 markets\n"
    "- Reduced time-to-quote by 30%\n"
)

CLEAN = (
    "# Marketplace Transformation\n\n"
    "## Description\n\n"
    "A two-year programme.\n\n"
    "## Key Achievements\n\n"
    "- Co-led the pricing workstream with two staff engineers\n"
    "- Reduced time-to-quote by 30%\n"
)


def run(file_path: str, content: str = "", *, new_string: str | None = None,
        env_override: bool = False, raw: str | None = None,
        payload: dict | None = None) -> subprocess.CompletedProcess:
    if payload is not None:
        body = json.dumps(payload)
    else:
        tool_input: dict = {"file_path": file_path}
        if new_string is not None:
            tool_input["new_string"] = new_string
        else:
            tool_input["content"] = content
        body = json.dumps({"tool_input": tool_input})
    stdin = raw if raw is not None else body
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"}
    if env_override:
        env["PROJECTS_OVERRIDE"] = "1"
    return subprocess.run([sys.executable, str(HOOK)], input=stdin,
                          capture_output=True, text=True, env=env)


def warned(r: subprocess.CompletedProcess) -> bool:
    return WARN_MARKER in r.stderr


# --- the guarantee: a solo-agency verb in Key Achievements is surfaced ------

def test_a_solo_agency_verb_in_key_achievements_is_warned_about():
    r = run(PROJECT_PATH, DIRTY)
    assert warned(r), r.stderr


def test_the_hook_never_blocks_even_when_it_flags():
    """PreToolUse only blocks on exit 2, and this hook deliberately never uses it. If a
    future edit made it exit 2 the write would start failing on judgement-call prose; if
    it made it exit 1 the call would proceed anyway and the message would be the only
    remaining signal. Pinning 0 here is what makes 'warn-only' a tested property rather
    than a comment. (The module docstring's 'Exit 2 only on infrastructure errors' line
    describes code that does not exist.)"""
    assert run(PROJECT_PATH, DIRTY).returncode == PROCEED


def test_the_warning_names_the_section_it_judged():
    """The reader needs to know which part of the file was examined; the hook only looks
    inside one section and a bare 'overclaim risk' would send them through the whole file."""
    err = run(PROJECT_PATH, DIRTY).stderr
    assert "Key Achievements uses solo-agency verbs" in err


def test_the_warning_misreports_the_first_match_and_quotes_it_correctly_from_the_second():
    """DOCUMENTS A REAL BUG, pinned so a fix has to come here deliberately.

    The verb pattern opens with `^\\s*`, and `\\s` matches newlines, so the first match
    starts at the top of the blank run above the bullet rather than at the bullet. Both
    derived values are then wrong: the line number points at the blank line and the quoted
    text is the empty string -- the one field that makes the warning actionable. Every
    later match resumes mid-line and is reported correctly, which is why this is easy to
    miss: a two-bullet section looks half-right.

    Below, '- Built ...' is line 5 and '- Led ...' is line 6."""
    content = "# T\n\n## Key Achievements\n\n- Built the pricing model\n- Led the rollout\n"
    err = run(PROJECT_PATH, content).stderr
    assert '  Line 4: ""' in err          # should be: Line 5: "- Built the pricing model"
    assert '  Line 6: "- Led the rollout"' in err


def test_the_warning_names_the_file_and_what_to_do_instead():
    """The remedy has to travel with the finding, or the reader's cheapest move is the
    override."""
    err = run(PROJECT_PATH, DIRTY).stderr
    assert PROJECT_PATH in err
    assert "co-led" in err
    assert "PROJECTS_OVERRIDE=1" in err


def test_an_edit_is_covered_not_just_a_write():
    """Edit sends new_string, not content. Reading only `content` would leave every Edit
    unwatched, and editing one bullet is the most common way a project file changes."""
    assert warned(run(PROJECT_PATH, new_string=DIRTY))


@pytest.mark.parametrize("verb", [
    "Built", "Designed", "Architected", "Created", "Founded", "Owned", "Led", "Drove",
    "Established", "Pioneered", "Spearheaded", "Launched", "Invented",
])
def test_every_listed_verb_actually_fires(verb):
    """Each alternation branch must contribute. A verb silently dropped from the pattern
    weakens the guard without failing anything -- the hook stays green and just sees less."""
    content = f"## Key Achievements\n\n- {verb} the thing\n"
    assert warned(run(PROJECT_PATH, content)), verb


def test_matching_is_case_insensitive():
    """Bullets get lowercased by hand-editing and by templating; the risk is identical."""
    assert warned(run(PROJECT_PATH, "## Key Achievements\n\n- built the thing\n"))


@pytest.mark.parametrize("bullet", [
    "- Built the thing",
    "* Built the thing",
    "1. Built the thing",
    "Built the thing",
    "  - Built the thing",
], ids=["dash", "star", "numbered", "bare", "indented"])
def test_every_bullet_shape_is_recognised(bullet):
    """Project files mix list styles. A shape the pattern misses is a whole file's worth
    of achievements going unread."""
    assert warned(run(PROJECT_PATH, f"## Key Achievements\n\n{bullet}\n")), bullet


@pytest.mark.parametrize("header", [
    "## Key Achievements", "### Key Achievement", "## Highlights", "## Highlight",
    "## Impact", "## Achievements", "## Results", "## Outcomes", "## key achievements",
])
def test_every_scoped_header_variant_is_recognised(header):
    """The template says 'Key Achievements' but real files drift. A header spelling the
    scope regex misses makes the whole section invisible to the hook."""
    assert warned(run(PROJECT_PATH, f"{header}\n\n- Built the thing\n")), header


# --- scope: the hook must not judge what it was not pointed at -------------

@pytest.mark.parametrize("path", [
    "/repo/data/profile.md",
    "/repo/data/projects/notes.txt",
    "/repo/data/projects/nested/deep.md",
    "/repo/output/acme/090226-cv.md",
    "/repo/coaching/coached-answers/why-did-you-leave.md",
    "/repo/README.md",
], ids=["other-data-file", "not-markdown", "nested-subdir", "output", "coaching", "root"])
def test_files_outside_data_projects_are_left_alone(path):
    """This hook exists for the CV-feeder files specifically. Warning on every markdown
    write in the repo is how a warn-only gate becomes background noise and gets silenced."""
    assert not warned(run(path, DIRTY)), path


def test_a_relative_project_path_is_still_matched():
    """Tool payloads carry both absolute and repo-relative paths depending on caller. A
    matcher that only worked on absolute paths would skip half the real writes."""
    assert warned(run("data/projects/marketplace.md", DIRTY))


def test_a_windows_style_path_is_normalised_before_matching():
    assert warned(run(r"C:\repo\data\projects\marketplace.md", DIRTY))


def test_a_verb_outside_the_achievements_block_is_not_flagged():
    """Description and Responsibilities are narrative prose where 'Led the workstream' is
    ordinary phrasing. Flagging there would fire on nearly every project file."""
    content = (
        "## Description\n\n"
        "Led the marketplace workstream end to end.\n\n"
        "## Key Achievements\n\n"
        "- Reduced time-to-quote by 30%\n"
    )
    assert not warned(run(PROJECT_PATH, content))


def test_the_block_stops_at_the_next_section_header():
    """The block is bounded by the next H2/H3. If the bound were dropped, every verb in
    the rest of the file would be attributed to Key Achievements."""
    content = (
        "## Key Achievements\n\n"
        "- Reduced time-to-quote by 30%\n\n"
        "## Technologies\n\n"
        "Built on Python and dbt.\n"
    )
    assert not warned(run(PROJECT_PATH, content))


def test_a_file_with_no_scoped_header_is_not_examined():
    """No Key Achievements section means nothing this hook has an opinion about."""
    content = "# Project\n\n## Description\n\n- Built the thing\n"
    assert not warned(run(PROJECT_PATH, content))


# --- false positives: the clean shapes a real file uses --------------------

@pytest.mark.parametrize("bullet", [
    "- Co-led the pricing workstream with two staff engineers",
    "- Partnered with the data team to ship the model",
    "- Contributed to the pricing rebuild alongside three engineers",
    "- Reduced time-to-quote by 30% across 40 markets",
    "- Worked with Legal to unblock the launch",
    "- Analysis that shifted the roadmap",
], ids=["co-led", "partnered", "contributed", "metric-first", "worked-with", "noun-first"])
def test_softened_and_neutral_bullets_are_not_flagged(bullet):
    """These are exactly the rewrites the warning asks for. If the hook still fired after
    the reader complied, the only stable state left would be the override."""
    assert not warned(run(PROJECT_PATH, f"## Key Achievements\n\n{bullet}\n")), bullet


def test_a_realistic_clean_project_file_passes_silently():
    """The whole-file case, not just a bullet: the common write must produce no output at
    all, or the signal-to-noise ratio makes the warning unreadable."""
    r = run(PROJECT_PATH, CLEAN)
    assert not warned(r)
    assert r.stderr == ""


@pytest.mark.parametrize("line", [
    "- The team built the pricing model",
    "- Six engineers designed the schema; I ran the analysis",
    "- Ownership of the roadmap moved to Product",
], ids=["mid-sentence-built", "mid-sentence-designed", "noun-form"])
def test_the_verb_must_start_the_bullet_not_merely_appear_in_it(line):
    """The claim being made is authorship. A bullet that explicitly credits a team is the
    corrected form, and matching the verb anywhere in the line would flag the fix."""
    assert not warned(run(PROJECT_PATH, f"## Key Achievements\n\n{line}\n")), line


def test_the_documented_override_silences_the_hook():
    r = run(PROJECT_PATH, DIRTY, env_override=True)
    assert r.returncode == PROCEED
    assert r.stderr == ""


# --- malformed and incomplete input: fail open, silently -------------------

@pytest.mark.parametrize("raw", ["", "{not json", "   ", "\x00\xff"],
                         ids=["empty", "truncated", "whitespace", "binary"])
def test_unparseable_stdin_fails_open_and_says_nothing(raw):
    """Fail-open is deliberate: a hook-internal problem must never interfere with real
    work, and a parse error must not print a scary message about a file it never read.
    The cost is that a crash is silent, which is exactly why the tests above exist."""
    r = run("", raw=raw)
    assert r.returncode == PROCEED
    assert r.stderr == ""


@pytest.mark.parametrize("raw", ["null", "[]", '"a string"', "7"],
                         ids=["null", "list", "string", "number"])
def test_valid_json_that_is_not_an_object_crashes_with_a_traceback(raw):
    """DOCUMENTS A REAL BUG. The try/except only wraps json.load, so a payload that parses
    to a non-dict reaches `data.get(...)` and raises AttributeError. Exit 1 is an
    infrastructure error, so the tool call still proceeds -- it fails open by accident
    rather than by design, and leaves a traceback on stderr that reads like a real finding.
    Pinned so a fix has to come here."""
    r = run("", raw=raw)
    assert r.returncode == 1
    assert "AttributeError" in r.stderr
    assert WARN_MARKER not in r.stderr


@pytest.mark.parametrize("payload", [
    {},
    {"tool_input": {}},
    {"tool_input": None},
    {"tool_input": {"file_path": PROJECT_PATH}},
    {"tool_input": {"content": DIRTY}},
    {"tool_input": {"file_path": PROJECT_PATH, "content": ""}},
    {"tool_input": {"file_path": "", "content": DIRTY}},
], ids=["no-tool-input", "no-fields", "null-tool-input", "path-only", "content-only",
        "empty-content", "empty-path"])
def test_incomplete_payloads_are_ignored_rather_than_guessed_at(payload):
    """Missing a path or a body means there is nothing to judge. Anything other than a
    silent exit 0 here fires on unrelated tool calls that happen to share the matcher."""
    r = run("", payload=payload)
    assert r.returncode == PROCEED
    assert r.stderr == ""


def test_a_multiedit_payload_produces_no_warning():
    """DOCUMENTS A REAL GAP, not a desired behaviour. The hook is wired on MultiEdit, but
    MultiEdit sends an `edits` list -- no `content`, no `new_string` -- so the hook reads
    nothing and exits. Locking the current behaviour in means a future fix has to come
    here and change this test deliberately."""
    payload = {"tool_input": {"file_path": PROJECT_PATH,
                              "edits": [{"old_string": "x", "new_string": DIRTY}]}}
    r = run("", payload=payload)
    assert r.returncode == PROCEED
    assert r.stderr == ""


def test_only_the_first_scoped_header_in_a_file_is_examined():
    """DOCUMENTS A REAL GAP. find_key_achievements_block takes headers[0] only, so a file
    whose first scoped header is a clean '## Impact' hides every overclaim in a later
    '## Key Achievements'. Real project files carry more than one of these headers."""
    content = (
        "## Impact\n\n"
        "- Reduced time-to-quote by 30%\n\n"
        "## Key Achievements\n\n"
        "- Built the pricing model\n"
    )
    assert not warned(run(PROJECT_PATH, content))


def test_a_test_fixture_under_data_projects_is_still_examined():
    """DOCUMENTS A REAL GAP. HOOK_AUTHORING requires a content hook to exclude its own
    fixtures; this one has no path exclusions, so any fixture path ending in
    data/projects/<name>.md is judged. Harmless while the hook is warn-only -- it becomes
    a suite-blocking false positive the moment anyone promotes it to exit 2."""
    assert warned(run("/repo/tests/fixtures/data/projects/sample.md", DIRTY))


# --- the assertion that has to fail first ----------------------------------

def test_the_hook_exists_and_its_pattern_lists_are_not_empty():
    """Every 'is not warned about' assertion above is satisfied by a hook that warns about
    nothing -- an emptied verb list, a scope regex that matches no header, or a moved file
    would turn most of this module green while the guard did nothing. This is the check
    that fails first, at both levels: the constants, and one end-to-end canary through the
    real CLI."""
    assert HOOK.is_file(), f"hook not found at {HOOK}"

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import check_data_projects as mod

    verbs = ["Built", "Designed", "Architected", "Created", "Founded", "Owned", "Led",
             "Drove", "Established", "Pioneered", "Spearheaded", "Launched", "Invented"]
    missing = [v for v in verbs if v not in mod.OVERCLAIM_VERBS.pattern]
    assert not missing, f"verbs dropped from OVERCLAIM_VERBS: {missing}"
    assert mod.OVERCLAIM_VERBS.search("- Built the thing\n")
    assert mod.SCOPE_HEADERS.search("## Key Achievements\n")
    assert mod.TARGET.search("data/projects/x.md")

    canary = run(PROJECT_PATH, DIRTY)
    assert WARN_MARKER in canary.stderr, (
        "the CLI produced no warning for a known-overclaiming payload; every negative "
        "assertion in this module is vacuous")
