"""One duplicate-reply rule, two callers: /remember's pre-flight and networking_write log.

Rounds 6 and 7 of the dedupe-inbound review each found a reply logged twice because
remember_apply's pre-flight was a second copy of the writer's rule and drifted from it (a
different name form; a check made at a different time). The pre-flight now runs the writer's
own code (networking_write.check_log_sequence, built on apply_log). This test holds them
together: for every scenario, the pre-flight refuses exactly what the real writer, called once
per note in order, would refuse, at the same note, with the same code.
"""
import json
import os
import subprocess
import sys
from datetime import datetime

import pytest

from conftest import write_fixture, TOOLS_DIR, REPO_ROOT

sys.path.insert(0, str(TOOLS_DIR))
import networking_write  # noqa: E402
import remember_apply  # noqa: E402

ROW_JANE = "| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |"
ROW_SAM = "| Sam/Example | Beta | VP | peer | 2026-01-01 | — | — |"
ROW_LEE = "| Lee Sample | Gamma | CTO | peer | 2026-01-01 | — | — |"


def networking(rows, sections):
    return ("# Networking\n\n## Contacts\n\n"
            "| Name | Company | Role | Relationship | Added | Last Interaction | Email |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            + "\n".join(rows) + "\n\n## Interaction Log\n\n" + sections)


def section(heading, *entries):
    body = "".join(f"#### {e}\n\n**Follow-up:** —\n\n" for e in entries)
    return f"### {heading}\n\n{body}"


OPEN = section("Jane Doe — Acme Corp", "2026-01-12 | email (inbound) | Jane replied",
               "2026-01-05 | email (outbound) | Intro")
ANSWERED = section("Jane Doe — Acme Corp", "2026-01-05 | email (outbound) | Intro")
TWO_WAY = section("Jane Doe — Acme Corp", "2026-01-12 | call (two-way) | Jane called back")

SCENARIOS = {
    "open reply, in": ([ROW_JANE], OPEN, [("Jane Doe", "in", False)]),
    "open reply, in with new_reply": ([ROW_JANE], OPEN, [("Jane Doe", "in", True)]),
    "open reply, out": ([ROW_JANE], OPEN, [("Jane Doe", "out", False)]),
    "open reply, none": ([ROW_JANE], OPEN, [("Jane Doe", "none", False)]),
    "open two-way, in": ([ROW_JANE], TWO_WAY, [("Jane Doe", "in", False)]),
    "answered, in then in": ([ROW_JANE], ANSWERED,
                             [("Jane Doe", "in", False), ("Jane Doe", "in", False)]),
    "answered, in, out, in": ([ROW_JANE], ANSWERED,
                              [("Jane Doe", "in", False), ("Jane Doe", "out", False),
                               ("Jane Doe", "in", False)]),
    "answered, both then in": ([ROW_JANE], ANSWERED,
                               [("Jane Doe", "both", False), ("Jane Doe", "in", False)]),
    "open reply, whitespace alias": ([ROW_JANE], OPEN, [("Jane  Doe", "in", False)]),
    "answered, two aliases in one call": ([ROW_JANE], ANSWERED,
                                          [("Jane Doe", "in", False), ("Jane\tDoe", "in", False)]),
    "pipe alias of an open reply": ([ROW_JANE, ROW_SAM],
                                    ANSWERED + section("Sam/Example — Beta",
                                                       "2026-01-12 | email (inbound) | Sam replied"),
                                    [("Sam|Example", "in", False)]),
    "open reply only in an archived section": (
        [ROW_JANE], section("[ARCHIVED] Jane Doe — Acme Corp",
                            "2026-01-12 | email (inbound) | Jane replied"),
        [("Jane Doe", "in", False)]),
    "unlisted contact": ([ROW_JANE], ANSWERED, [("Nobody Listed", "in", False)]),
    "two contacts, one reply each": ([ROW_JANE, ROW_LEE], ANSWERED,
                                     [("Jane Doe", "in", False), ("Lee Sample", "in", False)]),
}

SUMMARY = "Jane asked for a call next week"
DUP = "possible_duplicate_reply"

# The verdict each scenario must get, written out by hand: (index of the refused note, code)
# or None. With one implementation the pre-flight and the writer cannot disagree, so parity
# alone would pass if the shared rule itself broke (for example the duplicate check moved
# back under "a live section exists", which let an archived reply be logged twice).
EXPECTED = {
    "open reply, in": (0, DUP),
    "open reply, in with new_reply": None,
    "open reply, out": None,
    "open reply, none": None,
    "open two-way, in": (0, DUP),
    "answered, in then in": (1, DUP),
    "answered, in, out, in": None,
    "answered, both then in": (1, DUP),
    "open reply, whitespace alias": (0, DUP),
    "answered, two aliases in one call": (1, DUP),
    "pipe alias of an open reply": (0, DUP),
    "open reply only in an archived section": (0, DUP),
    "unlisted contact": (0, "not_found"),
    "two contacts, one reply each": None,
}


def today():
    return datetime.now().strftime("%Y-%m-%d")


def preflight(tmp_path, notes):
    """/remember's verdict: (index of the refused note, code), or None."""
    dests = [{"type": "contact_note", "entity": n, "direction": d,
              **({"new_reply": True} if nr else {})} for n, d, nr in notes]
    content = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    r = remember_apply.contact_note_refusal(SUMMARY, dests, content)
    return None if r is None else (r.get("index"), r["code"])


def writer(tmp_path, notes):
    """The real writer, one log per note in call order, as /remember applies them."""
    for i, (n, d, nr) in enumerate(notes):
        cmd = [sys.executable, str(TOOLS_DIR / "networking_write.py"), "--repo-root", str(tmp_path),
               "log", n, f"--date={today()}", "--type=remember", f"--summary={SUMMARY}",
               f"--direction={d}", "--no-todo"] + (["--new-reply"] if nr else [])
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
        out = json.loads(p.stdout)
        if p.returncode != 0 or out.get("status") != "ok":
            return (i, out.get("code"))
    return None


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_preflight_and_writer_refuse_the_same_note(tmp_path, monkeypatch, name):
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(tmp_path / "locks"))
    rows, sections, notes = SCENARIOS[name]
    write_fixture(tmp_path, "data/networking.md", networking(rows, sections))
    write_fixture(tmp_path, "data/outreach-log.md",
                  "# Outreach Log\n\n| Date | Skill | Channel | Recipient | Company | "
                  "Subject / Summary | Status |\n| --- | --- | --- | --- | --- | --- | --- |\n")
    before = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    expected = preflight(tmp_path, notes)
    assert (tmp_path / "data/networking.md").read_text(encoding="utf-8") == before, \
        "the pre-flight wrote to networking.md"
    assert expected == EXPECTED[name], "pre-flight verdict"
    assert writer(tmp_path, notes) == EXPECTED[name], "writer verdict"


def test_every_scenario_has_an_expected_verdict():
    assert set(EXPECTED) == set(SCENARIOS)


def test_the_scenarios_cover_each_outcome():
    """Guards the table: a parity test where every case passes, or every case refuses,
    would agree for the wrong reason."""
    outcomes = set()
    for rows, sections, notes in SCENARIOS.values():
        lines = networking(rows, sections).splitlines()
        r = networking_write.check_log_sequence(lines, [
            {"name": n, "direction": d, "new_reply": nr} for n, d, nr in notes],
            log_date=today(), entry_type="remember", summary=SUMMARY)
        outcomes.add(None if r is None else r["code"])
    assert outcomes == {None, "possible_duplicate_reply", "not_found"}


def test_check_mode_writes_nothing_and_reports_the_writers_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(tmp_path / "locks"))
    write_fixture(tmp_path, "data/networking.md", networking([ROW_JANE], OPEN))
    before = (tmp_path / "data/networking.md").read_text(encoding="utf-8")
    base = [sys.executable, str(TOOLS_DIR / "networking_write.py"), "--repo-root", str(tmp_path),
            "log", "Jane Doe", "--date=2026-06-19", "--type=email", "--summary=x", "--no-todo",
            "--check"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    refused = subprocess.run(base + ["--direction=in"], capture_output=True, text=True,
                             encoding="utf-8", env=env, cwd=str(REPO_ROOT))
    assert refused.returncode != 0
    assert json.loads(refused.stdout)["code"] == "possible_duplicate_reply"
    passed = subprocess.run(base + ["--direction=out"], capture_output=True, text=True,
                            encoding="utf-8", env=env, cwd=str(REPO_ROOT))
    out = json.loads(passed.stdout)
    assert passed.returncode == 0 and out["status"] == "ok" and out["checked"] is True, out
    assert (tmp_path / "data/networking.md").read_text(encoding="utf-8") == before


def test_a_note_without_a_direction_is_still_checked_against_the_roster(tmp_path):
    """check_log_sequence: a note whose direction is missing or invalid gets the roster check
    only (remember refuses it later as direction_required), so an unlisted contact is refused
    not_found before anything in the call is written."""
    lines = networking([ROW_JANE], ANSWERED).splitlines()
    for d in (None, "sideways"):
        r = networking_write.check_log_sequence(
            lines, [{"name": "Nobody Listed", "direction": d}],
            log_date=today(), entry_type="remember", summary=SUMMARY)
        assert r is not None and (r["code"], r["index"], r["name"]) == (
            "not_found", 0, "Nobody Listed"), r
        assert networking_write.check_log_sequence(
            lines, [{"name": "Jane Doe", "direction": d}],
            log_date=today(), entry_type="remember", summary=SUMMARY) is None


def test_remember_refusal_messages_tell_nick_what_to_do(tmp_path):
    """The pre-flight rewrites the writer's refusal into /remember's own instructions."""
    content = networking([ROW_JANE], OPEN)
    dup = remember_apply.contact_note_refusal(
        SUMMARY, [{"type": "contact_note", "entity": "Jane  Doe", "direction": "in"}], content)
    assert dup["code"] == "possible_duplicate_reply"
    assert dup["message"].startswith("Jane Doe already has a reply logged"), dup
    assert '"new_reply": true' in dup["message"] and "2026-01-12" in dup["message"], dup
    missing = remember_apply.contact_note_refusal(
        SUMMARY, [{"type": "contact_note", "entity": "Nobody Listed", "direction": "in"}], content)
    assert missing["code"] == "not_found"
    assert missing["message"] == ("Nobody Listed is not in the Contacts table of "
                                  "data/networking.md, so the note cannot be logged there."), missing
