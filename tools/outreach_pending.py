#!/usr/bin/env python3
"""
outreach_pending.py — Pre-process outreach follow-up status for /standup and /weekly-review.

Reads: data/outreach-log.md, data/networking.md, data/job-pipeline.md

Cross-references networking.md Interaction Log to detect replies that were logged
via networking_write.py but not reflected in outreach-log.md status, and reads the
pipeline's company→stage map so outreach tied to a closed opportunity is suppressed
from the awaiting-response lists (still counted in stats).

Outputs JSON to stdout with keys:
  awaiting_response[]           — outreach with no reply, not yet overdue
  awaiting_response_overdue[]   — outreach with no reply, overdue (>=threshold days)
  recent_outreach{}             — {sent, replied, no_reply, response_rate_percent}

Usage:
  python3 tools/outreach_pending.py [--days-threshold-overdue N] [--lookback-days N]
                                   [--target-date YYYY-MM-DD] [--repo-root PATH]
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Shared freeform-stage classifier (single source of truth). See stage_vocab.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage_vocab import is_terminal_stage  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--days-threshold-overdue", type=int, default=5,
                   help="Days without reply before marking overdue (default: 5).")
    p.add_argument("--lookback-days", type=int, default=30,
                   help="Days to look back in outreach log (default: 30).")
    p.add_argument("--target-date", default=None,
                   help="Date to use as 'today' (YYYY-MM-DD). Defaults to actual today.")
    p.add_argument("--repo-root", default=None,
                   help="Repository root path. Defaults to cwd.")
    return p.parse_args()


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return ""


def reconcile_key(name: str, company: str) -> str:
    """Normalized reconciliation key: first name token + first company token.

    Tolerant to last-name drift (e.g. "Casey Doe" vs "Casey Doe-Smith") and
    company-label drift (e.g. "Acme" vs "Acme (BrandX)") that defeat exact-name
    matching. Origin: 2026-06-15 — a contact's logged reply went undetected
    because outreach-log.md and networking.md spelled the same person's name and
    company slightly differently, so the exact-name lookup never reconciled and
    the thread sat in awaiting-response. First-name + first-company-token still
    disambiguates collisions (e.g. two "Casey"s at ClosedCo vs LiveCo) without
    re-breaking on a surname suffix or a company parenthetical.
    """
    name_tok = name.strip().lower().lstrip("(").split()
    comp_tok = company.strip().lower().lstrip("(").split()
    first_name = name_tok[0] if name_tok else ""
    first_company = comp_tok[0] if comp_tok else ""
    return f"{first_name}|{first_company}"


def parse_networking_interactions(networking_content: str) -> dict[str, date | None]:
    """Parse networking.md Interaction Log to find most recent interaction date per contact.

    Returns {reconcile_key: latest_interaction_date}, keyed by the normalized
    first-name + first-company-token (see reconcile_key) so reconciliation against
    outreach-log.md tolerates last-name and company-label drift between the files.
    """
    interactions: dict[str, date | None] = {}
    lines = networking_content.splitlines()
    current_key = None

    for line in lines:
        # Match ### Name or ### Name — Company (company captured for the reconcile key)
        m = re.match(r"^###\s+(.+?)(?:\s+[—–]\s+(.+))?$", line)
        if m:
            current_key = reconcile_key(m.group(1), m.group(2) or "")
            continue

        if current_key is None:
            continue

        # Match #### YYYY-MM-DD | type | summary
        entry_match = re.match(r"^####\s+(\d{4}-\d{2}-\d{2})\s*\|", line)
        if entry_match:
            try:
                entry_date = datetime.strptime(entry_match.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            existing = interactions.get(current_key)
            if existing is None or entry_date > existing:
                interactions[current_key] = entry_date

    return interactions


# Subject markers for messages that do not expect a reply — thank-you notes and
# graceful closes. These are real outreach (counted in stats) but are not open
# loops, so they must not appear in the "awaiting response" list.
_NO_REPLY_EXPECTED_MARKERS = (
    "thank-you", "thank you", "thanks for", "thanks again",
    "graceful close", "stay-in-touch", "stay in touch",
)


def parse_pipeline_stages(content: str) -> dict[str, str]:
    """Parse job-pipeline.md table rows → {lowercase_company: stage}.

    On duplicate company rows, the last (most recent toward bottom) wins; callers
    only use this to test closed-ness, so any closed row marking the company closed
    is the intent. Returns {} for empty/missing content.
    """
    stages: dict[str, str] = {}
    for line in content.splitlines():
        if not line.startswith("|") or line.startswith("| Company") or line.startswith("|---"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3:
            continue
        company = cols[0].lower()
        stage = cols[2]
        if company and company != "---":
            stages[company] = stage
    return stages


def is_closed_stage(stage: str) -> bool:
    """True if a pipeline stage means the opportunity is over.

    Delegates to the shared classifier so outreach tied to a closed company (incl.
    freeform mid-string closes like "Founder intro complete (...) - declined") is
    suppressed from the awaiting-response lists, consistently with pipe_read /
    pipeline_staleness / networking_read."""
    return is_terminal_stage(stage)


# "Deprioritized"/paused is NOT terminal for the pipeline (it stays counted as active
# backlog — it can be revived), but for OUTREACH nagging a set-aside company is not an
# open loop, so its threads are suppressed from the awaiting lists here. This is an
# outreach-only concern, deliberately kept out of the shared is_terminal_stage — the
# old _CLOSED_STAGE_PREFIXES included "deprioritized"; this preserves that behavior.
_OUTREACH_SUPPRESS_KEYWORDS = ("deprioritized", "on hold", "paused")


def is_outreach_dormant_stage(stage: str) -> bool:
    """True if outreach to this company is not an open loop (closed OR set-aside)."""
    if is_terminal_stage(stage):
        return True
    s = (stage or "").lower()
    return any(k in s for k in _OUTREACH_SUPPRESS_KEYWORDS)


def is_no_reply_expected(subject: str) -> bool:
    """True if the outreach subject indicates a thank-you / close (no reply expected)."""
    s = subject.lower()
    return any(marker in s for marker in _NO_REPLY_EXPECTED_MARKERS)


def parse_outreach_log(content: str, today: date, lookback_days: int,
                       overdue_threshold: int,
                       networking_interactions: dict[str, date | None] | None = None,
                       pipeline_stages: dict[str, str] | None = None) -> dict:
    """Parse outreach-log.md table rows, cross-referencing networking.md for replies.

    Outreach tied to a closed pipeline company, or whose subject is a thank-you /
    graceful close, is still counted in the sent/replied stats but is suppressed
    from the awaiting-response lists (it is not an open loop).
    """
    cutoff = today - timedelta(days=lookback_days)
    if networking_interactions is None:
        networking_interactions = {}
    if pipeline_stages is None:
        pipeline_stages = {}

    sent_count = 0
    replied_count = 0
    no_reply_count = 0
    awaiting = []
    awaiting_overdue = []

    for line in content.splitlines():
        if not line.startswith("|") or line.startswith("| Date") or line.startswith("|---"):
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 6:
            continue

        date_str = cols[0]
        outreach_type = cols[1]
        channel = cols[2]
        name = cols[3]
        company = cols[4]
        subject = cols[5]
        status = cols[6] if len(cols) > 6 else "Drafted"

        try:
            sent_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if sent_date < cutoff:
            continue

        sent_count += 1

        # Cross-reference: if networking.md has an interaction AFTER the outreach date,
        # treat this as replied regardless of outreach-log status
        effective_status = status
        latest_interaction = networking_interactions.get(reconcile_key(name, company))
        if (latest_interaction is not None
                and latest_interaction > sent_date
                and effective_status.lower() in ("sent", "drafted", "pending")):
            effective_status = "Replied"

        # Suppress from the awaiting lists (but NOT from the stats) when the
        # opportunity is closed or the message expects no reply.
        suppress_awaiting = (
            is_outreach_dormant_stage(pipeline_stages.get(company.lower().strip(), ""))
            or is_no_reply_expected(subject)
        )

        if effective_status.lower() in ("replied", "reply"):
            replied_count += 1
        elif effective_status.lower() in ("no reply", "no_reply", "noreply"):
            no_reply_count += 1
            if suppress_awaiting:
                continue
            days_since = (today - sent_date).days
            entry = {
                "date": date_str,
                "name": name,
                "company": company,
                "channel": channel,
                "subject": subject,
                "days_since_sent": days_since,
            }
            if days_since >= overdue_threshold:
                awaiting_overdue.append(entry)
            else:
                awaiting.append(entry)
        elif effective_status.lower() in ("drafted", "sent", "pending"):
            if suppress_awaiting:
                continue
            days_since = (today - sent_date).days
            if days_since >= overdue_threshold:
                entry = {
                    "date": date_str,
                    "name": name,
                    "company": company,
                    "channel": channel,
                    "subject": subject,
                    "days_since_sent": days_since,
                    "status": status,
                }
                awaiting_overdue.append(entry)

    # Sort by most overdue first
    awaiting_overdue.sort(key=lambda e: -e["days_since_sent"])
    awaiting.sort(key=lambda e: -e["days_since_sent"])

    # Response rate = replies / total sent within the lookback window. An earlier
    # version divided by (sent - replied), which is not a rate — it inflated the
    # number badly (14 replied / 29 sent reported as 93% instead of the honest 48%).
    response_rate = round((replied_count / sent_count) * 100) if sent_count > 0 else None

    # Plausibility invariants — surface internal inconsistency instead of letting a
    # confident wrong number ship silently (origin: 2026-06-02, the 93% rate bug
    # passed a test that encoded the wrong formula; consumers should flag, not trust).
    sanity_warnings = []
    if replied_count > sent_count:
        sanity_warnings.append(f"replied ({replied_count}) > sent ({sent_count})")
    if replied_count + no_reply_count > sent_count:
        sanity_warnings.append(
            f"replied+no_reply ({replied_count + no_reply_count}) > sent ({sent_count})")
    if response_rate is not None and not (0 <= response_rate <= 100):
        sanity_warnings.append(f"response_rate {response_rate}% outside [0,100]")

    return {
        "awaiting_response": awaiting,
        "awaiting_response_overdue": awaiting_overdue,
        "recent_outreach": {
            "sent": sent_count,
            "replied": replied_count,
            "no_reply": no_reply_count,
            "response_rate_percent": response_rate,
            "sanity_warnings": sanity_warnings,
        },
    }


def main():
    args = parse_args()

    today = (datetime.strptime(args.target_date, "%Y-%m-%d").date()
             if args.target_date else date.today())

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
    outreach_content = read_file(repo_root / "data" / "outreach-log.md")
    networking_content = read_file(repo_root / "data" / "networking.md")
    pipeline_content = read_file(repo_root / "data" / "job-pipeline.md")

    # Build interaction map from networking.md and company→stage map from the pipeline
    networking_interactions = parse_networking_interactions(networking_content)
    pipeline_stages = parse_pipeline_stages(pipeline_content)

    result = parse_outreach_log(
        outreach_content,
        today,
        lookback_days=args.lookback_days,
        overdue_threshold=args.days_threshold_overdue,
        networking_interactions=networking_interactions,
        pipeline_stages=pipeline_stages,
    )
    result["target_date"] = today.strftime("%Y-%m-%d")

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
