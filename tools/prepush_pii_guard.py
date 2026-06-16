#!/usr/bin/env python3
"""
prepush_pii_guard.py — public-repo pre-push PII gate.

The job-search OS repo is PUBLIC. This is the last automated line before code
leaves the machine: it runs the deterministic denylist scan over every tracked
public file and BLOCKS the push if any known real contact / target company /
email token is present. It exists because the public repo silently diverged and
shipped real names for weeks (see memory project_phase6_history_clean_done /
the 2026-06-15 reconcile); a tip-clean working tree is not enough — every push
must be gated.

Scope: deterministic only (high precision). The semantic catch-all for NEW names
the denylist does not yet carry is the `/audit-pii` skill — this guard prints a
reminder to run it, but cannot replace it.

Exit 0 = clean (allow push). Exit 1 = denylist hit (block).
Override a confirmed false positive with PUSH_PII_OVERRIDE=1.

Wired via .git/hooks/pre-push (fires only for the public claude-job-search-os
remote). Reuses check_public_pii.py's denylist + matcher (single source of truth).
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_public_pii as cp  # noqa: E402


def tracked_public_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        capture_output=True, text=True,
    ).stdout.splitlines()
    files = []
    for rel in out:
        rel = rel.strip()
        if not rel or not cp.is_public_path(rel):
            continue
        if cp.is_gitignored(root, rel):
            continue
        if (root / rel).is_file():
            files.append(rel)
    return files


def main() -> int:
    root = Path(
        subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True).stdout.strip()
        or "."
    )
    tokens = cp.load_denylist(root)
    if not tokens:
        # No denylist -> nothing to enforce. Fail open (matches the hook's design),
        # but warn loudly: a missing denylist is itself suspicious before a public push.
        print("prepush_pii_guard: no denylist found — skipping deterministic gate "
              "(regenerate with tools/gen_pii_denylist.py).", file=sys.stderr)
        return 0

    hits = {}
    for rel in tracked_public_files(root):
        try:
            content = (root / rel).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        found = cp.find_pii(content, tokens)
        if found:
            hits[rel] = sorted(set(found))

    if not hits:
        print("prepush_pii_guard: deterministic PII scan clean "
              f"({len(tokens)} tokens). Reminder: run /audit-pii for the semantic "
              "pass (new names the denylist does not carry).", file=sys.stderr)
        return 0

    if os.environ.get("PUSH_PII_OVERRIDE") == "1":
        print("prepush_pii_guard: PII hits present but PUSH_PII_OVERRIDE=1 — allowing.",
              file=sys.stderr)
        for rel, toks in hits.items():
            print(f"  (overridden) {rel}: {', '.join(toks)}", file=sys.stderr)
        return 0

    print("\nBLOCKED: push to the PUBLIC repo carries real PII (denylist hits).\n",
          file=sys.stderr)
    for rel, toks in hits.items():
        print(f"  ⛔ {rel}: {', '.join(toks)}", file=sys.stderr)
    print(
        "\nFix: genericize each to a placeholder (Acme / Beacon / Northwind / Jane Doe /"
        " *@example.com), then re-push. Run /audit-pii for the semantic pass too.\n"
        "If a hit is a genuine false positive, override THIS push with:\n"
        "  PUSH_PII_OVERRIDE=1 git push ...\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        # Fail open on guard error (never wedge a push on infrastructure failure),
        # but make the failure visible.
        print(f"prepush_pii_guard error (allowing push): {e}", file=sys.stderr)
        sys.exit(0)
