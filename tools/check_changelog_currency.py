#!/usr/bin/env python3
"""check_changelog_currency.py — Stop hook for Claude Code.

Warns (never blocks) when commits since the last `docs/CHANGELOG.md` edit have
touched system surfaces (tools/, .claude/skills/, framework/, settings, deps) but
the changelog was not updated. The changelog is the canonical "what was built /
fixed / learned" record; keeping it current is a manual end-of-build step that
gets skipped, so it silently drifts behind git log.

Structural promotion of [[feedback_changelog_drifts_without_structural_update]]:
N=1 origin 2026-06-04 (3-day / 22-commit gap), N=2 fire 2026-06-05 (two build
commits shipped with no changelog update; Nick had to ask twice). REOPEN gate
tripped -> this hook.

Detection: commits in `<last-CHANGELOG-commit>..HEAD` that touch any worthy
pathspec. If that set is non-empty, the changelog is behind. A commit that
touches BOTH code and docs/CHANGELOG.md becomes the new boundary, so doing it
right (code + changelog in one commit, or a follow-up changelog commit) clears
the warning on the next turn.

Noise control: a cursor (`tools/.changelog-currency-cursor.json`) records the
last HEAD evaluated, so the hook warns at most once per HEAD — not every turn.
A new build commit (HEAD moves) re-evaluates and re-warns if still behind.

"Changelog-worthy" is a judgment call, so this WARNs/reminds (stderr, exit 0) and
never auto-writes or blocks — entry prose still needs a human/Claude. Per
[[feedback_warn_vs_block_hook_design]].

CORRECTED 2026-08-25. This docstring previously claimed "PreToolUse WARN is invisible, so
this lives on Stop where stderr surfaces." That is FALSE and this hook is the proof: it
became the 4th fire of that rule, recorded as "a STOP hook, silent across 6 pushes." On
ANY hook surface, exit 0 + stderr reaches nobody -- Stop is not an exception. The claim is
corrected here because a prescription surface is read at authoring time and outranks every
corrected doc downstream; leaving it would keep generating new silent Stop hooks.
This hook remains warn-only pending Nick's exit-0-to-exit-2 decision, declared in
tools/hook-warn-allow.json rather than left implicit. See tools/check_hook_warn_tier.py.

Exit codes: always 0 (WARN tier, fail-open on any git/parse error).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CURSOR_FILE = REPO_ROOT / "tools" / ".changelog-currency-cursor.json"
CHANGELOG_REL = "docs/CHANGELOG.md"

# Commits touching any of these paths are "changelog-worthy" system surfaces.
# Directory globs (not bare `*.py`) so test-only or one-off-script churn doesn't
# nag; tool/skill/framework changes are the real changelog surface.
WORTHY_PATHSPECS = [
    "tools/",
    ".claude/skills/",
    ".claude/settings.json",
    "framework/",
    "requirements.txt",
]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=10,
    )


def last_changelog_commit(repo: Path) -> str:
    """SHA of the most recent commit that modified docs/CHANGELOG.md, or ''."""
    r = _git(repo, "log", "-1", "--format=%H", "--", CHANGELOG_REL)
    return r.stdout.strip() if r.returncode == 0 else ""


def unlogged_worthy_commits(repo: Path) -> list:
    """(short-sha, subject) for commits touching a worthy pathspec since the last
    docs/CHANGELOG.md edit. Empty when the changelog is current."""
    cl = last_changelog_commit(repo)
    rng = f"{cl}..HEAD" if cl else "HEAD"
    r = _git(repo, "log", rng, "--format=%h%x09%s", "--", *WORTHY_PATHSPECS)
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, subj = line.partition("\t")
        out.append((sha, subj))
    return out


def _load_cursor() -> dict:
    try:
        return json.loads(CURSOR_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cursor(state: dict) -> None:
    try:
        tmp = str(CURSOR_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, CURSOR_FILE)
    except OSError:
        pass


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        data = {}
    if data.get("stop_hook_active") is True:
        return 0

    try:
        if not (REPO_ROOT / CHANGELOG_REL).exists():
            return 0
        head = _git(REPO_ROOT, "rev-parse", "HEAD").stdout.strip()
        if not head:
            return 0
        if _load_cursor().get("last_warned_head") == head:
            return 0  # already evaluated this HEAD; don't re-warn every turn

        unlogged = unlogged_worthy_commits(REPO_ROOT)
        _save_cursor({"last_warned_head": head})

        if unlogged:
            listing = "\n".join(f"    {sha}  {subj}" for sha, subj in unlogged[:15])
            more = "" if len(unlogged) <= 15 else f"\n    ... and {len(unlogged) - 15} more"
            sys.stderr.write(
                f"⚠ changelog-currency: {len(unlogged)} commit(s) touched system "
                f"surfaces (tools/ .claude/skills/ framework/ settings deps) since the "
                f"last {CHANGELOG_REL} edit:\n{listing}{more}\n"
                f"Add a dated entry to {CHANGELOG_REL} "
                f"(per feedback_changelog_drifts_without_structural_update).\n"
            )
    except Exception:
        return 0  # fail-open: a reminder hook must never break the turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
