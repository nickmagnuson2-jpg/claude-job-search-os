"""Tests for tools/act_classify.py"""
from pathlib import Path

import pytest

from conftest import run_script, write_fixture


def test_empty_todos_and_inbox(tmp_path):
    """No todos or inbox items → all buckets empty, no crash."""
    write_fixture(tmp_path, "data/job-todos.md", """\
        # Job Todos

        ## Active
        | Task | Priority | Due | Status | Notes |
        |------|----------|-----|--------|-------|
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert result["bucket_a"] == []
    assert result["bucket_b"] == []
    assert result["inbox_items"] == []


def test_blocked_todo_goes_to_bucket_b(tmp_path):
    """A Pending todo whose notes contain 'access blocked' routes to Bucket B with blocked=True."""
    write_fixture(tmp_path, "data/job-todos.md", """\
        # Job Todos

        ## Active
        | Task | Priority | Due | Status | Notes |
        |------|----------|-----|--------|-------|
        | Check Acme careers | High | 2026-03-01 | Pending | access blocked — check manually |
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert len(result["bucket_b"]) == 1
    assert result["bucket_b"][0]["blocked"] is True
    assert result["bucket_a"] == []


def test_fresh_careers_checked_skipped(tmp_path):
    """A todo with 'Checked 2026-02-25' (3 days before target) skips with recheck date."""
    write_fixture(tmp_path, "data/job-todos.md", """\
        # Job Todos

        ## Active
        | Task | Priority | Due | Status | Notes |
        |------|----------|-----|--------|-------|
        | Check Acme careers | High | 2026-03-01 | Pending | Checked 2026-02-25 |
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert len(result["skipped_fresh_careers"]) == 1
    assert result["skipped_fresh_careers"][0]["checked_date"] == "2026-02-25"
    assert result["bucket_a"] == []
    assert result["bucket_b"] == []


def test_careers_check_in_bucket_a(tmp_path):
    """A 'Check <Company> careers' todo routes to Bucket A with type=careers_check."""
    write_fixture(tmp_path, "data/job-todos.md", """\
        # Job Todos

        ## Active
        | Task | Priority | Due | Status | Notes |
        |------|----------|-----|--------|-------|
        | Check Stripe careers | High | 2026-03-01 | Pending | — |
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert len(result["bucket_a"]) == 1
    assert result["bucket_a"][0]["type"] == "careers_check"


def test_article_read_in_bucket_a(tmp_path):
    """A 'Read ...' todo with a URL in notes routes to Bucket A with type=article_read."""
    write_fixture(tmp_path, "data/job-todos.md", """\
        # Job Todos

        ## Active
        | Task | Priority | Due | Status | Notes |
        |------|----------|-----|--------|-------|
        | Read climate tech overview | Med | 2026-03-01 | Pending | https://techcrunch.com/2026/01/15/climate |
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert len(result["bucket_a"]) == 1
    assert result["bucket_a"][0]["type"] == "article_read"


def test_inbox_job_ad_greenhouse(tmp_path):
    """An inbox file with a greenhouse.io URL classifies as job_ad with slug extracted."""
    write_fixture(tmp_path, "inbox/acme-job.md", """\
        Check out this engineering role at Acme:
        https://boards.greenhouse.io/acme/jobs/123456
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert len(result["inbox_items"]) == 1
    item = result["inbox_items"][0]
    assert item["type"] == "job_ad"
    assert item["company_slug"] == "acme"


def test_inbox_contact_capture(tmp_path):
    """An inbox file with a capitalized full name and 'met' context classifies as contact_capture."""
    write_fixture(tmp_path, "inbox/contact.md", """\
        met Sarah Chen at the networking event last night. She works at Google.
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert len(result["inbox_items"]) == 1
    assert result["inbox_items"][0]["type"] == "contact_capture"


def test_inbox_article_media_domain(tmp_path):
    """An inbox file with a TechCrunch URL classifies as article."""
    write_fixture(tmp_path, "inbox/article.md", """\
        https://techcrunch.com/2026/01/15/ai-startup-raises-series-b
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-02-28",
                        "--repo-root", str(tmp_path))
    assert len(result["inbox_items"]) == 1
    assert result["inbox_items"][0]["type"] == "article"


# ---------------------------------------------------------------------------
# Tests: sender_already_in_networking must NOT swallow replies
#
# Origin 2026-08-25: a cold email got a reply in 20 minutes. The contact had been
# logged to networking.md an hour earlier, so detect_stale_routed matched the
# sender and flagged the REPLY stale — and /act auto-deletes stale inbox files
# with no approval. The rule "contact already logged, so re-routing duplicates"
# holds for a first capture and is false for every reply. The more responsive the
# outreach, the more likely the response is destroyed.
# ---------------------------------------------------------------------------

_NETWORKING_WITH_CONTACT = """\
# Networking

| Name | Company | Role | Relationship | First Contact | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Jane Doe | ExampleCo | Commercial Lead | target | 2026-02-01 | 2026-02-01 | jane@example.com |
"""


def test_reply_from_known_contact_is_not_stale(tmp_path):
    """An inbound REPLY from an already-logged contact is new information, not a duplicate."""
    write_fixture(tmp_path, "data/networking.md", _NETWORKING_WITH_CONTACT)
    write_fixture(tmp_path, "inbox/reply.md", """\
        # Email: Re: Interested in the Deployments role

        > **From:** Jane Doe <jane@example.com>
        > **Date:** Tue, 25 Aug 2026 11:48:39 -0700

        <email-content source="gmail" sanitized="true">
        Thanks for reaching out. Grab any time here: https://cal.com/example/30min

        On Tue, Aug 25, 2026 at 11:28 AM Test User <test@example.com> wrote:

        > Hi Jane,
        >
        > Saw the role and wanted to reach out.
        </email-content>
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-08-25",
                        "--repo-root", str(tmp_path))
    item = result["inbox_items"][0]
    assert item.get("stale") is not True, (
        f"a reply must never be auto-deleted; got stale_reason={item.get('stale_reason')}"
    )


def test_first_capture_from_known_contact_is_still_stale(tmp_path):
    """The original guard must survive: a NON-reply from a logged contact stays stale."""
    write_fixture(tmp_path, "data/networking.md", _NETWORKING_WITH_CONTACT)
    write_fixture(tmp_path, "inbox/capture.md", """\
        # Email: Quick intro

        > **From:** Jane Doe <jane@example.com>
        > **Date:** Tue, 25 Aug 2026 09:00:00 -0700

        <email-content source="gmail" sanitized="true">
        Hi Nick, wanted to introduce myself. I lead commercial at ExampleCo.
        </email-content>
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-08-25",
                        "--repo-root", str(tmp_path))
    item = result["inbox_items"][0]
    assert item.get("stale") is True
    assert item.get("stale_reason") == "sender_already_in_networking"


def test_re_subject_alone_marks_a_reply(tmp_path):
    """A 'Re:' subject is sufficient — quoted-body detection is not the only signal."""
    write_fixture(tmp_path, "data/networking.md", _NETWORKING_WITH_CONTACT)
    write_fixture(tmp_path, "inbox/reply2.md", """\
        # Email: RE: the role we discussed

        > **From:** Jane Doe <jane@example.com>
        > **Date:** Tue, 25 Aug 2026 12:00:00 -0700

        <email-content source="gmail" sanitized="true">
        Sounds good, talk then.
        </email-content>
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-08-25",
                        "--repo-root", str(tmp_path))
    assert result["inbox_items"][0].get("stale") is not True


# ---------------------------------------------------------------------------
# Tests: the user's OWN sent copies are stale regardless of reply status
#
# Origin 2026-08-25: exempting replies from sender_already_in_networking (above)
# correctly saved inbound replies, but also un-staled the user's own sent copies,
# since those quote the thread too. Own sent mail is never routable — it is a
# carbon copy of something the user already did. This rule restores the noise
# reduction WITHOUT weakening the reply exemption, so it is checked first and is
# NOT gated on is_reply_email.
#
# The user's address is read from gitignored data/profile.md at runtime; it is
# never hardcoded, because tools/ and tests/ are public.
# ---------------------------------------------------------------------------

_PROFILE_WITH_EMAIL = """\
# Profile

- **Name:** Test User
- **E-Mail:** test@example.com
"""


def test_own_sent_copy_is_stale_even_when_a_reply(tmp_path):
    """Mail FROM the user is a carbon copy of their own action — never routable."""
    write_fixture(tmp_path, "data/profile.md", _PROFILE_WITH_EMAIL)
    write_fixture(tmp_path, "inbox/own.md", """\
        # Email: Re: Interested in the Deployments role

        > **From:** Test User <test@example.com>
        > **Date:** Tue, 25 Aug 2026 11:28:00 -0700

        <email-content source="gmail" sanitized="true">
        Hi Jane, saw the role and wanted to reach out.

        On Tue, Aug 25, 2026 at 9:00 AM Jane Doe <jane@example.com> wrote:

        > Original message.
        </email-content>
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-08-25",
                        "--repo-root", str(tmp_path))
    item = result["inbox_items"][0]
    assert item.get("stale") is True
    assert item.get("stale_reason") == "own_sent_copy"


def test_inbound_reply_still_survives_the_own_sent_rule(tmp_path):
    """Regression guard: the new rule must not re-break the reply exemption."""
    write_fixture(tmp_path, "data/profile.md", _PROFILE_WITH_EMAIL)
    write_fixture(tmp_path, "data/networking.md", _NETWORKING_WITH_CONTACT)
    write_fixture(tmp_path, "inbox/inbound.md", """\
        # Email: Re: Interested in the Deployments role

        > **From:** Jane Doe <jane@example.com>
        > **Date:** Tue, 25 Aug 2026 11:48:00 -0700

        <email-content source="gmail" sanitized="true">
        Thanks for reaching out. Grab any time here: https://cal.com/example/30min

        On Tue, Aug 25, 2026 at 11:28 AM Test User <test@example.com> wrote:

        > Hi Jane, saw the role.
        </email-content>
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-08-25",
                        "--repo-root", str(tmp_path))
    assert result["inbox_items"][0].get("stale") is not True


def test_no_profile_email_does_not_crash(tmp_path):
    """Missing/unparseable profile.md must degrade gracefully, never fail the run."""
    write_fixture(tmp_path, "inbox/own2.md", """\
        # Email: Re: something

        > **From:** Test User <test@example.com>
        > **Date:** Tue, 25 Aug 2026 11:28:00 -0700

        <email-content source="gmail" sanitized="true">
        body
        </email-content>
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-08-25",
                        "--repo-root", str(tmp_path))
    assert len(result["inbox_items"]) == 1


def test_quoted_attribution_alone_marks_a_reply(tmp_path):
    """A reply whose subject was REWRITTEN (no 'Re:') is still a reply.

    Kills a mutant found 2026-08-25: every other reply fixture carried both a
    'Re:' subject AND a quoted attribution, so the subject check short-circuited
    and the quoted-attribution fallback could be made a no-op with no test
    failing. This is the case the fallback exists for.
    """
    write_fixture(tmp_path, "data/networking.md", _NETWORKING_WITH_CONTACT)
    write_fixture(tmp_path, "inbox/renamed.md", """\
        # Email: booking a time

        > **From:** Jane Doe <jane@example.com>
        > **Date:** Tue, 25 Aug 2026 11:48:00 -0700

        <email-content source="gmail" sanitized="true">
        Grab any time here: https://cal.com/example/30min

        On Tue, Aug 25, 2026 at 11:28 AM Test User <test@example.com> wrote:

        > Hi Jane, saw the role and wanted to reach out.
        </email-content>
    """)
    result = run_script("act_classify.py",
                        "--target-date", "2026-08-25",
                        "--repo-root", str(tmp_path))
    item = result["inbox_items"][0]
    assert item.get("stale") is not True, (
        f"reply detected only by quoted attribution; got {item.get('stale_reason')}"
    )
