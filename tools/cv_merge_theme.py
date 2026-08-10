#!/usr/bin/env python3
"""Merge a CV content YAML with a design theme YAML.

The architecture (decided 2026-05-12, see data/job-todos.md row CV-INFRA 4/6):
keep Nick's CV design as a single shared YAML at framework/cv-themes/tuck-mbb.yaml
and compose it into every generated CV YAML before rendering. Output CV YAMLs
end up with the full design block baked in, so they remain reproducible
standalone — re-rendering an old CV doesn't depend on this theme file.

Usage:
    python3 tools/cv_merge_theme.py \
        --content output/<slug>/<file>.content.yaml \
        --theme   framework/cv-themes/tuck-mbb.yaml \
        --out     output/<slug>/<file>.yaml

If --theme is omitted, defaults to framework/cv-themes/tuck-mbb.yaml.
If --out is omitted, prints merged YAML to stdout.

The content YAML must NOT contain a `design:` block. If it does, the script
exits with an error — that's a signal the generator is duplicating the design
block instead of relying on the theme.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_THEME = REPO_ROOT / "framework" / "cv-themes" / "tuck-mbb.yaml"


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _check_scalar_sections(content: dict) -> None:
    """Catch the unquoted-colon mangle before it reaches rendercv.

    A summary/details string written with a bare ': ' (e.g. `- ... fluency: I
    build ...`) is parsed by YAML as a single-key MAPPING, not a string. rendercv
    then fails with an opaque "couldn't match this section with any entry types".
    Flag it here with an actionable message: quote the offending string.
    """
    sections = (content.get("cv") or {}).get("sections") or {}
    for name, entries in sections.items():
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            # A text-block entry that parsed as a one-key dict whose key is a
            # sentence (has spaces) is the colon-mangle signature.
            if isinstance(entry, dict) and len(entry) == 1:
                (key,) = entry.keys()
                if isinstance(key, str) and " " in key.strip():
                    raise SystemExit(
                        f"Section '{name}' entry #{i + 1} parsed as a mapping, not "
                        f"text — almost certainly an unquoted ': ' in the content "
                        f"YAML (YAML reads 'foo: bar' as a key/value). Wrap that "
                        f'string in double quotes. Offending key: "{key[:60]}...".'
                    )


def merge(content: dict, theme: dict, *, strict: bool = True) -> dict:
    if "design" in content and strict:
        raise SystemExit(
            "Content YAML already contains a `design:` block. Remove it — the "
            "theme owns design. If this is intentional, re-run with --override."
        )
    _check_scalar_sections(content)
    merged = dict(content)
    merged["design"] = theme["design"]
    # Preserve canonical top-level key order: cv, design, locale, settings.
    key_order = ["cv", "design", "locale", "settings"]
    ordered = {k: merged[k] for k in key_order if k in merged}
    for k in merged:
        if k not in ordered:
            ordered[k] = merged[k]
    return ordered


def dump_yaml(data: dict) -> str:
    return yaml.dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--content", required=True, type=Path)
    ap.add_argument("--theme", type=Path, default=DEFAULT_THEME)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--override",
        action="store_true",
        help="Allow content YAML to contain a design block (gets replaced).",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON status record to stdout instead of YAML.",
    )
    args = ap.parse_args(argv)

    if not args.content.exists():
        print(json.dumps({"status": "error", "message": f"Content YAML not found: {args.content}"}))
        return 1
    if not args.theme.exists():
        print(json.dumps({"status": "error", "message": f"Theme YAML not found: {args.theme}"}))
        return 1

    content = load_yaml(args.content) or {}
    theme = load_yaml(args.theme) or {}
    if "design" not in theme:
        print(json.dumps({"status": "error", "message": "Theme YAML missing `design:` block"}))
        return 1

    merged = merge(content, theme, strict=not args.override)
    rendered = dump_yaml(merged)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        if args.json:
            print(json.dumps({"status": "ok", "out": str(args.out)}))
        else:
            print(f"Wrote {args.out}")
    else:
        sys.stdout.write(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
