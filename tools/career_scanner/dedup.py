"""
dedup.py - Pipeline deduplication checker for career scanner.

Checks discovered roles against existing entries in data/job-pipeline.md
using exact company match (case-insensitive) + fuzzy title match (>= 80%).

Per D-10: If already in pipeline, skip silently.
"""
import json
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

_FUZZY_THRESHOLD = 0.80
_SUBPROCESS_TIMEOUT = 10  # seconds, per T-02-06 threat mitigation


def load_pipeline_entries(repo_root: Path) -> list[dict]:
    """Load active pipeline entries via pipe_read.py subprocess.

    Returns list of {"company": str, "role": str} dicts.
    Returns empty list on any failure (graceful degradation).
    """
    # No exists() check first: a missing script fails the subprocess, which returns []
    # below anyway (the check was a mutation survivor that nothing could tell apart).
    pipe_read_path = repo_root / "tools" / "pipe_read.py"
    try:
        result = subprocess.run(
            [sys.executable, str(pipe_read_path), "--repo-root", str(repo_root)],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
        )
        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        entries = []
        for entry in data.get("active_entries", []):
            company = entry.get("company", "").strip()
            role = entry.get("role", "").strip()
            if company:
                entries.append({"company": company, "role": role})
        return entries

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, KeyError):
        return []


def load_closed_entries(repo_root: Path) -> list[dict]:
    """Pipeline rows that are closed (terminal stage or under an Archived heading).

    The active-only dedup above lets a role Nick already rejected or withdrew from come
    back as if it were new (362.F4). Such a role is TAGGED, not dropped: a relisting can
    be worth a second look, but it must never read as unseen. Reads the rows through
    pipe_read.parse_all_rows, the one parser for "all rows". [] when anything fails.
    """
    from datetime import date
    try:
        from tools.pipe_read import parse_all_rows
        text = (Path(repo_root) / "data" / "job-pipeline.md").read_text(encoding="utf-8")
        rows = parse_all_rows(text, date.today())
    except (ImportError, OSError, UnicodeDecodeError):
        return []
    return [{"company": r["company"], "role": r["role"], "stage": r["stage"],
             "date_updated": r["date_updated"]}
            for r in rows if r["archived"] and r["company"]]


def find_match(role_title: str, role_company: str,
               pipeline_entries: list[dict]) -> dict | None:
    """The first entry at the same company (case-insensitive) whose role title is a fuzzy
    match (SequenceMatcher ratio >= 0.80, D-10), else None."""
    company_lower = role_company.lower().strip()
    if not company_lower:
        return None
    for entry in pipeline_entries:
        if entry["company"].lower().strip() != company_lower:
            continue
        if SequenceMatcher(None, role_title.lower(), entry["role"].lower()).ratio() >= _FUZZY_THRESHOLD:
            return entry
    return None


def tag_previously_closed(roles: list[dict], closed_entries: list[dict]) -> int:
    """Set `previously_closed` ("<stage>, <date>") on each role matching a closed pipeline
    row, and return how many were tagged."""
    tagged = 0
    for role in roles:
        hit = find_match(role.get("title", ""), role.get("company", ""), closed_entries)
        if hit:
            role["previously_closed"] = ", ".join(x for x in (hit["stage"], hit["date_updated"]) if x)
            tagged += 1
    return tagged


def is_duplicate(role_title: str, role_company: str,
                 pipeline_entries: list[dict]) -> bool:
    """Check if a role is already in the pipeline.

    Per D-10: Exact company match (case-insensitive) + fuzzy title match
    (SequenceMatcher ratio >= 0.80).

    Args:
        role_title: Title of the discovered role
        role_company: Company name from the role listing
        pipeline_entries: List of {"company": str, "role": str} dicts

    Returns:
        True if duplicate (should skip)
    """
    return find_match(role_title, role_company, pipeline_entries) is not None


def filter_duplicates(roles: list[dict],
                      pipeline_entries: list[dict]) -> tuple[list[dict], int]:
    """Filter a list of roles, removing duplicates found in pipeline.

    Args:
        roles: List of role dicts with "title" and "company" keys
        pipeline_entries: List of {"company": str, "role": str} dicts

    Returns:
        Tuple of (new_roles, skipped_count)
    """
    new_roles = []
    skipped = 0
    for role in roles:
        if is_duplicate(role.get("title", ""), role.get("company", ""), pipeline_entries):
            skipped += 1
        else:
            new_roles.append(role)
    return new_roles, skipped


# ---------------------------------------------------------------------------
# Role-level seen-set.
#
# filter_duplicates above answers "is this already in the pipeline?". That is NOT the
# same question as "have I shown this to Nick before?", and conflating the two is why
# `data/inbox.md` accumulated 56 career-scan blocks carrying the same ~30 roles: any
# role he had not promoted to the pipeline was re-emitted every single day, and the
# word "new" in the output meant only "not in your pipeline".
#
# Mirrors tools/agent_collect.py read_seen/write_seen deliberately -- one precedent in
# this repo, not two competing ones.
# ---------------------------------------------------------------------------

SEEN_FILENAME = ".career_seen.json"


def _seen_path(repo_root: Path) -> Path:
    return Path(repo_root) / "tools" / SEEN_FILENAME


def role_key(role: dict) -> str:
    """Stable identity for a posting.

    The ATS URL is preferred because it is the posting's own id and survives a title
    being edited. Falls back to company+title when a source supplies no URL, which is
    weaker (a re-listed role at a new URL correctly re-surfaces; one re-listed at the
    same URL will not) but is quiet rather than wrong -- and the standing list is always
    reported with a count, so nothing vanishes silently.
    """
    url = (role.get("url") or "").strip()
    if url:
        return url
    return f"{(role.get('company') or '').strip().lower()}::{(role.get('title') or '').strip().lower()}"


def load_seen(repo_root: Path) -> dict:
    """Read the seen-set. A missing file is an empty set, never an error.

    The nightly job must survive a first run on a fresh machine. An unreadable file is
    moved aside (`.corrupt-<time>`) and named on stderr before the run starts empty:
    starting empty in place re-announced every role as new and the next save overwrote
    the only copy of the first_seen history (601.F2).
    """
    path = _seen_path(repo_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    from datetime import datetime
    aside = path.with_name(f"{path.name}.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    try:
        path.replace(aside)
        print(f"career_scanner: seen-state unreadable; moved to {aside}, starting empty",
              file=sys.stderr)
    except OSError as e:
        print(f"career_scanner: seen-state unreadable and could not be moved aside ({e})",
              file=sys.stderr)
    return {}


def save_seen(repo_root: Path, seen: dict) -> None:
    """Write the seen-set atomically (temp file + os.replace), as agent_collect does."""
    import os
    import tempfile
    path = _seen_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def split_new_and_standing(roles: list[dict], seen: dict) -> tuple[list[dict], list[dict]]:
    """Partition roles into (never surfaced before, already surfaced).

    MUTATES `seen` in place so the caller can persist it with save_seen. Stamps
    `first_seen` on new roles so the surface can display age -- without it the reader
    cannot tell a role posted today from one that has been open since May.
    """
    from datetime import date
    today = date.today().isoformat()
    new, standing = [], []
    for role in roles:
        key = role_key(role)
        if key in seen:
            role["first_seen"] = seen[key].get("first_seen", "") if isinstance(seen[key], dict) else ""
            role["new"] = False
            standing.append(role)
        else:
            role["first_seen"] = today
            role["new"] = True     # explicit: first_seen == today is also true of a role seen earlier today
            seen[key] = {"first_seen": today,
                         "company": role.get("company", ""),
                         "title": role.get("title", "")}
            new.append(role)
    return new, standing
