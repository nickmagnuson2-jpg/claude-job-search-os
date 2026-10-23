#!/usr/bin/env python3
"""
check_derived_ratio.py -- Stop hook for Claude Code.

Catches a ratio Claude DERIVED in chat: arithmetic done in the reply, whose result no
tool ever printed. "12 of 40 (30%)" written from two numbers read off a tool output is
mental arithmetic presented as a measurement. It is often right, and when it is wrong
it reads exactly like a measured number, so it propagates into files a later session
trusts (CLAUDE.md Hard Rule: "Name the scope in the same sentence as the conclusion").
The cure is cheap: compute it in a script and quote what the script printed.

No PreToolUse or PostToolUse hook can see a number typed into chat. The Stop hook
reads the finished assistant message, so it is the only surface that can.

WHAT IT CATCHES (narrow by design)
  The derived value C in these shapes, after stripping fenced code, inline code,
  blockquote lines, double-quoted spans and URLs:
    A / B = C%          A/B = C
    N of M (C%)         N/M (C%)          N of M, or C%
    C% (N of M)         C% (N/M)
    that's C% of M      only when another number (N) is in the same sentence
  Up to three words may sit between M and the parenthesis ("7 of 22 turns (31.8%)").

  It blocks unless ONE tool_result or user-message text earlier in the transcript
  prints C together with both N and M. Pooling every number in the session was tried
  first and cleared almost everything by coincidence (a bare "10%" in an unrelated
  quote cleared "12 / 122 = 10%"); see the round-3 replay in
  output/learning-loop/102326-derived-ratio-replay.md. For "that's C% of M", N is any
  other number in the sentence. Printing C allows formatting variants: 10 = 10.0 = 10.00,
  with or without "%", the decimal 0.10 for 10% (and 10% for 0.10), and a printed
  value that rounds to C at C's own precision (tool printed 31.82, chat says 31.8%).
  A printed value with LESS precision than C does not count: 31.8 does not vouch for
  31.82.

WHAT IT DOES NOT CATCH (deliberately out of scope)
  Bare percentages, targets ("from 12% to 30%"), hypotheticals ("if 3 of 10 ..."),
  and numbers quoted from sources. A 30-day replay of a broad provenance check showed
  it would block about 1.7 replies per session, mostly wrongly, so this hook takes
  only the shapes where arithmetic visibly happened in the reply.

  Evidence is tool_result and user text only. The assistant's own earlier text is NOT
  evidence: a number Claude derived three turns ago is still a derived number.
  isSidechain and isCompactSummary records are skipped. Line-number prefixes from
  Read output ("    32<TAB>") are not printed values and are stripped.

  It does not check the arithmetic. A correct derived ratio and a wrong one are
  treated the same, because the fix (run it in a script) is the same.

EXIT CODES
  0 -- no derived ratio, or every derived value was printed by a tool or the user.
  2 -- at least one derived value with no printed source. Per tools/HOOK_AUTHORING.md
       an exit-0 warning is invisible in Claude Code, so a real warning is exit 2.
       Single correction: compute it in a script and quote the output, or drop it.

Override for one turn: DERIVED_RATIO_OVERRIDE=1
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import last_assistant_text, read_payload  # noqa: E402

OVERRIDE_ENV = "DERIVED_RATIO_OVERRIDE"

# Every number in a shape may carry thousands separators ("1,234 of 5,000",
# "123,456/7 = 17,636.57"). Before 2026-10-23 the slash-equals shape took plain digits
# only, so "123,456/7 = 17,636.57" was read as "456/7 = 17" and blocked a value a tool
# had printed (round-3 replay, session b9c23bc3). The left boundary also refuses a comma,
# so no match can start in the middle of a separated number.
INT = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
NUM = rf"{INT}(?:\.\d+)?"
LEFT = r"(?<![\w.,/])"
APPROX = r"(?:~|≈|about\s+|roughly\s+)?"
# Up to three words between M and what follows: "7 of 22 turns (31.8%)".
NOUNS = r"(?:\s+[A-Za-z][\w'-]*){0,3}"

SHAPES = [
    # A / B = C%   or   A/B = C
    ("slash-equals", re.compile(
        rf"{LEFT}(?P<n>{NUM})\s*/\s*(?P<m>{NUM})\s*(?:=|≈|~=)\s*{APPROX}(?P<c>{NUM})(?P<pct>\s*%)?")),
    # N of M (C%)   N/M (C%)
    ("of-paren", re.compile(
        rf"{LEFT}(?P<n>{INT})\s*(?:\bof\b|\bout of\b|/)\s*(?P<m>{INT}){NOUNS}\s*"
        rf"\(\s*{APPROX}(?P<c>{NUM})(?P<pct>\s*%)")),
    # N of M, or C%
    ("of-or", re.compile(
        rf"{LEFT}(?P<n>{INT})\s+(?:of|out of)\s+(?P<m>{INT}){NOUNS},?\s+or\s+{APPROX}"
        rf"(?P<c>{NUM})(?P<pct>\s*%)")),
    # C% (N of M)   C% (N/M)
    ("pct-paren", re.compile(
        rf"{LEFT}(?P<c>{NUM})(?P<pct>\s*%)\s*\(\s*(?P<n>{INT})\s*(?:of|out of|/)\s*(?P<m>{INT})")),
]

# that's C% of M -- needs N elsewhere in the same sentence.
THATS = re.compile(
    rf"\bthat(?:'s|’s|\s+is|\s+was)\s+{APPROX}(?P<c>{NUM})(?P<pct>\s*%)\s+of\s+(?:the\s+)?"
    rf"(?P<m>{INT})")

# A sentence that OPENS as a hypothetical: the ratio is an illustration, not a result.
# Only the sentence start counts (after list bullets, emphasis and numbering). Matching
# the words anywhere vetoed ordinary results such as "The results say 7 of 22 (31.8%)
# failed." and "I checked if errors clustered: 7 of 22 (31.8%) did." (Codex F2,
# 2026-10-23).
HYPOTHETICAL = re.compile(
    r"^[\s\-*_#()\[\]\d.]*"
    r"(?:(?:if|suppose|supposing|imagine|hypothetically|say)\b|e\.g\.|for (?:example|instance)\b)",
    re.I)

FENCE_RE = re.compile(r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
BLOCKQUOTE_RE = re.compile(r"^[ \t]*>.*$", re.M)
QUOTED_RE = re.compile(r"\"[^\"\n]{1,400}\"|“[^”\n]{1,400}”")
URL_RE = re.compile(r"\b(?:https?|ftp)://\S+|\bwww\.\S+", re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z*_(\[])|\n")

# Evidence numbers: any number, thousands separators allowed, not glued to a word on
# the left (so "test_33" and "v2" do not count, but "33%" and "(31.8)" do).
EVIDENCE_NUM = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
# The same, keeping a trailing percent sign, so evidence remembers its unit.
EVIDENCE_TOKEN = re.compile(r"(?<![\w.])((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(\s*%)?")
# Read-tool line-number prefix: "     32\t" or "32→".
LINE_NO_RE = re.compile(r"^[ \t]*\d+(?:\t|→)", re.M)


def strip_noise(text: str) -> str:
    """Remove the parts of a reply that are not Claude's own arithmetic."""
    text = FENCE_RE.sub(" ", text)
    text = INLINE_CODE_RE.sub(" ", text)
    text = BLOCKQUOTE_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    return QUOTED_RE.sub(" ", text)


def _sentences(text: str):
    """Yield (start, sentence) pairs."""
    pos = 0
    for m in SENTENCE_SPLIT.finditer(text):
        yield pos, text[pos:m.start()]
        pos = m.end()
    yield pos, text[pos:]


def find_derived(text: str) -> list[dict]:
    """Every derived ratio in the reply.

    Each hit: {"value", "pct", "shape", "match", "sentence", "operands", "alternates"}.
    `operands` are N and M, which a vouching text must also print. For the "that's C%
    of M" shape only M is marked, so `alternates` lists the sentence's other numbers and
    the vouching text must print at least one of them.
    """
    text = strip_noise(text)
    hits = []
    seen = set()
    for sent_start, sent in _sentences(text):
        if HYPOTHETICAL.match(sent):
            continue
        found = []
        for shape, rx in SHAPES:
            for m in rx.finditer(sent):
                found.append((shape, m))
        thats_others = {}
        for m in THATS.finditer(sent):
            others = [n.group(0) for n in EVIDENCE_NUM.finditer(sent)
                      if not (m.start() <= n.start() < m.end())]
            if others:
                found.append(("thats-of", m))
                thats_others[m.start()] = others
        # One hit per derived value: "3 of 9 (33.3%) (3/9)" matches two shapes at the
        # same C, and must report it once.
        for shape, m in found:
            key = (sent_start, m.start("c"))
            if key in seen:
                continue
            seen.add(key)
            if shape == "thats-of":
                # N is not marked in this shape: any other number in the sentence.
                operands, alternates = [m.group("m")], thats_others[m.start()]
            else:
                operands, alternates = [m.group("n"), m.group("m")], []
            hits.append({
                "value": m.group("c"),
                "pct": bool(m.group("pct")),
                "shape": shape,
                "match": m.group(0).strip(),
                "sentence": sent.strip(),
                "operands": operands,
                "alternates": alternates,
            })
    return hits


def evidence_texts(rec: dict) -> list[str]:
    """Tool-result and user text carried by one transcript record.

    Only `type == "user"` records count; that type carries both the user's own
    messages and tool results. Sidechain and compact-summary records are skipped.
    """
    if not isinstance(rec, dict) or rec.get("type") != "user":
        return []
    if rec.get("isSidechain") or rec.get("isCompactSummary"):
        return []
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, str):
        return [content]
    out = []
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                out.append(b.get("text") or "")
            elif b.get("type") == "tool_result":
                inner = b.get("content")
                if isinstance(inner, str):
                    out.append(inner)
                elif isinstance(inner, list):
                    out.extend(x.get("text") or "" for x in inner
                               if isinstance(x, dict) and x.get("type") == "text")
    return out


def numbers_in(text: str) -> set[str]:
    """The printed numbers in one evidence text, line-number prefixes removed.

    A number printed with a percent sign keeps it ("31.8%"), because the scale
    variants in `was_printed` depend on the unit (Codex F1, 2026-10-23).
    """
    return {num + ("%" if pct else "")
            for num, pct in EVIDENCE_TOKEN.findall(LINE_NO_RE.sub(" ", text))}


def _num(s: str) -> float:
    return float(s.replace(",", "").rstrip("%"))


def evidence_sets(transcript_path: str) -> list[set[str]]:
    """One set of printed numbers per tool result or user message in the transcript.

    Kept per text, not pooled: a value counts as printed only when ONE text prints it
    together with N and M (see `vouches`).
    """
    sets: list[set[str]] = []
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for t in evidence_texts(rec):
                    nums = numbers_in(t)
                    if nums:
                        sets.append(nums)
    except OSError:
        return []
    return sets


def _decimals(s: str) -> int:
    return len(s.split(".", 1)[1]) if "." in s else 0


def was_printed(value: str, numbers) -> bool:
    """True if some printed number equals `value` up to formatting and rounding.

    A printed number e vouches for C when e rounds to C at C's own number of decimals.
    A percent sign on the printed token does not matter for a direct match. Two scale
    variants cover 0.10 vs 10%, and each needs the printed token's form to support it:
      - e * 100, only from a fraction-form token: a decimal point, value <= 1, no
        percent sign (tool printed 0.318, chat says 31.8%);
      - e / 100, only from a percent-marked token, and only when C <= 1 (tool printed
        31.8%, chat says 0.318).
    Without the magnitude limits a count of 3892 "printed" 39% (round-1 replay). Without
    the form limits an unrelated count of 30 "printed" 0.3 (Codex F1). Both 2026-10-23.
    """
    c = _num(value)
    tol = 0.5 * 10 ** -_decimals(value) + 1e-9
    for s in numbers:
        e = _num(s)
        pct = s.endswith("%")
        if abs(e - c) <= tol:
            return True
        if not pct and "." in s and e <= 1 and abs(e * 100 - c) <= tol:
            return True
        if pct and c <= 1 and abs(e / 100 - c) <= tol:
            return True
    return False


def vouches(hit: dict, nums) -> bool:
    """True if ONE printed text (its number set `nums`) prints C together with N and M.

    Pooling every number in the session let coincidences through: in a long session a
    bare "10%" from an unrelated quote cleared "12 / 122 = 10%", and whole-number
    percentages almost always appeared somewhere. Requiring the operands in the same
    text took the full-history replay from 9 to 20 of 26 values worked out in chat
    (2026-10-23, output/learning-loop/102326-derived-ratio-replay.md, round 3).
    """
    printed = {_num(s) for s in nums}
    if not all(_num(o) in printed for o in hit["operands"]):
        return False
    if hit["alternates"] and not any(_num(o) in printed for o in hit["alternates"]):
        return False
    return was_printed(hit["value"], nums)


def unvouched(hits: list[dict], evidence) -> list[dict]:
    """The hits that no single printed text vouches for (`vouches`)."""
    return [h for h in hits if not any(vouches(h, nums) for nums in evidence)]


def unsourced(text: str, evidence) -> list[dict]:
    """The derived ratios in `text` that no single tool result or user message printed.

    `evidence` is a list of number sets, one per printed text (`evidence_sets`).
    """
    return unvouched(find_derived(text), evidence)


def main() -> int:
    if os.environ.get(OVERRIDE_ENV) == "1":
        return 0

    p = read_payload()
    if not p.ok:
        return 0  # fail open: an unreadable payload is not evidence of a derived ratio
    # Recursion guard: never re-enter our own stop chain. Read through the property,
    # which raises on an unread payload, so the `.ok` check above is observable.
    if p.stop_hook_active:
        return 0

    transcript = p.data.get("transcript_path") or ""
    text = last_assistant_text(transcript)
    hits = find_derived(text) if text else []
    if not hits:
        return 0

    bad = unvouched(hits, evidence_sets(transcript))
    if not bad:
        return 0

    listing = "\n".join(
        f"  - {h['value']}{'%' if h['pct'] else ''}  in: {h['match']}" for h in bad)
    sys.stderr.write(
        "BLOCKED check_derived_ratio: your reply states a ratio you computed in chat. "
        "No single tool output or user message in this session printed each value "
        "together with both of its counts:\n"
        f"{listing}\n\n"
        "Compute the number in a script and quote the printed result, or drop the "
        "derived number and give the raw counts only. The shared counting helper is "
        "tools/count_report.py. State the denominator and how it was obtained.\n"
        "If this is a false positive (the value came from a source, or it is not a "
        "ratio you derived), log it:\n"
        "  PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append "
        'check_derived_ratio.py "FP: <what got wrongly blocked>"\n'
        f"Override for this turn: {OVERRIDE_ENV}=1\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
