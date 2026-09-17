#!/usr/bin/env python3
"""stream_validate.py - deterministic evidence checks for /analyze-stream.

  form <form.json> --chunk <chunk.txt>
      Every quote in an ExtractionForm must be an exact contiguous match (after
      normalize) in the chunk text, the match must start inside the chunk's core
      window, and the stated ts must be within TS_TOLERANCE of the match start.
      Writes `verified` and `matched_start` back into each item. Exit 0 if all pass,
      3 if any fail.

  doc <doc.md> --transcript <file.vtt> [--root DIR]
      Every quoted string must carry an evidence tag on the same line, directly after
      the closing quote: [transcript H:MM:SS] or [file relative/path]. Each quote is
      checked ONLY against its tagged source. Candidate person/product names in prose
      must occur in the transcript, in a tagged file, or be followed in the same
      sentence by "(from the agenda)". No em dashes. Exit 0 on pass, 3 on any failure.

This is a strong check, not a guarantee: it proves quotes and names exist in their
declared sources. Whether a quote supports the claim around it is the verify step's
job (design v4, section 3).

ALWAYS run with PYTHONIOENCODING=utf-8.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stream_pipeline import normalize, parse_ts, parse_vtt  # noqa: E402

TS_TOLERANCE = 90
QUOTE_FIELDS = (("claims", "quote", "ts"), ("demos", "evidence_quote", "evidence_ts"),
                ("friction", "quote", "ts"), ("numbers", "quote", "ts"),
                ("techniques", "quote", "ts"), ("audience_questions", "question_quote", "ts"))
AGENDA_MARKER = "(from the agenda)"
DICT_PATH = Path("/usr/share/dict/words")


# ---------------------------------------------------------------------------
# Indexed text: normalized string with a start time per character offset
# ---------------------------------------------------------------------------

class TimedText:
    def __init__(self, entries: list[tuple[float, str]]):
        self.text = ""
        self.starts: list[tuple[int, float]] = []
        for t, s in entries:
            n = normalize(s)
            if not n:
                continue
            self.starts.append((len(self.text), t))
            self.text += n + " "

    def time_at(self, offset: int) -> float:
        t = self.starts[0][1] if self.starts else 0.0
        for o, tt in self.starts:
            if o <= offset:
                t = tt
            else:
                break
        return t

    def match_times(self, quote: str) -> list[float]:
        q = normalize(quote)
        if not q:
            return []
        # word-boundary match so "no" does not match inside "know"
        return [self.time_at(m.start()) for m in re.finditer(r"(?<![a-z0-9])" + re.escape(q) + r"(?![a-z0-9])", self.text)]


def parse_chunk(text: str) -> tuple[TimedText, float, float]:
    m = re.search(r"Core window:\s*(\d+:\d\d:\d\d)\s*-\s*(\d+:\d\d:\d\d)", text)
    if not m:
        raise ValueError("chunk header has no 'Core window:' line")
    core_start, core_end = parse_ts(m.group(1)), parse_ts(m.group(2))
    entries = []
    for line in text.splitlines():
        lm = re.match(r"\[(\d+:\d\d:\d\d)\](?: \[overlap\])? (.*)", line)
        if lm:
            entries.append((parse_ts(lm.group(1)), lm.group(2)))
    return TimedText(entries), core_start, core_end


def check_form(form: dict, chunk_text: str) -> dict:
    tt, core_start, core_end = parse_chunk(chunk_text)
    failures, passed = [], 0
    for section, qkey, tkey in QUOTE_FIELDS:
        for idx, item in enumerate(form.get(section, [])):
            quote = item.get(qkey) or ""
            if not normalize(quote):
                # a required evidence field that is missing, empty, or punctuation-only is a failure,
                # not a silent skip: the item would otherwise enter synthesis with no provenance
                item["verified"] = False
                item["matched_start"] = None
                failures.append({"section": section, "index": idx, "reason": "empty_quote", "quote": quote,
                                 "stated_ts": item.get(tkey)})
                continue
            times = tt.match_times(quote)
            in_core = [t for t in times if core_start <= t < core_end]
            reason = None
            if not times:
                reason = "not_found"
            elif not in_core:
                reason = "outside_core"
            else:
                try:
                    stated = parse_ts(str(item.get(tkey, "")))
                    ok_ts = [t for t in in_core if abs(t - stated) <= TS_TOLERANCE]
                except ValueError:
                    ok_ts = []
                if not ok_ts:
                    reason = "ts_off"
            item["verified"] = reason is None
            item["matched_start"] = in_core[0] if in_core else (times[0] if times else None)
            if reason:
                failures.append({"section": section, "index": idx, "reason": reason, "quote": quote[:160],
                                 "stated_ts": item.get(tkey)})
            else:
                passed += 1
    return {"passed": passed, "failed": len(failures), "failures": failures}


# ---------------------------------------------------------------------------
# Doc checks
# ---------------------------------------------------------------------------

TAG_RE = r"\s*\[(transcript)\s+(\d+:\d\d:\d\d)\]|\s*\[(file)\s+([^\]\s]+)\]"
QUOTE_RE = re.compile(r'"([^"\n]+)"(?:' + TAG_RE + r')?')


def strip_code(line: str) -> str:
    return re.sub(r"`[^`]*`", lambda m: " " * len(m.group(0)), line)


def extract_quotes(doc: str) -> list[dict]:
    out = []
    in_fence = False
    for n, raw in enumerate(doc.splitlines(), 1):
        if raw.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = strip_code(raw)
        for m in QUOTE_RE.finditer(line):
            # group 1 = quote; 2/3 = transcript tag + ts; 4/5 = file tag + path
            if m.group(3):
                src = {"kind": "transcript", "ts": m.group(3)}
            elif m.group(5):
                src = {"kind": "file", "path": m.group(5)}
            else:
                src = None
            out.append({"line": n, "quote": m.group(1), "source": src})
    return out


_DICT: set | None = None


def dictionary() -> set:
    global _DICT
    if _DICT is None:
        _DICT = set()
        if DICT_PATH.exists():
            _DICT = {w.strip().lower() for w in DICT_PATH.read_text(errors="ignore").splitlines()}
    return _DICT


STOP_CAPS = {"I", "A", "AI", "OK", "PT", "PM", "AM", "Q", "API", "UI", "PR", "PRs", "CLI", "VM", "JSON", "URL"}


def _is_word(tok: str, words: set) -> bool:
    """Dictionary lookup that tolerates common inflections (the system wordlist has lemmas only)."""
    w = tok.lower()
    if w in words:
        return True
    for suf, rep in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("d", ""),
                     ("ing", ""), ("ing", "e"), ("ly", ""), ("er", ""), ("ers", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and (w[: -len(suf)] + rep) in words:
            return True
    return False


def _is_acronym_or_compound(tok: str) -> bool:
    return tok.isupper() or "-" in tok or tok in STOP_CAPS


def extract_name_candidates(doc: str) -> list[dict]:
    """Runs of capitalized words in prose (headings, code, quotes, tags, URLs excluded).
    Possessive 's is stripped; all-caps and hyphenated tokens are not names; leading and
    trailing dictionary words are trimmed from a run (\"Guest Jane Roe\" -> \"Jane Roe\").
    A single remaining word is a candidate only if its lowercase form is not a dictionary word."""
    words = dictionary()
    out = []
    in_fence = False
    for n, raw in enumerate(doc.splitlines(), 1):
        if raw.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or raw.lstrip().startswith("#") or re.match(r"^\s*\|?\s*-{3,}", raw):
            continue
        line = strip_code(raw)
        line = re.sub(r'"[^"\n]*"', lambda m: " " * len(m.group(0)), line)
        line = re.sub(r"\[(?:transcript|file)[^\]]*\]", " ", line)
        line = re.sub(r"https?://\S+", " ", line)
        for sent in re.split(r"(?<=[.!?:;|*(])\s+|^\s*[-*]\s+|\|", line):
            toks = [(m.start(), m.end(), re.sub(r"'s$", "", m.group(0))) for m in re.finditer(r"[A-Za-z][A-Za-z0-9'\-]*", sent)]
            i = 0
            while i < len(toks):
                w = toks[i][2]
                if w[0].isupper() and not _is_acronym_or_compound(w):
                    j = i
                    while (j + 1 < len(toks) and toks[j + 1][2][0].isupper()
                           and not _is_acronym_or_compound(toks[j + 1][2]) and toks[j + 1][0] - toks[j][1] <= 1):
                        j += 1
                    run = [t[2] for t in toks[i:j + 1]]
                    while run and _is_word(run[0], words):
                        run.pop(0)
                    while run and _is_word(run[-1], words):
                        run.pop()
                    if run:
                        out.append({"line": n, "name": " ".join(run), "context": sent.strip()})
                    i = j + 1
                else:
                    i += 1
    return out


def check_doc(doc: str, transcript_cues: list[dict], root: Path) -> dict:
    tt = TimedText([(c["start"], c["text"]) for c in transcript_cues])
    failures = []
    file_texts: dict[str, str] = {}
    quotes = extract_quotes(doc)
    for q in quotes:
        if len(normalize(q["quote"]).split()) == 0:
            continue
        src = q["source"]
        if src is None:
            failures.append({"check": "quote_untagged", "line": q["line"], "quote": q["quote"][:160]})
            continue
        if src["kind"] == "transcript":
            times = tt.match_times(q["quote"])
            if not times:
                failures.append({"check": "quote_not_in_transcript", "line": q["line"], "quote": q["quote"][:160]})
            elif not any(abs(t - parse_ts(src["ts"])) <= TS_TOLERANCE for t in times):
                failures.append({"check": "quote_ts_off", "line": q["line"], "quote": q["quote"][:160],
                                 "tagged": src["ts"], "found_at": [round(t) for t in times[:3]]})
        else:
            fp = (root / src["path"]).resolve()
            if not str(fp).startswith(str(root.resolve())) or not fp.is_file():
                failures.append({"check": "file_missing", "line": q["line"], "path": src["path"]})
                continue
            txt = file_texts.setdefault(src["path"], normalize(fp.read_text(encoding="utf-8", errors="ignore")))
            nq = normalize(q["quote"])
            if not re.search(r"(?<![a-z0-9])" + re.escape(nq) + r"(?![a-z0-9])", txt):
                failures.append({"check": "quote_not_in_file", "line": q["line"], "path": src["path"],
                                 "quote": q["quote"][:160]})
    tagged_file_text = " ".join(file_texts.values())
    transcript_nospace = tt.text.replace(" ", "")
    files_nospace = tagged_file_text.replace(" ", "")
    unresolved = []
    for c in extract_name_candidates(doc):
        # compare with spaces removed so "YapBot" matches a transcribed "yap bot"
        nn = normalize(c["name"]).replace(" ", "")
        if nn and (nn in transcript_nospace or nn in files_nospace):
            continue
        if AGENDA_MARKER in c["context"] or AGENDA_MARKER in doc.splitlines()[c["line"] - 1]:
            continue
        unresolved.append(c)
    seen = set()
    for c in unresolved:
        if c["name"] not in seen:
            seen.add(c["name"])
            failures.append({"check": "name_unresolved", "line": c["line"], "name": c["name"]})
    for n, line in enumerate(doc.splitlines(), 1):
        if "\u2014" in line:
            failures.append({"check": "em_dash", "line": n})
    return {"quotes": len(quotes), "failed": len(failures), "failures": failures}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("form")
    f.add_argument("form", type=Path)
    f.add_argument("--chunk", type=Path, required=True)
    d = sub.add_parser("doc")
    d.add_argument("doc", type=Path)
    d.add_argument("--transcript", type=Path, required=True)
    d.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    a = ap.parse_args(argv)
    if a.cmd == "form":
        form = json.loads(a.form.read_text(encoding="utf-8"))
        res = check_form(form, a.chunk.read_text(encoding="utf-8"))
        a.form.write_text(json.dumps(form, indent=1), encoding="utf-8")
    else:
        cues = parse_vtt(a.transcript.read_text(encoding="utf-8"))
        res = check_doc(a.doc.read_text(encoding="utf-8"), cues, a.root)
    print(json.dumps({"ok": res["failed"] == 0, **res}, indent=1))
    sys.exit(0 if res["failed"] == 0 else 3)


if __name__ == "__main__":
    main()
