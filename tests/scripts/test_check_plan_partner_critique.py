"""The effort estimator must count effort, not every duration in the prose.

Origin of these tests, 2026-08-25. The estimator summed BARE durations anywhere in the
document. Measured against its own IN_SCOPE_PATTERNS over 37 real files it fired on 18 of
them, scoring an onsite prep plan at 1,340h, a diligence plan at 963h, and a morning-starter
doc at 720h. What it was summing was business metrics ("saves ~20 hrs/week"), date
references ("sanction 5 days ago"), narrative ("my first 30 days"), and in one case a single
sentence comparing two figures, counted twice.

The false-positive cases below are the real strings that produced those totals. They are
pinned as hard as the true positives, because an estimator that cannot be wrong in this
direction is what made the number meaningless.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_plan_partner_critique as pc  # noqa: E402


# ---------------------------------------------------------------- annotations DO count

@pytest.mark.parametrize("text,hours", [
    ("- Build the thing (4h)", 4.0),
    ("- Build the thing (2 hrs)", 2.0),
    ("- Build the thing (1 hour)", 1.0),
    ("- Build the thing (half day)", 4.0),
    ("- Build the thing (2 days)", 16.0),
    ("| Step | 2h |", 2.0),
    ("| Step | 3 hrs |", 3.0),
    ("Effort: 3 hours", 3.0),
    ("Estimate: 1.5 hrs", 1.5),
    ("Est: 2h", 2.0),
])
def test_annotation_shapes_are_counted(text, hours):
    assert pc.estimate_hours(text)[0] == pytest.approx(hours)


@pytest.mark.parametrize("text,hours", [
    ("- Task (45 min)", 0.75),
    ("| Step | 10 min |", 10 / 60),
    ("Effort: 90 minutes", 1.5),
])
def test_minute_granularity_is_counted(text, hours):
    """The repo annotates in minutes. An hours-only estimator scores those as zero, which
    is what produced a degenerate 0-of-37 on the first pass of this correction."""
    assert pc.estimate_hours(text)[0] == pytest.approx(hours)


def test_annotations_accumulate_across_a_document():
    doc = "- A (2h)\n- B (30 min)\n| C | 1h |\nEffort: 3 hours\n"
    assert pc.estimate_hours(doc)[0] == pytest.approx(2 + 0.5 + 1 + 3)


# ---------------------------------------------------------------- prose must NOT count

@pytest.mark.parametrize("text", [
    "Saves loan officers ~20 hrs/week.",                      # a business metric
    "USDA sanction 5 days ago (the Monday grenade)",          # a date reference
    "My first 30 days would be discovery",                    # narrative, x85 in output/
    "first 90 days",
    "3 days/week in the office",                              # a rate, x28 in output/
    "12.5 hours, against the 79 hours run 1 implied.",        # one sentence, was double-counted
    "SBA loans historically take 10 days to close",
    "the sprint ran 2 days over",
])
def test_prose_durations_are_not_effort(text):
    assert pc.estimate_hours(text)[0] == 0.0, (
        f"{text!r} is a duration in prose, not an effort estimate; counting it is what "
        "made an onsite prep plan score 1,340 hours"
    )


def test_a_document_of_pure_prose_durations_scores_zero():
    doc = ("Saves loan officers ~20 hrs/week.\nUSDA sanction 5 days ago.\n"
           "My first 30 days would be discovery.\n3 days/week onsite.\n")
    total, ev = pc.estimate_hours(doc)
    assert total == 0.0 and ev == []


def test_prose_and_annotation_in_one_document_counts_only_the_annotation():
    doc = "Saves ~20 hrs/week for the client.\n- Build the integration (2h)\n"
    assert pc.estimate_hours(doc)[0] == pytest.approx(2.0), (
        "the estimator must separate the two, not sum them"
    )


# ---------------------------------------------------------------- the threshold

def test_the_threshold_can_actually_be_crossed():
    """A check with no reachable failing mode is the bug wearing a safety vest."""
    doc = "\n".join(f"- Task {i} (2h)" for i in range(6))
    assert pc.estimate_hours(doc)[0] > pc.THRESHOLD_HOURS


def test_a_document_just_under_the_threshold_does_not_cross_it():
    doc = "\n".join(f"- Task {i} (1h)" for i in range(9))
    assert pc.estimate_hours(doc)[0] <= pc.THRESHOLD_HOURS


def test_evidence_lines_are_returned_for_every_counted_annotation():
    total, ev = pc.estimate_hours("- A (2h)\n- B (3h)\n")
    assert total == pytest.approx(5.0)
    assert len(ev) == 2 and all("Line" in e for e in ev)


# ---------------------------------------------------------------- scope

@pytest.mark.parametrize("path,expected", [
    ("output/acme/082526-build-plan.md", True),
    ("output/acme/082526-roadmap.md", True),
    ("data/workbooks/foundation-plan.md", True),
    ("output/acme/082526-dossier.md", False),
    ("data/profile.md", False),
    ("README.md", False),
])
def test_in_scope(path, expected):
    assert pc.in_scope(path) is expected


def test_in_scope_handles_windows_separators():
    assert pc.in_scope(r"output\acme\082526-build-plan.md") is True
