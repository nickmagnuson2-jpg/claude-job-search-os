#!/usr/bin/env python3
"""
check_doc_precedence.py — Catch a phrase that content-rules BANS while voice-reference
still ADVERTISES it.

WHY. `X rather than Y` sat in voice-reference.md's "Patterns to ADD" as a corpus-
validated replacement while content-rules B7 banned the same construction by name. A
drafting pass with both files loaded reached for the one framed as validated and shipped
a phrase Nick had cut 14 days earlier from an email to the same recipient. Neither file
was wrong on its own; nothing compared them.

PRECEDENCE: content-rules wins. voice-reference is *mechanics* (punctuation, length,
filler, sentence shape); content-rules is *judgment* (what to say, cut, or claim). A
phrase validated in voice-reference is void if a content rule bans it.

WHAT THIS DOES AND DOES NOT CATCH (read before trusting a green run). This is a
phrase-registry TEMPLATE INTERSECTION. It compares only phrases somebody registered, in
`banned_phrases:` / `preferred_phrases:` in the YAML and `<!-- voice-phrase: ... -->`
markers in voice-reference. It does NOT catch semantic contradictions expressed in
different words, and it cannot see a phrase nobody registered. The semantic pass is the
one-time reconciliation audit at output/analysis/081226-voice-content-rules-reconciliation.md.
**Do not read a green checker as "the docs agree."**

All three source files are GITIGNORED, so this cannot run in CI. Absent files -> SKIPPED
on stdout, exit 0. Never a silent pass.

Exit codes: 0 clean or SKIPPED | 1 conflicts found, or a malformed marker.
"""
import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_YAML = "framework/content-rules.yaml"
DEFAULT_MD = "framework/content-rules.md"
DEFAULT_VOICE = "framework/voice-reference.md"
DEFAULT_HOOK = "tools/check_draft_voice.py"
LOCAL_VALIDATORS = "tools/.local-validators.json"

# `X`/`Y` are the only placeholder tokens, and only as standalone capitals. Rendering
# them to fixed fillers (not random) keeps the subsumption test deterministic.
PLACEHOLDER_FILLERS = {"X": "alpha", "Y": "beta"}
_PLACEHOLDER_RE = re.compile(r"^[XY]$")
# A placeholder stands for a short noun phrase, not an arbitrary span; 0-4 extra words
# keeps `X rather than Y` from matching across a whole paragraph.
_PLACEHOLDER_PATTERN = r"\S+(?:\s+\S+){0,4}"

VOICE_MARKER_RE = re.compile(r"<!--\s*voice-phrase:(?P<body>.*?)-->", re.S)
_MARKER_BODY_RE = re.compile(
    r'^\s*(?P<state>ADD|RETIRED)\s+"(?P<phrase>[^"]*)"\s+scope=(?P<scope>literal|construction)'
    r'(?P<rest>\s+reason=\S+)?\s*$'
)

VALID_SCOPES = ("literal", "construction")


class MarkerError(Exception):
    """A malformed registry entry — reported as a parse error, never skipped."""


def normalize(phrase: str) -> str:
    """Casefold, collapse whitespace, strip trailing punctuation and wrapping quotes."""
    text = re.sub(r"\s+", " ", (phrase or "")).strip()
    text = text.strip("`\"'")
    text = text.rstrip(".,;:!?")
    return text.casefold().strip()


def _tokens(phrase: str) -> list[str]:
    # Split on whitespace but keep the ORIGINAL case so X/Y placeholders are detectable;
    # normalization to casefold happens per-token below.
    return [t for t in re.split(r"\s+", phrase.strip()) if t]


def compile_phrase(phrase: str, scope: str) -> re.Pattern:
    """Compile a registry entry to a matcher.

    literal      -> the escaped phrase, whitespace-flexible, \b-anchored, casefolded.
    construction -> same, except standalone X / Y become short-span wildcards.
    """
    parts = []
    for token in _tokens(phrase):
        if scope == "construction" and _PLACEHOLDER_RE.match(token):
            parts.append(_PLACEHOLDER_PATTERN)
        else:
            parts.append(re.escape(token.casefold()))
    if not parts:
        raise MarkerError(f"empty phrase (scope={scope})")
    body = r"\s+".join(parts)
    left = r"\b" if re.match(r"\w", _tokens(phrase)[0]) else ""
    right = r"\b" if re.search(r"\w$", _tokens(phrase)[-1]) else ""
    return re.compile(left + body + right, re.I)


def render_phrase(phrase: str, scope: str) -> str:
    """Render an entry to a concrete string by substituting fixed fillers for X / Y."""
    out = []
    for token in _tokens(phrase):
        if scope == "construction" and _PLACEHOLDER_RE.match(token):
            out.append(PLACEHOLDER_FILLERS[token])
        else:
            out.append(token)
    return " ".join(out).casefold()


def conflicts(a: dict, b: dict) -> str | None:
    """Bidirectional template subsumption. Returns the direction, or None.

    Set equality is not enough: B7's construction `X rather than Y` must collide with a
    voice-reference marker registering the LITERAL `judgment rather than recall`, and
    neither string equals the other.
    """
    a_pat, b_pat = compile_phrase(a["phrase"], a["scope"]), compile_phrase(b["phrase"], b["scope"])
    a_txt, b_txt = render_phrase(a["phrase"], a["scope"]), render_phrase(b["phrase"], b["scope"])
    if a_pat.search(b_txt):
        return "banned_subsumes_add"
    if b_pat.search(a_txt):
        return "add_subsumes_banned"
    return None


# ---------------------------------------------------------------- loaders


def load_yaml_registry(path: Path) -> tuple[list[dict], list[dict]]:
    """(banned, preferred) entries across every rule, each tagged with its rule id."""
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    banned, preferred = [], []
    for rule in data.get("rules", []) or []:
        rid = rule.get("id", "<no-id>")
        for field, sink in (("banned_phrases", banned), ("preferred_phrases", preferred)):
            entries = rule.get(field, []) or []
            if not isinstance(entries, list):
                raise MarkerError(f"{rid}: {field} must be a list, got {type(entries).__name__}")
            for entry in entries:
                if not isinstance(entry, dict) or "phrase" not in entry or "scope" not in entry:
                    raise MarkerError(f"{rid}: {field} entry must have `phrase` and `scope`: {entry!r}")
                if entry["scope"] not in VALID_SCOPES:
                    raise MarkerError(f"{rid}: invalid scope {entry['scope']!r} (expected one of {VALID_SCOPES})")
                sink.append({"rule_id": rid, "phrase": str(entry["phrase"]), "scope": entry["scope"]})
    return banned, preferred


def load_hook_remedies(path: Path) -> list[dict]:
    """The REMEDY strings a voice hook tells the drafter to use instead.

    WHY THIS EXISTS. The 8/11 ban on `X rather than Y` swept voice-reference.md and
    content-rules, but not `check_draft_voice.py`, whose PATTERNS list still carried
    'use "X rather than Y" (corpus-validated)' as its remedy for "not as X, but as Y".
    The hook is the highest-authority tier -- it fires on every draft -- so a stale
    remedy there re-prescribes a banned phrase on every run, and doc-vs-doc checking
    cannot see it. Found 2026-08-13, two days after the ban.

    Only element [2] of each PATTERNS tuple (the remedy) is read. Element [1] is the
    diagnostic message, which QUOTES the banned phrase by design -- scanning it would
    false-positive on every correctly-authored rule.

    Parsed with `ast` (never imported) so a hook module is not executed to lint it.
    """
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise MarkerError(f"{path} is not parseable Python: {exc}") from exc

    remedies = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "PATTERNS" for t in node.targets):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        for entry in node.value.elts:
            if not isinstance(entry, ast.Tuple) or len(entry.elts) < 3:
                continue
            remedy = entry.elts[2]
            if isinstance(remedy, ast.Constant) and isinstance(remedy.value, str):
                remedies.append({
                    "phrase": remedy.value,
                    "scope": "literal",
                    "line": remedy.lineno,
                })
    return remedies


def load_voice_markers(path: Path) -> list[dict]:
    """ADD markers from voice-reference.md. RETIRED markers are ignored by design."""
    text = path.read_text(encoding="utf-8")
    markers = []
    for match in VOICE_MARKER_RE.finditer(text):
        body = match.group("body")
        parsed = _MARKER_BODY_RE.match(body)
        if not parsed:
            line = text.count("\n", 0, match.start()) + 1
            raise MarkerError(
                f"malformed voice-phrase marker at line {line}: <!-- voice-phrase:{body}--> "
                f'(expected: ADD|RETIRED "<phrase>" scope=literal|construction [reason=<id>])'
            )
        if parsed.group("state") != "ADD":
            continue
        markers.append({
            "phrase": parsed.group("phrase"),
            "scope": parsed.group("scope"),
            "line": text.count("\n", 0, match.start()) + 1,
        })
    return markers


# ---------------------------------------------------------------- check


def run(yaml_path: Path, md_path: Path, voice_path: Path,
        hook_path: Path | None = None) -> tuple[int, dict]:
    missing = [str(p) for p in (yaml_path, md_path, voice_path) if not p.is_file()]
    if missing:
        return 0, {"status": "SKIPPED",
                   "reason": f"{', '.join(missing)} absent (gitignored); Tier 2 validation only"}

    try:
        banned, preferred = load_yaml_registry(yaml_path)
        markers = load_voice_markers(voice_path)
        # The hook is TRACKED (not gitignored), so absence is a real problem, not a skip.
        remedies = load_hook_remedies(hook_path) if hook_path and hook_path.is_file() else []
    except MarkerError as exc:
        return 1, {"status": "PARSE_ERROR", "error": str(exc), "conflicts": []}

    found = []
    for ban in banned:
        for marker in markers:
            direction = conflicts(ban, marker)
            if direction:
                found.append({
                    "phrase": ban["phrase"], "scope": ban["scope"],
                    "content_rule_id": ban["rule_id"], "voice_ref_line": marker["line"],
                    "direction": direction,
                    "kind": "voice_reference_advertises_a_banned_phrase",
                })

    # Self-contradiction: the same phrase banned and preferred inside content-rules.
    for ban in banned:
        for pref in preferred:
            if ban["rule_id"] != pref["rule_id"]:
                continue
            direction = conflicts(ban, pref)
            if direction:
                found.append({
                    "phrase": ban["phrase"], "scope": ban["scope"],
                    "content_rule_id": ban["rule_id"], "voice_ref_line": 0,
                    "direction": direction,
                    "kind": "content_rules_self_contradiction",
                })

    # A voice hook's REMEDY string that contains a banned phrase re-prescribes it on
    # every draft. Containment (search), not subsumption -- the remedy is a sentence
    # wrapping the phrase, e.g. 'use "X rather than Y" (corpus-validated)'.
    for ban in banned:
        pattern = compile_phrase(ban["phrase"], ban["scope"])
        for remedy in remedies:
            if pattern.search(remedy["phrase"]):
                found.append({
                    "phrase": ban["phrase"], "scope": ban["scope"],
                    "content_rule_id": ban["rule_id"], "voice_ref_line": 0,
                    "hook_line": remedy["line"], "hook_remedy": remedy["phrase"],
                    "direction": "hook_remedy_contains_banned_phrase",
                    "kind": "voice_hook_prescribes_a_banned_phrase",
                })

    return (1 if found else 0), {
        "status": "FAIL" if found else "PASS",
        "conflicts": found,
        "banned_count": len(banned),
        "validated_count": len(markers),
        "preferred_count": len(preferred),
        "hook_remedy_count": len(remedies),
    }


def validate_local() -> tuple[int, dict]:
    vpath = Path(LOCAL_VALIDATORS)
    if not vpath.exists():
        return 0, {"status": "SKIPPED", "reason": f"{LOCAL_VALIDATORS} absent"}
    cases = json.loads(vpath.read_text(encoding="utf-8")).get("w2", [])
    if not cases:
        return 0, {"status": "SKIPPED", "reason": f"no 'w2' cases in {LOCAL_VALIDATORS}"}

    results, failed = [], 0
    for case in cases:
        code, report = run(Path(case.get("content_rules_yaml", DEFAULT_YAML)),
                           Path(case.get("content_rules_md", DEFAULT_MD)),
                           Path(case.get("voice_reference", DEFAULT_VOICE)))
        expected = case.get("expect_conflicts", 0)
        actual = len(report.get("conflicts", []))
        ok = report.get("status") != "PARSE_ERROR" and actual == expected
        failed += not ok
        results.append({**report, "expect_conflicts": expected, "pass": ok})
    return (1 if failed else 0), {
        "status": "FAIL" if failed else "PASS",
        "cases": len(results), "failed": failed, "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check content-rules vs voice-reference precedence.")
    parser.add_argument("--content-rules-yaml", default=DEFAULT_YAML)
    parser.add_argument("--content-rules-md", default=DEFAULT_MD)
    parser.add_argument("--voice-reference", default=DEFAULT_VOICE)
    parser.add_argument("--voice-hook", default=DEFAULT_HOOK,
                        help="voice hook whose PATTERNS remedies are scanned for banned phrases")
    parser.add_argument("--validate-local", action="store_true", dest="validate_local")
    args = parser.parse_args()

    if args.validate_local:
        code, payload = validate_local()
    else:
        code, payload = run(Path(args.content_rules_yaml),
                            Path(args.content_rules_md),
                            Path(args.voice_reference),
                            Path(args.voice_hook))
    print(json.dumps(payload, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
