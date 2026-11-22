#!/usr/bin/env python3
"""decision_write.py -- the question queue: route each open question for Nick to the window
of the session that will act on it.

Why this exists: approvals do not relay between sessions (Nick, 2026-11-05), so when the
organizer collects an answer another session will act on, Nick says it twice or the relayed
answer goes stale. Three fires on 2026-11-06. Nick: "I don't really want to have to say things
twice to the organizer and the child agent." This is part 1 of the run manifest (workstream
orchestration, formerly overnight-swarm). Spec and threat model: output/question-queue-1106/110626-spec-threat-model.md.

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

The prior-ruling gate (Build Q, 2026-11-21; Nick: "where have I answered the same question
before", "make sure all decisions are backed in data"). `add` refuses a question Nick already
answered and prints the answering line, unless --why-not-answered records why that ruling does not
cover it. A lexical matcher over the ruling prose was measured on 90 real questions and rejected:
the wide corpus flagged 30 with about 4 real, the marked-lines corpus flagged 1, wrongly. So the
gate matches TOPICS, not prose:
  - Curated topics, append-only in data/run-manifest/ruling-topics.jsonl: regex patterns (case-
    insensitive; an acronym opts out with (?-i:PE)) plus a cite
    (a file and an anchor substring found on exactly one line, resolved to its CURRENT line number
    at match time). Made by `topic-add`, or by `resolve --topic` so the next asking of a question
    Nick just answered is caught.
  - Company topics, derived: every terminal row of data/job-pipeline.md (stage_vocab), matched by
    the company's canonical name and its data/companies.yaml aliases, whole word, case-sensitive.
    Never guessed: a company known by a name not in that file is not matched.
The learning ladder, as data: every refusal appends a "cited" event to questions.jsonl; `check`
lists a curated topic whose cite is outside data/goals.md as PROMOTE on its 3rd citation (a policy
candidate for goals.md that Nick approves), and counts overrides per topic (a topic overridden
often is too broad).

Every path comes from the MAIN checkout, so a worktree never reads or writes its own copy of
the queue, the workstream files or the board.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/decision_write.py add --owner <workstream|org> \
      --class <class> --question "..." --option "a: ..." --option "b: ..." --acts "..." \
      --predict a --predict-reason "..." [--usd N|unknown] [--commitment] [--by <session>] \
      --lean "a: <why>" --rests-on <path>:<line> [--rests-on ...] --if-nothing "..." \
      --group time-bound|role|policy [--by-date YYYY-MM-DD (time-bound only)]
      [--why-not-answered "<why the cited ruling does not cover this>"]
  PYTHONIOENCODING=utf-8 python3 tools/decision_write.py topic-add --key <slug> --pattern <regex> \
      [--pattern ...] --file <repo-relative path> --anchor "<text on exactly one line>" [--replace]
  PYTHONIOENCODING=utf-8 python3 tools/decision_write.py list [--owner X] [--json]
  PYTHONIOENCODING=utf-8 python3 tools/decision_write.py resolve Q12 [--same-reason|--new-reason] \
      [--topic <slug> --pattern <regex> ...]
  PYTHONIOENCODING=utf-8 python3 tools/decision_write.py takeover --org <session>
  PYTHONIOENCODING=utf-8 python3 tools/decision_write.py check
"""
import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inbox_lock  # noqa: E402
from count_report import report  # noqa: E402
from stage_vocab import is_terminal_stage  # noqa: E402
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
ORG_FILE = "orchestration"         # the organizer's rulings live in its own workstream
LEGACY_ORG_FILE = "overnight-swarm"  # its name before 2026-11-16; read while it is the only one
ORG_NAMES = (ORG, ORG_FILE, LEGACY_ORG_FILE)  # every owner name that means the organizer
SIGNING_NAMESPACE = "decision-approval"   # v2: approval_signing namespace for big items

QUESTIONS_REL = Path("data/run-manifest/questions.jsonl")
PREDICTIONS_REL = Path("data/run-manifest/predictions.jsonl")
TOPICS_REL = Path("data/run-manifest/ruling-topics.jsonl")
GOALS_REL = Path("data/goals.md")
PIPELINE_REL = Path("data/job-pipeline.md")
COMPANIES_REL = Path("data/companies.yaml")
PROMOTE_AT = 3                     # Build Q spec: PROMOTE on a ruling's 3rd citation
TOPIC_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
COMPANY_PREFIX = "company:"
WORKSTREAMS_REL = Path("data/workstreams")
BOARD_REL = Path("output/overnight-merge-back/SESSIONS.md")
ORG_FILE_RELS = {(WORKSTREAMS_REL / f"{n}.md").as_posix() for n in (ORG_FILE, LEGACY_ORG_FILE)}

OPTION_RE = re.compile(r"^([A-Za-z0-9]+):\s*(\S.*)$")
REST_RE = re.compile(r"^([^\s:][^:]*):([1-9]\d*)$")
GROUPS = ("time-bound", "role", "policy")  # how the morning page orders what is waiting on Nick
# Org c0, 2026-11-21: every new question shows Nick a lean. A shown lean anchors him, so the
# asked event says so (lean_shown) and resolve copies the stamp onto the resolved event; R1's
# blind measure reads only questions with lean_shown false (rows asked before the lean existed).
LEAN_FIELDS = ("lean", "lean_label", "lean_shown", "rests_on", "if_nothing", "group", "by_date")
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
    main = main_checkout(Path(root).resolve())
    if not (main / ".git").is_dir():
        raise Refusal("no_main_checkout", f"{root} is not a main checkout or a worktree of one")
    return main


def paths(main: Path) -> dict:
    return {"main": main, "questions": main / QUESTIONS_REL, "predictions": main / PREDICTIONS_REL,
            "workstreams": main / WORKSTREAMS_REL, "board": main / BOARD_REL,
            "topics": main / TOPICS_REL, "pipeline": main / PIPELINE_REL,
            "companies": main / COMPANIES_REL}


def org_file(p: dict) -> Path:
    """The organizer's workstream file. Renamed overnight-swarm -> orchestration (Nick,
    2026-11-16); the code landed before the data file moved, so the old name is read while it is
    the only one. Both present is refused: rulings must never split across two files."""
    new = p["workstreams"] / f"{ORG_FILE}.md"
    old = p["workstreams"] / f"{LEGACY_ORG_FILE}.md"
    if new.exists() and old.exists():
        raise Refusal("org_file_split", f"both {new.name} and {old.name} exist in {p['workstreams']}; "
                      f"merge {old.name} into {new.name} and remove it")
    return old if old.exists() else new


def canonical_owner(owner: str) -> str:
    """Every organizer name is stored and matched as org: the board routes the organizer by its
    org row, never by a workstream column."""
    return ORG if owner in ORG_NAMES else owner


def owner_file(p: dict, owner: str) -> Path:
    """The owner's workstream file, refused if it resolves outside data/workstreams."""
    f = org_file(p) if owner in ORG_NAMES else p["workstreams"] / f"{owner}.md"
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
    """The whole prediction record: a change to any field, `at` and `blind` included, shows."""
    return sha(canonical(pred))


def record_hash(event: dict) -> str:
    """Every field of an event but this one, so `check` sees any edit made outside the tool,
    including an `approval` changed from "none"."""
    return sha(canonical({k: v for k, v in event.items() if k != "record_hash"}))


def sealed(event: dict) -> dict:
    return dict(event, record_hash=record_hash(event))


def approval_payload(q: dict, ruling: dict) -> bytes:
    """The exact bytes a v2 Touch ID signature would cover. The namespace is inside the payload,
    so a signature made for this purpose can never verify as an inbox or calendar approval."""
    return canonical({"namespace": SIGNING_NAMESPACE, "question_hash": q["question_hash"],
                      "option": ruling["option"], "owner": q["owner"], "window": ruling["window"]})


# ---------------------------------------------------------------- the board
def session_name(cell: str) -> str:
    return re.split(r"[\s(\[]", cell.replace("*", "").strip(), maxsplit=1)[0]


def cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def read_board(path: Path) -> tuple:
    """(rows, status). rows = [(first cell, session, workstreams)] from THE table whose header has a
    'Workstreams' cell: the header, a separator line, then consecutive '|' rows. rows is None, and
    status says why, when the board is missing, has no such table, has two, or has a row with the
    wrong cell count: a dropped row could hide a second holder, so nothing is guessed."""
    if not path.exists():
        return None, "missing"
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if "Workstreams" in cells(line)]
    if len(starts) != 1:
        return None, ("unreadable: no Workstreams table" if not starts
                      else "unreadable: more than one Workstreams table")
    start = starts[0]
    if start + 1 >= len(lines) or not re.fullmatch(r"[|\-: ]+", lines[start + 1].strip()):
        return None, "unreadable: no separator under the Workstreams header"
    header, rows = cells(lines[start]), []
    col = header.index("Workstreams")
    for n, line in enumerate(lines[start + 2:], start + 3):
        if not line.strip().startswith("|"):
            break
        c = cells(line)
        if len(c) != len(header):
            return None, f"unreadable: line {n} has {len(c)} cells, the header has {len(header)}"
        if not session_name(c[0]):
            return None, f"unreadable: line {n} has no session name"
        rows.append((c[0], session_name(c[0]),
                     [w.strip() for w in c[col].split(",") if w.strip() not in ("", "-")]))
    return rows, "ok"


def routing(p: dict):
    """THE ownership lookup: (owner -> route, source status). Every route shown anywhere comes from
    here, so moving ownership off the board (to a workstream Owner row, if Nick supersedes R7) is
    a change to this one function."""
    board, status = read_board(p["board"])
    return (lambda owner: route(owner, board, status)), status


def route(owner: str, board, status: str = "missing") -> str:
    if board is None:
        return f"UNROUTED: board {status}"
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
                                  "usd", "at", "by", *LEAN_FIELDS, "not_answered")}


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def norm(text: str) -> str:
    return " ".join(text.lower().split())


# ---------------------------------------------------------------- prior rulings (Build Q)
def compile_patterns(patterns: list) -> list:
    if not patterns:
        raise Refusal("no_pattern", "a topic needs at least one --pattern")
    out = []
    for pat in patterns:
        try:
            rx = re.compile(pat, re.IGNORECASE)   # an acronym opts out: (?-i:PE)
        except re.error as exc:
            raise Refusal("bad_pattern", f"{pat!r} is not a valid regex: {exc}") from None
        if rx.search(""):
            raise Refusal("bad_pattern", f"{pat!r} matches empty text, so it would match every question")
        out.append(rx)
    return out


def cite_path(p: dict, rel: str) -> Path:
    f = (p["main"] / rel).resolve()
    if not f.is_relative_to(p["main"].resolve()):
        raise Refusal("cite_outside", f"{rel} resolves outside the main checkout")
    if not f.is_file():
        raise Refusal("cite_missing", f"no file {rel} in the main checkout")
    return f


def locate(p: dict, rel: str, anchor: str) -> tuple:
    """(line number, line text) of the ONE line holding `anchor`, read now, so a cite follows its
    line when lines move. Zero or several lines is a refusal: a cite must never guess."""
    lines = cite_path(p, rel).read_text(encoding="utf-8").splitlines()
    hits = [(n, l) for n, l in enumerate(lines, 1) if anchor in l]
    if not hits:
        raise Refusal("anchor_not_found", f"no line in {rel} holds {anchor!r}")
    if len(hits) > 1:
        raise Refusal("anchor_ambiguous",
                      f"{len(hits)} lines in {rel} hold {anchor!r} ({', '.join(str(n) for n, _ in hits)})")
    return hits[0]


def curated_topics(p: dict) -> dict:
    """key -> the latest topic event for that key (a --replace supersedes, never edits)."""
    return {e["key"]: e for e in load_events(p["topics"]) if e.get("type") == "topic"}


def company_topics(p: dict) -> tuple[list, list]:
    """(topics, warnings). One topic per company whose EVERY pipeline row is terminal (a company
    with one closed role and one live role is still in play: 3 such companies on 2026-11-21).
    Rows are grouped by canonical company through data/companies.yaml, so a live row written
    under an alias keeps the company live. Each topic is (key, names, rel, [(line number, line
    text) of each terminal row]); rows come from pipe_read's parser and are mapped back to their
    lines by their company, role and stage cells. The company file is read only when some row is
    terminal; if it cannot be loaded, companies match by their pipeline spelling only and a
    warning says so (the same as a missing file, but visible)."""
    if not p["pipeline"].is_file():
        return [], []
    from datetime import date
    from pipe_read import parse_all_rows            # lazy: only the gate needs it
    text = p["pipeline"].read_text(encoding="utf-8")
    rows = parse_all_rows(text, date.today())
    if not any(is_terminal_stage(r["stage"]) for r in rows):
        return [], []
    dim, warnings = None, []
    if p["companies"].is_file():
        import company_dim
        try:
            dim = company_dim.load(p["companies"])
        except Exception as exc:                    # yaml, schema or read error: degrade, visibly
            warnings.append(f"{COMPANIES_REL.as_posix()} unreadable ({exc}); companies matched by "
                            "their pipeline spelling only, aliases ignored")
    groups = {}
    for row in rows:
        entry = dim.resolve(row["company"]) if dim else None
        key = entry["canonical"] if entry else row["company"]
        g = groups.setdefault(key, {"entry": entry, "rows": []})
        g["rows"].append(row)
    cells_at = [(n, l, [c.strip() for c in l.strip().strip("|").split("|")])
                for n, l in enumerate(text.splitlines(), 1) if l.startswith("|")]
    out = []
    for key, g in groups.items():
        if not all(is_terminal_stage(r["stage"]) for r in g["rows"]):
            continue
        keys = {(r["company"], r["role"], r["stage"]) for r in g["rows"]}
        where = [(n, l) for n, l, c in cells_at if len(c) >= 3 and (c[0], c[1], c[2]) in keys]
        entry = g["entry"]
        names = {r["company"] for r in g["rows"]} | (
            {entry["canonical"], *(entry.get("aliases") or [])} if entry else set())
        out.append((COMPANY_PREFIX + key, sorted(names), PIPELINE_REL.as_posix(), where))
    return out, warnings


def prior_rulings(p: dict, question: str) -> tuple[list, list]:
    """(answers, warnings): every ruling the question repeats, with the answering line as it
    reads now."""
    answers = []
    for key, t in curated_topics(p).items():
        if not any(rx.search(question) for rx in compile_patterns(t["patterns"])):
            continue
        try:
            n, text = locate(p, t["file"], t["anchor"])
        except Refusal as r:
            raise Refusal("topic_cite_stale", f"topic {key} matches but its cite is stale: {r.message}; "
                          f"fix it with topic-add --replace") from None
        answers.append({"topic": key, "file": t["file"], "line": n, "text": text})
    companies, warnings = company_topics(p)
    for key, names, rel, where in companies:
        if any(re.search(rf"(?<!\w){re.escape(nm)}(?!\w)", question) for nm in names):
            answers += [{"topic": key, "file": rel, "line": n, "text": text} for n, text in where]
    return answers, warnings


def topic_event(p: dict, key: str, patterns: list, rel: str, anchor: str, by: str, replace: bool,
                existing: dict) -> dict:
    if not TOPIC_KEY_RE.match(key or ""):
        raise Refusal("bad_topic_key", f"a topic key is lowercase letters, digits and hyphens, got {key!r}")
    compile_patterns(patterns)
    n, text = locate(p, rel, anchor)
    # Stored as the resolved repo-relative path, so data/x/../goals.md is data/goals.md
    # everywhere a cite path is compared (Codex r1 F2).
    rel = cite_path(p, rel).relative_to(p["main"].resolve()).as_posix()
    if key in existing and not replace:
        raise Refusal("topic_exists", f"topic {key} exists; pass --replace to supersede it")
    return sealed({"type": "topic", "key": key, "at": now(), "by": by, "patterns": list(patterns),
                   "file": rel, "anchor": anchor, "line_at_add": n,
                   "line_hash_at_add": sha(text)})


def cmd_topic_add(p: dict, a) -> dict:
    with inbox_lock.file_lock(p["topics"]):
        ev = topic_event(p, a.key, a.pattern, a.file, a.anchor, a.by, a.replace, curated_topics(p))
        append(p["topics"], ev)
    return {"status": "ok", "topic": {"key": ev["key"], "file": ev["file"], "line": ev["line_at_add"],
                                      "patterns": ev["patterns"]}}


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
    if not math.isfinite(usd) or usd < 0:
        raise Refusal("bad_usd", f"--usd must be a finite amount of 0 or more, got {raw!r}")
    return usd


def cmd_add(p: dict, a) -> dict:
    owner = canonical_owner(a.owner)
    if not owner_file(p, owner).is_file() and owner != ORG:
        raise Refusal("unknown_owner", f"no workstream file for {owner!r}; owner is a workstream or 'org'")
    if a.cls not in CLASSES:
        raise Refusal("unknown_class", f"{a.cls!r} is not a codified class: {', '.join(CLASSES)}")
    checks = [("question", a.question), ("acts", a.acts), ("predict-reason", a.predict_reason)]
    if a.why_not_answered is not None:
        checks.append(("why-not-answered", a.why_not_answered))
    for name, val in checks:
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
    lean = lean_fields(p, a, labels)

    # A corrupt queue refuses every add before anything is matched or written, the citing path
    # included (T8; Grok r1 F1: the refusal path appended cited events past a corrupt line).
    load_events(p["questions"])
    answers, warnings = prior_rulings(p, a.question)
    if answers and a.why_not_answered is None:
        refusal = Refusal(
            "already_answered",
            "already answered at " + "; ".join(f"{x['file']}:{x['line']}" for x in answers)
            + ". The ruling text is in `answers`. If it does not cover this question, re-run with "
              "--why-not-answered \"<why>\"; the reason is recorded.",
            answers=answers, **({"warnings": warnings} if warnings else {}))
        if not a.dry_run:
            with inbox_lock.file_lock(p["questions"]):
                for x in answers:
                    append(p["questions"], sealed({
                        "type": "cited", "id": None, "at": now(), "by": a.by, "topic": x["topic"],
                        "file": x["file"], "line": x["line"], "line_hash": sha(x["text"]),
                        "question": a.question.strip()}))
        raise refusal
    # One entry per topic, however many rows it cites (Grok r1 F2: a two-row company counted twice).
    not_answered = ({"topics": list(dict.fromkeys(x["topic"] for x in answers)),
                     "reason": a.why_not_answered.strip()} if answers else None)

    def build(events: list, preds: list) -> tuple[dict, dict]:
        for q in open_questions(events).values():
            if q["owner"] == owner and norm(q["question"]) == norm(a.question):
                raise Refusal("duplicate_question", f"{q['id']} already asks this", existing=q["id"])
        # Ids come from BOTH files: an add interrupted between its two appends leaves a prediction
        # with no question, and its id must never be handed out again.
        used = [e["id"] for e in events if e.get("type") == "asked"] + [x.get("id") for x in preds]
        n = 1 + max([int(i[1:]) for i in used if isinstance(i, str) and re.fullmatch(r"Q\d+", i)],
                    default=0)
        q = {"type": "asked", "id": f"Q{n}", "at": now(), "by": a.by, "owner": owner,
             "class": a.cls, "kind": kind_of(a.cls, usd, a.commitment), "question": a.question.strip(),
             "options": [o.strip() for o in a.option], "acts": a.acts.strip(), "usd": usd}
        q["question_hash"] = question_hash(q)
        pred = {"id": q["id"], "at": q["at"], "option": a.predict, "reason": a.predict_reason.strip(),
                "blind": True, "question_hash": q["question_hash"]}
        q["prediction_hash"] = prediction_hash(pred)
        q.update(lean)
        if not_answered:
            q["not_answered"] = not_answered
        return sealed(q), pred

    extra = {**({"not_answered": not_answered} if not_answered else {}),
             **({"warnings": warnings} if warnings else {})}
    if a.dry_run:
        q, _ = build(load_events(p["questions"]), load_events(p["predictions"]))
        return {"status": "ok", "dry_run": True, "question": public_view(q), **extra}
    with inbox_lock.file_lock(p["questions"]):
        q, pred = build(load_events(p["questions"]), load_events(p["predictions"]))
        append(p["predictions"], pred)          # first, so an asked event never lacks one
        append(p["questions"], q)
    return {"status": "ok", "question": public_view(q), **extra}


def lean_fields(p: dict, a, labels: list) -> dict:
    """The fields rule 8 requires on every new question: the lean (an option label and why), the
    data lines it rests on (each must exist in the main checkout), what happens if Nick does
    nothing, and the group (time-bound needs a date; no other group takes one)."""
    for name, val in (("lean", a.lean), ("if-nothing", a.if_nothing)):
        if not val.strip():
            raise Refusal("blank_field", f"--{name} is blank")
    m = OPTION_RE.match(a.lean.strip())
    if not m or m.group(1) not in labels:
        raise Refusal("bad_lean", f"--lean is '<option label>: <why>' with a label in {labels}, "
                                  f"got {a.lean!r}")
    rests = []
    for cite in a.rests_on:
        cite = cite.strip()
        rm = REST_RE.match(cite)
        rel = Path(rm.group(1)) if rm else None
        if not rm or rel.is_absolute() or ".." in rel.parts:
            raise Refusal("bad_rests_on", f"--rests-on is '<repo-relative path>:<line>', got {cite!r}")
        main = p["main"].resolve()
        f = (main / rel).resolve()                 # follows symlinks: the TARGET must be inside
        if not f.is_relative_to(main):
            raise Refusal("bad_rests_on", f"{cite}: resolves outside the main checkout")
        n = int(rm.group(2))
        try:
            lines = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
        except UnicodeDecodeError:
            raise Refusal("rests_on_unreadable", f"{cite}: not a UTF-8 text file") from None
        if len(lines) < n:
            raise Refusal("rests_on_missing", f"{cite}: no such line in the main checkout")
        rests.append(cite)
    by_date = None
    if a.group == "time-bound":
        if not a.by_date:
            raise Refusal("by_date_required", "a time-bound question needs --by-date YYYY-MM-DD")
        try:
            by_date = datetime.strptime(a.by_date, "%Y-%m-%d").date().isoformat()
        except ValueError:
            raise Refusal("bad_by_date", f"--by-date is YYYY-MM-DD, got {a.by_date!r}") from None
    elif a.by_date:
        raise Refusal("by_date_not_time_bound", f"--by-date is only for --group time-bound, not {a.group}")
    return {"lean": a.lean.strip(), "lean_label": m.group(1), "lean_shown": True,
            "rests_on": rests, "if_nothing": a.if_nothing.strip(), "group": a.group,
            "by_date": by_date}


def listing(p: dict) -> tuple[list, str]:
    events = load_events(p["questions"])
    to, status = routing(p)
    return [dict(public_view(q), route=to(q["owner"])) for q in open_questions(events).values()], status


def cmd_list(p: dict, a) -> None:
    every, board = listing(p)
    qs = [q for q in every if not a.owner or q["owner"] == canonical_owner(a.owner)]
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
    print(f"board: {board}")
    report(f"open questions in {QUESTIONS_REL.as_posix()}", population=len(every), included=len(qs),
           excluded={f"owner is not {a.owner}": len(every) - len(qs)} if a.owner else {})


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
    # Stored relative to the main checkout, so `check` reads the same file from any checkout.
    return {"file": f.relative_to(p["main"]).as_posix(), "line": n, "line_hash": sha(line), "option": m.group(2),
            "reason": m.group(3), "window": m.group(4)}


def find_prediction(p: dict, q: dict) -> dict:
    mine = [x for x in load_events(p["predictions"]) if x.get("id") == q["id"]]
    if not mine:
        raise Refusal("prediction_missing", f"{q['id']}: no prediction recorded")
    if len(mine) > 1:
        raise Refusal("prediction_duplicate", f"{q['id']}: {len(mine)} predictions recorded; run `check`")
    if prediction_hash(mine[0]) != q.get("prediction_hash"):
        raise Refusal("prediction_changed", f"{q['id']}: the prediction changed after it was made")
    return mine[0]


def cmd_resolve(p: dict, a) -> dict:
    if a.pattern and not a.topic:
        raise Refusal("pattern_without_topic", "--pattern on resolve needs --topic")
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
        if record_hash(q) != q.get("record_hash"):
            # The lean_shown stamp is copied onto the resolved event below; copying a hand-edited
            # one would seal a false blind stamp for R1 (Codex r1 F1, Build M).
            raise Refusal("question_record_edited", f"{a.id}'s asked event was edited outside the tool")
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
        topic = None
        if a.topic:                              # validated in full before the resolved event,
                                                 # so a bad topic refuses with nothing written
            anchor = f"RULING {a.id} |"
            with inbox_lock.file_lock(p["topics"]):
                topic = topic_event(p, a.topic, a.pattern, ruling["file"], anchor, "resolve",
                                    False, curated_topics(p))
        payload = approval_payload(q, ruling)
        # The window is what the ruling line says; it is recorded beside the route the board gave
        # at that moment, never checked against it: a typed session name is not evidence.
        append(p["questions"], sealed({
            "type": "resolved", "id": a.id, "at": now(), "option": ruling["option"],
            "reason": ruling["reason"], "window": ruling["window"], "route_at_resolve": routing(p)[0](q["owner"]),
            "file": ruling["file"], "line": ruling["line"], "line_hash": ruling["line_hash"],
            "outcome": outcome, "lean_shown": q.get("lean_shown") is True,
            "lean_label": q.get("lean_label"),
            "approval": "none", "payload_hash": sha(payload), "signature": None}))
        if topic:
            with inbox_lock.file_lock(p["topics"]):
                append(p["topics"], topic)
    note = (f"routed only, not an approval: act only under the existing gate ({q['class']})"
            if q["kind"] == "big" else "design call recorded")
    out = {"status": "ok", "summary": f"{a.id} resolved: {note}", "question": public_view(q),
           "ruling": ruling, "prediction": {"option": pred["option"], "reason": pred["reason"]},
           "outcome": outcome, "lean_shown": q.get("lean_shown") is True, "approval": "none"}
    if topic:
        out["topic"] = {"key": topic["key"], "file": topic["file"], "line": topic["line_at_add"],
                        "patterns": topic["patterns"]}
    return out


def org_names(first_cell: str) -> set:
    return {session_name(first_cell), *re.findall(r"[(\[]([^)\]]+)[)\]]", first_cell)}


def cmd_takeover(p: dict, a) -> dict:
    board, _ = read_board(p["board"])
    org_rows = [c for c, s, _ in board or [] if s == ORG]
    if len(org_rows) != 1 or a.org not in org_names(org_rows[0]):
        raise Refusal("board_disagrees", f"the board's org row does not name {a.org!r}; update it first")
    qs, _ = listing(p)                     # refuses a corrupt queue before anything is written
    append(p["questions"], sealed({"type": "takeover", "id": None, "at": now(), "org": a.org}))
    return {"status": "ok", "org": a.org,
            "org_questions": [q for q in qs if q["owner"] == ORG],
            "unrouted": [q for q in qs if q["route"].startswith("UNROUTED")]}


def cmd_check(p: dict) -> tuple[dict, int]:
    org_file(p)     # the two-file split is refused up front, whatever the queue holds
    events, problems = read_lines(p["questions"])
    preds, pred_problems = read_lines(p["predictions"])
    problems += pred_problems
    by_id = {}
    for n, e in enumerate(events, 1):
        if record_hash(e) != e.get("record_hash"):
            problems.append(f"event {n} ({e.get('type')} {e.get('id')}): record edited outside the tool")
        if e.get("type") == "asked":
            if e["id"] in by_id:
                problems.append(f"duplicate id {e['id']}")
            by_id[e["id"]] = e
    pred_ids = [x.get("id") for x in preds]
    for i in sorted({i for i in pred_ids if pred_ids.count(i) > 1}, key=str):
        problems.append(f"{i}: duplicate prediction")
    for i in sorted({i for i in pred_ids if i not in by_id}, key=str):
        problems.append(f"{i}: prediction with no question (an interrupted add)")
    for i, q in by_id.items():
        if question_hash(q) != q.get("question_hash"):
            problems.append(f"{i}: question changed after it was asked")
        mine = [x for x in preds if x.get("id") == i]
        if not mine:
            problems.append(f"{i}: prediction missing")
        elif prediction_hash(mine[0]) != q.get("prediction_hash"):
            problems.append(f"{i}: prediction changed after it was made")
    for e in events:
        if e.get("type") != "resolved":
            continue
        f = p["main"] / e["file"]
        if Path(e["file"]).as_posix() in ORG_FILE_RELS:
            f = org_file(p)     # a sealed event names the file it read; the rename moved it
        lines = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
        if not any(l.startswith(f"RULING {e['id']} |") and sha(l) == e["line_hash"] for l in lines):
            problems.append(f"{e['id']}: ruling line changed or missing in {f}")
    topics, topic_problems = read_lines(p["topics"])
    problems += topic_problems
    current = {}
    for n, t in enumerate(topics, 1):
        if record_hash(t) != t.get("record_hash"):
            problems.append(f"topic event {n} ({t.get('key')}): record edited outside the tool")
        current[t.get("key")] = t       # the file holds only topic events; the latest per key wins
    for key, t in current.items():
        try:
            locate(p, t["file"], t["anchor"])
        except Refusal as r:
            problems.append(f"topic {key}: anchor stale ({r.message})")
    cited = {}
    for e in events:
        if e.get("type") == "cited":
            cited[e["topic"]] = cited.get(e["topic"], 0) + 1
    promote = [{"topic": k, "citations": cited[k], "file": t["file"], "anchor": t["anchor"]}
               for k, t in sorted(current.items())
               if cited.get(k, 0) >= PROMOTE_AT and Path(t["file"]).as_posix() != GOALS_REL.as_posix()]
    overrides = {}
    for e in events:
        for k in (e.get("not_answered") or {}).get("topics", []):
            overrides[k] = overrides.get(k, 0) + 1
    return ({"status": "ok" if not problems else "problems", "problems": problems,
             "promote": promote, "overrides": overrides, "citations": cited},
            (1 if problems else 0))


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
    add.add_argument("--lean", required=True, help="'<option label>: <why>', shown to Nick (rule 8)")
    add.add_argument("--rests-on", action="append", required=True,
                     help="'<repo-relative path>:<line>' the lean rests on; repeatable")
    add.add_argument("--if-nothing", required=True, help="what happens if Nick does nothing")
    add.add_argument("--group", required=True, choices=GROUPS)
    add.add_argument("--by-date", default=None, help="time-bound only: YYYY-MM-DD")
    add.add_argument("--why-not-answered", default=None,
                     help="why the cited prior ruling does not cover this question (recorded)")
    ta = sub.add_parser("topic-add")
    ta.add_argument("--key", required=True)
    ta.add_argument("--pattern", action="append", default=[])
    ta.add_argument("--file", required=True, help="repo-relative file holding the ruling")
    ta.add_argument("--anchor", required=True, help="text found on exactly one line of --file")
    ta.add_argument("--replace", action="store_true")
    ta.add_argument("--by", default="unknown")
    ls = sub.add_parser("list")
    ls.add_argument("--owner", default=None)
    ls.add_argument("--json", action="store_true")
    res = sub.add_parser("resolve")
    res.add_argument("id")
    how = res.add_mutually_exclusive_group()
    how.add_argument("--same-reason", action="store_true")
    how.add_argument("--new-reason", action="store_true")
    res.add_argument("--topic", default=None, help="record a topic so the next asking is caught")
    res.add_argument("--pattern", action="append", default=[])
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
        elif a.cmd == "topic-add":
            emit(cmd_topic_add(p, a))
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
