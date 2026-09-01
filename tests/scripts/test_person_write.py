"""Tests for tools/person_write.py"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import TOOLS_DIR


def run_pw(*args, repo_root, dry_run=False):
    """Run person_write.py against an isolated repo root. Returns (dict, rc).

    Global flags (--repo-root, --dry-run) go before the subcommand, matching
    the repo convention.
    """
    script_path = TOOLS_DIR / "person_write.py"
    pre = ["--repo-root", str(repo_root)]
    if dry_run:
        pre.append("--dry-run")
    cmd = [sys.executable, str(script_path), *pre, *[str(a) for a in args]]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return json.loads(result.stdout), result.returncode


def people_dir(repo_root):
    return Path(repo_root) / "data" / "people"


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

def test_create_writes_file_with_filled_placeholders(tmp_path):
    res, rc = run_pw("create", "Priya Anand Fábrega",
                     "--company", "Acme AI", "--role", "Interviewer",
                     "--relationship", "interviewer", repo_root=tmp_path)
    assert rc == 0 and res["status"] == "ok" and res["action"] == "create"
    assert res["slug"] == "priya-anand-fabrega"
    f = people_dir(tmp_path) / "priya-anand-fabrega.md"
    assert f.exists()
    text = f.read_text(encoding="utf-8")
    assert text.startswith("# Priya Anand Fábrega")
    assert "**Company:** Acme AI" in text
    assert "**Role:** Interviewer" in text
    assert "**Relationship:** interviewer" in text
    assert "**Slug:** priya-anand-fabrega" in text
    # No unfilled placeholders remain.
    assert "{NAME}" not in text and "{COMPANY}" not in text and "{SLUG}" not in text


def test_create_accent_folding_slug(tmp_path):
    res, rc = run_pw("create", "Désirée Gútiérrez", repo_root=tmp_path)
    assert rc == 0 and res["slug"] == "desiree-gutierrez"


def test_create_is_idempotent_no_clobber(tmp_path):
    run_pw("create", "Priya Anand", "--company", "Acme AI", repo_root=tmp_path)
    f = people_dir(tmp_path) / "priya-anand.md"
    f.write_text(f.read_text(encoding="utf-8") + "\nHAND EDIT\n", encoding="utf-8")
    res, rc = run_pw("create", "Priya Anand", "--company", "Different",
                     repo_root=tmp_path)
    assert rc == 0 and res["action"] == "exists"
    # Hand edit preserved; not clobbered.
    assert "HAND EDIT" in f.read_text(encoding="utf-8")
    assert "**Company:** Acme AI" in f.read_text(encoding="utf-8")


def test_create_explicit_slug(tmp_path):
    res, rc = run_pw("create", "Bob", "--slug", "bob-the-recruiter",
                     repo_root=tmp_path)
    assert rc == 0 and res["slug"] == "bob-the-recruiter"
    assert (people_dir(tmp_path) / "bob-the-recruiter.md").exists()


def test_create_dry_run_writes_nothing(tmp_path):
    res, rc = run_pw("create", "Ghost Person", repo_root=tmp_path, dry_run=True)
    assert rc == 0 and res.get("dry_run") is True
    assert not (people_dir(tmp_path) / "ghost-person.md").exists()


# ---------------------------------------------------------------------------
# add-entry
# ---------------------------------------------------------------------------

def test_add_entry_appends_under_section(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    res, rc = run_pw("add-entry", "sam-carter", "--section", "commitments",
                     "--text", "Will intro to the hiring panel", "--date", "2026-06-01",
                     repo_root=tmp_path)
    assert rc == 0 and res["action"] == "add-entry"
    text = (people_dir(tmp_path) / "sam-carter.md").read_text(encoding="utf-8")
    assert "- 2026-06-01 — Will intro to the hiring panel" in text
    # The bullet is under the Commitments heading, not elsewhere.
    commit_idx = text.index("## Commitments")
    owe_idx = text.index("## What I Owe")
    bullet_idx = text.index("Will intro to the hiring panel")
    assert commit_idx < bullet_idx < owe_idx


def test_add_entry_newest_first(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    run_pw("add-entry", "sam-carter", "--section", "owed",
           "--text", "older item", "--date", "2026-05-01", repo_root=tmp_path)
    run_pw("add-entry", "sam-carter", "--section", "owed",
           "--text", "newer item", "--date", "2026-06-01", repo_root=tmp_path)
    text = (people_dir(tmp_path) / "sam-carter.md").read_text(encoding="utf-8")
    assert text.index("newer item") < text.index("older item")


def test_add_entry_each_section(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    for sec in ("commitments", "owed", "touchpoints"):
        res, rc = run_pw("add-entry", "sam-carter", "--section", sec,
                         "--text", f"x-{sec}", repo_root=tmp_path)
        assert rc == 0, sec
    text = (people_dir(tmp_path) / "sam-carter.md").read_text(encoding="utf-8")
    assert "x-commitments" in text and "x-owed" in text and "x-touchpoints" in text


def test_add_entry_fuzzy_resolve_by_partial_name(tmp_path):
    run_pw("create", "Priya Anand Fábrega", repo_root=tmp_path)
    res, rc = run_pw("add-entry", "Priya", "--section", "commitments",
                     "--text", "fuzzy hit", repo_root=tmp_path)
    assert rc == 0 and res["action"] == "add-entry"


def test_add_entry_ambiguous_errors(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    run_pw("create", "Sam Diaz", repo_root=tmp_path)
    res, rc = run_pw("add-entry", "Sam", "--section", "commitments",
                     "--text", "who?", repo_root=tmp_path)
    assert rc == 1 and res["code"] == "ambiguous_match"
    assert len(res["matches"]) == 2


def test_add_entry_not_found_errors(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    res, rc = run_pw("add-entry", "Nobody", "--section", "commitments",
                     "--text", "x", repo_root=tmp_path)
    assert rc == 1 and res["code"] == "not_found"


def test_add_entry_bad_section_errors(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    res, rc = run_pw("add-entry", "sam-carter", "--section", "bogus",
                     "--text", "x", repo_root=tmp_path)
    assert rc == 1 and res["code"] == "bad_section"


def test_add_entry_dry_run_writes_nothing(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    before = (people_dir(tmp_path) / "sam-carter.md").read_text(encoding="utf-8")
    res, rc = run_pw("add-entry", "sam-carter", "--section", "owed",
                     "--text", "phantom", repo_root=tmp_path, dry_run=True)
    assert rc == 0 and res.get("dry_run") is True
    after = (people_dir(tmp_path) / "sam-carter.md").read_text(encoding="utf-8")
    assert before == after


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_enumerates_dossiers(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    run_pw("create", "Alex Kim", repo_root=tmp_path)
    res, rc = run_pw("list", repo_root=tmp_path)
    assert rc == 0
    slugs = {d["slug"] for d in res["dossiers"]}
    assert {"sam-carter", "alex-kim"} <= slugs


def test_list_empty_when_no_dir(tmp_path):
    res, rc = run_pw("list", repo_root=tmp_path)
    assert rc == 0 and res["dossiers"] == []
