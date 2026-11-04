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


def test_two_reply_notes_in_one_call_are_not_half_written(tmp_path):
    """The pre-flight is the ONE duplicate check (it saw no open reply for either). If the
    writer checked again, the second would be refused after the first was written."""
    setup(tmp_path)
    r, c = remember(tmp_path, note_dest("in"), note_dest("in"))
    assert c == 0 and not r["has_errors"], r
    assert read(tmp_path).count(f"| remember (inbound) |") == 2


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
