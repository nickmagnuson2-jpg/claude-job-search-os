"""tests for `friction_log.py close-stale` (overnight 2026-11-25, S6).

A counting, unresolved row (learning_state.friction_state due or open) whose last fire is
past the MACHINERY half-life is a staleness candidate. The half-life comes only from
learning_state.freshness_state / HALF_LIFE_DAYS (Nick, 2026-11-25: the same half-life
method, no second rule). `--dry-run` lists the candidates and the ledger hash and writes
nothing; `--apply --expect-sha H` closes exactly that plan with the RESOLVED marker,
keeping every other cell. Surfaces and natures below are invented.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "friction_log.py"
sys.path.insert(0, str(REPO / "tools"))
import friction_log as fl  # noqa: E402
import learning_state as ls  # noqa: E402

AS_OF = "2026-11-25"
# Window start is the earliest ledger date: 2026-05-21. MACHINERY half-life is 60 days,
# so a last fire on or after 2026-09-26 is current and anything older is unverified.
LEDGER = """# Friction Log

## Entries

| Date | Surface | Nature | Fix | Occurrences | Promotion | Kind |
|---|---|---|---|---|---|---|
| 2026-05-21..2026-07-01 | widget_write.py | widget parse failed on empty input | [auto-logged] | 9 | script-patch | friction |
| 2026-08-01 | gadget_write.py | gadget usage line printed | [auto-logged] | 1 | — | friction |
| 2026-06-01..2026-11-20 | sprocket_write.py | sprocket row not found | [auto-logged] | 4 | script-patch | friction |
| 2026-06-01..2026-09-26 | cog_write.py | cog boundary row exactly sixty days | [auto-logged] | 3 | script-patch | friction |
| 2026-06-01..2026-09-25 | cog_write.py | cog boundary row sixty one days | [auto-logged] | 3 | script-patch | friction |
| 2026-06-02 | check_widget.py | FP: blocked a legitimate widget edit | — | 2 | memory | false-positive-block |
| 2026-06-03 | block:check_widget.py | blocked a widget write as designed | — | 5 | model-habit | block-as-designed |
| 2026-06-04 | gear_write.py | gear write refused | RESOLVED 2026-07-01: fixed | 6 | script-patch | friction |
| undated | lever_write.py | lever row has no date | [auto-logged] | 2 | memory | friction |
"""

STALE_DUE = {"widget parse failed on empty input", "cog boundary row sixty one days",
             "FP: blocked a legitimate widget edit"}
STALE_OPEN = {"gadget usage line printed"}
STALE = STALE_DUE | STALE_OPEN


def cands(plan):
    return plan["due"] + plan["open"]


def run(ledger: Path, *args):
    r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                       env={"FRICTION_LEDGER": str(ledger), "PYTHONIOENCODING": "utf-8",
                            "PATH": "/usr/bin:/bin"}, timeout=60)
    lines = (r.stdout or "").strip().splitlines()
    return r.returncode, (json.loads(lines[-1]) if lines else {})


@pytest.fixture
def ledger(tmp_path):
    p = tmp_path / "friction-log.md"
    p.write_text(LEDGER, encoding="utf-8")
    return p


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def parsed():
    return [p for p in (fl.parse_row(r) for r in LEDGER.splitlines() if r.startswith("| ")
                        and not r.startswith("| Date")) if p]


def natures(rows):
    return {r["nature"] for r in rows}


# ------------------------------------------------------------------ the plan (in process)

def test_plan_closes_only_stale_counting_unresolved_rows():
    plan = fl.stale_plan(parsed(), fl.date.fromisoformat(AS_OF))
    assert natures(plan["due"]) == STALE_DUE
    assert natures(plan["open"]) == STALE_OPEN


def test_boundary_follows_freshness_state():
    # 60 days before as-of is still current (freshness_state uses <= H); 61 is not.
    names = natures(cands(fl.stale_plan(parsed(), fl.date.fromisoformat(AS_OF))))
    assert "cog boundary row exactly sixty days" not in names
    assert "cog boundary row sixty one days" in names


def test_resolved_and_non_counting_rows_are_never_candidates():
    names = natures(cands(fl.stale_plan(parsed(), fl.date.fromisoformat(AS_OF))))
    assert "gear write refused" not in names
    assert "blocked a widget write as designed" not in names


def test_undated_row_is_reported_not_closed():
    plan = fl.stale_plan(parsed(), fl.date.fromisoformat(AS_OF))
    assert "lever row has no date" not in natures(cands(plan))
    assert "lever row has no date" in natures(plan["skipped"])


def test_the_half_life_comes_from_learning_state(monkeypatch):
    """Done-when: changing the imported half-life changes the result (no second rule)."""
    as_of = fl.date.fromisoformat(AS_OF)
    monkeypatch.setitem(ls.HALF_LIFE_DAYS, "MACHINERY", 1000)
    assert cands(fl.stale_plan(parsed(), as_of)) == []
    monkeypatch.setitem(ls.HALF_LIFE_DAYS, "MACHINERY", 1)
    assert "sprocket row not found" in natures(cands(fl.stale_plan(parsed(), as_of)))


def test_plan_calls_freshness_state_with_machinery(monkeypatch):
    seen = []

    def spy(kind, last, start, end):
        seen.append((kind, start, end))
        return "current"

    monkeypatch.setattr(ls, "freshness_state", spy)
    assert cands(fl.stale_plan(parsed(), fl.date.fromisoformat(AS_OF))) == []
    assert seen and all(k == "MACHINERY" for k, _, _ in seen)
    assert {(s, e) for _, s, e in seen} == {(fl.date(2026, 5, 21), fl.date(2026, 11, 25))}


# ------------------------------------------------------------------ the CLI

def plan_sha(ledger, scope, as_of=AS_OF):
    code, out = run(ledger, "close-stale", "--dry-run", "--as-of", as_of)
    assert code == 0, out
    return out["plan_sha256"][scope]


def apply(ledger, scope, expect=None, as_of=AS_OF):
    return run(ledger, "close-stale", "--apply", "--scope", scope,
               "--expect-plan", expect or plan_sha(ledger, scope, as_of), "--as-of", as_of)


def body_rows(ledger):
    return {fl.parse_row(r)["nature"]: fl.parse_row(r)
            for r in ledger.read_text(encoding="utf-8").splitlines()
            if r.startswith("| 20") or r.startswith("| undated")}


def test_dry_run_lists_due_and_open_separately_and_writes_nothing(ledger):
    before = sha(ledger)
    code, out = run(ledger, "close-stale", "--dry-run", "--as-of", AS_OF)
    assert code == 0, out
    assert out["action"] == "dry-run"
    assert out["due"]["count"] == len(STALE_DUE)
    assert {r["nature"] for r in out["due"]["rows"]} == STALE_DUE
    assert out["open"]["count"] == len(STALE_OPEN)
    assert {r["nature"] for r in out["open"]["rows"]} == STALE_OPEN
    assert out["sha256"] == before
    assert out["half_life_days"] == ls.HALF_LIFE_DAYS["MACHINERY"]
    assert out["window"] == {"start": "2026-05-21", "end": AS_OF}
    assert out["already_stale_closed"] == 0
    assert set(out["plan_sha256"]) == {"due", "open", "both"}
    assert sha(ledger) == before


def test_apply_needs_an_explicit_scope(ledger):
    before = sha(ledger)
    code, out = run(ledger, "close-stale", "--apply", "--expect-plan",
                    plan_sha(ledger, "due"), "--as-of", AS_OF)
    assert code == 1 and out["status"] == "error"
    assert "--scope" in out["message"] and "no default" in out["message"]
    assert sha(ledger) == before


def test_apply_needs_the_plan_digest(ledger):
    before = sha(ledger)
    code, out = run(ledger, "close-stale", "--apply", "--scope", "due", "--as-of", AS_OF)
    assert code == 1 and out["status"] == "error"
    assert out["message"].startswith("--apply needs --expect-plan")
    assert sha(ledger) == before


def test_bad_as_of_is_refused(ledger):
    code, out = run(ledger, "close-stale", "--dry-run", "--as-of", "11/25/2026")
    assert code == 1 and out["status"] == "error"
    assert "--as-of must be YYYY-MM-DD" in out["message"]


def test_a_fire_after_as_of_is_skipped_not_closed():
    """A row whose last fire is after --as-of is reported, never closed, and does not
    crash freshness_state (which refuses an entry outside its window)."""
    rows = parsed()
    early = fl.date(2026, 7, 1)
    plan = fl.stale_plan(rows, early)
    names = natures(plan["skipped"])
    assert "sprocket row not found" in names                     # last fire 2026-11-20
    assert "sprocket row not found" not in natures(cands(plan))
    why = [r["_why"] for r in plan["skipped"] if r["nature"] == "sprocket row not found"]
    assert why == ["last fire 2026-11-20 is after 2026-07-01"]
    # A row whose whole range sits before as-of is still judged.


def test_apply_refuses_a_later_as_of_than_the_approved_plan(ledger):
    """Codex r1 F1 (P0): same ledger bytes, later --as-of; the boundary row turns stale and
    would be closed though the approved dry run never listed it."""
    approved = plan_sha(ledger, "due", AS_OF)
    before = sha(ledger)
    code, out = apply(ledger, "due", approved, as_of="2026-11-26")
    assert code != 0 and "plan changed" in out["message"]
    assert sha(ledger) == before


def test_apply_refuses_a_plan_for_another_scope(ledger):
    before = sha(ledger)
    code, out = apply(ledger, "both", plan_sha(ledger, "due"))
    assert code != 0 and "plan changed" in out["message"]
    assert sha(ledger) == before


def test_plan_digest_binds_the_half_life(monkeypatch):
    rows = parsed()[:1]
    as_of = fl.date.fromisoformat(AS_OF)
    a = fl.plan_digest("h", as_of, "due", rows)
    monkeypatch.setitem(ls.HALF_LIFE_DAYS, "MACHINERY", 61)
    assert fl.plan_digest("h", as_of, "due", rows) != a


def test_apply_refuses_when_the_ledger_changed(ledger):
    stale_sha = plan_sha(ledger, "due")
    ledger.write_text(LEDGER + "| 2026-11-24 | new_write.py | new row | x | 1 | — | friction |\n",
                      encoding="utf-8")
    before = sha(ledger)
    code, out = apply(ledger, "due", stale_sha)
    assert code != 0 and "changed" in out["message"]
    assert sha(ledger) == before


@pytest.mark.parametrize("scope,closed", [("due", STALE_DUE), ("open", STALE_OPEN),
                                          ("both", STALE)])
def test_apply_closes_only_the_scope_and_only_the_fix_cell(ledger, scope, closed):
    old = body_rows(ledger)
    code, out = apply(ledger, scope)
    assert code == 0, out
    assert out["changed"] == len(closed)
    new = body_rows(ledger)
    assert new.keys() == old.keys()
    for nature, row in new.items():
        for k in ("date", "surface", "nature", "occurrences", "promotion", "kind"):
            assert row[k] == old[nature][k]
        if nature in closed:
            assert fl.is_stale_close(row["fix"])
            assert ls.is_resolved(row["fix"])
            assert row["fix"].startswith(f"RESOLVED {AS_OF} (stale: half-life MACHINERY 60d")
        else:
            assert row["fix"] == old[nature]["fix"]


def test_stale_marker_is_distinct_from_a_fix():
    assert fl.is_stale_close("RESOLVED 2026-11-25 (stale: half-life MACHINERY 60d, "
                             "last fire 2026-07-01)")
    assert not fl.is_stale_close("RESOLVED 2026-07-01: fixed in abc123")
    assert not fl.is_stale_close("[auto-logged] stale cache")


def test_second_dry_run_after_apply_finds_nothing(ledger):
    apply(ledger, "both")
    code, out = run(ledger, "close-stale", "--dry-run", "--as-of", AS_OF)
    assert out["due"]["count"] == 0 and out["open"]["count"] == 0
    assert out["already_stale_closed"] == len(STALE)


def test_apply_changes_no_byte_outside_the_fix_cell(tmp_path):
    p = tmp_path / "friction-log.md"
    odd = LEDGER.replace("| 2026-08-01 | gadget_write.py |", "|  2026-08-01  |gadget_write.py|")
    p.write_text(odd, encoding="utf-8")
    before = p.read_text(encoding="utf-8").splitlines()
    code, out = apply(p, "open")
    assert code == 0 and out["changed"] == 1, out
    after = p.read_text(encoding="utf-8").splitlines()
    (old_row, new_row), = [(a, b) for a, b in zip(before, after) if a != b]
    o, n = old_row.split("|"), new_row.split("|")
    assert o[:4] == n[:4] and o[5:] == n[5:]


# ------------------------------------------------------------------ re-fire reopens

def test_auto_logged_refire_reopens_a_stale_closed_row(ledger):
    apply(ledger, "due")
    code, out = run(ledger, "append", "widget_write.py", "widget parse failed on empty input",
                    "--fix", "[auto-logged at error time]")
    assert code == 0 and out["action"] == "updated", out
    row = body_rows(ledger)["widget parse failed on empty input"]
    assert row["occurrences"] == "10"
    assert not ls.is_resolved(row["fix"])


def test_manual_refire_without_fix_reopens_a_stale_closed_row(ledger):
    apply(ledger, "due")
    code, out = run(ledger, "append", "widget_write.py", "widget parse failed on empty input")
    assert code == 0 and out["action"] == "updated", out
    row = body_rows(ledger)["widget parse failed on empty input"]
    assert row["occurrences"] == "10"
    assert not ls.is_resolved(row["fix"])
    assert ls.friction_state(row["kind"], row["fix"], row["occurrences"]) == "due"
    # The prior Fix text is kept, not erased (ce, 2026-11-25).
    assert row["fix"].startswith("REOPENED ")
    assert "; was: RESOLVED " in row["fix"] and "(stale: half-life MACHINERY 60d" in row["fix"]


def test_manual_refire_reopens_any_resolved_row_keeping_its_fix(ledger):
    """Pre-existing defect: a manual append matching a RESOLVED row kept the marker, so the
    count rose while the row read closed."""
    code, out = run(ledger, "append", "gear_write.py", "gear write refused")
    assert code == 0 and out["action"] == "updated", out
    row = body_rows(ledger)["gear write refused"]
    assert row["occurrences"] == "7"
    assert not ls.is_resolved(row["fix"])
    assert row["fix"].endswith("; was: RESOLVED 2026-07-01: fixed")


def test_manual_append_on_an_open_row_keeps_its_fix(ledger):
    run(ledger, "append", "sprocket_write.py", "sprocket row not found")
    assert body_rows(ledger)["sprocket row not found"]["fix"] == "[auto-logged]"
