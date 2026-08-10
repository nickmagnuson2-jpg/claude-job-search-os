#!/usr/bin/env python3
"""context_file_audit.py — measure an always-loaded context file section by section.

Files like CLAUDE.md and memory/MEMORY.md are loaded into EVERY session, so their
size is a per-session tax. This script is the deterministic half of
`/trim-context-file`: it measures and classifies, it never edits and never decides.
The KEEP/MOVE judgment belongs to the human in the skill.

Why a script and not eyeballing: line counts badly understate the real cost when a
section is a few enormous paragraphs (a 13-line "Hard Rules" section can outweigh a
91-line table). Bytes are the honest unit.

Heuristic signals per section (advisory only, never a verdict):
  rule_density      fraction of lines with imperative/normative markers
                    ("never", "always", "must", "do not", "required", "mandatory")
  lookup_density fraction of lines that are table rows, code fences, or list
                    entries pointing at paths -- the shape of lookup material
  A section that is high-reference and low-rule is a MOVE candidate: it is consulted
  when doing a specific task, not needed resident in every session.

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py CLAUDE.md
    PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py CLAUDE.md --json
    PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py memory/MEMORY.md --top 5
"""
import argparse
import json
import re
import sys
from pathlib import Path

RULE_MARKERS = re.compile(
    r"\b(never|always|must|do not|don't|required|mandatory|hard rule|forbidden|"
    r"before any|stop and|blocking)\b", re.I)
REFERENCE_MARKERS = re.compile(r"^\s*(\||```|[-*]\s+`|\d+\.\s+`)")
PATH_HINT = re.compile(r"`[^`]*\.(md|py|sh|ya?ml|json)`|`[a-z_]+/`")


def split_sections(text: str) -> list[dict]:
    """Split on '## ' headings. Content before the first heading is '(preamble)'."""
    sections, name, buf = [], "(preamble)", []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append({"name": name, "lines": buf})
            name, buf = line[3:].strip(), []
        else:
            buf.append(line)
    sections.append({"name": name, "lines": buf})
    return [s for s in sections if s["lines"] or s["name"] != "(preamble)"]


def classify(lines: list[str]) -> dict:
    body = [l for l in lines if l.strip()]
    if not body:
        return {"rule_density": 0.0, "lookup_density": 0.0}
    rule = sum(1 for l in body if RULE_MARKERS.search(l))
    ref = sum(1 for l in body if REFERENCE_MARKERS.match(l) or PATH_HINT.search(l))
    return {
        "rule_density": round(rule / len(body), 3),
        "lookup_density": round(ref / len(body), 3),
    }


def suggest(rule_d: float, ref_d: float, share: float) -> str:
    """Advisory only. The skill surfaces this; the human decides."""
    if rule_d >= 0.25:
        return "KEEP — rule-dense; belongs in the always-loaded tier"
    if ref_d >= 0.40 and share >= 0.05:
        return "MOVE — reference-shaped and heavy; consult on demand"
    if share >= 0.15:
        return "REVIEW — large enough that its share alone justifies a look"
    return "REVIEW"


def audit(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    total_bytes = len(text.encode("utf-8"))
    rows = []
    for sec in split_sections(text):
        raw = "\n".join(sec["lines"])
        nbytes = len(raw.encode("utf-8"))
        share = nbytes / total_bytes if total_bytes else 0.0
        sig = classify(sec["lines"])
        rows.append({
            "section": sec["name"],
            "lines": len(sec["lines"]),
            "bytes": nbytes,
            "share": round(share, 4),
            **sig,
            "suggestion": suggest(sig["rule_density"], sig["lookup_density"], share),
        })
    rows.sort(key=lambda r: r["bytes"], reverse=True)
    return {
        "file": str(path),
        "total_lines": len(text.splitlines()),
        "total_bytes": total_bytes,
        "sections": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--top", type=int, default=0, help="show only the N largest sections")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.is_file():
        print(json.dumps({"error": f"not a file: {p}"}), file=sys.stderr)
        return 1

    result = audit(p)
    if args.top:
        result["sections"] = result["sections"][: args.top]

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"{result['file']}: {result['total_lines']} lines, "
          f"{result['total_bytes'] / 1024:.1f} KB  (loaded every session)")
    print(f"{'bytes':>7} {'share':>7} {'rule':>6} {'ref':>6}  section / suggestion")
    for r in result["sections"]:
        print(f"{r['bytes']:>7} {r['share'] * 100:>6.1f}% {r['rule_density']:>6.2f} "
              f"{r['lookup_density']:>6.2f}  {r['section']}")
        print(f"{'':>29}  -> {r['suggestion']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
