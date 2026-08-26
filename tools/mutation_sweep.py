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

TWO FILES SELF-EXCLUDE, for the same reason and at different layers. `mutation_check.py`
refuses itself (a tool that rewrites live source must never rewrite itself). This file
excludes itself here, because the sweep executes FROM it: making it a target means
rewriting live source under the running process. Both are RECORDED as `mutants: -1` rather
than dropped, so `self_excluded` names them and the selected-vs-auditable accounting still
adds up. Neither is unmeasurable -- run `mutation_check.py` on either one directly, with no
sweep in flight. Both have test files as of 2026-08-26.

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
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(os.environ.get("MUTATION_REPO_ROOT",
                                Path(__file__).resolve().parents[1])).resolve()
DEFAULT_STATE = REPO_ROOT / "output" / "analysis" / "082626-mutation-baseline"
TOOL_TIMEOUT = 45 * 60          # one tool may not eat the whole night

# The sweep executes FROM this file. Letting it become a target means rewriting live
# source out from under the running process -- the same hazard mutation_check.py refuses
# itself for. Matched by NAME, not by resolved path, because REPO_ROOT is overridable.
SELF_NAME = Path(__file__).name

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
    allow_file = REPO_ROOT / "tools" / "mutation-allow.json"
    try:
        allowed = {k.split("::")[0] for k in json.loads(allow_file.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError):
        allowed = set()
    settings = (REPO_ROOT / ".claude" / "settings.json")
    wired = settings.read_text(encoding="utf-8") if settings.exists() else ""

    rows = []
    for tool in sorted((REPO_ROOT / "tools").glob("*.py")):
        rel = f"tools/{tool.name}"
        test_file = REPO_ROOT / "tests" / "scripts" / f"test_{tool.stem}.py"
        if not test_file.exists() or rel in allowed:
            continue
        if tool.name == SELF_NAME:
            # Recorded, not silently dropped: a -1 row lands in `self_excluded` so the
            # selected-vs-auditable accounting still names it. Measuring this file is
            # still possible -- run mutation_check.py on it directly, with no sweep in
            # flight.
            rows.append({"tool": rel, "w": True, "h": tool.name in wired,
                         "tests": count_tests(test_file), "mutants": -1})
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
                     "tests": count_tests(test_file),
                     "mutants": mutants})
    rows.sort(key=lambda r: (not r["w"], not r["h"], -r["tests"]))
    return rows


def run_sweep(state_dir: Path) -> int:
    targets_file, out = state_dir / "targets.json", state_dir / "baseline.jsonl"
    if not targets_file.exists():
        print(f"no target list at {targets_file}; run with --targets first", file=sys.stderr)
        return 1
    targets = json.loads(targets_file.read_text(encoding="utf-8"))

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

        if timed_out:
            # An empty or skipped scan is never a clean result.
            rec = {**base, "status": "UNAUDITED_TIMEOUT",
                   "note": f"exceeded {TOOL_TIMEOUT}s; NOT clean, just unmeasured"}
        else:
            try:
                d = json.loads(stdout)
                rec = {**base, "status": d.get("status"), "killed": d.get("killed"),
                       "survived": d.get("survived"), "weak": d.get("weak_kill_count"),
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
