#!/usr/bin/env python3
"""cv_fixed_sections.py - BLOCK a CV render whose fixed sections were copied from an older CV.

Why it exists (feedback_never_template_a_cv_off_a_prior_cv, gate tripped 2026-09-28; built
2026-10-29). Two CVs were templated off a prior CV and shipped that CV's stale fixed lines:
the second carried the pre-2026-09-23 Skills line. Two sections of ADDITIONAL INFORMATION are
fixed by the /generate-cv spec, not tailored, so a mechanical comparison settles them:

  * Skills details must EQUAL the fixed string in .claude/skills/generate-cv/SKILL.md (the
    backticked line that starts "*Operating:*"). The string is read from the skill file every
    run, so changing the spec there changes the check, and the two cannot drift.
  * Building details must START WITH "(github.com/" (the handle line comes first; RenderCV
    renders a leading "- " literally).

/generate-cv runs this in Step 6a-corrections, before the first render. Exit 2 blocks.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/cv_fixed_sections.py <cv.yaml> [--skill PATH]

Exit codes: 0 both sections match; 2 a section is wrong or missing (BLOCK); 1 bad input
(unreadable YAML, or the skill file no longer states the fixed Skills line).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / ".claude" / "skills" / "generate-cv" / "SKILL.md"
SECTION = "ADDITIONAL INFORMATION"
BUILDING_PREFIX = "(github.com/"
_FIXED_SKILLS = re.compile(r"^\s*`(\*Operating:\*[^`]+)`\s*$", re.M)


def fixed_skills(skill_text: str) -> str | None:
    """The one backticked line in the skill that starts with *Operating:*."""
    found = _FIXED_SKILLS.findall(skill_text)
    return found[0].strip() if len(found) == 1 else None


def entries(cv: dict) -> dict:
    sections = ((cv or {}).get("cv") or {}).get("sections") or {}
    out = {}
    for e in sections.get(SECTION) or []:
        if isinstance(e, dict) and "label" in e:
            out[str(e["label"]).strip()] = str(e.get("details") or "")
    return out


def check(cv: dict, skills_line: str) -> list[str]:
    """Problems, empty when both fixed sections are right."""
    got = entries(cv)
    problems = []
    if "Skills" not in got:
        problems.append(f"{SECTION} has no Skills entry")
    elif got["Skills"].strip() != skills_line:
        problems.append("Skills details differ from the fixed line in generate-cv SKILL.md.\n"
                        f"    expected: {skills_line}\n    found:    {got['Skills'].strip()}")
    if "Building" not in got:
        problems.append(f"{SECTION} has no Building entry")
    elif not got["Building"].startswith(BUILDING_PREFIX):
        problems.append(f"Building details must start with {BUILDING_PREFIX!r}; found "
                        f"{got['Building'][:40]!r}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cv")
    ap.add_argument("--skill", default=str(SKILL))
    a = ap.parse_args(argv)
    import yaml
    try:
        cv = yaml.safe_load(Path(a.cv).read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"ERROR: cannot read {a.cv}: {e}", file=sys.stderr)
        return 1
    try:
        line = fixed_skills(Path(a.skill).read_text(encoding="utf-8"))
    except OSError as e:
        print(f"ERROR: cannot read {a.skill}: {e}", file=sys.stderr)
        return 1
    if line is None:
        print(f"ERROR: {a.skill} does not state exactly one fixed '*Operating:*' Skills line",
              file=sys.stderr)
        return 1
    problems = check(cv, line)
    if problems:
        print("BLOCKED: fixed CV sections are wrong (was this CV templated off an older one?)",
              file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    print("fixed sections OK: Skills matches the spec, Building starts with the handle line")
    return 0


if __name__ == "__main__":
    sys.exit(main())
