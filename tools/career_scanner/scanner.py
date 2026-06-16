"""
scanner.py - Main orchestrator for career page scanning.

Pipeline: load targets -> fetch roles -> score -> dedup -> write inbox.

Functions:
  load_targets(repo_root) - Load active targets from scan-targets.yaml
  fetch_company_roles(target) - Fetch roles for a single company using appropriate parser
  scan_all_targets(repo_root, dry_run) - Full pipeline orchestrator
  format_inbox_entry(roles) - Format scan results as inbox entry text
  write_inbox(repo_root, roles) - Prepend scan results to data/inbox.md

CLI: Use cli.py for command-line invocation.
"""
import sys
import time
import yaml
from datetime import datetime, timezone
from pathlib import Path


def load_targets(repo_root: Path) -> list[dict]:
    """Load active targets from scan-targets.yaml.

    Returns list of company config dicts with active=True (or active not set).
    Returns empty list if config file is missing.
    """
    config_path = repo_root / "data" / "scan-targets.yaml"
    if not config_path.exists():
        print(f"No scan-targets.yaml found at {config_path}", file=sys.stderr)
        return []
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or not isinstance(data.get("companies"), list):
        return []
    return [c for c in data["companies"] if c.get("active", True)]


def fetch_company_roles(target: dict) -> list[dict]:
    """Fetch roles for a single company using the appropriate parser.

    Dispatches to the correct ATS parser based on target config.
    Overrides company name with display name from config.
    Applies role_filters if specified.

    Args:
        target: Company config dict from scan-targets.yaml

    Returns:
        List of standardized role dicts.
    """
    ats = target.get("ats", "generic")
    slug = target.get("slug", "")
    name = target.get("name", slug)

    try:
        if ats == "greenhouse":
            from tools.career_scanner.greenhouse import fetch_greenhouse
            roles = fetch_greenhouse(slug)
        elif ats == "lever":
            from tools.career_scanner.lever import fetch_lever
            roles = fetch_lever(slug)
        elif ats == "ashby":
            from tools.career_scanner.ashby import fetch_ashby
            roles = fetch_ashby(slug)
        elif ats == "generic":
            from tools.career_scanner.generic import fetch_generic
            careers_url = target.get("careers_url", "")
            if not careers_url:
                print(f"No careers_url for generic target '{name}'", file=sys.stderr)
                return []
            roles = fetch_generic(careers_url, name)
        else:
            print(f"Unknown ATS '{ats}' for {name}", file=sys.stderr)
            return []
    except Exception as e:
        print(f"Error fetching {name} ({ats}): {e}", file=sys.stderr)
        return []

    # Override company name with display name from config
    for r in roles:
        r["company"] = name

    # Apply role_filters if specified (empty list = all roles)
    filters = target.get("role_filters", [])
    if filters:
        roles = [r for r in roles if any(
            f.lower() in r.get("title", "").lower() for f in filters
        )]

    return roles


def scan_all_targets(repo_root: Path, dry_run: bool = False) -> dict:
    """Full pipeline: load targets -> fetch -> score -> dedup -> write inbox.

    Args:
        repo_root: Path to repository root.
        dry_run: If True, skip writing to inbox.

    Returns:
        Summary dict with keys: total_fetched, new_roles, skipped_dupes,
        companies_scanned, roles (list of scored/deduped role dicts).
    """
    from tools.career_scanner.scorer import score_role, load_scoring_context
    from tools.career_scanner.dedup import load_pipeline_entries, filter_duplicates

    targets = load_targets(repo_root)
    if not targets:
        return {"total_fetched": 0, "new_roles": 0, "skipped_dupes": 0,
                "companies_scanned": 0, "roles": []}

    scoring_ctx = load_scoring_context(repo_root)
    pipeline_entries = load_pipeline_entries(repo_root)

    all_roles = []
    errors = []
    for i, target in enumerate(targets):
        name = target.get("name", "?")
        ats = target.get("ats", "?")
        print(f"Scanning {name} ({ats})...", file=sys.stderr)
        roles = fetch_company_roles(target)
        if roles:
            all_roles.extend(roles)
            print(f"  Found {len(roles)} matching roles", file=sys.stderr)
        else:
            print(f"  No roles found", file=sys.stderr)
        # Rate limit: 0.5s between companies (T-02-09 mitigation)
        if i < len(targets) - 1:
            time.sleep(0.5)

    # Score all roles
    for role in all_roles:
        role["score"] = score_role(role, scoring_ctx)

    # Dedup against pipeline
    new_roles, skipped = filter_duplicates(all_roles, pipeline_entries)

    # Sort by score descending (D-05)
    new_roles.sort(key=lambda r: r.get("score", 0), reverse=True)

    summary = {
        "total_fetched": len(all_roles),
        "new_roles": len(new_roles),
        "skipped_dupes": skipped,
        "companies_scanned": len(targets),
        "roles": new_roles,
    }

    if new_roles and not dry_run:
        write_inbox(repo_root, new_roles)

    return summary


def format_inbox_entry(roles: list[dict]) -> str:
    """Format scan results as inbox entry text.

    Args:
        roles: List of scored role dicts, pre-sorted by score descending.

    Returns:
        Formatted string ready to prepend to inbox.md.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "## Career Scan Results",
        "",
        f"**Scanned:** {now_str}",
        f"**New roles found:** {len(roles)}",
        "",
    ]
    for role in roles:
        score = role.get("score", 0)
        loc = role.get("location", "")
        title = role.get("title", "Unknown")
        company = role.get("company", "Unknown")
        url = role.get("url", "")
        line = f"- **[{score}/10]** {title} at {company}"
        if loc:
            line += f" ({loc})"
        if url:
            line += f" - [View]({url})"
        lines.append(line)
    lines.append("")
    lines.append(f"*Source: /scan-companies | {now_str}*")
    lines.append("")
    return "\n".join(lines)


def write_inbox(repo_root: Path, roles: list[dict]):
    """Prepend scan results to data/inbox.md following granola_auto_debrief.py pattern.

    Reads existing content, finds insertion point after header + comments,
    prepends new entries, writes full file (per CLAUDE.md conventions).

    Args:
        repo_root: Path to repository root.
        roles: List of scored role dicts.
    """
    inbox_path = repo_root / "data" / "inbox.md"
    entry = format_inbox_entry(roles)

    if inbox_path.exists():
        content = inbox_path.read_text(encoding="utf-8")
    else:
        content = "# Inbox\n\n<!-- Items captured via /remember. Review and route to appropriate files periodically. -->\n"

    # Find insertion point: after the header line and any comment block
    # Follows granola_auto_debrief.py pattern (lines 203-215)
    lines = content.split("\n")
    insert_after = 0
    for i, line in enumerate(lines):
        if line.startswith("# Inbox"):
            insert_after = i + 1
            # Skip blank lines and HTML comments after header
            while insert_after < len(lines):
                next_line = lines[insert_after].strip()
                if next_line == "" or next_line.startswith("<!--") or next_line.endswith("-->"):
                    insert_after += 1
                else:
                    break
            break

    # Build new content: header + new entries + existing entries
    header_lines = lines[:insert_after]
    existing_lines = lines[insert_after:]

    new_content = "\n".join(header_lines) + "\n" + entry + "\n".join(existing_lines)

    # Full-file write (per CLAUDE.md conventions for data files)
    inbox_path.write_text(new_content, encoding="utf-8")

    print(f"Wrote {len(roles)} roles to {inbox_path}", file=sys.stderr)
