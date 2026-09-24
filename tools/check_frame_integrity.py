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
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
        # also_printed_on is a LIST of the same kind of token. Left unvalidated it is
        # the obvious way prose creeps back into a surface field, and F15 silently
        # stops matching -- which reads as the page being clean.
        extra = e.get("also_printed_on")
        if extra is None:
            continue
        if not isinstance(extra, list):
            errors.append(f"{_label(e, i)}.also_printed_on must be a list of "
                          f"identifiers, got {type(extra).__name__}")
            continue
        for val in extra:
            if not isinstance(val, str) or not rx.match(val):
                shown = str(val)[:60] + ("..." if len(str(val)) > 60 else "")
                errors.append(
                    f"{_label(e, i)}.also_printed_on entry must be an identifier "
                    f"matching {pattern}, got: {shown!r}")
    return errors


_ABSENT = object()


def validate_enums(frame, schema):
    """A field the schema gives a `values:` list must hold one of those values.

    WHY THIS EXISTS. The schema's own `limits:` block already records that
    `required: true` is DOCUMENTATION -- nothing read it, and a frame missing two
    required fields returned clean. `values:` was the same defect, undiscovered:
    _shape_of maps `enum` to `str`, so ANY string passed, and the vocabulary printed
    beside the field was decoration.

    FOUND ON THE LIVE FRAME, 2026-09-21. `status: submitted` had been sitting in a
    field whose declared vocabulary is in_progress | awaiting_outcome | complete |
    abandoned. It passed every gate for a day, and `submitted` is not a synonym --
    the state it names is `awaiting_outcome`, which is the value the run protocol
    reads. A consumer keying on the enum sees an unknown token and either crashes or,
    worse, falls through to a default.

    THE GENERAL SHAPE, third instance in this repo: a producer writes a value outside
    a declared vocabulary and every consumer collapses it silently. Enum membership is
    the cheapest possible check and it has to run at WRITE time, which is why this
    returns a STRUCTURAL error -- frame_write.py refuses a candidate carrying one --
    rather than a rule FAIL, which it would let through.

    Only fields the schema declares with an explicit `values:` list are checked, so
    adding a vocabulary is a schema edit and never a code edit.
    """
    fields = (schema or {}).get("fields")
    if not isinstance(fields, dict):
        return []
    errors = []
    for dotted, spec in fields.items():
        if not isinstance(spec, dict):
            continue
        values = spec.get("values")
        if not isinstance(values, list) or not values:
            continue
        declared = str(spec.get("type", ""))
        val = _dig(frame, dotted, _ABSENT)
        if val is _ABSENT or val is None:
            continue
        allowed = [str(v) for v in values]
        if declared.startswith("map["):
            # e.g. d1.metric_roles: map[metric -> enum]. The VALUES carry the vocabulary,
            # the keys are free. Checking the keys here would reject every real metric.
            if not isinstance(val, dict):
                continue
            for k, v in val.items():
                if str(v) not in allowed:
                    errors.append(
                        f"{dotted}[{k}] is {v!r}, which is not one of "
                        f"{'|'.join(allowed)}")
        elif declared.startswith("list["):
            if not isinstance(val, list):
                continue
            for v in val:
                if str(v) not in allowed:
                    errors.append(
                        f"{dotted} entry {v!r} is not one of {'|'.join(allowed)}")
        else:
            if str(val) not in allowed:
                errors.append(
                    f"{dotted} is {val!r}, which is not one of {'|'.join(allowed)}. "
                    "The vocabulary is the schema's, not the author's -- a consumer "
                    "keying on this field cannot see a token that is not in it")
    return errors


def validate_fact_stamps(frame):
    """Every fact carries `first_seen`.

    WHY STRUCTURAL. `first_seen` is what makes F2 mechanically checkable and it is
    BACKFILL-IMPOSSIBLE in the general case -- it can only be recovered here because
    this engagement happened to keep 55 numbered snapshots, which is not a guarantee
    the schema makes. A fact written without it silently removes itself from F2b's
    reach, and from any comparison against a recorded `delivery.version`, which is the
    only thing separating post-delivery authoring from backfill.

    MEASURED 2026-09-21: 15 of 61 facts on a live frame carried no stamp, all of them
    among the most recent, so "which facts existed when the artifact shipped" could only
    be inferred from absence rather than asserted. Recovered from the snapshots and
    stamped; this stops the next one.
    """
    facts = _facts(frame)
    if not facts:
        return []
    # A non-mapping fact was SKIPPED, so the malformed case sailed through the guard
    # entirely; and any non-null value counted as a stamp, so `first_seen: "soon"`
    # passed while being unusable by F2b and by the delivery comparison. Cross-model
    # review 2026-09-22 (F3). A bool is not an int here: `True` is 1 in Python and a
    # mis-keyed flag must not read as version 1.
    bad = []
    for k, v in facts.items():
        if not isinstance(v, dict):
            bad.append(f"{k} (not a mapping)")
            continue
        fs = v.get("first_seen")
        if fs is None:
            bad.append(k)
        elif not isinstance(fs, int) or isinstance(fs, bool):
            bad.append(f"{k} (first_seen is {fs!r}, not an int)")
    if not bad:
        return []
    shown = ", ".join(sorted(bad)[:8]) + (f" (+{len(bad) - 8} more)" if len(bad) > 8 else "")
    return [f"{len(bad)} fact(s) carry no `first_seen`: {shown}. Without it a fact is "
            "outside F2b's reach and cannot be placed before or after a recorded "
            "delivery, which is the difference between authoring and backfilling"]


def validate_structure(frame, schema):
    """Type-check every PRESENT field against the schema's declared shape.

    Absent fields are NOT errors here -- a frame is legitimately incomplete for most
    of its life (D1 is authored before D2, elements before closure). This checks only
    that what IS there has the right shape. A wrong shape is a hard error, never a
    silent CANNOT_RUN.
    """
    errors = list(detect_flat_dotted_keys(frame, schema))
    errors += validate_surface_identifiers(frame, schema)
    errors += validate_enums(frame, schema)
    errors += validate_fact_stamps(frame)
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
            # An unstamped element cannot be DATED, so its citations cannot be tested.
            # Skipping it let a retrofitted citation PASS by dropping the stamp
            # (cross-model 2026-09-23, Grok round 2, F1). It is an offender.
            if e.get("because"):
                bad.append(f"{_label(e, i)} has no first_seen, so the creation order of "
                           "its citations cannot be tested")
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
    locked = frame.get("locked") is True
    delivery = frame.get("delivery")
    delivered = isinstance(delivery, dict) and delivery.get("version") is not None
    if not locked and not delivered:
        return Result("F13", CANNOT_RUN,
                      "frame is not `locked: true` and no delivery is recorded; the "
                      "run record is still open")

    # A frame that already DELIVERED is not "still open", and reporting it that way is
    # how an engagement reads as pending forever. Delivery closes the record exactly as
    # lock does, so it gets the SAME two-field check. The delivered branch used to test
    # only the prediction, so delivered + a prediction + `proposals: []` fell through to
    # "no delivery is recorded", which was false. Closeout comb A1 F4 (P1), 2026-09-23.
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
        if locked:
            missing.append(
                "`prediction.will_be_probed` is empty -- it is contaminated the instant "
                "feedback arrives, so it cannot be added after the room")
        else:
            # The artifact went in the room; the pre-room prediction was never stamped
            # and now cannot be. A permanent, knowable loss, stated once rather than
            # deferred by a CANNOT_RUN each run.
            missing.append(
                "`prediction.will_be_probed` was never stamped before "
                "delivery. It is contaminated the instant feedback "
                "arrives, so this run has permanently lost it. Recording "
                "the loss is the honest end state -- do NOT author one "
                "now to clear this")

    when = "locked" if locked else f"delivered at v{delivery.get('version')}"
    if missing:
        return Result("F13", FAIL,
                      f"{when}; frame is missing backfill-impossible run record", missing)
    return Result("F13", PASS,
                  f"{when}; rejection record and pre-room prediction both present")


def check_F16(frame):
    """`delivery` names a real, already-written version, and a retrospective record says
    how that version was determined.

    WHY A BASIS IS MANDATORY. The version number is the whole value of this field, and
    after the fact it is RECONSTRUCTED rather than witnessed -- from a log line, a
    confirmation, a file timestamp. A reconstructed number with no stated basis is a
    guess wearing the authority of a field, and the next session cannot tell the two
    apart. Same shape as `status_reason` being mandatory for `abandoned`.
    """
    delivery = frame.get("delivery")
    if delivery is None:
        return Result("F16", CANNOT_RUN,
                      "no `delivery` recorded; whether this frame's artifact has gone "
                      "out is not stated either way")
    if not isinstance(delivery, dict):
        return Result("F16", FAIL, "`delivery` is not a mapping",
                      [f"got {type(delivery).__name__}"])

    problems = []
    dv = delivery.get("version")
    cur = frame.get("version")
    if dv is None:
        problems.append("`delivery.version` is missing -- the field's whole value is "
                        "naming WHICH version went out")
    elif not isinstance(dv, int) or isinstance(dv, bool):
        problems.append(f"`delivery.version` must be an int, got {dv!r}")
    elif not isinstance(cur, int) or isinstance(cur, bool):
        problems.append("the frame carries no integer `version`, so `delivery.version` "
                        f"{dv} cannot be checked against it; an unbounded delivery "
                        "version is not a verified one")
    elif dv < 1:
        problems.append(f"`delivery.version` is {dv}; versions start at 1 and must be "
                        ">= 1 (cross-model 2026-09-23 final, Codex F2)")
    elif dv > cur:
        problems.append(f"`delivery.version` is {dv}, ahead of the current version "
                        f"{cur}; a version that does not exist yet cannot have shipped")

    at = delivery.get("at")
    if not str(at or "").strip():
        problems.append("`delivery.at` is empty -- when it went out")
    elif not isinstance(at, _dt.date):
        # YAML may load an unquoted date as a date already; text must parse as one.
        try:
            _dt.date.fromisoformat(str(at).strip())
        except ValueError:
            problems.append(f"`delivery.at` is {at!r}, not a YYYY-MM-DD date; an "
                            "arbitrary string cannot order the delivery against anything")

    if delivery.get("retrospective") is True:
        if not str(delivery.get("basis") or "").strip():
            problems.append(
                "`delivery.retrospective` is true but `basis` is empty. A version "
                "reconstructed after the fact must say HOW it was determined, or the "
                "number is a guess with the authority of a field")

    if problems:
        return Result("F16", FAIL, f"{len(problems)} delivery record problem(s)",
                      problems)
    kind = "retrospective" if delivery.get("retrospective") is True else "recorded at the time"
    return Result("F16", PASS,
                  f"delivered at v{dv} on {delivery.get('at')} ({kind})")


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def load_yaml(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


VALID_DISPOSITIONS = {"promote", "engagement_only", "superseded"}
# promote and superseded both name WHAT the script became; engagement_only must say WHY
# it is policy rather than mechanism. A disposition with no required field is a label.
REQUIRES = {"promote": "target", "superseded": "target", "engagement_only": "reason"}


def check_F14(frame, frame_path=None):
    """Every script the engagement produced carries a disposition.

    THE CODE DRAIN. The self-learning loop drains rules and friction; nothing drains code.
    One engagement closed with 34 scripts beside its frame, four of which were already the
    probes the method was separately planning to build from scratch.

    TWO INDEPENDENT QUESTIONS, AND COLLAPSING THEM IS THE DEFECT THIS FILE EXISTS FOR:

      1. are the declarations WELL-FORMED?  -- answerable from the frame alone
      2. do they COVER what is on disk?     -- needs the directory, which needs frame_path

    A first draft answered (1) and returned PASS, reporting "all N dispositioned" while
    having compared them to nothing. Well-formed declarations whose coverage could not be
    checked are CANNOT_RUN, never PASS. Likewise an absent `scripts` block with no
    directory to enumerate: "produced none" and "never recorded" are indistinguishable
    from there. An EXPLICIT empty map, verified against an empty or absent tree, is a real
    answer and passes.
    """
    declared = frame.get("scripts")
    has_block = isinstance(declared, dict)

    enumerated, found, why_not = False, [], ""
    if frame_path is not None:
        parent = Path(frame_path).parent
        d = parent / "scripts"
        try:
            if d.is_dir():
                # EVERY file, not *.py. The check promises "every script the engagement
                # produced"; globbing *.py silently pre-filters the population to
                # top-level Python and then reports the remainder as absent. A .sh, an
                # .ipynb, an extensionless executable and anything in a subdirectory were
                # all invisible, so `scripts: {}` read PASS with a shell script sitting
                # beside the frame. What counts as a script is POLICY and belongs in the
                # disposition, not in a glob here.
                enumerated = True
                found = sorted(
                    str(f.relative_to(d)) for f in d.rglob("*")
                    if f.is_file()
                    and "__pycache__" not in f.parts
                    and not f.name.startswith(".")
                    and f.name != "__init__.py"
                )
            elif d.exists():
                why_not = f"{d} exists and is not a directory"
            elif not parent.is_dir():
                # The frame's own location is not readable, so an absent scripts/ proves
                # nothing. A mistyped path must not read as a verified empty tree.
                why_not = f"the frame's directory {parent} does not exist"
            else:
                # Parent readable and no scripts/ in it: a genuine, verified absence.
                enumerated = True
        except OSError as exc:
            why_not = f"{d} could not be read: {type(exc).__name__}: {exc}"

    if not has_block:
        if found:
            return Result("F14", FAIL,
                          f"{len(found)} script(s) beside the frame and no `scripts` block",
                          found)
        if not enumerated:
            reason = why_not or "no frame path to enumerate against"
            return Result("F14", CANNOT_RUN,
                          f"no `scripts` block and the tree could not be enumerated "
                          f"({reason}); 'produced none' and 'never recorded' cannot be "
                          "told apart")
        return Result("F14", CANNOT_RUN,
                      "no `scripts` block. The tree beside the frame is empty, but silence "
                      "is not a declaration -- write `scripts: {}` to state it")

    problems = []
    for name, spec in declared.items():
        if not isinstance(spec, dict):
            problems.append(f"{name}: disposition entry is not a mapping")
            continue
        disp = str(spec.get("disposition", "")).strip().lower()
        if disp not in VALID_DISPOSITIONS:
            problems.append(f"{name}: disposition {disp!r} not one of "
                            f"{sorted(VALID_DISPOSITIONS)}")
            continue
        need = REQUIRES[disp]
        if not str(spec.get(need, "")).strip():
            problems.append(f"{name}: disposition {disp!r} requires a non-empty {need!r}")

    if enumerated:
        undispositioned = [f for f in found if f not in declared]
        if undispositioned:
            problems.append(f"{len(undispositioned)} script(s) present but undispositioned: "
                            + ", ".join(undispositioned))

    # A `promote` or `superseded` target that does not exist is an UNFINISHED promotion.
    # Without this, "target: tools/foo.py" is a note-to-self: the disposition records a
    # decision and nothing ever checks that the decision was carried out. Same shape as
    # every other defect this file guards -- a well-formed declaration compared to nothing.
    # Relative targets resolve from the repo root (this file's parent's parent).
    repo_root = Path(__file__).resolve().parents[1]
    for name, spec in declared.items():
        if not isinstance(spec, dict):
            continue
        disp = str(spec.get("disposition", "")).strip().lower()
        target = str(spec.get("target", "")).strip()
        if disp not in ("promote", "superseded") or not target:
            continue
        # A bare filename names a SIBLING script, so the repo-path existence test does
        # not apply to it -- but `continue` here also skipped the retirement check
        # below, so a promote whose target was a bare filename was exempt from both.
        # Cross-model review 2026-09-22 (F4).
        # A bare filename is still a target that must exist: resolved against the scripts
        # directory, it got no existence test at all, so `gone.py -> nonexistent.py` with
        # neither file on disk read PASS. Closeout comb A1 F2 (P0), reproduced 2026-09-23.
        # With no tree to resolve it against, the target is unchecked and the enumeration
        # CANNOT_RUN below reports that.
        target_is_repo_path = "/" in target
        if target_is_repo_path:
            target_exists = (repo_root / target).exists()
        else:
            target_exists = (d / target).exists() if enumerated else None
        if target_exists is False:
            problems.append(
                f"{name}: disposition {disp!r} names target {target!r}, which does not "
                "exist. The decision was recorded and never carried out")
        # STEP 7, RETIRE THE ORIGINAL. F14 checked that the promotion TARGET exists and
        # never that the SOURCE went away, so a promoted script could sit beside the frame
        # as a full duplicate indefinitely -- three did, and one of them had drifted behind
        # its promoted twin by a whole check, while the gate read clean. "Keep these two
        # copies in sync" is prose; an absent original cannot drift.
        #
        # Only fires once the target EXISTS: between promoting and retiring there is a
        # legitimate window, and the target's absence is already reported above, so this
        # cannot double-report the same unfinished promotion.
        if disp == "promote" and enumerated and name in found and target_exists:
            problems.append(
                f"{name}: promoted to {target!r}, which exists, but the original is still "
                "on disk beside the frame. Retire it -- two copies of one mechanism drift, "
                "and the engagement-local copy is the one nothing tests")

    if problems:
        return Result("F14", FAIL,
                      f"{len(problems)} script disposition problem(s)", problems)

    if not enumerated:
        reason = why_not or "no frame path was supplied"
        return Result("F14", CANNOT_RUN,
                      f"{len(declared)} declaration(s) are well-formed, but COVERAGE was "
                      f"never checked against the tree ({reason})")

    # Declared but absent on disk: the registry has drifted. Never silent -- a stale entry
    # is how a count quietly shrinks while reading clean.
    stale = [n for n in declared if n not in found]
    detail = (f"all {len(found)} script(s) on disk dispositioned"
              if declared else
              "`scripts` explicitly empty and the tree agrees: this engagement produced none")
    if stale:
        detail += f" -- NOTE {len(stale)} declared but absent on disk: {', '.join(sorted(stale))}"
    return Result("F14", PASS, detail)


# --- F15: the reverse of F2a -------------------------------------------------------
#
# F2a asks whether every ELEMENT traces to a real fact. Nothing asked the reverse:
# whether every CLAIM PRINTED ON A SURFACE is carried by an element declaring that
# surface. The gap shipped. On 2026-09-21 a deck went to a client with a
# concurrency figure printed on slide 1 while the only element citing that fact
# declared `measure_surface: workbook`, and every gate stayed green, because F1b only
# checks that a measure sits on the surface that NAMES it -- never that a surface
# names only what some element accounts for.
#
# THE UNIT IS THE PRINTED NUMBER, NOT THE FACT. Measured 2026-09-21 by replaying both
# candidates across all 53 historical versions of one engagement's frame against the deck
# that actually shipped:
#   fact-anchored   ("is this fact cited on this surface?")   47 fires at v52, 1 real -> 2.1%
#   number-anchored ("is this printed number carried here?")  29 fires at v52
# The fact-anchored form fires for every fact that merely MENTIONS a number the page
# prints, and nine facts in that frame mention one population count because nine facts
# discuss that same population.
# A number identifies a QUANTITY, not a fact.
#
# RECALL, measured the same way: that figure on slide-1 reads unaccounted on v1 to v52
# and flips to accounted at v53, the version that added the citing element. The rule
# catches the real defect and self-clears when the frame is repaired.
#
# KNOWN FALSE POSITIVES, stated because an instrument that cannot state its own
# false-positive rate is not an instrument. Chart AXIS TICKS are printed numbers that
# are not claims, and they sit in the same <svg> as real chart values, so no structural
# rule separates them -- on the measured deck that is 2 of 36 printed numbers (5.6%).
# Provenance lines ARE separable and are excluded by class. Rounded restatements are
# missed: a fact stating a rate to two decimals and a page printing it rounded do not
# match, by choice, because a tolerance band wide enough to join them also collides two
# neighbouring whole percentages that were different claims on the measured deck.

SURFACE_NUMBER_MIN_VERSION = 3
PROVENANCE_CLASSES = ("src", "source", "footnote")


def _norm_number(tok: str) -> str:
    """12.0% and 12% are the same claim; 1,234 and 1234 are the same number.

    Precision and grouping are formatting. Value identity is what a claim is made of.
    """
    raw = tok.rstrip("%").replace(",", "").lstrip("+")
    # Decimal, not float `:g`. `:g` keeps six significant digits, so 1,234,567.5 and
    # 1,234,568.1 both became 1.23457e+06 and one printed figure read as carrying the
    # other. Closeout comb A1 F1 (P0), reproduced 2026-09-23.
    try:
        val = Decimal(raw)
    except InvalidOperation:
        return tok
    if not val.is_finite():
        return tok
    body = "0" if val == 0 else format(val.normalize(), "f")
    return body + "%" if tok.endswith("%") else body


def _claim_numbers(text: str) -> set:
    """The numeric tokens in `text` distinctive enough to identify a claim.

    Percentages at any precision, comma-grouped magnitudes, and bare integers of four
    digits or more. Bare small integers are deliberately excluded: they are step
    numbers, window sizes and page furniture, and they collide by construction. With
    them in, the naive rule fired 89 times on a two-page deck.
    """
    out = set()
    # The SIGN is part of the value: -5% and 5% are different claims, and dropping the
    # minus made them the same token. The DECIMAL TAIL is too -- `\d{1,3}(?:,\d{3})+`
    # matches "1,234" out of "1,234.56" and truncates it, so 1,234.56 and 1,234.99
    # collapsed onto one another. Both were found by cross-model review 2026-09-22 (F2).
    # A percent may be comma-grouped, and neither pattern may START inside a number:
    # without the lookbehind "1,234.5%" also yielded "234.5%", a figure the page never
    # printed, and the grouped pattern took "1,234.5" off the same token without its %.
    # Closeout comb A1 F3 (P1), reproduced 2026-09-23. The four-digit pattern's lookahead
    # skips `.` too: without it the pattern backtracked off the decimal and took "1234"
    # out of "1234.56%" (cross-model 2026-09-23, Grok F1).
    for m in re.findall(r"(?<![\d.,])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%", text):
        out.add(_norm_number(m))
    for m in re.findall(r"(?<![\d.,])[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d.,]*%)", text):
        out.add(_norm_number(m))
    for m in re.findall(r"(?<![\d.,%+-])[-+]?\d{4,}(?:\.\d+)?(?![\d.,]*%)(?![-\w])", text):
        out.add(_norm_number(m))
    return out


def check_F15(frame, deck_path=None):
    """Every distinctive number a surface prints is carried by a fact some element
    declaring that surface cites."""
    if not deck_path:
        return Result("F15", CANNOT_RUN,
                      "no --deck supplied; what a surface PRINTS cannot be read from "
                      "the frame alone, and the frame's own declarations are the thing "
                      "under test, so they cannot stand in for the artifact")

    ver = frame.get("schema_version")
    if isinstance(ver, int) and ver < SURFACE_NUMBER_MIN_VERSION:
        return Result("F15", CANNOT_RUN,
                      f"frame is at schema v{ver}, where surfaces are free prose and "
                      "cannot be matched to a rendered page")

    try:
        from slide_check import deck_pages
    except Exception as exc:  # pragma: no cover - checkout without the deck reader
        return Result("F15", CANNOT_RUN,
                      f"deck reader unavailable ({exc}); the page cannot be read")

    try:
        pages = deck_pages(Path(deck_path), exclude_classes=PROVENANCE_CLASSES)
    except Exception as exc:
        return Result("F15", CANNOT_RUN, f"cannot read deck {deck_path}: {exc}")
    if not pages:
        return Result("F15", CANNOT_RUN, f"{deck_path} renders no pages")

    els, facts = _elements(frame), _facts(frame)
    if not els:
        return Result("F15", CANNOT_RUN, "no `elements` in frame")
    if not facts:
        return Result("F15", CANNOT_RUN, "no `facts` block")

    # An element accounts for its facts on the surface where it is MADE, plus any
    # surface it declares its evidence also reaches. F1b deliberately does not read
    # also_printed_on: co-location of name and measure still means one surface, and
    # widening it here would let an element claim its measure sits where it does not.
    cited_on = {}
    for e in els:
        if not isinstance(e, dict):
            continue
        surfaces = []
        if e.get("name_surface"):
            surfaces.append(str(e["name_surface"]).strip())
        extra = e.get("also_printed_on")
        if isinstance(extra, list):
            surfaces += [str(x).strip() for x in extra if str(x).strip()]
        for surface in surfaces:
            cited_on.setdefault(surface, set()).update(e.get("because") or [])

    printed = {f"slide-{i + 1}": _claim_numbers(t) for i, t in enumerate(pages)}
    covered = [s for s in printed if s in cited_on]
    if not covered:
        return Result("F15", CANNOT_RUN,
                      f"the deck renders {', '.join(sorted(printed))} and no element "
                      f"declares any of them (declared: "
                      f"{', '.join(sorted(cited_on)) or 'nothing'}); the surface "
                      "vocabulary does not line up with the artifact")

    # A surface whose rendered text yields NO recognized number was reported as
    # "all 0 number(s) ... are carried", a green verdict on a page this rule measured
    # nothing about. That is the vacuous pass the three-state design exists to refuse,
    # and it is the same shape as F2a's empty-`because` case. Cross-model review
    # 2026-09-22 (F1). A surface can legitimately print no figures -- so it is reported
    # as UNMEASURED, and if NO covered surface yields a number the rule CANNOT_RUN.
    measurable = [sf for sf in covered if printed[sf]]
    unmeasured = sorted(sf for sf in covered if not printed[sf])
    if not measurable:
        return Result("F15", CANNOT_RUN,
                      f"no recognized number on any declared surface "
                      f"({', '.join(sorted(covered))}); the rule matched a page and "
                      "measured nothing on it, which is not a pass")

    bad, n_printed = [], 0
    for surface in sorted(measurable):
        carried = set()
        for fid in cited_on.get(surface, set()):
            f = facts.get(fid)
            if isinstance(f, dict):
                carried |= _claim_numbers(str(f.get("text", "")))
        n_printed += len(printed[surface])
        for n in sorted(printed[surface] - carried):
            bad.append(f"{surface} prints {n} -- carried by no fact any element "
                       f"declaring {surface} cites")

    checked = f"{n_printed} number(s) across {', '.join(sorted(measurable))}"
    if unmeasured:
        checked += f" -- NOTE no recognized number on {', '.join(unmeasured)}"
    if bad:
        return Result("F15", FAIL,
                      f"{len(bad)} of {checked} are printed but undeclared", bad)
    return Result("F15", PASS, f"all {checked} are carried by a declared element")


def run_checks(frame, prior=None, frame_path=None, deck_path=None):
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
        check_F14(frame, frame_path),
        check_F15(frame, deck_path),
        check_F16(frame),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frame", help="path to a frame.yaml")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--prior", default=None,
                    help="a prior locked frame.yaml; enables F9")
    ap.add_argument("--deck", default=None,
                    help="the rendered deck this frame's surfaces refer to; enables "
                         "F15, which reads what each page actually prints. Without it "
                         "F15 reports CANNOT_RUN rather than passing vacuously")
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

    results = run_checks(frame, prior, frame_path=frame_path,
                         deck_path=args.deck)
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
