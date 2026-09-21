#!/usr/bin/env python3
"""Tighten a chart's viewBox to its own ink, vertically, by MEASURING it.

PROMOTED from an engagement's scripts/ directory on 2026-09-21. It carried a FOURTH copy
of Chrome discovery -- found by searching tools/ before moving anything, which is step 1
of the promotion procedure in framework/analysis-method.md and the step that pays.
Discovery now comes from tools/chrome_runner.py.

WHY MEASURING RATHER THAN DECLARING. The horizontal extent of a chart is authored; the
vertical extent is whatever the renderer produced, and a viewBox that does not hug its
ink letterboxes the chart inside its column. A rule cannot see that; only a render can.
"""


from __future__ import annotations

import argparse
import json
import re
import shutil
from chrome_runner import ChromeNotFound, require_chrome, run as chrome_run
import sys
import tempfile
from pathlib import Path


# The face the deck renders in. Measuring the ink in a fallback face would tighten the
# viewBox to the wrong glyphs, so the page's own stylesheet link comes along.
HEAD = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        'family=Source+Sans+3:wght@400;600;700&display=swap">'
        '<style>body{margin:0;font-family:"Source Sans 3",Arial,sans-serif}'
        'text{font-family:"Source Sans 3",Arial,sans-serif}</style>')

PROBE = """
<pre id="__bb" style="display:none"></pre>
<script>
function go() {
  var el = document.querySelector('svg');
  var bb = el.getBBox();
  document.getElementById('__bb').textContent =
    JSON.stringify({x: bb.x, y: bb.y, w: bb.width, h: bb.height});
}
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(function () { setTimeout(go, 0); });
} else { window.addEventListener('load', go); }
</script>
"""




def measure(svg_text: str, chrome: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="chartfit-"))
    try:
        f = tmp / "c.html"
        f.write_text(HEAD + svg_text + PROBE, encoding="utf-8")
        # BASE_ARGS (--headless --disable-gpu --no-sandbox) and capture/text come from
        # chrome_run. Restating them here is the duplication the extraction removed.
        proc = chrome_run(
            chrome,
            ["--virtual-time-budget=4000", "--dump-dom", str(f)],
            timeout=60)
        m = re.search(r'<pre id="__bb"[^>]*>(.*?)</pre>', proc.stdout, re.S)
        if not m or not m.group(1).strip():
            raise RuntimeError("no bounding box measured (chrome exit %d)" % proc.returncode)
        return json.loads(m.group(1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--svg", required=True)
    ap.add_argument("--max-passes", type=int, default=4)
    ap.add_argument("--chrome", default=None,
                    help="explicit browser path; checked, never trusted")
    args = ap.parse_args()

    # Relative paths resolve from the CALLER'S cwd. The engagement-local original
    # resolved them against its own parent directory, which was correct beside the
    # charts and wrong from tools/.
    path = Path(args.svg)
    if not path.exists():
        print("cannot read " + str(path), file=sys.stderr)
        return 4

    try:
        chrome = require_chrome(args.chrome)
    except ChromeNotFound as exc:
        # "No browser" and "measured, found nothing to change" must not look alike.
        print(str(exc), file=sys.stderr)
        return 4

    for i in range(1, args.max_passes + 1):
        text = path.read_text(encoding="utf-8")
        vb = re.search(r'viewBox="([-\d. ]+)"', text)
        if not vb:
            print("no viewBox in " + str(path), file=sys.stderr)
            return 1
        x0, y0, w0, h0 = (float(v) for v in vb.group(1).split())
        bb = measure(text, chrome)
        dy, dh = bb["y"] - y0, (y0 + h0) - (bb["y"] + bb["h"])
        if abs(dy) <= 0.5 and abs(dh) <= 0.5:
            print("converged after %d pass(es): top slack %.2f, bottom slack %.2f"
                  % (i, dy, dh))
            print("  viewBox %.2f %.2f %.2f %.2f   (x and width untouched: the scale-1 "
                  "construction depends on them)" % (x0, y0, w0, h0))
            return 0
        new = 'viewBox="%g %.2f %g %.2f"' % (x0, bb["y"], w0, bb["h"])
        path.write_text(re.sub(r'viewBox="[-\d. ]+"', new, text, count=1), encoding="utf-8")
        print("pass %d: tightened y by %.2f, height by %.2f" % (i, dy, dh))

    print("did not converge in %d passes" % args.max_passes, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
