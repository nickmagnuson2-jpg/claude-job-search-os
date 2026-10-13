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


# --- A named file that was not read (2026-10-13) -----------------------------
# Exit 2 used to need EVERY path to be unreadable. One typo among three paths exited 0
# and the text mode printed "a real zero" without saying a file had been left out.

def _text(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def test_one_unread_file_among_readable_ones_is_exit_2_in_json(tmp_path):
    p = tmp_path / "clean.md"
    p.write_text(CLEAN, encoding="utf-8")
    out = run(str(p), str(tmp_path / "typo.md"), "--json", expect=2)
    assert out["scanned"] == 1 and out["unreadable"] == [str(tmp_path / "typo.md")]


def test_text_mode_names_the_unread_file_and_does_not_claim_a_real_zero(tmp_path):
    p = tmp_path / "clean.md"
    p.write_text(CLEAN, encoding="utf-8")
    r = _text(str(p), str(tmp_path / "typo.md"))
    assert r.returncode == 2
    assert "NOT READ (1 of 2 given)" in r.stdout and "typo.md" in r.stdout
    assert "real zero" not in r.stdout
    assert "No corrections found in the 1 file(s) read." in r.stdout


def test_corrections_in_the_read_files_are_still_printed_beside_an_unread_one(tmp_path):
    p = tmp_path / "acme.md"
    p.write_text(SAMPLE, encoding="utf-8")
    r = _text(str(p), str(tmp_path / "typo.md"))
    assert r.returncode == 2
    assert "NOT READ" in r.stdout and "CORRECTION" in r.stdout and "Lesson #17" in r.stdout


def test_a_fully_read_scope_keeps_exit_0_and_its_wording(tmp_path):
    clean, acme = tmp_path / "clean.md", tmp_path / "acme.md"
    clean.write_text(CLEAN, encoding="utf-8")
    acme.write_text(SAMPLE, encoding="utf-8")
    r = _text(str(clean))
    assert r.returncode == 0 and "real zero" in r.stdout and "NOT READ" not in r.stdout
    r = _text(str(acme))
    assert r.returncode == 0 and "NOT READ" not in r.stdout
    assert run(str(acme), "--json", expect=0)["unreadable"] == []


# --- Older behaviour nothing asserted (mutation survivors, 2026-10-13) --------

SCAFFOLDED = SAMPLE + "- A new bullet <!-- TODO: add the metric -->\n- Plain <!-- just a note -->\n"


def test_each_correction_carries_the_first_known_marker_it_names(tmp_path):
    p = tmp_path / "acme.md"
    p.write_text(SCAFFOLDED, encoding="utf-8")
    by_comment = {c["comment"][:12]: c["marker"] for c in run(str(p), "--json")["corrections"]}
    assert by_comment == {
        "HONESTY CORR": "HONESTY CORRECTION", '"Facilitated': "Lesson #",
        "SCOPE FLAG (": "SCOPE FLAG", "CORRECTED 20": "CORRECTED",
        "TODO: add th": None, "just a note": None,
    }


def test_template_todos_are_counted_apart_from_corrections(tmp_path):
    p = tmp_path / "acme.md"
    p.write_text(SCAFFOLDED, encoding="utf-8")
    out = run(str(p), "--json")
    assert (out["correction_count"], out["real_correction_count"], out["scaffold_count"]) == (6, 5, 1)


def test_empty_scope_says_why_and_reports_a_zero_denominator(tmp_path):
    out = run(str(tmp_path / "does-not-exist.md"), "--json", expect=2)
    assert out["scanned"] == 0 and "empty scope" in out["error"]
    assert "correction_count" not in out


def test_text_mode_prints_the_count_the_location_the_claim_and_the_correction(tmp_path):
    p = tmp_path / "acme.md"
    p.write_text(SCAFFOLDED, encoding="utf-8")
    out = _text(str(p)).stdout
    assert out.startswith("5 correction(s) across 1 file(s), plus 1 unfilled template TODO(s)")
    assert "acme.md:5 [HONESTY CORRECTION] " in out
    assert "    CLAIM     (section-level" in out
    assert "acme.md:7 [Lesson #] " in out
    assert "    CLAIM     Facilitated rhythm-of-business for a 600-person org" in out
    assert "acme.md:12 \n" in out, "a comment with no known marker prints no tag"
    p.write_text(SAMPLE, encoding="utf-8")
    assert _text(str(p)).stdout.startswith("4 correction(s) across 1 file(s).\n")


def test_an_undecodable_file_is_unread_and_does_not_discard_the_others(tmp_path):
    """It used to raise: exit 1, a traceback, and nothing from the file read before it."""
    good, bad = tmp_path / "acme.md", tmp_path / "bad.md"
    good.write_text(SAMPLE, encoding="utf-8")
    bad.write_bytes(b"\xff\xfe not utf-8 \xff")
    out = run(str(good), str(bad), "--json", expect=2)
    assert out["scanned"] == 1 and out["unreadable"] == [str(bad)]
    assert out["correction_count"] == 4
    r = _text(str(good), str(bad))
    assert r.returncode == 2 and r.stderr == ""
    assert "NOT READ (1 of 2 given)" in r.stdout and "Lesson #17" in r.stdout
    run(str(bad), "--json", expect=2)
    assert run(str(tmp_path), str(good), "--json", expect=2)["unreadable"] == [str(tmp_path)], (
        "a directory is not a file that was read")
