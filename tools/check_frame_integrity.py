#!/usr/bin/env python3
"""Run the deterministic subset of Section F against a frame.yaml.

Section F is the frame-integrity gate in framework/deck-rubric.md. Seven of its
twelve rules have a mechanically checkable component; the rest are delegated to a
blind agent or to the operator, and this script does NOT pretend to cover them.

THE THREE-STATE DESIGN IS THE POINT.

Every check reports PASS, FAIL, or CANNOT_RUN. A check that cannot execute -- because
the frame lacks the field it reads, or because the rule needs two versions and only
one exists -- is NEVER reported as a pass. Collapsing CANNOT_RUN into PASS is exactly
the "artifacts of rigor without the rigor" failure this gate was built against: it
produces a green result that means nothing and reads like coverage.

`clean` is true only when there are zero FAILs. `fully_covered` is true only when
there are additionally zero CANNOT_RUNs. Read both.

Exit codes are the contract:
  0  no FAILs (there may be CANNOT_RUNs -- check `fully_covered`)
  2  at least one FAIL
  3  schema refused (unknown schema_version, or schema file unreadable)
  4  frame file unreadable or not a mapping

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/check_frame_integrity.py <frame.yaml>
  ... --schema framework/frame-schema.yaml     # default
  ... --prior <frame.yaml>                     # explicit prior locked version, enables F9
  ... --json                                   # machine output only
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(4)


PASS = "PASS"
FAIL = "FAIL"
CANNOT_RUN = "CANNOT_RUN"

DEFAULT_SCHEMA = "framework/frame-schema.yaml"


class Result:
    """One rule's verdict. `detail` must always say WHY, including for CANNOT_RUN."""

    __slots__ = ("rule", "state", "detail", "offenders")

    def __init__(self, rule: str, state: str, detail: str, offenders=None):
        self.rule = rule
        self.state = state
        self.detail = detail
        self.offenders = offenders or []

    def as_dict(self):
        return {
            "rule": self.rule,
            "state": self.state,
            "detail": self.detail,
            "offenders": self.offenders,
        }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _elements(frame):
    els = frame.get("elements")
    return els if isinstance(els, list) else []


def _facts(frame):
    f = frame.get("facts")
    return f if isinstance(f, dict) else {}


def _dig(frame, dotted, default=None):
    """Read a dotted path like 'd1.metric_roles' out of nested mappings."""
    cur = frame
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _label(el, idx):
    return el.get("id") or el.get("name") or f"elements[{idx}]"


# --------------------------------------------------------------------------
# structural validation
#
# ADDED 2026-08-13 after an adversarial panel found, and a live test confirmed,
# that a frame with `elements` as a STRING and `d1` as a STRING returned
# clean=true and exit 0. Every reader helper coerces a malformed field to an
# empty list or dict, so the checks all degraded to CANNOT_RUN, and CANNOT_RUN
# does not block. The result was a green light on a structurally invalid file:
# artifacts of rigor without the rigor, rebuilt inside the gate meant to catch it.
#
# The schema's `fields:` block was pure documentation until now -- nothing read
# it. This is what reads it.
# --------------------------------------------------------------------------

def _shape_of(decl_type: str):
    """Coarse expected python type from the schema's `type:` string."""
    t = str(decl_type or "").strip()
    if t.startswith("list["):
        return list
    if t.startswith("map[") or t.startswith("{"):
        return dict
    if t == "bool":
        return bool
    if t == "int":
        return int
    if t == "timestamp":
        # YAML parses an unquoted 2026-07-21 into a date object, not a string.
        # Rejecting that was a false positive on a correctly-authored frame.
        return (str, _dt.date, _dt.datetime)
    if t in ("string", "enum"):
        return str
    return None  # unknown/unconstrained


def detect_flat_dotted_keys(frame, schema):
    """Catch a frame that wrote `d1.problem_statement:` as a FLAT top-level key.

    The schema declares its own field names in dotted notation, so an agent
    transcribing into it copies `d1.problem_statement:` literally as a key rather
    than nesting under `d1:`. That is a predictable error caused by the schema's
    notation, not a random one.

    It was invisible to the dotted-path walk in validate_structure(): with no `d1`
    parent, every nested field looked merely "not authored yet". The frame then
    passed structural validation and returned CANNOT_RUN on every check that reads
    those fields -- a well-formed file, a confident result, and nothing tested.

    Origin: 2026-08-13, second corpus run. The first corpus never hit it.
    """
    if not isinstance(frame, dict):
        return []
    dotted = {k for k in (schema or {}).get("fields", {}) if "." in k}
    hits = sorted(k for k in frame if k in dotted)
    if not hits:
        return []
    parents = sorted({k.split(".")[0] for k in hits})
    return [
        f"{len(hits)} field(s) written as FLAT dotted keys instead of nested under "
        f"{parents}: {', '.join(hits[:6])}"
        + (f" (+{len(hits) - 6} more)" if len(hits) > 6 else "")
        + f" -- every check reading {parents} silently returned CANNOT_RUN"
    ]


def validate_surface_identifiers(frame, schema):
    """Surfaces must be short comparable tokens, not sentences (v3+).

    Without this the field drifts straight back to prose and F1b silently returns to
    comparing descriptions, which is the bug v3 exists to fix. The pattern lives in
    the schema so tightening it is a schema edit, not a code edit.
    """
    ver = frame.get("schema_version")
    if not isinstance(ver, int) or ver < SURFACE_IDENTIFIER_MIN_VERSION:
        return []
    pattern = (schema or {}).get("identifier_pattern")
    if not pattern:
        return []
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return [f"schema identifier_pattern is not valid regex: {exc}"]

    errors = []
    for i, e in enumerate(_elements(frame)):
        if not isinstance(e, dict):
            continue
        for field in ("name_surface", "measure_surface"):
            val = e.get(field)
            if val is None:
                continue
            if not isinstance(val, str) or not rx.match(val):
                shown = str(val)[:60] + ("..." if len(str(val)) > 60 else "")
                errors.append(
                    f"{_label(e, i)}.{field} must be an identifier matching "
                    f"{pattern} (e.g. p5, slide-8, step-1), got prose: {shown!r}")
    return errors


def validate_structure(frame, schema):
    """Type-check every PRESENT field against the schema's declared shape.

    Absent fields are NOT errors here -- a frame is legitimately incomplete for most
    of its life (D1 is authored before D2, elements before closure). This checks only
    that what IS there has the right shape. A wrong shape is a hard error, never a
    silent CANNOT_RUN.
    """
    errors = list(detect_flat_dotted_keys(frame, schema))
    errors += validate_surface_identifiers(frame, schema)
    fields = (schema or {}).get("fields")
    if not isinstance(fields, dict):
        return errors + ["schema has no `fields:` block to validate against"]

    for dotted, spec in fields.items():
        if not isinstance(spec, dict):
            continue
        expected = _shape_of(spec.get("type"))
        if expected is None:
            continue

        parts = dotted.split(".")
        cur, ok = frame, True
        for p in parts[:-1]:
            if not isinstance(cur, dict):
                errors.append(f"{'.'.join(parts[:-1])} must be a mapping, got "
                              f"{type(cur).__name__}")
                ok = False
                break
            if p not in cur:
                ok = False  # parent absent: field simply not authored yet
                break
            cur = cur[p]
        if not ok or not isinstance(cur, dict):
            continue
        leaf = parts[-1]
        if leaf not in cur or cur[leaf] is None:
            continue  # absent or explicitly null is fine

        val = cur[leaf]
        # bool is a subclass of int in python; check it first so True != 1
        if expected is int and isinstance(val, bool):
            errors.append(f"{dotted} must be int, got bool")
        elif not isinstance(val, expected):
            errors.append(f"{dotted} must be {expected.__name__}, got "
                          f"{type(val).__name__}")

    # Dedup, order-preserving: one malformed parent (e.g. `d1.mode` as a string)
    # is hit once per child field declared under it, which reports the same error
    # N times and buries the distinct ones.
    seen, unique = set(), []
    for e in errors:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    return unique


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------

def check_F1a(frame):
    """Every element has a non-empty `measure`."""
    els = _elements(frame)
    if not els:
        return Result("F1a", CANNOT_RUN, "no `elements` in frame")
    bad = [_label(e, i) for i, e in enumerate(els)
           if not (isinstance(e, dict) and str(e.get("measure") or "").strip())]
    if bad:
        return Result("F1a", FAIL,
                      f"{len(bad)} of {len(els)} elements carry no measure", bad)
    return Result("F1a", PASS, f"all {len(els)} elements carry a measure")


SURFACE_IDENTIFIER_MIN_VERSION = 3


def check_F1b(frame):
    """`measure_surface` equals `name_surface` -- the level below sits where the name does.

    Only meaningful when surfaces are IDENTIFIERS. Below v3 they were free prose, and
    comparing two prose descriptions for equality produced 2 false failures out of 4 on
    a real frame: both elements said "same line, stated parenthetically with the name",
    which IS co-location, but the strings differed because one carried extra detail.

    A check that cannot be trusted must say so rather than emit findings. CANNOT_RUN is
    the honest verdict on a pre-v3 frame, not a best-effort string compare.
    """
    els = _elements(frame)
    if not els:
        return Result("F1b", CANNOT_RUN, "no `elements` in frame")

    ver = frame.get("schema_version")
    if isinstance(ver, int) and ver < SURFACE_IDENTIFIER_MIN_VERSION:
        return Result("F1b", CANNOT_RUN,
                      f"frame is at schema v{ver}, where surfaces are free prose. "
                      "Comparing prose descriptions for equality is unreliable "
                      f"(measured: 2 false failures of 4), so co-location is not "
                      f"checkable below v{SURFACE_IDENTIFIER_MIN_VERSION}")

    have = [e for e in els
            if isinstance(e, dict) and ("name_surface" in e or "measure_surface" in e)]
    if not have:
        return Result("F1b", CANNOT_RUN,
                      "no element declares name_surface/measure_surface; surface "
                      "co-location is unverifiable from this frame")
    bad = []
    for i, e in enumerate(els):
        if not isinstance(e, dict):
            continue
        ns, ms = e.get("name_surface"), e.get("measure_surface")
        if ns is None or ms is None:
            bad.append(f"{_label(e, i)} (surface not declared)")
        elif str(ns).strip() != str(ms).strip():
            bad.append(f"{_label(e, i)} (name on {ns!r}, measure on {ms!r})")
    if bad:
        return Result("F1b", FAIL,
                      f"{len(bad)} element(s) carry the measure off the naming surface", bad)
    return Result("F1b", PASS, "every measure sits on the surface that names it")


def check_F2a(frame):
    """`because` is NON-EMPTY and every id in it resolves to a real fact.

    The non-empty half is load-bearing. Zero ids all resolve vacuously, so an
    element citing nothing passes a naive resolve-only check -- which is precisely
    the untraced element the rule exists to catch.
    """
    els, facts = _elements(frame), _facts(frame)
    if not els:
        return Result("F2a", CANNOT_RUN, "no `elements` in frame")
    if not facts:
        return Result("F2a", CANNOT_RUN, "no `facts` block to resolve citations against")
    bad = []
    for i, e in enumerate(els):
        if not isinstance(e, dict):
            continue
        because = e.get("because")
        if not because:
            bad.append(f"{_label(e, i)} (cites nothing)")
            continue
        if not isinstance(because, list):
            bad.append(f"{_label(e, i)} (because is not a list)")
            continue
        missing = [b for b in because if b not in facts]
        if missing:
            bad.append(f"{_label(e, i)} (unresolved: {', '.join(map(str, missing))})")
    if bad:
        return Result("F2a", FAIL, f"{len(bad)} element(s) untraced or citing unknown facts", bad)
    return Result("F2a", PASS, f"all {len(els)} elements trace to real facts")


def check_F2b(frame):
    """A cited fact must not be NEWER than the element citing it.

    Forward-only. A finished artifact carries no version history, so a
    retrospective frame stamps everything first_seen: 1 and this cannot run.
    """
    els, facts = _elements(frame), _facts(frame)
    if not els or not facts:
        return Result("F2b", CANNOT_RUN, "needs both `elements` and `facts`")
    seens = [e.get("first_seen") for e in els if isinstance(e, dict)]
    seens += [f.get("first_seen") for f in facts.values() if isinstance(f, dict)]
    present = [s for s in seens if s is not None]
    if not present:
        return Result("F2b", CANNOT_RUN, "no `first_seen` stamps present")
    if len(set(present)) == 1:
        return Result("F2b", CANNOT_RUN,
                      f"every first_seen is {present[0]}; version history unavailable "
                      "(expected for a retrospective reconstruction) so creation order "
                      "cannot be tested")
    bad = []
    for i, e in enumerate(els):
        if not isinstance(e, dict):
            continue
        e_seen = e.get("first_seen")
        if e_seen is None:
            continue
        for b in (e.get("because") or []):
            f = facts.get(b)
            if isinstance(f, dict) and f.get("first_seen") is not None:
                if f["first_seen"] > e_seen:
                    bad.append(f"{_label(e, i)} cites {b} (fact v{f['first_seen']} "
                               f"> element v{e_seen}) -- retrofitted citation")
    if bad:
        return Result("F2b", FAIL, f"{len(bad)} retrofitted citation(s)", bad)
    return Result("F2b", PASS, "no element cites a fact newer than itself")


def check_F3(frame):
    """No single input is load-bearing in two elements."""
    els = _elements(frame)
    if not els:
        return Result("F3", CANNOT_RUN, "no `elements` in frame")
    declared = any(isinstance(e, dict) and e.get("inputs") for e in els)
    if not declared:
        return Result("F3", CANNOT_RUN, "no element declares `inputs`")
    owners = {}
    for i, e in enumerate(els):
        if not isinstance(e, dict):
            continue
        for inp in (e.get("inputs") or []):
            owners.setdefault(inp, []).append(_label(e, i))
    shared = {k: v for k, v in owners.items() if len(v) > 1}
    if shared:
        offenders = [f"{k} load-bearing in: {', '.join(v)}" for k, v in shared.items()]
        return Result("F3", FAIL,
                      f"{len(shared)} input(s) load-bearing in more than one element",
                      offenders)
    return Result("F3", PASS, f"{len(owners)} inputs, each load-bearing in exactly one element")


def check_F5(frame):
    """The set is closed and the closure is defended, with >=1 reasoned exclusion."""
    closure = str(frame.get("closure") or "").strip()
    exclusions = frame.get("exclusions")
    problems = []
    if not closure:
        problems.append("`closure` is empty -- the set is not defended")
    if not exclusions:
        problems.append("`exclusions` is empty -- nothing was deliberately left out")
    elif isinstance(exclusions, list):
        unreasoned = [str(x.get("element", x)) for x in exclusions
                      if isinstance(x, dict) and not str(x.get("reason") or "").strip()]
        if unreasoned:
            problems.append(f"exclusions without a reason: {', '.join(unreasoned)}")
    if problems:
        return Result("F5", FAIL, "the element set is not closed", problems)
    return Result("F5", PASS, "closure stated and at least one exclusion reasoned")


def check_F8a(frame):
    """Every fact cited by an element exists in the fact base."""
    els, facts = _elements(frame), _facts(frame)
    if not els:
        return Result("F8a", CANNOT_RUN, "no `elements` in frame")
    if not facts:
        return Result("F8a", CANNOT_RUN, "no `facts` block")
    missing = []
    for i, e in enumerate(els):
        if not isinstance(e, dict):
            continue
        for b in (e.get("because") or []):
            if b not in facts:
                missing.append(f"{_label(e, i)} -> {b}")
    if missing:
        return Result("F8a", FAIL, f"{len(missing)} citation(s) to nonexistent facts", missing)
    return Result("F8a", PASS, "every citation resolves into the fact base")


def check_F8b(frame):
    """No element takes a GUARDRAIL metric as a ranking input.

    The problem statement assigns each metric a role. A guardrail is a floor to be
    held, not an axis to rank on. An element ranking on a guardrail has silently
    reassigned its role, and nothing else in the process checks step 2 against step 1.
    """
    roles = _dig(frame, "d1.metric_roles")
    if not isinstance(roles, dict) or not roles:
        return Result("F8b", CANNOT_RUN,
                      "`d1.metric_roles` absent; the problem statement's metric roles "
                      "were never declared, so role reassignment is unverifiable")
    guardrails = {k for k, v in roles.items() if str(v).strip().lower() == "guardrail"}
    if not guardrails:
        return Result("F8b", PASS, "no metric is declared a guardrail")
    els = _elements(frame)
    inputs_map = frame.get("inputs") if isinstance(frame.get("inputs"), dict) else {}
    bad = []
    for i, e in enumerate(els):
        if not isinstance(e, dict):
            continue
        for inp in (e.get("inputs") or []):
            names = {str(inp)}
            spec = inputs_map.get(inp)
            if isinstance(spec, dict):
                if spec.get("name"):
                    names.add(str(spec["name"]))
                for a in (spec.get("aka") or []):
                    names.add(str(a))
            lowered = {n.strip().lower() for n in names}
            for g in guardrails:
                if g.strip().lower() in lowered:
                    bad.append(f"{_label(e, i)} ranks on {g!r}, declared a guardrail")
    if bad:
        return Result("F8b", FAIL, f"{len(bad)} guardrail metric(s) used as ranking inputs", bad)
    return Result("F8b", PASS, "no guardrail metric is used as a ranking input")


def check_F9(frame, prior):
    """A PROTECTED element must not lose its measure between locked versions."""
    if prior is None:
        return Result("F9", CANNOT_RUN,
                      "no prior locked version supplied; compression can only be "
                      "detected by diffing two versions")
    prior_measures = {}
    for e in _elements(prior):
        if isinstance(e, dict):
            key = e.get("id") or e.get("name")
            if key:
                prior_measures[key] = str(e.get("measure") or "").strip()
    if not prior_measures:
        return Result("F9", CANNOT_RUN, "prior version declares no elements")
    bad = []
    for i, e in enumerate(_elements(frame)):
        if not isinstance(e, dict):
            continue
        key = e.get("id") or e.get("name")
        if key in prior_measures and prior_measures[key]:
            now = str(e.get("measure") or "").strip()
            if not now:
                protected = e.get("protected", True)
                mark = "PROTECTED" if protected else "unprotected"
                bad.append(f"{_label(e, i)} lost its measure ({mark}): "
                           f"was {prior_measures[key]!r}")
    if bad:
        return Result("F9", FAIL, f"{len(bad)} element(s) silently compressed", bad)
    return Result("F9", PASS, "no protected element lost its measure")


def check_F10struct(frame):
    """Every unknown carries a disposition and that disposition's required fields."""
    unknowns = frame.get("unknowns")
    if not isinstance(unknowns, dict) or not unknowns:
        return Result("F10struct", CANNOT_RUN, "no `unknowns` block")
    required = {
        "assumption": ("basis", "sensitivity"),
        "data_request": ("owner", "due"),
        "question": ("owner",),
    }
    bad = []
    for key, u in unknowns.items():
        if not isinstance(u, dict):
            bad.append(f"{key} (not a mapping)")
            continue
        disp = str(u.get("disposition") or "").strip()
        if not disp:
            bad.append(f"{key} (no disposition)")
            continue
        if disp not in required:
            bad.append(f"{key} (unknown disposition {disp!r})")
            continue
        missing = [f for f in required[disp] if not str(u.get(f) or "").strip()]
        if missing:
            bad.append(f"{key} ({disp} missing: {', '.join(missing)})")
    if bad:
        return Result("F10struct", FAIL,
                      f"{len(bad)} of {len(unknowns)} unknowns are under-specified", bad)
    return Result("F10struct", PASS, f"all {len(unknowns)} unknowns fully dispositioned")


def check_F12(frame):
    """Every recommendation carries a stated confidence and a next action."""
    rec = frame.get("recommendation")
    if not isinstance(rec, dict) or not rec:
        return Result("F12", CANNOT_RUN, "no `recommendation` block")
    problems = []
    if not str(rec.get("confidence") or "").strip():
        problems.append("`confidence` is not stated")
    na = rec.get("next_action")
    if not na:
        problems.append("`next_action` is absent")
    elif isinstance(na, dict):
        for f in ("who", "what"):
            if not str(na.get(f) or "").strip():
                problems.append(f"`next_action.{f}` is empty")
    if problems:
        return Result("F12", FAIL, "recommendation is under-specified", problems)
    return Result("F12", PASS, "confidence stated and next action names who disposes it")


def check_F13(frame):
    """The backfill-impossible run record exists on any frame that went in the room.

    Triggered by `locked: true`, never by mere field presence. A retrospective
    reconstruction genuinely cannot carry a pre-room prediction or a live rejection
    record, so an unconditional requirement would fail the two corpora the acceptance
    regressions are pinned to. Lock is the moment the record stops being
    reconstructible, so it is exactly when the requirement should bite.

    WHY THIS CHECK EXISTS AT ALL: the schema marked these fields `required` and
    nothing ever read that. `required:` in frame-schema.yaml is documentation --
    grep the schema's own `validation:` block and there is no rule asserting a
    required field is present. A frame that lost both ledgers came back clean.
    """
    if frame.get("locked") is not True:
        return Result("F13", CANNOT_RUN,
                      "frame is not `locked: true`; the run record is still open")

    missing = []
    if not frame.get("proposals"):
        missing.append(
            "`proposals` is empty -- the rejection record is the only signal that "
            "separates an improved decision from a restated one")

    pred = frame.get("prediction")
    probed = pred.get("will_be_probed") if isinstance(pred, dict) else None
    if isinstance(probed, str):
        probed = probed.strip()
    if not probed:
        missing.append(
            "`prediction.will_be_probed` is empty -- it is contaminated the instant "
            "feedback arrives, so it cannot be added after the room")

    if missing:
        return Result("F13", FAIL,
                      "locked frame is missing backfill-impossible run record", missing)
    return Result("F13", PASS,
                  "rejection record and pre-room prediction both present at lock")


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def load_yaml(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def run_checks(frame, prior=None):
    return [
        check_F1a(frame),
        check_F1b(frame),
        check_F2a(frame),
        check_F2b(frame),
        check_F3(frame),
        check_F5(frame),
        check_F8a(frame),
        check_F8b(frame),
        check_F9(frame, prior),
        check_F10struct(frame),
        check_F12(frame),
        check_F13(frame),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frame", help="path to a frame.yaml")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--prior", default=None,
                    help="a prior locked frame.yaml; enables F9")
    ap.add_argument("--json", action="store_true", help="JSON only")
    args = ap.parse_args(argv)

    # --- schema gate: refuse a version we do not know, never silently accept ---
    schema_path = Path(args.schema)
    try:
        schema = load_yaml(schema_path)
        known = schema.get("schema_version")
    except Exception as exc:
        print(json.dumps({"status": "error", "stage": "schema",
                          "detail": f"cannot read {schema_path}: {exc}"}, indent=2))
        return 3

    frame_path = Path(args.frame)
    try:
        frame = load_yaml(frame_path)
    except Exception as exc:
        print(json.dumps({"status": "error", "stage": "frame",
                          "detail": f"cannot read {frame_path}: {exc}"}, indent=2))
        return 4
    if not isinstance(frame, dict):
        print(json.dumps({"status": "error", "stage": "frame",
                          "detail": "frame is not a mapping"}, indent=2))
        return 4

    # The schema declares which frame versions it still understands. Reading that
    # list rather than comparing equality means a version bump does not orphan every
    # frame written before it -- including the retrospective reconstructions that
    # acceptance regressions are pinned to. Adding a version is a schema edit, never
    # a code edit.
    supported = schema.get("supports_frames_at")
    if not isinstance(supported, list) or not supported:
        supported = [known]

    got = frame.get("schema_version")
    if got not in supported:
        print(json.dumps({"status": "refused", "stage": "schema",
                          "detail": f"frame schema_version {got!r} is not among the "
                                    f"versions this schema supports ({supported}); "
                                    "refusing rather than guessing",
                          "schema_version": known,
                          "supports_frames_at": supported}, indent=2))
        return 3

    prior = None
    if args.prior:
        try:
            prior = load_yaml(Path(args.prior))
        except Exception as exc:
            print(json.dumps({"status": "error", "stage": "prior",
                              "detail": f"cannot read {args.prior}: {exc}"}, indent=2))
            return 4

    # --- structural gate: a malformed field must never degrade into CANNOT_RUN ---
    structural = validate_structure(frame, schema)

    results = run_checks(frame, prior)
    fails = [r for r in results if r.state == FAIL]
    cannot = [r for r in results if r.state == CANNOT_RUN]
    passes = [r for r in results if r.state == PASS]

    # COVERAGE FLOOR. `clean` on a frame where almost nothing could execute is a
    # meaningless green light -- the exact result that exposed this bug (1 pass,
    # 0 fail, 10 cannot_run, clean=true). If fewer than two checks actually ran,
    # the frame has not been meaningfully tested and cannot be called clean.
    executed = len(passes) + len(fails)
    MIN_EXECUTED = 2
    under_floor = executed < MIN_EXECUTED

    payload = {
        "status": "ok",
        "frame": str(frame_path),
        "schema_version": got,
        "structural_errors": structural,
        "checks": [r.as_dict() for r in results],
        "counts": {"pass": len(passes), "fail": len(fails), "cannot_run": len(cannot),
                   "executed": executed},
        # clean requires: no FAILs, no structural errors, AND enough checks actually ran.
        # A CANNOT_RUN is never a pass. fully_covered additionally means nothing
        # went untested. Read all three.
        "clean": not fails and not structural and not under_floor,
        "under_coverage_floor": under_floor,
        "fully_covered": not fails and not structural and not cannot,
        "delegated_not_checked_here": {
            "blind_agent": ["F4", "F1 quality", "F2 vocabulary-vs-derivation",
                            "F3 distinction sentence", "F5 quality"],
            "operator": ["F6", "F7", "F10 which disposition", "F11"],
        },
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"\nframe: {frame_path}")
        print(f"schema_version: {got}\n")
        if structural:
            print("  STRUCTURAL ERRORS -- fields present but the wrong shape:")
            for e in structural:
                print(f"    ! {e}")
            print("    (a malformed field silently empties every check that reads it,")
            print("     so these are hard errors and not CANNOT_RUN)\n")
        for r in results:
            icon = {"PASS": "ok  ", "FAIL": "FAIL", "CANNOT_RUN": "----"}[r.state]
            print(f"  [{icon}] {r.rule:<10} {r.detail}")
            for o in r.offenders:
                print(f"           - {o}")
        c = payload["counts"]
        print(f"\n  {c['pass']} pass, {c['fail']} fail, {c['cannot_run']} cannot run "
              f"({c['executed']} actually executed)")
        print(f"  clean={payload['clean']}  fully_covered={payload['fully_covered']}")
        if under_floor:
            print(f"\n  UNDER COVERAGE FLOOR: only {executed} check(s) executed "
                  f"(min {MIN_EXECUTED}).\n  This frame has not been meaningfully "
                  "tested and is NOT clean regardless of failures.")
        if cannot:
            print("\n  NOTE: a CANNOT_RUN is not a pass. Those rules were not tested.")
        print()

    # Structural errors and an untested frame are both failures of the gate's
    # purpose, not merely absences. Exit non-zero for all three conditions.
    return 2 if (fails or structural or under_floor) else 0


if __name__ == "__main__":
    sys.exit(main())
