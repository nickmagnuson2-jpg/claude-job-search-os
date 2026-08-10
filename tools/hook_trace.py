#!/usr/bin/env python3
"""hook_trace.py — shared rotating trace-log writer for the auto-capture hooks.

Both log_tool_failure.py (PostToolUseFailure) and scan_transcript_failures.py
(Stop) emit best-effort diagnostic traces to tools/.hook-trace.log. Before the
fable-audit Theme 3 fix that file grew unbounded (>1MB) because each hook opened
it in append mode with no size cap. This centralizes the append with rotation:
when the log exceeds MAX_TRACE_BYTES it is rotated to `<path>.1` (one prior
generation kept for forensics), capping disk use at ~2x the limit.

Consolidation: the two hooks previously carried near-identical trace() bodies.
Per the CLAUDE.md single-source-of-truth rule, the write path now lives here so
the two cannot drift. friction_surface.py stays pure/no-I/O (its own contract);
this module owns the I/O.
"""
import os
from pathlib import Path

# Per generation. With one rotated `.1` sibling, total disk is ~2x this.
MAX_TRACE_BYTES = 256 * 1024  # 256 KB


def append(path: Path, message: str) -> None:
    """Append one line to a trace log, rotating first if over the size cap.

    Best-effort diagnostic: never raises. A single trailing newline is ensured.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.exists() and path.stat().st_size >= MAX_TRACE_BYTES:
                os.replace(path, path.with_suffix(path.suffix + ".1"))
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(message.rstrip("\n") + "\n")
    except Exception:
        pass
