"""/remember's contact_note goes through networking_write.py log (finding 4.F1, 2026-11-04).

networking_write.py is the only writer of data/networking.md. Until 2026-11-04
remember_apply.apply_contact_note rewrote the file itself, with its own section match (the
first heading, archived or not), its own entry block (no Follow-up line) and no Last
Interaction update, so a /remember note and a /networking log of the same event left
different records. These tests pin that the note now comes out exactly as
`networking_write.py log --type remember` writes it, and that the duplicate-reply check
stays in ONE place: apply_destinations' pre-flight, which writes nothing on a refusal.
"""
import json
import os
import subprocess
import sys
from datetime import datetime

import pytest

from conftest import write_fixture, TOOLS_DIR, REPO_ROOT

sys.path.insert(0, str(TOOLS_DIR))

NETWORKING = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |

## Interaction Log

### Jane Doe — Acme Corp

#### 2026-01-05 | email (outbound) | Intro

**Follow-up:** —

"""

OUTREACH = """\
# Outreach Log

| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-01-09 | cold-outreach | email | Jane Doe | Acme Corp | Hi Jane | Sent |
| 2026-01-10 | follow-up | email | Jane Doe | Acme Corp | Re: Hi Jane | Sent |
"""

NOTE = "Jane asked for   a call\nnext week"


def today():
    return datetime.now().strftime("%Y-%m-%d")


def run(tool, *args):
    r = subprocess.run([sys.executable, str(TOOLS_DIR / tool), *[str(a) for a in args]],
                       capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT),
                       timeout=60)
    return json.loads(r.stdout), r.returncode


def remember(root, *dests, note=NOTE):
    return run("remember_apply.py", "--repo-root", root, "--note", note,
               "--destinations", json.dumps(list(dests)))


def note_dest(direction="none", entity="Jane Doe", **extra):
    return {"type": "contact_note", "entity": entity, "direction": direction, **extra}


def setup(root, networking=NETWORKING):
    write_fixture(root, "data/networking.md", networking)
    write_fixture(root, "data/outreach-log.md", OUTREACH)


def read(root, rel="data/networking.md"):
    return (root / rel).read_text(encoding="utf-8")


def load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("remember_apply_via_writer",
                                                  str(TOOLS_DIR / "remember_apply.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_remember_apply_never_writes_networking_md_itself(tmp_path, monkeypatch):
    """The test 4.F1 asks for: it fails while remember_apply writes the file itself."""
    setup(tmp_path)
    mod = load()
    real = mod.write_atomic

    def guarded(path, content):
        assert path.name != "networking.md", "remember_apply wrote data/networking.md itself"
        return real(path, content)

    monkeypatch.setattr(mod, "write_atomic", guarded)
    result = mod.apply_contact_note("Met for coffee", note_dest(), tmp_path)
    assert result["status"] == "ok", result
    assert f"#### {today()} | remember | Met for coffee" in read(tmp_path)


@pytest.mark.parametrize("direction", ["none", "out", "in", "both"])
def test_the_note_is_written_exactly_as_networking_write_logs_it(tmp_path, direction):
    a, b = tmp_path / "a", tmp_path / "b"
    setup(a)
    setup(b)
    r, c = remember(a, note_dest(direction))
    assert c == 0 and r["status"] == "ok", r
    r, c = run("networking_write.py", "--repo-root", b, "log", "Jane Doe", "--date", today(),
               "--type", "remember", "--summary", "Jane asked for a call next week",
               "--direction", direction, "--no-todo")
    assert c == 0, r
    assert read(a) == read(b)
    assert read(a, "data/outreach-log.md") == read(b, "data/outreach-log.md")


def test_the_note_updates_last_interaction(tmp_path):
    setup(tmp_path)
    remember(tmp_path, note_dest())
    assert f"| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | {today()} | — |" in read(tmp_path)


def test_a_contact_not_in_the_roster_is_refused_and_nothing_is_written(tmp_path):
    setup(tmp_path)
    before = read(tmp_path)
    r, c = remember(tmp_path, note_dest(entity="Rex Random"))
    assert c != 0 and r["code"] == "not_found", r
    assert read(tmp_path) == before


def test_the_note_lands_in_the_live_section_not_an_archived_one(tmp_path):
    archived = NETWORKING.replace("## Interaction Log\n\n",
                                  "## Interaction Log\n\n### [ARCHIVED] Jane Doe — Old Co\n\n"
                                  "#### 2025-01-01 | email | old\n\n")
    setup(tmp_path, archived)
    remember(tmp_path, note_dest())
    text = read(tmp_path)
    live = text.index("### Jane Doe — Acme Corp")
    assert text.index(f"| remember | Jane asked for a call next week") > live
    assert text.index("### [ARCHIVED] Jane Doe") < live


def test_a_reply_flips_one_outreach_row_only(tmp_path):
    setup(tmp_path)
    r, c = remember(tmp_path, note_dest("in"))
    assert c == 0 and r["outreach_log_updated"] is True, r
    rows = [ln for ln in read(tmp_path, "data/outreach-log.md").splitlines() if "Jane Doe" in ln]
    assert [ln.rstrip(" |").split("| ")[-1] for ln in rows] == ["Sent", "Replied"], rows


def test_a_reply_with_no_open_row_keeps_remembers_warning(tmp_path):
    setup(tmp_path)
    write_fixture(tmp_path, "data/outreach-log.md", OUTREACH.replace("| Sent |", "| Replied |"))
    r, c = remember(tmp_path, note_dest("in"))
    assert c == 0 and r["outreach_log_updated"] is False, r
    assert "No open outreach row found for Jane Doe" in r["warning"]


def test_the_note_writes_no_todo(tmp_path):
    setup(tmp_path)
    write_fixture(tmp_path, "data/job-todos.md", "# To-Dos\n")
    remember(tmp_path, note_dest(), note="Jane said follow up next week")
    assert read(tmp_path, "data/job-todos.md") == "# To-Dos\n"


def test_two_reply_notes_for_one_contact_in_one_call_are_refused(tmp_path):
    """Round 5, 11.F1: two inbound notes for the same contact in one call are the same reply
    logged twice. The pre-flight reads the call in order, as the writer would, and refuses
    the second; nothing is written."""
    setup(tmp_path)
    before = read(tmp_path), read(tmp_path, "data/outreach-log.md")
    r, c = remember(tmp_path, note_dest("in"), note_dest("in"))
    assert c != 0 and r["code"] == "possible_duplicate_reply" and r["written"] == 0, r
    assert (read(tmp_path), read(tmp_path, "data/outreach-log.md")) == before


def test_a_send_between_two_reply_notes_in_one_call_lets_both_through(tmp_path):
    setup(tmp_path)
    r, c = remember(tmp_path, note_dest("in"), note_dest("out"), note_dest("both"))
    assert c == 0 and not r["has_errors"], r
    assert read(tmp_path).count("| remember (") == 3


def test_a_new_reply_flag_lets_a_second_note_in_one_call_through(tmp_path):
    setup(tmp_path)
    r, c = remember(tmp_path, note_dest("in"), note_dest("in", new_reply=True))
    assert c == 0 and not r["has_errors"], r
    assert read(tmp_path).count("| remember (inbound) |") == 2


def test_reply_notes_for_two_contacts_in_one_call_are_both_logged(tmp_path):
    setup(tmp_path, NETWORKING.replace(
        "| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |",
        "| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |\n"
        "| Sam Example | Beta Inc | VP | peer | 2026-01-01 | — | — |"))
    r, c = remember(tmp_path, note_dest("in"), note_dest("in", entity="Sam Example"))
    assert c == 0 and not r["has_errors"], r
    assert read(tmp_path).count("| remember (inbound) |") == 2


def test_an_unlisted_contact_refuses_the_whole_call(tmp_path):
    """Round 5, 9.F1: roster membership is checked in the pre-flight, so a not_found note
    leaves the call's other destinations unwritten too."""
    setup(tmp_path)
    write_fixture(tmp_path, "data/notes.md", "# Job Search Notes\n\n## Decisions\n\n## Notes\n")
    before = read(tmp_path), read(tmp_path, "data/notes.md")
    r, c = remember(tmp_path, {"type": "general_note"}, note_dest(),
                    note_dest(entity="Rex Random"))
    assert c != 0 and r["code"] == "not_found" and r["written"] == 0, r
    assert "Rex Random" in r["message"]
    assert (read(tmp_path), read(tmp_path, "data/notes.md")) == before


def test_a_missing_networking_file_refuses_the_whole_call(tmp_path):
    write_fixture(tmp_path, "data/notes.md", "# Job Search Notes\n\n## Decisions\n\n## Notes\n")
    r, c = remember(tmp_path, {"type": "general_note"}, note_dest())
    assert c != 0 and r["code"] == "file_not_found" and r["written"] == 0, r
    assert read(tmp_path, "data/notes.md") == "# Job Search Notes\n\n## Decisions\n\n## Notes\n"


def test_the_roster_match_ignores_case_and_surrounding_space(tmp_path):
    setup(tmp_path)
    r, c = remember(tmp_path, note_dest(entity="  jane doe "))
    assert c == 0, r


def test_an_open_reply_is_still_refused_once_with_nothing_written(tmp_path):
    md = NETWORKING.replace("#### 2026-01-05 | email (outbound) | Intro",
                            "#### 2026-01-12 | email (inbound) | Jane replied\n\n"
                            "#### 2026-01-05 | email (outbound) | Intro")
    setup(tmp_path, md)
    before = read(tmp_path)
    r, c = remember(tmp_path, note_dest("in"))
    assert c != 0 and r["code"] == "possible_duplicate_reply" and r["refused"] == 1, r
    assert read(tmp_path) == before


def test_new_reply_true_still_logs_past_an_open_reply(tmp_path):
    md = NETWORKING.replace("#### 2026-01-05 | email (outbound) | Intro",
                            "#### 2026-01-12 | email (inbound) | Jane replied\n\n"
                            "#### 2026-01-05 | email (outbound) | Intro")
    setup(tmp_path, md)
    r, c = remember(tmp_path, note_dest("in", new_reply=True))
    assert c == 0, r
    assert f"#### {today()} | remember (inbound) | Jane asked for a call next week" in read(tmp_path)


def test_a_writer_refusal_reaches_the_caller_with_its_code(tmp_path):
    setup(tmp_path)
    r, c = remember(tmp_path, note_dest(entity=""))
    assert c != 0 and r["status"] == "error" and r["code"] == "not_found", r


def test_apply_outreach_reply_reports_the_row_it_set(tmp_path):
    """contact_note calls it only after the writer set no row, but the function's own contract
    still holds: when it does set a row it says so and names the file."""
    setup(tmp_path)
    out = load().apply_outreach_reply("Jane Doe", tmp_path)
    assert out == {"outreach_log_updated": True,
                   "outreach_file": str(tmp_path / "data/outreach-log.md")}
    assert read(tmp_path, "data/outreach-log.md").count("| Replied |") == 1


def test_a_name_with_a_pipe_passes_the_preflight_and_is_logged(tmp_path):
    """Merge of table-routing (2026-11-04): networking_write stores a name through
    sanitize_cell ('|' -> '/'), so the pre-flight's roster check must compare the same way,
    or it refuses a contact the writer would find."""
    setup(tmp_path, NETWORKING.replace(
        "| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |",
        "| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |\n"
        "| Sam/Example | Beta | VP | peer | 2026-01-01 | — | — |"))
    r, c = remember(tmp_path, note_dest(entity="Sam|Example"))
    assert c == 0, r
    assert f"| Sam/Example | Beta | VP | peer | 2026-01-01 | {today()} | — |" in read(tmp_path)


def test_apply_contact_note_returns_the_writers_refusal_directly(tmp_path):
    """Since round 5 the pre-flight refuses an unlisted contact before apply_contact_note
    runs, so the call-level test above no longer reaches this branch. Called directly, a
    refusal from networking_write.py log comes back as an error carrying the writer's code
    (round-6 prep mutation: the branch could be dropped or return None unnoticed)."""
    setup(tmp_path)
    out = load().apply_contact_note(NOTE, note_dest(entity="Nobody Listed"), tmp_path)
    assert out["status"] == "error" and out["type"] == "contact_note", out
    assert out["code"] == "not_found", out
    assert out["message"].startswith("networking_write.py log refused:"), out
    assert "Nobody Listed" not in read(tmp_path)


OPEN_REPLY = NETWORKING.replace("#### 2026-01-05 | email (outbound) | Intro",
                                "#### 2026-01-12 | email (inbound) | Jane replied\n\n"
                                "#### 2026-01-05 | email (outbound) | Intro")


@pytest.mark.parametrize("alias", ["Jane  Doe", " Jane Doe ", "Jane\tDoe"])
def test_an_open_reply_is_refused_under_a_name_the_writer_would_normalise(tmp_path, alias):
    """Round 6 F1 (P0): networking_write stores and looks up a name through sanitize_cell
    (whitespace collapsed, '|' -> '/'), but the duplicate-reply pre-flight used the raw name,
    missed the open reply, and remember then passed --new-reply, so the reply was logged twice."""
    setup(tmp_path, OPEN_REPLY)
    before = read(tmp_path)
    r, c = remember(tmp_path, note_dest("in", entity=alias))
    assert c != 0 and r["code"] == "possible_duplicate_reply", r
    assert read(tmp_path) == before


def test_an_open_reply_is_refused_for_a_pipe_alias(tmp_path):
    md = NETWORKING.replace(
        "| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |",
        "| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |\n"
        "| Sam/Example | Beta | VP | peer | 2026-01-01 | — | — |") + (
        "### Sam/Example — Beta\n\n#### 2026-01-12 | email (inbound) | Sam replied\n\n"
        "**Follow-up:** —\n\n")
    setup(tmp_path, md)
    before = read(tmp_path)
    r, c = remember(tmp_path, note_dest("in", entity="Sam|Example"))
    assert c != 0 and r["code"] == "possible_duplicate_reply", r
    assert read(tmp_path) == before


def test_two_reply_notes_under_aliases_of_one_contact_are_refused(tmp_path):
    """The in-call simulation keys contacts the way the writer stores them, so two spellings
    of one contact in the same call are one contact."""
    setup(tmp_path)
    before = read(tmp_path)
    r, c = remember(tmp_path, note_dest("in"), note_dest("in", entity="Jane  Doe"))
    assert c != 0 and r["code"] == "possible_duplicate_reply", r
    assert read(tmp_path) == before


def test_the_writer_still_refuses_a_reply_that_landed_after_the_preflight(tmp_path):
    """Round 7 F1 (P0): two overlapping /remember calls each passed the pre-flight, and every
    reply note went to the writer with --new-reply, which skips its live check, so the second
    call logged the same reply again. Calling apply_contact_note after the reply is already in
    the file is exactly that second call: the writer must refuse it."""
    setup(tmp_path, OPEN_REPLY)
    before = read(tmp_path)
    out = load().apply_contact_note(NOTE, note_dest("in"), tmp_path)
    assert out["status"] == "error" and out["code"] == "possible_duplicate_reply", out
    assert read(tmp_path) == before


def test_new_reply_true_still_passes_the_writer(tmp_path):
    setup(tmp_path, OPEN_REPLY)
    out = load().apply_contact_note(NOTE, note_dest("in", new_reply=True), tmp_path)
    assert out["status"] == "ok", out
    assert f"#### {today()} | remember (inbound) | Jane asked for a call next week" in read(tmp_path)


def test_a_reply_arriving_mid_call_cannot_split_the_call(tmp_path, monkeypatch, capsys):
    """Round 8 F1: the pre-flight released the networking lock before the destinations were
    written, so a reply another writer logged in between made the writer refuse this call's
    contact note after its earlier destinations were written. The call now holds the lock
    from the pre-flight to the last destination: the concurrent writer waits (here: gives up,
    `locked`) and this call is written whole."""
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(tmp_path / "locks"))
    setup(tmp_path)
    ra = load()
    concurrent = {}
    original = ra.HANDLERS["general_note"]

    def general_then_a_concurrent_reply(n, d, r):
        out = original(n, d, r)
        p = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "networking_write.py"), "--repo-root", str(tmp_path),
             "log", "Jane Doe", "--date=2026-06-19", "--type=email", "--summary=Jane replied",
             "--direction=in", "--no-todo"],
            capture_output=True, text=True, encoding="utf-8",
            # Another session's writer: not this call's child, so it does not inherit the
            # variable that tells remember's own writer its parent holds the lock.
            env={**{k: v for k, v in os.environ.items() if k != "NETWORKING_LOCK_HELD_BY"},
                 "PYTHONIOENCODING": "utf-8", "NETWORKING_LOCK_TIMEOUT": "0.3"},
            cwd=str(REPO_ROOT))
        concurrent.update(json.loads(p.stdout))
        return out

    monkeypatch.setitem(ra.HANDLERS, "general_note", general_then_a_concurrent_reply)
    ra.apply_destinations(NOTE, [{"type": "general_note"}, note_dest("in")], tmp_path, False)
    out = json.loads(capsys.readouterr().out)
    assert concurrent.get("code") == "locked", concurrent
    assert out["status"] == "ok" and out["has_errors"] is False, out
    assert read(tmp_path).count("(inbound)") == 1


def test_a_failing_destination_still_releases_the_lock(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(tmp_path / "locks"))
    setup(tmp_path)
    ra = load()

    def boom(n, d, r):
        raise RuntimeError("handler failed")

    monkeypatch.setitem(ra.HANDLERS, "general_note", boom)
    ra.apply_destinations(NOTE, [{"type": "general_note"}, note_dest("out")], tmp_path, False)
    capsys.readouterr()
    sys.path.insert(0, str(TOOLS_DIR))
    import inbox_lock
    with inbox_lock.file_lock(tmp_path / "data/networking.md", timeout=0):
        pass


def test_remember_waits_for_another_writer_and_writes_nothing_if_it_cannot(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("NETWORKING_LOCK_TIMEOUT", "0.2")
    setup(tmp_path)
    before = read(tmp_path)
    sys.path.insert(0, str(TOOLS_DIR))
    import inbox_lock
    with inbox_lock.file_lock(tmp_path / "data/networking.md"):
        r, c = remember(tmp_path, {"type": "general_note"}, note_dest("in"))
    assert c != 0 and r["code"] == "locked", r
    assert read(tmp_path) == before
    assert not (tmp_path / "data/notes.md").exists()


def test_a_backdated_reply_note_runs_no_outreach_fallback(tmp_path):
    """Review of e644b586, Codex F1: when the contact holds a newer (here future-dated)
    entry, today's note is backdated; the writer skips the outreach step on purpose, so
    remember_apply's fallback must not mark the row Replied itself."""
    future = NETWORKING.replace("#### 2026-01-05 | email (outbound) | Intro",
                                "#### 2099-01-01 | email (outbound) | Planned\n\n"
                                "**Follow-up:** —\n\n#### 2026-01-05 | email (outbound) | Intro")
    setup(tmp_path, networking=future)
    r, c = remember(tmp_path, note_dest("in"))
    assert c == 0 and r["status"] == "ok", r
    assert read(tmp_path, "data/outreach-log.md") == OUTREACH
    assert "outreach_log_updated" not in r and "warning" not in r, r
