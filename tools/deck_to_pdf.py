#!/usr/bin/env python3
"""Print a screen-authored deck to a single 16:9 PDF, one slide per page.

PROMOTED from an engagement's scripts/ directory on 2026-09-21. It carried its own
byte-identical copy of Chrome discovery; that mechanism now lives in
tools/chrome_runner.py and this module keeps only its POLICY: the print CSS that turns
1280x720 screen boxes into pages, and what counts as a produced PDF.
"""


from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# A script in tools/ already has tools/ on sys.path, so these are plain sibling imports.
# The browser is located AND invoked through the shared runner: importing only
# find_chrome and then hand-rolling the subprocess call would leave the base flags
# duplicated here, which is half the duplication this promotion exists to remove.
from chrome_runner import ChromeNotFound, render_with_injection, require_chrome

W, H = 1280, 720

PRINT_CSS = """
<style>
  @page { size: %dpx %dpx; margin: 0; }
  html, body { background: #fff !important; margin: 0 !important; padding: 0 !important; }
  .slide {
    margin: 0 !important;
    box-shadow: none !important;
    break-after: page;
    page-break-after: always;
  }
  .slide:last-of-type { break-after: auto; page-break-after: auto; }
  /* Chrome drops background fills when printing unless this is set, and every tinted
     callout and every chart bar on these pages IS a background fill. */
  * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
</style>
""" % (W, H)




def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("deck")
    ap.add_argument("--out", required=True)
    ap.add_argument("--chrome")
    args = ap.parse_args()

    # Relative paths resolve from the CALLER'S cwd. The engagement-local original
    # resolved them against its own directory, which was right when the script lived
    # beside the deck and produces a doubled path now that it lives in tools/.
    deck = Path(args.deck)
    if not deck.exists():
        print("cannot read " + str(deck), file=sys.stderr)
        return 4

    try:
        chrome = require_chrome(args.chrome)
    except ChromeNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 4

    out = Path(args.out)
    if not out.is_absolute():
        out = deck.parent / out

    proc, tmpdir, _src = render_with_injection(
        deck, PRINT_CSS, chrome,
        ["--no-pdf-header-footer", "--virtual-time-budget=6000",
         "--print-to-pdf=" + str(out)],
        timeout=120, prefix="deckpdf-")
    try:
        # A zero-byte or absent PDF after a zero exit is the shape this repo names most
        # often: the command returned, so the work is assumed done. Check the artifact.
        if not out.exists() or out.stat().st_size == 0:
            print("Chrome produced no PDF (exit %d)\n%s"
                  % (proc.returncode, proc.stderr[-800:]), file=sys.stderr)
            return 1
        print("wrote %s, %.1f KB" % (out, out.stat().st_size / 1024))
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
