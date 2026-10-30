"""pipe_write.py: whole-row and whole-file checks, written from the 2026-10-30 mutation run.

That run left 36 survivors. Most share one cause: the fixtures carry "—" in every cell,
so a mutant that drops a column (writes "—" instead of the real value) is invisible. These
fixtures use a distinct value in every cell and compare whole rows or whole files.
"""
import json
import os
import subprocess
import sys
from datetime import date

import pytest

from conftest import REPO_ROOT, TOOLS_DIR, write_fixture

sys.path.insert(0, str(TOOLS_DIR))
import pipe_write as pw  # noqa: E402

TODAY = date.today().isoformat()
HEAD = "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |"
SEP = "| --- | --- | --- | --- | --- | --- | --- | --- |"
ACME = "| Acme Corp | PM | Applied | 2026-01-02 | Follow up Fri | cv-acme.pdf | Met at event | https://acme.example/job |"
BETA = "| Beta Inc | PM | Interviewing | 2026-01-03 | Prep | cv-beta.pdf | Warm intro | https://beta.example/job |"
WITH_ARCHIVE = f"# Pipeline\n\n## Active\n\n{HEAD}\n{SEP}\n{ACME}\n{BETA}\n\n## Archived\n\n{HEAD}\n{SEP}\n"
NO_ARCHIVE = f"# Pipeline\n\n## Active\n\n{HEAD}\n{SEP}\n{ACME}\n{BETA}\n"


def run(tmp_path, *args):
    r = subprocess.run([sys.executable, str(TOOLS_DIR / "pipe_write.py"), "--repo-root", str(tmp_path), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
    return json.loads(r.stdout.strip().splitlines()[-1]), r.returncode


def pipe(tmp_path, text):
    return write_fixture(tmp_path, "data/job-pipeline.md", text)


# --- helpers --------------------------------------------------------------------------

@pytest.mark.parametrize("line,want", [
    (ACME, True), ("not a row", False), (SEP, False), ("|---|---|", False), (HEAD, False),
])
def test_is_data_row_is_strictly_boolean(line, want):
    assert pw.is_data_row(line) is want


@pytest.mark.parametrize("base", ["", "—", "–", None])
def test_fit_note_on_an_empty_cell_is_just_the_tag(base):
    assert pw.compose_fit_note(base, "strong ops match", "fit", "2026-01-07") == \
        "[fit-reason 2026-01-07 fit: strong ops match]"


# --- add ------------------------------------------------------------------------------

def test_add_writes_exactly_one_row_with_dashes_and_no_fit_note(tmp_path):
    p = pipe(tmp_path, NO_ARCHIVE)
    out, code = run(tmp_path, "add", "Gamma LLC", "Ops")
    assert code == 0 and "other roles" not in out["summary"], out
    assert p.read_text(encoding="utf-8") == NO_ARCHIVE.replace(
        BETA + "\n", BETA + f"\n| Gamma LLC | Ops | Researching | {TODAY} | — | — | — | — |\n")


def test_add_of_a_role_another_company_has_is_not_a_duplicate(tmp_path):
    pipe(tmp_path, NO_ARCHIVE)
    out, code = run(tmp_path, "add", "Gamma LLC", "PM")
    assert code == 0 and out["action"] == "add" and "other roles" not in out["summary"], out


def test_add_does_not_treat_the_header_row_as_a_company(tmp_path):
    pipe(tmp_path, NO_ARCHIVE)
    out, code = run(tmp_path, "add", "Company", "Role")
    assert code == 0 and out["action"] == "add", out


def test_add_without_an_active_section_is_an_error(tmp_path):
    p = pipe(tmp_path, "# Pipeline\n")
    out, code = run(tmp_path, "add", "Gamma LLC", "Ops")
    assert code == 1 and out["code"] == "missing_section"
    assert p.read_text(encoding="utf-8") == "# Pipeline\n"


def test_a_file_without_a_trailing_newline_keeps_none(tmp_path):
    p = pipe(tmp_path, NO_ARCHIVE.rstrip("\n"))
    run(tmp_path, "add", "Gamma LLC", "Ops")
    assert not p.read_text(encoding="utf-8").endswith("\n")


# --- update ---------------------------------------------------------------------------

def test_update_keeps_every_cell_it_was_not_asked_to_change(tmp_path):
    p = pipe(tmp_path, NO_ARCHIVE)
    assert run(tmp_path, "update", "Acme Corp", "Interviewing")[1] == 0
    assert p.read_text(encoding="utf-8") == NO_ARCHIVE.replace(ACME, (
        f"| Acme Corp | PM | Interviewing | {TODAY} | Follow up Fri | cv-acme.pdf | Met at event "
        "| https://acme.example/job |"))


def test_update_and_remove_without_an_active_section_are_errors(tmp_path):
    text = f"# Pipeline\n\n## Archived\n\n{HEAD}\n{SEP}\n{ACME}\n"
    p = pipe(tmp_path, text)
    for args in (("update", "Acme Corp", "Interviewing"), ("remove", "Acme Corp")):
        out, code = run(tmp_path, *args)
        assert code == 1 and out["code"] == "missing_section", (args, out)
    assert p.read_text(encoding="utf-8") == text


def test_the_header_row_is_never_the_target_of_update_or_remove(tmp_path):
    p = pipe(tmp_path, NO_ARCHIVE)
    for args in (("update", "Company", "Interviewing"), ("remove", "Company")):
        out, code = run(tmp_path, *args)
        assert code == 1 and out["code"] == "not_found", (args, out)
    assert p.read_text(encoding="utf-8") == NO_ARCHIVE


# --- remove ---------------------------------------------------------------------------

ARCHIVED_ACME = (f"| Acme Corp | PM | Withdrawn | 2026-01-02 | Follow up Fri | cv-acme.pdf "
                 f"| Met at event - Withdrawn {TODAY} | https://acme.example/job |")


def test_remove_moves_the_whole_row_into_the_existing_archive(tmp_path):
    p = pipe(tmp_path, WITH_ARCHIVE)
    assert run(tmp_path, "remove", "Acme Corp")[1] == 0
    assert p.read_text(encoding="utf-8") == (
        f"# Pipeline\n\n## Active\n\n{HEAD}\n{SEP}\n{BETA}\n\n## Archived\n\n{HEAD}\n{SEP}\n{ARCHIVED_ACME}\n")


def test_remove_creates_the_archive_when_there_is_none(tmp_path):
    p = pipe(tmp_path, NO_ARCHIVE)
    assert run(tmp_path, "remove", "Acme Corp")[1] == 0
    assert p.read_text(encoding="utf-8") == (
        f"# Pipeline\n\n## Active\n\n{HEAD}\n{SEP}\n{BETA}\n\n## Archived\n\n{HEAD}\n{SEP}\n{ARCHIVED_ACME}\n")


def test_remove_of_a_row_with_empty_notes_writes_only_the_stage(tmp_path):
    p = pipe(tmp_path, WITH_ARCHIVE.replace("| Met at event |", "| — |"))
    run(tmp_path, "remove", "Acme Corp")
    assert f"| cv-acme.pdf | Withdrawn {TODAY} | https://acme.example/job |" in p.read_text(encoding="utf-8")


# --- main -----------------------------------------------------------------------------

def test_no_subcommand_is_a_usage_error(tmp_path):
    out, code = run(tmp_path)
    assert code == 1 and out["message"].startswith("Usage:")
