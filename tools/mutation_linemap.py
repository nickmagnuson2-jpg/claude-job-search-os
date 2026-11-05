#!/usr/bin/env python3
"""
mutation_linemap.py -- which tests execute which lines of ONE file, via sys.monitoring.

WHY THIS EXISTS (2026-11-04). mutation_check ran every mutant against every mapped test
file. For check_public_pii that was ~17s per mutant across 733 mutants (about 3.5 hours)
for a 6-line change, and with three mutation slots per machine, sessions queued for a
slot all day. Most of that time was tests that never execute the mutated line. This
module records, in one baseline pass, which test ids execute each line of the target, so
mutation_check can run a mutant against the tests that reach it first.

STDLIB ONLY (Nick's call, 2026-11-04): Python 3.12+ sys.monitoring, no coverage.py.

HOW
  - PY_START is the only global event. Its callback turns on LINE events for code objects
    whose file is the target and returns DISABLE everywhere, so code outside the target
    costs one callback per code object.
  - Each LINE event records (current test id, line) and returns DISABLE, so a line costs
    one callback until the next restart_events(). The pytest hooks below call
    restart_events() at the start of every test, which is what makes the record per test.
  - Subprocesses: mutation_check puts a directory holding `sitecustomize.py` (see
    SITECUSTOMIZE) on PYTHONPATH for the mapping pass only. Any Python child that inherits
    the environment starts recording at startup and attributes its lines to the test id
    the parent exported in ENV_TEST. A child with a scrubbed environment records nothing.

WHAT A MISSING RECORD MEANS. A line with no recorded test id is UNKNOWN, never "covered by
nothing": a scrubbed-env subprocess, an os._exit, or a test that reads the source instead
of running it are all invisible here. mutation_check treats every gap as "run the full
mapped set", so this module can only make a run faster, never change a verdict.
"""
from __future__ import annotations

import json
import os
import sys

ENV_TARGET = "MUTATION_LINEMAP_TARGET"   # absolute, resolved path of the file to record
ENV_OUT = "MUTATION_LINEMAP_OUT"         # directory each process writes its record into
ENV_TEST = "MUTATION_LINEMAP_TEST"       # the running test's absolute node id (subprocesses)

# Code that runs outside any test (collection, imports, session fixtures) is recorded
# under this id. mutation_check never selects it: it is not a test it could run.
OUTSIDE = ""
ORDER_FILE = "order.list"         # not *.json, so read_records never reads it as a record

_state: dict = {"tool": None, "target": None, "out": None, "current": None,
                "hits": {}, "names": {}, "rootpath": None}


def _same_file(filename: str) -> bool:
    """Is `filename` (a code object's co_filename) the target? Cached per string."""
    names = _state["names"]
    hit = names.get(filename)
    if hit is None:
        try:
            hit = os.path.realpath(filename) == _state["target"]
        except (OSError, ValueError):
            hit = False
        names[filename] = hit
    return hit


def _test_id() -> str:
    """The id lines are attributed to: the in-process test if one is running, else the id
    a parent exported, else OUTSIDE."""
    current = _state["current"]
    if current is not None:
        return current
    return os.environ.get(ENV_TEST, OUTSIDE)


def _on_start(code, _offset):
    mon = sys.monitoring
    if _same_file(code.co_filename):
        mon.set_local_events(_state["tool"], code, mon.events.LINE)
    return mon.DISABLE


def _on_line(_code, line):
    _state["hits"].setdefault(_test_id(), set()).add(line)
    return sys.monitoring.DISABLE


def start(target: str, out: str) -> bool:
    """Begin recording `target`. False when sys.monitoring is missing or no tool id is
    free (another tool, e.g. a coverage run, holds them all): nothing is recorded, and the
    empty record makes mutation_check fall back to the full mapped set."""
    mon = getattr(sys, "monitoring", None)
    if mon is None or _state["tool"] is not None:
        return False
    tool = None
    for candidate in range(1, 6):
        if mon.get_tool(candidate) is None:
            try:
                mon.use_tool_id(candidate, "mutation_linemap")
            except ValueError:
                continue
            tool = candidate
            break
    if tool is None:
        return False
    _state.update(tool=tool, target=os.path.realpath(target), out=out)
    mon.register_callback(tool, mon.events.PY_START, _on_start)
    mon.register_callback(tool, mon.events.LINE, _on_line)
    mon.set_events(tool, mon.events.PY_START)
    import atexit
    atexit.register(flush)
    return True


def start_from_env() -> bool:
    target, out = os.environ.get(ENV_TARGET), os.environ.get(ENV_OUT)
    if not target or not out:
        return False
    return start(target, out)


def flush() -> None:
    """Write this process's record as one JSON file in the out directory. Best effort: a
    record that cannot be written is a gap, and a gap only costs speed."""
    out, hits = _state["out"], _state["hits"]
    if not out or not hits:
        return
    try:
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, f"{os.getpid()}-{id(hits):x}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: sorted(v) for k, v in hits.items()}, f)
        os.replace(tmp, path)
    except OSError:
        pass


# --- pytest plugin hooks (loaded with `-p _mutation_linemap` in the mapping pass) -------
# Plain hooks, no pytest import: this module is also imported by every Python child at
# startup, where importing pytest would be a cost and a side effect.

def pytest_configure(config):
    _state["rootpath"] = str(config.rootpath)


def absolute_nodeid(nodeid: str, rootpath: str | None) -> str:
    """`path::name` with the path made absolute, so the id selects the same test whatever
    rootdir a later pytest run picks (this repo has no pytest ini file, so the rootdir
    follows the arguments)."""
    path, sep, rest = nodeid.partition("::")
    if rootpath and path and not os.path.isabs(path):
        path = os.path.join(rootpath, path)
    return path + sep + rest


def pytest_collection_finish(session):
    """Write the collection order once: mutation_check runs a covering test together with
    the tests before it in its file, so state they set is there as in the full run."""
    out = _state["out"]
    if not out:
        return
    try:
        os.makedirs(out, exist_ok=True)
        order = [absolute_nodeid(i.nodeid, _state["rootpath"]) for i in session.items]
        tmp = os.path.join(out, "order.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(order, f)
        os.replace(tmp, os.path.join(out, ORDER_FILE))
    except OSError:
        pass


def pytest_runtest_logstart(nodeid, location):
    tid = absolute_nodeid(nodeid, _state["rootpath"])
    _state["current"] = tid
    os.environ[ENV_TEST] = tid
    mon = getattr(sys, "monitoring", None)
    if mon is not None and _state["tool"] is not None:
        mon.restart_events()


def pytest_runtest_logfinish(nodeid, location):
    _state["current"] = None
    os.environ[ENV_TEST] = OUTSIDE


# --- what mutation_check writes into the mapping directory --------------------------------

# Imports the real sitecustomize first (Homebrew's rewrites sys.executable, and skipping it
# would change how every child of the mapping pass starts), then starts recording.
#
# NESTED PASSES (2026-11-04). A mapping pass whose tests run mutation_check starts a second
# pass, so two shim directories sit on PYTHONPATH. Skipping only its OWN directory, each
# shim found the other as "the real sitecustomize" and ran it, which ran the first again:
# RecursionError at startup, the inner pass red with no test named, and a silent fall back
# to the slow path (seen measuring this module with its own tests). Every directory that
# holds a recorder copy is skipped, so the search always ends at the real one.
SITECUSTOMIZE = '''\
import importlib.machinery, importlib.util, os, sys
_path = [p for p in sys.path
         if not os.path.exists(os.path.join(os.path.abspath(p or "."), "_mutation_linemap.py"))]
_spec = importlib.machinery.PathFinder.find_spec("sitecustomize", _path)
if _spec is not None and _spec.loader is not None:
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
try:
    import _mutation_linemap
    _mutation_linemap.start_from_env()
except Exception:
    pass
'''


def read_records(out: str) -> dict[str, set[int]]:
    """Merge every process's record into {test id: lines}. Unreadable files are skipped:
    each is a gap, which costs speed only."""
    merged: dict[str, set[int]] = {}
    try:
        names = sorted(os.listdir(out))
    except OSError:
        return merged
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(out, name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for tid, lines in data.items():
            if isinstance(tid, str) and isinstance(lines, list):
                merged.setdefault(tid, set()).update(
                    n for n in lines if isinstance(n, int))
    return merged


def read_order(out: str) -> list[str]:
    """The pass's collection order; [] when it was not written (a gap: costs the prefix)."""
    try:
        with open(os.path.join(out, ORDER_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [x for x in data if isinstance(x, str)] if isinstance(data, list) else []


def by_line(records: dict[str, set[int]]) -> dict[int, set[str]]:
    """Invert to {line: test ids}, dropping OUTSIDE: it is not a test that can be run."""
    out: dict[int, set[str]] = {}
    for tid, lines in records.items():
        if tid == OUTSIDE:
            continue
        for n in lines:
            out.setdefault(n, set()).add(tid)
    return out
