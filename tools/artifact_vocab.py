#!/usr/bin/env python3
"""
artifact_vocab.py — Single source of truth for the artifacts Nick sends people.

Why this exists (the same reason stage_vocab.py exists). `data/outreach-log.md` has no
artifact column: the only signal that a CV rode along with a message is free text in the
Summary cell ("... , CV attached", "resume attached (080426-placeholder.pdf)", "sent
tailored resume (071526-placeholder.pdf)"). Two consumers need to answer "does this
sentence name artifact X?" — outreach_status.py (scoping `delivered` to an artifact) and
check_prep_doc.py (extracting the artifact out of a suppressive sentence). If they
answered it with two private regexes they would drift, and a drift here is not cosmetic:
it re-opens the 2026-08-10 defect, where a prep doc asserted "CV already sent 8/4, do not
re-offer it" about a CV that had never been delivered.

Stdlib only (re, unicodedata) — safe to import anywhere in tools/.
"""
import re
import unicodedata

# Canonical tokens, in canonical order. find_in_text() returns hits in this order so its
# output is deterministic regardless of where the words appear in the sentence.
CANONICAL = ("cv", "cover-letter", "deck", "portfolio", "writeup", "link")

# Surface forms -> canonical token. Matched case-insensitively and \b-anchored as
# PHRASES (so "cover letter" matches without the hyphen). Keep every alias here rather
# than in a consumer; a consumer that adds a private alias is the drift this module bans.
ALIASES = {
    "cv": "cv",
    "resume": "cv",
    "résumé": "cv",
    "cover letter": "cover-letter",
    "cover-letter": "cover-letter",
    "deck": "deck",
    "presentation": "deck",
    "slides": "deck",
    "portfolio": "portfolio",
    "work samples": "portfolio",
    "write-up": "writeup",
    "writeup": "writeup",
    "memo": "writeup",
    "one-pager": "writeup",
    "link": "link",
    "github": "link",
    "demo": "link",
}


def _fold(text: str) -> str:
    """Casefold and strip accents so 'Résumé' and 'resume' are the same surface form."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


# Longest-first so "cover letter" wins over a bare "letter"-shaped prefix and
# "work samples" is never shadowed. Hyphens are matched literally; \b around a phrase
# whose edge char is non-word (e.g. a trailing hyphen) would never fire, so the anchors
# are applied conditionally.
def _compile(alias: str) -> re.Pattern:
    folded = _fold(alias)
    left = r"\b" if folded[:1].isalnum() else ""
    right = r"\b" if folded[-1:].isalnum() else ""
    # Split on the separators FIRST, then escape each part, then rejoin with a flexible
    # separator. Escaping the whole phrase and substituting afterwards corrupts the
    # pattern -- re.escape() escapes the space itself, so the backslash it leaves behind
    # turns the replacement's "[" into a literal bracket and the phrase never matches.
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", folded) if p]
    body = r"[\s\-]+".join(parts)
    return re.compile(left + body + right)


_ALIAS_PATTERNS = [
    (_compile(alias), canon)
    for alias, canon in sorted(ALIASES.items(), key=lambda kv: -len(kv[0]))
]


def valid_tokens() -> list[str]:
    """The canonical artifact tokens, in canonical order."""
    return list(CANONICAL)


def canonicalize(token: str) -> str | None:
    """Map a single token/alias to its canonical form, or None if unknown."""
    folded = _fold(token).strip()
    if folded in CANONICAL:
        return folded
    return ALIASES.get(folded) or ALIASES.get(re.sub(r"[\s\-]+", " ", folded))


def find_in_text(text: str) -> list[str]:
    """Canonical artifact tokens named anywhere in `text`, deduped, in canonical order.

    This is the function that reads an outreach-log Summary cell or a suppressive
    sentence. It is deliberately phrase-based and not word-based: "cover letter" is two
    words and one artifact.
    """
    folded = _fold(text)
    found = {canon for pattern, canon in _ALIAS_PATTERNS if pattern.search(folded)}
    return [tok for tok in CANONICAL if tok in found]


# ------------------------------------------------------- transmission sense (v1)
#
# find_in_text() answers "does this sentence NAME artifact X?". That is the right
# question for check_prep_doc.py (which artifact is a suppressive sentence about?) and
# the WRONG question for outreach_status.py's `delivered`, because a Summary cell names
# an artifact in plenty of non-transmission senses. Verified against the live log on
# 2026-08-12, naming-but-not-sending accounts for 8 of 16 artifact-bearing rows:
#
# The four shapes, INVENTED here to stand in for the private cells rather than quoted
# (the live log is gitignored and its rows name real people and companies):
#
#   "... proposed times, said a CV could follow"  -> offered, never sent
#   "... deck to follow next week"                -> promised, not yet sent
#   "... no CV this time, happy to send one"      -> explicitly withheld
#   "Re: the resume thread"                       -> a thread NAME, not a send
#
# Treating those as receipt evidence reproduces the exact 2026-08-10 defect from the
# other direction: the merely-offered row carries `Replied`, so a naive artifact scope
# returns delivered:true for a CV that was never sent, and the suppressive sentence is
# re-authorized. This function is the gate.
#
# NEAREST-MARKER-WINS. Within a 70-char window of the artifact mention, find the closest
# transmission marker and the closest non-transmission marker; the artifact counts as
# transmitted only if a positive marker exists and is strictly closer than any negative.
# Proximity (not mere presence) is what separates "request for a current resume,
# attached 071526-placeholder.pdf" (sent) from "a CV could follow" (not sent).
#
# The failure mode is deliberately asymmetric: a missed send yields `delivered: unknown`,
# which renders as "offer it again if asked" -- harmless. A false send yields
# `delivered: true`, which unlocks suppression. When in doubt this returns nothing.
#
# Both lists are versioned; extend them only together with a test case.
TRANSMISSION_MARKERS_VERSION = 1

_POSITIVE_MARKERS = re.compile(
    r"\b(attach(?:ed|ing|es)?|enclos(?:ed|ing)|sent|sending|shared|included)\b"
    r"|\b\d{6}-[a-z]+\.pdf\b"
)
_NEGATIVE_MARKERS = re.compile(
    r"\bnot\s+attached\b"
    r"|\b(offer(?:ed|ing|s)?|promis(?:ed|es|ing)|will\s+send"
    r"|ask(?:ed|ing|s)?\s+for|request(?:ed|ing|s)?(?:\s+for)?)\b"
    r"|send-"
)

_WINDOW = 70


def _nearest(folded: str, start: int, end: int, pattern: re.Pattern) -> int | None:
    """Distance from the [start,end) span to the closest match, or None beyond _WINDOW."""
    best = None
    for m in pattern.finditer(folded):
        if m.start() < end and m.end() > start:
            distance = 0
        else:
            distance = min(abs(m.start() - end), abs(start - m.end()))
        if distance <= _WINDOW and (best is None or distance < best):
            best = distance
    return best


def find_transmitted_in_text(text: str) -> list[str]:
    """Canonical artifacts this text says were actually SENT, in canonical order.

    Conservative by design -- see the note above. Use this for receipt reasoning;
    use find_in_text() when you only need to know which artifact is being discussed.
    """
    folded = _fold(text)
    found = set()
    for pattern, canon in _ALIAS_PATTERNS:
        if canon in found:
            continue
        for match in pattern.finditer(folded):
            start, end = match.span()
            positive = _nearest(folded, start, end, _POSITIVE_MARKERS)
            negative = _nearest(folded, start, end, _NEGATIVE_MARKERS)
            if positive is not None and (negative is None or positive < negative):
                found.add(canon)
                break
    return [tok for tok in CANONICAL if tok in found]


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Artifact-token vocabulary.")
    parser.add_argument("--canonicalize", metavar="TOKEN")
    parser.add_argument("--find-in-text", metavar="TEXT")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        print(json.dumps({"valid_tokens": valid_tokens()}))
        return 0
    if args.find_in_text is not None:
        print(json.dumps({"text": args.find_in_text, "artifacts": find_in_text(args.find_in_text)}))
        return 0
    if args.canonicalize is not None:
        canon = canonicalize(args.canonicalize)
        if canon is None:
            print(json.dumps({
                "error": f"unknown artifact token: {args.canonicalize}",
                "valid_tokens": valid_tokens(),
            }))
            return 4
        print(json.dumps({"input": args.canonicalize, "canonical": canon}))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
