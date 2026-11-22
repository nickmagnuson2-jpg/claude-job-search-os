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


def _flatten(node) -> str | None:
    """The literal text of a string expression, with HOLE for each non-literal part; None
    when the expression is not a string literal, an f-string or a + of them."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else HOLE for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _flatten(node.left), _flatten(node.right)
        if left is None and right is None:
            return None
        return (HOLE if left is None else left) + (HOLE if right is None else right)
    return None


def _str_consts(elts) -> set:
    return {e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def findings_in_source(src: str) -> list[tuple[int, str]]:
    """(line, signature) for every state-parsing or state-defining site in `src`."""
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.args:
            attr = n.func.attr
            arg = _flatten(n.args[0])
            is_re = isinstance(n.func.value, ast.Name) and n.func.value.id == "re"
            if is_re and attr in RE_FUNCS and arg is not None:
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
        elif isinstance(n, (ast.Tuple, ast.List, ast.Set)):
            vals = _str_consts(n.elts)
            if len(vals & TOKENS) >= 3 and vals & {"yes", "partial"} and vals & TIERS:
                out.append((n.lineno, "promoted_vocab"))
            if {"exit1", "exit2"} <= vals:
                out.append((n.lineno, "exit_paths"))
            if "block-as-designed" in vals:
                out.append((n.lineno, "friction_kinds"))
        elif isinstance(n, ast.Dict):
            if "MACHINERY" in _str_consts([k for k in n.keys if k is not None]):
                out.append((n.lineno, "half_life"))
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
        "tools/learning_ledger.py": {"key_scrape", "promoted_predicate"},
        "tools/friction_log.py": {"resolved_prefix"},
    }
    for path, sigs in expected.items():
        assert sigs <= {s for _, s in found.get(path, [])}, path


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
