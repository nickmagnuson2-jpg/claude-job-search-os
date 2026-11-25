"""Every operation a rule routes agents to exists in that writer.

Why: a guard that blocks a direct write tells the agent which writer subcommand to use instead
(its ROUTE text), and CLAUDE.md "Write-Only Files" names writer operations. If the writer loses
or renames that operation, the guard sends the agent to a command that fails. The required
operations are READ from those two sources, never typed here, so a new route is checked the day
it is written.

Sources of required operations:
  1. every string constant in tracked tools/check_*.py, pattern `<name>_write.py <op>` (backticked,
     so prose like "todo_write.py is positional" is not read as an op);
  2. the "### Write-Only Files" section of CLAUDE.md, every backticked bare word on a line that
     names `tools/<name>_write.py`.
Operations a writer offers: read by AST, never by import or execution: `add_parser("<op>")`,
`add_parser(name)` inside `for name in ("a", "b")`, and `cmd == "<op>"` dispatch.

Writers are tracked tools/*_write.py minus the check_*_write guards (13 on 2026-11-25). A writer
no rule names an operation for is reported as "no rule source" and is NOT counted covered; each
must be declared in tests/scripts/writer_no_rule_source.json with a reason, and the list can
only shrink. Gaps, 2026-11-25: output/overnight-112526/S4-writer-gaps.md (gitignored).
"""
import ast
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NO_SOURCE = REPO / "tests" / "scripts" / "writer_no_rule_source.json"
_ROUTE_RE = re.compile(r"`(?:tools/)?(\w+_write\.py) ([a-z][a-z-]*)")
_WRITER_LINE = re.compile(r"`tools/(\w+_write\.py)`")
_BARE_WORD = re.compile(r"`([a-z][a-z-]*)`")


def tracked(root: Path, pattern: str) -> list[Path]:
    out = subprocess.run(["git", "ls-files", pattern], cwd=root, check=True,
                         capture_output=True, text=True).stdout
    return [root / p for p in out.split() if p.count("/") == 1]


def writers(root: Path) -> list[str]:
    return sorted(p.name for p in tracked(root, "tools/*_write.py")
                  if not p.name.startswith("check_"))


def guard_routes(sources: dict[str, str]) -> dict[str, set[tuple[str, str]]]:
    """{writer: {(op, guard)}} from backticked `<w>_write.py <op>` in string constants."""
    req: dict[str, set[tuple[str, str]]] = {}
    for guard, src in sources.items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for w, op in _ROUTE_RE.findall(node.value):
                    req.setdefault(w, set()).add((op, guard))
    return req


def write_only_section(claude_md: str) -> str:
    m = re.search(r"^### Write-Only Files.*?$(.*?)^#{2,3} ", claude_md, re.S | re.M)
    return m.group(1) if m else ""


def claude_md_routes(section: str) -> dict[str, set[tuple[str, str]]]:
    req: dict[str, set[tuple[str, str]]] = {}
    for line in section.splitlines():
        m = _WRITER_LINE.search(line)
        if m:
            req.setdefault(m.group(1), set())
            for op in _BARE_WORD.findall(line[m.end():]):
                req[m.group(1)].add((op, "CLAUDE.md Write-Only Files"))
    return req


def writer_ops(src: str) -> set[str]:
    ops: set[str] = set()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # `for name in ("a", "b"): sub.add_parser(name)` -- only the loop that holds the call
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and isinstance(node.iter, (ast.Tuple, ast.List))):
            vals = {e.value for e in node.iter.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and getattr(inner.func, "attr", None) == "add_parser" and inner.args
                        and isinstance(inner.args[0], ast.Name)
                        and inner.args[0].id == node.target.id):
                    ops |= vals
        elif (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_parser"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            ops.add(node.args[0].value)
        elif (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
              and node.left.id == "cmd" and len(node.ops) == 1
              and isinstance(node.ops[0], ast.Eq)
              and isinstance(node.comparators[0], ast.Constant)
              and isinstance(node.comparators[0].value, str)):
            ops.add(node.comparators[0].value)
    return ops


def merge(*maps):
    out: dict[str, set] = {}
    for m in maps:
        for k, v in m.items():
            out.setdefault(k, set()).update(v)
    return out


def check(writer_names, required, ops_of, no_source: dict) -> list[str]:
    if not writer_names:
        return ["no tracked tools/*_write.py found: the enumeration is broken"]
    if not any(required.values()):
        return ["no required operation parsed from any guard or CLAUDE.md: a parser is broken"]
    errs = []
    for w in writer_names:
        needed = required.get(w, set())
        if not needed:
            if w not in no_source:
                errs.append(f"{w}: no rule names an operation for it; add a route to its guard, "
                            f"or declare it in {NO_SOURCE.name} with a reason")
            continue
        have = ops_of(w)
        if not have:
            errs.append(f"{w}: no operations parsed from the writer; the op parser is broken")
            continue
        for op, src in sorted(needed):
            if op not in have:
                errs.append(f"{w} has no `{op}` operation, but {src} routes agents to it")
    for w in sorted(set(required) - set(writer_names)):
        if required[w]:
            errs.append(f"{w}: a rule routes to it but it is not a tracked writer")
    for w, why in no_source.items():
        if not isinstance(why, str) or not why.strip():
            errs.append(f"{w}: no-rule-source entry has no reason")
        elif w not in writer_names:
            errs.append(f"{w}: declared no-rule-source but not a tracked writer; remove it")
        elif required.get(w):
            errs.append(f"{w}: declared no-rule-source but a rule now names its operations; "
                        f"remove the entry")
    return errs


def live_required(root: Path):
    guards = {p.name: p.read_text(encoding="utf-8") for p in tracked(root, "tools/check_*.py")}
    section = write_only_section((root / "CLAUDE.md").read_text(encoding="utf-8"))
    return guard_routes(guards), section


# ---- fixtures -------------------------------------------------------------------------------

def test_guard_routes_read_backticked_ops_only():
    src = 'ROUTE = "route: `x_write.py set <f>` or `tools/x_write.py bump`"\nM = "x_write.py is odd"\n'
    assert guard_routes({"check_x.py": src}) == {
        "x_write.py": {("set", "check_x.py"), ("bump", "check_x.py")}}


def test_claude_md_section_and_ops():
    md = ("### Write-Only Files (x)\n\n- `data/a.md` → `tools/a_write.py` (`audit` finds rows)\n"
          "- `data/b.md` → `tools/b_write.py`\n\n### Next\n- `tools/c_write.py` (`gone`)\n")
    sec = write_only_section(md)
    assert "c_write" not in sec
    assert claude_md_routes(sec) == {"a_write.py": {("audit", "CLAUDE.md Write-Only Files")},
                                     "b_write.py": set()}


def test_writer_ops_three_shapes():
    src = ('sub.add_parser("add")\nfor name in ("create", "delete"):\n    p = sub.add_parser(name)\n'
           'if cmd == "audit":\n    pass\nif other == "nope":\n    pass\n')
    assert writer_ops(src) == {"add", "create", "delete", "audit"}
    # a second loop over the same variable name that adds arguments, not parsers, is not read
    src2 = 'for name in ("create",):\n    sub.add_parser(name)\nfor name in ("--title",):\n    p.add_argument(name)\n'
    assert writer_ops(src2) == {"create"}


def test_missing_op_fails_present_op_passes():
    req = {"a_write.py": {("set", "check_a.py")}}
    assert check(["a_write.py"], req, lambda w: {"set"}, {}) == []
    errs = check(["a_write.py"], req, lambda w: {"put"}, {})
    assert errs == ["a_write.py has no `set` operation, but check_a.py routes agents to it"]


def test_no_source_writer_is_not_covered_unless_declared():
    req = {"a_write.py": {("set", "g")}}
    errs = check(["a_write.py", "b_write.py"], req, lambda w: {"set"}, {})
    assert len(errs) == 1 and errs[0].startswith("b_write.py: no rule names")
    assert check(["a_write.py", "b_write.py"], req, lambda w: {"set"}, {"b_write.py": "r"}) == []


def test_no_source_list_only_shrinks():
    req = {"a_write.py": {("set", "g")}}
    ok = lambda w: {"set"}
    assert "no reason" in check(["a_write.py", "b_write.py"], req, ok, {"b_write.py": ""})[0]
    assert "not a tracked writer" in check(["a_write.py"], req, ok, {"z_write.py": "r"})[0]
    assert "now names" in check(["a_write.py"], req, ok, {"a_write.py": "r"})[0]


def test_empty_inputs_fail_loudly():
    assert check([], {"a": {("x", "g")}}, lambda w: {"x"}, {})[0].startswith("no tracked")
    assert check(["a_write.py"], {}, lambda w: set(), {})[0].startswith("no required")
    assert check(["a_write.py"], {"a_write.py": set()}, lambda w: set(), {})[0].startswith(
        "no required")
    assert "op parser is broken" in check(["a_write.py"], {"a_write.py": {("x", "g")}},
                                          lambda w: set(), {})[0]


# ---- live ------------------------------------------------------------------------------------

def test_live_sources_parse_to_something():
    routes, section = live_required(REPO)
    assert routes, "no backticked writer route in any tools/check_*.py"
    assert section.strip(), "CLAUDE.md has no '### Write-Only Files' section"
    assert claude_md_routes(section), "Write-Only Files names no tools/*_write.py"


def test_live_every_routed_operation_exists():
    routes, section = live_required(REPO)
    required = merge(routes, claude_md_routes(section))
    names = writers(REPO)
    assert len(names) >= 10, f"only {len(names)} writers found"
    no_source = json.loads(NO_SOURCE.read_text(encoding="utf-8"))
    ops_of = lambda w: writer_ops((REPO / "tools" / w).read_text(encoding="utf-8"))
    errs = check(names, required, ops_of, no_source)
    assert not errs, "\n".join(errs)
