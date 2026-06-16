#!/usr/bin/env python3
"""Convert a markdown reference doc (dossier, cheat sheet, prep package, op-ed) to a multi-page PDF.

Use this for ANYTHING that isn't a CV. CVs use tools/md_to_pdf.py (one-page Times-style format).
This one renders multi-page Georgia-serif documents with proper tables, headings, and blockquotes.

Usage:
    PYTHONIOENCODING=utf-8 python3 tools/md_to_pdf_doc.py <input.md> [output.pdf]

Default output path: same directory and stem as input, .pdf extension.
"""

import subprocess
import sys
from datetime import date
from pathlib import Path

import markdown


CSS = """
@page { size: letter; margin: 0.6in; margin-bottom: 0.75in; }
body { font-family: Georgia, "Times New Roman", serif; font-size: 10.5pt; line-height: 1.4; color: #111; }
h1 { font-size: 18pt; border-bottom: 2px solid #000; padding-bottom: 4px; margin-bottom: 10pt; }
h2 { font-size: 13pt; margin-top: 18pt; border-bottom: 1px solid #999; padding-bottom: 2px; }
h3 { font-size: 11.5pt; margin-top: 12pt; }
h4 { font-size: 10.5pt; margin-top: 10pt; }
p { margin: 6pt 0; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.5pt; }
th, td { border: 1px solid #999; padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #eee; }
code { font-family: Menlo, Monaco, monospace; background: #f0f0f0; padding: 1px 3px; border-radius: 2px; font-size: 9.5pt; }
pre { background: #f6f6f6; padding: 8pt; border-radius: 3px; overflow-x: auto; }
blockquote { border-left: 3px solid #888; margin: 6pt 0; padding-left: 10pt; color: #333; }
ul, ol { margin: 4pt 0 4pt 18pt; }
li { margin-bottom: 2pt; }
strong { color: #000; }
hr { border: none; border-top: 1px solid #ccc; margin: 12pt 0; }
a { color: #0645ad; text-decoration: none; }

/* Print-reassembly footer: doc title (left), generated date (center), Page X of Y (right).
   So scattered printouts on the desk can be put back together. CVs are intentionally excluded
   (they use tools/md_to_pdf.py and are single-page). */
h1 { string-set: doctitle content(); }
@page {
  @bottom-left { content: string(doctitle); font-family: Georgia, serif; font-size: 8pt; color: #888; }
  @bottom-center { content: "Generated __GEN_DATE__"; font-family: Georgia, serif; font-size: 8pt; color: #888; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font-family: Georgia, serif; font-size: 8pt; color: #888; }
}
"""


def render(md_path: Path, pdf_path: Path) -> None:
    md = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
    css = CSS.replace("__GEN_DATE__", date.today().isoformat())
    html = (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{css}</style></head><body>{body}</body></html>"
    )
    html_tmp = pdf_path.with_suffix(".html.tmp")
    html_tmp.write_text(html, encoding="utf-8")
    try:
        subprocess.run(
            ["weasyprint", str(html_tmp), str(pdf_path)],
            check=True,
            capture_output=True,
        )
    finally:
        html_tmp.unlink(missing_ok=True)
    print(f"PDF written to {pdf_path}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 tools/md_to_pdf_doc.py <input.md> [output.pdf]", file=sys.stderr)
        sys.exit(1)
    md_path = Path(sys.argv[1])
    if not md_path.exists():
        print(f"Not found: {md_path}", file=sys.stderr)
        sys.exit(1)
    pdf_path = Path(sys.argv[2]) if len(sys.argv) > 2 else md_path.with_suffix(".pdf")
    render(md_path, pdf_path)


if __name__ == "__main__":
    main()
