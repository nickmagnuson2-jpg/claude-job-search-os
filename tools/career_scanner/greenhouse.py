"""
greenhouse.py - Greenhouse ATS API parser.

Fetches open roles from Greenhouse public boards API and returns
standardized role dicts.

Endpoint: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
Auth: None required (public API)
"""
import html
import json
import re
import sys
import urllib.error
import urllib.request


def fetch_greenhouse(slug: str) -> list[dict]:
    """Fetch all jobs from a Greenhouse job board.

    Args:
        slug: Company board token (e.g. 'discord')

    Returns:
        List of standardized role dicts, or [] on error.
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (compatible; career-scanner/1.0)")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"Greenhouse error for {slug}: HTTP {e.code}", file=sys.stderr)
        return []
    except (urllib.error.URLError, OSError) as e:
        print(f"Greenhouse network error for {slug}: {e}", file=sys.stderr)
        return []

    roles = []
    for job in data.get("jobs", []):
        # Strip HTML from content field for plain text scoring
        content_html = job.get("content", "")
        content_plain = re.sub(r"<[^>]+>", " ", html.unescape(content_html))
        content_plain = re.sub(r"\s+", " ", content_plain).strip()

        location_name = job.get("location", {}).get("name", "")
        departments = job.get("departments") or [{}]

        roles.append({
            "title": job.get("title", ""),
            "company": slug,
            "department": departments[0].get("name", ""),
            "team": "",
            "location": location_name,
            "remote": "remote" in location_name.lower(),
            "employment_type": "",
            "url": job.get("absolute_url", ""),
            "apply_url": job.get("absolute_url", "") + "#app",
            "published_at": job.get("first_published", "") or "",
            "description_plain": content_plain,
            "ats": "greenhouse",
        })

    return roles
