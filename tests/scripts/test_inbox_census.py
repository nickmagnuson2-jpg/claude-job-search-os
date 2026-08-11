"""Comment-span-aware census of data/inbox.md (D8 / spec section 3.8).

This is the oracle every later inbox step asserts against, so it is the one number
in the system that must not come from the code under test.

Why it exists: the rev-1 census counted `## ` headers with a naive line scan and
reported 150 live headers and 34 career-scan blocks. A 372-line HTML comment span
(lines 3567-3939 of the live file) wraps three of those headers. The true split is
147 live / 3 commented, and 31 live career-scan blocks. That contaminated
arithmetic was load-bearing for the extraction and removal steps: removal keyed on
a block list that did not match the file.

A commented-out header is not a block. Comment interiors are non-block territory:
excluded from the census, from extraction, and from removal, and reported separately.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"


def _run(tmp_path, *args):
    cmd = [sys.executable, str(TOOLS / "inbox_census.py"),
           "--repo-root", str(tmp_path), *[str(a) for a in args]]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        return json.loads(r.stdout), r.returncode
    except json.JSONDecodeError:
        raise AssertionError(f"non-JSON output: {r.stdout!r} / {r.stderr!r}")


def _write(tmp_path, text):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "inbox.md"
    p.write_text(text, encoding="utf-8")
    return p


FIXTURE = """\
# Inbox

## Career Scan Results

**Scanned:** 2026-07-09 12:30 UTC
- **[5/10]** Role at Northwind (SF) - [View](https://example.com/1)

## 2026-06-01 | A human capture

Some note.

<!--
## Career Scan Results

**Scanned:** 2026-01-01 00:00 UTC
- **[1/10]** Dead role at Globex (NY) - [View](https://example.com/2)

## 2026-01-02 | A commented-out capture
-->

## 2026-08-10 | Agent drip: lane-a (company)
<!-- review-gated: accept via /act or /networking -->
- **Acme** - does things (score 4)

## Call Debrief: someone

Notes.
"""


def test_commented_out_headers_are_not_counted_as_live(tmp_path):
    """The rev-1 bug: a naive scan counts headers inside a comment span."""
    _write(tmp_path, FIXTURE)
    res, code = _run(tmp_path)
    assert code == 0
    # Live: career scan, human capture, agent drip, call debrief.
    assert res["live_h2_headers"] == 4
    # Commented: one career scan + one dated capture, both inside the span.
    assert res["commented_h2_headers"] == 2


def test_commented_out_career_scan_block_is_excluded(tmp_path):
    _write(tmp_path, FIXTURE)
    res, _ = _run(tmp_path)
    assert res["career_scan_live"] == 1
    assert res["commented_scan_headers"] == 1


def test_comment_spans_are_reported_with_line_ranges(tmp_path):
    """Later steps need the exact spans to treat as non-block territory."""
    _write(tmp_path, FIXTURE)
    res, _ = _run(tmp_path)
    assert len(res["comment_spans"]) == 1
    start, end = res["comment_spans"][0]
    assert start < end
    assert res["comment_span_bytes"] > 0


def test_block_kinds_are_classified_first_match_wins(tmp_path):
    """A drip header also matches the bare-dated-capture regex. Order decides."""
    _write(tmp_path, FIXTURE)
    res, _ = _run(tmp_path)
    assert res["agent_drips"] == 1
    assert res["call_debrief"] == 1
    assert res["dated_captures"] == 1        # the drip must NOT be double-counted


def test_census_records_source_sha256(tmp_path):
    """The oracle is pinned to an exact file state."""
    import hashlib
    p = _write(tmp_path, FIXTURE)
    res, _ = _run(tmp_path)
    expect = hashlib.sha256(p.read_bytes()).hexdigest()
    assert res["source_sha256"] == expect


def test_unbalanced_comment_markers_abort(tmp_path):
    """An unclosed span means every downstream line range is untrustworthy."""
    _write(tmp_path, "# Inbox\n\n<!--\n## Career Scan Results\n")
    res, code = _run(tmp_path)
    assert code != 0
    assert res["status"] == "error"
    assert "unbalanced" in res["message"].lower()


def test_inline_comment_does_not_open_a_span(tmp_path):
    """`<!-- x -->` on one line must not swallow the rest of the file."""
    text = ("# Inbox\n\n<!-- review-gated: accept via /act -->\n\n"
            "## Career Scan Results\n\n**Scanned:** 2026-07-09 12:30 UTC\n")
    _write(tmp_path, text)
    res, code = _run(tmp_path)
    assert code == 0
    assert res["career_scan_live"] == 1
    assert res["commented_h2_headers"] == 0


def test_empty_census_is_an_error_not_a_pass(tmp_path):
    """Fail-open guard, per the trim-context-file precedent: a census that finds
    nothing must not report success. An empty result means the parse broke."""
    _write(tmp_path, "# Inbox\n\nNo blocks here at all.\n")
    res, code = _run(tmp_path)
    assert code != 0
    assert res["status"] == "error"


def test_writes_census_file_when_asked(tmp_path):
    _write(tmp_path, FIXTURE)
    res, code = _run(tmp_path, "--write")
    assert code == 0
    out = Path(res["census_path"])
    assert out.exists()
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk["career_scan_live"] == res["career_scan_live"]
    assert on_disk["source_sha256"] == res["source_sha256"]
