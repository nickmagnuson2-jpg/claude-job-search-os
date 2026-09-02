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

# Set by tools/mutation_check.py in the env of the pytest subprocesses it spawns.
# Those runs are SUPPOSED to see a mutated tree -- that is the whole mechanism.
MUTATION_ENV = "MUTATION_CHECK_ACTIVE"


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
