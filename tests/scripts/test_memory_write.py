"""tests for tools/memory_write.py: the CLI that routes frontmatter state edits through
memory_frontmatter.write() and record_fire().

Every case runs the real entry point in a subprocess against a temporary memory folder,
so the shipped surface (argument parsing, the CLAUDECODE tripwire, exit codes, JSON) is
what is tested. Fixture rules are invented.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "memory_write.py"

RULE = """---
name: feedback_example_rule
description: "An invented rule used as a fixture."
metadata:
  node_type: memory
  type: feedback
  occurrences: 2
  promoted: no
  reopen_gate: "REOPEN on the 3rd fire"
  last_cited: 2026-11-01
---

**Rule:** an invented rule.
"""


def run(args, memory_dir: Path, *, claudecode: str | None = "1"):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    if claudecode is not None:
        env["CLAUDECODE"] = claudecode
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, str(SCRIPT), "--memory-dir", str(memory_dir), *args],
                       capture_output=True, text=True, env=env, timeout=60)
    try:
        out = json.loads(p.stdout) if p.stdout.strip() else None
    except json.JSONDecodeError:
        out = None
    return p.returncode, out, p


@pytest.fixture
def mem(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    (d / "feedback_example_rule.md").write_text(RULE, encoding="utf-8")
    return d


def text(mem):
    return (mem / "feedback_example_rule.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------ tripwire

@pytest.mark.parametrize("value", [None, "0", ""])
@pytest.mark.parametrize("cmd", [["set", "feedback_example_rule.md", "promoted=hook, 2026-11-22"],
                                 ["bump", "feedback_example_rule.md", "--fire", "2026-11-22: seen"]])
def test_refuses_without_claudecode(mem, cmd, value):
    code, out, _ = run(cmd, mem, claudecode=value)
    assert code == 1
    assert out["action"] == "refused"
    assert "CLAUDECODE=1 is not set" in out["reasons"][0]
    assert "launchd" in out["reasons"][0]
    assert text(mem) == RULE


# ------------------------------------------------------------------ set

def test_set_refuses_occurrences_and_points_to_bump(mem):
    code, out, _ = run(["set", "feedback_example_rule.md", "occurrences=3"], mem)
    assert code == 1
    assert out["action"] == "refused"
    assert out["reasons"] == ["occurrences: never machine-written; record a witnessed fire "
                              "with `memory_write.py bump <file> --fire ...`"]
    assert text(mem) == RULE


def test_set_refuses_occurrences_even_beside_a_valid_key(mem):
    code, out, _ = run(["set", "feedback_example_rule.md", "promoted=hook, 2026-11-22",
                        "occurrences=9"], mem)
    assert code == 1
    assert text(mem) == RULE


def test_set_valid_promoted_writes_through_validator(mem):
    code, out, p = run(["set", "feedback_example_rule.md", "promoted=hook, 2026-11-22"], mem)
    assert code == 0, p.stdout + p.stderr
    assert out["action"] == "set"
    assert out["set"] == {"promoted": "hook, 2026-11-22"}
    new = text(mem)
    assert "promoted: hook, 2026-11-22" in new or "promoted: 'hook, 2026-11-22'" in new
    assert "occurrences: 2" in new
    assert new.endswith("**Rule:** an invented rule.\n")


def test_set_unknown_token_is_refused_and_file_untouched(mem):
    code, out, _ = run(["set", "feedback_example_rule.md", "promoted=maybe later"], mem)
    assert code == 1
    assert out["action"] == "refused"
    assert any("promoted" in r for r in out["reasons"])
    assert text(mem) == RULE


def test_set_pointer_without_target_is_refused(mem):
    code, out, _ = run(["set", "feedback_example_rule.md", "promoted=merged"], mem)
    assert code == 1
    assert text(mem) == RULE


def test_set_merged_into_existing_file_is_accepted(mem):
    (mem / "feedback_other_rule.md").write_text(RULE.replace("example_rule", "other_rule"),
                                                encoding="utf-8")
    code, out, p = run(["set", "feedback_example_rule.md",
                        "promoted=merged into feedback_other_rule.md"], mem)
    assert code == 0, p.stdout + p.stderr


@pytest.mark.parametrize("pairs,needle", [
    (["promoted"], "expected key=value"),
    (["=x"], "expected key=value"),
    (["promoted=hook, 2026-11-22", "promoted=no"], "given twice"),
])
def test_set_usage_errors_exit_2(mem, pairs, needle):
    code, out, _ = run(["set", "feedback_example_rule.md", *pairs], mem)
    assert code == 2
    assert out["action"] == "usage_error"
    assert any(needle in r for r in out["reasons"])
    assert text(mem) == RULE


# ------------------------------------------------------------------ bump

def test_bump_increments_and_appends_dated_evidence(mem):
    code, out, p = run(["bump", "feedback_example_rule.md", "--fire",
                        "2026-11-22: invented fire for the test"], mem)
    assert code == 0, p.stdout + p.stderr
    assert out == {"action": "bumped", "file": str(mem / "feedback_example_rule.md"),
                   "occurrences": 3}
    new = text(mem)
    assert "occurrences: 3" in new
    assert "occurrences: 2" not in new
    assert new.rstrip("\n").splitlines()[-1].startswith("- Fire 3 recorded ")
    assert new.rstrip("\n").endswith("2026-11-22: invented fire for the test")


def test_bump_without_a_date_is_refused(mem):
    code, out, _ = run(["bump", "feedback_example_rule.md", "--fire", "it happened again"], mem)
    assert code == 1
    assert out["action"] == "refused"
    assert text(mem) == RULE


def test_bump_requires_fire(mem):
    code, _, p = run(["bump", "feedback_example_rule.md"], mem)
    assert code == 2
    assert "--fire" in p.stderr
    assert text(mem) == RULE


# ------------------------------------------------------------------ file resolution

def test_missing_file_is_refused(mem):
    code, out, _ = run(["set", "feedback_absent.md", "promoted=hook, 2026-11-22"], mem)
    assert code == 1
    assert out["reasons"] == [f"no such file: {mem / 'feedback_absent.md'}"]


def test_a_path_is_used_as_given(mem, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "feedback_example_rule.md").write_text(RULE, encoding="utf-8")
    code, out, p = run(["set", str(other / "feedback_example_rule.md"),
                        "promoted=hook, 2026-11-22"], mem)
    assert code == 0, p.stdout + p.stderr
    assert text(mem) == RULE
    assert "hook, 2026-11-22" in (other / "feedback_example_rule.md").read_text(encoding="utf-8")


def test_default_memory_dir_is_the_main_checkouts_live_tier():
    sys.path.insert(0, str(REPO / "tools"))
    import memory_write
    from hook_runtime import claude_project_dir
    from vault_paths import main_checkout
    assert memory_write.default_memory_dir() == claude_project_dir(main_checkout(REPO)) / "memory"
    assert memory_write.default_memory_dir().name == "memory"


def test_a_home_relative_path_is_expanded(mem, tmp_path):
    home = tmp_path / "home"
    (home / "notes").mkdir(parents=True)
    (home / "notes" / "feedback_example_rule.md").write_text(RULE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env.update({"CLAUDECODE": "1", "HOME": str(home), "PYTHONIOENCODING": "utf-8"})
    p = subprocess.run([sys.executable, str(SCRIPT), "--memory-dir", str(mem), "set",
                        "~/notes/feedback_example_rule.md", "promoted=hook, 2026-11-22"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "hook, 2026-11-22" in (home / "notes" / "feedback_example_rule.md").read_text(encoding="utf-8")
    assert text(mem) == RULE


@pytest.mark.parametrize("pairs,reasons", [
    (["promoted"], ["'promoted': expected key=value"]),
    (["promoted=hook, 2026-11-22", "promoted=no"], ["promoted: given twice"]),
])
def test_a_bad_pair_is_never_written_even_beside_a_good_one(mem, pairs, reasons):
    code, out, _ = run(["set", "feedback_example_rule.md", *pairs], mem)
    assert code == 2
    assert out["reasons"] == reasons
    assert text(mem) == RULE
