"""W4 — the seven prep-doc checks.

Checks 1-3: a prep doc binds a primary AND a reserve proof, in canonically different
domains. Checks 4-6: it may not tell Nick to withhold an artifact unless a stamp says
that artifact actually arrived. Check 7: the Primary proof may not name a source listed in
DATED_PROOF_SOURCES unless the doc records Nick's override with a reason.

The two cases that encode real incidents:
  * test_synonym_collapse_fails_check_3 — a "reserve" in the primary's own domain
  * test_suppressive_without_stamp_fails — the 2026-08-10 sentence itself

People and companies are placeholders, except the owner's own past employers, which
appear as proof sources because check 7 matches on one by name. This file is public.
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
    if payload["status"] == "NO_CASES":
        assert proc.returncode == 3 and "absent" in payload["reason"]
    else:
        assert payload["status"] == "PASS" and proc.returncode == 0, payload


# --- No cases is a failure, not a skip (2026-10-13) ---------------------------

def _validate_local_in(cwd):
    import subprocess as _sp
    import sys as _sys
    from pathlib import Path as _P
    script = _P(__file__).resolve().parents[2] / "tools" / "check_prep_doc.py"
    r = _sp.run([_sys.executable, str(script), "--validate-local"], cwd=str(cwd),
                capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout)


def test_validate_local_with_no_validators_file_is_exit_3(tmp_path):
    code, payload = _validate_local_in(tmp_path)
    assert code == 3 and payload["status"] == "NO_CASES" and "absent" in payload["reason"]


def test_validate_local_with_a_file_holding_no_cases_for_this_tool_is_exit_3(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / ".local-validators.json").write_text(
        json.dumps({"other": [{"x": 1}], "w4": []}), encoding="utf-8")
    code, payload = _validate_local_in(tmp_path)
    assert code == 3 and payload["status"] == "NO_CASES"
    assert "no 'w4' cases" in payload["reason"]


def test_validate_local_with_a_case_runs_it_instead_of_reporting_no_cases(tmp_path):
    """Hermetic: the case points at something that is not there, so it must FAIL. What
    matters is that a file WITH cases is judged, and a broken case is exit 1, not 3."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / ".local-validators.json").write_text(
        json.dumps({"w4": [{"doc": "output/acme/no-such-prep.md"}]}), encoding="utf-8")
    code, payload = _validate_local_in(tmp_path)
    assert payload["status"] == "FAIL" and code == 1, payload
    assert len(payload["results"]) == 1 and payload["results"][0]["pass"] is False


# --- Check 7: a dated source never fills the Primary proof slot ---------------
# Two fires: a reviewer ranked an old proof first on strength alone, and the
# candidate had already rejected it as dated. The Reserve slot is exempt by design.

_RECENT = "rebuilt the onboarding path at Acme and cut client ramp time by half."
_DATED = "ESPN, sized an ad-delivery gap against in-season traffic."


def _proof_doc(tmp_path, primary: str, reserve: str, extra: str = "",
               after_primary: str = "") -> tuple[int, dict]:
    doc = tmp_path / "acme-prep.md"
    doc.write_text(
        "# Acme Corp prep\n\n## THE ONE THING\n\n"
        f"**Primary proof** (domain: customer-ops): {primary}\n"
        + (f"{after_primary}\n" if after_primary else "")
        + f"**Reserve proof** (domain: product-analytics): {reserve}\n"
        f"{extra}\n## Logistics\n\n- Camera on.\n",
        encoding="utf-8",
    )
    proc = run_script_raw("check_prep_doc.py", str(doc))
    return proc.returncode, json.loads(proc.stdout)


_MARKER = "<!-- proof_override: nick 2026-10-22 asked to lead with this one -->"

# Every passing case below is paired with the same doc changed in exactly one way that
# must FAIL check 7, so each test dies if check 7 is removed (final review 564.F3).


def test_dated_source_as_primary_proof_fails_check_7(tmp_path):
    code, report = _proof_doc(tmp_path, _DATED, _RECENT)
    assert code == 1
    assert 7 in failed_checks(report)
    assert "proof_override" in "".join(f["message"] for f in report["failures"])


def test_dated_source_in_reserve_only_passes(tmp_path):
    code, report = _proof_doc(tmp_path, _RECENT, _DATED)
    assert code == 0, report["failures"]
    assert 7 in failed_checks(_proof_doc(tmp_path, _DATED, _RECENT)[1])


def test_dated_primary_with_override_on_the_primary_line_passes(tmp_path):
    code, report = _proof_doc(tmp_path, _DATED, _RECENT, after_primary=_MARKER)
    assert code == 0, report["failures"]
    assert 7 in failed_checks(_proof_doc(tmp_path, _DATED, _RECENT)[1])


def test_an_override_elsewhere_in_the_doc_does_not_count(tmp_path):
    """554.F1: the override must sit with the Primary proof it excuses, not anywhere."""
    code, report = _proof_doc(tmp_path, _DATED, _RECENT, extra=_MARKER)
    assert code == 1 and 7 in failed_checks(report)


def test_override_with_no_reason_is_not_an_override(tmp_path):
    code, report = _proof_doc(tmp_path, _DATED, _RECENT,
                              after_primary="<!-- proof_override: nick -->")
    assert code == 1 and 7 in failed_checks(report)


def test_recent_primary_is_clean(tmp_path):
    reserve = "McKinsey contact-center pilot, built the business case off tracking data."
    code, report = _proof_doc(
        tmp_path, "Zuora, chief of staff, led the AI adoption with one measurable use case.",
        reserve)
    assert code == 0, report["failures"]
    assert 7 in failed_checks(_proof_doc(tmp_path, _DATED, reserve)[1])


def test_dated_source_match_is_word_bounded(tmp_path):
    """'espnet' or 'respnse' must not read as the dated source."""
    code, report = _proof_doc(tmp_path, "trained an ESPnet speech model at Acme.", _RECENT)
    assert 7 not in failed_checks(report)
    assert 7 in failed_checks(
        _proof_doc(tmp_path, "trained an ESPN speech model at Acme.", _RECENT)[1])


def test_override_helper_is_false_without_a_primary_line():
    """check_doc reaches the helper only when a Primary proof exists, so the
    no-Primary branch is pinned directly: it must return False, not crash."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "cpd_override", Path(__file__).resolve().parents[2] / "tools" / "check_prep_doc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._override_beside_primary(
        "# Prep\n<!-- proof_override: nick a reason -->\n") is False
    assert mod._override_beside_primary(
        "**Primary proof** (d): x <!-- proof_override: nick a reason -->") is True
