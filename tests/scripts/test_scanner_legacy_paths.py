"""Kill the mutation survivors in scanner.py paths no other test observed (title-gate-1118).

mutation_check ran whole-file on scanner.py (the branch edits existing tests, which widens
the scope) and 15 mutants survived in code the branch did not change: the missing-config
branch of load_targets, and the legacy inbox renderer/writer that write_inbox keeps for
callers outside the nightly path.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.career_scanner import scanner  # noqa: E402


def test_load_targets_without_a_config_returns_empty_and_says_so(tmp_path, capsys):
    assert scanner.load_targets(tmp_path) == []
    assert "No scan-targets.yaml found" in capsys.readouterr().err


ROLES = [
    {"score": 8, "title": "Deployment Strategist", "company": "Placeholder Co",
     "location": "San Francisco, CA", "url": "https://p.example/1"},
    {"score": 5, "title": "Chief of Staff", "company": "Open Board Co"},
]


def test_format_inbox_entry_renders_every_field_and_skips_empty_ones():
    text = scanner.format_inbox_entry(ROLES)
    lines = text.split("\n")
    assert lines[0] == "## Career Scan Results"
    assert lines[3] == "**New roles found:** 2"
    assert ("- **[8/10]** Deployment Strategist at Placeholder Co (San Francisco, CA)"
            " - [View](https://p.example/1)") in lines
    assert "- **[5/10]** Chief of Staff at Open Board Co" in lines
    assert lines[-3:-1] == ["", lines[-2]] and lines[-2].startswith("*Source: /scan-companies | ")
    assert lines[-1] == ""
    assert len(lines) == 5 + len(ROLES) + 3


def test_write_inbox_prepends_the_rendered_entry(tmp_path, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(scanner.inbox_lock, "prepend_entries",
                        lambda path, entry: seen.update(path=path, entry=entry))
    scanner.write_inbox(tmp_path, ROLES)
    assert seen["path"] == tmp_path / "data" / "inbox.md"
    assert "Deployment Strategist at Placeholder Co" in seen["entry"]
    assert "Wrote 2 roles to" in capsys.readouterr().err


def test_write_inbox_reports_a_lock_failure_and_does_not_claim_success(tmp_path, monkeypatch, capsys):
    def boom(path, entry):
        raise scanner.inbox_lock.LockTimeout("held")
    monkeypatch.setattr(scanner.inbox_lock, "prepend_entries", boom)
    scanner.write_inbox(tmp_path, ROLES)
    err = capsys.readouterr().err
    assert "ERROR: inbox not updated" in err and "2 roles NOT written" in err
    assert "Wrote" not in err
