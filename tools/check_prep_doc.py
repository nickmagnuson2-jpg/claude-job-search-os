#!/usr/bin/env python3
"""
check_prep_doc.py — Six mechanical checks on a /prep-interview prep doc.

Checks 1-3 enforce W4: a prep doc must bind a PRIMARY and a RESERVE proof, and the two
must sit in genuinely different domains (canonicalized, so `customer-experience` and
`customer-ops` cannot pose as a fallback for one another).

Checks 4-6 enforce W1 at the artifact level: a doc may not tell Nick to withhold
something unless the outreach log says it actually arrived. This is where the 2026-08-10
defect gets caught mechanically rather than by anyone remembering the rule.

Deliberately NOT a hook. The suppressive-phrasing boundary is contextual and a regex
hook over it would over-fire; this runs on demand against a specific doc.

Exit codes: 0 compliant | 1 one or more failed checks | 2 the doc cannot be read.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_vocab  # noqa: E402
import prep_doc_parse  # noqa: E402
import proof_domains  # noqa: E402

LOCAL_VALIDATORS = "tools/.local-validators.json"

# The frozen W1 v2 stamp. A v1 stamp (no `artifact=` field) must FAIL check 6 rather
# than be silently accepted, because a recipient-level stamp cannot license an
# artifact-level suppression and v1 had no way to say which artifact it meant.
STAMP_RE = re.compile(
    r"<!--\s*outreach_status:\s*"
    r"recipient=(?P<recipient>.+?)\s+"
    r"artifact=(?P<artifact>\S+)\s+"
    r"delivered=(?P<delivered>\S+)\s+"
    r"as_of=(?P<as_of>\S+)\s+"
    r"tool_version=(?P<version>\d+)\s*-->"
)
STAMP_MARKER_RE = re.compile(r"<!--\s*outreach_status:.*?-->", re.S)
EXPECTED_TOOL_VERSION = 2

# Versioned and ENUMERATED, not open-ended. Check 4 fires if and only if the Logistics
# block matches one of these — there is deliberately no "any send-state assertion"
# clause, because that judgment belongs to the model reading the /prep-interview skill
# instruction, not to a regex. Extension protocol: a new pattern ships together with a
# new fixture case in tests/fixtures/w4/, and this version constant is bumped.
SUPPRESSIVE_PATTERNS_VERSION = 1
SUPPRESSIVE_PATTERNS = [
    r"do ?n[o']t re-?offer",
    r"already sent",
    r"already handled",
    r"no need to send",
    r"already delivered",
    r"already have it",
]
_SUPPRESSIVE_RE = re.compile("|".join(f"(?:{p})" for p in SUPPRESSIVE_PATTERNS), re.I)

# The live 081026 prep doc writes `# Logistics` (h1) while the /prep-interview output
# template writes `## Logistics`. Both are real, so the block is located at ANY heading
# level; pinning it to `##` would have made check 4 silently never fire on the very doc
# that motivated this tool. Verified against output/<slug>/<MMDDYY>-prep.md on 2026-08-12.
_LOGISTICS_HEADING_RE = re.compile(r"^(#{1,6})\s*Logistics\b", re.I)
_ANY_HEADING_RE = re.compile(r"^(#{1,6})\s")

_DOC_DATE_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})-")


def doc_date(path: Path, fallback: str | None) -> str | None:
    """Doc date as YYYY-MM-DD from the MMDDYY filename prefix, else the --as-of fallback."""
    m = _DOC_DATE_RE.match(path.name)
    if m:
        month, day, year = m.groups()
        return f"20{year}-{month}-{day}"
    return fallback


def logistics_block(text: str) -> str:
    """The Logistics section, bounded by the next heading of the same or higher level."""
    lines = text.splitlines()
    start = level = None
    for i, line in enumerate(lines):
        m = _LOGISTICS_HEADING_RE.match(line)
        if m:
            start, level = i + 1, len(m.group(1))
            break
    if start is None:
        return ""
    for j in range(start, len(lines)):
        m = _ANY_HEADING_RE.match(lines[j])
        if m and len(m.group(1)) <= level:
            return "\n".join(lines[start:j])
    return "\n".join(lines[start:])


def _sentences(block: str) -> list[str]:
    """Split a block into sentences. A line/bullet boundary ends a sentence.

    Logistics blocks are bullet lists whose items often lack terminal punctuation, so
    line boundaries have to count -- otherwise two unrelated bullets merge into one
    "sentence" and the artifact named in the first is credited to the suppression in
    the second.
    """
    normalized = []
    for line in block.splitlines():
        stripped = re.sub(r"^\s*[-*]\s*", "", line).strip()
        if not stripped:
            continue
        normalized.append(stripped if stripped[-1] in ".?!" else stripped + ".")
    flat = " ".join(normalized)
    return [s.strip() for s in re.split(r"(?<=[.?!])\s+", flat) if s.strip()]


def parse_stamps(text: str) -> tuple[list[dict], list[str]]:
    """Return (well-formed stamps, malformed raw stamp strings)."""
    good, bad = [], []
    for raw in STAMP_MARKER_RE.findall(text):
        m = STAMP_RE.fullmatch(raw.strip())
        if m and int(m.group("version")) == EXPECTED_TOOL_VERSION:
            good.append(m.groupdict())
        else:
            bad.append(raw.strip())
    return good, bad


def check_doc(path: Path, as_of: str | None = None) -> tuple[int, dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return 2, {"doc": str(path), "compliant": False,
                   "failures": [{"check": 0, "message": f"cannot read doc: {exc}"}]}

    failures: list[dict] = []
    proofs = prep_doc_parse.parse_proofs(path)

    # --- checks 1 & 2: both proof lines present and well-formed
    for check_no, slot in ((1, "primary"), (2, "reserve")):
        if proofs[slot] is None:
            failures.append({
                "check": check_no,
                "message": (
                    f"no **{slot.capitalize()} proof** line matching the frozen format "
                    f"`**{slot.capitalize()} proof** (domain: <tag>): <proof>`. "
                    + ("Binding exactly one proof is a single point of failure: when the "
                       "interviewer excludes its domain mid-call there is no fallback."
                       if slot == "reserve" else
                       "The doc binds no primary proof.")
                ),
            })

    # --- check 3: valid AND canonically distinct domains
    if proofs["primary"] and proofs["reserve"]:
        canon = {}
        for slot in ("primary", "reserve"):
            tag = proofs[slot]["tag"]
            resolved = proof_domains.canonicalize(tag)
            if resolved is None:
                failures.append({
                    "check": 3,
                    "message": (f"{slot} domain tag '{tag}' is not in the closed enum. "
                                f"Valid tags: {', '.join(proof_domains.valid_tags())}"),
                })
            canon[slot] = resolved
        if canon.get("primary") and canon.get("primary") == canon.get("reserve"):
            failures.append({
                "check": 3,
                "message": (
                    f"primary '{proofs['primary']['tag']}' and reserve "
                    f"'{proofs['reserve']['tag']}' both canonicalize to "
                    f"'{canon['primary']}' — that is one domain wearing two labels, not a "
                    f"fallback. One exclusion sentence takes out both proofs."
                ),
            })

    # --- check 6 first: a malformed stamp must not be silently ignored, and checks 4/5
    #     must not reason from one.
    stamps, malformed = parse_stamps(text)
    for raw in malformed:
        failures.append({
            "check": 6,
            "message": (f"malformed outreach_status stamp (expected the frozen v"
                        f"{EXPECTED_TOOL_VERSION} format with an `artifact=` field): {raw}"),
        })

    # --- check 4: suppressive phrasing must name its artifact and be backed by receipt
    block = logistics_block(text)
    for sentence in _sentences(block):
        if not _SUPPRESSIVE_RE.search(sentence):
            continue
        artifacts = artifact_vocab.find_in_text(sentence)
        if not artifacts:
            failures.append({
                "check": 4,
                "message": ("suppressive phrasing must name the artifact it suppresses: "
                            f"{sentence!r}"),
            })
            continue
        for artifact in artifacts:
            backing = [s for s in stamps if s["artifact"] == artifact]
            if not backing:
                failures.append({
                    "check": 4,
                    "message": (f"suppressive phrasing about '{artifact}' has no "
                                f"outreach_status stamp for that artifact: {sentence!r}. "
                                f"A file under output/ is a draft archive, not a send."),
                })
                continue
            if not any(s["delivered"] == "true" for s in backing):
                states = sorted({s["delivered"] for s in backing})
                failures.append({
                    "check": 4,
                    "message": (
                        f"suppressive phrasing about '{artifact}' is backed only by "
                        f"delivered={'/'.join(states)}. Only delivered=true licenses "
                        f"suppression; write \"sent <date>, no delivery confirmation — "
                        f"offer it again if asked\" instead: {sentence!r}"
                    ),
                })

    # --- check 5: a stamp must not be older than the doc it justifies
    docdate = doc_date(path, as_of)
    if docdate:
        for stamp in stamps:
            if stamp["as_of"] and stamp["as_of"] < docdate:
                failures.append({
                    "check": 5,
                    "message": (f"stamp as_of={stamp['as_of']} predates the doc date "
                                f"{docdate}; the send-state was not checked for this doc."),
                })

    failures.sort(key=lambda f: f["check"])
    return (1 if failures else 0), {
        "doc": str(path),
        "compliant": not failures,
        "failures": failures,
        "suppressive_patterns_version": SUPPRESSIVE_PATTERNS_VERSION,
    }


def validate_local() -> tuple[int, dict]:
    """Tier 2: run the checker against the real historical prep doc named in the
    gitignored validators file, and assert it is caught."""
    vpath = Path(LOCAL_VALIDATORS)
    if not vpath.exists():
        return 0, {"status": "SKIPPED", "reason": f"{LOCAL_VALIDATORS} absent"}

    cases = json.loads(vpath.read_text(encoding="utf-8")).get("w4", [])
    if not cases:
        return 0, {"status": "SKIPPED", "reason": f"no 'w4' cases in {LOCAL_VALIDATORS}"}

    results, failed = [], 0
    for case in cases:
        doc = Path(case["doc"])
        if not doc.is_file():
            results.append({"doc": case["doc"], "pass": False, "reason": "doc not found"})
            failed += 1
            continue
        _, report = check_doc(doc, case.get("as_of"))
        got_checks = sorted({f["check"] for f in report["failures"]})
        problems = []
        if report["compliant"] != case.get("expect_compliant", False):
            problems.append(f"compliant={report['compliant']}")
        for expected in case.get("expect_failed_checks", []):
            if expected not in got_checks:
                problems.append(f"expected check {expected} to fail")
        failed += bool(problems)
        results.append({
            "doc": case["doc"], "compliant": report["compliant"],
            "failed_checks": got_checks, "pass": not problems, "problems": problems,
            "failures": report["failures"],
        })

    return (1 if failed else 0), {
        "status": "FAIL" if failed else "PASS",
        "cases": len(results), "failed": failed, "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a /prep-interview prep doc.")
    # The positional path is optional so that a bare --validate-local invocation does
    # not die in argparse before the tool runs.
    parser.add_argument("path", nargs="?")
    parser.add_argument("--as-of", dest="as_of")
    parser.add_argument("--validate-local", action="store_true", dest="validate_local")
    args = parser.parse_args()

    if args.validate_local:
        code, payload = validate_local()
        print(json.dumps(payload, ensure_ascii=False))
        return code

    if not args.path:
        print(json.dumps({"error": "a prep-doc path is required (or use --validate-local)"}))
        return 2

    code, payload = check_doc(Path(args.path), args.as_of)
    print(json.dumps(payload, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
