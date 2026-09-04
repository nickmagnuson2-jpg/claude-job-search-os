#!/usr/bin/env python3
"""Gate a v4 run: reject one whose evidence was not preserved, then emit the scoring input.

Reports only. It writes nothing; the eval scorer it once fed was retired 2026-09-03
(see the retired control layer's WHY-RETIRED note).

WHAT THIS CAN AND CANNOT DO. It establishes that required evidence fields are present,
manifest-linked, internally consistent, and not obvious filler. It CANNOT establish that a
URL supports a claim about a person's current employer -- no static check of JSON can.
Proving that needs preserved snapshots or a human pass, and that remains open.

Usage: PYTHONIOENCODING=utf-8 python3 tools/validate_run.py <run.json> --manifest <manifest.csv>

Schema, per record:
  company, function        -- must appear in run_manifest.tsv
  verdict                  -- FOUND | NOBODY
  FOUND requires:  person, title, employer, source_url, source_type, tier (A|B), retrieved
  NOBODY requires: searched (>=40 chars), would_count_as_hit (>=20 chars)
  every record:    titles_searched -- >=3, nonempty, distinct, manifest-linked
"""
import argparse, json, csv, sys, re, datetime, collections
from pathlib import Path


ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BAD_HOSTS = ("example.com", "example.org", "example.net", "localhost", "127.0.0.1", "test.com")
PLACEHOLDER_NAMES = {"jane doe", "john doe", "john smith", "jane smith", "test user",
                     "harmless person", "n/a", "unknown", "tbd", "placeholder"}
ENTITY_HAZARD = {
    # Confusable COMPANIES, not people. A name match can never catch these -- a run does not
    # self-report a decoy employer. They are surfaced for a human to eyeball, nothing more.
    "Acme Corp": ["Acme Co", "Acme Technologies", "Akme Corp", "Acme Group"],
}


def load_manifest(path):
    m = collections.defaultdict(dict)
    for r in csv.DictReader(open(path)):
        m[r["company"]][r["function"]] = [v.strip() for v in r["title_variants"].split(";") if v.strip()]
    return m


def real_date(s):
    try:
        d = datetime.date.fromisoformat(str(s))
    except ValueError:
        return None
    return d if d <= datetime.date.today() else None


def validate(path, manifest):
    man = load_manifest(manifest)
    recs = json.load(open(path))
    errs, warns, seen = [], [], set()
    by_fn = collections.defaultdict(list)

    for i, r in enumerate(recs):
        tag = f"[{i}] {r.get('company','?')}/{r.get('function','?')}"
        co, fn = r.get("company"), r.get("function")
        if co not in man:
            errs.append(f"{tag}: company not in manifest"); continue
        if fn not in man[co]:
            errs.append(f"{tag}: function not in manifest"); continue
        seen.add((co, fn))
        by_fn[(co, fn)].append(r)

        # --- titles_searched: present, nonempty, distinct, manifest-linked -------------
        ts = [str(t).strip() for t in (r.get("titles_searched") or [])]
        live = [t for t in ts if t]
        if len(ts) < 3:
            errs.append(f"{tag}: titles_searched has {len(ts)}, need >=3. This is the evidence v3 lost")
        if len(live) < len(ts):
            errs.append(f"{tag}: titles_searched contains {len(ts)-len(live)} blank entries. "
                        f"Blank strings satisfied the count check and preserved nothing")
        if len({t.lower() for t in live}) < len(live):
            errs.append(f"{tag}: titles_searched has duplicate entries; distinct variants required")
        variants = {v.lower() for v in man[co][fn]}
        need = min(2, len(variants))
        hit = len({t.lower() for t in live} & variants)
        if hit < need:
            errs.append(f"{tag}: titles_searched matches {hit} of the manifest's variants, need {need}. "
                        f"A run may not invent its own search vocabulary")

        v = r.get("verdict")
        if v == "FOUND":
            for f in ("person", "title", "employer", "source_url", "source_type", "tier", "retrieved"):
                if not str(r.get(f, "")).strip():
                    errs.append(f"{tag}: FOUND missing '{f}'")
            if r.get("tier") not in ("A", "B"):
                errs.append(f"{tag}: tier must be A or B (C is not usable)")
            if r.get("retrieved") and not ISO.match(str(r["retrieved"])):
                errs.append(f"{tag}: retrieved must be YYYY-MM-DD")
            elif r.get("retrieved") and not real_date(r["retrieved"]):
                errs.append(f"{tag}: retrieved '{r['retrieved']}' is not a real past calendar date")
            url = str(r.get("source_url", ""))
            if url and not url.startswith("http"):
                errs.append(f"{tag}: source_url not a URL")
            if any(h in url.lower() for h in BAD_HOSTS):
                errs.append(f"{tag}: source_url host is a placeholder ({url})")
            nm = str(r.get("person", "")).strip()
            if nm.lower() in PLACEHOLDER_NAMES:
                errs.append(f"{tag}: person '{nm}' is a placeholder name")
            elif nm and len(nm.split()) < 2:
                errs.append(f"{tag}: person '{nm}' is a single token; need a full name to identify anyone")
        elif v == "NOBODY":
            if len(str(r.get("searched", ""))) < 40:
                errs.append(f"{tag}: NOBODY needs 'searched' (>=40 chars). An absence without a search record is not reportable")
            if len(str(r.get("would_count_as_hit", ""))) < 20:
                errs.append(f"{tag}: NOBODY needs 'would_count_as_hit' (>=20 chars). Unbounded absence claims are how the false-absence incident happened")
        else:
            errs.append(f"{tag}: verdict must be FOUND or NOBODY, got {v!r}")

    # --- duplicates and contradictions -------------------------------------------------
    # Scoped so it CANNOT fire on two different people satisfying one function, which is a
    # legitimate and expected result (Acme Corp/alliances returns two).
    for (co, fn), rs in sorted(by_fn.items()):
        names = [str(r.get("person", "")).strip().lower() for r in rs if r.get("verdict") == "FOUND"]
        dupes = {n for n in names if n and names.count(n) > 1}
        for d in sorted(dupes):
            errs.append(f"{co}/{fn}: person '{d}' recorded more than once for the same function")
        verdicts = {r.get("verdict") for r in rs}
        if "FOUND" in verdicts and "NOBODY" in verdicts:
            errs.append(f"{co}/{fn}: run reports BOTH a FOUND person and NOBODY for the same function")

    # --- entity hazards: cannot be auto-scored, must be adjudicated --------------------
    adjudicate = []
    for (co, fn), rs in sorted(by_fn.items()):
        if co not in ENTITY_HAZARD:
            continue
        for r in rs:
            if r.get("verdict") != "FOUND":
                continue
            if not str(r.get("employer", "")).strip() or not str(r.get("source_url", "")).strip():
                errs.append(f"{co}/{fn}: a hazard-company row needs both 'employer' and 'source_url'")
            adjudicate.append(f"{co}/{fn}: {r.get('person')} -> employer '{r.get('employer')}' "
                              f"({r.get('source_url')})")

    missing = [f"{c}/{f}" for c in man for f in man[c] if (c, f) not in seen]

    print(f"records: {len(recs)} | manifest functions: {sum(len(v) for v in man.values())} | addressed: {len(seen)}")
    if missing:
        print(f"\nUNADDRESSED functions ({len(missing)}):")
        for m in missing[:12]: print(f"  - {m}")
        if len(missing) > 12: print(f"  ... and {len(missing)-12} more")
    if errs:
        print(f"\nSCHEMA ERRORS ({len(errs)}):")
        for e in errs[:25]: print(f"  - {e}")
        if len(errs) > 25: print(f"  ... and {len(errs)-25} more")
    if adjudicate:
        print(f"\nREQUIRES ENTITY ADJUDICATION ({len(adjudicate)}) -- these cannot be scored automatically.")
        for _co, _decoys in ENTITY_HAZARD.items():
            print(f"  {', '.join(_decoys)} are confusable with {_co}.")
        for a in adjudicate: print(f"  - {a}")

    ok = not errs and not missing
    if not ok:
        print("\nRESULT: INVALID - this run cannot be reverified later. Fix before scoring.")
        return 1

    print(f"\nRESULT: VALID - evidence fields preserved and manifest-linked.")
    print(f"  NOTE: this proves the evidence is PRESENT and consistent, not that it is TRUE.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Schema gate for a contact run. Exit 1 if evidence was not preserved.")
    ap.add_argument("run", help="run JSON to validate")
    ap.add_argument("--manifest", required=True, help="CSV manifest: company,url,function,open_question,title_variants")
    a = ap.parse_args()
    sys.exit(validate(a.run, a.manifest))
