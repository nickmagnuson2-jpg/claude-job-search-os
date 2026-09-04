#!/usr/bin/env python3
"""Mechanically diff v1 (workbook Contact Register) against v3 (records TSV).

Prose reading missed a real conflict (Acme Corp / Ada Placeholder). This does not read prose.
Usage: PYTHONIOENCODING=utf-8 python3 conflict_diff.py
"""
import argparse, csv, sys, collections, openpyxl

ap = argparse.ArgumentParser(description="Mechanically diff two contact runs. Exit 1 if hard conflicts remain.")
ap.add_argument("--workbook", required=True, help="xlsx holding the first run's Contact Register")
ap.add_argument("--records", required=True, help="CSV of the second run: company,function,person,verdict")
ap.add_argument("--sheet", default="Contact Register", help="worksheet name in --workbook")
ap.add_argument("--first-row", type=int, default=4, help="first data row in that sheet (1-indexed)")
_A = ap.parse_args()
XLSX, V3, SHEET, FIRST_ROW = _A.workbook, _A.records, _A.sheet, _A.first_row

# v1's "Sought title satisfied" strings -> canonical function keys.
MAP = {
 "americas managing director":"americas_lead", "global alliances":"alliances", "alliances":"alliances",
 "partnerships":"alliances", "chief revenue officer":"revenue", "chief commercial officer":"commercial",
 "ai strategy practice lead":"ai_strategy", "automation practice lead":"automation",
 "finance transformation partner":"finance_transformation",
 "business transformation partner":"business_transformation",
 "ceo/president":"ceo", "ceo":"ceo", "healthcare automation lead":"healthcare_automation",
 "ai & automation vp":"ai_automation", "sap practice lead":"sap", "sap lead":"sap",
 "chief growth officer":"growth", "ai/automation practice lead":"ai_automation",
 "agentic automation lead":"agentic_automation", "data & analytics partner":"data_analytics",
 "supply chain partner":"supply_chain", "life sciences partner":"life_sciences",
 "ai & data practice lead":"ai_data", "appian practice lead":"appian",
 "life sciences lead":"life_sciences",
 # "(added in verification)" is NOT mapped. Those rows are supplemental contacts gathered
 # during v1 verification; they never asserted a function. Coercing them into runs_company
 # fabricated a HARD leadership conflict (Bo Placeholder/Cy Placeholder vs Di
 # Placeholder) and split Ada Placeholder
 # into a matched pair of one-sided GAPs. They are reported separately and never compared.
 "(added in verification)":"SUPPLEMENTAL",
}

def load_v1():
    ws = openpyxl.load_workbook(XLSX, data_only=True)[SHEET]
    out, unmapped = collections.defaultdict(set), set()
    supp = collections.defaultdict(set)   # (company) -> {"name (title)"}
    for r in range(FIRST_ROW, ws.max_row+1):
        co, name, sought = ws.cell(r,3).value, ws.cell(r,1).value, ws.cell(r,12).value
        if not co or not name: continue
        key = MAP.get(str(sought).strip().lower())
        if key is None: unmapped.add(str(sought)); continue
        if key == "SUPPLEMENTAL":
            supp[co.strip()].add(f"{name.strip()} ({ws.cell(r,2).value})")
            continue
        out[(co.strip(), key)].add(name.strip())
    return out, unmapped, supp

def load_v3():
    out = collections.defaultdict(set); neg=set()
    with open(V3) as f:
        for row in csv.DictReader(f):
            k=(row["company"].strip(), row["function"].strip())
            if row["verdict"].strip()=="NOBODY": neg.add(k)
            elif row["person"].strip(): out[k].add(row["person"].strip())
    return out, neg

v1, unmapped, supp = load_v1(); v3, v3_neg = load_v3()
conflicts=[]
for k in sorted(set(v1) | set(v3) | v3_neg):
    co,fn = k; a, b = v1.get(k,set()), v3.get(k,set())
    if k in v3_neg and a:
        conflicts.append(("HARD","%s / %s"%(co,fn),"v1 names %s"%", ".join(sorted(a)),"v3 says NOBODY"))
    elif a and b and not (a & b):
        conflicts.append(("HARD","%s / %s"%(co,fn),"v1: %s"%", ".join(sorted(a)),"v3: %s"%", ".join(sorted(b))))
    elif a and b and a != b:
        conflicts.append(("SOFT","%s / %s"%(co,fn),"v1: %s"%", ".join(sorted(a)),"v3: %s"%", ".join(sorted(b))))
    elif a and not b and k not in v3_neg:
        conflicts.append(("V3_GAP","%s / %s"%(co,fn),"v1: %s"%", ".join(sorted(a)),"v3: not addressed"))
    elif b and not a:
        conflicts.append(("V1_GAP","%s / %s"%(co,fn),"v1: not addressed","v3: %s"%", ".join(sorted(b))))

v1_people = {(co, n) for (co, _), ns in v1.items() for n in ns} | \
            {(co, s.split(" (")[0]) for co, ss in supp.items() for s in ss}
v3_people = {(co, n) for (co, _), ns in v3.items() for n in ns}
both = sorted(v1_people & v3_people)

order={"HARD":0,"SOFT":1,"V3_GAP":2,"V1_GAP":3}
conflicts.sort(key=lambda c:(order[c[0]],c[1]))
print(f"v1 records: {sum(len(v) for v in v1.values())} | v3 found: {sum(len(v) for v in v3.values())} | v3 negatives: {len(v3_neg)}")
if unmapped: print(f"!! UNMAPPED v1 sought-titles (fix MAP): {sorted(unmapped)}")
print(f"\n{'KIND':<7} {'COMPANY / FUNCTION':<45} V1 / V3")
print("-"*118)
for kind,key,a,b in conflicts: print(f"{kind:<7} {key:<45} {a}  ||  {b}")
n=sum(1 for c in conflicts if c[0]=="HARD")
print("-"*118)
print(f"HARD conflicts: {n}   SOFT: {sum(1 for c in conflicts if c[0]=='SOFT')}   "
      f"v3 gaps: {sum(1 for c in conflicts if c[0]=='V3_GAP')}   v1 gaps: {sum(1 for c in conflicts if c[0]=='V1_GAP')}")

if supp:
    print(f"\nSUPPLEMENTAL v1 contacts ({sum(len(v) for v in supp.values())}) -- gathered during")
    print("verification, asserted NO function. Not comparable; adjudicate separately.")
    for co in sorted(supp):
        for s_ in sorted(supp[co]): print(f"  {co}: {s_}")

if both:
    print(f"\nPRESENT IN BOTH RUNS ({len(both)}) -- person-level, ignoring function keys.")
    print("A name here is NOT a gap even where the function rows above disagree.")
    for co, n in both: print(f"  {co}: {n}")
sys.exit(1 if n else 0)
