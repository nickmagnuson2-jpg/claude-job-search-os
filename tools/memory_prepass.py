#!/usr/bin/env python3
"""memory_prepass.py — the deterministic stage of the memory-mining pipeline.

WHY THIS EXISTS. The 2026-08-13 Phase 2 fan-out asked 12 LLM agents questions a script
could answer, and forbade them the one question only a repo check can answer. On the
subset where it mattered, **63% of its verdicts were wrong** (14 of 22: 8 rules already
fully enforced, 6 partially, all filed as "nothing to promote").

The corrected pipeline puts this script first. It computes every mechanically-decidable
fact and hands them to the agents as INPUT, so an agent is only ever asked for judgment:

  * distinct dated incidents in the body (the raw material of a fire count)
  * explicit recurrence phrases ("fired 3x", "second occurrence", "again on <date>")
  * the tier the file CLAIMS ("Current tier: hook")
  * every artifact path it names, and whether each exists on disk
  * whether the file declares itself intentionally terminal ("no structural gate possible")
  * its in-corpus wikilink neighbours, so families are not split across agents

ROUTING. Files are split into two lanes:

  NEEDS-AGENT — claims a structural tier, or names an artifact that exists, or shows
      >= 2 date signals. Only these can hide an already-landed promotion or a real
      recurrence, and only these are worth an agent.
  DETERMINISTIC — single dated incident, no existing named target, no tier claim.
      Reported with the evidence that placed it there, never as a bare "nothing".

The lane assignment is EVIDENCE, not a verdict: a DETERMINISTIC file is still reported
with its facts so a reviewer can see why. Silence is never a clear.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/memory_prepass.py --memory-dir <path> --repo-root .
  ... --json out.json          write the full fact sheet
  ... --chunk-dir <dir> --chunk-size 11   emit family-aware agent work-lists
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)
DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
RECURRENCE_RE = re.compile(
    r"(?i)\b(fired?\s+\d+\s*x|\d+\s*(?:st|nd|rd|th)\s+(?:fire|occurrence|time)"
    r"|second\s+(?:documented\s+)?occurrence|third\s+occurrence|happened\s+again"
    r"|again\s+on\s+20\d{2}-\d{2}-\d{2}|recurr\w+|\bN\s*=\s*[2-9])\b"
)
TIER_CLAIM_RE = re.compile(r"(?i)current tier:\s*\**\s*([a-z\- +]+)")
TERMINAL_RE = re.compile(
    r"(?i)(no structural gate possible|intentional(?:ly)? terminal|behavioral \(intentional"
    r"|structural enforcement isn'?t useful|cannot be promoted)"
)
ARTIFACT_RE = re.compile(
    r"(tools/[A-Za-z_0-9]+\.(?:py|sh|md)"
    r"|\.claude/skills/[a-z0-9\-]+/SKILL\.md"
    r"|framework/[a-z0-9\-]+\.(?:md|yaml)"
    r"|tools/launchd/[A-Za-z0-9_.\-]+\.plist)"
)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)")
SUPPLEMENT_RE = re.compile(r"(?m)^#{2,4}\s+.*?(20\d{2}-\d{2}-\d{2})")


def artifact_exists(rel: str, repo_root: Path) -> bool:
    """Resolve an artifact path against BOTH the repo and the global ~/.claude tree.

    Skills resolve from two roots: `.claude/skills/<name>/SKILL.md` may be project-local
    OR a global skill under `~/.claude/skills/`. Checking only the repo reported three
    live global skills as absent, and an agent reading that fact would conclude the rule's
    target does not exist -- a false "genuinely open" verdict, which is the exact error
    class this pipeline exists to eliminate. Found 2026-08-13 by an agent cross-checking
    a pre-pass fact against the filesystem, which is the intended behavior.
    """
    if (repo_root / rel).exists():
        return True
    return (Path.home() / rel).exists()


def body_of(text: str) -> str:
    m = FRONTMATTER_RE.match(text)
    return text[m.end():] if m else text


def analyse(path: Path, repo_root: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    body = body_of(text)

    dates = sorted({"-".join(g) for g in DATE_RE.findall(body)})
    supplements = sorted(set(SUPPLEMENT_RE.findall(body)))
    recurrence = sorted({m.group(0) for m in RECURRENCE_RE.finditer(body)})
    tier = TIER_CLAIM_RE.search(body)
    artifacts = sorted(set(ARTIFACT_RE.findall(body)))
    exists = {a: artifact_exists(a, repo_root) for a in artifacts}

    return {
        "file": path.name,
        "distinct_dates": dates,
        "date_count": len(dates),
        "dated_supplement_headers": supplements,
        "recurrence_phrases": recurrence,
        "claimed_tier": tier.group(1).strip() if tier else None,
        "declares_terminal_behavioral": bool(TERMINAL_RE.search(body)),
        "named_artifacts": artifacts,
        "artifact_exists": exists,
        "names_existing_artifact": any(exists.values()),
        "wikilinks": sorted(set(WIKILINK_RE.findall(body))),
    }


# A tier claim only needs verifying when it claims a STRUCTURAL tier. "Current tier:
# behavioral" is the absence of a promotion, not a claim of one -- routing on the bare
# phrase sent 293 of 406 files to an agent, most of them saying "nothing was built".
STRUCTURAL_TIER_RE = re.compile(r"(?i)\b(hook|skill|framework|hard.?rule|script|launchd|doc)\b")


def route(fact: dict) -> str:
    """NEEDS-AGENT only where an LLM can add something a script cannot.

    Validated against ground truth: all 24 files independently proven misclassified or
    verified by the 2026-08-13 corrective passes (8 already-landed, 6 partial, 9 real
    promotions, 1 ghost) route to NEEDS-AGENT under these rules. A router that let any
    of them fall into the deterministic lane would rebuild the original failure.
    """
    if fact["claimed_tier"] and STRUCTURAL_TIER_RE.search(fact["claimed_tier"]):
        return "NEEDS-AGENT"          # a structural-tier claim may be a ghost
    if fact["names_existing_artifact"]:
        return "NEEDS-AGENT"          # the artifact may already implement the rule
    # Two dates is the norm (an origin date plus a confirmation); it is not recurrence.
    # A dated supplement HEADER, an explicit recurrence phrase, or 3+ distinct dates is.
    if fact["dated_supplement_headers"] or fact["recurrence_phrases"] or fact["date_count"] >= 3:
        return "NEEDS-AGENT"          # possible real recurrence; needs judgment
    return "DETERMINISTIC"


def family_chunks(facts: list[dict], size: int) -> list[list[str]]:
    """Keep wikilink-connected files together so family-N is computable in one context.

    The 2026-08-13 run partitioned alphabetically; all 9 detected families spanned chunk
    boundaries, making family-N structurally uncomputable by any single agent.
    """
    names = {f["file"] for f in facts}
    stem_to_file = {n[:-3]: n for n in names}
    adj = defaultdict(set)
    for f in facts:
        for link in f["wikilinks"]:
            tgt = stem_to_file.get(link.strip())
            if tgt and tgt != f["file"]:
                adj[f["file"]].add(tgt)
                adj[tgt].add(f["file"])

    seen, components = set(), []
    for f in facts:
        n = f["file"]
        if n in seen:
            continue
        stack, comp = [n], []
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.append(x)
            stack.extend(y for y in adj.get(x, ()) if y not in seen)
        components.append(sorted(comp))

    # Giant components exceed one agent's budget; split them but keep the split contiguous
    # so neighbours still land together, and never mix two large families in one chunk.
    chunks: list[list[str]] = []
    buffer: list[str] = []
    for comp in sorted(components, key=len, reverse=True):
        if len(comp) >= size:
            for i in range(0, len(comp), size):
                chunks.append(comp[i:i + size])
            continue
        if len(buffer) + len(comp) > size:
            chunks.append(buffer)
            buffer = []
        buffer.extend(comp)
    if buffer:
        chunks.append(buffer)
    return chunks


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--memory-dir", required=True)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--glob", default="feedback_*.md")
    ap.add_argument("--json", default=None)
    ap.add_argument("--chunk-dir", default=None)
    ap.add_argument("--chunk-size", type=int, default=11)
    args = ap.parse_args(argv)

    memory_dir = Path(args.memory_dir).expanduser().resolve()
    repo_root = Path(args.repo_root).resolve()
    paths = sorted(memory_dir.glob(args.glob))
    if not paths:
        print(json.dumps({"status": "error",
                          "reason": f"ZERO files match {args.glob} in {memory_dir} — "
                                    "an empty scope is an error, not a clean pass"}, indent=2))
        return 2

    facts = [analyse(p, repo_root) for p in paths]
    for f in facts:
        f["lane"] = route(f)

    needs = [f for f in facts if f["lane"] == "NEEDS-AGENT"]
    det = [f for f in facts if f["lane"] == "DETERMINISTIC"]

    report = {
        "status": "ok",
        "scanned": len(facts),
        "needs_agent": len(needs),
        "deterministic": len(det),
        "reasons_needs_agent": {
            "claims_a_tier": sum(1 for f in needs if f["claimed_tier"]),
            "names_existing_artifact": sum(1 for f in needs if f["names_existing_artifact"]),
            "multi_date_or_recurrence": sum(
                1 for f in needs if f["date_count"] >= 2 or f["recurrence_phrases"]),
        },
        "declares_terminal_behavioral": sum(1 for f in facts if f["declares_terminal_behavioral"]),
    }

    if args.chunk_dir:
        cdir = Path(args.chunk_dir)
        cdir.mkdir(parents=True, exist_ok=True)
        chunks = family_chunks(needs, args.chunk_size)
        by_name = {f["file"]: f for f in facts}
        for i, chunk in enumerate(chunks, start=1):
            (cdir / f"work{i:02d}.json").write_text(
                json.dumps([by_name[n] for n in chunk], indent=1), encoding="utf-8")
        assigned = sum(len(c) for c in chunks)
        assert assigned == len(needs), f"chunking lost files: {assigned} != {len(needs)}"
        report["chunks"] = len(chunks)
        report["chunk_dir"] = str(cdir)
        report["assigned"] = assigned

    if args.json:
        Path(args.json).write_text(json.dumps(facts, indent=1), encoding="utf-8")
        report["fact_sheet"] = args.json

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
