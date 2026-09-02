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
import re
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

REPO = Path(__file__).resolve().parents[1]

# THE CANONICAL FRAME LOCATION. `framework/analysis-method.md` declares the State layer
# as `output/<slug>/frame.yaml`, one file per engagement. Until 2026-08-31 nothing
# enforced that, and THREE conventions existed in the tree simultaneously:
# `frames/<slug>/frame.yaml` (the only real precedent), `output/<slug>/frame.yaml` (the
# doc), and `output/<target-company>/casework/frame-<date>-reconstructed.yaml`. Nothing globs for
# frames either, so there is no enumeration to catch a stray one -- a frame written to
# the wrong place is silently lost, and the compounding loop has no population.
#
# Enforcement anchors to the REPO. A path inside it must be canonical; a path outside it
# is not what the convention governs and is left alone (this is also what keeps the test
# suite, which writes to pytest tmp dirs, able to run at all).
CANONICAL_DIR = "output"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")


def enforce_canonical_location(frame_path: Path) -> None:
    """Refuse an in-repo frame that is not at output/<slug>/frame.yaml.

    Refusing without naming the wanted path just relocates the guess, so the message
    carries the exact canonical path for the slug the caller implied.
    """
    resolved = frame_path.resolve()
    try:
        rel = resolved.relative_to(REPO)
    except ValueError:
        return  # outside the repo: not governed
    parts = rel.parts
    ok = (len(parts) == 3 and parts[0] == CANONICAL_DIR
          and SLUG_RE.match(parts[1]) and parts[2] == "frame.yaml")
    if ok:
        return
    slug = parts[1] if len(parts) >= 2 and SLUG_RE.match(parts[1]) else "<slug>"
    die(f"{rel} is not the canonical frame location. Inside this repo a frame MUST live "
        f"at {CANONICAL_DIR}/<slug>/frame.yaml (one file, whole engagement, per "
        f"framework/analysis-method.md). Use {CANONICAL_DIR}/{slug}/frame.yaml.",
        canonical=f"{CANONICAL_DIR}/{slug}/frame.yaml", given=str(rel))


CHECKER = Path(__file__).resolve().parent / "check_frame_integrity.py"
SCHEMA = Path(__file__).resolve().parents[1] / "framework" / "frame-schema.yaml"

# Fields the operator/agent may never set directly: they are derived, or they are
# the run-state the protocol owns.
DERIVED_FIELDS = {
    "version", "checks_fired", "stages_run",
    "operator_answer_count", "re_ask_count",
    # check_log is RAW OBSERVATION, deliberately not a verdict. One row per write,
    # holding what the checker actually reported at that version. It does NOT compute
    # "fired": two candidate definitions of that were tried and BOTH saturate (every
    # rule looks alive, so a demotion gate can never cut) -- see the 2026-08-16
    # changelog entry. Recording observations and letting a future consumer define
    # firing is the only form of this that cannot be wrong at capture time, and the
    # per-version history is the part that cannot be reconstructed afterward.
    "check_log",
    # segment_completed is THE resume point. Everything about running the method across
    # sessions rests on it, so it cannot be authored: it is set only by
    # `--complete-segment`, and only after that segment's exit condition is met.
    "segment_completed",
    # `prediction` is stamped by `lock` and must never be re-pointed by a later write.
    # `prediction.made_at_version` is what resolves F9's comparison target, so a caller
    # able to set it can aim the compression check at a snapshot that was never the
    # locked one -- producing a substantive-looking PASS against the wrong baseline.
    # Verified settable 2026-08-15 (`set --field prediction.made_at_version=99` landed).
    # Blocked as a subtree: the dotted-root guard makes `prediction.anything` refuse.
    "prediction",
    # schema_version is class: derived in frame-schema.yaml and was missing here.
    # Settable + a checker refusal read as a pass (see run_checker) chained into a
    # VALIDATION BYPASS: `set --field schema_version=9` landed, the checker refused
    # every subsequent read of that frame, and frame_write reported status ok with
    # clean/fully_covered/counts all null, forever. Measured 2026-08-15 on a scratch
    # frame: three consecutive unvalidated writes. A genuine schema migration is a
    # deliberate, rare act and does not belong on the `set` path.
    "schema_version",
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


def run_checker(candidate: Path) -> tuple:
    """Validate a candidate frame. Structural errors refuse the write; rule FAILs do not.

    A frame mid-run legitimately fails rules -- that is what the gate is FOR, and
    refusing to save a frame because F3 fires would make the gate unusable. What must
    never land is a frame the checker cannot read or will not judge.

    THE EXIT CODE IS PART OF THE CONTRACT AND WAS BEING DISCARDED. The checker
    documents: 0 no FAILs, 2 at least one FAIL, 3 schema REFUSED, 4 frame unreadable.
    This function previously parsed stdout and ignored the code entirely, so a
    `status: "refused"` verdict -- which carries no `structural_errors` and no
    `counts` -- sailed through the caller's check and the write landed unvalidated.

    Returns (returncode, verdict). The caller decides; this only reports.
    """
    r = subprocess.run(
        [sys.executable, str(CHECKER), str(candidate), "--schema", str(SCHEMA), "--json"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        return r.returncode, json.loads(r.stdout)
    except Exception:
        return r.returncode, {"status": "error", "stage": "checker",
                              "detail": (r.stderr or r.stdout
                                         or "checker produced no output")[:400]}


def verdict_refuses(rc: int, verdict: dict) -> str | None:
    """Return a refusal reason, or None if this verdict is a real judgement.

    Four independent ways a verdict can fail to be one, each of which previously
    read as a pass:

      1. an exit code outside the judged range (0 = no FAILs, 2 = FAILs present).
         3 is a schema refusal and 4 is an unreadable frame; neither judged anything.
      2. a status other than `ok` -- notably `refused`, which the old check missed
         because it tested only for `error`.
      3. structural errors, which is the original and still-correct refusal.
      4. no `counts` key. A judged verdict always carries counts; its absence means
         the shape changed underneath us, and defaulting to "fine" on an unrecognised
         shape is how the first bypass happened.
    """
    if rc not in (0, 2):
        return f"checker exited {rc} (only 0 and 2 are judgements)"
    status = verdict.get("status")
    if status != "ok":
        return f"checker returned status {status!r}: {verdict.get('detail') or ''}".strip()
    if verdict.get("structural_errors"):
        return "candidate has structural errors"
    if verdict.get("counts") is None:
        return "checker returned no counts; the verdict judged nothing"
    return None


# ---------------------------------------------------------------- segment ordering
#
# HARD ORDERING. A segment cannot begin until its predecessor has COMPLETED, and a
# segment completes only when its exit condition is met. Those two halves are
# load-bearing together: without exit conditions, "completed" means "somebody said so"
# and the ordering guarantees nothing.
#
# This replaces the method's most-repeated convention (D1 before D2, because the spec
# determines what data is worth gathering) with a refusal. Run D2 first and you build
# an evidence index before anything knows what will consume it.

SEGMENT_ORDER = ["A", "B", "C", "D", "LOCK", "E"]

# What must be TRUE for a segment to be declared complete. Dotted paths; a list or map
# value must additionally be non-empty, because an empty scope_out or an empty exclusion
# set is a defect rather than a clean answer.
SEGMENT_EXIT = {
    "A": ["d1.problem_statement", "d1.problem_type", "d1.mode.data_obtainable",
          "d1.mode.shots", "d1.scope_out", "d1.deliverable"],
    "B": ["facts", "unknowns"],
    "C": ["elements", "closure", "exclusions"],
    "D": ["recommendation.text", "recommendation.confidence", "recommendation.next_action"],
    "LOCK": ["locked", "prediction"],
    "E": [],   # E's exit is the changelog entry, which lives outside this file
}


def get_dotted(d: dict, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def predecessor(segment: str):
    i = SEGMENT_ORDER.index(segment)
    return SEGMENT_ORDER[i - 1] if i else None


def check_segment_order(current: dict, segment: str):
    """Refuse a write whose segment cannot legally be in progress right now."""
    done = current.get("segment_completed")
    want = predecessor(segment)
    if done == want:
        return
    if done == segment:
        die(f"segment {segment} is already COMPLETE (segment_completed={done!r}). "
            f"The next segment is {SEGMENT_ORDER[SEGMENT_ORDER.index(segment) + 1]!r} "
            "if one remains. Nothing was written.")
    die(f"OUT OF ORDER: segment {segment} needs segment_completed=={want!r}, "
        f"but the frame is at {done!r}. "
        + ("D1 must complete before D2 opens: the spec determines what data is worth "
           "gathering, and running D2 first builds an evidence index before anything "
           "knows what will consume it. " if segment == "B" else "")
        + "Nothing was written.")


def check_segment_exit(candidate: dict, segment: str):
    """Refuse to mark a segment complete while its exit condition is unmet."""
    missing = []
    for path in SEGMENT_EXIT.get(segment, []):
        v = get_dotted(candidate, path)
        if v is None or (isinstance(v, (list, dict, str)) and len(v) == 0):
            missing.append(path)
    if missing:
        die(f"cannot complete segment {segment}: {len(missing)} exit condition(s) unmet "
            f"({', '.join(missing)}). An empty list here is a DEFECT, not a clean answer. "
            "Nothing was written.",
            missing=missing, segment=segment)


def observe(verdict: dict, version: int, segment) -> dict:
    """One raw check_log row: what the checker REPORTED at this version.

    Deliberately not a judgement. `states` is the rule id to PASS / FAIL / CANNOT_RUN
    map exactly as reported, with no rule reclassified, excluded, or aggregated. The
    three-state distinction is preserved because collapsing it is the failure the gate
    itself was built against, and a consumer that wants counts can derive them.
    """
    return {
        "version": version,
        "segment": segment,
        "states": {c["rule"]: c["state"] for c in verdict.get("checks", [])
                   if isinstance(c, dict) and "rule" in c},
        "counts": verdict.get("counts"),
        "clean": verdict.get("clean"),
        "fully_covered": verdict.get("fully_covered"),
    }


def write_frame(frame_path: Path, new: dict, expect: int, segment=None,
                complete_segment: bool = False) -> dict:
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

        # Ordering is checked against the frame RE-READ UNDER THE LOCK, never against
        # whatever the caller looked at minutes ago.
        if segment:
            check_segment_order(current, segment)
            if complete_segment:
                check_segment_exit(new, segment)
                new["segment_completed"] = segment
        elif complete_segment:
            die("--complete-segment requires --segment; nothing was written")

        tmp = frame_path.with_suffix(".yaml.candidate")

        def check(payload: dict):
            tmp.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
                           encoding="utf-8")
            rc, v = run_checker(tmp)
            refusal = verdict_refuses(rc, v)
            if refusal:
                die(f"candidate frame REFUSED by the schema gate ({refusal}); nothing was written",
                    checker=v, checker_exit=rc)
            return v

        try:
            # Phase 1: judge the content, with no observation row in it. The row cannot
            # exist yet -- it records this very verdict.
            verdict = check(new)

            # Phase 2: append the observation, then RE-CHECK. Injecting after validation
            # would mean the bytes that land were never the bytes that were validated,
            # which silently voids the guarantee this whole script exists to provide.
            log = list(new.get("check_log") or [])
            log.append(observe(verdict, new["version"], segment))
            new["check_log"] = log
            final = check(new)

            # No rule reads check_log, so appending it must not move any count. If it
            # did, the log is perturbing what it observes. Both must be real dicts:
            # None == None would pass vacuously and hide exactly that.
            a, b = verdict.get("counts"), final.get("counts")
            if not (isinstance(a, dict) and isinstance(b, dict) and a == b):
                die("recording the observation changed the verdict; nothing was written",
                    before=a, after=b)

            snapshot = frame_path.parent / f"frame.v{actual}.yaml"
            snapshot.write_text(frame_path.read_text(encoding="utf-8"), encoding="utf-8")
            os.replace(tmp, frame_path)
        finally:
            if tmp.exists():
                tmp.unlink()

    return {"status": "ok", "version": new["version"], "snapshot": f"frame.v{actual}.yaml",
            "clean": verdict.get("clean"), "fully_covered": verdict.get("fully_covered"),
            "counts": verdict.get("counts"), "observed": len(new["check_log"])}


# ---------------------------------------------------------------- init

def schema_version_from_schema() -> int:
    """Read the current frame version from the schema. Never hardcode it here.

    A literal in this file drifts the moment the schema bumps, and the failure is
    silent until the checker refuses every write for that engagement.
    """
    try:
        d = yaml.safe_load(SCHEMA.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        die(f"cannot read the schema at {SCHEMA}: {exc}")
    v = d.get("schema_version")
    if not isinstance(v, int):
        die("schema has no integer schema_version")
    return v


def init_frame(frame_path: Path, engagement: str, today: str) -> dict:
    """Create version 1. The only write with no prior version to compare against.

    NO --force, deliberately. Every other path is protected by compare-and-swap; a
    force flag here would be the single unprotected way to overwrite a live frame.
    Discarding an engagement stays an explicit `rm`, with frame.v<N>.yaml as recovery.

    Atomic create via O_EXCL rather than a path.exists() test, which is a TOCTOU race:
    two runners can both see "absent" and both write, and the loser's content wins.

    Content fields are OMITTED, not seeded with "". `problem_statement: ""` reads as
    authored-and-blank, which is a different claim from not-yet-authored, and the
    difference is exactly what the resume point has to be able to tell.
    """
    skeleton = {
        "schema_version": schema_version_from_schema(),
        "version": 1,
        "locked": False,
        "engagement": engagement,
        "status": "in_progress",
        # NO `created:` field. It was written here once and dropped 2026-08-16: the
        # schema does not declare it, validate_structure ignores undeclared keys, so it
        # landed silently and nothing read it. That is the schema's own
        # `required: is documentation` trap in reverse -- an undeclared field looks like
        # data and is guaranteed to be unenforced. The creation date is already
        # recoverable from check_log[0] and frame.v1.yaml. `--today` is kept on the
        # signature because it belongs to init's contract, not because it is stored.
        # containers the ledgers append to; empty is honest here, absent is not
        "facts": {}, "unknowns": {}, "inputs": {},
        "elements": [], "exclusions": [],
        "compression_ledger": [], "proposals": [], "declines": [],
        "check_log": [],
        # no d1 block: segment A creates it, and its absence IS the resume signal
    }
    body = yaml.safe_dump(skeleton, sort_keys=False, allow_unicode=True)

    # init is the FIRST command a new engagement runs, so its parent directory is
    # normally absent -- `frames/<new-slug>/` does not exist until something makes it.
    # Without this, the validation temp-file write below died with an UNCAUGHT
    # FileNotFoundError: no die(), no structured JSON, just a traceback, from the tool
    # that is the SOLE sanctioned mutation path. It survived because every test in
    # test_frame_write.py uses pytest's `tmp_path`, which always exists, so the suite
    # was structurally blind to init's own precondition. Fixed 2026-08-31.
    # `exist_ok=True` because re-running init on an existing directory is legitimate;
    # the O_EXCL below is what refuses to overwrite an existing FRAME.
    frame_path.parent.mkdir(parents=True, exist_ok=True)

    tmp = frame_path.parent / f".{frame_path.name}.init"
    tmp.write_text(body, encoding="utf-8")
    try:
        rc, verdict = run_checker(tmp)
        refusal = verdict_refuses(rc, verdict)
        if refusal:
            die(f"skeleton REFUSED by the schema gate ({refusal}); nothing was written",
                checker=verdict, checker_exit=rc)
        try:
            fd = os.open(str(frame_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            die(f"a frame already exists at {frame_path}; init refuses rather than "
                "overwrite. There is no --force: delete it deliberately if that is "
                "what you mean.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
    finally:
        if tmp.exists():
            tmp.unlink()

    return {"status": "ok", "created": str(frame_path), "version": 1,
            "schema_version": skeleton["schema_version"],
            "clean": verdict.get("clean"), "fully_covered": verdict.get("fully_covered"),
            "counts": verdict.get("counts"),
            "note": "a version-1 skeleton legitimately fails content rules; that is not a defect"}


# ---------------------------------------------------------------- answers

IDENT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")


def answers_add(answers_path: Path, frame_path: Path, segment: str, question_id: str,
                asked: str, answer: str, answered_at: str) -> dict:
    """Append one operator decision. The script serialises; no model-authored YAML.

    Locks the FRAME first, then the answers file, in that fixed global order -- the
    order frame writes already take. A lone answers-lock cannot exclude a concurrent
    frame write, and two different orders between two callers is a deadlock.

    `at_version` is DERIVED by reading the frame under the lock, never passed. It is
    the join between the two files, and a caller-supplied value can drift from what
    actually happened.
    """
    if not IDENT.match(question_id):
        die(f"question_id {question_id!r} is not an identifier (^[a-z0-9][a-z0-9._-]{{0,39}}$). "
            "Prose ids under-count re-asks, which is the direction that HIDES the defect "
            "re_ask_count exists to surface.")

    with file_lock(frame_path):
        version = load_frame(frame_path).get("version")
        with file_lock(answers_path):
            if answers_path.exists():
                try:
                    doc = yaml.safe_load(answers_path.read_text(encoding="utf-8")) or {}
                except Exception as exc:
                    die(f"answers file does not parse: {exc}")
            else:
                doc = {"schema_version": 1, "answers": []}
            rows = doc.get("answers")
            if not isinstance(rows, list):
                die("answers.answers must be a list")

            row_index = len(rows) + 1
            rows.append({
                "row_index": row_index,          # a lost row shows up as a gap
                "segment": segment,
                "at_version": version,
                "question_id": question_id,
                "asked": asked,
                "answer": answer,
                "answered_at": answered_at,
            })
            doc["schema_version"] = doc.get("schema_version", 1)
            doc["answers"] = rows

            tmp = answers_path.parent / f".{answers_path.name}.tmp"
            tmp.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                           encoding="utf-8")
            os.replace(tmp, answers_path)

    repeats = sum(1 for r in rows if isinstance(r, dict) and r.get("question_id") == question_id)
    res = {"status": "ok", "row_index": row_index, "at_version": version,
           "answers_file": str(answers_path), "rows": len(rows)}
    if repeats > 1:
        # REPORTED, never refused. A genuine re-ask must stay recordable: refusing it
        # would suppress the falsifier instead of surfacing it.
        res["re_ask"] = True
        res["times_asked"] = repeats
        res["warning"] = (f"{question_id!r} has now been asked {repeats} times. A re-ask means a "
                          "segment could not tell where it was from the frame. That is a defect "
                          "in the resume design, not operator error.")
    return res


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
        # Optional, and recorded verbatim when given. NOT mandatory: the runner makes
        # writes that belong to no segment, and forcing a value would push a caller to
        # supply a false one to get the write through.
        p.add_argument("--segment", choices=SEGMENT_ORDER,
                       help="the segment this write belongs to. Ordering is ENFORCED: the "
                            "predecessor must be complete. Omitted records null and skips "
                            "the ordering check, for writes belonging to no segment")
        p.add_argument("--complete-segment", action="store_true",
                       help="mark --segment complete after this write. Refused unless that "
                            "segment's exit condition is met")

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

    p_init = sub.add_parser("init", help="create version 1; refuses if the frame exists")
    p_init.add_argument("--frame", required=True)
    p_init.add_argument("--engagement", required=True)
    p_init.add_argument("--today", required=True, help="YYYY-MM-DD (no clock in scripts here)")

    p_aa = sub.add_parser("answers-add", help="append one operator decision")
    p_aa.add_argument("--answers", required=True)
    p_aa.add_argument("--frame", required=True, help="read-only here; at_version is DERIVED from it")
    p_aa.add_argument("--segment", required=True, choices=["A", "B", "C", "D", "LOCK", "E"])
    p_aa.add_argument("--question-id", required=True, help="an IDENTIFIER, never prose")
    p_aa.add_argument("--asked", required=True)
    p_aa.add_argument("--answer", required=True)
    p_aa.add_argument("--answered-at", required=True, help="YYYY-MM-DD")

    p_m = sub.add_parser("answers-metrics", help="derive the two run metrics (read-only)")
    p_m.add_argument("--answers", required=True)

    p_s = sub.add_parser("show", help="version + resume point (read-only)")
    p_s.add_argument("--frame", required=True)

    a = ap.parse_args(argv)

    # Location gate: runs for EVERY subcommand that names a frame, before any read or
    # write, so a non-canonical path cannot be created, mutated, or even shown.
    if getattr(a, "frame", None):
        enforce_canonical_location(Path(a.frame))


    if a.cmd == "init":
        out(init_frame(Path(a.frame), a.engagement, a.today))

    if a.cmd == "answers-add":
        out(answers_add(Path(a.answers), Path(a.frame), a.segment, a.question_id,
                        a.asked, a.answer, a.answered_at))

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
        # Compare on the DOTTED ROOT, exactly as `set` does. An exact-key test let
        # {"checks_fired.F5": ...} straight past the guard, because set_dotted then
        # nests it under the derived field regardless. It happened to be caught
        # downstream only because every derived field is list-shaped today; the first
        # map-shaped derived field would land clean and self-scored.
        bad = sorted({k for k in payload if k.split(".")[0] in DERIVED_FIELDS})
        if bad:
            die(f"payload sets DERIVED field(s): {', '.join(bad)}")
        for k, v in payload.items():
            set_dotted(new, k, v)

    elif a.cmd == "lock":
        a.segment = "LOCK"
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

        if a.list == "declines" and len(cur) >= 2:
            # THE TWO-DECLINE RULE, from framework/analysis-method.md. A decline that
            # costs nothing is how a compounding loop quietly stops compounding: every
            # run declines, the changelog fills with "no change", and it reads as
            # stability rather than as stall. Enforced here rather than remembered,
            # because the whole failure mode is that nobody notices.
            die(f"REFUSED: {len(cur)} consecutive declines already recorded. The next run "
                "may not decline. Either apply a change, or state that the method is at a "
                "local optimum TOGETHER WITH the concrete observation that would reopen it "
                "-- and record that as the change. Nothing was written.",
                declines=len(cur))

        new[a.list] = cur + (item if isinstance(item, list) else [item])

    out(write_frame(frame_path, new, a.expect_version,
                    segment=getattr(a, "segment", None),
                    complete_segment=getattr(a, "complete_segment", False)))


if __name__ == "__main__":
    main()
