#!/usr/bin/env python3
"""
voice_corpus_audit.py — Falsifiable test for the two-tier capture principle.

Per `framework/two-tier-capture.md`: every synthesized artifact derived from
voice content (Wispr captures, Granola transcripts, /reflect rumbles) must
have a wiki-link header pointing to a raw-tier source file. This audit
finds synthesized files written in the last N days and verifies each one
either (a) has a wiki-link header pointing to an existing raw-tier file,
or (b) is on the allowlist of synthesis files that don't require one.

Run weekly:
  python3 tools/voice_corpus_audit.py --since 7d
  python3 tools/voice_corpus_audit.py --since 30d --strict

Exit codes:
  0 — clean (no orphaned syntheses)
  1 — drift detected (orphaned syntheses found)
  2 — usage error
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Folders that contain synthesized artifacts (the "back wall" per the principle).
# Files written here in the audit window must show their raw-tier wiki-link.
SYNTHESIS_TARGETS = [
    "coaching/progress-recruiter",
    "data/company-notes",
    "data/projects",
    "data/conviction.md",
    "data/professional-identity.md",
    "data/goals.md",
]

# Folders that contain raw-tier files. Wiki-links in synthesis must resolve here.
RAW_TIER_FOLDERS = [
    "data/reflections",
    "data/voice-corpus",
    "data/voice-corpus/granola",
    "data/voice-corpus/transcripts",
]

# Vault inbox (outside repo): Wispr raw archive lives here.
VAULT_INBOX = Path.home() / "Documents/Obsidian/00-voice-corpus-archive"

# Files exempt from the audit. Some artifacts are not voice-derived even if they
# live in synthesis folders — e.g., template files, summary files maintained by
# scripts, or pre-existing files predating the principle.
ALLOWLIST = {
    "coaching/progress-recruiter/_summary.md",      # script-maintained
    "data/projects/_template.md",
    "data/projects/altman-solon.md",                # legacy
    "data/projects/espn.md",                        # legacy
    "data/projects/ibm.md",                         # legacy
    "data/projects/kiki-crossword.md",              # legacy
    "data/projects/mckinsey.md",                    # legacy
    "data/projects/yahoo.md",                       # legacy
    "data/projects/ocean-tech-hackathon.md",        # legacy
    "data/goals.md",                                # not voice-derived; thesis/criteria doc
    "data/professional-identity.md",                # not voice-derived directly; identity reference
}

WIKI_LINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

# Folders whose .md files MUST carry a voice: <label> frontmatter per
# framework/two-tier-capture.md § "Voice classification labels".
LABEL_TARGETS = [
    "data/reflections",
    "data/voice-corpus",
    "data/voice-corpus/granola",
    "data/voice-corpus/transcripts",
    "data/company-notes",
    "data/projects",
    "coaching/progress-recruiter",
]

# Files exempt from label-check (templates, summaries, sealed, legacy).
LABEL_EXEMPT = {
    "data/reflections/_journal-template.md",
    "coaching/progress-recruiter/_summary.md",
    "data/projects/_template.md",
}

VALID_LABELS = {"pure-voice", "mixed-voice", "cloud-generated"}
VOICE_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
VOICE_FIELD_PATTERN = re.compile(r"^voice:\s*(\S+)\s*$", re.MULTILINE)


def extract_voice_label(text: str) -> str | None:
    """Return the voice label from YAML frontmatter, or None if missing/invalid."""
    m = VOICE_FRONTMATTER_PATTERN.match(text)
    if not m:
        return None
    fm_body = m.group(1)
    vm = VOICE_FIELD_PATTERN.search(fm_body)
    if not vm:
        return None
    return vm.group(1).strip()


def check_labels() -> tuple[list[dict], list[dict], list[dict]]:
    """Scan label-target folders. Return (ok, missing, invalid)."""
    ok, missing, invalid = [], [], []
    seen: set[Path] = set()
    for target in LABEL_TARGETS:
        path = REPO_ROOT / target
        if not path.is_dir():
            continue
        for f in path.rglob("*.md"):
            if f in seen:
                continue
            seen.add(f)
            rel = f.relative_to(REPO_ROOT).as_posix()
            if rel in LABEL_EXEMPT:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError) as e:
                invalid.append({"file": rel, "reason": f"read error: {e}"})
                continue
            label = extract_voice_label(text)
            if label is None:
                missing.append({"file": rel, "reason": "no voice: <label> in frontmatter"})
            elif label not in VALID_LABELS:
                invalid.append({"file": rel, "reason": f"invalid label '{label}' (expected one of {sorted(VALID_LABELS)})"})
            else:
                ok.append({"file": rel, "label": label})
    return ok, missing, invalid


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="7d", help="Audit window (e.g., 7d, 14d, 30d). Default 7d.")
    p.add_argument("--strict", action="store_true", help="Fail on warnings as well as errors.")
    p.add_argument("--json", action="store_true", help="Emit JSON report instead of human-readable.")
    p.add_argument(
        "--check-labels",
        action="store_true",
        help="Verify every voice-ecosystem file carries a 'voice: pure-voice|mixed-voice|cloud-generated' "
             "frontmatter label per framework/two-tier-capture.md § 'Voice classification labels'.",
    )
    return p.parse_args()


def parse_window(s: str) -> timedelta:
    m = re.match(r"^(\d+)([dh])$", s)
    if not m:
        sys.exit(f"--since must look like '7d' or '24h', got '{s}'")
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(days=n) if unit == "d" else timedelta(hours=n)


def list_synthesis_files(window: timedelta) -> list[Path]:
    """Find files in synthesis target folders modified within window."""
    cutoff = datetime.now().timestamp() - window.total_seconds()
    files = []
    for target in SYNTHESIS_TARGETS:
        path = REPO_ROOT / target
        if path.is_file():
            if path.stat().st_mtime >= cutoff:
                files.append(path)
        elif path.is_dir():
            for f in path.rglob("*.md"):
                if f.stat().st_mtime >= cutoff:
                    files.append(f)
    return files


def extract_wiki_links(text: str) -> set[str]:
    """Return the set of wiki-link targets (the part inside [[...]] before any |)."""
    return {m.group(1).strip() for m in WIKI_LINK_PATTERN.finditer(text)}


def find_raw_tier_files() -> dict[str, Path]:
    """Build a name → path map of all raw-tier files in the vault."""
    files: dict[str, Path] = {}
    for folder in RAW_TIER_FOLDERS:
        path = REPO_ROOT / folder
        if path.is_dir():
            for f in path.rglob("*.md"):
                # Index by stem (filename without extension)
                files[f.stem] = f
    # Also index Obsidian vault inbox (Wispr archives)
    if VAULT_INBOX.is_dir():
        for f in VAULT_INBOX.glob("wispr-*.md"):
            files[f.stem] = f
    return files


def classify(file: Path, raw_tier: dict[str, Path]) -> dict:
    """Return audit verdict for a single synthesis file."""
    rel = file.relative_to(REPO_ROOT).as_posix()
    if rel in ALLOWLIST:
        return {"file": rel, "status": "exempt", "reason": "allowlisted"}
    try:
        text = file.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError) as e:
        return {"file": rel, "status": "error", "reason": str(e)}

    links = extract_wiki_links(text)
    # Look for any link that resolves to a raw-tier file.
    raw_links = []
    for link in links:
        # Strip folder prefix (e.g., "projects/zuora" → "zuora")
        stem = link.split("/")[-1]
        if stem in raw_tier:
            raw_links.append(link)

    if not links:
        return {"file": rel, "status": "drift", "reason": "no wiki-links at all"}
    if not raw_links:
        return {"file": rel, "status": "drift", "reason": "wiki-links present but none resolve to raw-tier files"}
    return {"file": rel, "status": "ok", "raw_links": raw_links}


def main() -> int:
    args = parse_args()
    window = parse_window(args.since)
    raw_tier = find_raw_tier_files()
    synthesis_files = list_synthesis_files(window)

    results = [classify(f, raw_tier) for f in synthesis_files]
    drift = [r for r in results if r["status"] == "drift"]
    errors = [r for r in results if r["status"] == "error"]
    ok = [r for r in results if r["status"] == "ok"]
    exempt = [r for r in results if r["status"] == "exempt"]

    label_ok: list[dict] = []
    label_missing: list[dict] = []
    label_invalid: list[dict] = []
    if args.check_labels:
        label_ok, label_missing, label_invalid = check_labels()

    if args.json:
        payload = {
            "window": args.since,
            "total_synthesis_files": len(synthesis_files),
            "ok": len(ok),
            "exempt": len(exempt),
            "drift": len(drift),
            "errors": len(errors),
            "results": results,
        }
        if args.check_labels:
            payload["labels"] = {
                "checked": len(label_ok) + len(label_missing) + len(label_invalid),
                "ok": len(label_ok),
                "missing": label_missing,
                "invalid": label_invalid,
            }
        print(json.dumps(payload, indent=2))
    else:
        print(f"Voice-Corpus Audit (window: {args.since})")
        print(f"Synthesis files modified: {len(synthesis_files)}")
        print(f"  ✅ OK (raw-tier wiki-link present): {len(ok)}")
        print(f"  ⊘  Exempt (allowlisted): {len(exempt)}")
        print(f"  ⚠️  Drift (no raw-tier wiki-link): {len(drift)}")
        print(f"  ❌ Errors: {len(errors)}")
        print(f"Raw-tier files indexed: {len(raw_tier)}")
        print()
        if drift:
            print("DRIFT DETECTED — these synthesis files lack a raw-tier wiki-link:")
            for r in drift:
                print(f"  • {r['file']}  ({r['reason']})")
            print()
            print("Fix options:")
            print("  1. Add a wiki-link header to the synthesis file pointing to its raw source.")
            print("  2. Backfill the raw-tier file (data/reflections/ or data/voice-corpus/) and link it.")
            print("  3. Allowlist the file in tools/voice_corpus_audit.py if it's genuinely not voice-derived.")
        if errors:
            print("ERRORS:")
            for r in errors:
                print(f"  • {r['file']}: {r['reason']}")

        if args.check_labels:
            total_labeled = len(label_ok) + len(label_missing) + len(label_invalid)
            print()
            print(f"Voice-label check (--check-labels): {total_labeled} files in label-target folders")
            print(f"  ✅ Labeled correctly: {len(label_ok)}")
            print(f"  ⚠️  Missing label:     {len(label_missing)}")
            print(f"  ❌ Invalid label:     {len(label_invalid)}")
            if label_missing:
                print()
                print("MISSING voice: <pure-voice|mixed-voice|cloud-generated> frontmatter:")
                for r in label_missing:
                    print(f"  • {r['file']}")
            if label_invalid:
                print()
                print("INVALID voice labels:")
                for r in label_invalid:
                    print(f"  • {r['file']}  ({r['reason']})")

    label_problem = args.check_labels and (label_missing or label_invalid)
    if drift or label_problem or (args.strict and errors):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
