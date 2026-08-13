"""W1 — send-state derives from data/outreach-log.md, split sent/delivered, per-artifact.

The defect these lock down (2026-08-10): a prep doc asserted "CV already sent 8/4, do
not re-offer it" on the sole basis that a draft file existed under output/. The CV had
bounced and was never delivered. The suppressive half is the damaging half — it told
Nick not to fix it.

Two regressions matter more than the rest and are called out by name below:
  * test_bounce_regression          — transmission is not receipt
  * test_artifact_scope_regression  — receipt is per-artifact, not per-recipient

Identities here are placeholders only (Jane Doe / John Smith / Jane Smith / Robin /
Acme Corp / Northwind Inc). This file is public.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXTURES_DIR, run_script_raw, write_fixture

SAMPLE = FIXTURES_DIR / "w1" / "outreach-log-sample.md"

HEADER = """\
# Outreach Log

> Manual edits are fine. Status values: `Drafted` | `Sent` | `Replied` | `No reply` | `Bounced` | `Delivered` | `Delivery unknown`

| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |
| --- | --- | --- | --- | --- | --- | --- |
"""


def make_log(tmp_path: Path, rows: list[str], name: str = "log.md") -> Path:
    """Build a minimal log whose table shape matches the live file."""
    return write_fixture(tmp_path, name, HEADER + "".join(r.rstrip() + "\n" for r in rows))


def query(*args) -> tuple[int, dict]:
    proc = run_script_raw("outreach_status.py", *args)
    assert proc.stdout.strip(), f"no stdout; stderr={proc.stderr}"
    return proc.returncode, json.loads(proc.stdout)


# --------------------------------------------------------------- core state


def test_drafted_only_is_not_sent(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-01-05 | cold-outreach | email | Jane Doe | Acme Corp | Draft only, nothing attached | Drafted |",
    ])
    code, out = query("--recipient", "Jane Doe", "--log", log)
    assert code == 0
    assert out["sent"] is False
    assert out["delivered"] == "unknown"


def test_sent_only_awaits_reply(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-01-05 | cold-outreach | email | Jane Doe | Acme Corp | Cold intro | Sent |",
    ])
    code, out = query("--recipient", "Jane Doe", "--log", log)
    assert code == 0
    assert (out["sent"], out["delivered"], out["awaiting_reply"]) == (True, "unknown", True)


def test_replied_on_cv_row_is_delivered(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-01-20 | follow-up | email | Jane Doe | Acme Corp | Tailored CV attached (012026-placeholder.pdf) | Replied |",
    ])
    code, out = query("--recipient", "Jane Doe", "--artifact", "cv", "--log", log)
    assert code == 0
    assert out["delivered"] is True
    assert out["artifact"] == "cv"


def test_no_reply_still_counts_as_sent(tmp_path):
    """OQ1: `No reply` is a transmission with no receipt evidence — never a suppression."""
    log = make_log(tmp_path, [
        "| 2026-01-05 | cold-outreach | email | Jane Doe | Acme Corp | Cold intro | No reply |",
    ])
    code, out = query("--recipient", "Jane Doe", "--log", log)
    assert code == 0
    assert (out["sent"], out["delivered"], out["awaiting_reply"]) == (True, "unknown", True)


def test_bounce_regression(tmp_path):
    """THE regression: sent is true, delivered is false. A bounce is still a transmission."""
    log = make_log(tmp_path, [
        "| 2026-08-04 | follow-up | email | Jane Doe | Acme Corp | CV attached (080426-placeholder.pdf) | Sent |",
        "| 2026-08-07 | follow-up | email | Jane Doe | Acme Corp | CV attached (080426-placeholder.pdf) - address bounced | Bounced |",
    ])
    code, out = query("--recipient", "Jane Doe", "--artifact", "cv", "--log", log)
    assert code == 0
    assert out["sent"] is True
    assert out["delivered"] is False, "a bounced CV must never read as delivered"


def test_monotonic_not_latest_wins(tmp_path):
    """A later Drafted row does not un-send an earlier Sent row."""
    log = make_log(tmp_path, [
        "| 2026-01-01 | cold-outreach | email | Jane Doe | Acme Corp | Cold intro | Sent |",
        "| 2026-01-05 | follow-up | email | Jane Doe | Acme Corp | Second note, still drafting | Drafted |",
    ])
    code, out = query("--recipient", "Jane Doe", "--log", log)
    assert code == 0
    assert out["sent"] is True


def test_same_date_tie_resolves_to_file_order(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-02-02 | cold-outreach | email | John Smith | Northwind Inc | Cold note re: the ops role | Sent |",
        "| 2026-02-02 | follow-up | email | John Smith | Northwind Inc | Same-day bump on the ops role | No reply |",
    ])
    code, out = query("--recipient", "John Smith", "--log", log)
    assert code == 0
    assert out["latest_status"] == "No reply", "the later row in file order wins the tie"


# --------------------------------------------------------------- resolution


def test_multi_name_cell_resolves_each_person_exactly():
    for name in ("Jane Doe", "John Smith"):
        code, out = query("--recipient", name, "--log", SAMPLE)
        assert code == 0, name
        assert out["resolution"] == "exact", name
        joint = [r for r in out["matched_rows"] if "+" in r["recipient"]]
        assert joint, f"{name} should match the joint 'Jane Doe + John Smith' row"


def test_single_token_recipient():
    code, out = query("--recipient", "Robin", "--log", SAMPLE)
    assert code == 0 and out["resolution"] == "exact"

    code, out = query("--recipient", "Robin Jones", "--log", SAMPLE)
    assert code == 0 and out["resolution"] == "not_found", "a two-token query must not claim Robin"


def test_shared_surname_is_ambiguous_never_a_guess():
    code, out = query("--recipient", "Smith", "--log", SAMPLE)
    assert code == 2
    assert out["resolution"] == "ambiguous"
    assert {"john smith", "jane smith"} <= set(out["candidates"])
    assert out["matched_rows"] == [], "an ambiguous query must not report state"


def test_company_filter_tolerates_company_drift():
    """`Acme Corp (Northwind Inc)` and `Acme Corp` are the same company for filtering."""
    code, out = query("--recipient", "Jane Doe", "--company", "acme", "--log", SAMPLE)
    assert code == 0
    companies = {r["company"] for r in out["matched_rows"]}
    assert "Acme Corp" in companies and "Acme Corp (Northwind Inc)" in companies


def test_as_of_before_only_row_is_not_found():
    code, out = query("--recipient", "Robin", "--as-of", "2026-02-01", "--log", SAMPLE)
    assert code == 0
    assert out["resolution"] == "not_found"
    assert out["sent"] is False


# --------------------------------------------------------------- artifact scope


def test_artifact_scope_regression(tmp_path):
    """THE second regression: a Replied on an UNRELATED thread must not deliver the CV."""
    log = make_log(tmp_path, [
        "| 2026-01-12 | follow-up | email | Jane Doe | Acme Corp | Scheduling thread, nothing attached | Replied |",
        "| 2026-01-20 | follow-up | email | Jane Doe | Acme Corp | Tailored CV attached (012026-placeholder.pdf) | Sent |",
    ])
    code, scoped = query("--recipient", "Jane Doe", "--artifact", "cv", "--log", log)
    assert code == 0
    assert scoped["delivered"] == "unknown", "the reply was on a different thread"

    code, bare = query("--recipient", "Jane Doe", "--log", log)
    assert code == 0
    assert bare["delivered"] == "unknown"


def test_no_artifact_can_never_report_delivered(tmp_path):
    """A recipient-level query must never license an artifact-level suppression."""
    log = make_log(tmp_path, [
        "| 2026-01-20 | follow-up | email | Jane Doe | Acme Corp | CV attached (012026-placeholder.pdf) | Delivered |",
    ])
    code, out = query("--recipient", "Jane Doe", "--log", log)
    assert code == 0
    assert out["artifact"] == "none"
    assert out["delivered"] == "unknown"


def test_named_but_not_sent_is_not_receipt_evidence(tmp_path):
    """Live-data regression: an OFFERED CV on a Replied row is not a delivered CV.

    Found on 2026-08-12 by running --validate-local against the real log, where this
    shape returned delivered:true and re-authorized the historical claim. The rows below
    are invented to reproduce the shape — the real cells are private — but they keep the
    two negative markers ("offered", "promised") that do the actual work.
    """
    log = make_log(tmp_path, [
        "| 2026-07-28 | cold-outreach | email | Jane Doe | Acme Corp | Intro call scheduling; offered to send a CV if useful | Replied |",
        "| 2026-08-06 | follow-up | email | John Smith | Acme Corp | Answers to her questions; CV promised for next week | Replied |",
    ])
    for name in ("Jane Doe", "John Smith"):
        code, out = query("--recipient", name, "--artifact", "cv", "--log", log)
        assert code == 0, name
        assert out["delivered"] == "unknown", f"{name}: naming an artifact is not sending it"


def test_unknown_artifact_token_lists_the_valid_set(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-01-05 | cold-outreach | email | Jane Doe | Acme Corp | Cold intro | Sent |",
    ])
    proc = run_script_raw("outreach_status.py", "--recipient", "Jane Doe",
                          "--artifact", "hologram", "--log", log)
    assert proc.returncode == 4
    out = json.loads(proc.stdout)
    assert "cv" in out["valid_tokens"] and "cover-letter" in out["valid_tokens"]


# --------------------------------------------------------------- hygiene


def test_unrecognized_status_is_never_silently_coerced(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-01-05 | cold-outreach | email | Jane Doe | Acme Corp | Cold intro | Ghosted |",
    ])
    code, out = query("--recipient", "Jane Doe", "--log", log)
    assert code == 3
    assert out["unrecognized_statuses"] == ["Ghosted"]
    assert out["sent"] is False, "an unrecognized status must not count as sent"


def test_stamp_matches_the_frozen_v2_format(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-01-20 | follow-up | email | Jane Doe | Acme Corp | CV attached (012026-placeholder.pdf) | Replied |",
    ])
    proc = run_script_raw("outreach_status.py", "--recipient", "Jane Doe",
                          "--artifact", "cv", "--as-of", "2026-02-01",
                          "--stamp", "--log", log)
    assert proc.returncode == 0
    assert re.fullmatch(
        r"<!-- outreach_status: recipient=jane doe artifact=cv "
        r"delivered=true as_of=2026-02-01 tool_version=2 -->",
        proc.stdout.strip(),
    ), proc.stdout


# --------------------------------------------------------------- writer


def test_set_status_round_trip(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-08-04 | follow-up | email | Jane Doe | Acme Corp | CV attached (080426-placeholder.pdf) | Sent |",
        "| 2026-08-05 | follow-up | email | John Smith | Northwind Inc | Unrelated thread | Sent |",
    ])
    before = log.read_text(encoding="utf-8")

    code, out = query("--set-status", "--recipient", "Jane Doe", "--date", "2026-08-04",
                      "--artifact", "cv", "--status", "Bounced", "--log", log)
    assert code == 0, out
    assert out["ok"] is True and out["before"] == "Sent" and out["after"] == "Bounced"

    after = log.read_text(encoding="utf-8")
    assert len(before.splitlines()) == len(after.splitlines()), "line count must not change"
    changed = [i for i, (a, b) in enumerate(zip(before.splitlines(), after.splitlines())) if a != b]
    assert len(changed) == 1, f"exactly one line may change, got {changed}"

    code, out = query("--recipient", "Jane Doe", "--artifact", "cv", "--log", log)
    assert code == 0
    assert out["delivered"] is False


def test_set_status_refuses_an_ambiguous_target(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-08-04 | follow-up | email | Jane Doe | Acme Corp | CV attached (a.pdf) | Sent |",
        "| 2026-08-04 | follow-up | email | Jane Doe | Acme Corp | CV attached again (b.pdf) | Sent |",
    ])
    before = log.read_bytes()

    code, out = query("--set-status", "--recipient", "Jane Doe", "--date", "2026-08-04",
                      "--artifact", "cv", "--status", "Bounced", "--log", log)
    assert code == 2
    assert out["ok"] is False and len(out["candidates"]) == 2
    assert log.read_bytes() == before, "an ambiguous write must leave the file byte-identical"


def test_bare_validate_local_parses():
    """--validate-local must be a valid zero-argument invocation (no argparse exit 2)."""
    proc = run_script_raw("outreach_status.py", "--validate-local")
    assert proc.returncode != 2, proc.stderr
    payload = json.loads(proc.stdout)
    assert "status" in payload
    if payload["status"] == "SKIPPED":
        assert "absent" in payload["reason"]
