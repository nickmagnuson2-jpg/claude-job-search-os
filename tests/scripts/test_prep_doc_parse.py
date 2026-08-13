"""W4 — the shared prep-doc locator and the frozen proof-line regex.

/prep-interview writes these lines, /follow-up Step 3e reads the reserve slot out of
them, and check_prep_doc validates them. All three import from here, so a drift in the
regex cannot make the follow-up safety gate silently no-op.
"""
import json
import sys

from conftest import FIXTURES_DIR, TOOLS_DIR, run_script_raw

sys.path.insert(0, str(TOOLS_DIR))
import prep_doc_parse  # noqa: E402

W4 = FIXTURES_DIR / "w4"


def test_newest_by_mmddyy_prefix_wins(tmp_path):
    """070126 / 073126 / 081026 in one directory -> 081026."""
    doc = prep_doc_parse.find_prep_doc("acme-corp", repo_root=W4)
    assert doc == "output/acme-corp/081026-prep.md", doc


def test_missing_or_empty_slug_directory_returns_null(tmp_path):
    assert prep_doc_parse.find_prep_doc("no-such-company", repo_root=tmp_path) is None

    empty = tmp_path / "output" / "northwind-inc"
    empty.mkdir(parents=True)
    assert prep_doc_parse.find_prep_doc("northwind-inc", repo_root=tmp_path) is None


def test_cli_reports_null_for_a_missing_company(tmp_path):
    proc = run_script_raw("prep_doc_parse.py", "--company-slug", "no-such-company",
                          "--repo-root", tmp_path)
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"doc": None}


def test_frozen_regex_parses_a_compliant_pair():
    proofs = prep_doc_parse.parse_proofs(W4 / "compliant-prep.md")
    assert proofs["primary"]["tag"] == "customer-ops"
    assert proofs["reserve"]["tag"] == "product-analytics"
    assert "onboarding" in proofs["primary"]["proof"]
    assert proofs["primary"]["line"] < proofs["reserve"]["line"]


def test_frozen_regex_rejects_a_near_miss():
    """`**Primary proof**: no domain tag` must not parse as a binding."""
    proofs = prep_doc_parse.parse_proofs(W4 / "no-domain-tag.md")
    assert proofs["primary"] is None
    assert proofs["reserve"] is not None


def test_missing_reserve_is_none_not_a_crash():
    proofs = prep_doc_parse.parse_proofs(W4 / "missing-reserve.md")
    assert proofs["primary"] is not None
    assert proofs["reserve"] is None
