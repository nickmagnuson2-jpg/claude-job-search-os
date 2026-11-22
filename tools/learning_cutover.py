#!/usr/bin/env python3
"""learning_cutover.py: the hash-bound cutover report, and the check that binds to it.

Spec: output/memory-hygiene/112226-state-matrix.md, "Cutover" (org 68, 2026-11-22).

  report   reads the memory corpus and every migration proposal, and writes ONE markdown
           report: what each default rule does, every proposed change, and how each
           reader's state moves from today's tools (before) to learning_state (after, with
           the proposals applied in memory). Its header carries one SHA-256 over the corpus
           AND every input file. Nick approves the report by that hash.
  verify   recomputes the hash and refuses (exit 1) unless it equals --expect-sha, so a
           cutover cannot run against a corpus or a proposal that changed after approval.

This module applies NOTHING. It never writes to the memory folder, memory/lessons.md or
memory/friction-log.md; the only file it writes is the report (`report --out`).

Hash scope: every memory rule file (RULE_RE, the same set output/analysis/state_diff.py
measured), memory/lessons.md under --repo-root, and each bound input file. NOT
memory/friction-log.md: other sessions append to it continuously, and its migration is
rule-based (a kind from row content), re-run on the live ledger at apply by friction_log
migrate-kinds, whose own check confirms only Kind cells and the header changed. The report
shows those rules and their counts, labelled approximate. An input not given is recorded as MISSING, so one that appears later changes
the hash; an input named but absent is an error, never a silent MISSING.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import learning_state as ls  # noqa: E402  after
import memory_frontmatter as mf  # noqa: E402  after

REPO_ROOT = TOOLS.parent
RULE_RE = re.compile(r"(feedback|reference|project|user)_.*\.md$")
CORPUS_FILES = ("memory/lessons.md",)
# Bound inputs are in the hash. friction_kinds is read for the report only (org 68 ruling,
# 2026-11-22): the friction migration is rule-based and re-run on the live ledger at apply.
BOUND_INPUTS = ("reopen_at", "frontmatter", "lessons", "i7", "hand_edit")
INPUT_NAMES = BOUND_INPUTS + ("friction_kinds",)
DEFAULT_INPUTS = {
    "reopen_at": "output/memory-hygiene/112226-reopen-at-proposals.jsonl",
    "frontmatter": "output/memory-hygiene/112226-malformed-frontmatter-proposals.md",
    "lessons": "output/memory-hygiene/112226-lessons-repair-proposals.md",
    "i7": "output/memory-hygiene/112226-i7-proposals.jsonl",
    "hand_edit": "output/memory-hygiene/112226-b1-commented-promoted.jsonl",
    "friction_kinds": "output/memory-hygiene/112226-friction-kinds-dryrun.json",
}
MISSING = "MISSING"


# ---------------------------------------------------------------- the hash

def file_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def entries(memory_dir: Path, repo_root: Path, inputs: dict) -> dict[str, str]:
    """label -> sha256 (or MISSING for an input not given). Raises FileNotFoundError for a
    missing corpus file or a named input that does not exist."""
    out: dict[str, str] = {}
    for p in sorted(Path(memory_dir).iterdir()):
        if p.is_file() and RULE_RE.match(p.name):
            out[f"memory/{p.name}"] = file_sha(p)
    for rel in CORPUS_FILES:
        p = Path(repo_root) / rel
        if not p.is_file():
            raise FileNotFoundError(f"corpus file missing: {p}")
        out[f"repo/{rel}"] = file_sha(p)
    for name in BOUND_INPUTS:
        p = inputs.get(name)
        if p is None:
            out[f"input/{name}"] = MISSING
            continue
        if not Path(p).is_file():
            raise FileNotFoundError(f"input {name} named but missing: {p}")
        out[f"input/{name}"] = file_sha(p)
    return out


def digest_of(ents: dict[str, str]) -> str:
    body = "".join(f"{k}\t{v}\n" for k, v in sorted(ents.items()))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def cutover_digest(memory_dir: Path, repo_root: Path, inputs: dict) -> str:
    return digest_of(entries(memory_dir, repo_root, inputs))


class CorpusMoved(Exception):
    """A file changed while it was being read: there is no single state to bind to."""

    def __init__(self, changed: list[str]):
        super().__init__("changed during the scan: " + ", ".join(changed))
        self.changed = changed


def stable_entries(memory_dir: Path, repo_root: Path, inputs: dict) -> dict[str, str]:
    """entries() twice; the two scans must agree, else CorpusMoved. One pass hashes files
    one at a time, so a file hashed early can change while a later one is read (Codex B7
    F1). This catches a change that lands between the two passes; it cannot catch one made
    after a file's second hash (Grok B7 F1), nor one after verify returns. So the apply step
    must compare each file's sha with the report's .entries.json (the map the approved
    digest was computed over) at the moment it writes that file, and refuse on mismatch."""
    a = entries(memory_dir, repo_root, inputs)
    b = entries(memory_dir, repo_root, inputs)
    if a != b:
        raise CorpusMoved(sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k)))
    return a


def verify(memory_dir: Path, repo_root: Path, inputs: dict, expected: str,
           baseline: dict | None = None) -> tuple[bool, dict]:
    """(ok, info). info["changed"] lists labels that differ from `baseline` when given, or
    that changed during the scan (info["moved_during_scan"] True)."""
    try:
        ents = stable_entries(memory_dir, repo_root, inputs)
    except CorpusMoved as e:
        return False, {"expected": expected, "actual": None, "changed": e.changed,
                       "moved_during_scan": True}
    actual = digest_of(ents)
    info = {"expected": expected, "actual": actual, "changed": []}
    if baseline is not None:
        info["changed"] = sorted(k for k in set(ents) | set(baseline)
                                 if ents.get(k) != baseline.get(k))
    return actual == expected, info


# ---------------------------------------------------------------- proposal inputs

def load_reopen(path: Path | None) -> list[dict]:
    if path is None:
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def load_jsonl(path: Path | None) -> list[dict]:
    return load_reopen(path)


_FM_SECTION = re.compile(
    r"## \d+\. `(?P<file>[^`]+)`.*?sha256 of the current file: `(?P<sha>[0-9a-f]{64})`.*?"
    r"Current line:\n\n```yaml\n(?P<old>.*?)\n```.*?Proposed line:\n\n```yaml\n(?P<new>.*?)\n```",
    re.S)


def _sections(text: str, head: str) -> list[str]:
    """Every block from a heading matching `head` (a regex anchored at line start) to the
    next `## ` heading."""
    parts = re.split(r"(?m)^(?=## )", text)
    return [p for p in parts if re.match(head, p)]


def _parse_all(text: str, head: str, rx: re.Pattern, what: str) -> list[re.Match]:
    """One full match per section, or ValueError naming the section: a section that does
    not parse is never dropped from the count (Codex B7 F5)."""
    out = []
    for sec in _sections(text, head):
        m = rx.match(sec)
        if not m or sec.count("Current line:") != 1 or sec.count("Proposed line:") != 1:
            raise ValueError(f"{what}: section {sec.splitlines()[0]!r} does not parse")
        out.append(m)
    return out


def load_frontmatter_repairs(path: Path | None, memory_dir: Path) -> list[dict]:
    """One dict per proposed repair, in file order. stale: the file changed since the
    proposal was made (or is gone). applies: the old line occurs exactly once in the
    current file. A section that does not parse is a ValueError."""
    if path is None:
        return []
    out = []
    for m in _parse_all(Path(path).read_text(encoding="utf-8"), r"## \d+\. ", _FM_SECTION,
                        "frontmatter proposals"):
        if not RULE_RE.match(m["file"]) or "/" in m["file"]:
            raise ValueError(f"frontmatter proposals: {m['file']!r} is not a memory rule file the "
                             "report hashes")
        p = Path(memory_dir) / m["file"]
        text = p.read_text(encoding="utf-8") if p.is_file() else None
        out.append({"file": m["file"], "old": m["old"], "new": m["new"],
                    "stale": text is None or file_sha(p) != m["sha"],
                    "applies": text is not None and text.count(m["old"] + "\n") == 1})
    return out


_LES_SHA = re.compile(r"sha256 when these were made:\*\* `(?P<sha>[0-9a-f]{64})`")
_LES_SECTION = re.compile(
    r"## L(?P<line>\d+)\n.*?Current line:\n\n```text\n(?P<old>.*?)\n```.*?"
    r"Proposed line:\n\n```text\n(?P<new>.*?)\n```", re.S)


def load_lessons_repairs(path: Path | None, repo_root: Path) -> tuple[list[dict], bool | None]:
    """(repairs, stale). stale is None when there is no input. applies: the named line of
    the current lessons.md is exactly the old text (Codex B7 F6). A section that does not
    parse is a ValueError."""
    if path is None:
        return [], None
    text = Path(path).read_text(encoding="utf-8")
    m = _LES_SHA.search(text)
    lessons = Path(repo_root) / "memory/lessons.md"
    stale = m is None or file_sha(lessons) != m["sha"]
    lines = lessons.read_text(encoding="utf-8").split("\n")
    out = []
    for s in _parse_all(text, r"## L\d+\n", _LES_SECTION, "lessons proposals"):
        n = int(s["line"])
        if any(r["line"] == n for r in out):
            raise ValueError(f"lessons proposals: two sections for L{n}")
        out.append({"line": n, "old": s["old"], "new": s["new"],
                    "applies": 0 < n <= len(lines) and lines[n - 1] == s["old"]})
    return out, stale


def load_friction_kinds(path: Path | None, repo_root: Path):
    """The friction_log migrate-kinds dry-run output (B2: {"summary": {"sha256", "rules":
    [{"rule", "rows", "kind"}], ...}, "rows": [...]}), or None. Read for the report only;
    `current` says whether it was computed on the ledger as it is now."""
    if path is None:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    summary = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(summary, dict) or not isinstance(summary.get("rules"), list):
        raise ValueError(f"friction kinds input {path} has no summary.rules list")
    ledger = Path(repo_root) / "memory/friction-log.md"
    current = ledger.is_file() and summary.get("sha256") == file_sha(ledger)
    return {"rules": summary["rules"], "bound_sha": summary.get("sha256"), "current": current}


# ---------------------------------------------------------------- reader states

def _before_memory(text: str) -> str:
    import scan_promotion_candidates as spc  # today's reader
    fm = spc.parse_frontmatter(text)
    if spc.is_terminal(fm):
        return "terminal"
    if spc.is_promoted(fm):
        return "closed"
    try:
        occ = int(str(fm.get("occurrences", "0")).split()[0])
    except (ValueError, IndexError):
        occ = 0
    if occ < 2:
        return "open (<2, not scanned)"
    return spc.gate_state(fm.get("reopen_gate", ""), occ)


def _after_memory(fields: dict, defect, memory_dir: Path, repo_root: Path) -> str:
    """no_number is a defect only for a candidate (occurrences >= 2 and open). Below 2 the
    state is "not_due (below 2)" (org 68 matrix correction, 2026-11-22)."""
    if defect is not None:
        return f"defect: frontmatter ({defect[0]})"
    ms = ls.memory_state(fields, memory_dir=memory_dir, repo_root=repo_root)
    if ms.terminal:  # same order as _before_memory (Grok B7 F8)
        return "terminal"
    if ms.disposition.state == "closed":
        return "closed"
    if ms.disposition.state == "defect":
        return "defect: promoted"
    if (ms.due in ("no_number", "not_due") and fields.get("reopen_at") in (None, "")
            and (ls._count(fields.get("occurrences", 0)) or 0) < 2):
        return "not_due (below 2)"  # learning_state 231e2529 reads this as not_due
    return ms.due


def memory_transitions(memory_dir: Path, repo_root: Path, reopen: list[dict],
                       repairs: list[dict]) -> list[dict]:
    """before/after state per rule file. After applies, in memory only and in file order,
    every current frontmatter repair whose old text occurs exactly once in the text as
    repaired so far (Codex B7 F3), then the proposed reopen_at (status number). Nothing is
    written. `unapplied` counts a file's repairs that were stale or did not apply."""
    by_file: dict[str, list[dict]] = {}
    for r in repairs:
        by_file.setdefault(r["file"], []).append(r)
    at = {r["file"]: r["proposed_reopen_at"] for r in reopen
          if r.get("status") == "number" and isinstance(r.get("proposed_reopen_at"), int)}
    rows = []
    for p in sorted(Path(memory_dir).iterdir()):
        if not (p.is_file() and RULE_RE.match(p.name)):
            continue
        text = p.read_text(encoding="utf-8")
        before = _before_memory(text)
        unapplied = 0
        for fix in by_file.get(p.name, []):
            if not fix["stale"] and text.count(fix["old"] + "\n") == 1:
                text = text.replace(fix["old"] + "\n", fix["new"] + "\n", 1)
            else:
                unapplied += 1
        fields, defect = mf.read(text)
        if defect is None and p.name in at:
            fields = {**fields, "reopen_at": at[p.name]}
        rows.append({"file": p.name, "before": before, "unapplied": unapplied,
                     "after": _after_memory(fields, defect, memory_dir, repo_root)})
    return rows


def _lessons_rows(text: str) -> list[tuple[int, str]]:
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith("## Section 2")), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith(("## Section 1", "## Section 2"))), len(lines))  # as read_lessons
    return [(i + 1, lines[i]) for i in range(start + 1, end) if lines[i].strip().startswith("|")]


def lessons_transitions(repo_root: Path, memory_dir: Path, repairs: list[dict],
                        stale: bool | None) -> list[dict]:
    import learning_ledger as ll  # today's reader
    text = (Path(repo_root) / "memory/lessons.md").read_text(encoding="utf-8")
    fixes = {} if stale else {r["line"]: r for r in repairs if r["applies"]}
    rows = []
    for n, line in _lessons_rows(text):
        # before: today's reader alone (learning_ledger.read_lessons' Section 2 rule: a
        # row of 5+ cells that is not the header), never gated on the new parser (Codex
        # B7 F2).
        old = ll._row_cells(line)
        if not old or old[0].lower() == "pattern":
            continue  # separator or header, for both readers
        before = (("closed" if ll._disposition(old[3]) != ll.DISPOSITION_OPEN else "open")
                  if len(old) >= 5 else "not read (fewer than 5 cells)")
        fix = fixes.get(n)
        cells, bad = ls.parse_lessons_row(fix["new"] if fix else line)
        kinds: tuple = ()
        if bad is not None:
            after, kinds = "defect: row", ("row",)
        else:
            d, due, defects = ls.lessons_state(cells, memory_dir=memory_dir, repo_root=repo_root)
            kinds = tuple(x.split(":", 1)[0] for x in defects)
            after = "defect" if defects else (d.state if d.state == "closed" else f"{d.state}/{due}")
        rows.append({"line": n, "before": before, "after": after, "defect_kinds": kinds})
    return rows


# ---------------------------------------------------------------- the report

def _code_cell(text: str) -> str:
    """Render text as a code span inside a markdown table cell, byte-faithful: pipes are
    escaped (GFM honours \\| in a cell, code span or not) and the fence is longer than any
    backtick run in the text (Codex B7 r2 F2)."""
    text = str(text).replace("|", "\\|")
    run = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (run + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _promoted_lines(path: Path) -> list[str]:
    """The live `promoted:` lines of a rule file's frontmatter block, verbatim and stripped."""
    if not path.is_file():
        return []
    sp = mf.split(path.read_text(encoding="utf-8"))
    block = sp[0] if sp else ""
    return [ln.strip() for ln in block.split("\n") if ln.strip().startswith("promoted:")]


def _table(counter: Counter, head: tuple[str, str]) -> list[str]:
    out = [f"| {head[0]} | {head[1]} | Count |", "|---|---|---|"]
    for (b, a), n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"| {b} | {a} | {n} |")
    return out


def build_report(memory_dir: Path, repo_root: Path, inputs: dict,
                 today: _dt.date | None = None) -> tuple[str, str]:
    """(markdown, digest). Reads only; the caller writes the markdown."""
    text, digest, _, _ = build_report_entries(memory_dir, repo_root, inputs, today)
    return text, digest


def build_report_entries(memory_dir: Path, repo_root: Path, inputs: dict,
                         today: _dt.date | None = None) -> tuple[str, str, dict, list]:
    """(markdown, digest, the exact entries map the digest was computed over (Grok B7 F2),
    the files with an unresolved writer-refused line, which block apply)."""
    today = today or _dt.date.today()
    ents = stable_entries(memory_dir, repo_root, inputs)
    digest = digest_of(ents)
    reopen = load_reopen(inputs.get("reopen_at"))
    fm_repairs = load_frontmatter_repairs(inputs.get("frontmatter"), memory_dir)
    les_repairs, les_stale = load_lessons_repairs(inputs.get("lessons"), repo_root)
    i7 = load_jsonl(inputs.get("i7"))
    kinds = load_friction_kinds(inputs.get("friction_kinds"), repo_root)

    mem = memory_transitions(memory_dir, repo_root, reopen, fm_repairs)
    les = lessons_transitions(repo_root, memory_dir, les_repairs, les_stale)
    flagged = [r for r in reopen if r.get("status") == "relative" or r.get("confidence") == "low"]
    n_rel = sum(r.get("status") == "relative" for r in reopen)
    n_low = sum(r.get("confidence") == "low" for r in reopen)
    proposed = {r["file"] for r in reopen}
    uncovered = [r for r in mem if r["after"] == "no_number" and r["file"] not in proposed]
    n_rules = sum(1 for k in ents if k.startswith("memory/"))

    L = [f"# Learning-loop cutover report ({today.isoformat()})", "",
         f"**Cutover SHA-256:** `{digest}`", "",
         "__APPLY_STATUS__", "",
         "Approve by this hash. `learning_cutover.py verify --expect-sha <hash>` recomputes it "
         "and refuses if any memory rule file, memory/lessons.md or any bound input below "
         "changed. memory/friction-log.md is not bound (see Friction). This report applies nothing.", "",
         f"- Memory rule files hashed: {n_rules} (`(feedback|reference|project|user)_*.md` in {memory_dir})",
         f"- memory/lessons.md: `{ents['repo/memory/lessons.md']}`", "- Bound inputs:"]
    for name in BOUND_INPUTS:
        sha = ents[f"input/{name}"]
        L.append(f"  - {name}: input missing" if sha == MISSING
                 else f"  - {name}: `{sha}` ({inputs.get(name)})")
    fk = inputs.get("friction_kinds")
    L += [f"- Not bound (approximate): friction_kinds: "
          + ("input missing" if fk is None else f"`{file_sha(fk)}` ({fk})"), ""]

    mem_after = Counter(r["after"] for r in mem)
    les_after = Counter(r["after"] for r in les)
    L += ["## Default rules (applied to every record no proposal covers)", "",
          "| Rule | Records it decides |", "|---|---|",
          f"| Candidate (occurrences >= 2, open) with no reopen_at: `no_number`, a defect, never due | {mem_after['no_number']} "
          f"({mem_after['no_number'] - len(uncovered)} proposed with no number, {len(uncovered)} with no proposal) |",
          f"| Open rule below 2 occurrences: `not_due (below 2)`, not a defect | {mem_after['not_due (below 2)']} |",
          f"| Unknown promoted token or pointer token without a pointer: defect, stays open | {mem_after['defect: promoted'] + sum('promoted' in r['defect_kinds'] for r in les)} |",
          f"| Frontmatter the strict reader cannot parse (after proposed repairs): defect | {sum(v for k, v in mem_after.items() if k.startswith('defect: frontmatter'))} |",
          f"| Lessons row that cannot be read as 5 cells (after proposed repairs): defect | {les_after['defect: row']} |",
          f"| Lessons ladder, threshold {ls.LADDER_THRESHOLD}: rows due | {les_after['open/due'] + les_after['partial/due']} |",
          "| `occurrences` is never machine-written; no proposal changes it | - |", "",
          f"**Candidates with no reopen_at proposal ({len(uncovered)}):** occurrences >= 2 and open under "
          "learning_state, but not in the reopen_at proposals (the scanner's candidate set). "
          "Today's reader state shows why it skipped each.", "",
          "| File | Today's reader | After |", "|---|---|---|"]
    L += [f"| `{r['file']}` | {r['before']} | {r['after']} |" for r in uncovered]
    hand = load_jsonl(inputs.get("hand_edit"))
    # a proposal rewrites the promoted line only if it is current, applies, and its old
    # text is a promoted line (Codex B7 r2 F1)
    touched = {r["file"] for r in fm_repairs if not r["stale"] and r["applies"]
               and any(ln.strip().startswith("promoted:") for ln in r["old"].split("\n"))}
    # Nick, 2026-11-22: nothing is hand-edited. A line the writer refuses is either covered
    # by a current repair, needs no write at all, or is an UNRESOLVED defect that blocks apply.
    L += ["", "**Writer-refused lines (B1's writer refuses a `promoted` line carrying `#`):**", ""]
    unresolved: list[str] = []
    if inputs.get("hand_edit") is None:
        L.append("input missing")
    else:
        L += [f"{len(hand)} files. Nothing is edited by hand: each line is covered by a repair "
              "below, needs no write, or is UNRESOLVED and blocks apply.", "",
              "| File | Current line | Value | Outcome |", "|---|---|---|---|"]
        for r in hand:
            listed = [ln.strip() for ln in (r.get("promoted_lines_verbatim") or [])]
            live = _promoted_lines(Path(memory_dir) / str(r.get("file")))
            line = " ".join(live) if live else "(file or line not found)"
            stale = live != listed  # the input is a snapshot; the table shows the live line (r2 F3)
            if stale:
                line += " STALE: the input listed a different line"
            if r.get("file") in touched and not stale:
                outcome = "covered: a current repair rewrites this line"
            elif not r.get("hash_is_yaml_comment") and not stale:
                outcome = "no change needed: the # is inside the value and no proposal rewrites the line"
            else:
                outcome = ("UNRESOLVED: blocks apply (the input is stale)" if stale else
                           "UNRESOLVED: blocks apply (a YAML comment no repair removes)")
                unresolved.append(str(r.get("file")))
            L.append(f"| {_code_cell(r.get('file'))} | {_code_cell(line)} | "
                     f"{_code_cell(r.get('value_as_read'))} | {outcome} |")
    L += ["", "**Defects (known migration items):**", ""]
    L += [f"- `{r['file']}`: {r['after']}" for r in mem if r["after"].startswith("defect")] or ["- none"]
    L.append("")

    L += ["## For Nick: reopen_at rows to read before apply", "",
          f"{len(flagged)} distinct rows ({n_low} low confidence, {n_rel} relative, "
          f"{n_low + n_rel - len(flagged)} both). Each needs your number or `no_number`.", "",
          "| File | Occ | Proposed | Status | Confidence | Gate fragment |", "|---|---|---|---|---|---|"]
    for r in flagged:
        frag = str(r.get("prose_fragment", "")).replace("|", "/")[:90]
        L.append(f"| `{r['file']}` | {r.get('occurrences')} | {r.get('proposed_reopen_at')} | "
                 f"{r.get('status')} | {r.get('confidence')} | {frag} |")
    L.append("")

    L += ["## Proposed changes", "", "### reopen_at"]
    if inputs.get("reopen_at") is None:
        L.append("input missing")
    else:
        st = Counter(r.get("status") for r in reopen)
        cf = Counter(r.get("confidence") for r in reopen)
        L.append(f"{len(reopen)} rows. Status: " + ", ".join(f"{k} {v}" for k, v in sorted(st.items()))
                 + ". Confidence: " + ", ".join(f"{k} {v}" for k, v in sorted(cf.items())) + ".")
    L += ["", "### Frontmatter repairs"]
    if inputs.get("frontmatter") is None:
        L.append("input missing")
    else:
        L.append(f"{len(fm_repairs)} repairs. Stale (file changed since proposed): "
                 f"{sum(r['stale'] for r in fm_repairs)}. Old line not found exactly once: "
                 f"{sum(not r['applies'] for r in fm_repairs)}. Not applied in the after-state: "
                 f"{sum(r['unapplied'] for r in mem)}.")
        L += [f"- `{r['file']}`" + (" STALE" if r["stale"] else "") + ("" if r["applies"] else " DOES NOT APPLY")
              for r in fm_repairs]
    L += ["", "### Lessons repairs"]
    if inputs.get("lessons") is None:
        L.append("input missing")
    else:
        L.append(f"{len(les_repairs)} rows: " + ", ".join(f"L{r['line']}" for r in les_repairs)
                 + (". STALE: lessons.md changed since proposed; not applied below." if les_stale else "."))
        bad = [r for r in les_repairs if not r["applies"]]
        if bad:
            L.append(f"DOES NOT APPLY (the named line is not the old text): {len(bad)} ("
                     + ", ".join(f"L{r['line']}" for r in bad) + "); not applied below.")
    L += ["", "### I7 dispositions"]
    if inputs.get("i7") is None:
        L.append("input missing")
    else:
        L.append(f"{len(i7)} rows. " + ", ".join(
            f"{k} {v}" for k, v in sorted(Counter(r.get("disposition") for r in i7).items())) + ".")
    L += ["", "### Friction (approximate; not hash-bound)", ""]
    if kinds is None:
        L.append("Kinds dry run: input missing.")
    else:
        L += ["The friction migration is rule-based: each row's kind comes from its own content. At "
              "apply, `friction_log migrate-kinds` re-runs these rules on the live ledger, and its own "
              "check confirms only the Kind cells and the header changed. Counts are as of the dry run"
              + (" (computed on the ledger as it is now)." if kinds["current"] else
                 f" (ledger `{kinds['bound_sha']}`, which has since changed)."), "",
              "| Rule | Rows | Kind |", "|---|---|---|"]
        L += [f"| {r.get('rule')} | {r.get('rows')} | {r.get('kind')} |" for r in kinds["rules"]]
    L.append("")

    L += ["## Reader-state changes (before: today's tools; after: learning_state with proposals applied in memory)", "",
          "### Memory rules", ""]
    L += _table(Counter((r["before"], r["after"]) for r in mem), ("Before", "After"))
    L += ["", "### Lessons Section 2 rows", ""]
    L += _table(Counter((r["before"], r["after"]) for r in les), ("Before", "After"))
    L.append("")
    L[L.index("__APPLY_STATUS__")] = (
        f"**Apply: BLOCKED.** {len(unresolved)} unresolved defect(s): " + ", ".join(f"`{u}`" for u in unresolved)
        if unresolved else "**Apply: not blocked** by any unresolved writer-refused line.")
    if stable_entries(memory_dir, repo_root, inputs) != ents:
        raise CorpusMoved(["(the corpus or an input changed while the report was built)"])
    return "\n".join(L), digest, ents, unresolved


# ---------------------------------------------------------------- CLI

def _inputs(args) -> dict:
    out = {}
    for name in INPUT_NAMES:
        v = getattr(args, name)
        if v is None and not args.no_default_inputs:
            cand = Path(args.repo_root) / DEFAULT_INPUTS[name]
            v = cand if cand.is_file() else None
        out[name] = Path(v) if v is not None else None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("report", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--memory-dir", required=True,
                       help="the live memory folder (~/.claude/projects/<project-slug>/memory)")
        p.add_argument("--repo-root", default=str(REPO_ROOT))
        p.add_argument("--no-default-inputs", action="store_true",
                       help="use only the inputs named on the command line")
        for inp in INPUT_NAMES:
            p.add_argument("--" + inp.replace("_", "-"), dest=inp, default=None)
        if name == "report":
            p.add_argument("--out", required=False)
        else:
            p.add_argument("--expect-sha", required=True)
            p.add_argument("--entries", help="the report's .entries.json, to name what changed")
    args = ap.parse_args(argv)
    inputs = _inputs(args)
    mem, repo = Path(args.memory_dir), Path(args.repo_root)
    if args.cmd == "report":
        text, digest, hashed, unresolved = build_report_entries(mem, repo, inputs)
        out = Path(args.out) if args.out else (
            repo / "output/memory-hygiene" / f"{_dt.date.today():%m%d%y}-cutover-report.md")
        protected = [mem.resolve(), (repo / "memory").resolve()]
        corpus = [p for p in mem.iterdir() if p.is_file()] + [
            p for p in (repo / "memory").iterdir() if p.is_file()]
        for target in (out, out.with_suffix(".entries.json")):
            t = target.resolve()
            linked = target.exists() and any(target.samefile(c) for c in corpus)  # Grok B7 F3
            if linked or any(t == d or d in t.parents for d in protected):
                print(json.dumps({"status": "refused",
                                  "error": f"{target} is inside the memory folder or repo memory/; "
                                           "the report never writes there"}))
                return 2
        out.write_text(text, encoding="utf-8")
        side = out.with_suffix(".entries.json")
        side.write_text(json.dumps(hashed, indent=0, sort_keys=True), encoding="utf-8")
        print(json.dumps({"status": "blocked" if unresolved else "ok", "sha256": digest,
                          "report": str(out), "entries": str(side), "unresolved": unresolved}))
        return 3 if unresolved else 0
    baseline = json.loads(Path(args.entries).read_text(encoding="utf-8")) if args.entries else None
    ok, info = verify(mem, repo, inputs, args.expect_sha, baseline=baseline)
    print(json.dumps({"status": "ok" if ok else "refused", **info}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
