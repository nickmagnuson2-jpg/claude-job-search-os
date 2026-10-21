#!/usr/bin/env python3
"""recruiter_views.py - ledger of LinkedIn "Recruiter Views" and what was done about each.

WHY THIS EXISTS
    LinkedIn Premium lists recruiters who viewed the profile as "Recruiter at <company>",
    with an industry line and a relative time ("Viewed 11d ago"). The individual is not
    named. The list scrolls away and the relative times drift, so a view that is not
    recorded the day it is seen is lost. This ledger turns each row into a dated record and
    tracks it to an outcome: no jobs, roles found, pitched, or skipped.

    Agencies and in-house recruiters are handled differently, so every row carries a kind:
      agency   - a staffing firm. Its jobs are for unnamed clients; the pitch goes to the firm.
      in-house - the company is hiring for itself; scan its careers page and score the roles.
    The kind comes from the company dimension (tools/company_dim.py) when the company is in
    it, and from the LinkedIn industry line otherwise. The basis is recorded on the row.

    Each new row also records which of Nick's files already mention the company, via
    tools/records_lookup.py, so a view from a company with history (a closed process, an open
    thread) is visible before anyone pitches it.

USAGE
    PYTHONIOENCODING=utf-8 python3 tools/recruiter_views.py ingest \\
        --captured-at "2026-01-15 14:05" \\
        --row "Acme|Staffing and Recruiting|1h" --row "Globex|Software Development|11d"
    PYTHONIOENCODING=utf-8 python3 tools/recruiter_views.py set-status Acme no-jobs \\
        --note "View jobs showed nothing"
    PYTHONIOENCODING=utf-8 python3 tools/recruiter_views.py list [--status new]
    PYTHONIOENCODING=utf-8 python3 tools/recruiter_views.py reclassify   # after a dimension ruling

    --row is "<company>|<industry>|<age>". Industry may be empty (LinkedIn omits it for some
    companies). Age is the number and unit LinkedIn shows: 45m, 7h, 11d, 2w, 3mo, 1y.

    A view already in the ledger is skipped, so the same list can be ingested again from a
    later screenshot without duplicating rows. "Already in" means same company and a view
    date within the precision of the age shown: exact for minutes and hours (so a repeat
    view the next day, shown as "2h", is a new row), one day for "Nd", a week for "Nw".

    JSON on stdout. Atomic write. Ledger: data/recruiter-views.md (gitignored), newest first.
    Override with RECRUITER_VIEWS_LEDGER; override the records root with --repo-root.

EXIT CODES
    0 ok, 1 nothing matched (set-status on an unknown company), 2 usage error
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import company_dim  # noqa: E402
import records_lookup  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER = Path(os.environ.get(
    "RECRUITER_VIEWS_LEDGER", str(REPO_ROOT / "data" / "recruiter-views.md")))

STATUSES = ("new", "no-jobs", "roles-found", "pitched", "skip")
AGENCY_INDUSTRIES = ("staffing and recruiting",)
FILE_LABELS = {"job-pipeline.md": "pipeline", "networking.md": "networking",
               "job-todos.md": "todos", "inbox.md": "inbox", "outreach-log.md": "outreach-log"}
COLUMNS = ("Viewed", "Company", "Kind", "Industry", "Records", "Status", "Note", "Logged")
HEADER = "| " + " | ".join(COLUMNS) + " |\n|" + "---|" * len(COLUMNS)
PREAMBLE = (
    "# Recruiter Views\n\n"
    "<!-- Written by tools/recruiter_views.py. Do not hand-edit rows; use set-status. -->\n\n"
    "LinkedIn Premium recruiter views, one row per view, newest first. Viewed is APPROXIMATE:\n"
    "computed from LinkedIn's relative time at capture, exact for minute/hour ages, within a\n"
    "day for 'Nd', coarser for weeks and months. Kind basis: dim = company dimension,\n"
    "industry = LinkedIn industry line. Records is a substring hint, not a confirmed match:\n"
    "a company named with a common word (e.g. 'Stealth') matches unrelated prose.\n\n"
)

AGE_RE = re.compile(r"^\s*(\d+)\s*(m|min|h|hr|d|w|mo|y|yr)s?\s*(ago)?\s*$", re.IGNORECASE)
AGE_DAYS = {"d": 1, "w": 7, "mo": 30, "y": 365, "yr": 365}
# How far a computed date can sit from the true one, by the unit LinkedIn displayed. Minute and
# hour ages pin the date; a repeat view the next day shows as "2h" and must stay a new row.
AGE_SLACK_DAYS = {"m": 0, "min": 0, "h": 0, "hr": 0, "d": 1, "w": 7, "mo": 30, "y": 365, "yr": 365}


def _parse_age(age: str) -> tuple[int, str]:
    m = AGE_RE.match(age)
    if not m:
        raise ValueError(f"unreadable age {age!r}; expected e.g. 45m, 7h, 11d, 2w, 3mo")
    return int(m.group(1)), m.group(2).lower()


def viewed_on(age: str, captured_at: datetime) -> date:
    """Calendar date of a view from LinkedIn's relative age at capture time."""
    n, unit = _parse_age(age)
    if unit in ("m", "min"):
        return (captured_at - timedelta(minutes=n)).date()
    if unit in ("h", "hr"):
        return (captured_at - timedelta(hours=n)).date()
    return (captured_at - timedelta(days=n * AGE_DAYS[unit])).date()


def age_slack(age: str) -> int:
    return AGE_SLACK_DAYS[_parse_age(age)[1]]


def classify(company: str, industry: str, dim) -> tuple[str, str]:
    """(kind, basis). The company dimension wins over the industry line when it has ruled."""
    t = dim.type_of(company) if dim is not None else None
    if t == "recruiter":
        return "agency", "dim"
    if t == "target":
        return "in-house", "dim"
    if industry.strip().lower() in AGENCY_INDUSTRIES:
        return "agency", "industry"
    return "in-house", "industry"


def records_for(company: str, root: Path) -> str:
    """Which of Nick's record files mention the company. 'none' is scoped to records_lookup's
    default sources, which is what the column header means."""
    try:
        report = records_lookup.search(root, [company])
    except ValueError:
        return "n/a (name too short)"
    term = report["terms"][0]
    own = {Path(x).name for x in term["folders_with_hits"]}
    labels, mentions = [], 0
    for f in term["files_with_hits"]:
        parts = Path(f).parts
        if parts[0] == "output":
            # another company's dossier naming this one in passing: count it, don't list it
            mentions += parts[1] not in own
        elif "company-notes" in parts or "industry-notes" in parts or "people" in parts:
            labels.append(parts[-2])
        else:
            labels.append(FILE_LABELS.get(parts[-1], parts[-1]))
    if own:
        labels.append("dossier")
    if mentions:
        labels.append(f"named in {mentions} other dossier(s)")
    return ", ".join(dict.fromkeys(labels)) or "none"


def atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".recruiter_views_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cell(s: str) -> str:
    return (s or "").replace("|", "/").replace("\n", " ").strip()


def read_rows(path: Path = None) -> list[dict]:
    path = path or LEDGER
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith("| Viewed |"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != len(COLUMNS):
            raise ValueError(f"malformed ledger row ({len(cells)} cells): {line}")
        rows.append(dict(zip(COLUMNS, cells)))
    return rows


def write_rows(rows: list[dict], path: Path = None) -> None:
    path = path or LEDGER
    rows = sorted(rows, key=lambda r: r["Viewed"], reverse=True)
    body = "\n".join("| " + " | ".join(_cell(r[c]) for c in COLUMNS) + " |" for r in rows)
    atomic_write(path, PREAMBLE + HEADER + "\n" + body + ("\n" if body else ""))


@contextlib.contextmanager
def _locked():
    """Serialize read-modify-write across processes; atomic replace alone lets two writers
    each read version N and the later one erase the earlier one's change."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER.parent / ".recruiter-views.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def _same_view(row: dict, company: str, day: date, slack: int) -> bool:
    if company_dim.Dimension._key(row["Company"]) != company_dim.Dimension._key(company):
        return False
    return abs((date.fromisoformat(row["Viewed"]) - day).days) <= slack


def _parse_row(raw: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) != 3 or not parts[0] or not parts[2]:
        raise ValueError(f"--row must be 'company|industry|age', got {raw!r}")
    return parts[0], parts[1], parts[2]


def _load_dim(path: Path | None = None):
    """The dimension, or None when the file does not exist (a fresh clone). A file that
    exists but does not load raises: silently falling back would discard every ruling."""
    try:
        return company_dim.load(path or company_dim.DEFAULT_PATH)
    except FileNotFoundError:
        return None


def ingest(raw_rows: list[str], captured_at: datetime, root: Path, dim) -> dict:
    parsed = [_parse_row(r) for r in raw_rows]  # shape errors abort before the lock; a bad age
    with _locked():                              # raises inside, still before the single write
        return _ingest(parsed, captured_at, root, dim)


def _ingest(parsed, captured_at, root, dim) -> dict:
    rows = read_rows()
    added, skipped = [], []
    for company, industry, age in parsed:
        day = viewed_on(age, captured_at)
        if any(_same_view(r, company, day, age_slack(age)) for r in rows):
            skipped.append({"company": company, "viewed": day.isoformat()})
            continue
        kind, basis = classify(company, industry, dim)
        row = {"Viewed": day.isoformat(), "Company": company, "Kind": f"{kind} ({basis})",
               "Industry": industry or "-", "Records": records_for(company, root),
               "Status": "new", "Note": "-", "Logged": captured_at.date().isoformat()}
        rows.append(row)
        added.append(row)
    if added:
        write_rows(rows)
    return {"status": "ok", "added": added, "skipped_existing": skipped,
            "ledger": str(LEDGER)}


def set_status(company: str, status: str, note: str | None) -> dict:
    with _locked():
        return _set_status(company, status, note)


def _set_status(company: str, status: str, note: str | None) -> dict:
    rows = read_rows()
    key = company_dim.Dimension._key(company)
    matches = [r for r in rows if company_dim.Dimension._key(r["Company"]) == key]
    if not matches:
        return {"status": "not_found", "company": company, "ledger": str(LEDGER)}
    target = max(matches, key=lambda r: r["Viewed"])  # the most recent view of that company
    target["Status"] = status
    if note:
        target["Note"] = note
    write_rows(rows)
    return {"status": "ok", "updated": target}


def reclassify(dim) -> dict:
    """Recompute Kind on every row. Run after ruling on a company in the dimension, since a
    row's kind is otherwise fixed at ingest. Refuses without a dimension: recomputing from the
    industry line alone would silently undo every ruling."""
    if dim is None:
        raise ValueError("company dimension not found; reclassify needs it")
    with _locked():
        return _reclassify(dim)


def _reclassify(dim) -> dict:
    rows = read_rows()
    changed = []
    for r in rows:
        industry = "" if r["Industry"] == "-" else r["Industry"]
        kind, basis = classify(r["Company"], industry, dim)
        new = f"{kind} ({basis})"
        if new != r["Kind"]:
            changed.append({"company": r["Company"], "viewed": r["Viewed"],
                            "from": r["Kind"], "to": new})
            r["Kind"] = new
    if changed:
        write_rows(rows)
    return {"status": "ok", "changed": changed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--companies", type=Path, help="company dimension (default data/companies.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_in = sub.add_parser("ingest")
    p_in.add_argument("--captured-at", required=True, help="YYYY-MM-DD HH:MM, local time")
    p_in.add_argument("--row", action="append", required=True)
    p_set = sub.add_parser("set-status")
    p_set.add_argument("company")
    p_set.add_argument("status", choices=STATUSES)
    p_set.add_argument("--note")
    p_ls = sub.add_parser("list")
    p_ls.add_argument("--status", choices=STATUSES)
    sub.add_parser("reclassify")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "ingest":
            captured = datetime.strptime(args.captured_at, "%Y-%m-%d %H:%M")
            out = ingest(args.row, captured, Path(args.repo_root), _load_dim(args.companies))
        elif args.cmd == "set-status":
            out = set_status(args.company, args.status, args.note)
        elif args.cmd == "reclassify":
            out = reclassify(_load_dim(args.companies))
        else:
            rows = read_rows()
            if args.status:
                rows = [r for r in rows if r["Status"] == args.status]
            out = {"status": "ok", "count": len(rows), "rows": rows}
    except ValueError as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        return 2
    print(json.dumps(out, indent=2))
    return 1 if out["status"] == "not_found" else 0


if __name__ == "__main__":
    sys.exit(main())
