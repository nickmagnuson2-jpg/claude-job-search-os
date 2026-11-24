#!/usr/bin/env python3
"""run_metrics.py - rebuild one run's metrics from Claude Code session transcripts.

WHY. Of the 9 overnight runs from 10/26 to 11/21, none recorded dispatches, failures,
outcomes and timings together, and none recorded timings at all (DECISIONS.md row C7,
2026-11-21). Choosing what to codify next was judged rather than measured. This script
measures it from the transcripts, which every session writes whether or not anyone
remembers to log.

WHAT IT REPORTS for a time window (one run):
  * per session: name, first and last line in the window, active minutes (gaps over the
    idle cap excluded), Nick's prompts and Nick-minutes, branches seen.
  * per branch: first commit, landing time on the main ref, minutes from one to the other.
  * failure candidates: each event that reads as a caught failure, and who caught it
    (nick | org | peer | hook | self). CANDIDATES, not a count: validated 2026-11-23 on
    the 11-21 run, the catcher matched 6 of 14 hand-coded failure rows, and many
    candidates are not failures (see output/overnight-112226/S6-C7-report.md). A run's
    failure table is still written by the org; these candidates are its starting list.
  * run fields (RUN_FIELDS), which the overnight brief template asks every run to write.

DEFINITIONS (stated here because each one is a choice):
  * Nick prompt: a user line whose `origin.kind` is "human". Peer messages, task
    notifications, tool results and meta lines are not Nick.
  * Nick-minutes: for each Nick prompt, the time since the session's previous assistant
    line, capped at --nick-cap-min (default 10). A first prompt with no earlier output
    counts 0. This measures reading-and-replying time, not wall-clock attention.
  * Session name: found by matching the body of a SendMessage this session sent to a
    cross-session message another session received (which carries `from-name`). A session
    that sent nothing anyone received is named "sid:<first 8 chars>".
  * Failure and catcher. These are text heuristics, so validate them against a hand-coded
    table before trusting counts:
      - hook: a tool error naming a hook script, with "BLOCKED" or "hook error";
      - nick: a Nick prompt matching CORRECTION_RE;
      - org/peer: a received cross-session message matching CORRECTION_RE, classed org
        when the sender is in --org;
      - self: a message this session sent matching SELF_CORRECTION_RE.
    A plain tool error (file not found, a failing command) is not a caught failure.
  * Branch commits: the commit, rebase and cherry-pick entries in the branch's reflog that
    are still in its history (so a reset or rebase onto a later main does not count main's
    commits); without a reflog, main..tip. First commit = the earliest author time among
    them. A branch with no commits of its own has no timing. Commits brought in by
    `git reset` are not counted, so `reset_entries` > 0 means first_commit may be late.
  * Landing: the earliest entry of --land-ref's reflog (default origin/main: each push)
    that contains the tip. Without such an entry, the commit time of the first main
    first-parent commit containing the tip (land_source says which). A push this clone
    never recorded reads as not landed.

OUTPUT. JSON on stdout, or to --out. An --out path inside the repo must be gitignored
(run output is personal data and this repo is public).

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/run_metrics.py \
      --start 2026-01-10T16:00:00-08:00 --end 2026-01-11T09:00:00-08:00 \
      --org <org-session-name> --run-id <id> --out output/<dir>/run-metrics.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# tools/ is sys.path[0] only for a script run; this line keeps `import tools.run_metrics` working
# (tests/scripts/test_shared_module_package_import.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_runtime import claude_project_dir  # noqa: E402



def transcripts_dir_for(repo: Path) -> Path:
    """Claude Code's transcript folder for a repo, from the repo's one canonical helper
    (hook_runtime.claude_project_dir: "/" becomes "-", dots and spaces kept,
    CLAUDE_CONFIG_DIR honoured). Derived, so no home-directory name is typed into this
    public file; delegated, so there is no second copy of the rule (Grok r2 F1)."""
    return claude_project_dir(repo)

EXCERPT_CHARS = 240
MATCH_CHARS = 200

RUN_FIELDS = (
    "run_id", "window_start", "window_end", "org", "sessions_count", "sessions",
    "org_messages_sent", "nick_prompts_total", "nick_minutes_total",
    "branches", "failure_candidates", "candidates_by_catcher_class", "definitions",
)
SESSION_FIELDS = (
    "name", "session_id", "first", "last", "wall_min", "active_min",
    "nick_prompts", "nick_minutes", "sent_messages", "branches",
)

CORRECTION_RE = re.compile(
    r"\b(wrong|incorrect|correction|corrected|mistake|stale|missed|overwr\w*|race|"
    r"not true|isn't true|that's not|you didn't|didn't you|why (?:did|didn't|aren't|isn't)|duplicate)\b",
    re.I)
# Nick catches by asking or redirecting more often than by naming an error.
NICK_CATCH_RE = re.compile(r"^\s*(?:why\b|are you\b|did you\b|have you\b|again\b|make sure\b)", re.I)
SELF_CORRECTION_RE = re.compile(r"\b(correction|corrected|i was wrong|my mistake|i misstated|retract\w*)\b", re.I)
HOOK_RE = re.compile(r"(check_[a-z0-9_]+\.py|[a-z0-9_]+_guard\.py)")
WORKTREE_RE = re.compile(r"(?:claude-worktrees|\.claude/worktrees)/([A-Za-z0-9._-]+)")
XSESSION_RE = re.compile(r'<cross-session-message[^>]*from-name="([^"]*)"[^>]*>\n?(.*?)(?:</cross-session-message>|$)', re.S)


def parse_ts(s: str) -> float:
    """ISO 8601 (with Z or an offset) to epoch seconds."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def iso(t: float | None) -> str | None:
    if t is None:
        return None
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def active_minutes(stamps: list[float], idle_cap_min: float = 10) -> float:
    stamps = sorted(stamps)
    total = 0.0
    for a, b in zip(stamps, stamps[1:]):
        gap = b - a
        if gap <= idle_cap_min * 60:
            total += gap
    return total / 60


def _norm(text: str) -> str:
    return " ".join(text.split())[:MATCH_CHARS]


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "tool_result":
                c = b.get("content")
                parts.append(c if isinstance(c, str) else _text_of(c))
    return "\n".join(parts)


@dataclass
class Session:
    session_id: str
    stamps: list = field(default_factory=list)
    assistant_stamps: list = field(default_factory=list)
    human: list = field(default_factory=list)      # (t, text)
    received: list = field(default_factory=list)   # (t, from_name, body)
    sent: list = field(default_factory=list)       # (t, to, body)
    hook_errors: list = field(default_factory=list)  # (t, text)
    branches: set = field(default_factory=set)
    worktrees: set = field(default_factory=set)
    lookbehind: float | None = None
    _seen_received: set = field(default_factory=set, repr=False)

    @property
    def first(self):
        return min(self.stamps)

    @property
    def last(self):
        return max(self.stamps)

    @property
    def nick_prompts(self):
        return len(self.human)

    def nick_minutes(self, cap_min: float = 10) -> float:
        prior = sorted(self.assistant_stamps + ([self.lookbehind] if self.lookbehind else []))
        total = 0.0
        for t, _ in self.human:
            before = [a for a in prior if a < t]
            if before:
                total += min(t - before[-1], cap_min * 60)
        return total / 60


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _add_received(s: "Session", t: float, text: str) -> None:
    """Record each cross-session message in `text` once per session (same sender and body)."""
    for m in XSESSION_RE.finditer(text):
        key = (m.group(1), _norm(m.group(2)))
        if key in s._seen_received:
            continue
        s._seen_received.add(key)
        s.received.append((t, m.group(1), m.group(2)))


def load_session(path: Path, start: float, end: float) -> Session | None:
    s = Session(session_id=path.stem)
    with open(path, errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            raw = d.get("timestamp")
            if not raw or d.get("isSidechain"):
                continue
            try:
                t = parse_ts(raw)
            except ValueError:
                continue
            if t < start:
                # Keep the last output before the window: a Nick prompt just inside the
                # window replies to it (Codex r1 F1). It is look-behind, not activity.
                if d.get("type") == "assistant":
                    s.lookbehind = max(s.lookbehind or t, t)
                continue
            if t > end:
                continue
            kind = d.get("type")
            if kind not in ("user", "assistant"):
                # A busy session receives peer messages as queue operations or attachments,
                # not as user lines. They are not activity, so they add no timestamp.
                if kind in ("queue-operation", "attachment"):
                    for text in _strings(d):
                        if "cross-session-message" in text:
                            _add_received(s, t, text)
                continue
            s.stamps.append(t)
            br = d.get("gitBranch")
            if br:
                s.branches.add(br)
            content = (d.get("message") or {}).get("content")
            if kind == "assistant":
                s.assistant_stamps.append(t)
                for b in content or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        for text in _strings(b.get("input") or {}):
                            s.worktrees.update(WORKTREE_RE.findall(text))
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "SendMessage":
                        inp = b.get("input") or {}
                        if inp.get("message"):
                            s.sent.append((t, inp.get("to", ""), inp["message"]))
                continue
            origin = (d.get("origin") or {}).get("kind")
            text = _text_of(content)
            if origin == "human" and not d.get("isMeta"):
                s.human.append((t, text))
                continue
            _add_received(s, t, text)
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        et = _text_of([b])
                        if HOOK_RE.search(et) and re.search(r"BLOCKED|hook error", et):
                            s.hook_errors.append((t, et))
    return s if s.stamps else None


def resolve_names(sessions: list[Session]) -> dict:
    received = {}
    for s in sessions:
        for _, from_name, body in s.received:
            received.setdefault(_norm(body), from_name)
    names = {}
    for s in sessions:
        found = None
        for _, _, body in s.sent:
            hit = received.get(_norm(body))
            if hit:
                found = hit
                break
        names[s.session_id] = found
    return names


def display_name(s: Session, names: dict) -> str:
    return names.get(s.session_id) or f"sid:{s.session_id[:8]}"


def _event(t, session_name, catcher, cls, kind, text):
    flat = " ".join(text.split())
    return {"ts": iso(t), "session": session_name, "catcher": catcher, "catcher_class": cls,
            "kind": kind, "excerpt": flat[:EXCERPT_CHARS],
            "fingerprint": hashlib.sha1(flat.encode()).hexdigest()[:12]}


def failures(s: Session, names: dict, org_names: set) -> list:
    me = display_name(s, names)
    out = []
    for t, text in s.hook_errors:
        m = HOOK_RE.search(text)
        out.append(_event(t, me, f"hook:{m.group(1)}", "hook", "hook_block", text))
    for t, text in s.human:
        if CORRECTION_RE.search(text) or NICK_CATCH_RE.search(text):
            out.append(_event(t, me, "nick", "nick", "nick_correction", text))
    for t, sender, body in s.received:
        if CORRECTION_RE.search(body):
            cls = "org" if sender in org_names else "peer"
            out.append(_event(t, me, sender, cls, "peer_correction", body))
    for t, _, body in s.sent:
        if SELF_CORRECTION_RE.search(body):
            out.append(_event(t, me, me, "self", "self_correction", body))
    return sorted(out, key=lambda e: e["ts"])


def _git(repo: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _ok(repo: Path, *args) -> bool:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True).returncode == 0


def _reflog(repo: Path, ref: str) -> list:
    """(sha, epoch, subject) per reflog entry, oldest first; [] when the ref has no reflog."""
    try:
        out = _git(repo, "reflog", "show", "--date=unix", "--format=%H %gd %gs", ref)
    except subprocess.CalledProcessError:
        return []
    rows = []
    for line in out.splitlines():
        # The fixed --format always matches; a line that does not is a git change worth a crash.
        m = re.match(r"([0-9a-f]{40}) \S*@\{(\d+)\} ?(.*)", line)
        rows.append((m.group(1), int(m.group(2)), m.group(3)))
    return list(reversed(rows))


MADE_ON_BRANCH_RE = re.compile(r"^(commit|rebase|cherry-pick)\b")


def branch_timing(repo: Path, branch: str, main_ref: str = "main",
                  land_ref: str | None = "refs/remotes/origin/main") -> dict:
    """First commit, landing time and minutes between them for one local branch.

    Branch commits are the commits its reflog records it making that are still in its
    history; without a reflog, main..tip. Landing is the earliest
    entry of land_ref's reflog that contains the tip (a push); without one, the commit time
    of the first main first-parent commit containing the tip.
    """
    repo = Path(repo)
    rec = {"branch": branch, "commits": 0, "first_commit": None, "landed": None,
           "land_source": None, "time_to_land_min": None, "reset_entries": 0}
    if not _ok(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"):
        rec["error"] = "branch not found"
        return rec
    tip = _git(repo, "rev-parse", f"refs/heads/{branch}^{{commit}}")
    if not _ok(repo, "rev-parse", "--verify", "--quiet", f"{main_ref}^{{commit}}"):
        rec["error"] = "main ref not found"
        return rec
    main = _git(repo, "rev-parse", f"{main_ref}^{{commit}}")
    blog = _reflog(repo, f"refs/heads/{branch}")
    rec["reset_entries"] = sum(1 for _, _, subject in blog if subject.startswith("reset:"))
    if blog:
        # The commits this branch made (commit, rebase, cherry-pick entries) that are still in
        # its history. A reset onto a later main adds main's commits to base..tip, so the
        # creation point alone over-counts.
        commits = []
        for sha, _, subject in blog:
            if not MADE_ON_BRANCH_RE.match(subject):
                continue
            onto = re.search(r"\bonto ([0-9a-f]{40})\b", subject)
            # A finished rebase is one reflog entry for every commit it replayed (Codex r2 F1).
            made = (_git(repo, "rev-list", "--reverse", f"{onto.group(1)}..{sha}").split()
                    if subject.startswith("rebase") and onto else [sha])
            for c in made:
                if c not in commits and _ok(repo, "merge-base", "--is-ancestor", c, tip):
                    commits.append(c)
    else:
        commits = _git(repo, "rev-list", "--reverse", f"{main}..{tip}").split()
    rec["commits"] = len(commits)
    if not commits:
        return rec
    first_t = min(int(_git(repo, "show", "-s", "--format=%at", c)) for c in commits)
    rec["first_commit"] = iso(first_t)

    land_t = None
    if land_ref:
        for sha, t, _ in _reflog(repo, land_ref):
            if _ok(repo, "merge-base", "--is-ancestor", tip, sha):
                land_t, rec["land_source"] = t, f"reflog:{land_ref}"
                break
    if land_t is None and _ok(repo, "merge-base", "--is-ancestor", tip, main):
        chain = _git(repo, "rev-list", "--first-parent", "--reverse", main).split()
        # main itself is on the chain and contains the tip, so a containing commit always exists.
        landed_sha = next(c for c in chain if _ok(repo, "merge-base", "--is-ancestor", tip, c))
        land_t = int(_git(repo, "show", "-s", "--format=%ct", landed_sha))
        rec["land_source"] = "commit-time"
    if land_t is not None:
        rec["landed"] = iso(land_t)
        rec["time_to_land_min"] = round((land_t - first_t) / 60, 1)
    return rec


def dedupe_failures(events: list) -> list:
    """One event per (catcher, message). A message that is both the sender's self-correction
    and a correction seen by its receivers is kept once, as self."""
    rank = {"self": 0, "hook": 1, "nick": 1, "org": 2, "peer": 2}
    best = {}
    for e in sorted(events, key=lambda e: (rank.get(e["catcher_class"], 9), e["ts"])):
        fp = e.get("fingerprint") or e["excerpt"]
        # A hook block is one event per firing; a message is one event however many sessions saw it.
        key = (e["catcher"], fp, e["ts"], e["session"]) if e["catcher_class"] == "hook" else (e["catcher"], fp)
        best.setdefault(key, e)
    return sorted(best.values(), key=lambda e: e["ts"])


def worktree_branches(repo: Path) -> dict:
    """Map each worktree's directory name to its checked-out branch (detached ones are left out)."""
    out, path = {}, None
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/") and path:
            out[Path(path).name] = line[len("branch refs/heads/"):]
    return out


def build_report(transcripts: Path, repo: Path, start: str, end: str, org_names: set,
                 run_id: str, main_ref: str = "main", idle_cap_min: float = 10,
                 nick_cap_min: float = 10, land_ref: str | None = "refs/remotes/origin/main") -> dict:
    t0, t1 = parse_ts(start), parse_ts(end)
    sessions = []
    for p in sorted(Path(transcripts).glob("*.jsonl")):
        if os.path.getmtime(p) < t0:
            # Speed guard, not an equivalence: a transcript copied or restored with an old
            # mtime would be skipped (Codex r3 F2, parked; 0 of 395 live files on 2026-11-23).
            continue
        s = load_session(p, t0, t1)
        if s:
            sessions.append(s)
    names = resolve_names(sessions)
    wt_map = worktree_branches(Path(repo))
    fails, srows, branches = [], [], set()
    dispatches = 0
    for s in sessions:
        name = display_name(s, names)
        fails.extend(failures(s, names, org_names))
        found = s.branches | {wt_map[w] for w in s.worktrees if w in wt_map}
        bs = sorted(b for b in found if b not in (main_ref, "HEAD"))
        branches.update(bs)
        if name in org_names:
            dispatches += len(s.sent)
        srows.append({
            "name": name, "session_id": s.session_id, "first": iso(s.first), "last": iso(s.last),
            "wall_min": round((s.last - s.first) / 60, 1),
            "active_min": round(active_minutes(s.stamps, idle_cap_min), 1),
            "nick_prompts": s.nick_prompts, "nick_minutes": round(s.nick_minutes(nick_cap_min), 1),
            "sent_messages": len(s.sent), "branches": bs,
        })
    fails = dedupe_failures(fails)
    by_class = {}
    for f in fails:
        by_class[f["catcher_class"]] = by_class.get(f["catcher_class"], 0) + 1
    return {
        "run_id": run_id, "window_start": start, "window_end": end, "org": sorted(org_names),
        "sessions_count": len(srows), "sessions": sorted(srows, key=lambda r: r["first"]),
        "org_messages_sent": dispatches,
        "nick_prompts_total": sum(r["nick_prompts"] for r in srows),
        "nick_minutes_total": round(sum(r["nick_minutes"] for r in srows), 1),
        "branches": [branch_timing(Path(repo), b, main_ref, land_ref) for b in sorted(branches)],
        "failure_candidates": sorted(fails, key=lambda e: e["ts"]),
        "candidates_by_catcher_class": by_class,
        "definitions": {"idle_cap_min": idle_cap_min, "nick_cap_min": nick_cap_min,
                        "doc": "see tools/run_metrics.py module docstring, DEFINITIONS"},
    }


def check_out_path(out: Path, repo: Path) -> None:
    out, repo = Path(out).resolve(), Path(repo).resolve()
    try:
        rel = out.relative_to(repo)
    except ValueError:
        return
    tracked = subprocess.run(["git", "-C", str(repo), "ls-files", "--error-unmatch", str(rel)],
                             capture_output=True).returncode == 0
    ignored = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", str(rel)]).returncode == 0
    if tracked or not ignored:
        sys.exit(f"run_metrics: refusing --out {rel}: inside the repo and not gitignored "
                 "(run metrics are personal data; this repo is public). Use output/.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--transcripts", type=Path, default=None,
                    help="transcript folder; default: Claude Code's folder for --repo")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--org", action="append", default=[], help="org session name; repeatable (handovers)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--main-ref", default="main")
    ap.add_argument("--land-ref", default="refs/remotes/origin/main",
                    help="ref whose reflog records pushes; landing = first entry containing the tip")
    ap.add_argument("--idle-cap-min", type=float, default=10)
    ap.add_argument("--nick-cap-min", type=float, default=10)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    if a.out:
        check_out_path(a.out, a.repo)
    transcripts = a.transcripts or transcripts_dir_for(a.repo)
    if not Path(transcripts).is_dir():
        # A missing folder would read as a run with 0 sessions (Grok r2 F1).
        print(f"run_metrics: no transcript folder at {transcripts}; pass --transcripts", file=sys.stderr)
        return 2
    rep = build_report(transcripts, a.repo, a.start, a.end, set(a.org), a.run_id, a.main_ref,
                       a.idle_cap_min, a.nick_cap_min, a.land_ref)
    text = json.dumps(rep, indent=2)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
