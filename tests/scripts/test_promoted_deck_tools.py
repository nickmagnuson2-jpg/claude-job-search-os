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


# --------------------------------------------------------------------------
# Horizontal clipping. fit_chart_viewbox adjusts y and height only, and for a
# while it also LOOKED at y and height only -- while getBBox() handed it x and
# width in the same call. A shipped deck carried 588.37 units of ink in a 579.94
# unit viewBox, so a label read "billin", and this tool printed "converged" and
# exited 0 on it. Not touching x and width is correct. Not reading them was not.
# --------------------------------------------------------------------------


def _bb(x=0.0, w=100.0):
    return {"x": x, "y": 0.0, "w": w, "h": 50.0}


def test_no_clip_when_the_ink_sits_inside_the_viewbox():
    from fit_chart_viewbox import horizontal_clip

    assert horizontal_clip(_bb(x=0.0, w=100.0), x0=0.0, w0=100.0) == []


def test_a_boundary_stroke_is_not_reported_as_a_clip():
    """Half a 1.2-unit stroke painted on the edge reads as 0.6 outside. That is ink on
    the boundary, not content lost, and failing on it would make the rule unusable."""
    from fit_chart_viewbox import horizontal_clip

    assert horizontal_clip(_bb(x=-0.6, w=101.2), x0=0.0, w0=100.0) == []


def test_a_clipped_glyph_on_the_right_is_reported_with_its_overshoot():
    from fit_chart_viewbox import horizontal_clip

    (side, over), = horizontal_clip(_bb(x=0.0, w=108.43), x0=0.0, w0=100.0)
    assert side == "right"
    assert over == pytest.approx(8.43)


def test_a_clipped_glyph_on_the_left_is_reported():
    from fit_chart_viewbox import horizontal_clip

    assert horizontal_clip(_bb(x=-5.0, w=105.0), x0=0.0, w0=100.0) == [("left", 5.0)]


def test_both_edges_are_reported_when_both_clip():
    from fit_chart_viewbox import horizontal_clip

    sides = [s for s, _ in horizontal_clip(_bb(x=-5.0, w=110.0), x0=0.0, w0=100.0)]
    assert sides == ["left", "right"]


def test_a_non_zero_viewbox_origin_is_respected():
    """x0 is not always 0. Measuring against 0 would report a clip on every offset chart."""
    from fit_chart_viewbox import horizontal_clip

    assert horizontal_clip(_bb(x=12.0, w=100.0), x0=12.0, w0=100.0) == []


# --------------------------------------------------------------------------
# main() and measure(), which nothing covered. fit_chart_viewbox was promoted on
# 2026-09-21 with its Chrome-discovery property pinned and its OWN behaviour
# untested: 30 of 38 mutants survived, including "drop the clipped warning" and
# "return None instead of the exit code". A tool whose job is measuring cannot
# be the one thing nobody measures.
#
# Chrome is stubbed. These assert the tool's decisions, not the browser's.
# --------------------------------------------------------------------------


@pytest.fixture
def chart(tmp_path):
    p = tmp_path / "c.svg"
    p.write_text('<svg viewBox="0 0 100 50"><rect x="0" y="0" width="100" height="50"/></svg>',
                 encoding="utf-8")
    return p


@pytest.fixture
def stub_chrome(monkeypatch):
    """Replace browser discovery and measurement; yield a list to script the bboxes."""
    import fit_chart_viewbox as f

    boxes = []
    monkeypatch.setattr(f, "require_chrome", lambda _p: "/fake/chrome")
    monkeypatch.setattr(f, "measure", lambda _t, _c: boxes.pop(0))
    return boxes


def _run(argv):
    import fit_chart_viewbox as f

    return f.main(argv)


def test_a_converged_fit_with_nothing_clipped_exits_zero(chart, stub_chrome, capsys):
    stub_chrome.append({"x": 0.0, "y": 0.0, "w": 100.0, "h": 50.0})
    assert _run(["--svg", str(chart)]) == 0
    out = capsys.readouterr().out
    assert "converged" in out
    # The resulting viewBox is echoed. A "converged" with no numbers beside it is a claim
    # the reader cannot check against the file.
    assert "viewBox 0.00 0.00 100.00 50.00" in out
    assert "x and width untouched" in out


def test_a_converged_fit_with_clipped_content_exits_three(chart, stub_chrome, capsys):
    """The sankey's exact shape: vertically converged, horizontally overflowing. This
    combination printed 'converged' and exited 0, and a clipped word shipped."""
    stub_chrome.append({"x": 0.0, "y": 0.0, "w": 108.43, "h": 50.0})
    assert _run(["--svg", str(chart)]) == 3
    out = capsys.readouterr().out
    assert "CLIPPED" in out and "8.43" in out and "right" in out


def test_the_clip_is_reported_on_stdout_not_only_in_the_exit_code(chart, stub_chrome, capsys):
    stub_chrome.append({"x": 0.0, "y": 0.0, "w": 108.43, "h": 50.0})
    _run(["--svg", str(chart)])
    assert "CLIPPED" in capsys.readouterr().out


def test_a_loose_viewbox_is_tightened_then_converges(chart, stub_chrome, capsys):
    chart.write_text('<svg viewBox="0 -20 100 90"><rect/></svg>', encoding="utf-8")
    stub_chrome.append({"x": 0.0, "y": 0.0, "w": 100.0, "h": 50.0})
    stub_chrome.append({"x": 0.0, "y": 0.0, "w": 100.0, "h": 50.0})
    assert _run(["--svg", str(chart)]) == 0
    assert 'viewBox="0 0.00 100 50.00"' in chart.read_text(encoding="utf-8")
    assert "tightened" in capsys.readouterr().out


def test_a_missing_file_exits_four_and_says_so(tmp_path, capsys):
    assert _run(["--svg", str(tmp_path / "absent.svg")]) == 4
    assert "cannot read" in capsys.readouterr().err


def test_a_file_without_a_viewbox_exits_one(tmp_path, stub_chrome, capsys):
    p = tmp_path / "n.svg"
    p.write_text("<svg><rect/></svg>", encoding="utf-8")
    assert _run(["--svg", str(p)]) == 1
    assert "no viewBox" in capsys.readouterr().err


def test_failing_to_converge_exits_one_rather_than_reporting_success(chart, stub_chrome,
                                                                    capsys):
    """Each pass moves the box and the next measurement moves again. Two passes, no
    convergence: the caller must not read that as a fitted chart."""
    for shift in (10.0, 20.0, 30.0):
        stub_chrome.append({"x": 0.0, "y": shift, "w": 100.0, "h": 50.0})
    assert _run(["--svg", str(chart), "--max-passes", "2"]) == 1
    assert "did not converge in 2 passes" in capsys.readouterr().err


def test_max_passes_is_honoured(chart, stub_chrome):
    for shift in (10.0, 20.0, 30.0, 40.0):
        stub_chrome.append({"x": 0.0, "y": shift, "w": 100.0, "h": 50.0})
    _run(["--svg", str(chart), "--max-passes", "3"])
    assert len(stub_chrome) == 1  # three consumed, one left


def test_an_absent_browser_exits_four_and_does_not_look_like_a_clean_fit(chart, monkeypatch,
                                                                        capsys):
    import fit_chart_viewbox as f

    def boom(_p):
        raise f.ChromeNotFound("no browser here")

    monkeypatch.setattr(f, "require_chrome", boom)
    assert _run(["--svg", str(chart)]) == 4
    assert "no browser here" in capsys.readouterr().err


def test_measure_raises_when_the_probe_reports_nothing(monkeypatch):
    """An empty probe and a chart with no ink must not look alike."""
    import subprocess
    import fit_chart_viewbox as f

    monkeypatch.setattr(
        f, "chrome_run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, '<pre id="__bb"></pre>', ""))
    with pytest.raises(RuntimeError, match="no bounding box"):
        f.measure("<svg/>", "/fake/chrome")


def test_measure_parses_the_probe_payload(monkeypatch):
    import subprocess
    import fit_chart_viewbox as f

    monkeypatch.setattr(
        f, "chrome_run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0, '<pre id="__bb">{"x":1,"y":2,"w":3,"h":4}</pre>', ""))
    assert f.measure("<svg/>", "/fake/chrome") == {"x": 1, "y": 2, "w": 3, "h": 4}
