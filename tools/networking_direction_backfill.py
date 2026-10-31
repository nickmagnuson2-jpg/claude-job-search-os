#!/usr/bin/env python3
"""networking_direction_backfill.py - record the direction of every existing interaction.

Since 2026-10-30 `networking_write.py log --direction in|out|both|none` stores the direction
in the interaction type ("email (inbound)" / "(outbound)" / "(two-way)"). This one-off labels
the entries logged before that, from the verdict the summary-reading classifier gave each:

    reply                                       -> "<type> (inbound)"
    reply, and the summary also records a send  -> "<type> (two-way)"   (Nick, 13:42)
    send                                        -> "<type> (outbound)"
    None                                        -> unchanged

A type that already records a direction is left alone, with one exception (Nick, 14:02): an
entry already marked "(inbound)" whose summary also records a send is relabelled
"(two-way)". The current tools/reply_classify.py
reads only the type, so --classifier names the old one: a file holding
`git show dfccc00:tools/reply_classify.py` (the rules main ran on 2026-10-30).

"Also records a send" is a heuristic, used once and reviewed by Nick line by line: SENT in
capitals anywhere, an outbound opener (Nick replied / Nick sent / Outbound / Follow-up #<n> /
Cold outreach), or "Nick" or "I" followed by sent / replied / emailed / texted / messaged.
After the backfill, every reply is still a reply and every send still a send; the two-way
entries add one send each.

Two steps, and the second needs Nick's approval:

  plan   --classifier PATH [--today YYYY-MM-DD]. Reads data/networking.md, writes
         <out-dir>/backfill-review.md and <out-dir>/backfill-plan.json (every proposed
         change, the two-way list, the file's sha256). Never writes to data/. It also
         records (a) the acceptance check: entries whose verdict under the CURRENT classifier
         on the backfilled file is not the old verdict (a reply may become two-way, nothing
         else may move; must be empty), (b) the morning-page effect: day_check's week counts
         for the last 4 weeks, before and after, and (c) the interim effect: entries whose
         verdict --classifier itself would change after the backfill.
  apply  --plan PATH --classifier PATH --approved. Refuses unless the acceptance check is
         empty, the file's sha256 still equals the plan's, and the plan recomputed from the
         file and the classifier equals the plan file (so an edited plan cannot pass, Codex
         F2). Backs the file up to output/reply-direction/backups/, rewrites only the planned
         headings, and writes atomically.

Usage:
  git show dfccc00:tools/reply_classify.py > /tmp/old_reply_classify.py
  PYTHONIOENCODING=utf-8 python3 tools/networking_direction_backfill.py --repo-root . plan \\
      --classifier /tmp/old_reply_classify.py --out-dir output/reply-direction
  PYTHONIOENCODING=utf-8 python3 tools/networking_direction_backfill.py --repo-root . apply \\
      --plan output/reply-direction/backfill-plan.json --classifier /tmp/old_reply_classify.py \\
      --approved

Output: one JSON line, {"status": "ok", ...} or {"status": "error", "code": ...}.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_report import format_line  # noqa: E402
from day_check import INTERACTION_RE, collect  # noqa: E402
from md_table import out_error, out_ok, write_atomic  # noqa: E402
from reply_classify import (REPLY_VERDICTS, SEND_VERDICTS, base_type,  # noqa: E402
                            classify as classify_type, direction, with_direction)

NETWORKING = Path("data") / "networking.md"
BACKUPS = Path("output") / "reply-direction" / "backups"
SCOPE = "interaction headings in data/networking.md"
TO_DIRECTION = {"reply": "in", "send": "out"}
# The only verdict moves the backfill may make (old verdict, new verdict).
ALLOWED = {(None, None), ("send", "send"), ("reply", "reply"), ("reply", "both")}
# The one-time two-way heuristic (see the module docstring).
_SENT_UPPER = re.compile(r"\bSENT\b")
_OUT_OPENER = re.compile(r"^(?:nick (?:replied|sent)\b|outbound\b|follow-up #\d+|cold outreach)",
                         re.IGNORECASE)
_NICK_VERB = re.compile(r"\b(?:Nick|I)\s+(?:sent|replied|emailed|texted|messaged)\b")
# The files day_check.collect reads, copied to a scratch root for the week effect.
COLLECT_FILES = ("outreach-log.md", "job-pipeline.md", "job-todos.md", "goals.md")


def _entries(text: str) -> list[dict]:
    """Every interaction heading: 1-based line number, the type, the summary."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        m = INTERACTION_RE.match(line)
        if m:
            out.append({"line": i, "type": m.group(2), "summary": m.group(3),
                        "heading": line, "span": m.span(2)})
    return out


def _load_classifier(path: str):
    spec = importlib.util.spec_from_file_location("old_reply_classify", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.classify


def _also_sent(summary: str) -> bool:
    s = summary.strip()
    return bool(_SENT_UPPER.search(s) or _OUT_OPENER.match(s) or _NICK_VERB.search(s))


def _relabel(text: str, changes: list[dict], key: str = "new_type") -> str:
    """The text with each planned heading's type replaced, every other byte kept."""
    by_line = {c["line"]: c for c in changes}
    lines = text.splitlines(keepends=True)
    for e in _entries(text):
        c = by_line.get(e["line"])
        if c is None:
            continue
        raw = lines[e["line"] - 1]
        a, b = e["span"]
        lines[e["line"] - 1] = raw[:a] + c[key] + raw[b:]
    return "".join(lines)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _week_counts(root: Path, networking_text: str, targets: list[date]) -> list[dict]:
    """day_check's week counts for each target, with networking.md replaced by the text."""
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        data.mkdir()
        for name in COLLECT_FILES:
            if (root / "data" / name).is_file():
                shutil.copyfile(root / "data" / name, data / name)
        (data / "networking.md").write_text(networking_text, encoding="utf-8")
        return [collect(Path(tmp), t) for t in targets]


def _weeks(root: Path, before_text: str, after_text: str, today: date) -> list[dict]:
    """This week so far and the 3 full weeks before it, before and after the backfill."""
    last_sunday = today - timedelta(days=today.weekday() + 1)
    targets = [today] + [last_sunday - timedelta(days=7 * k) for k in range(3)]
    before = _week_counts(root, before_text, targets)
    after = _week_counts(root, after_text, targets)
    rows = []
    for t, b, a in zip(targets, before, after):
        rows.append({"week_start": b["week_start"], "through": t.isoformat(),
                     "replies_before": b["counts"]["replies"]["week"],
                     "replies_after": a["counts"]["replies"]["week"],
                     "sends_before": b["counts"]["sends"]["week"],
                     "sends_after": a["counts"]["sends"]["week"]})
    return sorted(rows, key=lambda r: r["week_start"])


def _plan_changes(entries: list[dict], old) -> tuple[list[dict], int, int]:
    """(changes, no-verdict count, already-recorded count) for the entries under `old`."""
    changes, no_verdict, already = [], 0, 0
    for e in entries:
        verdict = old(e["type"], e["summary"])
        recorded = direction(e["type"])
        # The one relabel: "<type> (inbound)" that also records a send (Nick, 14:02).
        relabel = (verdict == "reply" and recorded == "in" and base_type(e["type"]) != e["type"]
                   and _also_sent(e["summary"]))
        if verdict is None:
            no_verdict += 1
        elif recorded is not None and not relabel:
            already += 1
        else:
            two_way = verdict == "reply" and _also_sent(e["summary"])
            d = "both" if two_way else TO_DIRECTION[verdict]
            plain = base_type(e["type"]) if relabel else e["type"]
            changes.append({"line": e["line"], "old_type": e["type"],
                            "new_type": with_direction(plain, d),
                            # The same entry labelled from its old verdict alone: used for
                            # the "before" week counts, so both runs use the current code.
                            "single_type": with_direction(plain, TO_DIRECTION[verdict]),
                            "verdict": verdict, "two_way": two_way, "relabel": relabel,
                            "heading": e["heading"], "summary": e["summary"]})
    return changes, no_verdict, already


def cmd_plan(root: Path, out_dir: Path, classifier: str, today: date) -> None:
    if out_dir.resolve().is_relative_to((root / "data").resolve()):
        out_error(f"--out-dir {out_dir} is inside data/; plan never writes there",
                  "out_dir_in_data")
    path = root / NETWORKING
    if not path.is_file():
        out_error(f"not found: {path}", "file_not_found")
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    entries = _entries(text)
    old = _load_classifier(classifier)
    changes, no_verdict, already = _plan_changes(entries, old)
    scope_line = format_line(SCOPE, len(entries), len(changes),
                             {"no verdict": no_verdict, "direction already recorded": already})
    new_text = _relabel(text, changes)

    acceptance, interim = [], []
    totals = {"replies": {"before": 0, "after": 0}, "sends": {"before": 0, "after": 0}}
    for a, b in zip(entries, _entries(new_text)):
        before, after = old(a["type"], a["summary"]), classify_type(b["type"])
        totals["replies"]["before"] += before == "reply"
        totals["sends"]["before"] += before == "send"
        totals["replies"]["after"] += after in REPLY_VERDICTS
        totals["sends"]["after"] += after in SEND_VERDICTS
        row = {"line": a["line"], "before": before, "new_type": b["type"],
               "summary": a["summary"]}
        if (before, after) not in ALLOWED:
            acceptance.append({**row, "after": after})
        if old(b["type"], b["summary"]) != before:
            interim.append({**row, "after": old(b["type"], b["summary"])})

    plan = {"source": str(NETWORKING), "source_sha256": _sha(raw),
            "new_sha256": _sha(new_text.encode("utf-8")), "scope": scope_line,
            "changes": changes, "classifier": classifier, "totals": totals,
            "weeks": _weeks(root, _relabel(text, changes, "single_type"), new_text, today),
            "acceptance_diff": acceptance, "interim_diff": interim}
    plan_path = out_dir / "backfill-plan.json"
    review_path = out_dir / "backfill-review.md"
    write_atomic(plan_path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    write_atomic(review_path, _review(plan, plan_path))
    out_ok("plan", scope_line, changes=len(changes),
           two_way=sum(1 for c in changes if c["two_way"]),
           acceptance_changes=len(acceptance), interim_changes=len(interim),
           review=str(review_path), plan=str(plan_path))


def _cell(s) -> str:
    return str(s).replace("|", "/")


def _review(plan: dict, plan_path: Path) -> str:
    two_way = [c for c in plan["changes"] if c["two_way"]]
    t = plan["totals"]
    lines = [
        "# Interaction direction backfill: review", "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} by tools/networking_direction_backfill.py.",
        f"Source: {plan['source']} (sha256 {plan['source_sha256']}).", "",
        f"`{plan['scope']}`", "",
        f"Each change labels an entry with the verdict the old classifier gives it today. "
        f"Replies stay replies and sends stay sends; the {len(two_way)} two-way entries also "
        "count as a send. Nothing has been written to data/. To apply, Nick must approve this "
        "file, and then:", "",
        f"    PYTHONIOENCODING=utf-8 python3 tools/networking_direction_backfill.py "
        f"--repo-root . apply --plan {plan_path} --classifier {plan['classifier']} --approved",
        "",
        "## Proposed changes", "",
        "| Line | Old type | New type | Verdict | Summary (first 100 chars) |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {c['line']} | {_cell(c['old_type'])} | {_cell(c['new_type'])} | "
              f"{'two-way' if c['two_way'] else c['verdict']} | {_cell(c['summary'][:100])} |"
              for c in plan["changes"]]
    lines += ["", "## Two-way", "",
              "Replies whose summary also records a message from Nick (SENT, an outbound "
              "opener, or Nick/I sent|replied|emailed|texted|messaged). Strike any line that "
              "is not a message each way, and it is labelled (inbound) instead. Lines whose "
              "old type already says (inbound) are the 14:02 relabel.", "",
              "| Line | Old type | New type | Summary |", "| --- | --- | --- | --- |"]
    lines += [f"| {c['line']} | {_cell(c['old_type'])} | {_cell(c['new_type'])} | "
              f"{_cell(c['summary'])} |" for c in two_way]
    lines += _diff_section(
        "Acceptance: the current classifier on the backfilled file",
        plan["acceptance_diff"],
        "None: every reply is still a reply (or two-way) and every send still a send, under "
        f"the current tools/reply_classify.py against {plan['classifier']}. This must be "
        "empty before applying.",
        "These entries change verdict beyond reply -> two-way. Do not apply:")
    lines += ["", "## Effect on the morning page", "",
              f"Whole file: replies {t['replies']['before']} -> {t['replies']['after']}, "
              f"sends {t['sends']['before']} -> {t['sends']['after']}.", "",
              "day_check week counts (Monday to the date shown):", "",
              "| Week of | Through | Replies before | Replies after | Sends before | Sends after |",
              "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {w['week_start']} | {w['through']} | {w['replies_before']} | "
              f"{w['replies_after']} | {w['sends_before']} | {w['sends_after']} |"
              for w in plan["weeks"]]
    lines += _diff_section(
        "Interim effect if the backfill lands before the new classifier",
        plan["interim_diff"],
        f"None: {plan['classifier']} gives every entry the same verdict on the backfilled file.",
        f"Between applying the backfill and switching to the new classifier (fast-forwarding "
        f"the main checkout), the old code ({plan['classifier']}) reads "
        f"{len(plan['interim_diff'])} entries differently:")
    return "\n".join(lines) + "\n"


def _diff_section(title: str, rows: list[dict], empty: str, lead: str) -> list[str]:
    out = ["", f"## {title}", ""]
    if not rows:
        return out + [empty]
    out += [lead, "", "| Line | Before | After | New type | Summary (first 100 chars) |",
            "| --- | --- | --- | --- | --- |"]
    return out + [f"| {d['line']} | {d['before']} | {d['after']} | {_cell(d['new_type'])} | "
                  f"{_cell(d['summary'][:100])} |" for d in rows]


def cmd_apply(root: Path, plan_path: Path, classifier: str, approved: bool) -> None:
    if not approved:
        out_error("apply needs --approved (Nick's approval of the review file)", "not_approved")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    path = root / NETWORKING
    raw = path.read_bytes()
    if plan["acceptance_diff"]:
        out_error("the plan changes verdicts under the current classifier", "acceptance_failed")
    if _sha(raw) != plan["source_sha256"]:
        out_error(f"{path} changed after the plan was made; run plan again", "sha_mismatch")
    text = raw.decode("utf-8")
    recomputed, _, _ = _plan_changes(_entries(text), _load_classifier(classifier))
    if recomputed != plan["changes"]:
        out_error("the plan file differs from the plan recomputed from the file and the "
                  "classifier; run plan again", "plan_mismatch")
    new_text = _relabel(text, plan["changes"])
    if _sha(new_text.encode("utf-8")) != plan["new_sha256"]:
        out_error("the relabelled file does not match the plan", "result_mismatch")
    backup = BACKUPS / f"networking.md.{datetime.now():%Y%m%d-%H%M%S}.bak"
    (root / backup).parent.mkdir(parents=True, exist_ok=True)
    (root / backup).write_bytes(raw)
    write_atomic(path, new_text)
    written = _sha(path.read_bytes())
    if written != plan["new_sha256"]:
        out_error(f"written file sha {written} != planned; restore from {backup}",
                  "write_mismatch", backup=str(backup))
    out_ok("apply", f"relabelled {len(plan['changes'])} of the planned headings",
           backup=str(backup), new_sha256=written)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--repo-root", default=".")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("--out-dir", default=str(Path("output") / "reply-direction"))
    pl.add_argument("--classifier", required=True,
                    help="file holding the summary-reading reply_classify.py "
                         "(git show dfccc00:tools/reply_classify.py)")
    pl.add_argument("--today", type=date.fromisoformat, default=None,
                    help="the date the week counts end on (default: today)")
    ap = sub.add_parser("apply")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--classifier", required=True,
                    help="the same old classifier file the plan was made with")
    ap.add_argument("--approved", action="store_true")
    args = p.parse_args(argv)
    root = Path(args.repo_root).resolve()
    if args.cmd == "plan":
        out_dir = Path(args.out_dir)
        cmd_plan(root, out_dir if out_dir.is_absolute() else root / out_dir,
                 args.classifier, args.today or date.today())
    else:
        cmd_apply(root, Path(args.plan), args.classifier, args.approved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
