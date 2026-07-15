#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contact_finder.py - deterministic contact discovery via Exa search.

Retires the Selenium LinkedIn scraper (tools/linkedin-scanner/). Given a company,
fan out a few Exa /search calls restricted to linkedin.com, keep only real
/in/ profile pages, extract each person's name from the RESULT TITLE ONLY
(never from body text), canonicalize + dedup URLs, and emit UNRANKED candidate
JSON. All verification (current-vs-past employment, quote-or-drop) and ranking
happen SESSION-SIDE in the /scan-contacts skill - this tool does no LLM work and
needs no ANTHROPIC_API_KEY.

Design rules (from the 2026-07-15 architecture review):
  - Names come ONLY from the profile title/OG-title, cross-checked against the
    URL slug. Unparseable -> dropped, never surfaced as "LinkedIn Member".
  - People + URLs come ONLY from real Exa results. This tool never invents a
    person. (CLAUDE.md never-fabricate.)
  - Warm-tie / role tokens are loaded at RUNTIME from data/profile.md +
    data/goals.md (gitignored). Nothing about a real person is hardcoded here
    (public-repo PII gate).
  - Honest exit reasons + separate counts so "throttled" or "all-filtered" never
    masquerades as "nobody works here".

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/contact_finder.py --company "Acme AI" [--num 20]

Exit codes: 0 ok (even for no_results/all_filtered), 1 config/HTTP error
(JSON error body still printed).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Reuse exa_search.py's dotenv loader instead of adding a third one (CLAUDE.md
# one-source-of-truth). contact_finder.py lives in tools/, so exa_search is an
# importable sibling; it has no import-time side effects.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from exa_search import _load_dotenv  # noqa: E402

EXA_ENDPOINT = "https://api.exa.ai/search"
REPO_ROOT = Path(__file__).resolve().parent.parent

# Generic seniority/leadership tokens - NOT PII, safe to hardcode. Used to bias
# the discovery query toward hiring-decision people.
LEADERSHIP_TOKENS = "founder CEO co-founder president COO head of VP director lead"


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested in tests/scripts/test_contact_finder.py)
# --------------------------------------------------------------------------- #
def is_profile_url(url: str) -> bool:
    """True only for LinkedIn member profile pages (/in/...). Posts, company,
    and school pages are not people we can email."""
    if not url:
        return False
    u = url.lower()
    return "linkedin.com/in/" in u


def canonicalize_url(url: str) -> str:
    """Canonical form for dedup: lowercase host, drop country subdomain, strip
    query/fragment/trailing slash, normalize to the /in/<slug> profile root."""
    if not url:
        return ""
    u = url.strip()
    u = re.sub(r"#.*$", "", u)
    u = re.sub(r"\?.*$", "", u)
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = u.lower()
    # Drop any country/locale subdomain: xx.linkedin.com -> linkedin.com
    u = re.sub(r"^[a-z0-9-]+\.linkedin\.com", "linkedin.com", u)
    u = re.sub(r"^www\.linkedin\.com", "linkedin.com", u)
    m = re.search(r"linkedin\.com/in/([^/]+)", u)
    if m:
        return f"linkedin.com/in/{m.group(1).rstrip('/')}"
    return u.rstrip("/")


def slug_from_url(url: str) -> str:
    """The /in/<slug> slug, minus the trailing numeric/hash id LinkedIn appends."""
    m = re.search(r"linkedin\.com/in/([^/?#]+)", (url or "").lower())
    if not m:
        return ""
    slug = m.group(1)
    # Trim trailing "-<hexish id>" segment (e.g. jane-doe-26920336).
    parts = slug.split("-")
    while parts and re.fullmatch(r"[0-9a-f]{4,}", parts[-1]):
        parts.pop()
    return " ".join(parts)


_NON_NAME = re.compile(r"[^\w .'-]", re.UNICODE)


def extract_name(title: str, url: str) -> str | None:
    """Derive a person's display name from the Exa result TITLE ONLY, validated
    against the URL slug. Returns None (=> caller drops the candidate) when the
    title has no trustworthy name. Never reads body text (avoids 'People also
    viewed' contamination)."""
    if not title:
        return None
    t = title.strip()
    # Strip common LinkedIn title suffixes.
    t = re.sub(r"\s*[|\-–—]\s*LinkedIn\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+on LinkedIn.*$", "", t, flags=re.I)
    t = re.sub(r"^Post by\s+", "", t, flags=re.I)
    # Name is the segment before the first " - " / " | " / " , " headline break.
    t = re.split(r"\s+[|–—]\s+|\s-\s|,", t)[0].strip()
    t = _NON_NAME.sub("", t).strip()
    if not t:
        return None
    # Normalize ALL-CAPS to Title Case (LinkedIn shouty names).
    if t.isupper():
        t = t.title()
    words = [w for w in t.split() if w]
    # A plausible personal name: 2-4 tokens, not obviously a placeholder.
    if not (2 <= len(words) <= 4):
        return None
    if t.lower() in ("linkedin member", "linkedin user"):
        return None
    # Cross-check: at least one name token must appear in the URL slug.
    slug = slug_from_url(url)
    if slug:
        slug_tokens = set(slug.lower().split())
        name_tokens = {re.sub(r"[^\w]", "", w.lower()) for w in words}
        name_tokens = {w for w in name_tokens if len(w) > 1}
        if name_tokens and not (name_tokens & slug_tokens):
            return None
    return " ".join(words)


def parse_reach(text: str) -> str:
    """Pull public reach ('500 connections', '2,473 followers') from body text.
    A weak public proxy only - NOT the user's private degree/mutuals."""
    if not text:
        return ""
    bits = []
    for m in re.finditer(r"([\d][\d,]*)\+?\s+(connections?|followers?)", text, re.I):
        bits.append(f"{m.group(1)} {m.group(2).lower()}")
    # Dedup preserving order.
    seen, out = set(), []
    for b in bits:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return ", ".join(out[:2])


def company_mentioned(text: str, company: str) -> bool:
    """Deterministic pre-filter: does the bio mention the company in a WORK
    context ('at Acme', '@ Acme', 'Acme (Current)', 'Acme employs', 'joined
    Acme')? A bare token match is too weak - for an ambiguous name like 'Sydney'
    it fires on people merely *named* Sydney or on the unrelated 'Sydney
    Biosciences'. Non-work mentions route to needs_manual_check (not dropped);
    the session gate makes the final current-vs-past, right-company call."""
    if not text or not company:
        return False
    low = text.lower()
    # Test the full company plus its longest significant token (so "at Acme"
    # still matches when company == "Acme AI").
    names = {company.strip().lower()}
    toks = [w for w in re.split(r"[^\w]+", company.lower()) if len(w) > 2]
    if toks:
        names.add(max(toks, key=len))
    for nm in names:
        c = re.escape(nm)
        patterns = [
            rf"\b(?:at|with|joined?|join|@)\s+{c}\b",
            rf"\b{c}\s*\(current\)",
            rf"\b{c}\s+(?:employs|is a|helps|headquartered)",
        ]
        if any(re.search(p, low) for p in patterns):
            return True
    return False


def load_role_tokens(goals_path: Path) -> str:
    """Extract query tokens for the top-ranked target role from goals.md.
    Runtime read (goals.md is gitignored). Empty string on any failure."""
    try:
        lines = goals_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    in_roles = False
    for ln in lines:
        if ln.strip().startswith("**Role types**"):
            in_roles = True
            continue
        if in_roles and re.match(r"^\s*1\.\s+", ln):
            role = re.sub(r"^\s*1\.\s+", "", ln)
            role = re.split(r"\bat an?\b|\(", role)[0]  # drop "at an AI-native..." / parenthetical
            role = role.replace("/", " ").strip()
            return re.sub(r"\s+", " ", role)
    return ""


def load_affinity_tokens(profile_path: Path) -> list:
    """Extract the user's schools + past employers from profile.md at RUNTIME so
    an affinity fan-out query can surface warm-tie contacts. Never hardcoded
    (public-repo PII gate). Returns [] on failure."""
    try:
        text = profile_path.read_text(encoding="utf-8")
    except OSError:
        return []
    # Scope to the Career Background section so we don't scoop up header/comp
    # field labels (E-Mail, Salary, Flexibility, Equity, English...).
    m = re.search(r"###\s*Career Background(.*?)(?:\n###\s|\Z)", text, re.S | re.I)
    section = m.group(1) if m else text
    # Non-org bold labels that can appear even inside the section.
    STOP = {"education", "name", "title", "e-mail", "email", "mobile", "address",
            "linkedin", "salary", "flexibility", "equity", "english", "summary",
            "availability", "compensation", "employment", "status", "location",
            "type", "work model", "start date", "languages", "interests"}
    toks = []
    # Bolded employer labels: **Globex & Co:** -> Globex.
    for mm in re.finditer(r"\*\*([A-Z][\w&.'\- ]+?):\*\*", section):
        label = mm.group(1).strip()
        if label.lower() in STOP:
            continue
        first = label.split("&")[0].split(",")[0].strip().split()
        if first and len(first[0]) > 2:
            toks.append(first[0])
    # Education institutions from the Education line (anywhere in the profile).
    EDU_STOP = {"mba", "bachelor", "bachelors", "public", "policy", "school",
                "business", "university", "college", "science", "arts", "master",
                "education"}
    for ln in text.splitlines():
        if "education" in ln.lower() and ("mba" in ln.lower() or "bachelor" in ln.lower()):
            for mm in re.finditer(r"\b([A-Z][a-zA-Z]{3,})\b", ln):
                w = mm.group(1)
                if w.lower() not in EDU_STOP:
                    toks.append(w)
    # Dedup preserving order, cap to keep the query focused.
    seen, out = set(), []
    for t in toks:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:6]


def build_queries(company: str, role_tokens: str, affinity_tokens: list) -> list:
    """A small fan-out for recall against Exa's incomplete LinkedIn index:
    leadership, role-shape, and warm-tie affinity. Always company-anchored."""
    q = [f"{company} {LEADERSHIP_TOKENS}"]
    if role_tokens:
        q.append(f"{company} {role_tokens}")
    if affinity_tokens:
        q.append(f"{company} people who worked at or studied at " + " ".join(affinity_tokens))
    return q


def normalize_result(raw: dict, company: str) -> dict | None:
    """Turn one Exa result into a candidate record, or None to drop it.
    Returns a dict with a 'bucket' of 'candidate' or 'needs_check'."""
    url = raw.get("url") or ""
    if not is_profile_url(url):
        return None
    name = extract_name(raw.get("title") or "", url)
    if not name:
        return None  # unparseable name -> hard drop (counted as dropped_no_name)
    bio = (raw.get("text") or raw.get("snippet") or "").strip().replace("\n", " ")
    title = (raw.get("title") or "").strip()
    headline = re.sub(r"\s*[|\-–—]\s*LinkedIn\s*$", "", title, flags=re.I)
    headline = headline[len(name):].lstrip(" -|–—,") if headline.lower().startswith(name.lower()) else headline
    bucket = "candidate" if company_mentioned(bio, company) else "needs_check"
    return {
        "name": name,
        "url": f"https://www.{canonicalize_url(url)}",
        "canonical_url": canonicalize_url(url),
        "headline": headline.strip(),
        "reach": parse_reach(bio),
        "bio_text": bio[:1200],
        "retrieved_snippet": bio[:400],
        "source_url": url,
        "bucket": bucket,
    }


# --------------------------------------------------------------------------- #
# Exa I/O (live-tested, not unit-tested)
# --------------------------------------------------------------------------- #
def exa_search(query: str, api_key: str, num: int, timeout: int = 45):
    """POST Exa /search restricted to linkedin.com. Returns (results, error_str).
    Retries once on HTTP 429 with backoff."""
    body = {
        "query": query,
        "type": "auto",
        "numResults": max(1, min(num, 25)),
        "includeDomains": ["linkedin.com"],
        "contents": {"text": {"maxCharacters": 1000}},
    }
    data = json.dumps(body).encode("utf-8")
    for attempt in range(2):
        req = urllib.request.Request(
            EXA_ENDPOINT, data=data,
            headers={"x-api-key": api_key, "Content-Type": "application/json",
                     "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload.get("results", []), None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(2.0)
                continue
            detail = ""
            try:
                detail = e.read().decode("utf-8")[:200]
            except Exception:
                pass
            return [], f"HTTP {e.code}: {detail or e.reason}"
        except urllib.error.URLError as e:
            return [], f"network error: {e.reason}"
        except Exception as e:  # noqa: BLE001
            return [], f"unexpected error: {e}"
    return [], "throttled"


def _profile_guard(repo_root: Path) -> str | None:
    """Return an error string if profile.md/goals.md are missing or placeholder."""
    for fname in ("profile.md", "goals.md"):
        p = repo_root / "data" / fname
        if not p.is_file():
            return f"data/{fname} missing - run /import-cv (profile) or fill goals.md"
        body = p.read_text(encoding="utf-8")
        # Strip headers/comments; require real content beyond TODO scaffolding.
        stripped = re.sub(r"(?im)^\s*#.*$|<!--.*?-->", "", body)
        if len(stripped.strip()) < 80 or stripped.upper().count("TODO") > stripped.count("\n"):
            return f"data/{fname} looks empty/placeholder - fill it before scanning contacts"
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Find contacts at a company via Exa (no LinkedIn login).")
    ap.add_argument("--company", "-c", required=True, help="Company name to scan")
    ap.add_argument("--num", "-n", type=int, default=20, help="Approx profiles to return (default 20)")
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    args = ap.parse_args()

    repo_root = Path(args.repo_root)
    _load_dotenv()
    api_key = os.environ.get("EXA_API_KEY", "").strip()
    if not api_key:
        print(json.dumps({"company": args.company, "candidates": [], "error": "EXA_API_KEY not set in .env"}))
        sys.exit(1)

    guard_err = _profile_guard(repo_root)
    if guard_err:
        print(json.dumps({"company": args.company, "candidates": [], "error": guard_err}))
        sys.exit(1)

    role_tokens = load_role_tokens(repo_root / "data" / "goals.md")
    affinity_tokens = load_affinity_tokens(repo_root / "data" / "profile.md")
    queries = build_queries(args.company, role_tokens, affinity_tokens)

    per_q = max(6, args.num)  # over-fetch; dedup shrinks it
    raw_all, errors = [], []
    for q in queries:
        results, err = exa_search(q, api_key, per_q)
        if err:
            errors.append(f"[{q[:40]}...] {err}")
        raw_all.extend(results)

    # Normalize + bucket + dedup by canonical URL (keep the richer bio on collision).
    by_url = {}
    dropped_no_name = 0
    non_profile = 0
    for raw in raw_all:
        if not is_profile_url(raw.get("url") or ""):
            non_profile += 1
            continue
        rec = normalize_result(raw, args.company)
        if rec is None:
            dropped_no_name += 1
            continue
        key = rec["canonical_url"]
        if key not in by_url or len(rec["bio_text"]) > len(by_url[key]["bio_text"]):
            by_url[key] = rec

    candidates = [r for r in by_url.values() if r["bucket"] == "candidate"]
    needs_check = [r for r in by_url.values() if r["bucket"] == "needs_check"]
    candidates.sort(key=lambda r: len(r["bio_text"]), reverse=True)
    needs_check.sort(key=lambda r: len(r["bio_text"]), reverse=True)

    # Honest exit reason.
    if errors and not by_url:
        exit_reason = "exa_error"
    elif not raw_all:
        exit_reason = "no_results"
    elif not by_url:
        exit_reason = "all_filtered"
    else:
        exit_reason = "ok"

    print(json.dumps({
        "company": args.company,
        "queries_run": queries,
        "exit_reason": exit_reason,
        "counts": {
            "raw_results": len(raw_all),
            "profile_results": len(raw_all) - non_profile if raw_all else 0,
            "candidates_kept": len(candidates),
            "needs_manual_check": len(needs_check),
            "dropped_no_name": dropped_no_name,
        },
        "exa_errors": errors or None,
        "ranked": False,  # ranking is session-side in the /scan-contacts skill
        "candidates": candidates,
        "needs_check": needs_check,
        "error": None,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
