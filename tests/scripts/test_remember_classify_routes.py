"""remember_classify.py: exact destinations and loader outputs.

Written from the 2026-10-30 mutation run, which left 42 survivors: the routes were never
checked for their exact destination list, and the loaders were never given the rows they
must skip. Each test compares a whole result.
"""
import sys

import pytest

from conftest import TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
import remember_classify as rc  # noqa: E402


def types(result):
    return [(d["type"], d.get("entity")) for d in result["destinations"]]


def classify(note, contacts=(), companies=(), dossiers=()):
    return rc.classify_note(note, list(contacts), list(companies), list(dossiers))


# --- fallback routes, each with nothing earlier to catch it -----------------------------

@pytest.mark.parametrize("note,want", [
    ("Shipped the new resume generator", "accomplishment"),
    ("Interesting: a piece on agent evals", "source_article"),
    ("Park this idea about a newsletter", "deferred_idea"),
    ("https://example.com/post", "raw_capture"),
    ("check this out https://example.com/a-long-path", "raw_capture"),
])
def test_each_fallback_route_is_exactly_one_destination(note, want):
    out = classify(note)
    assert types(out) == [(want, None)] and out["ambiguous"] is False


def test_a_url_with_real_prose_is_a_general_note():
    out = classify("https://example.com/post about the hiring market in fintech")
    assert types(out) == [("general_note", None)] and out["ambiguous"] is True


def test_url_helpers_are_strictly_boolean():
    assert rc._has_url("see https://x.example") is True and rc._has_url("none") is False
    assert rc._url_only("https://x.example") is True
    assert rc._url_only("https://x.example plus enough words") is False


# --- contacts -------------------------------------------------------------------------

NET = """# Networking

## Contacts

prose line inside the section
| Name | Company | Role |
|------|---------|------|
| Jordan Lee | Acme AI | Director |
|  | Blank | x |
| Sam Example | Beta Inc | PM |

## Interaction Log

| Not A Contact | x | y |
"""


def test_load_contacts_reads_only_real_rows_of_the_contacts_table():
    assert rc.load_contacts(NET) == ["Jordan Lee", "Sam Example"]


def test_only_the_named_contact_matches():
    assert rc.find_matching_contacts("coffee with sam example", ["Jordan Lee", "Sam Example"]) == ["Sam Example"]


def test_two_contacts_each_get_their_own_contact_note():
    out = classify("Jordan Lee and Sam Example met at the event", ["Jordan Lee", "Sam Example"])
    assert types(out) == [("contact_note", "Jordan Lee"), ("contact_note", "Sam Example")]


def test_a_reply_gets_both_the_outreach_reply_and_the_contact_note():
    out = classify("Jordan Lee replied with times", ["Jordan Lee"])
    assert types(out) == [("outreach_reply", "Jordan Lee"), ("contact_note", "Jordan Lee")]


# --- companies and dossiers -------------------------------------------------------------

def test_load_dossier_slugs_lists_only_dossiers(tmp_path):
    out = tmp_path / "output"
    for folder, name in (("acme-corp", "acme-corp"), ("beta", "notes"), ("gamma", "gamma")):
        (out / folder).mkdir(parents=True)
        (out / folder / f"{name}.md").write_text("x", encoding="utf-8")
    assert sorted(rc.load_dossier_slugs(out)) == [("acme-corp", "Acme Corp"), ("gamma", "Gamma")]


def test_find_matching_companies_keeps_the_pipeline_entry_and_adds_dossier_only_ones():
    got = rc.find_matching_companies(
        "notes on acme corp and gamma, not delta",
        ["Acme Corp", "Northwind"],
        [("acme-corp", "Acme Corp"), ("gamma", "Gamma"), ("delta-co", "Delta Co")])
    assert got == [("Acme Corp", "acme-corp", True), ("Gamma", "gamma", False)]


def test_a_status_word_adds_a_pipeline_note_for_pipeline_companies_only():
    out = classify("Acme Corp moved my application forward; Gamma too",
                   companies=["Acme Corp"], dossiers=[("gamma", "Gamma")])
    assert types(out) == [("pipeline_note", "Acme Corp"), ("company_note", "Acme Corp"),
                          ("company_note", "Gamma")]


def test_no_status_word_means_no_pipeline_note():
    out = classify("Acme Corp has a nice office", companies=["Acme Corp"])
    assert types(out) == [("company_note", "Acme Corp")]
