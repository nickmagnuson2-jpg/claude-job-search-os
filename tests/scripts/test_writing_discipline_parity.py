#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parity tests for framework/writing-discipline.md and its consumer skills.

The property under test is not "does the doc exist." It is: **the canonical
file and the skills that claim to use it cannot silently disagree.**

This exists because the exact failure it guards against already happened. The
generative-spine architecture was recorded as "4 skills fixed in code"; a later
audit found only 3 (see .claude/skills/cover-letter/SKILL.md, origin note dated
2026-08-13). A one-directional check would not have caught that: the three real
copies all passed. Only comparing BOTH directions -- the doc's declared list
against the skills that actually reference it -- surfaces a skill that was
promised and never wired, or wired and never declared.

The second central test is the anti-restatement one. Before consolidation
on 2026-08-20, three skills each carried their own copy of the four provenance
labels, and diffing them showed real drift (one had a materially different `I`
definition). A skill that restates the labels locally has re-created the drift
surface even while carrying a correct pointer, so the pointer alone is not
sufficient evidence of consolidation.
"""
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CANON = REPO_ROOT / "framework" / "writing-discipline.md"
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

CANON_REF = "framework/writing-discipline.md"


def _canon_text():
    assert CANON.exists(), f"canonical file missing: {CANON}"
    return CANON.read_text(encoding="utf-8")


def declared_skills():
    """Skill names listed under the '## Mandatory read' section of the canon."""
    text = _canon_text()
    m = re.search(r"^## Mandatory read\s*$(.*?)^## ", text, re.S | re.M)
    assert m, "canonical file has no '## Mandatory read' section"
    return {n for n in re.findall(r"^- `/([a-z0-9-]+)`", m.group(1), re.M)}


def referencing_skills():
    """Skills whose SKILL.md actually points at the canonical file."""
    found = set()
    for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
        if CANON_REF in skill_md.read_text(encoding="utf-8"):
            found.add(skill_md.parent.name)
    return found


def test_canonical_file_declares_a_nonempty_scope():
    assert declared_skills(), "mandatory-read list is empty; the doc governs nothing"


def test_every_declared_skill_actually_references_the_canon():
    """Declared but not wired: the '4 skills fixed, audit found 3' failure."""
    missing = declared_skills() - referencing_skills()
    assert not missing, (
        f"declared in {CANON_REF} but no pointer in SKILL.md: {sorted(missing)}"
    )


def test_every_referencing_skill_is_declared():
    """Wired but not declared: the doc's scope silently understates reality."""
    undeclared = referencing_skills() - declared_skills()
    assert not undeclared, (
        f"references {CANON_REF} but absent from its mandatory-read list: "
        f"{sorted(undeclared)}"
    )


@pytest.mark.parametrize("skill", sorted(declared_skills()))
def test_skill_does_not_restate_the_provenance_labels(skill):
    """A local copy of the labels re-creates the drift surface consolidation removed."""
    text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "**Provenance labels:**" not in text, (
        f"/{skill} restates the provenance labels locally; they belong only in "
        f"{CANON_REF}"
    )
    # The definition lines themselves, not the bare letters (slot tables legitimately
    # mention `G`).
    for label, word in (("N", "Nick-dictated"), ("G", "Claude-generated")):
        assert f"**{label}** — {word}" not in text and f"**{label}** - {word}" not in text, (
            f"/{skill} redefines label {label} locally; remove it and rely on {CANON_REF}"
        )


def test_canon_defines_all_four_labels():
    text = _canon_text()
    for label in ("N", "C", "I", "G"):
        assert re.search(rf"^\|\s*\*\*{label}\*\*\s*\|", text, re.M), (
            f"canonical file does not define label {label}"
        )


def test_selection_gate_is_marked_unbuilt():
    """The endorsed-but-unbuilt gate must never read as covered.

    If someone builds it, this test should be updated deliberately, not deleted
    incidentally.
    """
    text = _canon_text()
    assert "PLACEHOLDER, NOT BUILT" in text, (
        "the Selection Gate placeholder lost its unbuilt marker; an endorsed "
        "principle with no mechanism must not read as shipped"
    )
    assert "REOPEN gate:" in text, "placeholder has no REOPEN gate"


# --------------------------------------------------------------------------
# Selection record (added 2026-08-20)
#
# These guard the wiring, not the judgment. The audit format can declare the
# fields and a test can assert they are declared and singly-sourced; no test
# can decide whether a chosen lead was the right lead. The honest boundary is
# stated in the canonical file and repeated here so a later reader does not
# mistake a green suite for a verified selection.
# --------------------------------------------------------------------------

YAML = REPO_ROOT / "framework" / "content-rules.yaml"


def _yaml_text():
    assert YAML.exists(), f"missing {YAML}"
    return YAML.read_text(encoding="utf-8")


def selection_rule_ids():
    """Rule ids tagged `decision_class: selection` in content-rules.yaml."""
    text = _yaml_text()
    ids = []
    current = None
    for line in text.splitlines():
        m = re.match(r"\s*- id: (\S+)\s*$", line)
        if m:
            current = m.group(1)
        elif "decision_class: selection" in line and current:
            ids.append(current)
    return set(ids)


def _audit_format_block():
    """The fenced example under '### Audit output format'.

    Scoped deliberately. An earlier version asserted the field names appeared
    anywhere in the file, which a mutation defeated: prose elsewhere mentions
    `Lead:` and `Cut:`, so breaking the actual template still passed.
    """
    text = _canon_text()
    i = text.index("### Audit output format")
    block = re.search(r"```(.*?)```", text[i:], re.S)
    assert block, "no fenced example under '### Audit output format'"
    return block.group(1)


def _selection_section():
    """The '### The selection record' section only, up to the next heading."""
    text = _canon_text()
    m = re.search(r"^### The selection record.*?$(.*?)^#{2,3} ", text, re.S | re.M)
    assert m, "canonical file has no '### The selection record' section"
    return m.group(1)


@pytest.mark.parametrize("field", ["Lead:", "Cut:"])
def test_audit_format_block_declares_the_selection_fields(field):
    assert field in _audit_format_block(), (
        f"the audit output TEMPLATE does not contain {field}; a prose mention "
        f"elsewhere in the file is not the template"
    )


def test_canon_marks_the_selection_fields_mandatory():
    text = _canon_text()
    assert "mandatory fields: `Lead:` and `Cut:`" in text, (
        "the selection record must be declared mandatory; an optional field is "
        "the under-selection defect it exists to prevent"
    )
    assert "empty `Cut:`" in text, (
        "canon must state when an empty Cut: is valid, or the field degrades to "
        "decoration"
    )


@pytest.mark.parametrize("skill", sorted(declared_skills()))
def test_skill_does_not_redefine_the_audit_output_format(skill):
    """The format is single-source; a local copy re-creates the drift surface."""
    text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "Substance audit:" not in text, (
        f"/{skill} restates the audit output format locally; it belongs only in "
        f"{CANON_REF}"
    )


def test_selection_rules_are_tagged_in_the_yaml():
    """Guards the consolidation half: the class must exist as a queryable tag."""
    tagged = selection_rule_ids()
    assert tagged, "no rule in content-rules.yaml is tagged decision_class: selection"
    for expected in ("C4", "B7", "B8"):
        assert expected in tagged, (
            f"{expected} was measured as a selection defect but is not tagged "
            f"decision_class: selection"
        )


@pytest.mark.parametrize("rule_id", sorted(selection_rule_ids()))
def test_every_tagged_selection_rule_is_actually_wired(rule_id):
    """The anti-shortfall test.

    Tagging a rule as `selection` and never referencing it from the file that
    consumes selection decisions is the 'built but not wired' failure: the
    classification exists, nothing reads it, and a later audit sees a tag and
    infers coverage. Bidirectional wiring is the only thing that makes the tag
    actually consumed rather than decorative.
    """
    assert rule_id in _selection_section(), (
        f"content-rules.yaml tags {rule_id} as decision_class: selection, but the "
        f"'selection record' section of {CANON_REF} never references it. A mention "
        f"elsewhere in the file (e.g. in the measurement write-up) does not wire it: "
        f"the tag is only central if the section that consumes selection "
        f"decisions names the rule."
    )
