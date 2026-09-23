"""B13 (closeout 2026-09-23): the cross-model ledger is in the nightly backup.

The ledger is gitignored and holds every finding and disposition the push gate reads.
Until this test existed it lived on one disk and the backup script never named it.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "backup-data.sh"


def _staged_paths():
    text = SCRIPT.read_text(encoding="utf-8")
    block = text.split("$GIT add --force", 1)[1].split("2>/dev/null", 1)[0]
    return re.findall(r'"\$WORK_TREE/([^"]+)"', block)


def test_the_backup_stages_the_cross_model_ledger():
    assert "tools/.cross-model-ledger.jsonl" in _staged_paths()


def test_the_staged_block_parses_to_the_known_trees():
    """Guard on the parser: an empty or misparsed block would make the test above
    pass or fail for the wrong reason."""
    paths = _staged_paths()
    assert "data/" in paths and "output/" in paths and len(paths) > 10


def test_the_ledger_file_the_backup_names_is_the_one_the_gate_reads():
    import sys
    sys.path.insert(0, str(REPO / "tools"))
    import cross_model_gate as g
    assert str(g.ledger_path(REPO).relative_to(REPO)) == "tools/.cross-model-ledger.jsonl"
