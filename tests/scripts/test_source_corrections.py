"""source_corrections.py must surface every dated correction attached to a source bullet.

WHY. Twice (2026-07-08 CVs, 2026-08-07 application answer) external-facing prose reintroduced
a claim that the source project file carried a dated, commented correction for. Both times the
correction comment was sitting on the very bullet being paraphrased, and both times it was
caught only by an expensive after-the-fact review agent.

The failure is not a knowledge gap -- the file had been read in full in the same session. It is
that a correction living in an HTML comment is invisible while you are reading the bullet text
it corrects. This tool makes it a command instead of an act of attention.

Fixtures are synthetic: data/projects/ is gitignored.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "source_corrections.py"

SAMPLE = """# Acme

## Key Achievements

<!-- HONESTY CORRECTIONS (2026-05-12): Verbs recalibrated per lesson #17. -->

- Facilitated rhythm-of-business for a 600-person org <!-- "Facilitated" not "Stood up": the processes existed. Lesson #17. -->
- Defined the AI strategy at the December offsite <!-- SCOPE FLAG (2026-07-24): strategy-level, not hands-on. -->
- A bullet with no annotation at all
- Supported partner ecosystem strategy <!-- CORRECTED 2026-07-08: driven by the partner team, not led. -->
"""

CLEAN = """# Clean Project

## Key Achievements

- A bullet with no annotation
- Another clean bullet
"""


def run(*args, expect=0):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)
    assert proc.returncode == expect, f"rc={proc.returncode} err={proc.stderr}\n{proc.stdout}"
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def test_finds_every_correction_with_its_bullet(tmp_path):
    p = tmp_path / "acme.md"
    p.write_text(SAMPLE, encoding="utf-8")
    out = run(str(p), "--json")
    assert out["scanned"] == 1
    assert out["correction_count"] == 4, out
    texts = " ".join(c["comment"] for c in out["corrections"])
    assert "Lesson #17" in texts and "SCOPE FLAG" in texts and "CORRECTED" in texts


def test_correction_carries_the_bullet_it_annotates(tmp_path):
    """A correction without its bullet is unusable -- you cannot tell what it corrects."""
    p = tmp_path / "acme.md"
    p.write_text(SAMPLE, encoding="utf-8")
    out = run(str(p), "--json")
    inline = [c for c in out["corrections"] if "Facilitated" in (c["bullet"] or "")]
    assert len(inline) == 1
    assert 'not "Stood up"' in inline[0]["comment"]
    assert inline[0]["line"] > 0


def test_file_level_comment_has_no_bullet_but_is_still_reported(tmp_path):
    """The HONESTY CORRECTIONS header applies to the whole section, not one bullet."""
    p = tmp_path / "acme.md"
    p.write_text(SAMPLE, encoding="utf-8")
    out = run(str(p), "--json")
    headers = [c for c in out["corrections"] if c["bullet"] is None]
    assert len(headers) == 1
    assert "HONESTY CORRECTIONS" in headers[0]["comment"]


def test_clean_file_reports_zero_with_a_denominator(tmp_path):
    """Zero corrections is a real answer, but only next to the count of files actually read."""
    p = tmp_path / "clean.md"
    p.write_text(CLEAN, encoding="utf-8")
    out = run(str(p), "--json")
    assert out["correction_count"] == 0
    assert out["scanned"] == 1, "a zero with no denominator is not a clearance"


def test_empty_scope_is_an_error_not_a_clean_result(tmp_path):
    """Same contract as sweep.py: scanning nothing must never look like finding nothing."""
    run(str(tmp_path / "does-not-exist.md"), "--json", expect=2)


def test_multiple_files_keep_their_provenance(tmp_path):
    a, b = tmp_path / "acme.md", tmp_path / "clean.md"
    a.write_text(SAMPLE, encoding="utf-8")
    b.write_text(CLEAN, encoding="utf-8")
    out = run(str(a), str(b), "--json")
    assert out["scanned"] == 2
    assert {Path(c["file"]).name for c in out["corrections"]} == {"acme.md"}


def test_human_output_is_not_json(tmp_path):
    p = tmp_path / "acme.md"
    p.write_text(SAMPLE, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), str(p)],
                          capture_output=True, text=True)
    assert proc.returncode == 0
    assert "Lesson #17" in proc.stdout
    assert not proc.stdout.lstrip().startswith("{")
