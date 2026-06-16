#!/usr/bin/env python3
"""Tests for the gmail-dedup additions to act_classify.detect_stale_routed.

Covers the 2026-06-02 fixes: transactional-notification auto-delete and
already-engaged-pipeline-company dedup, plus their false-positive guards.

Run:  PYTHONIOENCODING=utf-8 python3 tools/test_act_classify_stale.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import act_classify as ac  # noqa: E402


def _repo(pipeline_rows="", networking="") -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "data").mkdir()
    pipe = (
        "# Pipeline\n\n"
        "| Company | Role | Stage | Date Added | Notes |\n"
        "|---|---|---|---|---|\n" + pipeline_rows
    )
    (d / "data" / "job-pipeline.md").write_text(pipe, encoding="utf-8")
    (d / "data" / "networking.md").write_text(networking, encoding="utf-8")
    return d


def _gmail(subject, sender, body="Some content."):
    return (
        f"# Email: {subject}\n\n"
        f"> **From:** {sender}\n"
        f"> **Date:** Tue, 02 Jun 2026 13:45:37 -0700\n\n"
        f'<email-content source="gmail" sanitized="true">\n{body}\n</email-content>\n'
    )


PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


def reason(content, repo):
    r = ac.detect_stale_routed(content, repo)
    return r["reason"] if r else None


# ── Transactional notifications (auto-delete) ───────────────────────────────
repo = _repo("| Northwind | FDAS | Phone Screen | 2026-06-02 | x |\n")

c = _gmail("Candidate Vibe Check - Robin between Northwind and Nick",
           "Jordan Lee <hello@cal.com>", "Your event has been scheduled")
check("cal.com booking -> transactional", reason(c, repo) == "transactional_notification")

c = _gmail("Your application for Vertex - Deployment Strategist",
           "Y Combinator <workatastartup@ycombinator.com>",
           "Your application to Vertex has been received.")
check("YC receipt -> transactional", reason(c, repo) == "transactional_notification")

c = _gmail("Application received", "Greenhouse <no-reply@us.greenhouse-mail.io>",
           "We received your application.")
check("subject 'application received' -> transactional",
      reason(c, repo) == "transactional_notification")

# ── Already-engaged pipeline company (duplicate intro) ──────────────────────
c = _gmail("Nick & Robin (Northwind): referred on Talentbridge",
           "Casey Morgan <casey.morgan@talentbridge.com>",
           "I want to introduce you to Robin at Northwind.")
check("Talentbridge intro re engaged pipeline co -> company_already_in_pipeline",
      reason(c, repo) == "company_already_in_pipeline")

# ── False-positive guards ───────────────────────────────────────────────────
# Same company but only at Researching stage -> NOT stale (could be new/actionable)
repo_early = _repo("| Northwind | FDAS | Researching | 2026-06-02 | x |\n")
c = _gmail("Nick & Robin (Northwind): referred on Talentbridge",
           "Casey Morgan <casey.morgan@talentbridge.com>", "intro")
check("intro re Researching-stage co -> NOT stale", reason(c, repo_early) is None)

# Company not in pipeline at all -> NOT stale
c = _gmail("Intro to someone at Foobar Industries",
           "X <x@example.com>", "intro")
check("non-pipeline company -> NOT stale", reason(c, repo) is None)

# Short single-word company name, coincidental substring -> NOT stale
repo_short = _repo("| Pens | Ops | Applied | 2026-06-02 | x |\n")
c = _gmail("Re: your pens are ready", "X <x@example.com>", "hi")
check("short single-word co coincidence -> NOT stale", reason(c, repo_short) is None)

# Non-gmail content with a notification-ish phrase -> NOT transactional (gmail-only)
c = ("# Note\n\nReminder: the offsite has been scheduled for Friday.\n")
check("non-gmail 'has been scheduled' -> NOT stale", reason(c, repo) is None)

# Existing sender_already_in_networking still works
repo_net = _repo("", networking="Contact: jane@known.com logged 2026-05-01\n")
c = _gmail("Hello", "Jane <jane@known.com>", "hi")
check("sender in networking -> sender_already_in_networking",
      reason(c, repo_net) == "sender_already_in_networking")

print(f"\nact_classify stale tests: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
