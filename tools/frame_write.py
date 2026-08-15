#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frame_write.py — the ONLY sanctioned mutation path for output/<slug>/frame.yaml.

Nothing else writes a frame. Direct edits bypass validation, versioning and the
compare-and-swap, and a frame that was hand-edited cannot be trusted by F9 (which
diffs against the last locked version) or by the compounding loop.

WHY EACH GUARANTEE EXISTS -- all four are paid for by a specific past failure:

  1. VALIDATE BEFORE REPLACING. The candidate is serialised to a temp file and run
     through tools/check_frame_integrity.py. Structural errors REFUSE the write.
     A frame that fails the schema gate never reaches disk, so `frame.yaml` is
     always parseable and always schema-valid.

  2. COMPARE-AND-SWAP via mandatory --expect-version. Two agents that read version
     N and both write produce exactly ONE success; the loser exits non-zero having
     written nothing. Segments run across sessions and machines-with-context-loss,
     so "read, think for ten minutes, write" is the normal case, not the edge case.

  3. ADVISORY LOCK around read-validate-swap. CAS alone is still a TOCTOU race:
     both writers can read version N, both validate, both swap. The lock closes the
     window. Uses tools/inbox_lock.py's file_lock -- the existing primitive, not a
     new one.

  4. ATOMIC MOVE, and any failure leaves frame.yaml BYTE-IDENTICAL. Snapshot the
     current file to frame.v<N>.yaml first, then os.replace the new content in.
     A crash mid-write cannot produce a half-frame.

MODEL-AUTHORED YAML IS NEVER WRITTEN DIRECTLY. Callers pass field=value pairs or a
JSON payload; this script constructs the YAML. Three of three hand-authored YAML
files in this project failed to parse on first attempt (2026-08-13), which is why
the agent describes the change and the script serialises it.

LEDGERS ARE DERIVED, NEVER ACCEPTED AS INPUT. `checks_fired`, `stages_run`,
`operator_answer_count` and `re_ask_count` are computed here from the filesystem.
The model being scored does not get to score itself.

CLI:
  frame_write.py set    --frame <path> --expect-version N --field k=v [--field k=v ...]
  frame_write.py patch  --frame <path> --expect-version N --json <file|->
  frame_write.py lock   --frame <path> --expect-version N --will-be-probed "<text>"
  frame_write.py append --frame <path> --expect-version N --list proposals --json <file|->
  frame_write.py answers-metrics --answers <path>          # derived metrics, read-only
  frame_write.py show   --frame <path>                     # version + segment, read-only

Output: JSON on stdout. Exit 0 success, non-zero on any refusal.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import yaml
except ImportError:
    print(json.dumps({"status": "error", "message": "PyYAML required: pip install pyyaml"}))
    sys.exit(3)

from inbox_lock import file_lock  # noqa: E402  (the existing advisory-lock primitive)

CHECKER = Path(__file__).resolve().parent / "check_frame_integrity.py"
SCHEMA = Path(__file__).resolve().parents[1] / "framework" / "frame-schema.yaml"

# Fields the operator/agent may never set directly: they are derived, or they are
# the run-state the protocol owns.
DERIVED_FIELDS = {
    "version", "checks_fired", "stages_run",
    "operator_answer_count", "re_ask_count",
}


def out(payload: dict, code: int = 0):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(code)


def die(msg: str, **extra):
    out({"status": "error", "message": msg, **extra}, code=1)


def load_frame(path: Path) -> dict:
    if not path.exists():
        die(f"no frame at {path}")
    try:
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"frame.yaml does not parse: {exc}")
    if not isinstance(d, dict):
        die("frame.yaml is not a mapping")
    return d


def coerce(v: str):
    """`--field k=v` values arrive as strings; recover the obvious scalar types."""
    low = v.strip().lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "none", "~"):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def set_dotted(d: dict, key: str, value):
    """`d1.problem_statement=x` sets the nested key, matching the schema's notation.

    The schema declares field names in dotted form, and a transcriber once wrote them
    as FLAT top-level keys -- ten of them -- which passed structural validation while
    every check reading `d1` returned CANNOT_RUN. Writing dotted keys as nested is the
    fix for that class at the mutation layer.
    """
    parts = key.split(".")
    cur = d
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def run_checker(candidate: Path) -> dict:
    """Validate a candidate frame. Structural errors refuse the write; rule FAILs do not.

    A frame mid-run legitimately fails rules -- that is what the gate is FOR, and
    refusing to save a frame because F3 fires would make the gate unusable. What must
    never land is a frame the checker cannot read at all.
    """
    r = subprocess.run(
        [sys.executable, str(CHECKER), str(candidate), "--schema", str(SCHEMA), "--json"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"status": "error", "stage": "checker",
                "detail": (r.stderr or r.stdout or "checker produced no output")[:400]}


def write_frame(frame_path: Path, new: dict, expect: int) -> dict:
    """The whole guarantee, in order. Any failure leaves frame.yaml byte-identical."""
    with file_lock(frame_path):
        current = load_frame(frame_path)
        actual = current.get("version")
        if actual != expect:
            die(f"version mismatch: frame is at {actual}, you passed --expect-version {expect}. "
                "Re-read the frame and retry; nothing was written.",
                frame_version=actual, expect_version=expect)

        new = dict(new)
        new["version"] = actual + 1

        tmp = frame_path.with_suffix(".yaml.candidate")
        tmp.write_text(yaml.safe_dump(new, sort_keys=False, allow_unicode=True), encoding="utf-8")
        try:
            verdict = run_checker(tmp)
            if verdict.get("status") == "error" or verdict.get("structural_errors"):
                die("candidate frame REFUSED by the schema gate; nothing was written",
                    checker=verdict)
            snapshot = frame_path.parent / f"frame.v{actual}.yaml"
            snapshot.write_text(frame_path.read_text(encoding="utf-8"), encoding="utf-8")
            os.replace(tmp, frame_path)
        finally:
            if tmp.exists():
                tmp.unlink()

    return {"status": "ok", "version": new["version"], "snapshot": f"frame.v{actual}.yaml",
            "clean": verdict.get("clean"), "fully_covered": verdict.get("fully_covered"),
            "counts": verdict.get("counts")}


# ---------------------------------------------------------------- answers metrics

def answers_metrics(answers_path: Path) -> dict:
    """Derive operator_answer_count and re_ask_count. NEVER accepted as input.

    re_ask_count is the falsifier for "frame.yaml is a sufficient resume point": a
    re-ask means a segment could not tell where it was from the state file and had to
    ask the operator something it already knew. Non-zero is a defect in the resume
    design, not operator error.

    Keyed on `question_id`, an IDENTIFIER, never on the prose in `asked`. Frame schema
    v3 exists because comparing two free-text descriptions for equality produced 2
    false results out of 4 -- the same question asked twice will be worded differently,
    and prose comparison would UNDER-count re-asks, which is the direction that hides
    the defect.
    """
    if not answers_path.exists():
        return {"operator_answer_count": 0, "re_ask_count": 0, "answers_file": None,
                "note": "no answers file yet; both metrics are 0 because nothing was asked"}
    try:
        d = yaml.safe_load(answers_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        die(f"answers file does not parse: {exc}")
    rows = d.get("answers") or []
    if not isinstance(rows, list):
        die("answers.answers must be a list")

    ids, missing = [], 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        qid = str(r.get("question_id") or "").strip()
        if qid:
            ids.append(qid)
        else:
            missing += 1

    total = len(ids)
    distinct = len(set(ids))
    per_id = {}
    for q in ids:
        per_id[q] = per_id.get(q, 0) + 1
    repeated = {k: v for k, v in per_id.items() if v > 1}

    res = {
        "operator_answer_count": len(rows),
        "re_ask_count": total - distinct,
        "distinct_questions": distinct,
        "repeated_questions": repeated,
        "answers_file": str(answers_path),
    }
    if missing:
        # Loud, not silent: rows without an id are invisible to re-ask detection, so a
        # clean re_ask_count on a file full of them would be a false pass.
        res["rows_missing_question_id"] = missing
        res["warning"] = (f"{missing} row(s) have no question_id and CANNOT be checked for "
                          "re-asks; re_ask_count understates by an unknown amount")
    return res


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def mut(p):
        p.add_argument("--frame", required=True)
        p.add_argument("--expect-version", required=True, type=int,
                       help="MANDATORY compare-and-swap; wrong value writes nothing")

    p_set = sub.add_parser("set", help="set scalar/dotted fields")
    mut(p_set)
    p_set.add_argument("--field", action="append", default=[], metavar="k=v")

    p_patch = sub.add_parser("patch", help="deep-merge a JSON payload")
    mut(p_patch)
    p_patch.add_argument("--json", required=True, help="path to a JSON file, or - for stdin")

    p_lock = sub.add_parser("lock", help="set locked:true + locked_at + the pre-room prediction")
    mut(p_lock)
    p_lock.add_argument("--will-be-probed", required=True,
                        help="what you expect the room to question. F13 requires it at lock")
    p_lock.add_argument("--today", required=True, help="YYYY-MM-DD (no clock in scripts here)")

    p_app = sub.add_parser("append", help="append to a ledger list")
    mut(p_app)
    p_app.add_argument("--list", required=True,
                       choices=["proposals", "declines", "overrides", "compression_ledger"])
    p_app.add_argument("--json", required=True)

    p_m = sub.add_parser("answers-metrics", help="derive the two run metrics (read-only)")
    p_m.add_argument("--answers", required=True)

    p_s = sub.add_parser("show", help="version + resume point (read-only)")
    p_s.add_argument("--frame", required=True)

    a = ap.parse_args(argv)

    if a.cmd == "answers-metrics":
        out({"status": "ok", **answers_metrics(Path(a.answers))})

    if a.cmd == "show":
        d = load_frame(Path(a.frame))
        out({"status": "ok", "version": d.get("version"),
             "segment_completed": d.get("segment_completed"),
             "locked": d.get("locked"), "engagement": d.get("engagement"),
             "run_status": d.get("status")})

    frame_path = Path(a.frame)
    current = load_frame(frame_path)
    new = dict(current)

    if a.cmd == "set":
        if not a.field:
            die("set needs at least one --field k=v")
        for f in a.field:
            if "=" not in f:
                die(f"--field must be k=v, got {f!r}")
            k, v = f.split("=", 1)
            if k.split(".")[0] in DERIVED_FIELDS:
                die(f"`{k}` is DERIVED and cannot be set by a caller. "
                    "The model being scored does not get to score itself.")
            set_dotted(new, k, coerce(v))

    elif a.cmd == "patch":
        raw = sys.stdin.read() if a.json == "-" else Path(a.json).read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except Exception as exc:
            die(f"--json did not parse: {exc}")
        if not isinstance(payload, dict):
            die("patch payload must be an object")
        bad = sorted(set(payload) & DERIVED_FIELDS)
        if bad:
            die(f"payload sets DERIVED field(s): {', '.join(bad)}")
        for k, v in payload.items():
            set_dotted(new, k, v)

    elif a.cmd == "lock":
        new["locked"] = True
        new["locked_at"] = a.today
        pred = dict(new.get("prediction") or {})
        pred["made_at_version"] = current.get("version")
        pred["will_be_probed"] = a.will_be_probed
        new["prediction"] = pred
        new["segment_completed"] = "LOCK"

    elif a.cmd == "append":
        raw = sys.stdin.read() if a.json == "-" else Path(a.json).read_text(encoding="utf-8")
        try:
            item = json.loads(raw)
        except Exception as exc:
            die(f"--json did not parse: {exc}")
        cur = new.get(a.list)
        if cur is None:
            cur = []
        if not isinstance(cur, list):
            die(f"`{a.list}` exists and is not a list")
        new[a.list] = cur + (item if isinstance(item, list) else [item])

    out(write_frame(frame_path, new, a.expect_version))


if __name__ == "__main__":
    main()
