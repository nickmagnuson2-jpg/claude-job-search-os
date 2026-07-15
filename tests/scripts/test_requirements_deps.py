"""Guards for hard-imported dependencies being declared — fable-audit 2026-07-07 #11.

PyYAML is hard-imported by the CV pipeline (cv_merge_theme.py, projects_to_yaml.py),
/discover-companies, and career-scan, but was missing from requirements.txt, so a
fresh install broke the flagship CV path. This test fails if it drops out again.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _declared_packages(requirements_path):
    """Return the lowercased distribution names declared in a requirements file,
    ignoring comments/blank lines and version specifiers."""
    names = set()
    for line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def test_pyyaml_declared_in_requirements():
    pkgs = _declared_packages(REPO / "requirements.txt")
    assert "pyyaml" in pkgs, f"pyyaml missing from requirements.txt (found: {sorted(pkgs)})"


def test_pyyaml_actually_importable():
    """Sanity: the declared dep resolves to an importable module in this env."""
    import yaml  # noqa: F401
