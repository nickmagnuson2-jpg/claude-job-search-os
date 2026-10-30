#!/usr/bin/env python3
"""Log a recurring job-lead source check in ONE action.

Marking a source check used to take three hand-writes: append the result to
data/notes.md, close the recurring todo row, recreate it with the next due
date. The third step is the fragile one -- closing a recurring row WITHOUT
recreating it silently retires a lead source, which already happened once
(2026-09-02, caught by hand).

How `mark` works now (spec output/analysis/090826-source-check-upstream-rotate-spec.md):
  1. Rotate the todo row with ONE call to `todo_write.py rotate`: one locked
     read-modify-write, one atomic write, verified against the re-read file before it
     reports ok. There is no state in which the row is closed and its successor is not.
     `--expect-due` makes it a compare-and-swap: if anything changed the row's Due since
     this script read it, nothing is written.
  2. Then log the note, atomically (temp file + rename).
  Both steps carry an operation id `<source-id>:<date>:<hex>`: in the new row's Notes and
  as an HTML comment on the note. A retried `mark` for the same source and date finds the
  id and skips whichever step already landed, so a retry never rotates twice or logs twice.

What this file does NOT own any more: parsing the todo table. "Is the row live" is
todo_write.active_rows(), which reads the ## Active section only (37.F1: the old local
parser scanned the whole file, so a Pending row stranded under ## Completed read as live).

Known residual (spec section 6, Nick's decision, not built): nothing stops another caller
from running `todo_write.py done` on a recurring row. `due` reports that afterwards as
RETIRED; it does not prevent it.

Registry lives in data/source-checks.json (gitignored -- entries name real
people). This file carries no names.

Usage:
  source_check.py due  [--repo-root .] [--target-date YYYY-MM-DD] [--json]
  source_check.py mark <source-id> [--none | --found "<what turned up>"]
                                   [--repo-root .] [--date YYYY-MM-DD]
                                   [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from md_table import write_atomic  # noqa: E402
from todo_write import active_rows  # noqa: E402

REGISTRY_REL = "data/source-checks.json"
NOTES_REL = "data/notes.md"
TODOS_REL = "data/job-todos.md"
NOTES_ANCHOR = "## Notes"
PRIORITIES = ("High", "Med", "Low")
TODO_WRITE = Path(__file__).resolve().parent / "todo_write.py"


class SourceCheckError(Exception):
    """Fatal, with a message meant for the operator."""


def _today(target_date: str | None) -> _dt.date:
    if target_date:
        return _dt.date.fromisoformat(target_date)
    return _dt.date.today()


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def load_registry(repo_root: Path) -> list[dict]:
    """The registry, validated as a schema (37.F6), or SourceCheckError."""
    path = repo_root / REGISTRY_REL
    if not path.exists():
        raise SourceCheckError(
            f"registry not found: {path}. It is gitignored by design "
            "(entries name real people; this file is public and must stay "
            "name-free). Rebuild it from the schema enforced in load_registry "
            "below, never by pasting a filled-in example into this file."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceCheckError(f"registry is not valid JSON: {exc}") from exc
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, list) or not sources:
        raise SourceCheckError("registry has no 'sources' list, or it is empty")
    ids, fragments = set(), set()
    for n, src in enumerate(sources):
        if not isinstance(src, dict):
            raise SourceCheckError(f"registry entry #{n} is not an object")
        name = src.get("id", f"#{n}")
        for key in ("id", "label", "todo_fragment", "note_label"):
            if key not in src:
                raise SourceCheckError(f"registry entry {name!r} is missing {key!r}")
            if not _nonempty_str(src[key]):
                raise SourceCheckError(f"registry entry {name!r}: {key!r} must be a nonempty string")
        if "cadence_days" not in src:
            raise SourceCheckError(f"registry entry {name!r} is missing 'cadence_days'")
        cad = src["cadence_days"]
        if type(cad) is not int or cad <= 0:  # bool is an int subclass; refuse it too
            raise SourceCheckError(
                f"registry entry {name!r}: 'cadence_days' must be a positive integer, got {cad!r}")
        if "priority" in src and src["priority"] not in PRIORITIES:
            raise SourceCheckError(
                f"registry entry {name!r}: 'priority' must be one of {PRIORITIES}, "
                f"got {src['priority']!r}")
        if src["id"] in ids:
            raise SourceCheckError(f"registry has two entries with id {src['id']!r}")
        frag = src["todo_fragment"].lower()
        if frag in fragments:
            raise SourceCheckError(
                f"registry has two entries with todo_fragment {src['todo_fragment']!r}")
        ids.add(src["id"])
        fragments.add(frag)
    return sources


def get_source(sources: list[dict], source_id: str) -> dict:
    for src in sources:
        if src["id"] == source_id:
            return src
    known = ", ".join(s["id"] for s in sources)
    raise SourceCheckError(f"unknown source id {source_id!r}. Known: {known}")


def live_pending_rows(todos_text: str, fragment: str) -> list[list[str]]:
    """Every live Pending row whose Task contains `fragment`.

    Live means under ## Active (todo_write.active_rows); a Pending row stranded under
    ## Completed is NOT live and is not returned.
    """
    return [cols for _, cols in active_rows(todos_text.splitlines())
            if len(cols) > 3 and fragment.lower() in cols[0].lower()
            and cols[3].strip() == "Pending"]


def find_pending_row(todos_text: str, fragment: str) -> list[str] | None:
    """The first live Pending row whose Task contains `fragment`, or None."""
    rows = live_pending_rows(todos_text, fragment)
    return rows[0] if rows else None


def compute_due(sources: list[dict], todos_text: str, today: _dt.date) -> list[dict]:
    out = []
    for src in sources:
        row = find_pending_row(todos_text, src["todo_fragment"])
        entry = {
            "id": src["id"],
            "label": src["label"],
            "present": row is not None,
            "due": None,
            "days_overdue": None,
            "is_due": False,
        }
        if row is None:
            # A registered source with no live Pending row is the exact failure this
            # tool exists to prevent: the lead source has been retired.
            entry["retired"] = True
            entry["is_due"] = True
            out.append(entry)
            continue
        entry["retired"] = False
        raw_due = row[2].strip()
        if raw_due and raw_due not in {"—", "-", "--"}:
            try:
                due = _dt.date.fromisoformat(raw_due)
                entry["due"] = due.isoformat()
                entry["days_overdue"] = (today - due).days
                entry["is_due"] = today >= due
            except ValueError:
                entry["due"] = raw_due
                entry["is_due"] = True
        else:
            # No due date on a recurring row -- treat as due so it cannot hide.
            entry["is_due"] = True
        out.append(entry)
    return out


def build_note(src: dict, today: _dt.date, found: str | None) -> str:
    label = src["note_label"]
    if found:
        body = f"Checked the {label} ({today.isoformat()}): {found}"
    else:
        body = (
            f"Checked the {label} ({today.isoformat()}) and there were no "
            "available roles on it."
        )
    return f"**{today.isoformat()}:** {body}"


def op_prefix(src: dict, today: _dt.date) -> str:
    """`<source-id>:<date>:` -- what a retry for the same check searches for."""
    return f"{src['id']}:{today.isoformat()}:"


def note_marker(op_id: str) -> str:
    return f"<!-- source-check:{op_id} -->"


def note_already_logged(notes_text: str, src: dict, today: _dt.date) -> bool:
    return f"<!-- source-check:{op_prefix(src, today)}" in notes_text


def prepend_note(notes_path: Path, note: str) -> None:
    """Insert `note` as the newest entry under the Notes header, atomically."""
    text = notes_path.read_text(encoding="utf-8")
    if NOTES_ANCHOR not in text:
        raise SourceCheckError(
            f"{notes_path} has no '{NOTES_ANCHOR}' header; refusing to guess "
            "where the newest entry goes."
        )
    head, sep, tail = text.partition(NOTES_ANCHOR)
    tail_stripped = tail.lstrip("\n")
    write_atomic(notes_path, head + sep + "\n\n" + note + "\n\n" + tail_stripped)


def _run_todo(repo_root: Path, args: list[str]) -> dict:
    """Run the sibling todo_write.py against `repo_root`; its last JSON line, or an error."""
    cmd = [sys.executable, str(TODO_WRITE), *args, "--repo-root", str(repo_root)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(repo_root),
        env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"},
    )
    raw = (proc.stdout or "").strip()
    try:
        return json.loads(raw.splitlines()[-1]) if raw else {
            "status": "error", "message": proc.stderr.strip() or "no output"
        }
    except (json.JSONDecodeError, IndexError):
        return {"status": "error", "message": raw or proc.stderr.strip()}


def rotate_todo(repo_root: Path, src: dict, today: _dt.date, op_id: str) -> dict:
    """Close the recurring row and recreate it: ONE call to `todo_write.py rotate`.

    Returns {"closed", "next_due", "op_id", "rotated"}. When the live row already carries
    this source's op id for `today`, an earlier run rotated it: nothing is written and
    "rotated" is False.
    """
    fragment = src["todo_fragment"]
    todos_text = (repo_root / TODOS_REL).read_text(encoding="utf-8")
    rows = live_pending_rows(todos_text, fragment)
    if len(rows) > 1:
        # Checked here too, not only inside `todo_write.py rotate`: the retry path below
        # returns before rotate runs (review 1, Codex F1).
        raise SourceCheckError(
            f"{len(rows)} live Pending rows match {fragment!r}; exactly one is expected. "
            "Remove the duplicate before marking a check.")
    row = rows[0] if rows else None
    if row is None:
        raise SourceCheckError(
            f"no live Pending todo row matching {fragment!r}. The source may already "
            "have been retired -- run `source_check.py due` and recreate it "
            "before marking a check."
        )
    next_due = (today + _dt.timedelta(days=src["cadence_days"])).isoformat()
    done_before = re.search(re.escape(op_prefix(src, today)) + r"[0-9a-f]+", " | ".join(row[4:]))
    if done_before:
        return {"closed": row[0], "next_due": row[2].strip(), "op_id": done_before.group(0),
                "rotated": False}
    notes = (
        f"Recurring - check every {src['cadence_days']} days. "
        f"RECREATED {today.isoformat()} by tools/source_check.py after a logged check "
        f"(result in {NOTES_REL}). Closing a recurring row without recreating it "
        "silently retires a lead source."
    )
    res = _run_todo(repo_root, [
        "rotate", fragment, "--due", next_due, "--priority", src.get("priority", "High"),
        "--notes", notes, "--expect-due", row[2].strip(), "--op-id", op_id,
        "--closed-on", today.isoformat()])
    if res.get("status") != "ok":
        raise SourceCheckError(
            f"could not rotate the todo row; nothing was written to it: {res.get('message')}")
    return {"closed": row[0], "next_due": next_due, "op_id": op_id, "rotated": True}


def cmd_due(repo_root: Path, today: _dt.date, as_json: bool) -> int:
    sources = load_registry(repo_root)
    todos_text = (repo_root / TODOS_REL).read_text(encoding="utf-8")
    rows = compute_due(sources, todos_text, today)
    if as_json:
        print(json.dumps({"target_date": today.isoformat(), "sources": rows}, indent=2))
        return 0
    for r in rows:
        if r.get("retired"):
            print(f"RETIRED  {r['label']}: no Pending row -- lead source was dropped")
        elif r["is_due"]:
            od = r["days_overdue"]
            extra = f" ({od}d overdue)" if isinstance(od, int) and od > 0 else ""
            print(f"DUE      {r['label']}: due {r['due']}{extra}")
        else:
            print(f"ok       {r['label']}: due {r['due']}")
    return 0


def cmd_mark(
    repo_root: Path, source_id: str, today: _dt.date, found: str | None, dry_run: bool
) -> int:
    sources = load_registry(repo_root)
    src = get_source(sources, source_id)
    note = build_note(src, today, found)
    next_due = (today + _dt.timedelta(days=src["cadence_days"])).isoformat()
    if dry_run:
        print(json.dumps({
            "status": "dry-run", "source": src["id"], "note": note, "next_due": next_due,
        }, indent=2))
        return 0

    # Rotate first: it is the step that can fail meaningfully, and it is atomic and
    # verified. Once it has committed, the note records a committed fact.
    rot = rotate_todo(repo_root, src, today, op_prefix(src, today) + uuid.uuid4().hex[:6])
    notes_path = repo_root / NOTES_REL
    if note_already_logged(notes_path.read_text(encoding="utf-8"), src, today):
        note_status = "already-logged"
    else:
        prepend_note(notes_path, f"{note} {note_marker(rot['op_id'])}")
        note_status = "logged"
    print(json.dumps({
        "status": "ok", "action": "mark", "source": src["id"], "note": note,
        "note_status": note_status, "rotated": rot["rotated"],
        "next_due": rot["next_due"], "op_id": rot["op_id"],
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_due = sub.add_parser("due", help="which source checks are due")
    p_due.add_argument("--repo-root", dest="repo_root_sub", default=None)
    p_due.add_argument("--target-date")
    p_due.add_argument("--json", action="store_true")

    p_mark = sub.add_parser("mark", help="log a check: rotate the row, then the note")
    p_mark.add_argument("--repo-root", dest="repo_root_sub", default=None)
    p_mark.add_argument("source_id")
    p_mark.add_argument("--date")
    g = p_mark.add_mutually_exclusive_group()
    g.add_argument("--none", action="store_true", help="nothing available")
    g.add_argument("--found", help="what turned up")
    p_mark.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    # --repo-root is accepted on EITHER side of the subcommand. It was
    # top-level-only until 2026-09-08, when a live smoke test caught that the
    # documented `due --repo-root .` invocation died in argparse -- every unit
    # test had happened to pass the flag before the subcommand.
    repo_root = Path(getattr(args, "repo_root_sub", None) or args.repo_root).resolve()

    try:
        if args.cmd == "due":
            return cmd_due(repo_root, _today(args.target_date), args.json)
        if args.cmd == "mark":
            if not args.none and not args.found:
                raise SourceCheckError(
                    "say what happened: pass --none (nothing available) or "
                    '--found "<what turned up>"'
                )
            return cmd_mark(repo_root, args.source_id, _today(args.date),
                            args.found, args.dry_run)
        raise SourceCheckError(f"unknown subcommand {args.cmd!r}")
    except SourceCheckError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
