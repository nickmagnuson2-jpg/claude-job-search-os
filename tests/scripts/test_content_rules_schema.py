"""W2 — schema + md/yaml parity guard for the phrase registry.

Imitates test_stage_classification_consistency.py: codify the invariant so the drift
cannot come back silently. The drift this guards against is the one that caused the
2026-08-11 recurrence — the YAML and the human index (the file the drafting skills
actually LOAD) disagreeing about what a rule bans.

ALL THREE SOURCE FILES ARE GITIGNORED. From a clean clone these tests SKIP with a reason
naming the absent path — never fail, and never silently pass. Their real validation is
Tier 2, run locally in the owner's environment.
"""
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT

YAML_PATH = REPO_ROOT / "framework" / "content-rules.yaml"
MD_PATH = REPO_ROOT / "framework" / "content-rules.md"

VALID_SCOPES = {"literal", "construction"}


def _require(path: Path):
    if not path.is_file():
        pytest.skip(f"{path.relative_to(REPO_ROOT)} absent (gitignored); Tier 2 validation only")


@pytest.fixture
def rules():
    _require(YAML_PATH)
    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {}
    parsed = data.get("rules") or []
    assert parsed, "content-rules.yaml parsed to zero rules"
    return parsed


def test_every_rule_declares_both_phrase_lists(rules):
    for rule in rules:
        for field in ("banned_phrases", "preferred_phrases"):
            assert field in rule, f"{rule['id']}: missing {field}"
            assert isinstance(rule[field], list), f"{rule['id']}: {field} must be a list"


def test_every_phrase_entry_is_well_formed(rules):
    for rule in rules:
        for field in ("banned_phrases", "preferred_phrases"):
            for entry in rule[field]:
                assert isinstance(entry, dict), f"{rule['id']}: {field} entry must be a mapping"
                assert isinstance(entry.get("phrase"), str) and entry["phrase"].strip(), \
                    f"{rule['id']}: {field} entry needs a non-empty string `phrase`"
                assert entry.get("scope") in VALID_SCOPES, \
                    f"{rule['id']}: scope must be one of {sorted(VALID_SCOPES)}, got {entry.get('scope')!r}"


def test_no_phrase_is_both_banned_and_preferred(rules):
    for rule in rules:
        banned = {e["phrase"].casefold() for e in rule["banned_phrases"]}
        preferred = {e["phrase"].casefold() for e in rule["preferred_phrases"]}
        overlap = banned & preferred
        assert not overlap, f"{rule['id']}: phrase both banned and preferred: {sorted(overlap)}"


def test_md_yaml_phrase_parity(rules):
    """The human index and the machine source must register the same phrases.

    Scoped to rule ids that appear in the md: it lists only ACTIVE rules, while the yaml
    also carries reference-only rules that legitimately never appear there.
    """
    _require(MD_PATH)
    import re

    md_text = MD_PATH.read_text(encoding="utf-8")
    marker_re = re.compile(
        r"^- \*\*(?P<id>[A-Z]\d+)\*\*.*?$\n(?:\s*<!-- phrases: banned=(?P<banned>.*?) \| "
        r"preferred=(?P<preferred>.*?) -->)?",
        re.M,
    )
    phrase_re = re.compile(r'"([^"]*)"\((literal|construction)\)')

    md_rules = {}
    for m in marker_re.finditer(md_text):
        md_rules[m.group("id")] = {
            "banned": {p.casefold() for p, _ in phrase_re.findall(m.group("banned") or "")},
            "preferred": {p.casefold() for p, _ in phrase_re.findall(m.group("preferred") or "")},
        }
    assert md_rules, "no rule bullets parsed out of content-rules.md"

    by_id = {r["id"]: r for r in rules}
    for rid, md_entry in md_rules.items():
        rule = by_id.get(rid)
        assert rule is not None, f"{rid} appears in content-rules.md but not in the yaml"
        for field, key in (("banned_phrases", "banned"), ("preferred_phrases", "preferred")):
            yaml_set = {e["phrase"].casefold() for e in rule[field]}
            assert yaml_set == md_entry[key], (
                f"{rid}: {key} phrases differ between md and yaml.\n"
                f"  yaml: {sorted(yaml_set)}\n"
                f"  md:   {sorted(md_entry[key])}"
            )
