#!/usr/bin/env python3
"""Tests for the flush-race hardening in scan_transcript_failures.py.

Covers the 2026-07-08 fix (memory/friction-log.md "Pending Infra Fix",
diagnosed 2026-06-10): the Stop hook's transcript scanner can advance its
per-session `last_line` cursor past a tool_result line before that line is
durably flushed to disk, permanently skipping a real tool error (Edit
tool_use_errors bear the brunt because they have no real-time PostToolUseFailure
capture path — see the friction-log note for the full diagnosis).

Fix under test: (1) each scan starts from `max(0, last_line - OVERLAP_LINES)`
so a flush-missed line gets a second pass, and (2) a per-session `logged_ids`
set (keyed by tool_use_id) prevents the overlap re-scan from double-logging
(and inflating friction_log.py's occurrence count) an error already caught.

This is a fixture-transcript + 3-pass simulation, NOT a live-race reproduction
(per the friction-log note: "Don't rely on reproducing the live race"):
  Pass 1 — simulate the cursor having already raced past the error line (i.e.
           the state a live flush-race would leave behind).
  Pass 2 — the overlap re-scan recovers the previously-missed error and logs it.
  Pass 3 — re-scanning the same territory does NOT double-log it (dedup).

Never calls the real `friction_log.py append` / touches memory/friction-log.md
— `append_friction` is monkeypatched to a recording stub for the whole test.

Run:  PYTHONIOENCODING=utf-8 python3 -m pytest tools/test_scan_transcript_failures.py -v
  or: PYTHONIOENCODING=utf-8 python3 tools/test_scan_transcript_failures.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scan_transcript_failures as stf  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


def _tool_use_line(tool_use_id: str, name: str, command: str = "") -> str:
    return json.dumps({
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": name,
                 "input": {"command": command} if command else {"file_path": "/tmp/x.md"}},
            ],
        }
    })


def _tool_result_line(tool_use_id: str, text: str, is_error: bool) -> str:
    return json.dumps({
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id,
                 "content": text, "is_error": is_error},
            ],
        }
    })


def _padding_pair(i: int) -> list:
    """A harmless, non-error tool_use/tool_result pair used as filler lines."""
    tid = f"toolu_pad_{i}"
    return [
        _tool_use_line(tid, "Read", ""),
        _tool_result_line(tid, "some file contents", False),
    ]


def build_fixture_transcript() -> Path:
    """
    Layout (1-indexed lines):
      1..20   padding pairs (10 pairs = 20 lines)
      21      tool_use for the Edit call (id=toolu_edit_err)
      22      tool_result: is_error=True, "File has not been read yet."
      23..62  padding pairs (20 pairs = 40 lines) — pushes the error line
              well outside a *naive* re-scan of only the last couple of lines,
              but comfortably inside the OVERLAP_LINES=40 rewind window
              measured from a cursor sitting just past line 22.
    """
    lines = []
    for i in range(10):
        lines.extend(_padding_pair(i))
    lines.append(_tool_use_line("toolu_edit_err", "Edit"))
    lines.append(_tool_result_line(
        "toolu_edit_err",
        "<tool_use_error>File has not been read yet. Read it first before writing to it.</tool_use_error>",
        True,
    ))
    for i in range(10, 30):
        lines.extend(_padding_pair(i))

    d = Path(tempfile.mkdtemp())
    p = d / "fixture-transcript.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def main() -> int:
    transcript = build_fixture_transcript()
    all_lines = transcript.read_text(encoding="utf-8").splitlines()
    error_result_line_no = None
    for idx, ln in enumerate(all_lines, start=1):
        if '"toolu_edit_err"' in ln and '"is_error": true' in ln:
            error_result_line_no = idx
    assert error_result_line_no is not None, "fixture construction bug: error line not found"

    # Record every (surface, nature) the scanner would have logged, without
    # ever touching the real friction_log.py / memory/friction-log.md.
    logged_calls = []
    orig_append_friction = stf.append_friction

    def fake_append_friction(surface, nature, exit_hint=""):
        logged_calls.append((surface, nature))

    stf.append_friction = fake_append_friction
    try:
        # ── Pass 1: simulate the race ───────────────────────────────────
        # A prior scan's cursor already advanced PAST the error's tool_result
        # line before it was flushed — i.e. exactly what the live race
        # produces. Scanning from that cursor (no overlap) must NOT find it.
        raced_cursor = error_result_line_no  # cursor sitting ON/PAST the error line
        new_line1, logged1, logged_ids1 = stf.scan(transcript, raced_cursor, set())
        check("pass 1: race leaves the error unlogged from the raced cursor",
              logged1 == 0 and len(logged_calls) == 0)

        # ── Pass 2: overlap re-scan recovers it ─────────────────────────
        scan_start2 = max(0, raced_cursor - stf.OVERLAP_LINES)
        check("pass 2: overlap window actually rewinds before the error line",
              scan_start2 < error_result_line_no)
        new_line2, logged2, logged_ids2 = stf.scan(transcript, scan_start2, logged_ids1)
        check("pass 2: overlap re-scan recovers and logs the missed error",
              logged2 == 1 and len(logged_calls) == 1)
        check("pass 2: logged surface is the Edit tool",
              logged_calls[0][0] == "tool:Edit")
        check("pass 2: tool_use_id is recorded in logged_ids",
              "toolu_edit_err" in logged_ids2)

        # ── Pass 3: re-scanning the same territory does not double-log ──
        scan_start3 = max(0, new_line2 - stf.OVERLAP_LINES)
        new_line3, logged3, logged_ids3 = stf.scan(transcript, scan_start3, logged_ids2)
        check("pass 3: dedup prevents re-logging the same tool_use_id",
              logged3 == 0 and len(logged_calls) == 1)

        # ── Backward compatibility: missing logged_ids treated as empty ──
        new_line4, logged4, logged_ids4 = stf.scan(transcript, scan_start2, None)
        check("pass 4 (no prior logged_ids / old cursor schema): still finds it once",
              logged4 == 1)
    finally:
        stf.append_friction = orig_append_friction

    print(f"\nscan_transcript_failures flush-race tests: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())


# ── pytest entry point ──────────────────────────────────────────────────
def test_flush_race_hardening():
    assert main() == 0
