import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "tools" / "check_living_log_purity.py"


def run(payload):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
    )


def test_blocks_write_to_garden_log():
    r = run({"tool_name": "Write",
             "tool_input": {"file_path": "/Users/x/personal/data/garden-log.md",
                            "content": "## 2026-05-18\n\n**X.** y"}})
    assert r.returncode == 2
    assert "living_log_append.py" in r.stderr


def test_allows_unrelated_file():
    r = run({"tool_name": "Write",
             "tool_input": {"file_path": "/Users/x/personal/data/inbox.md",
                            "content": "note"}})
    assert r.returncode == 0


def test_blocks_edit_to_coffee_log():
    r = run({"tool_name": "Edit",
             "tool_input": {"file_path": "/any/dir/coffee-log.md",
                            "old_string": "a", "new_string": "b"}})
    assert r.returncode == 2


def test_ignores_non_write_tools():
    r = run({"tool_name": "Read",
             "tool_input": {"file_path": "/x/garden-log.md"}})
    assert r.returncode == 0


def test_fail_open_on_garbage_stdin():
    r = subprocess.run([sys.executable, str(HOOK)],
                        input="not json", capture_output=True, text=True)
    assert r.returncode == 0


@pytest.mark.parametrize("basename", [
    "garden-log.md", "practice-log.md", "coffee-log.md", "farmers-market-log.md",
])
def test_blocks_write_to_each_living_log(basename):
    r = run({"tool_name": "Write",
             "tool_input": {"file_path": f"/Users/x/personal/data/{basename}",
                            "content": "x"}})
    assert r.returncode == 2


def test_blocks_case_variant_basename():
    r = run({"tool_name": "Write",
             "tool_input": {"file_path": "/Users/x/personal/data/Garden-Log.md",
                            "content": "x"}})
    assert r.returncode == 2


def test_blocks_multiedit_to_practice_log():
    r = run({"tool_name": "MultiEdit",
             "tool_input": {"file_path": "/x/practice-log.md",
                            "edits": [{"old_string": "a", "new_string": "b"}]}})
    assert r.returncode == 2


def test_missing_file_path_allows():
    r = run({"tool_name": "Write", "tool_input": {}})
    assert r.returncode == 0


def test_null_tool_input_allows():
    r = run({"tool_name": "Write", "tool_input": None})
    assert r.returncode == 0
