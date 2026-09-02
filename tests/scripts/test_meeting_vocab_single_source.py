"""Meeting classification must have exactly ONE definition.

Extracted from granola_auto_debrief.py on 2026-08-19. The rules were never
duplicated before the split -- granola_cli.py correctly imported them -- but they
lived inside a sibling ORCHESTRATOR, so the interactive /granola-pull path had to
import a 1149-line launchd script to classify one meeting.

This is the guard half of the CLAUDE.md rule: when a domain rule is shared by a
second tool, consolidate into one module PLUS a single-source-of-truth guard test,
never patch copies in parallel. Without this test the split is cosmetic — nothing
would stop a second `def classify_meeting` reappearing.
"""
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))

OWNED = {
    "classify_meeting",
    "load_therapy_classifier_config",
    "load_personal_projects_config",
    "match_personal_project",
    "_attendee_is_therapist",
    "_transcript_names_therapist",
    "_is_nick",
}


def _defs(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def test_meeting_vocab_owns_the_classifier():
    assert OWNED <= _defs(TOOLS / "meeting_vocab.py")


def test_no_second_definition_anywhere_in_tools():
    """A re-export is fine; a second `def` is the drift this guards against."""
    offenders = {}
    for py in TOOLS.glob("*.py"):
        if py.name == "meeting_vocab.py":
            continue
        dupes = OWNED & _defs(py)
        if dupes:
            offenders[py.name] = sorted(dupes)
    assert not offenders, f"classification redefined outside meeting_vocab.py: {offenders}"


def test_orchestrator_reexport_is_the_same_object():
    """granola_auto_debrief re-exports for back-compat; it must not shadow with a copy."""
    import granola_auto_debrief as gad
    import meeting_vocab as mv
    for name in sorted(OWNED):
        assert getattr(gad, name) is getattr(mv, name), f"{name} diverged from meeting_vocab"


def test_cli_imports_rules_from_vocab_not_the_launchd_script():
    """The whole point of the split: the interactive path must not pull its rules
    out of the scheduled-run orchestrator."""
    tree = ast.parse((TOOLS / "granola_cli.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "granola_auto_debrief" in node.module:
            pulled = {a.name for a in node.names}
            assert not (pulled & OWNED), (
                f"granola_cli imports {sorted(pulled & OWNED)} from the orchestrator; "
                "import them from meeting_vocab instead"
            )


def test_classifier_still_fails_closed():
    """Sealing property: an unclassifiable meeting is persisted NOWHERE, never guessed."""
    import meeting_vocab as mv
    verdict = mv.classify_meeting({"title": "", "attendees": [], "transcript": ""},
                                  config={"emails": set(), "names": set()})
    assert verdict == "unknown"


def test_reexport_branches_list_identical_symbols():
    """The try/except import branches must re-export the SAME set.

    Origin 2026-08-19, found by an adversarial review: the `except ModuleNotFoundError`
    branch (package-style import) omitted three symbols the `try` branch had, so
    `from tools.granola_auto_debrief import _speaker_label` raised ImportError and
    `persist_via_granola_save` carried a live NameError on that path.

    The full suite stayed GREEN through it: other test files call sys.path.insert
    during collection, so by the time this module is first imported the bare branch
    succeeds. Run alone, four tests failed. A green suite was therefore not evidence,
    and no ordinary test could catch it — this asserts the structure directly.
    """
    import ast
    src = (TOOLS / "granola_auto_debrief.py").read_text(encoding="utf-8")
    branches = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("meeting_vocab"):
            branches.append({a.name for a in node.names})
    assert len(branches) == 2, f"expected 2 re-export branches, found {len(branches)}"
    assert branches[0] == branches[1], (
        "re-export branches differ; package-style import would lose: "
        f"{sorted(branches[0] ^ branches[1])}"
    )


def test_every_reexported_symbol_actually_resolves_package_style():
    """Behavioural companion: import each symbol the package-style way for real."""
    import importlib
    mod = importlib.import_module("tools.granola_auto_debrief")
    import ast
    src = (TOOLS / "granola_auto_debrief.py").read_text(encoding="utf-8")
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("meeting_vocab"):
            names |= {a.name for a in node.names}
    missing = [n for n in sorted(names) if not hasattr(mod, n)]
    assert not missing, f"re-exported but unresolvable package-style: {missing}"
