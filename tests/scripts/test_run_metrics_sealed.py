"""Tests for the sealed filter in tools/run_metrics.py (KB-S2, 2026-11-26).

Every sealed source in the KB brief's constraint 5 has a test: the sibling directory, the old
in-tree name, a configured vault folder, the SEAL_READ_OK override, a Granola sealed meeting
and a Wispr dictation. Plus the 5-turn window edges, the records that are not touches,
fail-closed configs, and load_session keeping counts while dropping sealed-window text.

Placeholders only: marker VALUES come from tools/check_sealed_read.py by import, the vault
folder is an invented `data/sealed-x` in a temporary config and a temporary vault, and the
meeting identities are example.com placeholders. No test reads a real transcript.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import check_sealed_read as csr  # noqa: E402
import meeting_vocab  # noqa: E402
import run_metrics as rm  # noqa: E402
import vault_paths  # noqa: E402

from test_run_metrics import WINDOW, assistant, human, peer, repo, send, tool_error, ts, write_session  # noqa: E402,F401

FOLDER = "data/sealed-x"
IN_TREE = csr.SEALED_REL.rsplit("/", 1)[-1]
VAULT = csr.PERSONAL.rsplit("/", 1)[-1]


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A temporary checkout whose vault holds FOLDER, so the config below is not fail-closed."""
    root = tmp_path / "checkout"
    root.mkdir()
    (root / csr.PERSONAL / FOLDER).mkdir(parents=True)
    (root / ".git").mkdir()      # a checkout: the filter refuses a root that is not one
    monkeypatch.setattr(csr, "_root", lambda: root)
    return root


@pytest.fixture
def conf(tmp_path, vault):
    p = tmp_path / "sealed.conf"
    p.write_text(f"# placeholder\n{FOLDER}\n")
    return p


def bash(minute, command):
    return assistant(minute, "reading", {"type": "tool_use", "id": f"b{minute}", "name": "Bash",
                                         "input": {"command": command}})


def typed_session(n, touch_at=None, touch=None):
    """n typed turns (0..n-1), each followed by an assistant line; `touch` goes after turn touch_at."""
    rows = []
    for k in range(n):
        rows.append(human(k * 2, f"message {k}"))
        rows.append(assistant(k * 2 + 0.5))
        if k == touch_at:
            rows.append(touch)
    return rows


def kept_texts(res):
    return [m["text"] for m in res.kept]


def run(tmp_path, rows, conf, **kw):
    write_session(tmp_path, "s1", rows)
    return rm.nick_messages(tmp_path / "s1.jsonl", sealed_config=conf, **kw)


def sealed_dir_cmd(minute):
    return bash(minute, f"cat ../{csr.SEALED_HOME}/x")


# ---------------------------------------------------------------- imports from the guard

def test_the_guard_names_this_filter_imports_still_exist():
    # s3-pii rev 7 changes check_sealed_read.py; a rename there must break here, loudly.
    for name in ("SEALED_HOME", "SEALED_REL", "OVERRIDE_WORD", "VAULT_RELS", "FAIL_CLOSED",
                 "PERSONAL", "_root", "_names_sealed", "_PARENT_END", "_bash_reads_sealed",
                 "_under_sealed"):
        assert hasattr(csr, name), name
    assert callable(vault_paths.sealed_rels)
    for name in ("load_sealed_meetings_config", "classify_meeting", "_transcript_names_sealed_contact"):
        assert callable(getattr(meeting_vocab, name, None)), name


def test_markers_are_built_from_the_guard_not_typed_here(conf):
    m = {v: c for c, v in rm.sealed_markers(conf)}
    assert m[csr.SEALED_HOME.lower()] == "sibling"
    assert m[IN_TREE.lower()] == "in_tree"
    assert m[csr.OVERRIDE_WORD.split("=")[0].lower()] == "override"
    assert m[FOLDER] == "vault"
    assert "fail_closed_vault" not in m.values()


def test_the_default_config_is_the_guards_own_reading():
    # Compared as booleans so a failure prints no private folder name.
    marks = rm.sealed_markers(None)
    same_vault = set(csr.VAULT_RELS) <= {v for c, v in marks if c == "vault"}
    same_closed = any(c == "fail_closed_vault" for c, _ in marks) == bool(csr.FAIL_CLOSED)
    assert same_vault and same_closed


# ---------------------------------------------------------------- one test per sealed source

@pytest.mark.parametrize("cls,command_of", [
    ("sibling", lambda: f"cat ../{csr.SEALED_HOME}/notes.md"),
    ("in_tree", lambda: f"cd data && ls {IN_TREE}/"),
    ("vault", lambda: f"cat ../{VAULT}/{FOLDER}/a.md"),
    ("override", lambda: f"{csr.OVERRIDE_WORD} cat ../somewhere/a.md"),
])
def test_each_sealed_source_excludes_its_window(tmp_path, conf, cls, command_of):
    res = run(tmp_path, typed_session(20, touch_at=10, touch=bash(20.7, command_of())), conf)
    assert kept_texts(res) == [f"message {k}" for k in list(range(5)) + list(range(16, 20))]
    assert res.excluded == 11 and res.typed_total == 20
    assert res.touches_by_class == {cls: 1}


@pytest.mark.parametrize("command", [
    f"{csr.OVERRIDE_WORD.split('=')[0]}='1' cat ../somewhere/a.md",   # quoted value
    f"export {csr.OVERRIDE_WORD.split('=')[0]}=\"1\"; cat a.md",      # exported, double-quoted
])
def test_the_override_counts_in_any_spelling_of_its_value(tmp_path, conf, command):
    # Reconciliation 2026-11-26: matching the whole `NAME=1` word kept 9 of KB-A's 2,325
    # excluded messages; the bare name restores exact agreement (0 differences either way).
    res = run(tmp_path, typed_session(3, touch_at=1, touch=bash(2.7, command)), conf)
    assert res.excluded == 3 and res.touches_by_class == {"override": 1}


@pytest.mark.parametrize("cls,command_of", [
    ("vault", lambda: f"cd ../{VAULT}/data && cat {FOLDER.rsplit('/', 1)[-1]}/note.md"),  # basename/
    ("vault_parent", lambda: f"grep -R needle ../{VAULT}/data"),         # recursive read of a parent
    ("vault_parent", lambda: f"grep -R needle ../{VAULT}/data/"),        # same, trailing slash
    ("vault_parent", lambda: f"ls ../{VAULT}/data/*"),                   # same, glob
])
def test_a_vault_folder_counts_in_the_guards_shorter_spellings(tmp_path, conf, cls, command_of):
    # Codex 2026-11-26 F1 (P0): the guard counts the folder's basename when path-shaped and
    # a recursive read of any parent inside the vault; the full `data/<folder>` string alone
    # let the turns around such a read keep their text.
    res = run(tmp_path, typed_session(3, touch_at=1, touch=bash(2.7, command_of())), conf)
    assert res.excluded == 3 and res.touches_by_class.get(cls) == 1


def test_a_named_file_beside_the_sealed_folder_is_not_a_touch(tmp_path, conf):
    # The guard: "A named file beside a sealed folder ... is not a parent." Measured on 629
    # transcripts, a plain substring for the parent added 349 exclusions the guard does not make.
    res = run(tmp_path, typed_session(3, touch_at=1, touch=bash(2.7, f"cat ../{VAULT}/data/log.md")), conf)
    assert res.excluded == 0 and res.touches_by_class == {}


def test_the_guards_own_matcher_is_consulted_on_the_default_config(tmp_path, monkeypatch):
    # The default config uses check_sealed_read._names_sealed itself (quote stripping,
    # parents, basenames) on every decoded string, so a spelling no marker lists still counts.
    seen = []
    monkeypatch.setattr(csr, "_names_sealed", lambda text: seen.append(text) or "MARK-Z" in text)
    write_session(tmp_path, "s1", typed_session(3, touch_at=1, touch=bash(2.7, "cat a\\nMARK-Z/b")))
    res = rm.nick_messages(tmp_path / "s1.jsonl")
    assert res.excluded == 3 and res.touches_by_class == {"guard": 1}
    assert any("MARK-Z" in t for t in seen)


def tool(minute, name, inp):
    return assistant(minute, "working", {"type": "tool_use", "id": f"t{minute}", "name": name, "input": inp})


@pytest.mark.parametrize("record_of", [
    lambda: tool(2.7, "Bash", {"command": f"cd {csr.PERSONAL} && grep -r needle ."}),   # Codex r2 F1
    lambda: tool(2.7, "Grep", {"pattern": "needle", "path": csr.PERSONAL}),
])
def test_a_walk_from_the_vault_root_is_a_touch_on_the_default_config(tmp_path, record_of):
    # Codex 2026-11-26 round 2 F1 (P0): the guard sees this only through its Bash parsing
    # and cd tracking (check_sealed_read._bash_reads_sealed) and its target test
    # (_under_sealed); no string names the sealed folder, so the string matchers miss it.
    res = run(tmp_path, typed_session(3, touch_at=1, touch=record_of()), None)
    assert res.excluded == 3 and res.touches_by_class == {"guard": 1}


def test_a_glob_pattern_rooted_at_the_vault_is_a_touch(tmp_path):
    res = run(tmp_path, typed_session(3, touch_at=1, touch=tool(2.7, "Glob", {"pattern": f"{csr.PERSONAL}/**/*.md"})), None)
    assert res.excluded == 3


def test_a_command_the_guard_cannot_judge_is_a_touch(tmp_path, monkeypatch):
    def boom(_):
        raise RuntimeError("parser failure")
    monkeypatch.setattr(csr, "_bash_reads_sealed", boom)
    res = run(tmp_path, typed_session(3, touch_at=1, touch=tool(2.7, "Bash", {"command": "ls"})), None)
    assert res.excluded == 3 and res.touches_by_class == {"guard": 1}


@pytest.mark.parametrize("record_of", [
    lambda: tool(2.7, "Bash", {"command": f"cd {csr.PERSONAL} && git status"}),
    lambda: tool(2.7, "Read", {"file_path": f"{csr.PERSONAL}/README.md"}),
])
def test_ordinary_vault_root_work_is_not_a_touch(tmp_path, record_of):
    res = run(tmp_path, typed_session(3, touch_at=1, touch=record_of()), None)
    assert res.excluded == 0 and res.touches_by_class == {}


def test_a_vault_root_walk_keeps_its_window_out_of_the_report(tmp_path, repo):
    tx = tmp_path / "tx"
    tx.mkdir()
    write_session(tx, "s1", [assistant(0), human(1, "that is wrong: PRIVATE WINDOW TEXT"),
                             tool(1.5, "Bash", {"command": f"cd {csr.PERSONAL} && grep -r needle ."})])
    rep = rm.build_report(tx, repo, WINDOW[0], WINDOW[1], set(), "r", "main")
    assert "PRIVATE WINDOW TEXT" not in json.dumps(rep) and rep["sealed_excluded_total"] == 1


def test_granola_sealed_meeting_classifies_sealed():
    cfg = {"attendees": {"placeholder.contact@example.com"}, "names": [], "keywords": []}
    meeting = {"title": "Weekly", "attendees": [{"name": "Placeholder Contact",
                                                  "email": "placeholder.contact@example.com"}]}
    assert meeting_vocab.classify_meeting(meeting, config=cfg) == "sealed"


WISPR_CFG = {"attendees": {"placeholder.contact@example.com"}, "names": ["Placeholder Contact"],
             "keywords": ["placeholder-kw"]}


@pytest.mark.parametrize("text", [
    "notes after talking with Placeholder Contact today",          # sealed name
    "invite from placeholder.contact@example.com moved",           # sealed attendee
    "the placeholder-kw session moved",                            # sealed keyword
    f"see ../{VAULT}/{FOLDER}/x.md",                               # sealed marker
])
def test_wispr_row_naming_anything_sealed_is_excluded(conf, text):
    assert rm.sealed_text(text, WISPR_CFG, conf) is True


def test_wispr_row_with_nothing_sealed_is_kept(conf):
    assert rm.sealed_text("draft the follow-up to the recruiter", WISPR_CFG, conf) is False


def test_wispr_fails_closed_without_a_sealed_meetings_config(conf):
    assert rm.sealed_text("draft the follow-up to the recruiter", None, conf, config_loader=lambda: None)


def test_wispr_loads_the_config_when_none_is_passed(conf):
    assert rm.sealed_text("the placeholder-kw session", None, conf, config_loader=lambda: WISPR_CFG)
    assert not rm.sealed_text("draft the follow-up", None, conf, config_loader=lambda: WISPR_CFG)


# ---------------------------------------------------------------- window edges

def test_window_is_five_typed_turns_each_side(tmp_path, conf):
    res = run(tmp_path, typed_session(30, touch_at=12, touch=sealed_dir_cmd(24.7)), conf)
    kept = kept_texts(res)
    assert "message 6" in kept and "message 18" in kept       # k-6, k+6 kept
    for k in (7, 12, 17):                                      # k-5, k, k+5 excluded
        assert f"message {k}" not in kept
    assert res.excluded == 11


def test_window_width_is_a_parameter(tmp_path, conf):
    res = run(tmp_path, typed_session(10, touch_at=5, touch=sealed_dir_cmd(10.7)), conf, window=1)
    assert kept_texts(res) == [f"message {k}" for k in (0, 1, 2, 3, 7, 8, 9)]


def test_a_touch_before_the_first_typed_turn_counts_as_turn_zero(tmp_path, conf):
    res = run(tmp_path, [sealed_dir_cmd(-1)] + typed_session(10), conf)
    assert kept_texts(res) == [f"message {k}" for k in range(6, 10)]


def test_a_hook_block_naming_the_sealed_dir_is_a_touch(tmp_path, conf):
    res = run(tmp_path, typed_session(3, touch_at=1, touch=tool_error(
        2.7, f"PreToolUse:Read hook error: check_sealed_read.py: BLOCKED ../{csr.SEALED_HOME}/x")), conf)
    assert res.excluded == 3


def test_a_sidechain_record_is_a_touch(tmp_path, conf):
    side = dict(sealed_dir_cmd(2.7), isSidechain=True)
    assert run(tmp_path, typed_session(3, touch_at=1, touch=side), conf).excluded == 3


def test_markers_match_in_any_case(tmp_path, conf):
    up = bash(2.7, f"cat ../{csr.SEALED_HOME.upper()}/x")
    assert run(tmp_path, typed_session(3, touch_at=1, touch=up), conf).excluded == 3


def test_an_unparseable_line_naming_the_dir_is_a_touch(tmp_path, conf):
    write_session(tmp_path, "s1", typed_session(3))
    with (tmp_path / "s1.jsonl").open("a") as f:
        f.write(f"{{broken ../{csr.SEALED_HOME}/x\n")
    assert rm.nick_messages(tmp_path / "s1.jsonl", sealed_config=conf).excluded == 3


def test_typed_text_itself_naming_the_dir_is_a_touch(tmp_path, conf):
    assert run(tmp_path, [human(0, f"look in ../{csr.SEALED_HOME}"), assistant(1)], conf).excluded == 1


# ---------------------------------------------------------------- not a touch

@pytest.mark.parametrize("record", [
    {"type": "attachment", "attachment": {"type": "instructions", "content": "SEALDIR"}},
    {"type": "attachment", "attachment": {"type": "skill_listing", "content": "SEALDIR"}},
    {"type": "attachment", "attachment": {"type": "invoked_skills", "content": "SEALDIR"}},
    {"type": "attachment", "attachment": {"type": "queued_command", "content": "SEALDIR"}},
    {"type": "attachment", "attachment": {"type": "nested_memory", "content": "SEALDIR"}},
    {"type": "queue-operation", "content": "SEALDIR"},
    {"type": "api-request", "content": "SEALDIR"},
    {"type": "api-request-blob", "content": "SEALDIR"},
    {"type": "api-request-shape", "content": "SEALDIR"},
    {"type": "user", "isMeta": True, "message": {"role": "user", "content": "SEALDIR"}},
])
def test_always_loaded_instruction_records_are_not_touches(tmp_path, conf, record):
    record = json.loads(json.dumps(dict(record, timestamp=ts(1))).replace("SEALDIR", csr.SEALED_HOME))
    res = run(tmp_path, [human(0, "go"), record, assistant(2)], conf)
    assert res.excluded == 0 and kept_texts(res) == ["go"] and res.touches_by_class == {}


@pytest.mark.parametrize("record", [
    {"type": "attachment", "attachment": {"type": "edited_text_file", "content": "SEALDIR"}},
    {"type": "user", "isMeta": True, "toolUseResult": {"stdout": "SEALDIR"},
     "message": {"role": "user", "content": "SEALDIR"}},
])
def test_file_attachments_and_meta_tool_results_are_touches(tmp_path, conf, record):
    record = json.loads(json.dumps(dict(record, timestamp=ts(1))).replace("SEALDIR", csr.SEALED_HOME))
    assert run(tmp_path, [human(0, "go"), record, assistant(2)], conf).excluded == 1


# ---------------------------------------------------------------- the kept record

def test_kept_messages_carry_uuid_ts_text_and_the_1_based_line(tmp_path, conf):
    rows = [assistant(0), dict(human(1, "first"), uuid="u-1"), assistant(2), dict(human(3, "second"), uuid="u-2")]
    res = run(tmp_path, rows, conf)
    assert res.kept == [{"uuid": "u-1", "ts": ts(1), "text": "first", "line": 2},
                        {"uuid": "u-2", "ts": ts(3), "text": "second", "line": 4}]


def test_only_human_typed_lines_are_turns(tmp_path, conf):
    rows = [human(0, "typed"), peer(1, "proj-org", "a peer note"), dict(human(2, "aside"), isSidechain=True),
            dict(human(3, "meta"), isMeta=True), {"type": "assistant", "timestamp": ts(4), "origin": {"kind": "human"},
                                                  "message": {"role": "assistant", "content": "x"}}]
    res = run(tmp_path, rows, conf)
    assert kept_texts(res) == ["typed"] and res.typed_total == 1


def test_is_typed_can_be_replaced(tmp_path, conf):
    rows = [human(0, "typed"), human(1, "<task-notification>x"), assistant(2)]
    res = run(tmp_path, rows, conf, is_typed=lambda d: rm.is_typed(d) and not
              rm._text_of(d["message"]["content"]).startswith("<task"))
    assert kept_texts(res) == ["typed"] and res.typed_total == 1


# ---------------------------------------------------------------- fail closed

def test_missing_vault_config_fails_closed_to_the_whole_vault_data(tmp_path, vault):
    marks = rm.sealed_markers(tmp_path / "missing.conf")
    assert ("fail_closed_vault", f"{VAULT}/data".lower()) in marks


def test_a_config_naming_a_folder_outside_the_vault_fails_closed(tmp_path, vault):
    bad = tmp_path / "bad.conf"
    bad.write_text("../escape\n")
    assert any(c == "fail_closed_vault" for c, _ in rm.sealed_markers(bad))


def test_a_config_naming_a_folder_that_is_not_there_fails_closed(tmp_path, vault):
    gone = tmp_path / "gone.conf"
    gone.write_text(f"{FOLDER}\ndata/not-there\n")
    marks = rm.sealed_markers(gone)
    assert ("vault", FOLDER) in marks and any(c == "fail_closed_vault" for c, _ in marks)


def test_fail_closed_seals_any_vault_data_path(tmp_path, vault):
    # Fail closed is wider than a parent: every path under the vault's data/ counts.
    res = run(tmp_path, typed_session(3, touch_at=1, touch=bash(2.7, f"cat ../{VAULT}/data/other/a.md")),
              tmp_path / "missing.conf")
    assert res.excluded == 3 and res.touches_by_class == {"fail_closed_vault": 1}


# ---------------------------------------------------------------- run_metrics no longer emits sealed text

def sealed_rows():
    return [assistant(0), human(1, "that is wrong, check the private note"),
            sealed_dir_cmd(1.5),
            peer(2, "proj-org", "correction: wrong file"),
            send(3, "proj-org", "Correction: I misstated it"),
            tool_error(4, "PreToolUse:Bash hook error: check_x.py: BLOCKED the private step")]


PRIVATE = ("private note", "misstated", "wrong file", "private step")


def test_load_session_keeps_counts_but_drops_sealed_window_text(tmp_path, conf, monkeypatch):
    monkeypatch.setattr(rm, "DEFAULT_SEALED_CONFIG", conf, raising=False)
    write_session(tmp_path, "s1", sealed_rows())
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.nick_prompts == 1 and len(s.sent) == 1 and len(s.received) == 1 and len(s.hook_errors) == 1
    assert s.sealed_excluded == 1
    assert s.human[0][1] is None and s.sent[0][2] is None and s.received[0][2] is None
    assert s.hook_errors[0][1] is None
    assert rm.failures(s, names={}, org_names={"proj-org"}) == []


def test_the_report_carries_no_sealed_window_text(tmp_path, conf, monkeypatch, repo):
    monkeypatch.setattr(rm, "DEFAULT_SEALED_CONFIG", conf, raising=False)
    tx = tmp_path / "tx"
    tx.mkdir()
    write_session(tx, "s1", sealed_rows())
    rep = rm.build_report(tx, repo, WINDOW[0], WINDOW[1], {"proj-org"}, "r", "main")
    out = json.dumps(rep)
    assert not any(p in out for p in PRIVATE)
    assert rep["sealed_excluded_total"] == 1 and rep["sessions"][0]["sealed_excluded"] == 1
    assert rep["nick_prompts_total"] == 1


def test_load_session_outside_any_window_is_unchanged(tmp_path, conf, monkeypatch):
    monkeypatch.setattr(rm, "DEFAULT_SEALED_CONFIG", conf, raising=False)
    write_session(tmp_path, "s1", [assistant(0), human(1, "that's wrong, the board is stale")])
    s = rm.load_session(tmp_path / "s1.jsonl", *map(rm.parse_ts, WINDOW))
    assert s.sealed_excluded == 0
    assert [x["catcher_class"] for x in rm.failures(s, names={}, org_names=set())] == ["nick"]


# Codex 2026-11-26 round 3 F1 (P0): a supplied config turned the guard off, so the round-2
# walk from the vault root was again invisible. The guard is asked whatever the config;
# a supplied config only ADDS markers.
@pytest.mark.parametrize("record_of", [
    lambda: tool(2.7, "Bash", {"command": f"cd {csr.PERSONAL} && grep -r needle ."}),
    lambda: tool(2.7, "Grep", {"pattern": "needle", "path": csr.PERSONAL}),
])
def test_a_walk_from_the_vault_root_is_a_touch_with_a_supplied_config(tmp_path, conf, record_of):
    res = run(tmp_path, typed_session(3, touch_at=1, touch=record_of()), conf)
    assert res.excluded == 3 and res.touches_by_class == {"guard": 1}


def test_the_report_drops_a_vault_root_walk_window_with_a_supplied_config(tmp_path, conf, monkeypatch, repo):
    monkeypatch.setattr(rm, "DEFAULT_SEALED_CONFIG", conf, raising=False)
    tx = tmp_path / "tx"
    tx.mkdir()
    write_session(tx, "s1", [assistant(0), human(1, "that is wrong: PRIVATE WINDOW TEXT"),
                             tool(1.5, "Bash", {"command": f"cd {csr.PERSONAL} && grep -r needle ."})])
    rep = rm.build_report(tx, repo, WINDOW[0], WINDOW[1], {"proj-org"}, "r", "main")
    assert "PRIVATE WINDOW TEXT" not in json.dumps(rep)
    assert rep["sealed_excluded_total"] == 1


def write_subagent(tmp_path, rows, sid="s1", name="agent-a"):
    folder = tmp_path / sid / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    write_session(folder, name, rows)


# Fable 2026-11-26 F1 (P0, confirmed on real data: 349 of 3,289 subagent files touch the sealed
# set): a subagent's transcript sits under <session-id>/, so its sealed read was never a touch
# in the parent. Each touching record maps to the parent typed turn current at its timestamp.
def test_a_sealed_read_in_a_subagent_transcript_is_a_touch_in_the_parent(tmp_path):
    write_subagent(tmp_path, [assistant(2.6), sealed_dir_cmd(2.7)])
    res = run(tmp_path, typed_session(3), None, window=0)
    assert res.excluded == 1 and kept_texts(res) == ["message 0", "message 2"]
    assert res.touches_by_class == {"subagent_sibling": 1}


def test_a_subagent_touch_takes_the_usual_window(tmp_path):
    write_subagent(tmp_path, [sealed_dir_cmd(2.7)])
    assert run(tmp_path, typed_session(3), None).excluded == 3


def test_a_clean_subagent_transcript_is_not_a_touch(tmp_path):
    write_subagent(tmp_path, [assistant(2.6), bash(2.7, "cat README.md")])
    res = run(tmp_path, typed_session(3), None)
    assert res.excluded == 0 and res.touches_by_class == {}


def test_a_subagent_touch_with_no_timestamp_seals_every_turn(tmp_path):
    rec = {k: v for k, v in sealed_dir_cmd(2.7).items() if k != "timestamp"}
    write_subagent(tmp_path, [rec])
    assert run(tmp_path, typed_session(4), None, window=0).excluded == 4


def test_the_report_drops_a_subagent_touch_window(tmp_path, monkeypatch, repo):
    tx = tmp_path / "tx"
    tx.mkdir()
    write_session(tx, "s1", [assistant(0), human(1, "that is wrong: PRIVATE WINDOW TEXT"), assistant(1.2)])
    write_subagent(tx, [sealed_dir_cmd(1.5)])
    rep = rm.build_report(tx, repo, WINDOW[0], WINDOW[1], {"proj-org"}, "r", "main")
    assert "PRIVATE WINDOW TEXT" not in json.dumps(rep)
    assert rep["sealed_excluded_total"] == 1


# dc 2026-11-26 14:49 (P0): run from a copy (git show / git archive into scratch), the guard's
# root resolves from the code's location, so the sibling and guard names came from the wrong
# directory and about 150 real turns that should be sealed were kept. A root that is not a
# checkout refuses unless SEAL_REPO_ROOT names one.
COPY_PROBE = "import run_metrics; run_metrics.sealed_markers(); print('ran')"


def _copy_tools(tmp_path):
    import shutil
    dst = tmp_path / "copy" / "tools"
    shutil.copytree(Path(rm.__file__).resolve().parent, dst,
                    ignore=shutil.ignore_patterns("__pycache__", ".*"))
    return dst


def _run_copy(tools, env_root):
    import os
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "SEAL_REPO_ROOT"}
    if env_root:
        env["SEAL_REPO_ROOT"] = str(env_root)
    return subprocess.run([sys.executable, "-c", COPY_PROBE], cwd=tools, env=env,
                          capture_output=True, text=True, timeout=60)


def test_a_copy_outside_any_checkout_refuses(tmp_path):
    proc = _run_copy(_copy_tools(tmp_path), None)
    assert proc.returncode != 0 and "ran" not in proc.stdout
    assert "SEAL_REPO_ROOT" in proc.stderr


def test_a_copy_with_seal_repo_root_set_runs(tmp_path):
    real = Path(rm.__file__).resolve().parents[1]
    proc = _run_copy(_copy_tools(tmp_path), real)
    assert proc.returncode == 0 and "ran" in proc.stdout


def test_sealed_text_asks_the_guard_with_a_supplied_config(conf, monkeypatch):
    monkeypatch.setattr(csr, "_names_sealed", lambda text: "MARK-Z" in text)
    assert rm.sealed_text("see MARK-Z/notes", WISPR_CFG, conf) is True
    assert rm.sealed_text("draft the follow-up to the recruiter", WISPR_CFG, conf) is False


# Mutation 2026-11-26 (c2fdd2b0): the marker dedup and _record_ts's fallback survived.
def test_each_marker_string_appears_once_and_the_fail_closed_class_wins(tmp_path, vault):
    # Two missing folders under one parent: fail-closed, and `<vault>/data` arises three times
    # (the fail-closed marker, then a parent of each folder). It is listed once, as fail-closed.
    p = tmp_path / "two.conf"
    p.write_text("data/missing-a\ndata/missing-b\n")
    marks = rm.sealed_markers(p)
    strings = [m for _, m in marks]
    assert len(strings) == len(set(strings))
    assert [c for c, m in marks if m == f"{VAULT}/data".lower()] == ["fail_closed_vault"]


def test_a_typed_turn_with_an_unparsable_timestamp_takes_the_one_before(tmp_path):
    # It must not break the placement of a subagent touch: turn 1 inherits turn 0's time,
    # so a touch at minute 2.7 still lands on turn 1 (turn 2 starts at minute 4).
    rows = typed_session(3)
    rows[2]["timestamp"] = "not-a-time"
    write_subagent(tmp_path, [sealed_dir_cmd(2.7)])
    res = run(tmp_path, rows, None, window=0)
    assert res.excluded == 1 and kept_texts(res) == ["message 0", "message 2"]
