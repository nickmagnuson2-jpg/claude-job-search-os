"""Tests for tools/act_classify.py"""
import textwrap
from pathlib import Path

import pytest

from conftest import run_script


def write_fixture(tmp_path: Path, filename: str, content: str) -> None:
    dest = tmp_path / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(textwrap.dedent(content), encoding="utf-8")


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
