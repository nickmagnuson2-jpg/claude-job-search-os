"""Tests for tools/title_cut_ruling.py: Nick's "should have shown" rulings on scan cuts (R1 log).

Placeholder companies only (public repo).
"""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
sys.path.insert(0, str(REPO))
from tools import title_cut_ruling as tcr  # noqa: E402

CUTS = [
    {"company": "Acme Corp", "title": "Head of Operations Planning", "location": "San Francisco, CA",
     "url": "https://jobs.example.com/acme/1", "rule": "title_filter",
     "reason": "title not in the target's role_filters"},
    {"company": "Beta Inc", "title": "Strategy Lead", "location": "London", "url": "",
     "rule": "location", "reason": "location: excluded"},
]
NOW = datetime(2026, 1, 7, 15, 0, tzinfo=timezone.utc)


def _root(tmp_path, cuts=CUTS, payload=None):
    (tmp_path / "tools").mkdir(exist_ok=True)
    body = payload if payload is not None else {"scanned_at": "2026-01-06T16:30:00+00:00",
                                                "cut_at_fetch": cuts}
    (tmp_path / "tools" / ".role-queue.json").write_text(
        body if isinstance(body, str) else json.dumps(body))
    return tmp_path


def _key(cut):
    from tools.career_scanner.dedup import role_key
    return hashlib.sha1(role_key(cut).encode()).hexdigest()[:8]


# --- keys ------------------------------------------------------------------------------------

def test_key_is_sha1_of_the_scanner_role_key_first_8_hex():
    assert tcr.cut_key(CUTS[0]) == hashlib.sha1(b"https://jobs.example.com/acme/1").hexdigest()[:8]
    # No URL: company::title, the scanner's fallback identity.
    assert tcr.cut_key(CUTS[1]) == hashlib.sha1(b"beta inc::strategy lead").hexdigest()[:8]
    assert len(tcr.cut_key(CUTS[0])) == tcr.KEY_LEN == 8


def test_the_key_ignores_the_reason_text_so_a_title_gate_rewording_keeps_it():
    assert tcr.cut_key({**CUTS[0], "reason": "title in out-of-lane family 'x'"}) == tcr.cut_key(CUTS[0])


# --- load_cuts ---------------------------------------------------------------------------------

def test_load_cuts_keys_every_cut(tmp_path):
    got = tcr.load_cuts(_root(tmp_path))
    assert got["error"] is None and got["scanned_at"] == "2026-01-06T16:30:00+00:00"
    assert [c["key"] for c in got["cuts"]] == [_key(CUTS[0]), _key(CUTS[1])]
    assert [c["role_key"] for c in got["cuts"]] == [CUTS[0]["url"], "beta inc::strategy lead"]
    assert got["fetch_failures"] == 0
    assert got["cuts"][0]["title"] == "Head of Operations Planning"


# A corrupt queue must not render a partial cut list with no error (funnel-page-1118 tip
# review, Codex F1 + Grok F2, parked P1). Same contract as every other bad queue: an error
# and cuts None, so the page says "Cuts unavailable" and funnel_state records no scan.

@pytest.mark.parametrize("bad", ["not a dict", None, 7, ["a", "list"]])
def test_a_non_dict_cut_is_an_error_not_a_silent_drop(tmp_path, bad):
    got = tcr.load_cuts(_root(tmp_path, cuts=CUTS + [bad]))
    assert got["cuts"] is None and got["scanned_at"] is None
    assert "1 of 3 cut_at_fetch entries" in got["error"] and "not an object" in got["error"]


def test_every_dropped_entry_is_counted(tmp_path):
    got = tcr.load_cuts(_root(tmp_path, cuts=["x", CUTS[0], 3]))
    assert got["cuts"] is None and "2 of 3 cut_at_fetch entries" in got["error"]


def _payload(cuts, count):
    return {"scanned_at": "2026-01-06T16:30:00+00:00", "cut_at_fetch": cuts,
            "cut_at_fetch_count": count}


@pytest.mark.parametrize("count", [3, 1, 0])
def test_a_count_that_disagrees_with_the_list_is_an_error(tmp_path, count):
    got = tcr.load_cuts(_root(tmp_path, payload=_payload(CUTS, count)))
    assert got["cuts"] is None and got["scanned_at"] is None
    assert f"cut_at_fetch_count is {count}" in got["error"] and "lists 2" in got["error"]


def test_a_matching_count_passes(tmp_path):
    got = tcr.load_cuts(_root(tmp_path, payload=_payload(CUTS, 2)))
    assert got["error"] is None and len(got["cuts"]) == 2


@pytest.mark.parametrize("count", ["2", 2.0, True, None, [2]])
def test_a_count_that_is_not_an_integer_is_an_error(tmp_path, count):
    got = tcr.load_cuts(_root(tmp_path, payload=_payload(CUTS, count)))
    assert got["cuts"] is None and "cut_at_fetch_count is not an integer" in got["error"]


def test_a_queue_with_no_count_field_still_loads(tmp_path):
    # Queues written before the count existed; the list alone is checked.
    got = tcr.load_cuts(_root(tmp_path))
    assert got["error"] is None and len(got["cuts"]) == 2


def test_mark_on_a_corrupt_queue_is_exit_2_and_writes_nothing(tmp_path):
    root = _root(tmp_path, cuts=CUTS + ["junk"])
    code, out = tcr.mark(root, _key(CUTS[0]), "should show", now=NOW)
    assert code == 2 and "not an object" in out["error"]
    assert not tcr.rulings_path(root).exists()


@pytest.mark.parametrize("payload,needle", [
    ("{not json", "unreadable role queue"),
    ([1, 2], "no cut_at_fetch list"),
    ({"scanned_at": "x"}, "no cut_at_fetch list"),
    ({"scanned_at": "x", "cut_at_fetch": {"a": 1}}, "corrupt role queue: cut_at_fetch is not a list"),
    ({"scanned_at": "x", "cut_at_fetch": None}, "corrupt role queue: cut_at_fetch is not a list"),
])
def test_load_cuts_degrades_on_a_bad_queue(tmp_path, payload, needle):
    got = tcr.load_cuts(_root(tmp_path, payload=payload if isinstance(payload, str) else payload))
    assert needle in got["error"] and got["cuts"] is None and got["scanned_at"] is None


def test_load_cuts_with_no_queue_says_so(tmp_path):
    got = tcr.load_cuts(tmp_path)
    assert "no role queue" in got["error"] and got["cuts"] is None


def test_load_cuts_never_rewrites_the_queue(tmp_path):
    root = _root(tmp_path, payload="{not json")
    tcr.load_cuts(root)
    assert (root / "tools" / ".role-queue.json").read_text() == "{not json"


# --- mark --------------------------------------------------------------------------------------

def test_mark_appends_one_line_with_the_cut_and_the_reason(tmp_path):
    root = _root(tmp_path)
    code, out = tcr.mark(root, _key(CUTS[0]).upper() + " ", "  ops planning is in lane ", now=NOW)
    assert code == 0
    lines = (root / tcr.RULINGS_REL).read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec == out["logged"]
    assert rec["key"] == _key(CUTS[0]) and rec["role_key"] == CUTS[0]["url"]
    assert rec["reason"] == "ops planning is in lane" and rec["by"] == "nick"
    assert rec["ruling"] == "should_have_shown" and rec["ruled_at"] == "2026-01-07T15:00:00+00:00"
    assert (rec["company"], rec["title"], rec["location"], rec["rule"], rec["cut_reason"]) == (
        "Acme Corp", "Head of Operations Planning", "San Francisco, CA", "title_filter",
        "title not in the target's role_filters")
    assert rec["url"] == CUTS[0]["url"] and rec["scan_at"] == "2026-01-06T16:30:00+00:00"
    assert out["log"] == str(tcr.RULINGS_REL)
    # a second, different role appends, never rewrites
    assert tcr.mark(root, _key(CUTS[1]), "worth a look", by="claude", now=NOW)[0] == 0
    lines = (root / tcr.RULINGS_REL).read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["by"] == "claude"
    assert json.loads(lines[0]) == rec


def test_mark_without_a_now_stamps_the_current_utc_time(tmp_path):
    code, out = tcr.mark(_root(tmp_path), _key(CUTS[0]), "r")
    assert code == 0
    t = datetime.fromisoformat(out["logged"]["ruled_at"])
    assert t.tzinfo is not None and abs((datetime.now(timezone.utc) - t).total_seconds()) < 60


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_mark_refuses_an_empty_reason_and_writes_nothing(tmp_path, reason):
    root = _root(tmp_path)
    code, out = tcr.mark(root, _key(CUTS[0]), reason)
    assert code == 2 and "needs a reason" in out["error"]
    assert not (root / tcr.RULINGS_REL).exists()


def test_mark_refuses_an_unknown_or_empty_key(tmp_path):
    root = _root(tmp_path)
    for key in ("deadbeef", "", "  "):
        code, out = tcr.mark(root, key, "r")
        assert code == 2 and "no cut with key" in out["error"] and "2026-01-06" in out["error"]
    assert not (root / tcr.RULINGS_REL).exists()


def test_mark_with_no_queue_is_exit_2(tmp_path):
    code, out = tcr.mark(tmp_path, "aaaa1111", "r")
    assert code == 2 and "no role queue" in out["error"]


def test_mark_refuses_a_second_ruling_on_the_same_role(tmp_path):
    root = _root(tmp_path)
    assert tcr.mark(root, _key(CUTS[0]), "first", now=NOW)[0] == 0
    code, out = tcr.mark(root, _key(CUTS[0]), "second")
    assert code == 1 and "already ruled on 2026-01-07T15:00:00+00:00: first" in out["error"]
    assert out["ruling"]["reason"] == "first"
    assert len((root / tcr.RULINGS_REL).read_text().splitlines()) == 1


def test_mark_with_an_unreadable_log_is_exit_2_and_does_not_append(tmp_path):
    root = _root(tmp_path)
    log = root / tcr.RULINGS_REL
    log.mkdir(parents=True)  # a directory where the file should be: read fails
    code, out = tcr.mark(root, _key(CUTS[0]), "r")
    assert code == 2 and "unreadable rulings log" in out["error"]


# --- load_rulings --------------------------------------------------------------------------------

def test_load_rulings_missing_log_is_empty_not_an_error(tmp_path):
    assert tcr.load_rulings(tmp_path) == {"error": None, "rulings": [], "bad_lines": 0}


def test_load_rulings_counts_bad_lines_and_keeps_good_ones(tmp_path):
    log = tmp_path / tcr.RULINGS_REL
    log.parent.mkdir(parents=True)
    log.write_text('{"key": "a1", "reason": "x"}\n\nnot json\n[1]\n{"reason": "no key"}\n'
                   '{"key": "b2"}\n')
    got = tcr.load_rulings(tmp_path)
    assert [r["key"] for r in got["rulings"]] == ["a1", "b2"]
    assert got["bad_lines"] == 3 and got["error"] is None


# --- CLI -----------------------------------------------------------------------------------------

def test_cli_mark_list_and_log(tmp_path, capsys):
    root = _root(tmp_path)
    assert tcr.main(["--repo-root", str(root), "list"]) == 0
    out = capsys.readouterr().out
    assert "population 2 | included 2" in out
    assert f"{_key(CUTS[0])}  Acme Corp: Head of Operations Planning  (title not in" in out
    assert tcr.main(["--repo-root", str(root), "list", "--grep", "BETA"]) == 0
    out = capsys.readouterr().out
    assert "included 1 | excluded: not matching --grep 1" in out and "Acme" not in out
    assert tcr.main(["--repo-root", str(root), "mark", "--key", _key(CUTS[1]),
                     "--reason", "in lane"]) == 0
    assert json.loads(capsys.readouterr().out)["logged"]["company"] == "Beta Inc"
    assert tcr.main(["--repo-root", str(root), "mark", "--key", _key(CUTS[1]),
                     "--reason", "again"]) == 1
    assert "already ruled" in capsys.readouterr().err
    assert tcr.main(["--repo-root", str(root), "log"]) == 0
    assert json.loads(capsys.readouterr().out)["rulings"][0]["reason"] == "in lane"


def test_cli_errors_go_to_stderr_with_exit_2(tmp_path, capsys):
    assert tcr.main(["--repo-root", str(tmp_path), "list"]) == 2
    assert "no role queue" in capsys.readouterr().err
    assert tcr.main(["--repo-root", str(tmp_path), "mark", "--key", "x", "--reason", "r"]) == 2
    assert "no role queue" in capsys.readouterr().err
    (tmp_path / tcr.RULINGS_REL).mkdir(parents=True)
    assert tcr.main(["--repo-root", str(tmp_path), "log"]) == 2
    assert "unreadable rulings log" in capsys.readouterr().err


def test_cli_by_accepts_only_nick_or_claude(tmp_path):
    with pytest.raises(SystemExit):
        tcr.main(["--repo-root", str(_root(tmp_path)), "mark", "--key", _key(CUTS[0]),
                  "--reason", "r", "--by", "someone"])


def test_the_script_runs_as_a_bare_script(tmp_path):
    root = _root(tmp_path)
    r = subprocess.run([sys.executable, str(TOOLS / "title_cut_ruling.py"), "--repo-root", str(root),
                        "mark", "--key", _key(CUTS[0]), "--reason", "in lane"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert (root / tcr.RULINGS_REL).is_file()


def test_mark_refuses_a_key_two_postings_share(tmp_path, monkeypatch):
    # Codex F5, 2026-11-18: an 8-hex key can collide; ruling hits[0] would rule the wrong role.
    root = _root(tmp_path)
    monkeypatch.setattr(tcr, "cut_key", lambda cut: "abcd1234")
    code, out = tcr.mark(root, "abcd1234", "r")
    assert code == 2 and "matches 2 different postings; no ruling written" in out["error"]
    assert not (root / tcr.RULINGS_REL).exists()


def test_the_same_posting_listed_twice_is_not_ambiguous(tmp_path):
    root = _root(tmp_path, cuts=[CUTS[0], dict(CUTS[0])])
    assert tcr.mark(root, _key(CUTS[0]), "r")[0] == 0


def test_non_string_fields_are_read_as_text_not_a_crash(tmp_path):
    cuts = [{"company": "Acme Corp", "title": 7, "url": None, "location": None, "rule": "title_filter"}]
    got = tcr.load_cuts(_root(tmp_path, cuts=cuts))
    c = got["cuts"][0]
    assert (c["title"], c["url"], c["location"], c["reason"]) == ("7", "", "", "")
    assert c["key"] == hashlib.sha1(b"acme corp::7").hexdigest()[:8]


def test_a_claude_label_does_not_block_nicks_ruling(tmp_path):
    root = _root(tmp_path)
    assert tcr.mark(root, _key(CUTS[0]), "label", by="claude")[0] == 0
    assert tcr.mark(root, _key(CUTS[0]), "mine")[0] == 0
    assert tcr.mark(root, _key(CUTS[0]), "again")[0] == 1
    assert tcr.mark(root, _key(CUTS[0]), "again", by="claude")[0] == 1



@pytest.mark.parametrize("ff,want", [(3, 3), (None, 0), ("2", 0), (True, 0)])
def test_load_cuts_reports_fetch_failures_as_a_count(tmp_path, ff, want):
    root = _root(tmp_path, payload={"scanned_at": "x", "cut_at_fetch": [], "fetch_failures": ff})
    assert tcr.load_cuts(root)["fetch_failures"] == want



def test_mark_checks_and_appends_under_the_rulings_lock(tmp_path, monkeypatch):
    import contextlib
    seen = []

    @contextlib.contextmanager
    def fake_lock(path):
        seen.append(Path(path))
        yield path
    monkeypatch.setattr(tcr, "file_lock", fake_lock)
    root = _root(tmp_path)
    assert tcr.mark(root, _key(CUTS[0]), "r")[0] == 0
    assert seen == [root / tcr.RULINGS_REL]
