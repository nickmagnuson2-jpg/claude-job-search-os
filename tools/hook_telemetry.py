#!/usr/bin/env python3
"""hook_telemetry.py -- the hooks workstream's speed and block-rate PROBE.

WHY THIS EXISTS. The hooks workstream's only probe counted wiring (wired / checked /
stderr-only), so "faster without losing function" had no number. The 2026-11-16 debug-log
telemetry run (output/infra-backlog/111626-telemetry/111626-synthesis.md) measured the
per-call cost directly: the PreToolUse:Bash wall p50 went from 113 ms to 239 ms in five
weeks (W42 to W46) as hooks per call went from 17 to 24, and real blocks cost a retry turn
each. RULING Q8 (data/workstreams/hooks.md, Nick, 2026-11-16) made it a standing probe.

WHAT IT READS. The Claude Code debug logs, ~/cc-debug-*.log, which exist only for sessions
launched with --debug. Only files modified inside the window are opened, and only lines
timestamped inside the window count. One Bash call is one BURST of
`Hooks: PreToolUse:Bash [...] finished with status N (Nms)` lines within 3 s of the
burst's first line. Hooks run in PARALLEL, so a call's wall is the SLOWEST hook in its
burst, never the sum. A status-2 line followed within three lines by "can't open file" is
a crash (the 2026-10-14 macOS permission failure), not a block.

WHAT IT PRINTS. One JSON object in the workstream_probe contract.
  default:  {"value": "bash_wall=200-299ms | blocks=2.0-2.9/100 | hooks/call=24"}
  --raw:    unbanded numbers plus calls= and logs=, for a person.
The default value is BANDED (100 ms; 1.0 blocks per 100 calls) because
workstream_state.py surfaces a workstream whenever its value changes: 50 ms bands put the
2026-11-16 p50 (244 ms) on a band edge, which would surface daily as noise.
A reading that cannot be taken (no Bash timing in the window) is exit 2 with
{"error": ...}, never a zero: if Nick stops launching with --debug, the probe errors, and
an error always surfaces.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/hook_telemetry.py [--days 7] [--raw]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import statistics
import sys
import time

DEFAULT_GLOB = "~/cc-debug-*.log"
BURST_GAP_S = 3.0
WALL_BAND_MS = 100
BLOCK_BAND = 1.0

LINE_RE = re.compile(
    r"^(\S+?)Z \[DEBUG\] Hooks: PreToolUse:Bash \[.*\] finished with status (\d+) \((\d+)ms\)")
CRASH_TAIL = "can't open file"


def _epoch(stamp: str) -> float:
    return dt.datetime.fromisoformat(stamp).replace(tzinfo=dt.timezone.utc).timestamp()


def read(pattern: str, now: float, days: int):
    """(files read, per-call walls ms, per-call hook counts, blocks) inside the window."""
    cutoff = now - days * 86400
    files = sorted(f for f in glob.glob(os.path.expanduser(pattern))
                   if os.path.getmtime(f) >= cutoff)
    walls: list[int] = []
    counts: list[int] = []
    blocks = 0
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        burst = None  # [start epoch, hooks, max ms]
        for i, text in enumerate(lines):
            m = LINE_RE.match(text)
            if not m:
                continue
            t = _epoch(m.group(1))
            if t < cutoff or t > now:
                continue
            status, ms = m.group(2), int(m.group(3))
            if status == "2" and any(CRASH_TAIL in x for x in lines[i + 1:i + 4]):
                continue
            if burst is None or t - burst[0] > BURST_GAP_S:
                if burst:
                    counts.append(burst[1]); walls.append(burst[2])
                burst = [t, 0, 0]
            burst[1] += 1
            burst[2] = max(burst[2], ms)
            blocks += status == "2"
        if burst:
            counts.append(burst[1]); walls.append(burst[2])
    return files, walls, counts, blocks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="hooks workstream speed + block-rate probe")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--raw", action="store_true", help="unbanded numbers, for a person")
    ap.add_argument("--logs-glob", default=DEFAULT_GLOB, help=argparse.SUPPRESS)
    ap.add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    now = a.now if a.now is not None else time.time()
    files, walls, counts, blocks = read(a.logs_glob, now, a.days)
    if not walls:
        print(json.dumps({"error": f"no PreToolUse:Bash timing in {len(files)} debug log(s) "
                                   f"from the last {a.days} day(s); is Claude Code launched with --debug?"}))
        return 2
    p50 = statistics.median(walls)
    per_call = statistics.median(counts)
    rate = 100 * blocks / len(walls)
    if a.raw:
        value = (f"bash_wall_p50={p50:.0f}ms | blocks_per_100={rate:.2f} | hooks/call={per_call:.0f}"
                 f" | calls={len(walls)} | logs={len(files)}")
    else:
        lo = int(p50 // WALL_BAND_MS) * WALL_BAND_MS
        b = int(rate // BLOCK_BAND) * BLOCK_BAND
        value = (f"bash_wall={lo}-{lo + WALL_BAND_MS - 1}ms | blocks={b:.1f}-{b + BLOCK_BAND - 0.1:.1f}/100"
                 f" | hooks/call={per_call:.0f}")
    print(json.dumps({"value": value}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
