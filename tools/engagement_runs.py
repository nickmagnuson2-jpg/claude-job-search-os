#!/usr/bin/env python3
"""How many times has the analysis method actually been run, and how far did each run get?

WHY THIS IS A TOOL AND NOT A SENTENCE IN A FILE. `data/workstreams/analysis-method.md` said
"the method's only real engagement run is <engagement-slug>, frame.yaml v1-v4, still in_progress". That
was true on 2026-09-14 and false a week later: a second engagement had reached v53 and closed
with its integrity gate green. Nobody wrote the wrong thing -- the sentence simply stopped
being true while nobody was looking at it, which is what every typed derivable eventually does.

So the method's workstream stops claiming its own run count and lifts it from the engagements
instead. An engagement IS a directory under `output/` holding a `frame.yaml`; that is the whole
definition, and it means a new run counts itself the moment it exists.

WHAT `version` MEANS HERE. It is the frame's own monotonic counter, advanced by
`tools/frame_write.py` on every governed write. It is a measure of how much the method was
exercised, NOT of quality: a run can reach v53 and still be wrong. Do not read it as a score.

EMPTY IS AN ERROR, NOT A RESULT. Zero engagements means the glob is pointed at the wrong tree,
not that the method was never run, and this raises rather than returning a cheerful zero.

    PYTHONIOENCODING=utf-8 python3 tools/engagement_runs.py --json

Emits: {"count": N, "runs": [{"slug","version","locked","frame"}...], "value": N}
`value` duplicates `count` so the workstream registry's default field works without policy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML required: python3 -m pip install pyyaml")


def find_frames(output_dir: Path) -> list[Path]:
    """Every `output/<slug>/frame.yaml`, sorted by slug.

    Deliberately NOT recursive: a frame snapshot (`frame.v12.yaml`) is a version of a run, not
    a run, and a nested fixture is not an engagement. One frame per directory, one directory
    per engagement.
    """
    return sorted(output_dir.glob("*/frame.yaml"))


def read_run(frame: Path) -> dict:
    """One run's slug, frame version and locked state.

    A frame that will not parse is reported with version None rather than skipped. Dropping it
    would shrink the count silently, which is the failure this module exists to prevent.
    """
    slug = frame.parent.name
    try:
        data = yaml.safe_load(frame.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        return {"slug": slug, "version": None, "locked": None,
                "frame": str(frame), "error": str(exc)[:200]}
    return {"slug": slug, "version": data.get("version"),
            "locked": bool(data.get("locked", False)), "frame": str(frame)}


def scan(output_dir: Path) -> dict:
    frames = find_frames(output_dir)
    if not frames:
        raise ValueError(
            f"no engagement frames under {output_dir}. A zero count here means the path is "
            "wrong, not that the method has never been run."
        )
    runs = [read_run(f) for f in frames]
    return {"count": len(runs), "value": len(runs), "runs": runs}


def render(result: dict) -> str:
    lines = [f"engagement runs of the analysis method: {result['count']}", ""]
    for r in sorted(result["runs"], key=lambda r: -(r["version"] or 0)):
        state = "locked" if r["locked"] else "open"
        ver = f"v{r['version']}" if r["version"] is not None else "UNPARSEABLE"
        lines.append(f"  {r['slug']:<28} {ver:>6}  {state}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output-dir", type=Path,
                    default=Path(__file__).resolve().parents[1] / "output",
                    help="the tree holding output/<slug>/frame.yaml")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        result = scan(a.output_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if a.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
