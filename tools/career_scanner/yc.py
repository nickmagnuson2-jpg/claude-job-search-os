"""
yc.py - YC company job pages (ycombinator.com/companies/<slug>/jobs) parser.

Built in an overnight run (S10). 15 of 19 in-lane roles found that night at companies with no
ATS board came from YC job pages, and no scanner read them. NOT WIRED: YC is not in
scan-targets.yaml or the scanner's source list. Whether YC becomes a scan source is Nick's
ruling (data/workstreams/funnel-intake.md next item, S3-extended).

How the page carries its data: the server renders an Inertia page whose data is HTML-escaped
JSON in a `data-page` attribute.
- Company jobs page, component "WaasShowJobsPage": props.jobPostings, one object per open
  role (title, url, location, salaryRange, createdAt, lastActive, ...). No descriptions.
- Job page, component "WaasShowJobPage": props.job, the same fields plus `description`
  (markdown) and `hiringManager`.

fetch_yc returns exactly the ROLE_KEYS shape that ashby/greenhouse/lever return.
fetch_yc_detailed (and parse_board_html) add the YC-only keys: salary_range, equity_range,
min_experience, created_age, last_active, created_age_days, last_active_days, stale,
hiring_manager. A job page is used only if it is a job page for the same posting URL.

YC gives only RELATIVE ages ("3 days", "about 2 months"), so `published_at` stays "" rather
than an invented date. `created_age_days` is an approximation (a month is 30 days).

False-zero channel: every failure is appended to `errors` as {"reason": ...}. A dead slug
(HTTP 404), a network error, or a page whose shape changed never reads as a live board with no
openings. A failed JOB page keeps the role (from the board) with an empty description and
records the failure with the job URL.
"""
import html
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request

from tools.career_scanner import ROLE_KEYS
from tools.career_scanner.url_segment import quote_segment

BASE = "https://www.ycombinator.com"

# Staleness: an old listing that has not been touched recently. Both lines are days.
STALE_CREATED_DAYS = 150   # about 5 months
STALE_ACTIVE_DAYS = 30     # last activity this old or older counts as no recent activity

# Pacing. A real-data run: about 70 unpaced requests drew HTTP 429 from YC.
PAGE_PAUSE_SECONDS = 0.5   # before each job-page request
RETRY_DEFAULT_SECONDS = 5  # wait before the one 429 retry when there is no Retry-After
RETRY_MAX_SECONDS = 30     # cap on a server-supplied Retry-After
_sleep = time.sleep        # module seam so tests do not wait

_DATA_PAGE = re.compile(r'data-page="([^"]*)"')
_AGE = re.compile(r"^(?:about|over|almost|less than)?\s*(a|an|\d+)\s+(minute|hour|day|month|year)s?$")
_UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "month": 30, "year": 365}


def _fail(errors: list | None, reason: str) -> None:
    if errors is not None:
        errors.append({"reason": reason})


def age_days(text) -> int | None:
    """'17 days' -> 17, 'about 2 months' -> 60, 'over 1 year' -> 365; unknown -> None."""
    if not isinstance(text, str):
        return None
    m = _AGE.match(text.strip().lower())
    if not m:
        return None
    n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
    return n * _UNIT_DAYS[m.group(2)]


def is_stale(created_days: int | None, active_days: int | None) -> bool:
    """Old (>= STALE_CREATED_DAYS) and not active within STALE_ACTIVE_DAYS.

    Unknown age is not evidence of staleness. A null lastActive on an old listing counts
    as no recent activity.
    """
    if created_days is None or created_days < STALE_CREATED_DAYS:
        return False
    return active_days is None or active_days >= STALE_ACTIVE_DAYS


def _page_data(text: str, errors: list | None) -> dict | None:
    m = _DATA_PAGE.search(text or "")
    if not m:
        _fail(errors, "no data-page attribute (page layout changed?)")
        return None
    try:
        data = json.loads(html.unescape(m.group(1)))
    except ValueError as e:
        _fail(errors, f"data-page is not JSON: {e}")
        return None
    if not isinstance(data, dict):
        _fail(errors, f"data-page is {type(data).__name__}, expected a JSON object")
        return None
    return data


def _plain(markdown: str) -> str:
    """Markdown to plain text for scoring: drop heading marks and emphasis, keep words."""
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", markdown or "", flags=re.M)
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.strip()


def _s(v) -> str:
    """A field YC should send as a string; anything else (a type drift) becomes ""."""
    return v if isinstance(v, str) else ""


def _manager(hm) -> str:
    if not isinstance(hm, dict):
        return ""
    return f"{_s(hm.get('first_name'))} {_s(hm.get('last_name'))}".strip()


def _absolute(url: str) -> str:
    return BASE + url if url.startswith("/") else url


_STRING_FIELDS = ("location", "prettyRole", "type", "applyUrl", "salaryRange", "equityRange",
                  "minExperience", "createdAt", "lastActive")


def _drift(job: dict, url: str, errors: list | None) -> None:
    """Record any optional field whose type changed. The role is still returned, with the
    field emptied, so one odd field does not hide an open role; the error flags the drift."""
    for f in _STRING_FIELDS:
        v = job.get(f)
        if v is not None and not isinstance(v, str):
            _fail(errors, f"field {f!r} is {type(v).__name__}, expected str: {url}")
    hm = job.get("hiringManager")
    if hm is not None and not isinstance(hm, dict):
        _fail(errors, f"field 'hiringManager' is {type(hm).__name__}, expected object: {url}")


def _role(job: dict, slug: str, errors: list | None = None) -> dict:
    """Board posting -> role dict: the ROLE_KEYS shape plus the YC-only keys."""
    url = _absolute(job["url"])
    _drift(job, url, errors)
    location = _s(job.get("location"))
    created, active = job.get("createdAt"), job.get("lastActive")
    created_d, active_d = age_days(created), age_days(active)
    return {
        "title": job["title"],
        "company": slug,
        "department": _s(job.get("prettyRole")),
        "team": "",
        "location": location,
        "remote": "remote" in location.lower(),
        "employment_type": _s(job.get("type")),
        "url": url,
        "apply_url": _s(job.get("applyUrl")) or url,
        "published_at": "",
        "description_plain": "",
        "ats": "yc",
        "salary_range": _s(job.get("salaryRange")),
        "equity_range": _s(job.get("equityRange")),
        "min_experience": _s(job.get("minExperience")),
        "created_age": created if isinstance(created, str) else None,
        "last_active": active if isinstance(active, str) else None,
        "created_age_days": created_d,
        "last_active_days": active_d,
        "stale": is_stale(created_d, active_d),
        "hiring_manager": _manager(job.get("hiringManager")),
    }


def parse_board_html(text: str, slug: str, errors: list | None = None) -> list[dict]:
    """Parse a company jobs page. Pure: no network."""
    data = _page_data(text, errors)
    if data is None:
        return []
    if data.get("component") != "WaasShowJobsPage":
        _fail(errors, f"not a YC jobs page (component {data.get('component')!r})")
        return []
    props = data.get("props")
    postings = props.get("jobPostings") if isinstance(props, dict) else None
    if not isinstance(postings, list):
        _fail(errors, "payload has no 'jobPostings' list")
        return []
    roles = []
    for job in postings:
        if (not isinstance(job, dict) or not isinstance(job.get("title"), str)
                or not isinstance(job.get("url"), str) or not job["title"] or not job["url"]):
            _fail(errors, f"posting missing title or url: {str(job)[:80]}")
            continue
        roles.append(_role(job, slug, errors))
    return roles


def parse_job_html(text: str, errors: list | None = None) -> dict | None:
    """Parse a job page into its `job` object, or None (recorded) when absent."""
    data = _page_data(text, errors)
    if data is None:
        return None
    if data.get("component") != "WaasShowJobPage":
        _fail(errors, f"not a YC job page (component {data.get('component')!r})")
        return None
    props = data.get("props")
    job = props.get("job") if isinstance(props, dict) else None
    if not isinstance(job, dict):
        _fail(errors, "no 'job' object on the job page")
        return None
    return job


def _retry_wait(err: urllib.error.HTTPError) -> float:
    try:
        wait = float((err.headers or {}).get("Retry-After"))
    except (TypeError, ValueError):
        wait = RETRY_DEFAULT_SECONDS
    return min(max(wait, 0), RETRY_MAX_SECONDS)


def _open(url: str) -> str:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (compatible; career-scanner/1.0)")
    req.add_header("Accept", "text/html")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _get(url: str, errors: list | None, label: str) -> str | None:
    """GET a page. One retry on HTTP 429; every other failure is recorded and None returned."""
    try:
        try:
            return _open(url)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            _sleep(_retry_wait(e))
            return _open(url)
    except urllib.error.HTTPError as e:
        print(f"YC error for {label}: HTTP {e.code}", file=sys.stderr)
        _fail(errors, f"HTTP {e.code}: {url}")
    except (urllib.error.URLError, OSError) as e:
        print(f"YC network error for {label}: {e}", file=sys.stderr)
        _fail(errors, f"{type(e).__name__}: {e}: {url}")
    except (http.client.HTTPException, ValueError) as e:
        print(f"YC request error for {label}: {e}", file=sys.stderr)
        _fail(errors, f"{type(e).__name__}: {e}: {url}")
    return None


def fetch_yc(slug: str, errors: list | None = None, descriptions: bool = True) -> list[dict]:
    """Fetch one YC company's open roles in the exact ROLE_KEYS shape every parser returns.

    The YC-only keys (salary, ages, stale, hiring manager) come from fetch_yc_detailed, kept
    separate for the same reason as ashby.fetch_ashby_with_compensation: one signature and one
    ROLE_KEYS contract across parsers (tests/scripts/test_parsers.py).
    """
    return [{k: r[k] for k in ROLE_KEYS} for r in fetch_yc_detailed(slug, errors, descriptions)]


def fetch_yc_detailed(slug: str, errors: list | None = None, descriptions: bool = True) -> list[dict]:
    """Fetch one YC company's open roles with the YC-only keys.

    Args:
        slug: the company segment of ycombinator.com/companies/<slug>.
        errors: optional out-list; every failure is appended as {"reason": ...}.
        descriptions: also fetch each job page for its description and hiring manager
            (one request per role). False reads only the board.
    """
    board_url = f"{BASE}/companies/{quote_segment(slug)}/jobs"
    text = _get(board_url, errors, slug)
    if text is None:
        return []
    roles = parse_board_html(text, slug, errors)
    if not descriptions:
        return roles
    for role in roles:
        _sleep(PAGE_PAUSE_SECONDS)
        page = _get(role["url"], errors, f"{slug} job")
        if page is None:
            continue
        job_errors: list = []
        job = parse_job_html(page, job_errors)
        if job is None:
            for e in job_errors:
                _fail(errors, f"{e['reason']}: {role['url']}")
            continue
        if _absolute(_s(job.get("url"))) != role["url"]:
            _fail(errors, f"job page is for a different posting ({_s(job.get('url')) or 'no url'}): {role['url']}")
            continue
        role["description_plain"] = _plain(_s(job.get("description")))
        role["hiring_manager"] = _manager(job.get("hiringManager")) or role["hiring_manager"]
    return roles
