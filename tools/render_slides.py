#!/usr/bin/env python3
"""Render each .slide in a deck HTML to its own PNG, one page per file.

PROMOTED from an engagement's scripts/ directory on 2026-09-21. It HARDCODED a path to
Google Chrome with no fallback; discovery now comes from tools/chrome_runner.py. What
stays here is policy: isolating one .slide per page, and writing the temp page BESIDE
the deck so asset-relative paths keep resolving.
"""


import argparse
import pathlib
import re
import shutil
from chrome_runner import ChromeNotFound, require_chrome, run as chrome_run
import sys
import tempfile

# Chrome discovery moved to tools/chrome_runner.py on promotion. This module used to
# HARDCODE /Applications/Google Chrome.app, which fails on any machine with Chromium
# instead -- and fails by producing no PNG rather than by saying why.
W, H = 1280, 720


def slide_count(html: str) -> int:
    return len(re.findall(r'class="slide[^"]*"', html))


def isolate(html: str, index: int) -> str:
    """Return the deck with only slide `index` (1-based) visible."""
    n = [0]

    def tag(m):
        n[0] += 1
        return f'{m.group(0)[:-1]} data-page="{n[0]}">'

    marked = re.sub(r'<div class="slide[^"]*"[^>]*>', tag, html, count=0)
    css = (
        "<style>html,body{margin:0;padding:0;background:#fff}"
        ".slide{margin:0 !important}"
        f'.slide:not([data-page="{index}"]){{display:none !important}}'
        "</style>"
    )
    return marked + css


def render(deck: pathlib.Path, out_dir: pathlib.Path, stem: str,
           chrome: str | None = None) -> list[pathlib.Path]:
    chrome = require_chrome(chrome)
    html = deck.read_text(encoding="utf-8")
    count = slide_count(html)
    if count == 0:
        sys.exit(f"no .slide blocks found in {deck}")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        # asset-relative paths keep working only if the temp file sits beside the deck
        for i in range(1, count + 1):
            page = deck.parent / f".render-p{i}.html"
            page.write_text(isolate(html, i), encoding="utf-8")
            shot = tmp / f"p{i}.png"
            proc = chrome_run(
                chrome,
                [
                    "--hide-scrollbars",
                    "--force-device-scale-factor=1",
                    "--default-background-color=FFFFFFFF",
                    f"--window-size={W},{H}",
                    "--virtual-time-budget=6000",
                    f"--screenshot={shot}",
                    page.as_uri(),
                ],
                timeout=120,
            )
            page.unlink()
            # check=True used to cover this. Chrome can exit 0 and write nothing, which
            # is the failure this repo names most often, so the ARTIFACT is the test.
            if proc.returncode != 0 or not shot.exists() or shot.stat().st_size == 0:
                raise RuntimeError(
                    f"Chrome produced no PNG for slide {i} (exit {proc.returncode}). "
                    f"{proc.stderr[-400:]}")
            dest = out_dir / f"{stem}-s{i}.png"
            shutil.copyfile(shot, dest)
            written.append(dest)
            print(f"  rendered page {i} -> {dest}")
    return written


def main(argv: list[str] | None = None) -> int:
    """Extracted from the __main__ block on promotion.

    Inline argparse under `if __name__` gives a tool no testable entry point: the CLI
    contract can only be exercised by spawning a subprocess, so in practice it is not
    exercised at all. The dispatch line below stays untestable by construction; every
    decision it makes now lives here.
    """
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("deck")
    ap.add_argument("--out", default="slides")
    ap.add_argument("--stem", default="render")
    ap.add_argument("--chrome", default=None,
                    help="explicit browser path; checked, never trusted")
    a = ap.parse_args(argv)

    deck = pathlib.Path(a.deck)
    if not deck.exists():
        print("cannot read " + str(deck), file=sys.stderr)
        return 4
    try:
        render(deck.resolve(), pathlib.Path(a.out), a.stem, chrome=a.chrome)
    except ChromeNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
