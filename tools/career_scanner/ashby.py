"""
ashby.py - Ashby ATS API parser.

Fetches open roles from Ashby public posting API and returns
standardized role dicts.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{slug}
Auth: None required (public API)
"""
import json
import sys
import urllib.error
import urllib.request


def fetch_ashby(slug: str) -> list[dict]:
    """Fetch all jobs from an Ashby job board.

    Args:
        slug: Company identifier (e.g. 'ramp')

    Returns:
        List of standardized role dicts, or [] on error.
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (compatible; career-scanner/1.0)")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"Ashby error for {slug}: HTTP {e.code}", file=sys.stderr)
        return []
    except (urllib.error.URLError, OSError) as e:
        print(f"Ashby network error for {slug}: {e}", file=sys.stderr)
        return []

    if not isinstance(data, dict):
        return []

    roles = []
    for job in data.get("jobs", []):
        roles.append({
            "title": job.get("title", ""),
            "company": slug,
            "department": job.get("department", "") or "",
            "team": job.get("team", "") or "",
            "location": job.get("location", "") or "",
            "remote": bool(job.get("isRemote", False)),
            "employment_type": job.get("employmentType", "") or "",
            "url": job.get("jobUrl", "") or "",
            "apply_url": job.get("applyUrl", "") or "",
            "published_at": job.get("publishedAt", "") or "",
            "description_plain": job.get("descriptionPlain", "") or "",
            "ats": "ashby",
        })

    return roles
