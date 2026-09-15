"""data/inbox.md as a denylist source.

Origin, 2026-09-14: two real companies reached public test fixtures and neither was on
ANY tier of the denylist -- not block, not ambiguous, not retired. Both had been surfaced
by the discovery drip and existed only in `data/inbox.md`, which was not a harvest source,
so the always-on PreToolUse hook was not failing to catch them. It was never looking.
They were caught by the `/audit-pii` semantic pass at staging, by hand, one step before a
public push.

The inbox is upstream of every other source by construction: the drip writes a company
there first, and it reaches `job-pipeline.md` or `scan-targets.yaml` only if Nick promotes
it. Harvesting only those covered companies from the moment Nick got interested and left
every researched-but-unpromoted one uncovered -- which is exactly the population that ends
up in a test fixture as a "realistic example".

All company names here are synthetic. `tests/` is tracked and therefore public.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from gen_pii_denylist import parse_inbox_companies  # noqa: E402

COMPANY_BLOCK = """## 2026-09-14 | Agent drip: lane-b (company)
<!-- review-gated: accept via /act or /networking -->
- **Zephyrine** - AI-native platform for small operators, including scheduling (score 6)
- **Quorvex AI** - Voice agents for field service businesses (score 4)

## 2026-09-14 | Agent drip: deployment-leads (person)
- **Jordan Lee** - Forward Deployed Engineer @ Someco - San Francisco
"""


def test_company_block_entries_are_harvested():
    got = parse_inbox_companies(COMPANY_BLOCK)
    assert "Zephyrine" in got
    assert "Quorvex AI" in got


def test_person_block_entries_are_NOT_harvested():
    """Deliberate, not an oversight.

    Drip person-entries are unreviewed scrapes that have carried wrong data before: a
    2026-08-17 drip stamped one company's HQ onto people who were eight time zones away.
    Routing unreviewed names into a BLOCK list turns a scraping error into a false
    positive on every future write. Person coverage stays with the semantic pass.
    """
    got = parse_inbox_companies(COMPANY_BLOCK)
    assert "Jordan Lee" not in got


def test_bullets_outside_any_company_block_are_ignored():
    """A note bullet is prose, not a company. Without the header gate the parser would
    harvest every bolded phrase in the inbox and flood the BLOCK list with English."""
    content = """## 2026-09-01 | A note about something
- **Read this article** - it was interesting
"""
    assert parse_inbox_companies(content) == set()


def test_a_company_block_ends_at_the_next_header():
    """Kills a parser that latches the in-block flag and never clears it."""
    content = COMPANY_BLOCK + """
## 2026-09-13 | Another plain note
- **Not A Company** - just a bullet in a note
"""
    got = parse_inbox_companies(content)
    assert "Zephyrine" in got
    assert "Not A Company" not in got, "the block flag was not cleared at the next header"


def test_empty_and_missing_content_are_not_errors():
    """`read_file` returns '' for a missing inbox; regeneration must degrade, not fail."""
    assert parse_inbox_companies("") == set()


def test_the_real_inbox_yields_companies_and_is_wired_into_the_generator():
    """Verified against the LIVE file, per the repo's data-tool rule.

    A green fixture suite here would prove the regex works and say nothing about whether
    the real inbox is shaped the way the regex expects -- which is the failure this whole
    change exists to fix. No count is pinned: the drip grows every week.
    """
    inbox = REPO_ROOT / "data" / "inbox.md"
    if not inbox.is_file():
        import pytest
        pytest.skip("data/inbox.md is gitignored and absent in this checkout")
    got = parse_inbox_companies(inbox.read_text(encoding="utf-8"))
    assert len(got) > 20, f"real inbox yielded only {len(got)} companies; parser may be stale"


def test_the_generator_actually_consumes_the_inbox(tmp_path):
    """End-to-end, in an ISOLATED repo root. This is the test that pins the wiring.

    An earlier version of this test asserted against the LIVE `.pii-denylist.txt` and was
    VACUOUS: deleting the wiring line from the generator left it green. The reason is
    `merge_retired()` -- once a token has been harvested it accumulates in
    `.pii-denylist-retired.txt` and is folded back into every subsequent rebuild, by
    design, so coverage outlives its source. The live list therefore reports "covered"
    whether or not the generator still reads the inbox, which is a second reason for the
    expected result and makes the assertion blind to the thing it claims to check.
    Caught by deleting the wiring line and re-running, 2026-09-14.

    A fresh root has no retired list, so the only path from inbox to denylist is the
    wiring. Delete that line and this test dies.
    """
    import subprocess
    (tmp_path / "data").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "data" / "inbox.md").write_text(COMPANY_BLOCK, encoding="utf-8")
    for stub in ("networking.md", "job-pipeline.md"):
        (tmp_path / "data" / stub).write_text("", encoding="utf-8")
    (tmp_path / "data" / "scan-targets.yaml").write_text("companies: []\n", encoding="utf-8")

    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "gen_pii_denylist.py"),
         "--repo-root", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
        env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin:/usr/local/bin"})
    assert r.returncode == 0, r.stderr[:400]

    produced = (tmp_path / "tools" / ".pii-denylist.txt")
    assert produced.is_file(), "generator wrote no denylist"
    tokens = {t.strip().lower() for t in produced.read_text(encoding="utf-8").splitlines() if t.strip()}
    assert "zephyrine" in tokens, (
        "a company present ONLY in data/inbox.md did not reach the denylist -- the "
        "generator is not consuming the inbox, which is the exact gap this change closes")
