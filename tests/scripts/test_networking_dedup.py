"""Tests for the fuzzy name-duplicate guard on `networking_write.py add`
and the underlying tools/name_dedup.py module.

Origin: 2026-06-12 — a contact was added under a variant spelling while the same
person already existed under a slightly different spelling; the exact-match dup
check missed it. See memory/feedback_dedup_before_appending_to_tracking_doc.md.

All names here are generic placeholders (public repo, no real PII).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

from name_dedup import normalize_name, similarity, find_near_duplicates  # noqa: E402

NETWORKING_SCRIPT = TOOLS / "networking_write.py"


# --------------------------------------------------------------------------
# Unit: name_dedup
# --------------------------------------------------------------------------

def test_normalize_folds_accents_case_punctuation():
    assert normalize_name("José  Doe") == "jose doe"
    assert normalize_name("O'Brien-Smith") == "o brien smith"
    assert normalize_name("  Casey   DOE ") == "casey doe"


def test_find_near_dup_catches_one_letter_variant():
    # The motivating real case shape: a single transposed/dropped letter.
    near = find_near_duplicates("Jordan Samples", ["Jordan Sample"])
    assert near and near[0][0] == "Jordan Sample"
    assert near[0][1] >= 0.84


def test_find_near_dup_catches_nickname_vs_full():
    near = find_near_duplicates("Samuel Nelsen", ["Sam Nelsen"])
    assert near and near[0][0] == "Sam Nelsen"


def test_find_near_dup_catches_accent_only_difference():
    near = find_near_duplicates("Jose Doe", ["José Doe"])
    assert near and near[0][1] == 1.0


def test_find_near_dup_ignores_distinct_names():
    assert find_near_duplicates("Taylor Rivers", ["Jordan Sample", "Casey Doe"]) == []


def test_find_near_dup_sorted_desc_and_handles_none():
    near = find_near_duplicates("Casey Doe", ["Casey Doh", None, "Casey Doe"])
    # exact (1.0) should sort ahead of the near variant
    assert near[0] == ("Casey Doe", 1.0)
    assert any(n[0] == "Casey Doh" for n in near)


# --------------------------------------------------------------------------
# Integration: networking_write.py add
# --------------------------------------------------------------------------

SEED = """# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Casey Doe | Acme | peer | friend | 2026-01-01 | 2026-01-01 | casey@example.com |

## Interaction Log

### Casey Doe — Acme

#### 2026-01-01 | other | seeded

**Follow-up:** —
"""


def _run_add(repo_root, *extra):
    cmd = [sys.executable, str(NETWORKING_SCRIPT), "--repo-root", str(repo_root),
           "add", *extra]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return json.loads(res.stdout), res.returncode


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "networking.md").write_text(SEED, encoding="utf-8")
    return tmp_path


def test_add_blocks_on_fuzzy_variant(repo):
    out, rc = _run_add(repo, "Casey Doh")  # one-letter variant of Casey Doe
    assert out["status"] == "error"
    assert out["code"] == "possible_duplicate"
    assert any(c["name"] == "Casey Doe" for c in out["candidates"])
    assert rc != 0
    # And it must NOT have written the row.
    assert "Casey Doh" not in (repo / "data" / "networking.md").read_text(encoding="utf-8")


def test_force_overrides_fuzzy_block(repo):
    out, rc = _run_add(repo, "Casey Doh", "--force")
    assert out["status"] == "ok"
    assert out["action"] == "add"
    assert "Casey Doh" in (repo / "data" / "networking.md").read_text(encoding="utf-8")


def test_exact_duplicate_still_warns(repo):
    out, _ = _run_add(repo, "Casey Doe")
    assert out["action"] == "duplicate_warning"


def test_distinct_name_adds_cleanly(repo):
    out, rc = _run_add(repo, "Taylor Rivers", "--company", "LiveCo")
    assert out["status"] == "ok"
    assert out["action"] == "add"
    assert rc == 0
    assert "Taylor Rivers" in (repo / "data" / "networking.md").read_text(encoding="utf-8")
