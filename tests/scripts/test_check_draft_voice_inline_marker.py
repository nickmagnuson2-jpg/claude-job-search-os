"""Tests for the inline-marker-write diagnostic in check_draft_voice.py.

Regression for the 2026-07-08 friction-log finding: `open_draft.py` PreToolUse
hook error sat at 13 occurrences, all the same race — a single Bash command
writes tools/.pending-draft.source (heredoc/printf) AND invokes open_draft.py
in the same call. PreToolUse fires before the command runs, so the write is
invisible to check_provenance(); the block message now says so explicitly
instead of leaving the failure mode to be re-discovered by trial and error.
This does NOT change the security model — provenance is still file-based and
unchanged; only the diagnostic text differs.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_draft_voice.py"
_spec = importlib.util.spec_from_file_location("check_draft_voice", SCRIPT)
cdv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdv)


@pytest.mark.parametrize("command", [
    'printf "draft-email\\n2026-07-08T00:00:00\\n" > tools/.pending-draft.source '
    '&& python3 tools/open_draft.py',
    "cat > tools/.pending-draft.source <<'EOF'\ndraft-email\nEOF\npython3 tools/open_draft.py",
])
def test_detects_inline_marker_write_attempt(command):
    assert cdv.command_attempts_inline_marker_write(command) is True


@pytest.mark.parametrize("command", [
    "python3 tools/open_draft.py",
    "git status",
    None,
    "",
])
def test_no_false_positive_on_clean_commands(command):
    assert cdv.command_attempts_inline_marker_write(command) is False


@pytest.mark.parametrize("command", [
    # Regression for code-review finding (2026-07-08): a plain READ of the
    # marker (no >/>>/tee write indicator) must not be flagged as an attempt
    # to write it -- the diagnostic hint would misdirect the actual fix.
    "cat tools/.pending-draft.source && python3 tools/open_draft.py",
    "grep draft-email tools/.pending-draft.source; python3 tools/open_draft.py",
    "ls -la tools/.pending-draft.source",
])
def test_read_only_mention_is_not_a_write_attempt(command):
    assert cdv.command_attempts_inline_marker_write(command) is False


def test_provenance_message_includes_inline_hint_when_marker_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(cdv, "SOURCE_FILE", tmp_path / ".pending-draft.source")
    command = ('printf "draft-email\\n2026-07-08T00:00:00\\n" > tools/.pending-draft.source '
               '&& python3 tools/open_draft.py')
    violations = cdv.check_provenance(command)
    assert violations
    assert "PreToolUse fires BEFORE the command runs" in violations[0]


def test_provenance_message_omits_hint_for_unrelated_missing_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(cdv, "SOURCE_FILE", tmp_path / ".pending-draft.source")
    violations = cdv.check_provenance("python3 tools/open_draft.py")
    assert violations
    assert "PreToolUse fires BEFORE" not in violations[0]
