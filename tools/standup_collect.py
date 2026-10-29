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
      [--read-only]

--read-only writes nothing: workstream_state runs without --render (which rewrites the
STATE-BLOCKs in data/workstreams/), sent_artifact_check runs with --no-state (no state file,
no Gmail token rewrite, no auth-failure alert), and every child runs with
PYTHONDONTWRITEBYTECODE=1 so no __pycache__ is written. The morning page renders from this
mode every 15 minutes, so a render must never change the tree it reads. /standup keeps the
default mode.

Limit: workstream_state still RUNS each workstream's declared PROBE command, and nothing
sandboxes it. Read-only covers what this collector and its scripts write; a probe that
writes a file is that probe's defect.

Output (stdout, one JSON object):
  {"target_date": "...", "ok": [names], "partial": [names], "failed": [names],
   "results": {name: {"rc": int, "data": <parsed JSON> | null, "text": <stdout if not JSON>,
                      "stderr": <last 2000 chars, only when rc != 0>}}}

`partial` is a script that exited nonzero but printed parseable JSON (workstream_state exits 1
when one of its probes errored): use its data and say what its stderr reports. `failed` is a
script with no usable output.

Exit codes: 0 nothing failed; 1 at least one script produced no usable output.
A failed script never stops the others: /standup degrades per source, it does not abort.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_runtime import claude_project_dir  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 300
STDERR_TAIL = 2000


def commands(target_date: str, read_only: bool = False,
             root: Path | None = None) -> dict[str, list[str]]:
    """The Step 1 scripts, in the order /standup reads them. Argv lists, never strings.

    read_only drops the two writes the default mode makes (see the module docstring)."""
    d = ["--target-date", target_date]
    r = ["--repo-root", "."]
    render = [] if read_only else ["--render"]
    no_state = ["--no-state"] if read_only else []
    # The live memory tier sits outside the repo, in Claude Code's per-project folder.
    memory = str(claude_project_dir(root or REPO_ROOT) / "memory")
    return {
        "pipeline_staleness": ["tools/pipeline_staleness.py", *d],
        "outreach_pending": ["tools/outreach_pending.py", *d],
        "networking_followup": ["tools/networking_followup.py", *d],
        "todos_summary": ["tools/todos_summary.py", *d, "--top-n", "6"],
        "automation_health": ["tools/check_automation_health.py", *r],
        "attention": ["tools/attention.py", *r, "--json"],
        "conversations_metric": ["tools/conversations_metric.py", *r, *d],
        "source_check": ["tools/source_check.py", "due", *r, *d, "--json"],
        "workstream_state": ["tools/workstream_state.py", *render, "--json"],
        "role_queue": ["tools/role_queue_read.py", *r, "--top", "5"],
        "sent_artifacts": ["tools/sent_artifact_check.py", *r, "--json", *no_state],
        "build_check": ["tools/build_check.py", *r, "--json"],
        # S7 (2026-10-28, "Merge into the collector"): the five capture lanes' due count,
        # which no surface printed. Summary only; the full record list stays in the tool.
        "learning_ledger": ["tools/learning_ledger.py", "--memory-dir", memory, *r,
                            "--json", "--summary"],
    }


def run_one(argv: list[str], root: Path, read_only: bool = False) -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if read_only:
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run([sys.executable, *argv], cwd=str(root), capture_output=True,
                              text=True, timeout=TIMEOUT, env=env)
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


def collect(root: Path, target_date: str, read_only: bool = False) -> dict:
    cmds = commands(target_date, read_only, root)
    with ThreadPoolExecutor(max_workers=len(cmds)) as pool:
        futures = {name: pool.submit(run_one, argv, root, read_only)
                   for name, argv in cmds.items()}
        results = {name: f.result() for name, f in futures.items()}
    # A nonzero exit WITH parsed output is not a dead script: workstream_state exits 1
    # when a probe errored, and its JSON says which (588.F5). Only no usable output fails.
    failed = [n for n, r in results.items()
              if r["data"] is None and (r["rc"] != 0 or not r["text"].strip())]
    partial = [n for n, r in results.items() if r["rc"] != 0 and r["data"] is not None]
    return {"target_date": target_date, "read_only": read_only,
            "ok": [n for n in results if n not in failed and n not in partial],
            "partial": partial, "failed": failed, "results": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    # Before 04:00 local the run is about the day that just ended (todo #31).
    ap.add_argument("--target-date", default=None)
    ap.add_argument("--read-only", action="store_true",
                    help="write nothing: no STATE-BLOCK render, no sent-check state update")
    args = ap.parse_args(argv)
    import todo_read
    target = args.target_date or todo_read.logical_today().isoformat()
    out = collect(Path(args.repo_root).resolve(), target, args.read_only)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
