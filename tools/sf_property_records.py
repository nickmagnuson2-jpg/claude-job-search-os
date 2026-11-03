"""sf_property_records.py - pull San Francisco public records for one street address.

Queries the city's open-data portal (data.sf.gov; the old data.sfgov.org host
redirects) for building, electrical, plumbing and boiler permits, DBI
complaints, fire permits/inspections/violations, planning records, registered
businesses at the address, and the assessor roll for the parcel. It also reads
PermitSF (tyz3-vt28), the city's newer permit system: new fire alarm, electrical
and plumbing filings land there and NOT in the older permit datasets above, so a
run without it misses them. Read-only; no API key needed. Prints one Markdown
section per dataset, each with a count_report line giving its reach.

Every query is paged to the end, so no dataset is cut off at a page size.
Every row is then assigned to a street by street_of() (the name, then a street
suffix, a unit word, a ZIP, "," or "#"), and select() keeps only the requested
street. A longer street that starts with the same name is excluded. Give the
suffix (ST, AVE, ...) when two streets share a name, such as a numbered street
that is both an ST and an AVE; without it, a dataset whose rows span more than
one suffix across all datasets prints none of them and says so. The street is
decided once for the whole report, not per dataset. An input whose last word is
also a suffix (SOUTH PARK) is read both ways; if both readings have records,
every section withholds them.

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/sf_property_records.py <street_number> <STREET NAME> [SUFFIX]
    e.g. python3 tools/sf_property_records.py 100 MARKET ST

Exit status: 0 when every query succeeded, 1 when any query failed (that section
says INCOMPLETE), 2 on a usage error.

Not covered (not in open data): health-department pool/spa permits and
inspections. Planning "project_decision" is often empty in the dataset even
when a decision exists; confirm in the Planning Department's own portal.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

from count_report import format_line

BASE = "https://data.sf.gov/resource/"
PAGE = 1000

# dataset id -> (title, columns to print). Matched on exact street_number and street_name.
DATASETS = {
    "i98e-djp9": ("Building permits", ["permit_number", "permit_type_definition", "filed_date", "issued_date", "status", "status_date", "revised_cost", "description"]),
    "ftty-kx6y": ("Electrical permits", ["permit_number", "filed_date", "issued_date", "completed_date", "status", "permit_valuation", "description"]),
    "a6aw-rudh": ("Plumbing permits", ["permit_number", "filed_date", "issued_date", "completed_date", "status", "valuation", "description"]),
    "5dp4-gtxk": ("Boiler permits", ["permit_number", "application_date", "status", "expiration_date", "boiler_type", "description"]),
    "gm2e-bten": ("DBI complaints", ["complaint_number", "date_filed", "status", "nov_type", "closed_date", "complaint_description"]),
}
# dataset id -> (title, free-text address field, columns). Matched by street_of().
ADDRESS_LIKE = {
    "893e-xam6": ("Fire permits", "permit_address", ["permit_number", "permit_type_description", "permit_application_date", "permit_date_approved", "permit_status", "permit_holder"]),
    "wb4c-6hwj": ("Fire inspections", "address", ["inspection_number", "inspection_type_description", "inspection_start_date", "inspection_status", "permit_number"]),
    "4zuq-2cbe": ("Fire violations", "address", ["violation_date", "status", "close_date", "violation_item_description"]),
    "qvu5-m3a2": ("Planning records", "project_address", ["record_id", "record_type", "open_date", "close_date", "record_status", "project_decision", "description"]),
    "g8m3-pdis": ("Registered businesses", "full_business_address", ["ownership_name", "dba_name", "location_start_date", "location_end_date", "self_reported_naics_code"]),
}
PERMITSF = ("tyz3-vt28", "PermitSF permits (newer system)", ["recordno", "streetno", "streetname", "recordtype", "description", "status_detail", "submitted_date", "permit_issued_date", "last_doc", "last_doc_date", "valuation", "url"])
ASSESSOR = ("wv5m-vpq2", ["closed_roll_year", "use_definition", "year_property_built", "number_of_stories", "property_area", "lot_area", "zoning_code", "current_sales_date", "assessed_land_value", "assessed_improvement_value"])

def query(dataset: str, where: str, page: int = PAGE) -> list[dict]:
    """Every row matching `where`, read page by page until a short page."""
    rows: list[dict] = []
    offset = 0
    while True:
        url = BASE + dataset + ".json?" + urllib.parse.urlencode(
            {"$where": where, "$limit": page, "$offset": offset, "$order": ":id"})
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                batch = json.load(resp)
        except Exception as exc:  # report, never silently return what was read so far
            return rows + [{"ERROR": str(exc), "url": url}]
        rows.extend(batch)
        if len(batch) < page:
            return rows
        offset += page


# Street suffixes in any spelling the city datasets use, mapped to one form. Measured on
# PermitSF and the five street_suffix fields on 2026-11-02; full words added from USPS.
CANON = {
    "ST": "ST", "STREET": "ST", "AV": "AVE", "AVE": "AVE", "AVENUE": "AVE",
    "BLVD": "BLVD", "BOULEVARD": "BLVD", "DR": "DR", "DRIVE": "DR", "TER": "TER",
    "TERRACE": "TER", "CT": "CT", "COURT": "CT", "PL": "PL", "PLACE": "PL", "LN": "LN",
    "LANE": "LN", "RD": "RD", "ROAD": "RD", "ALY": "ALY", "ALLEY": "ALY", "CIR": "CIR",
    "CIRCLE": "CIR", "PARK": "PARK", "ROW": "ROW", "XING": "XING", "CROSSING": "XING",
    "STWY": "STWY", "STAIRWAY": "STWY", "PLZ": "PLZ", "PLAZA": "PLZ", "HWY": "HWY",
    "HIGHWAY": "HWY", "WAY": "WAY", "WALK": "WALK", "LOOP": "LOOP", "SQUARE": "SQ", "SQ": "SQ",
    # The building department's two-letter codes, as stored in the older datasets'
    # street_suffix field (counted 2026-11-02: Bl 49,546 rows, Wy 43,433, Tr 18,575, ...).
    "BL": "BLVD", "WY": "WAY", "TR": "TER", "HY": "HWY", "HW": "HWY", "PZ": "PLZ",
    "CR": "CIR", "AL": "ALY", "PK": "PARK", "SW": "STWY", "RW": "ROW", "WK": "WALK", "LP": "LOOP",
}
# A street_suffix value that means "no suffix recorded" rather than a street type.
NO_SUFFIX = frozenset({"", "*"})
UNIT_WORDS = frozenset({"STE", "SUITE", "UNIT", "APT", "FL", "PARCEL"})
OTHER_NAME = "a different street or number"
AMBIGUOUS = "ambiguous: no suffix given"
OTHER_SUFFIX = "a different street suffix"
NO_ADDRESS = "no address in the record"


def canonical(token: str | None) -> str | None:
    """'Av' -> 'AVE', 'street.' -> 'ST'; None when the token is not a street suffix."""
    return CANON.get((token or "").strip().upper().rstrip(".,"))


def street_of(rest: str) -> str | None:
    """Read the text after the street name. -> the canonical suffix ('ST', 'AVE', ...),
    '' when the street is named with no suffix, or None for a longer, different name."""
    if not rest or rest[0] in ",#":
        return ""
    if rest[0] != " ":
        return None  # "MARKETS ...": the name continues
    words = rest.split()
    if not words:
        return None
    token = words[0].upper().rstrip(".,")
    suffix = canonical(token)
    if suffix:
        return suffix
    if token in UNIT_WORDS or token.isdigit() or token.startswith("#"):
        return ""
    return None


def classify(rows: list[dict], street) -> tuple[list[dict], list[tuple[dict, str]], dict[str, int]]:
    """Split rows into ERROR rows, (row, suffix) candidates on the requested street name,
    and exclusions by reason. `street(row)` returns a canonical suffix, '' (named without
    one), None (another street or number) or the NO_ADDRESS marker."""
    errors = [r for r in rows if "ERROR" in r]
    excluded: dict[str, int] = {}
    candidates = []
    for r in rows:
        if "ERROR" in r:
            continue
        s = street(r)
        if s is NO_ADDRESS or s is None:
            reason = NO_ADDRESS if s is NO_ADDRESS else OTHER_NAME
            excluded[reason] = excluded.get(reason, 0) + 1
        else:
            candidates.append((r, s))
    return errors, candidates, excluded


def suffixes_in(candidates: list[tuple[dict, str]]) -> set[str]:
    return {s for _, s in candidates if s}


def select(rows: list[dict], street, suffix: str | None,
           seen: list[str] | None = None) -> tuple[list[dict], dict[str, int], str]:
    """Keep the rows on the requested street. With `suffix`, keep that suffix and rows with
    none. Without it, refuse when the suffixes `seen` (by default this dataset's own; main()
    passes every dataset's) number more than one. -> (kept rows, exclusions, note or '')."""
    errors, candidates, excluded = classify(rows, street)
    if suffix:
        kept = [r for r, s in candidates if s in ("", suffix) or s.startswith("(named ")]
        other = len(candidates) - len(kept)
        if other:
            excluded[OTHER_SUFFIX] = other
        return kept + errors, excluded, ""
    seen = sorted(suffixes_in(candidates)) if seen is None else seen
    if len(seen) > 1:
        if candidates:
            excluded[AMBIGUOUS] = len(candidates)
        return errors, excluded, (f"AMBIGUOUS: withheld. Records at this number are on {len(seen)} "
                                  f"streets ({', '.join(seen)}); see the report's first line.")
    return [r for r, _ in candidates] + errors, excluded, ""


def address_street(field: str, prefix: str):
    """street() for a free-text address field that should start with `prefix`."""
    def street(r: dict):
        text = str(r.get(field) or "").strip()
        if not text:
            return NO_ADDRESS
        if not text.upper().startswith(prefix):
            return None
        return street_of(text[len(prefix):])
    return street


def show(title: str, rows: list[dict], cols: list[str],
         excluded: dict[str, int] | None = None, note: str = "") -> None:
    data = [r for r in rows if "ERROR" not in r]
    excluded = excluded or {}
    scope = title
    if any("ERROR" in r for r in rows):
        scope += " (INCOMPLETE: a query failed; counts cover rows read before the error)"
    print(f"\n### {title}")
    print(format_line(scope, len(data) + sum(excluded.values()), len(data), excluded or None))
    if note:
        print(note)
    for r in rows:
        if "ERROR" in r:
            print(f"ERROR: {r['ERROR']} ({r['url']})")
            continue
        print(" | ".join(cell(r.get(c, ""))[:220] for c in cols))


def cell(value) -> str:
    """One printable cell. PermitSF returns its record link as {"url": "..."}."""
    if isinstance(value, dict) and "url" in value:
        return str(value["url"])
    return str(value)


def soql(text: str) -> str:
    """A SoQL string literal's content: a quote is doubled, never dropped (O'FARRELL)."""
    return text.replace("'", "''")


def parse_address(argv: list[str]) -> tuple[str, str, str | None, str]:
    """argv after the script -> (number, street name, canonical suffix or None, full name).
    The last word is read as the suffix when it is one and a name word precedes it; the full
    name is kept too, because some names end in a suffix word (SOUTH PARK)."""
    num, words = argv[0].upper(), [w.upper() for w in argv[1:]]
    full = " ".join(words)
    if len(words) > 1 and canonical(words[-1]):
        return num, " ".join(words[:-1]), canonical(words[-1]), full
    return num, full, None, full


def named(full: str) -> str:
    """The label for a row on a street NAMED the full input (SOUTH PARK), as opposed to the
    shorter name plus that suffix (SOUTH + PARK)."""
    return f"(named {full})"


def exact_street(name: str, full: str):
    """street() for the older permit datasets, queried for both readings of the input: a
    row whose street_name is the full input is on it whatever its suffix field says; a row
    on the shorter name is decided by its street_suffix field."""
    def street(r: dict):
        if full != name and str(r.get("street_name") or "").upper() == full:
            return named(full)
        raw = str(r.get("street_suffix") or "").strip()
        if raw.upper() in NO_SUFFIX:
            return ""
        # An unknown code is its own suffix, never "none": read as none it would match any
        # requested suffix and hide from the ambiguity check (round 4, F1).
        return canonical(raw) or f"?{raw.upper()}"
    return street


def permitsf_where(num: str, street: str) -> str:
    """Street number exact; street name starting with the requested name. select() then
    drops longer names and other suffixes."""
    return f"streetno='{soql(num)}' AND upper(streetname) like '{soql(street)}%'"


def main(argv: list[str]) -> int:
    """Exit 0 when every query succeeded, 1 when any failed (its section says INCOMPLETE),
    2 on a usage error."""
    if len(argv) < 3:
        print(__doc__)
        return 2
    num, street, suffix, full = parse_address(argv[1:])
    names = sorted({street, full})
    name_in = ", ".join(f"'{soql(n)}'" for n in names)
    addr_where = f"street_number='{soql(num)}' AND upper(street_name) in ({name_in})"
    prefix = f"{num} {street}"
    sections = []  # (dataset, title, rows, street(), cols), all read before any is printed
    for ds, (title, cols) in DATASETS.items():
        sections.append((ds, title, query(ds, addr_where), exact_street(street, full), cols))
    for ds, (title, field, cols) in ADDRESS_LIKE.items():
        sections.append((ds, title, query(ds, f"upper({field}) like '{soql(prefix)}%'"),
                         address_street(field, prefix), cols))
    sections.append((PERMITSF[0], PERMITSF[1], query(PERMITSF[0], permitsf_where(num, street)),
                     address_street("streetname", street), PERMITSF[2]))

    # One street for the whole report: a suffix decided per dataset could print one
    # street's records in one section and another street's in the next.
    seen = None
    labels = set().union(*(suffixes_in(classify(rows, fn)[1]) for _, _, rows, fn, _ in sections))
    # Both readings of an input ending in a suffix word (SOUTH PARK) have records. Only the
    # older datasets count here: they store the name and the suffix apart, while a free-text
    # "100 SOUTH PARK" reads the same either way and is evidence for neither.
    exact = set().union(*(suffixes_in(classify(rows, fn)[1]) for ds, _, rows, fn, _ in sections
                          if ds in DATASETS))
    if suffix in exact and named(full) in exact:
        seen, suffix = sorted([named(full), f"{street} {suffix}"]), None
        print(f"AMBIGUOUS: {num} {full} matches a street named {full} and the street {street} "
              f"with suffix {seen[1].split()[-1]}. Every section below withholds its records; "
              "check them by hand.")
    if not suffix and seen is None:
        seen = sorted(labels)
        partial = any("ERROR" in r for _, _, rows, _, _ in sections for r in rows)
        if len(seen) == 1 and seen[0].startswith("?"):
            print(f"Street: {num} {street}, with suffix code {seen[0][1:]!r}, which this tool does not "
                  "know; its records print below.")
        elif len(seen) == 1:
            suffix = seen[0]
            print(f"Street: {num} {street} {suffix} (no suffix given; the only one in these records"
                  + ("; some queries failed, so this read is INCOMPLETE)" if partial else ")"))
        elif len(seen) > 1:
            print(f"AMBIGUOUS: {num} {street} matches {len(seen)} streets ({', '.join(seen)}). "
                  "Every section below withholds its records; rerun with the suffix.")
    failed = False
    building: list[dict] = []
    for ds, title, rows, fn, cols in sections:
        kept, excluded, note = select(rows, fn, suffix, seen)
        failed = failed or any("ERROR" in r for r in rows)
        if ds == "i98e-djp9":
            building = kept
        show(title, kept, cols, excluded, note)

    if seen and len(seen) > 1 and not suffix:
        print("\n### Assessor roll\nWithheld: the building permits are ambiguous; rerun with the suffix.")
        return 1 if failed else 0
    pairs = {(r.get("block"), r.get("lot")) for r in building if "ERROR" not in r and r.get("block")}
    parcels = sorted((b, l) for b, l in pairs if l)
    for block, lot in parcels:
        rows = query(ASSESSOR[0], f"block='{soql(str(block))}' AND lot='{soql(str(lot))}'")
        failed = failed or any("ERROR" in r for r in rows)
        show(f"Assessor roll, block {block} lot {lot}", rows, ASSESSOR[1])
    if not parcels and any("ERROR" in r for r in building):
        print("\n### Assessor roll\nUnknown: the building permits query failed, so no parcel could be read.")
    elif not parcels:
        print("\n### Assessor roll\nNo block and lot found in the building permits; look up the parcel manually.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
