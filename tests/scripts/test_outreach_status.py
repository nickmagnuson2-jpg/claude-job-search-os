"""W1 — send-state derives from data/outreach-log.md, split sent/delivered, per-artifact.

The defect these lock down: a prep doc asserted "CV already sent, do
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
        "| 2026-02-03 | cold-outreach | email | Jane Doe | Acme Corp | Intro call scheduling; offered to send a CV if useful | Replied |",
        "| 2026-02-11 | follow-up | email | John Smith | Acme Corp | Notes from the call; CV promised once the role is posted | Replied |",
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


def test_set_status_without_artifact_addresses_the_row_by_recipient_and_date(tmp_path):
    """A plain LinkedIn DM carries no artifact. Marking it Replied must not require one."""
    log = make_log(tmp_path, [
        "| 2026-10-21 | cold-outreach | linkedin | Jane Doe | Acme Corp | Peer DM, compare notes | Sent |",
        "| 2026-10-21 | cold-outreach | linkedin | John Smith | Northwind Inc | Peer DM | Sent |",
        "| 2026-10-20 | cold-outreach | linkedin | Jane Doe | Acme Corp | Earlier connect note | Sent |",
    ])
    before = log.read_text(encoding="utf-8").splitlines()

    code, out = query("--set-status", "--recipient", "Jane Doe", "--date", "2026-10-21",
                      "--status", "Replied", "--log", log)
    assert code == 0, out
    assert out["ok"] is True and out["before"] == "Sent" and out["after"] == "Replied"

    after = log.read_text(encoding="utf-8").splitlines()
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(changed) == 1, f"exactly one line may change, got {changed}"
    assert "Jane Doe" in after[changed[0]] and "2026-10-21" in after[changed[0]]
    assert after[changed[0]].rstrip().endswith("| Replied |")


def test_set_status_without_artifact_refuses_two_rows_on_the_same_date(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-10-21 | cold-outreach | linkedin | Jane Doe | Acme Corp | Connect note | Sent |",
        "| 2026-10-21 | follow-up | email | Jane Doe | Acme Corp | CV attached (a.pdf) | Sent |",
    ])
    before = log.read_bytes()

    code, out = query("--set-status", "--recipient", "Jane Doe", "--date", "2026-10-21",
                      "--status", "Replied", "--log", log)
    assert code == 2
    assert out["ok"] is False and len(out["candidates"]) == 2
    assert "--artifact" in out["reason"], "the refusal must say how to disambiguate"
    assert log.read_bytes() == before, "an ambiguous write must leave the file byte-identical"


def test_set_status_with_no_row_on_that_date_writes_nothing(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-10-20 | cold-outreach | linkedin | Jane Doe | Acme Corp | Connect note | Sent |",
    ])
    before = log.read_bytes()

    code, out = query("--set-status", "--recipient", "Jane Doe", "--date", "2026-10-21",
                      "--status", "Replied", "--log", log)
    assert code == 2 and out["ok"] is False and out["candidates"] == []
    assert "--artifact" not in out["reason"], "no row to narrow, so no --artifact hint"
    assert log.read_bytes() == before


def test_set_status_to_the_current_value_is_a_clean_no_op(tmp_path):
    """Re-applying the status a row already has must succeed without writing, not report
    a failed write. Found 2026-10-21 on the real log: it returned ok:false, rolled_back."""
    log = make_log(tmp_path, [
        # Hand-edited padding: a rewrite would normalize it, so byte identity proves no write.
        "| 2026-10-21 | cold-outreach | linkedin | Jane Doe | Acme Corp | Peer DM |  Replied  |",
    ])
    before = log.read_bytes()

    code, out = query("--set-status", "--recipient", "Jane Doe", "--date", "2026-10-21",
                      "--status", "Replied", "--log", log)
    assert code == 0, out
    assert out["ok"] is True and out["before"] == out["after"] == "Replied"
    assert log.read_bytes() == before


# ------------------------------------------------- latest open row (shared writer)


def test_latest_picks_the_newest_open_row_and_skips_closed_ones(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-10-01 | cold-outreach | email | Jane Doe | Acme Corp | First note | Sent |",
        "| 2026-10-05 | follow-up | email | Jane Doe | Acme Corp | Nudge | Sent |",
        "| 2026-10-09 | follow-up | email | Jane Doe | Acme Corp | Already answered | Replied |",
    ])
    code, out = query("--set-status", "--latest", "--recipient", "Jane Doe",
                      "--status", "Replied", "--log", log)
    assert code == 0, out
    assert out["row"]["date"] == "2026-10-05" and out["before"] == "Sent"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert [l.rstrip().endswith("| Replied |") for l in lines[-3:]] == [False, True, True]


def test_latest_breaks_a_same_date_tie_by_position_since_the_log_is_chronological(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-10-21 | cold-outreach | linkedin | Jane Doe | Acme Corp | Morning DM | Sent |",
        "| 2026-10-21 | follow-up | email | Jane Doe | Acme Corp | Afternoon email | Sent |",
    ])
    code, out = query("--set-status", "--latest", "--recipient", "Jane Doe",
                      "--status", "Replied", "--log", log)
    assert code == 0, out
    assert out["row"]["summary"] == "Afternoon email"


def test_latest_with_no_open_row_writes_nothing(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-10-21 | cold-outreach | linkedin | Jane Doe | Acme Corp | DM | Replied |",
    ])
    before = log.read_bytes()
    code, out = query("--set-status", "--latest", "--recipient", "Jane Doe",
                      "--status", "Replied", "--log", log)
    assert code == 2 and out["ok"] is False
    assert log.read_bytes() == before


def test_latest_and_date_are_mutually_exclusive(tmp_path):
    log = make_log(tmp_path, [
        "| 2026-10-21 | cold-outreach | linkedin | Jane Doe | Acme Corp | DM | Sent |",
    ])
    code, out = query("--set-status", "--latest", "--date", "2026-10-21",
                      "--recipient", "Jane Doe", "--status", "Replied", "--log", log)
    assert code == 2 and "error" in out
    code, out = query("--set-status", "--recipient", "Jane Doe",
                      "--status", "Replied", "--log", log)
    assert code == 2 and "--date" in out["error"], "without --latest, --date is required"


@pytest.mark.parametrize("address", [["--latest"], ["--date", "2026-10-21"]])
def test_set_status_refuses_an_ambiguous_name_on_both_paths(tmp_path, address):
    """The shared matcher refuses rather than guesses; neither row may change."""
    log = make_log(tmp_path, [
        "| 2026-10-21 | cold-outreach | email | Jane Doe | Acme Corp | Note | Sent |",
        "| 2026-10-21 | cold-outreach | email | Mark Doe | Northwind Inc | Note | Sent |",
    ])
    before = log.read_bytes()
    code, out = query("--set-status", *address, "--recipient", "Doe",
                      "--status", "Replied", "--log", log)
    assert code == 2 and out["reason"] == "ambiguous recipient"
    assert out["candidates"] == ["jane doe", "mark doe"]
    assert log.read_bytes() == before


def test_status_cell_write_keeps_the_column_aligned(tmp_path):
    """Only the status cell changes, padded like every other cell."""
    log = make_log(tmp_path, [
        "| 2026-10-21 | cold-outreach | linkedin | Jane Doe | Acme Corp | DM | Sent |",
    ])
    query("--set-status", "--latest", "--recipient", "Jane Doe",
          "--status", "No reply", "--log", log)
    assert log.read_text(encoding="utf-8").splitlines()[-1] == (
        "| 2026-10-21 | cold-outreach | linkedin | Jane Doe | Acme Corp | DM | No reply |")


def _function_calls(path: Path, func: str) -> set[str]:
    """Names of every call made inside one top-level function of a tool."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func)
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return names


@pytest.mark.parametrize("tool,func", [
    ("networking_write.py", "update_outreach_status"),
    ("remember_apply.py", "apply_outreach_reply"),
])
def test_reply_callers_call_set_latest_open_and_no_write_function_directly(tool, func):
    """Regression test for the two copies removed 2026-10-21: each caller must call
    set_latest_open and must make no direct write call in its own body. It inspects only
    that body, so a write moved into a helper would pass; it does not prove that
    write_status is the only writer in tools/."""
    calls = _function_calls(TOOLS / tool, func)
    assert "set_latest_open" in calls, calls
    assert not calls & {"write_text", "write_atomic", "open", "replace"}, calls


def test_no_other_tool_writes_a_status_literal_into_a_cell():
    """Backstop for a copy elsewhere, in the shape both old copies used."""
    writers = sorted(p.name for p in TOOLS.glob("*.py")
                     if STATUS_LITERAL_INTO_CELL.search(p.read_text(encoding="utf-8")))
    assert writers == [], writers


TOOLS = Path(__file__).resolve().parents[2] / "tools"
STATUS_LITERAL_INTO_CELL = re.compile(
    r"\w+\[\d+\]\s*=\s*f?[\"'][^\"']*\b(Replied|No reply|Bounced|Delivered|Sent)\b")


def test_the_literal_guard_catches_the_old_copies():
    assert STATUS_LITERAL_INTO_CELL.search('    cols[6] = "Replied"\n')
    assert not STATUS_LITERAL_INTO_CELL.search('    cells[7] = f" {new_status} "\n')


# ------------------------------------------------- write_status, called directly


def _writer():
    sys.path.insert(0, str(TOOLS))
    import outreach_status
    return outreach_status


def _other_writer_before_replace(monkeypatch, os_, edit, times):
    """Simulate another process writing the log after write_status read it and before it
    replaces it: the dangerous order, where a stale whole-file write would erase the edit."""
    real = os_.tempfile.mkstemp
    calls = {"n": 0}

    def mkstemp(*a, **k):
        if calls["n"] < times:
            calls["n"] += 1
            edit(calls["n"])
        return real(*a, **k)

    monkeypatch.setattr(os_.tempfile, "mkstemp", mkstemp)


def test_write_status_keeps_a_concurrent_edit_to_another_row(tmp_path, monkeypatch):
    os_ = _writer()
    log = make_log(tmp_path, [
        "| 2026-10-21 | cold-outreach | email | Jane Doe | Acme Corp | Note | Sent |",
        "| 2026-10-21 | cold-outreach | email | John Smith | Northwind Inc | Note | Sent |",
    ])

    def bounce_john(_):
        log.write_text(log.read_text(encoding="utf-8").replace(
            "| John Smith | Northwind Inc | Note | Sent |",
            "| John Smith | Northwind Inc | Note | Bounced |"), encoding="utf-8")

    _other_writer_before_replace(monkeypatch, os_, bounce_john, times=1)
    code, out = os_.set_status(log, "Jane Doe", "2026-10-21", None, "Replied")
    assert code == 0, out
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[-2].endswith("| Replied |") and lines[-1].endswith("| Bounced |")
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_status_gives_up_when_the_log_keeps_changing(tmp_path, monkeypatch):
    os_ = _writer()
    log = make_log(tmp_path, [
        "| 2026-10-21 | cold-outreach | email | Jane Doe | Acme Corp | Note | Sent |",
    ])

    def append_row(n):
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"| 2026-10-2{n} | follow-up | email | John Smith | Northwind Inc | Row {n} | Sent |\n")

    _other_writer_before_replace(monkeypatch, os_, append_row, times=99)
    code, out = os_.set_latest_open(log, "Jane Doe", "Replied")
    assert code == 1 and out["ok"] is False and "changed" in out["reason"]
    text = log.read_text(encoding="utf-8")
    assert "| Jane Doe | Acme Corp | Note | Sent |" in text, "our write must not land"
    assert text.count("| John Smith |") == os_.WRITE_ATTEMPTS, "every other write is kept"
    assert list(tmp_path.glob("*.tmp")) == []


def test_bare_validate_local_parses():
    """--validate-local must be a valid zero-argument invocation (no argparse exit 2)."""
    proc = run_script_raw("outreach_status.py", "--validate-local")
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
    script = _P(__file__).resolve().parents[2] / "tools" / "outreach_status.py"
    r = _sp.run([_sys.executable, str(script), "--validate-local"], cwd=str(cwd),
                capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout)


def test_validate_local_with_no_validators_file_is_exit_3(tmp_path):
    code, payload = _validate_local_in(tmp_path)
    assert code == 3 and payload["status"] == "NO_CASES" and "absent" in payload["reason"]


def test_validate_local_with_a_file_holding_no_cases_for_this_tool_is_exit_3(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / ".local-validators.json").write_text(
        json.dumps({"other": [{"x": 1}], "w1": []}), encoding="utf-8")
    code, payload = _validate_local_in(tmp_path)
    assert code == 3 and payload["status"] == "NO_CASES"
    assert "no 'w1' cases" in payload["reason"]


def test_validate_local_with_a_case_runs_it_instead_of_reporting_no_cases(tmp_path):
    """Hermetic: the case points at something that is not there, so it must FAIL. What
    matters is that a file WITH cases is judged, and a broken case is exit 1, not 3."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / ".local-validators.json").write_text(
        json.dumps({"w1": [{"recipient": "Casey Doe", "expect": {"state": "replied"}}]}), encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "outreach-log.md").write_text(
        "# Outreach Log\n\n| Date | Skill | Channel | Recipient | Company | Subject / Summary "
        "| Status |\n|---|---|---|---|---|---|---|\n"
        "| 2026-09-01 | follow-up | email | Jordan Sample | Acme | subj | Sent |\n",
        encoding="utf-8")
    code, payload = _validate_local_in(tmp_path)
    assert payload["status"] == "FAIL" and code == 1, payload
    assert len(payload["results"]) == 1 and payload["results"][0]["pass"] is False
