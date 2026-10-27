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
are deliberate evasions rather than default paths, which is the bar. `copy_destinations` also
works from the command text plus what already exists on disk, so it cannot see: a glob or a
variable (`cp *.md out/`, `cp $X out/`), a source that does not exist yet when the hook runs
(made earlier in the same command), and the contents of a source folder past the walk cap
(WALK_MAX_DEPTH, WALK_MAX_FILES), where it falls back to the folder's own name. The enumerating test
`tests/scripts/test_handoff_disposition.py` is the defence-in-depth that catches what lands
anyway; it imports `is_handoff_shaped` from here so the two scopes cannot drift (F4).
"""
from __future__ import annotations

import json
import os
import re
import shlex
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
    r"(?:^|[\n;&|(])\s*(?:\w+=\S+\s+)*(cp|mv|rsync|install)(?![\w.\-])([^|;&\n]*)")

# Options whose NEXT word is their argument, per command, so that word is neither a source
# nor the destination: `rsync --exclude page.html src/ dst/` must not read page.html as a
# file (codex round 8). Per command because a letter means different things: rsync `-o`
# and `-g` take no argument, install `-o` and `-g` do. The `--opt=value` form needs no entry,
# since any word starting with `-` is already dropped.
#
# THE SAFE DIRECTION. Skipping a word that is really a source hides a landed file (a silent
# allow); not skipping an option's argument only adds a path to judge (at worst a block that
# names the wrong file). So a flag goes in this table only when it takes an argument in every
# variant of the command this repo can meet, GNU and the macOS BSD tools alike. Checked
# 2026-10-26: BSD `install -S` takes no argument (GNU's takes a suffix), so `-S` is not listed
# for install; BSD `install -B` takes a suffix and GNU has no `-B`, so it is.
OPTION_ARGS = {
    "cp": {"-S", "--suffix"},
    "mv": {"-S", "--suffix"},
    "install": {"-m", "--mode", "-o", "--owner", "-g", "--group", "-B", "--suffix"},
    "rsync": {"--exclude", "--include", "--filter", "-f", "--exclude-from", "--include-from",
              "--files-from", "-e", "--rsh", "--backup-dir", "--suffix", "-T", "--temp-dir",
              "--partial-dir", "--compare-dest", "--copy-dest", "--link-dest", "--chmod",
              "--log-file", "--out-format", "-B", "--block-size", "-M", "--remote-option",
              "--password-file"},
}

# How far `copy_destinations` walks a source FOLDER to list the files it lands. Past either
# cap the walk is abandoned and only the folder's own name is reported, as before.
WALK_MAX_DEPTH = 6
WALK_MAX_FILES = 500

# Commands where `-t DIR` / `--target-directory` names the destination folder. Not rsync,
# whose `-t` means --times and takes no argument (grok round 9).
TARGET_DIR_COMMANDS = {"cp", "mv", "install"}

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


def _shell_words(segment: str) -> list[str]:
    """Shell words with quotes resolved, so `"/tmp/my page.html"` is one word (codex round 8).

    Falls back to whitespace splitting when the quoting does not balance, which happens when
    a segment is cut at a `|` or `;` inside a quoted string.
    """
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _files_under(folder: str) -> list[str] | None:
    """Paths of the files under `folder`, relative to it, or None when a cap is passed."""
    base = folder.rstrip("/")
    out: list[str] = []
    for root, dirs, files in os.walk(base):
        rel_root = os.path.relpath(root, base)
        depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        if depth > WALK_MAX_DEPTH:
            return None
        for f in files:
            out.append(f if rel_root == "." else f"{rel_root}/{f}")
            if len(out) > WALK_MAX_FILES:
                return None
    return out


def _expandable(tok: str) -> bool:
    """A token with a variable or command substitution anywhere in it is unresolvable."""
    return "$" in tok or "`" in tok


def copy_destinations(command: str) -> list[str]:
    """Where `cp`/`mv`/`rsync`/`install` land files; the PII extractor omits these BY DESIGN.

    Target-only: we never need the SOURCE bytes, only where they land. That is exactly why
    the PII gate's documented reason for omitting this family does not transfer here.

    A DIRECTORY destination lands each source under its own basename, so those landed paths
    are returned too, alongside the destination itself. Without them `cp /tmp/page.html
    output/acme/` reported only `output/acme/` and a gate judging the landed file saw no
    file (codex and grok F1, 2026-10-26). The destination is read as a directory when it
    ends in `/`, has no extension in its last segment, follows `-t`/`--target-directory`,
    or receives more than one source, or is an existing directory. The text tests come
    first; the directory check exists for a dotted folder named without a trailing slash
    (`cp page.html output/acme/v1.0`, codex round 9). It is resolved against the hook's
    working directory and can only ADD landed paths, so a wrong working directory falls
    back to the text-only answer and never drops a path.
    """
    out = []
    for m in COPY_FAMILY.finditer(mask_heredoc_bodies(command)):
        words = _shell_words(REDIRECTION.sub("", m.group(2)))
        command = m.group(1)
        takes_arg = OPTION_ARGS[command]
        has_target_dir = m.group(1) in TARGET_DIR_COMMANDS
        target_dir = None
        toks = []
        skip = False
        for i, w in enumerate(words):
            if skip:
                skip = False
            elif has_target_dir and w in ("-t", "--target-directory") and i + 1 < len(words):
                target_dir, skip = words[i + 1], True
            elif has_target_dir and w.startswith("--target-directory="):
                target_dir = w.split("=", 1)[1]
            elif w in takes_arg:
                skip = True
            elif not w.startswith("-"):
                toks.append(w)
        if target_dir is not None:
            raw, sources = target_dir, toks
        elif len(toks) >= 2:  # a source AND a destination; a lone token is not a copy
            raw, sources = toks[-1], toks[:-1]
        else:
            continue
        # Test the RAW token for shell expansion ANYWHERE in it, not just at the start,
        # and before unquoting. `cp a.md `echo x`` splits to a final token of "x`" --
        # the backtick trails, `_unquote` does not remove it, and the result is the
        # plausible-looking path "x`". Command substitution and variables are
        # unresolvable at hook time, so a destination containing either is not a target.
        if _expandable(raw):
            continue
        dest = _unquote(raw)
        if not dest:
            continue
        out.append(dest)
        srcs = [_unquote(s) for s in sources if not _expandable(s)]
        folder_srcs = [s for s in srcs if os.path.isdir(s)]
        last = dest.rstrip("/").rsplit("/", 1)[-1]
        if not (target_dir is not None or dest.endswith("/") or "." not in last
                or len(sources) > 1 or os.path.isdir(dest) or folder_srcs):
            continue
        base = dest.rstrip("/")
        for src in srcs:
            name = src.rstrip("/").rsplit("/", 1)[-1]
            if not name:
                continue
            out.append(base + "/" + name)
            # A source FOLDER lands its files (codex round 10). Where depends on the command:
            #   rsync `src/` copies the contents into dest; rsync `src` copies the folder.
            #   cp/mv/install `src` copies the folder into dest when dest already exists,
            #   and becomes dest when it does not.
            #   cp `src/` differs between BSD (contents) and GNU (the folder), so both;
            #   mv and install with `src/` move the folder itself, as without the slash.
            # Reporting a path the files cannot land on blocks correct work (grok round 11,
            # 663.F1); missing one they do land on is a silent allow, so the one ambiguous
            # case keeps both. A source that is a file, or does not exist, walks to nothing.
            slash = src.endswith("/")
            if command == "rsync":
                under_name, into_dest = not slash, slash
            elif slash and command == "cp":
                under_name = into_dest = True
            else:
                under_name = os.path.isdir(dest)
                into_dest = not under_name
            for rel in _files_under(src) or []:
                if under_name:
                    out.append(f"{base}/{name}/{rel}")
                if into_dest:
                    out.append(f"{base}/{rel}")
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
