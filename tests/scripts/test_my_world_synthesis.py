"""Tests for tools/my_world_synthesis.py"""
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import TOOLS_DIR


def run(*args, repo_root, stdin=None):
    """Run my_world_synthesis.py against an isolated repo root. (dict, rc)."""
    script = TOOLS_DIR / "my_world_synthesis.py"
    cmd = [sys.executable, str(script), "--repo-root", str(repo_root),
           *[str(a) for a in args]]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       input=stdin, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return json.loads(r.stdout), r.returncode


def refl_dir(repo_root):
    return Path(repo_root) / "data" / "reflections"


def seed_reflections(repo_root, dates):
    """Create dated reflection files plus noise files that must be excluded."""
    d = refl_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    for ds in dates:
        (d / f"{ds}.md").write_text(f"# Reflection {ds}\n> note\n", encoding="utf-8")
    # noise that must be excluded
    (d / "_themes.md").write_text("---\nvoice: cloud-generated\n---\n# Themes\n", encoding="utf-8")
    (d / "_template.md").write_text("# Template\n", encoding="utf-8")
    (d / "notes.md").write_text("# Not dated\n", encoding="utf-8")


def test_status_baseline_no_longitudinal(tmp_path):
    seed_reflections(tmp_path, ["2026-05-01", "2026-05-02", "2026-05-03",
                                "2026-05-04", "2026-05-05", "2026-05-06"])
    res, rc = run("status", repo_root=tmp_path)
    assert rc == 0 and res["status"] == "ok"
    assert res["is_baseline"] is True
    assert res["covered_through"] is None
    assert res["total_reflections"] == 6        # noise excluded
    assert res["should_synthesize"] is True      # 6 >= default 5
    assert res["newest_reflection"] == "2026-05-06"


def test_status_below_threshold_baseline(tmp_path):
    seed_reflections(tmp_path, ["2026-05-01", "2026-05-02"])
    res, rc = run("status", repo_root=tmp_path)
    assert res["should_synthesize"] is False     # 2 < 5
    assert res["total_reflections"] == 2


def test_status_threshold_override(tmp_path):
    seed_reflections(tmp_path, ["2026-05-01", "2026-05-02"])
    res, rc = run("status", "--threshold", "2", repo_root=tmp_path)
    assert res["should_synthesize"] is True       # 2 >= 2


def test_status_baseline_window_caps_new_files(tmp_path):
    dates = [f"2026-05-{day:02d}" for day in range(1, 16)]  # 15 reflections
    seed_reflections(tmp_path, dates)
    res, rc = run("status", repo_root=tmp_path)
    assert res["total_reflections"] == 15
    assert res["new_count"] == 12                 # BASELINE_WINDOW cap
    assert len(res["new_files"]) == 12
    assert res["newest_reflection"] == "2026-05-15"


def test_status_excludes_underscore_and_nondated(tmp_path):
    seed_reflections(tmp_path, ["2026-05-01"])
    res, rc = run("status", repo_root=tmp_path)
    assert res["total_reflections"] == 1
    assert all("_themes" not in f and "notes.md" not in f for f in res["new_files"])


SYNTH_BLOCK = """## 2026-05-06 (covered 6 reflections through 2026-05-06)

### Thinking patterns
Frame X keeps recurring.

### Decision biases
Hesitates on Y.

### Communication tendencies
Hedges with "kind of"."""


def long_path(repo_root):
    return refl_dir(repo_root) / "_longitudinal.md"


def test_append_creates_file_when_missing(tmp_path):
    seed_reflections(tmp_path, ["2026-05-06"])
    res, rc = run("append", "--covered-through", "2026-05-06", "--date", "2026-06-01",
                  repo_root=tmp_path, stdin=SYNTH_BLOCK)
    assert rc == 0 and res["action"] == "append" and res["created"] is True
    text = long_path(tmp_path).read_text(encoding="utf-8")
    assert text.startswith("---\nvoice: cloud-generated")
    assert "last-synthesis: 2026-06-01" in text
    assert "covered-through: 2026-05-06" in text
    assert "Frame X keeps recurring." in text


def test_append_newest_first_and_updates_markers(tmp_path):
    seed_reflections(tmp_path, ["2026-05-06"])
    run("append", "--covered-through", "2026-05-06", "--date", "2026-05-10",
        repo_root=tmp_path, stdin="## 2026-05-10 older\n### Thinking patterns\nold")
    run("append", "--covered-through", "2026-05-20", "--date", "2026-05-25",
        repo_root=tmp_path, stdin="## 2026-05-25 newer\n### Thinking patterns\nnew")
    text = long_path(tmp_path).read_text(encoding="utf-8")
    # newest block appears before the older one
    assert text.index("2026-05-25 newer") < text.index("2026-05-10 older")
    # markers reflect the latest append
    assert "last-synthesis: 2026-05-25" in text
    assert "covered-through: 2026-05-20" in text


def test_append_then_status_sees_marker(tmp_path):
    seed_reflections(tmp_path, ["2026-05-01", "2026-05-02", "2026-05-03"])
    run("append", "--covered-through", "2026-05-02", "--date", "2026-06-01",
        repo_root=tmp_path, stdin=SYNTH_BLOCK)
    res, rc = run("status", repo_root=tmp_path)
    assert res["is_baseline"] is False
    assert res["covered_through"] == "2026-05-02"
    assert res["new_count"] == 1          # only 2026-05-03 is newer
    assert res["new_files"][0].endswith("2026-05-03.md")


def test_append_empty_input_errors(tmp_path):
    seed_reflections(tmp_path, ["2026-05-06"])
    res, rc = run("append", "--covered-through", "2026-05-06",
                  repo_root=tmp_path, stdin="   \n")
    assert rc == 1 and res["code"] == "bad_input"


def test_append_inserts_missing_markers(tmp_path):
    # Fix 1: well-formed frontmatter lacking marker keys gets both inserted.
    seed_reflections(tmp_path, ["2026-05-06"])
    long_path(tmp_path).write_text(
        "---\nvoice: cloud-generated\n---\n# header\n", encoding="utf-8")
    res, rc = run("append", "--covered-through", "2026-05-06", "--date", "2026-06-01",
                  repo_root=tmp_path, stdin=SYNTH_BLOCK)
    assert rc == 0 and res["action"] == "append"
    text = long_path(tmp_path).read_text(encoding="utf-8")
    assert "last-synthesis: 2026-06-01" in text
    assert "covered-through: 2026-05-06" in text


def test_append_malformed_frontmatter_warns(tmp_path):
    # Fix 2: unclosed frontmatter writes block but flags the warning.
    seed_reflections(tmp_path, ["2026-05-06"])
    long_path(tmp_path).write_text(
        "---\nvoice: cloud-generated\n# no closing fence\n## old entry\n",
        encoding="utf-8")
    res, rc = run("append", "--covered-through", "2026-05-06", "--date", "2026-06-01",
                  repo_root=tmp_path, stdin=SYNTH_BLOCK)
    assert rc == 0
    assert res.get("warning") == "malformed_frontmatter_markers_not_updated"
    text = long_path(tmp_path).read_text(encoding="utf-8")
    assert "Frame X keeps recurring." in text


def test_append_wellformed_has_no_warning(tmp_path):
    # Fix 2 negative: a normal append carries no warning key.
    seed_reflections(tmp_path, ["2026-05-06"])
    run("append", "--covered-through", "2026-05-06", "--date", "2026-06-01",
        repo_root=tmp_path, stdin=SYNTH_BLOCK)
    res, rc = run("append", "--covered-through", "2026-05-07", "--date", "2026-06-01",
                  repo_root=tmp_path, stdin=SYNTH_BLOCK)
    assert rc == 0
    assert "warning" not in res


def test_append_eof_fallback_blank_separator(tmp_path):
    # Fix 3: blank-line separator before block when last line is non-blank.
    seed_reflections(tmp_path, ["2026-05-06"])
    long_path(tmp_path).write_text(
        "---\nvoice: cloud-generated\nlast-synthesis: 2026-01-01\n"
        "covered-through: 2026-01-01\n---\nSome intro prose.",
        encoding="utf-8")
    res, rc = run("append", "--covered-through", "2026-05-06", "--date", "2026-06-01",
                  repo_root=tmp_path, stdin=SYNTH_BLOCK)
    assert rc == 0
    text = long_path(tmp_path).read_text(encoding="utf-8")
    assert "Some intro prose.\n\n## 2026-05-06" in text


def test_append_repo_root_isolation(tmp_path):
    # no writes escape tmp_path (per feedback_verify_repo_root_isolation_before_trusting_build)
    seed_reflections(tmp_path, ["2026-05-06"])
    run("append", "--covered-through", "2026-05-06", repo_root=tmp_path, stdin=SYNTH_BLOCK)
    assert long_path(tmp_path).exists()
    # the only _longitudinal.md created is under tmp_path
    assert (refl_dir(tmp_path) / "_longitudinal.md").exists()
