#!/usr/bin/env python3
"""standup_collect.py — run every /standup Step 1 preprocessing script and return one JSON.

Why it exists. /standup Step 1 used to have the model start nine scripts by hand. In zsh the
obvious loop (`for c in "x.py --flag" ...; do python3 tools/$c; done`) passes each quoted
string as ONE argument, so python looks for a file named "x.py --flag": every script fails
(fired 2026-09-30, 10-05, 10-13, 10-21, 10-22). Piping `2>&1` into a JSON parser also broke
when a script printed progress on stderr. Here every command is an argv list run without a
shell, stdout and stderr are kept apart, and a script that fails is reported, never dropped.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/standup_collect.py [--repo-root .] [--target-date YYYY-MM-DD]

Output (stdout, one JSON object):
  {"target_date": "...", "ok": [names], "failed": [names],
   "results": {name: {"rc": int, "data": <parsed JSON> | null, "text": <stdout if not JSON>,
                      "stderr": <last 2000 chars, only when rc != 0>}}}

Exit codes: 0 every script ran and exited 0; 1 at least one failed (its entry says why).
A failed script never stops the others: /standup degrades per source, it does not abort.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 300
STDERR_TAIL = 2000


def commands(target_date: str) -> dict[str, list[str]]:
    """The Step 1 scripts, in the order /standup reads them. Argv lists, never strings."""
    d = ["--target-date", target_date]
    r = ["--repo-root", "."]
    return {
        "pipeline_staleness": ["tools/pipeline_staleness.py", *d],
        "outreach_pending": ["tools/outreach_pending.py", *d],
        "networking_followup": ["tools/networking_followup.py", *d],
        "todos_summary": ["tools/todos_summary.py", *d, "--top-n", "6"],
        "automation_health": ["tools/check_automation_health.py", *r],
        "attention": ["tools/attention.py", *r, "--json"],
        "conversations_metric": ["tools/conversations_metric.py", *r, *d],
        "source_check": ["tools/source_check.py", "due", *r, *d, "--json"],
        "workstream_state": ["tools/workstream_state.py", "--render", "--json"],
        "role_queue": ["tools/role_queue_read.py", *r, "--top", "5"],
    }


def run_one(argv: list[str], root: Path) -> dict:
    try:
        proc = subprocess.run([sys.executable, *argv], cwd=str(root), capture_output=True,
                              text=True, timeout=TIMEOUT,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        return {"rc": -1, "data": None, "text": "", "stderr": f"timed out after {TIMEOUT}s"}
    except OSError as e:
        return {"rc": -1, "data": None, "text": "", "stderr": f"could not start: {e}"}
    out: dict = {"rc": proc.returncode, "data": None, "text": ""}
    try:
        out["data"] = json.loads(proc.stdout)
    except ValueError:
        out["text"] = proc.stdout
    if proc.returncode != 0:
        out["stderr"] = proc.stderr[-STDERR_TAIL:]
    return out


def collect(root: Path, target_date: str) -> dict:
    cmds = commands(target_date)
    with ThreadPoolExecutor(max_workers=len(cmds)) as pool:
        futures = {name: pool.submit(run_one, argv, root) for name, argv in cmds.items()}
        results = {name: f.result() for name, f in futures.items()}
    failed = [n for n, r in results.items() if r["rc"] != 0]
    return {"target_date": target_date, "ok": [n for n in results if n not in failed],
            "failed": failed, "results": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--target-date", default=date.today().isoformat())
    args = ap.parse_args(argv)
    out = collect(Path(args.repo_root).resolve(), args.target_date)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
