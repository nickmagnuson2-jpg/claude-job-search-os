#!/usr/bin/env python3
"""decision_write.py -- the question queue: route each open question for Nick to the window
of the session that will act on it.

Why this exists: approvals do not relay between sessions (Nick, 2026-11-05), so when the
organizer collects an answer another session will act on, Nick says it twice or the relayed
answer goes stale. Three fires on 2026-11-06. Nick: "I don't really want to have to say things
twice to the organizer and the child agent." This is part 1 of the run manifest (workstream
overnight-swarm). Spec and threat model: output/question-queue-1106/110626-spec-threat-model.md.

What it holds and what it does not:
  - OPEN questions, as append-only events in data/run-manifest/questions.jsonl. A question is
    open when it has an "asked" event and no "resolved" one.
  - Rulings live ONLY in the owner's workstream file, as one line:
        RULING Q12 | option: b | reason: <why> | window: <session Nick answered in>
    `resolve` reads that line and binds to it by hash; it never takes the answer as a flag.
  - The organizer's prediction, committed at `add`, lives in data/run-manifest/predictions.jsonl.
    Only `resolve` opens that file, after the ruling is checked, so no listing can show it
    while the question is open (the blind log the trust ladder's R1 needs; D6 decides the
    outcome: a different option is a correction, the only outcome that counts).
  - Big items (spend of $5 or more, live data, guarded edits, pushes, waivers) are ROUTED,
    never approved: every "resolved" event carries approval "none". The gates that approve
    (Touch ID on push, guarded-edit approval in the editing window) are unchanged.
    approval_payload() is the exact v2 signing payload; nothing signs it in v1.

Every path comes from the MAIN checkout, so a worktree never reads or writes its own copy of
the queue, the workstream files or the board.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/decision_write.py add --owner <workstream|org> \
      --class <class> --question "..." --option "a: ..." --option "b: ..." --acts "..." \
      --predict a --predict-reason "..." [--usd N|unknown] [--commitment] [--by <session>]
  PYTHONIOENCODING=utf-8 python3 tools/decision_write.py list [--owner X] [--json]
  PYTHONIOENCODING=utf-8 python3 tools/decision_write.py resolve Q12 [--same-reason|--new-reason]
  PYTHONIOENCODING=utf-8 python3 tools/decision_write.py takeover --org <session>
  PYTHONIOENCODING=utf-8 python3 tools/decision_write.py check
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inbox_lock  # noqa: E402
from vault_paths import main_checkout  # noqa: E402

# Decision classes, codified (Nick, 2026-11-07: "The decision class should be codified here").
# A new class is a reviewed code change; there is no catch-all.
CLASSES = {
    "spend": "spend",              # big or design by amount, see kind_of()
    "live-data-write": "big",
    "guarded-edit": "big",
    "push": "big",
    "waiver": "big",
    "scope": "design",
    "design-shape": "design",
    "order": "design",
    "assignment": "design",
    "workstream-status": "design",
    "finding-rule": "design",
    "fact-check": "design",
}
SPEND_THRESHOLD_USD = 5.0          # Nick, 2026-11-07: the total a decision commits
ORG = "org"
ORG_FILE = "overnight-swarm"       # the organizer's rulings live in its own workstream
SIGNING_NAMESPACE = "decision-approval"   # v2: approval_signing namespace for big items

QUESTIONS_REL = Path("data/run-manifest/questions.jsonl")
PREDICTIONS_REL = Path("data/run-manifest/predictions.jsonl")
WORKSTREAMS_REL = Path("data/workstreams")
BOARD_REL = Path("output/overnight-merge-back/SESSIONS.md")

OPTION_RE = re.compile(r"^([A-Za-z0-9]+):\s*(\S.*)$")
RULING_RE = re.compile(r"^RULING (Q\d+) \| option: *(\S+) *\| reason: *(.*?) *\| window: *(.*?) *$")


class Refusal(Exception):
    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.message = message
        self.extra = extra


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha(data) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- paths
def resolve_main(root: Path) -> Path:
    """The main checkout for `root`. main_checkout() falls back to its input, so refuse unless
    the result holds a .git DIRECTORY: a worktree must never use its own copies."""
    main = main_checkout(Path(root))
    if not (main / ".git").is_dir():
        raise Refusal("no_main_checkout", f"{root} is not a main checkout or a worktree of one")
    return main


def paths(main: Path) -> dict:
    return {"questions": main / QUESTIONS_REL, "predictions": main / PREDICTIONS_REL,
            "workstreams": main / WORKSTREAMS_REL, "board": main / BOARD_REL}


def owner_file(p: dict, owner: str) -> Path:
    """The owner's workstream file, refused if it resolves outside data/workstreams."""
    f = p["workstreams"] / f"{ORG_FILE if owner == ORG else owner}.md"
    if not f.resolve().is_relative_to(p["workstreams"].resolve()):
        raise Refusal("owner_file_outside", f"{f} resolves outside {p['workstreams']}")
    return f


# ---------------------------------------------------------------- the event file
def read_lines(path: Path) -> tuple[list, list]:
    """(events, problems). A line that is not a JSON object is a problem, never skipped."""
    if not path.exists():
        return [], []
    events, problems = [], []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            problems.append(f"{path.name} line {n}: not JSON")
            continue
        if not isinstance(ev, dict):
            problems.append(f"{path.name} line {n}: not an object")
            continue
        events.append(ev)
    return events, problems


def load_events(path: Path) -> list:
    events, problems = read_lines(path)
    if problems:
        raise Refusal("corrupt_queue", f"{problems[0]}; run `check`")
    return events


def open_questions(events: list) -> dict:
    asked = {e["id"]: e for e in events if e.get("type") == "asked"}
    done = {e["id"] for e in events if e.get("type") == "resolved"}
    return {i: q for i, q in asked.items() if i not in done}


def append(path: Path, row: dict) -> None:
    line = json.dumps(row, ensure_ascii=False) + "\n"      # atomic_update creates the folder
    inbox_lock.atomic_update(path, lambda text: text + line)


# ---------------------------------------------------------------- hashes and the v2 seam
BOUND = ("id", "owner", "class", "question", "options", "acts", "usd")


def question_hash(q: dict) -> str:
    return sha(canonical({k: q.get(k) for k in BOUND}))


def prediction_hash(pred: dict) -> str:
    return sha(canonical({k: pred.get(k) for k in ("id", "option", "reason", "question_hash")}))


def approval_payload(q: dict, ruling: dict) -> bytes:
    """The exact bytes a v2 Touch ID signature would cover (namespace SIGNING_NAMESPACE)."""
    return canonical({"question_hash": q["question_hash"], "option": ruling["option"],
                      "owner": q["owner"], "window": ruling["window"]})


# ---------------------------------------------------------------- the board
def session_name(cell: str) -> str:
    return re.split(r"[\s(\[]", cell.replace("*", "").strip(), maxsplit=1)[0]


def cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def read_board(path: Path):
    """[(first cell, session, workstreams)] from the first table whose header has a 'Workstreams'
    cell, or None when the board or that table is missing. The table is the header, a separator
    line, then consecutive '|' rows; a row with the wrong cell count is skipped."""
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if "Workstreams" in cells(line)), None)
    if start is None or start + 1 >= len(lines) or not re.fullmatch(r"[|\-: ]+", lines[start + 1].strip()):
        return None
    header, rows = cells(lines[start]), []
    col = header.index("Workstreams")
    for line in lines[start + 2:]:
        if not line.strip().startswith("|"):
            break
        c = cells(line)
        if len(c) == len(header):
            rows.append((c[0], session_name(c[0]),
                         [w.strip() for w in c[col].split(",") if w.strip() not in ("", "-")]))
    return rows


def routing(p: dict):
    """THE ownership lookup: (owner -> route, source status). Every route shown anywhere comes from
    here, so moving ownership off the board (to a workstream Owner row, if Nick supersedes R7) is
    a change to this one function."""
    board = read_board(p["board"])
    return (lambda owner: route(owner, board)), ("missing" if board is None else "ok")


def route(owner: str, board) -> str:
    if board is None:
        return "UNROUTED: no board"
    hits = [s for _, s, ws in board if (s == ORG if owner == ORG else owner in ws)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return f"UNROUTED: no session holds {owner}"
    return f"UNROUTED: ambiguous ({', '.join(hits)})"


# ---------------------------------------------------------------- views
def public_view(q: dict) -> dict:
    """The only form in which a question is ever printed. No prediction field exists here."""
    return {k: q.get(k) for k in ("id", "owner", "class", "kind", "question", "options", "acts",
                                  "usd", "at", "by")}


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def norm(text: str) -> str:
    return " ".join(text.lower().split())


# ---------------------------------------------------------------- commands
def kind_of(cls: str, usd, commitment: bool) -> str:
    if CLASSES[cls] != "spend":
        return CLASSES[cls]
    return "big" if (usd is None or commitment or usd >= SPEND_THRESHOLD_USD) else "design"


def parse_usd(cls: str, raw):
    if cls != "spend":
        if raw is not None:
            raise Refusal("usd_not_spend", "--usd applies only to class spend")
        return None
    if raw is None:
        raise Refusal("usd_required", "a spend question needs --usd <total> or --usd unknown")
    if raw == "unknown":
        return None
    try:
        usd = float(raw)
    except ValueError:
        raise Refusal("bad_usd", f"--usd must be a number or 'unknown', got {raw!r}") from None
    if usd < 0:
        raise Refusal("bad_usd", "--usd cannot be negative")
    return usd


def cmd_add(p: dict, a) -> dict:
    owner = a.owner
    if owner != ORG and not owner_file(p, owner).is_file():
        raise Refusal("unknown_owner", f"no workstream file for {owner!r}; owner is a workstream or 'org'")
    if a.cls not in CLASSES:
        raise Refusal("unknown_class", f"{a.cls!r} is not a codified class: {', '.join(CLASSES)}")
    for name, val in (("question", a.question), ("acts", a.acts), ("predict-reason", a.predict_reason)):
        if not val.strip():
            raise Refusal("blank_field", f"--{name} is blank")
    labels = []
    for opt in a.option:
        m = OPTION_RE.match(opt.strip())
        if not m:
            raise Refusal("bad_option", f"an option is '<label>: <text>', got {opt!r}")
        labels.append(m.group(1))
    if len(labels) < 2:
        raise Refusal("too_few_options", "a question needs at least two options (Claim 143)")
    if len(set(labels)) != len(labels):
        raise Refusal("duplicate_option", "option labels must be distinct")
    if a.predict not in labels:
        raise Refusal("bad_prediction", f"--predict must be one of {labels}")
    usd = parse_usd(a.cls, a.usd)

    def build(events: list) -> tuple[dict, dict]:
        for q in open_questions(events).values():
            if q["owner"] == owner and norm(q["question"]) == norm(a.question):
                raise Refusal("duplicate_question", f"{q['id']} already asks this", existing=q["id"])
        n = 1 + max([int(e["id"][1:]) for e in events if e.get("type") == "asked"], default=0)
        q = {"type": "asked", "id": f"Q{n}", "at": now(), "by": a.by, "owner": owner,
             "class": a.cls, "kind": kind_of(a.cls, usd, a.commitment), "question": a.question.strip(),
             "options": [o.strip() for o in a.option], "acts": a.acts.strip(), "usd": usd}
        q["question_hash"] = question_hash(q)
        pred = {"id": q["id"], "at": q["at"], "option": a.predict, "reason": a.predict_reason.strip(),
                "blind": True, "question_hash": q["question_hash"]}
        q["prediction_hash"] = prediction_hash(pred)
        return q, pred

    if a.dry_run:
        q, _ = build(load_events(p["questions"]))
        return {"status": "ok", "dry_run": True, "question": public_view(q)}
    with inbox_lock.file_lock(p["questions"]):
        q, pred = build(load_events(p["questions"]))
        append(p["predictions"], pred)          # first, so an asked event never lacks one
        append(p["questions"], q)
    return {"status": "ok", "question": public_view(q)}


def listing(p: dict, owner=None) -> tuple[list, str]:
    events = load_events(p["questions"])
    to, status = routing(p)
    out = [dict(public_view(q), route=to(q["owner"])) for q in open_questions(events).values()
           if not owner or q["owner"] == owner]
    return out, status


def cmd_list(p: dict, a) -> None:
    qs, board = listing(p, a.owner)
    if a.json:
        emit({"status": "ok", "board": board, "open": qs})
        return
    qs.sort(key=lambda q: not q["route"].startswith("UNROUTED"))
    for q in qs:
        where = (q["route"] if q["route"].startswith("UNROUTED")
                 else f"answer in {q['route']}'s window" + (f" only (big: {q['class']})"
                                                            if q["kind"] == "big" else ""))
        print(f"{q['id']} [{q['class']}] {q['question']} -> {where}")
        for opt in q["options"]:
            print(f"    {opt}")
    print(f"{len(qs)} open question(s); board {board}")


def find_ruling(p: dict, q: dict) -> dict:
    f = owner_file(p, q["owner"])
    lines = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
    hits = [(n, l) for n, l in enumerate(lines, 1) if l.startswith(f"RULING {q['id']} |")]
    if not hits:
        raise Refusal("no_ruling", f"no 'RULING {q['id']} |' line in {f}")
    if len(hits) > 1:
        raise Refusal("ambiguous_ruling", f"{len(hits)} 'RULING {q['id']}' lines in {f}")
    n, line = hits[0]
    m = RULING_RE.match(line)
    labels = [OPTION_RE.match(o).group(1) for o in q["options"]]
    if not m or m.group(2) not in labels or not m.group(3) or not m.group(4):
        raise Refusal("bad_ruling", f"{f}:{n} needs 'RULING {q['id']} | option: <one of {labels}> "
                                    "| reason: <why> | window: <session>'")
    return {"file": str(f), "line": n, "line_hash": sha(line), "option": m.group(2),
            "reason": m.group(3), "window": m.group(4)}


def find_prediction(p: dict, q: dict) -> dict:
    preds, _ = read_lines(p["predictions"])
    for pred in preds:
        if pred.get("id") == q["id"]:
            if prediction_hash(pred) != q.get("prediction_hash"):
                raise Refusal("prediction_changed", f"{q['id']}: the prediction changed after it was made")
            return pred
    raise Refusal("prediction_missing", f"{q['id']}: no prediction recorded")


def cmd_resolve(p: dict, a) -> dict:
    with inbox_lock.file_lock(p["questions"]):
        events = load_events(p["questions"])
        asked = {e["id"]: e for e in events if e.get("type") == "asked"}
        if a.id not in asked:
            raise Refusal("unknown_question", f"no question {a.id}")
        q = open_questions(events).get(a.id)
        if q is None:
            raise Refusal("already_resolved", f"{a.id} is already resolved")
        if question_hash(q) != q["question_hash"]:
            raise Refusal("question_changed", f"{a.id} was edited after it was asked")
        ruling = find_ruling(p, q)
        pred = find_prediction(p, q)
        if ruling["option"] != pred["option"]:
            if a.same_reason or a.new_reason:
                raise Refusal("reason_flag_on_correction", "a different option is a correction")
            outcome = "correction"
        elif a.same_reason:
            outcome = "agree"
        elif a.new_reason:
            outcome = "amendment"
        else:
            raise Refusal("reason_comparison_required",
                          "same option as predicted: pass --same-reason or --new-reason (D6)")
        payload = approval_payload(q, ruling)
        append(p["questions"], {"type": "resolved", "id": a.id, "at": now(), "option": ruling["option"],
                                "reason": ruling["reason"], "window": ruling["window"],
                                "file": ruling["file"], "line": ruling["line"],
                                "line_hash": ruling["line_hash"], "outcome": outcome,
                                "approval": "none", "payload_hash": sha(payload), "signature": None})
    note = (f"routed only, not an approval: act only under the existing gate ({q['class']})"
            if q["kind"] == "big" else "design call recorded")
    return {"status": "ok", "summary": f"{a.id} resolved: {note}", "question": public_view(q),
            "ruling": ruling, "prediction": {"option": pred["option"], "reason": pred["reason"]},
            "outcome": outcome, "approval": "none"}


def org_names(first_cell: str) -> set:
    return {session_name(first_cell), *re.findall(r"[(\[]([^)\]]+)[)\]]", first_cell)}


def cmd_takeover(p: dict, a) -> dict:
    board = read_board(p["board"]) or []
    org_rows = [c for c, s, _ in board if s == ORG]
    if len(org_rows) != 1 or a.org not in org_names(org_rows[0]):
        raise Refusal("board_disagrees", f"the board's org row does not name {a.org!r}; update it first")
    qs, _ = listing(p)                     # refuses a corrupt queue before anything is written
    append(p["questions"], {"type": "takeover", "id": None, "at": now(), "org": a.org})
    return {"status": "ok", "org": a.org,
            "org_questions": [q for q in qs if q["owner"] == ORG],
            "unrouted": [q for q in qs if q["route"].startswith("UNROUTED")]}


def cmd_check(p: dict) -> tuple[dict, int]:
    events, problems = read_lines(p["questions"])
    preds, pred_problems = read_lines(p["predictions"])
    problems += pred_problems
    by_id = {}
    for e in events:
        if e.get("type") == "asked":
            if e["id"] in by_id:
                problems.append(f"duplicate id {e['id']}")
            by_id[e["id"]] = e
    pred_by_id = {x.get("id"): x for x in preds}
    for i, q in by_id.items():
        if question_hash(q) != q.get("question_hash"):
            problems.append(f"{i}: question changed after it was asked")
        pred = pred_by_id.get(i)
        if pred is None:
            problems.append(f"{i}: prediction missing")
        elif prediction_hash(pred) != q.get("prediction_hash"):
            problems.append(f"{i}: prediction changed after it was made")
    for e in events:
        if e.get("type") != "resolved":
            continue
        f = Path(e["file"])
        lines = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
        if not any(l.startswith(f"RULING {e['id']} |") and sha(l) == e["line_hash"] for l in lines):
            problems.append(f"{e['id']}: ruling line changed or missing in {f}")
    return {"status": "ok" if not problems else "problems", "problems": problems}, (1 if problems else 0)


# ---------------------------------------------------------------- CLI
def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("--owner", required=True)
    add.add_argument("--class", dest="cls", required=True)
    add.add_argument("--question", required=True)
    add.add_argument("--option", action="append", default=[])
    add.add_argument("--acts", required=True, help="what the owner will do with the answer")
    add.add_argument("--predict", required=True, help="the organizer's option label (hidden until resolve)")
    add.add_argument("--predict-reason", required=True)
    add.add_argument("--usd", default=None, help="spend only: total USD the decision commits, or 'unknown'")
    add.add_argument("--commitment", action="store_true", help="spend only: credits or a subscription")
    add.add_argument("--by", default="unknown", help="the session adding the question")
    add.add_argument("--dry-run", action="store_true")
    ls = sub.add_parser("list")
    ls.add_argument("--owner", default=None)
    ls.add_argument("--json", action="store_true")
    res = sub.add_parser("resolve")
    res.add_argument("id")
    how = res.add_mutually_exclusive_group()
    how.add_argument("--same-reason", action="store_true")
    how.add_argument("--new-reason", action="store_true")
    tk = sub.add_parser("takeover")
    tk.add_argument("--org", required=True)
    sub.add_parser("check")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        p = paths(resolve_main(Path(a.repo_root)))
        if a.cmd == "add":
            emit(cmd_add(p, a))
        elif a.cmd == "list":
            cmd_list(p, a)
        elif a.cmd == "resolve":
            emit(cmd_resolve(p, a))
        elif a.cmd == "takeover":
            emit(cmd_takeover(p, a))
        else:
            result, code = cmd_check(p)
            emit(result)
            return code
    except Refusal as r:
        emit({"status": "error", "code": r.code, "message": r.message, **r.extra})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
