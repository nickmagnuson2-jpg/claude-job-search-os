"""A coverage or middle-state field needs a consumer that fails closed on it.

WHY THIS EXISTS. Making a check three-state is the easy half. Every surface that triggers
work reads one boolean, so the coverage dimension is computed and then dropped one layer
downstream. Three fires on 2026-09-21, each with a correct, tested producer:

  1. A module's own docstring recipe exited on `clean`, which is true when every filter
     CANNOT_RUNs; and a cron predicate enumerated four of five gap buckets.
  2. `promoted: "yes -- <domain-scoped prose>"` where the vocabulary already had
     `partial`. The collapse was at the WRITE end: nothing validated the token.
  3. A workstream runner classified rows `typed | changed | unchanged | error`, and its
     only consumer filtered on one derived boolean, so `typed` rows could never surface.

WHAT THIS ENFORCES, MECHANICALLY.

  A. Every tools/*.py that emits a coverage-style key, or assigns a "did not measure"
     value to `status`/`state`/`verdict`, has a REGISTRY row. A new producer cannot be
     added silently, and a row whose producer is gone fails as stale.
  B. Every registered consumer still CONTAINS A BRANCH on the field, checked on the AST
     (a conditional that reads it), not by searching for the name. A consumer that is a
     skill step can only be checked as text, and is labelled as such below.
  C. A field with no fail-closed consumer is recorded in the registry as `Gap(reason)`.
     The number of gaps has a ceiling, so the list can shrink and cannot grow unnoticed.
  D. Write end: every `VALID_*` vocabulary declared in tools/ is used in a validation
     expression, and the tokens the `promoted` readers branch on are in the vocabulary
     its validator enforces.

WHAT THIS DOES NOT PROVE. (B) shows a branch exists, not that the branch does the right
thing; behaviour stays with each tool's own tests. A `Text` consumer proves a sentence is
present in a skill file, not that a session follows it. The detector reads dict-literal
keys and `x["key"] = ...` assignments only: a field built by `dict(key=...)`, by a
dataclass, or under a name outside COVERAGE_KEY_RE is invisible to it. `skipped_*`
counters are deliberately out of scope (they count filtered rows, not unread ones).
"""
from __future__ import annotations

import ast
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import REPO_ROOT

# --------------------------------------------------------------------------- detector

COVERAGE_KEY_RE = re.compile(
    r"^(fully_covered|complete|incomplete|partial|skipped|truncated|degraded)$"
    r"|coverage|truncated$|_no_verdict$|^unreadable|unmeasured|uncountable")
STATE_KEYS = frozenset({"status", "state", "verdict"})
MIDDLE_VALUE_RE = re.compile(
    r"skip|partial|cannot_run|unmeasured|unaudited|timed?_?out|^typed$|parse_error"
    r"|no_pattern|unreadable|unknown|truncat|degrad", re.IGNORECASE)


def _string_arms(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _string_arms(node.body) + _string_arms(node.orelse)
    return []


def emitted_fields(path: Path) -> dict[tuple[str, str, str | None], list[int]]:
    """(file, key, middle value or None) -> line numbers, for one tool."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pairs: list[tuple[str, ast.AST, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            pairs += [(k.value, v, k.lineno) for k, v in zip(node.keys, node.values)
                      if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        elif isinstance(node, ast.Assign):
            pairs += [(t.slice.value, node.value, t.lineno) for t in node.targets
                      if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                      and isinstance(t.slice.value, str)]
    out: dict[tuple[str, str, str | None], list[int]] = {}
    for key, value, line in pairs:
        if COVERAGE_KEY_RE.search(key):
            out.setdefault((path.name, key, None), []).append(line)
        if key in STATE_KEYS:
            for arm in _string_arms(value):
                if MIDDLE_VALUE_RE.search(arm):
                    out.setdefault((path.name, key, arm), []).append(line)
    return out


def all_emitted(root: Path) -> dict[tuple[str, str, str | None], list[int]]:
    found: dict[tuple[str, str, str | None], list[int]] = {}
    tools = sorted((root / "tools").glob("*.py"))
    assert tools, f"no tools/*.py under {root}: an empty scan is not a clean result"
    for path in tools:
        found.update(emitted_fields(path))
    return found


# --------------------------------------------------------------------------- consumers

@dataclass(frozen=True)
class Branch:
    """A conditional in `file` (inside `func`, when given) that reads `reads`.

    `reads` is a dict key, an attribute, or a local name the field is derived from.
    `literal`, when given, must appear as a string constant in the same conditional.
    `closes="exit"` additionally requires the conditional to govern a return, a raise
    or a sys.exit, which is what makes an exit code a consumer.
    """
    file: str
    func: str | None
    reads: str
    literal: str | None = None
    closes: str | None = None


@dataclass(frozen=True)
class Text:
    """A line in a non-Python consumer (a skill step, a docstring recipe).

    Presence only. `forbid` is a pattern that must NOT match any line.
    """
    file: str
    pattern: str
    forbid: str | None = None


@dataclass(frozen=True)
class Gap:
    """No consumer fails closed on this field. A finding, not an approval."""
    reason: str


@dataclass(frozen=True)
class NotCoverage:
    """The key name matches the detector and the field is not a measurement signal."""
    reason: str


def _reads(node: ast.AST, name: str) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                and n.slice.value == name:
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "get" and n.args \
                and isinstance(n.args[0], ast.Constant) and n.args[0].value == name:
            return True
        if isinstance(n, ast.Attribute) and n.attr == name:
            return True
        if isinstance(n, ast.Name) and n.id == name:
            return True
    return False


def _has_literal(node: ast.AST, literal: str) -> bool:
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and literal in n.value
               for n in ast.walk(node))


def _is_exit(node: ast.AST) -> bool:
    if isinstance(node, (ast.Return, ast.Raise)):
        return True
    if isinstance(node, ast.Call):
        f = node.func
        return (isinstance(f, ast.Attribute) and f.attr == "exit") or \
               (isinstance(f, ast.Name) and f.id in ("exit", "SystemExit"))
    return False


def _conditionals(scope: ast.AST):
    """Yield (test expression, governs an exit) for every conditional in scope."""
    for n in ast.walk(scope):
        if isinstance(n, ast.If):
            governs = any(_is_exit(x) for part in (n.body, n.orelse)
                          for stmt in part for x in ast.walk(stmt))
            yield n.test, governs
        elif isinstance(n, (ast.While, ast.Assert)):
            yield n.test, isinstance(n, ast.Assert)
        elif isinstance(n, ast.comprehension):
            for cond in n.ifs:
                yield cond, False
    # A conditional EXPRESSION governs an exit when it sits inside one.
    exits = [x for x in ast.walk(scope) if _is_exit(x)]
    inside_exit = {id(y) for x in exits for y in ast.walk(x)}
    for n in ast.walk(scope):
        if isinstance(n, ast.IfExp):
            yield n.test, id(n) in inside_exit
        elif isinstance(n, (ast.Compare, ast.BoolOp)):
            yield n, id(n) in inside_exit


def branch_problem(spec: Branch, root: Path) -> str | None:
    """None when the consumer still branches on the field; otherwise why not."""
    path = root / "tools" / spec.file
    if not path.is_file():
        return f"consumer file tools/{spec.file} does not exist"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scopes: list[ast.AST] = [tree]
    if spec.func is not None:
        scopes = [n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == spec.func]
        if not scopes:
            return f"tools/{spec.file} has no function {spec.func}()"
    where = f"tools/{spec.file}" + (f":{spec.func}()" if spec.func else "")
    seen_read = False
    for scope in scopes:
        for test, governs_exit in _conditionals(scope):
            if not _reads(test, spec.reads):
                continue
            seen_read = True
            if spec.literal is not None and not _has_literal(test, spec.literal):
                continue
            if spec.closes == "exit" and not governs_exit:
                continue
            return None
    if not seen_read:
        return f"{where} has no conditional that reads `{spec.reads}`"
    if spec.literal is not None and spec.closes != "exit":
        return f"{where} reads `{spec.reads}` but never compares it with {spec.literal!r}"
    return (f"{where} reads `{spec.reads}` in a conditional, but none"
            + (f" naming {spec.literal!r}" if spec.literal else "")
            + " governs a return, raise or sys.exit")


def text_problem(spec: Text, root: Path) -> str | None:
    path = root / spec.file
    if not path.is_file():
        return f"consumer file {spec.file} does not exist"
    lines = path.read_text(encoding="utf-8").splitlines()
    if not any(re.search(spec.pattern, ln) for ln in lines):
        return f"{spec.file} has no line matching {spec.pattern!r}"
    if spec.forbid:
        bad = [i + 1 for i, ln in enumerate(lines) if re.search(spec.forbid, ln)]
        if bad:
            return f"{spec.file}:{bad[0]} matches the forbidden pattern {spec.forbid!r}"
    return None


def consumer_problem(spec, root: Path) -> str | None:
    return branch_problem(spec, root) if isinstance(spec, Branch) else text_problem(spec, root)


# --------------------------------------------------------------------------- registry
#
# (producer file, key, middle value or None) -> consumers, a Gap, or NotCoverage.
# A status/state field gets ONE ROW PER MIDDLE VALUE: fire 3 was a field whose `error`
# value had a consumer and whose `typed` value had none, which a per-field row hides.

STANDUP = ".claude/skills/standup/SKILL.md"
_UNDER_FLOOR = "exit 2 from main() when fewer than the minimum number of checks executed"
_FULLY_COVERED_GAP = (
    "exit code reads the coverage FLOOR, not this field: a run with CANNOT_RUN rows above "
    "the floor exits 0 and no code anywhere branches on fully_covered (printed only)")
_VALIDATE_LOCAL_GAP = (
    "--validate-local returns exit 0 with status SKIPPED when the gitignored validators "
    "file is absent or has no cases, and nothing invokes the mode or reads the status")

REGISTRY: dict[tuple[str, str, str | None], object] = {
    # --- fire 1: clean vs fully_covered, and the family gap buckets ------------------
    ("population_probe.py", "fully_covered", None): [
        # A library with no caller in tools/. Its consumer is the recipe a caller copies.
        Text("tools/population_probe.py",
             r"^\s*sys\.exit\(0 if report\.fully_covered else 2\)",
             forbid=r"^\s*sys\.exit\(0 if report\.clean\b")],
    ("scan_promotion_candidates.py", "fully_covered", None): [
        Branch("scan_promotion_candidates.py", "main", reads="fully_covered")],
    ("check_frame_integrity.py", "under_coverage_floor", None): [
        Branch("check_frame_integrity.py", "main", reads="under_floor", closes="exit")],
    ("check_deck_geometry.py", "under_coverage_floor", None): [
        Branch("check_deck_geometry.py", "main", reads="under_floor", closes="exit")],
    ("check_deck_craft.py", "under_coverage_floor", None): [
        Branch("check_deck_craft.py", "main", reads="under_floor", closes="exit")],
    ("check_frame_integrity.py", "fully_covered", None): Gap(_FULLY_COVERED_GAP),
    ("check_deck_geometry.py", "fully_covered", None): Gap(_FULLY_COVERED_GAP),
    ("check_deck_craft.py", "fully_covered", None): Gap(_FULLY_COVERED_GAP),
    ("frame_write.py", "fully_covered", None): Gap(
        "copies the checker's value into its own JSON and the frame's check_log; "
        "never branches on it"),

    # --- fire 2 lives at the write end; see the VALID_* tests below ------------------
    ("scan_promotion_candidates.py", "partial", None): [
        Branch("attention.py", "queue_promotion", reads="partial"),
        Text(STANDUP, r"`partial` is called out separately")],
    ("scan_promotion_candidates.py", "schema_coverage", None): Gap(
        "printed as a Signal health line and passed to attention.py as a denominator; "
        "no threshold, so a scan that can see few of the rules still reports normally"),

    # --- fire 3: the workstream runner ----------------------------------------------
    ("workstream_state.py", "state", "typed"): Gap(
        "a typed row sets surfaces=False and main() exits non-zero on `error` only; the "
        "standup step filters on `surfaces`, so a workstream with no probe never surfaces. "
        "Whether typed rows should print daily is a product decision, not a code fix"),

    # --- the attention queue ---------------------------------------------------------
    ("attention.py", "status", "SKIPPED"): [
        Branch("attention.py", "build", reads="status", literal="ok")],
    ("attention.py", "complete", None): [
        Branch("attention.py", "render", reads="complete"),
        Text(STANDUP, r"`complete: false` is surfaced, always")],
    ("attention.py", "partial", None): [
        Text(STANDUP, r"`partial` is called out separately")],
    ("attention.py", "coverage_pct", None): Gap(
        "emitted under detail and read by nothing in tools/ or the standup step"),
    ("conversations_metric.py", "complete", None): [
        Text(STANDUP, r"If `complete: false`, append")],
    ("conversations_metric.py", "unreadable_sources", None): [
        Text(STANDUP, r"unreadable: <unreadable_sources>")],

    # --- writers that report a partial apply -----------------------------------------
    ("apply_memory_verdicts.py", "status", "partial"): [
        Branch("apply_memory_verdicts.py", "main", reads="failed", closes="exit")],
    ("backfill_memory_schema.py", "status", "partial"): [
        Branch("backfill_memory_schema.py", "main", reads="failed", closes="exit")],

    # --- validators ------------------------------------------------------------------
    ("check_doc_precedence.py", "status", "PARSE_ERROR"): [
        Branch("check_doc_precedence.py", "validate_local", reads="status",
               literal="PARSE_ERROR")],
    ("check_doc_precedence.py", "status", "SKIPPED"): Gap(
        _VALIDATE_LOCAL_GAP + "; the default mode also exits 0 with SKIPPED when any of "
        "its three gitignored inputs is absent"),
    ("check_prep_doc.py", "status", "SKIPPED"): Gap(_VALIDATE_LOCAL_GAP),
    ("outreach_status.py", "status", "SKIPPED"): Gap(_VALIDATE_LOCAL_GAP),
    ("transcript_exclusions.py", "status", "SKIPPED"): Gap(_VALIDATE_LOCAL_GAP),
    ("detector_probe.py", "status", "no_pattern_found"): [
        # The consumer reads the sibling boolean, which is False for every non-fired state.
        Branch("detector_run.py", "validate", reads="fired")],

    # --- mutation tooling ------------------------------------------------------------
    ("mutation_check.py", "status", "isolation_unmeasured"): [
        Branch("mutation_sweep.py", "partially_measured_tools", reads="status",
               literal="isolation_unmeasured")],
    ("mutation_check.py", "status", "mutants_timed_out"): [
        Branch("mutation_sweep.py", "partially_measured_tools", reads="status",
               literal="mutants_timed_out")],
    ("mutation_sweep.py", "status", "UNAUDITED_ERROR"): [
        Branch("mutation_state.py", "is_engine_failure", reads="status",
               literal="UNAUDITED", closes="exit")],
    ("mutation_sweep.py", "status", "UNAUDITED_TIMEOUT"): [
        Branch("mutation_state.py", "is_engine_failure", reads="status",
               literal="UNAUDITED", closes="exit")],
    ("mutation_state.py", "tools_no_verdict", None): Gap(
        "a column in the trend table; no reader compares it with anything"),

    # --- agent runs ------------------------------------------------------------------
    ("agent_core.py", "status", "timeout"): [
        Branch("agent_collect.py", None, reads="status", literal="completed"),
        Branch("agent_discover.py", None, reads="status", literal="completed")],
    ("webset_discover.py", "partial", None): [
        Text(".claude/skills/discover-companies/SKILL.md", r"`partial: true`")],

    # --- sweeps and lookups ----------------------------------------------------------
    ("sweep.py", "skipped", None): Gap(
        "`clean` and the exit code ignore it: a sweep that could not read some of its "
        "paths reports clean and exits 0 unless EVERY path was unreadable"),
    ("source_corrections.py", "unreadable", None): Gap(
        "exit 2 only when zero files were readable; a partly unreadable scope exits 0 "
        "and the text mode prints a 'real zero' sentence without naming the unread files"),
    ("check_public_pii.py", "skipped", None): Gap(
        "the sweep fails only when scanned == 0; an unreadable public file is appended "
        "to skipped beside the legitimate skips and does not change clean or the exit"),
    ("records_lookup.py", "hits_truncated", None): Gap(
        "no reader; low severity because hit_count is always the full count"),
    ("calibration_review.py", "skipped", None): Gap(
        "worksheet edits with an invalid label are listed and the command exits 0"),

    # --- name matches, not a measurement signal --------------------------------------
    ("granola_save.py", "status", "skip"): NotCoverage(
        "an idempotent no-op: the meeting is already saved, nothing went unread"),
    ("mutation_trend.py", "status", "skipped"): NotCoverage(
        "an idempotent no-op: this baseline is already recorded"),
    ("outreach_metrics.py", "coverage", None): NotCoverage(
        "a business metric (pipeline companies with outreach), not scan coverage"),
    ("transcript_exclusions.py", "coverage", None): NotCoverage(
        "a constant caveat string describing the phrase list, not a per-run measurement"),
}

# Lower this when a gap is closed. Raising it is a decision to ship a field nothing
# fails closed on, and should be made in review rather than by editing a row.
MAX_KNOWN_GAPS = 17


def registry_problems(root: Path, registry: dict) -> list[str]:
    """Every way the registry and the tree disagree. Empty means consistent."""
    emitted = all_emitted(root)
    problems = []
    for key, lines in sorted(emitted.items(), key=lambda kv: tuple(map(str, kv[0]))):
        if key not in registry:
            file, field, value = key
            label = field if value is None else f"{field}={value!r}"
            problems.append(
                f"UNREGISTERED tools/{file}:{lines[0]} emits `{label}`. Name the consumer "
                "that fails closed on it in REGISTRY, or record a Gap with the reason")
    for key in sorted(set(registry) - set(emitted), key=lambda k: tuple(map(str, k))):
        problems.append(f"STALE registry row {key}: the producer no longer emits it")
    return problems


def _consumer_rows(registry: dict):
    return [(key, spec) for key, entry in registry.items()
            if isinstance(entry, list) for spec in entry]


# --------------------------------------------------------------------------- the guard

def test_every_coverage_field_is_registered_and_no_row_is_stale():
    problems = registry_problems(REPO_ROOT, REGISTRY)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    "key,spec", _consumer_rows(REGISTRY),
    ids=[f"{k[0]}:{k[1]}{'=' + k[2] if k[2] else ''}->{s.file}"
         for k, s in _consumer_rows(REGISTRY)])
def test_registered_consumer_still_branches_on_the_field(key, spec):
    problem = consumer_problem(spec, REPO_ROOT)
    assert problem is None, f"{key}: {problem}"


def test_every_row_names_a_consumer_or_a_written_reason():
    for key, entry in REGISTRY.items():
        if isinstance(entry, list):
            assert entry, f"{key}: an empty consumer list is a field with no consumer"
        else:
            assert isinstance(entry, (Gap, NotCoverage)), f"{key}: unknown entry type"
            assert len(entry.reason.strip()) >= 20, f"{key}: the reason is missing or too short"


def test_known_gaps_can_shrink_and_cannot_grow():
    gaps = [k for k, e in REGISTRY.items() if isinstance(e, Gap)]
    assert len(gaps) <= MAX_KNOWN_GAPS, (
        f"{len(gaps)} fields have no fail-closed consumer; the ceiling is {MAX_KNOWN_GAPS}. "
        "A new status field needs a consumer, not a Gap row")
    assert len(gaps) == MAX_KNOWN_GAPS, (
        f"{MAX_KNOWN_GAPS - len(gaps)} gap(s) were closed: lower MAX_KNOWN_GAPS to "
        f"{len(gaps)} so the ceiling follows the list down")


# --------------------------------------------------------------------------- write end

def _valid_constants(root: Path) -> dict[tuple[str, str], int]:
    """Module-level VALID_* collections of string literals in tools/*.py."""
    out = {}
    for path in sorted((root / "tools").glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                name, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                    and node.value is not None:
                name, value = node.target.id, node.value
            else:
                continue
            if isinstance(value, ast.Call) and value.args:
                value = value.args[0]
            if name.startswith("VALID_") and isinstance(value, (ast.Tuple, ast.Set, ast.List)) \
                    and value.elts and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                                           for e in value.elts):
                out[(path.name, name)] = node.lineno
    return out


def _is_validated(root: Path, file: str, name: str) -> bool:
    """Is `name` an operand of a membership test or a set difference in its own file?"""
    tree = ast.parse((root / "tools" / file).read_text(encoding="utf-8"))

    def is_name(n):
        return (isinstance(n, ast.Name) and n.id == name) or \
               (isinstance(n, ast.Attribute) and n.attr == name)
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in n.ops) \
                and any(is_name(c) for c in n.comparators):
            return True
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Sub) and is_name(n.right):
            return True
    return False


def unvalidated_vocabularies(root: Path) -> list[str]:
    return [f"tools/{file}:{line} declares {name} and never tests a value against it"
            for (file, name), line in sorted(_valid_constants(root).items())
            if not _is_validated(root, file, name)]


def test_every_declared_vocabulary_is_validated_against():
    """A `values:` list beside a field that nothing checks is documentation."""
    found = _valid_constants(REPO_ROOT)
    assert ("promotion_schema.py", "VALID_PROMOTED_TOKENS") in found, (
        "the `promoted` vocabulary is gone from promotion_schema.py; fire 2 was a value "
        "nothing validated, and this is the constant that closed it")
    problems = unvalidated_vocabularies(REPO_ROOT)
    assert not problems, "\n".join(problems)


def test_tokens_the_promoted_readers_branch_on_are_in_the_enforced_vocabulary():
    """A reader that special-cases a token the writer's validator rejects, or the
    reverse, is two vocabularies. The readers' tokens are read off their source."""
    import sys
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import promotion_schema
    src = (REPO_ROOT / "tools" / "scan_promotion_candidates.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    tokens: set[str] = set()
    for fname in ("is_promoted", "is_partially_promoted"):
        assert fname in funcs, f"scan_promotion_candidates.{fname} is gone; re-anchor this test"
        for n in ast.walk(funcs[fname]):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "startswith" and n.args \
                    and isinstance(n.args[0], ast.Constant):
                tokens.add(n.args[0].value)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "match" and n.args \
                    and isinstance(n.args[0], ast.Constant):
                group = re.search(r"\(\?:([a-z|]+)\)", n.args[0].value)
                if group:
                    tokens.update(group.group(1).split("|"))
    assert "partial" in tokens and "no" in tokens, (
        f"expected to read `partial` and `no` off the readers, got {sorted(tokens)}")
    missing = sorted(tokens - set(promotion_schema.VALID_PROMOTED_TOKENS))
    assert not missing, f"readers branch on {missing}, which the write-time check rejects"
    violations = promotion_schema.check_file(
        "feedback_example.md", {"promoted": "mostly -- one domain only"}, grandfathered=True)
    assert any("I6" in v for v in violations), violations


# --------------------------------------------------------------------------- the guard bites
#
# Each check above is run here against a copy of the tree with the defect put back, so a
# change that makes the guard vacuous fails in this file rather than passing quietly.

def _copy_tools(tmp_path: Path, names: list[str]) -> Path:
    (tmp_path / "tools").mkdir()
    for name in names:
        shutil.copy(REPO_ROOT / "tools" / name, tmp_path / "tools" / name)
    return tmp_path


def test_guard_fails_when_an_exit_code_stops_reading_the_coverage_floor(tmp_path):
    root = _copy_tools(tmp_path, ["check_frame_integrity.py"])
    spec = Branch("check_frame_integrity.py", "main", reads="under_floor", closes="exit")
    assert branch_problem(spec, root) is None
    target = root / "tools" / "check_frame_integrity.py"
    src = target.read_text(encoding="utf-8")
    broken = src.replace("return 2 if (fails or structural or under_floor) else 0",
                         "return 2 if (fails or structural) else 0")
    assert broken != src, "the exit line moved; re-anchor this mutation"
    target.write_text(broken, encoding="utf-8")
    problem = branch_problem(spec, root)
    assert problem and "governs a return" in problem, problem


def test_guard_fails_when_the_backlog_filter_stops_reading_fully_covered(tmp_path):
    root = _copy_tools(tmp_path, ["scan_promotion_candidates.py"])
    spec = Branch("scan_promotion_candidates.py", "main", reads="fully_covered")
    assert branch_problem(spec, root) is None
    target = root / "tools" / "scan_promotion_candidates.py"
    src = target.read_text(encoding="utf-8")
    broken = src.replace('if not r.get("fully_covered", False)]', 'if r.get("stale_members")]')
    assert broken != src, "the registry_defects filter moved; re-anchor this mutation"
    target.write_text(broken, encoding="utf-8")
    problem = branch_problem(spec, root)
    assert problem and "no conditional that reads `fully_covered`" in problem, problem


def test_guard_fails_when_a_mention_replaces_the_branch(tmp_path):
    """A comment or a print naming the field is a mention, and must not count."""
    root = _copy_tools(tmp_path, [])
    (root / "tools" / "reader.py").write_text(
        "def main(report):\n"
        "    # complete is read by the caller\n"
        "    print('complete', report['complete'])\n"
        "    return 0\n", encoding="utf-8")
    problem = branch_problem(Branch("reader.py", "main", reads="complete"), root)
    assert problem and "no conditional that reads `complete`" in problem, problem


def test_guard_fails_when_the_docstring_recipe_exits_on_clean(tmp_path):
    root = _copy_tools(tmp_path, ["population_probe.py"])
    (spec,) = REGISTRY[("population_probe.py", "fully_covered", None)]
    assert text_problem(spec, root) is None
    target = root / "tools" / "population_probe.py"
    src = target.read_text(encoding="utf-8")
    broken = src.replace("sys.exit(0 if report.fully_covered else 2)",
                         "sys.exit(0 if report.clean else 2)")
    assert broken != src
    target.write_text(broken, encoding="utf-8")
    assert text_problem(spec, root) is not None


def test_guard_fails_on_an_unregistered_producer_and_on_a_new_middle_value(tmp_path):
    root = _copy_tools(tmp_path, ["workstream_state.py"])
    registry = {k: v for k, v in REGISTRY.items() if k[0] == "workstream_state.py"}
    assert registry_problems(root, registry) == []

    (root / "tools" / "example_probe.py").write_text(
        "def report(rows):\n"
        "    return {'status': 'ok', 'fully_covered': not rows, 'truncated': False}\n",
        encoding="utf-8")
    problems = registry_problems(root, registry)
    assert len(problems) == 2 and all(p.startswith("UNREGISTERED") for p in problems), problems
    (root / "tools" / "example_probe.py").unlink()

    target = root / "tools" / "workstream_state.py"
    src = target.read_text(encoding="utf-8")
    broken = src.replace('row["state"] = "typed"', 'row["state"] = "unknown"')
    assert broken != src, "the typed assignment moved; re-anchor this mutation"
    target.write_text(broken, encoding="utf-8")
    problems = registry_problems(root, registry)
    assert any(p.startswith("UNREGISTERED") and "'unknown'" in p for p in problems), problems
    assert any(p.startswith("STALE") and "typed" in p for p in problems), problems


def test_guard_fails_on_a_vocabulary_nothing_validates(tmp_path):
    root = _copy_tools(tmp_path, [])
    (root / "tools" / "example_schema.py").write_text(
        "VALID_TIERS = ('alpha', 'beta')\n"
        "def check(value):\n"
        "    return 'tier must be one of ' + ', '.join(VALID_TIERS)\n", encoding="utf-8")
    assert len(unvalidated_vocabularies(root)) == 1
    (root / "tools" / "example_schema.py").write_text(
        "VALID_TIERS = ('alpha', 'beta')\n"
        "def check(value):\n"
        "    return value in VALID_TIERS\n", encoding="utf-8")
    assert unvalidated_vocabularies(root) == []
