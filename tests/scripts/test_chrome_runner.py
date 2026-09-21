"""Tests for tools/chrome_runner.py, the one browser-discovery mechanism.

WHY THESE LIVE HERE. Browser discovery was implemented three times: twice byte-identically
and once as a hardcoded path with no fallback. Its tests lived in the deck-geometry suite,
which is a CONSUMER. Testing a generalizable input at a consumer means the test patches a
re-export, passes for the wrong reason, and has to be rewritten every time another caller
appears. The mechanism is tested once, here; each consumer tests only what it alone does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import chrome_runner as cr  # noqa: E402


def test_first_existing_candidate_wins(tmp_path, monkeypatch):
    a, b = tmp_path / "one", tmp_path / "two"
    b.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cr, "CHROME_CANDIDATES", (str(a), str(b)))
    assert cr.find_chrome() == str(b)


def test_order_is_respected_not_just_existence(tmp_path, monkeypatch):
    """An explicit Chrome install must beat a PATH entry, so order is load-bearing."""
    a, b = tmp_path / "one", tmp_path / "two"
    a.write_text("#!/bin/sh\n", encoding="utf-8")
    b.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cr, "CHROME_CANDIDATES", (str(a), str(b)))
    assert cr.find_chrome() == str(a)


def test_returns_none_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "CHROME_CANDIDATES", (str(tmp_path / "nope"),))
    assert cr.find_chrome() is None


def test_explicit_path_is_checked_not_trusted(tmp_path, monkeypatch):
    """A bad --chrome argument must read as NOT FOUND, not be handed to subprocess.

    Returning the string unchecked would defer the failure to an obscure OSError three
    frames later, on a run the caller then cannot explain.
    """
    monkeypatch.setattr(cr, "CHROME_CANDIDATES", ())
    assert cr.find_chrome(str(tmp_path / "missing")) is None


def test_explicit_path_wins_when_it_exists(tmp_path, monkeypatch):
    good = tmp_path / "mychrome"
    good.write_text("#!/bin/sh\n", encoding="utf-8")
    other = tmp_path / "other"
    other.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cr, "CHROME_CANDIDATES", (str(other),))
    assert cr.find_chrome(str(good)) == str(good)


def test_require_chrome_raises_and_names_what_it_searched(tmp_path, monkeypatch):
    """The failure must carry the search list, or the next person re-derives it."""
    monkeypatch.setattr(cr, "CHROME_CANDIDATES", (str(tmp_path / "nope"),))
    with pytest.raises(cr.ChromeNotFound) as e:
        cr.require_chrome()
    assert "Searched, in order" in str(e.value)
    assert "nope" in str(e.value)


def test_require_chrome_names_the_explicit_path_too(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "CHROME_CANDIDATES", ())
    with pytest.raises(cr.ChromeNotFound) as e:
        cr.require_chrome("/no/such/chrome")
    assert "/no/such/chrome" in str(e.value)


def test_require_chrome_returns_the_hit(tmp_path, monkeypatch):
    good = tmp_path / "c"
    good.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cr, "CHROME_CANDIDATES", (str(good),))
    assert cr.require_chrome() == str(good)


def test_base_args_are_always_applied(tmp_path, monkeypatch):
    """Every caller needs headless/--disable-gpu/--no-sandbox; none should restate them."""
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["kw"] = kw
        return "done"

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    out = cr.run("/bin/chrome", ["--dump-dom", "x.html"], timeout=7)
    assert out == "done"
    assert seen["argv"][0] == "/bin/chrome"
    for flag in cr.BASE_ARGS:
        assert flag in seen["argv"]
    assert seen["argv"][-1] == "x.html", "caller args come after the base flags"
    assert seen["kw"]["timeout"] == 7


def test_injection_is_appended_and_the_source_is_written(tmp_path, monkeypatch):
    deck = tmp_path / "deck.html"
    deck.write_text("<div class='slide'>a</div>", encoding="utf-8")
    monkeypatch.setattr(cr, "run", lambda chrome, args, timeout=90: ("ran", args))
    proc, tmpdir, src = cr.render_with_injection(deck, "<!--INJECTED-->", "/bin/chrome",
                                                 ["--dump-dom"])
    body = src.read_text(encoding="utf-8")
    assert body.startswith("<div class='slide'>a</div>")
    assert body.endswith("<!--INJECTED-->")
    assert tmpdir.is_dir()
    assert str(src) in proc[1], "the source path must be passed to chrome"


def test_injection_dir_is_not_deleted(tmp_path, monkeypatch):
    """Callers read files Chrome wrote beside the source; cleaning up here would break
    every caller but the first one written."""
    deck = tmp_path / "d.html"
    deck.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cr, "run", lambda *a, **k: None)
    _, tmpdir, src = cr.render_with_injection(deck, "", "/bin/chrome", [])
    assert tmpdir.exists() and src.exists()
