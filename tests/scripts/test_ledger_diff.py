"""Tests for tools/ledger_diff.py — the cross-call commitment ledger.

This pins the OWES(...) line contract that /meeting Step 5 emits. If the format
changes, these tests are the thing that says so.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import ledger_diff as L  # noqa: E402

HDR = "# Call\n\n## Facts\n\n### Commitments\n"


def note(tmp, name, *lines):
    p = tmp / name
    p.write_text(HDR + "\n".join(lines) + "\n", encoding="utf-8")
    return p


# --- parsing ---------------------------------------------------------------

def test_parses_the_full_documented_line(tmp_path):
    p = note(tmp_path, "2026-08-06-robin-call.md",
             '- [ ] OWES(robin): re-price the standard package — "let me redo the numbers" (2026-08-06) [#pricing]')
    r = L.parse_note(p)[0]
    assert r["owner"] == "robin"
    assert r["what"] == "re-price the standard package"
    assert r["quote"] == "let me redo the numbers"
    assert r["stated"] == "2026-08-06"
    assert r["thread"] == "pricing"
    assert r["done"] is False


def test_parses_minimal_line_without_quote_date_or_thread(tmp_path):
    """Notes written before the full format existed must still yield owner+text."""
    p = note(tmp_path, "2026-07-01-robin-call.md", "- [ ] OWES(nick): send the flyer")
    r = L.parse_note(p)[0]
    assert (r["owner"], r["what"]) == ("nick", "send the flyer")
    assert r["stated"] == "2026-07-01"   # falls back to the note's own date


def test_checked_box_is_done(tmp_path):
    p = note(tmp_path, "2026-08-06-robin-call.md", "- [x] OWES(nick): send the flyer")
    assert L.parse_note(p)[0]["done"] is True


def test_unassigned_owner_is_preserved_not_guessed(tmp_path):
    p = note(tmp_path, "2026-08-06-robin-call.md",
             "- [ ] OWES(unassigned): someone should call the plumber")
    assert L.parse_note(p)[0]["owner"] == "unassigned"


def test_non_commitment_lines_ignored(tmp_path):
    p = note(tmp_path, "2026-08-06-robin-call.md",
             "- a normal bullet", "OWES(x): missing checkbox", "## Read")
    assert L.parse_note(p) == []


# --- cross-note identity ---------------------------------------------------

def test_reworded_restatement_folds_into_one_item(tmp_path):
    note(tmp_path, "2026-07-01-robin-call.md", "- [ ] OWES(nick): design the signup flyer")
    note(tmp_path, "2026-08-06-robin-call.md", "- [ ] OWES(nick): design the signup flyer for the storefront")
    led = L.build_ledger(L.collect(tmp_path))
    assert len(led) == 1
    assert len(led[0]["mentions"]) == 2


def test_different_owners_never_merge(tmp_path):
    note(tmp_path, "2026-08-06-robin-call.md",
         "- [ ] OWES(nick): send the flyer", "- [ ] OWES(robin): send the flyer")
    assert len(L.build_ledger(L.collect(tmp_path))) == 2


def test_thread_tag_beats_fuzzy_text(tmp_path):
    """Same thread = same commitment even when the wording drifts a long way."""
    note(tmp_path, "2026-07-01-robin-call.md", "- [ ] OWES(nick): incorporate [#entity]")
    note(tmp_path, "2026-08-06-robin-call.md",
         "- [ ] OWES(nick): file the entity paperwork with the state [#entity]")
    assert len(L.build_ledger(L.collect(tmp_path))) == 1


def test_unrelated_commitments_stay_separate(tmp_path):
    note(tmp_path, "2026-08-06-robin-call.md",
         "- [ ] OWES(nick): book the venue", "- [ ] OWES(nick): research vendor options")
    assert len(L.build_ledger(L.collect(tmp_path))) == 2


# --- open + age ------------------------------------------------------------

def test_open_excludes_closed_and_reports_age(tmp_path):
    note(tmp_path, "2026-07-01-robin-call.md",
         "- [ ] OWES(nick): send the flyer", "- [ ] OWES(robin): resend the email")
    note(tmp_path, "2026-08-06-robin-call.md", "- [x] OWES(robin): resend the email")
    items = L.open_items(L.build_ledger(L.collect(tmp_path)), today="2026-08-07")
    assert [i["owner"] for i in items] == ["nick"]
    assert items[0]["age_days"] == 37


def test_open_sorted_oldest_first(tmp_path):
    note(tmp_path, "2026-06-01-robin-call.md", "- [ ] OWES(nick): old thing")
    note(tmp_path, "2026-08-01-robin-call.md", "- [ ] OWES(nick): recent thing")
    items = L.open_items(L.build_ledger(L.collect(tmp_path)), today="2026-08-07")
    assert items[0]["what"] == "old thing"


# --- since (the whole point) ----------------------------------------------

def test_since_classifies_done_carried_unmentioned_and_new(tmp_path):
    note(tmp_path, "2026-07-01-robin-call.md",
         "- [ ] OWES(nick): send the flyer",
         "- [ ] OWES(robin): resend the email",
         "- [ ] OWES(nick): book the venue")
    newest = note(tmp_path, "2026-08-06-robin-call.md",
                  "- [x] OWES(robin): resend the email",
                  "- [ ] OWES(nick): send the flyer",
                  "- [ ] OWES(nick): research vendor options")
    r = L.since(tmp_path, newest, today="2026-08-07")
    assert [x["owner"] for x in r["done"]] == ["robin"]
    assert [x["what"] for x in r["carried"]] == ["send the flyer"]
    assert [x["what"] for x in r["unmentioned"]] == ["book the venue"]
    assert [x["what"] for x in r["new_this_call"]] == ["research vendor options"]


def test_silently_dropped_commitment_is_surfaced(tmp_path):
    """The failure this whole script exists for: a promise nobody mentions again."""
    note(tmp_path, "2026-06-01-robin-call.md", "- [ ] OWES(robin): send the quote")
    note(tmp_path, "2026-07-01-robin-call.md", "- [ ] OWES(nick): unrelated task")
    newest = note(tmp_path, "2026-08-06-robin-call.md", "- [ ] OWES(nick): another thing")
    r = L.since(tmp_path, newest, today="2026-08-07")
    dropped = [x for x in r["unmentioned"] if x["what"] == "send the quote"]
    assert dropped and dropped[0]["age_days"] == 67


def test_counterpart_filter_scopes_to_one_person(tmp_path):
    note(tmp_path, "2026-08-01-robin-call.md", "- [ ] OWES(robin): robin thing")
    note(tmp_path, "2026-08-02-casey-call.md", "- [ ] OWES(casey): casey thing")
    items = L.open_items(L.build_ledger(L.collect(tmp_path, "robin")), today="2026-08-07")
    assert [i["owner"] for i in items] == ["robin"]


def test_empty_dir_is_not_an_error(tmp_path):
    assert L.open_items(L.build_ledger(L.collect(tmp_path))) == []


# --- CLI -------------------------------------------------------------------

def run(*args):
    r = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "ledger_diff.py"), *args],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def test_cli_open_json(tmp_path):
    note(tmp_path, "2026-07-01-robin-call.md", "- [ ] OWES(nick): send the flyer")
    rc, out, err = run("open", "--dir", str(tmp_path), "--today", "2026-08-07")
    assert rc == 0, err
    d = json.loads(out)
    assert d["status"] == "ok" and d["open"][0]["age_days"] == 37


def test_cli_since_text_names_the_dropped_item(tmp_path):
    note(tmp_path, "2026-07-01-robin-call.md", "- [ ] OWES(robin): send the quote")
    newest = note(tmp_path, "2026-08-06-robin-call.md", "- [ ] OWES(nick): other")
    rc, out, err = run("since", "--dir", str(tmp_path), "--note", str(newest),
                       "--today", "2026-08-07", "--format", "text")
    assert rc == 0, err
    assert "UNMENTIONED" in out and "send the quote" in out


def test_cli_bad_dir_exits_nonzero():
    rc, out, _ = run("open", "--dir", "/nonexistent/xyz")
    assert rc == 1 and json.loads(out)["status"] == "error"


def test_containment_does_not_over_merge_on_a_short_fragment(tmp_path):
    """Containment must not let a short phrase swallow unrelated commitments."""
    note(tmp_path, "2026-08-06-robin-call.md",
         "- [ ] OWES(nick): call",
         "- [ ] OWES(nick): call the venue about the deposit")
    assert len(L.build_ledger(L.collect(tmp_path))) == 2


def test_containment_merges_a_genuine_elaboration(tmp_path):
    note(tmp_path, "2026-07-01-robin-call.md", "- [ ] OWES(nick): research vendor options")
    note(tmp_path, "2026-08-06-robin-call.md",
         "- [ ] OWES(nick): research vendor options and compare suppliers")
    assert len(L.build_ledger(L.collect(tmp_path))) == 1
