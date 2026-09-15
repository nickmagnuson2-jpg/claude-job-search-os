#!/usr/bin/env python3
"""PreToolUse BLOCK: no NEW handoff-shaped file under `output/`.

WHY THIS EXISTS. The workstreams registry (`data/workstreams/`, `tools/workstream_state.py`)
fixed handoff sprawl DOWNSTREAM -- discovery and freshness. It did not touch the PRODUCER.
Sessions could still write another `output/analysis/*-handoff.md` with no disposition, no
successor pointer and no retirement of the artifact it replaced, and one did: 21 handoff-shaped
files exist, and `091426-session-handoff-outreach.md` was written the same day the registry
shipped. Codex F6, 2026-09-14.

NOTHING IN THE REPO INSTRUCTS A SESSION TO WRITE ONE. Scope checked 2026-09-15:
`.claude/skills/*/SKILL.md`, `.claude/workflows/`, `docs/`, `framework/`, root `*.md` --
grepped for `handoff`, `next.session`, `kickoff` and `output/analysis`. Any instruction
addressed to a session lives in one of those trees, which is why that scope would have
contained it. The habit is emergent model behaviour, so CLAUDE.md's Trace-to-source rule
("fix the artifact that prescribes the failing pattern") has no target here and the gate is
the whole fix.

WHY CREATION IS CLOSED, AND WHY ANNOTATION IS NOT THE BAR. The first design allowed any write
carrying a valid DISPOSITION banner. Cross-model review killed it (2026-09-15, F1, P0): the
cheapest correction a model reaches for is to ADD THE FIELD, so that gate would have enforced
annotation rather than redirection and the count would have kept climbing with better
metadata. A banner is lifecycle metadata for a record that already exists; it must never be
the capability that authorizes creating the artifact class it describes.

So the rule is about CREATION, not about content:

  * a handoff-shaped path under `output/` that does NOT yet exist  -> BLOCK, always.
  * a handoff-shaped path that DOES exist                          -> allowed, except that
    an edit may not REMOVE or invalidate a banner the file already carries. The pre-edit
    file having a banner is not evidence the post-edit file does, so the PROSPECTIVE
    content is what gets judged (F3).

That shape is what lets the retained `reference` records stay editable, and what lets the
stamping pass introduce banners into the 21 existing files, while a 22nd file cannot be born.
Qualifying cross-session state goes to `data/workstreams/` instead.

BASH COVERAGE, AND WHY THE PII EXTRACTOR ALONE IS NOT ENOUGH. `check_public_pii.extract_write_targets`
documents a DELIBERATE gap for `cp`/`mv`/`rsync`, reasoning that those move bytes absent from
the command string so there is nothing to content-scan. That reasoning is sound for a PII gate
and FALSE here: this gate needs only the destination, so `cp scratch.md
output/analysis/x-handoff.md` would have walked straight through (F2, P0). We reuse the shared
syntax machinery -- heredoc masking, quote stripping -- and add target-only coverage for the
copy/move family rather than adding an 11th copy of it.

SCOPE IS THE INFRASTRUCTURE CLASS ONLY -- `output/analysis/` and flat `output/`. See
`is_handoff_shaped` for why the 13 company-prep files under `output/<company>/` are exempt.

RESIDUAL, STATED RATHER THAN BURIED: a programmatic writer (`python -c`, a script that opens a
path) is not intercepted, and renaming a file to dodge the basename pattern evades this. Both
are deliberate evasions rather than default paths, which is the bar. The enumerating test
`tests/scripts/test_handoff_disposition.py` is the defence-in-depth that catches what lands
anyway; it imports `is_handoff_shaped` from here so the two scopes cannot drift (F4).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_public_pii import (  # noqa: E402
    extract_write_targets,
    mask_heredoc_bodies,
    _unquote,
)
from hook_runtime import read_payload  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

# Basename shapes that mean "this is a session handoff". Substring, deliberately: the corpus
# uses `-handoff`, `-HANDOFF`, `NEXT-SESSION-HANDOFF`, `next-session-plan`, `-kickoff-prompt`
# and `INBOX-DRAIN-KICKOFF`, and a start-anchored pattern would miss most of them.
HANDOFF_NAME = re.compile(r"handoff|next-session|kickoff", re.I)

BANNER_RE = re.compile(r"<!--\s*DISPOSITION:\s*(\{.*?\})\s*-->", re.S)

VALID_STATUS = {"superseded", "absorbed", "reference", "live"}
VALID_KIND = {"work-landed", "replaced-by-doc"}

# `cp`/`mv`/`rsync`/`install` in COMMAND POSITION -- start of line or after a separator, with
# optional leading VAR=val. Never a bare substring: `grep -r "cp " docs/` must stay clean.
COPY_FAMILY = re.compile(
    r"(?:^|[\n;&|(])\s*(?:\w+=\S+\s+)*(?:cp|mv|rsync|install)(?![\w.\-])([^|;&\n]*)")

# A redirection is not an argument, and it must be removed from the SEGMENT rather than
# filtered out token-by-token: `cp src dst 2>&1` splits to a last token of "2>&1", and
# `cp src dst > log` splits ">" and "log" apart so the filtered last token becomes "log".
# Either way `dst` is silently skipped -- which is how the very first live smoke of this hook
# let a real `cp` through while 68 unit tests passed (2026-09-15). Unit tests use the inputs
# you chose; the smoke surfaces the shapes you did not imagine.
REDIRECTION = re.compile(r"\s\d*[<>]+\s*\S*")


def is_handoff_shaped(rel: str) -> bool:
    """True for an INFRASTRUCTURE session handoff: `output/analysis/*.md` or flat `output/*.md`
    whose basename reads as a handoff.

    THE SINGLE SOURCE OF TRUTH for this predicate. The enumerating test imports it rather
    than restating the glob: the hook governing one scope while the test enumerated another
    is exactly how a scope mismatch recreates the counting defect this work exists to fix
    (F4, 2026-09-15).

    WHY COMPANY DIRECTORIES ARE EXEMPT, decided by Nick 2026-09-15. A recursive sweep of
    `output/` finds 35 handoff-shaped files, but 13 of them live under per-company folders
    (`output/<company-slug>/`, and one `probes/` subfolder) -- interview-prep artifacts under
    the settled company-first hierarchy, NOT infrastructure state competing to be a source of
    truth. F6's defect is the latter. Blocking the former
    would fire on live interview prep, and a gate that false-positives on real work is a gate
    that gets disabled. The infrastructure class is exactly two locations, so the predicate
    names them rather than sweeping everything and carving exceptions back out.

    `tests/` is excluded -- a content hook that blocks its own fixtures makes the suite
    unrunnable (`check_prep_doc_format.py` precedent, HOOK_AUTHORING.md).
    """
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if not rel.endswith(".md"):
        return False
    parts = [seg for seg in rel.split("/") if seg]
    if not parts or parts[0] != "output" or "tests" in parts:
        return False
    is_flat = len(parts) == 2
    is_analysis = len(parts) == 3 and parts[1] == "analysis"
    if not (is_flat or is_analysis):
        return False
    return bool(HANDOFF_NAME.search(parts[-1]))


def relativize(path: str, root: Path = REPO_ROOT) -> str:
    """Repo-relative POSIX path, or the input unchanged when it lies outside the repo."""
    if not path:
        return ""
    try:
        p = Path(path)
        p = p if p.is_absolute() else root / p
        return p.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.replace("\\", "/")


def parse_banner(text: str) -> dict | None:
    """The DISPOSITION banner as a dict, or None when absent or unparseable."""
    m = BANNER_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def banner_errors(banner: dict | None) -> list[str]:
    """Which schema conditions a banner fails. Pure, so the schema itself is testable."""
    if banner is None:
        return ["no parseable DISPOSITION banner"]
    out = []
    status = banner.get("status")
    if status not in VALID_STATUS:
        out.append(f"status must be one of {sorted(VALID_STATUS)}, got {status!r}")
    if status == "superseded" and banner.get("kind") not in VALID_KIND:
        out.append(f"status 'superseded' requires kind in {sorted(VALID_KIND)}")
    if status != "reference" and not banner.get("successor"):
        out.append("successor is required unless status is 'reference'")
    return out


def apply_edits(text: str, tool_input: dict) -> str:
    """The PROSPECTIVE file content after an Edit/MultiEdit.

    Judging the PRE-edit file is what let an edit delete a banner while the gate allowed it
    (F3): an edit whose old_string is the banner and whose new_string is empty leaves an
    invalid file, and the enumerating test would only notice later.
    """
    edits = tool_input.get("edits")
    if not isinstance(edits, list):
        edits = [tool_input]
    for e in edits:
        if not isinstance(e, dict):
            continue
        old = e.get("old_string")
        new = e.get("new_string") or ""
        if not isinstance(old, str) or not old or not isinstance(new, str):
            continue
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def copy_destinations(command: str) -> list[str]:
    """Destinations of `cp`/`mv`/`rsync`/`install`, which the PII extractor omits BY DESIGN.

    Target-only: we never need the SOURCE bytes, only where they land. That is exactly why
    the PII gate's documented reason for omitting this family does not transfer here.
    """
    out = []
    for m in COPY_FAMILY.finditer(mask_heredoc_bodies(command)):
        segment = REDIRECTION.sub("", m.group(1))
        toks = [t for t in segment.split() if not t.startswith("-")]
        if len(toks) >= 2:  # a source AND a destination; a lone token is not a copy
            raw = toks[-1]
            # Test the RAW token for shell expansion ANYWHERE in it, not just at the start,
            # and before unquoting. `cp a.md `echo x`` splits to a final token of "x`" --
            # the backtick trails, `_unquote` does not remove it, and the result is the
            # plausible-looking path "x`". Command substitution and variables are
            # unresolvable at hook time, so a destination containing either is not a target.
            if "$" in raw or "`" in raw:
                continue
            dest = _unquote(raw)
            if dest:
                out.append(dest)
    return out


def all_write_targets(command: str) -> list[str]:
    """Every path this command may land a file at: redirect/tee/dd/sed -i, plus copy/move."""
    return list(extract_write_targets(command)) + copy_destinations(command)


ROUTE = """BLOCKED: this creates a NEW handoff-shaped file under output/.

  {rel}

Handoff docs in output/ are the sprawl the workstreams registry exists to replace: 22 of them
accumulated, each with an invented format and no disposition, and a future session cannot tell
which one is authoritative.

Route the state instead:
  * A workstream exists when it has A DECISION OPEN ACROSS SESSIONS **and** STATE A FUTURE
    SESSION WOULD OTHERWISE RE-DERIVE. If both hold, add or update a file in
    `data/workstreams/` -- derivable numbers go in a PROBE declaration, never typed.
  * If only one holds, it is a todo: `tools/todo_write.py`.
  * If it is a one-off analysis rather than session state, name it for its subject and leave
    'handoff' / 'kickoff' / 'next-session' out of the filename.

Editing an EXISTING handoff is allowed -- this gate is on creation. A genuinely new retained
record is Nick's call, not a default.

See CLAUDE.md Output Conventions."""

STRIP = """BLOCKED: this edit removes or invalidates the DISPOSITION banner on

  {rel}

  {why}

The banner is what makes a retired handoff non-authoritative. Fix the disposition rather than
deleting it."""


def decide(tool_name: str, tool_input: dict, *, root: Path = REPO_ROOT) -> str | None:
    """The BLOCK message for this tool call, or None to allow. Pure given the filesystem."""
    if not isinstance(tool_input, dict):
        return None

    if tool_name == "Bash":
        for target in all_write_targets(tool_input.get("command") or ""):
            rel = relativize(target, root)
            if is_handoff_shaped(rel) and not (root / rel).exists():
                return ROUTE.format(rel=rel)
        return None

    if tool_name not in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        return None

    rel = relativize(tool_input.get("file_path") or "", root)
    if not is_handoff_shaped(rel):
        return None

    path = root / rel
    if not path.exists():
        return ROUTE.format(rel=rel)

    try:
        before = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None  # cannot read it; never block on our own inability to judge

    if not parse_banner(before):
        return None  # unstamped today -- the stamping pass has to be able to land

    after = ((tool_input.get("content") or "") if tool_name == "Write"
             else apply_edits(before, tool_input))
    errs = banner_errors(parse_banner(after))
    return STRIP.format(rel=rel, why="; ".join(errs)) if errs else None


def main() -> None:
    p = read_payload()
    if not p.ok:
        # FAIL OPEN on an unreadable payload -- but NOT on a truncated one. Truncation means
        # the guard could not SEE what it exists to inspect, and for a BLOCK-tier gate that is
        # a silent bypass: send a payload over the ceiling and the check passes without ever
        # reading it (hook_runtime F1, 2026-09-14).
        if p.truncated:
            print("BLOCKED: the hook payload was truncated, so this write could not be "
                  "checked against the handoff-producer gate. Split the write.",
                  file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    message = decide(p.tool_name, p.tool_input)
    if message:
        print(message, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
