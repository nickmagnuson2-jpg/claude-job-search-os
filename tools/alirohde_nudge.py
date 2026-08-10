#!/usr/bin/env python3
"""
alirohde_nudge.py — Write an inbox triage nudge when a new Ali Rohde Jobs
edition has landed (deterministic detect + extract; no scoring).

"Ali Rohde Jobs" is a weekly Substack jobs newsletter (alirohdejobs@substack.com)
heavy on Chief of Staff / Strategic Operations roles — Nick's target shape.
gmail_fetch.py already drops each edition into inbox/. This script runs daily
(cheap-check): it no-ops unless a NEW edition number is present, then writes a
review-gated nudge pointing at /scan-jobs (which does profile-fit scoring and
keeps its own Y/N pipeline gate). Scoring is deliberately NOT done here —
deterministic detection only; LLM judgment stays in /scan-jobs.

State: tools/.alirohde_state.json  {"last_nudged_edition": <int>}
Only ever nudges the newest unprocessed edition (older ones marked seen, no
spam — consistent with the stale-nudge norms in act_classify.py).

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/alirohde_nudge.py [--repo-root PATH] [--force]
"""
import argparse
import json
import re
from datetime import date
from pathlib import Path

EDITION_RE = re.compile(r"edition-(\d+)", re.IGNORECASE)
NAME_RE = re.compile(r"ali[-]?rohde", re.IGNORECASE)
URL_RE = re.compile(r"https://alirohdejobs\.substack\.com/p/[^\s)\]]+", re.IGNORECASE)
REDIRECT_RE = re.compile(r"\s*\[\s*https://substack\.com/redirect[^\]]*\]\s*", re.IGNORECASE)


def find_editions(inbox: Path):
    """Return {edition_int: Path} for every Ali Rohde edition file in inbox/."""
    editions = {}
    for f in inbox.glob("*.md"):
        name = f.name.lower()
        if not NAME_RE.search(name):
            continue
        m = EDITION_RE.search(name)
        if not m:
            continue
        ed = int(m.group(1))
        # Prefer a FULL copy over a truncated one if both exist for an edition.
        if ed not in editions or "full" in name:
            editions[ed] = f
    return editions


def extract_roles(text: str, limit: int = 25):
    """Heuristic: role lines are the ones carrying a substack redirect link.
    Returns a list of cleaned 'Role — Company / context' strings (preview only;
    the local copy is often truncated — the full list lives at the URL)."""
    roles = []
    for raw in text.splitlines():
        line = raw.strip()
        if "substack.com/redirect" not in line:
            continue
        cleaned = REDIRECT_RE.sub("", line)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).replace(" ,", ",").strip(" —,\t")
        if not cleaned or len(cleaned) <= 3:
            continue
        # Drop a line truncated mid-redirect (no closing bracket → URL survives).
        if "substack.com/redirect" in cleaned or "://" in cleaned:
            continue
        low = cleaned.lower()
        if (low.startswith(("welcome", "if you", "view this post", "join "))
                or "fill out the form" in low or "subscrib" in low):
            continue
        roles.append(cleaned)
        if len(roles) >= limit:
            break
    return roles


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=None)
    p.add_argument("--force", action="store_true",
                   help="Re-nudge the newest edition even if already nudged.")
    args = p.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).parent.parent
    inbox = repo_root / "inbox"
    state_path = repo_root / "tools" / ".alirohde_state.json"

    if not inbox.exists():
        print("No inbox/ directory — skipping.")
        return

    editions = find_editions(inbox)
    if not editions:
        print("No Ali Rohde editions in inbox/ — skipping.")
        return

    newest_ed = max(editions)
    newest_file = editions[newest_ed]

    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
    last_nudged = int(state.get("last_nudged_edition", 0))

    if newest_ed <= last_nudged and not args.force:
        print(f"Newest edition {newest_ed} already nudged (state={last_nudged}) — no-op.")
        return

    text = newest_file.read_text(encoding="utf-8", errors="replace")
    url_m = URL_RE.search(text)
    edition_url = (url_m.group(0) if url_m
                   else f"https://alirohdejobs.substack.com/p/edition-{newest_ed}-ali-rohde-jobs")
    roles = extract_roles(text)

    today = date.today()
    lines = [
        f"# Ali Rohde Jobs — Edition {newest_ed} triage — {today.isoformat()}",
        "",
        f"**New Ali Rohde Jobs edition detected** (Edition {newest_ed}). "
        "Weekly curated jobs newsletter, CoS / Strategic-Ops heavy — Nick's target shape.",
        "",
        f"- Local copy: `inbox/{newest_file.name}` (often truncated mid-list)",
        f"- Full edition: {edition_url}",
        "",
        "## Roles (preview — local copy may be partial)",
        "",
    ]
    if roles:
        lines += [f"- {r}" for r in roles]
    else:
        lines.append("- (none extracted from local copy — open the full edition URL)")
    lines += [
        "",
        "## Next action (review-gated)",
        "",
        f"→ Run `/scan-jobs {edition_url}` — it scores each role against "
        "`data/profile.md`, dedupes against its cache, and asks Y/N before "
        "adding any role to `data/job-pipeline.md` (profile guard preserved).",
        "",
        "Use the URL, not the local copy, so the full role list is scored.",
        "",
    ]

    out_path = inbox / f"{today.strftime('%Y%m%d')}-alirohde-edition-{newest_ed}-triage.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")

    state["last_nudged_edition"] = newest_ed
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {out_path.name} (edition {newest_ed}, {len(roles)} role(s) previewed)")


if __name__ == "__main__":
    main()
