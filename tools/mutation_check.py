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
    1  usage / setup error (no tests mapped, unparseable target, ...)
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# MUTATION_REPO_ROOT overrides for tests, mirroring the PII_REPO_ROOT /
# SESSION_REPO_ROOT / GMAIL_LABELS_CONF_PATH seams elsewhere in tools/. Without
# it this tool cannot be exercised against a fixture repo, which would leave the
# gate that demands mutation evidence with none of its own.
REPO_ROOT = Path(os.environ.get("MUTATION_REPO_ROOT",
                                str(Path(__file__).resolve().parent.parent)))
ALLOW_FILE = REPO_ROOT / "tools" / "mutation-allow.json"
DEFAULT_TIMEOUT = 300


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

def map_tests(target: Path) -> list[Path]:
    """Test files that plausibly cover `target`, by name and by import reference."""
    stem = target.stem
    tests_dir = REPO_ROOT / "tests"
    if not tests_dir.exists():
        return []
    hits = set()
    direct = tests_dir / "scripts" / f"test_{stem}.py"
    if direct.exists():
        hits.add(direct)
    for path in tests_dir.rglob("test_*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if stem in text:
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
_ASSERTION_KINDS = {"AssertionError", "Failed"}


def run_tests(test_files: list[Path], timeout: int) -> tuple[bool, str]:
    """(passed, kill_kind).

    kill_kind classifies HOW the suite noticed a mutation, which is the difference
    between a test that ASSERTS something about output and one that merely CRASHES:

      "assertion"  at least one failure was an AssertionError -> a test checked a value
      "error"      failures were all non-assertion (ImportError, TypeError, collection
                   error, ...) -> the mutation was OBSERVED but nothing was asserted
      "timeout"    the run hung; a detected behavior change, but nothing was asserted
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
    env = {**os.environ, "MUTATION_CHECK_ACTIVE": "1"}
    try:
        r = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True,
                           text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    if r.returncode == 0:
        return True, ""
    out = r.stdout + r.stderr
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


def backup_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".mutation_backup")


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


def assert_intact(target: Path, original: str) -> None:
    """Hard-abort if the target does not match its pre-run content.

    Belt and braces over the `finally`: the finally itself can be mutated away when the
    target IS this tool, and a silent mismatch means corrupted source on disk.
    """
    if target.read_text(encoding="utf-8") != original:
        target.write_text(original, encoding="utf-8")
        raise SystemExit("mutation_check: target did not match after restore; "
                         "rewrote it from the in-memory original. Verify with git diff.")


def load_allowlist() -> dict:
    try:
        return json.loads(ALLOW_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


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
    args = ap.parse_args()

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

    recovered = recover_if_stranded(target)

    original = target.read_text(encoding="utf-8")
    backup_path(target).write_text(original, encoding="utf-8")
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
    baseline_pass, _ = run_tests(test_files, args.timeout)
    if not baseline_pass:
        print(json.dumps({"status": "error", "code": "baseline_red",
                          "message": "mapped tests fail on unmutated source; fix that first",
                          "tests": [str(t) for t in test_files]}))
        return 1

    survived, killed, allowed, weak = [], 0, [], []
    unclassified = []
    try:
        for entry in catalog:
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
            target.write_text(mutated, encoding="utf-8")
            passed, kind = run_tests(test_files, args.timeout)
            if passed:
                if key in allow and str(allow[key]).strip():
                    allowed.append({**entry, "reason": allow[key]})
                else:
                    survived.append(entry)
            else:
                killed += 1
                # Killed WITHOUT an assertion firing: the suite noticed the mutation
                # only by crashing or hanging. Observability without assertion.
                if kind in ("error", "timeout"):
                    weak.append({**entry, "killed_by": kind})
                elif kind == "unknown":
                    unclassified.append({**entry, "killed_by": kind})
    finally:
        target.write_text(original, encoding="utf-8")
        assert_intact(target, original)
        try:
            backup_path(target).unlink()
        except OSError:
            pass

    audit = audit_test_quality(test_files)
    result = {
        "recovered_stranded_file": recovered,
        "status": "ok" if not survived else "survivors",
        "target": rel,
        "tests": [str(t) for t in test_files],
        "killed": killed,
        "survived": len(survived),
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
        lonely = []
        for t in test_files:
            cmd = [sys.executable, "-m", "pytest", "-q", "--no-header", str(t)]
            r = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            if r.returncode != 0:
                lonely.append(str(t))
        result["isolation_failures"] = lonely
        if lonely:
            result["status"] = "isolation_failed"

    print(json.dumps(result, indent=2))
    hard_fail = bool(survived) or bool(result.get("isolation_failures"))
    if args.strict:
        hard_fail = hard_fail or bool(weak) or bool(audit["assertion_free"]) \
            or bool(audit["tautological"])
        if hard_fail and result["status"] == "ok":
            result["status"] = "strict_failed"
    return 2 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
