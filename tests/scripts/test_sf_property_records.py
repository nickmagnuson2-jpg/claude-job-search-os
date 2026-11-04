"""sf_property_records: the query each dataset receives, which rows are kept, and what prints.

WHAT IT GUARDS (2026-11-02).
  - PermitSF (tyz3-vt28), the city's newer permit system, was not queried at all, so the
    tool missed every permit filed there.
  - A street-name prefix matched longer streets that start with the same name ("MARKET"
    against a longer "MARKET ..." street), so another address's records could print as
    this one's. street_of() and select() now decide, in one place, for every dataset.
  - Results were silently cut off at 200 rows. query() now pages to the end.
The network is stubbed, so these run offline. Addresses are placeholders.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import sf_property_records as spr  # noqa: E402

TOOL = Path(spr.__file__)


def run_main(monkeypatch, capsys, responses=None, argv=("100", "market")):
    """Run main() with query() stubbed. Returns (calls, stdout)."""
    calls = []

    def fake_query(dataset, where, page=spr.PAGE):
        calls.append((dataset, where))
        return (responses or {}).get(dataset, [])

    monkeypatch.setattr(spr, "query", fake_query)
    code = spr.main(["sf_property_records.py", *argv])
    assert code == (1 if any("ERROR" in r for rows in (responses or {}).values() for r in rows) else 0)
    return calls, capsys.readouterr().out


def permitsf_row(street="MARKET ST", recordno="FIRE-26-0001"):
    return {"recordno": recordno, "streetno": "100", "streetname": street,
            "recordtype": "Fire alarm permit", "description": "Fire alarm installation",
            "status_detail": "Active - Issued", "submitted_date": "2026-09-01",
            "permit_issued_date": "2026-10-01", "last_doc": "Fire Alarm Permit",
            "last_doc_date": "2026-10-01", "valuation": "1000.0",
            "url": {"url": "https://example.test/records/1"}}


# --- which street a row is on --------------------------------------------------------

@pytest.mark.parametrize("rest,street", [
    ("", ""), (", SAN FRANCISCO", ""), ("#2", ""), (" #2", ""), (" STE 205", ""),
    (" 94103", ""), (" ST", "ST"), (" st", "ST"), (" Ave", "AVE"), (" AV", "AVE"),
    (" AVENUE.", "AVE"), (" STREET", "ST"), (" BLVD", "BLVD"), (" ST 94103", "ST"),
    (" ST, SAN FRANCISCO", "ST"),
    (" ROCK ST", None), (" BAY BLVD", None), ("PLACE ST", None), ("S", None),
    ("ST", None), ("123", None),  # no space: the name itself continues
    ("   ", None),
])
def test_street_of(rest, street):
    assert spr.street_of(rest) == street


def test_canonical():
    assert spr.canonical("Av") == "AVE" and spr.canonical("street.") == "ST"
    assert spr.canonical(None) is None and spr.canonical("ROCK") is None


@pytest.mark.parametrize("args,parsed", [
    (["100", "market"], ("100", "MARKET", None, "MARKET")),
    (["100a", "market"], ("100A", "MARKET", None, "MARKET")),
    (["100", "market", "st"], ("100", "MARKET", "ST", "MARKET ST")),
    (["100", "19th", "Av"], ("100", "19TH", "AVE", "19TH AV")),
    (["100", "van", "ness", "avenue"], ("100", "VAN NESS", "AVE", "VAN NESS AVENUE")),
    (["100", "park"], ("100", "PARK", None, "PARK")),  # a lone word is the name, even if a suffix
])
def test_parse_address(args, parsed):
    assert spr.parse_address(args) == parsed


def test_soql_doubles_a_quote():
    assert spr.soql("O'FARRELL") == "O''FARRELL"


def street_text(row):
    return spr.address_street("a", "100 MARKET")(row)


def test_select_keeps_one_street_and_names_each_exclusion():
    rows = [{"a": "100 MARKET ST"}, {"a": "100 market st #3"}, {"a": "100 MARKET ROCK ST"},
            {"a": "  100 MARKET  "}, {"a": "200 MARKET ST"}, {"a": ""},
            {"ERROR": "x", "url": "u"}]
    kept, excluded, note = spr.select(rows, street_text, None)
    assert kept == [rows[0], rows[1], rows[3], rows[6]]
    assert excluded == {"a different street or number": 2, "no address in the record": 1}
    assert note == ""


def test_select_with_no_suffix_refuses_two_streets():
    rows = [{"a": "100 MARKET ST"}, {"a": "100 MARKET AVE"}, {"a": "100 MARKET"}]
    kept, excluded, note = spr.select(rows, street_text, None)
    assert kept == []
    assert excluded == {"ambiguous: no suffix given": 3}
    assert note == "AMBIGUOUS: withheld. Records at this number are on 2 streets (AVE, ST); see the report's first line."


def test_select_with_a_suffix_keeps_that_street_and_unsuffixed_rows():
    rows = [{"a": "100 MARKET ST"}, {"a": "100 MARKET AVE"}, {"a": "100 MARKET"},
            {"ERROR": "x", "url": "u"}]
    kept, excluded, note = spr.select(rows, street_text, "AVE")
    assert kept == [rows[1], rows[2], rows[3]]
    assert excluded == {"a different street suffix": 1} and note == ""


def test_exact_street_reads_the_suffix_field_or_the_full_name():
    street = spr.exact_street("SOUTH", "SOUTH PARK")
    assert street({"street_name": "South Park", "street_suffix": "St"}) == "(named SOUTH PARK)"
    assert street({"street_name": "South", "street_suffix": "Park"}) == "PARK"
    plain = spr.exact_street("19TH", "19TH")
    assert plain({"street_name": "19th", "street_suffix": "Av"}) == "AVE"
    assert plain({"street_name": "19th"}) == ""


# --- PermitSF ------------------------------------------------------------------------------

def test_permitsf_is_queried_with_the_address(monkeypatch, capsys):
    calls, _ = run_main(monkeypatch, capsys)
    assert [w for ds, w in calls if ds == "tyz3-vt28"] == [
        "streetno='100' AND upper(streetname) like 'MARKET%'"]


def test_permitsf_keeps_the_street_and_excludes_a_longer_one(monkeypatch, capsys):
    rows = [permitsf_row("MARKET ST", "A-1"), permitsf_row("MARKET", "A-2"),
            permitsf_row("MARKET ROCK ST", "B-1")]
    _, out = run_main(monkeypatch, capsys, {"tyz3-vt28": rows})
    section = out.split("### PermitSF permits (newer system)")[1].split("###")[0]
    assert ("scope: PermitSF permits (newer system) | population 3 | included 2 | "
            "excluded: a different street or number 1") in section
    assert "A-1 |" in section and "A-2 |" in section and "B-1" not in section


def test_a_permitsf_row_prints_its_address_and_record_link(monkeypatch, capsys):
    _, out = run_main(monkeypatch, capsys, {"tyz3-vt28": [permitsf_row()]})
    assert ("FIRE-26-0001 | 100 | MARKET ST | Fire alarm permit | Fire alarm installation | "
            "Active - Issued | 2026-09-01 | 2026-10-01 | Fire Alarm Permit | 2026-10-01 | "
            "1000.0 | https://example.test/records/1") in out


def test_a_permitsf_error_is_reported_not_dropped(monkeypatch, capsys):
    err = [{"ERROR": "timed out", "url": "https://example.test/q"}]
    _, out = run_main(monkeypatch, capsys, {"tyz3-vt28": err})
    assert ("scope: PermitSF permits (newer system) (INCOMPLETE: a query failed; counts cover "
            "rows read before the error) | population 0 | included 0 | excluded: none") in out
    assert "ERROR: timed out (https://example.test/q)" in out


def test_cell_flattens_a_link_and_leaves_other_values_alone():
    assert spr.cell({"url": "https://example.test/x"}) == "https://example.test/x"
    assert spr.cell({"other": 1}) == "{'other': 1}"
    assert spr.cell(12.5) == "12.5"
    assert spr.cell("") == ""


# --- the older datasets ---------------------------------------------------------------------

def test_every_dataset_gets_its_address_query(monkeypatch, capsys):
    calls, out = run_main(monkeypatch, capsys)
    addr = "street_number='100' AND upper(street_name) in ('MARKET')"
    for ds in spr.DATASETS:
        assert (ds, addr) in calls, ds
    for ds, (_title, field, _cols) in spr.ADDRESS_LIKE.items():
        assert (ds, f"upper({field}) like '100 MARKET%'") in calls, ds
    for title, *_rest in [*spr.DATASETS.values(), *spr.ADDRESS_LIKE.values()]:
        assert f"### {title}\nscope: {title} | population 0 | included 0 | excluded: none" in out
    assert len(calls) == len(spr.DATASETS) + len(spr.ADDRESS_LIKE) + 1  # + PermitSF, no assessor


def test_an_address_text_dataset_excludes_a_longer_street(monkeypatch, capsys):
    rows = [{"permit_address": "100 Market St", "permit_number": "P1"},
            {"permit_address": "100 Market Rock St", "permit_number": "P2"}]
    _, out = run_main(monkeypatch, capsys, {"893e-xam6": rows})
    section = out.split("### Fire permits")[1].split("###")[0]
    assert ("scope: Fire permits | population 2 | included 1 | "
            "excluded: a different street or number 1") in section
    assert "P1 |" in section and "P2" not in section


def test_show_prints_an_error_row_and_keeps_going(capsys):
    spr.show("T", [{"ERROR": "e", "url": "u"}, {"a": "x", "b": "y"}], ["a", "b"])
    assert capsys.readouterr().out == (
        "\n### T\nscope: T (INCOMPLETE: a query failed; counts cover rows read before the error)"
        " | population 1 | included 1 | excluded: none\nERROR: e (u)\nx | y\n")


def test_show_cuts_a_long_cell_at_220_characters(capsys):
    spr.show("T", [{"a": "x" * 300}], ["a"])
    assert capsys.readouterr().out.splitlines()[-1] == "x" * 220


# --- the assessor roll ----------------------------------------------------------------------

def test_no_parcel_prints_the_manual_lookup_note(monkeypatch, capsys):
    calls, out = run_main(monkeypatch, capsys)
    assert "No block and lot found in the building permits; look up the parcel manually." in out
    assert not [c for c in calls if c[0] == spr.ASSESSOR[0]]


def test_the_parcel_comes_from_the_building_permits_already_read(monkeypatch, capsys):
    permits = [{"block": "0001", "lot": "002"}, {"block": "0001", "lot": "002"}, {"lot": "9"}]
    roll = [{"closed_roll_year": "2025"}]
    calls, out = run_main(monkeypatch, capsys, {"i98e-djp9": permits, spr.ASSESSOR[0]: roll})
    assert [c for c in calls if c[0] == "i98e-djp9"] == [
        ("i98e-djp9", "street_number='100' AND upper(street_name) in ('MARKET')")]
    assert [c for c in calls if c[0] == spr.ASSESSOR[0]] == [
        (spr.ASSESSOR[0], "block='0001' AND lot='002'")]
    assert "### Assessor roll, block 0001 lot 002\nscope: Assessor roll, block 0001 lot 002 | population 1" in out
    assert "look up the parcel manually" not in out


def test_parcels_print_in_order(monkeypatch, capsys):
    blocks = ["0005", "0003", "0001", "0004", "0002", "0006"]  # one order in 720 is sorted
    permits = [{"block": b, "lot": "001"} for b in blocks]
    _, out = run_main(monkeypatch, capsys, {"i98e-djp9": permits})
    found = [out.index(f"block {b} lot 001") for b in sorted(blocks)]
    assert found == sorted(found)


def test_a_parcel_with_no_lot_is_skipped_not_queried(monkeypatch, capsys):
    permits = [{"block": "0001", "lot": "002"}, {"block": "0001", "lot": None}, {"block": "0003"}]
    calls, out = run_main(monkeypatch, capsys, {"i98e-djp9": permits})
    assert [c for c in calls if c[0] == spr.ASSESSOR[0]] == [(spr.ASSESSOR[0], "block='0001' AND lot='002'")]
    assert "lot None" not in out


def test_a_failed_building_query_is_not_read_as_a_parcel(monkeypatch, capsys):
    calls, out = run_main(monkeypatch, capsys, {"i98e-djp9": [{"ERROR": "x", "url": "u", "block": "9"}]})
    assert not [c for c in calls if c[0] == spr.ASSESSOR[0]]


def test_the_suffix_reaches_every_dataset(monkeypatch, capsys):
    permits = [{"street_suffix": "St", "permit_number": "S1"}, {"street_suffix": "Av", "permit_number": "A1"}]
    _, out = run_main(monkeypatch, capsys, {"i98e-djp9": permits}, argv=("100", "19th", "av"))
    section = out.split("### Building permits")[1].split("###")[0]
    assert "population 2 | included 1 | excluded: a different street suffix 1" in section
    assert "A1 |" in section and "S1" not in section


def test_without_a_suffix_two_streets_print_nothing_and_say_why(monkeypatch, capsys):
    permits = [{"street_suffix": "St", "permit_number": "S1"}, {"street_suffix": "Av", "permit_number": "A1"}]
    _, out = run_main(monkeypatch, capsys, {"i98e-djp9": permits}, argv=("100", "19th"))
    section = out.split("### Building permits")[1].split("###")[0]
    assert "AMBIGUOUS: withheld. Records at this number are on 2 streets (AVE, ST);" in section
    assert "S1" not in section and "A1" not in section


def test_an_apostrophe_is_escaped_not_dropped(monkeypatch, capsys):
    calls, _ = run_main(monkeypatch, capsys, argv=("100", "o'farrell"))
    assert ("i98e-djp9", "street_number='100' AND upper(street_name) in ('O''FARRELL')") in calls
    assert ("tyz3-vt28", "streetno='100' AND upper(streetname) like 'O''FARRELL%'") in calls
    assert ("893e-xam6", "upper(permit_address) like '100 O''FARRELL%'") in calls


# --- query(): paging and errors ------------------------------------------------------------

def fake_pages(monkeypatch, pages, fail_at=None):
    urls = []

    def urlopen(url, timeout):
        urls.append((url, timeout))
        if fail_at is not None and len(urls) - 1 == fail_at:
            raise OSError("network down")
        return io.BytesIO(json.dumps(pages[len(urls) - 1]).encode())

    monkeypatch.setattr(spr.urllib.request, "urlopen", urlopen)
    return urls


def test_query_reads_every_page_until_a_short_one(monkeypatch):
    urls = fake_pages(monkeypatch, [[{"i": 1}, {"i": 2}], [{"i": 3}, {"i": 4}], [{"i": 5}]])
    assert spr.query("abcd-1234", "x='1'", page=2) == [{"i": n} for n in range(1, 6)]
    base = "https://data.sf.gov/resource/abcd-1234.json?%24where=x%3D%271%27&%24limit=2"
    assert urls == [(f"{base}&%24offset=0&%24order=%3Aid", 60),
                    (f"{base}&%24offset=2&%24order=%3Aid", 60),
                    (f"{base}&%24offset=4&%24order=%3Aid", 60)]


def test_query_stops_after_an_empty_page(monkeypatch):
    urls = fake_pages(monkeypatch, [[{"i": 1}, {"i": 2}], []])
    assert spr.query("abcd-1234", "x='1'", page=2) == [{"i": 1}, {"i": 2}]
    assert len(urls) == 2


def test_query_default_page_is_1000(monkeypatch):
    urls = fake_pages(monkeypatch, [[]])
    spr.query("abcd-1234", "x='1'")
    assert "%24limit=1000&" in urls[0][0]


def test_a_failure_mid_way_keeps_the_rows_read_and_reports_the_error(monkeypatch):
    fake_pages(monkeypatch, [[{"i": 1}, {"i": 2}]], fail_at=1)
    rows = spr.query("abcd-1234", "x='1'", page=2)
    assert rows[:2] == [{"i": 1}, {"i": 2}]
    assert rows[2]["ERROR"] == "network down" and "%24offset=2" in rows[2]["url"]
    assert len(rows) == 3


# --- the command line -----------------------------------------------------------------------

def test_too_few_arguments_prints_usage_and_exits_2():
    proc = subprocess.run([sys.executable, str(TOOL), "100"], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 2
    assert "Usage:" in proc.stdout


def test_a_multi_word_street_is_joined_and_upper_cased(monkeypatch, capsys):
    calls, _ = run_main(monkeypatch, capsys, argv=("100", "van", "ness"))
    assert ("i98e-djp9", "street_number='100' AND upper(street_name) in ('VAN NESS')") in calls


def test_a_suffix_with_nothing_to_exclude_reports_no_exclusion():
    kept, excluded, note = spr.select([{"a": "100 MARKET ST"}], street_text, "ST")
    assert (len(kept), excluded, note) == (1, {}, "")


# --- one street for the whole report (round 3) ---------------------------------------------

def test_two_datasets_on_different_suffixes_are_both_withheld(monkeypatch, capsys):
    resp = {"i98e-djp9": [{"street_suffix": "St", "permit_number": "S1", "block": "1", "lot": "2"}],
            "ftty-kx6y": [{"street_suffix": "Av", "permit_number": "A1"}]}
    calls, out = run_main(monkeypatch, capsys, resp, argv=("100", "19th"))
    assert out.startswith("AMBIGUOUS: 100 19TH matches 2 streets (AVE, ST).")
    assert "S1" not in out and "A1" not in out
    assert "Withheld: the building permits are ambiguous; rerun with the suffix." in out
    assert not [c for c in calls if c[0] == spr.ASSESSOR[0]]
    # a dataset with no records reports no exclusion, not "ambiguous 0"
    assert "scope: PermitSF permits (newer system) | population 0 | included 0 | excluded: none" in out


def test_one_suffix_across_all_datasets_is_named_at_the_top(monkeypatch, capsys):
    resp = {"i98e-djp9": [{"street_suffix": "St", "permit_number": "S1"}],
            "893e-xam6": [{"permit_address": "100 19th", "permit_number": "F1"}]}
    _, out = run_main(monkeypatch, capsys, resp, argv=("100", "19th"))
    assert out.startswith("Street: 100 19TH ST (no suffix given; the only one in these records)\n")
    assert "S1 |" in out and "F1 |" in out


def test_no_suffix_anywhere_prints_no_street_line(monkeypatch, capsys):
    _, out = run_main(monkeypatch, capsys)
    assert not out.startswith("Street:") and "AMBIGUOUS" not in out


def test_a_name_ending_in_a_suffix_word_is_queried_both_ways(monkeypatch, capsys):
    resp = {"i98e-djp9": [{"street_name": "South Park", "street_suffix": "St", "permit_number": "P1"},
                          {"street_name": "South", "street_suffix": "St", "permit_number": "P2"}]}
    calls, out = run_main(monkeypatch, capsys, resp, argv=("100", "south", "park"))
    assert ("i98e-djp9", "street_number='100' AND upper(street_name) in ('SOUTH', 'SOUTH PARK')") in calls
    section = out.split("### Building permits")[1].split("###")[0]
    assert "P1 |" in section and "P2" not in section


def test_any_failed_query_makes_the_exit_status_1(monkeypatch):
    monkeypatch.setattr(spr, "query", lambda ds, where, page=spr.PAGE: (
        [{"ERROR": "down", "url": "u"}] if ds == "gm2e-bten" else []))
    assert spr.main(["sf_property_records.py", "100", "market"]) == 1


def test_a_failed_assessor_query_makes_the_exit_status_1(monkeypatch):
    def fake(ds, where, page=spr.PAGE):
        if ds == "i98e-djp9":
            return [{"block": "1", "lot": "2"}]
        return [{"ERROR": "down", "url": "u"}] if ds == spr.ASSESSOR[0] else []
    monkeypatch.setattr(spr, "query", fake)
    assert spr.main(["sf_property_records.py", "100", "market"]) == 1


# --- the building department's suffix codes (round 4) ------------------------------------

@pytest.mark.parametrize("raw,street", [
    ("Bl", "BLVD"), ("WY", "WAY"), ("Tr", "TER"), ("Hy", "HWY"), ("Hw", "HWY"), ("Pz", "PLZ"),
    ("Cr", "CIR"), ("Al", "ALY"), ("Pk", "PARK"), ("Sw", "STWY"), ("Rw", "ROW"), ("Wk", "WALK"),
    ("Lp", "LOOP"), ("Sq", "SQ"), ("St", "ST"), ("Av", "AVE"),
    ("*", ""), ("", ""), (None, ""), ("  ", ""),
    ("Zz", "?ZZ"), ("4", "?4"),
])
def test_exact_street_maps_department_codes_and_flags_unknown_ones(raw, street):
    assert spr.exact_street("MARKET", "MARKET")({"street_name": "Market", "street_suffix": raw}) == street


def test_an_unknown_code_is_excluded_under_a_requested_suffix(monkeypatch, capsys):
    resp = {"i98e-djp9": [{"street_suffix": "St", "permit_number": "S1"},
                          {"street_suffix": "Zz", "permit_number": "Z1"}]}
    _, out = run_main(monkeypatch, capsys, resp, argv=("100", "market", "st"))
    section = out.split("### Building permits")[1].split("###")[0]
    assert "included 1 | excluded: a different street suffix 1" in section and "Z1" not in section


def test_an_unknown_code_beside_a_known_one_is_ambiguous(monkeypatch, capsys):
    resp = {"i98e-djp9": [{"street_suffix": "St", "permit_number": "S1"},
                          {"street_suffix": "Zz", "permit_number": "Z1"}]}
    _, out = run_main(monkeypatch, capsys, resp, argv=("100", "market"))
    assert out.startswith("AMBIGUOUS: 100 MARKET matches 2 streets (?ZZ, ST).")


def test_a_resolved_street_after_a_failure_says_incomplete(monkeypatch, capsys):
    resp = {"i98e-djp9": [{"street_suffix": "St", "permit_number": "S1"}],
            "gm2e-bten": [{"ERROR": "down", "url": "u"}]}
    _, out = run_main(monkeypatch, capsys, resp)
    assert out.startswith("Street: 100 MARKET ST (no suffix given; the only one in these records; "
                          "some queries failed, so this read is INCOMPLETE)\n")


def test_a_failed_building_query_does_not_claim_no_parcel(monkeypatch, capsys):
    _, out = run_main(monkeypatch, capsys, {"i98e-djp9": [{"ERROR": "down", "url": "u"}]})
    assert "Unknown: the building permits query failed, so no parcel could be read." in out
    assert "No block and lot found" not in out


# --- round 5 ---------------------------------------------------------------------------------

def test_both_readings_of_a_suffix_ending_name_are_withheld(monkeypatch, capsys):
    resp = {"i98e-djp9": [{"street_name": "South Park", "permit_number": "P1"},
                          {"street_name": "South", "street_suffix": "Pk", "permit_number": "P2"}]}
    _, out = run_main(monkeypatch, capsys, resp, argv=("100", "south", "park"))
    assert out.startswith("AMBIGUOUS: 100 SOUTH PARK matches a street named SOUTH PARK and the street "
                          "SOUTH with suffix PARK.")
    assert "P1" not in out and "P2" not in out


def test_only_the_full_name_reading_is_kept(monkeypatch, capsys):
    resp = {"i98e-djp9": [{"street_name": "South Park", "permit_number": "P1"},
                          {"street_name": "South", "street_suffix": "St", "permit_number": "P2"}]}
    _, out = run_main(monkeypatch, capsys, resp, argv=("100", "south", "park"))
    assert not out.startswith("AMBIGUOUS")
    section = out.split("### Building permits")[1].split("###")[0]
    assert "P1 |" in section and "P2" not in section


def test_a_lone_unknown_code_is_not_adopted_as_the_street(monkeypatch, capsys):
    resp = {"i98e-djp9": [{"street_suffix": "Zz", "permit_number": "Z1"}]}
    _, out = run_main(monkeypatch, capsys, resp)
    assert out.startswith("Street: 100 MARKET, with suffix code 'ZZ', which this tool does not know;")
    assert "Z1 |" in out


def test_free_text_alone_is_not_evidence_for_the_second_reading(monkeypatch, capsys):
    resp = {"i98e-djp9": [{"street_name": "South Park", "permit_number": "P1"}],
            "893e-xam6": [{"permit_address": "100 South Park", "permit_number": "F1"}]}
    _, out = run_main(monkeypatch, capsys, resp, argv=("100", "south", "park"))
    assert not out.startswith("AMBIGUOUS")
    assert "P1 |" in out and "F1 |" in out
