#!/usr/bin/env python3
"""
mutation_check.py — deterministic mutation testing: does a green suite actually test anything?

WHY THIS EXISTS
    A passing test suite is ambiguous evidence. It is green either because the behavior
    works, or because no test can SEE the behavior. Prose ("write meaningful tests")
    cannot tell those apart, because it needs in-moment judgment. Mutation survival can:
    break the code on purpose, and if every test still passes, the suite is decorative
    at that spot. That is a COUNT, not an opinion.

    Origin, 2026-08-19. In one session, all of these were green and all were hollow:
      - `--personal` could be turned into a no-op and NO test failed (the flag decides
        whether private mail lands in a PUBLIC repo).
      - Both primary harvest parsers of the PII denylist could `return set()` and 20/20
        of their tests passed -- and the stopwords guard built on them goes VACUOUS when
        they die, because an empty entity set trivially has "no offenders."
      - A guard hook's whole Bash branch, its /dev/ filter, and its scoping could each be
        removed with only some of them noticed.

    Each was found by hand-mutating and re-running. This automates that so it is not
    optional and not dependent on remembering.

WHAT IT DOES NOT DO
    It does not prove tests are good. A mutant killed by an unrelated crash still counts
    as killed. It measures ONE property: that some test observes each mutated decision.
    It is also blind to the failure mode where a test only passes because of test-ordering
    side effects -- for that, run the file alone (`--isolation` does this).

EQUIVALENT MUTANTS ARE REAL
    Some mutants cannot change behavior (a redundant guard that a later check subsumes).
    Those may be allowlisted in `tools/mutation-allow.json`, but ONLY with a written
    reason. The reason is a required field, not a shrug: an allowlist without one is how
    this decays back into "green means done."

USAGE
    PYTHONIOENCODING=utf-8 python3 tools/mutation_check.py tools/foo.py
    ... tools/foo.py --tests tests/scripts/test_foo.py --json
    ... tools/foo.py --isolation          # also run each mapped test file ALONE
    ... --list                            # show mutants without running them

EXIT CODES
    0  every mutant killed (or allowlisted with a reason)
    2  at least one mutant SURVIVED, or an isolation run failed
    1  usage / setup error (no tests mapped, unparseable target, ...), or the target
       could not be put back: `code` is target_diverged (another process edited it
       during the run, and it was left as found) or target_not_restored

WHAT IT LEAVES ON DISK
    On every exit it can act on (normal, exception, SIGTERM, SIGINT, SIGHUP) the target
    is put back to its PRE-RUN content, read back, and the result is reported: silence
    means restored, `TARGET NOT RESTORED` on stderr means it was not. SIGKILL and power
    loss cannot be handled here; the backup stays and the next run recovers from it.
"""
from __future__ import annotations

import argparse
import ast
import atexit
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# MUTATION_REPO_ROOT overrides for tests, mirroring the PII_REPO_ROOT /
# SESSION_REPO_ROOT / GMAIL_LABELS_CONF_PATH seams elsewhere in tools/. Without
# it this tool cannot be exercised against a fixture repo, which would leave the
# gate that demands mutation evidence with none of its own.
# Tests set it. Floored at 1: 0 would divide by zero after the first mutant (Codex
# commit-3 round 2 F3).
PROGRESS_EVERY = max(1, int(os.environ.get("MUTATION_PROGRESS_EVERY", "25")))
REPO_ROOT = Path(os.environ.get("MUTATION_REPO_ROOT",
                                str(Path(__file__).resolve().parent.parent)))
ALLOW_FILE = REPO_ROOT / "tools" / "mutation-allow.json"

# tests/conftest.py exits with this when a *.mutation_backup exists anywhere in the tree.
# It means "I refused to measure", never "your test failed".
# Imported, never re-declared: tests/conftest.py produces this exit code and this file
# matches on it. A second literal would drift silently. See tools/conftest_guard.py.
# Sibling import: resolve tools/ explicitly rather than relying on sys.path[0] being
# the script dir. Running this as a script works either way, but importing it (a test
# helper, mutation_sweep, an ad-hoc loader) does not, and a bare import made this
# module unimportable outside a pytest run that had already inserted tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest_guard import (  # noqa: E402
    CONFTEST_REFUSAL, backup_dir, backup_path, prune_orphans,
)
DEFAULT_TIMEOUT = 300

# Pytest's own short-summary lines: `FAILED path::test_name - reason` / `ERROR path`.
# Used to name the failing test in a baseline_red verdict, because a verdict without
# evidence cost seven nights of unreadable sweep logs (2026-09-14).
_FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.M)


# ---------------------------------------------------------------------------
# Mutation operators
# ---------------------------------------------------------------------------
# Each returns a list of (lineno, col, op_name, description, new_node_factory).
# Everything is derived from the AST in source order, so a given file always
# produces the same mutants in the same order. No randomness, no sampling: a
# non-deterministic gate cannot be a gate.

class _Mutator(ast.NodeTransformer):
    """Applies exactly ONE mutation, identified by (lineno, col_offset, op)."""

    def __init__(self, target_key):
        self.target = target_key
        self.applied = False

    def _hit(self, node, op):
        return (node.lineno, node.col_offset, op) == self.target

    def visit_If(self, node):
        self.generic_visit(node)
        if self._hit(node, "IF_FALSE"):
            self.applied = True
            node.test = ast.Constant(value=False)
        elif self._hit(node, "IF_TRUE"):
            self.applied = True
            node.test = ast.Constant(value=True)
        return node

    def visit_Compare(self, node):
        self.generic_visit(node)
        if self._hit(node, "NEGATE_CMP"):
            self.applied = True
            return ast.UnaryOp(op=ast.Not(), operand=node)
        return node

    def visit_Return(self, node):
        self.generic_visit(node)
        if self._hit(node, "RETURN_NONE"):
            self.applied = True
            node.value = ast.Constant(value=None)
        return node

    def visit_Expr(self, node):
        self.generic_visit(node)
        if self._hit(node, "DROP_CALL"):
            self.applied = True
            return ast.Pass()
        return node

    def visit_Continue(self, node):
        if self._hit(node, "DROP_CONTINUE"):
            self.applied = True
            return ast.Pass()
        return node


def enumerate_mutants(tree: ast.AST) -> list[tuple]:
    """All (lineno, col, op) mutation points, in deterministic source order."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            out.append((node.lineno, node.col_offset, "IF_FALSE"))
            out.append((node.lineno, node.col_offset, "IF_TRUE"))
        elif isinstance(node, ast.Compare):
            out.append((node.lineno, node.col_offset, "NEGATE_CMP"))
        elif isinstance(node, ast.Return) and node.value is not None:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                out.append((node.lineno, node.col_offset, "RETURN_NONE"))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            out.append((node.lineno, node.col_offset, "DROP_CALL"))
        elif isinstance(node, ast.Continue):
            out.append((node.lineno, node.col_offset, "DROP_CONTINUE"))
    return sorted(set(out))


class SelfMutationRefused(Exception):
    """This file was named as a target. A decision, not a failure to analyse."""


def count_mutants(target: Path) -> int:
    """How many mutants `target` has. Reads and parses; touches nothing else.

    For a tool that has mapped tests this is the number `--list` prints, for callers that
    want only the number. (`--list` prints no count at all for a tool with no mapped tests,
    and it runs stranded-file recovery before it counts; this does neither.)
    `mutation_sweep --targets` used to get it by starting `mutation_check.py <tool> --list`
    once per tool, and `--list` runs main()'s whole prologue first: tree lock, orphan
    pruning, stranded-file recovery, a backup of the target, an atexit restore. Counting a
    corpus of ~160 tools therefore took the lock 160 times and wrote 160 backups of real
    tools. Two tests of the sweep do that scan on the real repo, so (measured 2026-10-05):

      * every tool that maps their file had a 430s baseline against the 300s budget and
        was never measured, for as long as the baseline has existed;
      * a child SIGKILLed at that timeout stranded its backup, which made pytest and
        cross-model dispatch refuse to run the next morning (three logged blocks);
      * under a sweep the children inherit MUTATION_SWEEP_ACTIVE, skip the lock, and
        "recover" the tool being measured, deleting the live run's only backup.

    Raises SelfMutationRefused for this file, and SyntaxError / OSError / ValueError for a
    file it cannot read or parse. It never returns a number for a file it did not analyse.
    """
    target = Path(target)
    if target.resolve() == SELF:
        raise SelfMutationRefused(
            "refusing to enumerate mutation_check.py as a mutation target")
    return len(enumerate_mutants(ast.parse(target.read_text(encoding="utf-8"))))


def enclosing_function(tree: ast.AST, lineno: int) -> str:
    """Innermost def containing `lineno`; used for a line-drift-resistant key."""
    best, best_span = "<module>", None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= lineno <= end:
                span = end - node.lineno
                if best_span is None or span < best_span:
                    best, best_span = node.name, span
    return best


def mutant_key(rel_path: str, func: str, op: str, source_line: str) -> str:
    """Stable identity for the allowlist.

    Deliberately NOT the line number, which drifts on every edit above it. Uses the
    enclosing function plus a hash of the stripped source line, so a mutant keeps its
    identity when unrelated code moves.
    """
    h = hashlib.sha1(source_line.strip().encode("utf-8")).hexdigest()[:8]
    return f"{rel_path}::{func}::{op}::{h}"


# ---------------------------------------------------------------------------
# Test mapping
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=4096)
def code_refs(source: str) -> tuple[str, tuple[str, ...]] | None:
    """The parts of a test file that can actually REFERENCE a module, in two groups:
    (identifiers, string literals). Identifiers are names, attribute names, def/class
    names and imported module names. Comments and docstrings are in neither group.
    None means the file would not parse, and the caller keeps it selected.

    TWO GROUPS BECAUSE THEY NEED DIFFERENT TESTS (2026-10-06). An identifier equal to the
    module's name is a reference. A string literal that merely contains the name as a word
    is usually a sentence: tools/sweep.py was mapped to 14 test files, 10 of them through
    the English word "sweep" in an assertion message or a job name ("mutation-sweep"), and
    one of those was test_mutation_sweep.py, slow enough that sweep.py could not be
    measured inside the time limit. See _LITERAL_REF for what a literal has to look like.

    Origin 2026-08-31. map_tests selected files with `if stem in text` over the raw source:
    a bare substring scan, no word boundary, prose included. For tools/todo_write.py that
    chose 15 files instead of 6 (56s per run instead of 2s), and at 541 mutants the sweep
    blew its 5h cap and recorded UNAUDITED_TIMEOUT - the tool went unmeasured. The three
    costly extras named the tool only in prose, and the worst match was the string
    `personal_todo_write` (a DIFFERENT tool that merely contains this name) inside a
    comment listing launchd jobs.

    Two deliberate choices about which way to err:

    STRING LITERALS ARE KEPT. Many tests invoke a tool as a subprocess rather than
    importing it (`subprocess.run([..., "tools/todo_write.py"])`). Dropping literals would
    un-cover every CLI-invoked tool and convert real kills into survivors.

    UNPARSEABLE FILES FAIL OPEN. A file that will not parse returns None and map_tests
    keeps it selected. Excluding it would drop real coverage; including it only costs
    runtime. When the analysis is uncertain, measure more rather than less.

    REMEMBERED BY CONTENT (2026-10-06). map_tests calls this for every test file, once per
    tool, so mapping the corpus parsed 222 files 184 times: 86s, against 0.43s to parse
    each file once. Keyed on the source text itself, never the path, so an edited file is
    simply a different key and there is no staleness to reason about. A path-keyed memo
    would freeze a mapping at whatever the file said the first time it was read.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None  # fail open: map_tests keeps an unparseable file selected

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            first = body[0] if body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))

    parts: list[str] = []
    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
        elif isinstance(node, ast.arg):
            parts.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                parts.extend([a.name, a.asname or ""])
        elif isinstance(node, ast.ImportFrom):
            parts.append(node.module or "")
            for a in node.names:
                parts.extend([a.name, a.asname or ""])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                literals.append(node.value)
    return "\n".join(parts), tuple(literals)


def _literal_ref(names: set[str]) -> re.Pattern:
    """What a string literal must contain to count as naming one of `names`.

    A file name (`todo_write.py`, as in a subprocess argv or a path), a dotted path into
    the module (`todo_write.main`, as in monkeypatch.setattr or `-m`), or the bare name
    as the WHOLE string (importlib.import_module("todo_write")). Not the name as one word
    among others.

    Measured on the real repo, 2026-10-06, old rule against this one over every
    tools/*.py: 652 tool-to-test pairs become 638, and no pair is added. The 14 dropped
    are in 4 tools and each was read: eleven are the word "sweep" in prose or in
    "mutation-sweep", one is an `outreach_status:` stamp in fixture text, one is a
    free-text note naming schema_guard, one is "attention" inside another file's name
    ("what-captures-attention.md"). None imports, runs or patches the tool.
    """
    alt = "|".join(re.escape(n) for n in sorted(names))
    # A hyphen before the name is part of a longer word ("mutation-sweep.plist"), so it
    # is excluded with the word characters and the dot.
    # `tools.<name>` is the one dotted prefix that IS a reference: the package-qualified
    # form, as in importlib.import_module("tools.todo_write") or a patch target.
    return re.compile(
        r"(?:(?<![\w.\-])|(?<=\btools\.))(?:%s)(?:\.py\b|\.[A-Za-z_])"
        r"|^\s*(?:tools\.)?(?:%s)\s*$" % (alt, alt))


def covering_names(stem: str, tools_dir: Path) -> set[str]:
    """`stem` plus every tools/ module that transitively imports it.

    A test can exercise a target WITHOUT naming it: it imports `check_pipe_close_via_update`,
    which imports `hook_command_lint`, so mutating hook_command_lint can fail that test -- a
    real kill. Selecting only on the target's own name would drop that file and report the
    mutant as a survivor.

    Found 2026-08-31 while tightening map_tests off raw-substring matching: of 66 selections
    the tightening dropped, exactly 2 had such an indirect path
    (hook_command_lint <- test_check_pipe_close_via_update, todo_daily_metrics <-
    test_todo_write_roundtrip). Both are cheap to keep, and losing a real kill is the one
    error this whole change exists to avoid.
    """
    imports: dict[str, set[str]] = {}
    for f in tools_dir.glob("*.py"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        imports[f.stem] = set(re.findall(r"^\s*(?:import|from)\s+(\w+)", text, re.M))

    covering = {stem}
    changed = True
    while changed:                      # fixpoint; terminates, `covering` only grows
        changed = False
        for mod, deps in imports.items():
            if mod not in covering and deps & covering:
                covering.add(mod)
                changed = True
    return covering


def map_tests(target: Path, repo_root: Path | None = None) -> list[Path]:
    """Test files that plausibly cover `target`, by name and by import reference.

    `repo_root` is explicit for callers in another module. mutation_sweep imports this
    function to decide selection, and a module-level REPO_ROOT resolved at ITS import time
    can differ from the caller's -- which is exactly how selection and measurement drifted
    apart before. Passing it removes the coupling instead of documenting it.
    """
    stem = target.stem
    tests_dir = (repo_root or REPO_ROOT) / "tests"
    if not tests_dir.exists():
        return []
    hits = set()
    # Selection by NAME. `test_<stem>.py` plus the split-suite convention this repo
    # actually uses (`test_todo_write_guard.py`, `_roundtrip`, `_sync`, ...). A filename
    # is a deliberate authoring signal and carries no prose, so it is matched by prefix
    # rather than by word boundary - `test_todo_write_roundtrip.py` has no word break
    # after the stem, and requiring one would drop all six of that tool's real suites.
    # `test_personal_todo_write*.py` is NOT selected: the prefix is anchored at the start.
    for path in (tests_dir / "scripts").glob(f"test_{stem}*.py"):
        hits.add(path)
    names = covering_names(stem, target.parent)
    word = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in sorted(names)) + r")\b")
    literal = _literal_ref(names)
    for path in tests_dir.rglob("test_*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        refs = code_refs(text)
        if refs is None:
            hits.add(path)              # would not parse: measure more rather than less
            continue
        identifiers, literals = refs
        if word.search(identifiers) or any(literal.search(s) for s in literals):
            hits.add(path)
    return sorted(hits)


# Two independent sources for the failure reason, because ONE is not reliable:
#   summary  `FAILED path::test - AssertionError: msg`   (-rf)
#   traceback `path:LINE: AssertionError: msg`            (--tb=line)
# The summary form OMITS the reason for some ids (notably a parametrized test whose
# id renders as `name[]`). Relying on it alone made every kill look like a non-assertion
# kill, i.e. the tool accused correct tests of asserting nothing (found 2026-08-19 by
# probing pytest's real output instead of assuming it).
_SUMMARY_RE = re.compile(r"^(?:FAILED|ERROR)\s+\S+\s+-\s+([A-Za-z_][\w.]*)", re.M)
_TB_RE = re.compile(r"^\S+:\d+:\s+([A-Za-z_][\w.]*)", re.M)
# `Failed` is what pytest.raises emits when the expected exception does NOT arrive.
# That is an assertion about behavior, not an incidental crash, so it counts as one.
#
# `assert` is pytest's rendering of a BARE assertion, and it is here because omitting it
# made this whole metric measure the wrong thing (found 2026-08-26). Under `--tb=line`
# pytest prefixes "AssertionError:" only when it generates a multi-line explanation:
#
#   assert 'neg' == 'pos'   ->  test.py:3: AssertionError: assert 'neg' == 'pos'
#   assert 1 == 2           ->  test.py:3: assert 1 == 2
#
# so an ordinary `assert got == 2` was filed as a CRASH kill, while the same assertion
# over strings was filed correctly. The bug was conditional on the compared TYPE, which
# made weak_kill_count incomparable between an int-heavy tool and a string-heavy one and
# is why adding message strings to existing tests moved it 31 -> 11 with no code change.
#
# Capturing the bare token is unambiguous: `assert` is a Python keyword, so it can never
# be the name of an exception class arriving on that line.
_ASSERTION_KINDS = {"AssertionError", "Failed", "assert"}


# Held for the life of the process. flock is released by the OS when the fd closes, so
# this must stay referenced -- a garbage-collected fd silently drops the lock.
_RUN_LOCK_FDS: dict[str, int] = {}

# Set by mutation_sweep in the env of the mutation_check children it spawns. Those children
# are running UNDER a lock the sweep already holds; making them queue for it would deadlock
# the sweep against itself.
SWEEP_ENV = "MUTATION_SWEEP_ACTIVE"

# Set by THIS tool in the env of every test process it starts (mutant runs and the
# isolation pass), because it holds the tree lock for its whole life. It is deliberately
# NOT SWEEP_ENV and acquire_run_lock does not read it: SWEEP_ENV makes the lock itself
# return success, so exporting that to tests would let any test start a second mutation of
# the same tree unlocked. This one is honoured only by callers that READ the tree, today
# `mutation_sweep --targets`, which would otherwise be refused by the run executing it.
LOCK_HELD_ENV = "MUTATION_TREE_LOCK_HELD"


def _run_lock_path(root: Path | None = None) -> Path:
    """One lock per REPO, keyed the way backups are.

    NOT one global lock: two different checkouts are independent trees and must not block
    each other. NOT the `.mutation_backup` suffix either -- `conftest_guard.stranded_backups`
    matches on that suffix, so a lock file named that way would itself read as a stranded
    mutation and refuse the entire test suite.
    """
    enc = str(root or REPO_ROOT).replace("%", "%25").replace("/", "%2F")
    return backup_dir() / (enc + LOCK_SUFFIX)


def run_lock_held(root: Path) -> bool:
    """True when a mutation run holds the lock for `root`, or when that cannot be told.

    The question callers used to answer with `pgrep -f mutation_check.py`, which also
    matches any process whose arguments merely NAME this file (a review of it, an editor),
    so it could report a run that did not exist. The flock is held for exactly the life of
    a run and released by the OS when the run dies, so trying it without waiting answers
    the question directly.
    """
    lock = _run_lock_path(Path(root).resolve())
    try:
        fd = os.open(lock, os.O_RDWR)
    except FileNotFoundError:
        return False                    # no run has ever taken this repo's lock
    except OSError:
        return True                     # cannot tell: say alive, so callers stay careful
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:                     # BlockingIOError included: someone holds it
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


LOCK_SUFFIX = ".mutation-run.lock"


def prune_stale_locks() -> list[Path]:
    """Delete lock files whose tree no longer exists, returning what was removed.

    The lock is one file per repo root, in a store shared by every tree. Releasing an
    flock does not remove the file, and every test that runs this tool in a throwaway
    repo leaves one behind for a directory pytest then deletes: 713 of them on
    2026-09-23, 1,083 on 2026-10-06, and nothing removed any. Same reason and same rule
    as prune_orphans: a file that names a tree which is gone cannot be in use.

    Three checks, in this order, before a file is removed. The tree is missing. The lock
    can be taken without waiting, so no process holds it. The tree is STILL missing once
    the lock is held, which covers a tree created at the same path in between (pytest
    reuses its temp directory names).
    """
    removed: list[Path] = []
    store = backup_dir()
    if not store.is_dir():
        return removed
    for lock in sorted(store.glob("*" + LOCK_SUFFIX)):
        root = Path(lock.name[:-len(LOCK_SUFFIX)].replace("%2F", "/").replace("%25", "%"))
        if root.exists():
            continue
        try:
            fd = os.open(lock, os.O_RDWR)
        except OSError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not root.exists():
                os.unlink(lock)
                removed.append(lock)
        except OSError:
            pass                            # held, or already gone: not ours to remove
        finally:
            os.close(fd)
    return removed


def release_run_lock() -> None:
    """Drop the lock so one process can run several sweeps in sequence (the test suite)."""
    fd = _RUN_LOCK_FDS.pop(str(_run_lock_path()), None)
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


DEFAULT_RUN_LOCK_WAIT = 600.0


def acquire_run_lock(wait: float = 0.0) -> tuple[bool, str]:
    """Take an exclusive lock on the mutation tree. (acquired, holder).

    WAIT, DO NOT REFUSE (2026-09-07). This refused instantly and told the caller to
    "wait for it", which meant every caller hand-rolled the waiting. In one evening one
    session was refused five times across four concurrent sessions and wrote the same
    `while ls <backup dir>; do sleep; done` loop in bash each time -- roughly 25 minutes
    of blocked wall-clock, plus a suite run whose 34 failures were pure concurrency
    noise. A guard that is correct and unusable gets worked around, and a hand-rolled
    wait polls the wrong thing: the backup directory is empty between mutants, so the
    loop races back in mid-run.

    Waiting here polls the lock itself, which is the only thing that is actually true.
    `wait=0` keeps the old non-blocking behaviour for callers that want to fail fast.

    RE-ENTRANT WITHIN A PROCESS. flock is per open-file-description, so a second `open` of
    the same path from the SAME process conflicts with the first -- which broke 28 sweep
    tests on first attempt, because each test called run_sweep in one pytest process and
    every call after the first was refused by its own predecessor.

    WHY THIS EXISTS (2026-09-06). Nothing stopped two mutation runs from rewriting the same
    tools/*.py at once, and it happened three times in one evening: a second Claude Code
    session ran mutation_check while a sweep was live, which produced a spurious
    `baseline_red` on an unrelated tool, and twice made the test suite refuse to run for
    reasons that looked like corruption. Two runs also restore from each other's backups,
    which can leave a file holding another mutant's source.

    flock rather than a pidfile ON PURPOSE. The OS releases an flock when the holding
    process dies, including a SIGKILL or a power loss. A pidfile would have survived the
    2026-09-05 crash and blocked every subsequent sweep until someone deleted it by hand --
    trading a concurrency bug for a stale-lock bug that is worse, because it fails closed on
    a machine nobody has looked at yet.
    """
    if os.environ.get(SWEEP_ENV):
        return True, ""                     # spawned BY a sweep that already holds it
    lock_path = _run_lock_path()
    if str(lock_path) in _RUN_LOCK_FDS:
        return True, ""                     # this process already holds it
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    started = time.monotonic()
    deadline = started + max(0.0, wait)
    announced = False
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                holder = os.read(fd, 128).decode("utf-8", "replace").strip() or "unknown"
            except OSError:
                holder = "unknown"
            if time.monotonic() >= deadline:
                os.close(fd)
                return False, holder
            if not announced:
                # stderr, not stdout: stdout is the JSON contract and a caller
                # parsing it must not receive progress chatter.
                print(f"mutation_check: tree held by {holder}; waiting up to "
                      f"{wait:.0f}s", file=sys.stderr, flush=True)
                announced = True
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
    os.ftruncate(fd, 0)
    os.write(fd, f"pid={os.getpid()} since={time.strftime('%Y-%m-%dT%H:%M:%S')}".encode())
    _RUN_LOCK_FDS[str(lock_path)] = fd
    # Same log inbox_lock writes, so the two contention sources are comparable in one
    # place. Best effort and deliberately last: a logging fault must never cost a lock
    # that is already held.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import inbox_lock
        inbox_lock.log_contention("mutation-tree-lock", time.monotonic() - started)
    except Exception:
        pass
    return True, ""


def _kill_process_group(proc: subprocess.Popen) -> None:
    """SIGKILL the child's entire process group, falling back to the child alone."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _main_checkout(root: Path) -> Path | None:
    """The main checkout's root when `root` is a linked git worktree, else None. A linked
    worktree has a `.git` FILE naming `gitdir: <main>/.git/worktrees/<name>`."""
    dotgit = root / ".git"
    if not dotgit.is_file():
        return None
    try:
        line = dotgit.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = Path(line.split(":", 1)[1].strip())
    gitdir = gitdir if gitdir.is_absolute() else (root / gitdir)
    # <main>/.git/worktrees/<name> -> <main>
    parts = gitdir.resolve().parts
    if "worktrees" in parts:
        i = len(parts) - 1 - parts[::-1].index("worktrees")
        if i >= 1 and parts[i - 1] == ".git":
            return Path(*parts[:i - 1])
    return None


def _test_env_base(environ=None, root: Path | None = None) -> dict:
    """The environment the test subprocesses inherit.

    In a linked worktree (plan A3, output/mutation-parallel-102826/plan.md), any variable
    whose value is a path inside the MAIN checkout is dropped. `CLAUDE_PROJECT_DIR` is the
    known case: hooks and tools resolve the repo from it, so a worker mutating its own copy
    would otherwise test the live checkout. Dropping by value, not by name, also covers the
    tool-specific overrides (SEAL_REPO_ROOT, PII_REPO_ROOT, FRICTION_LEDGER, ...) that a
    session may have set. In the main checkout nothing is dropped.
    """
    environ = dict(os.environ if environ is None else environ)
    main = _main_checkout(root or REPO_ROOT)
    if main is None:
        return environ
    main = main.resolve()

    def points_home(value: str) -> bool:
        for piece in value.split(os.pathsep):
            if not piece.startswith(("/", "~")):
                continue
            try:
                q = Path(piece).expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if q == main or main in q.parents:
                return True
        return False

    return {k: v for k, v in environ.items() if not points_home(v)}


def _run_reaping_descendants(cmd: list[str], env: dict[str, str], timeout: int,
                             cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    """subprocess.run, except a timeout kills the whole PROCESS GROUP.

    WHY THIS EXISTS (2026-09-06). `subprocess.run(timeout=...)` SIGKILLs only the DIRECT
    child. Here that child is pytest, and the tools pytest exercises spawn subprocesses of
    their own. Those GRANDCHILDREN survive the kill, are reparented to launchd (ppid 1),
    and run forever.

    Observed that day: a sweep spawned
    `check_prep_doc.py tests/fixtures/w4/compliant-prep.md` four seconds in. The runner
    timed out, moved to the next tool, and the sweep ran to completion -- while that
    grandchild sat at 98.8% CPU and was still going 36 minutes later, after the sweep had
    exited. It had to be killed by hand.

    One orphan appeared in the first four seconds of a 33-minute run. The 2026-09-05 run
    went unattended for over nine hours and ended with the machine wedged badly enough to
    need a power cycle, with no panic and no clean shutdown recorded. Orphans accumulating
    at that rate, each pinning a core, is the best-supported mechanism for it.

    `start_new_session=True` puts the child in its own process group, so `killpg` reaches
    every descendant it spawned rather than just the one process we can see.
    """
    # cwd is a PARAMETER, not this module's REPO_ROOT: mutation_sweep calls this with its
    # own root, and its tests drive it against a throwaway fixture repo. Defaulting to
    # REPO_ROOT here silently ran the sweep's engine in the REAL tree during those tests,
    # so the stub engine recorded nothing and 18 tests failed on an empty call list.
    proc = subprocess.Popen(cmd, cwd=str(cwd or REPO_ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        # Drain after the kill so the pipes close and no zombie is left behind. The
        # group is already dead, so this returns immediately; the bound is paranoia.
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def run_tests(test_files: list[Path], timeout: int,
              target: Path | None = None,
              details: dict | None = None) -> tuple[bool, str]:
    """(passed, kill_kind).

    kill_kind classifies HOW the suite noticed a mutation, which is the difference
    between a test that ASSERTS something about output and one that merely CRASHES:

      "assertion"  at least one failure was an AssertionError -> a test checked a value
      "error"      failures were all non-assertion (ImportError, TypeError, collection
                   error, ...) -> the mutation was OBSERVED but nothing was asserted
      "timeout"    the run exceeded the time limit. NOT a kill and NOT a survivor: a
                   mutant that loops forever and a suite that was merely slow that
                   night look identical from here, so the caller records it as
                   unmeasured (2026-10-06; it used to be scored as a kill)
      ""           the suite passed (mutant survived)

    WHY THIS EXISTS: plain mutation survival measures OBSERVABILITY, not assertion
    quality. A suite that kills every mutant by crashing has perfect mutation scores
    and near-zero assertions -- exactly the hollow-but-green state this tool is meant
    to expose. Distinguishing the two costs one regex over pytest's summary lines.

    A weak kill is REPORTED, never failed on by default: some code legitimately signals
    by raising, so an error-kill is the correct observation there. Failing on it would
    make the gate noisy, and a noisy gate gets disabled -- which is worse than no gate,
    because it still reads as protection.
    """
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "--no-header",
           "--tb=line", "-rf", *map(str, test_files)]
    # tests/conftest.py refuses to run when a *.mutation_backup exists, because that means
    # the tree is mid-mutation and any pass/fail is measuring a file nobody meant to test
    # (2026-08-24 incident, see that fixture's docstring). These subprocesses are the ONE
    # caller for which a mutated tree is correct, so they are exempted explicitly rather
    # than by the fixture trying to guess who spawned it.
    # PYTHONDONTWRITEBYTECODE: this loop rewrites the target dozens of times per second,
    # and CPython invalidates a cached .pyc by (mtime, size) -- a granularity coarse enough
    # that a later mutant can be executed as an EARLIER mutant's bytecode. That produces
    # FALSE KILLS, the one error this tool must never make: a false survivor costs a test
    # nobody needed, a false kill certifies that a suite protects a behavior it does not.
    # Measured 2026-08-31 on ss_route_conversation.py -- a mutant that survives by hand
    # 3 of 3 reported KILLED in 5 of 6 runs, correct only on the first run after the cache
    # was cleared. Writing no bytecode at all makes the whole class unreachable.
    # LOCK_HELD_ENV: this process holds the tree lock for its whole life, so a read-only
    # caller these tests start must be told its own ancestor has it. Without it a test
    # that starts `mutation_sweep --targets` is refused by the run executing it
    # (2026-10-06: this turned mutation_sweep.py's own baseline red in 15s).
    env = {**_test_env_base(), "MUTATION_CHECK_ACTIVE": "1",
           "PYTHONDONTWRITEBYTECODE": "1", LOCK_HELD_ENV: "1"}
    # MUTATION_CHECK_TARGET names the ONE file this run is deliberately rewriting.
    # A guard that detects corrupted source cannot tell an intended mutant from real
    # corruption, so without this it fires on every mutant and the mutant is scored
    # KILLED regardless of what the mutation changed -- a false-perfect survived=0.
    # Measured 2026-09-06: every tool whose mapped tests include the unparse ratchet in
    # test_no_silent_failures.py reported exactly 0 survivors (5 of 5 tools, 0 of 180
    # mutants) while tools without it survived at 34.1%.
    # Naming the single path rather than setting a blanket "suppress" flag is the point:
    # BYSTANDER corruption still fails, and that is the case that actually recurs
    # (vault_paths.py 2026-08-19 and 2026-08-28, todo_write.py 2026-09-05).
    if target is not None:
        env["MUTATION_CHECK_TARGET"] = str(target.relative_to(REPO_ROOT))
    try:
        r = _run_reaping_descendants(cmd, env, timeout)
    except subprocess.TimeoutExpired:
        if details is not None:
            details.update(timed_out=True, timeout=timeout, failed=[],
                           output=f"run exceeded the {timeout}s timeout; no pytest "
                                  f"output was produced. This is a TIMEOUT, not a red "
                                  f"suite -- the distinction changes the fix.")
        return False, "timeout"
    if r.returncode == 0:
        return True, ""
    out = r.stdout + r.stderr
    if details is not None:
        # Node ids first: the single most useful line is WHICH test died.
        details.update(timed_out=False, returncode=r.returncode,
                       failed=_FAILED_RE.findall(out)[:20],
                       output=out[-4000:])
    kinds = set(_SUMMARY_RE.findall(out)) | set(_TB_RE.findall(out))
    if not kinds:
        # Unparseable, NOT "no assertion". Reporting an unknown as a weak kill would
        # accuse a correct test of checking nothing -- a false accusation is worse
        # than a missing signal, because it sends someone to rewrite a good test.
        return False, "unknown"
    return False, ("assertion" if kinds & _ASSERTION_KINDS else "error")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def audit_test_quality(test_files: list[Path]) -> dict:
    """Static defects that mutation testing structurally cannot see.

    A test with NO assertion cannot fail for the right reason -- it can only fail by
    crashing, so it registers as a kill while checking nothing. A tautological
    assertion (`assert True`, `assert x == x`) is worse: it looks like a check and is
    unfalsifiable. Neither shows up as a surviving mutant, because both still observe
    a crash. This is the half of "does the test actually test something" that lives in
    the test source rather than in the mutation result.
    """
    ASSERT_METHODS = ("assertEqual", "assertTrue", "assertFalse", "assertIn",
                      "assertRaises", "assertIs", "assertIsNone", "assertNotEqual",
                      "assertAlmostEqual", "assertGreater", "assertLess")
    assertion_free, tautological = [], []

    for path in test_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        try:
            rel = str(path.relative_to(REPO_ROOT))
        except ValueError:
            rel = str(path)          # outside the root (fixture repos, ad-hoc paths)

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not fn.name.startswith("test"):
                continue

            has_assert = False
            for node in ast.walk(fn):
                if isinstance(node, ast.Assert):
                    has_assert = True
                    t = node.test
                    # `assert <literal>` — unfalsifiable
                    if isinstance(t, ast.Constant):
                        tautological.append({"file": rel, "test": fn.name,
                                             "line": node.lineno,
                                             "why": f"assert on the constant {t.value!r}"})
                    # `assert x == x` — compares a value to itself
                    elif (isinstance(t, ast.Compare) and len(t.comparators) == 1
                          and isinstance(t.ops[0], (ast.Eq, ast.Is))):
                        try:
                            if ast.dump(t.left) == ast.dump(t.comparators[0]):
                                tautological.append({"file": rel, "test": fn.name,
                                                     "line": node.lineno,
                                                     "why": "compares a value to itself"})
                        except Exception:
                            pass
                elif isinstance(node, ast.Call):
                    f = node.func
                    name = getattr(f, "attr", None) or getattr(f, "id", None)
                    if name in ASSERT_METHODS or name == "raises":
                        has_assert = True
                elif isinstance(node, ast.With):
                    for item in node.items:
                        if "raises" in ast.dump(item.context_expr):
                            has_assert = True

            if not has_assert:
                assertion_free.append({"file": rel, "test": fn.name, "line": fn.lineno})

    return {"assertion_free": assertion_free, "tautological": tautological}


SELF = Path(__file__).resolve()


# backup_path is imported from conftest_guard, not defined here: the guard, the sweep's
# repair step and this file must agree on where a backup lives, and it moved out of the
# working tree on 2026-09-01 (iCloud was making conflict copies of it). One definition.


def recover_if_stranded(target: Path) -> str | None:
    """Restore a file left mutated by a previous crashed run.

    THE INCIDENT THIS EXISTS FOR (2026-08-19, first self-audit of this tool): the tool
    was run ON ITSELF. Its own test suite contains a test that invokes the tool against
    a REAL repo file. So a mutant that broke the restore logic executed, restored
    nothing, and left a BYSTANDER file (`tools/vault_paths.py`) mutated -- `living_log`
    silently returning None. A second file was corrupted the same way. Both were caught
    only because the full suite went red afterwards.

    Two lessons, both encoded below: a tool that rewrites live source must be able to
    recover WITHOUT itself being correct, and it must never mutate itself.
    """
    bak = backup_path(target)
    if not bak.exists():
        return None
    try:
        original = bak.read_text(encoding="utf-8")
        if target.read_text(encoding="utf-8") != original:
            target.write_text(original, encoding="utf-8")
            bak.unlink()
            return "restored a file stranded by a previous crashed run"
        bak.unlink()
        return None
    except OSError:
        return "FOUND a stranded backup but could not restore it -- restore by hand"


_IN_FLIGHT: tuple[Path, str] | None = None

# The text this process last put on the target: the pre-run content until the first mutant
# is written, then the latest mutant. A later mutant and the restore may replace THIS or
# the pre-run content and nothing else. Anything else on disk was put there by someone
# else, and this tool has no way to rebuild it.
_LAST_WRITTEN: str | None = None

# Whether the pre-run content was identical to git HEAD. True / False / None (unknown: not
# a git checkout, untracked file, git unavailable). It decides nothing. It is reported, so
# that a reader of a failure knows whether git still holds the pre-run content.
_HEAD_MATCHED: bool | None = None

# What the one restore of this run found, kept so that later callers (the atexit hook, a
# signal arriving during the isolation pass) repeat it instead of touching the file again.
_RESTORE_OUTCOME: dict | None = None

RESTORED, DIVERGED, FAILED = "restored", "diverged", "failed"

# Copies set aside when the target is left as found. In the backup store, which is outside
# every working tree. The names must NOT end in `.mutation_backup`: that suffix makes
# tests/conftest.py refuse the suite and makes the next run's recover_if_stranded write
# the file over the target, which is the overwrite these copies exist to avoid.
ASIDE_ORIGINAL = ".pre-run-original"
ASIDE_MUTANT = ".last-mutant"

_HELD_SIGNALS = tuple(getattr(signal, n) for n in ("SIGTERM", "SIGINT", "SIGHUP")
                      if hasattr(signal, n))


class TargetDiverged(Exception):
    """The target holds content this run did not write. Nothing was overwritten."""


@contextlib.contextmanager
def _signals_held():
    """Hold SIGTERM / SIGINT / SIGHUP until the block ends, then let them arrive.

    The handler restores the target, and it decides what to do from what it reads on
    disk. `write_text` truncates and then writes, so a handler that ran between the two
    would read an empty file, take it for someone else's content and leave it empty. The
    same applies between a write and the line that records it. The signal is not lost:
    the OS keeps it pending and delivers it when the mask is put back.
    """
    try:
        old = signal.pthread_sigmask(signal.SIG_BLOCK, _HELD_SIGNALS)
    except (AttributeError, ValueError, OSError):
        yield                     # no pthread_sigmask on this platform: run unprotected
        return
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


def _shout(text: str) -> None:
    """Write to stderr through the file descriptor.

    Called from a signal handler, which can interrupt the main thread inside a write to
    sys.stderr; a second write through the same buffered object raises RuntimeError
    there. stderr, never stdout: stdout is the JSON contract.
    """
    try:
        os.write(2, (text.rstrip("\n") + "\n").encode("utf-8", "replace"))
    except OSError:
        pass


def instrumented(original: str, marker: Path) -> str | None:
    """`original` with one line that touches `marker` whenever the module runs.

    Placed after the docstring and any `from __future__` imports, which must stay first.
    None when no safe place exists (a statement sharing a line with the one before it).
    """
    try:
        body = ast.parse(original).body
    except SyntaxError:
        return None
    i = 0
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        i = 1
    while i < len(body) and isinstance(body[i], ast.ImportFrom) and body[i].module == "__future__":
        i += 1
    lines = original.splitlines(keepends=True)
    line = f"__import__('pathlib').Path({str(marker)!r}).touch()\n"
    if i < len(body):
        node = body[i]
        if node.col_offset != 0:
            return None
        at = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]) - 1
    else:
        at = len(lines)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
    text = "".join(lines[:at] + [line] + lines[at:])
    try:
        compile(text, "<instrumented>", "exec")
    except SyntaxError:
        return None
    return text


def files_that_run(target: Path, original: str, test_files: list[Path],
                   timeout: int) -> list[Path] | None:
    """The test files that execute `target` at all, found by running each once against a
    copy that touches a marker file. None when the target cannot be instrumented.

    WHY (2026-10-25). Selection is deliberately broad: a test that runs a script as a
    subprocess names it only in a string, so any mention of the path selects the file.
    That made each todo_daily_metrics mutant rerun 30 files and 1,918 tests in about 50s.
    The two biggest, a PII-hook suite and a guard-edit suite, mention the path only in
    example commands and never run it. A file that never runs the target can still catch a
    mutant by READING its source, so this list only orders the work; see main().

    The instrumented copy goes through write_mutant like any mutant, so the restore that
    protects every mutant protects it too.
    """
    with tempfile.TemporaryDirectory(prefix="mutation-probe-") as d:
        marker = Path(d) / "ran"
        text = instrumented(original, marker)
        if text is None:
            return None
        write_mutant(target, original, text)
        kept = []
        for f in test_files:
            marker.unlink(missing_ok=True)
            run_tests([f], timeout)
            if marker.exists():
                kept.append(f)
        return kept


def matches_head(target: Path, text: str, root: Path | None = None) -> bool | None:
    """Is `text` identical to the committed version of `target`? None when unknown.

    Read-only (`git show`), and never a guess: any failure to ask git is None. GIT_*
    variables are dropped so that a run started from a git hook asks about the tree it is
    standing in, not the one the hook's environment points at.
    """
    root = Path(root or REPO_ROOT)
    try:
        rel = Path(target).resolve().relative_to(root.resolve()).as_posix()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        r = subprocess.run(["git", "show", f"HEAD:./{rel}"], cwd=str(root), env=env,
                           capture_output=True, timeout=15)
        if r.returncode != 0:
            return None
        # read_text() translates line endings, so the committed bytes get the same
        # treatment before the comparison.
        head = r.stdout.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return head == text


def _write_owned(target: Path, text: str) -> None:
    """Write the target and record what was written, with no signal able to separate the two."""
    global _LAST_WRITTEN
    with _signals_held():
        target.write_text(text, encoding="utf-8")
        _LAST_WRITTEN = text


def write_mutant(target: Path, original: str, mutated: str) -> None:
    """Put a mutant on disk, unless the file is not what this run left there.

    THE INCIDENT THIS EXISTS FOR (2026-09-06, twice in one day). Each mutant was written
    over whatever the file held, and the final restore did the same. A peer session lost
    93 uncommitted lines in a tool that way, and hours later a backgrounded run reverted
    an edit made by the session that had started it. Both commands reported success and
    the file matched its last commit afterwards, so there was nothing to notice.

    The check is a read followed by a write, so an edit landing in the microseconds
    between them is still lost. The window used to be the whole run.
    """
    with _signals_held():
        on_disk = target.read_text(encoding="utf-8")
        if on_disk != original and on_disk != _LAST_WRITTEN:
            raise TargetDiverged(str(target))
        _write_owned(target, mutated)


def _set_aside(target: Path, original: str) -> dict:
    """Keep the pre-run content and the last mutant when the target is left as found.

    The pre-run content may exist nowhere else (uncommitted work), so it is written
    first and the `.mutation_backup` is removed only once that copy is on disk. The
    backup has to go: the next run's recover_if_stranded, and the sweep's repair step,
    both write a backup over the target without looking at what is there.
    """
    bak = backup_path(target)
    stamp = time.strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    out: dict = {"original_saved_at": None, "mutant_saved_at": None}
    try:
        bak.parent.mkdir(parents=True, exist_ok=True)
        aside = bak.with_name(f"{bak.stem}.{stamp}{ASIDE_ORIGINAL}")
        aside.write_text(original, encoding="utf-8")
        out["original_saved_at"] = str(aside)
        if _LAST_WRITTEN is not None and _LAST_WRITTEN != original:
            mutant = bak.with_name(f"{bak.stem}.{stamp}{ASIDE_MUTANT}")
            mutant.write_text(_LAST_WRITTEN, encoding="utf-8")
            out["mutant_saved_at"] = str(mutant)
    except OSError as e:
        out["aside_error"] = f"{type(e).__name__}: {e}"
    if out["original_saved_at"]:
        try:
            bak.unlink()
        except OSError:
            pass
    elif bak.exists():
        out["backup"] = str(bak)      # the only copy left; say where it is
    return out


def _restore(target: Path, original: str) -> dict:
    """One attempt to put the target back. Returns what happened; raises nothing expected."""
    out = {"target": str(target), "head_matched": _HEAD_MATCHED}
    bak = backup_path(target)
    try:
        try:
            on_disk = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            on_disk = None            # nothing on disk, so nothing to destroy
        if on_disk is not None and on_disk != original and on_disk != _LAST_WRITTEN:
            return {**out, "state": DIVERGED, **_set_aside(target, original)}
        if on_disk != original:
            _write_owned(target, original)
        # Read it back. A write that raised nothing is not evidence that the file
        # now holds the text.
        if target.read_text(encoding="utf-8") != original:
            raise OSError("the file does not hold the pre-run content after the write")
    except (OSError, ValueError) as e:
        # The backup is deliberately KEPT: it is how the next run recovers.
        return {**out, "state": FAILED, "error": f"{type(e).__name__}: {e}",
                "backup": str(bak) if bak.exists() else None}
    try:
        bak.unlink()
    except OSError:
        pass
    return {**out, "state": RESTORED}


def _failure_report(outcome: dict) -> str:
    target = outcome["target"]
    lines = [f"mutation_check: TARGET NOT RESTORED: {target}"]
    if outcome["state"] == DIVERGED:
        lines += [
            "  The file holds content this run did not write: it is neither the pre-run",
            "  content nor the last mutant. Another process edited it during the run.",
            "  It was left exactly as found, because writing over it would delete that edit.",
            "  If the edit was made on top of a mutant, the file still contains the mutation.",
        ]
        if outcome.get("original_saved_at"):
            lines.append(f"  Pre-run content saved at: {outcome['original_saved_at']}")
        else:
            lines.append(f"  Pre-run content could NOT be saved aside "
                         f"({outcome.get('aside_error')}); it is still in the backup "
                         f"{outcome.get('backup')}, which the next mutation_check run on "
                         f"this file will write over the target.")
        if outcome.get("mutant_saved_at"):
            lines.append(f"  Last mutant this run wrote: {outcome['mutant_saved_at']}")
            lines.append("  A diff of that file against the target shows the other edit alone.")
    else:
        lines += [
            f"  Putting the pre-run content back failed: {outcome.get('error')}",
            "  The file may hold a mutant.",
            f"  Pre-run content is in the backup: {outcome.get('backup')}",
            "  Running mutation_check on this file again restores it from that backup.",
        ]
    head = outcome.get("head_matched")
    if head is True:
        lines.append("  The pre-run content was identical to git HEAD, so `git diff` on the "
                     "file shows everything that is different now.")
    elif head is False:
        lines.append("  The pre-run content had uncommitted changes, so it is NOT in git. "
                     "The copy named above is the only one.")
    else:
        lines.append("  The pre-run content could not be compared with git HEAD (not a "
                     "checkout, or the file is untracked), so do not assume git has it.")
    return "\n".join(lines)


def _restore_in_flight() -> dict | None:
    """Put the target back, check that it took, and report if it did not.

    Runs ONCE per run. The `finally` in main(), the signal handler and atexit all call
    it; every call after the first returns the first one's outcome and does not touch the
    file. That matters: after the restore this tool no longer owns the file, and the
    atexit call used to compare it with the pre-run content again and write that back,
    reverting anything edited during the isolation pass.

    Returns None when nothing was armed. Signals are held for the duration, so a second
    signal cannot abandon a restore half way.
    """
    global _IN_FLIGHT, _RESTORE_OUTCOME
    with _signals_held():
        if _IN_FLIGHT is None:
            return _RESTORE_OUTCOME
        target, original = _IN_FLIGHT
        outcome = _restore(target, original)
        _IN_FLIGHT = None
        _RESTORE_OUTCOME = outcome
        if outcome["state"] != RESTORED:
            _shout(_failure_report(outcome))
    return outcome


def _on_signal(signum, _frame):
    outcome = _restore_in_flight()
    if outcome is not None and outcome["state"] == RESTORED:
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = f"signal {signum}"
        # Said out loud because a killed run prints no result, and the question after
        # killing one is always what the file holds now (2026-09-03: asked three times).
        _shout(f"mutation_check: stopped by {name}; {outcome['target']} was restored to "
               f"its pre-run content and read back to confirm.")
    raise SystemExit(128 + signum)


def arm_restore(target: Path, original: str, head_matched: bool | None = None) -> None:
    """Restore the target even if this process is SIGTERMed mid-mutation.

    THE INCIDENT THIS EXISTS FOR (2026-08-26). `finally` covers a clean exit and it
    covers SIGINT, because SIGINT raises KeyboardInterrupt in Python. It does NOT cover
    SIGTERM, which kills the interpreter outright. An overnight sweep was stopped that
    way and left four live tools mutated on disk with stranded backups -- one of them
    pipe_write.py with its archive-section test inverted, which would have appended a
    duplicate `## Archived` header to the real job-pipeline.md on the next /pipe call.
    The hazard had been written up in prose hours earlier, in the same run that then
    tripped over it again. A handler is the enforcement tier; the write-up was not.

    WHAT "RESTORE" MEANS: the content the file had when this run started, never git HEAD.
    A file with uncommitted edits is a normal thing to measure, and restoring it to HEAD
    would delete those edits. `head_matched` is carried only so a failure report can say
    whether git also holds the pre-run content.
    """
    global _IN_FLIGHT, _LAST_WRITTEN, _HEAD_MATCHED, _RESTORE_OUTCOME
    _IN_FLIGHT = (target, original)
    _LAST_WRITTEN = original
    _HEAD_MATCHED = head_matched
    _RESTORE_OUTCOME = None
    atexit.register(_restore_in_flight)
    for sig in _HELD_SIGNALS:
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass          # not on the main thread, or unsupported on this platform


def load_allowlist() -> dict:
    try:
        return json.loads(ALLOW_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# Modules every wired hook imports. Mutating one changes every live guard at once, so it
# is refused like a wired hook. Named, not computed: two modules cover the known cases.
LIVE_HOOK_IMPORTS = {"hook_runtime.py", "shell_tokens.py"}


def _is_linked_worktree(root: Path) -> bool:
    """True only when git says `root` is a LINKED worktree (git-dir != git-common-dir).
    Any failure to ask git is False, so the refusal below fails closed."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        out = subprocess.run(["git", "rev-parse", "--git-dir", "--git-common-dir"],
                             cwd=str(root), env=env, capture_output=True, text=True,
                             timeout=15).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False
    return len(out) == 2 and (root / out[0]).resolve() != (root / out[1]).resolve()


LIVE_WIRED_MESSAGE = (
    "{name} is a wired hook (or a module wired hooks import) and a Claude Code session is "
    "live (CLAUDECODE set): mutating it in place makes this session's own tool calls answer "
    "to the mutant. Commit the change, then rerun with --in-worktree: the run happens in a "
    "throwaway linked worktree made from HEAD and removed afterwards (by hand: `git worktree "
    "add --detach <scratch>/wt HEAD`, run from inside it, `git worktree remove --force "
    "<scratch>/wt`). Or pass "
    "--allow-live-wired when no session is issuing tool calls.")


def _in_repo(arg: str) -> str:
    """An absolute path inside the repo, made relative so the worktree child resolves it in
    its own checkout, not back in the live tree (review 744.F1). Outside the repo: refuse."""
    if arg.startswith("-") or not Path(arg).is_absolute():
        return arg
    try:
        return str(Path(arg).resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        raise ValueError(f"{arg} is outside the repository; a worktree run cannot reach it")


def worktree_argv(argv: list[str]) -> list[str]:
    """This run's arguments for the copy inside the worktree: everything but the flag, with
    absolute in-repo paths made relative."""
    return [_in_repo(a) for a in argv if a != "--in-worktree"]


def run_inputs(args) -> set[str]:
    """The files a run reads, as repo-relative paths: the target, its mapped tests and any
    --tests paths. Read from argparse's own result, so option order cannot hide the
    target (review 747.F1 / 749.F1)."""
    def rel(a: str) -> str:
        return _in_repo(a if Path(a).is_absolute() else str(REPO_ROOT / a))

    inputs = {rel(a) for a in [args.target, *(args.tests or [])]}
    target = REPO_ROOT / rel(args.target)
    if not args.tests and target.exists():   # explicit --tests replace the mapping
        inputs |= {rel(str(x)) for x in map_tests(target, REPO_ROOT)}
    return inputs


def uncommitted_inputs(root: Path, only: set[str] | None = None) -> list[str]:
    """Changed or untracked paths under tools/ and tests/, limited to `only` when given. A
    worktree is made from HEAD, so any of these would be missing from it and the run would
    test the wrong code. Scoped to the run's own inputs, because another session's
    uncommitted work elsewhere in tools/ is not this run's business (2026-10-27)."""
    # Scoped runs check their own inputs wherever they live (review 747.F2).
    spec = ["--", "tools", "tests"] if only is None else []
    proc = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"] + spec,
                          capture_output=True, text=True, cwd=str(root))
    paths = [line[3:].split(" -> ")[-1] for line in proc.stdout.splitlines() if line.strip()]
    return [q for q in paths if only is None or q in only]


def run_in_worktree(argv: list[str], args) -> int:
    """Run this tool in a throwaway linked worktree from HEAD, then remove it (2026-10-27:
    the refusal used to print these steps for the caller to do by hand)."""
    try:
        child_argv, inputs = worktree_argv(argv), run_inputs(args)
    except ValueError as e:
        print(json.dumps({"status": "error", "code": "path_outside_repo", "message": str(e)}))
        return 1
    dirty = uncommitted_inputs(REPO_ROOT, only=inputs)
    if dirty:
        print(json.dumps({"status": "error", "code": "uncommitted_inputs",
                          "message": "commit first: a worktree is made from HEAD, so these "
                                     "changes would be missing from it",
                          "paths": dirty}))
        return 1
    scratch = Path(tempfile.mkdtemp(prefix="mutation-wt-"))
    wt = scratch / "wt"
    add = subprocess.run(["git", "worktree", "add", "--detach", str(wt), "HEAD"],
                         capture_output=True, text=True, cwd=str(REPO_ROOT))
    if add.returncode != 0:
        print(json.dumps({"status": "error", "code": "worktree_add_failed",
                          "message": add.stderr.strip()}))
        return 1
    try:
        return subprocess.run([sys.executable, str(wt / "tools" / "mutation_check.py")]
                              + child_argv, cwd=str(wt)).returncode
    finally:
        rm = subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                            capture_output=True, text=True, cwd=str(REPO_ROOT))
        shutil.rmtree(scratch, ignore_errors=True)
        # If remove failed, the directory is gone now; prune drops git's record of it
        # (review 744.F4), and a failure of either is reported, not swallowed (747.F4).
        pr = subprocess.run(["git", "worktree", "prune"], capture_output=True, text=True,
                            cwd=str(REPO_ROOT))
        for step, res in (("worktree remove", rm), ("worktree prune", pr)):
            if res.returncode != 0:
                print(f"mutation_check: {step} failed for {wt}: {res.stderr.strip()}",
                      file=sys.stderr)


def live_wired_refusal(target: Path, args) -> dict | None:
    """Refuse to rewrite a live guard while a Claude Code session is issuing tool calls.

    This tool mutates its target IN PLACE, so a wired hook (or a module wired hooks import)
    judges the session's own Bash/Write/Edit calls with whatever mutant is on disk. Fired
    3x through this tool directly (2026-09-07, 10-01, 10-22); mutation_sweep's own
    CLAUDECODE skip never saw those runs. CLAUDECODE is set in every Claude Code tool call
    and absent under launchd, so unattended runs are untouched. --list rewrites nothing.
    """
    if args.list or not os.environ.get("CLAUDECODE"):
        return None
    if target.name == "hook_runtime.py" and not args.tests:
        return {"code": "tests_required",
                "message": "pass --tests for hook_runtime.py: every test file maps to it, "
                           "and an unscoped run cost 16 min per mutant on 2026-10-22."}
    if args.allow_live_wired or _is_linked_worktree(REPO_ROOT):
        return None
    import mutation_sweep  # lazy: mutation_sweep imports this module at its top
    try:
        wired = mutation_sweep.currently_wired(REPO_ROOT)
    except Exception:
        wired = {target.name}   # an unreadable registry is not an empty one
    if target.name not in wired | LIVE_HOOK_IMPORTS:
        return None
    return {"code": "live_wired_target_refused",
            "message": LIVE_WIRED_MESSAGE.format(name=target.name)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="python file to mutate (e.g. tools/foo.py)")
    ap.add_argument("--tests", nargs="*", default=None,
                    help="test files (default: auto-mapped by name + import reference)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true", help="enumerate mutants, run nothing")
    ap.add_argument("--isolation", action="store_true",
                    help="also run each mapped test file ALONE (catches suites that only "
                         "pass because another file polluted sys.path during collection)")
    ap.add_argument("--strict", action="store_true",
                    help="also FAIL on weak kills (mutants killed without any assertion "
                         "firing) and on assertion-free / tautological tests. Off by "
                         "default: some code legitimately signals by raising, and a noisy "
                         "gate gets disabled, which is worse than no gate.")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--max-hours", type=float, default=None,
                    help="stop starting new mutants after this many hours; the rest are "
                         "reported as unfinished (status 'incomplete'), never as killed")
    ap.add_argument("--wait", type=float, default=DEFAULT_RUN_LOCK_WAIT,
                    help="seconds to wait for the tree lock before giving up "
                         "(0 = fail immediately, the pre-2026-09-07 behaviour)")
    ap.add_argument("--allow-live-wired", action="store_true",
                    help="mutate a wired hook in place even though CLAUDECODE is set. Only "
                         "when no session is issuing tool calls; prefer a linked worktree.")
    ap.add_argument("--in-worktree", action="store_true",
                    help="run in a throwaway linked worktree made from HEAD and removed "
                         "afterwards; the way to test a wired hook during a live session. "
                         "Refuses if the target or its mapped tests have uncommitted changes.")
    args = ap.parse_args()
    if args.in_worktree and not _is_linked_worktree(REPO_ROOT):
        return run_in_worktree(sys.argv[1:], args)

    target = Path(args.target)
    if not target.is_absolute():
        target = REPO_ROOT / target
    if not target.exists():
        print(json.dumps({"status": "error", "message": f"no such file: {target}"}))
        return 1

    if target.resolve() == SELF:
        print(json.dumps({"status": "error", "code": "self_mutation_refused",
                          "message": "refusing to mutate mutation_check.py itself. A "
                                     "mutant that breaks the restore logic corrupts "
                                     "whatever its own test suite touches -- this "
                                     "happened on 2026-08-19 and corrupted two "
                                     "unrelated files. Test this tool with "
                                     "tests/scripts/test_mutation_check.py instead."}))
        return 1

    refusal = live_wired_refusal(target, args)
    if refusal:
        print(json.dumps({"status": "error", **refusal}))
        return 1

    # Refuse to start while another run owns the tree. Before prune_orphans and
    # recover_if_stranded deliberately: both TOUCH the shared backup store, and doing that
    # under a live run from another process is how a file ends up holding another mutant's
    # source. The lock lives in the backup dir, which is outside the repo, so it can never
    # be committed and never appears in `git status` as mystery noise.
    acquired, holder = acquire_run_lock(wait=args.wait)
    if not acquired:
        print(json.dumps({
            "status": "error", "code": "run_in_flight",
            "message": f"another mutation run owns the tree ({holder}); gave up after "
                       f"{args.wait:.0f}s. "
                       f"Two runs rewrite the same tools/*.py and restore from each other's "
                       f"backups. To wait for it, rerun with `--wait <seconds>`, which blocks on "
                       f"the tree lock itself rather than on a process-name grep."}))
        return 1

    # --list READS THE TREE. It holds the lock above, because the target is unstable while
    # another run rewrites it, and then changes neither the target nor any backup: no
    # pruning, no stranded-file recovery, no backup, no atexit restore. Taking the lock
    # does create the store directory and this tree's lock file, so it is not write-free. It used to fall through all four before listing, so
    # listing a tool "recovered" it -- a backup that differed from the target was written
    # over the target and deleted, which under a live run is the run's mutant replaced
    # behind its back and its only on-disk original thrown away (2026-10-05/06).
    recovered = None
    if not args.list:
        # Unrestorable leftovers from trees that no longer exist. The store is shared and
        # outside every tree it serves, so nothing else would ever clear them.
        prune_orphans()
        prune_stale_locks()
        recovered = recover_if_stranded(target)

    original = target.read_text(encoding="utf-8")
    head_matched = None
    if not args.list:
        # Asked BEFORE the backup exists, so the git call adds no time during which a
        # kill would leave a backup behind.
        head_matched = matches_head(target, original)
        backup_dir().mkdir(parents=True, exist_ok=True)
        backup_path(target).write_text(original, encoding="utf-8")
        arm_restore(target, original, head_matched)
        if head_matched is False:
            print(f"mutation_check: note: {target} differs from git HEAD before this run "
                  f"(uncommitted edits, or a mutant an earlier run left behind). This run "
                  f"restores the content it started from, not HEAD.",
                  file=sys.stderr, flush=True)
    src_lines = original.splitlines()
    try:
        tree = ast.parse(original)
    except SyntaxError as e:
        print(json.dumps({"status": "error", "message": f"unparseable: {e}"}))
        return 1

    rel = str(target.relative_to(REPO_ROOT))
    test_files = ([Path(t) for t in args.tests] if args.tests else map_tests(target))
    if not test_files:
        print(json.dumps({"status": "error", "code": "no_tests",
                          "message": f"no test files map to {rel}; a file with no tests "
                                     "cannot be mutation-checked -- write one first"}))
        return 1

    points = enumerate_mutants(tree)
    allow = load_allowlist()

    catalog = []
    for lineno, col, op in points:
        line = src_lines[lineno - 1] if 0 < lineno <= len(src_lines) else ""
        func = enclosing_function(tree, lineno)
        catalog.append({"line": lineno, "col": col, "op": op, "func": func,
                        "key": mutant_key(rel, func, op, line),
                        "source": line.strip()[:100]})

    if args.list:
        print(json.dumps({"status": "ok", "target": rel, "mutants": len(catalog),
                          "tests": [str(t) for t in test_files], "catalog": catalog},
                         indent=2))
        return 0

    # Baseline: the suite must be green BEFORE mutating, or every result is meaningless.
    baseline_details: dict = {}
    baseline_started = time.monotonic()
    baseline_pass, _ = run_tests(test_files, args.timeout, details=baseline_details)
    baseline_seconds = round(time.monotonic() - baseline_started, 1)
    if not baseline_pass:
        timed_out = baseline_details.get("timed_out")
        print(json.dumps({"status": "error",
                          "code": "baseline_timeout" if timed_out else "baseline_red",
                          "message": (f"mapped tests exceeded the {args.timeout}s timeout on "
                                      f"unmutated source; this is NOT a failing suite")
                                     if timed_out else
                                     "mapped tests fail on unmutated source; fix that first",
                          "failed_tests": baseline_details.get("failed", []),
                          "output_tail": baseline_details.get("output", ""),
                          "tests": [str(t) for t in test_files]}))
        return 1

    survived, killed, allowed, weak = [], 0, [], []
    unclassified, timed_out = [], []
    diverged = False
    fast, probe_seconds, full_reruns = None, 0.0, 0
    # A5 (plan, output/mutation-parallel-102826/plan.md): two runs went 8+ hours with no
    # output (2026-10-25, 2026-10-27). A progress line every PROGRESS_EVERY mutants, a
    # --max-hours stop, and the total wall time in the result.
    run_started, done, unfinished = time.monotonic(), 0, []
    try:
        # Two tiers. Each mutant runs first against the files that execute the target;
        # only a mutant that survives them is run against every mapped file. A kill in
        # the first tier is a kill in the full set, and a survivor is only reported after
        # the full set has had its chance, so the verdicts match a full run.
        probe_started = time.monotonic()
        try:
            fast = files_that_run(target, original, test_files, args.timeout)
        except TargetDiverged:
            diverged = True
        probe_seconds = round(time.monotonic() - probe_started, 1)
        tiered = bool(fast) and len(fast) < len(test_files)
        for n, entry in enumerate([] if diverged else catalog):
            if args.max_hours is not None and \
                    time.monotonic() - run_started > args.max_hours * 3600:
                unfinished = catalog[n:]
                break
            if done and done % PROGRESS_EVERY == 0:
                print(f"mutation_check: {done}/{len(catalog)} mutants run, {killed} killed, "
                      f"{len(survived)} survived, {len(timed_out)} timed out, "
                      f"{round(time.monotonic() - run_started)}s", file=sys.stderr, flush=True)
            done += 1
            key = entry["key"]
            mut = _Mutator((entry["line"], entry["col"], entry["op"]))
            new_tree = mut.visit(ast.parse(original))
            if not mut.applied:
                continue
            ast.fix_missing_locations(new_tree)
            try:
                mutated = ast.unparse(new_tree)
            except Exception:
                continue
            if mutated == original:
                continue
            try:
                write_mutant(target, original, mutated)
            except TargetDiverged:
                # Someone else's content is on disk. Stop: the mutants measured so far
                # ran against a file that was being edited, and the next write would
                # delete that edit. The restore below reports it and leaves it alone.
                diverged = True
                break
            # `target` is passed ONLY here, never on the baseline run above: at baseline
            # the file is unmutated, so a ratchet firing on it means real corruption and
            # must still turn the run baseline_red.
            passed, kind = run_tests(fast if tiered else test_files, args.timeout, target)
            if passed and tiered:
                full_reruns += 1
                passed, kind = run_tests(test_files, args.timeout, target)
            if passed:
                if key in allow and str(allow[key]).strip():
                    allowed.append({**entry, "reason": allow[key]})
                else:
                    survived.append(entry)
            elif kind == "timeout":
                # UNMEASURED, never killed (2026-10-06). The limit is one number shared by
                # the baseline and every mutant, and night runs measure about twice as slow
                # as day runs, so a mutant whose tests only ran long was being scored as a
                # kill. A false kill is the one error this tool must not make: it certifies
                # that a suite protects a behaviour it does not. A mutant that really does
                # hang is named here instead, where someone can read it.
                timed_out.append(entry)
            else:
                killed += 1
                # Killed WITHOUT an assertion firing: the suite noticed the mutation
                # only by crashing. Observability without assertion.
                if kind == "error":
                    weak.append({**entry, "killed_by": kind})
                elif kind == "unknown":
                    unclassified.append({**entry, "killed_by": kind})
    finally:
        outcome = _restore_in_flight()

    if diverged or outcome is None or outcome["state"] != RESTORED:
        # No result is printed. A partial count from a run whose target was being edited
        # would read as a measurement.
        state = (outcome or {}).get("state")
        print(json.dumps({
            "status": "error",
            "code": "target_not_restored" if state == FAILED else "target_diverged",
            "message": ("the target was edited by another process during the run and was "
                        "left as found; the results are discarded. See stderr for where "
                        "the pre-run content was saved."
                        if state != FAILED else
                        "the target could not be put back to its pre-run content. See "
                        "stderr; the backup was kept."),
            "target": rel,
            "restore_state": state,
            "original_saved_at": (outcome or {}).get("original_saved_at"),
            "mutant_saved_at": (outcome or {}).get("mutant_saved_at"),
            "backup": (outcome or {}).get("backup"),
            "target_matched_head_at_start": head_matched,
            "tests": [str(t) for t in test_files]}))
        return 1

    audit = audit_test_quality(test_files)
    result = {
        "recovered_stranded_file": recovered,
        # "ok" claims every mutant got a verdict. One that timed out did not, so a run
        # with timeouts and no survivors is its own status rather than a clean one.
        # Inline, not behind a helper: tests/scripts/test_coverage_field_has_fail_closed_
        # consumer.py reads the string arms of "status" here to know what this tool emits.
        # "incomplete" outranks "survivors": a run stopped by --max-hours has not looked at
        # every mutant, so its survivor list is a lower bound (Codex commit-3 review F2).
        # "ok" claims every mutant got a verdict, so timeouts are their own status.
        "status": ("incomplete" if unfinished
                   else "survivors" if survived
                   else "mutants_timed_out" if timed_out else "ok"),
        "target": rel,
        # True means: put back to the pre-run content AND read back. Not inferred.
        "target_restored": True,
        "target_matched_head_at_start": head_matched,
        "tests": [str(t) for t in test_files],
        "killed": killed,
        "survived": len(survived),
        "timed_out": len(timed_out),
        "timed_out_mutants": timed_out,
        "timeout": args.timeout,
        # The unmutated suite's own duration: the margin every mutant run has under the
        # same limit, and the only measurement of how much slower a night run is.
        "baseline_seconds": baseline_seconds,
        "run_seconds": round(time.monotonic() - run_started, 1),
        "max_hours": args.max_hours,
        "unfinished": len(unfinished),
        "unfinished_mutants": [e["key"] for e in unfinished],
        # The first tier (see files_that_run). null when the target could not be
        # instrumented, in which case every mutant ran against every mapped file.
        "fast_tests": None if fast is None else [str(t) for t in fast],
        "probe_seconds": probe_seconds,
        "full_reruns": full_reruns,
        "allowlisted": len(allowed),
        "survivors": survived,
        "allowlisted_detail": allowed,
        # Reported, not failed on by default -- see run_tests(). --strict promotes
        # these to failures for a suite you want held to the higher bar.
        "weak_kills": weak,
        "weak_kill_count": len(weak),
        "unclassified_kills": len(unclassified),
        "assertion_free_tests": audit["assertion_free"],
        "tautological_assertions": audit["tautological"],
    }

    if args.isolation:
        lonely, refused, timed_out_alone = [], [], []
        for t in test_files:
            cmd = [sys.executable, "-m", "pytest", "-q", "--no-header", str(t)]
            # PYTHONDONTWRITEBYTECODE for the same reason as run_tests: this pass must not
            # repopulate the .pyc cache that the mutation loop depends on being absent.
            # LOCK_HELD_ENV for the same reason as run_tests: the lock is still held here,
            # and this pass builds its own environment, so it has to say so itself.
            # BOUNDED, and reaped by process group (2026-10-06). This was a bare
            # subprocess.run with no timeout: one file that hangs when run alone consumed
            # the rest of the sweep's 5-hour allowance for the tool, and a plain timeout
            # would have orphaned whatever pytest had started (the 2026-09-06 class).
            try:
                r = _run_reaping_descendants(
                    # _test_env_base, not os.environ: the same worktree scrub as the
                    # mutation runs (Codex commit-3 review F1).
                    cmd, {**_test_env_base(), "PYTHONDONTWRITEBYTECODE": "1", LOCK_HELD_ENV: "1"},
                    args.timeout)
            except subprocess.TimeoutExpired:
                # Nothing was learned about this file. Unmeasured, never "fails alone".
                timed_out_alone.append(str(t))
                continue
            if r.returncode == CONFTEST_REFUSAL:
                # tests/conftest.py refused to run because a *.mutation_backup exists
                # somewhere in the tree -- a sibling run's wreckage, not a property of
                # this test file. Recording it as an isolation failure is a false
                # accusation, and on 2026-08-26 it produced 41 of them from one stranded
                # backup. Unmeasured is not the same as failing.
                refused.append(str(t))
            elif r.returncode != 0:
                lonely.append(str(t))
        result["isolation_failures"] = lonely
        if timed_out_alone:
            result["isolation_timed_out"] = timed_out_alone
            result["status"] = "isolation_unmeasured"
        if refused:
            result["isolation_refused"] = refused
            result["status"] = "isolation_unmeasured"
        if lonely:
            result["status"] = "isolation_failed"
        if unfinished:
            # Codex commit-3 round 2 F1: isolation findings must not hide that the run
            # stopped early; they stay in their own fields.
            result["status"] = "incomplete"

    # An unfinished run is not a pass: exit 2 so nothing reads it as green.
    hard_fail = bool(survived) or bool(result.get("isolation_failures")) or bool(unfinished)
    if args.strict:
        hard_fail = hard_fail or bool(weak) or bool(audit["assertion_free"]) \
            or bool(audit["tautological"])
        # Set BEFORE printing. It was set after the print, so a strict failure printed
        # "status": "ok" while exiting 2 (simplify.md #8, found 2026-10-28).
        if hard_fail and result["status"] == "ok":
            result["status"] = "strict_failed"
    print(json.dumps(result, indent=2))
    return 2 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
