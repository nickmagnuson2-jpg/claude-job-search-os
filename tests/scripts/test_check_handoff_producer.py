"""The handoff-producer gate: creation is closed, annotation is not the bar.

Every Codex finding from the 2026-09-15 plan review has a named regression here, because
each of them was a design that LOOKED right and would have shipped a gate that does not gate:

  F1 -- a self-asserted banner must NOT authorize creating a new handoff.
  F2 -- `cp`/`mv`/`rsync` destinations must be seen, which the PII extractor omits by design.
  F3 -- an edit must not be able to strip a banner off a file that has one.
  F4 -- the hook and the enumerating test must share ONE path predicate.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_handoff_producer as hp  # noqa: E402

SCRIPT = REPO_ROOT / "tools" / "check_handoff_producer.py"

GOOD = ('<!-- DISPOSITION: {"status":"superseded","kind":"work-landed",'
        '"successor":"data/workstreams/mutation.md","date":"2026-09-15"} -->')
REFERENCE = '<!-- DISPOSITION: {"status":"reference","date":"2026-09-15"} -->'


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "output" / "analysis").mkdir(parents=True)
    (tmp_path / "data" / "workstreams").mkdir(parents=True)
    return tmp_path


def existing(repo: Path, rel: str, body: str) -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# --- F1: creation is closed, and a banner does not open it -------------------

def test_a_new_handoff_is_blocked(repo):
    msg = hp.decide("Write", {"file_path": "output/analysis/091526-session-handoff.md",
                              "content": "# notes"}, root=repo)
    assert msg and "creates a NEW handoff-shaped file" in msg


def test_a_new_handoff_is_blocked_EVEN_WITH_a_valid_banner(repo):
    """F1, the P0. The cheapest correction a model reaches for is to add the field.

    If this test ever goes green by allowing the write, the gate has silently degraded from
    redirection to annotation and the handoff count will climb with better metadata.
    """
    msg = hp.decide("Write", {"file_path": "output/analysis/091526-session-handoff.md",
                              "content": GOOD + "\n# notes"}, root=repo)
    assert msg is not None
    assert "creates a NEW handoff-shaped file" in msg


def test_a_new_handoff_is_blocked_even_when_self_declared_reference(repo):
    msg = hp.decide("Write", {"file_path": "output/analysis/091526-x-kickoff.md",
                              "content": REFERENCE}, root=repo)
    assert msg is not None


def test_the_block_message_names_the_admission_test_and_the_registry(repo):
    """A block that does not say where the state SHOULD go just gets reworded around."""
    msg = hp.decide("Write", {"file_path": "output/analysis/a-handoff.md", "content": ""},
                    root=repo)
    assert "data/workstreams/" in msg
    assert "DECISION OPEN ACROSS SESSIONS" in msg
    assert "todo_write.py" in msg


# --- the allow side: existing files stay editable ----------------------------

def test_editing_an_existing_stamped_handoff_is_allowed(repo):
    existing(repo, "output/analysis/a-handoff.md", GOOD + "\n# body")
    assert hp.decide("Edit", {"file_path": "output/analysis/a-handoff.md",
                              "old_string": "# body", "new_string": "# body two"},
                     root=repo) is None


def test_stamping_an_unstamped_handoff_is_allowed(repo):
    """The migration pass has to be able to land, or the gate blocks its own remediation."""
    existing(repo, "output/analysis/a-handoff.md", "# body")
    assert hp.decide("Edit", {"file_path": "output/analysis/a-handoff.md",
                              "old_string": "# body", "new_string": GOOD + "\n# body"},
                     root=repo) is None


def test_a_non_handoff_file_is_never_judged(repo):
    assert hp.decide("Write", {"file_path": "output/analysis/090826-defects.md",
                               "content": "x"}, root=repo) is None


def test_a_workstream_file_is_never_judged(repo):
    assert hp.decide("Write", {"file_path": "data/workstreams/mutation.md",
                               "content": "x"}, root=repo) is None


def test_an_unrelated_tool_is_never_judged(repo):
    assert hp.decide("Read", {"file_path": "output/analysis/a-handoff.md"}, root=repo) is None


# --- F3: an edit must not be able to strip the banner ------------------------

def test_an_edit_that_deletes_the_banner_is_blocked(repo):
    existing(repo, "output/analysis/a-handoff.md", GOOD + "\n# body")
    msg = hp.decide("Edit", {"file_path": "output/analysis/a-handoff.md",
                             "old_string": GOOD + "\n", "new_string": ""}, root=repo)
    assert msg and "removes or invalidates the DISPOSITION banner" in msg


def test_an_edit_that_corrupts_a_required_field_is_blocked(repo):
    existing(repo, "output/analysis/a-handoff.md", GOOD + "\n# body")
    broken = ('<!-- DISPOSITION: {"status":"superseded","successor":"x.md"} -->')
    msg = hp.decide("Edit", {"file_path": "output/analysis/a-handoff.md",
                             "old_string": GOOD, "new_string": broken}, root=repo)
    assert msg and "kind" in msg


def test_a_multiedit_is_judged_on_the_composed_result(repo):
    """All edits apply in order; the LAST one removing the banner must still be caught."""
    existing(repo, "output/analysis/a-handoff.md", GOOD + "\n# body")
    msg = hp.decide("MultiEdit", {"file_path": "output/analysis/a-handoff.md", "edits": [
        {"old_string": "# body", "new_string": "# body two"},
        {"old_string": GOOD + "\n", "new_string": ""},
    ]}, root=repo)
    assert msg is not None


def test_a_full_rewrite_that_drops_the_banner_is_blocked(repo):
    """Write over an existing stamped file is a rewrite, not a creation, and can strip too."""
    existing(repo, "output/analysis/a-handoff.md", GOOD + "\n# body")
    assert hp.decide("Write", {"file_path": "output/analysis/a-handoff.md",
                               "content": "# body only"}, root=repo) is not None


def test_a_full_rewrite_that_keeps_the_banner_is_allowed(repo):
    existing(repo, "output/analysis/a-handoff.md", GOOD + "\n# body")
    assert hp.decide("Write", {"file_path": "output/analysis/a-handoff.md",
                               "content": GOOD + "\n# rewritten"}, root=repo) is None


# --- F2: Bash destinations, including the copy family ------------------------

@pytest.mark.parametrize("command", [
    "echo x > output/analysis/a-handoff.md",
    "echo x >> output/analysis/a-handoff.md",
    "cat t | tee output/analysis/a-handoff.md",
    "cp /tmp/scratch.md output/analysis/a-handoff.md",
    "mv /tmp/scratch.md output/analysis/a-handoff.md",
    "rsync -a /tmp/scratch.md output/analysis/a-handoff.md",
    "ls; cp /tmp/s.md output/analysis/091526-next-session-plan.md",
    # Regression, found by the FIRST LIVE SMOKE of this hook while 68 unit tests passed:
    # a trailing redirection becomes the last token, so the real destination is skipped.
    "cp /tmp/s.md output/analysis/a-handoff.md 2>&1",
    "cp /tmp/s.md output/analysis/a-handoff.md > /tmp/log",
    "mv /tmp/s.md output/analysis/a-handoff.md 2>/dev/null",
])
def test_bash_writes_that_create_a_handoff_are_blocked(repo, command):
    assert hp.decide("Bash", {"command": command}, root=repo) is not None


@pytest.mark.parametrize("command", [
    'grep -rn "cp " docs/',                                  # the verb as a grep pattern
    'git commit -m "moved the handoff to output/analysis"',  # the word in a message
    "cp /tmp/a.md output/analysis/090826-defects.md",        # a real copy, not handoff-shaped
    "echo x > data/workstreams/mutation.md",                 # the correct destination
    "cp /tmp/a.md /tmp/b.md",                                # nothing under output/
    "git log --oneline",
])
def test_bash_commands_that_are_not_handoff_creation_are_allowed(repo, command):
    assert hp.decide("Bash", {"command": command}, root=repo) is None


def test_a_bash_write_to_an_EXISTING_handoff_is_allowed(repo):
    existing(repo, "output/analysis/a-handoff.md", GOOD)
    assert hp.decide("Bash", {"command": "echo x >> output/analysis/a-handoff.md"},
                     root=repo) is None


def test_the_copy_family_is_not_matched_as_a_substring():
    """Command-position, never substring: `scp`, `cpio`, `--copy` must not read as `cp`."""
    assert hp.copy_destinations("scp a.md output/analysis/b-handoff.md") == []
    assert hp.copy_destinations("mycp a.md output/analysis/b-handoff.md") == []


def test_a_copy_with_no_destination_is_not_a_target():
    """A lone token is a source, not a destination; inventing one would over-block."""
    assert hp.copy_destinations("cp output/analysis/a-handoff.md") == []


# --- F4: the shared predicate ------------------------------------------------

@pytest.mark.parametrize("rel,expected", [
    ("output/analysis/081326-build-state-HANDOFF.md", True),
    ("output/analysis/090226-NEXT-SESSION-HANDOFF.md", True),
    ("output/analysis/082026-next-session-plan.md", True),
    ("output/analysis/090826-INBOX-DRAIN-KICKOFF.md", True),
    ("output/090726-session-handoff-metrics.md", True),   # flat output/ is infrastructure too
    ("./output/analysis/a-handoff.md", True),       # leading ./ normalizes
    ("output/analysis/090826-defects.md", False),
    ("output/analysis/a-handoff.txt", False),       # markdown only
    ("docs/a-handoff.md", False),                   # outside output/
    ("tests/fixtures/output/a-handoff.md", False),  # never judge our own fixtures
    # Company-prep artifacts are a DIFFERENT CLASS and are deliberately exempt.
    ("output/acme-corp/073026-HANDOFF.md", False),
    ("output/example-ventures/063026-drill-kickoff.md", False),
    ("output/acme-corp/probes/handoff-1-idempotency.md", False),
])
def test_is_handoff_shaped(rel, expected):
    assert hp.is_handoff_shaped(rel) is expected


def test_the_predicate_matches_the_real_corpus():
    """Coverage, not self-consistency: the predicate must capture the files it was built for.

    A detector can be perfectly consistent about what it sees and silently miss the rest.
    """
    live = REPO_ROOT / "output" / "analysis"
    if not live.is_dir():
        pytest.skip("output/ is gitignored and absent here")
    names = {p.name for p in live.glob("*.md")}
    for known in ["090226-NEXT-SESSION-HANDOFF.md", "081126-session-handoff.md",
                  "090826-INBOX-DRAIN-KICKOFF.md", "082026-next-session-plan.md"]:
        if known in names:
            assert hp.is_handoff_shaped(f"output/analysis/{known}")
    # and a near-miss in the same directory stays out
    assert not hp.is_handoff_shaped("output/analysis/090826-infrastructure-defects.md")


# --- the banner schema itself ------------------------------------------------

@pytest.mark.parametrize("banner,ok", [
    ({"status": "superseded", "kind": "work-landed", "successor": "x.md"}, True),
    ({"status": "absorbed", "successor": "x.md"}, True),
    ({"status": "reference"}, True),
    ({"status": "live", "successor": "x.md"}, True),
    ({"status": "superseded", "successor": "x.md"}, False),        # kind missing
    ({"status": "superseded", "kind": "invented", "successor": "x.md"}, False),
    ({"status": "absorbed"}, False),                               # successor missing
    ({"status": "retired", "successor": "x.md"}, False),           # status not in vocabulary
    (None, False),
])
def test_banner_errors(banner, ok):
    assert (hp.banner_errors(banner) == []) is ok


# --- the guards, unit-tested directly ---------------------------------------
#
# Each of these killed a mutant that the behavioural tests above could not see. The L265
# case is the sharpest: `test_stamping_an_unstamped_handoff_is_allowed` cannot see its
# target, because the edit it uses INTRODUCES a valid banner, so the real code (early
# return) and the mutant (fall through, validate, find a valid banner) agree for different
# reasons. A test whose input succeeds twice is structurally incapable of seeing the guard,
# and no assertion is strong enough to fix it -- only a different INPUT is.

def test_an_ordinary_edit_to_an_UNSTAMPED_handoff_is_allowed(repo):
    """The L265 killer: the file has no banner before OR after, so the early return is the
    only thing that allows this edit."""
    existing(repo, "output/analysis/a-handoff.md", "# body\nmore text")
    assert hp.decide("Edit", {"file_path": "output/analysis/a-handoff.md",
                              "old_string": "more text", "new_string": "other text"},
                     root=repo) is None


def test_relativize_of_an_empty_path_is_empty(repo):
    """Without the guard, Path('') resolves to the repo root and returns '.', which
    is_handoff_shaped would then judge."""
    assert hp.relativize("", root=repo) == ""


def test_relativize_handles_a_path_outside_the_repo(repo):
    assert hp.relativize("/etc/hosts", root=repo) == "/etc/hosts"


def test_apply_edits_skips_malformed_entries_instead_of_raising():
    """A non-dict entry, a missing old_string, and a non-string new_string must all be
    stepped over. Without the guards these raise, and a raising hook fails open silently."""
    text = "hello world"
    assert hp.apply_edits(text, {"edits": ["not a dict", 17, None]}) == text
    assert hp.apply_edits(text, {"edits": [{"new_string": "x"}]}) == text
    assert hp.apply_edits(text, {"edits": [{"old_string": "", "new_string": "x"}]}) == text
    assert hp.apply_edits(text, {"edits": [{"old_string": "hello", "new_string": 17}]}) == text


def test_a_missing_new_string_means_deletion():
    """Claude Code's Edit omits new_string to delete, so None must mean "" -- NOT skip.
    This is the pair to the guard above: 17 is malformed, None is a deletion."""
    assert hp.apply_edits("hello world", {"old_string": "hello ", "new_string": None}) == "world"


def test_apply_edits_applies_a_valid_edit_after_a_malformed_one():
    """The skip must be a `continue`, not an abort -- otherwise one bad entry hides the rest."""
    assert hp.apply_edits("hello world", {"edits": [
        {"old_string": None}, {"old_string": "world", "new_string": "there"}]}) == "hello there"


def test_apply_edits_replace_all():
    assert hp.apply_edits("a a a", {"old_string": "a", "new_string": "b",
                                    "replace_all": True}) == "b b b"
    assert hp.apply_edits("a a a", {"old_string": "a", "new_string": "b"}) == "b a a"


def test_an_unresolvable_shell_destination_is_not_a_target():
    """`$DEST` cannot be resolved at hook time, so treating it as a path would over-block.

    The backtick case is the one that bit: `cp a.md `echo x`` splits to a final token of
    "x`", where the backtick TRAILS, so a startswith() check misses it and `_unquote` leaves
    it in place -- yielding the plausible-looking path "x`".
    """
    assert hp.copy_destinations("cp a.md $DEST") == []
    assert hp.copy_destinations("cp a.md `echo x`") == []
    assert hp.copy_destinations("cp a.md out/$X/f.md") == []


def test_an_empty_quoted_destination_is_not_a_target():
    """`cp a.md ""` unquotes to the empty string, which is not a path."""
    assert hp.copy_destinations('cp a.md ""') == []


def test_a_non_dict_tool_input_is_not_judged(repo):
    """A malformed payload must not crash the gate into failing open."""
    assert hp.decide("Write", None, root=repo) is None
    assert hp.decide("Write", "not a dict", root=repo) is None


def test_an_unparseable_banner_is_not_a_banner():
    assert hp.parse_banner("<!-- DISPOSITION: {not json} -->") is None
    assert hp.parse_banner("<!-- DISPOSITION: [1,2] -->") is None
    assert hp.parse_banner("no banner here") is None


# --- the shipped surface, not just the helper --------------------------------

def _run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)], input=json.dumps(payload), text=True,
        capture_output=True, env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin",
                                  "PYTHONPATH": str(REPO_ROOT / "tools")})


def test_the_cli_blocks_with_exit_2_and_an_explanation():
    """A guarantee the CLI advertises needs a test that RUNS the CLI."""
    r = _run({"tool_name": "Write",
              "tool_input": {"file_path": "output/analysis/zzz-new-handoff.md",
                             "content": "x"}})
    assert r.returncode == 2
    assert "data/workstreams/" in r.stderr


def test_the_cli_allows_an_ordinary_write():
    r = _run({"tool_name": "Write", "tool_input": {"file_path": "docs/usage.md",
                                                   "content": "x"}})
    assert r.returncode == 0
    assert r.stderr == ""


def test_an_unreadable_payload_fails_OPEN():
    r = subprocess.run([sys.executable, str(SCRIPT)], input="not json", text=True,
                       capture_output=True,
                       env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin",
                            "PYTHONPATH": str(REPO_ROOT / "tools")})
    assert r.returncode == 0


def test_a_truncated_payload_fails_CLOSED():
    """The one case where failing open is a silent bypass: oversize the payload and the
    gate would pass without ever reading what it exists to inspect."""
    huge = {"tool_name": "Write",
            "tool_input": {"file_path": "output/analysis/zzz-new-handoff.md",
                           "content": "x" * (2 * 1024 * 1024)}}
    r = _run(huge)
    assert r.returncode == 2
    assert "truncated" in r.stderr.lower()
