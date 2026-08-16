#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
blind_view.py — extract a REDACTED view of a frame for a blind agent.

THE POINT: replace a promise with a mechanism.

`framework/frame-schema.yaml` says `interaction` must be "filled by a BLIND agent that
has seen element names and one-line definitions only, never the artifact," because an
agent holding the artifact rationalises overlap as emphasis. Until now that was an
INSTRUCTION, and anti-anchoring failure is silent by construction: a contaminated agent's
output looks exactly like an independent one. There is no tell to notice afterward.

So the agent no longer gets told what not to look at. It gets handed a file that cannot
contain it.

WHITELIST, NEVER BLACKLIST. Each view names the exact keys it emits, and anything else is
dropped and then ASSERTED absent. A blacklist silently passes every field added to the
schema later, which is the same default-allow defect that let a `status: refused` verdict
read as a pass in frame_write.py (2026-08-15). Enumerate what is permitted; refuse the
complement.

EMPTY INPUT HARD-ABORTS. A blind view over zero elements would let an agent return a
confident "no interactions found," which is a false clean rather than an absence of
evidence. Per the guard-must-hard-abort-on-empty-input rule (7 fires in this repo).

CLI:
  blind_view.py --frame <path> --view interaction|closure|distinction [--out <path>]

Writes the view to --out (default: alongside the frame as blind-<view>.yaml) and prints
JSON describing what was emitted and what was withheld. Hand the AGENT the --out path and
nothing else. Never pass the frame path to a blind agent.
"""
import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(json.dumps({"status": "error", "message": "PyYAML required: pip install pyyaml"}))
    sys.exit(3)

# Per-element keys each view may emit. Everything else in an element is withheld:
# `because` (fact ids) is provenance, `inputs` would hand over the very overlap F3 exists
# to detect independently, and `first_seen`/`protected` are run bookkeeping.
VIEWS = {
    # F4: does the element set interact? Needs the name and the level below it, nothing more.
    "interaction": {
        "element_keys": ["id", "name", "measure"],
        "task": ("For each PAIR of elements, state whether they are independent, "
                 "overlapping, or opposed, and why. You are seeing names and measures "
                 "only. If two measures could move for the same underlying reason, say "
                 "so plainly."),
    },
    # F5 quality: does the set close? Names only -- a measure would invite the agent to
    # argue from the measure rather than from what the set covers.
    "closure": {
        "element_keys": ["id", "name"],
        "task": ("Name what this set of elements does NOT cover. Propose at least one "
                 "plausible element that is missing, and say what it would add."),
    },
    # F3 distinction sentence: can each pair be told apart in one sentence?
    "distinction": {
        "element_keys": ["id", "name", "measure"],
        "task": ("For each PAIR, write ONE sentence that distinguishes them. If you "
                 "cannot write it without repeating yourself, the pair is a "
                 "double-count. Say which pairs failed."),
    },
}

# Top-level frame keys that must NEVER appear in any view. Named explicitly so the
# refusal message can say what was being protected, not just that something leaked.
NEVER = ["d1", "facts", "unknowns", "closure", "exclusions", "interaction",
         "recommendation", "proposals", "overrides", "falsification", "prediction",
         "compression_ledger", "declines", "check_log", "engagement"]


def out(payload: dict, code: int = 0):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(code)


def die(msg: str, **extra):
    out({"status": "error", "message": msg, **extra}, code=1)


def assert_no_leak(payload: dict, allowed: list):
    """The last line of defence, and it must be REACHABLE BY A TEST or it is decoration.

    Called on the finished payload rather than trusted from the construction loop above,
    because the whole class of failure here is a future edit to that loop. Kept as a
    separate function specifically so a test can hand it a contaminated payload directly:
    a guard whose trigger condition can never occur in production is not evidence of
    anything until it has been exercised. Mutation-tested 2026-08-16 -- an earlier inline
    version SURVIVED its mutant, which is what prompted extracting it.
    """
    for el in payload.get("elements", []):
        extra = set(el) - set(allowed)
        if extra:
            die(f"REFUSED: view emitted non-whitelisted element key(s) {sorted(extra)}; "
                "refusing rather than shipping a contaminated view",
                leaked_keys=sorted(extra))
    for key in NEVER:
        if key in payload:
            die(f"REFUSED: view emitted protected top-level key {key!r}; refusing",
                leaked_keys=[key])


def build(frame: dict, view: str) -> tuple:
    spec = VIEWS[view]
    allowed = spec["element_keys"]

    elements = frame.get("elements")
    if not isinstance(elements, list) or not elements:
        die("REFUSED: the frame has no elements, so a blind view would be a view of "
            "nothing. An agent handed an empty set returns a confident 'no problems "
            "found', which is a FALSE CLEAN, not an absence of evidence.")

    redacted, withheld = [], set()
    for i, el in enumerate(elements):
        if not isinstance(el, dict):
            die(f"element {i} is not a mapping; refusing to build a view from it")
        row = {}
        for k in allowed:
            if k in el:
                row[k] = el[k]
        withheld |= (set(el) - set(allowed))
        if not row.get("name"):
            die(f"element {i} has no name; a blind view keyed on names cannot be built. "
                "Nothing was written.")
        redacted.append(row)

    payload = {
        "view": view,
        "element_count": len(redacted),
        "elements": redacted,
        "task": spec["task"],
        "you_are_blind": (
            "This is a DELIBERATELY REDACTED view. You are not seeing the problem "
            "statement, the fact base, the artifact, or the reasoning behind any "
            "element, and that is intentional: an agent holding the artifact "
            "rationalises overlap as emphasis. Answer from what is here. If you cannot "
            "answer without more context, say so plainly rather than inferring the "
            "missing context. Do not ask for the full frame."
        ),
    }

    # The assertion is the guard, not the construction. Building the dict correctly and
    # trusting that is how a leak ships silently; this fails loudly instead.
    assert_no_leak(payload, allowed)

    return payload, sorted(withheld)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frame", required=True)
    ap.add_argument("--view", required=True, choices=sorted(VIEWS))
    ap.add_argument("--out", help="default: blind-<view>.yaml beside the frame")
    a = ap.parse_args(argv)

    fp = Path(a.frame)
    if not fp.exists():
        die(f"no frame at {fp}")
    try:
        frame = yaml.safe_load(fp.read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"frame does not parse: {exc}")
    if not isinstance(frame, dict):
        die("frame is not a mapping")

    payload, withheld = build(frame, a.view)
    dest = Path(a.out) if a.out else fp.parent / f"blind-{a.view}.yaml"
    dest.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")

    out({
        "status": "ok",
        "view": a.view,
        "wrote": str(dest),
        "element_count": payload["element_count"],
        "emitted_element_keys": VIEWS[a.view]["element_keys"],
        "withheld_element_keys": withheld,
        "withheld_frame_keys": [k for k in NEVER if k in frame],
        "hand_the_agent": str(dest),
        "warning": ("Hand the agent THIS PATH ONLY. Passing the frame path, the brief, or "
                    "any dossier alongside it silently defeats the whole mechanism, and a "
                    "contaminated blind agent produces output indistinguishable from a "
                    "clean one."),
    })


if __name__ == "__main__":
    main()
