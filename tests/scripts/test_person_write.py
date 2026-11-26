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


@pytest.mark.parametrize("section", ["Touchpoints", "TOUCHPOINTS", "touchpoints"])
def test_add_entry_section_is_case_insensitive(tmp_path, section):
    """Friction row: "Unknown section: 'Touchpoints'" while the dossier heading and the
    error message both name it. The section name matches regardless of case."""
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    res, rc = run_pw("add-entry", "sam-carter", "--section", section,
                     "--text", "Coffee chat", "--date", "2026-06-02", repo_root=tmp_path)
    assert rc == 0 and res["action"] == "add-entry", res
    text = (people_dir(tmp_path) / "sam-carter.md").read_text(encoding="utf-8")
    assert text.index("- 2026-06-02 — Coffee chat") > text.index("## Touchpoints")


def test_add_entry_unknown_section_still_refused(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    res, rc = run_pw("add-entry", "sam-carter", "--section", "Notes",
                     "--text", "x", repo_root=tmp_path)
    assert rc != 0 and res["code"] == "bad_section"


# ---------------------------------------------------------------------------
# S6c (2026-11-25): behaviors the whole-file mutation run showed no test observed
# ---------------------------------------------------------------------------

def _dossier(tmp_path, slug="sam-carter"):
    return (people_dir(tmp_path) / f"{slug}.md").read_text(encoding="utf-8")


def test_add_entry_lands_directly_under_the_sections_comment_marker(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    run_pw("add-entry", "sam-carter", "--section", "touchpoints", "--text", "first",
           "--date", "2026-06-01", repo_root=tmp_path)
    lines = _dossier(tmp_path).splitlines()
    head = lines.index("## Touchpoints")
    marker = next(i for i in range(head, len(lines)) if lines[i].startswith("<!--"))
    assert lines[marker + 1] == "- 2026-06-01 — first"


def test_add_entry_without_a_marker_stays_in_its_own_section(tmp_path):
    """A section with no comment marker takes the bullet right under its heading, never
    under a later section's marker."""
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    p = people_dir(tmp_path) / "sam-carter.md"
    text = p.read_text(encoding="utf-8").splitlines()
    head = text.index("## Touchpoints")
    marker = next(i for i in range(head, len(text)) if text[i].startswith("<!--"))
    del text[marker]
    text.append("<!-- a later section's marker -->")
    p.write_text("\n".join(text) + "\n", encoding="utf-8")
    run_pw("add-entry", "sam-carter", "--section", "touchpoints", "--text", "x",
           "--date", "2026-06-01", repo_root=tmp_path)
    lines = _dossier(tmp_path).splitlines()
    assert lines[lines.index("## Touchpoints") + 1] == "- 2026-06-01 — x"


@pytest.mark.parametrize("trailing", [True, False])
def test_add_entry_keeps_the_files_trailing_newline_state(tmp_path, trailing):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    p = people_dir(tmp_path) / "sam-carter.md"
    body = p.read_text(encoding="utf-8").rstrip("\n")
    p.write_text(body + ("\n" if trailing else ""), encoding="utf-8")
    run_pw("add-entry", "sam-carter", "--section", "owed", "--text", "x", repo_root=tmp_path)
    assert p.read_text(encoding="utf-8").endswith("\n") is trailing


def test_add_entry_refuses_a_dossier_missing_the_sections_heading(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    p = people_dir(tmp_path) / "sam-carter.md"
    p.write_text(p.read_text(encoding="utf-8").replace("## What I Owe", "## Owed (renamed)"),
                 encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    res, rc = run_pw("add-entry", "sam-carter", "--section", "owed", "--text", "x",
                     repo_root=tmp_path)
    assert rc != 0 and res["code"] == "missing_section"
    assert p.read_text(encoding="utf-8") == before


def test_an_exact_slug_wins_over_a_longer_fuzzy_match(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    run_pw("create", "Sam Carter Jr", repo_root=tmp_path)
    res, rc = run_pw("add-entry", "sam-carter", "--section", "owed", "--text", "x",
                     repo_root=tmp_path)
    assert rc == 0 and res["path"].endswith("sam-carter.md")


def test_a_slug_fragment_resolves_one_dossier(tmp_path):
    """Matched on the slug ("sam-c" is not in the title "Sam Carter")."""
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    res, rc = run_pw("add-entry", "sam-c", "--section", "owed", "--text", "x",
                     repo_root=tmp_path)
    assert rc == 0 and res["path"].endswith("sam-carter.md")


def test_a_title_fragment_with_no_slug_resolves_one_dossier(tmp_path):
    """A reference whose slug is empty ("Σ") can still match the dossier title."""
    run_pw("create", "Σ Person", repo_root=tmp_path)
    res, rc = run_pw("add-entry", "Σ", "--section", "owed", "--text", "x",
                     repo_root=tmp_path)
    assert rc == 0, res
    assert res["path"].endswith("person.md")


def test_list_names_each_dossier_by_its_title_or_slug(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    (people_dir(tmp_path) / "no-title.md").write_text("no heading here\n", encoding="utf-8")
    res, rc = run_pw("list", repo_root=tmp_path)
    names = {d["slug"]: d["name"] for d in res["dossiers"]}
    assert rc == 0 and names == {"sam-carter": "Sam Carter", "no-title": "no-title"}


def test_list_with_no_people_directory_is_empty(tmp_path):
    res, rc = run_pw("list", repo_root=tmp_path)
    assert rc == 0 and res["dossiers"] == []


def test_create_refuses_a_name_with_no_slug(tmp_path):
    res, rc = run_pw("create", "!!!", repo_root=tmp_path)
    assert rc != 0 and res["code"] == "bad_slug"
    assert not people_dir(tmp_path).exists() or not any(people_dir(tmp_path).iterdir())


def test_no_subcommand_is_a_usage_error(tmp_path):
    res, rc = run_pw(repo_root=tmp_path)
    assert rc != 0 and res["status"] == "error" and res["message"].startswith("Usage:")


def test_the_module_imports_without_running_main():
    """Importing person_write (as a sibling or as tools.person_write) runs nothing."""
    import subprocess as sp
    for code in ("import sys; sys.path.insert(0, 'tools'); import person_write",
                 "import tools.person_write"):
        r = sp.run([sys.executable, "-c", code], capture_output=True, text=True,
                   cwd=str(TOOLS_DIR.parent), env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        assert r.returncode == 0 and r.stdout == "", (code, r.stdout, r.stderr)


def test_add_entry_dry_run_refuses_a_dossier_missing_the_sections_heading(tmp_path):
    """1478.F3: the dry run returned "Would append" before the missing_section refusal
    the real add-entry makes (test above), so a preview said yes to a refused write."""
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    p = people_dir(tmp_path) / "sam-carter.md"
    p.write_text(p.read_text(encoding="utf-8").replace("## What I Owe", "## Owed (renamed)"),
                 encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    res, rc = run_pw("add-entry", "sam-carter", "--section", "owed", "--text", "x",
                     repo_root=tmp_path, dry_run=True)
    assert rc == 1 and res["code"] == "missing_section", res
    assert p.read_text(encoding="utf-8") == before


def test_add_entry_dry_run_previews_the_bullet_and_writes_nothing(tmp_path):
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    p = people_dir(tmp_path) / "sam-carter.md"
    before = p.read_text(encoding="utf-8")
    res, rc = run_pw("add-entry", "sam-carter", "--section", "owed", "--text", "lunch",
                     "--date", "2026-11-26", repo_root=tmp_path, dry_run=True)
    assert rc == 0 and res["dry_run"] is True and res["section"] == "owed", res
    assert res["summary"] == "Would append to owed: - 2026-11-26 — lunch"
    assert p.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("rename", [False, True], ids=["accepted", "missing-section"])
def test_add_entry_dry_run_and_real_give_the_same_verdict(tmp_path, rename):
    """Parity: the dry run's exit code and error code match the real add-entry's."""
    run_pw("create", "Sam Carter", repo_root=tmp_path)
    p = people_dir(tmp_path) / "sam-carter.md"
    if rename:
        p.write_text(p.read_text(encoding="utf-8").replace("## What I Owe", "## Owed"),
                     encoding="utf-8")
    argv = ("add-entry", "sam-carter", "--section", "owed", "--text", "x")
    dry, drc = run_pw(*argv, repo_root=tmp_path, dry_run=True)
    real, rrc = run_pw(*argv, repo_root=tmp_path)
    assert (drc, dry.get("code")) == (rrc, real.get("code")), (dry, real)
