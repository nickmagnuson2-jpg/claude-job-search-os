"""Contract tests for the deck tools promoted out of an engagement on 2026-09-21.

These do NOT re-test rendering: that is chrome_runner's job and it has its own suite, and
each tool was verified against the real artifact by hand (deck_to_pdf reproduced the sent
deliverable at 393,163 bytes across 2 pages; fit_chart_viewbox converged on the shipped
chart with zero slack and left its viewBox byte-identical).

What they pin is the property the promotion exists to create: NONE of these may carry its
own browser discovery again. Four copies existed before the extraction, one of them a
hardcoded path with no fallback. That is the regression worth a test.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

PROMOTED = ("deck_to_pdf", "render_slides", "fit_chart_viewbox")


@pytest.mark.parametrize("mod", PROMOTED)
def test_promoted_tool_imports(mod):
    __import__(mod)


@pytest.mark.parametrize("mod", PROMOTED)
def test_no_tool_redeclares_browser_discovery(mod):
    """The whole point. A second CHROME_CANDIDATES here is the duplication returning."""
    src = (TOOLS / f"{mod}.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    assigned = {
        t.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    assert "CHROME_CANDIDATES" not in assigned, f"{mod} re-declared the candidate list"
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "find_chrome" not in defined, f"{mod} re-implemented find_chrome"


@pytest.mark.parametrize("mod", PROMOTED)
def test_no_tool_hardcodes_a_browser_path(mod):
    """render_slides shipped a literal /Applications/Google Chrome.app path for weeks."""
    src = (TOOLS / f"{mod}.py").read_text(encoding="utf-8")
    body = "\n".join(
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert "/Applications/Google Chrome.app" not in body, f"{mod} hardcodes a browser"


@pytest.mark.parametrize("mod", PROMOTED)
def test_each_tool_sources_chrome_from_the_shared_module(mod):
    src = (TOOLS / f"{mod}.py").read_text(encoding="utf-8")
    imported = [
        n.module
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.ImportFrom) and n.module
    ]
    assert "chrome_runner" in imported, f"{mod} does not use the shared runner"


@pytest.mark.parametrize("mod", PROMOTED)
def test_missing_browser_is_reported_not_silently_empty(mod, monkeypatch, capsys):
    """No browser and 'ran, produced nothing' must not look alike at the exit code."""
    import chrome_runner as cr

    monkeypatch.setattr(cr, "CHROME_CANDIDATES", ())
    m = __import__(mod)
    argv = {
        "deck_to_pdf": [mod, "/no/deck.html", "--out", "/tmp/x.pdf"],
        "render_slides": [mod, "/no/deck.html", "--out", "/tmp/x"],
        "fit_chart_viewbox": [mod, "--svg", "/no/chart.svg"],
    }[mod]
    monkeypatch.setattr(sys, "argv", argv)
    rc = m.main()
    assert rc != 0, "a missing deck or missing browser must not exit 0"
