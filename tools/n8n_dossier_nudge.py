#!/usr/bin/env python3
"""
n8n_dossier_nudge.py — Write inbox nudge if stale research dossiers exist.

Run by n8n "Follow-up Nudge + Dossier Freshness" workflow daily at 9am.
Delegates to dossier_freshness.py; writes an inbox/ item only when there
are dossiers older than the staleness threshold so /act picks it up.

Usage:
  PYTHONIOENCODING=utf-8 python tools/n8n_dossier_nudge.py [--repo-root PATH]
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
        [sys.executable, str(repo_root / "tools" / "dossier_freshness.py"),
         "--repo-root", str(repo_root)],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"ERROR running dossier_freshness.py: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(result.stdout)
    stale = data.get("staleness_alerts", [])

    if not stale:
        print("No stale dossiers — skipping inbox write.")
        return

    today = date.today()
    inbox = repo_root / "inbox"
    inbox.mkdir(exist_ok=True)

    lines = [
        f"# Dossier Freshness Alert — {today.isoformat()}",
        "",
        f"**{len(stale)} stale dossier(s)** (>30 days old) flagged by n8n automation.",
        "",
        "## Stale Dossiers",
        "",
    ]
    for item in stale:
        slug = item.get("slug", "unknown")
        days = item.get("days_old", "?")
        last = item.get("last_updated", "unknown")
        lines.append(f"- **{slug}** — {days} days old (last updated {last})")

    lines += ["", "→ Run `/research-company <name>` to refresh a dossier.", ""]

    out_path = inbox / f"{today.strftime('%Y%m%d')}-dossier-freshness-alert.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path.name}")


if __name__ == "__main__":
    main()
