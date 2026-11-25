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


# --- acceptances and the ladder (L2b, 2026-11-24) ------------------------------------------------
# Nick, NEEDS-NICK 10: only an explicit acceptance counts; a cut seen and not marked counts for
# nothing. The count is decision_write.ladder() (CLIMB_AT 20), never a copy.
from tools import decision_write as dw  # noqa: E402

SCAN = "2026-01-06T16:30:00+00:00"
PREV = "2026-01-05T16:30:00+00:00"
FIRST = "2026-01-04T16:30:00+00:00"   # the oldest stored scan: PREV has a predecessor
SALES = "title in out-of-lane family 'sales'"


def _sales_cut(n, company="Acme Corp"):
    return {"company": company, "title": f"Sales Manager {n}", "location": "San Francisco, CA",
            "url": f"https://jobs.example.com/acme/s{n}", "rule": "title_filter", "reason": SALES}


def _page_root(tmp_path, cuts, previous=(), shown=True):
    """A root where the morning page has shown SCAN: queue, targets, cut-scan store."""
    root = _root(tmp_path, cuts=cuts)
    (root / "data" / "calibration").mkdir(parents=True, exist_ok=True)
    (root / "data" / "scan-targets.yaml").write_text(
        "companies:\n  - name: Acme Corp\n  - name: Beta Inc\n")
    store = {FIRST: [], PREV: sorted(previous)}
    if shown:
        store[SCAN] = sorted(tcr.load_cuts(root)["cuts"][i]["role_key"] for i in range(len(cuts)))
    (root / "data" / "calibration" / "title-cut-scans.json").write_text(json.dumps(store))
    return root


def _rk(cut):
    from tools.career_scanner.dedup import role_key
    return role_key(cut)


def test_accept_records_only_the_pages_list_and_skips_corrected_cuts(tmp_path):
    eng = {"company": "Acme Corp", "title": "Software Engineer", "location": "San Francisco, CA",
           "url": "https://jobs.example.com/acme/e1", "rule": "title_filter", "reason": "engineer title"}
    old, cuts = _sales_cut(0), [_sales_cut(0), _sales_cut(1), _sales_cut(2), eng]
    root = _page_root(tmp_path, cuts, previous=[_rk(old)])
    assert tcr.mark(root, tcr.cut_key(cuts[2]), "in lane", now=NOW)[0] == 0
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert code == 0
    # Shown = review and new: s1 and s2 (s0 was in the previous scan; the engineer title is not
    # on the review list). s2 was corrected, so only s1 is accepted.
    assert (out["shown"], out["accepted"], out["skipped_corrected"]) == (2, 1, 1)
    lines = [x for x in tcr.load_accepts(root)["rulings"] if x["ruling"] == "accepted"]
    assert [x["role_key"] for x in lines] == [_rk(cuts[1])]
    assert lines[0]["cut_reason"] == SALES and lines[0]["ruling"] == "accepted"
    assert "lean_shown" not in lines[0]          # the adapter sets it, the log never stores it
    # The morning page's reader sees no acceptance: the rulings log holds the correction only.
    assert [r["ruling"] for r in tcr.load_rulings(root)["rulings"]] == ["should_have_shown"]


def test_a_scan_is_accepted_once(tmp_path):
    root = _page_root(tmp_path, [_sales_cut(1)])
    assert tcr.accept(root, SCAN, now=NOW)[1]["accepted"] == 1
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert code == 1 and "already accepted" in out["error"]
    assert [x["ruling"] for x in tcr.load_accepts(root)["rulings"]] == ["accepted", "scan_accepted"]


def test_accept_takes_the_label_the_page_prints(tmp_path):
    from tools.morning_page import _when
    root = _page_root(tmp_path, [_sales_cut(1)])
    code, out = tcr.accept(root, _when(SCAN), now=NOW)
    assert code == 0 and out["scan"] == SCAN


def test_accept_refuses_a_scan_not_in_the_store_and_lists_the_valid_ones(tmp_path):
    root = _page_root(tmp_path, [_sales_cut(1)])
    code, out = tcr.accept(root, "2025-12-31T00:00:00+00:00", now=NOW)
    assert code == 2 and "not a scan in the cut-scan store" in out["error"]
    assert SCAN in out["error"] and PREV in out["error"]
    assert not tcr.accepts_path(root).exists()


def test_an_ambiguous_label_is_refused():
    from tools.morning_page import _when
    a, b = "2026-01-06T16:30:00+00:00", "2026-01-06T16:30:40+00:00"
    assert _when(a) == _when(b)
    got, why = tcr.resolve_scan({a: [], b: []}, _when(a))
    assert got is None and "names 2 scans" in why


def test_a_cut_corrected_in_an_earlier_scan_is_skipped(tmp_path):
    cuts = [_sales_cut(1), _sales_cut(2)]
    root = _page_root(tmp_path, cuts)
    assert tcr.mark(root, tcr.cut_key(cuts[1]), "in lane", now=NOW)[0] == 0
    out = tcr.accept(root, SCAN, now=NOW)[1]
    assert (out["accepted"], out["skipped_corrected"]) == (1, 1)


def test_accept_refuses_a_scan_the_page_has_not_shown(tmp_path):
    root = _page_root(tmp_path, [_sales_cut(1)], shown=False)
    store = tcr.Path(root / "data" / "calibration" / "title-cut-scans.json")
    before = store.read_text()
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert code == 2 and "not a scan in the cut-scan store" in out["error"]
    assert store.read_text() == before          # read-only: the store is never written here
    assert not tcr.accepts_path(root).exists()


def _accepts(root, n, start=0):
    root = Path(root)
    (root / "data" / "calibration").mkdir(parents=True, exist_ok=True)
    with open(tcr.accepts_path(root), "a") as f:
        for i in range(start, start + n):
            f.write(json.dumps({"accepted_at": f"2026-01-06T10:{i:02d}:00+00:00",
                                "key": f"k{i}", "role_key": f"rk{i}", "ruling": "accepted",
                                "by": "nick", "cut_reason": SALES}) + "\n")


def _correction(root, minute, by="nick"):
    Path(root, "data", "calibration").mkdir(parents=True, exist_ok=True)
    with open(tcr.rulings_path(root), "a") as f:
        f.write(json.dumps({"ruled_at": f"2026-01-06T10:{minute:02d}:00+00:00", "key": "kc",
                            "role_key": f"rc{minute}", "ruling": "should_have_shown",
                            "reason": "in lane", "by": by, "cut_reason": SALES}) + "\n")


def test_twenty_accepted_cuts_with_no_correction_reach_the_climb(tmp_path):
    _accepts(tmp_path, dw.CLIMB_AT - 1)
    c = tcr.ladder(tmp_path, now=NOW)[1]["classes"][SALES]
    assert (c["rung"], c["agreements_since_correction"]) == ("R1", dw.CLIMB_AT - 1)
    _accepts(tmp_path, 1, start=dw.CLIMB_AT - 1)
    c = tcr.ladder(tmp_path, now=NOW)[1]["classes"][SALES]
    assert (c["rung"], c["agreements_since_correction"], c["corrections"]) == ("R2", dw.CLIMB_AT, 0)


def test_one_correction_resets_the_class(tmp_path):
    _accepts(tmp_path, dw.CLIMB_AT)
    _correction(tmp_path, dw.CLIMB_AT + 1)
    c = tcr.ladder(tmp_path, now=NOW)[1]["classes"][SALES]
    assert (c["rung"], c["agreements_since_correction"], c["corrections"]) == ("R1", 0, 1)


def test_order_is_time_so_acceptances_after_a_correction_count_from_zero(tmp_path):
    _correction(tmp_path, 0)
    _accepts(tmp_path, 3, start=1)
    c = tcr.ladder(tmp_path, now=NOW)[1]["classes"][SALES]
    assert (c["agreements_since_correction"], c["corrections"]) == (3, 1)


def test_a_cut_seen_but_not_marked_counts_for_nothing(tmp_path):
    # Nick, NEEDS-NICK 10: seen on the page and never accepted or corrected -> no count.
    root = _page_root(tmp_path, [_sales_cut(n) for n in range(dw.CLIMB_AT + 5)])
    assert len(tcr.shown_cuts(root, SCAN)[1]["cuts"]) == dw.CLIMB_AT + 5
    assert tcr.ladder(root, now=NOW)[1]["classes"] == {}
    assert not tcr.accepts_path(root).exists()


def test_only_nicks_lines_count(tmp_path):
    _accepts(tmp_path, dw.CLIMB_AT)
    _correction(tmp_path, dw.CLIMB_AT + 1, by="claude")
    c = tcr.ladder(tmp_path, now=NOW)[1]["classes"][SALES]
    assert (c["rung"], c["corrections"]) == ("R2", 0)


def test_the_adapter_marks_every_cut_held_out(tmp_path):
    ev = tcr.ladder_events([], [{"accepted_at": "2026-01-06T10:00:00+00:00", "by": "nick",
                                  "ruling": "accepted", "cut_reason": SALES}])
    assert [e for e in ev if e["type"] == "resolved"][0]["lean_shown"] is False


def test_accept_cli_runs(tmp_path):
    root = _page_root(tmp_path, [_sales_cut(1)])
    p = subprocess.run([sys.executable, str(TOOLS / "title_cut_ruling.py"), "--repo-root", str(root),
                        "accept", "--scan", SCAN], capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["accepted"] == 1
    p = subprocess.run([sys.executable, str(TOOLS / "title_cut_ruling.py"), "--repo-root", str(root),
                        "ladder"], capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["classes"][SALES]["agreements_since_correction"] == 1


def test_shown_cuts_takes_a_string_root_too(tmp_path):
    root = _page_root(tmp_path, [_sales_cut(1)])
    assert tcr.shown_cuts(str(root), SCAN)[0] == 0


def test_shown_cuts_refuses_without_a_role_queue(tmp_path):
    root = _page_root(tmp_path, [_sales_cut(1)])
    (root / "tools" / ".role-queue.json").unlink()
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert code == 2 and "no role queue" in out["error"]


@pytest.mark.parametrize("body, text", [("{not json", "cut-scan store unreadable"),
                                        ("[]", "not an object of scans")])
def test_shown_cuts_refuses_a_bad_store(tmp_path, body, text):
    root = _page_root(tmp_path, [_sales_cut(1)])
    (root / "data" / "calibration" / "title-cut-scans.json").write_text(body)
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert code == 2 and text in out["error"]
    assert not tcr.accepts_path(root).exists()


def test_shown_cuts_refuses_without_scan_targets(tmp_path):
    root = _page_root(tmp_path, [_sales_cut(1)])
    (root / "data" / "scan-targets.yaml").unlink()
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert code == 2 and "scan-targets.yaml" in out["error"]


@pytest.mark.parametrize("which", ["rulings", "accepts"])
def test_accept_and_ladder_refuse_an_unreadable_log(tmp_path, which):
    root = _page_root(tmp_path, [_sales_cut(1)])
    path = tcr.rulings_path(root) if which == "rulings" else tcr.accepts_path(root)
    path.mkdir(parents=True)                      # a directory: read_text raises OSError
    code, out = tcr.ladder(root, now=NOW)
    assert code == 2 and "unreadable" in out["error"]
    if which == "rulings":                        # an accepts dir would also break the lock
        code, out = tcr.accept(root, SCAN, now=NOW)
        assert code == 2 and "unreadable" in out["error"]


def test_accept_counts_each_skip_reason_apart(tmp_path):
    cuts = [_sales_cut(n) for n in range(1, 6)]
    root = _page_root(tmp_path, cuts)
    assert tcr.mark(root, tcr.cut_key(cuts[0]), "in lane", now=NOW)[0] == 0
    with open(tcr.accepts_path(root), "a") as f:   # accepted earlier, under another scan
        f.write(json.dumps({"accepted_at": "2026-01-05T10:00:00+00:00", "key": tcr.cut_key(cuts[1]),
                            "role_key": _rk(cuts[1]),
                            "ruling": "accepted", "by": "nick", "cut_reason": SALES,
                            "scan_at": "2026-01-03T00:00:00+00:00"}) + "\n")
    out = tcr.accept(root, SCAN, now=NOW)[1]
    assert (out["shown"], out["accepted"], out["skipped_corrected"],
            out["skipped_already_accepted"]) == (5, 3, 1, 1)


# --- current scan only (b7, after Codex r1 F1) and the r1 fixes F2-F4 ----------------------------

def test_an_older_scan_is_refused_and_the_refusal_names_the_current_ones_label(tmp_path):
    from tools.morning_page import _when
    root = _page_root(tmp_path, [_sales_cut(1)])
    code, out = tcr.accept(root, PREV, now=NOW)
    assert code == 2 and "newer scan" in out["error"]
    assert SCAN in out["error"] and _when(SCAN) in out["error"]
    assert not tcr.accepts_path(root).exists()


def test_a_scan_with_no_earlier_stored_scan_is_refused(tmp_path):
    root = _page_root(tmp_path, [_sales_cut(1)])
    store = root / "data" / "calibration" / "title-cut-scans.json"
    store.write_text(json.dumps({SCAN: json.loads(store.read_text())[SCAN]}))
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert code == 2 and "no earlier scan" in out["error"]


def test_scan_targets_changed_after_the_page_showed_the_scan_is_refused(tmp_path):
    import os
    root = _page_root(tmp_path, [_sales_cut(1)])
    store = root / "data" / "calibration" / "title-cut-scans.json"
    t = store.stat().st_mtime
    os.utime(root / "data" / "scan-targets.yaml", (t + 60, t + 60))
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert code == 2 and "scan-targets.yaml changed" in out["error"]
    os.utime(root / "data" / "scan-targets.yaml", (t - 60, t - 60))
    assert tcr.accept(root, SCAN, now=NOW)[0] == 0


def test_shown_cuts_never_writes_the_store(tmp_path):
    root = _page_root(tmp_path, [_sales_cut(1)])
    store = root / "data" / "calibration" / "title-cut-scans.json"
    before = (store.read_text(), store.stat().st_mtime_ns)
    assert tcr.shown_cuts(root, SCAN)[0] == 0 and tcr.accept(root, SCAN, now=NOW)[0] == 0
    assert (store.read_text(), store.stat().st_mtime_ns) == before


def test_an_accept_that_adds_no_cut_still_leaves_a_receipt_and_blocks_a_second(tmp_path):
    # Codex r1 F2: every shown cut corrected, so no cut line; the scan is still accepted once.
    cuts = [_sales_cut(1)]
    root = _page_root(tmp_path, cuts)
    assert tcr.mark(root, tcr.cut_key(cuts[0]), "in lane", now=NOW)[0] == 0
    assert tcr.accept(root, SCAN, now=NOW)[1]["accepted"] == 0
    lines = tcr.load_accepts(root)["rulings"]
    assert [(x["ruling"], x["scan_at"], x["accepted"]) for x in lines] == [("scan_accepted", SCAN, 0)]
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert code == 1 and "already accepted" in out["error"]


def test_receipts_never_count_on_the_ladder(tmp_path):
    root = _page_root(tmp_path, [_sales_cut(1)])
    assert tcr.accept(root, SCAN, now=NOW)[1]["accepted"] == 1
    c = tcr.ladder(root, now=NOW)[1]["classes"]
    assert list(c) == [SALES] and c[SALES]["agreements_since_correction"] == 1


def test_a_correction_in_the_same_second_as_an_acceptance_resets_the_class(tmp_path):
    # Codex r1 F3: second-resolution timestamps; a tie must sort the correction last.
    _accepts(tmp_path, 1)                     # accepted_at 10:00:00
    _correction(tmp_path, 0)                  # ruled_at    10:00:00, the same second
    c = tcr.ladder(tmp_path, now=NOW)[1]["classes"][SALES]
    assert (c["agreements_since_correction"], c["corrections"]) == (0, 1)


# --- accept re-validates inside one lock over both logs (11-24 P1s) ----------------------------
# Codex r2 F1: accept validated the scan, then took the lock without re-checking, so a rollover
# in between recorded an acceptance one scan late. Grok F2: the rulings log was read under the
# accepts lock only, so a mark in the same moment could be missed. Grok F1: the predecessor's
# keys were not checked as a list of strings, the way record_scan checks them.

def test_a_scan_rollover_between_validation_and_the_lock_is_refused(tmp_path, monkeypatch):
    import contextlib
    root = _page_root(tmp_path, [_sales_cut(1)])
    real = tcr.file_lock
    rolled = []

    @contextlib.contextmanager
    def roll_then_lock(path, *a, **k):
        if not rolled:      # the scan rolls over just before accept takes its first lock
            rolled.append(1)
            q = root / "tools" / ".role-queue.json"
            body = json.loads(q.read_text())
            body["scanned_at"] = "2026-01-07T16:30:00+00:00"
            q.write_text(json.dumps(body))
        with real(path, *a, **k) as held:
            yield held
    monkeypatch.setattr(tcr, "file_lock", roll_then_lock)
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert code == 2 and "newer scan" in out["error"], out
    assert not [x for x in tcr.load_accepts(root)["rulings"] if x["ruling"] == "accepted"]


def test_a_list_that_changed_under_the_lock_is_refused(tmp_path, monkeypatch):
    root = _page_root(tmp_path, [_sales_cut(1), _sales_cut(2)])
    real, calls = tcr.shown_cuts, []

    def second_call_sees_a_shorter_list(r, scan):
        code, got = real(r, scan)
        calls.append(1)
        if len(calls) == 2:
            got = {**got, "cuts": got["cuts"][:1]}
        return code, got
    monkeypatch.setattr(tcr, "shown_cuts", second_call_sees_a_shorter_list)
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert len(calls) == 2
    assert code == 2 and "changed" in out["error"], out
    assert not tcr.accepts_path(root).exists() or not tcr.load_accepts(root)["rulings"]


def test_accept_holds_the_rulings_lock_then_the_accepts_lock(tmp_path, monkeypatch):
    import contextlib
    root = _page_root(tmp_path, [_sales_cut(1)])
    order, real = [], tcr.file_lock

    @contextlib.contextmanager
    def record(path, *a, **k):
        order.append(("enter", Path(path)))
        with real(path, *a, **k) as held:
            yield held
        order.append(("exit", Path(path)))
    monkeypatch.setattr(tcr, "file_lock", record)
    assert tcr.accept(root, SCAN, now=NOW)[0] == 0
    r, a = tcr.rulings_path(root), tcr.accepts_path(root)
    assert order == [("enter", r), ("enter", a), ("exit", a), ("exit", r)]


def test_a_mark_during_accept_waits_for_it(tmp_path, monkeypatch):
    """A real mark in another process blocks while accept is between its read of the rulings
    log and its append, so it can never land unseen in that window."""
    import threading
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", str(tmp_path / "locks"))
    cuts = [_sales_cut(1), _sales_cut(2)]
    root = _page_root(tmp_path, cuts)
    inside, release = threading.Event(), threading.Event()
    real_load = tcr.load_rulings

    def pause_after_reading(r):
        out = real_load(r)
        inside.set()
        release.wait(20)
        return out
    monkeypatch.setattr(tcr, "load_rulings", pause_after_reading)
    result = {}
    t = threading.Thread(target=lambda: result.update(r=tcr.accept(root, SCAN, now=NOW)))
    t.start()
    try:
        assert inside.wait(20)
        mark = subprocess.Popen(
            [sys.executable, str(TOOLS / "title_cut_ruling.py"), "--repo-root", str(root),
             "mark", "--key", tcr.cut_key(cuts[1]), "--reason", "in lane"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            mark.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        assert mark.poll() is None, ("mark wrote while accept held the rulings log unread", mark.communicate() if mark.poll() is not None else "")
    finally:
        release.set()
        t.join(20)
    assert mark.wait(timeout=30) == 0
    assert result["r"][0] == 0


@pytest.mark.parametrize("bad", ["abc", [1, 2], ["ok", None], {"k": 1}])
def test_a_corrupt_predecessor_in_the_store_is_refused(tmp_path, bad):
    root = _page_root(tmp_path, [_sales_cut(1)])
    store_path = root / "data" / "calibration" / "title-cut-scans.json"
    store = json.loads(store_path.read_text())
    store[PREV] = bad
    store_path.write_text(json.dumps(store))
    code, out = tcr.accept(root, SCAN, now=NOW)
    assert code == 2 and "not a list of key strings" in out["error"], out
    assert not tcr.accepts_path(root).exists()


def test_the_adapter_emits_decision_writes_own_word_agree():
    ev = tcr.ladder_events([], [{"accepted_at": "2026-01-06T10:00:00+00:00", "by": "nick",
                                  "ruling": "accepted", "cut_reason": SALES}])
    assert [e["outcome"] for e in ev if e["type"] == "resolved"] == ["agree"]
