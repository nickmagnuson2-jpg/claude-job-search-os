#!/usr/bin/env python3
"""Log a recurring job-lead source check in ONE action.

Marking a source check used to take three hand-writes: append the result to
data/notes.md, close the recurring todo row, recreate it with the next due
date. The third step is the fragile one -- closing a recurring row WITHOUT
recreating it silently retires a lead source, which already happened once
(2026-09-02, caught by hand).

This script bundles close+recreate into one command and re-reads the file
afterwards. Both guarantees are WEAKER than they look -- see KNOWN DEFECTS. If the recreate fails, it
restores the closed row rather than leaving the source retired.

Registry lives in data/source-checks.json (gitignored -- entries name real
people). This file carries no names.

KNOWN DEFECTS (codex run 37, 2026-09-08, all verified, all PARKED by Nick):
  F1 P0  _active_rows ignores section boundaries; a stranded Pending row under
         ## Completed reads as live. The one case this tool exists to detect.
  F2 P1  close+recreate are two subprocesses, not one commit; a crash between
         them retires the source. "Indivisible" in this docstring's earlier
         version was WRONG.
  F4 P0  rotate_todo's re-read accepts ANY matching Pending row, so a no-op
         writer passes verification. The advertised safety net is decorative.
  F5 P2  note-first has no idempotency key; a retried mark double-logs.
  F6 P2  registry validation is key-presence only.
  Full spec for the upstream fix, and the reasoning:
  output/analysis/090826-source-check-upstream-rotate-spec.md

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
import subprocess
import sys
from pathlib import Path

REGISTRY_REL = "data/source-checks.json"
NOTES_REL = "data/notes.md"
TODOS_REL = "data/job-todos.md"
NOTES_ANCHOR = "## Notes"


class SourceCheckError(Exception):
    """Fatal, with a message meant for the operator."""


def _today(target_date: str | None) -> _dt.date:
    if target_date:
        return _dt.date.fromisoformat(target_date)
    return _dt.date.today()


def load_registry(repo_root: Path) -> list[dict]:
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
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SourceCheckError("registry has no 'sources' list, or it is empty")
    for src in sources:
        for key in ("id", "label", "todo_fragment", "cadence_days", "note_label"):
            if key not in src:
                raise SourceCheckError(
                    f"registry entry {src.get('id', '<no id>')!r} is missing {key!r}"
                )
    return sources


def get_source(sources: list[dict], source_id: str) -> dict:
    for src in sources:
        if src["id"] == source_id:
            return src
    known = ", ".join(s["id"] for s in sources)
    raise SourceCheckError(f"unknown source id {source_id!r}. Known: {known}")


def _active_rows(todos_text: str) -> list[list[str]]:
    """Every five-column data row in the file, as column lists.

    NOTE the name and the original docstring both lied: this does NOT scope to
    the ## Active section, it scans the whole document. That is finding F1 of
    codex run 37 (P0, reproduced) -- a five-column Pending row stranded under
    ## Completed is returned as live, so `due` reports retired=False for a
    source that is actually gone. todo_write.py carries find_misfiled() because
    that shape occurs here. Fix is section-aware parsing, spec 3.4.
    """
    rows = []
    for line in todos_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith("|--"):
            continue
        cols = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cols) < 5:
            continue
        if cols[0] == "Task":
            continue
        rows.append(cols)
    return rows


def find_pending_row(todos_text: str, fragment: str) -> list[str] | None:
    """The Pending row whose Task contains `fragment`, or None.

    Matches only Status == 'Pending' so a Completed/Withdrawn row with the
    same text is never mistaken for a live one.
    """
    for cols in _active_rows(todos_text):
        if fragment.lower() in cols[0].lower() and cols[3].strip() == "Pending":
            return cols
    return None


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
            # A registered source with no Pending row is the exact failure this
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


def prepend_note(notes_path: Path, note: str) -> None:
    """Insert `note` as the newest entry under the Notes header."""
    text = notes_path.read_text(encoding="utf-8")
    if NOTES_ANCHOR not in text:
        raise SourceCheckError(
            f"{notes_path} has no '{NOTES_ANCHOR}' header; refusing to guess "
            "where the newest entry goes."
        )
    head, sep, tail = text.partition(NOTES_ANCHOR)
    tail_stripped = tail.lstrip("\n")
    new_tail = "\n\n" + note + "\n\n" + tail_stripped
    notes_path.write_text(head + sep + new_tail, encoding="utf-8")


def _run_todo(repo_root: Path, args: list[str]) -> dict:
    cmd = [sys.executable, str(repo_root / "tools" / "todo_write.py"), *args]
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


def rotate_todo(repo_root: Path, src: dict, today: _dt.date, note: str) -> dict:
    """Close the recurring row and recreate it. Never one without the other.

    Verifies the recreate by re-reading the file. On failure, restores the
    row that was closed so the source is not silently retired.
    """
    todos_path = repo_root / TODOS_REL
    fragment = src["todo_fragment"]
    before = todos_path.read_text(encoding="utf-8")
    original = find_pending_row(before, fragment)
    if original is None:
        raise SourceCheckError(
            f"no Pending todo row matching {fragment!r}. The source may already "
            "have been retired -- run `source_check.py due` and recreate it "
            "before marking a check."
        )

    next_due = (today + _dt.timedelta(days=int(src["cadence_days"]))).isoformat()
    priority = src.get("priority", "High")
    new_notes = (
        f"Recurring - check every {src['cadence_days']} days. "
        f"RECREATED {today.isoformat()} by tools/source_check.py after a logged check "
        f"(result in {NOTES_REL}). Closing a recurring row without recreating it "
        "silently retires a lead source."
    )

    done_res = _run_todo(repo_root, ["done", fragment])
    if done_res.get("status") != "ok":
        raise SourceCheckError(f"could not close the todo row: {done_res.get('message')}")

    add_res = _run_todo(repo_root, ["add", original[0], priority, next_due, new_notes])
    after = todos_path.read_text(encoding="utf-8")
    recreated = find_pending_row(after, fragment)
    if add_res.get("status") != "ok" or recreated is None:
        # Restore rather than leave the source retired.
        todos_path.write_text(before, encoding="utf-8")
        raise SourceCheckError(
            "recreate FAILED after the close succeeded; restored the original "
            f"todo table. The source is NOT retired. Cause: {add_res.get('message')}"
        )
    return {"closed": original[0], "next_due": next_due}


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
    if dry_run:
        print(json.dumps({
            "status": "dry-run", "source": src["id"], "note": note,
            "next_due": (today + _dt.timedelta(days=int(src["cadence_days"]))).isoformat(),
        }, indent=2))
        return 0

    prepend_note(repo_root / NOTES_REL, note)
    try:
        rot = rotate_todo(repo_root, src, today, note)
    except SourceCheckError:
        # The note is already logged; that is harmless and true. Re-raise so the
        # todo failure is loud rather than swallowed.
        raise
    print(json.dumps({
        "status": "ok", "action": "mark", "source": src["id"],
        "note": note, "next_due": rot["next_due"],
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

    p_mark = sub.add_parser("mark", help="log a check: note + close + recreate")
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
