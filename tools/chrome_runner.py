#!/usr/bin/env python3
"""Find headless Chrome, and run it against a deck. The mechanism, once.

WHY THIS MODULE EXISTS. Three copies of this were running by 2026-09-21:

  tools/check_deck_geometry.py   CHROME_CANDIDATES + find_chrome(), its own
  deck_to_pdf.py                 CHROME_CANDIDATES + find_chrome(), BYTE-IDENTICAL
  render_slides.py               a HARDCODED path to Google Chrome, no fallback at all

The third is the reason this is not merely tidiness. A hardcoded
`/Applications/Google Chrome.app/...` fails on any machine that installed Chromium
instead, and it fails by producing no output rather than by saying why. The two
identical copies would have drifted the first time one of them learned a new flag.

WHAT IS MECHANISM AND WHAT IS NOT. Locating a browser and invoking it headless is
identical for every caller, so it lives here. What gets INJECTED into the deck (a
geometry probe, print CSS, slide isolation) and what gets EXTRACTED afterwards (a DOM
blob, a PDF file, a PNG per slide) differ completely, and stay with the caller. A
shared "render a deck" function that tried to own both would be the same mistake one
level up.

FAILURE IS LOUD. `find_chrome` returns None rather than a guessed path, and `run`
raises on a timeout instead of returning an empty result that reads like a clean one.
A browser that never started and a page with nothing on it produce the same empty
output, which is the defect this repo names most often.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

# Order matters: an explicit Google Chrome install wins over a PATH entry, because a
# `chromium` on PATH may be a snap wrapper with a different sandbox story.
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
)

# Common to every caller. Per-caller flags (--dump-dom, --print-to-pdf, --screenshot)
# are the caller's business and are passed through.
BASE_ARGS = ("--headless", "--disable-gpu", "--no-sandbox")


class ChromeNotFound(RuntimeError):
    """No browser was located. Distinct from 'the browser ran and produced nothing'."""


def find_chrome(explicit: str | None = None) -> str | None:
    """Return a usable Chrome path, or None. Never guesses.

    `explicit` is checked the same way as a candidate, so a bad --chrome argument
    reports as not-found rather than being handed to subprocess to fail obscurely.
    """
    if explicit:
        return explicit if (Path(explicit).exists() or shutil.which(explicit)) else None
    for c in CHROME_CANDIDATES:
        if Path(c).exists() or shutil.which(c):
            return c
    return None


def require_chrome(explicit: str | None = None) -> str:
    """find_chrome, but raising with the list that was searched.

    A caller that needs a browser should fail here, naming what it looked for, rather
    than three frames later on an empty output it cannot explain.
    """
    chrome = find_chrome(explicit)
    if chrome:
        return chrome
    raise ChromeNotFound(
        "no headless browser found. Searched, in order: "
        + ", ".join(CHROME_CANDIDATES)
        + (f" (and the explicit path {explicit!r})" if explicit else "")
    )


def run(chrome: str, args: list[str] | tuple[str, ...], timeout: int = 90):
    """Invoke Chrome headless with BASE_ARGS + `args`. Returns the CompletedProcess.

    Raises on timeout rather than returning a partial result: a run that was killed
    half way and a run that found nothing produce the same empty stdout, and the
    caller cannot tell them apart afterwards.
    """
    return subprocess.run(
        [chrome, *BASE_ARGS, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def render_with_injection(deck: Path, injected: str, chrome: str,
                          args: list[str] | tuple[str, ...], timeout: int = 90,
                          prefix: str = "deck-"):
    """Append `injected` to the deck HTML, write it to a temp file, and run Chrome on it.

    The temp file is NOT cleaned up here: every caller so far needs to read something
    Chrome wrote beside it, and a helper that deleted the directory out from under them
    would be a helper that only works for the caller it was written for. Callers own
    the returned directory.

    Returns (CompletedProcess, tmpdir, source_path).
    """
    tmpdir = Path(tempfile.mkdtemp(prefix=prefix))
    src = tmpdir / "page.html"
    src.write_text(deck.read_text(encoding="utf-8") + injected, encoding="utf-8")
    proc = run(chrome, [*args, str(src)], timeout=timeout)
    return proc, tmpdir, src
