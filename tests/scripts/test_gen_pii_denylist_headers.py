"""Harvesting the interaction-log headers: `### Name — Company`.

WHY THIS EXISTS (2026-09-02). `parse_networking_names` kept only group(1) of that header
and discarded the company half by design, so a company appearing only in an interaction log
reached NO denylist tier -- 179 headers were in that state. One of those companies was
sitting in a tracked, public test fixture while the deterministic PII scan reported the tree
clean. Separately, a leading `[ARCHIVED]` tag was captured as part of the name, so
`[ARCHIVED] First Last` was denylisted and the real name was not.

TIER CHOICE IS DELIBERATE. Companies from this source route to AMBIGUOUS/WARN, not BLOCK:
they are parsed out of prose-shaped headers rather than a structured column, so extraction
is less trustworthy than the pipeline table, and a false BLOCK on an always-on PreToolUse
hook stops real work. /audit-pii Step 1 reports ambiguous_hits[] to a human, which is the
tier's only functioning consumer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import gen_pii_denylist as g  # noqa: E402


# --- the company half is harvested at all -----------------------------------

def test_the_company_half_of_a_header_is_captured():
    assert "Northwind" in g.parse_networking_companies("### Jordan Lee — Northwind\n")


@pytest.mark.parametrize("dash", ["—", "–", "|"])
def test_every_separator_the_log_actually_uses_is_handled(dash):
    assert g.parse_networking_companies(f"### Jordan Lee {dash} Northwind\n") == {"Northwind"}


def test_a_header_with_no_company_yields_nothing():
    assert g.parse_networking_companies("### Jordan Lee\n") == set()


def test_non_header_lines_are_ignored():
    assert g.parse_networking_companies(
        "Some prose — with an em dash\n#### Deeper — Heading\n") == set()


def test_multiple_headers_all_land():
    got = g.parse_networking_companies(
        "### Jordan Lee — Northwind\n\ntext\n\n### Casey Doe — Contoso\n")
    assert got == {"Northwind", "Contoso"}


# --- the status prefix, which broke both halves -----------------------------

def test_a_status_prefixed_header_yields_the_real_company():
    assert g.parse_networking_companies("### [ARCHIVED] Jordan Lee — Northwind\n") == {"Northwind"}


def test_a_status_prefix_is_stripped_from_the_name():
    """Without this the token is `[ARCHIVED] Jordan Lee`, which matches nothing -- so the
    real contact reaches no tier at all."""
    assert "Jordan Lee" in g.parse_networking_names("### [ARCHIVED] Jordan Lee — Northwind\n")


def test_an_unprefixed_name_is_unchanged():
    assert "Jordan Lee" in g.parse_networking_names("### Jordan Lee — Northwind\n")


def test_a_bracket_that_is_not_a_status_tag_is_left_alone():
    """The strip is anchored and length-bounded so it cannot eat a real name."""
    assert g._strip_status_prefix("Jordan [the second] Lee") == "Jordan [the second] Lee"


# --- annotation is not part of the name -------------------------------------

def test_a_trailing_parenthetical_is_dropped():
    assert g.parse_networking_companies(
        "### Jordan Lee — Northwind (intro via Casey)\n") == {"Northwind"}


def test_trailing_punctuation_is_trimmed():
    assert g.parse_networking_companies("### Jordan Lee — Northwind.\n") == {"Northwind"}


def test_a_two_character_fragment_is_not_a_company():
    """Short fragments are parse noise; as WARN-tier tokens they would fire constantly."""
    assert g.parse_networking_companies("### Jordan Lee — Co\n") == set()


# --- the tier: WARN, never BLOCK --------------------------------------------

def test_header_companies_do_not_reach_the_block_tier(tmp_path, monkeypatch):
    """The whole point of the tier choice. A false BLOCK on an always-on PreToolUse hook
    stops real work, and this source is prose-parsed."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "networking.md").write_text(
        "### Jordan Lee — Zephyrine\n", encoding="utf-8")
    (tmp_path / "data" / "job-pipeline.md").write_text(
        "| Company | Role |\n|---|---|\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv",
                        ["gen_pii_denylist.py", "--repo-root", str(tmp_path), "--dry-run"])
    import io, json, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            g.main()
        except SystemExit:
            pass
    out = json.loads(buf.getvalue())
    assert "Zephyrine" in out["ambiguous"], "header company must reach the WARN tier"
    assert "Zephyrine" not in out["tokens"], "header company must NOT reach BLOCK"


# --- the assertion that has to fail first -----------------------------------

def test_the_harvester_sees_the_real_corpus():
    """Every assertion above works on synthetic input. If the live file stops parsing,
    this fails rather than letting the suite report health it never measured."""
    live = REPO_ROOT / "data" / "networking.md"
    if not live.is_file():
        pytest.skip("no live networking.md in this tree")
        return
    content = live.read_text(encoding="utf-8", errors="ignore")
    companies = g.parse_networking_companies(content)
    assert len(companies) > 20, (
        f"only {len(companies)} companies harvested from the live log; the parse broke")
    assert not any(c.startswith("[") for c in companies), "status tags leaked into tokens"


# --- the fictional cast must never reach a denylist tier --------------------
#
# Found 2026-09-02, by the gate blocking three legitimate public files minutes after the
# harvester fix landed. `data/networking.md` contains a demo interaction logged against a
# cast persona; the harvester cannot tell a demo row from a real contact, because both are
# `### Name — Company`. Fixing the status-prefix bug therefore promoted a FICTIONAL surname
# to BLOCK, and the always-on PreToolUse hook immediately blocked examples/README.md -- the
# file that DEFINES the persona as fictional.
#
# This is the false-positive half of the WARN-vs-BLOCK decision, arriving in practice: a
# BLOCK-tier false positive stops real work, and the fix has to be exclusion by declared
# name, not a heuristic guessing which log rows are demos.

@pytest.mark.parametrize("name", sorted(g.FICTIONAL_CAST))
def test_no_cast_member_produces_a_denylist_token(name):
    assert g.build_denylist({name}, set(), g.load_dictionary()) == []


@pytest.mark.parametrize("name", [n for n in sorted(g.FICTIONAL_CAST) if " " in n])
def test_no_cast_surname_is_emitted_as_a_component(name):
    assert g.person_components(name, g.load_dictionary()) == []


def test_a_real_looking_name_still_produces_tokens():
    """The exclusion must be narrow. If it swallowed ordinary names the whole BLOCK tier
    would quietly empty out, which is the failure this file is guarding against inverted."""
    toks = g.build_denylist({"Wilhelmina Okonkwo"}, set(), g.load_dictionary())
    assert toks, "a non-cast full name must still be denylisted"
    assert "Okonkwo" in g.person_components("Wilhelmina Okonkwo", g.load_dictionary())


def test_the_cast_list_is_not_empty():
    """Every assertion above passes vacuously against an empty cast set."""
    assert len(g.FICTIONAL_CAST) >= 5
    assert g.FICTIONAL_SURNAMES, "surnames are derived from the cast; an empty set guards nothing"


def test_the_live_denylist_carries_no_cast_member():
    """The generated artefact, not just the function. The retired-tokens file merges every
    token ever emitted, so a cast name that lands once persists through later rebuilds --
    which is exactly what happened, and why the file had to be purged by hand."""
    for rel in ("tools/.pii-denylist.txt", "tools/.pii-denylist-ambiguous.txt",
                "tools/.pii-denylist-retired.txt"):
        f = REPO_ROOT / rel
        if not f.is_file():
            continue
        tokens = {l.strip().lower() for l in f.read_text(encoding="utf-8").splitlines()
                  if l.strip() and not l.startswith("#")}
        leaked = tokens & (g.FICTIONAL_CAST | {s.lower() for s in g.FICTIONAL_SURNAMES})
        assert not leaked, f"{rel} carries fictional-cast token(s): {sorted(leaked)}"
