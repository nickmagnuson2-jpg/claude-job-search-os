"""tests for the friction ledger's event Kind (7th column, 2026-11-22, learning-loop B2).

Every row carries one kind from learning_state.FRICTION_KINDS. `append` writes it
(--kind, else "friction", except a block: surface or an FP: nature, which must name it).
A legacy row has an empty Kind until `migrate-kinds` assigns one from a named rule; the
apply step is bound to the SHA-256 of the ledger the dry run reported. Surfaces and
natures below are invented.
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
import learning_state as ls  # noqa: E402

LEGACY = """# Friction Log

## Entries

| Date | Surface | Nature | Fix | Occurrences | Promotion |
|---|---|---|---|---|---|
| 2026-06-02 | widget_sync.py | command not found: widget interpreter missing | [auto-logged] | 1 | — |
| 2026-06-03 | block:check_widget.py | blocked a widget write as designed | — | 1 | — |
| 2026-06-04 | check_gadget.py | FP: blocked a legitimate gadget edit | — | 1 | — |
| 2026-06-05 | gadget_runner.py | pytest output captured by the auto logger | RESOLVED 2026-11-22: retag 2026-11-22: misattributed: not friction (test output) | 1 | — |
| 2026-06-06 | sprocket_hook.py | blocked sprocket edit | RESOLVED 2026-11-22: retag 2026-11-22: hook blocked as designed; not a friction | 1 | — |
| 2026-06-07 | sprocket_tool.py | sprocket file not found under output | RESOLVED 2026-11-22: retag 2026-11-22: misattributed: wrong surface | 2 | memory |
"""
HEADER7 = "| Date | Surface | Nature | Fix | Occurrences | Promotion | Kind |"


def run(ledger: Path, *args):
    r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                       env={"FRICTION_LEDGER": str(ledger), "PYTHONIOENCODING": "utf-8",
                            "PATH": "/usr/bin:/bin"}, timeout=60)
    lines = (r.stdout or "").strip().splitlines()
    return r.returncode, (json.loads(lines[-1]) if lines else {})


@pytest.fixture
def ledger(tmp_path):
    p = tmp_path / "friction-log.md"
    p.write_text(LEGACY, encoding="utf-8")
    return p


def rows(ledger):
    return [ln for ln in ledger.read_text(encoding="utf-8").splitlines()
            if ln.startswith("| 20")]


def kind_cell(row):
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return cells[6] if len(cells) > 6 else None


# ------------------------------------------------------------------ append

def test_append_default_kind_is_friction_and_is_written(ledger):
    code, out = run(ledger, "append", "doohickey.py", "doohickey parser rejects a valid flag")
    assert code == 0
    assert out["kind"] == "friction"
    assert out["counts"] is True
    assert kind_cell(rows(ledger)[0]) == "friction"


def test_first_write_upgrades_the_header_and_keeps_legacy_rows(ledger):
    run(ledger, "append", "doohickey.py", "doohickey parser rejects a valid flag")
    text = ledger.read_text(encoding="utf-8")
    assert HEADER7 in text
    assert "|---|---|---|---|---|---|---|" in text
    assert "| Date | Surface | Nature | Fix | Occurrences | Promotion |\n" not in text
    for legacy in LEGACY.splitlines():
        if legacy.startswith("| 20"):
            assert legacy in text            # untouched byte for byte, kind still empty


@pytest.mark.parametrize("kind", ["block-as-designed", "harness", "false-positive-block"])
def test_append_explicit_kind(ledger, kind):
    code, out = run(ledger, "append", "doohickey.py", "doohickey event of another kind",
                    "--kind", kind)
    assert code == 0
    assert out["kind"] == kind
    assert out["counts"] is ls.counts(kind)
    assert kind_cell(rows(ledger)[0]) == kind


def test_append_unknown_kind_is_refused(ledger):
    code, out = run(ledger, "append", "doohickey.py", "doohickey event", "--kind", "oops")
    assert code == 1
    assert out["message"].startswith("--kind 'oops' is not one of")
    assert ledger.read_text(encoding="utf-8") == LEGACY


@pytest.mark.parametrize("surface,nature,kind", [
    ("block:check_doohickey.py", "blocked a doohickey write", "block-as-designed"),
    ("check_doohickey.py", "FP: blocked a legitimate doohickey edit", "false-positive-block"),
    ("check_doohickey.py", "  fp: lower-case prefix still counts", "false-positive-block"),
    ("block:check_doohickey.py", "FP: the block was wrong", "false-positive-block"),
    ("harness:permission", "permission prompt denied", "harness"),
    ("doohickey.py", "blocked: the word alone is not a marker", "friction"),
])
def test_the_events_own_markers_set_the_kind(ledger, surface, nature, kind):
    """The auto-loggers never pass --kind; the markers they write decide."""
    code, out = run(ledger, "append", surface, nature)
    assert code == 0
    assert out["kind"] == kind
    assert kind_cell(rows(ledger)[0]) == kind


def test_explicit_kind_beats_the_markers(ledger):
    code, out = run(ledger, "append", "block:check_doohickey.py", "blocked a doohickey write",
                    "--kind", "false-positive-block")
    assert out["kind"] == "false-positive-block"
    assert out["counts"] is True


def test_block_as_designed_never_asks_for_promotion(ledger):
    for _ in range(3):
        code, out = run(ledger, "append", "block:check_doohickey.py", "blocked a doohickey write")
    assert out["occurrences"] == 3
    assert out["promotion_action"] == "none"


def test_non_counting_kind_never_asks_for_promotion(ledger):
    for _ in range(3):
        code, out = run(ledger, "append", "harness_probe.py", "permission prompt denied probe run",
                        "--kind", "harness")
    assert out["occurrences"] == 3
    assert out["counts"] is False
    assert out["promotion_action"] == "none"


def test_counting_kind_climbs_the_ladder(ledger):
    for _ in range(3):
        code, out = run(ledger, "append", "doohickey.py", "doohickey parser rejects a valid flag")
    assert out["occurrences"] == 3
    assert out["promotion_action"] == "script-patch"


def test_a_row_of_another_kind_is_never_merged(ledger):
    run(ledger, "append", "doohickey.py", "doohickey parser rejects a valid flag")
    code, out = run(ledger, "append", "doohickey.py", "doohickey parser rejects a valid flag",
                    "--kind", "harness")
    assert out["action"] == "appended"
    assert out["occurrences"] == 1
    kinds = sorted(kind_cell(r) for r in rows(ledger) if "doohickey parser" in r)
    assert kinds == ["friction", "harness"]


def test_a_legacy_row_takes_the_new_events_kind(ledger):
    code, out = run(ledger, "append", "widget_sync.py",
                    "command not found: widget interpreter missing")
    assert out["action"] == "updated"
    assert out["occurrences"] == 2
    row = next(r for r in rows(ledger) if "widget interpreter" in r)
    assert kind_cell(row) == "friction"


def test_resolve_keeps_the_kind(ledger):
    run(ledger, "append", "doohickey.py", "doohickey parser rejects a valid flag", "--kind", "harness")
    code, out = run(ledger, "resolve", "doohickey.py", "doohickey parser rejects", "--note", "done")
    assert out["action"] == "resolved"
    row = next(r for r in rows(ledger) if "doohickey parser" in r)
    assert kind_cell(row) == "harness"


def test_dedup_never_merges_across_kinds(ledger):
    text = LEGACY.replace("| Promotion |\n|---|---|---|---|---|---|",
                          "| Promotion | Kind |\n|---|---|---|---|---|---|---|")
    text += ("| 2026-06-08 | doohickey.py | doohickey parser rejects a valid flag | — | 1 | — | friction |\n"
             "| 2026-06-09 | doohickey.py | doohickey parser rejects a valid flag | — | 1 | — | harness |\n")
    ledger.write_text(text, encoding="utf-8")
    code, out = run(ledger, "dedup")
    assert out["action"] == "rebuilt"
    kinds = sorted(kind_cell(r) for r in rows(ledger) if "doohickey parser" in r)
    assert kinds == ["friction", "harness"]


# ------------------------------------------------------------------ migrate-kinds

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_dry_run_names_every_rule_default_first_and_writes_nothing(ledger):
    code, out = run(ledger, "migrate-kinds", "--dry-run")
    assert code == 0
    assert out["sha256"] == sha(ledger)
    rules = {r["rule"]: (r["rows"], r["kind"]) for r in out["rules"]}
    assert out["rules"][0]["rule"] == "default-friction"
    assert rules["default-friction"] == (2, "friction")      # widget_sync + wrong-surface row
    assert rules["block-surface"] == (1, "block-as-designed")
    assert rules["retag-hook-blocked-as-designed"] == (1, "block-as-designed")
    assert rules["retag-not-friction"] == (1, "not-friction")
    assert rules["fp-nature"] == (1, "false-positive-block")
    assert out["rows"] == 6
    assert sum(r["rows"] for r in out["rules"]) == 6
    assert ledger.read_text(encoding="utf-8") == LEGACY


def test_apply_needs_the_hash(ledger):
    code, out = run(ledger, "migrate-kinds", "--apply")
    assert code == 1
    assert "--expect-sha" in out["message"]
    assert ledger.read_text(encoding="utf-8") == LEGACY


def test_apply_refuses_when_the_ledger_changed(ledger):
    stale = sha(ledger)
    run(ledger, "append", "doohickey.py", "doohickey parser rejects a valid flag")
    changed = ledger.read_text(encoding="utf-8")
    code, out = run(ledger, "migrate-kinds", "--apply", "--expect-sha", stale)
    assert code == 1
    assert "ledger changed since the approved dry run" in out["message"]
    assert ledger.read_text(encoding="utf-8") == changed


def test_apply_sets_only_kind_cells(ledger):
    code, out = run(ledger, "migrate-kinds", "--apply", "--expect-sha", sha(ledger))
    assert code == 0, out
    assert out["changed"] == 6
    before = [ln for ln in LEGACY.splitlines() if ln.startswith("| 20")]
    after = rows(ledger)
    assert len(before) == len(after)
    expected = ["friction", "block-as-designed", "false-positive-block", "not-friction",
                "block-as-designed", "friction"]
    for b, a, k in zip(before, after, expected):
        assert a == b + f" {k} |"
    assert HEADER7 in ledger.read_text(encoding="utf-8")
    # A second dry run finds nothing left to assign.
    code, out = run(ledger, "migrate-kinds", "--dry-run")
    counts = {r["rule"]: r["rows"] for r in out["rules"]}
    assert counts["already-set"] == 6
    assert sum(counts.values()) == 6          # each row is planned exactly once
    assert counts["unrecognized-kind"] == 0


def test_unrecognized_kind_is_reported_and_left_alone(ledger):
    text = LEGACY.replace("| Promotion |\n|---|---|---|---|---|---|",
                          "| Promotion | Kind |\n|---|---|---|---|---|---|---|")
    text += "| 2026-06-08 | doohickey.py | doohickey thing | — | 1 | — | mystery |\n"
    ledger.write_text(text, encoding="utf-8")
    code, out = run(ledger, "migrate-kinds", "--dry-run")
    assert {r["rule"]: r["rows"] for r in out["rules"]}["unrecognized-kind"] == 1
    assert "mystery" not in out["kinds_after"]
    assert sum(out["kinds_after"].values()) == 6
    assert out["unrecognized"] == ["| 2026-06-08 | doohickey.py | doohickey thing | — | 1 | — | mystery |"]
    code, out = run(ledger, "migrate-kinds", "--apply", "--expect-sha", sha(ledger))
    assert code == 0
    assert "| 2026-06-08 | doohickey.py | doohickey thing | — | 1 | — | mystery |" in rows(ledger)


def test_seven_cell_row_with_empty_kind_is_filled(ledger):
    text = LEGACY.replace("| Promotion |\n|---|---|---|---|---|---|",
                          "| Promotion | Kind |\n|---|---|---|---|---|---|---|")
    text += "| 2026-06-08 | doohickey.py | doohickey thing | — | 1 | — |  |\n"
    ledger.write_text(text, encoding="utf-8")
    code, out = run(ledger, "migrate-kinds", "--apply", "--expect-sha", sha(ledger))
    assert code == 0, out
    assert "| 2026-06-08 | doohickey.py | doohickey thing | — | 1 | — | friction |" in rows(ledger)


def test_identical_duplicate_rows_are_each_counted(ledger):
    dup = "| 2026-06-02 | widget_sync.py | command not found: widget interpreter missing | [auto-logged] | 1 | — |"
    ledger.write_text(LEGACY + dup + "\n", encoding="utf-8")
    code, out = run(ledger, "migrate-kinds", "--apply", "--expect-sha", sha(ledger))
    assert code == 0, out
    assert out["changed"] == 7
    assert rows(ledger).count(dup + " friction |") == 2


def test_apply_keeps_text_after_the_table_and_ends_in_one_newline(ledger):
    ledger.write_text(LEGACY + "\nA note after the table.\n", encoding="utf-8")
    code, out = run(ledger, "migrate-kinds", "--apply", "--expect-sha", sha(ledger))
    assert code == 0, out
    text = ledger.read_text(encoding="utf-8")
    assert text.endswith("|\n\nA note after the table.\n")


def test_apply_on_a_ledger_without_a_final_newline(ledger):
    ledger.write_text(LEGACY.rstrip("\n"), encoding="utf-8")
    code, out = run(ledger, "migrate-kinds", "--apply", "--expect-sha", sha(ledger))
    assert code == 0, out
    text = ledger.read_text(encoding="utf-8")
    assert text.endswith(" friction |\n") and not text.endswith("\n\n")


def test_only_kind_changed():
    import friction_log as fl
    old = "| 2026-06-02 | widget_sync.py | widget missing | — | 1 | — |"
    assert fl.only_kind_changed(old, old[:-1] + "| friction |")
    assert not fl.only_kind_changed(old, old[:-1] + "| mystery |")
    assert not fl.only_kind_changed(old, old.replace("| 1 |", "| 2 |")[:-1] + "| friction |")
    assert not fl.only_kind_changed(old, old.replace("widget missing", "x")[:-1] + "| friction |")
    assert not fl.only_kind_changed("not a row", old[:-1] + "| friction |")


def test_whitespace_after_the_table_is_dropped_on_apply(ledger):
    ledger.write_text(LEGACY + "   \n", encoding="utf-8")
    code, out = run(ledger, "migrate-kinds", "--apply", "--expect-sha", sha(ledger))
    assert code == 0, out
    assert ledger.read_text(encoding="utf-8").endswith("misattributed: wrong surface | 2 | memory | friction |\n")


def test_a_non_counting_row_never_stores_a_rung_and_is_not_listed_as_unpromoted(ledger):
    """Codex r1 F2: two block-as-designed events stored 'memory (do now)' and list
    --unpromoted returned the row as promotion work."""
    for _ in range(2):
        run(ledger, "append", "block:check_doohickey.py", "blocked a doohickey write")
    row = next(r for r in rows(ledger) if "doohickey write" in r)
    assert row.endswith("| 2 | — | block-as-designed |")
    code, out = run(ledger, "list", "--unpromoted")
    assert all("doohickey write" not in r["nature"] for r in out["rows"])


def test_a_legacy_non_counting_rung_is_not_listed_as_unpromoted(ledger):
    text = LEGACY.replace("| Promotion |\n|---|---|---|---|---|---|",
                          "| Promotion | Kind |\n|---|---|---|---|---|---|---|")
    text += "| 2026-06-08 | harness_probe.py | permission prompt | — | 2 | memory (do now) | harness |\n"
    text += "| 2026-06-09 | doohickey.py | doohickey parser fails | — | 2 | memory (do now) | friction |\n"
    ledger.write_text(text, encoding="utf-8")
    code, out = run(ledger, "list", "--unpromoted")
    natures = [r["nature"] for r in out["rows"]]
    assert "doohickey parser fails" in natures
    assert "permission prompt" not in natures


def test_dedup_keeps_a_non_counting_cluster_off_the_ladder(ledger):
    text = LEGACY.replace("| Promotion |\n|---|---|---|---|---|---|",
                          "| Promotion | Kind |\n|---|---|---|---|---|---|---|")
    text += ("| 2026-06-08 | harness_probe.py | permission prompt denied probe | — | 1 | — | harness |\n"
             "| 2026-06-09 | harness_probe.py | permission prompt denied probe | — | 1 | — | harness |\n")
    ledger.write_text(text, encoding="utf-8")
    run(ledger, "dedup")
    row = next(r for r in rows(ledger) if "permission prompt" in r)
    assert row.endswith("| 2 | — | harness |")
