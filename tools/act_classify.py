#!/usr/bin/env python3
"""
act_classify.py — Pre-process /act classification for bucket A/B routing and inbox triage.

Reads:
  data/job-todos.md        — all Pending rows
  inbox/                   — all files except README.md
  output/                  — dossier freshness map (stem == parent folder name)

Computes per-todo:
  blocked       — Notes contains "access blocked" (case-insensitive)
  careers_fresh — Notes contains "Checked YYYY-MM-DD" within last 7 days
  bucket A or B — using exact keyword/URL pattern table from /act SKILL.md

Classifies each inbox item into:
  job_ad | contact_capture | article | company_research | unclassifiable

NO file mutations. Read-only.

Output JSON (stdout):
  {
    "target_date": "YYYY-MM-DD",
    "bucket_a": [...],
    "bucket_b": [...],
    "skipped_fresh_careers": [...],
    "skipped_fresh_dossier": [...],
    "inbox_items": [...],
    "dossier_map": { "slug": {"exists": true, "fresh": true, "last_updated": "..."} }
  }

Usage:
  PYTHONIOENCODING=utf-8 python tools/act_classify.py [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ── ATS URL patterns that signal a job ad ────────────────────────────────────
ATS_PATTERNS = [
    r"greenhouse\.io",
    r"lever\.co",
    r"ashbyhq\.com",
    r"linkedin\.com/jobs/",
    r"myworkdayjobs\.com",
    r"smartrecruiters\.com",
]
ATS_SLUG_EXTRACTORS = [
    # (pattern, group_index) — extracts the company slug from ATS URL path
    (r"greenhouse\.io/([^/\s?]+)", 1),
    (r"lever\.co/([^/\s?]+)", 1),
    (r"ashbyhq\.com/([^/\s?]+)", 1),
]

# ── Editorial/media domains that signal an article ───────────────────────────
MEDIA_DOMAINS = [
    "techcrunch", "forbes", "statnews", "mobihealthnews", "axios",
    "substack", "medium", "bloomberg", "reuters", "wsj", "nytimes",
    "hbr", "fastcompany", "wired", "venturebeat", "fiercehealthcare",
    "healthleadersmedia", "rockhealth", "manualcompoundplanning", "compoundplanning",
]
ARTICLE_PATH_PATTERNS = ["/news/", "/blog/", "/article/", "/post/", "/insights/",
                          "/chapters/", "/stories/", "/editorial/"]

# ── Contact capture context keywords ─────────────────────────────────────────
CONTACT_CONTEXT_WORDS = ["met", "intro", "talk to", "reach out", "connect with",
                          "introduced me to", "referred", "know someone"]

# ── Company research context keywords ────────────────────────────────────────
RESEARCH_CONTEXT_WORDS = ["check out", "research", "look into", "interesting",
                           "target", "run /research-company"]

# ── Bucket A detection patterns ───────────────────────────────────────────────
BUCKET_A_PATTERNS = [
    # (category, match_fn)
    # Tested in order; first match wins.
    # IMPORTANT: article_read must precede company_research so "Read [article]" tasks
    # with a URL don't accidentally match the research pattern.
    (
        "careers_check",
        # Match "Check [Company] careers" / "Check [Company] for [roles]"
        # URL is desirable but not required — skill handles the "no URL" case.
        lambda task, notes: bool(
            re.search(r"\bcheck\b.+\bcareers\b", task, re.IGNORECASE)
        ),
    ),
    (
        "article_read",
        # "Read ..." or "Review [document/article] ..." with a URL in notes
        lambda task, notes: bool(
            re.match(r"^(read|review)\b", task, re.IGNORECASE)
            and _has_url(notes)
        ),
    ),
    (
        "resource_browse",
        # "Browse [platform] ..." with a URL in notes
        lambda task, notes: bool(
            re.match(r"^browse\b", task, re.IGNORECASE)
            and _has_url(notes)
        ),
    ),
    (
        "company_research",
        # "Research [Company]" task, "Deep-dive research [Company]" task,
        # OR notes explicitly say "run /research-company" (not a source
        # attribution like "From /research-company on 2026-02-27").
        # Requires research to appear at the start or be "deep-dive research"
        # to avoid matching "/research-company" mid-task or notes text.
        lambda task, notes: bool(
            re.match(r"^research\s+\S", task, re.IGNORECASE)
            or re.search(r"\bdeep-dive research\b", task, re.IGNORECASE)
            or re.search(r"run\s+`?/research-company`?", notes, re.IGNORECASE)
        ),
    ),
]

# ── Bucket B detection patterns ───────────────────────────────────────────────
BUCKET_B_PATTERNS = [
    (r"^(subscribe|join)\b", "subscription"),
    (r"^(follow|connect with)\b", "social_action"),
    (r"^(visit|attend|text|call)\b", "physical_action"),
    (r"\btuck\b.*(alumni|network|classmate)", "alumni_network"),
    (r"^(learn|study|listen to)\b", "study_learn"),
    (r"^(follow up|send email|reach out|contact)", "outreach"),
    (r"\b(search linkedin|search tuck|search alumni)\b", "alumni_network"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _has_url(text: str) -> bool:
    return bool(re.search(r"https?://\S+", text) or re.search(r"\]\(https?://", text))


def _extract_url(text: str) -> str:
    """Extract the first URL from a Notes string (bare or markdown link)."""
    m = re.search(r"\]\((https?://[^)]+)\)", text)
    if m:
        return m.group(1)
    m = re.search(r"https?://\S+", text)
    return m.group(0).rstrip(").,") if m else ""


def _slug(name: str) -> str:
    """Convert company display name to URL slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _deslug(slug: str) -> str:
    """Convert slug to display name (title case)."""
    return " ".join(w.capitalize() for w in slug.replace("-", " ").split())


# ─────────────────────────────────────────────────────────────────────────────
# Dossier freshness map
# ─────────────────────────────────────────────────────────────────────────────

def build_dossier_map(repo_root: Path, today: date) -> dict:
    """
    Walk output/ and find files where stem == parent folder name.
    These are canonical dossiers (e.g., output/amae-health/amae-health.md).
    Return { slug: {exists, fresh, last_updated} }
    """
    dossier_map = {}
    output_dir = repo_root / "output"
    if not output_dir.exists():
        return dossier_map

    for md_file in output_dir.rglob("*.md"):
        if md_file.stem == md_file.parent.name:
            slug = md_file.parent.name
            last_updated = None
            content = read_file(md_file)
            m = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", content)
            if m:
                try:
                    last_updated = m.group(1)
                    dt = datetime.strptime(last_updated, "%Y-%m-%d").date()
                    fresh = (today - dt).days < 30
                except ValueError:
                    fresh = False
            else:
                fresh = False

            dossier_map[slug] = {
                "exists": True,
                "fresh": fresh,
                "last_updated": last_updated,
            }

    return dossier_map


# ─────────────────────────────────────────────────────────────────────────────
# Todo parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_todos(content: str) -> list[dict]:
    """
    Parse data/job-todos.md and return rows from the Active section.
    Columns: Task | Priority | Due | Status | Notes
    """
    rows = []
    in_active = False

    for line in content.splitlines():
        # Track section
        if re.match(r"^##\s+Active", line, re.IGNORECASE):
            in_active = True
            continue
        if re.match(r"^##\s+", line) and in_active:
            in_active = False
            continue

        if not in_active:
            continue
        if not line.startswith("|") or line.startswith("| Task") or line.startswith("|---") or line.startswith("| ---"):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 4 or cols[0] == "---":
            continue

        rows.append({
            "task":     cols[0] if len(cols) > 0 else "",
            "priority": cols[1] if len(cols) > 1 else "",
            "due":      cols[2] if len(cols) > 2 else "",
            "status":   cols[3] if len(cols) > 3 else "",
            "notes":    cols[4] if len(cols) > 4 else "",
        })

    return rows


def classify_todo(row: dict, today: date, dossier_map: dict) -> tuple[str, dict]:
    """
    Return (bucket, enriched_row) where bucket is one of:
      "a", "b", "skipped_fresh_careers", "skipped_fresh_dossier", "skip"
    """
    task  = row["task"]
    notes = row["notes"]
    status = row["status"]

    # Only process Pending rows
    if status.lower() != "pending":
        return ("skip", row)

    # ── Pre-filter 1: blocked ─────────────────────────────────────────────
    if re.search(r"access blocked", notes, re.IGNORECASE):
        return ("b", {**row, "blocked": True, "type": "previously_blocked",
                      "url": _extract_url(notes)})

    # ── Pre-filter 2: careers check freshness ────────────────────────────
    m = re.search(r"checked\s+(\d{4}-\d{2}-\d{2})", notes, re.IGNORECASE)
    if m:
        try:
            checked_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            if (today - checked_date).days < 7:
                recheck = checked_date + timedelta(days=7)
                return ("skipped_fresh_careers", {
                    **row,
                    "checked_date": m.group(1),
                    "recheck_after": recheck.strftime("%Y-%m-%d"),
                })
        except ValueError:
            pass

    # ── Pre-filter 3: skip if already has fresh dossier ──────────────────
    # Only applies to company_research todos, not careers_check todos
    is_research = (
        re.search(r"\b(research|deep-dive research)\b", task, re.IGNORECASE)
        or "/research-company" in notes.lower()
    )
    if is_research:
        company_slug = _extract_company_slug_from_task(task)
        if company_slug and company_slug in dossier_map and dossier_map[company_slug]["fresh"]:
            return ("skipped_fresh_dossier", {
                **row,
                "company": _deslug(company_slug),
                "dossier_date": dossier_map[company_slug]["last_updated"],
            })

    # ── Bucket A matching ─────────────────────────────────────────────────
    for category, match_fn in BUCKET_A_PATTERNS:
        if match_fn(task, notes):
            return ("a", {**row, "type": category, "url": _extract_url(notes)})

    # ── Bucket B matching ─────────────────────────────────────────────────
    for pattern, btype in BUCKET_B_PATTERNS:
        if re.search(pattern, task, re.IGNORECASE):
            return ("b", {**row, "blocked": False, "type": btype,
                          "url": _extract_url(notes)})

    # ── Default: Bucket B (unclassified manual) ───────────────────────────
    return ("b", {**row, "blocked": False, "type": "other", "url": _extract_url(notes)})


def _extract_company_slug_from_task(task: str) -> str | None:
    """
    Try to extract a company slug from task text like:
    'Research Amae Health' or 'Deep-dive research Ebb Carbon'
    """
    m = re.search(
        r"(?:research|deep-dive research)\s+([A-Z][A-Za-z0-9\s&]+?)(?:\s*—|\s*\(|\s*;|$)",
        task, re.IGNORECASE
    )
    if m:
        return _slug(m.group(1).strip())
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Inbox classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_inbox_file(filename: str, content: str) -> dict:
    """
    Classify a single inbox file. Returns a dict with type and metadata.
    Priority order: job_ad > contact_capture > article > company_research > unclassifiable
    """
    base = {
        "filename": filename,
        "content": content[:500],  # truncate for output
    }

    # ── 1. Job ad: ATS URL match ──────────────────────────────────────────
    for pat in ATS_PATTERNS:
        if re.search(pat, content, re.IGNORECASE):
            slug, display = _extract_ats_company(content)
            url = _extract_url(content)
            return {**base, "type": "job_ad", "company_slug": slug,
                    "company_display": display, "url": url}

    # Generic /careers/ URL with numeric or UUID path
    if re.search(r"/careers/[a-zA-Z0-9_-]{6,}", content):
        url = _extract_url(content)
        return {**base, "type": "job_ad", "company_slug": "", "company_display": "", "url": url}

    # ── 2. Contact capture: full name + context words ────────────────────
    # Two consecutive capitalized words (not stop words)
    name_match = re.search(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\b", content)
    if name_match:
        for kw in CONTACT_CONTEXT_WORDS:
            if kw.lower() in content.lower():
                return {**base, "type": "contact_capture",
                        "name": name_match.group(1)}

    # ── 3. Article: media domain or editorial path ───────────────────────
    urls_in_content = re.findall(r"https?://[^\s)>\"]+", content)
    for url in urls_in_content:
        domain_part = url.split("/")[2].lower().replace("www.", "")
        for media in MEDIA_DOMAINS:
            if media in domain_part:
                return {**base, "type": "article", "url": url}
        for path_pat in ARTICLE_PATH_PATTERNS:
            if path_pat in url.lower():
                return {**base, "type": "article", "url": url}

    # ── 4. Company research: company name + research context + homepage URL
    for kw in RESEARCH_CONTEXT_WORDS:
        if kw.lower() in content.lower():
            return {**base, "type": "company_research", "url": _extract_url(content)}

    # ── 5. Unclassifiable ─────────────────────────────────────────────────
    return {**base, "type": "unclassifiable", "url": _extract_url(content)}


def _extract_ats_company(content: str) -> tuple[str, str]:
    """Try to extract company slug from known ATS URL patterns."""
    for pattern, group in ATS_SLUG_EXTRACTORS:
        m = re.search(pattern, content, re.IGNORECASE)
        if m:
            slug = m.group(group).strip("/")
            return slug, _deslug(slug)
    return "", ""


def build_inbox_items(repo_root: Path) -> list[dict]:
    inbox_dir = repo_root / "inbox"
    if not inbox_dir.exists():
        return []

    items = []
    for f in sorted(inbox_dir.iterdir()):
        if f.name == "README.md" or not f.is_file():
            continue
        # Only process text-like files; skip binary/html with embedded assets dirs
        if f.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".gif"):
            continue
        content = read_file(f)
        if not content:
            continue
        items.append(classify_inbox_file(f.name, content))

    return items


# ─────────────────────────────────────────────────────────────────────────────
# Priority sort key
# ─────────────────────────────────────────────────────────────────────────────

PRIORITY_ORDER = {"high": 0, "med": 1, "medium": 1, "low": 2, "": 3}

def _priority_key(row: dict) -> int:
    return PRIORITY_ORDER.get(row.get("priority", "").lower(), 3)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Classify /act todos and inbox items into bucket A/B. Read-only."
    )
    p.add_argument("--target-date", default=None,
                   help="Date to treat as today (YYYY-MM-DD). Defaults to actual today.")
    p.add_argument("--repo-root", default=None,
                   help="Repository root. Defaults to cwd.")
    return p.parse_args()


def main():
    args = parse_args()

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()

    # ── Dossier freshness map ─────────────────────────────────────────────
    dossier_map = build_dossier_map(repo_root, today)

    # ── Parse todos ───────────────────────────────────────────────────────
    todos_content = read_file(repo_root / "data" / "job-todos.md")
    todos = parse_todos(todos_content)

    bucket_a: list[dict] = []
    bucket_b: list[dict] = []
    skipped_fresh_careers: list[dict] = []
    skipped_fresh_dossier: list[dict] = []

    for row in todos:
        bucket, enriched = classify_todo(row, today, dossier_map)
        if bucket == "a":
            bucket_a.append(enriched)
        elif bucket == "b":
            bucket_b.append(enriched)
        elif bucket == "skipped_fresh_careers":
            skipped_fresh_careers.append(enriched)
        elif bucket == "skipped_fresh_dossier":
            skipped_fresh_dossier.append(enriched)
        # "skip" = Done/Withdrawn/In Progress — omit entirely

    # Sort by priority
    bucket_a.sort(key=_priority_key)
    bucket_b.sort(key=_priority_key)

    # ── Inbox items ───────────────────────────────────────────────────────
    inbox_items = build_inbox_items(repo_root)

    # ── Output ────────────────────────────────────────────────────────────
    result = {
        "target_date": today.strftime("%Y-%m-%d"),
        "bucket_a": bucket_a,
        "bucket_b": bucket_b,
        "skipped_fresh_careers": skipped_fresh_careers,
        "skipped_fresh_dossier": skipped_fresh_dossier,
        "inbox_items": inbox_items,
        "dossier_map": dossier_map,
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
