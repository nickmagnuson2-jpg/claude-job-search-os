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
measured), memory/friction-log.md and memory/lessons.md under --repo-root, and each named
input file. An input not given is recorded as MISSING, so one that appears later changes
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
MEMORY_DIR = Path.home() / ".claude" / "projects" / "-Users-mag-Documents-Obsidian-30-projects-job-search" / "memory"
RULE_RE = re.compile(r"(feedback|reference|project|user)_.*\.md$")
CORPUS_FILES = ("memory/friction-log.md", "memory/lessons.md")
INPUT_NAMES = ("reopen_at", "frontmatter", "lessons", "i7", "friction_kinds")
DEFAULT_INPUTS = {
    "reopen_at": "output/memory-hygiene/112226-reopen-at-proposals.jsonl",
    "frontmatter": "output/memory-hygiene/112226-malformed-frontmatter-proposals.md",
    "lessons": "output/memory-hygiene/112226-lessons-repair-proposals.md",
    "i7": "output/memory-hygiene/112226-i7-proposals.jsonl",
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
    for name in INPUT_NAMES:
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


def verify(memory_dir: Path, repo_root: Path, inputs: dict, expected: str,
           baseline: dict | None = None) -> tuple[bool, dict]:
    """(ok, info). info["changed"] lists labels that differ from `baseline` when given."""
    ents = entries(memory_dir, repo_root, inputs)
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


def load_frontmatter_repairs(path: Path | None, memory_dir: Path) -> list[dict]:
    """One dict per proposed repair. stale: the file changed since the proposal was made
    (or is gone). applies: the old line occurs exactly once in the current file."""
    if path is None:
        return []
    out = []
    for m in _FM_SECTION.finditer(Path(path).read_text(encoding="utf-8")):
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
    """(repairs, stale). stale is None when there is no input."""
    if path is None:
        return [], None
    text = Path(path).read_text(encoding="utf-8")
    m = _LES_SHA.search(text)
    stale = m is None or file_sha(Path(repo_root) / "memory/lessons.md") != m["sha"]
    return [{"line": int(s["line"]), "old": s["old"], "new": s["new"]}
            for s in _LES_SECTION.finditer(text)], stale


def load_friction_kinds(path: Path | None, repo_root: Path):
    """The friction_log migrate-kinds dry-run output (B2: {"summary": {"sha256": <ledger
    sha>, ...}, "rows": [{"date", "surface", "nature", "planned_kind", ...}]}), or None.
    stale: the ledger changed since the dry run (or the dry run names no ledger sha)."""
    if path is None:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"friction kinds input {path} has no rows list")
    bound = (data.get("summary") or {}).get("sha256")
    stale = bound != file_sha(Path(repo_root) / "memory/friction-log.md")
    return {"rows": rows, "bound_sha": bound, "stale": stale}


def _friction_key(row: dict) -> tuple:
    return (str(row.get("date", "")).strip(), str(row.get("surface", "")).strip(),
            str(row.get("nature", "")).strip())


def friction_keys(repo_root: Path) -> list[tuple]:
    """(date, surface, nature) of every ledger data row, read with today's parse_row."""
    import friction_log as fl
    text = (Path(repo_root) / "memory/friction-log.md").read_text(encoding="utf-8")
    body = text.split(fl.ENTRIES_MARKER, 1)[1] if fl.ENTRIES_MARKER in text else ""
    out = []
    for line in body.split("\n"):
        p = fl.parse_row(line) if line.strip().startswith("|") else {}
        if p and p["date"] != "Date" and not set(p["date"]) <= set("-: "):
            out.append(_friction_key(p))
    return out


def friction_transitions(repo_root: Path, kinds: dict | None) -> list[dict]:
    """before (today: RESOLVED prefix, then the ladder on rows whose Promotion cell is
    set) and after (learning_state.friction_state on the planned kind) per ledger row. A
    row the dry run does not cover gets no after-state: it is never guessed."""
    import friction_log as fl  # today's reader
    text = (Path(repo_root) / "memory/friction-log.md").read_text(encoding="utf-8")
    body = text.split(fl.ENTRIES_MARKER, 1)[1] if fl.ENTRIES_MARKER in text else ""
    planned = {} if kinds is None else {_friction_key(r): r.get("planned_kind") for r in kinds["rows"]}
    rows = []
    for line in body.split("\n"):
        p = fl.parse_row(line) if line.strip().startswith("|") else {}
        if not p or p["date"] in ("Date",) or set(p["date"]) <= set("-: "):
            continue
        occ = ls._count(str(p["occurrences"]).split()[0] if str(p["occurrences"]).split() else "")
        if fl.is_resolved(p["fix"]):
            before = "resolved"
        elif p["promotion"].strip() not in ("", "-", "--") and (occ or 0) >= 2:
            before = "due"
        else:
            before = "open"
        key = _friction_key(p)
        if kinds is None:
            after = "input missing"
        elif key not in planned:
            after = "no planned kind (row not in the dry run)"
        else:
            after = ls.friction_state(planned[key], p["fix"], occ if occ is not None else p["occurrences"])
        rows.append({"before": before, "after": after})
    return rows


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
    if defect is not None:
        return f"defect: frontmatter ({defect[0]})"
    ms = ls.memory_state(fields, memory_dir=memory_dir, repo_root=repo_root)
    if ms.disposition.state == "closed":
        return "closed"
    if ms.terminal:
        return "terminal"
    if ms.disposition.state == "defect":
        return "defect: promoted"
    return ms.due


def memory_transitions(memory_dir: Path, repo_root: Path, reopen: list[dict],
                       repairs: dict[str, dict]) -> list[dict]:
    """before/after state per rule file. After applies, in memory only, the proposed
    frontmatter repair and the proposed reopen_at (status number); nothing is written."""
    at = {r["file"]: r["proposed_reopen_at"] for r in reopen
          if r.get("status") == "number" and isinstance(r.get("proposed_reopen_at"), int)}
    rows = []
    for p in sorted(Path(memory_dir).iterdir()):
        if not (p.is_file() and RULE_RE.match(p.name)):
            continue
        text = p.read_text(encoding="utf-8")
        before = _before_memory(text)
        fix = repairs.get(p.name)
        if fix and fix["applies"] and not fix["stale"]:
            text = text.replace(fix["old"] + "\n", fix["new"] + "\n", 1)
        fields, defect = mf.read(text)
        if defect is None and p.name in at:
            fields = {**fields, "reopen_at": at[p.name]}
        rows.append({"file": p.name, "before": before,
                     "after": _after_memory(fields, defect, memory_dir, repo_root)})
    return rows


def _lessons_rows(text: str) -> list[tuple[int, str]]:
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith("## Section 2")), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return [(i + 1, lines[i]) for i in range(start + 1, end) if lines[i].strip().startswith("|")]


def lessons_transitions(repo_root: Path, memory_dir: Path, repairs: list[dict],
                        stale: bool | None) -> list[dict]:
    import learning_ledger as ll  # today's reader
    text = (Path(repo_root) / "memory/lessons.md").read_text(encoding="utf-8")
    fixes = {} if stale else {r["line"]: r for r in repairs}
    rows = []
    for n, line in _lessons_rows(text):
        cells, bad = ls.parse_lessons_row(line)
        if cells is None and bad is None:
            continue  # header or separator
        if cells is not None:
            before = "closed" if ll._disposition(cells[3]) != ll.DISPOSITION_OPEN else "open"
        else:
            before = "unreadable"
        fix = fixes.get(n)
        if fix and fix["old"] == line:
            cells, bad = ls.parse_lessons_row(fix["new"])
        if bad is not None:
            after = "defect: row"
        else:
            d, due, defects = ls.lessons_state(cells, memory_dir=memory_dir, repo_root=repo_root)
            after = "defect" if defects else (d.state if d.state == "closed" else f"{d.state}/{due}")
        rows.append({"line": n, "before": before, "after": after})
    return rows


# ---------------------------------------------------------------- the report

def _table(counter: Counter, head: tuple[str, str]) -> list[str]:
    out = [f"| {head[0]} | {head[1]} | Count |", "|---|---|---|"]
    for (b, a), n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"| {b} | {a} | {n} |")
    return out


def build_report(memory_dir: Path, repo_root: Path, inputs: dict,
                 today: _dt.date | None = None) -> tuple[str, str]:
    """(markdown, digest). Reads only; the caller writes the markdown."""
    today = today or _dt.date.today()
    ents = entries(memory_dir, repo_root, inputs)
    digest = digest_of(ents)
    reopen = load_reopen(inputs.get("reopen_at"))
    fm_repairs = load_frontmatter_repairs(inputs.get("frontmatter"), memory_dir)
    les_repairs, les_stale = load_lessons_repairs(inputs.get("lessons"), repo_root)
    i7 = load_jsonl(inputs.get("i7"))
    kinds = load_friction_kinds(inputs.get("friction_kinds"), repo_root)

    mem = memory_transitions(memory_dir, repo_root, reopen, {r["file"]: r for r in fm_repairs})
    les = lessons_transitions(repo_root, memory_dir, les_repairs, les_stale)
    fr = friction_transitions(repo_root, kinds)
    flagged = [r for r in reopen if r.get("status") == "relative" or r.get("confidence") == "low"]
    n_rel = sum(r.get("status") == "relative" for r in reopen)
    n_low = sum(r.get("confidence") == "low" for r in reopen)
    no_num = Counter()
    for r in mem:
        if r["after"] == "no_number":
            o = mf.read_file(Path(memory_dir) / r["file"])[0].get("occurrences")
            no_num["occurrences missing" if o in (None, "") else
                   "occurrences < 2" if isinstance(o, int) and o < 2 else
                   "occurrences >= 2" if isinstance(o, int) else "occurrences not a count"] += 1
    n_rules = sum(1 for k in ents if k.startswith("memory/"))

    L = [f"# Learning-loop cutover report ({today.isoformat()})", "",
         f"**Cutover SHA-256:** `{digest}`", "",
         "Approve by this hash. `learning_cutover.py verify --expect-sha <hash>` recomputes it "
         "and refuses if any rule file, memory/friction-log.md, memory/lessons.md or any input "
         "below changed. This report applies nothing.", "",
         f"- Memory rule files hashed: {n_rules} (`(feedback|reference|project|user)_*.md` in {memory_dir})",
         f"- memory/friction-log.md: `{ents['repo/memory/friction-log.md']}`",
         f"- memory/lessons.md: `{ents['repo/memory/lessons.md']}`", "- Inputs:"]
    for name in INPUT_NAMES:
        sha = ents[f"input/{name}"]
        where = inputs.get(name)
        L.append(f"  - {name}: input missing" if sha == MISSING else f"  - {name}: `{sha}` ({where})")
    L.append("")

    mem_after = Counter(r["after"] for r in mem)
    les_after = Counter(r["after"] for r in les)
    L += ["## Default rules (applied to every record no proposal covers)", "",
          "| Rule | Records it decides |", "|---|---|",
          f"| Memory rule in the backlog with no reopen_at: `no_number`, a defect, never due | {mem_after['no_number']} |",
          f"| Unknown promoted token or pointer token without a pointer: defect, stays open | {mem_after['defect: promoted'] + les_after['defect']} |",
          f"| Frontmatter the strict reader cannot parse (after proposed repairs): defect | {sum(v for k, v in mem_after.items() if k.startswith('defect: frontmatter'))} |",
          f"| Lessons row that cannot be read as 5 cells (after proposed repairs): defect | {les_after['defect: row']} |",
          f"| Ladder lanes (lessons, friction) use threshold {ls.LADDER_THRESHOLD}: lessons rows due | {les_after['open/due'] + les_after['partial/due']} |",
          f"| Same ladder: friction rows due | {Counter(r['after'] for r in fr)['due'] if kinds else 'input missing'} |",
          "| `occurrences` is never machine-written; no proposal changes it | - |", "",
          "`no_number` by occurrences: " + (", ".join(f"{k} {v}" for k, v in sorted(no_num.items())) or "none") + ".", "",
          "Records in a defect state:", ""]
    L += [f"- `{r['file']}`: {r['after']}" for r in mem if r["after"].startswith("defect")] or ["- none"]
    L.append("")

    L += ["## For Nick: reopen_at rows to read before apply", "",
          f"{len(flagged)} distinct rows ({n_low} low confidence, {n_rel} relative, {n_low + n_rel - len(flagged)} both). "
          "Each needs your number or `no_number`.", "",
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
                 f"{sum(not r['applies'] for r in fm_repairs)}.")
        L += [f"- `{r['file']}`" + (" STALE" if r["stale"] else "") + ("" if r["applies"] else " DOES NOT APPLY")
              for r in fm_repairs]
    L += ["", "### Lessons repairs"]
    if inputs.get("lessons") is None:
        L.append("input missing")
    else:
        L.append(f"{len(les_repairs)} rows: " + ", ".join(f"L{r['line']}" for r in les_repairs)
                 + (". STALE: lessons.md changed since proposed; not applied below." if les_stale else "."))
    L += ["", "### I7 dispositions"]
    if inputs.get("i7") is None:
        L.append("input missing")
    else:
        L.append(f"{len(i7)} rows. " + ", ".join(
            f"{k} {v}" for k, v in sorted(Counter(r.get("disposition") for r in i7).items())) + ".")
    L += ["", "### Friction"]
    if kinds is None:
        L.append("Kinds dry run: input missing. Friction after-states are not computed.")
    else:
        kc = Counter(r.get("planned_kind") for r in kinds["rows"])
        L.append(f"Kinds dry run: {len(kinds['rows'])} rows. Planned kinds: "
                 + ", ".join(f"{k} {v}" for k, v in sorted(kc.items(), key=str)) + ".")
        ledger_keys = set(friction_keys(repo_root))
        gone = sum(1 for r in kinds["rows"] if _friction_key(r) not in ledger_keys)
        L.append(f"Dry-run rows no longer in the ledger: {gone}. Ledger rows the dry run does not "
                 f"cover: {Counter(r['after'] for r in fr)['no planned kind (row not in the dry run)']}.")
        if kinds["stale"]:
            L.append(f"**STALE:** the dry run was bound to friction-log.md `{kinds['bound_sha']}`; the ledger "
                     "hashed above differs. Rows added since have no planned kind below. Re-run the dry run "
                     "before approval.")
    L.append("")

    L += ["## Reader-state changes (before: today's tools; after: learning_state with proposals applied in memory)", "",
          "### Memory rules", ""]
    L += _table(Counter((r["before"], r["after"]) for r in mem), ("Before", "After"))
    L += ["", "### Lessons Section 2 rows", ""]
    L += _table(Counter((r["before"], r["after"]) for r in les), ("Before", "After"))
    L += ["", "### Friction ledger rows", ""]
    L += _table(Counter((r["before"], r["after"]) for r in fr), ("Before", "After"))
    L.append("")
    return "\n".join(L), digest


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
        p.add_argument("--memory-dir", default=str(MEMORY_DIR))
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
        text, digest = build_report(mem, repo, inputs)
        out = Path(args.out) if args.out else (
            repo / "output/memory-hygiene" / f"{_dt.date.today():%m%d%y}-cutover-report.md")
        out.write_text(text, encoding="utf-8")
        side = out.with_suffix(".entries.json")
        side.write_text(json.dumps(entries(mem, repo, inputs), indent=0, sort_keys=True), encoding="utf-8")
        print(json.dumps({"status": "ok", "sha256": digest, "report": str(out), "entries": str(side)}))
        return 0
    baseline = json.loads(Path(args.entries).read_text(encoding="utf-8")) if args.entries else None
    ok, info = verify(mem, repo, inputs, args.expect_sha, baseline=baseline)
    print(json.dumps({"status": "ok" if ok else "refused", **info}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
