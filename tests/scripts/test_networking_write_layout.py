"""networking_write.py: exact file output, not just "the row is in there".

Mutation run 2026-10-29 left 83 survivors in networking_write.py, nearly all of the same
kind: a dropped blank line, a skipped "create the missing section" branch, a loop that
matches the first row instead of the named one. The existing tests check that a value
appears somewhere in the file, which every one of those mutants still satisfies. These
tests compare the WHOLE file (or the whole JSON error), so a layout or matching change fails.
"""
import json
import os
import subprocess
import sys
from datetime import date

import pytest

from conftest import REPO_ROOT, TOOLS_DIR, write_fixture

sys.path.insert(0, str(TOOLS_DIR))
import networking_write as nw  # noqa: E402

TODAY = date.today().isoformat()
HEADER = "| Name | Company | Role | Relationship | Added | Last Interaction | Email |"
SEP = "| --- | --- | --- | --- | --- | --- | --- |"


def run(tmp_path, *args):
    r = subprocess.run([sys.executable, str(TOOLS_DIR / "networking_write.py"),
                        "--repo-root", str(tmp_path), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
    return json.loads(r.stdout.strip().splitlines()[-1]), r.returncode


def net(tmp_path, text):
    return write_fixture(tmp_path, "data/networking.md", text)


TWO_CONTACTS = f"""# Networking

## Contacts

{HEADER}
{SEP}
| Sam Example | Acme Corp | PM | peer | 2026-01-01 | — | — |
| Jane Doe | Beta Inc | Eng | peer | 2026-01-02 | — | — |

## Interaction Log

### Sam Example — Acme Corp

#### 2026-01-03 | email | older note

**Follow-up:** —

### Jane Doe — Beta Inc

#### 2026-01-04 | email | jane older

**Follow-up:** —
"""


# --- log ----------------------------------------------------------------------------

def test_log_into_an_existing_section_is_newest_first_with_exact_spacing(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    out, code = run(tmp_path, "log", "Jane Doe", "--date", "2026-02-01", "--type", "call",
                    "--summary", "talked", "--content", "line one\n\nline three", "--no-todo")
    assert code == 0, out
    want = TWO_CONTACTS.replace(
        "| Jane Doe | Beta Inc | Eng | peer | 2026-01-02 | — | — |",
        "| Jane Doe | Beta Inc | Eng | peer | 2026-01-02 | 2026-02-01 | — |").replace(
        "### Jane Doe — Beta Inc\n\n",
        "### Jane Doe — Beta Inc\n\n#### 2026-02-01 | call | talked\n\n> line one\n>\n> line three\n\n"
        "**Follow-up:** —\n\n")
    assert p.read_text(encoding="utf-8") == want


def test_log_creates_a_missing_contact_section_with_company(tmp_path):
    text = TWO_CONTACTS.split("### Jane Doe — Beta Inc")[0].rstrip("\n") + "\n"
    p = net(tmp_path, text)
    out, code = run(tmp_path, "log", "Jane Doe", "--date", "2026-02-01", "--summary", "hi",
                    "--no-todo")
    assert code == 0, out
    want = text.replace("| 2026-01-02 | — | — |", "| 2026-01-02 | 2026-02-01 | — |") + (
        "\n### Jane Doe — Beta Inc\n\n#### 2026-02-01 | other | hi\n\n**Follow-up:** —\n\n")
    assert p.read_text(encoding="utf-8") == want


def test_log_creates_a_missing_contact_section_without_company(tmp_path):
    text = TWO_CONTACTS.replace("| Jane Doe | Beta Inc |", "| Jane Doe | — |")
    text = text.split("### Jane Doe — Beta Inc")[0].rstrip("\n") + "\n"
    p = net(tmp_path, text)
    assert run(tmp_path, "log", "Jane Doe", "--date", "2026-02-01", "--summary", "hi",
               "--no-todo")[1] == 0
    assert p.read_text(encoding="utf-8").endswith(
        "\n### Jane Doe\n\n#### 2026-02-01 | other | hi\n\n**Follow-up:** —\n\n")


def test_log_creates_the_whole_interaction_log_when_absent(tmp_path):
    text = TWO_CONTACTS.split("## Interaction Log")[0].rstrip("\n") + "\n"
    p = net(tmp_path, text)
    assert run(tmp_path, "log", "Sam Example", "--date", "2026-02-01", "--summary", "hi",
               "--no-todo")[1] == 0
    want = text.replace("| 2026-01-01 | — | — |", "| 2026-01-01 | 2026-02-01 | — |") + (
        "\n## Interaction Log\n\n### Sam Example — Acme Corp\n\n"
        "#### 2026-02-01 | other | hi\n\n**Follow-up:** —\n\n")
    assert p.read_text(encoding="utf-8") == want


def test_log_whole_section_without_company(tmp_path):
    text = TWO_CONTACTS.replace("| Sam Example | Acme Corp |", "| Sam Example | — |")
    text = text.split("## Interaction Log")[0].rstrip("\n") + "\n"
    p = net(tmp_path, text)
    assert run(tmp_path, "log", "Sam Example", "--date", "2026-02-01", "--summary", "hi",
               "--no-todo")[1] == 0
    assert "\n## Interaction Log\n\n### Sam Example\n\n#### 2026-02-01" in p.read_text(encoding="utf-8")


def test_log_matches_the_named_contact_not_the_first_row(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    run(tmp_path, "log", "Jane Doe", "--date", "2026-02-01", "--summary", "hi", "--no-todo")
    text = p.read_text(encoding="utf-8")
    assert "| Sam Example | Acme Corp | PM | peer | 2026-01-01 | — | — |" in text
    assert "| Jane Doe | Beta Inc | Eng | peer | 2026-01-02 | 2026-02-01 | — |" in text


def test_log_for_an_unknown_contact_is_not_found_and_writes_nothing(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    out, code = run(tmp_path, "log", "Nobody Here", "--summary", "hi", "--no-todo")
    assert code == 1 and out["code"] == "not_found"
    assert p.read_text(encoding="utf-8") == TWO_CONTACTS


def test_log_dry_run_reports_the_entry_and_writes_nothing(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    out, code = run(tmp_path, "--dry-run", "log", "Jane Doe", "--date", "2026-02-01",
                    "--summary", "hi")
    assert code == 0 and out["dry_run"] is True
    assert out["would_mutate"][0]["entry"] == "#### 2026-02-01 | other | hi"
    assert p.read_text(encoding="utf-8") == TWO_CONTACTS


def test_log_pads_a_short_contact_row_to_seven_columns(tmp_path):
    text = TWO_CONTACTS.replace("| Jane Doe | Beta Inc | Eng | peer | 2026-01-02 | — | — |",
                                "| Jane Doe | Beta Inc | Eng | peer | 2026-01-02 |")
    p = net(tmp_path, text)
    assert run(tmp_path, "log", "Jane Doe", "--date", "2026-02-01", "--summary", "hi",
               "--no-todo")[1] == 0
    assert "| Jane Doe | Beta Inc | Eng | peer | 2026-01-02 | 2026-02-01 | — |" in p.read_text(encoding="utf-8")


def test_log_into_a_heading_with_no_blank_line_after_it(tmp_path):
    text = TWO_CONTACTS.replace("### Jane Doe — Beta Inc\n\n", "### Jane Doe — Beta Inc\n")
    p = net(tmp_path, text)
    run(tmp_path, "log", "Jane Doe", "--date", "2026-02-01", "--summary", "hi", "--no-todo")
    assert "### Jane Doe — Beta Inc\n#### 2026-02-01 | other | hi\n\n**Follow-up:** —\n\n#### 2026-01-04" \
        in p.read_text(encoding="utf-8")


def test_a_file_without_a_trailing_newline_keeps_none(tmp_path):
    p = net(tmp_path, TWO_CONTACTS.rstrip("\n"))
    run(tmp_path, "log", "Sam Example", "--date", "2026-02-01", "--summary", "hi", "--no-todo")
    assert not p.read_text(encoding="utf-8").endswith("\n")


# --- add ----------------------------------------------------------------------------

def test_add_appends_the_row_and_a_stub_at_the_end_of_the_log(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    out, code = run(tmp_path, "add", "Lee Sample", "--company", "Gamma LLC")
    assert code == 0, out
    want = TWO_CONTACTS.replace(
        "| Jane Doe | Beta Inc | Eng | peer | 2026-01-02 | — | — |\n",
        "| Jane Doe | Beta Inc | Eng | peer | 2026-01-02 | — | — |\n"
        f"| Lee Sample | Gamma LLC | — | peer | {TODAY} | — | — |\n") + "\n### Lee Sample — Gamma LLC\n\n"
    assert p.read_text(encoding="utf-8") == want


def test_add_without_a_company_uses_a_bare_heading(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    run(tmp_path, "add", "Lee Sample")
    assert p.read_text(encoding="utf-8").endswith("\n### Lee Sample\n\n")


def test_add_creates_both_sections_when_the_file_has_neither(tmp_path):
    p = net(tmp_path, "# Networking\n")
    out, code = run(tmp_path, "add", "Lee Sample", "--company", "Gamma LLC")
    assert code == 0, out
    assert p.read_text(encoding="utf-8") == (
        f"# Networking\n\n## Contacts\n\n{HEADER}\n{SEP}\n"
        f"| Lee Sample | Gamma LLC | — | peer | {TODAY} | — | — |\n"
        "\n## Interaction Log\n\n### Lee Sample — Gamma LLC\n\n")


def test_add_detects_a_duplicate_on_any_row_and_reports_its_company(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    out, code = run(tmp_path, "add", "jane doe")
    assert code == 0 and out["action"] == "duplicate_warning"
    assert out["existing_company"] == "Beta Inc"
    assert p.read_text(encoding="utf-8") == TWO_CONTACTS


def test_add_near_duplicate_of_the_second_row_is_blocked(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    out, code = run(tmp_path, "add", "Jane Do")
    assert code == 1 and out["code"] == "possible_duplicate"
    assert out["candidates"][0]["name"] == "Jane Doe"
    assert out["candidates"][0]["company"] == "Beta Inc"
    assert p.read_text(encoding="utf-8") == TWO_CONTACTS


# --- update -------------------------------------------------------------------------

def test_update_without_a_contacts_section_is_an_error(tmp_path):
    p = net(tmp_path, "# Networking\n\n## Interaction Log\n")
    out, code = run(tmp_path, "update", "Jane Doe", "--role", "Lead")
    assert code == 1 and out["code"] == "missing_section"
    assert p.read_text(encoding="utf-8") == "# Networking\n\n## Interaction Log\n"


def test_update_changes_only_the_named_row(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    assert run(tmp_path, "update", "Jane Doe", "--role", "Lead")[1] == 0
    assert p.read_text(encoding="utf-8") == TWO_CONTACTS.replace("| Beta Inc | Eng |", "| Beta Inc | Lead |")


def test_update_refuses_a_row_with_a_stray_pipe(tmp_path):
    text = TWO_CONTACTS.replace("| Jane Doe | Beta Inc | Eng |", "| Jane Doe | Beta | Inc | Eng |")
    p = net(tmp_path, text)
    out, code = run(tmp_path, "update", "Jane Doe", "--role", "Lead")
    assert code == 1 and out["code"] == "malformed_row" and out["column_count"] == 8
    assert p.read_text(encoding="utf-8") == text


def test_update_company_renames_the_log_heading(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    run(tmp_path, "update", "Jane Doe", "--company", "Delta Co")
    text = p.read_text(encoding="utf-8")
    assert "### Jane Doe — Delta Co\n" in text and "### Jane Doe — Beta Inc" not in text


def test_update_company_with_no_log_section_changes_only_the_row(tmp_path):
    text = TWO_CONTACTS.split("### Jane Doe — Beta Inc")[0]
    p = net(tmp_path, text)
    assert run(tmp_path, "update", "Jane Doe", "--company", "Delta Co")[1] == 0
    assert p.read_text(encoding="utf-8") == text.replace("| Jane Doe | Beta Inc |", "| Jane Doe | Delta Co |")


# --- remove -------------------------------------------------------------------------

def test_remove_drops_only_the_named_row_and_archives_its_heading(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    out, code = run(tmp_path, "remove", "Jane Doe")
    assert code == 0, out
    want = TWO_CONTACTS.replace("| Jane Doe | Beta Inc | Eng | peer | 2026-01-02 | — | — |\n", "").replace(
        "### Jane Doe — Beta Inc", "### [ARCHIVED] Jane Doe — Beta Inc")
    assert p.read_text(encoding="utf-8") == want


def test_remove_of_an_unknown_contact_is_not_found(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    out, code = run(tmp_path, "remove", "Nobody Here")
    assert code == 1 and out["code"] == "not_found"
    assert p.read_text(encoding="utf-8") == TWO_CONTACTS


def test_remove_without_a_log_section_still_removes_the_row(tmp_path):
    text = TWO_CONTACTS.split("### Jane Doe — Beta Inc")[0]
    p = net(tmp_path, text)
    assert run(tmp_path, "remove", "Jane Doe")[1] == 0
    assert p.read_text(encoding="utf-8") == text.replace(
        "| Jane Doe | Beta Inc | Eng | peer | 2026-01-02 | — | — |\n", "")


def test_remove_dry_run_writes_nothing(tmp_path):
    p = net(tmp_path, TWO_CONTACTS)
    out, code = run(tmp_path, "--dry-run", "remove", "Jane Doe")
    assert code == 0 and out["dry_run"] is True and out["action"] == "remove"
    assert p.read_text(encoding="utf-8") == TWO_CONTACTS


# --- main ---------------------------------------------------------------------------

def test_no_subcommand_is_a_usage_error(tmp_path):
    out, code = run(tmp_path)
    assert code == 1 and out["message"].startswith("Usage:")


# --- helpers ------------------------------------------------------------------------

@pytest.mark.parametrize("line,want", [
    ("| Sam | Acme |", True),
    ("not a row", False),
    ("| --- | --- |", False),
    ("|---|---|", False),
    ("| Name | Company |", False),
])
def test_is_data_row(line, want):
    assert nw.is_data_row(line) is want


def test_find_contact_log_section_bounds():
    lines = TWO_CONTACTS.splitlines()
    s, e = nw.find_contact_log_section(lines, "jane doe")
    assert lines[s] == "### Jane Doe — Beta Inc" and e == len(lines)
    s2, e2 = nw.find_contact_log_section(lines, "Sam Example")
    assert lines[s2] == "### Sam Example — Acme Corp" and lines[e2] == "### Jane Doe — Beta Inc"
    assert nw.find_contact_log_section(lines, "Nobody") == (-1, -1)
    assert nw.find_contact_log_section(["#### Jane Doe"], "Jane Doe") == (-1, -1)
    assert nw.find_contact_log_section(["### Jane Doe", "x", "## Next", "y"], "Jane Doe") == (0, 2)
