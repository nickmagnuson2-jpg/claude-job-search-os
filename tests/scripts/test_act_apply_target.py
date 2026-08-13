"""target-add / target-reject subcommands of tools/act_apply.py.

These are the write path for `data/scan-targets.yaml`. Before them, nothing could
add to that file programmatically, so triaging discovery output into it was a
hand-edit — which is why 44 scored candidates sat unrouted.

The compounding property under test: a rejection is recorded, not discarded. The
weekly Exa collector dedups against `load_known_names`, which reads both
`companies:` and `rejected:`. So every company Nick turns down permanently
shrinks the next run's noise. Discarding rejections instead means the same
company returns to the inbox every week forever.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"

CONFIG = """\
companies:
  - name: Existing Co
    ats: greenhouse
    slug: existing
    active: true
"""


def _write(tmp_path, text=CONFIG):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "scan-targets.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def _run(tmp_path, *args):
    cmd = [sys.executable, str(TOOLS / "act_apply.py"),
           "--repo-root", str(tmp_path), *[str(a) for a in args]]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        return json.loads(r.stdout), r.returncode
    except json.JSONDecodeError:
        raise AssertionError(f"non-JSON output: {r.stdout!r} / {r.stderr!r}")


def _load(p):
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _run_inproc(mod, tmp_path, *args):
    """Run act_apply.main() in-process so internals can be monkeypatched.

    Returns the exit code. A subprocess cannot be patched, and these tests need
    to simulate a writer that corrupts the file.
    """
    import contextlib
    import io
    argv = ["act_apply.py", "--repo-root", str(tmp_path), *[str(a) for a in args]]
    old_argv = sys.argv
    sys.argv = argv
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            mod.main()
        return 0
    except SystemExit as e:
        return e.code or 0
    finally:
        sys.argv = old_argv


def test_target_add_writes_an_outreach_only_row(tmp_path):
    p = _write(tmp_path)
    res, code = _run(tmp_path, "target-add", "Northwind AI",
                     "--lane", "lane-b v5", "--outreach")
    assert code == 0 and res["status"] == "ok"

    row = next(c for c in _load(p)["companies"] if c["name"] == "Northwind AI")
    assert row["lane"] == "lane-b v5"
    assert row["outreach"] is True
    assert "ats" not in row          # outreach-only: nothing to scan
    assert row["active"] is True


def test_target_add_accepts_an_optional_ats(tmp_path):
    p = _write(tmp_path)
    _run(tmp_path, "target-add", "Globex Systems", "--lane", "lane-b v5",
         "--outreach", "--ats", "greenhouse", "--slug", "globex-systems")
    row = next(c for c in _load(p)["companies"] if c["name"] == "Globex Systems")
    assert row["ats"] == "greenhouse"
    assert row["slug"] == "globex-systems"


def test_target_add_is_idempotent(tmp_path):
    """Re-running a triage must not duplicate rows."""
    p = _write(tmp_path)
    _run(tmp_path, "target-add", "Northwind AI", "--lane", "b", "--outreach")
    res, code = _run(tmp_path, "target-add", "Northwind AI", "--lane", "b", "--outreach")
    assert code == 0
    names = [c["name"] for c in _load(p)["companies"]]
    assert names.count("Northwind AI") == 1


def test_target_add_preserves_existing_rows(tmp_path):
    p = _write(tmp_path)
    _run(tmp_path, "target-add", "Northwind AI", "--lane", "b", "--outreach")
    row = next(c for c in _load(p)["companies"] if c["name"] == "Existing Co")
    assert row["ats"] == "greenhouse"
    assert row["slug"] == "existing"


def test_target_reject_records_the_company_and_reason(tmp_path):
    p = _write(tmp_path)
    res, code = _run(tmp_path, "target-reject", "Not A Fit Co",
                     "--reason", "not AI-native")
    assert code == 0 and res["status"] == "ok"

    row = next(c for c in _load(p)["rejected"] if c["name"] == "Not A Fit Co")
    assert row["reason"] == "not AI-native"
    assert row["date"]


def test_rejected_company_is_not_added_to_companies(tmp_path):
    p = _write(tmp_path)
    _run(tmp_path, "target-reject", "Not A Fit Co", "--reason", "wrong customer")
    assert "Not A Fit Co" not in [c["name"] for c in _load(p)["companies"]]


def test_target_reject_is_idempotent(tmp_path):
    p = _write(tmp_path)
    _run(tmp_path, "target-reject", "Not A Fit Co", "--reason", "x")
    _run(tmp_path, "target-reject", "Not A Fit Co", "--reason", "x")
    names = [c["name"] for c in _load(p)["rejected"]]
    assert names.count("Not A Fit Co") == 1


def test_rejection_suppresses_future_discovery_proposals(tmp_path):
    """The compounding property, end to end.

    Reject a company, then ask the collector's dedup helper what it knows. If the
    rejection did not register, that company comes back to the inbox next week.
    """
    sys.path.insert(0, str(TOOLS))
    from agent_core import load_known_names

    p = _write(tmp_path)
    _run(tmp_path, "target-reject", "Not A Fit Co", "--reason", "not AI-native")
    known = load_known_names([str(p)])
    assert any("not a fit co" in k for k in known)


COMMENTED_CONFIG = """\
# Career page scan targets
# ATS options: greenhouse, lever, ashby, generic
#
# Finding slugs:
#   Greenhouse: boards.greenhouse.io/SLUG

companies:
  # -- From pipeline (verified ATS from application URLs) --

  - name: Existing Co
    ats: generic
    # Verify slug - custom careers site
    careers_url: "https://careers.example.com/jobs"
    active: false  # Set to true after verifying careers URL works
"""


def test_target_add_preserves_comments(tmp_path):
    """yaml.safe_dump strips comments. This file documents how to find ATS slugs
    and why individual targets are disabled — re-serializing it destroys that."""
    p = _write(tmp_path, COMMENTED_CONFIG)
    before = p.read_text(encoding="utf-8")
    _run(tmp_path, "target-add", "Northwind AI", "--lane", "b", "--outreach")
    after = p.read_text(encoding="utf-8")

    for comment in ("# Career page scan targets",
                    "# ATS options: greenhouse, lever, ashby, generic",
                    "#   Greenhouse: boards.greenhouse.io/SLUG",
                    "# -- From pipeline (verified ATS from application URLs) --",
                    "# Verify slug - custom careers site",
                    "# Set to true after verifying careers URL works"):
        assert comment in after, f"lost comment: {comment}"
    assert before.rstrip() in after or "Existing Co" in after


def test_target_reject_preserves_comments(tmp_path):
    p = _write(tmp_path, COMMENTED_CONFIG)
    _run(tmp_path, "target-reject", "Nope Co", "--reason", "not a fit")
    after = p.read_text(encoding="utf-8")
    assert "# Career page scan targets" in after
    assert "# Set to true after verifying careers URL works" in after
    assert yaml.safe_load(after)["rejected"][0]["name"] == "Nope Co"


def test_existing_target_values_survive_an_add(tmp_path):
    """The inactive flag and careers_url must not be rewritten or dropped."""
    p = _write(tmp_path, COMMENTED_CONFIG)
    _run(tmp_path, "target-add", "Northwind AI", "--lane", "b", "--outreach")
    row = next(c for c in _load(p)["companies"] if c["name"] == "Existing Co")
    assert row["active"] is False
    assert row["careers_url"] == "https://careers.example.com/jobs"


def test_add_then_reject_then_add_keeps_file_valid(tmp_path):
    """Interleaved writes must not corrupt the document."""
    p = _write(tmp_path, COMMENTED_CONFIG)
    _run(tmp_path, "target-add", "A Co", "--lane", "b", "--outreach")
    _run(tmp_path, "target-reject", "B Co", "--reason", "no")
    _run(tmp_path, "target-add", "C Co", "--lane", "b", "--outreach")
    doc = _load(p)
    names = [c["name"] for c in doc["companies"]]
    assert names == ["Existing Co", "A Co", "C Co"]
    assert [c["name"] for c in doc["rejected"]] == ["B Co"]
    assert "# Career page scan targets" in p.read_text(encoding="utf-8")


def test_write_is_validated_and_rolled_back_on_corruption(tmp_path, monkeypatch):
    """A text-level append must verify its own result, not assume it.

    The writer appends raw lines rather than re-serialising, which is what
    preserves comments. Nothing verified the file afterwards, so a malformed
    append would land silently. Simulated here by forcing the rendered entry to
    be invalid YAML.
    """
    p = _write(tmp_path, COMMENTED_CONFIG)
    before = p.read_text(encoding="utf-8")

    sys.path.insert(0, str(TOOLS))
    import act_apply

    monkeypatch.setattr(act_apply, "_render_entry",
                        lambda row, indent="  ": "  - name: [unclosed")
    rc = _run_inproc(act_apply, tmp_path, "target-add", "Bad Co",
                     "--lane", "b", "--outreach")
    assert rc != 0
    assert p.read_text(encoding="utf-8") == before, "file was not rolled back"


def test_validation_catches_a_dropped_preexisting_entry(tmp_path, monkeypatch):
    """The check that matters most: an append that loses existing content."""
    p = _write(tmp_path, COMMENTED_CONFIG)
    before = p.read_text(encoding="utf-8")

    sys.path.insert(0, str(TOOLS))
    import act_apply

    # Simulate a writer that clobbers the file instead of appending.
    def clobber(path, key, row):
        path.write_text("companies:\n  - name: Only Me\n", encoding="utf-8")

    monkeypatch.setattr(act_apply, "_append_entry", clobber)
    rc = _run_inproc(act_apply, tmp_path, "target-add", "New Co",
                     "--lane", "b", "--outreach")
    assert rc != 0
    assert p.read_text(encoding="utf-8") == before


def test_validation_catches_comment_loss(tmp_path, monkeypatch):
    """Regression guard for the exact bug that wiped 14 comments."""
    p = _write(tmp_path, COMMENTED_CONFIG)
    before = p.read_text(encoding="utf-8")

    sys.path.insert(0, str(TOOLS))
    import act_apply, yaml as _y

    def reserialise(path, key, row):
        doc = _y.safe_load(path.read_text(encoding="utf-8")) or {}
        doc.setdefault(key, []).append(row)
        path.write_text(_y.safe_dump(doc, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(act_apply, "_append_entry", reserialise)
    rc = _run_inproc(act_apply, tmp_path, "target-add", "New Co",
                     "--lane", "b", "--outreach")
    assert rc != 0, "re-serialisation dropped comments and was not caught"
    assert p.read_text(encoding="utf-8") == before


def test_valid_write_still_succeeds(tmp_path):
    """Control: validation must not reject correct writes."""
    p = _write(tmp_path, COMMENTED_CONFIG)
    res, code = _run(tmp_path, "target-add", "Good Co", "--lane", "b", "--outreach")
    assert code == 0 and res["status"] == "ok"
    assert "Good Co" in [c["name"] for c in _load(p)["companies"]]
    assert "# Career page scan targets" in p.read_text(encoding="utf-8")


def test_dry_run_writes_nothing(tmp_path):
    p = _write(tmp_path)
    before = p.read_text(encoding="utf-8")
    res, code = _run(tmp_path, "--dry-run", "target-add", "Northwind AI",
                     "--lane", "b", "--outreach")
    assert code == 0
    assert res.get("dry_run") is True
    assert p.read_text(encoding="utf-8") == before
