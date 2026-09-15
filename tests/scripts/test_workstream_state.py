"""Tests for tools/workstream_state.py — the generic registry probe runner.

Written BEFORE the implementation, against output/analysis/091426-workstreams-build-spec.md.
The spec's D1 state table and D3 runner contract are the requirements; each test names the
clause it pins.

The defect these exist to prevent, from the /codex-verify run on 2026-09-14 (F7): a probe
that fails must never collapse to `unchanged` or to zero. A broken probe would otherwise make
a workstream go quiet, which reads identically to a workstream that is fine.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import workstream_state as ws  # noqa: E402


def write_ws(tmp_path: Path, name: str, *, probe=None, status="ACTIVE", body="") -> Path:
    p = tmp_path / f"{name}.md"
    parts = [f"# Workstream: {name}", "", f"| **Status** | {status} |", ""]
    if probe is not None:
        parts.append(f"<!-- PROBE: {json.dumps(probe)} -->")
        parts.append("")
    parts.append(body or "## Why")
    p.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return p


_probe_seq = itertools.count()


def probe_emitting(tmp_path: Path, payload: str, *, code: int = 0, sleep: float = 0) -> list:
    """An argv list for a throwaway python probe with controlled output/exit.

    Each call gets its OWN file. A single shared `probe.py` silently made every probe in a
    test emit whatever the LAST call wrote, which made test_summary_counts_each_state pass
    for the wrong reason until mutation testing forced the counts to be asserted exactly.
    """
    s = tmp_path / f"probe_{next(_probe_seq)}.py"
    s.write_text(
        f"import sys,time\n"
        f"time.sleep({sleep})\n"
        f"sys.stdout.write({payload!r})\n"
        f"sys.exit({code})\n",
        encoding="utf-8",
    )
    return [sys.executable, str(s)]


# ---------- D3: the runner contract ----------

def test_probe_success_returns_value(tmp_path):
    """D3: stdout must be JSON with a declared `value` field."""
    p = write_ws(tmp_path, "ok", probe=probe_emitting(tmp_path, '{"value": 42}'))
    r = ws.evaluate(p, repo_root=tmp_path)
    assert r["state"] in ("changed", "unchanged")
    assert r["value"] == 42


def test_nonzero_exit_is_error_not_zero(tmp_path):
    """D3 + F7: a failing probe is `error`. It must NOT read as 0 or as unchanged."""
    p = write_ws(tmp_path, "boom", probe=probe_emitting(tmp_path, '{"value": 1}', code=3))
    r = ws.evaluate(p, repo_root=tmp_path)
    assert r["state"] == "error"
    assert r.get("value") != 0


def test_unparseable_stdout_is_error(tmp_path):
    """D3: anything that is not JSON is `error`."""
    p = write_ws(tmp_path, "junk", probe=probe_emitting(tmp_path, "not json at all"))
    assert ws.evaluate(p, repo_root=tmp_path)["state"] == "error"


def test_json_without_value_field_is_error(tmp_path):
    """D3: the `value` field is required; a schema change must not pass silently."""
    p = write_ws(tmp_path, "noval", probe=probe_emitting(tmp_path, '{"count": 7}'))
    assert ws.evaluate(p, repo_root=tmp_path)["state"] == "error"


def test_timeout_is_error(tmp_path):
    """D3: exceeding the timeout is `error`, not zero."""
    p = write_ws(tmp_path, "slow", probe=probe_emitting(tmp_path, '{"value":1}', sleep=2))
    assert ws.evaluate(p, repo_root=tmp_path, timeout=0.3)["state"] == "error"


def test_oversized_output_is_error(tmp_path):
    """D3: output above the cap is `error`, never truncated-and-accepted."""
    big = json.dumps({"value": "x" * 5000})
    p = write_ws(tmp_path, "big", probe=probe_emitting(tmp_path, big))
    assert ws.evaluate(p, repo_root=tmp_path, max_bytes=1000)["state"] == "error"


def test_probe_is_never_run_through_a_shell(tmp_path):
    """D3: argv list, never shell=True. A shell metachar must not be interpreted."""
    p = write_ws(tmp_path, "shelly", probe=["echo", "hi; rm -rf /tmp/nope"])
    r = ws.evaluate(p, repo_root=tmp_path)
    # echo's output is not JSON -> error. The point is it did not tokenise on `;`.
    assert r["state"] == "error"
    assert not Path("/tmp/nope").exists()


# ---------- D1: the state table ----------

def test_no_probe_is_typed_not_error(tmp_path):
    """D1: absence of a probe is a legitimate state, distinct from a failed probe."""
    p = write_ws(tmp_path, "typed", probe=None)
    assert ws.evaluate(p, repo_root=tmp_path)["state"] == "typed"


def test_terminal_is_excluded_entirely(tmp_path):
    """D1: terminal never surfaces, even if its probe would report a change."""
    p = write_ws(tmp_path, "done", probe=probe_emitting(tmp_path, '{"value": 9}'),
                 status="TERMINAL")
    r = ws.evaluate(p, repo_root=tmp_path)
    assert r["state"] == "terminal"
    assert r["surfaces"] is False


def test_changed_vs_unchanged_is_measured_against_last_surfaced(tmp_path):
    """D1: changed ::= current != last_surfaced. Nothing else."""
    p = write_ws(tmp_path, "drift", probe=probe_emitting(tmp_path, '{"value": 5}'))
    obs = {"drift": {"value": 5, "at": "2026-09-14T00:00:00"}}
    assert ws.evaluate(p, repo_root=tmp_path, observations=obs)["state"] == "unchanged"
    obs["drift"]["value"] = 4
    assert ws.evaluate(p, repo_root=tmp_path, observations=obs)["state"] == "changed"


def test_first_ever_run_surfaces(tmp_path):
    """D1: no prior observation means the human has never seen it -> surface."""
    p = write_ws(tmp_path, "fresh", probe=probe_emitting(tmp_path, '{"value": 1}'))
    r = ws.evaluate(p, repo_root=tmp_path, observations={})
    assert r["state"] == "changed" and r["surfaces"] is True


def test_error_surfaces(tmp_path):
    """F7: an error is loud."""
    p = write_ws(tmp_path, "err", probe=probe_emitting(tmp_path, "{}", code=1))
    assert ws.evaluate(p, repo_root=tmp_path)["surfaces"] is True


# ---------- the ack, and why it is written last ----------

def test_ack_is_not_written_by_evaluate(tmp_path):
    """D1: the runner must NOT advance last-surfaced. Only a successful render may."""
    p = write_ws(tmp_path, "noack", probe=probe_emitting(tmp_path, '{"value": 3}'))
    obs_path = tmp_path / ".observations.json"
    ws.evaluate(p, repo_root=tmp_path)
    assert not obs_path.exists(), "evaluate() wrote an ack; a crash would then drop the change"


def test_acknowledge_advances_only_named_entries(tmp_path):
    p = write_ws(tmp_path, "a", probe=probe_emitting(tmp_path, '{"value": 1}'))
    obs_path = tmp_path / ".observations.json"
    ws.acknowledge(obs_path, {"a": 1, "b": 2})
    got = json.loads(obs_path.read_text())
    assert got["a"]["value"] == 1 and got["b"]["value"] == 2
    assert "at" in got["a"]


# ---------- D3: block writing ----------

def test_write_block_is_idempotent(tmp_path):
    p = write_ws(tmp_path, "blk", probe=probe_emitting(tmp_path, '{"value": 8}'))
    ws.render(p, repo_root=tmp_path)
    first = p.read_text(encoding="utf-8")
    ws.render(p, repo_root=tmp_path)
    assert p.read_text(encoding="utf-8").count(ws.BEGIN) == 1
    assert first == p.read_text(encoding="utf-8")


def test_duplicate_markers_are_a_hard_failure(tmp_path):
    """D3: a malformed/duplicated marker pair must never silently double-write."""
    p = write_ws(tmp_path, "dup", probe=probe_emitting(tmp_path, '{"value": 1}'))
    p.write_text(p.read_text(encoding="utf-8") + f"\n{ws.BEGIN}\n{ws.END}\n{ws.BEGIN}\n{ws.END}\n",
                 encoding="utf-8")
    with pytest.raises(ws.MarkerError):
        ws.render(p, repo_root=tmp_path)


def test_unterminated_marker_is_a_hard_failure(tmp_path):
    p = write_ws(tmp_path, "unterm", probe=probe_emitting(tmp_path, '{"value": 1}'))
    p.write_text(p.read_text(encoding="utf-8") + f"\n{ws.BEGIN}\n", encoding="utf-8")
    with pytest.raises(ws.MarkerError):
        ws.render(p, repo_root=tmp_path)


# ---------- the registry drift guard (replaces the handoff-specific one) ----------

def test_scan_registry_enumerates_every_file(tmp_path):
    write_ws(tmp_path, "one", probe=probe_emitting(tmp_path, '{"value": 1}'))
    write_ws(tmp_path, "two", probe=None)
    write_ws(tmp_path, "three", probe=probe_emitting(tmp_path, '{"value": 2}'), status="TERMINAL")
    rows = ws.scan_registry(tmp_path, repo_root=tmp_path)
    assert {r["name"] for r in rows} == {"one", "two", "three"}
    assert sum(1 for r in rows if r["surfaces"]) == 1  # typed-not-stale and terminal do not


def test_dotfiles_are_not_workstreams(tmp_path):
    write_ws(tmp_path, "real", probe=None)
    (tmp_path / ".observations.json").write_text("{}", encoding="utf-8")
    assert {r["name"] for r in ws.scan_registry(tmp_path, repo_root=tmp_path)} == {"real"}


# ---------- block CONTENT (added after mutation testing: 34 survivors clustered here) ----------
# The first pass tested that a block was written and was idempotent, but never what it SAID.
# Every state_block mutant survived as a result -- the value line could be dropped entirely
# and 19 tests still passed.

def test_block_carries_the_value_when_there_is_one(tmp_path):
    p = write_ws(tmp_path, "v", probe=probe_emitting(tmp_path, '{"value": 77}'))
    ws.render(p, repo_root=tmp_path)
    text = p.read_text(encoding="utf-8")
    assert "value: 77" in text
    assert "state: changed" in text
    assert "error:" not in text


def test_block_carries_the_error_and_no_value(tmp_path):
    p = write_ws(tmp_path, "e", probe=probe_emitting(tmp_path, '{"value":1}', code=2))
    ws.render(p, repo_root=tmp_path)
    text = p.read_text(encoding="utf-8")
    assert "state: error" in text
    assert "error: exit 2" in text
    assert "\nvalue:" not in text


def test_block_for_a_typed_workstream_has_neither(tmp_path):
    p = write_ws(tmp_path, "t", probe=None)
    ws.render(p, repo_root=tmp_path)
    text = p.read_text(encoding="utf-8")
    assert "state: typed" in text
    assert "\nvalue:" not in text and "error:" not in text


def test_block_is_delimited_by_both_markers(tmp_path):
    p = write_ws(tmp_path, "d", probe=probe_emitting(tmp_path, '{"value": 1}'))
    ws.render(p, repo_root=tmp_path)
    text = p.read_text(encoding="utf-8")
    assert text.count(ws.BEGIN) == 1 and text.count(ws.END) == 1
    assert text.index(ws.BEGIN) < text.index(ws.END)


# ---------- malformed probe declarations degrade to typed, never to a crash ----------

@pytest.mark.parametrize("decl", ['"not a list"', "[]", '[1, 2]', "{not json"])
def test_malformed_probe_declaration_is_typed(tmp_path, decl):
    p = tmp_path / "bad.md"
    p.write_text(f"# x\n\n| **Status** | ACTIVE |\n\n<!-- PROBE: {decl} -->\n", encoding="utf-8")
    assert ws.evaluate(p, repo_root=tmp_path)["state"] == "typed"


def test_probe_binary_that_does_not_exist_is_error(tmp_path):
    p = write_ws(tmp_path, "missing", probe=["/nonexistent/binary/xyzzy"])
    r = ws.evaluate(p, repo_root=tmp_path)
    assert r["state"] == "error" and "could not start" in r["error"]


# ---------- the on-disk observation default path ----------

def test_evaluate_loads_observations_from_disk_when_not_passed(tmp_path):
    p = write_ws(tmp_path, "disk", probe=probe_emitting(tmp_path, '{"value": 5}'))
    ws.acknowledge(tmp_path / ws.OBSERVATIONS, {"disk": 5})
    assert ws.evaluate(p, repo_root=tmp_path)["state"] == "unchanged"
    ws.acknowledge(tmp_path / ws.OBSERVATIONS, {"disk": 6})
    assert ws.evaluate(p, repo_root=tmp_path)["state"] == "changed"


def test_scan_registry_loads_observations_from_disk(tmp_path):
    write_ws(tmp_path, "s", probe=probe_emitting(tmp_path, '{"value": 2}'))
    ws.acknowledge(tmp_path / ws.OBSERVATIONS, {"s": 2})
    rows = ws.scan_registry(tmp_path, repo_root=tmp_path)
    assert rows[0]["state"] == "unchanged"


def test_corrupt_observation_file_does_not_crash(tmp_path):
    p = write_ws(tmp_path, "c", probe=probe_emitting(tmp_path, '{"value": 1}'))
    (tmp_path / ws.OBSERVATIONS).write_text("{{{ not json", encoding="utf-8")
    assert ws.evaluate(p, repo_root=tmp_path)["state"] == "changed"


def test_render_returns_the_row(tmp_path):
    p = write_ws(tmp_path, "r", probe=probe_emitting(tmp_path, '{"value": 12}'))
    row = ws.render(p, repo_root=tmp_path)
    assert row["name"] == "r" and row["value"] == 12


def test_previous_value_is_reported_for_a_changed_row(tmp_path):
    p = write_ws(tmp_path, "pv", probe=probe_emitting(tmp_path, '{"value": 9}'))
    r = ws.evaluate(p, repo_root=tmp_path, observations={"pv": {"value": 4}})
    assert r["previous"] == 4 and r["value"] == 9


def test_summary_counts_each_state(tmp_path):
    write_ws(tmp_path, "a", probe=probe_emitting(tmp_path, '{"value": 1}'))
    write_ws(tmp_path, "b", probe=None)
    write_ws(tmp_path, "c", probe=probe_emitting(tmp_path, "nope"))
    write_ws(tmp_path, "d", probe=probe_emitting(tmp_path, '{"value": 1}'), status="TERMINAL")
    s = ws.summary(ws.scan_registry(tmp_path, repo_root=tmp_path))
    assert s["total"] == 4 and s["changed"] == 1 and s["error"] == 1
    assert s["typed"] == 1 and s["terminal"] == 1
    assert set(s["names"]) == {"a", "c"}


# ---------- the CLI (13 mutants survived here with no test at all) ----------

def _cli(tmp_path, *extra):
    return ws.main(["--registry", str(tmp_path), "--repo-root", str(tmp_path), *extra])


def test_cli_reports_and_exits_zero_when_no_errors(tmp_path, capsys):
    write_ws(tmp_path, "a", probe=probe_emitting(tmp_path, '{"value": 1}'))
    assert _cli(tmp_path) == 0
    out = capsys.readouterr().out
    assert "changed" in out and "a" in out and "1 of 1 surfacing" in out


def test_cli_exits_nonzero_when_a_probe_errors(tmp_path, capsys):
    """A broken probe must be visible in the exit code, not only in prose."""
    write_ws(tmp_path, "bad", probe=probe_emitting(tmp_path, "nope"))
    assert _cli(tmp_path) == 1
    assert "error" in capsys.readouterr().out


def test_cli_json_mode_emits_summary_and_rows(tmp_path, capsys):
    write_ws(tmp_path, "j", probe=probe_emitting(tmp_path, '{"value": 3}'))
    _cli(tmp_path, "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["total"] == 1
    assert payload["rows"][0]["value"] == 3


def test_cli_without_render_does_not_write_a_block(tmp_path):
    p = write_ws(tmp_path, "nw", probe=probe_emitting(tmp_path, '{"value": 1}'))
    _cli(tmp_path)
    assert ws.BEGIN not in p.read_text(encoding="utf-8")


def test_cli_render_writes_the_block(tmp_path):
    p = write_ws(tmp_path, "rw", probe=probe_emitting(tmp_path, '{"value": 1}'))
    _cli(tmp_path, "--render")
    assert ws.BEGIN in p.read_text(encoding="utf-8")


def test_scan_registry_accepts_explicit_observations(tmp_path):
    write_ws(tmp_path, "x", probe=probe_emitting(tmp_path, '{"value": 7}'))
    rows = ws.scan_registry(tmp_path, repo_root=tmp_path, observations={"x": {"value": 7}})
    assert rows[0]["state"] == "unchanged"


# ---------- the object PROBE form (added when the contract met the real tools) ----------
# The first contract demanded a `value` field and exit 0. scoring_probe.py exits 1 when it
# finds violations -- its normal state -- and check_hook_warn_tier.py prints a line, not JSON.
# `field`, `ok_exit` and `text` are policy and belong in the workstream file.

def _obj_ws(tmp_path, name, argv, **policy):
    spec = {"argv": argv, **policy}
    p = tmp_path / f"{name}.md"
    p.write_text(f"# {name}\n\n| **Status** | ACTIVE |\n\n<!-- PROBE: {json.dumps(spec)} -->\n",
                 encoding="utf-8")
    return p


def test_object_form_lifts_a_named_field(tmp_path):
    argv = probe_emitting(tmp_path, '{"violations": 6, "status": "ok"}')
    p = _obj_ws(tmp_path, "f", argv, field="violations")
    assert ws.evaluate(p, repo_root=tmp_path)["value"] == 6


def test_object_form_lifts_a_dotted_path(tmp_path):
    argv = probe_emitting(tmp_path, '{"summary": {"open": 7}}')
    p = _obj_ws(tmp_path, "d", argv, field="summary.open")
    assert ws.evaluate(p, repo_root=tmp_path)["value"] == 7


def test_missing_dotted_path_is_error_naming_the_field(tmp_path):
    argv = probe_emitting(tmp_path, '{"summary": {}}')
    p = _obj_ws(tmp_path, "m", argv, field="summary.open")
    r = ws.evaluate(p, repo_root=tmp_path)
    assert r["state"] == "error" and "summary.open" in r["error"]


def test_ok_exit_lets_a_findings_exit_code_count_as_success(tmp_path):
    """scoring_probe.py exits 1 when it finds violations. That is not a failure."""
    argv = probe_emitting(tmp_path, '{"violations": 6}', code=1)
    p = _obj_ws(tmp_path, "ok1", argv, field="violations", ok_exit=[0, 1])
    r = ws.evaluate(p, repo_root=tmp_path)
    assert r["state"] in ("changed", "unchanged") and r["value"] == 6


def test_exit_outside_ok_exit_is_still_error(tmp_path):
    argv = probe_emitting(tmp_path, '{"violations": 6}', code=2)
    p = _obj_ws(tmp_path, "ok2", argv, field="violations", ok_exit=[0, 1])
    r = ws.evaluate(p, repo_root=tmp_path)
    assert r["state"] == "error" and "ok_exit=[0, 1]" in r["error"]


def test_text_mode_takes_the_first_line(tmp_path):
    argv = probe_emitting(tmp_path, "wired 34 | checked 34\nsecond line\n")
    p = _obj_ws(tmp_path, "t", argv, text=True)
    assert ws.evaluate(p, repo_root=tmp_path)["value"] == "wired 34 | checked 34"


def test_text_mode_with_no_output_is_error(tmp_path):
    argv = probe_emitting(tmp_path, "")
    p = _obj_ws(tmp_path, "te", argv, text=True)
    assert ws.evaluate(p, repo_root=tmp_path)["state"] == "error"


def test_malformed_ok_exit_degrades_to_typed(tmp_path):
    argv = probe_emitting(tmp_path, '{"value": 1}')
    p = _obj_ws(tmp_path, "bad", argv, ok_exit="nope")
    assert ws.evaluate(p, repo_root=tmp_path)["state"] == "typed"


def test_dotted_path_through_a_non_dict_is_error(tmp_path):
    """_lift must stop when a path segment lands on a scalar, not raise TypeError."""
    argv = probe_emitting(tmp_path, '{"summary": 5}')
    p = _obj_ws(tmp_path, "scalar", argv, field="summary.open")
    r = ws.evaluate(p, repo_root=tmp_path)
    assert r["state"] == "error" and "summary.open" in r["error"]
