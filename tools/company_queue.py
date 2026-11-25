#!/usr/bin/env python3
"""company_queue.py - the discovery queue: where discovered companies wait for Nick's call.

Built 2026-11-17 to execute decisions Nick settled on 2026-11-06 in
data/workstreams/funnel-intake.md, which sat unbuilt for 11 days:

  1. Discovery output lands in a queue file /standup reads (the role-queue pattern),
     NOT in data/inbox.md. Before this, tools/agent_collect.py prepended every weekly
     result to the inbox, where nothing read it (36 blocks on 10/19, 44 on 11/17).
  2. "Accept" checks for a public Ashby / Greenhouse / Lever board at accept time:
     board found -> a scan target with `ats` and `slug`; none -> an outreach-only
     target (no `ats`). A board that answers with zero postings is still a board: it
     becomes a scan target too, and `read` names it under `empty_boards` until Nick
     clears it with `ack-empty` (Nick 2026-11-19: "empty boards can stay scan targets,
     but I want them to bring it up"). Both go through tools/act_apply.py target-add, the one writer
     for data/scan-targets.yaml. "Reject" goes through target-reject, which records the
     company under `rejected:` so the weekly collector never proposes it again.

Reading never consumes. Only `decide` moves an entry out of `pending`, and only after
act_apply reported success, so a failed write leaves the company in the queue.

CLI (top-level options before the subcommand):
  company_queue.py [--repo-root R] read [--top 4]
  company_queue.py [--repo-root R] add --preset lane-a [--entity company] [--today D] < cands.json
  company_queue.py [--repo-root R] decide --name "Acme" --accept [--lane a|b] [--dry-run]
  company_queue.py [--repo-root R] decide --name "Acme" --reject [--reason "..."]
  company_queue.py [--repo-root R] import-inbox [--inbox data/inbox.md] [--remove]
  company_queue.py [--repo-root R] ack-empty --name "Acme"
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT))

import inbox_lock  # noqa: E402
from company_key import KeyIndex, exact as exact_company  # noqa: E402

QUEUE_NAME = "tools/.company-queue.json"

# The keys add() stores from a candidate. The weekly collector sends the first ten plus
# stage_text and slug_guess (agent_discover.py struct_to_candidates); /discover-companies
# also sends its Step 3 judgment (lane, fit, bench, recommendation, pipeline verdict).
# Before l2-discover-queue-1124 everything past the first ten was silently dropped.
KEPT_KEYS = ("description", "score", "geo_band", "geo_flag", "possible_match",
             "possible_duplicate", "url", "role", "company", "location",
             "stage_text", "slug_guess", "lane", "fit", "bench", "bench_note",
             "recommendation", "pipeline_note")
# What read() shows per company, so /standup can render the skill's whole proposal row.
READ_KEYS = ("name", "description", "score", "geo_band", "geo_flag", "possible_match",
             "possible_duplicate", "preset", "first_seen", "url", "stage_text",
             "slug_guess", "lane", "fit", "bench", "bench_note", "recommendation",
             "pipeline_note")


def queue_path(repo_root: Path) -> Path:
    return Path(repo_root) / QUEUE_NAME


def norm(name: str) -> str:
    """Same normalization as agent_core._norm_name, so keys agree with the collector.
    The format of every stored `key`: kept as is so stored entries stay valid. Whether
    two companies are the same is decided by tools/company_key.py, not by this."""
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def entry_key(entry: dict) -> str:
    if entry.get("entity") == "person":
        return f"person:{norm(entry.get('name', ''))}|{norm(entry.get('company', ''))}"
    return f"company:{norm(entry.get('name', ''))}"


def _empty() -> dict:
    return {"pending": [], "decided": []}


def _load_text(text: str) -> dict:
    data = json.loads(text) if text.strip() else _empty()
    if not isinstance(data, dict):
        raise ValueError("company queue top level is not an object")
    data.setdefault("pending", [])
    data.setdefault("decided", [])
    for k in ("pending", "decided"):
        if not isinstance(data[k], list):
            raise ValueError(f"company queue {k!r} is not a list")
    if not isinstance(data.setdefault("scan_boards", {}), dict):
        raise ValueError("company queue 'scan_boards' is not an object")
    return data


def _dump(data: dict) -> str:
    data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def load(repo_root: Path) -> dict:
    p = queue_path(repo_root)
    return _load_text(p.read_text(encoding="utf-8")) if p.is_file() else _empty()


def add(repo_root: Path, preset: str, entity: str, candidates: list[dict],
        today: str) -> dict:
    """Queue candidates. A key already pending or already decided is skipped, so a
    company Nick rejected here is never queued twice even if the collector's own seen
    set is reset."""
    counts = {"added": 0, "skipped": 0}

    def transform(text: str) -> str:
        data = _load_text(text)
        known = {e.get("key") for e in data["pending"]} | {e.get("key") for e in data["decided"]}
        # The stored key is ASCII-only: "Møller" and "Möller" share one, while "Café" and
        # "Cafe" do not. So it never decides company identity; it only marks a likely
        # duplicate when two non-exact names collide on it.
        names_by_key: dict = {}
        for e in data["pending"] + data["decided"]:
            if isinstance(e, dict) and e.get("entity") != "person":
                names_by_key.setdefault(e.get("key"), []).append(e.get("name"))
        # A company written another way ("Acme" / "Acme AI") is KEPT and tagged for Nick,
        # never skipped: this is a discovery path (company-key-1121 ruling).
        companies = KeyIndex([e.get("name") for e in data["pending"] + data["decided"]
                              if isinstance(e, dict) and e.get("entity") != "person"])
        counts["added"] = counts["skipped"] = 0
        for c in candidates:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            row = {"entity": entity, "preset": preset, "first_seen": today,
                   "name": c.get("name"), **{k: c[k] for k in KEPT_KEYS if k in c}}
            row["key"] = entry_key(row)
            # People: the stored key decides, as before. Companies: exact normalize_name
            # decides, whatever the stored keys say (Codex r1 F1 and r2 F1).
            if (row["key"] in known if entity == "person" else companies.exact(row["name"])):
                counts["skipped"] += 1
                continue
            known.add(row["key"])
            if entity != "person":
                # A legacy-key collision that is not exact is still a likely duplicate.
                collided = list(names_by_key.get(row["key"], []))
                dup = companies.possible(row["name"]) or collided
                if dup and not row.get("possible_duplicate"):
                    row["possible_duplicate"] = dup[0]
                companies.add(row["name"])
                names_by_key.setdefault(row["key"], []).append(row["name"])
            data["pending"].append(row)
            counts["added"] += 1
        return _dump(data)

    queue_path(repo_root).parent.mkdir(parents=True, exist_ok=True)
    inbox_lock.atomic_update(queue_path(repo_root), transform)
    return dict(counts)


def read(repo_root: Path, top: int = 4) -> dict:
    """What /standup renders. Never raises: a broken queue reports exists: false."""
    p = queue_path(repo_root)
    if not p.is_file():
        return {"exists": False, "reason": f"no queue at {p}; the discovery job has not "
                "written one yet", "pending_companies": None, "companies": [],
                "empty_boards": []}
    try:
        data = _load_text(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"exists": False, "reason": f"unreadable queue: {exc}",
                "pending_companies": None, "companies": [], "empty_boards": None}
    pend = [e for e in data["pending"] if isinstance(e, dict)]
    comps = [e for e in pend if e.get("entity") != "person"]
    people = [e for e in pend if e.get("entity") == "person"]

    def score(e):
        s = e.get("score")
        return s if isinstance(s, (int, float)) else -1

    comps.sort(key=lambda e: (score(e), e.get("first_seen") or ""), reverse=True)
    oldest = min((e.get("first_seen") or "" for e in pend), default=None) or None
    return {"exists": True, "updated_at": data.get("updated_at"),
            "pending_companies": len(comps), "pending_people": len(people),
            "oldest_first_seen": oldest, "decided_count": len(data["decided"]),
            "companies": [{k: e.get(k) for k in READ_KEYS} for e in comps[:top]],
            "empty_boards": _empty_boards(data)}


def _live_flag(flag) -> bool:
    """An empty-board flag Nick has not cleared and whose board has not filled again."""
    return (isinstance(flag, dict) and not flag.get("acked_on")
            and not flag.get("recovered_on") and not flag.get("retired_on"))


def _empty_boards(data: dict) -> list[dict]:
    """Boards with 0 postings that Nick has not cleared: accepted with an empty board
    (source accept), or a scan target whose board the nightly scan found empty (source
    scan, empty-board-drop-1121)."""
    out = []
    for e in data["decided"]:
        flag = e.get("empty_board") if isinstance(e, dict) else None
        if _live_flag(flag):
            board = e.get("board") if isinstance(e.get("board"), dict) else {}
            out.append({"name": e.get("name"), "ats": board.get("ats"),
                        "slug": board.get("slug"), "detected_on": flag.get("detected_on"),
                        "source": "accept"})
    for rec in data.get("scan_boards", {}).values():
        if isinstance(rec, dict) and _live_flag(rec.get("empty_board")):
            out.append({"name": rec.get("name"), "ats": rec.get("ats"), "slug": rec.get("slug"),
                        "detected_on": rec["empty_board"].get("detected_on"),
                        "source": "scan", "after": rec["empty_board"].get("after")})
    return out


def ack_empty(repo_root: Path, name: str, board: str | None = None) -> dict:
    """Nick has seen an empty-board flag: stop naming it in read. Writes nothing else.

    Every live flag under `name` is a candidate, accept-time and scan-time alike. `board`
    ("ats:slug") picks one. A bare name that matches more than one is refused and nothing
    is written: clearing them all would hide a board the scan will not flag again while
    it stays empty (Grok r1 F2, Codex r2 F1)."""
    found: dict = {}

    def bid(b) -> str | None:
        return f"{b.get('ats')}:{b.get('slug')}" if isinstance(b, dict) else None

    def transform(text: str) -> str:
        d = _load_text(text)
        found.clear()
        cands = []  # (board id, the flag dict to ack, display name)
        for e in d["decided"]:
            flag = e.get("empty_board") if isinstance(e, dict) else None
            if _live_flag(flag) and exact_company(name, e.get("name", "")):
                cands.append((bid(e.get("board")), flag, e.get("name")))
        for r in d["scan_boards"].values():
            if (isinstance(r, dict) and _live_flag(r.get("empty_board"))
                    and exact_company(name, r.get("name", ""))):
                cands.append((bid(r), r["empty_board"], r.get("name")))
        if board is not None:
            # The /standup line prints ats/slug; ats:slug is accepted too (Grok r2 F1).
            want_board = board.replace("/", ":", 1)
            cands = [c for c in cands if c[0] == want_board]
        elif len(cands) > 1:
            found["ambiguous"] = sorted(str(c[0]) for c in cands)
            return text
        for _, flag, nm in cands:
            flag["acked_on"] = str(datetime.now().date())
            found["name"] = nm
        return _dump(d) if found.get("name") else text  # nothing to clear: file unchanged

    if not queue_path(repo_root).is_file():
        return {"status": "error", "code": "not_flagged", "message": "no queue file"}
    inbox_lock.atomic_update(queue_path(repo_root), transform)
    if found.get("ambiguous"):
        return {"status": "error", "code": "ambiguous", "boards": found["ambiguous"],
                "message": f"{name!r} names {len(found['ambiguous'])} empty boards; nothing "
                           "cleared. Pass --board ats:slug for the one Nick has seen"}
    if not found.get("name"):
        return {"status": "error", "code": "not_flagged",
                "message": f"no unacknowledged empty-board flag for {name!r}"
                           + (f" on board {board}" if board else "")}
    return {"status": "ok", "name": found["name"]}


# --- scan-time empty boards (empty-board-drop-1121, Nick 2026-11-21 "4, sure") -----------
def board_key(ats, slug) -> str:
    """The identity of one scan target's board: never the display name, which two
    targets can share and a rename changes (Codex r1 F1/F2, empty-board-drop-1121)."""
    return f"board:{ats}:{slug}"


def record_board_counts(repo_root: Path, counts: list[dict], today: str,
                        current: set | None = None, observed_at: str | None = None) -> dict:
    """Record the nightly scan's raw posting count per ATS board and flag a board at 0.

    `counts` holds {name, ats, slug, count, observed_at} for every target whose fetch had
    NO error: a failed fetch is left out by the scanner, so its state never moves (a
    failure is a fetch failure, never an empty board). State lives in `scan_boards`,
    keyed by board_key(ats, slug); `name` is the latest display name, for the line.
    `read` names a live flag until `ack-empty` clears it.

    The transition rule (Nick 2026-11-21, funnel-intake "Empty-board drop-to-0: Nick's
    two design rulings"): a board at 0 is flagged only when the last scan of it saw
    postings ("Only after it had postings"). A board that stays at 0 is not flagged
    again, so an acked flag stays acked until the board fills and empties again. A board
    with postings marks a live flag `recovered_on`, which clears the line with no ack
    ("Clear it automatically"); a later drop flags afresh.

    `current` is every board_key the scan targets, including ones whose fetch failed. A
    record outside it is marked `retired_on` (and so is its live flag), so a removed or
    re-slugged target is not named forever. A retired board seen again keeps its last
    count, so it is flagged only if that count was above 0. Omitted, nothing is retired. `observed_at` is the
    scan's start: retirement skips a record a newer scan observed (Codex r2 F3).

    `observed_at` (the scan's start, ISO UTC) orders observations: one older than the
    board's recorded observation is skipped, so an overlapping older scan that commits
    last cannot overwrite a newer count or invent a drop (Codex r1 F3).
    """
    out = {"flagged": [], "recovered": [], "retired": [], "ignored": 0, "stale": 0}

    def transform(text: str) -> str:
        d = _load_text(text)
        out.update(flagged=[], recovered=[], retired=[], ignored=0, stale=0)
        boards = d["scan_boards"]
        for c in counts:
            n = c.get("count") if isinstance(c, dict) else None
            if (not isinstance(n, int) or isinstance(n, bool) or n < 0 or not c.get("name")
                    or not c.get("ats") or not c.get("slug")):
                out["ignored"] += 1
                continue
            key = board_key(c["ats"], c["slug"])
            prev = boards.get(key) if isinstance(boards.get(key), dict) else None
            seen_at = c.get("observed_at")
            if prev and seen_at and prev.get("observed_at") and seen_at < prev["observed_at"]:
                out["stale"] += 1
                continue
            rec = {**(prev or {}), "name": c["name"], "ats": c["ats"], "slug": c["slug"],
                   "last_count": n, "last_scan": today}
            rec.pop("retired_on", None)  # targeted and read again
            if seen_at:
                rec["observed_at"] = seen_at
            if n == 0:
                # Nick 2026-11-21, ruling 1, "Only after it had postings": a 0 is flagged
                # only when the last scan of this board saw postings. A board never seen
                # with postings (and so one already flagged empty at accept) never is.
                if prev is not None and (prev.get("last_count") or 0) > 0:
                    rec["empty_board"] = {"detected_on": today, "acked_on": None,
                                          "after": "postings"}
                    out["flagged"].append(c["name"])
            else:
                if _live_flag(rec.get("empty_board")):
                    rec["empty_board"] = {**rec["empty_board"], "recovered_on": today}
                    out["recovered"].append(c["name"])
                # Ruling 2 covers the accept-time line too: postings on that board clear it
                # (Codex ruling-delta F1), so a later drop shows one line, not two.
                for e in d["decided"]:
                    flag = e.get("empty_board") if isinstance(e, dict) else None
                    b = e.get("board") if isinstance(e, dict) else None
                    if (_live_flag(flag) and isinstance(b, dict)
                            and board_key(b.get("ats"), b.get("slug")) == key):
                        flag["recovered_on"] = today
                        if c["name"] not in out["recovered"]:
                            out["recovered"].append(c["name"])
            boards[key] = rec
        if current is not None:
            for key, rec in boards.items():
                if key in current or not isinstance(rec, dict) or rec.get("retired_on"):
                    continue
                if observed_at and rec.get("observed_at") and rec["observed_at"] > observed_at:
                    continue  # a newer scan observed this board after this scan's snapshot
                rec["retired_on"] = today
                if observed_at:
                    rec["observed_at"] = observed_at
                if _live_flag(rec.get("empty_board")):
                    rec["empty_board"] = {**rec["empty_board"], "retired_on": today}
                    out["retired"].append(rec.get("name"))
        return _dump(d)

    inbox_lock.atomic_update(queue_path(repo_root), transform)  # creates tools/ if absent
    return out


# --- accept-time board check (decision 1, 2026-11-06) ---------------------------------

# Lowercase letters NFKD does not decompose into an ASCII base letter plus a mark. Applied
# AFTER NFKD, because some letters only appear there ("ǿ" decomposes to "ø" + a mark).
_FOLD_EXTRA = str.maketrans({"ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l", "đ": "d",
                             "ð": "d", "þ": "th", "ı": "i", "ħ": "h", "ŋ": "ng", "ŧ": "t",
                             "ə": "e", "ĸ": "k", "ſ": "s", "ƒ": "f"})


def _fold_letter(c: str) -> str:
    """One non-ASCII letter as ASCII: NFKD, then _FOLD_EXTRA, marks dropped, and anything
    still non-ASCII turned into a space. "" or a space means the letter has no ASCII form."""
    import unicodedata
    out = unicodedata.normalize("NFKD", c).translate(_FOLD_EXTRA)
    return "".join(x if x.isascii() else " " for x in out if not unicodedata.combining(x))


def _ascii_fold(text: str) -> str:
    """Lowercase ASCII, decided per ORIGINAL character:
      - ASCII stays as it is;
      - a letter is folded to its base letter ("Å" -> "a", "ø" -> "o"). Folding, not
        dropping, is the point: the ASCII variants used to drop an accented letter outright,
        so a company's most likely board slug ("acme..." for "Åcme ...") was never probed
        (overnight S14, 2026-11-21). A letter no rule covers becomes a space, and
        `fold_lost_letters` reports it;
      - a combining mark (an accent typed separately) is dropped;
      - anything else (a Unicode dash, curly apostrophe, no-break space, "™", "²") becomes a
        space. It is a separator, which is what the hyphenated variant always made of it;
        folding it through NFKD would glue "tm" or "2" into the slug (Grok F1)."""
    import unicodedata
    out = []
    for c in text.lower():
        if c.isascii():
            out.append(c)
        elif unicodedata.category(c).startswith("L"):
            out.append(_fold_letter(c))
        elif not unicodedata.category(c).startswith("M"):
            out.append(" ")
    return "".join(out)


def fold_lost_letters(name: str) -> bool:
    """True when folding dropped a letter (one with no ASCII form, e.g. CJK, or a Latin
    letter outside _FOLD_EXTRA). The slugs built from such a name may miss the company's
    real board, so "no board found" cannot be claimed from them (Codex F1, 2026-11-21).
    Judged per ORIGINAL character, before decomposition: some letters decompose into a
    combining mark alone (U+FF9E), so a category check after NFKD missed them (Codex r2 F1).
    A letter counts as kept only if its fold contributes an ASCII letter or digit: some fold
    to a bare space (U+037A), which no slug variant keeps (Codex r3 F1)."""
    import unicodedata
    return any(not c.isascii() and unicodedata.category(c).startswith("L")
               and not re.search(r"[a-z0-9]", _fold_letter(c.lower()))
               for c in (name or ""))


def slug_candidates(name: str) -> list[str]:
    """Board slugs to probe for a company name, most likely first. Always ASCII: every
    variant is built from the accent-folded name, so no variant carries a character a
    board URL cannot take. A name that folds to nothing gives no candidates."""
    low = _ascii_fold((name or "").strip())
    stripped = re.sub(r"[\s.,-]*(ai|inc|labs|hq)$", "", low).strip()
    out = []
    for s in (low, stripped):
        for v in (re.sub(r"[^a-z0-9]", "", s), re.sub(r"[^a-z0-9]+", "-", s).strip("-"),
                  s.replace(" ", "")):
            if v and v not in out:
                out.append(v)
    base = re.sub(r"[^a-z0-9]", "", stripped)
    if base and f"{base}ai" not in out:   # a bare "ai" would probe a stranger's board
        out.append(f"{base}ai")
    return out


def _default_fetchers():
    from tools.career_scanner.ashby import fetch_ashby
    from tools.career_scanner.greenhouse import fetch_greenhouse
    from tools.career_scanner.lever import fetch_lever
    return {"ashby": fetch_ashby, "greenhouse": fetch_greenhouse, "lever": fetch_lever}


class BoardCheckIndeterminate(Exception):
    """A probe failed (timeout, 5xx, rate limit), or no slug could be built from the name,
    so "no board found" cannot be claimed."""


def detect_board(name: str, fetchers: dict | None = None) -> dict | None:
    """First public board with at least one posting; else the first empty board; else None.

    An empty board is `{"ats", "slug", "n_roles": 0, "empty": True}`: a fetcher with an
    `errors` channel answered with no postings and recorded nothing (all three public
    APIs answer a wrong slug with HTTP 404, checked live 2026-11-19), or Ashby's hosted
    page showed a live board with 0 postings. Nick 2026-11-19: "empty boards can stay
    scan targets, but I want them to bring it up." A fetcher without an `errors` channel
    cannot tell an empty board from a wrong slug, so it never yields one.

    A board with postings is conclusive and returns at once, even if an earlier probe
    failed (that is the base contract, test_detect_board_returns_the_first_board_with_
    postings). Otherwise a probe that errored (an exception, or an entry the fetcher
    appended to its `errors` list) makes the result indeterminate: raise
    BoardCheckIndeterminate so the caller leaves the company queued instead of recording
    it outreach-only (review 2026-11-17, Codex/Grok F1). That holds when an empty board
    was found too: the failed probe may have been a board with postings. The same holds
    when the name has no slug, or folding dropped a letter (`fold_lost_letters`): the
    slug that was never built may be the board with postings (2026-11-21). None means
    every probe answered and none found a board."""
    fetchers = fetchers if fetchers is not None else _default_fetchers()
    candidates = slug_candidates(name)
    if not candidates:
        # Nothing was probed, so "no board" would be a claim no check made.
        raise BoardCheckIndeterminate(f"no ASCII board slug can be built from {name!r}")
    failed = []
    empty = None
    for slug in candidates:
        for ats, fetch in fetchers.items():
            errors: list = []
            channel = _takes_errors(fetch)
            try:
                roles = fetch(slug, errors) if channel else fetch(slug)
            except Exception as exc:  # noqa: BLE001 -- recorded, decides the verdict below
                failed.append(f"{ats}/{slug}: {exc}")
                continue
            if roles:
                return {"ats": ats, "slug": slug, "n_roles": len(roles)}
            if empty is None and channel and (
                    not errors or any(_is_empty_live_board(e) for e in errors)):
                empty = {"ats": ats, "slug": slug, "n_roles": 0, "empty": True}
            failed += [f"{ats}/{slug}: {e.get('reason', e) if isinstance(e, dict) else e}"
                       for e in errors if not _is_not_found(e)]
    if failed:
        raise BoardCheckIndeterminate("; ".join(failed[:5]))
    if fold_lost_letters(name):
        raise BoardCheckIndeterminate(
            f"{name!r} has a letter with no ASCII form, so its board slug may not have been tried")
    return empty


def _is_empty_live_board(err) -> bool:
    """ashby.py found a live hosted board with 0 postings behind a posting-API 404."""
    return isinstance(err, dict) and bool(err.get("live_board")) and err.get("postings") == 0


def _takes_errors(fetch) -> bool:
    import inspect
    try:
        return "errors" in inspect.signature(fetch).parameters
    except (TypeError, ValueError):
        return False


def _is_not_found(err) -> bool:
    """A 404 is an answer (no board at this slug), not a failed probe. Except when the
    fetcher saw a live board behind the 404 (ashby.py `live_board`, 2026-11-18)."""
    if isinstance(err, dict) and "not_found" in err:
        return bool(err["not_found"])  # the fetcher decided (ashby.py, 2026-11-18)
    if isinstance(err, dict) and err.get("live_board"):
        return False
    return "404" in str(err.get("reason", err) if isinstance(err, dict) else err)


def _act_apply(repo_root: Path, args: list[str]) -> dict:
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "act_apply.py"),
                           "--repo-root", str(repo_root), *args],
                          capture_output=True, text=True)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "error", "message": (proc.stderr or proc.stdout)[-500:]}


def decide(repo_root: Path, name: str, decision: str, reason: str | None = None,
           lane: str | None = None, dry_run: bool = False, detector=detect_board,
           runner=_act_apply) -> dict:
    data = load(repo_root)
    # Exact normalize_name, not the stored key: two different companies can share a
    # stored key ("Møller" / "Möller"), and add() keeps both (Grok r1 F1, company-key-1121).
    match = [e for e in data["pending"]
             if isinstance(e, dict) and e.get("entity") != "person"
             and exact_company(name, e.get("name", ""))]
    if not match:
        return {"status": "error", "code": "not_found",
                "message": f"no pending company named {name!r} in the queue"}
    entry = match[0]
    if decision == "accept":
        try:
            board = detector(entry["name"])
        except BoardCheckIndeterminate as exc:
            return {"status": "error", "code": "board_check_failed",
                    "message": f"board check failed, nothing written, {entry['name']} "
                               f"stays queued: {exc}"}
        args = ["target-add", entry["name"]]
        if board:
            args += ["--ats", board["ats"], "--slug", board["slug"]]
        else:
            args += ["--outreach"]
        if lane:
            args += ["--lane", lane]
        note = (f"Accepted from the discovery queue {datetime.now().date()} "
                f"({entry.get('preset')}, first seen {entry.get('first_seen')})")
        if board and board.get("empty"):
            note += "; empty board (0 postings) at accept"
        args += ["--notes", note]
    elif decision == "reject":
        board = None
        args = ["target-reject", entry["name"]]
        if reason:
            args += ["--reason", reason]
        if lane:
            args += ["--lane", lane]
    else:
        return {"status": "error", "code": "bad_decision", "message": decision}

    if dry_run:
        return {"status": "ok", "dry_run": True, "would_run": args, "board": board}
    result = runner(repo_root, args)
    # act_apply answers status ok with added: false when the company is ALREADY under
    # companies: or rejected:, which may be the opposite of this decision. Only a write
    # that happened moves the entry (review 2026-11-17, Codex/Grok F2).
    if result.get("status") == "ok" and result.get("added") is False:
        return {"status": "error", "code": "already_known", "act_apply": result,
                "message": f"{entry['name']} is already in scan-targets.yaml (companies or "
                           f"rejected); nothing written, it stays queued for Nick to resolve"}
    if result.get("status") != "ok":
        return {"status": "error", "code": "act_apply_failed", "act_apply": result,
                "message": "scan-targets.yaml not changed; the company stays queued"}

    def same_entry(e) -> bool:
        """The matched company row, by exact name; never another row that shares its key."""
        return (isinstance(e, dict) and e.get("entity") != "person"
                and exact_company(entry["name"], e.get("name", "")))

    def transform(text: str) -> str:
        d = _load_text(text)
        moved = [e for e in d["pending"] if same_entry(e)]
        d["pending"] = [e for e in d["pending"] if not same_entry(e)]
        for e in moved:
            row = {**e, "decision": decision, "decided_on": str(datetime.now().date()),
                   "board": board, "reason": reason}
            if board and board.get("empty"):
                # Recorded once, at first detection; read names it until ack_empty.
                row["empty_board"] = {"detected_on": str(datetime.now().date()), "acked_on": None}
            d["decided"].append(row)
        return _dump(d)

    inbox_lock.atomic_update(queue_path(repo_root), transform)
    return {"status": "ok", "decision": decision, "name": entry["name"], "board": board,
            "act_apply": result}


# --- one-time migration of the drip blocks already in the inbox ------------------------

_BLOCK_HDR = re.compile(r"^## (\d{4}-\d{2}-\d{2}) \| Agent drip: (\S+) \((company|person)\)\s*$")
_COMPANY_LINE = re.compile(r"^- \*\*(.+?)\*\* - (.*?) \(score ([^)]*)\)(.*)$")
_PERSON_LINE = re.compile(r"^- \*\*(.+?)\*\* - (.*?) @ (.*?) · (.*)$")


def parse_inbox_blocks(text: str) -> tuple[list[dict], list[tuple[int, int]]]:
    """Return (entries, spans) for every Agent-drip block. spans are line ranges
    [start, end) in text.splitlines(), so the caller can remove exactly those blocks."""
    lines = text.splitlines()
    entries, spans = [], []
    i = 0
    while i < len(lines):
        m = _BLOCK_HDR.match(lines[i])
        if not m:
            i += 1
            continue
        today, preset, entity = m.groups()
        j = i + 1
        while j < len(lines) and not lines[j].startswith("## "):
            line = lines[j]
            if entity == "company":
                cm = _COMPANY_LINE.match(line)
                if cm:
                    nm, desc, score, tail = cm.groups()
                    try:
                        sc = float(score)
                        sc = int(sc) if sc.is_integer() else sc
                    except ValueError:
                        sc = None
                    pm = re.search(r"\[possible match: (.+?) in pipeline\]", tail)
                    entries.append({"entity": "company", "preset": preset, "first_seen": today,
                                    "name": nm.strip(), "description": desc.strip(), "score": sc,
                                    **({"possible_match": pm.group(1)} if pm else {}),
                                    "inbox_note": tail.strip() or None})
            else:
                pm = _PERSON_LINE.match(line)
                if pm:
                    nm, role, company, loc = pm.groups()
                    entries.append({"entity": "person", "preset": preset, "first_seen": today,
                                    "name": nm.strip(), "role": role.strip(),
                                    "company": company.strip(), "location": loc.strip()})
            j += 1
        spans.append((i, j))
        i = j
    return entries, spans


def import_inbox(repo_root: Path, inbox: Path, remove: bool = False) -> dict:
    text = inbox.read_text(encoding="utf-8") if inbox.is_file() else ""
    entries, spans = parse_inbox_blocks(text)
    by = {}
    for e in entries:
        by.setdefault((e["preset"], e["entity"], e["first_seen"]), []).append(e)
    added = skipped = 0
    for (preset, entity, day), group in sorted(by.items(), key=lambda kv: kv[0][2]):
        r = add(repo_root, preset, entity, group, day)
        added += r["added"]
        skipped += r["skipped"]
    removed = 0
    if remove and spans:
        def transform(t: str) -> str:
            _, sp = parse_inbox_blocks(t)
            ls = t.splitlines(keepends=True)
            drop = set()
            # Only the lines this parser understood: the header, the review-gated marker,
            # the entry lines it imported, and blank lines. Anything else in a span stays
            # (review 2026-11-17, Codex/Grok F3).
            for a, b in sp:
                for k in range(a, b):
                    line = ls[k].rstrip("\n")
                    if (k == a or not line.strip() or line.startswith("<!-- review-gated")
                            or _COMPANY_LINE.match(line) or _PERSON_LINE.match(line)):
                        drop.add(k)
            return "".join(l for k, l in enumerate(ls) if k not in drop)
        before = len(spans)
        inbox_lock.atomic_update(inbox, transform)
        removed = before
    return {"blocks": len(spans), "entries": len(entries), "added": added,
            "skipped": skipped, "blocks_removed": removed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read")
    r.add_argument("--top", type=int, default=4)
    d = sub.add_parser("decide")
    d.add_argument("--name", required=True)
    g = d.add_mutually_exclusive_group(required=True)
    g.add_argument("--accept", action="store_true")
    g.add_argument("--reject", action="store_true")
    d.add_argument("--reason")
    d.add_argument("--lane")
    d.add_argument("--dry-run", action="store_true")
    m = sub.add_parser("import-inbox")
    m.add_argument("--inbox", default=None)
    m.add_argument("--remove", action="store_true",
                   help="also delete the imported Agent-drip blocks from the inbox")
    n = sub.add_parser("add", help="queue a JSON list of candidates read from stdin "
                       "(the /discover-companies proposal)")
    n.add_argument("--preset", required=True)
    n.add_argument("--entity", choices=("company", "person"), default="company")
    n.add_argument("--today", default=None, help="YYYY-MM-DD; default the local date")
    k = sub.add_parser("ack-empty", help="clear one empty-board flag after Nick has seen it")
    k.add_argument("--name", required=True)
    k.add_argument("--board", default=None, metavar="ATS/SLUG",
                   help="the one board to clear when two empty boards share a name, as the /standup line prints it (ats/slug)")
    a = ap.parse_args(argv)
    root = Path(a.repo_root)
    if a.cmd == "read":
        out = read(root, a.top)
    elif a.cmd == "decide":
        out = decide(root, a.name, "accept" if a.accept else "reject", a.reason, a.lane, a.dry_run)
    elif a.cmd == "ack-empty":
        out = ack_empty(root, a.name, a.board)
    elif a.cmd == "add":
        try:
            cands = json.loads(sys.stdin.read())
        except ValueError as exc:
            cands, out = None, {"status": "error", "error": f"stdin is not JSON: {exc}"}
        if cands is not None and not isinstance(cands, list):
            cands, out = None, {"status": "error", "error": "stdin must be a JSON list"}
        if cands is not None:
            today = a.today or datetime.now().date().isoformat()
            out = {"status": "ok", **add(root, a.preset, a.entity, cands, today)}
    else:
        out = import_inbox(root, Path(a.inbox) if a.inbox else root / "data" / "inbox.md", a.remove)
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    return 0 if out.get("status", "ok") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
