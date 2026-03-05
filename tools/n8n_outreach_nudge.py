#!/usr/bin/env python3
"""
n8n_outreach_nudge.py — Write inbox nudge if overdue follow-ups exist.

Run by n8n "Follow-up Nudge + Dossier Freshness" workflow daily at 9am.
Delegates to outreach_pending.py; writes an inbox/ item only when there
are overdue contacts so /act picks it up next session.

Usage:
  PYTHONIOENCODING=utf-8 python tools/n8n_outreach_nudge.py [--repo-root PATH]
"""
import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=None)
    args = p.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).parent.parent

    result = subprocess.run(
        [sys.executable, str(repo_root / "tools" / "outreach_pending.py"),
         "--repo-root", str(repo_root)],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"ERROR running outreach_pending.py: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(result.stdout)
    overdue = data.get("awaiting_response_overdue", [])

    if not overdue:
        print("No overdue follow-ups — skipping inbox write.")
        return

    today = date.today()
    inbox = repo_root / "inbox"
    inbox.mkdir(exist_ok=True)

    lines = [
        f"# Follow-up Nudge — {today.isoformat()}",
        "",
        f"**{len(overdue)} overdue follow-up(s)** flagged by n8n automation.",
        "",
        "## Overdue Follow-ups",
        "",
    ]
    for item in overdue:
        name = item.get("name", "Unknown")
        days = item.get("days_since_outreach", "?")
        last = item.get("last_outreach_date", "?")
        lines.append(f"- **{name}** — {days} days since last contact ({last})")

    lines += ["", "→ Run `/follow-up <name>` to draft a follow-up message.", ""]

    out_path = inbox / f"{today.strftime('%Y%m%d')}-follow-up-nudge.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path.name}")


if __name__ == "__main__":
    main()
