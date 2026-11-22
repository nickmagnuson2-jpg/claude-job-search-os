"""Tests for memory_usage.py's output: rows, meta row, file writing and the /standup line.

The rules (kind, half-life, re-confirmation breadth, freshness state) are owned by
tools/learning_state.py (B1). These tests pin that memory_usage USES them and adds no rule
of its own: the matrix "Kind" and "Freshness" rows. Fixtures are synthetic.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import learning_state as ls  # noqa: E402
import memory_usage as mu  # noqa: E402

W_START, W_END = date(2026, 8, 24), date(2026, 11, 22)   # 90-day window, as on real data
TRUSTED = sorted(ls.TRUSTED_KIND_SOURCES)


def _class_rows():
    return [
        {"file": "feedback_mach_recent", "kind": "MACHINERY", "source": TRUSTED[0]},
        {"file": "feedback_mach_old", "kind": "MACHINERY", "source": TRUSTED[0]},
        {"file": "feedback_mach_never", "kind": "MACHINERY", "source": TRUSTED[1]},
        {"file": "feedback_lesson_never", "kind": "LESSON", "source": TRUSTED[0]},
        {"file": "feedback_desc_only", "kind": "MACHINERY", "source": "112226 P1a description-read"},
    ]


def _summary():
    return {
        "feedback_mach_recent": {"last_context_entry": date(2026, 11, 1), "last_entry_any_session": date(2026, 11, 1),
                                 "channels": {"read": 1}, "sessions": 1, "use_days": [date(2026, 11, 1)],
                                 "broad_session_entries": 0},
        "feedback_mach_old": {"last_context_entry": date(2026, 8, 30), "last_entry_any_session": date(2026, 11, 10),
                              "channels": {"bash_print": 2}, "sessions": 1, "use_days": [date(2026, 8, 30)],
                              "broad_session_entries": 1},
        "feedback_desc_only": {"last_context_entry": date(2026, 11, 20), "last_entry_any_session": date(2026, 11, 20),
                               "channels": {"grep_line": 1}, "sessions": 1, "use_days": [date(2026, 11, 20)],
                               "broad_session_entries": 0},
    }


STEMS = {r["file"] for r in _class_rows()} | {"feedback_unmapped"}


def _rows():
    return {r["file"]: r for r in mu.build_rows(_summary(), STEMS, _class_rows(), W_START, W_END)}


# ---------------------------------------------------------------------------- rows

def test_every_rule_file_gets_a_row_even_with_no_entry():
    assert set(_rows()) == STEMS


@pytest.mark.parametrize("stem", sorted(STEMS))
def test_each_row_state_is_exactly_learning_states_answer(stem):
    """No rule of memory_usage's own: the state is freshness_state(kind_of(row), last entry)."""
    by_file = {r["file"]: r for r in _class_rows()}
    kind = ls.kind_of(by_file[stem]) if stem in by_file else None
    last = _summary().get(stem, {}).get("last_context_entry")
    assert _rows()[stem]["state"] == ls.freshness_state(kind, last, W_START, W_END)
    assert _rows()[stem]["kind"] == kind


def test_expected_states_on_the_fixture():
    r = _rows()
    assert r["feedback_mach_recent"]["state"] == "current"
    assert r["feedback_mach_old"]["state"] == "unverified"         # entry older than 60 days
    assert r["feedback_mach_never"]["state"] == "unverified"       # W 90 >= H 60, no entry
    assert r["feedback_lesson_never"]["state"] == "unknown_not_observed"   # W 90 < H 240
    assert r["feedback_desc_only"]["state"] == "unknown_no_kind"   # description-only source
    assert r["feedback_unmapped"]["state"] == "unknown_no_kind"


def test_a_broad_session_entry_does_not_reconfirm():
    """feedback_mach_old was entered on 11-10 only by a broad session: still unverified."""
    r = _rows()["feedback_mach_old"]
    assert r["last_context_entry"] == "2026-08-30"
    assert r["last_entry_any_session"] == "2026-11-10"
    assert r["state"] == "unverified"


def test_the_window_start_is_never_written_as_a_date_of_entry():
    for r in _rows().values():
        assert r["last_context_entry"] != W_START.isoformat() or r["file"] in _summary()


def test_rows_record_the_kind_source():
    r = _rows()
    assert r["feedback_mach_recent"]["kind_source"] == TRUSTED[0]
    assert r["feedback_desc_only"]["kind_source"] == "112226 P1a description-read"
    assert r["feedback_unmapped"]["kind_source"] is None


# ---------------------------------------------------------------------------- meta

def _meta(previous=None, summary=None):
    rows = mu.build_rows(summary or _summary(), STEMS, _class_rows(), W_START, W_END)
    scan = mu.ScanResult([], W_START, W_END, {"heredoc_refs": 7, "sibling_transcripts_not_scanned": 2}, 10)
    return mu.build_meta(rows, scan, summary or _summary(), previous_counts=previous)


def test_meta_counts_states_by_kind():
    m = _meta()
    assert m["type"] == "meta"
    assert m["counts"]["MACHINERY"] == {"current": 1, "unverified": 2}
    assert m["counts"]["LESSON"] == {"unknown_not_observed": 1}
    assert m["counts"]["no kind"] == {"unknown_no_kind": 2}


def test_meta_states_the_window_breadth_rule_and_known_gaps():
    m = _meta()
    assert (m["window_start"], m["window_end"], m["window_days"]) == ("2026-08-24", "2026-11-22", 90)
    assert m["audit_breadth"] == ls.AUDIT_BREADTH
    assert m["half_life_days"] == ls.HALF_LIFE_DAYS
    assert m["known_gaps"]["heredoc_refs"] == 7
    assert m["known_gaps"]["sibling_transcripts_not_scanned"] == 2
    assert "never claims a rule is unused" in m["known_gaps"]["note"]


def test_meta_carries_the_previous_runs_counts():
    prev = {"MACHINERY": {"current": 5}}
    assert _meta(previous=prev)["previous_counts"] == prev
    assert _meta()["previous_counts"] is None


def test_gaps_are_recorded_with_their_sample_size_and_no_half_life_is_proposed():
    """Org 68, 2026-11-22: p75 of reuse gaps measures reuse, not validity, and the window
    cuts off long gaps. Record the distribution; propose nothing until Nick rules."""
    many = {f"feedback_m{i}": {"last_context_entry": date(2026, 11, 20), "last_entry_any_session": date(2026, 11, 20),
                               "channels": {"read": 1}, "sessions": 1, "broad_session_entries": 0,
                               "use_days": [date(2026, 11, 1), date(2026, 11, 11), date(2026, 11, 20)]}
            for i in range(15)}                                   # 30 gaps of 10 and 9 days
    rows = [{"file": k, "kind": "MACHINERY", "source": TRUSTED[0]} for k in many]
    built = mu.build_rows(many, set(many), rows, W_START, W_END)
    scan = mu.ScanResult([], W_START, W_END, {"heredoc_refs": 0, "sibling_transcripts_not_scanned": 0}, 1)
    m = mu.build_meta(built, scan, many, previous_counts=None)
    g = m["gaps_by_kind"]["MACHINERY"]
    assert (g["n"], g["p75"], g["half_life_days"]) == (30, 10, 60)
    for kind in ls.HALF_LIFE_DAYS:
        assert m["gaps_by_kind"][kind]["proposal"] == "no proposal: estimator under review"
        assert "proposed_half_life_days" not in m["gaps_by_kind"][kind]
    assert m["gaps_by_kind"]["FACT"]["n"] == 0 and m["gaps_by_kind"]["FACT"]["p75"] is None


# ---------------------------------------------------------------------------- file

def test_write_then_read_round_trips_and_keeps_previous_counts(tmp_path):
    out = tmp_path / "memory-usage.jsonl"
    rows = mu.build_rows(_summary(), STEMS, _class_rows(), W_START, W_END)
    meta = _meta()
    mu.write_usage(out, rows, meta)
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert lines[0]["type"] == "meta" and len(lines) == 1 + len(STEMS)
    assert mu.read_previous_counts(out) == meta["counts"]
    assert mu.read_previous_counts(tmp_path / "missing.jsonl") is None


def test_writing_into_the_live_memory_folder_from_a_test_is_refused(monkeypatch):
    target = mu.LIVE_MEMORY_DIR / "memory-usage.jsonl"
    with pytest.raises(RuntimeError, match="live memory folder"):
        mu.write_usage(target, [], {"type": "meta"})


def test_main_dry_run_writes_nothing(tmp_path, capsys):
    proj = tmp_path / "proj"
    (proj / "memory").mkdir(parents=True)
    (proj / "memory" / "feedback_alpha_rule.md").write_text("x")
    (proj / "s1.jsonl").write_text(json.dumps({"type": "user", "timestamp": "2026-11-20T18:00:00Z",
                                               "message": {"content": "hi"}}) + "\n")
    rc = mu.main(["--project-dir", str(proj), "--dry-run"])
    assert rc == 0
    assert not (proj / "memory" / "memory-usage.jsonl").exists()
    assert '"type": "meta"' in capsys.readouterr().out


def test_main_writes_the_file_and_a_missing_class_map_means_no_kind(tmp_path):
    proj = tmp_path / "proj"
    (proj / "memory").mkdir(parents=True)
    (proj / "memory" / "feedback_alpha_rule.md").write_text("x")
    (proj / "s1.jsonl").write_text(json.dumps({"type": "user", "timestamp": "2026-11-20T18:00:00Z",
                                               "message": {"content": "hi"}}) + "\n")
    assert mu.main(["--project-dir", str(proj)]) == 0
    lines = [json.loads(x) for x in (proj / "memory" / "memory-usage.jsonl").read_text().splitlines()]
    assert lines[0]["class_map"] is None
    assert lines[1]["state"] == "unknown_no_kind"


def test_main_with_no_transcripts_fails_loudly(tmp_path, capsys):
    proj = tmp_path / "proj"
    (proj / "memory").mkdir(parents=True)
    assert mu.main(["--project-dir", str(proj)]) == 2
    assert "no transcripts" in capsys.readouterr().err


# ---------------------------------------------------------------------------- /standup line

def test_standup_line_shows_states_by_kind_and_the_change(tmp_path):
    out = tmp_path / "u.jsonl"
    meta = _meta(previous={"MACHINERY": {"current": 2, "unverified": 1}, "LESSON": {"unknown_not_observed": 1},
                           "no kind": {"unknown_no_kind": 2}})
    mu.write_usage(out, [], meta)
    line = mu.standup_line(out)
    assert line.startswith("Memory freshness (context entry, 2026-08-24 to 2026-11-22):")
    assert "MACHINERY 1 current / 2 unverified / 0 unknown" in line
    assert "LESSON 0 current / 0 unverified / 1 unknown" in line
    assert "no kind 2" in line
    assert "MACHINERY current -1, unverified +1" in line


def test_standup_line_first_run_and_missing_file(tmp_path):
    out = tmp_path / "u.jsonl"
    mu.write_usage(out, [], _meta())
    assert "first run" in mu.standup_line(out)
    assert mu.standup_line(tmp_path / "none.jsonl") is None


def test_standup_line_says_unverified_is_not_unused(tmp_path):
    out = tmp_path / "u.jsonl"
    mu.write_usage(out, [], _meta())
    assert "unverified = not re-confirmed, not unused" in mu.standup_line(out)


# ---------------------------------------------------------------------------- class map, CLI (mutation run 3)

def test_load_class_rows_skips_corrupt_and_fileless_lines(tmp_path):
    p = tmp_path / "classes.jsonl"
    p.write_text('{"file": "feedback_a", "kind": "FACT", "source": "s"}\n{bad json\n[1]\n{"kind": "FACT"}\n'
                 '{"file": "", "kind": "FACT"}\n{"file": "feedback_b", "kind": "LESSON", "source": "s"}\n')
    assert [r["file"] for r in mu.load_class_rows(p)] == ["feedback_a", "feedback_b"]
    assert mu.load_class_rows(tmp_path / "missing.jsonl") is None
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert mu.load_class_rows(empty) == []


def test_only_paths_inside_the_live_memory_folder_are_live(tmp_path):
    assert mu._is_live(mu.LIVE_MEMORY_DIR / "memory-usage.jsonl") is True
    assert mu._is_live(tmp_path / "memory-usage.jsonl") is False


def test_the_change_part_names_only_kinds_that_changed(tmp_path):
    out = tmp_path / "u.jsonl"
    prev = {k: dict(v) for k, v in _meta()["counts"].items()}
    prev["MACHINERY"] = {"current": 2, "unverified": 1}
    mu.write_usage(out, [], _meta(previous=prev))
    assert mu.standup_line(out).endswith("Change since last run: MACHINERY current -1, unverified +1.")
    mu.write_usage(out, [], _meta(previous=_meta()["counts"]))
    assert mu.standup_line(out).endswith("No change since last run.")


def _proj(tmp_path):
    proj = tmp_path / "proj"
    (proj / "memory").mkdir(parents=True)
    (proj / "memory" / "feedback_alpha_rule.md").write_text("x")
    (proj / "s1.jsonl").write_text(json.dumps({"type": "user", "timestamp": "2026-11-20T18:00:00Z",
                                               "message": {"content": "hi"}}) + "\n")
    return proj


def test_main_standup_prints_the_line_or_says_no_run_yet(tmp_path, capsys):
    proj = _proj(tmp_path)
    assert mu.main(["--project-dir", str(proj), "--standup"]) == 0
    assert "no run yet" in capsys.readouterr().out
    assert not (proj / "memory" / "memory-usage.jsonl").exists()
    assert mu.main(["--project-dir", str(proj)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("scope: memory rule files in") and lines[1].startswith("Memory freshness (context entry")
    assert mu.main(["--project-dir", str(proj), "--standup"]) == 0
    assert "first run" in capsys.readouterr().out


def test_the_script_runs_from_the_command_line(tmp_path):
    import subprocess
    proj = _proj(tmp_path)
    r = subprocess.run([sys.executable, str(Path(mu.__file__)), "--project-dir", str(proj), "--dry-run"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and '"type": "meta"' in r.stdout


def test_a_run_states_its_reach(tmp_path, capsys):
    """Count rule (tools/count_report.py): the run names its population and what it left out."""
    proj = _proj(tmp_path)
    (proj / "memory" / "reference_beta_fact.md").write_text("x")
    (proj / "s1.jsonl").write_text(json.dumps({"type": "user", "timestamp": "2026-11-20T18:00:00Z",
                                               "message": {"content": "hi"}}) + "\n"
                                   + json.dumps({"type": "attachment", "timestamp": "2026-11-20T18:00:00Z",
                                                 "attachment": {"type": "file",
                                                                "filename": str(proj / "memory" / "feedback_alpha_rule.md")}})
                                   + "\n")
    assert mu.main(["--project-dir", str(proj)]) == 0
    out = capsys.readouterr().out
    assert (f"scope: memory rule files in {proj / 'memory'} | population 2 | included 1 | "
            "excluded: no re-confirming context entry in the window 1") in out
