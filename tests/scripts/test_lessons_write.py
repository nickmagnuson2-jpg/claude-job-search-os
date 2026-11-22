"""tests for tools/lessons_write.py: the sanctioned writer for memory/lessons.md Section 2.

The real entry point runs in a subprocess against a temporary lessons file (LESSONS_PATH).
Fixture rows are invented.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "lessons_write.py"

LONG = "x" * 600   # rows over 500 characters are the reason Edit is not the route

LESSONS = f"""# Lessons

## Section 1 — General Corrections

| # | Pattern (what went wrong) | Rule (what to do instead) | Date |
|---|--------------------------|---------------------------|------|
| 1 | an invented general pattern | an invented general rule | 2026-01-01 |

---

## Section 2 — Email / Outreach Corrections

| Pattern | Rule | Occurrences | Promoted | Date |
|---------|------|-------------|----------|------|
| Invented pattern alpha about a nudge to Casey Doe at Acme {LONG} | Invented rule alpha | 1 | No | 2026-11-01 |
| Invented pattern beta about a thank-you note | Invented rule beta | 2 | No | 2026-10-01 |
| Invented pattern gamma about a thank-you note too | Invented rule gamma | 1 | No | 2026-09-01 |
"""


def run(args, path: Path):
    env = {**os.environ, "LESSONS_PATH": str(path), "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                       env=env, timeout=60)
    out = json.loads(p.stdout) if p.stdout.strip().startswith("{") else None
    return p.returncode, out, p


@pytest.fixture
def lessons(tmp_path):
    f = tmp_path / "lessons.md"
    f.write_text(LESSONS, encoding="utf-8")
    return f


def lines_of(f):
    return f.read_text(encoding="utf-8").split("\n")


# ------------------------------------------------------------------ set-promoted

def test_set_promoted_rewrites_only_that_cell_on_a_long_row(lessons):
    code, out, p = run(["set-promoted", "Invented pattern alpha", "Yes: encoded (voice guide, 2026-11-22)"], lessons)
    assert code == 0, p.stdout + p.stderr
    assert out["action"] == "set-promoted"
    assert out["old"] == "No"
    assert out["new"] == "Yes: encoded (voice guide, 2026-11-22)"
    before, after = LESSONS.split("\n"), lines_of(lessons)
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(before) == len(after)
    assert changed == [out["line"] - 1]
    assert after[changed[0]] == (f"| Invented pattern alpha about a nudge to Casey Doe at Acme {LONG} | "
                                 "Invented rule alpha | 1 | Yes: encoded (voice guide, 2026-11-22) | 2026-11-01 |")


def test_set_promoted_refuses_a_bad_token(lessons):
    code, out, _ = run(["set-promoted", "Invented pattern alpha", "Probably"], lessons)
    assert code == 1
    assert out["action"] == "refused"
    assert lessons.read_text(encoding="utf-8") == LESSONS


def test_set_promoted_refuses_a_pointer_without_target(lessons):
    code, out, _ = run(["set-promoted", "Invented pattern alpha", "superseded"], lessons)
    assert code == 1
    assert lessons.read_text(encoding="utf-8") == LESSONS


@pytest.mark.parametrize("match,needle", [
    ("about a thank-you", "matches 2 Section 2 rows"),
    ("no row has this text", "matches 0 Section 2 rows"),
    ("short", "at least 12 characters"),
    ("an invented general pattern", "matches 0 Section 2 rows"),   # Section 1 is out of scope
])
def test_set_promoted_needs_exactly_one_section2_row(lessons, match, needle):
    code, out, _ = run(["set-promoted", match, "No"], lessons)
    assert code == 1
    assert any(needle in r for r in out["reasons"])
    assert lessons.read_text(encoding="utf-8") == LESSONS


def test_set_promoted_refuses_a_pipe_in_the_value(lessons):
    code, out, _ = run(["set-promoted", "Invented pattern alpha", "Yes | hook"], lessons)
    assert code == 1
    assert "Promoted: may not contain '|' or a newline" in out["reasons"]
    assert lessons.read_text(encoding="utf-8") == LESSONS


# ------------------------------------------------------------------ append-row

def row_args(**over):
    base = {"pattern": "Invented new pattern", "rule": "Invented new rule",
            "occurrences": "1", "promoted": "No", "date": "2026-11-22"}
    base.update(over)
    out = []
    for k, v in base.items():
        out += [f"--{k}", v]
    return out


def test_append_row_inserts_at_top_of_section2(lessons):
    code, out, p = run(["append-row", *row_args()], lessons)
    assert code == 0, p.stdout + p.stderr
    after = lines_of(lessons)
    header = after.index("| Pattern | Rule | Occurrences | Promoted | Date |")
    assert after[header + 2] == "| Invented new pattern | Invented new rule | 1 | No | 2026-11-22 |"
    assert out["line"] == header + 3
    assert len(after) == len(LESSONS.split("\n")) + 1
    assert [ln for ln in after if ln != after[header + 2]] == LESSONS.split("\n")


@pytest.mark.parametrize("over,needle", [
    ({"date": "Nov 22"}, None),
    ({"occurrences": "twice"}, None),
    ({"promoted": "Someday"}, None),
    ({"rule": "a | b"}, "Rule: may not contain '|' or a newline"),
    ({"pattern": "line one\nline two"}, "Pattern: may not contain '|' or a newline"),
    ({"pattern": "  "}, "Pattern: empty"),
])
def test_append_row_refuses_an_invalid_row(lessons, over, needle):
    code, out, _ = run(["append-row", *row_args(**over)], lessons)
    assert code == 1
    assert out["action"] == "refused"
    if needle:
        assert needle in out["reasons"]
    assert lessons.read_text(encoding="utf-8") == LESSONS


# ------------------------------------------------------------------ file shape

def test_missing_file_is_refused(tmp_path):
    code, out, _ = run(["append-row", *row_args()], tmp_path / "absent.md")
    assert code == 1
    assert out["reasons"] == [f"no such file: {tmp_path / 'absent.md'}"]


@pytest.mark.parametrize("broken,needle", [
    (LESSONS.replace("## Section 2", "## Section Two"), "expected exactly one '## Section 2' heading, found 0"),
    (LESSONS + "\n## Section 2 again\n", "found 2"),
    (LESSONS.replace("| Pattern | Rule | Occurrences | Promoted | Date |", "| Pattern | Rule | Promoted | Date |"),
     "Section 2 header is"),
    (LESSONS.replace("|---------|------|-------------|----------|------|\n", ""), "no separator row"),
], ids=["no-section2", "two-section2", "four-column-header", "no-separator"])
def test_unexpected_section2_shape_is_refused(tmp_path, broken, needle):
    f = tmp_path / "lessons.md"
    f.write_text(broken, encoding="utf-8")
    code, out, _ = run(["append-row", *row_args()], f)
    assert code == 1
    assert any(needle in r for r in out["reasons"])
    assert f.read_text(encoding="utf-8") == broken


def test_default_path_is_main_checkouts_lessons():
    sys.path.insert(0, str(REPO / "tools"))
    import lessons_write
    from vault_paths import main_checkout
    env = os.environ.pop("LESSONS_PATH", None)
    try:
        assert lessons_write.lessons_path() == main_checkout(REPO) / "memory" / "lessons.md"
    finally:
        if env is not None:
            os.environ["LESSONS_PATH"] = env


def test_append_row_accepts_a_date_cell_with_later_fires(lessons):
    """Live rows carry later fires after the first date: '2026-07-24 (6th: 2026-09-22)'."""
    code, out, p = run(["append-row", *row_args(date="2026-07-24 (2nd: 2026-09-22)")], lessons)
    assert code == 0, p.stdout + p.stderr
    assert "| Invented new pattern | Invented new rule | 1 | No | 2026-07-24 (2nd: 2026-09-22) |" in lines_of(lessons)


def test_set_promoted_refuses_a_matched_row_that_cannot_be_read(tmp_path):
    broken = LESSONS.replace("| Invented rule beta | 2 | No | 2026-10-01 |",
                             "| Invented rule beta | 2 | No | 2026-10-01 | <!-- note -->")
    f = tmp_path / "lessons.md"
    f.write_text(broken, encoding="utf-8")
    code, out, _ = run(["set-promoted", "Invented pattern beta", "No"], f)
    assert code == 1
    assert out["reasons"][0].startswith("the matched row cannot be read: ")
    assert f.read_text(encoding="utf-8") == broken


def test_section2_without_a_table_is_refused(tmp_path):
    f = tmp_path / "lessons.md"
    broken = "# Lessons\n\n## Section 2 — Email\n\nno table yet\n\n## Section 3\n\n| a | b |\n|---|---|\n"
    f.write_text(broken, encoding="utf-8")
    code, out, _ = run(["append-row", *row_args()], f)
    assert code == 1
    assert out["reasons"] == ["Section 2 has no table"]


def test_atomic_write_leaves_no_temp_file_when_replace_fails(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "tools"))
    import lessons_write
    target = tmp_path / "lessons.md"
    target.write_text("original\n", encoding="utf-8")

    def boom(src, dst):
        raise OSError("replace failed")
    monkeypatch.setattr(lessons_write.os, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        lessons_write.atomic_write(target, "new\n")
    assert target.read_text(encoding="utf-8") == "original\n"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["lessons.md"]
