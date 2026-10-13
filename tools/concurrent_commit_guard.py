#!/usr/bin/env python3
"""Refuse a commit that carries a path the committing Claude Code session never named.

WHY
---
More than one Claude Code session works in this tree at once. Another session's
uncommitted edits look like ordinary dirty files, and its staged files sit in the same
index. `git add -A`, `git add .`, `git commit -a`, or a plain `git commit` after a
path-scoped `git add`, all commit work this session did not write, under a message
that does not describe it. On this PUBLIC repo that work has carried a PII leak.

WHAT IT CHECKS
--------------
Run from tools/hooks/pre-commit. It takes the staged path list from git (the index git
hands the hook, so `git commit -- <paths>` shows only those paths) and compares it with
what this session's own transcript shows the session naming:

  * the file_path of every Edit / Write / MultiEdit / NotebookEdit call, and
  * every path spelled out in a Bash command (repo-relative or absolute).

The session is identified by CLAUDE_CODE_SESSION_ID. Its transcripts are
<config>/projects/*/<id>.jsonl plus every .jsonl under <config>/projects/*/<id>/
(subagents and workflow agents), so a subagent's edits count as the session's own.

A staged path that appears in neither list is refused. Naming the path in a command is
what attributes it, so `git commit -m "..." -- <paths>` passes by construction, and so
does a deliberate `git add <their file>` when a peer's work is being landed on purpose.

WHAT IT CANNOT DETECT (read before trusting a pass)
---------------------------------------------------
  * A path this session named in ANY Bash command counts, including `cat <path>` or
    `git diff <path>`. Only the Read / Grep / Glob tools are excluded. A file this
    session inspected by shell and another session edited is attributed to this one.
  * Two sessions editing the SAME file: the path is attributed, the other session's
    hunks ride along. The guard compares paths, never content.
  * Sibling subagents of one session are one session here. It cannot separate them.
  * Files a script generated without their path appearing in a command (a tool that
    writes its own output file, `git mv` into a directory, a `cd` followed by a
    relative name, a directory pathspec such as `git add tools/`) are NOT attributed
    and are refused. Those are false positives, cleared with the override.
  * A commit made outside Claude Code (no CLAUDE_CODE_SESSION_ID) is not judged.
  * `git commit --no-verify` skips every pre-commit hook, this one included.
  * It guards commits only. Overwriting a gitignored file another session is writing,
    or the clipboard, is outside what a git hook can see.
  * If the session's transcript cannot be found, attribution is impossible and the
    guard falls back to the narrower check: refuse when another live `claude` process
    has its working directory inside this repo, pass when none does.

Override, recorded in <git dir>/concurrent-commit-waivers.jsonl:
    CONCURRENT_COMMIT_WAIVE="why" git commit ...

Usage:  PYTHONIOENCODING=utf-8 python3 tools/concurrent_commit_guard.py --repo-root <root>
Exit:   0 pass or waived, 1 refused.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
WAIVE_ENV = "CONCURRENT_COMMIT_WAIVE"
WAIVER_LOG = "concurrent-commit-waivers.jsonl"

# Tools whose input names a file the session WROTE. Read / Grep / Glob are absent on
# purpose: opening a file does not make its uncommitted changes yours.
_EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}
_CLAUDE_NAMES = {"claude", "claude.exe"}


@dataclass
class Claims:
    """What one session's transcripts show it naming."""
    edited: set[str] = field(default_factory=set)      # raw file_path values
    commands: list[str] = field(default_factory=list)  # raw Bash command strings


@dataclass
class Verdict:
    blocked: bool
    paths: list[str] = field(default_factory=list)
    mode: str = ""   # "attribution" | "no-transcript" | "not-a-session" | "nothing-staged"


# ------------------------------------------------------------------ transcript reading

def config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def transcript_files(cfg: Path, session_id: str) -> list[Path]:
    """The session's main transcript plus every agent transcript beneath it."""
    out: list[Path] = []
    projects = Path(cfg) / "projects"
    if not session_id or not projects.is_dir():
        return out
    for proj in projects.iterdir():
        main = proj / f"{session_id}.jsonl"
        if main.is_file():
            out.append(main)
        # rglob on a directory that does not exist yields nothing.
        out.extend(sorted((proj / session_id).rglob("*.jsonl")))
    return out


def session_claims(files: list[Path]) -> Claims:
    claims = Claims()
    for f in files:
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue          # a blank or half-written line
                msg = rec.get("message") if isinstance(rec, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    inp = block.get("input")
                    if not isinstance(inp, dict):
                        continue
                    name = block.get("name")
                    if name in _EDIT_TOOLS:
                        p = inp.get(_EDIT_TOOLS[name])
                        if isinstance(p, str) and p:
                            claims.edited.add(p)
                    elif name == "Bash":
                        c = inp.get("command")
                        if isinstance(c, str) and c:
                            claims.commands.append(c)
    return claims


# ------------------------------------------------------------------------ attribution

def _roots(root: Path) -> list[str]:
    """Both spellings of the repo root (macOS: /tmp and /private/tmp), longest first."""
    seen = {str(root), os.path.realpath(root)}
    return sorted((r.rstrip("/") for r in seen), key=len, reverse=True)


def _to_rel(path: str, roots: list[str]) -> str | None:
    """Repo-relative form of an edited path, or None when it lies outside the repo."""
    if os.path.isabs(path):
        for cand in (path, os.path.realpath(path)):
            for r in roots:
                if cand.startswith(r + "/"):
                    return os.path.normpath(cand[len(r) + 1:])
        return None
    return os.path.normpath(path)


def unattributed(staged: list[str], claims: Claims, root: Path) -> list[str]:
    """Staged paths the session named nowhere. Order of `staged` is preserved."""
    roots = _roots(Path(root))
    edited = {r for r in (_to_rel(p, roots) for p in claims.edited) if r}
    text = "\n".join(claims.commands)
    for r in roots:
        text = text.replace(r + "/", "")
    text = re.sub(r"(?<![\w.])\./", "", text)          # ./tools/a.py -> tools/a.py
    missing = []
    for path in staged:
        if path in edited:
            continue
        # The path as a whole token: not the tail of a longer path, not the head of
        # one, and not followed by a further extension (a.py is not a.py.bak).
        pat = r"(?<![\w./~-])" + re.escape(path) + r"(?![\w/-]|\.\w)"
        if text and re.search(pat, text):
            continue
        missing.append(path)
    return missing


# --------------------------------------------------------------------- peer detection

def _ancestry(pid: int, parent: dict[int, int]) -> list[int]:
    """pid, then its parent, then its grandparent, as far as the table goes.

    Bounded by the table size, because the table can hold a loop: macOS lists pid 0
    as its own parent.
    """
    chain: list[int] = []
    for _ in range(len(parent)):
        if pid not in parent:
            break
        chain.append(pid)
        pid = parent[pid]
    return chain


def peer_sessions(rows, own_pid: int, root: Path) -> list[int]:
    """PIDs of `claude` processes working in this repo that are not this session.

    rows: iterable of (pid, ppid, command name, cwd). Excluded: every ancestor of
    own_pid, and every descendant of the nearest `claude` ancestor (processes this
    session itself spawned).
    """
    rows = list(rows)
    parent = {pid: ppid for pid, ppid, _c, _d in rows}
    name = {pid: os.path.basename(c) for pid, _p, c, _d in rows}

    chain = _ancestry(own_pid, parent)
    session = next((x for x in chain if name.get(x) in _CLAUDE_NAMES), None)

    roots = _roots(Path(root))
    peers = []
    for pid, _ppid, comm, cwd in rows:
        if os.path.basename(comm) not in _CLAUDE_NAMES or pid in chain:
            continue
        if session in _ancestry(pid, parent):      # never true when session is None
            continue
        cands = {cwd, os.path.realpath(cwd)} if cwd else set()
        if any(c == r or c.startswith(r + "/") for c in cands for r in roots):
            peers.append(pid)
    return sorted(peers)


def live_process_rows():
    """(pid, ppid, command name, cwd) for every process; cwd filled for claude only."""
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="], capture_output=True,
                            text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in ps.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        pid, ppid, comm = int(parts[0]), int(parts[1]), parts[2].strip()
        cwd = ""
        if os.path.basename(comm) in _CLAUDE_NAMES:
            try:
                out = subprocess.run(["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"],
                                     capture_output=True, text=True, timeout=10).stdout
                cwd = next((x[1:] for x in out.splitlines() if x.startswith("n")), "")
            except (OSError, subprocess.SubprocessError):
                cwd = ""
        rows.append((pid, ppid, comm, cwd))
    return rows


# --------------------------------------------------------------------------- decision

def decide(staged: list[str], *, session_id: str, transcripts: list[Path],
           peers: list[int], root: Path) -> Verdict:
    if not staged:
        return Verdict(False, mode="nothing-staged")
    if not session_id:
        return Verdict(False, mode="not-a-session")
    if not transcripts:
        # Attribution is impossible. The narrower check that is still reliable.
        return Verdict(bool(peers), list(staged) if peers else [], "no-transcript")
    missing = unattributed(staged, session_claims(transcripts), root)
    return Verdict(bool(missing), missing, "attribution")


# -------------------------------------------------------------------------------- git

def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(root), capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace')}")
    return r.stdout.decode("utf-8", errors="surrogateescape")


def staged_paths(root: Path) -> list[str]:
    """Paths in the index git handed this hook. --no-renames lists both sides."""
    out = _git(root, "diff", "--cached", "--name-only", "--no-renames", "-z")
    return [p for p in out.split("\0") if p]


def record_waiver(root: Path, paths: list[str], reason: str, mode: str) -> Path:
    # git prints the path relative to the repo root in an ordinary checkout and absolute
    # in a linked worktree. Joining an absolute path onto root leaves it unchanged.
    log = Path(root) / _git(root, "rev-parse", "--git-path", WAIVER_LOG).strip()
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "reason": reason, "mode": mode,
           "session": os.environ.get(SESSION_ENV, ""), "paths": paths}
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return log


def _message(v: Verdict, peers: list[int]) -> str:
    listing = "\n".join(f"    {p}" for p in v.paths)
    if peers:
        peer_line = (f"{len(peers)} other live claude process(es) have this repo as "
                     f"working directory (pid {', '.join(map(str, peers))}).")
    else:
        peer_line = ("No other live claude process has this repo as working directory "
                     "right now; the paths may be left over from an ended session.")
    if v.mode == "no-transcript":
        why = ("This session's transcript was not found, so no staged path can be "
               "attributed to it, and another session is live in this tree.")
    else:
        why = (f"{len(v.paths)} staged path(s) were never named by this session: no "
               "Edit/Write call on them and no Bash command spelling them out.")
    return (
        "\nBLOCKED: concurrent-session commit guard\n\n"
        f"{why}\n{peer_line}\n\n{listing}\n\n"
        "If they are another session's work, leave them out. This form commits only\n"
        "the paths you name and ignores the rest of the index:\n"
        "    git commit -m \"...\" -- <your paths>\n"
        "To unstage one instead:  git restore --staged <path>\n"
        "    (not `git rm --cached`, which stages a deletion of a tracked file)\n\n"
        "If they ARE yours (a script generated them, or you are landing a peer's\n"
        "finished work on purpose), override with a reason. It is recorded:\n"
        f"    {WAIVE_ENV}='<why>' git commit ...\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    waive = os.environ.get(WAIVE_ENV, "").strip()

    try:
        staged = staged_paths(root)
        session_id = os.environ.get(SESSION_ENV, "").strip()
        transcripts = transcript_files(config_dir(), session_id) if session_id else []
        need_peers = bool(staged) and bool(session_id) and not transcripts
        peers = (peer_sessions(live_process_rows(), os.getpid(), root)
                 if need_peers else [])
        v = decide(staged, session_id=session_id, transcripts=transcripts,
                   peers=peers, root=root)
        if not v.blocked:
            return 0
        if waive:
            log = record_waiver(root, v.paths, waive, v.mode)
            print(f"concurrent-session commit guard WAIVED: {waive}\n"
                  f"  {len(v.paths)} path(s) recorded in {log}", file=sys.stderr)
            return 0
        if not need_peers:
            peers = peer_sessions(live_process_rows(), os.getpid(), root)
        print(_message(v, peers), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- a guard that crashes must say so
        if waive:
            print(f"concurrent-session commit guard errored ({exc}); waived: {waive}",
                  file=sys.stderr)
            return 0
        print(f"\nBLOCKED: concurrent-session commit guard could not run: {exc}\n"
              f"  Override with a reason:  {WAIVE_ENV}='<why>' git commit ...\n",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
