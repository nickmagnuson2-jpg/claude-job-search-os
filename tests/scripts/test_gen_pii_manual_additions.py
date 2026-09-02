"""Hand-maintained PII additions must reach the denylist.

Origin 2026-08-18: the generator harvested only networking.md, job-pipeline.md and
scan-targets.yaml. A real person or company appearing ONLY in data/projects/, a
source article, a reflection, or the personal vault was invisible to the
deterministic hook -- a regen produced 430 entries with zero of three known real
surnames. Routing such people through networking.md to gain coverage would pollute
the job-search roster with non-contacts, so the fix is a merged manual file.

Second fire of this shape; the first was scan-targets.yaml (see
test_gen_pii_denylist_targets.py), which is why this is a file the generator reads
rather than another special-cased parser.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
MANUAL_REL = Path("tools") / ".pii-manual-additions.txt"


def _run(tmp_path):
    cmd = [sys.executable, str(TOOLS / "gen_pii_denylist.py"),
           "--repo-root", str(tmp_path), "--dry-run"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _setup(tmp_path, manual=None):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "networking.md").write_text(
        "# Networking\n\n## Contacts\n\n"
        "| Name | Company | Role | Relationship | Added | Last Interaction | Email |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n", encoding="utf-8")
    (d / "job-pipeline.md").write_text(
        "# Job Pipeline\n\n## Active Pipeline\n\n"
        "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n", encoding="utf-8")
    if manual is not None:
        mp = tmp_path / MANUAL_REL
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(manual, encoding="utf-8")
    return tmp_path


def test_manual_block_entry_reaches_denylist(tmp_path):
    res = _run(_setup(tmp_path, "[block]\nThessaly Quorne\n"))
    assert "Thessaly Quorne" in res["tokens"]
    assert "Thessaly Quorne" in res["manual_added"]


def test_block_is_the_default_section(tmp_path):
    """A bare list with no section header is BLOCK tier -- the safe default."""
    res = _run(_setup(tmp_path, "Thessaly Quorne\nVantablack Orrery\n"))
    assert "Thessaly Quorne" in res["tokens"]
    assert "Vantablack Orrery" in res["tokens"]


def test_ambiguous_section_routes_to_warn_tier_not_block(tmp_path):
    res = _run(_setup(tmp_path, "[ambiguous]\nLantern\n"))
    assert "Lantern" in res["ambiguous"]
    assert "Lantern" not in res["tokens"]


def test_both_sections_in_one_file(tmp_path):
    res = _run(_setup(tmp_path, "[block]\nThessaly Quorne\n\n[ambiguous]\nLantern\n"))
    assert "Thessaly Quorne" in res["tokens"]
    assert "Lantern" in res["ambiguous"]


def test_comments_and_blank_lines_ignored(tmp_path):
    res = _run(_setup(tmp_path, "# a comment\n\n[block]\n\nThessaly Quorne\n\n# trailing\n"))
    assert "Thessaly Quorne" in res["tokens"]
    assert not any(t.startswith("#") for t in res["tokens"])


def test_single_ordinary_word_survives_distinctiveness_filter(tmp_path):
    """A manual entry is a deliberate human decision, so it must NOT be second-guessed
    by the dictionary filter that (correctly) drops auto-parsed ordinary words."""
    res = _run(_setup(tmp_path, "[block]\nOrchard\n"))
    assert "Orchard" in res["tokens"]


def test_block_entry_wins_over_ambiguous_duplicate(tmp_path):
    """Same token in both sections must resolve to the STRICTER tier, never WARN-only."""
    res = _run(_setup(tmp_path, "[block]\nLantern\n[ambiguous]\nLantern\n"))
    assert "Lantern" in res["tokens"]
    assert "Lantern" not in res["ambiguous"]


def test_missing_manual_file_is_not_an_error(tmp_path):
    """Graceful degradation, and the template is created so the affordance is findable."""
    res = _run(_setup(tmp_path, manual=None))
    assert "count" in res
    assert res["manual_added"] == []
    assert (tmp_path / MANUAL_REL).exists(), "template should be created on first run"


def test_created_template_is_inert(tmp_path):
    """The auto-created template must contribute zero tokens -- its section headers
    and comments must not be parsed as names."""
    _run(_setup(tmp_path, manual=None))          # first run writes the template
    res = _run(tmp_path)                          # second run reads it back
    assert res["manual_added"] == []


def test_manual_entries_are_not_overwritten_by_regen(tmp_path):
    """Unlike the generated lists, this file is an input and must survive a regen."""
    _setup(tmp_path, "[block]\nThessaly Quorne\n")
    _run(tmp_path)
    _run(tmp_path)
    assert "Thessaly Quorne" in (tmp_path / MANUAL_REL).read_text(encoding="utf-8")


def test_template_ends_with_block_so_naive_append_is_strict(tmp_path):
    """REGRESSION (2026-08-18, caught in live smoke, not by unit tests): the first
    template ended with [ambiguous], so appending a name to the end of the file --
    the obvious action -- silently landed it in the WARN-only tier. A PreToolUse WARN
    is never surfaced by Claude Code, so the entry would have looked added while
    protecting nothing.

    The LAST section header in the template must be [block].
    """
    _setup(tmp_path, manual=None)
    _run(tmp_path)                                   # writes the template
    text = (tmp_path / MANUAL_REL).read_text(encoding="utf-8")
    headers = [ln.strip().lower() for ln in text.splitlines()
               if ln.strip().lower() in ("[block]", "[ambiguous]")]
    assert headers, "template must declare its sections"
    assert headers[-1] == "[block]", f"last section is {headers[-1]}; appends would be WARN-only"


def test_naive_append_to_template_lands_in_block_tier(tmp_path):
    """Behavioural form of the test above: append to the end, regen, assert BLOCK."""
    _setup(tmp_path, manual=None)
    _run(tmp_path)
    mp = tmp_path / MANUAL_REL
    mp.write_text(mp.read_text(encoding="utf-8") + "Marisol Vantrell\n", encoding="utf-8")
    res = _run(tmp_path)
    assert "Marisol Vantrell" in res["tokens"]
    assert "Marisol Vantrell" not in res["ambiguous"]
