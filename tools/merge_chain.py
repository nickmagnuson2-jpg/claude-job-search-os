#!/usr/bin/env python3
"""merge_chain.py -- the one rule for a push of merge commits that adds nothing of its own.

WHY (Nick, 2026-11-18: "No PRs"; build brief output/overnight-merge-back/
111826-merge-push-build-brief.md). Ready branches land as a chain of merge commits pushed
straight to main: M1 = merge(main, b1), M2 = merge(M1, b2), ... Each branch was reviewed,
audited and suite-checked at its own tip, so a merge may be trusted only when it is exactly
git's own merge of its two parents and nothing was added inside it. This module states that
rule once. tools/cross_model_gate.py, tools/prepush_pii_guard.py and tools/land_branch.py
import it, so the builder and both gates cannot disagree about what a valid merge is (CLAUDE.md
Hard Rule 1, fourth tier: an import cannot drift).

THE RULE, for the first-parent walk base..tip:
  - at least one commit; every commit has exactly 2 parents;
  - the first parent of the oldest is `base`, and each later first parent is the commit
    before it, so the chain starts on the remote main and never leaves it;
  - each commit's tree equals expected_tree(p1, p2).

expected_tree(p1, p2) is `git merge-tree --write-tree p1 p2` when that merge is clean. The
ONE exception (Nick's ruling, 2026-11-18): when the only conflicted path is
tools/mutation-allow.json, that file is replaced by allowlist_merge.merge() of its three
versions written in allowlist_write.serialize() form, and every other path stays git's
merge. Any other conflict has no expected tree.

LIMIT: a file both sides changed in different hunks merges cleanly into content neither
review saw whole. That is the same gap a GitHub merge button has; the full suite on the
merged tree (land_branch --merge-push) is what covers it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import allowlist_merge
import allowlist_write

ALLOWLIST = "tools/mutation-allow.json"
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


class ChainError(ValueError):
    """The range is not a verified merge chain, or a merge cannot be built. The message
    says which commit and why."""


@dataclass(frozen=True)
class Merge:
    sha: str
    p1: str
    p2: str
    tree: str
    resolved_allowlist: bool


def _git(root, *args: str, stdin: str | None = None, env: dict | None = None):
    return subprocess.run(["git", "-C", str(root), *args], input=stdin, text=True,
                          capture_output=True, env=env)


def _commit(root, sha: str) -> str | None:
    r = _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _show(root, rev: str, path: str) -> str | None:
    r = _git(root, "show", f"{rev}:{path}")
    return r.stdout if r.returncode == 0 else None


def _merge_tree(root, p1: str, p2: str) -> tuple[str | None, list[str], str]:
    """(tree, conflicted paths, error). tree is None only when git printed none."""
    r = _git(root, "merge-tree", "--write-tree", "--name-only", "--no-messages", p1, p2)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    # rc 1 is a conflict ONLY when a tree id was printed: git also exits 1 on a bad ref.
    if r.returncode not in (0, 1) or not lines or not _SHA.match(lines[0]):
        return None, [], (r.stderr.strip()[:300] or f"merge-tree rc {r.returncode}")
    conflicted = sorted(set(lines[1:])) if r.returncode == 1 else []
    return lines[0], conflicted, ""


def _resolve_allowlist(root, p1: str, p2: str, conflict_tree: str) -> tuple[str | None, str]:
    """The conflict tree with mutation-allow.json replaced by the key-wise merge."""
    bases = _git(root, "merge-base", "--all", p1, p2)
    # rc 1 with no output is git's answer for unrelated histories (no merge base); any
    # other failure is an error, never "no base".
    if bases.returncode not in (0, 1):
        return None, f"{ALLOWLIST}: merge-base failed: {bases.stderr.strip()[:200]}"
    base_shas = bases.stdout.split()
    if len(base_shas) > 1:
        return None, f"{ALLOWLIST}: {len(base_shas)} merge bases; refusing to guess one"
    ours, theirs = _show(root, p1, ALLOWLIST), _show(root, p2, ALLOWLIST)
    if ours is None or theirs is None:
        return None, f"{ALLOWLIST}: missing on one side (a delete conflict is not resolved)"
    base_text = (_show(root, base_shas[0], ALLOWLIST) or "") if base_shas else ""
    try:
        def load(text: str, empty_ok: bool) -> dict:
            if not text.strip():
                if empty_ok:
                    return {}
                raise ValueError("empty side")
            return json.loads(text, object_pairs_hook=allowlist_merge._no_duplicates)
        merged = allowlist_merge.merge(load(base_text, True), load(ours, False),
                                       load(theirs, False))
    except allowlist_merge.AllowlistConflict as e:
        return None, f"{ALLOWLIST}: {e}"
    except ValueError as e:
        return None, f"{ALLOWLIST}: unreadable side: {e}"
    blob = _git(root, "hash-object", "-w", "--stdin", stdin=allowlist_write.serialize(merged))
    if blob.returncode != 0:
        return None, f"{ALLOWLIST}: hash-object failed: {blob.stderr.strip()[:200]}"
    listed = _git(root, "ls-tree", p1, "--", ALLOWLIST)
    if listed.returncode != 0 or not listed.stdout.split():
        return None, f"{ALLOWLIST}: ls-tree failed: {listed.stderr.strip()[:200]}"
    mode = listed.stdout.split()[0]
    with tempfile.TemporaryDirectory(prefix="merge-chain-index.") as tmp:
        # A path that does not exist yet: read-tree creates the index there.
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        steps = (("read-tree", conflict_tree),
                 ("update-index", "--cacheinfo", f"{mode},{blob.stdout.strip()},{ALLOWLIST}"))
        for step in steps:
            r = _git(root, *step, env=env)
            if r.returncode != 0:
                return None, f"{ALLOWLIST}: {step[0]} failed: {r.stderr.strip()[:200]}"
        r = _git(root, "write-tree", env=env)
        if r.returncode != 0 or not _SHA.match(r.stdout.strip()):
            return None, f"{ALLOWLIST}: write-tree failed: {r.stderr.strip()[:200]}"
        return r.stdout.strip(), "allowlist resolved"


def expected_tree(root, p1: str, p2: str) -> tuple[str | None, str]:
    """(tree, why). tree is the only tree a merge of p1 and p2 may have; None when there
    is none, and `why` then names the conflicting paths or the error."""
    tree, conflicted, err = _merge_tree(root, p1, p2)
    if tree is None:
        return None, err
    if not conflicted:
        return tree, "clean"
    if conflicted != [ALLOWLIST]:
        return None, "conflicts in " + ", ".join(conflicted)
    return _resolve_allowlist(root, p1, p2, tree)


def build(root, p1: str, p2: str, message: str) -> str:
    """An unreferenced merge commit of p1 and p2 with the expected tree. Raises ChainError."""
    tree, why = expected_tree(root, p1, p2)
    if tree is None:
        raise ChainError(f"cannot merge {p2[:8]} onto {p1[:8]}: {why}")
    r = _git(root, "commit-tree", tree, "-p", p1, "-p", p2, "-F", "-", stdin=message)
    if r.returncode != 0 or not _SHA.match(r.stdout.strip()):
        raise ChainError(f"commit-tree failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def walk(root, base: str, tip: str) -> list[Merge]:
    """Every first-parent commit in base..tip, oldest first, each verified. Raises
    ChainError on the first commit that breaks the rule."""
    b, t = _commit(root, base), _commit(root, tip)
    if b is None or t is None:
        raise ChainError(f"not a commit here: {base if b is None else tip}")
    r = _git(root, "rev-list", "--first-parent", "--parents", "--reverse", f"{b}..{t}")
    if r.returncode != 0:
        raise ChainError(f"rev-list failed: {r.stderr.strip()[:200]}")
    rows = [ln.split() for ln in r.stdout.splitlines() if ln.strip()]
    if not rows:
        raise ChainError(f"no commits in {b[:8]}..{t[:8]}")
    out: list[Merge] = []
    prev = b
    for sha, *parents in rows:
        if len(parents) != 2:
            raise ChainError(f"{sha[:8]} has {len(parents)} parent(s); a merge chain needs "
                             "2 parents on every commit")
        p1, p2 = parents
        if p1 != prev:
            raise ChainError(f"{sha[:8]}'s first parent is {p1[:8]}, not {prev[:8]}: the "
                             "chain does not start on the pushed base")
        want, why = expected_tree(root, p1, p2)
        tree = _git(root, "rev-parse", f"{sha}^{{tree}}").stdout.strip()
        if want is None or tree != want:
            raise ChainError(f"{sha[:8]}'s tree {tree[:8]} is not git's clean merge of "
                             f"{p1[:8]} and {p2[:8]} ({why})")
        out.append(Merge(sha, p1, p2, tree, why == "allowlist resolved"))
        prev = sha
    # The last row is always t: rev-list starts from t, and t is not under base (rows is
    # not empty), so a check that the walk ended at t could never fail.
    return out


def parse_numstat(text: str) -> list[tuple[str, int, int]]:
    """`git diff --numstat` lines to (path, added, removed); binary counts as 0."""
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, r, path = parts
        out.append((path, int(a) if a.isdigit() else 0, int(r) if r.isdigit() else 0))
    return out


def numstat(root, a: str, b: str) -> list[tuple[str, int, int]]:
    """Changed paths a..b. Renames are two paths (old and new), so neither escapes a
    per-path rule. Raises ChainError when git fails: an unreadable diff is never empty."""
    r = _git(root, "-c", "core.quotepath=off", "diff", "--numstat", "--no-renames", a, b)
    if r.returncode != 0:
        raise ChainError(f"git diff {a[:8]} {b[:8]} failed: {r.stderr.strip()[:200]}")
    return parse_numstat(r.stdout)
