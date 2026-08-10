#!/usr/bin/env python3
"""
calibration_review.py — human-in-the-loop review of the calibration ledger.

The continuous-learning loop routes low-confidence / unlabeled outcomes to human
adjudication before they harden into scoring weight. This tool is that step:

  worksheet  — generate data/calibration/review-worksheet.md from
               company-outcomes.json (every company pre-filled with its current
               label + evidence; edit LABEL, add freeform COLOR).
  apply      — parse the filled worksheet back into company-outcomes.json:
               sets final_label from LABEL, preserves the model's label as
               extractor_label, records human_color + human_labeled=true.
               Prints a diff of what changed.

Nick edits the worksheet in Obsidian; nothing is fabricated — a blank COLOR just
means no added context. Read-only on everything except company-outcomes.json (and
only on `apply`). Regenerate the ledger afterward with `scorer_eval.py ledger`.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/calibration_review.py worksheet --repo-root .
  PYTHONIOENCODING=utf-8 python3 tools/calibration_review.py apply --repo-root .
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

LABELS = ("engaged", "rejected-early", "nick-declined", "unlabeled")


def _load(repo_root: Path):
    path = repo_root / "data" / "calibration" / "company-outcomes.json"
    records = json.loads(path.read_text(encoding="utf-8"))
    return path, records


def _manifest(repo_root: Path) -> dict:
    p = repo_root / "data" / "calibration" / ".manifest.json"
    if not p.exists():
        return {}
    return {m["company"]: m for m in json.loads(p.read_text(encoding="utf-8"))}


def _evidence(rec: dict) -> str:
    j = (rec.get("justification") or "").replace("\n", " ")
    return j[:180]


def _sources(man_entry: dict) -> str:
    s = []
    if man_entry.get("company_note"):
        s.append("note")
    if man_entry.get("debriefs"):
        s.append(f"{len(man_entry['debriefs'])}debrief")
    if man_entry.get("dossier_dir"):
        s.append("dossier")
    return "+".join(s) if s else "stage-only"


BUCKETS = [
    ("FLAGGED — needs your call (low-confidence or extractor/verifier disagreed)",
     lambda r: r.get("needs_review") or r.get("disagreement")),
    ("ENGAGED (real process happened) — confirm or correct",
     lambda r: r.get("final_label") == "engaged" and not (r.get("needs_review") or r.get("disagreement"))),
    ("REJECTED-EARLY (applied, screened out) — confirm or correct",
     lambda r: r.get("final_label") == "rejected-early" and not (r.get("needs_review") or r.get("disagreement"))),
    ("NICK-DECLINED (you passed) — confirm or correct",
     lambda r: r.get("final_label") == "nick-declined" and not (r.get("needs_review") or r.get("disagreement"))),
    ("UNLABELED (backlog / pending / never pursued) — add color; relabel any that had a process",
     lambda r: r.get("final_label") == "unlabeled" and not (r.get("needs_review") or r.get("disagreement"))),
]


def generate_worksheet(repo_root: Path) -> str:
    _, records = _load(repo_root)
    man = _manifest(repo_root)
    seen = set()
    out = [
        "# Calibration Review Worksheet",
        "",
        "> Edit `LABEL:` if the outcome is wrong (one of: engaged | rejected-early | nick-declined | unlabeled).",
        "> Add anything you remember on the `COLOR:` line — what really happened, why you passed, how interested you were, timing. Leave blank if nothing to add.",
        "> Save, then tell Claude to run `calibration_review.py apply`. Nothing here is used elsewhere until you apply it.",
        "",
        "Labels: **engaged** = a real screen/interview/call happened · **rejected-early** = you applied, they closed it with no screen · **nick-declined** = you passed/withdrew yourself · **unlabeled** = no outcome / still pending / never really pursued.",
        "",
    ]
    for title, pred in BUCKETS:
        rows = [r for r in records if pred(r) and r["entity"] not in seen]
        for r in rows:
            seen.add(r["entity"])
        if not rows:
            continue
        out.append(f"## {title}  ({len(rows)})")
        out.append("")
        for r in rows:
            me = man.get(r["entity"], {})
            stage = (me.get("stages") or [""])[0][:50]
            out.append(f"### {r['entity']}")
            out.append(f"`current: {r.get('final_label')}` · conf {r.get('confidence','?')} · "
                       f"interest {r.get('interest_level','?')} · sources {_sources(me)} · stage: {stage}")
            out.append(f"_{_evidence(r)}_")
            out.append(f"LABEL: {r.get('final_label')}")
            out.append("COLOR: ")
            out.append("")
    return "\n".join(out)


_BLOCK_RE = re.compile(r"^### (.+?)$", re.MULTILINE)


def parse_worksheet(text: str) -> dict:
    """Return {company: {label, color}} for every block with a LABEL line."""
    result = {}
    parts = _BLOCK_RE.split(text)
    # parts = [preamble, name1, body1, name2, body2, ...]
    for i in range(1, len(parts), 2):
        name = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        label_m = re.search(r"^LABEL:\s*(.*)$", body, re.MULTILINE)
        color_m = re.search(r"^COLOR:\s*(.*)$", body, re.MULTILINE)
        label = (label_m.group(1).strip() if label_m else "")
        color = (color_m.group(1).strip() if color_m else "")
        if label:
            result[name] = {"label": label, "color": color}
    return result


def apply_worksheet(repo_root: Path) -> dict:
    ws = repo_root / "data" / "calibration" / "review-worksheet.md"
    if not ws.exists():
        return {"error": "no review-worksheet.md found"}
    edits = parse_worksheet(ws.read_text(encoding="utf-8"))
    path, records = _load(repo_root)
    changes = []
    for r in records:
        e = edits.get(r["entity"])
        if not e:
            continue
        new_label = e["label"]
        if new_label not in LABELS:
            changes.append({"entity": r["entity"], "skipped": f"invalid label '{new_label}'"})
            continue
        old = r.get("final_label")
        if new_label != old:
            r.setdefault("extractor_label", r.get("label"))
            r["final_label"] = new_label
            r["human_labeled"] = True
            changes.append({"entity": r["entity"], "from": old, "to": new_label})
        if e["color"]:
            r["human_color"] = e["color"]
            r["human_labeled"] = True
    # Atomic write.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    relabels = [c for c in changes if "to" in c]
    return {"relabels": relabels, "skipped": [c for c in changes if "skipped" in c],
            "colored_or_changed": sum(1 for r in records if r.get("human_labeled"))}


def main() -> int:
    ap = argparse.ArgumentParser(description="Human-in-the-loop calibration review.")
    ap.add_argument("command", choices=["worksheet", "apply"])
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args()
    repo_root = Path(args.repo_root).resolve()

    if args.command == "worksheet":
        text = generate_worksheet(repo_root)
        out = repo_root / "data" / "calibration" / "review-worksheet.md"
        out.write_text(text, encoding="utf-8")
        n = text.count("### ")
        print(json.dumps({"status": "ok", "worksheet": str(out), "companies": n}, indent=2))
    else:
        result = apply_worksheet(repo_root)
        print(json.dumps({"status": "ok", **result}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
