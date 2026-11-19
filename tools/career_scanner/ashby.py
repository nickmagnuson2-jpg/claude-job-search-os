"""
ashby.py - Ashby ATS API parser.

Fetches open roles from Ashby public posting API and returns
standardized role dicts.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{slug}
Auth: None required (public API)
"""
import http.client
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from tools.career_scanner.url_segment import quote_segment



HOSTED = "https://jobs.ashbyhq.com/"
_APP_DATA = re.compile(r"window\.__appData\s*=\s*")


def _hosted_board(slug: str) -> tuple[str, object]:
    """What the hosted board page says about slug, after the posting API answered 404.

    Returns one of:
      ("live", n)        the page's __appData names this slug; n postings (n may be 0)
      ("absent", None)   the page answered and is not a board for this slug (404 or
                         another organization's slug): the 404 stands as "no board"
      ("unverified", why)  the check itself failed or the page shape is unknown: the
                         404 is NOT proof of absence (review 2026-11-18, Codex F2)

    On 2026-11-18 one board (of about 300 companies probed) returned 404 from the API
    while its hosted page listed 11 roles (the second no-board scan report that day, in
    output/funnel-intake/), and company_queue.detect_board read that 404 as "no board at this
    slug". This reads two fields of __appData. It parses no roles: the page has no
    publishedAt and no description, so recovered roles would enter the scanner's gates
    degraded. REOPEN a role fallback on the 3rd live-board 404.
    """
    req = urllib.request.Request(HOSTED + quote_segment(slug))
    req.add_header("User-Agent", "Mozilla/5.0 (compatible; career-scanner/1.0)")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return ("absent", None) if e.code == 404 else ("unverified", f"hosted page HTTP {e.code}")
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
        return ("unverified", f"hosted page {type(e).__name__}: {e}")
    m = _APP_DATA.search(html)
    if not m:
        return ("unverified", "hosted page has no __appData")
    try:
        app, _ = json.JSONDecoder().raw_decode(html, m.end())
    except ValueError:
        return ("unverified", "hosted page __appData is not JSON")
    if not isinstance(app, dict):
        return ("unverified", "hosted page __appData is not an object")
    org = app.get("organization")
    page_slug = org.get("hostedJobsPageSlug") if isinstance(org, dict) else None
    if not isinstance(page_slug, str):
        # Review 2026-11-18 round 2 (Codex F1): no slug to compare is not proof of absence.
        return ("unverified", "hosted page __appData has no organization slug")
    if page_slug.casefold() != urllib.parse.unquote(slug).casefold():
        return ("absent", None)
    board = app.get("jobBoard")
    posts = board.get("jobPostings") if isinstance(board, dict) else None
    if not isinstance(posts, list):
        return ("unverified", "hosted page names this board but has no jobPostings list")
    return ("live", len(posts))


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
    url = f"https://api.ashbyhq.com/posting-api/job-board/{quote_segment(slug)}"
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
        state, detail = _hosted_board(slug) if e.code == 404 else ("absent", None)
        if errors is None:
            return []
        if state == "live":
            # A live board with postings is not "no board". With 0 postings the verdict
            # matches an empty API board (detect_board: no board WITH postings), but the
            # reason still says what was seen.
            errors.append({"reason": f"posting API HTTP 404, but the hosted board is live "
                                     f"({detail} postings): {HOSTED}{quote_segment(slug)}",
                           "live_board": True, "not_found": detail == 0})
        elif state == "unverified":
            errors.append({"reason": f"posting API HTTP 404; live-board check failed "
                                     f"({detail})", "not_found": False})
        else:
            _fail(errors, f"HTTP {e.code}")
        return []
    except (urllib.error.URLError, OSError) as e:
        print(f"Ashby network error for {slug}: {e}", file=sys.stderr)
        _fail(errors, f"{type(e).__name__}: {e}")
        return []
    except (http.client.HTTPException, ValueError) as e:
        # InvalidURL and a malformed body land here; neither is URLError or OSError.
        print(f"Ashby request error for {slug}: {e}", file=sys.stderr)
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
