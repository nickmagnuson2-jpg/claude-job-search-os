#!/usr/bin/env python3
"""launch_swarm.py - open one Terminal window per overnight-swarm section, but only from a
brief that passes brief_check.

WHY. Across 9 overnight runs, about 12-14 failures came from what the brief left out:
no ranked backlog, no file ownership, no standby org, rules sent by message after launch
(DECISIONS.md row C1, 2026-11-21). On 2026-11-23 the same shape fired three times before
any section had worked an hour: the brief named a previous org while the launch named
another; no section had a log file and the logging rules arrived after launch; rules
12-18 were added to the brief after all sessions had read it. A template alone is prose.
This launcher is the step every swarm already goes through, so the template's
requirements are checked here, before any window opens.

WHAT brief_check REQUIRES (framework/templates/overnight-brief.md is the shape):
  * an `Organizer: **<name>**` line equal to --org, a `Standby org: **<name>**` line and a
    `Needs Nick:` line, and output/<run>/NEEDS-NICK.md existing next to the brief;
  * the headings Ranked backlog, Rules for every session, Sections and Close-out: run
    metrics, with the backlog before the sections;
  * rules numbered 1 to 18 under Rules for every session;
  * at least one `### <ID>: <title> | moves: <...>` section, each with Owns, Log file
    (a backticked path) and Done when lines; no two sections owning the same backticked path;
  * no unfilled `<...>` placeholder in the header lines, the backlog, the rules-checked
    table or the sections. (Placeholders inside the shared rules and in fenced code are
    meant to stay.)

  * a Codex (family openai) AND a Grok (family xai) review of the brief's CURRENT text in
    tools/.cross-model-ledger.jsonl, with every finding on those rows dispositioned
    (review_check, below).

THE REVIEW CHECK. On 2026-11-24 a brief passed every check above while Codex and Grok had
both found the same P0 in it (ledger 1323/1324 F1); the checks read form, not substance.
A ledger row covers the brief when the row is usable, not a waiver, and its `target` holds
a block `===BRIEF START===` ... `===BRIEF END===` whose stripped text EQUALS the brief's
stripped current text. Every brief review in the ledger so far (1323-1332) is framed this
way. Equality, not containment: containment let a reviewed brief launch after text was cut
from its start or end (Codex, S1 r1 F1, P0). Matching is on the text, not a git tree
(the push gate's key), because briefs live in gitignored output/; and codex_verify refuses
gitignored --paths, so a brief review inlines the brief in --target. Rows are read and
numbered exactly as cross_model_gate and finding_write read them, so a refusal names
addresses `finding_write.py set` accepts. Known limits, accepted by the org 2026-11-24:
  * a review fed an excerpt, or a target without the markers, never matches (fails closed);
  * open findings on rows that reviewed an EARLIER text of the brief do not block once
    both families have reviewed the current text. Mitigation: any such row whose framed
    brief carries the same title line and has an open P0 is printed as a WARNING with its
    address, so the org sees it.

THE PROMPT each window starts with is written here (section_prompt), including the logging
block, so that text has one copy.

ADOPTION IS NOT ENFORCED: a swarm opened by hand skips all of this.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/launch_swarm.py output/overnight-<MMDDYY>/brief.md --org <name> [--dry-run] [SECTION ...]
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cross_model_gate as cmg  # tools/ is sys.path[0] when this runs as a script
from finding_write import read_lines

REQUIRED_HEADINGS = ("Ranked backlog", "Rules for every session", "Sections", "Close-out: run metrics")
RULE_COUNT = 18
LOGGING_BLOCK_MARK = "LOGGING IS MANDATORY"
SECTION_RE = re.compile(r"^### ([SL]\d+): (.*?)\s*$", re.M)
PLACEHOLDER_RE = re.compile(r"<[^<>\n]+>")
MOVES = {"conversations", "applications", "speed"}
TEMPLATE = Path(__file__).resolve().parents[1] / "framework" / "templates" / "overnight-brief.md"
LEDGER_ROOT = Path(__file__).resolve().parents[1]
REVIEW_FAMILIES = {"openai": "codex", "xai": "grok"}  # family -> the /verify model that supplies it
# Greedy, first START to last END: the brief itself may contain the marker text (the template's
# own HOW TO USE comment does), and review_command puts no marker before or after the frame.
# A target with two frames is read as one and covers nothing (fails closed). Codex S1 r2 F1.
BRIEF_FRAME_RE = re.compile(r"===BRIEF START===(.*)===BRIEF END===", re.S)
REVIEW_ASK = ("Plan review of this overnight swarm brief (shape: framework/templates/overnight-brief.md). "
              "A finding must show a section that cannot reach its Done-when, a claim false against the "
              "named code, a violated constraint, two sections editing one tracked file, or a misstated "
              "number. Severity P0/P1/P2.")


def review_command(brief: str | Path, model: str, repo: Path | None = None) -> str:
    """The one review command whose ledger row covers this brief: the brief's file is inlined
    between the frame markers by $(cat), so the row carries exactly its text. The template's
    HOW TO USE block prints this same command (test-enforced)."""
    cd = f"cd {shlex.quote(str(repo))} && " if repo else ""
    return (f"{cd}PYTHONIOENCODING=utf-8 python3 tools/codex_verify.py --model {model} "
            f"--target \"{REVIEW_ASK} ===BRIEF START===$(cat {shlex.quote(str(brief))})===BRIEF END===\"")
BACKLOG_ROW_RE = re.compile(r"^\|\s*\d+\s*\|", re.M)


@dataclass
class Section:
    id: str
    title: str
    moves: str | None
    owns: str | None
    log_file: str | None
    done_when: str | None
    body: str


@dataclass
class Brief:
    org: str | None
    standby: str | None
    needs_nick: str | None
    headings: list
    sections: list
    text: str


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _field(body: str, name: str) -> str | None:
    m = re.search(rf"^- \*\*{re.escape(name)}:\*\*\s*(.*)$", body, re.M)
    return m.group(1).strip() if m and m.group(1).strip() else None


def _h2_block(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*$", text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    n = re.search(r"^## ", rest, re.M)
    return rest[: n.start()] if n else rest


def parse_brief(text: str) -> Brief:
    text = _strip_comments(text)
    org = re.search(r"^Organizer: \*\*([^*]+)\*\*", text, re.M)
    standby = re.search(r"^Standby org: \*\*([^*]+)\*\*", text, re.M)
    needs = re.search(r"^Needs Nick: `([^`]+)`", text, re.M)
    headings = re.findall(r"^## (.+?)\s*$", text, re.M)
    sections_block = _h2_block(text, "Sections")
    sections = []
    matches = list(SECTION_RE.finditer(sections_block))
    for i, m in enumerate(matches):
        body = sections_block[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(sections_block)]
        head = m.group(2)
        title, _, moves = head.partition("| moves:")
        log = _field(body, "Log file")
        log_path = re.fullmatch(r"`([^`]+)`", log or "")
        sections.append(Section(
            id=m.group(1), title=title.strip(), moves=moves.strip() or None,
            owns=_field(body, "Owns"), log_file=log_path.group(1) if log_path else None,
            done_when=_field(body, "Done when"), body=body))
    return Brief(org=org.group(1).strip() if org else None,
                 standby=standby.group(1).strip() if standby else None,
                 needs_nick=needs.group(1) if needs else None,
                 headings=headings, sections=sections, text=text)


def _rule_lines(text: str) -> dict:
    block = _h2_block(_strip_comments(text), "Rules for every session")
    return {int(m.group(1)): m.group(0).strip() for m in re.finditer(r"^(\d+)\. .*$", block, re.M)}


def template_rules(template: Path = TEMPLATE) -> dict:
    """The template's rules, each as a pattern in which its <placeholders> match any text.
    The template is the one copy of the shared rules; a brief carries them verbatim."""
    out = {}
    for n, line in _rule_lines(template.read_text()).items():
        parts = PLACEHOLDER_RE.split(line)
        out[n] = re.compile(".+?".join(re.escape(x) for x in parts) + r"\Z")
    return out


def _moves_ok(moves: str) -> bool:
    values = [v.strip().lower() for v in re.split(r"[,+/]|\band\b", moves) if v.strip()]
    return bool(values) and all(v in MOVES for v in values)


def _ledger_rows(ledger_root: Path) -> tuple[list, int]:
    """(run number, row) pairs numbered as finding_write numbers them, plus unreadable lines."""
    rows, corrupt = [], 0
    for n, line in enumerate(read_lines(cmg.ledger_path(ledger_root)), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = None
        if isinstance(row, dict):
            rows.append((n, row))
        else:
            corrupt += 1
    return rows, corrupt


def _open(row: dict) -> list:
    """Findings with a blank disposition: cross_model_gate.open_findings' definition."""
    return [f for f in row.get("findings") or []
            if isinstance(f, dict) and not (f.get("disposition") or "").strip()]


def framed_briefs(row: dict) -> list:
    """The stripped brief text a review row carries between its START/END markers, if any."""
    m = BRIEF_FRAME_RE.search(row.get("target") or "")
    return [m.group(1).strip()] if m else []


def review_check(text: str, ledger_root: Path, brief_path: Path | None = None) -> list:
    """Every reason the ledger does not show this brief text reviewed and dispositioned."""
    body = text.strip()
    rows, corrupt = _ledger_rows(ledger_root)
    if corrupt:
        return [f"{cmg.ledger_path(ledger_root)} has {corrupt} unreadable line(s); "
                f"a corrupt ledger cannot show the brief was reviewed"]
    covering = [(n, r) for n, r in rows
                if body and body in framed_briefs(r)
                and not r.get("waived") and cmg.row_is_usable(r)]
    problems = []
    have = {cmg.row_model(r) for _, r in covering}
    for fam, model in REVIEW_FAMILIES.items():
        if fam not in have:
            problems.append(f"no {model} review (family {fam}) in the cross-model ledger covers this brief's "
                            f"current text (an edit after a review voids it). Review it with:\n      "
                            f"{review_command(brief_path or '<brief.md>', model, ledger_root)}")
    for n, r in covering:
        for f in _open(r):
            problems.append(f"open finding {n}.{f.get('id')} ({f.get('severity', '?')}, {r.get('model', '?')}) "
                            f"on a review of this brief; disposition it with finding_write.py set")
    return problems


def review_warnings(text: str, ledger_root: Path) -> list:
    """Open P0s on reviews of an EARLIER text of this brief (same title line). Not blocking."""
    body = text.strip()
    title = next((ln for ln in body.splitlines() if ln.startswith("# ")), None)  # None matches no line
    out = []
    for n, r in _ledger_rows(ledger_root)[0]:
        framed = framed_briefs(r)
        if body not in framed and any(title in fb.splitlines() for fb in framed):
            for f in _open(r):
                if f.get("severity") == "P0":
                    out.append(f"open P0 {n}.{f.get('id')} ({r.get('model', '?')}) on a review of an earlier "
                               f"text of this brief: {f.get('summary', '')[:160]}")
    return out


def brief_check(path: Path, org: str, template: Path = TEMPLATE, ledger_root: Path | None = None) -> list:
    """Every reason this brief may not launch; [] means it may."""
    path = Path(path)
    raw = path.read_text()
    b = parse_brief(raw)
    problems = []

    if b.org is None:
        problems.append("no 'Organizer: **<name>**' line")
    elif b.org != org:
        problems.append(f"Organizer line names {b.org!r} but the launch names --org {org!r}")
    if b.standby is None:
        problems.append("no 'Standby org: **<name>**' line")
    if b.needs_nick is None:
        problems.append("no 'Needs Nick: `<path>`' line")
    if not (path.parent / "NEEDS-NICK.md").is_file():
        problems.append(f"{path.parent / 'NEEDS-NICK.md'} does not exist; create it before any window opens")

    for h in REQUIRED_HEADINGS:
        if h not in b.headings:
            problems.append(f"missing heading '## {h}'")
    if "Ranked backlog" in b.headings and "Sections" in b.headings:
        if b.headings.index("Ranked backlog") > b.headings.index("Sections"):
            problems.append("'## Ranked backlog' must come before '## Sections'")
    backlog = _h2_block(b.text, "Ranked backlog")
    if "Ranked backlog" in b.headings and not BACKLOG_ROW_RE.search(backlog):
        problems.append("'## Ranked backlog' has no ranked row ('| 1 | <item> | ...'); rank the backlog before the sections")
    section_ids = {s.id for s in b.sections}
    table = [ln for ln in backlog.splitlines() if ln.strip().startswith("|")]
    # Every table row after the header and separator is an item (Codex r3 F1: a row
    # whose rank is empty or not a number used to skip all row checks).
    for n, line in enumerate(table[2:] if len(table) >= 2 else [], start=1):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rank = cells[0] if cells[0].isdigit() else f"#{n}"
        if not cells[0].isdigit():
            problems.append(f"backlog row {rank}: rank {cells[0]!r} is not a number")
        # Rank | Item | Moves | Evidence | Section (Codex r2 F1: each row is checked, not only its presence)
        moves, evidence, section = (cells + ["", "", "", "", ""])[2:5]
        if not _moves_ok(moves):
            problems.append(f"backlog row {rank}: moves {moves!r} is not one or more of conversations, applications, speed")
        if not evidence:
            problems.append(f"backlog row {rank}: no evidence for its rank")
        named = [x for x in re.split(r"[,/ ]+", section) if x]
        missing = [x for x in named if x not in section_ids]
        if not named or missing:
            problems.append(f"backlog row {rank}: section {section!r} is not a section in this brief"
                            f"{' (' + ', '.join(missing) + ')' if missing else ''}")

    rules = _h2_block(b.text, "Rules for every session")
    numbers = {int(n) for n in re.findall(r"^(\d+)\. ", rules, re.M)}
    want = template_rules(template)
    have = _rule_lines(b.text)
    for n in range(1, RULE_COUNT + 1):
        if n not in numbers:
            problems.append(f"rule {n} is missing under '## Rules for every session'")
        elif n in want and not want[n].match(have.get(n, "")):
            problems.append(f"rule {n} does not match the template's rule text ({template.name}); copy it verbatim")

    if not b.sections:
        problems.append("no sections ('### S<n>: <title> | moves: <...>') under '## Sections'")
    owners = {}
    for s in b.sections:
        if not s.moves:
            problems.append(f"{s.id}: heading does not name what it moves ('| moves: ...')")
        elif not _moves_ok(s.moves):
            problems.append(f"{s.id}: moves {s.moves!r} is not one or more of conversations, applications, speed")
        if not s.owns:
            problems.append(f"{s.id}: no '- **Owns:**' line")
        if not s.log_file:
            problems.append(f"{s.id}: no '- **Log file:**' line with a backticked path")
        if not s.done_when:
            problems.append(f"{s.id}: no '- **Done when:**' line")
        for p in re.findall(r"`([^`]+)`", s.owns or ""):
            owners.setdefault(p, []).append(s.id)
    for p, ids in owners.items():
        if len(ids) > 1:
            problems.append(f"{p} is owned by more than one section: {', '.join(ids)}")

    checked = "\n".join(
        [ln for ln in b.text.splitlines() if re.match(r"^(# |Organizer:|Standby org:|Needs Nick:)", ln)]
        + [_h2_block(b.text, h) for h in ("Ranked backlog", "Rules checked against this plan", "Sections")])
    found = sorted(set(PLACEHOLDER_RE.findall(checked)))
    if found:
        problems.append(f"unfilled placeholder(s): {', '.join(found[:8])}")
    problems += review_check(raw, LEDGER_ROOT if ledger_root is None else ledger_root, path.resolve())
    return problems


def section_prompt(section: Section, org: str, brief_rel: str) -> str:
    x = section.id
    return (
        f"You are {x} in the swarm. Org is {org}. Read {brief_rel}: the rules, then section {x}. "
        f"Check in with {org} now.\n\n"
        f"{LOGGING_BLOCK_MARK}. Keep your log current the whole run:\n"
        f"- Log file: {section.log_file} (append only, with cat >>; other sessions share it).\n"
        f"- Write a dated section headed '## YYYY-MM-DD HH:MM by `date` | {x}: <what>' at start, at every commit, "
        f"at every review result, when blocked, when you change plan, and when done. Never longer than 30 minutes "
        f"between entries while you work.\n"
        f"- Time comes from running `date`, never an estimate. Each entry states what changed, the evidence "
        f"(sha, report path, count with its denominator), and what is next.\n"
        f"- Log first, then message {org} with the same facts. {org}'s check-in cron reads your newest "
        f"'| {x}:' heading and chases a gap over 30 minutes.\n"
    )


# A Terminal window opened from inside a Claude session inherits that session's env
# (CLAUDECODE, CLAUDE_CODE_CHILD_SESSION, CLAUDE_CODE_SESSION_ID, CLAUDE_PID, AI_AGENT).
# A claude started with them runs as a child session and writes no transcript, so
# run_metrics.py cannot see it (cause measured 2026-11-24, orchestration.md 17:38 S1).
# The names are read at run time, so a CLAUDE* var added later is dropped too.
CLEAN_ENV = "unset $(env | sed -n 's/^\\(CLAUDE[A-Za-z0-9_]*\\)=.*/\\1/p') AI_AGENT;"


def window_command(repo: Path, prompt_path: Path, program: str = "claude") -> str:
    """The shell line one section window runs: clean env, then claude with the prompt."""
    return f"{CLEAN_ENV} cd '{repo}' && {program} \"$(cat '{prompt_path}')\""


def open_window(cmd: str) -> None:
    subprocess.run(["osascript", "-e", "on run argv", "-e",
                    'tell application "Terminal" to do script (item 1 of argv)', "-e", "end run", cmd],
                   check=True, capture_output=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("brief", type=Path)
    ap.add_argument("--org", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("sections", nargs="*")
    a = ap.parse_args(argv)

    ledger_root = a.repo.resolve()
    for w in review_warnings(a.brief.read_text(), ledger_root):
        print(f"WARNING: {w}", file=sys.stderr)
    problems = brief_check(a.brief, a.org, ledger_root=ledger_root)
    if problems:
        print("launch_swarm: refusing to launch; fix the brief first:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    b = parse_brief(a.brief.read_text())
    by_id = {s.id: s for s in b.sections}
    wanted = a.sections or list(by_id)
    unknown = [x for x in wanted if x not in by_id]
    if unknown:
        print(f"launch_swarm: no such section(s) in the brief: {', '.join(unknown)}", file=sys.stderr)
        return 2

    repo = a.repo.resolve()
    brief_abs = a.brief.resolve()
    try:
        brief_rel = str(brief_abs.relative_to(repo))
    except ValueError:
        brief_rel = str(brief_abs)
    prompts = brief_abs.parent / "prompts"
    prompts.mkdir(exist_ok=True)
    for x in wanted:
        p = prompts / f"{x}.txt"
        p.write_text(section_prompt(by_id[x], a.org, brief_rel))
        cmd = window_command(repo, p)
        if a.dry_run:
            print(f"[dry-run] {x}: {cmd}")
        else:
            open_window(cmd)
            print(f"opened {x}")
            time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
