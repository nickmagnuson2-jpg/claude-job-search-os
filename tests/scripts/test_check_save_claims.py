#!/usr/bin/env python3
"""Tests for tools/check_save_claims.py — the Stop hook that catches
"I saved it to X" when X was never written.

Covers the clean/block pair required by tools/HOOK_AUTHORING.md, plus the
false-positive classes that would make the hook unusable if it fired on them.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "tools" / "check_save_claims.py"

spec = importlib.util.spec_from_file_location("csc", HOOK)
csc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csc)


def extract(text):
    return csc.extract_claimed_paths(text)


# ---------- extraction: should CATCH ----------

def test_a_bare_name_with_an_unlisted_extension_is_not_a_claim():
    """No slash and an extension outside the list (config.toml, v1.2): not a file claim."""
    assert extract("I saved config.toml for later.") == []


def test_a_path_with_an_unlisted_extension_is_still_a_claim():
    """A slash makes it a path whatever the extension."""
    assert extract("I saved output/acme/config.toml for later.") == ["output/acme/config.toml"]


def test_a_bare_name_with_a_listed_extension_is_a_claim():
    assert extract("I saved notes.md for later.") == ["notes.md"]


def test_an_unlisted_bare_name_does_not_hide_the_next_real_path():
    """The skip moves on to the next token; it does not end the search."""
    assert extract("I saved config.toml and output/acme/brief.md today.") == [
        "output/acme/brief.md"]


def test_a_file_named_inside_a_quoted_command_is_not_a_claim():
    """2026-10-26, two false blocks: the file a command would touch is not a written file."""
    assert extract("I wrote the test for `cp /tmp/x-handoff.md output/analysis/` today.") == []


def test_a_quoted_path_with_a_space_is_still_a_claim():
    """codex, 2026-10-26: a real file whose name holds a space is not a command."""
    # The path matcher stops at the space, so the claim is read as its tail; what matters
    # is that the span is not dropped as a command.
    assert extract("I saved `output/acme/my brief.md` today.") == ["brief.md"]


def test_a_bare_code_span_after_a_command_span_is_still_a_claim():
    assert extract("I wrote `cp a.md b/` and then saved `output/acme/brief.md`.") == [
        "output/acme/brief.md"]


def test_code_spans_pair_in_order_so_text_between_them_is_not_a_command():
    """Spans found WITH whitespace only would pair `a.md`'s closing tick with `b.md`'s
    opening one and hide b.md; finding every span in order keeps the pairing right."""
    assert extract("I wrote `a.md` and `output/b.md` today.") == ["a.md"]
    assert extract("I wrote `x` then `output/acme/b.md` here.") == ["output/acme/b.md"]


def test_a_claim_verb_inside_a_filename_in_a_command_span_opens_no_window():
    assert extract("Run `cp tools/foo.SAVED-bar.py out/` then check data/x.md.") == []


def test_catches_saved_to():
    assert "output/acme/foo.md" in extract("Saved to `output/acme/foo.md` — done.")


def test_catches_written_to():
    assert "tools/bar.py" in extract("The script is written to tools/bar.py now.")


def test_catches_wrote():
    assert "data/x.md" in extract("I wrote data/x.md with the full transcript.")


def test_catches_created():
    assert "a/b/c.html" in extract("Created a/b/c.html and rendered it.")


def test_catches_rendered():
    assert "deck.pdf" in extract("Rendered deck.pdf at 15 pages.")


def test_catches_multiple_distinct_claims():
    got = extract("Wrote tools/a.py. Also saved to tools/b.py for later.")
    assert "tools/a.py" in got and "tools/b.py" in got


# ---------- extraction: should NOT catch (false-positive guards) ----------

def test_ignores_future_tense():
    assert extract("I'll save this to output/acme/new.md next.") == []


def test_ignores_would():
    assert extract("That would be written to tools/nope.py.") == []


def test_ignores_you_can():
    assert extract("You can save it to output/whatever.md yourself.") == []


def test_ignores_negation():
    assert extract("I have not written tools/missing.py yet.") == []


def test_ignores_fenced_code_blocks():
    text = "Here is the script:\n```python\nopen('tools/inside_block.py')\n# wrote tools/x.py\n```\n"
    assert extract(text) == []


def test_ignores_urls():
    assert extract("Saved to https://example.com/thing.html for reference.") == []


def test_ignores_version_numbers():
    assert extract("Rendered with Python 3.11 support.") == []


# ---------- end-to-end hook behaviour ----------

def _run(transcript_text, tmpdir, stop_active=False):
    """Build a one-message transcript and run the hook against it."""
    tpath = Path(tmpdir) / "t.jsonl"
    rec = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": transcript_text}]},
    }
    tpath.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    payload = {"transcript_path": str(tpath), "stop_hook_active": stop_active}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PYTHONIOENCODING": "utf-8", "CLAUDE_PROJECT_DIR": tmpdir, "PATH": "/usr/bin:/bin"},
    )


def test_blocks_when_claimed_file_missing():
    with tempfile.TemporaryDirectory() as td:
        r = _run("Saved to output/ghost.md — all set.", td)
        assert r.returncode == 2
        assert "output/ghost.md" in r.stderr
        assert "BLOCKED check_save_claims" in r.stderr


def test_clean_when_claimed_file_exists():
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "real.md").write_text("hi", encoding="utf-8")
        r = _run("Saved to real.md — all set.", td)
        assert r.returncode == 0, r.stderr


def test_recursion_guard():
    with tempfile.TemporaryDirectory() as td:
        r = _run("Saved to output/ghost.md.", td, stop_active=True)
        assert r.returncode == 0


def test_missing_transcript_is_noop():
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"transcript_path": "/nonexistent/x.jsonl"}),
        capture_output=True, text=True,
        env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"},
    )
    assert r.returncode == 0


def test_malformed_stdin_is_noop():
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json", capture_output=True, text=True,
        env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"},
    )
    assert r.returncode == 0


def test_override_env_bypasses():
    with tempfile.TemporaryDirectory() as td:
        tpath = Path(td) / "t.jsonl"
        rec = {"type": "assistant", "message": {"role": "assistant",
               "content": [{"type": "text", "text": "Saved to output/ghost.md."}]}}
        tpath.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"transcript_path": str(tpath)}),
            capture_output=True, text=True,
            env={"PYTHONIOENCODING": "utf-8", "CLAUDE_PROJECT_DIR": td,
                 "SAVE_CLAIMS_OVERRIDE": "1", "PATH": "/usr/bin:/bin"},
        )
        assert r.returncode == 0


def test_regression_the_actual_incident():
    """The exact sentence from 2026-08-04 that started this."""
    with tempfile.TemporaryDirectory() as td:
        r = _run(
            "Saved to `output/acme/010526-casey-sim-prompt.md`. "
            "Paste this into the Claude App and switch to voice:", td)
        assert r.returncode == 2
        assert "010526-casey-sim-prompt.md" in r.stderr


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", __file__]))


# ---------- bare-basename resolution (FP class found in live smoke 2026-08-04) ----------

def test_bare_basename_found_in_subdirectory_is_clean():
    """We refer to files by name without their directory constantly. A bare
    basename that exists one level down must NOT block."""
    with tempfile.TemporaryDirectory() as td:
        sub = Path(td) / "output" / "acme"
        sub.mkdir(parents=True)
        (sub / "some-doc.md").write_text("x", encoding="utf-8")
        r = _run("Saved to some-doc.md — updated, no longer stale.", td)
        assert r.returncode == 0, r.stderr


def test_bare_basename_genuinely_absent_still_blocks():
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "output").mkdir()
        r = _run("Saved to phantom-doc.md just now.", td)
        assert r.returncode == 2
        assert "phantom-doc.md" in r.stderr


def test_wrong_directory_still_blocks():
    """A path WITH a directory component is checked exactly — a wrong
    directory is itself a defect worth catching."""
    with tempfile.TemporaryDirectory() as td:
        real = Path(td) / "output" / "acme"
        real.mkdir(parents=True)
        (real / "doc.md").write_text("x", encoding="utf-8")
        r = _run("Saved to coaching/doc.md for later.", td)
        assert r.returncode == 2


# --- Regression: claim verbs embedded inside filenames (2026-09-01, 3rd fire) -------
# CLAIM_RE had no word boundaries, so a verb matched INSIDE an identifier. Because the
# path window starts at the verb's end, the extracted token was the tail of the word that
# contained it, producing a path that appeared nowhere in the message and existed nowhere
# on disk — while the file it was really about had been written correctly.

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "tools"))
import check_save_claims as _csc  # noqa: E402


class TestClaimVerbEmbeddedInFilename:
    def test_rendered_inside_filename_yields_no_path(self):
        got = _csc.extract_claimed_paths(
            "- `reference_recovering_js_rendered_share_links.md`")
        assert got == [], f"phantom path from embedded 'rendered': {got!r}"

    def test_created_inside_filename_yields_no_path(self):
        got = _csc.extract_claimed_paths(
            "- `feedback_an_artifact_created_by_what_you_are_verifying_is_not_evidence.md`")
        assert got == [], f"phantom path from embedded 'created': {got!r}"

    def test_no_extracted_token_ever_starts_with_a_separator(self):
        """The truncation signature: a token beginning with _ , - or . is a tail,
        never a real filename anyone would write."""
        for text in (
            "- `reference_recovering_js_rendered_share_links.md`",
            "- `feedback_an_artifact_created_by_what_you_are_verifying.md`",
            "- `notes_saved_by_hand.md`",
            "- `report_written_up.md`",
            "- `x_added_later.md`",
            "- `y_copied_over.md`",
            "- `z_placed_here.md`",
            "- `w_persisted_state.md`",
            "- `v_wrote_up.md`",
        ):
            for tok in _csc.extract_claimed_paths(text):
                assert not tok.startswith(("_", "-", ".")), \
                    f"truncated token {tok!r} from {text!r}"

    def test_every_claim_verb_is_boundary_anchored(self):
        """Each verb in the vocabulary, embedded in an identifier, must not match."""
        for verb in ("saved", "written", "wrote", "created", "persisted",
                     "rendered", "copied", "placed", "added"):
            text = f"- `prefix_{verb}_suffix.md`"
            assert _csc.extract_claimed_paths(text) == [], \
                f"verb {verb!r} matched inside an identifier"

    def test_real_claims_are_still_detected(self):
        """The fix must not blind the hook to genuine claims."""
        for text, expect in (
            ("I wrote tools/real_file.py just now.", ["tools/real_file.py"]),
            ("Saved data/notes.md for you.", ["data/notes.md"]),
            ("Created output/x/report.md.", ["output/x/report.md"]),
            ("Persisted output/y/state.json already.", ["output/y/state.json"]),
        ):
            assert _csc.extract_claimed_paths(text) == expect, text

    def test_verb_adjacent_to_punctuation_still_matches(self):
        """\\b must not break normal prose where the verb abuts punctuation."""
        assert _csc.extract_claimed_paths(
            '(Wrote) tools/x.py.') == ["tools/x.py"]


# --- a claim does not reach across a paragraph break ------------------------

def test_a_filename_in_the_next_paragraph_is_not_the_referent():
    """The 3rd-fire false positive (friction ledger 2026-09-01..02). A /lessons-learned
    proposal names candidate files that correctly do not exist yet -- the skill mandates
    proposing before writing -- and a claim verb ending the previous paragraph swallowed
    the next paragraph's heading. The sentence rule could not cut it: after "class."
    comes "\\n\\n**[3]", and `*` is not `[A-Z]`."""
    text = ("...in a file written specifically to catch this class.\n"
            "\n"
            "**[3] NEW — `feedback_some_proposed_rule.md`**\n")
    assert "feedback_some_proposed_rule.md" not in csc.extract_claimed_paths(text)


def test_a_file_label_line_is_not_the_referent_of_an_earlier_claim_verb():
    # /lessons-learned lists proposed target files as "File: <name>" lines; a claim
    # verb earlier in the block ("I copied it") must not reach down into that label.
    text = ('[1] NEW feedback - x\n    Source: "I copied it"\n    File: feedback_some_proposed_rule.md\n\nSave these?')
    assert "feedback_some_proposed_rule.md" not in csc.extract_claimed_paths(text)


def test_a_claim_still_finds_its_referent_in_the_same_paragraph():
    """The cut must not be so eager that real claims stop being caught."""
    assert "docs/notes.md" in csc.extract_claimed_paths("Saved to docs/notes.md just now.")


def test_a_claim_finds_a_referent_on_the_next_line_of_the_same_paragraph():
    """A single newline is not a paragraph break; wrapping must not defeat the check."""
    got = csc.extract_claimed_paths("I wrote the summary to\ndocs/notes.md for you.")
    assert "docs/notes.md" in got


def test_two_claims_in_separate_paragraphs_each_keep_their_own_referent():
    text = ("Saved to docs/first.md here.\n\nAlso wrote docs/second.md there.\n")
    got = csc.extract_claimed_paths(text)
    assert "docs/first.md" in got and "docs/second.md" in got


def test_claim_verb_inside_a_filename_does_not_open_a_window():
    """A verb between a dot and a hyphen is \\b-delimited but is NOT a claim.

    `\\b` guards against underscores (word chars) but not dots and hyphens, which are
    what filenames are made of. Before the fix, `...schedule.SAVED-noaa-090226.html`
    matched `\\bsaved\\b` inside the name, the window opened after the verb, and the hook
    blocked on the fragment `-noaa-090226.html`. Fired 2026-09-02.
    """
    got = csc.extract_claimed_paths(
        "- `tide-chart-schedule.SAVED-noaa-090226.html` on disk"
    )
    assert "-noaa-090226.html" not in got


def test_a_dotted_filename_is_still_reported_whole_when_really_claimed():
    """The fix must not swallow a genuine claim whose path contains a verb."""
    got = csc.extract_claimed_paths("I wrote tools/foo.SAVED-bar.py just now.")
    assert "tools/foo.SAVED-bar.py" in got


def test_a_filename_containing_a_verb_alone_is_not_a_claim():
    got = csc.extract_claimed_paths(
        "The file baseline.pre-bytecode-fix-083126.jsonl is the old one."
    )
    assert got == []


# ---------- peer-repo resolution (friction ladder 3rd rung, 2026-09-04) ----------
# Sibling projects live beside this repo (30-projects/job-search, .../peer-repo,
# .../personal) and prose names their files repo-relatively. Resolving those against
# CLAUDE_PROJECT_DIR reported genuine cross-repo writes as false claims, 3 times.

def _peer_layout(tmp):
    """Build 30-projects/{job-search,peer-repo} and return (root, peer)."""
    projects = Path(tmp) / "30-projects"
    root = projects / "job-search"
    peer = projects / "peer-repo" / "data"
    peer.mkdir(parents=True)
    root.mkdir(parents=True)
    return root, peer


def test_peer_repo_file_that_exists_is_not_reported_missing():
    with tempfile.TemporaryDirectory() as tmp:
        root, peer = _peer_layout(tmp)
        (peer / "method.md").write_text("x", encoding="utf-8")
        assert csc.exists_anywhere("peer-repo/data/method.md", root) is True


def test_peer_repo_file_that_does_not_exist_is_still_reported_missing():
    """The guard must not blanket-pass anything with a sibling-looking prefix."""
    with tempfile.TemporaryDirectory() as tmp:
        root, _ = _peer_layout(tmp)
        assert csc.exists_anywhere("peer-repo/data/never-written.md", root) is False


def test_unknown_first_segment_is_not_treated_as_a_peer_repo():
    with tempfile.TemporaryDirectory() as tmp:
        root, _ = _peer_layout(tmp)
        assert csc.exists_anywhere("not-a-sibling/data/method.md", root) is False


def test_a_dotdot_token_is_checked_only_where_it_points():
    """'../x' is joined onto root and nowhere else: no sibling or skills candidate."""
    with tempfile.TemporaryDirectory() as tmp:
        root, _ = _peer_layout(tmp)
        tok = "../peer-repo/data/method.md"
        assert csc.candidate_paths(tok, root) == [root / tok]


def test_own_repo_name_prefix_resolves_to_the_same_real_file():
    """'job-search/tools/x.py' written from inside job-search names the SAME file,
    because parent/tok == root/tools/x.py. It should pass when the file exists."""
    with tempfile.TemporaryDirectory() as tmp:
        root, _ = _peer_layout(tmp)
        (root / "tools").mkdir()
        (root / "tools" / "real.py").write_text("x", encoding="utf-8")
        assert csc.exists_anywhere("job-search/tools/real.py", root) is True


def test_own_repo_name_prefix_still_fails_when_the_file_is_absent():
    with tempfile.TemporaryDirectory() as tmp:
        root, _ = _peer_layout(tmp)
        assert csc.exists_anywhere("job-search/tools/nope.py", root) is False


def test_bare_basename_still_takes_the_walk_path_not_the_peer_path():
    with tempfile.TemporaryDirectory() as tmp:
        root, _ = _peer_layout(tmp)
        (root / "found.md").write_text("x", encoding="utf-8")
        assert root.parent / "found.md" not in csc.candidate_paths("found.md", root)
        assert csc.exists_anywhere("found.md", root) is True


def test_bare_basename_is_not_resolved_against_the_projects_dir():
    """Killing the leading-character guard would let a bare basename match a file
    sitting in the projects directory itself. It must not."""
    with tempfile.TemporaryDirectory() as tmp:
        root, _ = _peer_layout(tmp)
        (root.parent / "found.md").write_text("x", encoding="utf-8")
        assert csc.exists_anywhere("found.md", root) is False


def test_absolute_path_is_not_reresolved_under_the_projects_dir():
    with tempfile.TemporaryDirectory() as tmp:
        root, _ = _peer_layout(tmp)
        assert csc.candidate_paths("/etc/hosts", root) == [Path("/etc/hosts")]


def test_tilde_path_is_not_treated_as_a_peer_reference():
    with tempfile.TemporaryDirectory() as tmp:
        root, _ = _peer_layout(tmp)
        assert csc.candidate_paths("~/Documents/x.md", root) == [Path.home() / "Documents/x.md"]


# --- live memory tier (outside the repo) -------------------------------------
# Origin: 3rd fire of check_save_claims on 2026-09-07. A rule file written to
# ~/.claude/projects/<slug>/memory/ was reported as a fabricated save because
# exists_anywhere only walks the repo root.


def _tier_layout(tmp):
    """A repo root plus its live memory tier under a fake HOME."""
    root = Path(tmp) / "30-projects" / "job-search"
    root.mkdir(parents=True)
    home = Path(tmp) / "home"
    slug = str(root).replace("/", "-")
    tier = home / ".claude" / "projects" / slug / "memory"
    tier.mkdir(parents=True)
    return root, home, tier


def test_memory_tier_bare_basename_that_exists_is_found():
    with tempfile.TemporaryDirectory() as tmp:
        root, home, tier = _tier_layout(tmp)
        (tier / "feedback_x.md").write_text("x", encoding="utf-8")
        assert csc.exists_anywhere("feedback_x.md", root, home) is True


def test_memory_tier_prefixed_form_that_exists_is_found():
    with tempfile.TemporaryDirectory() as tmp:
        root, home, tier = _tier_layout(tmp)
        (tier / "feedback_x.md").write_text("x", encoding="utf-8")
        assert csc.exists_anywhere("memory/feedback_x.md", root, home) is True


def test_memory_tier_file_that_does_not_exist_is_still_missing():
    with tempfile.TemporaryDirectory() as tmp:
        root, home, _tier = _tier_layout(tmp)
        assert csc.exists_anywhere("feedback_never_written.md", root, home) is False
        assert csc.exists_anywhere("memory/feedback_never_written.md", root, home) is False


def test_memory_tier_does_not_relax_multi_segment_tokens():
    """A wrong directory is still a defect; only bare and memory/ forms are accepted."""
    with tempfile.TemporaryDirectory() as tmp:
        root, home, tier = _tier_layout(tmp)
        (tier / "feedback_x.md").write_text("x", encoding="utf-8")
        assert csc.exists_anywhere("tools/feedback_x.md", root, home) is False
        assert csc.exists_anywhere("a/b/feedback_x.md", root, home) is False


def test_memory_tier_rejects_absolute_and_tilde_and_traversal():
    with tempfile.TemporaryDirectory() as tmp:
        root, home, tier = _tier_layout(tmp)
        (tier / "feedback_x.md").write_text("x", encoding="utf-8")
        assert csc.exists_anywhere("/feedback_x.md", root, home) is False
        assert csc.exists_anywhere("~/feedback_x.md", root, home) is False
        assert csc.exists_anywhere("memory/../feedback_x.md", root, home) is False
        assert csc.exists_anywhere("", root, home) is False


def test_memory_tier_absent_tier_is_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        assert csc.exists_anywhere("feedback_x.md", root, Path(tmp) / "no-home") is False


def test_exists_anywhere_finds_memory_tier_file(monkeypatch):
    """The integration path: the hook must not report a real memory write as missing."""
    with tempfile.TemporaryDirectory() as tmp:
        root, home, tier = _tier_layout(tmp)
        (tier / "feedback_y.md").write_text("x", encoding="utf-8")
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        monkeypatch.setattr(csc.Path, "home", staticmethod(lambda: home))
        assert csc.exists_anywhere("feedback_y.md", root) is True
        assert csc.exists_anywhere("feedback_absent.md", root) is False


def test_exists_anywhere_finds_the_tier_under_claude_config_dir(monkeypatch):
    """With no explicit home, the hook looks where Claude Code keeps projects:
    CLAUDE_CONFIG_DIR when set."""
    with tempfile.TemporaryDirectory() as tmp:
        root, home, tier = _tier_layout(tmp)
        (tier / "feedback_c.md").write_text("x", encoding="utf-8")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
        monkeypatch.setattr(csc.Path, "home", staticmethod(lambda: Path(tmp) / "elsewhere"))
        assert csc.exists_anywhere("feedback_c.md", root) is True


def test_memory_tier_bare_prefix_does_not_match_the_directory_itself():
    """`memory/` alone must not resolve to the tier directory and report "exists"."""
    with tempfile.TemporaryDirectory() as tmp:
        root, home, _tier = _tier_layout(tmp)
        assert csc.exists_anywhere("memory/", root, home) is False


def test_memory_tier_rejects_dot_and_dotdot_names():
    """`memory/..` must not resolve to the parent directory and report "exists"."""
    with tempfile.TemporaryDirectory() as tmp:
        root, home, _tier = _tier_layout(tmp)
        assert csc.exists_anywhere("memory/..", root, home) is False
        assert csc.exists_anywhere("memory/.", root, home) is False


def test_memory_tier_directory_does_not_satisfy_a_file_claim():
    """A DIRECTORY named like a file must not convert MISSING to EXISTS.

    Cross-model review 2026-09-07 (20.F1, P1): Path.exists() is true for directories,
    so a claim naming a directory in the tier would pass.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root, home, tier = _tier_layout(tmp)
        (tier / "feedback_looks_like_a_file.md").mkdir()
        assert csc.exists_anywhere("feedback_looks_like_a_file.md", root, home) is False
        assert csc.exists_anywhere("memory/feedback_looks_like_a_file.md", root, home) is False


def test_memory_tier_rejects_a_nested_path_even_when_that_file_really_exists():
    """The memory tier takes exactly two prose forms: a bare basename and a `memory/`
    prefixed one. A deeper path is checked exactly, even when the file genuinely exists
    nested inside the tier; otherwise the tier would become a general suffix matcher.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root, home, tier = _tier_layout(tmp)
        (tier / "sub").mkdir()
        (tier / "sub" / "feedback_z.md").write_text("x", encoding="utf-8")
        assert (tier / "sub" / "feedback_z.md").is_file()
        assert csc.exists_anywhere("sub/feedback_z.md", root, home) is False
        assert csc.exists_anywhere("memory/sub/feedback_z.md", root, home) is False


# --- project_root canonicalization -------------------------------------------
# Cross-model review 2026-09-07 (F1, P1): a relative CLAUDE_PROJECT_DIR produced a
# nonsense memory-tier slug, so the hook searched a directory that cannot exist and
# falsely blocked a real save. Fixed at the point of derivation because every
# candidate path is built from `root` and all are wrong for the same reason.


def test_project_root_makes_a_relative_value_absolute():
    assert csc.project_root("job-search").is_absolute()
    assert csc.project_root(".").is_absolute()


def test_project_root_normalizes_dot_and_dotdot():
    got = csc.project_root("/a/b/../c")
    assert str(got) == "/a/c"
    assert str(csc.project_root("/a/b/./c")) == "/a/b/c"


def test_project_root_expands_tilde():
    got = csc.project_root("~/somewhere")
    assert got.is_absolute()
    assert "~" not in str(got)


def test_project_root_leaves_an_absolute_path_alone():
    assert str(csc.project_root("/Users/x/repo")) == "/Users/x/repo"


def test_project_root_strips_trailing_slash_so_the_slug_has_no_trailing_dash():
    assert str(csc.project_root("/Users/x/repo/")) == "/Users/x/repo"


def test_project_root_reads_the_env_when_no_argument_is_given(monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/x/from-env")
    assert str(csc.project_root()) == "/Users/x/from-env"


def test_relative_root_would_have_searched_the_wrong_tier():
    """The regression itself: a relative root must still find a real memory file."""
    with tempfile.TemporaryDirectory() as tmp:
        root, home, tier = _tier_layout(tmp)
        (tier / "feedback_r.md").write_text("x", encoding="utf-8")
        assert csc.exists_anywhere("feedback_r.md", root, home) is True
        # The un-canonicalized form the hook used to pass through names another tier:
        assert csc.memory_tier(Path("."), home) != tier
        # ...and canonicalizing is what makes it agree with the absolute form.
        assert csc.memory_tier(csc.project_root(str(root)), home) == tier


# ---------- 2026-10-21: short skill paths, and sentences that start with a backtick ----------
# Third fire of the abbreviated-skill-path false positive (friction ledger 2026-10-01,
# 2026-10-21 x2). Two independent causes, each tested on its own.

def test_a_status_note_naming_a_real_short_skill_path_passes(tmp_path):
    """The shape that fired 3x: a claim sentence followed by a status note that names a
    real skill file by its short path. The path is in the claim window (the cut is [A-Z]
    only); it passes because the file exists, not because the window was cut."""
    (tmp_path / ".claude" / "skills" / "other-skill").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "other-skill" / "SKILL.md").write_text("x")
    text = ("Three portable claims added to the coaching file. "
            "`other-skill/SKILL.md` no longer shows as modified.")
    assert "other-skill/SKILL.md" in csc.extract_claimed_paths(text)
    r = _run(text, str(tmp_path))
    assert r.returncode == 0, r.stderr


def test_a_backticked_referent_in_the_same_sentence_is_still_caught():
    assert "docs/notes.md" in csc.extract_claimed_paths("Saved to `docs/notes.md`. Done.")


# Must still block: a claim whose referent opens the NEXT sentence. Widening the cut to
# any non-lowercase start (7497c5d) dropped all three; cross-model review found it.

@pytest.mark.parametrize("text, path", [
    ("I saved the briefing. `output/acme/brief.md` is ready.", "output/acme/brief.md"),
    ("I saved the briefing.\n`output/acme/brief.md` is ready.", "output/acme/brief.md"),
    ("Wrote the analysis. 102126-notes.md is in output/analysis/.", "102126-notes.md"),
])
def test_a_missing_referent_opening_the_next_sentence_blocks(text, path, tmp_path):
    """Run the hook, not just the extractor: 419.F1/421.F1 noted extraction alone
    cannot fail when blocking stops."""
    r = _run(text, str(tmp_path))
    assert r.returncode == 2, r.stderr
    assert path in r.stderr


def test_a_short_skill_path_resolves_under_dot_claude_skills(tmp_path):
    (tmp_path / ".claude" / "skills" / "some-skill").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "some-skill" / "SKILL.md").write_text("x")
    assert csc.exists_anywhere("some-skill/SKILL.md", tmp_path) is True


def test_a_short_skill_path_with_no_file_is_still_missing(tmp_path):
    (tmp_path / ".claude" / "skills" / "some-skill").mkdir(parents=True)
    assert csc.exists_anywhere("some-skill/SKILL.md", tmp_path) is False
    assert csc.exists_anywhere("unknown-skill/SKILL.md", tmp_path) is False


def test_skill_lookup_does_not_escape_via_dotdot_or_absolute(tmp_path):
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / "secret.md").write_text("x")
    assert csc.candidate_paths("../../secret.md", tmp_path) == [tmp_path / "../../secret.md"]
    assert csc.candidate_paths("/etc/hosts", tmp_path) == [Path("/etc/hosts")]


def test_skill_lookup_refuses_a_dotdot_segment_after_a_real_dir(tmp_path):
    """Under skills, foo/../../secret.md would resolve to .claude/secret.md, a real file.
    Only the '..' segment check refuses it; the leading-character check does not apply."""
    (tmp_path / ".claude" / "skills" / "foo").mkdir(parents=True)
    (tmp_path / ".claude" / "secret.md").write_text("x")
    assert csc.exists_anywhere("foo/../../secret.md", tmp_path) is False


def test_skill_lookup_refuses_a_tilde_path(tmp_path):
    """A literal '~' directory under skills would otherwise satisfy '~/x'."""
    (tmp_path / ".claude" / "skills" / "~").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "~" / "x.md").write_text("x")
    assert csc.exists_anywhere("~/x.md", tmp_path) is False


def test_end_to_end_short_skill_path_claim_is_clean(tmp_path):
    (tmp_path / ".claude" / "skills" / "some-skill").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "some-skill" / "SKILL.md").write_text("x")
    text = "I updated and saved some-skill/SKILL.md for you."
    # Exit 0 alone would also pass if nothing were extracted; pin the extraction too.
    assert "some-skill/SKILL.md" in csc.extract_claimed_paths(text)
    r = _run(text, str(tmp_path))
    assert r.returncode == 0, r.stderr


def test_a_directory_named_like_a_file_does_not_satisfy_a_claim(tmp_path):
    """23.F2: the direct check used exists(), so a directory named x.md passed."""
    root = tmp_path / "projects" / "repo"
    (root / "docs" / "x.md").mkdir(parents=True)
    (tmp_path / "projects" / "peer" / "y.md").mkdir(parents=True)
    assert csc.exists_anywhere("docs/x.md", root) is False
    assert csc.exists_anywhere("peer/y.md", root) is False


def test_a_slashed_or_tilde_token_never_walks_the_repo(tmp_path, monkeypatch):
    """Only a bare basename is searched for by name. A token with a directory part is
    checked exactly, and walking the repo for it would cost time and find nothing."""
    def no_walk(*a, **k):
        raise AssertionError("os.walk called for a token that cannot be a basename")
    monkeypatch.setattr(csc.os, "walk", no_walk)
    assert csc.exists_anywhere("docs/missing.md", tmp_path) is False
    assert csc.exists_anywhere("~/missing-xyz.md", tmp_path) is False


def test_a_mid_token_dotdot_still_reaches_a_sibling_repo_but_not_skills(tmp_path):
    """Kept from the pre-2026-10-21 sibling lookup, which accepted "a/../b/x.md";
    the skills place never did."""
    root = tmp_path / "projects" / "repo"
    (tmp_path / "projects" / "b").mkdir(parents=True)
    (tmp_path / "projects" / "a").mkdir()   # the OS resolves "a/.." only if "a" exists
    (tmp_path / "projects" / "b" / "x.md").write_text("x")
    root.mkdir()
    assert csc.exists_anywhere("a/../b/x.md", root) is True
    assert root / ".claude" / "skills" / "a/../b/x.md" not in csc.candidate_paths("a/../b/x.md", root)


# ---------- library names in prose (Next.js, Node.js) ----------

@pytest.mark.parametrize("text", [
    "I wrote the app on Next.js and Postgres and saved the notes.",
    "Built it with Node.js, then committed.",
    "I created the chart with D3.js.",
])
def test_a_capitalized_js_library_name_is_not_a_claimed_file(text):
    assert extract(text) == []


@pytest.mark.parametrize("text,path", [
    ("I wrote plan-hardening.js for the workflow.", "plan-hardening.js"),
    ("Saved to `.claude/workflows/Deck.js` today.", ".claude/workflows/Deck.js"),
    ("I wrote Deck.js for the slides.", "Deck.js"),     # capitalized but not a library
])
def test_a_real_js_file_is_still_a_claimed_file(text, path):
    assert path in extract(text)


# ---------- absence claims contradicted by the filesystem (2026-10-22) ----------
# The inverse of a save claim: "X is missing" when X is on disk. Origin: 2026-06-12,
# `lessons.md` declared gone after checking only the live memory tier; it lives in the
# repo's ./memory/. Fired 4x per feedback_two_memory_locations_check_both_before_absence.

DETECTOR_CONTROL = "lessons.md is missing but cited by 9 files. How do you want to resolve it?"


def _run_with_home(text, root, home):
    """_run with an explicit HOME, so the live memory tier resolves under a tmp dir."""
    tpath = Path(root) / "t.jsonl"
    rec = {"type": "assistant",
           "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    tpath.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"transcript_path": str(tpath)}),
        capture_output=True, text=True,
        env={"PYTHONIOENCODING": "utf-8", "CLAUDE_PROJECT_DIR": str(root),
             "HOME": str(home), "PATH": "/usr/bin:/bin"},
    )


def test_absence_claim_about_a_repo_memory_file_that_exists_blocks(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "lessons.md").write_text("x", encoding="utf-8")
    r = _run(DETECTOR_CONTROL, str(tmp_path))
    assert r.returncode == 2, r.stderr
    assert "lessons.md" in r.stderr
    assert "does not exist" in r.stderr and "both memory roots" in r.stderr


def test_an_absence_only_block_reports_only_the_absence(tmp_path):
    """Kills two mutants: the write-claim section must not print when nothing was
    claimed written, and the false-positive logging hint must always print."""
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "lessons.md").write_text("x", encoding="utf-8")
    r = _run(DETECTOR_CONTROL, str(tmp_path))
    assert r.returncode == 2, r.stderr
    assert "were written" not in r.stderr
    assert "friction_log.py append" in r.stderr


def test_absence_claim_about_a_file_absent_everywhere_is_clean(tmp_path):
    r = _run(DETECTOR_CONTROL, str(tmp_path))
    assert r.returncode == 0, r.stderr


def test_absence_claim_about_a_live_tier_file_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        root, home, tier = _tier_layout(tmp)
        (tier / "feedback_x.md").write_text("x", encoding="utf-8")
        r = _run_with_home("feedback_x.md does not exist, so I will recreate it.", root, home)
        assert r.returncode == 2, r.stderr
        assert "feedback_x.md" in r.stderr
        # Same message, file nowhere: clean. Pins that the block came from the tier lookup.
        (tier / "feedback_x.md").unlink()
        r = _run_with_home("feedback_x.md does not exist, so I will recreate it.", root, home)
        assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("text", [
    "If `data/job-todos.md` doesn't exist, create it with a header.",
    "When data/job-todos.md is missing the skill falls back to defaults.",
    "Check whether data/job-todos.md no longer exists after the move.",
    "Are you sure data/job-todos.md is gone?",
    "lessons.md is missing in ./memory on that clone.",
    "data/job-todos.md is missing from the sidecar.",
    "data/job-todos.md was not found at that path.",
    "Here is the check:\n```\ndata/job-todos.md does not exist\n```\n",
    "> data/job-todos.md is missing\n\nThat quote is from your note.",
    "lessons.md is missing, but only in the sidecar.",
    # Restraint-replay false positives (2026-10-22): the file is not the subject, or
    # the phrase takes an object, or it is a case label.
    "**`data/job-todos.md`'s history is missing the 9/04 entries.**",
    "`data/job-todos.md:28` claims a local file that doesn't exist.",
    "`data/job-todos.md` is missing companies that appear in the pipeline.",
    "`data/job-todos.md`, the 8th error, is absent from that list.",
    "- **`data/job-todos.md` doesn't exist:** create it with the standard header.",
])
def test_absence_claim_skips_conditionals_questions_scopes_and_quotes(text, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-todos.md").write_text("x", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "lessons.md").write_text("x", encoding="utf-8")
    r = _run(text, str(tmp_path))
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("text", [
    "data/job-todos.md doesn't exist anymore.",
    "data/job-todos.md doesn’t exist anymore.",          # curly apostrophe
    "The pipeline file `data/job-todos.md` is gone.",
    "Both a.md and data/job-todos.md is absent, as far as I can tell.",
    "data/job-todos.md: not found.",
    "If you want, I can look. data/job-todos.md is gone.",   # 'If' is another sentence
])
def test_absence_claim_shapes_that_block(text, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-todos.md").write_text("x", encoding="utf-8")
    r = _run(text, str(tmp_path))
    assert r.returncode == 2, r.stderr
    assert "data/job-todos.md" in r.stderr


def test_a_false_save_claim_and_a_false_absence_claim_are_both_reported(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "lessons.md").write_text("x", encoding="utf-8")
    r = _run("Saved to output/ghost.md. Also, lessons.md is missing.", str(tmp_path))
    assert r.returncode == 2, r.stderr
    assert "output/ghost.md" in r.stderr
    assert "you told the user lessons.md does not exist" in r.stderr


def test_an_inline_quoted_diagnostic_is_not_an_absence_claim():
    """Final review 552.F2 / 555.F4: a quoted error message is the tool's words."""
    assert csc.extract_absence_claims(
        "The error says `data/job-todos.md is missing`.") == []
    assert csc.extract_absence_claims(
        "It printed `ERROR: lessons.md does not exist` and stopped.") == []


def test_a_backticked_file_name_is_still_an_absence_claim():
    """The canonical claim puts only the NAME in backticks, so it must still count."""
    assert csc.extract_absence_claims("`lessons.md` is missing.") == ["lessons.md"]


def test_absence_extractor_takes_the_nearest_token():
    assert csc.extract_absence_claims("Both a.md and b.md is absent.") == ["b.md"]
    assert csc.extract_absence_claims("lessons.md. The other file is missing.") == []
    assert csc.extract_absence_claims("x.md is gone. Yes, x.md is gone.") == ["x.md"]


def test_absence_override_reuses_the_hook_override(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "lessons.md").write_text("x", encoding="utf-8")
    tpath = tmp_path / "t.jsonl"
    rec = {"type": "assistant", "message": {"role": "assistant",
           "content": [{"type": "text", "text": DETECTOR_CONTROL}]}}
    tpath.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps({"transcript_path": str(tpath)}),
        capture_output=True, text=True,
        env={"PYTHONIOENCODING": "utf-8", "CLAUDE_PROJECT_DIR": str(tmp_path),
             "SAVE_CLAIMS_OVERRIDE": "1", "PATH": "/usr/bin:/bin"},
    )
    assert r.returncode == 0


def test_a_path_cut_by_the_lookahead_limit_is_not_claimed():
    # The lookahead window can end mid-filename. The token was then a fragment
    # ("job-todos.p") that exists nowhere, so an existing file was reported missing
    # (2026-10-26). A path the window cuts is out of reach: neither fragment nor whole.
    path = "output/table-audit/job-todos.pre-cleanup-102526.md"
    head, prose, bullet = "a flag was saved", " as the task, more prose", "\n- the copy is at `"
    keep = len("output/table-audit/job-todos.p")   # where the window must end
    pad = "x" * (csc.LOOKAHEAD - len(prose) - len(bullet) - keep)
    text = f"{head}{prose}{pad}{bullet}{path}`, fine."
    window = text[len(head):len(head) + csc.LOOKAHEAD]
    assert window.endswith("job-todos.p")  # the old window held only the fragment
    assert extract(text) == []
    # Move the path back so it ends exactly at the limit, and it is the claim.
    fits = f"{head}{prose}{pad[:-(len(path) - keep)]}{bullet}{path}`, fine."
    assert extract(fits) == [path]


def test_a_verb_inside_a_filename_does_not_claim_the_next_path():
    # The verb sits inside the first filename, so it claims nothing, including a
    # second path that follows inside the window.
    text = "Compare `tide.SAVED-noaa.html` with `notes/other.md` before deciding."
    assert extract(text) == []
