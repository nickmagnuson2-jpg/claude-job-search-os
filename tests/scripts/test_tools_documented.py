"""Every tracked tools/*.py is findable: named in docs/tools-reference.md or docs/hooks.md, or
declared in tests/scripts/tools_doc_allowlist.json with a reason.

Why: the retrieval-capability Hard Rule fired on tools that existed but were not found. On
2026-11-25, 111 of 246 tracked tools were named in neither doc. Same shape as
tools/coverage_invariant.py: nothing is exempt by default, every exception is a declared entry
with a reason, and the allowlist can only shrink (an entry for a tool that is gone or now
documented fails). Tracked files only: gitignored private tools must not be named in a public doc.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCS = ("docs/tools-reference.md", "docs/hooks.md")
ALLOWLIST = REPO / "tests" / "scripts" / "tools_doc_allowlist.json"


def tracked_tools(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "tools/*.py"], cwd=root, check=True,
                         capture_output=True, text=True).stdout
    return sorted(Path(p).name for p in out.split() if p.count("/") == 1)


def named_in(text: str, name: str) -> bool:
    """`x.py` must not match inside `foo_x.py` or `tools/sub.x.py`."""
    return re.search(r"(?<![\w.-])" + re.escape(name) + r"(?!\w)", text) is not None


def undocumented(tools: list[str], docs_text: str) -> list[str]:
    return [t for t in tools if not named_in(docs_text, t)]


def check(tools: list[str], docs_text: str, allow: dict) -> list[str]:
    """Return failure lines; empty means the ratchet holds."""
    if not tools:
        return ["no tracked tools/*.py found: the enumeration is broken, not the docs"]
    if not docs_text.strip():
        return ["both docs are empty: nothing was read"]
    errs = []
    missing = undocumented(tools, docs_text)
    for t in missing:
        if t not in allow:
            errs.append(f"{t}: named in neither {DOCS[0]} nor {DOCS[1]}; add one line, "
                        f"or an allowlist entry with a reason")
    for t, why in allow.items():
        if not isinstance(why, str) or not why.strip():
            errs.append(f"{t}: allowlist entry has no reason")
        elif t not in tools:
            errs.append(f"{t}: allowlisted but no longer a tracked tool; remove the entry")
        elif t not in missing:
            errs.append(f"{t}: allowlisted but now documented; remove the entry")
    return errs


def _docs_text(root: Path) -> str:
    return "\n".join((root / d).read_text(encoding="utf-8") for d in DOCS)


# ---- fixtures -------------------------------------------------------------------------------

def test_named_in_is_bounded():
    assert named_in("see `foo.py` here", "foo.py")
    assert named_in("| `foo.py` | x |", "foo.py")
    assert not named_in("see `bar_foo.py`", "foo.py")
    assert not named_in("see `foo.pyc`", "foo.py")
    assert not named_in("see foo-x.py", "x.py")


def test_undocumented_tool_fails_and_documented_passes():
    assert check(["a.py", "b.py"], "`a.py` and `b.py`", {}) == []
    errs = check(["a.py", "b.py"], "`a.py` only", {})
    assert len(errs) == 1 and errs[0].startswith("b.py:")


def test_allowlist_needs_a_reason_and_only_shrinks():
    assert check(["a.py", "b.py"], "`a.py`", {"b.py": "library, no entry point"}) == []
    assert "no reason" in check(["a.py", "b.py"], "`a.py`", {"b.py": " "})[0]
    assert "no longer a tracked tool" in check(["a.py"], "`a.py`", {"gone.py": "r"})[0]
    assert "now documented" in check(["a.py"], "`a.py`", {"a.py": "r"})[0]


def test_empty_inputs_fail_loudly():
    assert check([], "`a.py`", {})[0].startswith("no tracked tools")
    assert check(["a.py"], "  \n", {})[0].startswith("both docs are empty")


def test_enumeration_is_tracked_files_only(tmp_path):
    root = tmp_path / "r"
    (root / "tools" / "sub").mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
    run("init", "-q")
    (root / ".gitignore").write_text("/tools/private.py\n")
    for p in ("tools/a.py", "tools/private.py", "tools/sub/deep.py"):
        (root / p).write_text("x = 1\n")
    run("add", ".")
    assert tracked_tools(root) == ["a.py"]


# ---- live ------------------------------------------------------------------------------------

def test_live_every_tracked_tool_is_documented_or_allowlisted():
    tools = tracked_tools(REPO)
    assert len(tools) > 100, f"only {len(tools)} tracked tools found; enumeration broken"
    allow = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    errs = check(tools, _docs_text(REPO), allow)
    assert not errs, "\n".join(errs)
