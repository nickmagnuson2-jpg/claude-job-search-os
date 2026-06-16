import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "tools" / "check_replace_all_safety.py"


def run(payload, env=None):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
    )


def write_file(tmp_path, text):
    f = tmp_path / "doc.md"
    f.write_text(text, encoding="utf-8")
    return str(f)


# --- BLOCK cases: short old_string embedded in a longer word ----------------

def test_blocks_superstring_name_correction(tmp_path):
    """The origin shape: a spelling fix that is a substring of an existing word."""
    fp = write_file(tmp_path, "Anne Smith and Ann Jones attended.\n")
    r = run({"tool_name": "Edit",
             "tool_input": {"file_path": fp,
                            "old_string": "Ann", "new_string": "Anne",
                            "replace_all": True}})
    assert r.returncode == 2
    assert "Anne" in r.stderr  # surfaces the longer word it would corrupt


def test_blocks_short_token_inside_longer_words(tmp_path):
    fp = write_file(tmp_path, "We compare and compete on comp.\n")
    r = run({"tool_name": "Edit",
             "tool_input": {"file_path": fp,
                            "old_string": "comp", "new_string": "company",
                            "replace_all": True}})
    assert r.returncode == 2


def test_blocks_multiedit_with_one_unsafe_replace_all(tmp_path):
    fp = write_file(tmp_path, "colorful and dysfunction\n")
    r = run({"tool_name": "MultiEdit",
             "tool_input": {"file_path": fp,
                            "edits": [
                                {"old_string": "and", "new_string": "AND"},
                                {"old_string": "color", "new_string": "colour",
                                 "replace_all": True},
                            ]}})
    assert r.returncode == 2


# --- ALLOW cases ------------------------------------------------------------

def test_allows_standalone_only_occurrences(tmp_path):
    fp = write_file(tmp_path, "the cat sat on the mat\n")
    r = run({"tool_name": "Edit",
             "tool_input": {"file_path": fp,
                            "old_string": "cat", "new_string": "dog",
                            "replace_all": True}})
    assert r.returncode == 0


def test_allows_punctuation_boundary_not_word_boundary(tmp_path):
    """color in background-color is hyphen-bounded, NOT embedded in a word."""
    fp = write_file(tmp_path, "background-color: red; color: blue;\n")
    r = run({"tool_name": "Edit",
             "tool_input": {"file_path": fp,
                            "old_string": "color", "new_string": "colour",
                            "replace_all": True}})
    assert r.returncode == 0


def test_allows_when_replace_all_false(tmp_path):
    fp = write_file(tmp_path, "Anne and Ann\n")
    r = run({"tool_name": "Edit",
             "tool_input": {"file_path": fp,
                            "old_string": "Ann", "new_string": "Anne",
                            "replace_all": False}})
    assert r.returncode == 0


def test_allows_multiword_old_string(tmp_path):
    fp = write_file(tmp_path, "the quick brown foxiness\n")
    r = run({"tool_name": "Edit",
             "tool_input": {"file_path": fp,
                            "old_string": "quick brown", "new_string": "slow",
                            "replace_all": True}})
    assert r.returncode == 0


def test_allows_long_unique_old_string(tmp_path):
    fp = write_file(tmp_path, "supercalifragilistic expialidocious\n")
    r = run({"tool_name": "Edit",
             "tool_input": {"file_path": fp,
                            "old_string": "supercalifragilistic",
                            "new_string": "x", "replace_all": True}})
    assert r.returncode == 0


def test_allows_missing_file(tmp_path):
    r = run({"tool_name": "Edit",
             "tool_input": {"file_path": str(tmp_path / "nope.md"),
                            "old_string": "Ann", "new_string": "Anne",
                            "replace_all": True}})
    assert r.returncode == 0


def test_allows_non_edit_tool(tmp_path):
    fp = write_file(tmp_path, "Anne Ann\n")
    r = run({"tool_name": "Read", "tool_input": {"file_path": fp}})
    assert r.returncode == 0


def test_allows_empty_old_string(tmp_path):
    fp = write_file(tmp_path, "Anne\n")
    r = run({"tool_name": "Edit",
             "tool_input": {"file_path": fp,
                            "old_string": "", "new_string": "x",
                            "replace_all": True}})
    assert r.returncode == 0


def test_fail_open_on_garbage_stdin():
    r = subprocess.run([sys.executable, str(HOOK)],
                       input="not json", capture_output=True, text=True)
    assert r.returncode == 0


def test_null_tool_input_allows():
    r = run({"tool_name": "Edit", "tool_input": None})
    assert r.returncode == 0


def test_override_env_bypasses(tmp_path):
    import os
    fp = write_file(tmp_path, "Anne and Ann\n")
    env = dict(os.environ, REPLACE_ALL_OVERRIDE="1")
    r = run({"tool_name": "Edit",
             "tool_input": {"file_path": fp,
                            "old_string": "Ann", "new_string": "Anne",
                            "replace_all": True}}, env=env)
    assert r.returncode == 0
