"""
lever.py - Lever ATS API parser.

Fetches open roles from Lever public postings API and returns
standardized role dicts.

Endpoint: https://api.lever.co/v0/postings/{slug}?mode=json
Auth: None required (public API)
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone


def fetch_lever(slug: str) -> list[dict]:
    """Fetch all postings from a Lever job board.

    Args:
        slug: Company identifier (e.g. 'leverdemo')

    Returns:
        List of standardized role dicts, or [] on error.
    """
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (compatible; career-scanner/1.0)")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"Lever error for {slug}: HTTP {e.code}", file=sys.stderr)
        return []
    except (urllib.error.URLError, OSError) as e:
        print(f"Lever network error for {slug}: {e}", file=sys.stderr)
        return []

    # Lever returns {"ok": false, "error": "..."} for invalid slugs
    if isinstance(data, dict) and data.get("ok") is False:
        return []

    # Lever returns a list of posting objects
    if not isinstance(data, list):
        return []

    roles = []
    for posting in data:
        categories = posting.get("categories", {}) or {}

        # Convert millisecond timestamp to ISO date string
        created_at = posting.get("createdAt")
        published_at = ""
        if created_at and isinstance(created_at, (int, float)):
            try:
                dt = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
                published_at = dt.strftime("%Y-%m-%d")
            except (ValueError, OSError):
                published_at = ""

        all_locations = str(categories.get("allLocations", "")).lower()

        roles.append({
            "title": posting.get("text", ""),
            "company": slug,
            "department": categories.get("department", "") or "",
            "team": categories.get("team", "") or "",
            "location": categories.get("location", "") or "",
            "remote": "remote" in all_locations,
            "employment_type": categories.get("commitment", "") or "",
            "url": posting.get("hostedUrl", "") or "",
            "apply_url": posting.get("applyUrl", "") or "",
            "published_at": published_at,
            "description_plain": posting.get("descriptionPlain", "") or "",
            "ats": "lever",
        })

    return roles
