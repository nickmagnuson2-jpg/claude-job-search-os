"""Tests for tools/networking_direction_backfill.py (2026-10-30).

The backfill labels every existing interaction with the direction the classifier gives it
today, so the summary rules can be deleted without changing a single verdict:
reply -> "<type> (inbound)", send -> "<type> (outbound)", None -> unchanged.

  1. PLAN writes a review file and a plan, and never touches data/.
  2. APPLY needs --approved, refuses a file that changed since the plan, backs up the
     original byte for byte, and changes only the planned headings.
  3. NO VERDICT CHANGES - the type-only classifier on the backfilled file gives every entry
     the verdict the summary-reading classifier gave the original.

The summary-reading classifier is passed as a file (--classifier). These tests use OLD_RULES,
a small stand-in written for this fixture, so they do not depend on git history.
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, TOOLS_DIR, write_fixture

sys.path.insert(0, str(TOOLS_DIR))
import day_check as dc  # noqa: E402
import reply_classify as rc  # noqa: E402

TOOL = TOOLS_DIR / "networking_direction_backfill.py"

NETWORKING = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Sam Example | Acme Corp | PM | peer | 2026-01-01 | 2026-01-09 | — |

## Interaction Log

### Sam Example — Acme Corp

#### 2026-01-09 | email | Sam replied 2:00 PM with times

**Follow-up:** —

#### 2026-01-08 | linkedin | Follow-up #1 SENT

#### 2026-01-07 | call | Talked through the role

#### 2026-01-06 | email (inbound) | Asked for a resume

#### 2026-01-05 | inbound | INBOUND note

#### 2026-01-04 | email-sent | Cold outreach to the team

#### 2026-01-03 | text | Sam texted you at 4 PM

#### 2026-01-02 | email | Follow-up #2 SENT; Sam replied 3 PM with times

#### 2026-01-01 | linkedin | I sent the deck and Sam replied same day

#### 2025-12-31 | email | Sam replied: I will be out until January

#### 2025-12-30 | email (inbound) | Sam replied with times; I sent the deck after
"""

# A summary-reading classifier, standing in for dfccc00:tools/reply_classify.py.
OLD_RULES = (
    "def classify(kind, summary):\n"
    "    k = (kind or '').strip().casefold()\n"
    "    if k in {'email (inbound)', 'inbound'} or 'replied' in summary or 'INBOUND' in summary:\n"
    "        return 'reply'\n"
    "    if 'SENT' in summary or summary.startswith('Cold outreach'):\n"
    "        return 'send'\n"
    "    if k == 'text' and 'texted you' in summary:\n"
    "        return 'reply'\n"
    "    return None\n")

# day_check reports sends only when both logs are readable.
OUTREACH_HEADER = ("# Outreach Log\n\n"
                   "| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |\n"
                   "| --- | --- | --- | --- | --- | --- | --- |\n")

PLANNED = {
    13: ("email", "email (inbound)"),
    17: ("linkedin", "linkedin (outbound)"),
    25: ("email-sent", "email-sent (outbound)"),
    27: ("text", "text (inbound)"),
    29: ("email", "email (two-way)"),
    31: ("linkedin", "linkedin (two-way)"),
    33: ("email", "email (inbound)"),
    35: ("email (inbound)", "email (two-way)"),
}
TWO_WAY = {29, 31, 35}


def run(tmp_path, *args):
    r = subprocess.run(
        [sys.executable, str(TOOL), "--repo-root", str(tmp_path), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
    out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else None
    return out, r.returncode, r.stderr


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def headings(text):
    return [m for m in map(dc.INTERACTION_RE.match, text.splitlines()) if m]


def old_classify():
    ns = {}
    exec(OLD_RULES, ns)
    return ns["classify"]


@pytest.fixture
def repo(tmp_path):
    write_fixture(tmp_path, "data/networking.md", NETWORKING)
    write_fixture(tmp_path, "data/outreach-log.md", OUTREACH_HEADER)
    write_fixture(tmp_path, "old_rules.py", OLD_RULES)
    return tmp_path


def apply_(repo, *args, rules="old_rules.py"):
    """apply recomputes the plan, so it needs the same old classifier (Codex F2)."""
    return run(repo, "apply", *args, "--classifier", repo / rules)


def plan(repo, rules="old_rules.py"):
    out, code, err = run(repo, "plan", "--out-dir", repo / "review",
                         "--classifier", repo / rules)
    assert code == 0, err
    return out


# --- 1. plan -------------------------------------------------------------------------

def test_plan_proposes_exactly_the_reply_and_send_entries(repo):
    out = plan(repo)
    p = json.loads((repo / "review" / "backfill-plan.json").read_text(encoding="utf-8"))
    got = {c["line"]: (c["old_type"], c["new_type"]) for c in p["changes"]}
    assert got == PLANNED
    assert p["source_sha256"] == sha(repo / "data/networking.md")
    assert out["changes"] == 8 and out["two_way"] == 3


def test_plan_never_touches_the_data_file(repo):
    before = sha(repo / "data/networking.md")
    plan(repo)
    assert sha(repo / "data/networking.md") == before
    assert sorted(p.name for p in (repo / "data").iterdir()) == ["networking.md", "outreach-log.md"]


def test_the_review_file_lists_every_change_and_states_its_scope(repo):
    plan(repo)
    review = (repo / "review" / "backfill-review.md").read_text(encoding="utf-8")
    for line, (old, new) in PLANNED.items():
        assert f"| {line} | {old} | {new} |" in review
    assert "population 11 | included 8 | excluded: no verdict 1, direction already recorded 2" \
        in review
    assert "Sam replied 2:00 PM" in review  # the summary excerpt
    assert "approve" in review.lower()


def test_the_plan_reports_the_interim_diff_under_the_old_classifier(repo):
    """Before the merge, the live classifier is the old one. It reads "(inbound)" only on
    "email (inbound)" and "inbound", so "text (inbound)" loses its message-verb reply."""
    out = plan(repo)
    p = json.loads((repo / "review" / "backfill-plan.json").read_text(encoding="utf-8"))
    assert [(d["line"], d["before"], d["after"]) for d in p["interim_diff"]] == [
        (27, "reply", None)]
    assert out["interim_changes"] == 1
    assert "| 27 | reply | None |" in (repo / "review" / "backfill-review.md").read_text(
        encoding="utf-8")


def test_the_acceptance_diff_is_empty_for_a_faithful_backfill(repo):
    out = plan(repo)
    p = json.loads((repo / "review" / "backfill-plan.json").read_text(encoding="utf-8"))
    assert p["acceptance_diff"] == [] and out["acceptance_changes"] == 0
    assert "This must be empty before applying" in (
        repo / "review" / "backfill-review.md").read_text(encoding="utf-8")


def test_a_plan_that_fails_acceptance_cannot_be_applied(repo):
    """Rules that call an "email (inbound)" entry nothing: the backfill leaves it alone (its
    type already records a direction), so the type-only classifier calls it a reply. That
    is a verdict change, and apply must refuse."""
    write_fixture(repo, "odd_rules.py", OLD_RULES.replace(
        "    k = (kind or '').strip().casefold()\n",
        "    k = (kind or '').strip().casefold()\n"
        "    if k == 'email (inbound)':\n        return None\n"))
    out = plan(repo, "odd_rules.py")
    p = json.loads((repo / "review" / "backfill-plan.json").read_text(encoding="utf-8"))
    assert [(d["line"], d["before"], d["after"]) for d in p["acceptance_diff"]] == [
        (21, None, "reply"), (35, None, "reply")]
    before = sha(repo / "data/networking.md")
    out, code, _ = apply_(repo, "--plan", repo / "review" / "backfill-plan.json",
                       "--approved")
    assert code != 0 and out["code"] == "acceptance_failed"
    assert sha(repo / "data/networking.md") == before


def test_plan_requires_the_classifier(repo):
    _, code, err = run(repo, "plan", "--out-dir", repo / "review")
    assert code != 0 and "--classifier" in err


def test_plan_on_a_missing_file_is_an_error(tmp_path):
    write_fixture(tmp_path, "old_rules.py", OLD_RULES)
    out, code, _ = run(tmp_path, "plan", "--out-dir", tmp_path / "review",
                       "--classifier", tmp_path / "old_rules.py")
    assert code != 0 and out["status"] == "error"


# --- 2. apply ------------------------------------------------------------------------

def test_apply_without_approval_writes_nothing(repo):
    plan(repo)
    before = sha(repo / "data/networking.md")
    out, code, _ = apply_(repo, "--plan", repo / "review" / "backfill-plan.json")
    assert code != 0 and out["code"] == "not_approved"
    assert sha(repo / "data/networking.md") == before


def test_apply_refuses_a_file_that_changed_after_the_plan(repo):
    plan(repo)
    path = repo / "data/networking.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    before = sha(path)
    out, code, _ = apply_(repo, "--plan", repo / "review" / "backfill-plan.json",
                       "--approved")
    assert code != 0 and out["code"] == "sha_mismatch"
    assert sha(path) == before
    assert not (repo / "output").exists()


def test_apply_changes_only_the_planned_headings_and_backs_up_the_original(repo):
    plan(repo)
    path = repo / "data/networking.md"
    original = path.read_bytes()
    out, code, err = apply_(repo, "--plan", repo / "review" / "backfill-plan.json",
                         "--approved")
    assert code == 0, err
    assert (repo / out["backup"]).read_bytes() == original
    old_lines = original.decode("utf-8").split("\n")
    new_lines = path.read_text(encoding="utf-8").split("\n")
    assert len(new_lines) == len(old_lines)
    changed = {i + 1 for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b}
    assert changed == set(PLANNED)
    for line, (old, new) in PLANNED.items():
        assert new_lines[line - 1].split(" | ")[1] == new
        assert old_lines[line - 1].replace(f"| {old} |", f"| {new} |", 1) == new_lines[line - 1]
    assert out["new_sha256"] == sha(path)


def test_apply_keeps_crlf_line_endings(tmp_path):
    write_fixture(tmp_path, "data/networking.md", "")
    write_fixture(tmp_path, "old_rules.py", OLD_RULES)
    path = tmp_path / "data/networking.md"
    path.write_bytes(NETWORKING.replace("\n", "\r\n").encode("utf-8"))
    plan(tmp_path)
    _, code, err = apply_(tmp_path, "--plan", tmp_path / "review" / "backfill-plan.json",
                       "--approved")
    assert code == 0, err
    raw = path.read_bytes()
    assert raw.count(b"\r\n") == NETWORKING.count("\n") and b"\r\r" not in raw
    assert b"| email (inbound) | Sam replied" in raw


def test_a_second_plan_after_apply_proposes_nothing(repo):
    plan(repo)
    apply_(repo, "--plan", repo / "review" / "backfill-plan.json", "--approved")
    assert plan(repo)["changes"] == 0


# --- 2b. two-way (Nick, 2026-10-30 13:42) ---------------------------------------------

def test_a_reply_that_also_records_a_send_becomes_two_way(repo):
    """Old verdict reply, AND an outbound signal in the same summary: SENT, an outbound
    opener, or "Nick/I sent|replied|emailed|texted|messaged". A reply that merely contains
    "I will" (line 33) is not one."""
    plan(repo)
    p = json.loads((repo / "review" / "backfill-plan.json").read_text(encoding="utf-8"))
    assert sorted(c["line"] for c in p["changes"] if c["two_way"]) == sorted(TWO_WAY)


def test_an_entry_already_marked_inbound_that_also_records_a_send_becomes_two_way(repo):
    """Nick, 2026-10-30 14:02: the one exception to "a recorded direction is never
    relabelled". Only "(inbound)" with an outbound signal; line 21's plain inbound entry
    stays, and nothing else that is already marked moves."""
    plan(repo)
    p = json.loads((repo / "review" / "backfill-plan.json").read_text(encoding="utf-8"))
    by_line = {c["line"]: c for c in p["changes"]}
    assert by_line[35]["relabel"] is True and by_line[35]["new_type"] == "email (two-way)"
    assert 21 not in by_line and 23 not in by_line
    assert [c["line"] for c in p["changes"] if c["relabel"]] == [35]
    review = (repo / "review" / "backfill-review.md").read_text(encoding="utf-8")
    two_way = review[review.index("## Two-way"):review.index("## Acceptance")]
    assert "| 35 | email (inbound) | email (two-way) |" in two_way


def test_the_review_lists_every_two_way_line_with_its_whole_summary(repo):
    plan(repo)
    review = (repo / "review" / "backfill-review.md").read_text(encoding="utf-8")
    two_way = review[review.index("## Two-way"):review.index("## Acceptance")]
    assert "| 29 | email | email (two-way) | Follow-up #2 SENT; Sam replied 3 PM with times |" \
        in two_way
    assert "| 31 | linkedin | linkedin (two-way) | I sent the deck and Sam replied same day |" \
        in two_way
    assert "| 13 |" not in two_way
    assert "strike" in two_way.lower()


def test_two_way_keeps_every_reply_and_adds_a_send(repo):
    """The acceptance rule after two-way: a reply may become two-way, nothing else moves."""
    out = plan(repo)
    p = json.loads((repo / "review" / "backfill-plan.json").read_text(encoding="utf-8"))
    assert p["acceptance_diff"] == [] and out["acceptance_changes"] == 0
    assert p["totals"] == {"replies": {"before": 8, "after": 8},
                           "sends": {"before": 2, "after": 5}}


def test_the_plan_reports_the_day_check_week_effect(repo):
    """2026-01-07 is a Wednesday. Weeks: Mon 01-05 to Wed 01-07 (no entries), then the three
    full weeks before it. The 01-01 and 01-02 two-way entries fall in the week ending
    Sunday 01-04, and so does the 12-30 relabelled one: sends rise by 3, replies stay put."""
    out, code, err = run(repo, "plan", "--out-dir", repo / "review",
                         "--classifier", repo / "old_rules.py", "--today", "2026-01-07")
    assert code == 0, err
    p = json.loads((repo / "review" / "backfill-plan.json").read_text(encoding="utf-8"))
    weeks = {w["week_start"]: w for w in p["weeks"]}
    assert sorted(weeks) == ["2025-12-15", "2025-12-22", "2025-12-29", "2026-01-05"]
    w = weeks["2025-12-29"]
    assert w["replies_before"] == w["replies_after"]
    assert w["sends_after"] - w["sends_before"] == 3
    assert all(weeks[k]["sends_after"] == weeks[k]["sends_before"]
               for k in ("2025-12-15", "2025-12-22", "2026-01-05"))
    review = (repo / "review" / "backfill-review.md").read_text(encoding="utf-8")
    assert "## Effect on the morning page" in review and "| 2025-12-29 |" in review


def test_a_hand_edited_result_sha_in_the_plan_is_refused(repo):
    """The plan's own new_sha256 is checked against the recomputed result, so an edit to
    that field alone is caught (an edited change list is plan_mismatch, tested below)."""
    plan(repo)
    path = repo / "review" / "backfill-plan.json"
    p = json.loads(path.read_text(encoding="utf-8"))
    p["new_sha256"] = "0" * 64
    path.write_text(json.dumps(p), encoding="utf-8")
    before = sha(repo / "data/networking.md")
    out, code, _ = apply_(repo, "--plan", path, "--approved")
    assert code != 0 and out["code"] == "result_mismatch"
    assert sha(repo / "data/networking.md") == before


def test_a_hand_edited_heading_in_the_plan_is_refused(repo):
    plan(repo)
    path = repo / "review" / "backfill-plan.json"
    p = json.loads(path.read_text(encoding="utf-8"))
    p["changes"][0]["heading"] += " edited"
    path.write_text(json.dumps(p), encoding="utf-8")
    before = sha(repo / "data/networking.md")
    out, code, _ = apply_(repo, "--plan", path, "--approved")
    assert code != 0 and out["code"] == "plan_mismatch"
    assert sha(repo / "data/networking.md") == before


def test_a_write_that_lands_wrong_is_reported_with_the_backup(repo, monkeypatch, capsys):
    import importlib.util
    plan(repo)
    spec = importlib.util.spec_from_file_location("ndb_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "write_atomic", lambda path, text: path.write_text(text + "x"))
    with pytest.raises(SystemExit) as e:
        mod.main(["--repo-root", str(repo), "apply", "--plan",
                  str(repo / "review" / "backfill-plan.json"), "--approved",
                  "--classifier", str(repo / "old_rules.py")])
    assert e.value.code == 1
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["code"] == "write_mismatch" and out["backup"].endswith(".bak")
    assert (repo / out["backup"]).read_text(encoding="utf-8") == NETWORKING


# --- 3. no verdict changes -----------------------------------------------------------

def test_no_entry_changes_its_verdict(repo):
    """The acceptance check itself: old rules on the original vs the current type-only
    classifier on the backfilled file, entry by entry."""
    old = old_classify()
    before = [old(m.group(2), m.group(3))
              for m in headings((repo / "data/networking.md").read_text(encoding="utf-8"))]
    plan(repo)
    apply_(repo, "--plan", repo / "review" / "backfill-plan.json", "--approved")
    TWO_WAY_INDEX = {i + 1 for i, m in enumerate(
        headings((repo / "data/networking.md").read_text(encoding="utf-8")))
        if "(two-way)" in m.group(2)}
    after = [rc.classify(m.group(2))
             for m in headings((repo / "data/networking.md").read_text(encoding="utf-8"))]
    assert [b if i + 1 not in TWO_WAY_INDEX else "both" for i, b in enumerate(before)] == after
    assert before.count("reply") == 8 and before.count("send") == 2


# --- Codex round 1 (2026-10-30) ----------------------------------------------------------

def test_a_coherently_edited_plan_is_refused(repo):
    """F2: an edit that changes a send to inbound AND rewrites acceptance_diff and new_sha256
    to match passed every check that only read the plan. apply now recomputes the plan from
    the file and the old classifier, and refuses any difference."""
    import hashlib
    sys.path.insert(0, str(TOOLS_DIR))
    import networking_direction_backfill as ndb
    plan(repo)
    path = repo / "review" / "backfill-plan.json"
    p = json.loads(path.read_text(encoding="utf-8"))
    for c in p["changes"]:
        if c["line"] == 17:
            c["new_type"] = "linkedin (inbound)"
    text = (repo / "data/networking.md").read_text(encoding="utf-8")
    p["new_sha256"] = hashlib.sha256(ndb._relabel(text, p["changes"]).encode("utf-8")).hexdigest()
    p["acceptance_diff"] = []
    path.write_text(json.dumps(p), encoding="utf-8")
    before = sha(repo / "data/networking.md")
    out, code, _ = apply_(repo, "--plan", path, "--approved")
    assert code != 0 and out["code"] == "plan_mismatch"
    assert sha(repo / "data/networking.md") == before


def test_apply_requires_the_classifier(repo):
    plan(repo)
    _, code, err = run(repo, "apply", "--plan", repo / "review" / "backfill-plan.json",
                       "--approved")
    assert code != 0 and "--classifier" in err


@pytest.mark.parametrize("out_dir", ["data/review", "data", "./data/../data/x"])
def test_plan_refuses_to_write_under_data(repo, out_dir):
    """F3: plan never writes data/, whatever --out-dir says."""
    before = sorted(p.relative_to(repo) for p in (repo / "data").rglob("*"))
    out, code, _ = run(repo, "plan", "--out-dir", out_dir, "--classifier", repo / "old_rules.py")
    assert code != 0 and out["code"] == "out_dir_in_data"
    assert sorted(p.relative_to(repo) for p in (repo / "data").rglob("*")) == before
