#!/usr/bin/env python3
"""friction_log.py — append/query the friction ledger.

Ledger of small script/hook/template errors that burned a turn. One row per
logical friction: `append` updates the matching row in place (increment count,
extend the first..last date range) rather than adding a duplicate. `dedup`
collapses any legacy duplicate rows and drops false-positive auto-logged rows.
Counts occurrences across (surface, nature-keyword) matches and signals
promotion when threshold crossed.

Promotion ladder:
  1st fire  → row logged, no promotion
  2nd fire  → promotion_action="memory" (create feedback_<slug>.md)
  3rd fire  → promotion_action="script-patch" (mandatory structural fix)

Usage:
  friction_log.py append <surface> <nature> [--fix <description>]
  friction_log.py resolve <surface> <nature> [--note <text>]  # close a row, no count change
  friction_log.py list [--surface <name>] [--unpromoted] [--unresolved]
  friction_log.py query <text>     # fuzzy search nature/surface
  friction_log.py dedup [--dry-run]  # collapse duplicate rows + drop FP rows

Examples:
  friction_log.py append living_log_append.py "rejects multi-line text" \\
      --fix "flattened newlines manually"
  friction_log.py list --surface check_voice_pure.py
  friction_log.py query "blockquote"

JSON-only output on stdout. Atomic write. Single-file ledger at
memory/friction-log.md, table after the "## Entries" marker.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import friction_surface as fs  # shared surface/nature derivation (single source of truth)

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER = Path(os.environ.get(
    "FRICTION_LEDGER", str(REPO_ROOT / "memory" / "friction-log.md")))
ENTRIES_MARKER = "## Entries"

# A "row" is a markdown table row.
# Schema:
#   | Date | Surface | Nature | Fix | Occurrences | Promotion |
HEADER = (
    "| Date | Surface | Nature | Fix | Occurrences | Promotion |\n"
    "|---|---|---|---|---|---|"
)

# Stopwords stripped before keyword overlap; tuned for tech-error text.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "on", "in", "for", "with", "of",
    "to", "is", "it", "that", "this", "be", "was", "are", "as", "at", "by",
    "not", "no", "from", "if", "then", "else", "when", "where", "which",
    "who", "what", "how", "why", "got", "get", "gets", "fix", "fixed",
    "error", "errors", "issue", "issues", "bug", "bugs",
}


def fail(msg: str, code: int = 1) -> "None":
    sys.stdout.write(json.dumps({"status": "error", "message": msg}) + "\n")
    sys.exit(code)


def ok(payload: dict) -> "None":
    payload = {"status": "ok", **payload}
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.exit(0)


# Path segments common to nearly every absolute path this ledger will ever see
# (repo root + macOS home-dir scaffolding). Left untouched, these tokenize as
# ordinary "significant" keywords and dominate the Jaccard similarity between
# two UNRELATED "file not found" errors that merely happen to live under the
# same repo — e.g. two different missing scripts under tools/ can hit Jaccard
# 0.75, well past DEDUP_THRESHOLD (0.6), silently merging distinct frictions
# into one inflated row. Stripped before tokenizing so only the distinguishing
# tail (the actual filename/subpath) drives matching. Origin: 2026-07-08
# friction-log audit — the bash:ls "career_scanner test path missing" row (17
# occurrences) traced back to exactly this over-clustering.
# Derived from REPO_ROOT (not hand-typed) so a relocated checkout can't
# silently desync the regex from the actual path — a hardcoded literal here
# would stop matching if the repo ever moved, quietly reintroducing the very
# over-clustering bug this fix exists to prevent, with no test catching it.
_PATH_NOISE_RE = re.compile(re.escape(str(REPO_ROOT)) + "/", re.IGNORECASE)

# The auto-capture wrapper text every PreToolUse-hook-block row shares
# verbatim, regardless of which specific thing was blocked: the "[auto]"/
# "[auto-stop]" provenance tag and the "PreToolUse:<Tool> hook error:
# [python3 .../check_X.py]:" invocation preamble. Left untouched, this
# boilerplate tokenizes into a dozen "significant" keywords (pretooluse, hook,
# error, python3, write, blocked, ...) shared by EVERY hook-block row, which
# is enough on its own to push two block events for DIFFERENT targets (e.g.
# check_voice_pure.py blocking two unrelated reflection files) to Jaccard 0.75
# — past DEDUP_THRESHOLD — over-merging distinct frictions into one inflated
# row. Same failure class as _PATH_NOISE_RE, different noise source. Origin:
# 2026-07-08 friction-log audit — the check_voice_pure.py row (25 occurrences)
# traced back to this.
_BOILERPLATE_NOISE_RE = re.compile(
    r"^\[auto[^\]]*\]\s*"
    r"|PreToolUse:\w+ hook error:\s*"
    r"|\[python3?\b[^\]]*\]:?",
    re.IGNORECASE,
)


def keywords(text: str) -> set[str]:
    """Lowercased significant words (len>=4, not in stopwords). Repo-root path
    scaffolding and hook-wrapper boilerplate are stripped first — see
    _PATH_NOISE_RE / _BOILERPLATE_NOISE_RE docstrings."""
    text = _PATH_NOISE_RE.sub(" ", text)
    text = _BOILERPLATE_NOISE_RE.sub(" ", text)
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    return {t for t in tokens if len(t) >= 4 and t not in STOPWORDS}


def normalize_surface(s: str) -> str:
    return s.strip().lower()


def atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".friction_log_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_ledger() -> tuple[str, str, list[str], str]:
    """Return (preamble, header_line, data_rows, trailing).

    Splits the ledger on the '## Entries' marker. Inserts header table if
    missing. data_rows are the raw '| ... |' lines after the header.
    """
    if not LEDGER.exists():
        fail(f"ledger not found: {LEDGER} (create it with the header block first)")

    raw = LEDGER.read_text(encoding="utf-8")
    if ENTRIES_MARKER not in raw:
        fail(f"ledger missing '{ENTRIES_MARKER}' marker")

    pre, post = raw.split(ENTRIES_MARKER, 1)
    pre = pre + ENTRIES_MARKER + "\n"

    # Find header table in post; if absent, plant it just below the marker.
    lines = post.splitlines()
    # Strip leading blank/comment lines up to first table-or-EOF
    header_idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("| Date "):
            header_idx = i
            break
    if header_idx is None:
        return pre, HEADER, [], ""

    # Header row + separator row + data rows
    header_block = "\n".join(lines[header_idx:header_idx + 2])
    data_rows: list[str] = []
    trailing_lines: list[str] = []
    in_data = True
    for ln in lines[header_idx + 2:]:
        if in_data and ln.startswith("|"):
            data_rows.append(ln)
        else:
            in_data = False
            trailing_lines.append(ln)
    trailing = "\n".join(trailing_lines)
    return pre, header_block, data_rows, trailing


def parse_row(row: str) -> dict:
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    if len(cells) < 6:
        return {}
    return {
        "date": cells[0],
        "surface": cells[1],
        "nature": cells[2],
        "fix": cells[3],
        "occurrences": cells[4],
        "promotion": cells[5],
        "_raw": row,
    }


def format_row(d: dict) -> str:
    return (
        f"| {d['date']} | {d['surface']} | {d['nature']} | "
        f"{d['fix']} | {d['occurrences']} | {d['promotion']} |"
    )


# A friction's identity is its NATURE (the error produced), not which script
# surfaced it. When two rows' natures are near-identical (e.g. "command not found:
# python" on different scripts) they are the SAME logical friction even on a
# different surface. Same-surface rows still match at the looser find_matches
# threshold; cross-surface only counts when the nature is near-identical.
CROSS_SURFACE_MATCH = 0.6


def match_score(existing: dict, surface: str, nature_kw: set[str]) -> float:
    """0..1 match between an existing row and a candidate (surface, nature_kw)."""
    ex_kw = keywords(existing["nature"])
    if not ex_kw or not nature_kw:
        return 0.0
    overlap = len(ex_kw & nature_kw)
    if overlap == 0:
        return 0.0
    jac = overlap / len(ex_kw | nature_kw)  # Jaccard
    if normalize_surface(existing["surface"]) == normalize_surface(surface):
        return jac
    return jac if jac >= CROSS_SURFACE_MATCH else 0.0


def find_matches(rows: list[dict], surface: str, nature: str, threshold: float = 0.25):
    nature_kw = keywords(nature)
    scored = []
    for r in rows:
        s = match_score(r, surface, nature_kw)
        if s >= threshold:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return scored


# --- data-quality helpers (dedup / FP purge / promotion recompute) ---------

DEDUP_THRESHOLD = 0.6  # higher than append's match threshold: only collapse
                       # near-identical natures, keep distinct error messages apart.

# A row whose (auto-tag-stripped) nature matches any of these is NOT a real
# tool/script friction — it is test-runner output or success stdout that the
# auto-log hook captured by mistake. dedup drops these and reports each one.
FP_PATTERNS = [
    r"test session starts",
    r"\.\.\.\s*(ok|FAIL|ERROR|skipped)\b",  # unittest result line (with or without the (module) prefix)
    r"ResourceWarning",
    r"^Total messages:",
    r"^PDF written to\b",
    r"^-rw-",                 # `ls -l` listing line
    r"^[\w.\-/]+\.md$",       # bare filename echoed to stdout
    # echoed shell scaffolding the auto-log hook captured by mistake — not frictions:
    r"={3,}",                 # `echo "=== section header ==="`
    r"^\.{3,}",               # pytest progress dots (".........")
    r"\[\s*\d{1,3}%\]",       # pytest percent marker ("[100%]")
    r'^\s*"""',               # docstring / cat'd source first line
    r"^\?\?\s",               # `git status --porcelain` untracked line
    r"^\d+\s+/",              # `wc -l <path>` line ("151 /path/file")
    r"^\d+$",                 # bare count (e.g. `grep -c` → 0)
    r"Cancelled: parallel tool call",  # sibling-cancellation cascade, not its own friction
    r"unittest\.loader\._FailedTest",  # pytest collection-error node id, not a friction
]

AUTO_TAG_RE = re.compile(r"^\[auto[^\]]*\]\s*")


def strip_auto_tag(nature: str) -> str:
    return AUTO_TAG_RE.sub("", nature).strip()


def is_false_positive(nature: str, fix: str = "") -> bool:
    body = strip_auto_tag(nature)
    if any(re.search(p, body) for p in FP_PATTERNS):
        return True
    # Masked-benign PostToolUseFailure: exit code unresolved ("exit=?" in the
    # auto-logged fix text) AND the captured nature has no error signal —
    # almost always an earlier stage's successful stdout in a chained/piped
    # bash command. See friction_surface.looks_like_real_error and the
    # 2026-07-08 friction-log audit (On branch main / shebang-line / markdown
    # header rows all matched this shape). log_tool_failure.py now guards this
    # at capture time too; this is the retroactive-cleanup half via dedup.
    if "exit=?" in (fix or "") and not fs.looks_like_real_error(body):
        return True
    return False


def _dates_in(cell: str) -> list[str]:
    return re.findall(r"\d{4}-\d{2}-\d{2}", cell)


def _occ_int(cell: str) -> int:
    """Parse a row's running occurrence count; default 1 if unparseable."""
    m = re.search(r"\d+", cell or "")
    return int(m.group()) if m else 1


def first_seen(cell: str) -> str:
    ds = _dates_in(cell)
    return ds[0] if ds else cell.strip()


def date_cell(first: str, last: str) -> str:
    return first if first == last else f"{first}..{last}"


def promotion_for(occurrences: int) -> str:
    if occurrences <= 1:
        return "—"
    if occurrences == 2:
        return "memory (do now)"
    return "script-patch (mandatory)"


def cmd_append(args: argparse.Namespace) -> None:
    surface = args.surface.strip()
    nature = args.nature.strip()
    fix = (args.fix or "—").strip().replace("\n", " ")
    if not surface or not nature:
        fail("surface and nature required")
    if "|" in surface or "|" in nature or "|" in fix:
        fail("'|' not allowed in fields (breaks table); use '/' or ';'")

    # Non-friction guard (D2-H1, 2026-06-01): test-runner output and success
    # stdout get swept in by the auto-log hooks (log_tool_failure.py on a failing
    # `pytest` exit; scan_transcript_failures.py on an is_error tool_result). A
    # failing test is dev signal, not a tool friction. Reject at this single
    # chokepoint — every logger (auto + manual + backfill) funnels through here —
    # reusing the same is_false_positive() the dedup pass uses.
    if is_false_positive(nature, fix):
        ok({"action": "skipped",
            "reason": "non-friction (test-runner or stdout output)",
            "surface": surface, "nature": nature[:120]})

    pre, header_block, data_rows, trailing = read_ledger()
    parsed = [parse_row(r) for r in data_rows]
    parsed = [p for p in parsed if p]

    matches = find_matches(parsed, surface, nature)
    today = str(date.today())

    if matches:
        # Update the matched row IN PLACE: increment its running count and
        # extend its date range, instead of prepending a duplicate row. This
        # keeps the ledger at one row per logical friction.
        best = matches[0][1]
        occurrences = int(best.get("occurrences", "1") or 1) + 1
        new_row = {
            "date": date_cell(first_seen(best["date"]), today),
            "surface": best["surface"],
            "nature": best["nature"],
            "fix": fix if args.fix else best["fix"],
            "occurrences": str(occurrences),
            "promotion": promotion_for(occurrences),
        }
        # Drop the old matched row, re-add the updated one at the top.
        new_rows = [format_row(new_row)] + [r for r in data_rows if r != best["_raw"]]
        matched_raw = [best["_raw"]]
    else:
        occurrences = 1
        new_row = {
            "date": today,
            "surface": surface,
            "nature": nature,
            "fix": fix,
            "occurrences": "1",
            "promotion": promotion_for(1),
        }
        new_rows = [format_row(new_row)] + data_rows
        matched_raw = []

    body = pre + "\n" + header_block + "\n" + "\n".join(new_rows)
    if trailing.strip():
        body += "\n" + trailing
    if not body.endswith("\n"):
        body += "\n"

    atomic_write(LEDGER, body)

    promotion_action = (
        "none" if occurrences == 1
        else "memory" if occurrences == 2
        else "script-patch"
    )
    ok({
        "action": "updated" if matches else "appended",
        "occurrences": occurrences,
        "promotion_action": promotion_action,
        "matched_rows": matched_raw,
        "row": format_row(new_row),
    })


RESOLVED_PREFIX = "RESOLVED"


def is_resolved(fix: str) -> bool:
    return fix.strip().startswith(RESOLVED_PREFIX)


def cmd_resolve(args: argparse.Namespace) -> None:
    """Mark the row matching (surface, nature-query) resolved.

    Resolution is recorded in the Fix cell as "RESOLVED <date>: <note>". The
    occurrence count and promotion signal are left untouched — resolving a
    friction does not erase its history, it just stops it reading as open. This
    is the only sanctioned way to close a row: the ledger header forbids
    hand-editing entries, and `append` would inflate the count. If the query is
    ambiguous (top two matches within a small margin), refuse and list the
    candidates rather than resolve the wrong row.
    """
    surface = args.surface.strip()
    query = args.nature.strip()
    note = (args.note or "").strip().replace("\n", " ")
    if "|" in note:
        fail("'|' not allowed in note (breaks table); use '/' or ';'")
    if not surface or not query:
        fail("surface and nature-query required")

    pre, header_block, data_rows, trailing = read_ledger()
    parsed = [p for p in (parse_row(r) for r in data_rows) if p]

    matches = find_matches(parsed, surface, query, threshold=0.4)
    if not matches:
        fail(f"no row matches surface={surface!r} nature~={query!r}")
    if len(matches) > 1 and (matches[0][0] - matches[1][0]) < 0.15:
        # too close to call — don't guess which row to close
        ok({"action": "ambiguous",
            "reason": "multiple rows match closely; narrow the nature query",
            "rows": [m[1] for m in matches[:5]]})

    best = matches[0][1]
    today = str(date.today())
    resolved_fix = f"{RESOLVED_PREFIX} {today}: {note}" if note else f"{RESOLVED_PREFIX} {today}"
    updated = {k: v for k, v in best.items() if not k.startswith("_")}
    updated["fix"] = resolved_fix

    new_rows = [format_row(updated) if r == best["_raw"] else r for r in data_rows]
    body = pre + "\n" + header_block + "\n" + "\n".join(new_rows)
    if trailing.strip():
        body += "\n" + trailing
    if not body.endswith("\n"):
        body += "\n"
    atomic_write(LEDGER, body)

    ok({"action": "resolved", "matched": best["_raw"], "row": format_row(updated)})


def cmd_list(args: argparse.Namespace) -> None:
    _, _, data_rows, _ = read_ledger()
    parsed = [parse_row(r) for r in data_rows]
    parsed = [p for p in parsed if p]
    out = parsed
    if args.surface:
        out = [r for r in out if normalize_surface(r["surface"]) == normalize_surface(args.surface)]
    if args.unpromoted:
        # A row is "unpromoted" when it HAS a promotion action due (memory or
        # script-patch — i.e. the promotion field is set, meaning it crossed
        # the 2nd/3rd-fire threshold) AND that action hasn't been executed yet
        # (no RESOLVED marker in the Fix cell). The inverse of this — filtering
        # to rows where promotion is EMPTY — silently hid every row with a real
        # promotion due, breaking the promotion loop end to end. Origin: 2026-07-08.
        out = [
            r for r in out
            if r["promotion"].strip() not in ("—", "-", "")
            and not is_resolved(r["fix"])
        ]
    if getattr(args, "unresolved", False):
        out = [r for r in out if not is_resolved(r["fix"])]
    # Scope, stated next to the conclusion (2026-08-19). `--surface ""` is falsy, so the
    # filter was silently skipped and a scoped query returned all 257 rows looking exactly
    # like a 1-row answer. The ladder counts fires PER SURFACE, so an unfiltered read
    # misstates a rung. Additive only: status and exit codes are unchanged.
    ok({
        "count": len(out),
        "total_rows": len(parsed),
        "filters_applied": {
            # Mirrors the EXACT condition the filter above uses, deliberately.
            # An earlier version reported .strip()-emptiness, which meant
            # `--surface "   "` filtered (to zero rows) while the report said no
            # filter ran -- a report that lies about its own scope is worse than
            # the silent-skip it was meant to fix.
            "surface": args.surface if args.surface else None,
            "unpromoted": bool(args.unpromoted),
            "unresolved": bool(getattr(args, "unresolved", False)),
        },
        "rows": out,
    })


def cmd_query(args: argparse.Namespace) -> None:
    _, _, data_rows, _ = read_ledger()
    parsed = [parse_row(r) for r in data_rows]
    parsed = [p for p in parsed if p]
    q_kw = keywords(args.text)
    if not q_kw:
        fail("query needs at least one significant word (len>=4)")
    scored = []
    for r in parsed:
        kw = keywords(r["surface"]) | keywords(r["nature"]) | keywords(r["fix"])
        overlap = len(kw & q_kw)
        if overlap:
            scored.append((overlap, r))
    scored.sort(key=lambda x: (-x[0], x[1]["date"]))
    ok({"count": len(scored), "rows": [r for _, r in scored[:20]]})


def cmd_dedup(args: argparse.Namespace) -> None:
    """Collapse the ledger to one row per logical friction.

    - Drops false-positive rows (test-runner output / success stdout the
      auto-log hook captured by mistake) and reports each.
    - Groups remaining rows by (surface, nature-keyword Jaccard >= threshold).
    - Each group becomes one row: occurrences = group size (true total),
      Date = first-seen..last-seen, promotion recomputed from the count.
    - --dry-run reports the plan without writing.
    """
    pre, header_block, data_rows, trailing = read_ledger()
    parsed = [p for p in (parse_row(r) for r in data_rows) if p]

    dropped, kept = [], []
    for p in parsed:
        (dropped if is_false_positive(p["nature"], p.get("fix", "")) else kept).append(p)

    # Cluster on nature similarity ALONE — surface is contextual metadata, not the
    # friction's identity. The bare-`python` interpreter error recurs across many
    # surfaces (pipeline_staleness.py, networking_write.py, bash:cd, ...); keying on
    # surface fragmented it into single-count rows so the promotion ladder never
    # fired. DEDUP_THRESHOLD (0.6) is strict enough that distinct errors stay apart.
    clusters: list[dict] = []  # {"rows": [...], "kw": set}
    for p in kept:
        p_kw = keywords(p["nature"])
        placed = False
        for c in clusters:
            if not p_kw or not c["kw"]:
                continue
            jac = len(p_kw & c["kw"]) / len(p_kw | c["kw"])
            if jac >= DEDUP_THRESHOLD:
                c["rows"].append(p)
                placed = True
                break
        if not placed:
            clusters.append({"rows": [p], "kw": p_kw})

    collapsed = []
    for c in clusters:
        rows = c["rows"]
        # Sum the rows' running counts — a row may already carry occurrences>1 from
        # prior in-place appends; counting rows alone would silently deflate it.
        occ = sum(_occ_int(r["occurrences"]) for r in rows)
        dates = sorted(d for r in rows for d in _dates_in(r["date"]))
        first = dates[0] if dates else rows[0]["date"]
        last = dates[-1] if dates else rows[0]["date"]
        nat_counts = Counter(strip_auto_tag(r["nature"]) for r in rows)
        rep_nature = sorted(nat_counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))[0][0]
        # Representative surface = most common across the cluster (cross-surface
        # frictions like the python error pick whichever script hit it most).
        surf_counts = Counter(r["surface"] for r in rows)
        rep_surface = surf_counts.most_common(1)[0][0]
        real_fixes = [
            r["fix"] for r in rows
            if r["fix"] and not r["fix"].startswith("[auto") and r["fix"] not in ("—", "-")
        ]
        rep_fix = max(real_fixes, key=len) if real_fixes else rows[0]["fix"]
        collapsed.append({
            "date": date_cell(first, last),
            "surface": rep_surface,
            "nature": rep_nature,
            "fix": rep_fix,
            "occurrences": str(occ),
            "promotion": promotion_for(occ),
            "_occ": occ,
            "_last": last,
        })

    collapsed.sort(key=lambda d: d["_last"], reverse=True)
    collapsed.sort(key=lambda d: d["_occ"], reverse=True)

    # Reconciliation invariant: every source row is either dropped as FP or lands
    # in exactly one cluster. If this is False the rebuild lost or double-counted
    # rows — caller should NOT trust the result. (Origin: 2026-06-02 dedup audit.)
    rows_clustered = sum(len(c["rows"]) for c in clusters)
    reconciles = (len(dropped) + rows_clustered == len(parsed))

    report = {
        "rows_before": len(parsed),
        "rows_after": len(collapsed),
        "dropped_fp": len(dropped),
        "reconciles": reconciles,
        "dropped_samples": [strip_auto_tag(d["nature"])[:90] for d in dropped],
        "clusters": [
            {"surface": d["surface"], "occurrences": d["_occ"],
             "promotion": d["promotion"], "nature": d["nature"][:70]}
            for d in collapsed
        ],
    }

    if args.dry_run:
        ok({"action": "dry-run", **report})
        return

    new_rows = [
        format_row({k: v for k, v in d.items() if not k.startswith("_")})
        for d in collapsed
    ]
    body = pre + "\n" + header_block + "\n" + "\n".join(new_rows)
    if trailing.strip():
        body += "\n" + trailing
    if not body.endswith("\n"):
        body += "\n"
    atomic_write(LEDGER, body)
    ok({"action": "rebuilt", **report})


def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("append", help="log a new friction")
    pa.add_argument("surface", help="script/hook/template that fired (e.g. living_log_append.py)")
    pa.add_argument("nature", help="short description of the friction")
    pa.add_argument("--fix", default=None, help="how it was resolved this turn")
    pa.set_defaults(func=cmd_append)

    pl = sub.add_parser("list", help="list ledger rows")
    pl.add_argument("--surface", default=None)
    pl.add_argument("--unpromoted", action="store_true")
    pl.add_argument("--unresolved", action="store_true",
                    help="only rows whose Fix is not yet a RESOLVED marker")
    pl.set_defaults(func=cmd_list)

    pr = sub.add_parser("resolve", help="mark a row resolved (no count change)")
    pr.add_argument("surface", help="surface of the row to resolve")
    pr.add_argument("nature", help="nature keywords identifying the row")
    pr.add_argument("--note", default=None, help="resolution note for the Fix cell")
    pr.set_defaults(func=cmd_resolve)

    pq = sub.add_parser("query", help="fuzzy search rows")
    pq.add_argument("text", help="search text")
    pq.set_defaults(func=cmd_query)

    pd = sub.add_parser("dedup", help="collapse to one row per friction; drop FP rows")
    pd.add_argument("--dry-run", action="store_true", help="report the plan without writing")
    pd.set_defaults(func=cmd_dedup)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
