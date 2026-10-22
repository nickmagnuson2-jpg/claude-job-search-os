"""reader_review.py -- three-question cross-model review of a finished document for a named reader.

WHY THIS EXISTS
---------------
Two review passes on reader-facing pages (2026-09-23 and 2026-09-24) ran the same three
questions through codex_verify.py by hand: is it ACCURATE, does it HELP THIS READER, does it
LEAK anything it must not. Both times the prompt, the rendered-text extraction and the
light-mode screenshots were rebuilt from scratch, and both times the pass caught the
defects that mattered (a mis-attributed company priority the reader would have spotted at
once; hard numbers about a former employer). Rebuilding it by hand is how a step gets
skipped, so the deterministic parts live here:

  1. render: HTML -> rendered-DOM text (hidden rows and collapsed quotes INCLUDED, because
     a reader can expand them) + light-mode screenshots split into page images.
     Markdown -> text as-is, no images.
  2. prompt: the three questions, built from --reader / --purpose / --protect.
  3. dispatch: codex_verify.py once per model, in parallel. Grok cannot take images
     (image_via="none" in codex_verify.MODELS), so it gets text only.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/reader_review.py PATH/to/page.html \\
      --reader "Director of Operations at a mid-size manufacturer" \\
      --purpose "how to set up a monthly operating review" \\
      --protect "internal figures from the author's past employers" \\
      --source-dir output/<slug>/research
  --dry-run prints the prompt, each model's evidence scope and the planned commands
  without dispatching.

EVIDENCE SCOPE (added 2026-10-12)
---------------------------------
A reviewer's "unsupported" means "not in the evidence I was shown", exactly as its "clean"
means "nothing wrong in the bytes I was shown". The sandbox in codex_verify.py denies data/
and coaching/, so a true career claim sourced there comes back flagged on every prep
document. After each model returns, its report gets a block prepended that states what that
model could and could not read, and lists each unsupported-claim finding whose wording
matches a line in data/projects or coaching/coached-answers that the model was not shown,
labeled "outside reviewer scope?". The label is a pointer for a human check. It does not
change the findings array, a severity or the ledger.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as htmllib
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from tools.slug import slugify as _slugify  # noqa: E402

# Imported, not copied: the denied trees and the findings parser are the facts the scope
# block reports, and a second copy here could disagree with the sandbox that enforces them.
from tools import codex_verify  # noqa: E402

DEFAULT_MODELS = ("codex", "fable", "grok")
TEXT_ONLY_MODELS = {"grok"}
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SLICE_PX = 3000


def html_to_lines(dom: str) -> list[str]:
    """Rendered DOM -> readable lines. Keeps hidden content, drops script/style/svg."""
    s = re.sub(r"(?s)<head>.*?</head>", "", dom)
    s = re.sub(r"(?s)<(script|style|svg|noscript)\b.*?</\1>", "", s)
    s = re.sub(r"<(h[1-4])[^>]*>", r"\n## ", s)
    s = re.sub(r"<(li|tr|p|div|section|details|summary|dt|article|blockquote|pre)\b[^>]*>", "\n", s)
    s = re.sub(r"<(td|th|dd)\b[^>]*>", " | ", s)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s)
    lines = (re.sub(r"[ \t]+", " ", x).strip() for x in s.split("\n"))
    return [ln for ln in lines if ln and ln != "|"]


def build_target(text_path: str, reader: str, purpose: str, protect: str,
                 source_dir: str = "") -> str:
    """The three-question review prompt. Q3 is skipped explicitly when nothing is protected."""
    sources = (f" Check factual claims against the files in {source_dir}, which cite their"
               f" sources inline; say which file and line supports or contradicts each claim."
               if source_dir else
               " No source files were supplied, so for Q1 flag every factual claim you cannot"
               " support from the page itself and mark it UNVERIFIED rather than guessing.")
    q3 = (f"Q3 CONFIDENTIALITY: list every item on the page that concerns {protect}, including"
          f" numbers written in words. For each, say whether it would embarrass the author or"
          f" the subject if the page leaked, and suggest a safe rewording. Material labeled as"
          f" invented or illustrative is out of scope for Q3."
          if protect.strip() else
          "Q3 CONFIDENTIALITY: SKIPPED. The author named nothing to protect for this run."
          " Write 'Q3 skipped: nothing named to protect' in your report and do not invent a scope.")
    return (
        f"Review ONLY {text_path} (the page as a reader sees it, with collapsed and hidden"
        f" content included) and any attached page images.{sources} Do not read anything"
        f" under data/.\n"
        f"THE READER: {reader}.\n"
        f"WHAT THE READER NEEDS FROM IT: {purpose}.\n"
        f"Answer three questions, quoting the page line for every reference.\n"
        f"Q1 ACCURACY: list each claim that is wrong, unsupported, overstated, missing a"
        f" qualifier, or that picks one side of a contradiction without saying so.\n"
        f"Q2 USEFULNESS: does this give THIS reader what they need? What is missing, unclear,"
        f" contradictory, wrong for their role or company, or telling them what they already"
        f" know? What reads as filler or AI-sounding? What would you cut?\n"
        f"{q3}"
    )


def claims(protect: str) -> list[str]:
    out = ["Every factual claim on the page is accurate and supported",
           "The page gives this reader what they need"]
    if protect.strip():
        out.append(f"The page discloses nothing that concerns {protect}")
    return out


def plan_commands(models, target, text_rel, images_rel, claim_list, report_for,
                  known_errors="", source_paths=()) -> list[tuple[str, list[str]]]:
    """One codex_verify.py argv per model. Text-only models never receive --image."""
    plans = []
    for m in models:
        argv = [sys.executable, "tools/codex_verify.py", "--model", m, "--target", target,
                "--paths", text_rel, *source_paths, "--report", report_for(m)]
        for c in claim_list:
            argv += ["--claim", c]
        if known_errors:
            argv += ["--known-errors", known_errors]
        if m not in TEXT_ONLY_MODELS:
            for img in images_rel:
                argv += ["--image", img]
        plans.append((m, argv))
    return plans


# --- EVIDENCE SCOPE -------------------------------------------------------------------------
# Trees that hold the sourced record of the author's own career claims. Both sit under a
# sandbox-denied tree, so no reviewer can confirm a claim against them.
CLAIM_SOURCE_DIRS = ("data/projects", "coaching/coached-answers")
OUTSIDE_LABEL = "outside reviewer scope?"
SCOPE_BEGIN = "<!-- reader-review-scope:begin -->"
SCOPE_END = "<!-- reader-review-scope:end -->"

# A finding is of the "unsupported claim" kind when its summary says the reviewer could not
# find support. Wording outside this list is a FALSE NEGATIVE: the finding is not examined.
UNSUPPORTED_RE = re.compile(
    r"\b(unsupported|unsourced|unverified|unverifiable|uncorroborated|unsubstantiated"
    r"|not (be )?(supported|sourced|verified|substantiated|established|corroborated)"
    r"|cannot (be )?verif\w*"
    r"|no (supporting )?(source|sources|evidence|support)"
    r"|without (any )?(source|sources|evidence|support))\b", re.I)

# Words a reviewer uses to describe a finding, which say nothing about the claim itself.
REVIEW_WORDS = frozenset("""
about above across after against allowed among asserts attributes before being between claim
claimed could doing during evidence exact figure files given having number other page
permitted presented presents reader review reviewer should source statement stated states
supplied support supported their there these thing those through under unsupported
unverified where whether which while without would
""".split())
MIN_SHARED = 4          # distinct shared terms on ONE source line; a figure counts as two
_YEAR = re.compile(r"(19|20)\d\d")


def claim_terms(text: str) -> set[str]:
    """Words of 5+ letters (trailing s dropped) and figures such as $2m, 37%, 183."""
    out = set()
    for tok in re.findall(r"\$?\d[\d,.]*[a-z%]*|[a-z]{5,}", text.lower()):
        if tok[0].isalpha():
            if tok not in REVIEW_WORDS:
                out.add(tok[:-1] if tok.endswith("s") and len(tok) > 5 else tok)
        else:
            tok = tok.replace(",", "").rstrip(".")
            bare_digits = tok.isdigit()
            if (bare_digits and len(tok) < 2) or (bare_digits and _YEAR.fullmatch(tok)):
                continue        # a lone digit or a year matches too much to count
            out.add(tok)
    return out


def _weight(shared: set[str]) -> int:
    return sum(1 if t[0].isalpha() else 2 for t in shared)


def is_unsupported_finding(summary: str) -> bool:
    return bool(UNSUPPORTED_RE.search(summary or ""))


def _located_line(location, repo: Path) -> str:
    """The line a finding points at, or '' when it cannot be read from inside the repo."""
    if not isinstance(location, str) or ":" not in location:
        return ""
    path, _, line = location.rpartition(":")
    if not line.strip().isdigit():
        return ""
    target = (repo / path.strip()).resolve()
    if repo.resolve() not in target.parents or not target.is_file():
        return ""
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    n = int(line)
    return lines[n - 1] if 1 <= n <= len(lines) else ""


def withheld_lines(repo: Path, shown=()) -> list[tuple[str, int, set[str]]]:
    """(relative path, line number, terms) for every line the reviewer was NOT shown."""
    shown = {str(Path(s).as_posix()) for s in shown}
    out = []
    for d in CLAIM_SOURCE_DIRS:
        for f in sorted((repo / d).glob("*.md")):
            rel = f.relative_to(repo).as_posix()
            if rel in shown:
                continue
            for i, ln in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                terms = claim_terms(ln)
                if terms:
                    out.append((rel, i, terms))
    return out


def label_outside_scope(findings, repo: Path, shown=()) -> list[dict]:
    """Unsupported-claim findings whose wording matches one withheld source line.

    The claim text is the finding's summary plus the page line its location names. A finding
    is labeled when ONE line of a withheld file shares MIN_SHARED distinct terms with it
    (a figure counts as two). The best line is reported as path:line, never its content.

    FALSE POSITIVES: shared vocabulary without shared meaning. The source line can state a
    DIFFERENT figure or scope than the page (the words match, the number does not), so the
    label means "open this line before rejecting or accepting the finding", never "the
    claim is supported". Long source lines and long page lines match more easily.
    FALSE NEGATIVES: a claim paraphrased with other words; a figure written differently
    ("$2M" against "2 million"); support split across two source lines; a finding whose
    summary does not use the wording in UNSUPPORTED_RE; support that lives outside
    CLAIM_SOURCE_DIRS (an email, data/profile.md, a subdirectory). No label is not evidence
    that the reviewer saw everything relevant.
    """
    repo = Path(repo)
    index = None
    labels = []
    for f in findings or []:
        summary = str(f.get("summary") or "")
        if not is_unsupported_finding(summary):
            continue
        terms = claim_terms(summary) | claim_terms(_located_line(f.get("location"), repo))
        if index is None:
            index = withheld_lines(repo, shown)
        best = None
        for rel, n, line_terms in index:
            w = _weight(terms & line_terms)
            if w >= MIN_SHARED and (best is None or w > best[0]):
                best = (w, rel, n)
        if best:
            labels.append({"id": str(f.get("id")), "label": OUTSIDE_LABEL,
                           "evidence": f"{best[1]}:{best[2]}", "shared": best[0]})
    return labels


def evidence_scope(model: str, text_rel: str, source_dir: str, source_paths, images_rel) -> dict:
    """What one reviewing model was given and what it was denied. Facts only, no judgment."""
    text_only = model in TEXT_ONLY_MODELS
    return {"model": model, "page_text": text_rel, "source_dir": source_dir,
            "source_paths": list(source_paths), "images_rendered": len(images_rel),
            "images_sent": 0 if text_only else len(images_rel), "text_only": text_only,
            "denied": [f"{t}/" for t in codex_verify.PRIVATE_TREES]}


def scope_header(scope: dict, labels=None) -> str:
    """The block prepended to a report. labels=None (dry run) omits the label section."""
    s = scope
    if s["text_only"]:
        images = f"0 ({s['model']} is text-only; {s['images_rendered']} rendered, withheld)"
    elif s["images_sent"]:
        images = str(s["images_sent"])
    else:
        images = "0 (none rendered)"
    if s["source_dir"]:
        src = f"{s['source_dir']} ({len(s['source_paths'])} file(s) named to the reviewer)"
        src_files = "".join(f"\n  - {p}" for p in s["source_paths"]) or "\n  - (no .md files found)"
    else:
        src = "NOT GIVEN (the reviewer had the page alone to check claims against)"
        src_files = ""
    out = [
        SCOPE_BEGIN,
        f"## EVIDENCE SCOPE ({s['model']}) -- written by tools/reader_review.py, not by the reviewer",
        "",
        f"- Told to review: {s['page_text']}",
        f"- --source-dir: {src}{src_files}",
        f"- Page images sent: {images}",
        f"- Sandbox-denied, unreadable whatever the prompt says: {', '.join(s['denied'])}"
        " (plus sealed subpaths and sibling repositories)",
        "- Prompt instruction: \"Do not read anything under data/\"",
        "- Readable but not named: the rest of this repository outside the denied trees.",
        f"- So the reviewer could NOT check a claim against {' or '.join(CLAIM_SOURCE_DIRS)},"
        " or against any email.",
        "",
        "Every \"unsupported\" or \"unverified\" below means \"not in the evidence listed above\"."
        " Every \"clean\" covers the same evidence and nothing wider.",
    ]
    if labels is not None:
        out += ["", f"### Findings auto-labeled \"{OUTSIDE_LABEL}\""]
        if labels:
            out += [f"- {l['id']}: {l['label']} -> check {l['evidence']}"
                    f" (shared-term weight {l['shared']})" for l in labels]
            out.append("A label means one withheld line shares wording with the claim. It may"
                       " state a different figure. Open the line before rejecting the finding.")
        else:
            out.append("No finding was auto-labeled. That is a narrow word match over"
                       f" {' and '.join(CLAIM_SOURCE_DIRS)}; it does not show that the reviewer"
                       " saw every relevant source.")
    out += [SCOPE_END, "", ""]
    return "\n".join(out)


def annotate_report(report: Path, scope: dict, repo: Path, shown=()):
    """Prepend the scope block to a written report. Returns the labels, or None if no report.

    Prepended, never inserted after the findings marker: codex_verify parses the first JSON
    array after that marker, so nothing here can change what the ledger recorded. Re-running
    replaces the earlier block.
    """
    report = Path(report)
    if not report.is_file():
        return None
    labels = label_outside_scope(codex_verify.parse_findings(report), repo, shown)
    text = report.read_text(encoding="utf-8")
    if text.startswith(SCOPE_BEGIN) and SCOPE_END in text:
        text = text.split(SCOPE_END, 1)[1].lstrip("\n")
    report.write_text(scope_header(scope, labels) + text, encoding="utf-8")
    return labels


def render(doc: Path, out_dir: Path) -> tuple[Path, list[Path]]:
    """Write page-text.md (and page-N.png for HTML) into out_dir. Fails loudly without Chrome."""
    out_dir.mkdir(parents=True, exist_ok=True)
    text_path = out_dir / "page-text.md"
    if doc.suffix.lower() in (".md", ".markdown", ".txt"):
        text_path.write_text(doc.read_text(encoding="utf-8"), encoding="utf-8")
        return text_path, []
    if not Path(CHROME).exists():
        raise SystemExit(f"reader_review: Chrome not found at {CHROME}; cannot render {doc}")
    dom = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--virtual-time-budget=4000",
                          "--dump-dom", doc.resolve().as_uri()],
                         capture_output=True, text=True, timeout=120).stdout
    lines = html_to_lines(dom)
    if not lines:
        raise SystemExit(f"reader_review: rendered no text from {doc}")
    text_path.write_text(f"# Page text for review: {doc.name}\n\n" + "\n".join(lines) + "\n",
                         encoding="utf-8")
    light = out_dir / "_light.html"
    light.write_text(re.sub(r"<html\b", '<html data-theme="light"',
                            doc.read_text(encoding="utf-8"), count=1), encoding="utf-8")
    height = page_height(light)
    shot = out_dir / "_full.png"
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    f"--window-size=1280,{height}", "--virtual-time-budget=5000",
                    f"--screenshot={shot}", light.as_uri()], capture_output=True, timeout=180)
    images: list[Path] = []
    try:
        from PIL import Image
        im = Image.open(shot).convert("RGB")
        w, h = im.size
        for i, top in enumerate(range(0, h, SLICE_PX), start=1):
            p = out_dir / f"page-{i}.png"
            im.crop((0, top, w, min(h, top + SLICE_PX))).save(p)
            images.append(p)
    finally:
        shot.unlink(missing_ok=True)
        light.unlink(missing_ok=True)
    return text_path, images


def page_height(light: Path, fallback: int = 24000) -> int:
    """Measured document height in px, so a long page is never silently cut off."""
    probe = light.with_name("_probe.html")
    probe.write_text(light.read_text(encoding="utf-8").replace(
        "</body>", '<script>setTimeout(()=>document.body.setAttribute("data-rr-h",'
                   'document.documentElement.scrollHeight),800)</script></body>', 1),
        encoding="utf-8")
    try:
        dom = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--window-size=1280,1000",
                              "--virtual-time-budget=3000", "--dump-dom", probe.as_uri()],
                             capture_output=True, text=True, timeout=120).stdout
    finally:
        probe.unlink(missing_ok=True)
    m = re.search(r'data-rr-h="(\d+)"', dom or "")
    return int(m.group(1)) + 100 if m else fallback


def run_one(argv: list[str], repo_root: Path) -> dict:
    proc = subprocess.run(argv, cwd=repo_root, capture_output=True, text=True,
                          env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    tail = proc.stdout[proc.stdout.rfind("{"):] if "{" in proc.stdout else ""
    try:
        data = json.loads(tail)
    except ValueError:
        data = {}
    return {"rc": proc.returncode, "findings": data.get("findings"),
            "report": data.get("report"), "stderr": proc.stderr[-400:]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("document", help="HTML or Markdown file the reader will see")
    ap.add_argument("--reader", required=True, help="who reads it: role, company, situation")
    ap.add_argument("--purpose", required=True, help="what the reader needs from it")
    ap.add_argument("--protect", default="",
                    help="what must not leak (e.g. 'internal figures from past employers'); "
                         "omitted -> Q3 is skipped and the report says so")
    ap.add_argument("--source-dir", default="", help="repo dir of research files for Q1")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--slug", default="")
    ap.add_argument("--known-errors", default="")
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    repo = Path(a.repo_root).resolve()
    doc = Path(a.document).expanduser()
    if not doc.exists():
        print(f"reader_review: no such document: {doc}", file=sys.stderr)
        return 2
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    slug = a.slug or _slugify(doc.stem)
    stamp = dt.date.today().strftime("%m%d%y")
    out_dir = repo / "output" / "analysis" / "reader-review-inputs" / f"{stamp}-{slug}"
    text_path, images = render(doc, out_dir)
    rel = lambda p: str(Path(p).resolve().relative_to(repo))  # noqa: E731
    source_paths = sorted(rel(p) for p in (repo / a.source_dir).glob("*.md")) if a.source_dir else []
    target = build_target(rel(text_path), a.reader, a.purpose, a.protect, a.source_dir)
    report_for = lambda m: f"output/analysis/{stamp}-{m}-{slug}-reader-review.md"  # noqa: E731
    plans = plan_commands(models, target, rel(text_path), [rel(i) for i in images],
                          claims(a.protect), report_for, a.known_errors, source_paths)
    images_rel = [rel(i) for i in images]
    scopes = {m: evidence_scope(m, rel(text_path), a.source_dir, source_paths, images_rel)
              for m in models}
    if a.dry_run:
        print(target, "\n")
        for m, argv_ in plans:
            print(m, "images:", argv_.count("--image"), "report:", report_for(m))
        for m in models:
            print()
            print(scope_header(scopes[m]).rstrip())
        return 0
    with ThreadPoolExecutor(max_workers=len(plans)) as ex:
        results = dict(zip([m for m, _ in plans], ex.map(lambda p: run_one(p[1], repo), plans)))
    for m, r in results.items():
        # None = no report on disk to annotate, which is different from "nothing labeled".
        r["outside_scope"] = annotate_report(repo / report_for(m), scopes[m], repo, source_paths)
    print(json.dumps({"inputs": rel(out_dir), "images": len(images), "results": results}, indent=2))
    return 0 if all(r["rc"] == 0 for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
