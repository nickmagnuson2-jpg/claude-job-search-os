"""9b: tools/cv_fixed_sections.py blocks a CV whose fixed sections came from an older CV.

Placeholder CV content only (public repo).
"""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import cv_fixed_sections as cfs  # noqa: E402

SKILLS = "*Operating:* a, b. *Data:* SQL. *Building with AI:* c."
SKILL_MD = f"# x\n\n- **Skills section:** fixed:\n  `{SKILLS}`\n  - other text with `code`\n"


def cv(skills=SKILLS, building="(github.com/example)\n- one\n- two", drop=None):
    entries = [{"label": "Building", "details": building}, {"label": "Skills", "details": skills},
               {"label": "Hobbies", "details": "Running"}]
    entries = [e for e in entries if e["label"] != drop]
    return {"cv": {"name": "Alex Example", "sections": {"ADDITIONAL INFORMATION": entries}}}


def test_the_real_skill_states_exactly_one_fixed_line():
    line = cfs.fixed_skills(cfs.SKILL.read_text(encoding="utf-8"))
    assert line and line.startswith("*Operating:*") and "*Building with AI:*" in line


def test_fixed_skills_needs_exactly_one():
    assert cfs.fixed_skills(SKILL_MD) == SKILLS
    assert cfs.fixed_skills("no line") is None
    assert cfs.fixed_skills(SKILL_MD + f"  `{SKILLS} extra`\n") is None


def test_a_correct_cv_passes():
    assert cfs.check(cv(), SKILLS) == []
    assert cfs.check(cv(skills=f"  {SKILLS}  "), SKILLS) == []


@pytest.mark.parametrize("kw,needle", [
    ({"skills": "*Operating:* old. *Data:* SQL."}, "Skills details differ"),
    ({"building": "- one\n- two"}, "Building details must start with"),
    ({"drop": "Skills"}, "no Skills entry"),
    ({"drop": "Building"}, "no Building entry"),
])
def test_each_problem(kw, needle):
    problems = cfs.check(cv(**kw), SKILLS)
    assert len(problems) == 1 and needle in problems[0]


def test_entries_tolerates_odd_shapes():
    assert cfs.entries({}) == {} and cfs.entries(None) == {}
    assert cfs.entries(["a", "list"]) == {} and cfs.entries({"cv": "text"}) == {}
    assert cfs.entries({"cv": {"sections": ["x"]}}) == {}
    assert cfs.entries({"cv": {"sections": {"ADDITIONAL INFORMATION": ["text", {"x": 1}]}}}) == {}


def _run(tmp_path, data, skill=SKILL_MD):
    y = tmp_path / "cv.yaml"
    y.write_text(data if isinstance(data, str) else yaml.safe_dump(data))
    s = tmp_path / "SKILL.md"
    s.write_text(skill)
    return subprocess.run([sys.executable, str(REPO / "tools" / "cv_fixed_sections.py"), str(y),
                           "--skill", str(s)], capture_output=True, text=True, timeout=60)


def test_cli_exit_codes(tmp_path):
    ok = _run(tmp_path, cv())
    assert ok.returncode == 0 and "fixed sections OK" in ok.stdout
    bad = _run(tmp_path, cv(skills="old line"))
    assert bad.returncode == 2 and "BLOCKED" in bad.stderr and "expected:" in bad.stderr
    assert _run(tmp_path, "a: [unclosed").returncode == 1
    nospec = _run(tmp_path, cv(), skill="nothing here")
    assert nospec.returncode == 1 and "exactly one fixed" in nospec.stderr
    missing = subprocess.run([sys.executable, str(REPO / "tools" / "cv_fixed_sections.py"),
                              str(tmp_path / "nope.yaml")], capture_output=True, text=True, timeout=60)
    assert missing.returncode == 1 and "cannot read" in missing.stderr
    noskill = subprocess.run([sys.executable, str(REPO / "tools" / "cv_fixed_sections.py"),
                              str(tmp_path / "cv.yaml"), "--skill", str(tmp_path / "x.md")],
                             capture_output=True, text=True, timeout=60)
    assert noskill.returncode == 1 and "cannot read" in noskill.stderr


def test_the_skill_runs_it_before_the_first_render():
    text = cfs.SKILL.read_text(encoding="utf-8")
    i = text.index("tools/cv_fixed_sections.py")
    assert i < text.index("### Step 9b: Render PDF")
