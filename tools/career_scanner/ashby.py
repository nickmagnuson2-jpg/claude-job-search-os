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


def _fail(errors: list | None, reason: str) -> None:
    """Record a fetch failure on the out-parameter. No-op when the caller passed none."""
    if errors is not None:
        errors.append({"reason": reason})


def fetch_ashby(slug: str, errors: list | None = None) -> list[dict]:
    """Fetch all jobs from an Ashby job board.

    Args:
        slug: Company identifier (e.g. 'ramp')
        errors: Optional list. Appended with {"reason": ...} on ANY failure path.
            THE FALSE-ZERO CHANNEL (2026-09-02): this parser catches its own HTTP and
            network errors and returns [], so without this out-parameter a dead slug
            returning 404 is indistinguishable from a live board with no openings, and
            a scan in which every board failed reports a clean zero.

    Returns:
        List of standardized role dicts, or [] on error.
    """
    return _fetch_board(slug, errors, include_compensation=False)


def fetch_ashby_with_compensation(slug: str, errors: list | None = None) -> list[dict]:
    """Fetch an Ashby board WITH pay: same as fetch_ashby, plus two keys per role.

    Adds ``id`` (the posting id) and ``compensation_summary`` (the posting's
    ``compensationTierSummary``; "" when the posting publishes no pay). The board is
    requested with ``includeCompensation=true``.

    A separate function, not a flag on fetch_ashby, because every parser shares one
    signature and one ROLE_KEYS contract (tests/scripts/test_career_scanner_filters.py,
    tests/scripts/test_parsers.py). The nightly scan keeps calling fetch_ashby.

    Added 2026-11-18: in the session history, 103 of 147 hand-rolled Ashby board
    fetches asked for compensation, which fetch_ashby did not return, so they went
    around the project parser and lost its error channel.
    """
    return _fetch_board(slug, errors, include_compensation=True)


def _fetch_board(slug: str, errors: list | None, include_compensation: bool) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    if include_compensation:
        url += "?includeCompensation=true"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (compatible; career-scanner/1.0)")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"Ashby error for {slug}: HTTP {e.code}", file=sys.stderr)
        _fail(errors, f"HTTP {e.code}")
        return []
    except (urllib.error.URLError, OSError) as e:
        print(f"Ashby network error for {slug}: {e}", file=sys.stderr)
        _fail(errors, f"{type(e).__name__}: {e}")
        return []

    if not isinstance(data, dict):
        _fail(errors, f"payload is {type(data).__name__}, expected a JSON object")
        return []

    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        _fail(errors, "payload has no 'jobs' list")
        return []

    roles = []
    for job in jobs:
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
        if include_compensation:
            comp = job.get("compensation")
            summary = comp.get("compensationTierSummary") if isinstance(comp, dict) else ""
            roles[-1]["id"] = job.get("id", "") or ""
            roles[-1]["compensation_summary"] = summary or ""

    return roles
