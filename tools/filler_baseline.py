#!/usr/bin/env python3
"""Compute the FULL ranked filler-density baseline over the Granola voice corpus.

WHY THIS EXISTS
---------------
2026-08-24: a debrief reported "lowest filler density in the corpus, on the highest
Nick-word count in the corpus." Measured over the actual corpus it was 8th of 37 real
calls. The splitter had been validated -- correctly, against three already-logged files --
and that validation was then treated as licence for a corpus-wide superlative assembled
from a five-row hand-picked comparison table. 13th fire of
`feedback_name_the_scope_before_stating_the_conclusion`.

The rule was never the problem. Computing the denominator was manual, so a hand table was
the path of least resistance. This tool removes that excuse: the ranked set is one command.

TWO REFUSALS, both deliberate
-----------------------------
1. **Silent dropouts are reported, never dropped.** A transcript that parses to zero `Me:`
   turns is EXCLUDED WITH A REASON and counted in the output. In the original incident 25
   label-corrupted files parsed to zero turns and vanished from every comparison with no
   error, hiding seven real calls that beat the claimed best. An exclusion you cannot see
   is a denominator defect wearing a data-quality costume.
2. **Every answer carries its denominator and its scope.** `--rank` never prints a bare
   position; it prints "N of M (scope)". There is no output mode that yields a superlative
   without the set it was computed over.

Usage
-----
  filler_baseline.py                         # full ranked table, real calls
  filler_baseline.py --scope all             # include drills and non-calls
  filler_baseline.py --rank <substring>      # rank one file, with denominator
  filler_baseline.py --min-words 2000        # restrict to substantial calls
  filler_baseline.py --json
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meeting_vocab import split_transcript_turns  # noqa: E402  single source of truth

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "data" / "voice-corpus" / "granola"

# Core filler tokens. Matches the hand counts in coaching/anti-pattern-tracker.md so the
# script and the ledger stay comparable; changing this set invalidates every logged number.
CORE = {
    "kind of": r"\bkind of\b",
    "really": r"\breally\b",
    "absolutely": r"\babsolutely\b",
    "definitely": r"\bdefinitely\b",
    "pretty": r"\bpretty\b",
}
EXTRA = {
    "filler-like": r"\b, like,|\blike,\s",
    "just": r"\bjust\b",
}

DRILL_RE = re.compile(
    r"sim|probs|drill|rep-|-rep|concept-probe|scaling-judgment|"
    r"authorization-and-access|claim-retrieval|handoff"
)
NONCALL_RE = re.compile(
    r"endowment|west-point-inn|how-to-build-a-business|article|insights-on|podcast"
)
CORRUPT_RE = re.compile(r"\{'source':[^}]*\}:")

# Files whose per-speaker attribution cannot be trusted, with the reason. These are RANKED
# but flagged, never silently dropped -- a reader deciding whether to cite one needs to see
# both the number and why it is soft.
# A call where the counterpart holds less than this share of the words did not have its
# channels separated: both voices landed on the owner channel, so the density measures a
# conversation rather than a speaker. Computed, not hand-listed -- a hand-maintained list of
# bad files is the same pattern that let the corrupted transcripts hide (2026-08-25: the
# first hand-written entry here already missed a second collapsed call that was ranked 4th).
MIN_COUNTERPART_SHARE_PCT = 10.0

# Reserved for calls the share heuristic CANNOT catch: both speakers are labelled and the
# split looks healthy, but the labels are wrong. An in-person capture is the known case --
# the room arrives on the owner channel, so the counterpart's share looks normal while the
# attribution is scrambled. Structural checks cannot see this; it needs a human who was there.
UNRELIABLE = {
    "2026-08-05-1712-nick-magnuson-sierra.md":
        "in-person onsite; the laptop mic captured the room, so Me/Them attribution is "
        "unreliable and the density should not be cited",
}


def _collapsed_channel_reason(owner_words: int, other_words: int) -> str | None:
    """Flag a transcript whose two speakers were never separated.

    Detected rather than hand-listed. Granola only produces Me/Them when system audio and
    the microphone are captured on separate channels; on a speakerphone call (or when system
    audio is not captured) every voice arrives on the mic and the whole conversation is
    attributed to the owner. The transcript is still perfectly good CONTENT -- it is only the
    per-speaker COUNTING that is meaningless, and those are separate properties.

    Verified at the source 2026-08-25: re-fetching such a meeting returns one undifferentiated
    block, so nothing was lost locally and nothing can be recovered.
    """
    total = owner_words + other_words
    if not total:
        return None
    share = other_words / total * 100
    if share >= MIN_COUNTERPART_SHARE_PCT:
        return None
    return (f"speaker channels not separated -- the counterpart holds only {share:.1f}% of the "
            f"words ({other_words} vs {owner_words}), so this density measures a conversation, "
            "not a speaker. Not recoverable: the diarization does not exist upstream either. "
            "Content is still usable; the number is not")


def parse_file(p: Path) -> dict:
    raw = p.read_text(encoding="utf-8")
    corrupt = len(CORRUPT_RE.findall(raw))
    # Delegated to meeting_vocab so the three on-disk label formats are decoded in ONE
    # place. Hardcoding `Me:` here is what made 5 Microphone:/Speaker: transcripts score
    # zero and vanish from the baseline (2026-08-24).
    nick, them = split_transcript_turns(raw)
    if not nick and not them:
        return {
            "file": p.name, "excluded": True,
            "reason": ("corrupted speaker labels -- parses to zero turns "
                       f"({corrupt} dict-shaped labels found)")
            if corrupt else "no recognised speaker labels (solo note, or a new label format)",
        }
    nw = sum(len(t.split()) for t in nick)
    tw = sum(len(t.split()) for t in them)
    text = " ".join(nick).lower()
    counts = {k: len(re.findall(v, text)) for k, v in CORE.items()}
    extra = {k: len(re.findall(v, text)) for k, v in EXTRA.items()}
    core = sum(counts.values())
    kind = ("drill" if DRILL_RE.search(p.name)
            else "non-call" if NONCALL_RE.search(p.name) else "real")
    return {
        "file": p.name, "excluded": False, "kind": kind,
        "nick_words": nw, "them_words": tw,
        "airtime_pct": round(nw / (nw + tw) * 100, 1) if nw + tw else 0.0,
        "core": core,
        "density_pct": round(core / nw * 100, 2) if nw else 0.0,
        "per_1k": round(core / nw * 1000, 1) if nw else 0.0,
        "counts": counts, "extra": extra,
        "unreliable": UNRELIABLE.get(p.name) or _collapsed_channel_reason(nw, tw),
    }


def collect(min_words: int, scope: str, corpus: Path | None = None) -> dict:
    # `corpus` is a test seam, mirroring the PII_REPO_ROOT seam in check_public_pii.py: it
    # lets the suite exercise real CLI behaviour against a fixture instead of hardcoding a
    # real transcript filename into a PUBLIC test file.
    corpus = corpus or CORPUS
    if not corpus.is_dir():
        return {"status": "error", "message": f"corpus not found: {corpus}"}
    # Non-recursive on purpose: _duplicates/ holds quarantined second captures of meetings
    # that already have a canonical transcript here. Including them double-counts, which is
    # the harm data/voice-corpus/granola/_duplicates/README.md exists to prevent.
    files = [p for p in sorted(corpus.glob("*.md")) if not p.name.endswith("-summary.md")]
    parsed = [parse_file(p) for p in files]
    excluded = [r for r in parsed if r["excluded"]]
    ok = [r for r in parsed if not r["excluded"]]
    too_short = [r for r in ok if r["nick_words"] < min_words]
    ok = [r for r in ok if r["nick_words"] >= min_words]
    if scope == "real":
        out_of_scope = [r for r in ok if r["kind"] != "real"]
        ok = [r for r in ok if r["kind"] == "real"]
    else:
        out_of_scope = []
    ok.sort(key=lambda r: r["density_pct"])
    for i, r in enumerate(ok, 1):
        r["rank"] = i
    # Count the transcripts, not the directory listing: subtracting 1 for a README
    # silently under-reports by one whenever the README is absent.
    quarantined = len([q for q in (corpus / "_duplicates").glob("*.md")
                       if q.name.lower() != "readme.md"])
    return {
        "status": "ok",
        "scope": scope,
        "min_words": min_words,
        "denominator": len(ok),
        "ranked": ok,
        "excluded_corrupt_or_unparseable": excluded,
        "excluded_below_min_words": len(too_short),
        "excluded_out_of_scope": len(out_of_scope),
        "quarantined_duplicates_not_scanned": max(quarantined, 0),
        "median_density_pct": (round(ok[len(ok) // 2]["density_pct"], 2) if ok else None),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", choices=["real", "all"], default="real")
    ap.add_argument("--min-words", type=int, default=300,
                    help="exclude transcripts below this Nick-word count; density is noise "
                         "on short samples (default 300)")
    ap.add_argument("--rank", metavar="SUBSTRING",
                    help="report one file's rank WITH its denominator and scope")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=0, help="print only the top N rows")
    ap.add_argument("--corpus", default=None,
                    help="override the corpus directory (test seam)")
    args = ap.parse_args()

    d = collect(args.min_words, args.scope,
                Path(args.corpus) if args.corpus else None)
    if d["status"] != "ok":
        print(json.dumps(d)); return 1

    if args.rank:
        hits = [r for r in d["ranked"] if args.rank in r["file"]]
        if not hits:
            print(json.dumps({"status": "error", "code": "not_ranked",
                              "message": f"no ranked file matches {args.rank!r}. It may be "
                                         "excluded -- check excluded_corrupt_or_unparseable, "
                                         "or below --min-words, or out of --scope.",
                              "denominator": d["denominator"]}, indent=1))
            return 1
        if len(hits) > 1:
            print(json.dumps({"status": "error", "code": "ambiguous",
                              "matches": [r["file"] for r in hits]}, indent=1))
            return 1
        r = hits[0]
        claim = (f"{r['density_pct']}% filler, rank {r['rank']} of {d['denominator']} "
                 f"({args.scope} calls, >={args.min_words} Nick-words)")
        out = {"status": "ok", "file": r["file"], "rank": r["rank"],
               "denominator": d["denominator"], "scope": args.scope,
               "min_words": args.min_words, "density_pct": r["density_pct"],
               "nick_words": r["nick_words"], "core": r["core"], "counts": r["counts"],
               "median_density_pct": d["median_density_pct"],
               "citable_claim": claim,
               "unreliable": r["unreliable"],
               "excluded_corrupt": len(d["excluded_corrupt_or_unparseable"])}
        print(json.dumps(out, indent=1))
        return 0

    if args.json:
        print(json.dumps(d, indent=1)); return 0

    rows = d["ranked"][:args.top] if args.top else d["ranked"]
    print(f"scope={d['scope']}  min_words={d['min_words']}  DENOMINATOR={d['denominator']}"
          f"  median={d['median_density_pct']}%")
    print(f"{'rk':>3s} {'dens':>7s} {'words':>6s} {'core':>5s}  file")
    for r in rows:
        flag = "  [UNRELIABLE ATTRIBUTION]" if r["unreliable"] else ""
        print(f"{r['rank']:3d} {r['density_pct']:6.2f}% {r['nick_words']:6d} "
              f"{r['core']:5d}  {r['file'][:56]}{flag}")

    exc = d["excluded_corrupt_or_unparseable"]
    print(f"\nEXCLUDED, reported not dropped: {len(exc)} unparseable, "
          f"{d['excluded_below_min_words']} below min-words, "
          f"{d['excluded_out_of_scope']} out of scope, "
          f"{d['quarantined_duplicates_not_scanned']} quarantined duplicates.")
    for r in exc:
        print(f"    {r['file'][:60]}: {r['reason']}")
    if exc:
        print("\n  A file that parses to zero Me: turns is a DENOMINATOR defect, not a data\n"
              "  nit. Repair it before citing any ranking that excluded it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
