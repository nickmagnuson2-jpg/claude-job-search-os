"""artifact_vocab.py: the one vocabulary for artifacts named in, or carried by, an outreach.

Three properties are pinned here:
  * the Artifact-cell tokens added 2026-10-26 (page, demo, takehome, note, none) are
    CANONICAL only, with no free-text alias, so plain English in a Summary cell
    ("golden playbook", "thank-you note") never reads as an artifact;
  * `demo` is its own token, no longer an alias of `link`;
  * check_cell(), the validator both the outreach-log parser and its row writer call,
    accepts empty, `none` and `<canonical type>:<ref>`, and nothing else.

Placeholders only (Acme, Casey Doe). This file is public.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import artifact_vocab as av  # noqa: E402

SCRIPT = ROOT / "tools" / "artifact_vocab.py"


def cli(*args) -> tuple[int, dict | None, str]:
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)
    out = proc.stdout.strip()
    try:
        return proc.returncode, json.loads(out), out
    except ValueError:
        return proc.returncode, None, out


# ------------------------------------------------------------ the vocabulary


def test_canonical_order_is_the_old_six_then_the_five_cell_tokens():
    assert av.valid_tokens() == ["cv", "cover-letter", "deck", "portfolio", "writeup", "link",
                                 "page", "demo", "takehome", "note", "none"]


@pytest.mark.parametrize("token", ["page", "demo", "takehome", "note", "none"])
def test_each_new_token_canonicalizes_to_itself(token):
    assert av.canonicalize(token) == token
    assert av.canonicalize(token.upper()) == token


def test_demo_is_its_own_token_not_a_link():
    assert av.canonicalize("demo") == "demo"
    assert av.find_in_text("shared the Acme demo") == ["demo"]
    assert av.find_transmitted_in_text("demo link sent to Casey Doe") == ["link", "demo"]


@pytest.mark.parametrize("text", [
    "thank-you note after the call",
    "the golden playbook they described",
    "a page of notes on the use case",
    "none of this was attached",
    "the takehome is due Friday",
])
def test_new_tokens_have_no_free_text_alias(text):
    """A Summary cell's plain English must not read as a page, note, takehome or none."""
    assert av.find_in_text(text) == []


def test_old_aliases_still_map():
    assert av.canonicalize("Résumé") == "cv"
    assert av.canonicalize("cover letter") == "cover-letter"
    assert av.canonicalize("cover  letter") == "cover-letter"
    assert av.canonicalize("work-samples") == "portfolio"
    assert av.canonicalize("banana") is None


def test_find_in_text_returns_canonical_order_deduped():
    assert av.find_in_text("slides and a resume, plus the CV again and a deck") == ["cv", "deck"]


# ------------------------------------------------------------ check_cell


@pytest.mark.parametrize("cell", [
    "",
    "none",
    "page:acme-playbook.example.com",
    "demo:acme-demo.example.com",
    "takehome:output/acme/case-brief.pdf",
    "note:Google Doc (market research)",
    "cv:output/acme/cv.pdf",
    "writeup:a: label with a colon",
])
def test_check_cell_accepts(cell):
    assert av.check_cell(cell) is None


@pytest.mark.parametrize("cell,needle", [
    ("banana:x", "unknown artifact type 'banana'"),
    ("resume:output/acme/cv.pdf", "unknown artifact type 'resume'"),
    ("none:anything", "unknown artifact type 'none'"),
    ("Page:x", "unknown artifact type 'Page'"),
    ("page", "needs a ref"),
    ("page:", "needs a ref"),
    ("page:   ", "needs a ref"),
    ("None", "unknown artifact type"),
])
def test_check_cell_refuses(cell, needle):
    problem = av.check_cell(cell)
    assert problem is not None and needle in problem, problem


def test_check_cell_refusal_lists_the_valid_tokens():
    assert "takehome" in av.check_cell("banana:x")


def test_check_cell_needs_ref_names_the_type():
    assert av.check_cell("deck") == "artifact 'deck' needs a ref: write deck:<url, path or label>"


# ------------------------------------------------------------ CLI


def test_cli_list():
    code, out, _ = cli("--list")
    assert code == 0 and out == {"valid_tokens": av.valid_tokens()}


def test_cli_canonicalize_known_and_unknown():
    code, out, _ = cli("--canonicalize", "slides")
    assert code == 0 and out == {"input": "slides", "canonical": "deck"}
    code, out, _ = cli("--canonicalize", "banana")
    assert code == 4
    assert out["error"] == "unknown artifact token: banana"
    assert out["valid_tokens"] == av.valid_tokens()


def test_cli_find_in_text():
    code, out, _ = cli("--find-in-text", "deck attached")
    assert code == 0 and out == {"text": "deck attached", "artifacts": ["deck"]}


def test_cli_empty_find_in_text_is_still_a_query():
    code, out, _ = cli("--find-in-text", "")
    assert code == 0 and out == {"text": "", "artifacts": []}


def test_cli_with_no_arguments_prints_help():
    code, out, raw = cli()
    assert code == 0 and out is None and "usage" in raw


# ------------------------------------------------------------ transmission sense
# Each case pins one decision inside find_transmitted_in_text / _nearest.

FILLER = " and some unrelated words about the weather and the week ahead and the rest"


def test_a_marker_overlapping_the_mention_is_distance_zero():
    """'resume' sits inside the dated filename: the positive overlaps it (distance 0) and
    beats the negative 8 chars away."""
    assert av.find_transmitted_in_text("offered 010226-resume.pdf") == ["cv"]


def test_a_negative_beyond_the_window_does_not_count():
    text = "CV attached" + FILLER + " offered"
    assert len(text) - len("offered") - len("CV") > av._WINDOW
    assert av.find_transmitted_in_text(text) == ["cv"]


def test_a_positive_beyond_the_window_does_not_count():
    text = "CV" + FILLER + " attached"
    assert av.find_transmitted_in_text(text) == []


def test_the_nearest_marker_wins_not_the_farthest():
    text = "resume attached, offered a call, then later on it was sent"
    assert av.find_transmitted_in_text(text) == ["cv"]


def test_a_tie_between_positive_and_negative_is_not_a_send():
    assert av.find_transmitted_in_text("offered resume attached") == []


def test_no_positive_marker_is_not_a_send():
    assert av.find_transmitted_in_text("the resume thread") == []
