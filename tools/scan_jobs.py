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
  {"title", "company", "location", "description", "url", "ad_date", "department", "team"}
  title and company are required; the rest may be "" when the listing does not show them.

Status comes from the score with fixed cutoffs (STATUS_CUTOFFS), calibrated 2026-10-22 on
72 labeled pipeline roles: score >= 7 had 60% precision, 5-6 61%, <= 4 38% (base rate 50%).
`data/calibration/fit-spec.yaml` may override them under `status_cutoffs`. An ad with a
blank description is capped at 'maybe' and tagged `title_only`: a title is not a posting.

Usage:
  ... | PYTHONIOENCODING=utf-8 python3 tools/scan_jobs.py assess [--repo-root .]
  ... | PYTHONIOENCODING=utf-8 python3 tools/scan_jobs.py record [--repo-root .]
"""
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from md_table import JsonArgumentParser, out_error, out_ok, parse_cols, read_file, safe_cell, write_atomic  # noqa: E402
from name_dedup import normalize_name  # noqa: E402
from slug import slugify  # noqa: E402
from tools.career_scanner.company_scorer import location_band  # noqa: E402
from tools.pipeline_history import history, load_history  # noqa: E402
from tools.career_scanner.scanner import geo_ok  # noqa: E402
from tools.career_scanner.scorer import load_scoring_context, score_role  # noqa: E402

CACHE = Path(".claude/skills/scan-jobs/cache.md")
STATUS_CUTOFFS = {"shortlisted": 7, "maybe": 5}
REQUIRED = ("title", "company")
# Bump when the judging LOGIC changes (scorer, gates, status rules). Data-driven rules
# (fit-spec.yaml, its status_cutoffs) are hashed in rules_version directly.
RULES_LOGIC = "2026-11-06"
FIT_SPEC = Path("data/calibration/fit-spec.yaml")
# A row this script wrote: "(scan_jobs.py)" or "(scan_jobs.py rules:<8 hex>)".
_MACHINE_NOTE = re.compile(r"\(scan_jobs\.py(?: rules:([0-9a-f]{8}))?\)")


def _norm(text: str) -> str:
    """slug.py title mode with the hyphens removed: Latin accents folded ('Café' == 'Cafe'),
    letters in any script kept, punctuation and spacing ignored."""
    return slugify(text, drop_punctuation=True).replace("-", "")


def key(title: str, company: str) -> str:
    """Dedup key: normalized title and company, kept apart by a separator, so
    'Senior Engineer / Backend' and 'Senior Engineer/Backend' are the same ad and a
    different title/company split cannot hash to the same key."""
    return f"{_norm(title)}|{_norm(company)}"


def cell_url(url) -> str:
    """A URL as stored in the cache: '|', '(' and ')' percent-encoded, since a literal '|'
    splits the row and a ')' ends the markdown link early. Encoding changes no link, and
    lookups use the same form."""
    return (url or "").strip().replace("|", "%7C").replace("(", "%28").replace(")", "%29")


URL_IN_CELL = re.compile(r"\]\(([^)]+)\)")


def cached_index(cache_text: str) -> tuple[set, set, set]:
    """(keys of all rows, keys of rows with no URL, URLs). A posting URL is a role's
    identity, as in the nightly scanner; title+company is the fallback for URL-less rows."""
    keys, keys_no_url, urls = set(), set(), set()
    for line in cache_text.splitlines():
        if not line.startswith("|") or re.match(r"^\|[-: |]+\|$", line):
            continue
        cols = parse_cols(line)
        if len(cols) >= 2 and cols[0] != "Title":
            k = key(cols[0], cols[1])
            keys.add(k)
            m = URL_IN_CELL.search(cols[5]) if len(cols) > 5 else None
            if m:
                urls.add(m.group(1).strip())
            else:
                keys_no_url.add(k)
    return keys, keys_no_url, urls


def rules_version(root: Path) -> str:
    """The version of the rules a verdict was judged under: 8 hex of the logic version,
    the default cutoffs and fit-spec.yaml's text. A cached verdict from another version
    is re-checked when its ad is seen again (scan-drops-1106, Codex 940.F2 / Grok 941.F4)."""
    spec = read_file(Path(root) / FIT_SPEC) or ""
    basis = "\n".join([RULES_LOGIC, json.dumps(STATUS_CUTOFFS, sort_keys=True), spec])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:8]


def _row_ids(cols: list) -> list:
    """The ids a stored row answers to: its URL, else its title+company key."""
    m = URL_IN_CELL.search(cols[5]) if len(cols) > 5 else None
    return [("u", m.group(1).strip())] if m else [("k", key(cols[0], cols[1]))]


def _ad_ids(ad: dict) -> list:
    """The ids an ad can match, in is_cached's order: its URL, then title+company."""
    url = cell_url(ad.get("url"))
    k = ("k", key(ad["title"], ad["company"]))
    return [("u", url), k] if url else [k]


def machine_verdicts(cache_text: str) -> dict:
    """id -> {status, version} for each row THIS SCRIPT wrote. A row Nick wrote by hand
    carries no marker and is absent: his verdicts are never re-judged."""
    out = {}
    for line in cache_text.splitlines():
        if not line.startswith("|") or re.match(r"^\|[-: |]+\|$", line):
            continue
        cols = parse_cols(line)
        if len(cols) < 7 or cols[0] == "Title":
            continue
        m = _MACHINE_NOTE.search(cols[6])
        if m:
            for i in _row_ids(cols):
                out[i] = {"status": cols[3].strip(), "version": m.group(1) or ""}
    return out


def is_cached(ad: dict, keys: set, keys_no_url: set, urls: set) -> bool:
    """With a URL: that URL is cached, or a URL-less row has this title+company. Without
    one: title+company. So one title at one company in two offices is two roles."""
    k = key(ad["title"], ad["company"])
    url = cell_url(ad.get("url"))
    return (url in urls or k in keys_no_url) if url else k in keys


def index_add(row: dict, keys: set, keys_no_url: set, urls: set) -> None:
    """Add a row to the index the way cached_index reads a stored row, so a later row in
    the same call is judged against it exactly as if it were already in the cache."""
    k = key(row["title"], row["company"])
    keys.add(k)
    url = cell_url(row.get("url"))
    if url:
        urls.add(url)
    else:
        keys_no_url.add(k)


def status_for(score: int, cutoffs: dict) -> str:
    if score >= cutoffs["shortlisted"]:
        return "shortlisted"
    return "maybe" if score >= cutoffs["maybe"] else "skip"


def assess(ads: list, root: Path) -> dict:
    ctx = load_scoring_context(root)
    cutoffs = {**STATUS_CUTOFFS, **((ctx.get("fit_spec") or {}).get("status_cutoffs") or {})}
    cache_text = read_file(root / CACHE)
    keys, keys_no_url, urls = cached_index(cache_text)
    version = rules_version(root)
    verdicts = machine_verdicts(cache_text)
    past = load_history(root)
    cached, new, done, repeats = [], [], set(), []
    rechecked_unchanged = 0
    for ad in ads:
        ids = _ad_ids(ad)
        if ids[0] in done:   # a listing repeated within one scan is assessed once
            cached.append({"title": ad["title"], "company": ad["company"]})
            continue
        done.add(ids[0])
        was = None
        if is_cached(ad, keys, keys_no_url, urls):
            prior = next((verdicts[i] for i in ids if i in verdicts), None)
            if prior is None or prior["version"] == version:
                cached.append({"title": ad["title"], "company": ad["company"]})
                continue
            was = prior["status"]   # judged under other rules: re-check once
        else:
            index_add(ad, keys, keys_no_url, urls)
        role = {f: ad.get(f, "") for f in ("title", "company", "location", "description",
                                           "department", "team")}
        score = score_role(role, ctx)
        # The nightly scanner's own location rule: outside the Bay or on the Peninsula /
        # South Bay fails; an unknown location does not.
        located = geo_ok(ad.get("location", ""))
        status = status_for(score, cutoffs) if located else "skip"
        reason = f"score {score}/10" if located else "location excluded"
        if located and location_band(ad.get("location", "")) == "remote":
            reason += " (remote)"   # a remote seat passes, flagged (Nick, 2026-11-06)
        # A title is not a posting: the same title names opposite jobs (the team doing the
        # work vs the team supporting it). With no description copied, cap at 'maybe' so a
        # title-only ad is never shortlisted or proposed for the pipeline.
        if located and not str(ad.get("description") or "").strip():
            status = "maybe" if status == "shortlisted" else status
            reason += " (title_only)"
        row = {**{f: ad.get(f, "") for f in ("title", "company", "location", "url", "ad_date")},
               "score": score, "status": status, "reason": reason}
        if was is not None:
            if status == was:
                rechecked_unchanged += 1
                cached.append({"title": ad["title"], "company": ad["company"]})
                continue
            row["reason"] = f"{reason} (re-checked under new rules; was {was})"
            row["rechecked"] = True
        # Pipeline history (Nick, 2026-11-06): every role shows, labelled; only an exact
        # repeat of a posting he passed on is held back, onto skipped_repeats.
        h = history(ad, past)
        row["history"] = h["label"]
        if h["declined_repeat"]:
            repeats.append({f: row[f] for f in ("title", "company", "url", "history")})
            continue
        if h["already_in_pipeline"]:
            row["already_in_pipeline"] = True
        if h["possible_match"]:
            row["possible_match"] = h["possible_match"]   # Nick decides, never dropped
        new.append(row)
    # A role already in the pipeline as the same role is not proposed again; a different
    # role at a company with history is, carrying its label.
    propose = [r for r in new if r["status"] == "shortlisted" and not r.get("already_in_pipeline")]
    return {"found": len(ads), "cached": cached, "new": new, "propose_for_pipeline": propose,
            "cutoffs": cutoffs, "rules_version": version,
            "rechecked_unchanged": rechecked_unchanged, "skipped_repeats": repeats}


def record(rows: list, root: Path) -> dict:
    path = root / CACHE
    text = read_file(path)
    if not text:
        out_error(f"cache not found or empty: {path}", "file_not_found")
    keys, keys_no_url, urls = cached_index(text)
    version = rules_version(root)
    lines = text.rstrip("\n").split("\n")
    # Each accepted row joins the index, so the same role twice in ONE call is refused
    # like a role already in the cache (565.F3). A re-checked row REPLACES the machine
    # row it re-judged, in place; one with no such row to replace is refused.
    dupes, replace = [], {}
    for r in rows:
        if r.get("rechecked"):
            at = _machine_line(lines, r)
            if at is None or at in replace:
                dupes.append(r["title"])
            else:
                replace[at] = r
        elif is_cached(r, keys, keys_no_url, urls):
            dupes.append(r["title"])
        else:
            index_add(r, keys, keys_no_url, urls)
    if dupes:
        out_error(f"already in the cache: {dupes}", "duplicate")
    for at, r in replace.items():
        lines[at] = _cache_line(r, version)
    for r in rows:
        if not r.get("rechecked"):
            lines.append(_cache_line(r, version))
    out = re.sub(r"^Last updated: .*$", f"Last updated: {date.today().isoformat()}",
                 "\n".join(lines) + "\n", count=1, flags=re.M)
    write_atomic(path, out)
    return {"appended": len(rows) - len(replace), "replaced": len(replace)}


def _cache_line(r: dict, version: str) -> str:
    url = f"[Listing]({cell_url(r['url'])})" if cell_url(r.get("url")) else "—"
    return (f"| {safe_cell(r['title'])} | {safe_cell(r['company'])} | {r['score']}/10 | "
            f"{r['status']} | {r.get('ad_date') or '—'} | {url} | "
            f"{safe_cell(r['reason'])} (scan_jobs.py rules:{version}) |")


def _machine_line(lines: list, r: dict):
    """Index of the machine-written row this re-checked row re-judged, or None."""
    want = set(_ad_ids(r))
    for i, line in enumerate(lines):
        if not line.startswith("|") or re.match(r"^\|[-: |]+\|$", line):
            continue
        cols = parse_cols(line)
        if len(cols) >= 7 and cols[0] != "Title" and _MACHINE_NOTE.search(cols[6]) \
                and want & set(_row_ids(cols)):
            return i
    return None


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
