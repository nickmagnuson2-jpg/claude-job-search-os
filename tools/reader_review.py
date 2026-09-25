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
  --dry-run prints the prompt and the planned commands without dispatching.
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
    slug = a.slug or re.sub(r"[^a-z0-9]+", "-", doc.stem.lower()).strip("-")
    stamp = dt.date.today().strftime("%m%d%y")
    out_dir = repo / "output" / "analysis" / "reader-review-inputs" / f"{stamp}-{slug}"
    text_path, images = render(doc, out_dir)
    rel = lambda p: str(Path(p).resolve().relative_to(repo))  # noqa: E731
    source_paths = sorted(rel(p) for p in (repo / a.source_dir).glob("*.md")) if a.source_dir else []
    target = build_target(rel(text_path), a.reader, a.purpose, a.protect, a.source_dir)
    report_for = lambda m: f"output/analysis/{stamp}-{m}-{slug}-reader-review.md"  # noqa: E731
    plans = plan_commands(models, target, rel(text_path), [rel(i) for i in images],
                          claims(a.protect), report_for, a.known_errors, source_paths)
    if a.dry_run:
        print(target, "\n")
        for m, argv_ in plans:
            print(m, "images:", argv_.count("--image"), "report:", report_for(m))
        return 0
    with ThreadPoolExecutor(max_workers=len(plans)) as ex:
        results = dict(zip([m for m, _ in plans], ex.map(lambda p: run_one(p[1], repo), plans)))
    print(json.dumps({"inputs": rel(out_dir), "images": len(images), "results": results}, indent=2))
    return 0 if all(r["rc"] == 0 for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
