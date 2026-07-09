"""Tests for friction_log.py's keywords() tokenizer / dedup Jaccard precision.

Regression for a 2026-07-08 finding: keywords() tokenized full absolute repo
paths, so shared repo-structural segments (users/documents/obsidian/projects/
tools/search) dominated the Jaccard similarity between two UNRELATED
"file not found" errors, pushing distinct frictions above DEDUP_THRESHOLD and
silently over-merging them into one inflated row (confirmed 0.75 for two
different missing files both under tools/, vs. the 0.6 threshold).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

import friction_log as fl  # noqa: E402


def _jaccard(a: str, b: str) -> float:
    ka, kb = fl.keywords(a), fl.keywords(b)
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / len(ka | kb)


def test_repo_path_prefix_stripped_from_keywords():
    text = ("ls: /Users/mag/Documents/Obsidian/30-projects/job-search/tools/"
            "career_scanner/test_company_scorer.py: No such file or directory")
    kw = fl.keywords(text)
    assert "test_company_scorer" in kw
    # Repo-structural noise must not survive.
    for noise in ("users", "documents", "obsidian", "projects", "search"):
        assert noise not in kw


def test_two_distinct_missing_files_stay_below_dedup_threshold():
    a = ("ls: /Users/mag/Documents/Obsidian/30-projects/job-search/tools/"
         "career_scanner/test_company_scorer.py: No such file or directory")
    b = ("ls: /Users/mag/Documents/Obsidian/30-projects/job-search/tools/"
         "some_other_missing_script.py: No such file or directory")
    assert _jaccard(a, b) < fl.DEDUP_THRESHOLD


def test_genuinely_identical_error_still_matches():
    a = ("ls: /Users/mag/Documents/Obsidian/30-projects/job-search/tools/"
         "career_scanner/test_company_scorer.py: No such file or directory")
    assert _jaccard(a, a) == 1.0
