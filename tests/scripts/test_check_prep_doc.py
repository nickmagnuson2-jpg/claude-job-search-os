"""W4 — the six prep-doc checks.

Checks 1-3: a prep doc binds a primary AND a reserve proof, in canonically different
domains. Checks 4-6: it may not tell Nick to withhold an artifact unless a stamp says
that artifact actually arrived.

The two cases that encode real incidents:
  * test_synonym_collapse_fails_check_3 — a "reserve" in the primary's own domain
  * test_suppressive_without_stamp_fails — the 2026-08-10 sentence itself

All identities are placeholders. This file is public.
"""
import json

from conftest import FIXTURES_DIR, run_script_raw

W4 = FIXTURES_DIR / "w4"


def check(fixture: str, *args) -> tuple[int, dict]:
    proc = run_script_raw("check_prep_doc.py", str(W4 / fixture), *args)
    assert proc.stdout.strip(), f"no stdout; stderr={proc.stderr}"
    return proc.returncode, json.loads(proc.stdout)


def failed_checks(report: dict) -> set[int]:
    return {f["check"] for f in report["failures"]}


# --------------------------------------------------------------- proofs (1-3)


def test_compliant_doc_passes():
    code, report = check("compliant-prep.md")
    assert code == 0, report["failures"]
    assert report["compliant"] is True


def test_missing_reserve_fails_check_2():
    code, report = check("missing-reserve.md")
    assert code == 1
    assert 2 in failed_checks(report)
    assert "single point of failure" in "".join(f["message"] for f in report["failures"])


def test_synonym_collapse_fails_check_3():
    """customer-experience + customer-ops name ONE domain. This is the hole W4 closes."""
    code, report = check("synonym-collapse.md")
    assert code == 1
    assert 3 in failed_checks(report)
    msg = "".join(f["message"] for f in report["failures"])
    assert "canonicalize" in msg and "customer-ops" in msg


def test_missing_domain_tag_fails_check_1():
    code, report = check("no-domain-tag.md")
    assert code == 1
    assert 1 in failed_checks(report)


def test_unknown_tag_fails_check_3_with_the_valid_list():
    code, report = check("unknown-tag.md")
    assert code == 1
    assert 3 in failed_checks(report)
    msg = "".join(f["message"] for f in report["failures"])
    assert "vibes" in msg and "product-analytics" in msg


# --------------------------------------------------------------- suppression (4-6)


def test_suppressive_without_stamp_fails():
    """The historical sentence, verbatim in shape: no stamp, no license."""
    code, report = check("081026-suppressive-no-stamp.md")
    assert code == 1
    assert 4 in failed_checks(report)
    assert "draft archive" in "".join(f["message"] for f in report["failures"])


def test_delivered_unknown_is_not_a_license():
    code, report = check("081026-suppressive-unknown.md")
    assert code == 1
    assert 4 in failed_checks(report)
    assert "delivered=true" in "".join(f["message"] for f in report["failures"])


def test_delivered_true_passes_check_4():
    code, report = check("081026-suppressive-delivered.md")
    assert code == 0, report["failures"]
    assert report["compliant"] is True


def test_suppression_naming_no_artifact_fails():
    """"Already handled" suppresses something unnamed — un-auditable, so it fails."""
    code, report = check("081026-no-artifact-named.md")
    assert code == 1
    assert 4 in failed_checks(report)
    assert "must name the artifact" in "".join(f["message"] for f in report["failures"])


def test_recipient_level_stamp_never_licenses_artifact_suppression():
    """artifact=none delivered=true means "they replied to something", not "got the CV"."""
    code, report = check("081026-recipient-level-stamp.md")
    assert code == 1
    assert 4 in failed_checks(report)


def test_stale_stamp_fails_check_5():
    code, report = check("081026-stale-stamp.md")
    assert code == 1
    assert 5 in failed_checks(report)
    assert "predates the doc date" in "".join(f["message"] for f in report["failures"])


def test_malformed_v1_stamp_fails_check_6_rather_than_being_ignored():
    code, report = check("081026-malformed-stamp.md")
    assert code == 1
    assert 6 in failed_checks(report)
    # and the suppression it was meant to justify is still unbacked
    assert 4 in failed_checks(report)


# --------------------------------------------------------------- CLI


def test_unreadable_doc_exits_2():
    proc = run_script_raw("check_prep_doc.py", str(W4 / "does-not-exist.md"))
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["compliant"] is False


def test_bare_validate_local_parses():
    """--validate-local must work with no positional path (no argparse exit 2)."""
    proc = run_script_raw("check_prep_doc.py", "--validate-local")
    assert proc.returncode != 2, proc.stderr
    payload = json.loads(proc.stdout)
    assert "status" in payload
    if payload["status"] == "SKIPPED":
        assert "absent" in payload["reason"]
