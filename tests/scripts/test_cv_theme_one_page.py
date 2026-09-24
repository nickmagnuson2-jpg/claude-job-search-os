"""The CV theme must render a full-length CV to ONE page.

THE DRIFT THIS EXISTS TO STOP (2026-09-23). Four values in
`framework/cv-themes/tuck-mbb.yaml` did not match what shipped CVs actually used.
Someone had hand-tightened a render to make it fit one page and never wrote the
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

ON THE FIXTURE'S PROVENANCE, stated precisely because the earlier wording overclaimed:
every NAME and COMPANY here is a placeholder, but the SHAPE is a real CV - real titles,
real date ranges, real bullet structure. That is deliberate and necessary, since the
thing under test is whether a full-length CV overflows a page. It is not a leak: every
masked label resolves to a past employer or school, which this repo's PII boundary
treats as public-safe. Do not describe this fixture as fully anonymized.
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

# The density values that shipped CVs actually render at. Changing any of these
# loosens the layout; the render test below is what tells you whether you have
# pushed a real CV onto a second page.
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
  social_networks:
    - network: LinkedIn
      username: casey-doe
  sections:
    SUMMARY:
      - "The model is rarely the hard part. Deployments break on the people whose job changes, on records that lose their identity moving between systems, and on proving it worked when most measurement is decorative until you break it on purpose. I have worked all three: on site with a customer's support organization through the redesign, the training and the launch, and inside a sponsor-owned product organization sequencing use cases by what could be measured. That runs through consulting implementation, chief of staff to the technology lead of a large organization, and an application of my own in production."
    EXPERIENCE:
      - company: ACME CORP
        position: Chief of Staff to the Chief Product and Technology Officer
        date: 2025 - 2026
        location: Example City, CA
        highlights:
          - Worked directly with the sponsor's operating partner and the finance lead on how a large technology organization set and measured its priorities.
          - Built the goal tree and worked with stakeholders across product, engineering, and operations on the transition to an outcome-based roadmap.
          - "Defined the strategy and objectives, sequencing use cases by what the organization could measure: a velocity target first, harder-to-attribute revenue use cases second; partnered with the head of engineering on the proof-of-concept portfolio."
          - Prototyped an assisted executive digest with the technology lead, pulling each team's material into a synthesis rather than manual recaps; beta-tested with leadership.
      - company: LIVECO
        position: Director, Business Intelligence
        date: 2024
        location: Example City, CA
        highlights:
          - Led cross-team analytics initiatives that improved engagement 25%, and cut operational inefficiency 20% with a new dashboard.
      - company: CONSULTCO
        position: Associate
        date: 2022 - 2024
        location: Example City, CA
        highlights:
          - "Spearheaded a support pilot for a marketplace client, shifting staff from inbound handling to outbound calls: on site with the client team, reviewed the recordings, built the training materials, and built the business case off their tracking data, with analytics verifying the platform could measure a case type that did not yet exist. Won the client's steering committee against competing pilots; the pilot launched."
          - Designed and executed change management initiatives across the pod structure and quarterly review cadence of a retailer's operating model transformation, resulting in 50% reduction in client onboarding time.
          - Supported a workshop at a large telecommunications client, coaching a cross-organizational executive team on vision development, prioritization of use cases, and the industry landscape.
      - company: MEDIACO
        position: Growth Manager, Platform Analytics (2018 - 2020); Analyst, Product Analytics (2017 - 2018)
        date: 2017 - 2020
        location: Sample Borough, CT
        highlights:
          - Delivered revenue improvement by finding in SQL that video ads were not delivering on high-traffic pages, then repricing the defect against in-season impression volume instead of the near-zero offseason traffic that had kept it out of the engineering queue.
          - Built and led a team of 2 direct reports, establishing and scaling the off-platform content rights management process for a very large social following.
      - company: ENTERPRISECO
        position: Business Analytics and Strategy Consultant
        date: 2015 - 2016
        location: Example Harbor, NY
    EDUCATION:
      - institution: EXAMPLE SCHOOL OF BUSINESS
        area: Management Science and Quantitative Methods (STEM)
        degree: MBA
        date: 2020 - 2022
        location: Example Village, NH
      - institution: EXAMPLE UNIVERSITY
        area: Public Policy, Markets and Management Certificate
        degree: BA
        date: 2011 - 2015
        location: Example Park, NC
    ADDITIONAL INFORMATION:
      - label: Skills
        details: "Building: agent workflows, application frameworks, relational databases. Data analysis and modeling: SQL, R, dashboards, spreadsheets. Executive decks: presentation software."
      - label: Building
        details: "A sample application built and run solo in production: a six-stage generation pipeline with two model stages around a deterministic core, telemetry to a database, and a north star metric measured against an empirical ceiling. Also an agentic operating system that runs a job search end to end."
      - label: Hobbies
        details: Mountain biking, skiing, yoga, learning to woodwork.
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
        f"shipped CVs render at: {missing}. If this was deliberate, run the render test "
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
