"""scan-targets.yaml must feed the PII denylist.

Origin 2026-08-12: a real Lane B target company was used as a fixture name in a
PUBLIC test file and the deterministic hook did not block it, because
gen_pii_denylist.py harvested only networking.md and job-pipeline.md. The entire
target pool (35 companies + 20 rejected at the time) was invisible to the guard,
which is precisely the set most likely to end up in a freshly written test.

The semantic subagent caught it. The deterministic layer should have.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"

TARGETS = """\
companies:
  - name: Zephyrine Dynamics
    ats: greenhouse
    slug: zephyrine
    active: true
  - name: Quillfeather Labs
    active: true
    outreach: true

rejected:
  - name: Marrowgate Systems
    date: 2025-01-15
    reason: not a fit
"""


def _run(tmp_path):
    cmd = [sys.executable, str(TOOLS / "gen_pii_denylist.py"),
           "--repo-root", str(tmp_path), "--dry-run"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return json.loads(r.stdout)


def _setup(tmp_path):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "networking.md").write_text(
        "# Networking\n\n## Contacts\n\n"
        "| Name | Company | Role | Relationship | Added | Last Interaction | Email |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n", encoding="utf-8")
    (d / "job-pipeline.md").write_text(
        "# Job Pipeline\n\n## Active Pipeline\n\n"
        "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n", encoding="utf-8")
    (d / "scan-targets.yaml").write_text(TARGETS, encoding="utf-8")
    return tmp_path


def _tokens(res):
    return " ".join(res["tokens"]).lower()


def test_scan_target_companies_are_harvested(tmp_path):
    res = _run(_setup(tmp_path))
    assert "zephyrine" in _tokens(res)


def test_outreach_only_targets_are_harvested(tmp_path):
    """Outreach-only rows have no `ats`; they are still real company names."""
    res = _run(_setup(tmp_path))
    assert "quillfeather" in _tokens(res)


def test_rejected_companies_are_harvested(tmp_path):
    """A company reviewed and declined is still a real company Nick looked at."""
    res = _run(_setup(tmp_path))
    assert "marrowgate" in _tokens(res)


def test_missing_scan_targets_file_is_not_an_error(tmp_path):
    """Graceful degradation: the file is optional."""
    _setup(tmp_path)
    (tmp_path / "data" / "scan-targets.yaml").unlink()
    res = _run(tmp_path)
    assert "count" in res
