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
  PYTHONIOENCODING=utf-8 python3 tools/act_classify.py [--target-date YYYY-MM-DD] [--repo-root PATH]
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

# ── Transactional / automated-notification gmail signals ─────────────────────
# Gmail emails that are pure machine notifications (calendar booking
# confirmations, application receipts, invites) are never routable contacts or
# pipeline adds. They auto-delete as stale rather than queue for approval.
# Origin: 2026-06-02 — cal.com vibe-check confirmation + YC/workatastartup
# application receipt both surfaced in /act as fresh contact_capture/unclassifiable
# even though they carry no routable content. Per memory
# feedback_dont_offer_deferral_for_user_flagged_pain.
TRANSACTIONAL_SENDER_DOMAINS = {
    "cal.com", "calendly.com", "calendar-server.bounces.google.com",
    "workatastartup.com", "ycombinator.com",
}
NOTIFICATION_SUBJECT_RE = re.compile(
    r"(your event has been scheduled"
    r"|has been scheduled"
    r"|your application (?:for .+? )?(?:has been )?(?:received|submitted)"
    r"|application (?:has been )?received"
    r"|^(?:invitation|accepted|declined|updated invitation):"
    r"|you have been invited)",
    re.IGNORECASE,
)

# Pipeline stages that mean Nick is already actively engaged — an intro or
# notification email about such a company duplicates the captured pipeline record.
ENGAGED_PIPELINE_STAGES = {
    "applied", "phone screen", "screen", "interview", "interviewing",
    "onsite", "final", "final round", "offer", "reference", "negotiating",
}

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


def _extract_email_subject(content: str) -> str:
    """Extract the subject line from a gmail-fetched inbox file ('# Email: ...')."""
    m = re.search(r"^#\s*Email:\s*(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _extract_sender_domain(content: str) -> str:
    """Extract the sender's email domain from a gmail-fetched inbox file."""
    m = re.search(r"From:\*{0,2}\s*[^<\n]*<[^@>]+@([a-zA-Z0-9.\-]+)>", content)
    return m.group(1).lower() if m else ""


def load_pipeline_companies(repo_root: Path) -> list[tuple[str, str]]:
    """Return [(company_name, stage_lower)] for each pipeline row. [] on error.

    Used to dedupe gmail notifications about companies already being tracked.
    """
    pipeline = repo_root / "data" / "job-pipeline.md"
    if not pipeline.exists():
        return []
    out = []
    try:
        for line in pipeline.read_text(encoding="utf-8").splitlines():
            if not line.lstrip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            company = cells[0]
            # skip header + separator rows
            if (not company or company.lower() == "company"
                    or set(company) <= set("-: ")):
                continue
            out.append((company, cells[2].lower()))
    except Exception:
        return []
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Dossier freshness map
# ─────────────────────────────────────────────────────────────────────────────

def build_dossier_map(repo_root: Path, today: date) -> dict:
    """
    Walk output/ and find files where stem == parent folder name.
    These are canonical dossiers (e.g., output/acme-ai/acme-ai.md).
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
    'Research Acme AI' or 'Deep-dive research Ebb Carbon'
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

def detect_stale_routed(content: str, repo_root: Path) -> dict | None:
    """
    Detect inbox files whose content has already been routed to a destination.

    Cases handled:
      1. Gmail emails whose sender's email address already appears in data/networking.md
         (means the contact was already logged — re-routing would duplicate)
      2. Gmail emails whose company slug already has a non-terminal pipeline entry
         (means the job_ad was already pipelined — re-routing would duplicate)

    Returns a dict with reason + destination if stale; None otherwise.

    Origin: 2026-05-14 — a contact/company case. Inbox file from 5/12 was processed
    via /networking-write + /draft-email, but the inbox file was never deleted; /act
    re-classified it as a fresh contact_capture and queued a duplicate write.
    Per memory feedback_search_destination_folder_first.
    """
    is_gmail = 'source="gmail"' in content
    subject = _extract_email_subject(content) if is_gmail else ""

    # ── Transactional / automated notification (gmail only) ─────────────────
    # Calendar booking confirmations, application receipts, invites — never
    # routable. Auto-delete instead of queueing for approval.
    if is_gmail:
        sender_domain = _extract_sender_domain(content)
        domain_hit = any(
            sender_domain == d or sender_domain.endswith("." + d)
            for d in TRANSACTIONAL_SENDER_DOMAINS
        )
        if domain_hit or NOTIFICATION_SUBJECT_RE.search(subject):
            return {
                "reason": "transactional_notification",
                "detail": sender_domain or (subject[:50] or "automated notification"),
                "destination": "delete (transactional notification — not routable)",
            }

    # ── Gmail sender check ──────────────────────────────────────────────────
    sender_match = re.search(
        r"From:\*{0,2}\s*[^<\n]*<([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+)>",
        content,
    )
    if sender_match:
        sender_email = sender_match.group(1).lower()
        networking = repo_root / "data" / "networking.md"
        if networking.exists():
            try:
                net_content = networking.read_text(encoding="utf-8").lower()
                if sender_email in net_content:
                    return {
                        "reason": "sender_already_in_networking",
                        "detail": sender_email,
                        "destination": "data/networking.md",
                    }
            except Exception:
                pass

    # ── ATS company slug pipeline check (single-listing files only) ──────────
    # Skip newsletter-style files with multiple ATS URLs — those need manual
    # triage, not auto-staleness off a single matching company.
    ats_url_count = 0
    for pat in ATS_PATTERNS:
        ats_url_count += len(re.findall(pat, content, re.IGNORECASE))

    if ats_url_count == 1:
        for pattern, group_idx in ATS_SLUG_EXTRACTORS:
            m = re.search(pattern, content, re.IGNORECASE)
            if m:
                slug = m.group(group_idx).strip("/").lower()
                pipeline = repo_root / "data" / "job-pipeline.md"
                if pipeline.exists() and len(slug) >= 3:
                    try:
                        pipe_content = pipeline.read_text(encoding="utf-8").lower()
                        display = _deslug(slug).lower()
                        if slug in pipe_content or display in pipe_content:
                            return {
                                "reason": "company_already_in_pipeline",
                                "detail": slug,
                                "destination": "data/job-pipeline.md",
                            }
                    except Exception:
                        pass
                break

    # ── Gmail about an already-engaged pipeline company (duplicate intro) ────
    # An intro / coordination email about a company already at an engaged stage
    # (Phone Screen, Applied, Interview, Offer …) duplicates the captured
    # pipeline record. Guarded against false positives: the company name must be
    # distinctive (multi-word OR >=6 chars), appear as a whole phrase in the
    # subject, AND the pipeline stage must be "engaged" (not Researching/To Apply
    # — those could be genuinely new actionable contacts).
    if is_gmail and subject:
        for company, stage in load_pipeline_companies(repo_root):
            cname = company.strip()
            if " " not in cname and len(cname) < 6:
                continue  # too short / not distinctive
            if stage not in ENGAGED_PIPELINE_STAGES:
                continue  # only dedupe once actively engaged
            if re.search(r"\b" + re.escape(cname) + r"\b", subject, re.IGNORECASE):
                return {
                    "reason": "company_already_in_pipeline",
                    "detail": f"{cname} (pipeline stage: {stage})",
                    "destination": "data/job-pipeline.md",
                }

    return None


# Known launchd-generated nudge kinds — older copies are superseded by newer ones
_NUDGE_KINDS = {
    "dossier-freshness-alert",
    "follow-up-nudge",
    "weekly-review-reminder",
}
_NUDGE_FILENAME_RE = re.compile(r"^(\d{8})-(.+)\.md$")


def detect_stale_superseded(filename: str, all_filenames: list[str]) -> dict | None:
    """
    Detect inbox files that are older copies of a daily launchd nudge pattern.

    Only the most recent file per `kind` is canonical; older copies are noise
    (same 3 contacts, same 41 stale dossiers, etc.) and should be cleared.

    Returns dict with reason + newest_date if stale; None otherwise.
    """
    m = _NUDGE_FILENAME_RE.match(filename)
    if not m:
        return None
    date_part, kind_part = m.group(1), m.group(2)
    if kind_part not in _NUDGE_KINDS:
        return None

    # Find all files of the same kind
    same_kind_dates = []
    pat = re.compile(rf"^(\d{{8}})-{re.escape(kind_part)}\.md$")
    for fn in all_filenames:
        km = pat.match(fn)
        if km:
            same_kind_dates.append(km.group(1))

    if not same_kind_dates:
        return None
    newest = max(same_kind_dates)
    if date_part < newest:
        return {
            "reason": "superseded_by_newer_nudge",
            "detail": f"{kind_part} (newest: {newest})",
            "destination": "delete (system-generated nudge)",
        }
    return None


def classify_inbox_file(filename: str, content: str, repo_root: Path | None = None,
                        all_filenames: list[str] | None = None) -> dict:
    """
    Classify a single inbox file. Returns a dict with type and metadata.
    Priority order: stale > job_ad > contact_capture > article > company_research > unclassifiable

    Stale detection (when repo_root provided):
      - Gmail sender already in networking.md → stale=True
      - ATS company slug already in pipeline.md → stale=True
      - Old daily-nudge filename superseded by newer copy → stale=True

    Stale items keep their primary `type` (for diagnostic display) but add:
      stale: true
      stale_reason: <code>
      stale_detail: <human-readable>
      stale_destination: <path or action>

    /act consumes the stale flag to auto-delete the inbox file instead of queueing
    for routing.

    Gmail-sourced files (containing source="gmail" in XML delimiter) are tagged
    with source_type="gmail". Non-stale Gmail items require explicit confirmation
    before any write.
    """
    is_gmail = 'source="gmail"' in content

    base = {
        "filename": filename,
        "content": content[:500],  # truncate for output
    }
    if is_gmail:
        base["source_type"] = "gmail"

    # ── Stale detection (cross-check destinations + supersedence) ───────────
    stale_info = None
    if repo_root is not None:
        stale_info = detect_stale_routed(content, repo_root)
    if stale_info is None and all_filenames is not None:
        stale_info = detect_stale_superseded(filename, all_filenames)
    if stale_info is not None:
        base["stale"] = True
        base["stale_reason"] = stale_info["reason"]
        base["stale_detail"] = stale_info["detail"]
        base["stale_destination"] = stale_info["destination"]

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

    # First pass: collect text-like filenames (needed for supersedence check)
    candidates = []
    for f in sorted(inbox_dir.iterdir()):
        if f.name == "README.md" or not f.is_file():
            continue
        if f.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".gif"):
            continue
        candidates.append(f)

    all_filenames = [f.name for f in candidates]

    items = []
    for f in candidates:
        content = read_file(f)
        if not content:
            continue
        items.append(classify_inbox_file(f.name, content,
                                         repo_root=repo_root,
                                         all_filenames=all_filenames))

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
