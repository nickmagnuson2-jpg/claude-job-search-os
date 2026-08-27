#!/usr/bin/env python3
"""person_history.py — the whole record on a person or company, ORIGIN FIRST.

WHY THIS EXISTS (2026-08-27). A recommendation about re-approaching a target company was
wrong in two ways in the same answer: it called an ask "invented" when the records showed
it was the original thread, and it advised leading with a different role that turned out
to be the same seat under another name. Both errors were refutable from
`data/networking.md` in about ten seconds. The line that settled it looked like this:

    "<introducer> resurfaced 7/19 with <Company> CoS role, redirected to Strategist 7/21"

and had been sitting in the file the whole time. What happened is a SCOPE choice: the
externally-checkable question ("is the role posted?") got a live web search, and the
load-bearing internal question ("what is the history of this ask?") got nothing. The repo
already has a rule for that shape (name the scope in the same sentence as the conclusion);
it just had no cheap instrument pointed at its own records.

ORIGIN FIRST IS THE DESIGN, not a display preference. Interactions print OLDEST FIRST and
the earliest is surfaced separately as `origin`. Recency is what a reader reconstructs by
default, and recency is precisely what misled the recommendation: the most recent
interactions were all about a loop closing, and none of them mentioned that the
relationship had begun with a different role the candidate himself had steered away from.

WHAT IT IS NOT. A retrieval tool, not a gate. It cannot stop anyone from recommending
something unchecked; it makes the check one command instead of six greps, so the correct
move is also the cheapest one. The enforcement tier is whatever skill calls it.

USAGE
    PYTHONIOENCODING=utf-8 python3 tools/person_history.py "Casey Doe"
    PYTHONIOENCODING=utf-8 python3 tools/person_history.py --company Acme
    PYTHONIOENCODING=utf-8 python3 tools/person_history.py "Casey Doe" --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get("PERSON_HISTORY_REPO_ROOT",
                                Path(__file__).resolve().parents[1])).resolve()

NETWORKING = "data/networking.md"
PIPELINE = "data/job-pipeline.md"
TODOS = "data/job-todos.md"
OUTREACH = "data/outreach-log.md"

_ENTRY_RE = re.compile(r"^#### (\d{4}-\d{2}-\d{2}) \| ([^|]*) \| (.*)$")


def _read(rel: str) -> str:
    try:
        return (REPO_ROOT / rel).read_text(encoding="utf-8")
    except OSError:
        return ""


def contact_row(name: str) -> dict | None:
    """The person's row in the networking contacts table."""
    for line in _read(NETWORKING).splitlines():
        if not line.startswith("| "):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if cols and cols[0].lower() == name.lower():
            keys = ("name", "company", "role", "relationship",
                    "first_contact", "last_interaction", "email")
            return {k: (cols[i] if i < len(cols) else "") for i, k in enumerate(keys)}
    return None


def interactions(name: str) -> list[dict]:
    """Every logged interaction, OLDEST FIRST.

    The file stores newest-first; this reverses it deliberately. A caller skimming the top
    of a newest-first list sees only how a thread is ENDING, which is the exact reading
    that produced the 2026-08-27 error.
    """
    text = _read(NETWORKING)
    i = text.find(f"### {name} ")
    if i == -1:
        i = text.find(f"### {name}\n")
    if i == -1:
        return []
    j = text.find("\n### ", i + 5)
    section = text[i:j if j != -1 else len(text)]

    out = []
    for line in section.splitlines():
        m = _ENTRY_RE.match(line)
        if m:
            out.append({"date": m.group(1), "channel": m.group(2).strip(),
                        "summary": m.group(3).strip()})
    out.sort(key=lambda r: r["date"])
    return out


def _table_rows(rel: str, needle: str) -> list[str]:
    return [l.strip() for l in _read(rel).splitlines()
            if l.startswith("| ") and needle.lower() in l.lower()]


def pipeline_rows(needle: str) -> list[dict]:
    rows = []
    for line in _table_rows(PIPELINE, needle):
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) >= 3 and cols[0] and not cols[0].startswith("-"):
            rows.append({"company": cols[0], "role": cols[1], "stage": cols[2],
                         "updated": cols[3] if len(cols) > 3 else ""})
    return rows


def todos(needle: str) -> list[dict]:
    rows = []
    for line in _table_rows(TODOS, needle):
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) >= 4 and cols[0] and not cols[0].startswith("-"):
            rows.append({"task": cols[0], "priority": cols[1],
                         "due": cols[2], "status": cols[3]})
    return rows


def outreach_rows(needle: str) -> list[dict]:
    rows = []
    for line in _table_rows(OUTREACH, needle):
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) >= 7 and re.match(r"\d{4}-\d{2}-\d{2}", cols[0]):
            rows.append({"date": cols[0], "type": cols[1], "channel": cols[2],
                         "recipient": cols[3], "status": cols[6]})
    return rows


def artifacts(needle: str) -> list[str]:
    """Debriefs and generated output that name this person or company."""
    hits = []
    for sub in ("coaching/progress", "output"):
        base = REPO_ROOT / sub
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.md")):
            try:
                if needle.lower() in p.read_text(encoding="utf-8", errors="ignore").lower():
                    hits.append(str(p.relative_to(REPO_ROOT)))
            except OSError:
                continue
    return hits


def build(needle: str, is_company: bool = False) -> dict:
    ints = [] if is_company else interactions(needle)
    contact = None if is_company else contact_row(needle)

    # A person's pipeline history lives under their COMPANY, never their name, so a
    # name-only search returns nothing and the lookup silently looks complete. Widening
    # to the employer is the cross-reference the 2026-08-27 error actually needed: the
    # relevant row was filed under the company the whole time.
    pipe = pipeline_rows(needle)
    company = (contact or {}).get("company", "").strip()
    if company and company != "-":
        seen = {(r["company"], r["role"]) for r in pipe}
        for r in pipeline_rows(company):
            if (r["company"], r["role"]) not in seen:
                r = {**r, "via": f"company of {needle}"}
                pipe.append(r)
    return {
        "query": needle,
        "kind": "company" if is_company else "person",
        "contact": contact,
        # The single most-missed field. Named separately so it cannot be skimmed past.
        "origin": ints[0] if ints else None,
        "interactions_oldest_first": ints,
        "interaction_count": len(ints),
        "pipeline": pipe,
        "todos": todos(needle),
        "outreach": outreach_rows(needle),
        "artifacts": artifacts(needle),
    }


def render(d: dict) -> str:
    o = [f"# {d['query']} — full record ({d['kind']})", ""]
    c = d.get("contact")
    if c:
        o.append(f"**{c['name']}** | {c['company']} | {c['role']}")
        o.append(f"Relationship: {c['relationship'][:200]}")
        o.append(f"First contact: {c['first_contact']} · Last: {c['last_interaction']} "
                 f"· {c['email']}")
        o.append("")

    if d["origin"]:
        g = d["origin"]
        o += ["## ORIGIN — how this relationship started", "",
              f"**{g['date']} | {g['channel']}** {g['summary'][:600]}", "",
              "> Read this before recommending anything. A newest-first skim shows how a "
              "thread is ENDING and hides what it was FOR.", ""]

    if d["interactions_oldest_first"]:
        o += [f"## Interactions ({d['interaction_count']}, oldest first)", ""]
        for r in d["interactions_oldest_first"]:
            o.append(f"- **{r['date']}** ({r['channel']}) {r['summary'][:240]}")
        o.append("")

    for key, title in (("pipeline", "Pipeline"), ("todos", "To-dos"),
                       ("outreach", "Outreach log")):
        if d[key]:
            o += [f"## {title}", ""]
            for r in d[key]:
                o.append("- " + " · ".join(f"{k}={v}" for k, v in r.items() if v))
            o.append("")

    if d["artifacts"]:
        o += [f"## Artifacts ({len(d['artifacts'])})", ""]
        o += [f"- `{p}`" for p in d["artifacts"]]
    return "\n".join(o)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="person's full name")
    ap.add_argument("--company", help="look up a company instead of a person")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    needle, is_company = (args.company, True) if args.company else (args.name, False)
    if not needle:
        print("give a name or --company", file=sys.stderr)
        return 2

    d = build(needle, is_company)
    if not any((d["contact"], d["interactions_oldest_first"], d["pipeline"],
                d["todos"], d["outreach"], d["artifacts"])):
        # Absence is a REPORTABLE result, not an empty success: "no record" is exactly the
        # finding that should stop a confident recommendation.
        print(json.dumps({"query": needle, "found": False,
                          "note": "NO RECORD in networking, pipeline, todos, outreach, "
                                  "or artifacts. Treat any claim about prior history as "
                                  "unverified."}, indent=2))
        return 1

    print(json.dumps(d, indent=2) if args.json else render(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
