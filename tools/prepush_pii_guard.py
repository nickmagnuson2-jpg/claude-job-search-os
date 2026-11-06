#!/usr/bin/env python3
"""
prepush_pii_guard.py — public-repo pre-push PII gate.

The job-search OS repo is PUBLIC. This is the last automated line before code
leaves the machine: it runs the deterministic denylist scan over the committed
blobs at the pushed sha(s) (read from the pre-push hook's stdin) and BLOCKS the
push if any known real contact / target company / email token is present. It
scans what is actually being pushed — every blob in every pushed commit that the
remote does not already have, not the on-disk working tree and not only each ref's
tip — so a commit that carries PII in history is caught even when a later commit
removed it.
When invoked manually (no pre-push stdin) it falls back to a working-tree scan. It exists because the public repo silently diverged and
shipped real names for weeks (see memory project_phase6_history_clean_done /
the 2026-06-15 reconcile); a tip-clean working tree is not enough — every push
must be gated.

Two checks, in order:
  1. Deterministic denylist scan (high precision). Override a confirmed false
     positive with PUSH_PII_OVERRIDE=1. A missing denylist skips only this scan.
  2. The /audit-pii record (Nick, 2026-11-05). Every pushed tip's TREE must have a
     clean record from tools/audit_pii_record.py, written by /audit-pii on a clean
     semantic pass, which catches NEW names the denylist does not carry. Waive one
     push with AUDIT_PII_WAIVE='<reason>' (appended to the audit ledger). An
     unreadable ledger blocks. PUSH_PII_OVERRIDE does not clear this check.
     Limit: Claude writes the record, so it proves the pass ran, not that it was good.
A manual run (no pre-push stdin) scans the working tree and REPORTS whether HEAD's
tree has a record, without blocking on it. Deletion-only pushes pass both checks.

Exit 0 = allow. Exit 1 = a denylist hit, a missing record, or the scan could not
run (block; a guard error fails CLOSED since 2026-10-13).

Wired via .git/hooks/pre-push (fires only for the public claude-job-search-os
remote). Reuses check_public_pii.py's denylist + matcher (single source of truth).
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_public_pii as cp  # noqa: E402
import audit_pii_record as rec  # noqa: E402


ZERO = "0" * 40


class GitReadError(Exception):
    """git could not answer. Never read as "nothing to scan"."""


def _git_bytes(root: Path, *args, stdin: bytes | None = None) -> bytes:
    r = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True)
    if r.returncode != 0:
        raise GitReadError(f"git {' '.join(args[:2])} exited {r.returncode}: "
                           f"{r.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return r.stdout


def _decode_path(raw: bytes) -> str:
    # Display and the binary-extension check only. Content is read by object id,
    # never by this name, so a name that does not decode cleanly cannot hide a blob.
    return raw.decode("utf-8", "replace")


def tracked_public_files(root: Path) -> list[str]:
    """Every tracked, non-binary path. Every tracked file ships, so tracked IS the scope
    (review 311/313). Names come NUL-separated and unstripped (review 316.F3): a newline
    or a leading space in a legal name used to drop the file from the scan."""
    out = _git_bytes(root, "ls-files", "-z")
    return [rel for rel in (_decode_path(raw) for raw in out.split(b"\0") if raw)
            if not cp.is_binary(rel)]


def read_working_copy(root: Path, rel: str) -> str:
    """What a manual scan reads for one tracked path. A symlink is committed as its
    link text, so that is what is scanned, not the file it points to (review 316.F3).
    A tracked path missing from disk, or a directory (a submodule), raises OSError."""
    path = root / rel
    if path.is_symlink():
        return os.readlink(path)
    return path.read_bytes().decode("utf-8", "replace")


def read_prepush_stdin():
    """Parse the pre-push hook's stdin. Each line is:
        <local ref> <local sha> <remote ref> <remote sha>
    Returns (stdin_present, pairs):
      - stdin_present is False for a manual run (tty or empty pipe) — the caller
        then falls back to a working-tree scan.
      - stdin_present is True when the hook piped ref lines; pairs holds
        (local sha, remote sha) for every ref that is not a deletion. A deletion-only
        push yields True + [], which means "nothing to scan, allow" (NOT a
        working-tree fallback)."""
    if sys.stdin is None or sys.stdin.isatty():
        return False, []
    lines = sys.stdin.read().splitlines()
    if not lines:
        return False, []
    pairs = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1] and set(parts[1]) != {"0"}:
            remote = parts[3] if len(parts) >= 4 else ZERO
            pairs.append((parts[1], remote))
    return True, pairs


def _exclusion(root: Path, remote: str) -> list[str]:
    """What the remote already has. Its own tip when this clone knows that commit;
    otherwise every remote-tracking ref (a new branch, or a remote that moved since
    the last fetch; the all-zero id of a new branch is never a known commit).
    Over-scanning is safe; under-scanning is the defect."""
    r = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{remote}^{{commit}}"],
                       capture_output=True)
    return [remote] if r.returncode == 0 else ["--remotes"]


def new_blobs(root: Path, pairs):
    """Yield (rel, content) for every non-binary blob the push sends that the remote
    does not already have, across EVERY pushed commit, not only each ref's tip.

    Review 316.F1 (P0): scanning only the tip tree let a commit that adds a real name,
    followed by one that removes it, publish the name while the scan reported clean.
    The pushed tree is still the scope in the sense of review 313: no path or
    gitignore filter, only the binary-extension skip. Content is read by object id
    (review 316.F2): reading `sha:path` with a lossily decoded, stripped name returned
    empty content for some legal names, and that passed as clean."""
    for local, remote in pairs:
        # Blobs only. Commits still appear, as bare ids with no `path=` token after
        # them; every blob is followed by its path.
        tokens = _git_bytes(root, "rev-list", "--objects", "-z", "--filter=object:type=blob",
                            local, "--not", *_exclusion(root, remote)).split(b"\0")
        for tok, nxt in zip(tokens, tokens[1:]):
            if not nxt.startswith(b"path="):
                continue
            oid, rel = tok.decode("ascii"), _decode_path(nxt[len(b"path="):])
            if cp.is_binary(rel):
                continue
            yield rel, _git_bytes(root, "cat-file", "blob", oid).decode("utf-8", "replace")


def record_gate(root: Path, pairs) -> int:
    """Check 2: every pushed tip's tree needs a clean /audit-pii record, or a waiver."""
    missing, problem = [], None
    try:
        for local, _remote in pairs:
            tree = rec.tree_of(root, local)
            if tree is None:
                raise GitReadError(f"pushed sha {local} is not a commit here")
            if not rec.has_clean_record(root, tree):
                missing.append((local, tree))
    except rec.LedgerError as e:
        # Unreadable is not "no record" and not "recorded": block, naming the cause.
        problem = f"the audit ledger is unreadable: {e}"
        missing = [(local, rec.tree_of(root, local) or "") for local, _ in pairs]
    if not missing:
        print("prepush_pii_guard: every pushed tree has a clean /audit-pii record.",
              file=sys.stderr)
        return 0
    reason = os.environ.get("AUDIT_PII_WAIVE", "").strip()
    if reason:
        for local, tree in missing:
            rec.append_waiver(root, tree, local, reason)
        print(f"prepush_pii_guard: AUDIT_PII_WAIVE used ({reason}); recorded for "
              + ", ".join(local[:8] for local, _ in missing) + ".", file=sys.stderr)
        return 0
    print("\nBLOCKED: no clean /audit-pii record for the pushed tree"
          + (f" ({problem})" if problem else "") + ":", file=sys.stderr)
    for local, _tree in missing:
        print(f"  ⛔ {local[:8]}", file=sys.stderr)
    print("\nFix: run /audit-pii on this branch; on a clean verdict it records the tree, "
          "then re-push.\nTo push this once without it (recorded):\n"
          "  AUDIT_PII_WAIVE='<reason>' git push ...\n", file=sys.stderr)
    return 1


def report_head_record(root: Path) -> None:
    """Manual run: say whether HEAD's tree has a record. Never blocks."""
    tree = rec.tree_of(root, "HEAD")
    try:
        ok = tree is not None and rec.has_clean_record(root, tree)
    except rec.LedgerError as e:
        print(f"prepush_pii_guard: audit ledger unreadable: {e}", file=sys.stderr)
        return
    if ok:
        print("prepush_pii_guard: HEAD's tree has a clean /audit-pii record.", file=sys.stderr)
    else:
        print("prepush_pii_guard: HEAD's tree has no clean /audit-pii record; a push of it "
              "will be blocked until /audit-pii records one.", file=sys.stderr)


def main() -> int:
    root = Path(
        subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True).stdout.strip()
        or "."
    )
    tokens = cp.load_denylist(root)
    if not tokens:
        # No denylist: skip ONLY the deterministic scan (warn loudly; a missing denylist
        # is itself suspicious). The /audit-pii record check below still applies.
        print("prepush_pii_guard: no denylist found — skipping deterministic gate "
              "(regenerate with tools/gen_pii_denylist.py).", file=sys.stderr)

    target_words = cp.load_target_words(root) if tokens else []
    hits = {}
    stdin_present, pairs = read_prepush_stdin()
    if not tokens:
        pass
    elif pairs:
        # Scan what is actually being pushed: every new blob in every pushed commit.
        for rel, content in new_blobs(root, pairs):
            found = cp.find_pii(content, tokens) + cp.find_target_words(content, target_words)
            if found:
                hits[rel] = sorted(set(hits.get(rel, []) + found))
    elif stdin_present:
        # Deletion-only push (all-zero shas): nothing being added, allow.
        pass
    else:
        # Manual invocation (no pre-push stdin): fall back to the working-tree scan.
        for rel in tracked_public_files(root):
            try:
                content = read_working_copy(root, rel)
            except OSError:
                continue
            found = cp.find_pii(content, tokens) + cp.find_target_words(content, target_words)
            if found:
                hits[rel] = sorted(set(found))

    if not hits:
        if tokens:
            print("prepush_pii_guard: deterministic PII scan clean "
                  f"({len(tokens)} tokens).", file=sys.stderr)
        return record_gate(root, pairs) if pairs else _manual_report(root, stdin_present)

    if os.environ.get("PUSH_PII_OVERRIDE") == "1":
        print("prepush_pii_guard: PII hits present but PUSH_PII_OVERRIDE=1 — allowing.",
              file=sys.stderr)
        for rel, toks in hits.items():
            print(f"  (overridden) {rel}: {', '.join(toks)}", file=sys.stderr)
        return record_gate(root, pairs) if pairs else _manual_report(root, stdin_present)

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


def _manual_report(root: Path, stdin_present: bool) -> int:
    """No pushed refs: a deletion-only push passes silently; a manual run reports."""
    if not stdin_present:
        report_head_record(root)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        # Fail CLOSED (Nick, 2026-10-13: "Block the push until it gets worked").
        # A scan that could not run has not shown the push is clean, and this is the
        # last check before a PUBLIC remote. The one way past is the same deliberate
        # override as for a false positive.
        if os.environ.get("PUSH_PII_OVERRIDE") == "1":
            print(f"prepush_pii_guard error, PUSH_PII_OVERRIDE=1 -- allowing UNSCANNED "
                  f"push: {e}", file=sys.stderr)
            sys.exit(0)
        print(f"\nBLOCKED: prepush_pii_guard could not scan this push: {e}\n"
              "Fix the cause and re-push. To push unscanned anyway:\n"
              "  PUSH_PII_OVERRIDE=1 git push ...\n", file=sys.stderr)
        sys.exit(1)
