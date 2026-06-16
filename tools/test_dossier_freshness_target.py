#!/usr/bin/env python3
"""Tests for the 2026-06-02 target-filter in dossier_freshness.py.

Non-target stale dossiers (abandoned lanes) should NOT generate alerts;
target dossiers (company in active pipeline) still do. --all-stale restores
legacy behavior. older_than_30_days always lists everyone (no data loss).

Run:  PYTHONIOENCODING=utf-8 python3 tools/test_dossier_freshness_target.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parent / "dossier_freshness.py"


def _dossier(root: Path, slug: str, last_updated: str):
    d = root / "output" / slug
    d.mkdir(parents=True)
    (d / f"{slug}.md").write_text(
        f"# {slug}\n\nLast updated: {last_updated}\n\nbody\n", encoding="utf-8")


def _run(root: Path, all_stale=False):
    cmd = [sys.executable, str(SCRIPT), "--repo-root", str(root),
           "--target-date", "2026-06-02"]
    if all_stale:
        cmd.append("--all-stale")
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


root = Path(tempfile.mkdtemp())
(root / "data").mkdir()
# Northwind = active pipeline target; good-eggs / climate-ai = abandoned lanes.
(root / "data" / "job-pipeline.md").write_text(
    "| Company | Role | Stage |\n|---|---|---|\n"
    "| Northwind | FDAS | Phone Screen |\n"
    "| Tessera | Ops | Withdrawn |\n", encoding="utf-8")

_dossier(root, "northwind", "2026-02-24")   # stale + active pipeline target
_dossier(root, "tessera", "2026-02-24")      # stale + pipeline BUT terminal stage
_dossier(root, "good-eggs", "2026-02-23")        # stale + not in pipeline at all
_dossier(root, "climate-ai", "2026-02-25")       # stale + not in pipeline
_dossier(root, "fresh-co", "2026-06-01")         # fresh (not stale)

data = _run(root)
alert_slugs = {a["slug"] for a in data["staleness_alerts"]}

check("active-stage target alerts", "northwind" in alert_slugs)
check("terminal-stage pipeline co suppressed (tessera withdrawn)",
      "tessera" not in alert_slugs)
check("non-pipeline stale dossier suppressed (good-eggs)",
      "good-eggs" not in alert_slugs)
check("non-pipeline stale dossier suppressed (climate-ai)",
      "climate-ai" not in alert_slugs)
check("suppressed_non_target == 3", data["summary"]["suppressed_non_target"] == 3)
check("stale_target_count == 1", data["summary"]["stale_target_count"] == 1)
check("older_than_30_days still lists all 4 stale (no data loss)",
      len({e["slug"] for e in data["recent_dossiers"]["older_than_30_days"]}
          & {"northwind", "tessera", "good-eggs", "climate-ai"}) == 4)
check("fresh dossier not in alerts", "fresh-co" not in alert_slugs)

# --all-stale restores legacy behavior
data_all = _run(root, all_stale=True)
all_slugs = {a["slug"] for a in data_all["staleness_alerts"]}
check("--all-stale includes non-target + terminal",
      {"northwind", "tessera", "good-eggs", "climate-ai"} <= all_slugs)

print(f"\ndossier_freshness target tests: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
