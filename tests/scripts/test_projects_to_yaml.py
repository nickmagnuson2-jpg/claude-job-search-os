"""projects_to_yaml.parse_project: HTML comments never reach a CV field.

Origin 2026-11-26: the Zuora Role line carries a dated <!-- CHANGED ... --> note, and
the generator copied the whole comment into the CV `position:` field. Inline comments
were stripped from achievement bullets only, and the line-by-line comment tracker
dropped any field or bullet line that opened a multi-line comment, plus any text after
`-->` on the closing line. Every form of the comment is covered here, one test each.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import projects_to_yaml as p2y  # noqa: E402

HEAD = "# Acme\n\n- **Period:** 01/2020 - 02/2021\n- **Client:** Acme Corp\n"


def parse(tmp_path, body):
    f = tmp_path / "acme.md"
    f.write_text(body, encoding="utf-8")
    return p2y.parse_project(f)


def test_inline_comment_on_a_field_is_stripped(tmp_path):
    proj = parse(tmp_path, HEAD + "- **Role:** Head of Ops <!-- changed per owner -->\n")
    assert proj.role == "Head of Ops"


def test_a_field_line_that_opens_a_multiline_comment_keeps_its_value(tmp_path):
    proj = parse(tmp_path, HEAD + "- **Role:** Head of Ops <!-- note\nspanning lines -->\n")
    assert proj.role == "Head of Ops"


def test_text_after_a_comment_closes_on_the_same_line_is_kept(tmp_path):
    body = HEAD + "- **Role:** Head of Ops\n\n## Key Achievements\n\n<!-- a\nb --> - Cut costs 20%\n"
    proj = parse(tmp_path, body)
    assert proj.key_achievements == ["Cut costs 20%"]


def test_a_bullet_that_opens_a_multiline_comment_keeps_its_text(tmp_path):
    body = HEAD + "- **Role:** Head of Ops\n\n## Key Achievements\n\n- Cut costs 20% <!-- source:\nthe Q3 deck -->\n- Hired 4\n"
    proj = parse(tmp_path, body)
    assert proj.key_achievements == ["Cut costs 20%", "Hired 4"]


def test_a_comment_only_line_between_fields_changes_nothing(tmp_path):
    proj = parse(tmp_path, HEAD + "<!-- whole-line note -->\n- **Role:** Head of Ops\n")
    assert proj.role == "Head of Ops"


def test_an_unclosed_comment_hides_the_rest_of_the_file(tmp_path):
    body = HEAD + "- **Role:** Head of Ops\n\n## Key Achievements\n\n- Kept\n<!-- unclosed\n- Hidden\n"
    proj = parse(tmp_path, body)
    assert proj.key_achievements == ["Kept"]


def test_a_comment_holding_a_field_line_does_not_set_the_field(tmp_path):
    body = HEAD + "- **Role:** Head of Ops\n<!--\n- **Role:** Old Title\n-->\n"
    proj = parse(tmp_path, body)
    assert proj.role == "Head of Ops"


def test_no_comment_marker_reaches_any_parsed_value(tmp_path):
    body = (HEAD.replace("Acme Corp", "Acme Corp <!-- legal name -->")
            + "- **Role:** Head of Ops <!-- x -->\n\n## Key Achievements\n\n- Did it <!-- y -->\n")
    proj = parse(tmp_path, body)
    for v in (proj.client, proj.role, proj.period, *proj.key_achievements):
        assert "<!--" not in v and "-->" not in v, v


# --- behaviour the file had no tests for until 2026-11-26 (mutation survivors on the fix branch) ---

def test_an_unknown_field_name_sets_nothing(tmp_path):
    proj = parse(tmp_path, HEAD + "- **Role:** Head of Ops\n- **Slug:** hijacked\n- **Notes:** x\n")
    assert proj.slug == "acme"
    assert not hasattr(proj, "notes")


def test_bullets_outside_key_achievements_are_not_achievements(tmp_path):
    body = HEAD + "- **Role:** Head of Ops\n\n## Description\n\n- Context bullet\n\n## Key Achievements\n\n- Real win\n"
    assert parse(tmp_path, body).key_achievements == ["Real win"]


def test_placeholder_bullets_are_skipped(tmp_path):
    body = HEAD + "- **Role:** Head of Ops\n\n## Key Achievements\n\n- -\n- Real win\n"
    assert parse(tmp_path, body).key_achievements == ["Real win"]


def test_a_file_without_role_period_or_client_is_not_a_project(tmp_path):
    assert parse(tmp_path, "# Notes\n\n- **Period:** 2020\n- **Client:** Acme\n") is None
    assert parse(tmp_path, "# Notes\n\n- **Role:** X\n- **Client:** Acme\n") is None
    assert parse(tmp_path, "# Notes\n\n- **Period:** 2020\n- **Role:** X\n") is None
