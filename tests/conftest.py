"""Session-wide guards for the test suite.

Currently one: refuse to run while a mutation run owns a source file.
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Single source of truth for the refusal code and for what counts as a stranded backup,
# shared with tools/mutation_check.py, which matches on the exit code. See that module.
sys.path.insert(0, str(REPO_ROOT / "tools"))
from conftest_guard import (  # noqa: E402
    CONFTEST_REFUSAL, orphan_backups, source_of, stranded_backups,
)

# Tests that check a tool against the owner's REAL data (the "verified on real data, not
# fixtures" rule) cannot pass in a linked git worktree, which never has the gitignored
# files. Before 2026-10-28 they failed there, so every mutation run in a worktree stopped
# at baseline_red (plan A1, output/mutation-parallel-102826/plan.md). Marked
# `@pytest.mark.live_data`, they now SKIP in a linked worktree with a reason that starts
# LIVE_DATA_SKIP, and still RUN in the main checkout, where missing data is a real failure.
LIVE_DATA_SKIP = "LIVE-DATA-ABSENT"


def _is_linked_worktree(root: Path) -> bool:
    """A linked worktree has a `.git` FILE pointing at the main repo; the main checkout has
    a `.git` directory."""
    return (root / ".git").is_file()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live_data: reads the owner's gitignored data; skipped in a linked worktree")


def pytest_runtest_setup(item):
    if item.get_closest_marker("live_data") and _is_linked_worktree(REPO_ROOT):
        pytest.skip(f"{LIVE_DATA_SKIP}: needs the owner's gitignored data, which a linked "
                    "worktree does not have")


# Set by tools/mutation_check.py in the env of the pytest subprocesses it spawns.
# Those runs are SUPPOSED to see a mutated tree -- that is the whole mechanism.
MUTATION_ENV = "MUTATION_CHECK_ACTIVE"


@pytest.fixture(scope="session", autouse=True)
def _draft_captures_go_to_a_throwaway_store(tmp_path_factory):
    """No test, and no tool a test starts, writes into the real output/draft-captures/.

    tools/draft_capture.py records drafts on the way out, and tools that every CV or email
    passes through call it. A test that runs one of those tools as a subprocess therefore
    wrote a "first draft" record into the real store: on 2026-10-06 one existing CV test,
    run once per mutant by a mutation check, left about 150 of them there within two
    minutes of the capture step being added. Set here, for the whole session and inherited
    by every child process, so a new test cannot forget it.
    """
    previous = os.environ.get("DRAFT_CAPTURE_DIR")
    os.environ["DRAFT_CAPTURE_DIR"] = str(tmp_path_factory.mktemp("draft-captures"))
    yield
    if previous is None:
        os.environ.pop("DRAFT_CAPTURE_DIR", None)
    else:
        os.environ["DRAFT_CAPTURE_DIR"] = previous


# The owner's real tables. `data/` is gitignored and holds live state; in a scratch
# worktree whose data/ is a symlink to the real folder, a test there writes the real file.
REAL_TABLES = ("job-todos.md", "job-pipeline.md", "outreach-log.md", "networking.md")


def _fingerprints() -> dict:
    out = {}
    for name in REAL_TABLES:
        try:
            st = (REPO_ROOT / "data" / name).stat()          # follows a symlinked data/
        except OSError:
            continue
        out[name] = (st.st_mtime_ns, st.st_size)
    return out


def _changed_tables(before: dict, after: dict) -> list:
    """Tables whose fingerprint differs, including ones created or deleted.

    Over the union of both key sets: comparing over the after-keys only let a test that
    deleted a real table pass (cross-model finding 647.F1, 2026-10-26).
    """
    return sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))


@pytest.fixture(autouse=True)
def _no_test_writes_the_real_tables(request):
    """Fail the test that changes one of the owner's real data tables.

    WHY (2026-10-25). Two tests ran tools/scan_promotion_candidates.py in cron mode with
    `--repo-root .`, so every suite and mutation run added a "Memory refresh" row to the
    real data/job-todos.md: 358 such rows in the 2026-10-22 backup, 2,009 three days
    later. The 2026-10-06 draft-capture leak above was the same class, fixed for one
    folder. This covers the class: a test that writes a real table fails, by name.

    Mutation runs are checked too: most of those 2,009 rows came from them, and a mutant
    that makes a tool write the real file should fail.
    """
    before = _fingerprints()
    yield
    changed = _changed_tables(before, _fingerprints())
    if changed:
        pytest.fail(f"{request.node.nodeid} changed the real data/{', data/'.join(changed)}. "
                    "Point the tool at tmp_path (--repo-root, or the tool's path override) "
                    "instead of the repo. If another process wrote the file at the same "
                    "moment, rerun this test alone to confirm.", pytrace=False)


def _show(backup: Path) -> str:
    """Render a backup as the SOURCE file it protects, repo-relative where possible.

    Backups live outside the working tree since 2026-09-01, so `relative_to(REPO_ROOT)` on
    the backup path itself raises ValueError -- and the backup's own filename is a
    percent-encoded absolute path, which is not what a reader needs. The source is.
    """
    src = source_of(backup)
    try:
        return f"{src.relative_to(REPO_ROOT)}  (backup: {backup})"
    except ValueError:
        return f"{src}  (backup: {backup})"


def _stranded_backups() -> list[Path]:
    """Backup files left by tools/mutation_check.py that mean the tree is really mutated.

    The tool copies <target> to <target>.mutation_backup for the duration of a run and
    rewrites the target in place. Such a backup means one of two things, and both
    invalidate a test result: a run is in progress right now, or a previous run crashed
    and left the tree mutated.

    A backup whose implied source does NOT exist is an orphan and is deliberately excluded
    -- it cannot be an active mutation, and treating one as if it were cost a 108-tool
    sweep its entire isolation signal on 2026-08-31. See tools/conftest_guard.py.
    """
    return stranded_backups(REPO_ROOT)


@pytest.fixture(scope="session", autouse=True)
def _refuse_to_run_under_an_active_mutation(request):
    """Fail loudly when the source tree is mid-mutation.

    WHY THIS EXISTS (2026-08-24). tools/mutation_check.py rewrites its target in place via
    an AST round-trip. A mutation run was backgrounded and tests were run against the same
    file, which produced, in order:

      1. the identical pytest command giving 11 failures and then 110 passes minutes apart;
      2. one of those failures read as "the PII hook now lets leaks through" -- it did not,
         the hook source was simply mutated at that instant;
      3. the nondeterminism blamed on pytest-randomly, which is NOT INSTALLED, so the
         `-p no:randomly` flag that appeared to fix it was a silent no-op and the whole
         A/B comparison built on it was meaningless;
      4. a snapshot copy taken during the run that was itself a mutant -- detectable only
         because the AST round-trip drops the shebang line.

    Every one of those is a false conclusion drawn from a green or red suite that was
    measuring a file nobody intended to test. The failure mode is not "tests are flaky",
    it is "the artifact under test changed underneath the runner", and it is invisible
    unless something looks for the backup file.

    Rule: memory/feedback_inplace_mutator_invalidates_concurrent_runs.md
    """
    if os.environ.get(MUTATION_ENV):
        return  # spawned BY mutation_check; seeing a mutated tree is the point

    stranded = _stranded_backups()
    orphans = orphan_backups(REPO_ROOT)
    if not stranded:
        if orphans:
            # Junk, not an in-flight mutation, so it must not halt the run -- but it is
            # still junk, and staying silent is how one of these sat in the tree long
            # enough to void a whole sweep's isolation signal.
            #
            # Written through the terminal reporter, NOT print(): pytest captures stdout
            # from a session fixture, so a print here is invisible in a default run --
            # which would make this exactly the kind of warning nobody ever sees that the
            # project's hook-tier rule exists to forbid. Verified by
            # test_the_orphan_note_is_actually_VISIBLE_in_default_output.
            junk = "\n".join(f"    {_show(p)}" for p in orphans)
            msg = (
                "\nNOTE: orphaned mutation backup(s) present -- no source file beside "
                "them, so\nthey do not indicate a live mutation and are NOT blocking "
                f"this run:\n{junk}\nDelete them; they are stale copies.\n"
            )
            reporter = request.config.pluginmanager.get_plugin("terminalreporter")
            if reporter is not None:
                reporter.write_line(msg, yellow=True)
            else:
                print(msg)
        return

    names = "\n".join(f"    {_show(p)}" for p in stranded)
    pytest.exit(
        "\n"
        "REFUSING TO RUN: a mutation run owns the source tree.\n\n"
        "Found mutation_check backup file(s):\n"
        f"{names}\n\n"
        "One of two things is true, and both make this run's result meaningless:\n"
        "  (a) tools/mutation_check.py is running right now -- the file under test is\n"
        "      mutated at this instant, so pass/fail says nothing about your code; or\n"
        "  (b) a previous run crashed and left the tree mutated.\n\n"
        "For (a): wait for it to finish -- `ps aux | grep \"[m]utation_check\"`.\n"
        "For (b): re-run mutation_check on that target; it restores stranded backups on\n"
        "         startup (recover_if_stranded), or restore the .mutation_backup by hand.\n\n"
        "Do NOT delete the backup to make this message go away: it is the only copy of\n"
        "the unmutated source while a run is in flight.\n",
        returncode=CONFTEST_REFUSAL,
    )
