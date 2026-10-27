#!/usr/bin/env python3
"""PreToolUse BLOCK: no build file under `output/<slug>/` until that folder has a BUILD-LOG.

WHY THIS EXISTS. Lessons from past builds were written in five places and none of them reached
the next build: when a later build started, nothing loaded the earlier workflow, a toy demo was
proposed, and three reviewers rejected it before the earlier fast path turned up. The artifact
workflow puts every decision that costs time late into a BUILD-LOG header written at hour 0.
This hook is what makes "at hour 0" true for HTML, code and deck builds: the first build file
is blocked until the log exists. Proposal 1c-W of the 2026-10-23 codify run, approved by Nick
2026-10-26, take-home exercises included.

WHAT IT BLOCKS. A Write, Edit, MultiEdit or NotebookEdit, or a Bash write (redirect, tee,
sed -i, dd, cp/mv/rsync/install, including a copy into a folder, which lands the file under
its own name), whose target is a build file (BUILD_EXT) under
`output/<slug>/`, unless a `BUILD-LOG.md` in the file's own folder or a folder above it, up
to `output/<slug>/`, carries a
filled `reader_ask:` line, AND the company has its dossier at `output/<slug>/<slug>.md`.
A `waived: nick YYYY-MM-DD` line in a BUILD-LOG clears both: it is Nick saying the build is
not for a reader, so neither the ask nor the dossier applies. Creates and edits alike: an
edit to an existing page with no log is the same failure as a new one. The dossier rule is
Nick's, 2026-10-26: every company he builds a demo or tool for gets a dossier.

WHAT IT DOES NOT SEE, stated rather than buried:
  * Markdown and PDF builds.
  * Files a script writes from inside Python (`python build_deck.py` landing a .pptx): the
    target is not in the command text. The script itself is caught unless it sits under a
    `scripts/` or `data/` subfolder, which are exempt by the spec.
  * A relative Bash target after `cd output/x &&` resolves against the repo root, the same
    limitation `check_handoff_producer.py` has, whose target parser this reuses.
  * In a Bash copy, a glob or variable (`cp *.html output/acme/`, `cp $X output/acme/`), a
    source that does not exist yet when the hook runs, and the contents of a source folder
    past the parser's walk cap (`check_handoff_producer.WALK_MAX_DEPTH`, `WALK_MAX_FILES`).
    The parser reads the command text plus what is already on disk, nothing more.
  * It is a tripwire, not proof: Claude can write the log, or the waiver line, itself. It
    stops a build that runs with no log at all; whether the log is any good is a review.

WORK FOLDERS. `output/` also holds folders that are not a company or a person. They are
listed in WORK_FOLDERS (an allow-list of exemptions). A new work folder is blocked until
it is added there: the safe direction, since the other way lets a real build through.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_check import ask_state  # noqa: E402
from check_handoff_producer import all_write_targets, relativize  # noqa: E402
from hook_runtime import read_payload  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

BUILD_EXT = {".html", ".js", ".jsx", ".ts", ".tsx", ".css", ".py", ".ipynb",
             ".xlsx", ".pptx", ".key"}

# Folders under output/ that hold analysis or infrastructure work, not a company or person.
WORK_FOLDERS = {"analysis", "draft-captures", "artifact-gate", "application-workflow",
                "table-audit", "mutation-state", "rendercv_output"}
WORK_PREFIXES = ("codify-",)

# The field that names the target file, per tool.
TARGET_FIELD = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
                "NotebookEdit": "notebook_path"}

def build_slug(rel: str) -> str | None:
    """The `<slug>` whose folder must hold a BUILD-LOG, or None when the path is not gated."""
    parts = [seg for seg in rel.replace("\\", "/").split("/") if seg and seg != "."]
    if len(parts) < 3 or parts[0] != "output":
        return None
    slug, inner, name = parts[1], parts[2:-1], parts[-1]
    if slug in WORK_FOLDERS or slug.startswith(WORK_PREFIXES):
        return None
    ext = os.path.splitext(name)[1].lower()
    if ext not in BUILD_EXT:
        return None
    if "tests" in inner or "sources" in inner:
        return None
    if ext == ".py" and ("scripts" in inner or "data" in inner):
        return None
    return slug


def log_state(target_dir: Path, slug_dir: Path) -> str:
    """`waived`, `filled` or `missing`, from the BUILD-LOG.md files on the path from the
    target file's folder up to `output/<slug>/`.

    Only that path counts: a log in a sibling folder belongs to another build, and taking
    every log under the slug let an earlier build's log clear a new one (codex and grok
    review, 2026-10-26). Each file is read by build_check.ask_state, the one reader of
    `reader_ask` and `waived:`, so this hook and the /standup report cannot disagree."""
    state = "missing"
    chain = []
    for d in [target_dir, *target_dir.parents]:  # finite: parents end at the root
        if d != slug_dir and slug_dir not in d.parents:
            break  # left output/<slug>/
        chain.append(d / "BUILD-LOG.md")
    for log in chain:  # a missing log, or a folder named BUILD-LOG.md, raises OSError: skipped
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        one = ask_state(text)
        if one == "waived":
            return "waived"
        if one == "filled":
            state = "filled"
    return state


BLOCK = """BLOCKED check_build_log_present: a build file under output/{slug}/ before its {missing}.

  {rel}
{reasons}
If this is not a build for a reader (scratch work, a tool for yourself), ask Nick. Only he
writes `waived: nick YYYY-MM-DD (<reason>)` into a BUILD-LOG, which clears both."""

NO_LOG = """
No BUILD-LOG with reader_ask filled. Write output/{slug}/BUILD-LOG.md first, from
framework/templates/build-log.md (rules: framework/artifact-workflow.md), with reader_ask
filled: the reader's own words, quoted, with where they said it (a transcript line, a saved
message, a JD under sources/). That is the line this check reads.
"""

NO_DOSSIER = """
No dossier at output/{slug}/{slug}.md. Every company Nick builds for gets one: run
/research-company first.
"""


def decide(tool_name: str, tool_input: dict, *, root: Path = REPO_ROOT) -> str | None:
    """The BLOCK message for this tool call, or None to allow. Pure given the filesystem."""
    if not isinstance(tool_input, dict):
        return None
    if tool_name == "Bash":
        targets = all_write_targets(tool_input.get("command") or "")
    elif tool_name in TARGET_FIELD:
        targets = [tool_input.get(TARGET_FIELD[tool_name]) or ""]
    else:
        return None
    for target in targets:
        rel = relativize(target, root)
        slug = build_slug(rel)
        if not slug:
            continue
        folder = root / "output" / slug
        state = log_state((root / rel).parent, folder)
        if state == "waived":
            continue
        missing = []
        if state != "filled":
            missing.append(("BUILD-LOG", NO_LOG))
        if not (folder / f"{slug}.md").is_file():
            missing.append(("dossier", NO_DOSSIER))
        if missing:
            return BLOCK.format(slug=slug, rel=rel,
                                missing=" and ".join(name for name, _ in missing),
                                reasons="".join(text.format(slug=slug) for _, text in missing))
    return None


def main() -> None:
    p = read_payload()
    if not p.ok:
        # Fail open on an unreadable payload, but not on a truncated one: truncation means
        # the target could not be seen, which for a BLOCK-tier gate is a silent bypass.
        if p.truncated:
            print("BLOCKED check_build_log_present: the hook payload was truncated, so this "
                  "write could not be checked. Split the write.", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    message = decide(p.tool_name, p.tool_input)
    if message:
        print(message, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
