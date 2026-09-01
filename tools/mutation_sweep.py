#!/usr/bin/env python3
"""mutation_sweep.py — measure mutation survival across every auditable tool.

WHY THIS EXISTS. `mutation_check.py` answers "is THIS tool's suite real?" one file at a
time. Nobody had ever asked it of the whole corpus, so the repo carried a 2,840-test suite
and no idea what fraction of its decisions any test observes. The first 7 tools measured
on 2026-08-26 returned 518 survivors of 1,356 mutants -- 38% of mutated decisions changed
behaviour with the suite still green, and one tool (`gmail_fetch.py`) survived 282 of 377.
A green suite is not evidence; this produces the count that is.

SELECTION IS DETERMINISTIC, not a judgment call: every `tools/*.py` that has a matching
`tests/scripts/test_<name>.py` and no entry in `tools/mutation-allow.json`.

THREE FILES ARE EXCLUDED, for the same reason at different layers. `mutation_check.py`
refuses itself (a tool that rewrites live source must never rewrite itself). This file
excludes itself here, because the sweep executes FROM it: making it a target means
rewriting live source under the running process. Both are RECORDED as `mutants: -1` rather
than dropped, so `self_excluded` names them and the selected-vs-auditable accounting still
adds up. Neither is unmeasurable -- run `mutation_check.py` on either one directly, with no
sweep in flight. Both have test files as of 2026-08-26.

The third is `job_quiesce.py`, added 2026-09-01: it is the runner's own restore path for
the launchd jobs the sweep takes down, and a SIGKILL mid-mutation would leave the NEXT run
importing a mutated restorer at startup. Same remedy -- measure it directly, not in a
sweep.

SERIAL ON PURPOSE. `mutation_check` rewrites its target in place, so two concurrent runs in
one tree corrupt each other -- agents hit `isolation_failed` purely from a sibling's
stranded backup, and every suite-green claim in that run became worthless. Do NOT
"parallelise" by backgrounding several copies: that recreates the exact bug this works
around. If parallelism is wanted, it is one git worktree per runner, not one tree.

RESUMABLE. Completed tools are read back from the state file and skipped, so a kill, crash,
or reboot costs at most the single tool in flight.

WHILE IT RUNS THE TREE IS UNSAFE FOR ORDINARY WORK. At any instant one `tools/*.py` is
mutated on disk. `tests/conftest.py` refuses to run at all (exit 3) while a
`.mutation_backup` exists, which is the loud half. The silent half is skills: `/remember`,
`/pipe`, `/act` and friends shell out to `tools/*.py` and would execute the mutant against
real data files. See docs/mutation-baseline-runbook.md.

USAGE
    PYTHONIOENCODING=utf-8 python3 tools/mutation_sweep.py --targets      # build the list
    PYTHONIOENCODING=utf-8 python3 tools/mutation_sweep.py                # run / resume
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

# ONE source of truth for "which tests cover this tool". Restating the rule here is what
# let selection and measurement drift apart.
#
# The path insert is load-bearing, not defensive boilerplate: `python3 tools/mutation_sweep.py`
# puts tools/ on sys.path[0], but importing this file BY PATH (spec_from_file_location, which
# is how its own tests load it) does not, and the bare import then dies with
# ModuleNotFoundError. Same file, two import mechanisms, and only one of them worked.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutation_check  # noqa: E402
import job_quiesce  # noqa: E402

REPO_ROOT = Path(os.environ.get("MUTATION_REPO_ROOT",
                                Path(__file__).resolve().parents[1])).resolve()
DEFAULT_STATE = REPO_ROOT / "output" / "analysis" / "082626-mutation-baseline"
# pipe_write.py needs 68 minutes and was lost at the old 45-minute cap, taking the rest
# of the run's isolation results with it.
#
# Raised 120 -> 300 on 2026-08-28. Cost is mutants x mapped test files, and todo_write.py
# is 541 x 15 = 8115 test-runs. Calibrated against pipe_write's measured 68 minutes for
# 2431 runs (~1.7s/run), that is ~227 minutes, so at 120 it could never finish: it burned
# two hours and returned UNAUDITED_TIMEOUT, which is no information at all. The cap exists
# to stop one tool eating a whole night, not to be shorter than the corpus's largest tool.
TOOL_TIMEOUT = 300 * 60

# The sweep executes FROM this file. Letting it become a target means rewriting live
# source out from under the running process -- the same hazard mutation_check.py refuses
# itself for. Matched by NAME, not by resolved path, because REPO_ROOT is overridable.
SELF_NAME = Path(__file__).name

# Modules the RUNNER depends on, which therefore must not be rewritten as targets.
# job_quiesce.py is the restore path for the launchd jobs this sweep takes down. The
# parent holds it in sys.modules for the whole run, so mutating it on disk is harmless
# while the run is alive -- the hazard is the NEXT run. A SIGKILL mid-mutation leaves a
# mutated job_quiesce.py behind, and the following sweep imports it at startup and uses it
# to restore whatever the dead run stranded. A mutated restorer deciding whether Nick's
# mail fetch comes back is precisely the failure that module exists to prevent.
RUNNER_DEPENDENCIES = {"job_quiesce.py"}

# A tool that opens anything for writing can silently corrupt a real data file, which is a
# higher blast radius than a hook misfiring. Recorded per tool so the report can rank by it.
_WRITER_RE = re.compile(r"os\.replace|open\([^)]*['\"][wa]['\"]|write_atomic|\.write_text\(")


def count_tests(path: Path) -> int:
    """Count test functions via AST, not a regex.

    `^def test_` misses class-nested tests entirely: it undercounted
    context_file_audit.py as 0 when it has 110.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return 0
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name.startswith("test_"))


def build_targets() -> list[dict]:
    # NO tool-level allowlist exclusion. `mutation-allow.json` is keyed per MUTANT
    # (`tools/x.py::func::OP::hash`) and mutation_check.py already honours it that way,
    # counting an allowlisted mutant as `allowlisted` rather than as a survivor. Dropping
    # the whole tool here meant justifying ONE mutant silently removed the tool from the
    # corpus forever -- and it had removed check_public_pii.py, the always-on hook that
    # keeps real names out of this PUBLIC repo, along with 8 others. Doing the right thing
    # must not delete the measurement. (2026-08-26)
    settings = (REPO_ROOT / ".claude" / "settings.json")
    wired = settings.read_text(encoding="utf-8") if settings.exists() else ""

    rows = []
    for tool in sorted((REPO_ROOT / "tools").glob("*.py")):
        rel = f"tools/{tool.name}"
        test_files = mutation_check.map_tests(tool, REPO_ROOT)
        if not test_files:
            continue
        if tool.name == SELF_NAME or tool.name in RUNNER_DEPENDENCIES:
            # Recorded, not silently dropped: a -1 row lands in `self_excluded` so the
            # selected-vs-auditable accounting still names it. Measuring this file is
            # still possible -- run mutation_check.py on it directly, with no sweep in
            # flight.
            rows.append({"tool": rel, "w": True, "h": tool.name in wired,
                         "tests": sum(count_tests(f) for f in test_files),
                         "test_files": len(test_files), "mutants": -1})
            continue
        proc = subprocess.run(
            [sys.executable, "tools/mutation_check.py", rel, "--list"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        try:
            mutants = json.loads(proc.stdout)["mutants"]
        except (json.JSONDecodeError, KeyError):
            mutants = -1                      # self-refusal, or a tool that will not parse
        rows.append({"tool": rel,
                     "w": bool(_WRITER_RE.search(tool.read_text(encoding="utf-8"))),
                     "h": tool.name in wired,
                     "tests": sum(count_tests(f) for f in test_files),
                     "test_files": len(test_files),
                     "mutants": mutants})
    # SCHEDULING, not priority. The report ranks worst-first by survivors and does its
    # own sorting, so this order only decides what gets measured per hour -- and that
    # matters because a run is often stopped or resumed rather than finished.
    #
    # Cost is mutants x mapped test files: each mutant re-runs EVERY mapped file. Measured
    # 2026-08-26 at ~8s per mutant on a 2-file tool, so the corpus is ~24h, far past one
    # night. Running the expensive tools first is actively wasteful: todo_write.py is
    # 541 mutants x 15 files = 8115 test-runs, will exceed the 120-minute cap, and a
    # timeout yields NO information at all -- where the same two hours spent on cheap
    # tools yields twenty finished measurements. Blast radius still decides what a HUMAN
    # works on next; it should not decide what a partial run spends its night on.
    rows.sort(key=lambda r: (max(r["mutants"], 0) * max(r["test_files"], 1),
                             not r["w"], not r["h"]))
    return rows


def repair_stranded(rel: str) -> str | None:
    """Restore a target left mutated on disk, and say so.

    `subprocess.run(timeout=)` kills the child with SIGKILL, which cannot be caught -- so
    mutation_check's SIGTERM/SIGINT/SIGHUP handler, added for exactly this class of
    failure, does not cover the sweep's own timeout path. On 2026-08-26 that left
    pipe_write.py mutated on disk with a stranded backup, and every later tool's
    `--isolation` run then tripped tests/conftest.py's refusal and was recorded as
    `isolation_failed`: 41 consecutive false findings from one timeout.

    Checked after EVERY tool, not just after a timeout. A crash that outruns the handler
    leaves the same wreckage, and the cost of looking is one stat() call.
    """
    target = REPO_ROOT / rel
    # Same function mutation_check writes with and tests/conftest.py scans -- the location
    # moved out of the working tree on 2026-09-01 and a second derivation here would have
    # silently stopped finding anything.
    backup = mutation_check.backup_path(target)
    if not backup.exists():
        return None
    os.replace(backup, target)
    return f"restored {rel} from a stranded .mutation_backup"


def _report_quiesce(action: str, res: dict) -> None:
    """Report a quiesce or restore into the log launchd writes.

    ALWAYS prints, including on success. A clean run that says nothing leaves no evidence
    the jobs ever went down, and "was the run actually protected?" becomes unanswerable
    from a 10-hour unattended log the next morning. The jobs are NAMED, not counted: the
    useful question at 08:00 is which one is still down, not how many.

    A job left down is a debt on Nick's mail, not a property of the measurement, so it is
    printed rather than folded into the exit status where the sweep's own result hides it.
    """
    done = res.get("quiesced" if action == "quiesce" else "restored") or []
    stamp = time.strftime("%H:%M:%S")
    if done:
        print(f"{stamp}  {action}d {len(done)} launchd job(s): {', '.join(done)}",
              flush=True)
    else:
        print(f"{stamp}  {action}: no launchd jobs affected", flush=True)
    for note in res.get("notes") or []:
        print(f"{stamp}  {action}: {note}", flush=True)
    if res.get("failed"):
        print(f"{stamp}  !! {len(res['failed'])} launchd job(s) STILL DOWN after "
              f"{action}: {', '.join(res['failed'])} -- restore with "
              f"`bash tools/launchd/install.sh install`", flush=True)


def run_sweep(state_dir: Path) -> int:
    targets_file, out = state_dir / "targets.json", state_dir / "baseline.jsonl"
    if not targets_file.exists():
        print(f"no target list at {targets_file}; run with --targets first", file=sys.stderr)
        return 1
    targets = json.loads(targets_file.read_text(encoding="utf-8"))

    # THE SCHEDULED JOBS COME DOWN FIRST, before any tool is mutated. gmail-fetch fires
    # every 900s and granola-auto-debrief every 3h, both shelling into the tools/*.py
    # this loop rewrites; unattended overnight that is dozens of mutant executions
    # against real Gmail and real data files. conftest guards pytest, not launchd.
    # See tools/job_quiesce.py for why the guard cannot live inside the mutated files.
    marker = state_dir / ".quiesced-jobs.json"
    restored_once = []

    def put_jobs_back():
        if restored_once:
            return
        restored_once.append(True)
        _report_quiesce("restore", job_quiesce.restore(REPO_ROOT, marker))

    def _on_signal(signum, _frame):
        # launchd SIGTERMs a job it wants gone and Nick may ctrl-C the run. Neither
        # unwinds `finally` by itself, so without this the jobs stay down until the
        # next sweep or the 08:00 health check notices the stranded marker.
        put_jobs_back()
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _on_signal)
    _report_quiesce("quiesce", job_quiesce.quiesce(REPO_ROOT, marker))
    try:
        return _run_sweep_inner(targets, out)
    finally:
        put_jobs_back()


def _run_sweep_inner(targets, out: Path) -> int:

    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["tool"])

    todo = [t for t in targets if t["mutants"] > 0 and t["tool"] not in done]
    print(f"{time.strftime('%H:%M:%S')}  {len(todo)} tools to measure "
          f"({len(done)} already banked)", flush=True)

    # Full environment, not a stripped one: unattended, a missing env var would turn into a
    # red baseline and get recorded as a finding. PYTHONIOENCODING is mandatory -- every
    # tools/*.py crashes on Unicode without it.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    for i, t in enumerate(todo, 1):
        start = time.time()
        timed_out = False
        try:
            proc = subprocess.run(
                [sys.executable, "tools/mutation_check.py", t["tool"], "--isolation", "--json"],
                capture_output=True, text=True, cwd=str(REPO_ROOT), env=env,
                timeout=TOOL_TIMEOUT)
            stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            stdout, stderr, rc, timed_out = "", "", None, True

        base = {k: t[k] for k in ("tool", "w", "h", "tests", "mutants")}
        base |= {"elapsed": round(time.time() - start, 1), "rc": rc}

        # Before the next tool starts: a mutated file left here contaminates every
        # measurement after it, not just this one.
        repaired = repair_stranded(t["tool"])
        if repaired:
            base["repaired"] = repaired
            print(f"{time.strftime('%H:%M:%S')}  REPAIRED {repaired}", flush=True)

        if timed_out:
            # An empty or skipped scan is never a clean result.
            rec = {**base, "status": "UNAUDITED_TIMEOUT",
                   "note": f"exceeded {TOOL_TIMEOUT}s; NOT clean, just unmeasured"}
        else:
            try:
                d = json.loads(stdout)
                rec = {**base, "status": d.get("status"), "killed": d.get("killed"),
                       "survived": d.get("survived"), "weak": d.get("weak_kill_count"),
                       # Carried, not dropped. mutation_check recovers a previous crash's
                       # wreckage at ITS startup, so `repaired` below (which only sees what
                       # is left AFTER a tool) stays None and the run looks pristine. An
                       # unattended run that quietly fixes itself teaches you the crash was
                       # harmless. Same omission class as isolation_refused, which made a
                       # 108-tool sweep report a status its own record could not explain.
                       "recovered_stranded_file": d.get("recovered_stranded_file"),
                       "isolation_refused": d.get("isolation_refused"),
                       "isolation_failures": d.get("isolation_failures"),
                       "assertion_free_tests": d.get("assertion_free_tests"),
                       "tautological_assertions": d.get("tautological_assertions"),
                       "survivors": d.get("survivors") or []}
            except (json.JSONDecodeError, AttributeError) as exc:
                rec = {**base, "status": "UNAUDITED_ERROR", "error": str(exc)[:200],
                       "stdout": stdout[-1000:], "stderr": stderr[-1000:]}

        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"{time.strftime('%H:%M:%S')}  [{i}/{len(todo)}] {t['tool']}: "
              f"status={rec.get('status')} survived={rec.get('survived')} "
              f"of {t['mutants']} ({base['elapsed']}s)", flush=True)

    print(f"{time.strftime('%H:%M:%S')}  SWEEP COMPLETE", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-dir", type=Path, default=DEFAULT_STATE,
                    help="where targets.json and baseline.jsonl live (default: gitignored "
                         "output/analysis/082626-mutation-baseline)")
    ap.add_argument("--targets", action="store_true",
                    help="rebuild the target list and exit, running no mutations")
    args = ap.parse_args(argv)

    args.state_dir.mkdir(parents=True, exist_ok=True)
    if args.targets:
        rows = build_targets()
        (args.state_dir / "targets.json").write_text(
            json.dumps(rows, indent=1), encoding="utf-8")
        auditable = [r for r in rows if r["mutants"] > 0]
        print(json.dumps({"status": "ok", "selected": len(rows),
                          "auditable": len(auditable),
                          "self_excluded": [r["tool"] for r in rows if r["mutants"] <= 0],
                          "writers": sum(r["w"] for r in auditable),
                          "hooked": sum(r["h"] for r in auditable),
                          "mutants": sum(r["mutants"] for r in auditable)}, indent=2))
        return 0
    return run_sweep(args.state_dir)


if __name__ == "__main__":
    sys.exit(main())
