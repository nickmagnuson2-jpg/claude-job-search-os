#!/usr/bin/env python3
"""
precommit_pii_guard.py -- refuse a commit whose ADDED lines carry a known real name.

Run by tools/hooks/pre-commit. Uses the same matchers and denylists as
tools/check_public_pii.py, over the lines this commit adds to public files.

WHY THIS EXISTS (2026-10-25). The PII scan before a commit was a step the committer ran,
so the commit went through whenever the scan's verdict did not reach it. Fired 4 times
(memory: feedback_bash_confirm_must_chain_to_operation): on 2026-10-23 and 2026-10-25 a
scan exited 3 and the commit ran anyway, because the command was
`scan | grep ...; git commit`. Here the verdict is the hook's exit code, so it gates the
commit however the command is written.

WHY ADDED LINES ONLY. `--scan` reads whole files, so an ordinary word already on an
untouched line needs a fresh verdict on every commit to that file
(friction log, 2026-10-25). A commit can only create new exposure through the lines it
adds. Text already committed is the push guard's and /audit-pii's job.

What blocks:
  * a BLOCK-tier denylist token, or a target word used as a name, on an added line;
  * a capitalized ambiguous-tier word (a pipeline company that is also an ordinary word)
    used as a name on an added line, that is, not opening a sentence, unless accepted
    for that file: PII_COMMIT_ACCEPT="FILE:TOKEN FILE:TOKEN";
  * a denylist file that exists but is empty, or a staged public file git cannot show.

What only warns (printed; git shows a hook's output, so the committer sees it): the rest
of the ambiguous tier, meaning lowercase entries and words that open a sentence. Replayed
over the last 400 commits on 2026-10-25: blocking the whole tier would have stopped 68 of
them, almost all on two common words; blocking only capitalized names
stops 14, among them two real company names on the ambiguous tier (both since scrubbed).

What passes: gitignored, binary and deleted paths; a checkout with no denylist file at
all (a fork without private data has nothing to match against; the hook says so).

Override with a reason, recorded in the git dir:  PII_COMMIT_OVERRIDE="why" git commit ...

Exit codes: 0 pass, 1 refused.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_public_pii as pii  # noqa: E402

ACCEPT_ENV = "PII_COMMIT_ACCEPT"
OVERRIDE_ENV = "PII_COMMIT_OVERRIDE"
OVERRIDE_LOG = "pii-commit-overrides.jsonl"


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(root), capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace')}")
    return r.stdout.decode("utf-8", errors="surrogateescape")


def staged_paths(root: Path) -> list[str]:
    """Added, copied, modified and renamed paths in the index git handed this hook."""
    out = _git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.split("\0") if p]


def added_text(root: Path, rel: str) -> str:
    """The lines this commit adds to `rel`, read from the index, not the working tree."""
    out = _git(root, "diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--", rel)
    return "\n".join(ln[1:] for ln in out.splitlines()
                     if ln.startswith("+") and not ln.startswith("+++"))


def judge(root: Path, paths: list[str], accepted: set[str]) -> dict:
    """{'blocked', 'ambiguous' (blocks), 'warned' (prints only), 'errors', 'scanned'}"""
    tokens = pii.load_denylist(root)
    ambiguous = pii.load_ambiguous(root)
    target_words = pii.load_target_words(root)
    named = [a for a in ambiguous if a[:1].isupper()]
    result = {"blocked": [], "ambiguous": [], "warned": [], "errors": [], "scanned": 0}
    for rel in paths:
        if not pii.is_public_path(rel, root) or pii.is_gitignored(root, rel) \
                or pii.is_binary(rel):
            continue
        try:
            text = added_text(root, rel)
        except RuntimeError as exc:
            result["errors"].append(f"{rel}: {exc}")
            continue
        result["scanned"] += 1
        hits = pii.find_pii(text, tokens) + pii.find_target_words(text, target_words)
        if hits:
            result["blocked"].append({"file": rel, "tokens": hits})
        as_name = pii.find_target_words(text, named)
        result["ambiguous"] += [f"{rel}:{t}" for t in as_name if f"{rel}:{t}" not in accepted]
        rest = [t for t in pii.find_pii(text, ambiguous) + pii.find_pii(text, target_words)
                if t not in hits and t not in as_name]
        result["warned"] += [f"{rel}:{t}" for t in dict.fromkeys(rest)]
    return result


def record_override(root: Path, reason: str, result: dict) -> Path:
    # git prints this path relative in an ordinary checkout and absolute in a linked
    # worktree; joining an absolute path onto root leaves it unchanged.
    log = Path(root) / _git(root, "rev-parse", "--git-path", OVERRIDE_LOG).strip()
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "reason": reason,
           "blocked": result["blocked"], "ambiguous": result["ambiguous"]}
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return log


def _message(result: dict) -> str:
    lines = ["BLOCKED: this commit adds text the PII gate refuses (precommit_pii_guard.py)."]
    for h in result["blocked"]:
        lines.append(f"  - {h['file']}: real name(s) {', '.join(h['tokens'])}. "
                     "Replace with a placeholder (Acme, Casey Doe) and restage.")
    if result["ambiguous"]:
        lines.append("  Words used as a NAME that are also a pipeline company name, on added lines:")
        lines += [f"    {e}" for e in result["ambiguous"]]
        lines.append("  Open each one. A real reference: replace it. The ordinary word: commit with")
        lines.append(f"    {ACCEPT_ENV}=\"{' '.join(result['ambiguous'])}\" git commit ...")
    for e in result["errors"]:
        lines.append(f"  - could not read the staged change, so it was not checked: {e}")
    lines.append(f"  Override with a reason (recorded): {OVERRIDE_ENV}='<why>' git commit ...")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--repo-root", default=None)
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve() if args.repo_root else pii.repo_root()

    if not (root / pii.DENYLIST_REL).is_file():
        print(f"precommit PII gate: no {pii.DENYLIST_REL} in this checkout, nothing to "
              "match against; run tools/gen_pii_denylist.py to enable it.", file=sys.stderr)
        return 0
    if not pii.load_denylist(root):
        print(f"BLOCKED: {pii.DENYLIST_REL} is empty, so this check would pass without "
              "checking anything. Regenerate it: PYTHONIOENCODING=utf-8 python3 "
              "tools/gen_pii_denylist.py --repo-root .", file=sys.stderr)
        return 1

    accepted = set(os.environ.get(ACCEPT_ENV, "").split())
    try:
        result = judge(root, staged_paths(root), accepted)
    except RuntimeError as exc:
        print(f"BLOCKED: precommit PII gate could not list the staged paths: {exc}",
              file=sys.stderr)
        return 1
    if result["warned"]:
        print("precommit PII gate: ordinary words that are also a pipeline company name, on "
              "added lines. Check each is the ordinary word:\n"
              + "\n".join(f"    {w}" for w in result["warned"]), file=sys.stderr)
    if not (result["blocked"] or result["ambiguous"] or result["errors"]):
        return 0

    reason = os.environ.get(OVERRIDE_ENV, "").strip()
    if reason:
        log = record_override(root, reason, result)
        print(f"precommit PII gate OVERRIDDEN: {reason}\n  recorded in {log}", file=sys.stderr)
        return 0
    print(_message(result), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
