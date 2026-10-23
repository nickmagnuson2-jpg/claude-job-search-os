"""The CV theme must render a full-length CV to ONE page.

THE DRIFT THIS EXISTS TO STOP (2026-09-23). Four values in
`framework/cv-themes/tuck-mbb.yaml` did not match what some hand-tuned one-page
renders used (others shipped at the old values). Someone had hand-tightened a render to make it fit one page and never wrote the
values back to the theme, so `cv_merge_theme.py` composed a TWO-PAGE CV from
content that had shipped as one page. Nothing caught it: the merge exited 0, the
render exited 0, and the only symptom was a second page nobody looked at. It was
found by eye, weeks later, while building an unrelated CV.

Two tests, doing different jobs:

  1. `test_theme_pins_the_shipped_density_values` — dependency-free, always runs.
     Pins the four values. It cannot prove one page; its job is to make loosening
     them a DELIBERATE act that requires editing this file and reading why.

  2. `test_theme_renders_a_full_length_cv_to_one_page` — the one with teeth.
     Composes a full-length generic CV through the real theme, renders it, and
     counts pages. Reverting line_spacing (0.52em -> 0.6em) flips this to two
     pages; the other three values do not change the page count on their own and
     are caught only by the exact-value pin in test 1 (cross-model review 2026-09-23).

PAGE COUNTING: read `/Type /Page` out of the PDF bytes. Do NOT use `mdls`, which
serves a cached Spotlight page count for an overwritten file and reported 2 pages
for a 1-page PDF on 2026-09-23, sending the author off cutting content that was
already fine.

The fixture is deliberately sized to match a real single-page CV. If you shrink
it, this test stops testing anything, because a short fixture fits on one page no
matter how loose the theme gets.

ON THE FIXTURE'S PROVENANCE: every name, company, title, and bullet here is fictional.
Only the SHAPE follows a real single-page CV: the same sections, the same number of
entries and bullets, and bullet text written to roughly the same length line for line.
That is deliberate, since the thing under test is whether a full-length CV overflows a
page. If you edit the text, keep each line about as long as it is now.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
THEME = REPO / "framework" / "cv-themes" / "tuck-mbb.yaml"
MERGE = REPO / "tools" / "cv_merge_theme.py"

# The density values some hand-tuned one-page renders used (not every shipped CV:
# some shipped at the old theme values; see the theme's DRIFT FIX note). Only
# line_spacing decides the page count of the fixture; the render test below is what
# tells you whether a real-length CV has been pushed onto a second page.
SHIPPED_DENSITY = {
    "name": "19pt",
    "line_spacing": "0.52em",
    "space_above": "0.28cm",
    "space_between_regular_entries": "0.32em",
}

FIXTURE_CONTENT = """\
cv:
  name: CASEY DOE
  location: Example City, CA
  email: casey.doe@example.com
  phone: tel:+1-415-555-0100
  social_networks:
    - network: LinkedIn
      username: casey-doe
  sections:
    SUMMARY:
      - "Operations generalist who turns loose plans into schedules that teams actually keep. Most of my work sits between the people who set goals and the people who carry them out: writing the plan, setting the review cadence, tracking the numbers, and noticing early when a project is drifting. I have done this inside a consulting practice, as chief of staff to a senior executive at a mid-sized software company, and in small ventures of my own. I like short feedback loops, plain documents, and meetings that end with a named owner and a date for every open item on the list, plus a check-in date on the shared calendar."
    EXPERIENCE:
      - company: ACME CORP
        position: Chief of Staff to the Senior Vice President of Operations
        date: 2025 - 2026
        location: Example City, CA
        highlights:
          - Worked with the finance and operations leads on a quarterly planning calendar used by every department in the company, from intake to final review.
          - Ran the weekly leadership review, keeping the agenda, decision log, and follow-up list current for a group of department heads and their staff.
          - "Drafted the annual plan and its quarterly goals, ordering projects by what the team could actually staff: two hiring goals first, longer process projects second; worked with the head of engineering on a small set of trial projects for the year."
          - Built a shared weekly status page with the executive team, collecting each group's updates in one place instead of separate email threads; piloted it for a quarter.
      - company: LIVECO
        position: Manager, Business Reporting
        date: 2024
        location: Example City, CA
        highlights:
          - Led reporting projects across three teams that raised survey response 25%, and cut manual data entry 20% with a new tracker.
      - company: CONSULTCO
        position: Associate
        date: 2022 - 2024
        location: Example City, CA
        highlights:
          - "Ran a scheduling pilot for a regional clinic network, moving front-desk staff from walk-in handling to booked appointments: on site with the clinic managers, sat in on shifts, wrote the training guides, and built the cost model from their visit logs, with the data team confirming the booking system could report a visit type it did not yet track. Presented to the client steering group alongside other proposals; the pilot went ahead."
          - Planned and delivered training sessions across the regional teams and monthly review meetings of a distributor's reorganization program, resulting in a 50% reduction in new-account setup time.
          - Supported a planning workshop at a large insurance client, helping a mixed group of department leaders agree on goals, rank candidate projects, and review what peer companies were doing.
      - company: MEDIACO
        position: Senior Analyst, Audience Insights (2018 - 2020); Analyst, Reporting and Research (2017 - 2018)
        date: 2017 - 2020
        location: Sample Borough, CT
        highlights:
          - Recovered lost revenue by finding in a weekly report that sponsor placements were missing from the busiest pages, then sizing the gap against peak-season traffic instead of the quiet months that had kept it low on the fix list for the product team.
          - Hired and managed a team of 2 direct reports, setting up and growing the licensing request process for photos and video shared by partner publishers.
      - company: ENTERPRISECO
        position: Business Operations and Reporting Consultant
        date: 2015 - 2016
        location: Example Harbor, NY
    EDUCATION:
      - institution: EXAMPLE SCHOOL OF BUSINESS
        area: Operations Research and Applied Statistics (STEM)
        degree: MBA
        date: 2020 - 2022
        location: Example Village, NH
      - institution: EXAMPLE UNIVERSITY
        area: Economics, with a Statistics and Data Science Minor
        degree: BA
        date: 2011 - 2015
        location: Example Park, NC
    ADDITIONAL INFORMATION:
      - label: Skills
        details: "Tools: workflow automation, project trackers, shared databases. Data analysis and reporting: SQL, Python, dashboards, spreadsheets. Writing: plans, memos, slide decks."
      - label: Building
        details: "A small scheduling app built and run solo for a local club: a booking form, a waitlist that fills open slots in order, reminder emails, and a weekly attendance report checked against the sign-up sheet. Also a set of scripts that keep a personal budget and its monthly reports up to date."
      - label: Hobbies
        details: Trail running, cooking, chess, learning to garden.
locale:
  language: english
settings:
  current_date: today
  bold_keywords: []
  pdf_title: Casey Doe - CV
"""


def _theme_text() -> str:
    return THEME.read_text(encoding="utf-8")


def _pdf_page_count(pdf: Path) -> int:
    """Count pages from the PDF bytes.

    Deliberately not `mdls`: Spotlight caches a page count per path and will
    report the PREVIOUS render's count for an overwritten file.
    """
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))


def test_theme_pins_the_shipped_density_values():
    """The four values a hand-tightened render used, written back into the theme.

    This is the cheap always-on half. It proves nothing about page count; it makes
    loosening the layout a deliberate act rather than a silent one.
    """
    text = _theme_text()
    missing = []
    for key, value in SHIPPED_DENSITY.items():
        if not re.search(rf"^\s*{re.escape(key)}:\s*{re.escape(value)}\s*$", text, re.M):
            missing.append(f"{key}: {value}")
    assert not missing, (
        "framework/cv-themes/tuck-mbb.yaml no longer carries the density values that "
        f"hand-tuned one-page renders used: {missing}. If this was deliberate, run the render test "
        "below and confirm a full-length CV is still ONE page, then update SHIPPED_DENSITY."
    )


@pytest.mark.skipif(shutil.which("rendercv") is None
                    and not (Path.home() / ".local/bin/rendercv").exists(),
                    reason="rendercv not installed (public/CI checkout)")
def test_theme_renders_a_full_length_cv_to_one_page(tmp_path):
    """Compose a full-length generic CV through the REAL theme and count pages.

    This is the half with teeth. Loosen `line_spacing` back toward the pre-2026-09-23
    default and this flips to two pages.
    """
    content = tmp_path / "fixture.content.yaml"
    content.write_text(FIXTURE_CONTENT, encoding="utf-8")
    merged = tmp_path / "fixture.yaml"

    merge = subprocess.run(
        [sys.executable, str(MERGE), "--content", str(content),
         "--theme", str(THEME), "--out", str(merged), "--json"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert merge.returncode == 0, f"cv_merge_theme failed: {merge.stderr}"
    assert merged.exists(), "merge reported success but wrote no file"

    rendercv = shutil.which("rendercv") or str(Path.home() / ".local/bin/rendercv")
    render = subprocess.run(
        [rendercv, "render", merged.name],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert render.returncode == 0, f"rendercv failed: {render.stdout}\n{render.stderr}"

    pdfs = list((tmp_path / "rendercv_output").glob("*.pdf"))
    assert len(pdfs) == 1, f"expected exactly one PDF, got {pdfs}"

    pages = _pdf_page_count(pdfs[0])
    assert pages == 1, (
        f"A full-length CV composed through framework/cv-themes/tuck-mbb.yaml rendered to "
        f"{pages} pages. One page is the contract. This is the 2026-09-23 drift recurring: "
        "check line_spacing, font_size.name, section_titles.space_above and "
        "sections.space_between_regular_entries against SHIPPED_DENSITY above. "
        "If the theme is right and the CONTENT grew, fix the content, not the theme."
    )
