"""Session-wide guards for the test suite.

Currently one: refuse to run while a mutation run owns a source file.
"""
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Set by tools/mutation_check.py in the env of the pytest subprocesses it spawns.
# Those runs are SUPPOSED to see a mutated tree -- that is the whole mechanism.
MUTATION_ENV = "MUTATION_CHECK_ACTIVE"


def _stranded_backups() -> list[Path]:
    """Backup files left by tools/mutation_check.py.

    The tool copies <target> to <target>.mutation_backup for the duration of a run and
    rewrites the target in place. The backup existing therefore means one of two things,
    and both invalidate a test result: a run is in progress right now, or a previous run
    crashed and left the tree mutated.
    """
    return sorted(REPO_ROOT.glob("**/*.mutation_backup"))


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
    if not stranded:
        return

    names = "\n".join(f"    {p.relative_to(REPO_ROOT)}" for p in stranded)
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
        returncode=3,
    )
