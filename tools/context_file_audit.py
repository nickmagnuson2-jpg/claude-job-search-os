#!/usr/bin/env python3
"""context_file_audit.py — measure an always-loaded context file section by section.

Files like CLAUDE.md and memory/MEMORY.md are loaded into EVERY session, so their
size is a per-session tax. This script is the deterministic half of
`/trim-context-file`: it measures and classifies, it never edits and never decides.
The KEEP/MOVE judgment belongs to the human in the skill.

Why a script and not eyeballing: line counts badly understate the real cost when a
section is a few enormous paragraphs (a 13-line "Hard Rules" section can outweigh a
91-line table). Bytes are the honest unit.

Heuristic signals per section (advisory only, never a verdict):
  rule_density      fraction of lines with imperative/normative markers
                    ("never", "always", "must", "do not", "required", "mandatory")
  lookup_density fraction of lines that are table rows, code fences, or list
                    entries pointing at paths -- the shape of lookup material
  A section that is high-reference and low-rule is a MOVE candidate: it is consulted
  when doing a specific task, not needed resident in every session.

Beyond measuring, this script supplies the *verification primitives* the skill's
safety gate depends on (`--rules`, `--emit-blocks`, `--capabilities`). Those exist
so a before/after comparison can actually fail. The governing rule: a guard with no
failing mode is the same bug wearing a safety vest -- so an empty artifact is always
an error with a non-zero exit, never a successful empty output.

Exit codes:
    0  success
    1  path is not a file
    2  argparse usage error (e.g. no path when one is required)
    3  --require named a capability this script does not support
    4  --rules detected zero normative lines (wrong file or broken detector)
    5  --emit-blocks would have emitted zero blocks
    6  --emit-blocks target directory is unsafe to write (see "Dirty output dirs")
    7  --expect-blocks N did not match the number of blocks emitted

Dirty output dirs: an empty block directory is guarded (exit 5), but a *dirty* one is
worse -- a caller that globs the directory instead of reading manifest.json would
verify a stale block from an older revision of the source as though it were current.
So `--emit-blocks` refuses (exit 6) unless the target is absent, empty, or provably a
prior run's output, where the proof is a readable `manifest.json` and the only files
deleted are the ones it declares. Filename shape is never treated as provenance, and a
directory this script cannot prove it wrote is refused untouched, never cleaned up.

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py CLAUDE.md
    PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py CLAUDE.md --json
    PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py memory/MEMORY.md --top 5
    PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py --capabilities
    PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py --require rules,emit-blocks
    PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py CLAUDE.md --rules --json
    PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py CLAUDE.md --emit-blocks /tmp/blocks
    PYTHONIOENCODING=utf-8 python3 tools/context_file_audit.py CLAUDE.md --emit-blocks /tmp/blocks --expect-blocks 13

What `--rules` is and is not: it is a *sample* of normative lines (keyword signal plus a
structural bolded-list-item signal), not a cover of every rule in the file. Before and
after are parsed by the same detector, so a systematic omission cancels on both sides of
a diff. The rules diff is corroborating evidence; **byte conservation via --emit-blocks
is the load-bearing guarantee.** Exit 4 catches an empty baseline; nothing can catch a
thin one, which is why the structural signal exists and why each rule records which
signal matched it.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

RULE_MARKERS = re.compile(
    r"\b(never|always|must|do not|don't|required|mandatory|hard rule|forbidden|"
    r"before any|stop and|blocking)\b", re.I)
REFERENCE_MARKERS = re.compile(r"^\s*(\||```|[-*]\s+`|\d+\.\s+`)")
PATH_HINT = re.compile(r"`[^`]*\.(md|py|sh|ya?ml|json)`|`[a-z_]+/`")

FENCE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})(.*)$")
# Structural normative signal: a list item whose content leads with bold -- the
# "- **Rule name.** body" shape this repo writes hard rules in. Structural detection
# exists because a keyword list can silently narrow (drop "must" and the detector
# quietly stops seeing a third of the rules) while a shape cannot narrow without an
# obvious code change. Ordered items count too: a numbered normative step is still a
# rule. Deliberately NOT "every non-blank line" -- that would abort on any reflow.
BOLD_LEAD_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\*\*(?=\S)")
BLOCK_FILE = re.compile(r"^\d{3,}-[a-z0-9-]+\.md$")
MANIFEST_NAME = "manifest.json"

CAPABILITY_SCHEMA_VERSION = 1
CAPABILITIES = [
    "audit",                  # default per-section measurement table
    "json",                   # --json machine-readable output
    "top",                    # --top N largest sections
    "rules",                  # --rules normative-line extraction (exit 4 if empty)
    "emit-blocks",            # --emit-blocks DIR verbatim per-section files + manifest
    "fence-aware-sections",   # headings inside ``` / ~~~ fences are not sections
    "structural-rules",       # --rules also detects bolded-lead list items, not just keywords
    "rule-signals",           # each rule records which signal(s) matched
    "unclosed-fence-warning", # stderr warning when a fence runs to EOF
    "clean-emit-dir",         # --emit-blocks never leaves a stale block behind (exit 6)
    "expect-blocks",          # --expect-blocks N asserts the emitted count (exit 7)
    "capabilities",           # --capabilities self-description
    "require",                # --require cap1,cap2 exit-code gate
]


def _fence_delim(line: str) -> str | None:
    """Return the fence delimiter run for a fence line, else None."""
    m = FENCE.match(line)
    return m.group(2) if m else None


def _fence_mask(all_lines: list[str]) -> tuple[list[bool], bool]:
    """Per-line "is inside a fenced code block" mask, plus whether one is open at EOF.

    mask[i] is True when line i must not be read as a heading. The opening fence line
    itself is False (it is not a heading either way); every line after it, including
    the closing delimiter, is True. A closing fence uses the same character, is at
    least as long, and carries no info string -- anything else is content.

    Single source of truth: both split_sections() and the unclosed-fence warning read
    this, so the two can never disagree about where a fence starts or ends.
    """
    mask: list[bool] = []
    open_fence: str | None = None
    for line in all_lines:
        mask.append(open_fence is not None)
        delim = _fence_delim(line)
        if open_fence is None:
            if delim is not None:
                open_fence = delim
        elif delim is not None:
            info = FENCE.match(line).group(3).strip()
            if delim[0] == open_fence[0] and len(delim) >= len(open_fence) and not info:
                open_fence = None
    return mask, open_fence is not None


def has_unclosed_fence(text: str) -> bool:
    """True when the file ends with a code fence still open.

    Not a data-loss bug -- byte accounting still balances -- but per CommonMark the
    fence runs to EOF, so every heading after it disappears and the operator sees one
    giant structureless section. A stray fence in CLAUDE.md would make a whole trim
    silently non-actionable, so it is worth a warning even though the split is correct.
    """
    return _fence_mask(text.splitlines())[1]


def split_sections(text: str) -> list[dict]:
    """Split on '## ' headings. Content before the first heading is '(preamble)'.

    Fence-aware: a '## ' line inside a ``` or ~~~ fenced code block is content, not
    a heading. Without this, a fenced markdown example creates a phantom section and
    corrupts byte accounting.

    Each section carries:
      name              heading text, or '(preamble)'
      lines             body lines, heading excluded (unchanged from the original API)
      heading           the verbatim heading line, or None for the preamble
      start_line        1-based line number of the region's first line (the heading,
                        or the first body line for the preamble)
      body_start_line   1-based line number of lines[0]
    """
    all_lines = text.splitlines()
    mask, _ = _fence_mask(all_lines)
    sections: list[dict] = []
    name, heading, buf = "(preamble)", None, []
    start_line, body_start_line = 1, 1

    for idx, line in enumerate(all_lines):
        if mask[idx]:
            buf.append(line)
            continue

        if line.startswith("## "):
            sections.append({
                "name": name, "lines": buf, "heading": heading,
                "start_line": start_line, "body_start_line": body_start_line,
            })
            name, heading, buf = line[3:].strip(), line, []
            start_line = idx + 1
            body_start_line = idx + 2
        else:
            buf.append(line)

    sections.append({
        "name": name, "lines": buf, "heading": heading,
        "start_line": start_line, "body_start_line": body_start_line,
    })
    return [s for s in sections if s["lines"] or s["name"] != "(preamble)"]


def classify(lines: list[str]) -> dict:
    body = [l for l in lines if l.strip()]
    if not body:
        return {"rule_density": 0.0, "lookup_density": 0.0}
    rule = sum(1 for l in body if RULE_MARKERS.search(l))
    ref = sum(1 for l in body if REFERENCE_MARKERS.match(l) or PATH_HINT.search(l))
    return {
        "rule_density": round(rule / len(body), 3),
        "lookup_density": round(ref / len(body), 3),
    }


def suggest(rule_d: float, ref_d: float, share: float) -> str:
    """Advisory only. The skill surfaces this; the human decides."""
    if rule_d >= 0.25:
        return "KEEP — rule-dense; belongs in the always-loaded tier"
    if ref_d >= 0.40 and share >= 0.05:
        return "MOVE — reference-shaped and heavy; consult on demand"
    if share >= 0.15:
        return "REVIEW — large enough that its share alone justifies a look"
    return "REVIEW"


def audit(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    total_bytes = len(text.encode("utf-8"))
    rows = []
    for sec in split_sections(text):
        raw = "\n".join(sec["lines"])
        nbytes = len(raw.encode("utf-8"))
        share = nbytes / total_bytes if total_bytes else 0.0
        sig = classify(sec["lines"])
        rows.append({
            "section": sec["name"],
            "lines": len(sec["lines"]),
            "bytes": nbytes,
            "share": round(share, 4),
            **sig,
            "suggestion": suggest(sig["rule_density"], sig["lookup_density"], share),
        })
    rows.sort(key=lambda r: r["bytes"], reverse=True)
    return {
        "file": str(path),
        "total_lines": len(text.splitlines()),
        "total_bytes": total_bytes,
        "sections": rows,
    }


def rule_signals(line: str) -> list[str]:
    """Which normative signals a line trips: "keyword", "structural", or both.

    Recorded per rule so a future narrowing is visible in a diff rather than silent.
    If someone drops a word from RULE_MARKERS, the affected rules do not vanish from
    the baseline -- their signal list changes -- which is exactly the mutation that
    survived the suite before.
    """
    signals = []
    if RULE_MARKERS.search(line):
        signals.append("keyword")
    if BOLD_LEAD_ITEM.match(line):
        signals.append("structural")
    return signals


def collect_rules(text: str) -> list[dict]:
    """Every normative line, attributed to its section, verbatim, one entry per line.

    A line is normative if it trips the RULE_MARKERS keyword regex OR the structural
    BOLD_LEAD_ITEM shape. A line matching both is recorded once with both signals, so
    rule_count always equals len(rules).

    The heading line counts as part of its own section (a heading like '## Hard Rules'
    is itself normative signal).

    Caveat worth stating where the code lives: this is a *sample* of normative content,
    not a cover. Before and after are parsed by the same detector, so a systematic
    omission cancels on both sides of a diff. Treat the rules diff as corroborating
    evidence; byte conservation is the load-bearing guarantee.
    """
    rules: list[dict] = []
    for sec in split_sections(text):
        region: list[tuple[int, str]] = []
        if sec["heading"] is not None:
            region.append((sec["start_line"], sec["heading"]))
        region += [(sec["body_start_line"] + i, l) for i, l in enumerate(sec["lines"])]
        for lineno, line in region:
            if not line.strip():
                continue
            signals = rule_signals(line)
            if signals:
                rules.append({"section": sec["name"], "line": lineno,
                              "text": line, "signals": signals})
    rules.sort(key=lambda r: r["line"])
    return rules


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "section")[:60]


def build_blocks(text: str) -> list[dict]:
    """Per-section verbatim source regions, byte-identical to the original file.

    The region is reconstructed from the original line endings, so the concatenation
    of all block contents reproduces the source file exactly.
    """
    keep = text.splitlines(keepends=True)
    blocks = []
    for i, sec in enumerate(split_sections(text)):
        count = len(sec["lines"]) + (1 if sec["heading"] is not None else 0)
        start = sec["start_line"] - 1
        content = "".join(keep[start:start + count])
        blocks.append({
            "index": i,
            "section": sec["name"],
            "start_line": sec["start_line"],
            "line_count": count,
            "content": content,
        })
    return blocks


def _declared_block_files(outdir: Path) -> set[str] | None:
    """Filenames a readable manifest.json in outdir declares, or None if there isn't one.

    This is the *only* evidence this script accepts that it produced a directory's
    contents. A malformed or unreadable manifest yields an empty set, not a guess --
    an empty set means nothing is deletable, so the caller refuses.
    """
    mf = outdir / MANIFEST_NAME
    if not mf.is_file():
        return None
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        return {b["file"] for b in data["blocks"] if isinstance(b.get("file"), str)}
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, KeyError, AttributeError):
        return set()


def prepare_block_dir(outdir: Path) -> str | None:
    """Make outdir safe to write blocks into. Returns an error message, or None on OK.

    A leftover block from an older revision of the source is indistinguishable from a
    current one to any caller that globs the directory, so on success the directory must
    hold exactly this run's output. Cases:

      absent / empty        -> create and proceed
      prior run's output    -> a readable manifest.json proves we wrote it; delete the
                               files that manifest declares (and the manifest), then write
      anything else         -> refuse (exit 6). Delete nothing.

    **Provenance comes from the manifest, never from filename shape.** An earlier version
    of this function keyed deletion off a `NNN-slug.md` regex, which destroyed an ordinary
    folder of numbered notes (`001-introduction.md`, `002-methods.md`) and exited 0 while
    reporting they were "from a previous run" -- a provenance claim it had never
    established. Shape is a guess; the manifest is evidence. The costs are asymmetric:
    refusing is trivially recoverable (delete the directory, or point elsewhere), deleting
    someone's notes is not. So a crashed partial run that left blocks without a manifest
    is now refused rather than cleaned up; that user can remove the directory themselves.

    Deletion is further narrowed to the intersection of "declared by the manifest" and
    "shaped like our output", so a foreign file cannot be removed even from a directory
    we did write.
    """
    try:
        if not outdir.exists():
            outdir.mkdir(parents=True, exist_ok=True)
            return None
        if not outdir.is_dir():
            return f"--emit-blocks target exists and is not a directory: {outdir}"

        entries = sorted(outdir.iterdir(), key=lambda p: p.name)
        if not entries:
            return None

        names = [e.name for e in entries]
        declared = _declared_block_files(outdir)
        if declared is None:
            return (f"refusing to write blocks into non-empty {outdir}: it has no "
                    f"{MANIFEST_NAME}, so this script has no evidence it produced what is "
                    f"there ({', '.join(names[:5])}{', ...' if len(names) > 5 else ''}) and "
                    f"will not delete it. A crashed partial run is recoverable by removing "
                    f"the directory yourself; a deleted file is not. Point --emit-blocks at "
                    f"a new or empty directory.")

        # Decide deletability for EVERY entry before deleting any. An entry is ours only
        # if it is a regular file that the manifest declares AND that matches the shape
        # this script emits. If even one entry fails that test, refuse whole -- deleting
        # the rest would be a partial write, and keeping the rest would leave something
        # in the directory that the new manifest does not list, breaking the invariant
        # that a globbing caller can never see a block we did not just write.
        undeletable = [e.name for e in entries if not (
            e.is_file() and (e.name == MANIFEST_NAME
                             or (e.name in declared and BLOCK_FILE.match(e.name))))]
        if undeletable:
            return (f"refusing to write blocks into {outdir}: it holds entr(ies) its "
                    f"{MANIFEST_NAME} does not declare as block output "
                    f"({', '.join(undeletable[:5])}"
                    f"{', ...' if len(undeletable) > 5 else ''}). This script cannot prove "
                    f"it wrote them, so it will not delete them -- and leaving them in "
                    f"place would let a caller that globs the directory verify a stale "
                    f"block as current. Point --emit-blocks at a new or empty directory.")

        # Count block files only: the manifest does not declare itself, so folding it
        # into this number would claim a provenance the manifest never asserted.
        n_blocks = sum(1 for e in entries if e.name != MANIFEST_NAME)
        for e in entries:
            e.unlink()
        if entries:
            print(f"note: removed {n_blocks} block file(s) declared by the existing "
                  f"{MANIFEST_NAME} in {outdir}, and the manifest itself, before "
                  f"rewriting", file=sys.stderr)
        return None
    except OSError as exc:
        return f"--emit-blocks target directory is not usable: {outdir} ({exc})"


def emit_blocks(path: Path, outdir: Path) -> dict:
    """Write one verbatim file per section into outdir, plus manifest.json.

    Assumes prepare_block_dir() has already cleared/validated outdir.
    """
    text = path.read_text(encoding="utf-8")
    blocks = build_blocks(text)
    outdir.mkdir(parents=True, exist_ok=True)
    entries = []
    for b in blocks:
        fname = f"{b['index']:03d}-{_slug(b['section'])}.md"
        raw = b["content"].encode("utf-8")
        (outdir / fname).write_bytes(raw)
        entries.append({
            "index": b["index"],
            "section": b["section"],
            "file": fname,
            "start_line": b["start_line"],
            "line_count": b["line_count"],
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    manifest = {
        "source": str(path),
        "source_bytes": len(text.encode("utf-8")),
        "block_count": len(entries),
        "blocks": entries,
    }
    (outdir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _nonneg_int(value: str) -> int:
    """--top must be >= 0. A negative slices sections OFF the end of a size audit."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}")
    if n < 0:
        raise argparse.ArgumentTypeError(
            f"must be >= 0 (0 means all sections), got {n}; a negative would silently "
            f"drop the smallest sections from the audit")
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="markdown context file to audit")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--top", type=_nonneg_int, default=0,
                    help="show only the N largest sections (0 = all)")
    ap.add_argument("--capabilities", action="store_true",
                    help="print supported capabilities as JSON and exit 0")
    ap.add_argument("--require", metavar="CAP[,CAP...]",
                    help="exit 0 if all named capabilities are supported, 3 if any are missing")
    ap.add_argument("--rules", action="store_true",
                    help="emit normative lines; exits 4 if zero are found")
    ap.add_argument("--expect-blocks", metavar="N", type=_nonneg_int, default=None,
                    help="with --emit-blocks: exit 7 unless exactly N blocks were emitted")
    ap.add_argument("--emit-blocks", metavar="DIR", dest="emit_blocks",
                    help="write one verbatim file per section into DIR plus manifest.json")
    args = ap.parse_args()

    # Capability probes answer without a path -- the skill gates on these exit codes.
    if args.capabilities:
        print(json.dumps({
            "tool": "context_file_audit.py",
            "capability_schema_version": CAPABILITY_SCHEMA_VERSION,
            "capabilities": CAPABILITIES,
        }, indent=2))
        return 0

    if args.require:
        wanted = [c.strip() for c in args.require.split(",") if c.strip()]
        if not wanted:
            print("--require needs at least one capability name", file=sys.stderr)
            return 3
        missing = [c for c in wanted if c not in CAPABILITIES]
        if missing:
            print(f"unsupported capabilities: {', '.join(missing)}", file=sys.stderr)
            return 3
        return 0

    if args.path is None:
        ap.error("the following arguments are required: path")

    p = Path(args.path)
    if not p.is_file():
        print(json.dumps({"error": f"not a file: {p}"}), file=sys.stderr)
        return 1

    if args.expect_blocks is not None and not args.emit_blocks:
        ap.error("--expect-blocks requires --emit-blocks")

    # Warn (never fail) on a fence that runs to EOF: the split is correct CommonMark,
    # but every heading after the stray fence vanishes and the audit looks structureless.
    try:
        if has_unclosed_fence(p.read_text(encoding="utf-8")):
            print(f"WARNING: {p} ends with an unclosed code fence. Per CommonMark it runs "
                  f"to EOF, so every '## ' heading after it is treated as code and will "
                  f"NOT appear as a section. Byte accounting still balances, but the "
                  f"section view is not actionable until the fence is closed.",
                  file=sys.stderr)
    except (OSError, UnicodeDecodeError):
        pass  # the real read below reports the failure properly

    if args.rules:
        text = p.read_text(encoding="utf-8")
        rules = collect_rules(text)
        if not rules:
            print(f"error: zero normative lines detected in {p}. A context file with no "
                  f"rules means the wrong file or a broken detector; refusing to emit an "
                  f"empty baseline that would make a later comparison vacuously pass.",
                  file=sys.stderr)
            return 4
        result = {"file": str(p), "rule_count": len(rules), "rules": rules}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{result['file']}: {result['rule_count']} normative lines")
            for r in rules:
                print(f"{r['line']:>5}  [{r['section']}]  {r['text']}")
        return 0

    if args.emit_blocks:
        outdir = Path(args.emit_blocks)
        text = p.read_text(encoding="utf-8")
        n_blocks = len(build_blocks(text))
        if not n_blocks:
            print(f"error: zero blocks would be emitted from {p}. Refusing to create an "
                  f"empty directory that would make a per-block diff loop iterate nothing "
                  f"and report PASS.", file=sys.stderr)
            return 5
        # Checked BEFORE the directory is touched: on a mismatch nothing is written and
        # no existing output is cleared, so the caller's prior state survives intact.
        if args.expect_blocks is not None and n_blocks != args.expect_blocks:
            print(f"error: block count mismatch for {p}: expected {args.expect_blocks}, "
                  f"would emit {n_blocks}. Nothing was written. The approved block count "
                  f"and the emitted count must agree exactly, or the per-block diff is "
                  f"checking a different set of blocks than the one you approved.",
                  file=sys.stderr)
            return 7
        err = prepare_block_dir(outdir)
        if err:
            print(f"error: {err}", file=sys.stderr)
            return 6
        try:
            manifest = emit_blocks(p, outdir)
        except OSError as exc:
            print(f"error: could not write blocks into {outdir} ({exc})", file=sys.stderr)
            return 6
        if args.json:
            print(json.dumps(manifest, indent=2))
        else:
            print(f"{manifest['source']}: {manifest['block_count']} blocks -> {outdir}")
            for b in manifest["blocks"]:
                print(f"{b['bytes']:>7}  {b['file']}  {b['sha256'][:12]}  {b['section']}")
        return 0

    result = audit(p)
    if args.top:
        result["sections"] = result["sections"][: args.top]

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"{result['file']}: {result['total_lines']} lines, "
          f"{result['total_bytes'] / 1024:.1f} KB  (loaded every session)")
    print(f"{'bytes':>7} {'share':>7} {'rule':>6} {'ref':>6}  section / suggestion")
    for r in result["sections"]:
        print(f"{r['bytes']:>7} {r['share'] * 100:>6.1f}% {r['rule_density']:>6.2f} "
              f"{r['lookup_density']:>6.2f}  {r['section']}")
        print(f"{'':>29}  -> {r['suggestion']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
