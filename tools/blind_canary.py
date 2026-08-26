#!/usr/bin/env python3
"""Detect whether a supposedly-blind agent saw material it was not given.

THE PROBLEM. Several steps in the analysis method depend on an agent being blind:
F3 and F4 must run on element names and one-line definitions only, never on the
artifact, because an agent holding the artifact rationalises overlap as emphasis.
Today that blindness is enforced by asking the agent nicely. An instruction is not
an enforcement mechanism, and an agent that reads more than it was given produces
output indistinguishable from an honest one.

THE METHOD. Blindness becomes checkable by making it a set difference on disk:

    outside_only = tokens(everything in the engagement dir) - tokens(the scratch file)

Those tokens exist ONLY in material the agent was never handed. If any appears in
its output, it read something it should not have. This is computed from real files,
not from the agent's own account of what it opened -- a self-report is exactly what
a contaminated agent gets wrong.

WHAT THIS CANNOT DO, stated plainly so a PASS is not over-read:

  * It detects TOKEN leakage, not CONCEPT leakage. An agent that reads the artifact
    and paraphrases it in its own words evades this entirely. A clean result means
    "no verbatim tokens crossed", never "the agent was blind".
  * It cannot see reasoning, only text returned.

Both limitations are why the canary reports its denominators rather than a bare
verdict, and why `clean` is withheld whenever the mechanism is unproven.

TWO REFUSALS, both deliberate:

  1. An EMPTY outside-only token set exits 2. If the scratch file and the wider
     material share all their vocabulary there is nothing that could ever be
     detected, so a "clean" verdict would be structurally meaningless. An empty
     scope is an error, never a negative result. (Same rule as tools/sweep.py.)
  2. A missing CONTROL token exits 3. A distinctive token planted in the scratch
     file MUST appear in the agent's output. If it does not, either the agent never
     read its input or the matching is broken -- and in both cases a "no leakage"
     finding is worthless, because nothing was proven to be detectable. A negative
     result is only trustworthy once the mechanism is shown to work.

Exit codes:
  0  no leaked tokens, control proven      -> clean
  1  leaked tokens found                   -> contaminated
  2  empty outside-only set                -> mechanism cannot detect anything
  3  control token absent from agent output-> mechanism unproven, verdict withheld
  4  unreadable input

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/blind_canary.py \\
      --scratch  output/<slug>/blind-scratch.md \\
      --outside  output/<slug> \\
      --output   output/<slug>/agent-return.txt \\
      --control  CANARY-7Q2
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Tokens shorter than this are noise ("the", "id", "e1"). Five keeps identifiers
# like `i_csat` and domain nouns while dropping most filler.
MIN_TOKEN_LEN = 5

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{%d,}" % (MIN_TOKEN_LEN - 1))

BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf", ".gif", ".zip", ".gz", ".webp"}


def tokens_of(text: str) -> set[str]:
    return {m.group(0).lower() for m in TOKEN_RE.finditer(text)}


def read_text(path: Path) -> str | None:
    if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def collect(root: Path, exclude: set[Path]) -> tuple[set[str], list[str], list[str]]:
    """Token set for every readable file under root, excluding given paths."""
    toks: set[str] = set()
    scanned: list[str] = []
    skipped: list[str] = []
    candidates = sorted(root.rglob("*")) if root.is_dir() else [root]
    for p in candidates:
        if not p.is_file() or p.resolve() in exclude:
            continue
        text = read_text(p)
        if text is None:
            skipped.append(str(p))
            continue
        toks |= tokens_of(text)
        scanned.append(str(p))
    return toks, scanned, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scratch", required=True,
                    help="the ONLY file the blind agent was given")
    ap.add_argument("--outside", required=True,
                    help="dir (or file) holding material the agent must NOT have seen")
    ap.add_argument("--output", required=True,
                    help="file containing the agent's returned text")
    ap.add_argument("--control", default=None,
                    help="token planted in the scratch file; must appear in the output")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    scratch_p, outside_p, output_p = Path(args.scratch), Path(args.outside), Path(args.output)

    scratch_text = read_text(scratch_p)
    agent_text = read_text(output_p)
    if scratch_text is None or agent_text is None:
        missing = [str(p) for p, t in ((scratch_p, scratch_text), (output_p, agent_text))
                   if t is None]
        print(json.dumps({"status": "error", "detail": f"unreadable: {missing}"}, indent=2))
        return 4
    if not outside_p.exists():
        print(json.dumps({"status": "error",
                          "detail": f"outside path does not exist: {outside_p}"}, indent=2))
        return 4

    scratch_tokens = tokens_of(scratch_text)
    outside_tokens, scanned, skipped = collect(
        outside_p, exclude={scratch_p.resolve(), output_p.resolve()})

    outside_only = outside_tokens - scratch_tokens
    agent_tokens = tokens_of(agent_text)
    leaked = sorted(outside_only & agent_tokens)

    payload = {
        "scratch": str(scratch_p),
        "outside_files_scanned": len(scanned),
        "outside_files_skipped": len(skipped),
        "scratch_token_count": len(scratch_tokens),
        "outside_only_token_count": len(outside_only),
        "agent_token_count": len(agent_tokens),
        "leaked_tokens": leaked,
        "leaked_count": len(leaked),
        "paths_scanned": scanned,
        "limitation": ("Detects VERBATIM token overlap only. An agent that read the "
                       "outside material and paraphrased it is not caught. A clean "
                       "result means no tokens crossed, never that the agent was blind."),
    }

    # --- refusal 1: nothing distinctive to detect ---
    if not outside_only:
        payload.update({
            "status": "refused",
            "clean": None,
            "reason": ("the outside material shares ALL of its vocabulary with the "
                       "scratch file, so no leak could ever be detected here. An empty "
                       "scope is an error, not a clean result."),
        })
        print(json.dumps(payload, indent=2))
        return 2

    # --- refusal 2: mechanism unproven ---
    # The `control` key is ALWAYS emitted, including when no token was supplied.
    # Rationale (2026-08-19): `--control ""` used to skip this block entirely, so the
    # payload came back status:ok / clean:true with no `control` key at all -- a reader
    # could not distinguish a proven-clean run from an unproven one. A WRONG token
    # correctly refuses, so an EMPTY one silently passing was backwards. The fix is to
    # make the report state its own scope rather than to reject the input: `checked`
    # says whether the proof ran, so a clean result can never be over-read.
    # Additive only -- `status`, `clean`, and every exit code are unchanged.
    if not args.control or not args.control.strip():
        payload["control"] = {
            "token": None,
            "checked": False,
            "proves_agent_read_input": False,
            "note": ("no control token supplied, so blindness is UNPROVEN. A clean result "
                     "below means no leaked token was found, NOT that the mechanism was "
                     "shown able to find one. Plant a token and pass --control to prove it."),
        }
    else:
        control_in_scratch = args.control.lower() in scratch_text.lower()
        control_in_output = args.control.lower() in agent_text.lower()
        payload["control"] = {"token": args.control,
                              "checked": True,
                              "proves_agent_read_input": control_in_scratch and control_in_output,
                              "present_in_scratch": control_in_scratch,
                              "present_in_agent_output": control_in_output}
        if not control_in_scratch:
            payload.update({
                "status": "refused", "clean": None,
                "reason": (f"control token {args.control!r} is not in the scratch file, "
                           "so it cannot prove the agent read its input. Plant it first."),
            })
            print(json.dumps(payload, indent=2))
            return 3
        if not control_in_output:
            payload.update({
                "status": "refused", "clean": None,
                "reason": (f"control token {args.control!r} never appeared in the agent's "
                           "output. Either the agent did not read the scratch file or the "
                           "matching is broken. A no-leak finding is worthless until the "
                           "mechanism is proven able to find something."),
            })
            print(json.dumps(payload, indent=2))
            return 3

    payload["status"] = "ok"
    payload["clean"] = not leaked
    if leaked:
        payload["reason"] = (f"{len(leaked)} token(s) appear in the agent's output that exist "
                             "ONLY outside its scratch file. It read material it was not given.")
    print(json.dumps(payload, indent=2))
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.exit(main())
