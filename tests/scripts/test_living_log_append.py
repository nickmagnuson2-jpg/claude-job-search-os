import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "living_log_append.py"

HEADER = """---
voice: pure-voice
source: wispr-flow
---

# Garden Log

> Dated entries, newest first.

## 2026-05-10

**Old.** prior day content.
"""


def run(args, cwd):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=cwd,
    )


def test_new_day_inserts_above_existing_dates(tmp_path):
    f = tmp_path / "garden-log.md"
    f.write_text(HEADER, encoding="utf-8")
    r = run(["garden", "2026-05-12", "Lefty", "looking fierce",
             "wispr-2026-05-12-foo", "--file", str(f)], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["status"] == "ok"
    assert out["created_section"] is True
    body = f.read_text(encoding="utf-8")
    assert body.index("## 2026-05-12") < body.index("## 2026-05-10")
    assert body.index("# Garden Log") < body.index("## 2026-05-12")
    assert "**Lefty.** looking fierce (Source: [[wispr-2026-05-12-foo]])" in body


def test_same_day_appends_within_section(tmp_path):
    f = tmp_path / "garden-log.md"
    # Valid newest-first order: 2026-05-12 section is already first.
    content = (
        "---\nvoice: pure-voice\nsource: wispr-flow\n---\n\n"
        "# Garden Log\n\n> Dated entries, newest first.\n\n"
        "## 2026-05-12\n\n**First.** one.\n\n"
        "## 2026-05-10\n\n**Old.** prior day content.\n"
    )
    f.write_text(content, encoding="utf-8")
    r = run(["garden", "2026-05-12", "Second", "two", "--file", str(f)], tmp_path)
    assert r.returncode == 0, r.stderr
    body = f.read_text(encoding="utf-8")
    seg = body[body.index("## 2026-05-12"):body.index("## 2026-05-10")]
    # appended in place, within the existing 2026-05-12 section, after the existing entry
    assert seg.count("## 2026-05-12") == 1
    assert seg.index("**First.** one.") < seg.index("**Second.** two")
    # NO relocation: order preserved, 2026-05-10 section untouched
    assert body.index("## 2026-05-12") < body.index("## 2026-05-10")
    assert "**Old.** prior day content." in body


def test_unknown_log_errors(tmp_path):
    r = run(["bogus", "2026-05-12", "L", "t"], tmp_path)
    assert r.returncode == 1
    assert json.loads(r.stdout)["status"] == "error"


def test_bad_date_errors(tmp_path):
    f = tmp_path / "garden-log.md"
    f.write_text(HEADER, encoding="utf-8")
    r = run(["garden", "5/12/26", "L", "t", "--file", str(f)], tmp_path)
    assert r.returncode == 1
    assert "date" in json.loads(r.stdout)["message"]


def test_same_day_append_when_target_is_last_section(tmp_path):
    f = tmp_path / "garden-log.md"
    content = (
        "---\nvoice: pure-voice\nsource: wispr-flow\n---\n\n"
        "# T\n\n> intro\n\n"
        "## 2026-05-12\n\n**A.** one.\n"
    )
    f.write_text(content, encoding="utf-8")
    r = run(["garden", "2026-05-12", "B", "two", "--file", str(f)], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["status"] == "ok"
    assert out["created_section"] is False
    body = f.read_text(encoding="utf-8")
    assert body.count("## 2026-05-12") == 1
    assert body.index("**A.** one.") < body.index("**B.** two")


def test_same_day_append_empty_body_section(tmp_path):
    f = tmp_path / "garden-log.md"
    content = (
        "---\nvoice: pure-voice\nsource: wispr-flow\n---\n\n"
        "# T\n\n> intro\n\n"
        "## 2026-05-12\n\n## 2026-05-10\n\n**Old.** x.\n"
    )
    f.write_text(content, encoding="utf-8")
    r = run(["garden", "2026-05-12", "New", "fresh", "--file", str(f)], tmp_path)
    assert r.returncode == 0, r.stderr
    body = f.read_text(encoding="utf-8")
    assert body.count("## 2026-05-12") == 1
    assert body.index("## 2026-05-12") < body.index("**New.** fresh")
    assert body.index("**New.** fresh") < body.index("## 2026-05-10")
    assert "**Old.** x." in body


def test_no_trailing_newline_source(tmp_path):
    f = tmp_path / "garden-log.md"
    content = (
        "---\nvoice: pure-voice\nsource: wispr-flow\n---\n\n"
        "# T\n\n> intro\n\n"
        "## 2026-05-12\n\n**A.** one."
    )
    f.write_text(content, encoding="utf-8")
    r = run(["garden", "2026-05-12", "B", "two", "--file", str(f)], tmp_path)
    assert r.returncode == 0, r.stderr
    body = f.read_text(encoding="utf-8")
    assert "**B.** two" in body
    assert body.endswith("\n")
    assert not body.endswith("\n\n")


def test_no_existing_dated_sections(tmp_path):
    f = tmp_path / "garden-log.md"
    content = (
        "---\nvoice: pure-voice\nsource: wispr-flow\n---\n\n"
        "# Log\n\n> intro\n"
    )
    f.write_text(content, encoding="utf-8")
    r = run(["garden", "2026-05-12", "L", "t", "--file", str(f)], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["status"] == "ok"
    assert out["created_section"] is True
    body = f.read_text(encoding="utf-8")
    assert "## 2026-05-12" in body
    assert body.index("# Log") < body.index("## 2026-05-12")
    assert body.index("> intro") < body.index("## 2026-05-12")
    assert "**L.** t" in body


def test_crlf_file_preserves_crlf(tmp_path):
    f = tmp_path / "garden-log.md"
    content = (
        "---\nvoice: pure-voice\nsource: wispr-flow\n---\n\n"
        "# T\n\n> intro\n\n"
        "## 2026-05-12\n\n**A.** one.\n"
    ).replace("\n", "\r\n")
    f.write_bytes(content.encode("utf-8"))
    r = run(["garden", "2026-05-12", "B", "two", "--file", str(f)], tmp_path)
    assert r.returncode == 0, r.stderr
    data = f.read_bytes()
    assert b"\r\n" in data
    assert "**B.** two" in data.decode("utf-8")
