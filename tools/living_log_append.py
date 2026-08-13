#!/usr/bin/env python3
"""
living_log_append.py — the ONLY sanctioned writer for the voice-pure living logs.

Living logs (personal vault, voice-pure per personal/CLAUDE.md):
  garden | practice | coffee | farmers-market

Claude must never Write/Edit these files directly (enforced by
tools/check_living_log_purity.py). /wispr routes verbatim chunks here via
this script. Verbatim text is written exactly as passed — no paraphrase.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/living_log_append.py \\
      <log> <date> <label> <text> [source_wikilink] [--file PATH]

Output: JSON to stdout.
  ok:    {"status":"ok","action":"appended","log":..,"date":..,"created_section":bool}
  error: {"status":"error","message":..}  (exit 1)
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import vault_paths

# Log names only. The vault root that hosts them is private (this repo is
# public), so paths resolve at call time via tools/vault_paths.py rather than
# being hardcoded here. A dict of resolved paths would need the vault at import
# time and make the module unimportable when it is unconfigured.
LOG_NAMES = ("garden", "practice", "coffee", "farmers-market")


def log_path(name: str) -> Path:
    """Resolve a living log's path, raising VaultRootMissing if unconfigured."""
    if name not in LOG_NAMES:
        raise KeyError(name)
    return vault_paths.living_log(name)
DATE_H2 = re.compile(r"^## \d{4}-\d{2}-\d{2}\s*$", re.M)


def fail(msg):
    print(json.dumps({"status": "error", "message": msg}))
    sys.exit(1)


def write_atomic(path: Path, content: str) -> None:
    """Write file atomically via a sibling temp file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def main(argv):
    file_override = None
    if "--file" in argv:
        i = argv.index("--file")
        if i + 1 >= len(argv):
            fail("--file requires a path argument")
        file_override = argv[i + 1]
        args = argv[:i] + argv[i + 2:]
    else:
        args = list(argv)
    if len(args) < 4:
        fail("need: <log> <date> <label> <text> [source_wikilink]")
    log, date, label, text = args[0], args[1], args[2], args[3]
    source = args[4] if len(args) >= 5 else None
    if "\n" in label or "\n" in text:
        fail("label/text must be single-line (got embedded newline)")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        fail(f"bad date (want YYYY-MM-DD): {date}")
    if file_override:
        path = Path(file_override)
    elif log in LOG_NAMES:
        path = log_path(log)          # resolves the private vault root, may raise
    else:
        path = None
    if path is None:
        fail(f"unknown log '{log}'; valid: {', '.join(LOG_NAMES)}")
    if not path.exists():
        fail(f"log file not found: {path}")

    entry = f"**{label}.** {text}"
    if source:
        entry += f" (Source: [[{source}]])"

    raw = path.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    day_hdr = f"## {date}"
    created = False

    if any(ln.strip() == day_hdr for ln in lines):
        start = next(i for i, ln in enumerate(lines) if ln.strip() == day_hdr)
        end = len(lines)
        for j in range(start + 1, len(lines)):
            s = lines[j].strip()
            if s.startswith("## ") or s == "---":
                end = j
                break
        insert_at = end
        while insert_at - 1 > start and lines[insert_at - 1].strip() == "":
            insert_at -= 1
        new_lines = lines[:insert_at] + ["", entry] + lines[insert_at:]
    else:
        m = DATE_H2.search("\n".join(lines))
        block = [day_hdr, "", entry]
        if m:
            first_date_line = "\n".join(lines)[:m.start()].count("\n")
            new_lines = lines[:first_date_line] + block + [""] + lines[first_date_line:]
        else:
            tail = lines + ([""] if lines and lines[-1].strip() else []) + block
            new_lines = tail
        created = True

    write_atomic(path, nl.join(new_lines) + nl)
    print(json.dumps({
        "status": "ok", "action": "appended",
        "log": log, "date": date, "created_section": created,
    }))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except SystemExit:
        raise
    except Exception as e:
        fail(f"unexpected: {e}")
