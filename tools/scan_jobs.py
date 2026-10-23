#!/usr/bin/env python3
"""scan_jobs.py — the deterministic half of /scan-jobs.

The model's only job in /scan-jobs is to fetch listings and copy each one into the ad
schema below. Everything after that is here, so the same ads always get the same verdict:

  assess   stdin: JSON array of ads. Dedupes against the cache, applies the hard location
           gate, scores with the career scanner's scorer, assigns a status, and lists
           shortlisted companies not yet in the pipeline. Writes nothing.
  record   stdin: the `new` array from `assess`. Appends those rows to the cache and
           stamps "Last updated". Refuses an ad already in the cache.

Ad schema (one object per listing; copy text verbatim, do not summarize):
  {"title", "company", "location", "description", "url", "ad_date"}

Status comes from the score with fixed cutoffs (STATUS_CUTOFFS), calibrated 2026-10-22 on
72 labeled pipeline roles: score >= 7 had 60% precision, 5-6 61%, <= 4 38% (base rate 50%).
`data/calibration/fit-spec.yaml` may override them under `status_cutoffs`.

Usage:
  ... | PYTHONIOENCODING=utf-8 python3 tools/scan_jobs.py assess [--repo-root .]
  ... | PYTHONIOENCODING=utf-8 python3 tools/scan_jobs.py record [--repo-root .]
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from md_table import JsonArgumentParser, out_error, out_ok, parse_cols, read_file, safe_cell, write_atomic  # noqa: E402
from tools.career_scanner.company_scorer import geo_gate  # noqa: E402
from tools.career_scanner.scorer import load_scoring_context, score_role  # noqa: E402

CACHE = Path(".claude/skills/scan-jobs/cache.md")
PIPELINE = Path("data/job-pipeline.md")
STATUS_CUTOFFS = {"shortlisted": 7, "maybe": 5}
REQUIRED = ("title", "company")


def key(title: str, company: str) -> str:
    """Dedup key: title + company, lowercased, every non-alphanumeric run removed, so
    'Senior Engineer / Backend' and 'Senior Engineer/Backend' are the same ad."""
    return re.sub(r"[^a-z0-9]+", "", f"{title}|{company}".lower())


def cached_keys(cache_text: str) -> set:
    keys = set()
    for line in cache_text.splitlines():
        if not line.startswith("|") or re.match(r"^\|[-: |]+\|$", line):
            continue
        cols = parse_cols(line)
        if len(cols) >= 2 and cols[0] != "Title":
            keys.add(key(cols[0], cols[1]))
    return keys


def status_for(score: int, cutoffs: dict) -> str:
    if score >= cutoffs["shortlisted"]:
        return "shortlisted"
    return "maybe" if score >= cutoffs["maybe"] else "skip"


def pipeline_companies(text: str) -> list:
    out = []
    for line in text.splitlines():
        if line.startswith("|") and not re.match(r"^\|[-: |]+\|$", line):
            cols = parse_cols(line)
            if cols and cols[0] not in ("Company", "---"):
                out.append(cols[0].lower())
    return out


def assess(ads: list, root: Path) -> dict:
    ctx = load_scoring_context(root)
    cutoffs = {**STATUS_CUTOFFS, **((ctx.get("fit_spec") or {}).get("status_cutoffs") or {})}
    seen = cached_keys(read_file(root / CACHE))
    tracked = pipeline_companies(read_file(root / PIPELINE))
    cached, new = [], []
    for ad in ads:
        k = key(ad["title"], ad["company"])
        if k in seen:
            cached.append({"title": ad["title"], "company": ad["company"]})
            continue
        seen.add(k)  # a listing repeated within one scan is assessed once
        role = {"title": ad["title"], "company": ad["company"],
                "location": ad.get("location", ""), "description": ad.get("description", "")}
        score = score_role(role, ctx)
        gate = geo_gate(ad.get("location", ""))
        status = "skip" if gate["excluded"] else status_for(score, cutoffs)
        new.append({**{f: ad.get(f, "") for f in ("title", "company", "location", "url", "ad_date")},
                    "score": score, "status": status,
                    "reason": "location excluded" if gate["excluded"] else f"score {score}/10"})
    propose = [r for r in new if r["status"] == "shortlisted"
               and not any(r["company"].lower() in c or c in r["company"].lower() for c in tracked)]
    return {"found": len(ads), "cached": cached, "new": new, "propose_for_pipeline": propose,
            "cutoffs": cutoffs}


def record(rows: list, root: Path) -> dict:
    path = root / CACHE
    text = read_file(path)
    if not text:
        out_error(f"cache not found or empty: {path}", "file_not_found")
    seen = cached_keys(text)
    dupes = [r["title"] for r in rows if key(r["title"], r["company"]) in seen]
    if dupes:
        out_error(f"already in the cache: {dupes}", "duplicate")
    lines = text.rstrip("\n").split("\n")
    for r in rows:
        url = f"[Listing]({r['url']})" if r.get("url") else "—"
        lines.append(f"| {safe_cell(r['title'])} | {safe_cell(r['company'])} | {r['score']}/10 | "
                     f"{r['status']} | {r.get('ad_date') or '—'} | {url} | "
                     f"{safe_cell(r['reason'])} (scan_jobs.py) |")
    out = re.sub(r"^Last updated: .*$", f"Last updated: {date.today().isoformat()}",
                 "\n".join(lines) + "\n", count=1, flags=re.M)
    write_atomic(path, out)
    return {"appended": len(rows)}


def main(argv=None) -> int:
    ap = JsonArgumentParser(prog="scan_jobs.py", description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["assess", "record"])
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        out_error(f"stdin is not valid JSON: {e}", "bad_json")
    if not isinstance(data, list) or not all(isinstance(a, dict) for a in data):
        out_error("expected a JSON array of objects", "not_a_list")
    missing = [i for i, a in enumerate(data) if any(not str(a.get(f) or "").strip() for f in REQUIRED)]
    if missing:
        out_error(f"ads at {missing[:10]} lack a title or company", "missing_fields")
    if args.command == "assess":
        out_ok("assess", f"{len(data)} ad(s)", **assess(data, root))
    else:
        out_ok("record", f"{len(data)} row(s)", **record(data, root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
