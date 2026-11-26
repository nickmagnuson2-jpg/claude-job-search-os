"""Finding the main checkout from a linked worktree has ONE definition: vault_paths.main_checkout.

11-26 swarm S4 (run 3 finalist 1). Before this, five tools each placed the main checkout
themselves, by parsing the worktree's `.git` file or by asking git for `--git-common-dir`, and
the copies drifted (7 worktree-ledger friction rows, 10 occurrences since 11-21). Callers now
import vault_paths.main_checkout; a caller that must fail closed wraps it (see
cross_model_gate.main_checkout_of) instead of re-deriving the path.

This test fails when a new copy appears: any code string (not a docstring or comment) in
tools/*.py outside vault_paths.py that names `--git-common-dir` or parses a `gitdir:` line.
A site that stays needs an entry in ALLOWED with the reason its behaviour differs.

Kept and not matched by the markers: codex_verify._main_checkout, which asks `git worktree
list` and rejects a --separate-git-dir holder; it places main for the review sandbox, where
vault_paths' fall-back to root would leave the main checkout readable. finding_write and
audit_pii_record reuse it, so the review and audit ledgers place main the same way.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
OWNER = "vault_paths.py"
MARKERS = ("--git-common-dir", "gitdir:")

# (file, enclosing function) -> why it is not a copy of vault_paths.main_checkout.
ALLOWED = {
    ("land_branch.py", "trusted_tools"):
        "the common dir's parent, which also resolves FROM the main checkout, and None on "
        "failure; vault_paths falls back to root, i.e. the candidate's own tools (Codex r2 F2)",
    ("suite_record.py", "common_dir"):
        "returns the git common dir itself (the suite ledger lives there), not the checkout",
    ("suite_record.py", "admin_dir"):
        "reads git's per-worktree admin dir for cleanup, not the main checkout",
    ("mutation_check.py", "_is_linked_worktree"):
        "a yes/no predicate (git-dir != git-common-dir) that fails closed, not a locator",
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _sites(path: Path) -> set[tuple[str, str]]:
    """(file, innermost enclosing function or '<module>') for each code string naming a marker."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstring_nodes(tree)
    found = set()

    def visit(node: ast.AST, func: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs and any(m in node.value for m in MARKERS)):
            found.add((path.name, func))
        for child in ast.iter_child_nodes(node):
            visit(child, func)

    visit(tree, "<module>")
    return found


def _all_sites() -> set[tuple[str, str]]:
    sites = set()
    for path in sorted(TOOLS.glob("*.py")):
        if path.name != OWNER:
            sites |= _sites(path)
    return sites


def test_no_main_checkout_resolver_outside_vault_paths():
    extra = sorted(_all_sites() - set(ALLOWED))
    assert not extra, (
        "a main-checkout resolver outside vault_paths.py; import vault_paths.main_checkout "
        f"(wrap it to fail closed if needed) or add an ALLOWED entry with a reason: {extra}")


def test_every_allowed_site_still_exists():
    """A stale entry would let a new copy hide behind an old name."""
    stale = sorted(set(ALLOWED) - _all_sites())
    assert not stale, f"ALLOWED entries no longer match any code: {stale}"


def test_every_allowed_site_has_a_reason():
    assert all(len(reason) > 20 for reason in ALLOWED.values())


def test_the_scan_sees_a_copy(tmp_path):
    """The scanner itself: a code string is found, a docstring and a comment are not."""
    f = tmp_path / "x.py"
    f.write_text('def f():\n    """gitdir: in a docstring"""\n    # --git-common-dir in a comment\n'
                 '    return ["git", "rev-parse", "--git-common-dir"]\n'
                 'def g():\n    """only --git-common-dir prose"""\n', encoding="utf-8")
    assert _sites(f) == {("x.py", "f")}


def test_the_owner_still_defines_it():
    tree = ast.parse((TOOLS / OWNER).read_text(encoding="utf-8"))
    assert "main_checkout" in {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
