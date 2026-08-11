#!/usr/bin/env python3
"""
inbox_triage.py — extract the non-machine blocks of data/inbox.md into a review doc.

Read-only. Never mutates the inbox. Emits a markdown review document with one
entry per block: full header, line range, size, content preview, a proposed
destination, and a decision checkbox.

Why routing and not archiving: the oldest blocks turned out to be the most
substantive. Thinking-work does not expire the way a job link does, so "age means
dead" is the wrong heuristic here. Blocks are grouped by proposed destination and
sorted largest-first inside each group, because size correlates with substance.

Machine-generated blocks (career scan, agent drips) are excluded. Comment-span
interiors are non-block territory, per the same rules as inbox_census.py.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/inbox_triage.py --repo-root . \
      --out output/analysis/081026-inbox-review.md

Output: JSON summary to stdout; the review doc to --out.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

INBOX_REL = "data/inbox.md"

MACHINE = re.compile(r"^##\s+(Career Scan Results\s*$|.*Agent drip:)", re.I)

# Proposed destination, FIRST MATCH WINS. Matched against the header plus body,
# so an open-loop signature buried in a block still surfaces it.
DESTINATIONS = [
    # Explicit user tags come FIRST. Nick tagged these himself; an inferred
    # signature must never override a tag he wrote by hand.
    ("personal-vault",
     re.compile(r"#personal\b", re.I),
     "Tagged #personal. Belongs in the personal vault per "
     "framework/personal-vs-job-os-architecture.md, not this repo. Move, do not archive."),
    ("open-loop",
     re.compile(r"SS-NEW-CONTACT|SESSION HANDOFF|Context handoff|Reach back out|"
                r"FOR STANDUP|GAP:|overdue todo", re.I),
     "Live action item or unfinished thread. Route via todo_write.py add / "
     "networking_write.py, then archive."),
    ("coaching",
     re.compile(r"^##\s+Call Debrief|interview|recruiter screen|onsite", re.I),
     "Interview and call corpus. Route to coaching/ so /debrief can do cross-call work."),
    ("system-design",
     re.compile(r"system design|knowledge system|design my AI|compound|workflow|"
                r"skill|framework|infra", re.I),
     "Long-form thinking about the system itself. Route to framework/ or docs/."),
    ("reflection",
     re.compile(r"reflect|my read|what I learned|decision packet|research:", re.I),
     "Personal processing or a decision record. Route to data/reflections/ or "
     "data/decisions.md."),
    ("saved-read",
     re.compile(r"saved read|article|linkedin post|newsletter|podcast", re.I),
     "External content Nick saved. Route to a company/industry note, or drop."),
    # Short imperative captures: one-line ideas and follow-ups. Matched on the
    # HEADER only, so a long block that merely contains "look into" elsewhere is
    # not miscategorised as a one-liner.
    ("idea-todo",
     re.compile(r"^##\s+[\d|\s-]*\|?\s*(Look into|Build|Find|Move|Bring in|Open up|"
                r"Reply to|Research task|Try|Read|Watch|Check out|Set up|Ask)\b", re.I),
     "Short actionable idea. Route via todo_write.py add, or drop if overtaken."),
]
FALLBACK = ("unclassified",
            "No signature matched. Needs your eyes before anything happens to it.")


def find_comment_spans(lines):
    spans, open_at = [], None
    for i, line in enumerate(lines, start=1):
        if open_at is None:
            j = line.find("<!--")
            if j != -1 and "-->" not in line[j:]:
                open_at = i
        elif "-->" in line:
            spans.append((open_at, i))
            open_at = None
    if open_at is not None:
        print(json.dumps({"status": "error", "code": "unbalanced_comment",
                          "message": f"unclosed '<!--' at line {open_at}"}))
        sys.exit(1)
    return spans


def classify(header: str, body: str):
    blob = header + "\n" + body
    for key, rx, guidance in DESTINATIONS:
        if rx.search(blob):
            return key, guidance
    return FALLBACK[0], FALLBACK[1]


def preview(body_lines, n=8):
    out = []
    for l in body_lines:
        s = l.strip()
        if not s or s.startswith("<!--"):
            continue
        out.append(s)
        if len(out) >= n:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--preview-lines", type=int, default=8)
    args = ap.parse_args()

    root = Path(args.repo_root) if args.repo_root else Path.cwd()
    inbox = root / INBOX_REL
    if not inbox.is_file():
        print(json.dumps({"status": "error", "message": f"not found: {inbox}"}))
        sys.exit(1)

    text = inbox.read_text(encoding="utf-8")
    lines = text.splitlines()
    spans = find_comment_spans(lines)
    in_span = lambda n: any(s <= n <= e for s, e in spans)

    heads = [(i, l) for i, l in enumerate(lines, start=1)
             if l.startswith("## ") and not in_span(i)]

    blocks = []
    for n, (i, header) in enumerate(heads):
        end = heads[n + 1][0] - 1 if n + 1 < len(heads) else len(lines)
        if MACHINE.match(header):
            continue
        # Comment interiors are non-block territory. Without this filter the
        # LAST live block swallows any trailing comment span into its body,
        # because the commented headers that would have bounded it are excluded
        # from `heads` by design.
        body_lines = [lines[k - 1] for k in range(i + 1, end + 1) if not in_span(k)]
        body = "\n".join(body_lines)
        key, guidance = classify(header, body)
        blocks.append({
            "start": i, "end": end, "size": end - i + 1,
            "header": header[3:].strip(),
            "dest": key, "guidance": guidance,
            "preview": preview(body_lines, args.preview_lines),
            "sha256": hashlib.sha256(
                ("\n".join([header] + body_lines)).encode("utf-8")).hexdigest(),
        })

    if not blocks:
        print(json.dumps({"status": "error", "code": "empty_extraction",
                          "message": "no non-machine blocks found — parse failure, "
                                     "not an empty inbox"}))
        sys.exit(1)

    order = [d[0] for d in DESTINATIONS] + [FALLBACK[0]]
    groups = {k: [] for k in order}
    for b in blocks:
        groups[b["dest"]].append(b)
    for k in groups:
        groups[k].sort(key=lambda b: -b["size"])

    out = []
    out.append("# Inbox review — non-machine blocks\n")
    out.append(f"Source: `data/inbox.md` sha256 `{hashlib.sha256(text.encode()).hexdigest()[:16]}...`  ")
    out.append(f"Blocks: **{len(blocks)}**. Read-only extraction; the inbox is untouched.\n")
    out.append("Grouped by proposed destination, largest first inside each group, "
               "because size tracks substance. Tick `[x] keep` to route, `[x] drop` "
               "to archive. Nothing happens without your mark.\n")
    out.append("| Group | Blocks | Lines |")
    out.append("|---|---|---|")
    for k in order:
        if groups[k]:
            out.append(f"| {k} | {len(groups[k])} | {sum(b['size'] for b in groups[k])} |")
    out.append("")

    guide = {k: g for k, _, g in DESTINATIONS}
    guide[FALLBACK[0]] = FALLBACK[1]

    for k in order:
        if not groups[k]:
            continue
        out.append(f"\n---\n\n## {k}  ({len(groups[k])} blocks)\n")
        out.append(f"*{guide[k]}*\n")
        for b in groups[k]:
            out.append(f"### {b['header']}\n")
            out.append(f"`L{b['start']}-{b['end']}` · {b['size']} lines · "
                       f"`{b['sha256'][:12]}`\n")
            out.append("- [ ] keep  - [ ] drop\n")
            if b["preview"]:
                out.append("```")
                for line in b["preview"]:
                    out.append(line[:160])
                out.append("```\n")

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(out) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": "ok", "blocks": len(blocks), "out": str(dest),
        "by_group": {k: len(v) for k, v in groups.items() if v},
        "total_lines": sum(b["size"] for b in blocks),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
