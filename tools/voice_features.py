#!/usr/bin/env python3
"""
voice_features.py - Compute quantitative voice features across data/voice-corpus/.

Validates the descriptive rules in framework/voice-reference.md by computing
measurable stylometric features for each email and aggregating across the
corpus. Output is markdown report + per-email JSON.

Features computed:
  - Sentence count, length distribution (mean, median, stdev, min, max)
  - Word count, character count
  - Function-word top-30 frequencies
  - Punctuation density per 100 words: comma, semicolon, hyphen, em-dash,
    space-hyphen-space, exclamation, question, asterisk
  - Pronoun ratio (I/me/my : you/your : we/us/our)
  - Hedge counts (kind of, really, particularly, specifically, just)
  - Signature phrase counts (sweet spot, intersection of, drawn to, resonates,
    small world, roll up my sleeves)
  - Opener: first 3 words after "Hi <Name>,"
  - Closer: last sentence

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/voice_features.py
  PYTHONIOENCODING=utf-8 python3 tools/voice_features.py --corpus data/voice-corpus
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path


SIGNATURE_PHRASES = [
    "sweet spot",
    "intersection of",
    "drawn to",
    "particularly drawn",
    "resonates",
    "really resonated",
    "small world",
    "roll up my sleeves",
    "what excites me",
    "what particularly",
    "translating",
    "energizing",
    "i'd love",
    "happy to",
]

# Plain hedges (count any occurrence)
HEDGES_SIMPLE = ["kinda", "really", "particularly", "specifically", "just"]

# "kind of" requires disambiguation: hedge-use ("I'm kind of nervous") modifies an
# adjective/adverb; type-use ("kind of innovation") modifies a noun. Hedge-use
# almost always follows a state/perception verb, so only count those instances.
HEDGE_KIND_OF_RE = re.compile(
    r"\b(?:am|is|are|was|were|been|being|feel|feels|felt|seem|seems|seemed|"
    r"looks?|looked|got|get|gets|getting|i'?m|it'?s|that'?s|he'?s|she'?s|"
    r"we'?re|they'?re|you'?re)\s+kind of\b",
    re.IGNORECASE,
)

PRONOUNS_FIRST = {"i", "me", "my", "mine", "myself", "i'm", "i've", "i'd", "i'll"}
PRONOUNS_SECOND = {"you", "your", "yours", "yourself", "you're", "you've", "you'd"}
PRONOUNS_FIRST_PLURAL = {"we", "us", "our", "ours", "we're", "we've", "we'd"}

# Common stopwords / function words to surface top-30 of
COMMON_FUNCTION_WORDS = [
    "the", "of", "and", "to", "a", "in", "is", "it", "you", "that",
    "was", "for", "on", "are", "with", "as", "i", "his", "they", "be",
    "at", "one", "have", "this", "from", "or", "had", "by", "but",
    "not", "what", "all", "were", "we", "when", "your", "can", "said",
    "there", "use", "an", "each", "which", "she", "do", "how", "their",
    "if", "will", "up", "other", "about", "out", "many", "then", "them",
    "these", "so", "some", "her", "would", "make", "like", "him", "into",
    "time", "has", "look", "two", "more", "go", "see", "no", "way", "could",
    "people", "than", "first", "been", "call", "who", "its", "now", "find",
    "long", "down", "day", "did", "get", "come", "made", "may", "part",
    "my", "our", "me", "us", "your", "any", "want", "would", "love",
]


def parse_email_body(content: str) -> str:
    """Strip the markdown header + metadata blockquote, return just the prose."""
    lines = content.split("\n")
    # Skip "# Subject" line + metadata blockquote (lines starting with > **)
    start = 0
    for i, line in enumerate(lines):
        # Body starts after the empty line following the metadata block
        if i > 0 and line.strip() == "" and i + 1 < len(lines) and not lines[i+1].lstrip().startswith(">"):
            if any(l.strip().startswith(">") for l in lines[:i]):
                start = i + 1
                break
    return "\n".join(lines[start:]).strip()


def tokenize_sentences(text: str) -> list[str]:
    """Split text into sentences. Naive but adequate."""
    # Replace bullet markers and newlines with periods to delimit sentences
    cleaned = re.sub(r"\n\s*-\s+", ". ", text)  # bullet starts as new sentence
    # Split on sentence-ending punctuation
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    return [s.strip() for s in sentences if len(s.strip().split()) >= 3]


def tokenize_words(text: str) -> list[str]:
    """Extract lowercase word tokens."""
    return re.findall(r"[a-zA-Z']+", text.lower())


def find_opener(body: str) -> str:
    """Extract first 3 words after 'Hi <Name>,' line."""
    # Find "Hi X," line
    m = re.search(r"^Hi\s+\S+,\s*\n+([^\n]+)", body, re.MULTILINE | re.IGNORECASE)
    if m:
        first_line = m.group(1).strip()
        words = first_line.split()
        return " ".join(words[:8]) if words else ""
    return ""


def find_closer(body: str) -> str:
    """Extract last 1-2 sentences before signoff."""
    # Find content before final "Best,/Thanks," etc.
    parts = re.split(r"\n+(Best|Thanks|Sincerely|Cheers|Regards)[,!]?\s*\n", body, maxsplit=1)
    main = parts[0].strip()
    # Get last paragraph
    paras = [p.strip() for p in main.split("\n\n") if p.strip()]
    return paras[-1][:200] if paras else ""


def find_signoff(body: str) -> str:
    """Extract closing word ('Best,' / 'Thanks,' / etc.)."""
    m = re.search(r"\n+(Best|Thanks|Thanks for [^,\n]+|Sincerely|Cheers|Regards)[,!]?\s*\n+\s*Nick", body, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def compute_features(body: str) -> dict:
    """Compute all features for a single email body."""
    sentences = tokenize_sentences(body)
    words = tokenize_words(body)
    word_count = len(words)
    sentence_lengths = [len(s.split()) for s in sentences]

    # Pronoun counts
    pronoun_first = sum(1 for w in words if w in PRONOUNS_FIRST)
    pronoun_second = sum(1 for w in words if w in PRONOUNS_SECOND)
    pronoun_we = sum(1 for w in words if w in PRONOUNS_FIRST_PLURAL)

    # Hedges — simple ones via word boundary, "kind of" via disambiguation regex
    hedge_counts = {}
    body_lower = body.lower()
    for h in HEDGES_SIMPLE:
        hedge_counts[h] = len(re.findall(r"\b" + re.escape(h) + r"\b", body_lower))
    hedge_counts["kind of (hedge-use)"] = len(HEDGE_KIND_OF_RE.findall(body))

    # Signature phrases
    signature_counts = {}
    for phrase in SIGNATURE_PHRASES:
        signature_counts[phrase] = body_lower.count(phrase)

    # Punctuation
    punct = {
        "comma": body.count(","),
        "semicolon": body.count(";"),
        "em_dash": body.count("—"),  # —
        "en_dash": body.count("–"),  # –
        "hyphen_inline": len(re.findall(r" - ", body)),  # ' - ' Nick's signature
        "exclamation": body.count("!"),
        "question": body.count("?"),
        "asterisk_emphasis": len(re.findall(r"\*[^*]+\*", body)),
        "calendly_link": int("calendly.com/nickmagnuson" in body_lower),
    }

    # Function word frequencies
    word_freq = Counter(words)
    function_word_freqs = {w: word_freq.get(w, 0) for w in COMMON_FUNCTION_WORDS}

    return {
        "word_count": word_count,
        "char_count": len(body),
        "sentence_count": len(sentences),
        "sentence_length_mean": round(statistics.mean(sentence_lengths), 2) if sentence_lengths else 0,
        "sentence_length_median": round(statistics.median(sentence_lengths), 2) if sentence_lengths else 0,
        "sentence_length_stdev": round(statistics.stdev(sentence_lengths), 2) if len(sentence_lengths) > 1 else 0,
        "sentence_length_min": min(sentence_lengths) if sentence_lengths else 0,
        "sentence_length_max": max(sentence_lengths) if sentence_lengths else 0,
        "pronoun_first_singular": pronoun_first,
        "pronoun_second": pronoun_second,
        "pronoun_first_plural": pronoun_we,
        "pronoun_ratio_per_100w": {
            "I": round(pronoun_first / word_count * 100, 2) if word_count else 0,
            "you": round(pronoun_second / word_count * 100, 2) if word_count else 0,
            "we": round(pronoun_we / word_count * 100, 2) if word_count else 0,
        },
        "hedges": hedge_counts,
        "signature_phrases": signature_counts,
        "punctuation_per_100w": {
            k: round(v / word_count * 100, 2) if word_count else 0
            for k, v in punct.items()
        },
        "punctuation_raw": punct,
        "opener": find_opener(body),
        "signoff": find_signoff(body),
        "closer_last_para": find_closer(body),
    }


def aggregate(features_list: list[dict]) -> dict:
    """Aggregate per-email features across the corpus."""
    n = len(features_list)
    if n == 0:
        return {}

    def avg(key):
        return round(statistics.mean(f[key] for f in features_list), 2)

    def total(key):
        return sum(f[key] for f in features_list)

    # Sentence stats
    all_sent_lengths = []
    for f in features_list:
        # Reconstruct from min/max/mean approximation — not perfect, but indicative
        all_sent_lengths.append(f["sentence_length_mean"])

    # Aggregate punctuation
    punct_totals = Counter()
    for f in features_list:
        for k, v in f["punctuation_raw"].items():
            punct_totals[k] += v
    total_words = total("word_count")
    punct_per_100w = {k: round(v / total_words * 100, 3) if total_words else 0
                      for k, v in punct_totals.items()}

    # Aggregate hedges
    hedge_totals = Counter()
    for f in features_list:
        for k, v in f["hedges"].items():
            hedge_totals[k] += v

    # Aggregate signature phrases
    sig_totals = Counter()
    for f in features_list:
        for k, v in f["signature_phrases"].items():
            sig_totals[k] += v

    # Aggregate pronouns
    pn_first = total("pronoun_first_singular")
    pn_second = total("pronoun_second")
    pn_we = total("pronoun_first_plural")
    pn_total = pn_first + pn_second + pn_we

    # Openers / signoffs
    openers = [f["opener"] for f in features_list if f["opener"]]
    signoffs = Counter(f["signoff"] for f in features_list if f["signoff"])

    return {
        "email_count": n,
        "total_words": total_words,
        "total_chars": total("char_count"),
        "total_sentences": total("sentence_count"),
        "avg_words_per_email": avg("word_count"),
        "avg_sentences_per_email": avg("sentence_count"),
        "avg_sentence_length": round(statistics.mean(all_sent_lengths), 2) if all_sent_lengths else 0,
        "median_sentence_length_per_email": round(statistics.median([f["sentence_length_median"] for f in features_list]), 2),
        "min_email_words": min(f["word_count"] for f in features_list),
        "max_email_words": max(f["word_count"] for f in features_list),
        "punctuation_per_100w_aggregate": punct_per_100w,
        "hedge_totals": dict(hedge_totals.most_common()),
        "hedge_per_100w": {k: round(v / total_words * 100, 3) if total_words else 0
                           for k, v in hedge_totals.items()},
        "signature_phrases_total": dict(sig_totals.most_common()),
        "pronoun_distribution_pct": {
            "I": round(pn_first / pn_total * 100, 1) if pn_total else 0,
            "you": round(pn_second / pn_total * 100, 1) if pn_total else 0,
            "we": round(pn_we / pn_total * 100, 1) if pn_total else 0,
        },
        "signoff_distribution": dict(signoffs.most_common()),
        "openers_sample": openers[:10],
        "calendly_link_count": punct_totals.get("calendly_link", 0),
    }


def render_report(features_list: list[dict], aggregate: dict) -> str:
    """Render markdown report comparing aggregates against voice-reference rules."""
    lines = []
    lines.append("# Voice Features Report\n")
    lines.append(f"**Corpus:** {aggregate['email_count']} emails, "
                 f"{aggregate['total_words']:,} words, "
                 f"{aggregate['total_sentences']:,} sentences\n")
    lines.append("## Aggregate stats\n")
    lines.append(f"- Words per email: avg **{aggregate['avg_words_per_email']}** (range {aggregate['min_email_words']}–{aggregate['max_email_words']})")
    lines.append(f"- Sentences per email: avg **{aggregate['avg_sentences_per_email']}**")
    lines.append(f"- Sentence length: mean **{aggregate['avg_sentence_length']} words** (median {aggregate['median_sentence_length_per_email']})\n")

    lines.append("## Pronoun distribution\n")
    pn = aggregate["pronoun_distribution_pct"]
    lines.append(f"- I/me/my: **{pn['I']}%**")
    lines.append(f"- you/your: **{pn['you']}%**")
    lines.append(f"- we/us/our: **{pn['we']}%**\n")

    lines.append("## Punctuation per 100 words\n")
    pp = aggregate["punctuation_per_100w_aggregate"]
    lines.append(f"| Pattern | Per 100 words |")
    lines.append(f"|---|---|")
    for k in ["comma", "semicolon", "em_dash", "en_dash", "hyphen_inline", "exclamation", "question", "asterisk_emphasis"]:
        lines.append(f"| `{k}` | {pp.get(k, 0)} |")
    lines.append("")

    lines.append("## Signoff distribution\n")
    for sig, n in aggregate["signoff_distribution"].items():
        lines.append(f"- `{sig}` × {n}")
    lines.append("")

    lines.append("## Hedge counts (raw)\n")
    for h, n in aggregate["hedge_totals"].items():
        lines.append(f"- `{h}` × {n}")
    lines.append("")

    lines.append("## Signature phrases\n")
    for p, n in aggregate["signature_phrases_total"].items():
        if n > 0:
            lines.append(f"- `{p}` × {n}")
    lines.append("")

    lines.append("## Calendly link\n")
    lines.append(f"- `calendly.com/nickmagnuson` appears in {aggregate['calendly_link_count']} of {aggregate['email_count']} emails\n")

    lines.append("## Openers (first 8 words after greeting, sample)\n")
    for op in aggregate["openers_sample"]:
        lines.append(f"- `{op}`")
    lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="data/voice-corpus", help="Corpus directory")
    ap.add_argument("--output-json", default="data/voice-features.json", help="Per-email + aggregate JSON")
    ap.add_argument("--output-md", default="data/voice-features-report.md", help="Markdown report")
    args = ap.parse_args()

    corpus = Path(args.corpus)
    if not corpus.exists():
        print(f"ERROR: corpus not found: {corpus}", file=sys.stderr)
        sys.exit(1)

    files = sorted(corpus.glob("*.md"))
    print(f"Processing {len(files)} files from {corpus}", file=sys.stderr)

    per_email = []
    for f in files:
        content = f.read_text(encoding="utf-8")
        body = parse_email_body(content)
        features = compute_features(body)
        features["filename"] = f.name
        per_email.append(features)

    agg = aggregate(per_email)

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    output_json_path.parent.mkdir(parents=True, exist_ok=True)

    output_json_path.write_text(
        json.dumps({"aggregate": agg, "per_email": per_email}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    output_md_path.write_text(render_report(per_email, agg), encoding="utf-8")

    print(f"Wrote {output_json_path}", file=sys.stderr)
    print(f"Wrote {output_md_path}", file=sys.stderr)
    # Also print report to stdout for quick inspection
    print(render_report(per_email, agg))


if __name__ == "__main__":
    main()
