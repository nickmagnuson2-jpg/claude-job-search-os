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

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "tools" / "check_save_claims.py"

spec = importlib.util.spec_from_file_location("csc", HOOK)
csc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csc)


def extract(text):
    return csc.extract_claimed_paths(text)


# ---------- extraction: should CATCH ----------

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
            "Saved to `output/acme/080426-casey-whiteboard-sim-prompt.md`. "
            "Paste this into the Claude App and switch to voice:", td)
        assert r.returncode == 2
        assert "080426-casey-whiteboard-sim-prompt.md" in r.stderr


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
