#!/usr/bin/env python3
"""promotion_schema.py -- make a promotion an OBSERVABLE EVENT, and make exit 2 cost something.

Why this exists
---------------
On 2026-08-25 the corpus held 509 feedback rules, 358 of them (70.3%) never promoted.
The drain rate could not even be measured, because only 38 of the 149 promoted files
carried a date anywhere. A promotion left no record, so "are we draining faster than we
capture" had no answer. Per CLAUDE.md's enforcement trichotomy, "a field a schema demands"
is a real tier; a prose request to write the date down is not.

The second job is harder and matters more. A rule leaves the queue by one of two exits:

    exit1     mechanical -- a hook, a schema field, a deterministic check. Verifiable.
    exit2     a principle no gate can express, promoted into always-loaded MEMORY.md.

exit2 is unverifiable and cheap, and exit1 is verifiable and expensive, so exit2 is the
path of least resistance and will be taken by default unless it is made to cost something.
The mechanism: **a detector is the price of admission to exit2**. An exit2 rule must ship
a violation signature detectable in a session transcript after the fact -- slot three of
the trichotomy, "a record a run cannot skip". A rule too vague to write a detector for is
too vague to follow, so failing this check sends the rule back for restating rather than
into the loaded channel.

That requirement is enforced here, in the schema, rather than asked for in a prompt.

Invariants
----------
I1  promoted yes/partial  => `promoted_date: YYYY-MM-DD` present and a real date.
I2  promoted yes/partial  => `exit_path` present and one of exit1|exit2|terminal.
I3  exit_path == exit2    => `detector_signature` present and non-empty.
I4  exit_path present     => `reopen_gate` names a NUMBER ("3rd fire"), never the no-op
                             "reopen on the next dated fire", which cannot trip on a count.
I5  a grandfather entry   => carries a non-empty written reason.
I6  promoted              => its leading token is in the vocabulary, never invented.
I7  exit_path == exit2    => the rule is named in MEMORY.md, the channel exit2 MEANS.
I8  a COMPLETED exit1     => `exit1_gate` names a concrete gate (an executable file in the
    (exit1 + any promoted    repo: .py, .sh, or an executable hook) AND `exit1_evidence`
     value but no/not/false) names a pytest node (`tests/...py::test_name`) that pytest
                             itself collects and passes (I8 RUNS it), whose own body reaches
                             that gate and asserts. It binds the claim "this gate blocks the
                             origin" to one passing node, instead of to prose nobody can run.

I1 and I2 are waived for files listed in the grandfather file, which records promotions
made before the field existed. The waiver is deliberately noisy: the legacy debt is
counted and named rather than hidden, the same discipline tools/mutation-allow.json uses
for surviving mutants. I3 and I4 are NEVER waived -- they govern promotions made from now
on, and waiving them would rebuild the escape hatch this module exists to close.
I8 is never waived either, for the reason it exists: before it, `exit_path: exit1` was an
enum value anyone could write, and the archive measured what that bought -- all 30 exit-1
agents claimed their check blocked the origin, and independent replay found 16 did not
(workstream memory-hygiene, v2 codex F1). A waived I8 is that relabel with a permit.

Exit codes
----------
    0  every scanned file satisfies the invariants
    2  at least one violation (BLOCK -- an exit-0 warning is not a gate, since Claude Code
       never surfaces hook stderr)
    1  bad usage / unreadable inputs
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

VALID_EXIT_PATHS = ("exit1", "exit2", "terminal")

# The vocabulary `promoted:` may open with. A qualifier after the token is ENCOURAGED --
# "partial -- open_draft.py reply mode (NOT proposed)" is the good shape -- but the TOKEN
# is what every consumer keys on, so it may not be invented.
#
# MEASURED 2026-09-21 across the live tier: 584 `no`, 119 `yes`, 52 `partial`, and 3
# strays ("n/a -- reference" x2, "SUPERSEDED ..." x1). The vocabulary was already closed
# in practice and enforced nowhere, which is the condition in which it drifts. The three
# strays currently read as PROMOTED by scan_promotion_candidates, because its recogniser
# fails OPEN: anything it does not recognise as no/not/false/partial is counted as a
# completed promotion and leaves the backlog silently.
#
# It includes the TIER NAMES as well as the yes/no/partial tokens, because
# scan_promotion_candidates deliberately reads a bare tier as a promotion and that
# behaviour is pinned by its own tests. Closing the vocabulary here rather than there is
# the point: the scanner must keep failing open on an unknown token (or every future tier
# lands in the backlog), so the vocabulary needs a gate somewhere that a human reads.
VALID_PROMOTED_TOKENS = (
    "no", "not", "false", "yes", "partial",
    "skill", "hook", "principle", "hard-rule", "framework", "script", "doc", "schema",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GRANDFATHER = REPO_ROOT / "tools" / "promotion-schema-grandfather.json"

# A gate that names no number cannot trip on a count; it waits for a human to notice.
# 38 of the 69 live candidates read like this on 2026-08-25.
# A gate "names a number" in any of the three shapes people actually write, and the
# ordinal branch is GENERATED rather than enumerated.
#
# MEASURED 2026-09-21, and the old pattern was wrong in three ways at once. It listed
# `2nd|3rd|4th|5th|6th` literally, so "7th fire" and "32nd fire" were rejected -- and the
# corpus holds a rule at 31 occurrences. It required the number BEFORE the noun, so
# "TRIPPED by fire 6" was rejected. And `(?:fire|occurrence)\b` could not match the
# plural, so "reopen at 3 fires" and "2 more fires" were both rejected. Every one of
# those is a well-formed gate, and the check was reporting them as unset.
#
# That matters more than a missed match: the fix for an I4 violation is to EDIT the rule
# file, so a detector that rejects good gates makes the corpus worse when it is obeyed.
_NUMBER_IN_GATE = re.compile(
    r"\b(?:"
    r"\d+\s*(?:st|nd|rd|th)"                                        # 2nd time, 3rd, 32nd fire
    r"|\d+\s*(?:more\s+)?(?:dated\s+)?(?:fire|occurrence|instance|catch|time)s?"
    r"|(?:fire|occurrence|instance)s?\s*#?\s*\d+"                   # fire 6, occurrence 2
    r"|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
    r")\b",
    re.IGNORECASE,
)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# I8. A gate is a file the interpreter or the hook runner executes: Python, shell, or an
# extensionless executable hook (tools/hooks/pre-push). A document is the "written down
# and followed" tier, which CLAUDE.md says is not built yet, and a data file (an allowlist
# JSON, a config) is something a gate READS, so neither is a gate.
_GATE_SUFFIXES = (".py", ".sh", "")
# The evidence is a pytest node id: a test file, then `::`, then the test, optionally inside
# a class and optionally parametrized. `tests/scripts/test_x.py::test_y[case]`.
_EVIDENCE_NODE = re.compile(r"^(?P<path>tests/[^\s:]+\.py)::(?:(?P<cls>[A-Za-z_]\w*)::)?"
                            r"(?P<test>test\w*)(?P<param>\[[^\]]*\])?$")


def parse_frontmatter(text: str) -> dict:
    """Flat key/value scrape of the YAML frontmatter block.

    Deliberately not yaml.safe_load: these files nest the keys we want under `metadata:`
    and carry prose values with unescaped colons. We only ever read scalars, and a flat
    scrape reads the same key identically whether it sits at the top level or under
    metadata. Values keep their raw text; quotes are stripped.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    lines = text[3:end].splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()

        # YAML BLOCK SCALARS. `reopen_gate: >-` puts the value on the FOLLOWING lines,
        # and the flat scrape used to store the marker ">-" as the value. Measured
        # 2026-09-21: one file in the corpus writes its gate this way, and its gate is
        # word-perfect ("4th fire, OR any fix shipped with a test that was never run
        # against the reverted code") -- reported as naming no number for as long as the
        # check has existed. A parser that silently returns a marker instead of a value
        # makes every downstream check wrong about that file, in the direction of
        # inventing a defect.
        if re.fullmatch(r"[>|][-+]?", val):
            indent = len(line) - len(line.lstrip())
            parts = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                parts.append(nxt.strip())
                i += 1
            val = " ".join(x for x in parts if x)

        if val[:1] in ("'", '"') and val[-1:] == val[:1] and len(val) >= 2:
            val = val[1:-1]
        out[key] = val
    return out


def claims_a_landed_gate(value: str) -> bool:
    """True for every `promoted:` value that says some enforcement LANDED, which is the
    population I8 holds.

    It is the union of two readers. scan_promotion_candidates.is_promoted fails OPEN:
    only no/not/false read as unpromoted, so `promoted: hook` and every bare tier leave the
    backlog, and a bare tier must not be a route around I8. It also keeps `partial` IN the
    backlog, but a partial claims that half of a gate landed (is_promoted_value, I1/I2), and
    the half that landed is exactly what I8 asks to see.
    """
    return not reads_as_unpromoted(value)


def reads_as_unpromoted(value: str) -> bool:
    """True when a `promoted:` value says nothing landed: it opens with the WORD no, not or
    false (an annotated "no -- reason" included), or it is empty or "0".

    The one definition. tools/scan_promotion_candidates.is_promoted imports it, so the
    backlog and I8 cannot disagree about a value. They did: I8 read the leading token, which
    stops at "_" or a digit, while the scanner used a word boundary, so `promoted: not_yet`
    left the backlog and skipped I8 (Grok r6 P1, 2026-11-06). `\\b` keeps a tier that merely
    starts with those letters ("none", "notation") promoted, and fails OPEN on purpose: tier
    names are open-ended.
    """
    v = str(value or "").strip().lower()
    return bool(re.match(r"^(?:no|not|false)\b", v)) or v in ("", "0")


def is_promoted_value(value: str) -> bool:
    """True for `yes` and `partial`. Both are promotions and both need a record.

    `partial` counts deliberately: it means enforcement is half-landed, which is a
    promotion event that happened on a date via some exit path, and the half that landed
    is exactly the part a future session will otherwise re-derive from scratch.
    """
    v = (value or "").strip().lower()
    return v.startswith("yes") or v.startswith("partial")


def promoted_token(value: str) -> str:
    """The leading word of a `promoted:` value, lowercased. '' when there is none."""
    m = re.match(r"\s*([A-Za-z][A-Za-z/-]*)", str(value or ""))
    if not m:
        return ""
    tok = m.group(1).lower().rstrip("-")
    # "n/a" is one token, not "n"; keep the slash so it cannot masquerade as a tier.
    return tok


def gate_names_a_number(gate: str) -> bool:
    return bool(_NUMBER_IN_GATE.search(gate or ""))


def load_grandfather(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object of file -> reason")
    return data


def _gate_path(gate: str) -> str:
    """The file part of an `exit1_gate` value: its first token, before any `::` suffix."""
    first = (gate or "").strip().split()
    return first[0].split("::", 1)[0].strip("`") if first else ""


def _escapes_repo(rel: str) -> bool:
    """True for an absolute path or one that climbs out with `..`: neither names a repo file."""
    p = Path(rel)
    return p.is_absolute() or rel.startswith("~") or ".." in p.parts


def _module_is_gate(module: str, gate_file: str) -> bool:
    """True when a dotted import names THIS gate file: the bare stem (the repo puts tools/
    on sys.path, so `import check_x` is the house style), or a dotted path that is a
    suffix of the gate's path. `vendor.check_x` shares the stem and is a different module."""
    gate_mod = Path(gate_file).with_suffix("").as_posix()
    dotted = module.replace(".", "/")
    return dotted == Path(gate_file).stem or gate_mod == dotted or gate_mod.endswith("/" + dotted)


def _visible(tree: ast.Module, node) -> list:
    """The statements whose bindings the cited node can see: the module's own top level
    (descending into if/try/with blocks, never into another function or class), plus the
    node itself. An import inside a SIBLING test binds nothing here; using it raises
    NameError before the gate is reached."""
    out, stack = [], list(tree.body)
    while stack:
        st = stack.pop()
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        out.append(st)
        for field in ("body", "orelse", "finalbody", "handlers"):
            stack.extend(getattr(st, field, None) or [])
    return out + [node]


def _gate_refs(tree: ast.Module, gate_file: str, node) -> tuple[set[str], set[str]]:
    """What a test may use to reach a .py gate. Returns (names, chains): a NAME bound
    directly to the gate module or to something imported from it, and an attribute CHAIN
    (`tools.check_x`) for a dotted import, which binds only its top package -- a use of
    `tools.other` is not a use of the gate."""
    names: set[str] = set()
    chains: set[str] = set()
    gate_assigns: set[int] = set()
    scope = _visible(tree, node)
    # Imports come from the flattened top-level statements themselves (never a walk into
    # them, which would enter a function defined inside an `if`), plus anything in the node.
    found = scope[:-1] + list(ast.walk(node))
    # A module-level constant that names the gate path (`HOOK = REPO / "tools/x.py"`) is
    # how a test that runs a hook or script usually reaches it. Adjacent string pieces are
    # joined with "/", so `REPO / "tools" / "x.py"` names the path too.
    for st in scope:
        if isinstance(st, ast.Assign) and st is not node:
            consts = sorted((c for c in ast.walk(st.value)
                             if isinstance(c, ast.Constant) and isinstance(c.value, str)),
                            key=lambda c: (c.lineno, c.col_offset))
            pieces = [c.value for c in consts]
            if _names_a_path("/".join(pieces), gate_file) or any(
                    _names_a_path(x, gate_file) for x in pieces):
                names.update(t.id for t in st.targets if isinstance(t, ast.Name))
                gate_assigns.add(id(st))
    if gate_file.endswith(".py"):
        _add_gate_imports(found, gate_file, names, chains)
    # A name the MODULE binds again to something else (an assignment, a def, a non-gate
    # import, an `except ... as`) may not hold the gate when the test runs. Order is not
    # modelled, so any second binding drops the name: fail closed.
    other = _module_rebindings(tree, scope[:-1], gate_file, gate_assigns)
    names -= other
    chains = {c for c in chains if c.split(".")[0] not in other}
    return names, chains


def _add_gate_imports(found, gate_file: str, names: set[str], chains: set[str]) -> None:
    for imp in found:
        if isinstance(imp, ast.Import):
            for a in imp.names:
                if not _module_is_gate(a.name, gate_file):
                    continue
                if a.asname or "." not in a.name:
                    names.add(a.asname or a.name)
                else:
                    chains.add(a.name)
        elif isinstance(imp, ast.ImportFrom) and _module_is_gate(imp.module or "", gate_file):
            names.update(a.asname or a.name for a in imp.names)


_BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers")


def _module_rebindings(tree: ast.Module, stmts: list, gate_file: str,
                       gate_assigns: set[int]) -> set[str]:
    """Names the module binds to something OTHER than the gate. `stmts` is the flattened
    top level from _visible, so a compound statement contributes only its HEADER (a for
    target, a with-as, a walrus in a test); its body is already in the list and is never
    walked into twice."""
    other: set[str] = set()
    for st in (x for x in stmts if id(x) not in gate_assigns):
        if isinstance(st, (ast.Import, ast.ImportFrom)):
            gate_from = isinstance(st, ast.ImportFrom) and _module_is_gate(st.module or "",
                                                                           gate_file)
            other |= {a.asname or a.name.split(".")[0] for a in st.names
                      if not (gate_from or (isinstance(st, ast.Import)
                                            and _module_is_gate(a.name, gate_file)))}
        else:
            other |= {getattr(st, "name", None)} - {None}      # `except ... as name`
            header = [v for f, v in ast.iter_fields(st) if f not in _BLOCK_FIELDS]
            for part in (x for v in header for x in (v if isinstance(v, list) else [v])):
                if isinstance(part, ast.AST):
                    other |= {n.id for n in ast.walk(part)
                              if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    # Module-level defs and classes, including those inside a top-level block, but never
    # a method or a nested def: those bind inside their own scope.
    stack = list(tree.body)
    while stack:
        st = stack.pop()
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            other.add(st.name)
        else:
            stack.extend(x for f in _BLOCK_FIELDS for x in (getattr(st, f, None) or []))
    return other


def _names_a_path(value: str, gate_file: str) -> bool:
    """A string names the gate when one of its whitespace tokens IS the gate path or ends
    in `/<gate path>` (an absolute spelling of the same file). A shared basename is not
    the same file: `tests/scripts/test_check_x.py` and `/tmp/check_x.py` both contain
    `check_x.py`."""
    toks = (t.strip("'\"`,;()[]") for t in value.split())
    return any(t == gate_file or t.endswith("/" + gate_file) for t in toks)


def _reaches_gate(node, tree: ast.Module, gate_file: str) -> bool:
    names, chains = _gate_refs(tree, gate_file, node)
    # A name the test rebinds locally (an argument, an assignment) is not the import.
    local = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)
             and isinstance(n.ctx, ast.Store)}
    local |= {a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg)}
    names -= local
    chains = {c for c in chains if c.split(".")[0] not in local}
    def uses_gate(n) -> bool:
        return bool(
            (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in names)
            or (isinstance(n, ast.Attribute)
                and any(ast.unparse(n) == c or ast.unparse(n).startswith(c + ".")
                        for c in chains))
            or (isinstance(n, ast.Constant) and isinstance(n.value, str)
                and _names_a_path(n.value, gate_file)))

    # The BODY only: a decorator's strings (a parametrize list naming the gate path) are
    # arguments the test receives, not code that reaches the gate.
    return any(uses_gate(n) for st in node.body for n in ast.walk(st))


def _find_node(tree: ast.Module, cls: str | None, test: str):
    """The source of the named test: a module-level def, or a method of the named class.
    Only its body is read here (reach and assertion). Whether pytest collects it is
    pytest's answer, asked by _pytest_verdict, not guessed from the AST."""
    body = tree.body
    if cls:
        owner = next((n for n in body if isinstance(n, ast.ClassDef) and n.name == cls), None)
        if owner is None:
            return None
        body = owner.body
    return next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == test), None)


PYTEST_TIMEOUT = 120
_SUMMARY_COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)")


def _pytest(repo_root: Path, *args: str) -> tuple[int, str]:
    """Run pytest on this repo's tests. (exit code, combined output). -1 when it could not
    run at all (timeout, interpreter missing): unknowable, which the caller fails closed on."""
    try:
        # PYTEST_ADDOPTS from the caller (-x, -n, -qq) would change what the output means.
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
        p = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
                           cwd=str(repo_root), capture_output=True, text=True, env=env,
                           encoding="utf-8", errors="replace", timeout=PYTEST_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as e:
        return -1, str(e)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _pytest_verdict(repo_root: Path, node_id: str) -> str | None:
    """Ask pytest itself whether the cited node exists and passes. None when it does; else
    the reason. Replaces six rounds of static guessing at pytest's collection rules
    (fixtures named test_*, __init__/__new__ classes, parameter ids, skip and skipif marks),
    each round of which found a case the guess got wrong (Nick's decision, 2026-11-06).

    Collected: `--collect-only -q` must list the node itself, or for an id-less parametrized
    test at least one `node[...]` case. Passes: a run must report passed tests and no
    skipped, failed or errored ones, so an unconditional or literal-true skip, a missing
    async plugin, and a failing assertion are all not evidence.
    """
    rc, out = _pytest(repo_root, "--collect-only", "-q", node_id)
    if rc == -1:
        return f"pytest could not run ({out[:120]}), so the node is unverified, not proven"
    listed = [ln.strip() for ln in out.splitlines() if "::" in ln]
    if not any(ln == node_id or ln.startswith(node_id + "[") for ln in listed):
        return "pytest does not collect it"
    rc, out = _pytest(repo_root, "-q", "-rA", node_id)
    if rc == -1:
        return f"pytest could not run ({out[:120]}), so the node is unverified, not proven"
    # Only pytest's own final summary line counts. Captured output that -rA prints (a test
    # that logs "3 failed") must not be read as the result.
    summary = next((ln for ln in reversed(out.splitlines()) if _SUMMARY_COUNT.search(ln)), "")
    counts = {}
    for n, kind in _SUMMARY_COUNT.findall(summary):
        counts[kind.rstrip("s") if kind.startswith("error") else kind] = int(n)
    bad = {k: v for k, v in counts.items() if k in ("failed", "skipped", "error", "xfailed")}
    if rc != 0 or not counts.get("passed") or bad:
        what = ", ".join(f"{v} {k}" for k, v in bad.items()) or f"exit {rc}"
        return f"pytest does not pass it ({what})"
    return None


def _asserts(node) -> bool:
    """An `assert` statement, or a `pytest.raises(...)` call (as a context manager or not),
    in the node's own body. An attribute merely NAMED raises is not one (Codex r6 F3)."""
    for st in node.body:
        for n in ast.walk(st):
            if isinstance(n, ast.Assert):
                return True
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "raises" and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "pytest"):
                return True
    return False


def _check_exit1_evidence(name: str, fm: dict, repo_root: Path | None) -> list[str]:
    """I8 violations for one completed exit1 record. Empty list means it passed.

    What passing establishes, and what it does not: the gate is an executable file in the
    repo, and the named test is a node pytest collects and passes (pytest is run on it), whose
    OWN body reaches that gate and asserts something. Whether the assertion is about the
    rule's origin is the author's claim; I8 makes that claim point at one passing node,
    instead of at prose.

    `repo_root` None means the repo is not visible. Unknowable is not a pass: presence and
    shape are still checked, and the record is then reported as unverified, never proven.
    """
    out: list[str] = []
    gate = fm.get("exit1_gate", "").strip()
    evidence = fm.get("exit1_evidence", "").strip().strip("`")

    gate_file = _gate_path(gate)
    gate_ok = False
    if not gate_file:
        out.append(
            f"{name}: I8 exit_path is exit1 and promoted, but exit1_gate is missing. A "
            "completed exit1 names the gate that blocks; without it the record is a relabel")
    elif _escapes_repo(gate_file):
        out.append(
            f"{name}: I8 exit1_gate {gate_file!r} is not a repo-relative path, so it names "
            "no gate this repo can run")
    elif Path(gate_file).suffix.lower() not in _GATE_SUFFIXES:
        out.append(
            f"{name}: I8 exit1_gate {gate_file!r} is not executable code. A document or data "
            "file (an allowlist, a config) is read by a gate; it is not one")
    elif repo_root is not None and not (repo_root / gate_file).is_file():
        out.append(
            f"{name}: I8 exit1_gate {gate_file!r} does not resolve to a file in the repo, so "
            "the gate it names cannot be shown to exist")
    elif (repo_root is not None and not Path(gate_file).suffix
          and not os.access(repo_root / gate_file, os.X_OK)):
        out.append(
            f"{name}: I8 exit1_gate {gate_file!r} has no suffix and is not executable, so "
            "nothing runs it as a gate")
    else:
        gate_ok = True

    if not evidence:
        out.append(
            f"{name}: I8 exit_path is exit1 and promoted, but exit1_evidence is missing. A "
            "completed exit1 names the test that replays its origin against the gate")
        return out
    m = _EVIDENCE_NODE.match(evidence)
    if not m or _escapes_repo(m.group("path")):
        out.append(
            f"{name}: I8 exit1_evidence {evidence!r} is not a test node id "
            "(tests/<path>.py::test_<name>), so there is nothing to run")
        return out
    if repo_root is None:
        out.append(
            f"{name}: I8 exit1_evidence cannot be resolved without the repo, so this record "
            "is unverified, not proven")
        return out
    test_file = repo_root / m.group("path")
    if not test_file.is_file():
        out.append(f"{name}: I8 exit1_evidence file {m.group('path')!r} does not exist")
        return out
    try:
        tree = ast.parse(test_file.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        out.append(f"{name}: I8 exit1_evidence file {m.group('path')!r} does not parse")
        return out
    node = _find_node(tree, m.group("cls"), m.group("test"))
    where = f"{m.group('cls')}::" if m.group("cls") else ""
    if node is None:
        out.append(
            f"{name}: I8 exit1_evidence names {where}{m.group('test')}, which is not defined "
            f"in {m.group('path')}")
        return out
    verdict = _pytest_verdict(repo_root, evidence)
    if verdict:
        out.append(f"{name}: I8 exit1_evidence {evidence}: {verdict}, so it is not evidence "
                   "that the gate blocks anything")
    if not _asserts(node):
        out.append(
            f"{name}: I8 exit1_evidence {m.group('test')} asserts nothing, so it cannot "
            "show the gate blocking anything")
    # The node's OWN body must reach this gate: an import elsewhere in the file, or a
    # sibling test that uses it, is not evidence about the node the record cites.
    if gate_ok:
        if not _reaches_gate(node, tree, gate_file):
            out.append(
                f"{name}: I8 exit1_evidence {m.group('test')} never reaches the gate "
                f"{gate_file!r} (no use of a name importing it, no string naming it), so it "
                "cannot be the evidence that this gate blocks the origin")
    return out


def check_file(name: str, fm: dict, grandfathered: bool,
               in_memory_md: bool | None = None,
               repo_root: Path | None = REPO_ROOT) -> list[str]:
    """Return the list of invariant violations for one file. Empty list means clean."""
    violations: list[str] = []
    promoted = fm.get("promoted", "no")
    exit_path = fm.get("exit_path", "").strip()

    if is_promoted_value(promoted):
        if not grandfathered:
            stamp = fm.get("promoted_date", "").strip()
            if not stamp:
                violations.append(
                    f"{name}: I1 promoted is {promoted!r} but promoted_date is missing, "
                    "so this promotion is not an observable event"
                )
            elif not _ISO_DATE.match(stamp):
                violations.append(f"{name}: I1 promoted_date {stamp!r} is not YYYY-MM-DD")
            if not exit_path:
                violations.append(f"{name}: I2 promoted is {promoted!r} but exit_path is missing")

    # I6. The token is the part consumers key on, so it may not be invented. Without
    # this the `values:` list beside the field is decoration -- the same defect the frame
    # schema carried in its own enum fields until 2026-09-21.
    tok = promoted_token(promoted)
    if tok and tok not in VALID_PROMOTED_TOKENS:
        violations.append(
            f"{name}: I6 promoted opens with {tok!r}, which is not one of "
            f"{'|'.join(VALID_PROMOTED_TOKENS)}. A consumer keying on this token cannot "
            "see a value outside the vocabulary, and the recogniser fails OPEN -- an "
            "unknown token reads as a completed promotion and leaves the backlog"
        )

    if exit_path and exit_path not in VALID_EXIT_PATHS:
        violations.append(
            f"{name}: I2 exit_path {exit_path!r} is not one of {'|'.join(VALID_EXIT_PATHS)}"
        )

    # I8. A COMPLETED exit1 must carry its gate and the evidence that the gate blocks the
    # rule's own origin. Without this, `exit_path: exit1` is an enum value, and flipping a
    # rule to it records the rule as handled with nothing delivered -- the relabeling
    # loophole v3 step 1 of the memory-hygiene workstream exists to close.
    if exit_path == "exit1" and claims_a_landed_gate(promoted):
        violations.extend(_check_exit1_evidence(name, fm, repo_root))

    # I3 and I4 are never waived. See the module docstring.
    # I7. exit2 is defined at the top of this file as "a principle no gate can express,
    # PROMOTED INTO ALWAYS-LOADED MEMORY.md". I3 charges the detector as the price of
    # admission and nothing ever checked that the rule was admitted.
    #
    # MEASURED 2026-09-21: 50 rules carry exit_path exit2 and 2 appear in MEMORY.md. The
    # other 48 paid the price and never got in. Each one reads as PROMOTED -- it leaves
    # the backlog, the detector stops surfacing it -- while the mechanism it claims, being
    # loaded into every conversation, does not exist for it. That is the same defect this
    # whole file was built to catch, at the one tier that cannot be verified from the file
    # itself, which is exactly why it went unnoticed.
    #
    # None when the caller cannot see MEMORY.md: unknowable is not a pass, so it is simply
    # not checked rather than assumed either way.
    if exit_path == "exit2" and in_memory_md is False:
        violations.append(
            f"{name}: I7 exit_path is exit2, which MEANS promoted into always-loaded "
            "MEMORY.md, and this rule is not named there. It reads as promoted and left "
            "the backlog, while the channel it claims does not carry it")

    if exit_path == "exit2" and not fm.get("detector_signature", "").strip():
        violations.append(
            f"{name}: I3 exit_path is exit2 but detector_signature is empty. A detector is "
            "the price of admission to the always-loaded channel: a rule too vague to detect "
            "is too vague to follow, and must be restated rather than loaded"
        )

    if exit_path and not gate_names_a_number(fm.get("reopen_gate", "")):
        violations.append(
            f"{name}: I4 reopen_gate names no number, so it can never trip on a count and "
            "waits for a human to notice"
        )

    return violations


def scan(memory_dir: Path, grandfather: dict, repo_root: Path | None = REPO_ROOT) -> dict:
    files = sorted(p for p in memory_dir.glob("feedback_*.md") if p.is_file())
    if not files:
        # An empty scan is an error, never a clean bill of health. A guard with no failing
        # mode is the same bug wearing a safety vest.
        raise ValueError(f"no feedback_*.md files found under {memory_dir}")

    # Read the always-loaded channel ONCE. Its absence is not a failure: a caller may
    # point this at a corpus that has no MEMORY.md, and then I7 simply cannot run.
    memory_md = memory_dir / "MEMORY.md"
    memory_text = (memory_md.read_text(encoding="utf-8", errors="replace")
                   if memory_md.exists() else None)

    violations: list[str] = []
    promoted_total = 0
    with_date = 0
    with_exit_path = 0
    by_exit: dict[str, int] = {}
    # I8 reach, stated so the pass/fail split is never reported without its denominator.
    exit1_completed = 0
    exit1_proven = 0

    for p in files:
        fm = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        name = p.name
        in_md = None if memory_text is None else (p.stem in memory_text)
        if is_promoted_value(fm.get("promoted", "no")):
            promoted_total += 1
            if fm.get("promoted_date", "").strip():
                with_date += 1
        ep = fm.get("exit_path", "").strip()
        if ep:
            with_exit_path += 1
            by_exit[ep] = by_exit.get(ep, 0) + 1
        file_violations = check_file(name, fm, grandfathered=name in grandfather,
                                     in_memory_md=in_md, repo_root=repo_root)
        if ep == "exit1" and claims_a_landed_gate(fm.get("promoted", "no")):
            exit1_completed += 1
            if not any(": I8 " in v for v in file_violations):
                exit1_proven += 1
        violations.extend(file_violations)

    for name, reason in grandfather.items():
        if not str(reason).strip():
            violations.append(
                f"{name}: I5 grandfather entry has an empty reason. An allowlist without "
                "justification is how this decays back into green means done"
            )

    return {
        "scanned": len(files),
        "promoted_total": promoted_total,
        "promoted_with_date": with_date,
        "with_exit_path": with_exit_path,
        "by_exit_path": by_exit,
        "grandfathered": len(grandfather),
        "exit1_completed": exit1_completed,
        "exit1_proven": exit1_proven,
        "violations": violations,
        "ok": not violations,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-dir", required=True, type=Path)
    ap.add_argument("--grandfather", type=Path, default=DEFAULT_GRANDFATHER)
    ap.add_argument("--json", action="store_true", help="emit the full report as JSON")
    args = ap.parse_args(argv)

    if not args.memory_dir.is_dir():
        print(f"not a directory: {args.memory_dir}", file=sys.stderr)
        return 1
    try:
        gf = load_grandfather(args.grandfather)
        report = scan(args.memory_dir, gf)
    except (ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"scanned {report['scanned']} rules | promoted {report['promoted_total']} "
            f"({report['promoted_with_date']} dated) | dispositioned {report['with_exit_path']} "
            f"{report['by_exit_path']} | grandfathered {report['grandfathered']} | "
            f"exit1 proven {report['exit1_proven']} of {report['exit1_completed']} completed"
        )
        for v in report["violations"]:
            print(f"  VIOLATION {v}")

    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
