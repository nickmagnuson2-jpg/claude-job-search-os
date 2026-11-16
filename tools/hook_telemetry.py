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
timestamped inside the window count.

HOW CALLS ARE FOUND. The finish lines (`Hooks: PreToolUse:Bash [...] finished with status N
(Nms)`) carry no call id, so they are never grouped by time. Each Bash call instead ends
with one TERMINATOR line the log does write per call: `[Stall] tool_dispatch_start
tool=Bash toolUseId=...` when it ran, `Bash tool permission denied` when it did not
(present in every log with Bash hooks, 84 of 84 in the 30 days to 2026-11-16, v2.1.296+).
  - calls  = terminators inside the window (exact, whatever overlaps);
  - blocks = `Hook denied tool use for Bash` lines inside the window, minus crashes;
  - wall and hooks/call come from SEGMENTS (the finish lines since the previous
    terminator) closed by exactly one terminator with no hook repeated. Hooks run in
    PARALLEL, so a call's wall is the SLOWEST hook, never the sum. A segment holding
    overlapping calls cannot be split by any id the log carries, so it is counted as
    `ambiguous` and left out, never split into invented calls (Codex r2 F1 on 8f72d6b6;
    about 1 in 30 segments on real logs).
A hook denial whose only status-2 finishes are each followed within three lines by
"can't open file" naming THAT file (basename equality, Codex r2 F2) is a crash (the
2026-10-14 macOS permission failure), not a block.

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
WALL_BAND_MS = 100
BLOCK_BAND = 1.0

LINE_RE = re.compile(
    r"^(\S+?)Z \[DEBUG\] Hooks: PreToolUse:Bash \[.*\] finished with status (\d+) \((\d+)ms\)")
CRASH_RE = re.compile(r"can't open file '([^']+)'")
TERMINATOR_RE = re.compile(r"^(\S+?)Z .*(?:\[Stall\] tool_dispatch_start tool=Bash |Bash tool permission denied)")
HOOK_DENIED_RE = re.compile(r"^(\S+?)Z .*Hook denied tool use for Bash")


def _epoch(stamp: str) -> float:
    return dt.datetime.fromisoformat(stamp).replace(tzinfo=dt.timezone.utc).timestamp()


HOOK_RE = re.compile(r"tools/([A-Za-z0-9_]+\.py)")


def _crashed(lines: list[str], i: int, hook: str) -> bool:
    """True when one of the three lines after line i says THIS hook file could not be opened."""
    return any(os.path.basename(m.group(1)) == hook
               for m in map(CRASH_RE.search, lines[i + 1:i + 4]) if m)


def read(pattern: str, now: float, days: int):
    """(files read, per-call walls ms, per-call hook counts, calls, blocked calls, ambiguous
    segments) inside the window. See the module docstring for how calls are found.
    Raises OSError / ValueError on an unreadable log; main turns that into exit 2."""
    cutoff = now - days * 86400
    files = sorted(f for f in glob.glob(os.path.expanduser(pattern))
                   if os.path.getmtime(f) >= cutoff)
    walls: list[int] = []
    counts: list[int] = []
    calls = blocked = ambiguous = 0
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        seg: list[tuple[str, int, bool | None]] = []   # (hook, ms, crashed-if-status-2) since the last terminator
        closing: list | None = None             # the segment the latest terminator(s) closed
        terminators = 0

        def settle():
            nonlocal ambiguous
            if closing is None:
                return
            hooks = [h for h, _, _ in closing]
            if terminators == 1 and len(hooks) == len(set(hooks)):
                walls.append(max(ms for _, ms, _ in closing)); counts.append(len(hooks))
            else:
                ambiguous += 1

        for i, text in enumerate(lines):
            if m := LINE_RE.match(text):
                hm = HOOK_RE.search(text)
                hook = hm.group(1) if hm else text
                status_2 = m.group(2) == "2"
                seg.append((hook, int(m.group(3)), status_2 and bool(hm) and _crashed(lines, i, hook)
                            if status_2 else None))
            elif d := HOOK_DENIED_RE.match(text):
                # Third field: None = status 0, True = status 2 that crashed, False = real 2.
                twos = [c for _, _, c in (seg or closing or []) if c is not None]
                if cutoff <= _epoch(d.group(1)) <= now and not (twos and all(twos)):
                    blocked += 1
            elif tm := TERMINATOR_RE.match(text):
                if not (cutoff <= _epoch(tm.group(1)) <= now):
                    settle()
                    seg, closing, terminators = [], None, 0
                elif seg:
                    calls += 1
                    settle()
                    closing, seg, terminators = seg, [], 1
                else:
                    calls += 1
                    terminators += 1
        settle()
    return files, walls, counts, calls, blocked, ambiguous


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="hooks workstream speed + block-rate probe")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--raw", action="store_true", help="unbanded numbers, for a person")
    ap.add_argument("--logs-glob", default=DEFAULT_GLOB, help=argparse.SUPPRESS)
    ap.add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    now = a.now if a.now is not None else time.time()
    try:
        files, walls, counts, calls, blocks, ambiguous = read(a.logs_glob, now, a.days)
    except (OSError, ValueError) as e:
        print(json.dumps({"error": f"could not read the debug logs: {e!r}"}))
        return 2
    if not walls:
        print(json.dumps({"error": f"no PreToolUse:Bash timing in {len(files)} debug log(s) "
                                   f"from the last {a.days} day(s); is Claude Code launched with --debug?"}))
        return 2
    p50 = statistics.median(walls)
    per_call = statistics.median(counts)
    rate = 100 * blocks / calls
    if a.raw:
        value = (f"bash_wall_p50={p50:.0f}ms | blocks_per_100={rate:.2f} | hooks/call={per_call:.0f}"
                 f" | calls={calls} | ambiguous={ambiguous} | logs={len(files)}")
    else:
        lo = int(p50 // WALL_BAND_MS) * WALL_BAND_MS
        b = int(rate // BLOCK_BAND) * BLOCK_BAND
        value = (f"bash_wall={lo}-{lo + WALL_BAND_MS - 1}ms | blocks={b:.1f}-{b + BLOCK_BAND - 0.1:.1f}/100"
                 f" | hooks/call={per_call:.0f}")
    print(json.dumps({"value": value}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
