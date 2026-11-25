"""Single-source guard: learning-loop state is parsed and defined in two modules only.

Nick, 2026-11-22: "I want to codify on one" (frontmatter reader) and "I only want to have 1
definition of due one version". The import of tools/memory_frontmatter.py and
tools/learning_state.py enforces NON-DRIFT; this test enforces ADOPTION (CLAUDE.md
enforcement-tier rule: an import cannot stop a new file from writing its own copy).

Detection is by BEHAVIOR, not by function name (state matrix, "Shared mechanism": the
name-based inventory missed learning_ledger.read_memory's inline fm()). It walks the AST of
every tools/**/*.py outside the two modules and reports:

  frontmatter_split   a regex anchored on `---` (\\A--- or ^---), or a str method
                      (startswith/find/index/split/partition/...) called with "---"
  key_scrape          a regex (re.compile/search/match/...) that reads a frontmatter state
                      key at line start: `^\\s*promoted:`, or an interpolated key, f-string
                      or concatenation (`rf"^\\s*{key}:"`, `r"\\s*" + key + r"\\s*:"`)
  gate_grammar        a regex reading a reopen gate: TRIPPED/OWED, or fire/occurrence with \\d
  promoted_vocab      a collection literal holding 3+ promoted tokens incl. yes/partial and a tier
  exit_paths          a collection literal holding "exit1" and "exit2"
  friction_kinds      a collection literal holding "block-as-designed"
  half_life           a dict literal keyed "MACHINERY"
  promoted_predicate  .startswith("yes" | "partial" | "promoted")
  resolved_prefix     an assignment of the literal "RESOLVED"

ALLOWLIST: files that parse the frontmatter of something that is NOT a memory rule, each
with its reason. An allowlist entry that no longer matches anything fails the test, so the
list cannot outlive its reason.

STATE ON 2026-11-22: today's tree FAILS (the 8 named parsers, learning_ledger.read_memory's
fm(), the gate grammars and friction_log's prefix). test_no_state_parsing_outside_the_two_modules
is xfail(strict=True) until the one-commit reader cutover, which removes the xfail; strict
means the cutover cannot forget to. test_detector_finds_todays_known_sites pins that the
detector sees each of them on the real tree; the cutover deletes it with the sites.
"""
import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
OWNERS = {"memory_frontmatter.py", "learning_state.py"}

STATE_KEYS = ("promoted", "occurrences", "reopen_gate", "reopen_at", "exit_path", "terminal",
              "last_cited", "exit1_gate", "exit1_evidence")
TOKENS = {"no", "not", "false", "yes", "partial", "skill", "hook", "principle", "hard-rule",
          "framework", "script", "doc", "schema", "superseded", "merged", "parked"}
TIERS = {"skill", "hook", "principle", "hard-rule", "framework", "script", "doc", "schema"}
RE_FUNCS = {"compile", "search", "match", "fullmatch", "findall", "finditer", "sub", "subn", "split"}
STR_SPLITTERS = {"startswith", "find", "rfind", "index", "rindex", "split", "rsplit",
                 "partition", "rpartition", "removeprefix"}
HOLE = "\x00"  # stands for any interpolated or concatenated non-literal part

# A key at line start: ^, \s*, (\s*) or \s+ before it, optional whitespace, then a colon.
_KEY_AT_LINE_START = re.compile(
    r"(?:\^|\\s\*|\(\\s\*\)|\\s\+)(?:\b(?:" + "|".join(STATE_KEYS) + r")\b|" + HOLE + r")"
    r"(?:\\s[*+?]?|\s)*:")
_ANCHORED_DASHES = re.compile(r"(?:\\A|\^)---")

ALLOWLIST = {
    "tools/act_apply.py": "matches `^<key>:` lines in data/ markdown key-value sections, "
                          "not a frontmatter block and not a memory rule",
    "tools/check_no_confabulation.py": "strips the frontmatter of agent prompt files before "
                                       "checking their body; reads no state key",
    "tools/check_voice_pure.py": "checks the `voice:` label of dated reflection files; reads "
                                 "no state key",
    "tools/voice_corpus_audit.py": "reads the `voice:` label of voice-corpus files; reads no "
                                   "state key",
}


def _flatten(node, consts: dict | None = None) -> str | None:
    """The literal text of a string expression, with HOLE for each non-literal part; None
    when the expression is not a string literal, an f-string, a + of them, or a name bound
    to a string literal in the same file (`SEP = "---"`)."""
    consts = consts or {}
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else HOLE for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _flatten(node.left, consts), _flatten(node.right, consts)
        if left is None and right is None:
            return None
        return (HOLE if left is None else left) + (HOLE if right is None else right)
    return None


def _str_consts(elts) -> set:
    return {e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def _bindings(tree) -> tuple[set, dict, dict]:
    """(names bound to the re module, {alias: re function}, {name: str literal it is bound to})."""
    re_mods, re_funcs, consts = set(), {}, {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            re_mods |= {a.asname or a.name for a in n.names if a.name == "re"}
        elif isinstance(n, ast.ImportFrom) and n.module == "re":
            re_funcs.update({a.asname or a.name: a.name for a in n.names if a.name in RE_FUNCS})
        elif (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)):
            consts[n.targets[0].id] = n.value.value
    return re_mods, re_funcs, consts


def _colon_parser_lines(scope) -> list[int]:
    """Lines of `x.split(":", ...)` / `x.partition(":")` in a scope that also compares a
    value with a state key literal: a hand-rolled key: value parser."""
    splits, compares_key = [], False
    for n in ast.walk(scope):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("split", "partition", "rsplit") and n.args
                and isinstance(n.args[0], ast.Constant) and n.args[0].value == ":"):
            splits.append(n.lineno)
        elif isinstance(n, ast.Compare):
            for side in [n.left] + n.comparators:
                if isinstance(side, ast.Constant) and side.value in STATE_KEYS:
                    compares_key = True
    return splits if compares_key else []


def findings_in_source(src: str) -> list[tuple[int, str]]:
    """(line, signature) for every state-parsing or state-defining site in `src`."""
    out = []
    tree = ast.parse(src)
    re_mods, re_funcs, consts = _bindings(tree)
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    module_level = ast.Module(body=[st for st in tree.body if not isinstance(st, defs)], type_ignores=[])
    for scope in [module_level] + [n for n in ast.walk(tree)
                                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        out += [(line, "key_scrape") for line in _colon_parser_lines(scope)]
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and n.args:
            arg = _flatten(n.args[0], consts)
            if isinstance(n.func, ast.Attribute):
                attr = n.func.attr
                is_re = (isinstance(n.func.value, ast.Name) and n.func.value.id in re_mods
                         and attr in RE_FUNCS)
            elif isinstance(n.func, ast.Name) and n.func.id in re_funcs:
                attr, is_re = re_funcs[n.func.id], True
            else:
                continue
            if is_re and arg is not None:
                if _KEY_AT_LINE_START.search(arg):
                    out.append((n.lineno, "key_scrape"))
                if _ANCHORED_DASHES.search(arg):
                    out.append((n.lineno, "frontmatter_split"))
                if re.search(r"TRIPPED|OWED", arg) or (re.search(r"fire|occurrence", arg)
                                                       and "\\d" in arg):
                    out.append((n.lineno, "gate_grammar"))
            elif attr in STR_SPLITTERS and arg is not None and arg.strip("\n") == "---":
                out.append((n.lineno, "frontmatter_split"))
            if attr == "startswith" and arg is not None and arg.lower() in ("yes", "partial", "promoted"):
                out.append((n.lineno, "promoted_predicate"))
            if attr == "startswith" and arg is not None and arg.strip().lower() == "resolved":
                out.append((n.lineno, "resolved_prefix"))
        elif isinstance(n, (ast.Tuple, ast.List, ast.Set)):
            vals = _str_consts(n.elts)
            if len(vals & TOKENS) >= 3 and vals & {"yes", "partial"} and vals & TIERS:
                out.append((n.lineno, "promoted_vocab"))
            if {"exit1", "exit2"} <= vals:
                out.append((n.lineno, "exit_paths"))
            if "block-as-designed" in vals:
                out.append((n.lineno, "friction_kinds"))
        elif isinstance(n, ast.Dict):
            keys = _str_consts([k for k in n.keys if k is not None])
            if "MACHINERY" in keys:
                out.append((n.lineno, "half_life"))
            if len(keys & TOKENS) >= 3 and keys & {"yes", "partial"} and keys & TIERS:
                out.append((n.lineno, "promoted_vocab"))
        elif isinstance(n, ast.Assign):
            if isinstance(n.value, ast.Constant) and n.value.value == "RESOLVED":
                out.append((n.lineno, "resolved_prefix"))
    return sorted(set(out))


def tree_findings(root: Path = REPO_ROOT) -> dict[str, list]:
    out = {}
    for p in sorted((root / "tools").rglob("*.py")):
        if p.name in OWNERS:
            continue
        found = findings_in_source(p.read_text(encoding="utf-8"))
        if found:
            out[p.relative_to(root).as_posix()] = found
    return out


# ------------------------------------------------------------------ the detector, on synthetic code

@pytest.mark.parametrize("src,sig", [
    ('import re\nm = re.match(r"\\A---\\n(.*?\\n)---\\n", t)', "frontmatter_split"),
    ('import re\nm = re.match(r"^---\\n(.*?)\\n---", t)', "frontmatter_split"),
    ('if text.startswith("---"):\n    pass', "frontmatter_split"),
    ('end = text.find("\\n---", 3)', "frontmatter_split"),
    ('parts = text.split("---", 2)', "frontmatter_split"),
    ('import re\nre.search(r"^\\s*promoted:\\s*(.+)$", t)', "key_scrape"),
    ('import re\nre.search(rf"^\\s*{key}:\\s*(.+)$", t, re.M)', "key_scrape"),
    ('import re\nre.match(r"\\s*" + re.escape(key) + r"\\s*:\\s*(.*)", line)', "key_scrape"),
    ('import re\nre.compile(r"^(\\s*)occurrences:[ \\t]*(.*)$")', "key_scrape"),
    ('import re\nR = re.compile(r"\\b(TRIPPED|OWED|REOPENED)\\b")', "gate_grammar"),
    ('import re\nR = re.compile(r"\\b(\\d+)(?:st|nd|rd|th)\\s+fire")', "gate_grammar"),
    ('V = ("no", "yes", "partial", "hook")', "promoted_vocab"),
    ('V = {"exit1", "exit2", "terminal"}', "exit_paths"),
    ('K = ["friction", "block-as-designed"]', "friction_kinds"),
    ('H = {"MACHINERY": 60, "FACT": 180}', "half_life"),
    ('ok = v.startswith("yes")', "promoted_predicate"),
    ('ok = v.lower().startswith("partial")', "promoted_predicate"),
    ('PREFIX = "RESOLVED"', "resolved_prefix"),
])
def test_detector_finds_each_signature(src, sig):
    assert sig in {s for _, s in findings_in_source(src)}


@pytest.mark.parametrize("src", [
    'def f():\n    """A --- block and promoted: yes in prose."""',
    'SEP = "|---|---|"',
    'print("---")',
    'out = "---\\nvoice: self\\n---\\n"',
    'import re\nre.search(r"/\\*" + name + r":START\\*/", t)',
    'import re\nre.search(r"promoted", t)',
    'import re\nre.search(r"(\\d+) passed", t)',
    'V = ("yes", "no", "maybe")',
    'ok = v.startswith("y")',
    'X = "RESOLVED " + note',
])
def test_detector_ignores_lookalikes(src):
    assert findings_in_source(src) == []


# ------------------------------------------------------------------ the real tree

def test_detector_finds_todays_known_sites():
    """Real data: each site named in the B1 brief is seen on today's tree. Deleted by the
    cutover commit together with the sites."""
    found = tree_findings()
    expected = {
        "tools/scan_promotion_candidates.py": {"frontmatter_split", "gate_grammar", "promoted_predicate"},
        "tools/promotion_schema.py": {"frontmatter_split", "gate_grammar", "promoted_vocab",
                                      "exit_paths", "promoted_predicate"},
        "tools/detector_run.py": {"frontmatter_split"},
        "tools/apply_memory_verdicts.py": {"frontmatter_split", "key_scrape"},
        "tools/backfill_memory_schema.py": {"frontmatter_split", "key_scrape"},
        "tools/memory_prepass.py": {"frontmatter_split", "gate_grammar"},
        "tools/merge_mining_verdicts.py": {"frontmatter_split"},
        "tools/workstream_probe.py": {"frontmatter_split", "key_scrape"},
        # key_scrape removed 2026-11-25 (S1): read_memory iterates learning_state.iter_records.
        "tools/learning_ledger.py": {"promoted_predicate"},
        "tools/friction_log.py": {"resolved_prefix"},
    }
    for path, sigs in expected.items():
        assert sigs <= {s for _, s in found.get(path, [])}, path
    # Equality on the FILE set (Grok r1 F4): a 12th parser in a new file fails here, while
    # the tree-wide test is still xfail. The cutover deletes this test with the sites.
    assert set(found) == set(expected) | set(ALLOWLIST)


def test_allowlist_entries_have_reasons_and_still_match():
    found = tree_findings()
    for path, reason in ALLOWLIST.items():
        assert len(reason) > 30, path
        assert path in found, f"{path} is allowlisted but no longer matches: remove it"
        assert {s for _, s in found[path]} <= {"frontmatter_split", "key_scrape"}, path


def test_the_owners_exist():
    for name in OWNERS:
        assert (TOOLS / name).is_file()


@pytest.mark.xfail(strict=True, reason=(
    "2026-11-22: 11 state-parsing sites remain until the one-commit reader cutover "
    "(the 8 named frontmatter parsers, learning_ledger.read_memory's fm(), the gate "
    "grammars in scan_promotion_candidates / promotion_schema / memory_prepass, and "
    "friction_log's RESOLVED prefix). The cutover removes this mark."))
def test_no_state_parsing_outside_the_two_modules():
    offenders = {p: f for p, f in tree_findings().items() if p not in ALLOWLIST}
    assert offenders == {}, "\n".join(f"{p}: {f}" for p, f in offenders.items())


# ------------------------------------------------------------------ Codex r1 F2: realistic false negatives

@pytest.mark.parametrize("src,sig", [
    ('import re as rx\nrx.search(r"^\\s*promoted:", text)', "key_scrape"),
    ('from re import search\nsearch(r"^\\s*promoted:", text)', "key_scrape"),
    ('from re import compile as c\nR = c(r"\\b(TRIPPED|OWED)\\b")', "gate_grammar"),
    ('SEP = "---"\nparts = text.split(SEP, 2)', "frontmatter_split"),
    ('def f(text):\n    for line in text.splitlines():\n        key, value = line.split(":", 1)\n'
     '        if key == "promoted":\n            state = value', "key_scrape"),
    ('def f(line):\n    k, _, v = line.partition(":")\n    return v if k.strip() == "reopen_gate" else None',
     "key_scrape"),
    ('STATE = {"no": "open", "yes": "closed", "partial": "partial", "hook": "closed"}', "promoted_vocab"),
    ('resolved = fix.startswith("RESOLVED")', "resolved_prefix"),
])
def test_detector_finds_codex_r1_false_negatives(src, sig):
    assert sig in {s for _, s in findings_in_source(src)}


@pytest.mark.parametrize("src", [
    'def f(t):\n    h, m = t.split(":", 1)\n    return int(h)',
    'def f(line):\n    key, value = line.split(":", 1)\n    return key == "name"',
    'import re as rx\nrx.search(r"\\d+ passed", t)',
])
def test_detector_r1_lookalikes_stay_clean(src):
    assert findings_in_source(src) == []
