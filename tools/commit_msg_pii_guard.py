#!/usr/bin/env python3
"""commit_msg_pii_guard.py -- refuse a commit whose MESSAGE names a known real entity.

WHY (2026-10-25). Commit messages are published with every push, and no gate read them
at commit time. Two commit messages that night quoted denylist-tier words, and only a
hand-run sweep before the push caught them. Fixing it then meant rewriting unpushed
history. The pre-push guard scans the files a push carries, not the messages.

Run by tools/hooks/commit-msg, which git calls with the path of the message file.
Comment lines (core.commentChar, '#' by default) and everything below the scissors line
are dropped first: git removes them from the final message, and they list staged paths.

What blocks: a BLOCK-tier denylist token in the message; a denylist file that exists but
is empty; a message file that cannot be read.
What only warns: the ambiguous and target-word tiers, as in precommit_pii_guard.py.
What passes: a checkout with no denylist file at all (nothing to match against).

Override with a reason, recorded in the git dir:  PII_COMMIT_OVERRIDE="why" git commit ...
(the same variable and log as the pre-commit gate: one override covers one commit).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_public_pii as pii  # noqa: E402
import precommit_pii_guard as pre  # noqa: E402

SCISSORS = "------------------------ >8 ------------------------"


def comment_char(root: Path) -> str:
    """git's core.commentChar, or '#'. 'auto' picks per message, so assume the default."""
    r = subprocess.run(["git", "config", "--get", "core.commentChar"], cwd=str(root),
                       capture_output=True, text=True)
    value = r.stdout.strip()
    return value if value and value != "auto" else "#"


def message_text(raw: str, cc: str = "#") -> str:
    """The part of the message git keeps: no comment lines, nothing below the scissors."""
    kept = []
    for line in raw.splitlines():
        if line.startswith(cc) and SCISSORS in line:
            break
        if not line.startswith(cc):
            kept.append(line)
    return "\n".join(kept)


def judge(root: Path, text: str) -> dict:
    """{'blocked': [tokens], 'warned': [tokens]}"""
    blocked = pii.find_pii(text, pii.load_denylist(root))
    rest = [t for t in pii.find_pii(text, pii.load_ambiguous(root))
            + pii.find_pii(text, pii.load_target_words(root)) if t not in blocked]
    return {"blocked": blocked, "warned": list(dict.fromkeys(rest))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("message_file")
    ap.add_argument("--repo-root", default=None)
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve() if args.repo_root else pii.repo_root()

    if not (root / pii.DENYLIST_REL).is_file():
        print(f"commit-msg PII gate: no {pii.DENYLIST_REL} in this checkout, nothing to "
              "match against; run tools/gen_pii_denylist.py to enable it.", file=sys.stderr)
        return 0
    if not pii.load_denylist(root):
        print(f"BLOCKED: {pii.DENYLIST_REL} is empty, so this check would pass without "
              "checking anything. Regenerate it: PYTHONIOENCODING=utf-8 python3 "
              "tools/gen_pii_denylist.py --repo-root .", file=sys.stderr)
        return 1
    try:
        raw = Path(args.message_file).read_text(encoding="utf-8", errors="surrogateescape")
    except OSError as exc:
        print(f"BLOCKED: commit-msg PII gate could not read the message: {exc}", file=sys.stderr)
        return 1

    result = judge(root, message_text(raw, comment_char(root)))
    if result["warned"]:
        print("commit-msg PII gate: ordinary words that are also a pipeline company name, in "
              "the message. Check each is the ordinary word: "
              + ", ".join(result["warned"]), file=sys.stderr)
    if not result["blocked"]:
        return 0

    reason = os.environ.get(pre.OVERRIDE_ENV, "").strip()
    if reason:
        log = pre.record_override(root, reason, {"blocked": [
            {"file": "<commit message>", "tokens": result["blocked"]}]})
        print(f"commit-msg PII gate OVERRIDDEN: {reason}\n  recorded in {log}", file=sys.stderr)
        return 0
    print("BLOCKED: the commit message names real entities the PII gate refuses "
          f"(commit_msg_pii_guard.py): {', '.join(result['blocked'])}.\n"
          "  Commit messages are public. Describe the case by kind instead, or override "
          f"with a reason (recorded): {pre.OVERRIDE_ENV}='<why>' git commit ...",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
