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
    pipe_read_path = repo_root / "tools" / "pipe_read.py"
    if not pipe_read_path.exists():
        return []

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
    company_lower = role_company.lower().strip()
    if not company_lower:
        return False

    for entry in pipeline_entries:
        if entry["company"].lower().strip() != company_lower:
            continue
        ratio = SequenceMatcher(
            None,
            role_title.lower(),
            entry["role"].lower()
        ).ratio()
        if ratio >= _FUZZY_THRESHOLD:
            return True
    return False


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
