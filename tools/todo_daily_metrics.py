#!/usr/bin/env python3
"""
todo_daily_metrics.py — Pre-process daily job search metrics for /checkout and /weekly-review.

Reads: data/job-todos.md, data/job-todos-daily-log.md, data/job-pipeline.md,
       data/networking.md, data/outreach-log.md, docs/CHANGELOG.md, output/**/*.md

Outputs JSON to stdout with keys:
  completed_today[]      — tasks completed today
  active_remaining[]     — tasks still active (separator rows filtered)
  overdue[]              — tasks past due date
  outreach_sent_today[]  — outreach sent today (outreach-log + today's networking blockquotes)
  research_completed_today[] — dossiers updated today
  changelog_today[]      — changelog entries for today
  metrics{}              — streak, this_week, last_7, last_30, all_time, avg_per_day, overdue_trend
  pipeline_snapshot{}    — { active_process: [], evaluation_backlog: [], terminal: [] }
                           Active Process = roles actively moving forward
                           Evaluation Backlog = "To Evaluate" / "To Apply" / "Researching"
                           Terminal = Closed / Rejected / Withdrawn / Skipped / Archived
  upcoming_scheduled[]   — time-anchored events (interviews/screens/calls) parsed from
                           the pipeline Next-Action column, within the next 3 days; pin
                           these atop Top-3 (they live in free text, not dated todos)
  networking_activity[]  — contacts with pending follow-up (separator rows filtered)
  substantive_work{}     — artifact volume signal: outputs_today, reflections_today,
                           memories_today, inbox_entries_today, outreach_touches_today

Built 2026-05-13 with separator-row filtering + pipeline bucketing + streak fix for
today-not-yet-in-log + substantive-work signal. Mirrors the /standup → todos_summary.py
data-hygiene fix from earlier same day.

Usage:
  python3 tools/todo_daily_metrics.py [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--target-date", default=None,
                   help="Date to evaluate as 'today' (YYYY-MM-DD). Defaults to actual today.")
    p.add_argument("--repo-root", default=None,
                   help="Repository root path. Defaults to cwd.")
    return p.parse_args()


def read_file(path: Path) -> str:
    """Read file contents or return empty string if missing."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return ""


def _is_separator_row(*values: str) -> bool:
    """Return True if any value is a literal '---' separator (the file's known artifact)."""
    return any(v.strip() in ("---", "—", "") for v in values[:1])


def parse_todos(content: str, today: date) -> tuple[list, list, list]:
    """Parse job-todos.md into completed_today, active, overdue lists. Strips '---' separator rows."""
    completed_today = []
    active = []
    overdue = []

    today_str = today.strftime("%Y-%m-%d")

    # Find Active section
    active_match = re.search(r"## Active.*?\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if active_match:
        active_text = active_match.group(1)
        # Parse markdown table rows (skip header/separator)
        for line in active_text.splitlines():
            if not line.startswith("|") or line.startswith("| Task") or line.startswith("|---"):
                continue
            cols = [c.strip() for c in line.strip("|").split("|")]
            if len(cols) < 4:
                continue
            task, priority, due, status = cols[0], cols[1], cols[2], cols[3]
            notes = cols[4] if len(cols) > 4 else ""

            # Skip in-table separator rows (literal '---' as task)
            if _is_separator_row(task):
                continue

            item = {"task": task, "priority": priority, "due": due, "status": status, "notes": notes}
            active.append(item)

            # Check if overdue
            if due and due != "—" and due != "-":
                try:
                    due_date = datetime.strptime(due, "%Y-%m-%d").date()
                    if due_date < today and status not in ("Done", "Withdrawn"):
                        overdue.append(item)
                except ValueError:
                    pass

    # Find Completed section — look for today's date in Notes or Completed column
    completed_match = re.search(r"## Completed.*?\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if completed_match:
        completed_text = completed_match.group(1)
        for line in completed_text.splitlines():
            if not line.startswith("|") or line.startswith("| Task") or line.startswith("|---"):
                continue
            cols = [c.strip() for c in line.strip("|").split("|")]
            if len(cols) < 3:
                continue
            task, priority = cols[0], cols[1]
            completed_date = cols[2] if len(cols) > 2 else ""
            notes = cols[3] if len(cols) > 3 else ""

            # Withdrawn rows live in the Completed section too (marked in the Completed
            # column or Notes), but a withdrawal is NOT a completion — it must not inflate
            # streak/velocity/all-time counts.
            if "withdrawn" in completed_date.lower() or "withdrawn" in notes.lower():
                continue

            # Check if completed today
            if today_str in completed_date or today_str in notes:
                completed_today.append({"task": task, "priority": priority, "notes": notes})

    return completed_today, active, overdue


def parse_daily_log(content: str, today: date, today_completions_from_todos: int = 0) -> dict:
    """Parse daily log and compute streak + velocity metrics."""
    today_str = today.strftime("%Y-%m-%d")

    # Extract all dated entries: ### YYYY-MM-DD
    entries = {}
    current_date = None
    current_lines = []

    for line in content.splitlines():
        m = re.match(r"^### (\d{4}-\d{2}-\d{2})", line)
        if m:
            if current_date:
                entries[current_date] = "\n".join(current_lines)
            current_date = m.group(1)
            current_lines = [line]
        elif current_date:
            current_lines.append(line)

    if current_date:
        entries[current_date] = "\n".join(current_lines)

    def completions_for(date_str: str) -> int:
        text = entries.get(date_str, "")
        m = re.search(r"\*\*Completed today:\s*(\d+)\*\*", text)
        return int(m.group(1)) if m else 0

    # This week (last 7 days including today)
    this_week = sum(completions_for((today - timedelta(days=i)).strftime("%Y-%m-%d"))
                    for i in range(7))

    # Last 30 days
    last_30 = sum(completions_for((today - timedelta(days=i)).strftime("%Y-%m-%d"))
                  for i in range(30))

    # All-time
    all_time = sum(
        int(re.search(r"\*\*Completed today:\s*(\d+)\*\*", text).group(1))
        for text in entries.values()
        if re.search(r"\*\*Completed today:\s*(\d+)\*\*", text)
    )

    # Streak — consecutive days ending today with >=1 completion
    # Today's log entry may not exist yet (chicken/egg with /checkout writing it),
    # so use today_completions_from_todos as the today-day signal if log is empty.
    streak = 0
    check_date = today
    today_str_for_streak = today.strftime("%Y-%m-%d")
    today_log_count = completions_for(today_str_for_streak)
    effective_today = max(today_log_count, today_completions_from_todos)

    if effective_today >= 1:
        streak = 1
        check_date = today - timedelta(days=1)
        while True:
            ds = check_date.strftime("%Y-%m-%d")
            if completions_for(ds) >= 1:
                streak += 1
                check_date -= timedelta(days=1)
            else:
                break
    # If today is zero, streak stays 0 — that's the correct semantic.

    # Avg per active day
    active_days = sum(1 for text in entries.values()
                      if re.search(r"\*\*Completed today:\s*[1-9]", text))
    avg_per_day = round(all_time / active_days, 1) if active_days > 0 else 0.0

    # Overdue trend — compare today vs 7-day rolling average
    def overdue_for(date_str: str) -> int:
        text = entries.get(date_str, "")
        m = re.search(r"Overdue:\s*(\d+)", text)
        return int(m.group(1)) if m else 0

    today_overdue = overdue_for(today_str)
    avg_overdue = sum(overdue_for((today - timedelta(days=i)).strftime("%Y-%m-%d"))
                      for i in range(1, 8)) / 7
    if today_overdue > avg_overdue + 0.5:
        overdue_trend = "↑"
    elif today_overdue < avg_overdue - 0.5:
        overdue_trend = "↓"
    else:
        overdue_trend = "→"

    return {
        "streak": streak,
        "this_week": this_week,
        "last_7": this_week,
        "last_30": last_30,
        "all_time": all_time,
        "avg_per_day": avg_per_day,
        "overdue_trend": overdue_trend,
        "entry_count": len(entries),
    }


def parse_outreach_today(content: str, today: date) -> list:
    """Extract outreach entries sent today from outreach-log.md."""
    today_str = today.strftime("%Y-%m-%d")
    results = []

    for line in content.splitlines():
        if not line.startswith("|") or line.startswith("| Date") or line.startswith("|---"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) >= 5 and cols[0] == today_str:
            results.append({
                "date": cols[0],
                "type": cols[1] if len(cols) > 1 else "",
                "channel": cols[2] if len(cols) > 2 else "",
                "name": cols[3] if len(cols) > 3 else "",
                "company": cols[4] if len(cols) > 4 else "",
                "subject": cols[5] if len(cols) > 5 else "",
            })

    return results


def find_research_today(output_dir: Path, today: date) -> list:
    """Find dossier files updated today (output/<slug>/<slug>.md pattern)."""
    today_str = today.strftime("%Y-%m-%d")
    results = []

    for md_file in output_dir.rglob("*.md"):
        # Dossier = file where stem matches parent directory name
        if md_file.stem == md_file.parent.name:
            try:
                # Read only first 15 lines for efficiency
                lines = []
                with open(md_file, encoding="utf-8") as f:
                    for i, line in enumerate(f):
                        if i >= 15:
                            break
                        lines.append(line)
                header = "".join(lines)
                if f"Last updated: {today_str}" in header:
                    results.append({
                        "name": md_file.parent.name,
                        "path": str(md_file.relative_to(output_dir.parent)),
                    })
            except (IOError, UnicodeDecodeError):
                pass

    return results


def parse_changelog_today(content: str, today: date) -> list:
    """Extract changelog entries for today."""
    today_str = today.strftime("%Y-%m-%d")
    results = []

    for line in content.splitlines():
        m = re.match(rf"^## {re.escape(today_str)}\s*[—-]?\s*(.*)", line)
        if m:
            results.append({"title": m.group(1).strip(), "date": today_str})

    return results


ACTIVE_PROCESS_STAGES = {
    "applied", "phone screen", "screening", "first round", "interview", "interviewing",
    "onsite", "onsite loop", "offer", "recruiter submitted", "recruiter screen pending",
    "recruiter contact", "hiring manager", "founder meet", "peer meet", "networking",
}
EVALUATION_BACKLOG_STAGES = {"to evaluate", "to apply", "researching"}
TERMINAL_STAGES = {"closed", "rejected", "withdrawn", "skipped", "archived", "accepted"}


def _bucket_for_stage(stage: str) -> str:
    s = stage.strip().lower()
    if s in ACTIVE_PROCESS_STAGES:
        return "active_process"
    if s in EVALUATION_BACKLOG_STAGES:
        return "evaluation_backlog"
    if s in TERMINAL_STAGES:
        return "terminal"
    # Unknown stage = active by default (don't lose real entries)
    return "active_process"


def parse_pipeline(content: str) -> dict:
    """Parse pipeline entries from job-pipeline.md and bucket by stage category.

    Returns:
      { "active_process": [...], "evaluation_backlog": [...], "terminal": [...] }
    """
    buckets = {"active_process": [], "evaluation_backlog": [], "terminal": []}

    # Find the Active section (or fall back to whole file)
    active_match = re.search(r"## Active.*?\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if not active_match:
        active_match = re.search(r"\n(.*)", content, re.DOTALL)
    if not active_match:
        return buckets

    for line in active_match.group(1).splitlines():
        if not line.startswith("|") or line.startswith("| Company") or line.startswith("|---"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or not cols[0]:
            continue
        # Skip in-table separator rows
        if _is_separator_row(cols[0]):
            continue
        entry = {
            "company": cols[0],
            "role": cols[1] if len(cols) > 1 else "",
            "stage": cols[2] if len(cols) > 2 else "",
        }
        bucket = _bucket_for_stage(entry["stage"])
        buckets[bucket].append(entry)

    return buckets


# Keywords that mark a Next-Action as a true time-anchored event (interview,
# screen, call, etc.) versus a generic dated follow-up. Used to set is_event so
# the Top-3 ranker can pin real events above routine follow-ups.
SCHEDULED_EVENT_RE = re.compile(
    r"\b(interview|screen|call|onsite|debrief|panel|meet|meeting)\b", re.IGNORECASE
)

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MD_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")


def _extract_dates(text: str, today: date) -> list:
    """Pull candidate dates from free text. Handles ISO (YYYY-MM-DD) and bare
    M/D (year inferred relative to `today`, with Dec->Jan rollover). Times like
    "12:30" use a colon and are never matched by the slash-based M/D regex."""
    found = []
    for m in _ISO_DATE_RE.finditer(text):
        try:
            found.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            continue
    for m in _MD_DATE_RE.finditer(text):
        month, day = int(m.group(1)), int(m.group(2))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        try:
            cand = date(today.year, month, day)
        except ValueError:
            continue
        # A bare M/D landing far in the past most likely means next year.
        if cand < today - timedelta(days=180):
            try:
                cand = date(today.year + 1, month, day)
            except ValueError:
                pass
        found.append(cand)
    return found


def parse_upcoming_scheduled(pipeline_content: str, today: date,
                             window_days: int = 3) -> list:
    """Surface time-anchored events from the pipeline Next-Action column that
    fall within [today, today + window_days].

    Interviews/screens/calls are routinely written into the Next-Action free
    text (e.g. "Interview w/ Casey Doe Fri 6/12 12:30pm PT") rather than as dated
    todos, so the Top-3 ranker never sees them. This parses those dates out so
    /checkout and /standup can pin imminent events above undated todos.

    Returns a list sorted by date ascending, then events before follow-ups:
      [{ company, role, date: "YYYY-MM-DD", days_until, is_event, next_action }]

    Origin: 2026-06-11 — a next-day interview written into the pipeline
    Next-Action was invisible to /standup and /checkout because both rank only
    from todo due-dates + pipeline stage. See
    feedback_surface_scheduled_events_in_ranking.
    """
    window_end = today + timedelta(days=window_days)
    results = []

    active_match = re.search(r"## Active.*?\n(.*?)(?=\n## |\Z)",
                             pipeline_content, re.DOTALL)
    if not active_match:
        active_match = re.search(r"\n(.*)", pipeline_content, re.DOTALL)
    if not active_match:
        return results

    for line in active_match.group(1).splitlines():
        if (not line.startswith("|") or line.startswith("| Company")
                or line.startswith("|---")):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 5 or not cols[0]:
            continue
        if _is_separator_row(cols[0]):
            continue
        next_action = cols[4]
        if not next_action or next_action.lower() in ("none", "—", "-"):
            continue
        dates = [d for d in _extract_dates(next_action, today)
                 if today <= d <= window_end]
        if not dates:
            continue
        event_date = min(dates)
        results.append({
            "company": cols[0],
            "role": cols[1] if len(cols) > 1 else "",
            "date": event_date.strftime("%Y-%m-%d"),
            "days_until": (event_date - today).days,
            "is_event": bool(SCHEDULED_EVENT_RE.search(next_action)),
            "next_action": next_action,
        })

    # Sort by date ascending; within a date, true events before follow-ups.
    results.sort(key=lambda e: (e["date"], not e["is_event"]))
    return results


def parse_networking_activity(networking_content: str, todos_content: str) -> list:
    """Find contacts with pending follow-up to-dos. Filters '---' separator rows."""
    results = []

    # Extract contact names from networking.md
    contacts = []
    for line in networking_content.splitlines():
        if not line.startswith("|") or line.startswith("| Name") or line.startswith("|---"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or not cols[0]:
            continue
        # Skip in-table separator rows (literal '---' as name)
        if _is_separator_row(cols[0]):
            continue
        contacts.append({
            "name": cols[0],
            "company": cols[1] if len(cols) > 1 else "",
            "last_interaction": cols[4] if len(cols) > 4 else "",
        })

    # Cross-reference with active todos (full name matching)
    for contact in contacts:
        name = contact["name"]
        if name and name in todos_content:
            # Count pending todos mentioning this contact
            count = todos_content.count(name)
            if count > 0:
                results.append({
                    "name": name,
                    "company": contact["company"],
                    "last_interaction": contact["last_interaction"],
                    "pending_todos": count,
                })

    return results


def parse_networking_touches_today(networking_content: str, today: date) -> list:
    """Find blockquoted interaction entries in networking.md dated today.

    These are touches logged via networking_write.py that may not have synced
    to outreach-log.md (the sync is conditional). Surface them as supplementary
    outreach signal.
    """
    today_str = today.strftime("%Y-%m-%d")
    results = []

    # Walk per-contact subsections (### Name — Company)
    current_name = None
    current_company = ""
    lines = networking_content.splitlines()

    for i, line in enumerate(lines):
        m = re.match(r"^###\s+(.+?)(?:\s+[—–-]\s+(.+))?$", line)
        if m:
            current_name = m.group(1).strip()
            current_company = (m.group(2) or "").strip()
            continue
        # Detect today-dated interaction header: #### YYYY-MM-DD | type | summary
        m2 = re.match(rf"^####\s+{re.escape(today_str)}\s*\|\s*(\w+)\s*\|\s*(.*)", line)
        if m2 and current_name and not _is_separator_row(current_name):
            channel = m2.group(1).strip()
            summary = m2.group(2).strip()
            results.append({
                "date": today_str,
                "name": current_name,
                "company": current_company,
                "channel": channel,
                "subject": summary[:120],
                "source": "networking.md",
            })

    return results


def scan_substantive_work(repo_root: Path, today: date) -> dict:
    """Scan filesystem for artifacts produced today. Mirrors /checkout Step 1b.

    Returns counts by bucket. Heavy work-up days surface as 'really happened'
    even when todo completion count is low.
    """
    today_str = today.strftime("%Y-%m-%d")
    # mtime cutoff: midnight at start of target date (local)
    cutoff = datetime.strptime(today_str, "%Y-%m-%d").timestamp()

    def count_modified_after(globpath: str) -> tuple[int, list]:
        n = 0
        paths = []
        for p in glob.glob(globpath, recursive=True):
            try:
                if os.path.getmtime(p) >= cutoff:
                    n += 1
                    paths.append(p)
            except OSError:
                pass
        return n, paths

    outputs_n, outputs_paths = count_modified_after(str(repo_root / "output" / "**" / "*.md"))
    reflections_n, reflections_paths = count_modified_after(
        str(repo_root / "data" / "reflections" / "*.md")
    )

    # Memory dir is in ~/.claude/projects/<sanitized-cwd>/memory/
    sanitized_cwd = "-" + str(repo_root).replace("/", "-").lstrip("-")
    memory_dir = Path.home() / ".claude" / "projects" / sanitized_cwd / "memory"
    memories_n, memories_paths = (0, [])
    if memory_dir.exists():
        memories_n, memories_paths = count_modified_after(str(memory_dir / "*.md"))
        # Exclude MEMORY.md (index) from count
        memories_paths = [p for p in memories_paths if not p.endswith("MEMORY.md")]
        memories_n = len(memories_paths)

    # Inbox entries today (count of '## YYYY-MM-DD' headers matching today)
    inbox = read_file(repo_root / "data" / "inbox.md")
    inbox_today_count = len(re.findall(rf"^##\s+{re.escape(today_str)}\b", inbox, re.MULTILINE))

    # Group outputs by entity (immediate parent dir)
    outputs_by_entity = {}
    for p in outputs_paths:
        rel = os.path.relpath(p, repo_root / "output")
        parts = rel.split(os.sep)
        entity = parts[0] if len(parts) > 1 else "_flat"
        outputs_by_entity.setdefault(entity, []).append(rel)

    return {
        "outputs_today": outputs_n,
        "outputs_by_entity": {k: len(v) for k, v in outputs_by_entity.items()},
        "outputs_paths_by_entity": outputs_by_entity,
        "reflections_today": reflections_n,
        "reflections_paths": [os.path.relpath(p, repo_root) for p in reflections_paths],
        "memories_today": memories_n,
        "memories_names": [os.path.basename(p).replace(".md", "") for p in memories_paths],
        "inbox_entries_today": inbox_today_count,
    }


def main():
    args = parse_args()

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()

    # Read all source files
    todos_content = read_file(repo_root / "data" / "job-todos.md")
    daily_log_content = read_file(repo_root / "data" / "job-todos-daily-log.md")
    pipeline_content = read_file(repo_root / "data" / "job-pipeline.md")
    networking_content = read_file(repo_root / "data" / "networking.md")
    outreach_content = read_file(repo_root / "data" / "outreach-log.md")
    changelog_content = read_file(repo_root / "docs" / "CHANGELOG.md")

    output_dir = repo_root / "output"

    # Process each source
    completed_today, active, overdue = parse_todos(todos_content, today)
    metrics = parse_daily_log(daily_log_content, today, today_completions_from_todos=len(completed_today))
    outreach_today_from_log = parse_outreach_today(outreach_content, today)
    networking_touches_today = parse_networking_touches_today(networking_content, today)

    # Union outreach signals: outreach-log entries + today's networking blockquotes
    # De-dupe by (name + channel) to avoid double-counting when networking_write.py
    # successfully synced to outreach-log.
    seen_keys = {(o.get("name", "").lower(), o.get("channel", "").lower())
                 for o in outreach_today_from_log}
    outreach_today = list(outreach_today_from_log)
    for touch in networking_touches_today:
        key = (touch["name"].lower(), touch["channel"].lower())
        if key not in seen_keys:
            outreach_today.append(touch)
            seen_keys.add(key)

    research_today = find_research_today(output_dir, today) if output_dir.exists() else []
    changelog_today = parse_changelog_today(changelog_content, today)
    pipeline = parse_pipeline(pipeline_content)
    upcoming_scheduled = parse_upcoming_scheduled(pipeline_content, today)
    networking = parse_networking_activity(networking_content, todos_content)
    substantive_work = scan_substantive_work(repo_root, today)

    result = {
        "target_date": today.strftime("%Y-%m-%d"),
        "completed_today": completed_today,
        "active_remaining": active,
        "overdue": overdue,
        "outreach_sent_today": outreach_today,
        "outreach_touches_today_from_networking": networking_touches_today,
        "research_completed_today": research_today,
        "changelog_today": changelog_today,
        "metrics": metrics,
        "pipeline_snapshot": pipeline,
        "upcoming_scheduled": upcoming_scheduled,
        "networking_activity": networking,
        "substantive_work": substantive_work,
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
