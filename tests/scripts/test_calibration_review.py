"""calibration_review.py apply: a worksheet edit that was not applied is not `ok`.

2026-10-13. An edit whose LABEL is not one of the allowed labels was listed under
`skipped` while the command printed status ok and exited 0, so a typo in a label looked
applied. A missing worksheet did the same. Fixtures are synthetic: data/ is gitignored.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "calibration_review.py"

RECORDS = [
    {"entity": "Acme Widgets", "label": "unlabeled", "final_label": "unlabeled"},
    {"entity": "Northwind Systems", "label": "engaged", "final_label": "engaged"},
]


def _repo(tmp_path, worksheet=None):
    d = tmp_path / "data" / "calibration"
    d.mkdir(parents=True)
    (d / "company-outcomes.json").write_text(json.dumps(RECORDS), encoding="utf-8")
    if worksheet is not None:
        (d / "review-worksheet.md").write_text(worksheet, encoding="utf-8")
    return tmp_path


def _apply(root):
    r = subprocess.run([sys.executable, str(SCRIPT), "apply", "--repo-root", str(root)],
                       capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout)


def _sheet(root):
    r = subprocess.run([sys.executable, str(SCRIPT), "worksheet", "--repo-root", str(root)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return root / "data" / "calibration" / "review-worksheet.md"


def _records(root):
    return {r["entity"]: r for r in json.loads(
        (root / "data" / "calibration" / "company-outcomes.json").read_text(encoding="utf-8"))}


def _relabel(sheet, entity, label):
    """Set the LABEL line of one company's block in a generated worksheet."""
    text = sheet.read_text(encoding="utf-8")
    head, sep, tail = text.partition(entity)
    assert sep, f"{entity} not in the worksheet"
    before, marker, rest = tail.partition("LABEL:")
    assert marker
    line, nl, after = rest.partition("\n")
    sheet.write_text(head + sep + before + f"LABEL: {label}" + nl + after, encoding="utf-8")


def test_a_valid_edit_applies_at_exit_0(tmp_path):
    root = _repo(tmp_path)
    _relabel(_sheet(root), "Acme Widgets", "engaged")
    code, out = _apply(root)
    assert code == 0 and out["status"] == "ok" and out["skipped"] == []
    assert out["relabels"] == [{"entity": "Acme Widgets", "from": "unlabeled", "to": "engaged"}]
    assert _records(root)["Acme Widgets"]["final_label"] == "engaged"


def test_an_invalid_label_is_partial_and_exit_1(tmp_path):
    root = _repo(tmp_path)
    sheet = _sheet(root)
    _relabel(sheet, "Acme Widgets", "engagd")
    _relabel(sheet, "Northwind Systems", "rejected-early")
    code, out = _apply(root)
    assert code == 1 and out["status"] == "partial"
    assert [c["entity"] for c in out["skipped"]] == ["Acme Widgets"]
    recs = _records(root)
    assert recs["Acme Widgets"]["final_label"] == "unlabeled", "the bad label is not written"
    assert recs["Northwind Systems"]["final_label"] == "rejected-early", "the good edit still lands"


def test_a_missing_worksheet_is_an_error_and_exit_1(tmp_path):
    code, out = _apply(_repo(tmp_path))
    assert code == 1 and out["status"] == "error" and "review-worksheet" in out["error"]


def test_an_untouched_worksheet_changes_nothing_at_exit_0(tmp_path):
    root = _repo(tmp_path)
    _sheet(root)
    code, out = _apply(root)
    assert code == 0 and out["status"] == "ok" and out["relabels"] == []


def test_an_edit_for_a_company_not_in_the_ledger_is_partial_and_exit_1(tmp_path):
    """Removed or renamed after the worksheet was generated: the edit matched no record
    and the command used to print status ok."""
    root = _repo(tmp_path)
    sheet = _sheet(root)
    _relabel(sheet, "Acme Widgets", "engaged")
    ledger = root / "data" / "calibration" / "company-outcomes.json"
    ledger.write_text(json.dumps([r for r in RECORDS if r["entity"] != "Northwind Systems"]),
                      encoding="utf-8")
    code, out = _apply(root)
    assert code == 1 and out["status"] == "partial"
    assert out["skipped"] == [{"entity": "Northwind Systems",
                               "skipped": "no such company in the ledger"}]
    assert _records(root)["Acme Widgets"]["final_label"] == "engaged"
