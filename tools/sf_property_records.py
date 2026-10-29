"""sf_property_records.py - pull San Francisco public records for one street address.

Queries the city's open-data portal (data.sf.gov; the old data.sfgov.org host
redirects) for building, electrical, plumbing and boiler permits, DBI
complaints, fire permits/inspections/violations, planning records, registered
businesses at the address, and the assessor roll for the parcel. Read-only;
no API key needed. Prints one Markdown section per dataset.

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/sf_property_records.py <street_number> <STREET NAME>
    e.g. python3 tools/sf_property_records.py 100 MARKET

Not covered (not in open data): health-department pool/spa permits and
inspections. Planning "project_decision" is often empty in the dataset even
when a decision exists; confirm in the Planning Department's own portal.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

BASE = "https://data.sf.gov/resource/"

# dataset id -> (title, columns to print)
DATASETS = {
    "i98e-djp9": ("Building permits", ["permit_number", "permit_type_definition", "filed_date", "issued_date", "status", "status_date", "revised_cost", "description"]),
    "ftty-kx6y": ("Electrical permits", ["permit_number", "filed_date", "issued_date", "completed_date", "status", "permit_valuation", "description"]),
    "a6aw-rudh": ("Plumbing permits", ["permit_number", "filed_date", "issued_date", "completed_date", "status", "valuation", "description"]),
    "5dp4-gtxk": ("Boiler permits", ["permit_number", "application_date", "status", "expiration_date", "boiler_type", "description"]),
    "gm2e-bten": ("DBI complaints", ["complaint_number", "date_filed", "status", "nov_type", "closed_date", "complaint_description"]),
}
ADDRESS_LIKE = {
    "893e-xam6": ("Fire permits", "permit_address", ["permit_number", "permit_type_description", "permit_application_date", "permit_date_approved", "permit_status", "permit_holder"]),
    "wb4c-6hwj": ("Fire inspections", "address", ["inspection_number", "inspection_type_description", "inspection_start_date", "inspection_status", "permit_number"]),
    "4zuq-2cbe": ("Fire violations", "address", ["violation_date", "status", "close_date", "violation_item_description"]),
    "qvu5-m3a2": ("Planning records", "project_address", ["record_id", "record_type", "open_date", "close_date", "record_status", "project_decision", "description"]),
    "g8m3-pdis": ("Registered businesses", "full_business_address", ["ownership_name", "dba_name", "location_start_date", "location_end_date", "self_reported_naics_code"]),
}
ASSESSOR = ("wv5m-vpq2", ["closed_roll_year", "use_definition", "year_property_built", "number_of_stories", "property_area", "lot_area", "zoning_code", "current_sales_date", "assessed_land_value", "assessed_improvement_value"])


def query(dataset: str, where: str, limit: int = 200) -> list[dict]:
    url = BASE + dataset + ".json?" + urllib.parse.urlencode({"$where": where, "$limit": limit})
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return json.load(resp)
    except Exception as exc:  # report, never silently return empty
        return [{"ERROR": str(exc), "url": url}]


def show(title: str, rows: list[dict], cols: list[str]) -> None:
    print(f"\n### {title} ({len(rows)} rows)")
    for r in rows:
        if "ERROR" in r:
            print(f"ERROR: {r['ERROR']} ({r['url']})")
            continue
        print(" | ".join(str(r.get(c, ""))[:220] for c in cols))


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    num, street = argv[1], " ".join(argv[2:]).upper().replace("'", "")
    addr_where = f"street_number='{num}' AND upper(street_name)='{street}'"
    for ds, (title, cols) in DATASETS.items():
        show(title, query(ds, addr_where), cols)
    for ds, (title, field, cols) in ADDRESS_LIKE.items():
        show(title, query(ds, f"upper({field}) like '{num} {street}%'"), cols)
    parcels = {(r.get("block"), r.get("lot")) for r in query("i98e-djp9", addr_where, 5) if r.get("block")}
    for block, lot in parcels:
        show(f"Assessor roll, block {block} lot {lot}", query(ASSESSOR[0], f"block='{block}' AND lot='{lot}'"), ASSESSOR[1])
    if not parcels:
        print("\n### Assessor roll\nNo block/lot found from building permits; look up the parcel manually.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
