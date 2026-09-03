#!/usr/bin/env python3
"""
check_banned_phrase.py — PreToolUse hook for Write|Edit|MultiEdit (CONTENT hook).

WHAT IT IS
----------
An ENUMERATED-PHRASE gate. It matches the regexes listed in
`tools/mannered-phrases.txt` against the text being written, and blocks on a hit.

It is NOT a mannered-prose detector, and nothing downstream may describe it as
one. Manneredness is a semantic property; no static signature can certify it.
Two prior attempts at semantic-property detectors in this repo
(`name_the_scope`, `guard_must_hard_abort_on_empty_input`) were measured against
real corpora and BOTH failed for exactly this reason. A clean exit here means no
LISTED phrase was found. It never means the prose is good.

WHAT IT CARRIES
---------------
Two rule families, distinguished by the table's `scope` column:

  scope=all       The load/bearing ban (2026-05-25). Nick: "I don't want that
                  anywhere in your vocabulary." Its documented reach is "anywhere
                  in output to Nick -- prep docs, chat responses, memory writes,
                  agent prompts, anywhere," so it is NOT narrowed by the
                  path exemptions added for the mannered-prose rows.

  scope=authored  Mannered prose (2026-09-03). Nick's definition: metaphor and
                  flourish substituted for direct statement. These rows stand
                  down on verbatim trees (transcripts, other people's email),
                  Nick's dated reflections, the append-only interaction logs, and
                  his own identity/decision files -- see AUTHORED_EXEMPT.

The per-row scope exists because a single shared exemption list would have
silently shrunk the older rule while generalizing it. Cross-model review,
2026-09-03, F1.

WHY IT EXISTS AT ALL
--------------------
The load/bearing rule was marked "promoted" and the promotion did not hold. Its
`promoted:` field read PARTIAL: check_draft_voice.py gated ONE surface
(tools/.pending-draft.txt), while the rule claimed seven. On 2026-08-20 Claude
used the phrase 6x across 3 authored files plus repeatedly in chat, with that
flag reading yes. This hook is the matcher the reopen gate asked for.

It then sat UNWIRED in `.claude/settings.json` from its own build until
2026-09-03, so for that entire period it judged nothing at all. That is the
defect it now also serves as a reminder of: a written hook is not a wired one.

WHAT IT MEASURES (a property, not a presence)
---------------------------------------------
A fact about the bytes being written: does a listed phrase appear on a line that
is not itself documenting the ban? Measured at build time: the load/bearing
compound appeared 580 times across tracked+untracked `.md` files.

HOOK TYPE: content hook. Per tools/HOOK_AUTHORING.md, a content hook's
false-positive surface is PATH SCOPE, not command position. Exemptions:

  1. PATH, universal (EXEMPT_PATH) — `fixtures/` dirs, `test_*` files, and the
     rule's own artifacts including the table itself. A fixture must be allowed
     to carry the bad pattern on purpose or the suite becomes unrunnable.
  2. PATH, scope=authored only (AUTHORED_EXEMPT) — the verbatim and Nick-authored
     trees named above.
  3. CONTENT — a write that is *about* the ban must be able to name the phrase.
     File-level: content mentioning the rule slug, this checker, or the table
     passes wholesale. Line-level: a line carrying a ban marker passes.

The line-level hatch is trivial to satisfy in good faith and therefore trivial to
abuse; that is the accepted cost of not blocking the rule's own documentation. It
guards against reflexive use, which is the failure mode actually observed.

KNOWN FALSE NEGATIVES — do not mistake a clean exit for coverage
----------------------------------------------------------------
  * Chat output is not gated. No PreToolUse surface exists for it, and it is the
    largest surface this rule applies to.
  * Content written through Bash is not gated: heredocs, `>` redirects, and every
    `tools/*.py` writer. Most writes to `data/` go through Bash helpers.
  * `new_content()` sees only the ADDED text, so an Edit whose `new_string`
    completes a phrase together with unchanged surrounding text is missed
    (`load-` already present, `bearing` added). Pre-existing; cross-model review
    2026-09-03, F8. Fixing it requires reconstructing the prospective result.
  * Only TEXT_SUFFIXES are judged. A `.py` or `.json` file is not.

Exit codes:
  0 — clean, unparseable payload, non-text target, no content, or exempt
  2 — banned phrase in new content, OR the table could not be loaded

Origin: 2026-05-25 ~9:30am pre-case. Nick, reading his own prep docs, found the
phrase ~35 times across active files. 2nd fire 2026-08-20. Generalized to a table
and wired 2026-09-03.
"""
import json
import re
import sys
from pathlib import Path

class TableError(Exception):
    """The denylist could not be loaded. Always a BLOCK, never a pass."""


# The shipped table. Not overridable from the payload: a hook whose policy file
# can be redirected by its own input is not a gate.
TABLE_PATH = Path(__file__).resolve().parent / "mannered-phrases.txt"

# A line that is documenting the ban may name the phrase.
BAN_MARKER = re.compile(
    r"\b(?:ban|bans|banned|banning|forbidden|prohibit(?:ed)?|"
    r"never\s+use|do\s+not\s+use|don'?t\s+use|avoid\s+the\s+phrase|"
    r"llm[\s-]tell|llm\s+term|replacement\s+table)\b",
    re.IGNORECASE,
)

# A file that names the rule or this checker anywhere is ban documentation.
FILE_MARKER = re.compile(
    r"feedback_no_load_bearing_vocabulary|check_banned_phrase|check_draft_voice"
    r"|mannered-phrases",
    re.IGNORECASE,
)

# Surfaces Nick reads as prose. Code files are judged by other gates; a .py
# docstring naming a phrase is almost always a checker, not prose to Nick.
TEXT_SUFFIXES = (".md", ".markdown", ".mdx", ".txt")

# Universal exemptions — apply to EVERY scope, including "all".
# Fixtures and tests must be allowed to carry the bad pattern on purpose, or the
# suite becomes unrunnable. The rule's own artifacts must be writable.
EXEMPT_PATH = re.compile(
    r"(?:^|/)(?:fixtures?|__pycache__)/"
    r"|(?:^|/)test_[^/]*$"
    r"|feedback_no_load_bearing_vocabulary\.md$"
    r"|HOOK_AUTHORING\.md$"
    r"|mannered-phrases\.txt$",
    re.IGNORECASE,
)

# Exemptions that apply ONLY to scope "authored" — the mannered-prose rows.
#
# These are trees whose prose is NOT Claude's: verbatim transcripts, other
# people's email, Nick's own dated reflections and identity files, and the
# append-only interaction logs that quote correspondence in full.
#
# Deliberately NOT exempt, because they are Claude-authored synthesis living
# inside otherwise-personal trees: coaching/progress/, data/people/,
# data/company-notes/, and the underscore-prefixed files in data/reflections/
# (_longitudinal.md carries `voice: cloud-generated`). Exempting those whole
# trees would let mannered prose land in a debrief or dossier and be copied
# into a gated artifact later. Origin: cross-model review 2026-09-03, F3.
#
# The five identity/decision files (profile, goals, professional-identity,
# decisions, accomplishments) are exempt because they are NICK'S OWN WRITING.
# That started as Claude's judgment call and was CONFIRMED BY NICK 2026-09-03.
# Do not re-litigate it; changing it needs him, not a reading of this comment.
# `captures/` is a VERBATIM tree, the same class as data/voice-corpus and
# data/source-emails: it holds other people's frozen words -- call transcripts,
# meeting summaries, text threads -- which Claude did not author and must never
# edit. Added 2026-09-03 on Nick's explicit approval, when freezing a third
# party's ChatGPT-written call summary into a new sibling private repo was
# blocked on a phrase inside that person's own text. Paraphrasing to satisfy the
# gate would have corrupted the capture, which is the one thing a verbatim tree
# exists to prevent. Verified at add time: no `captures/` directory existed in
# job-search or personal, so this widened nothing already on disk. Matched
# anywhere in the path, deliberately -- the tree lives in a sibling repo, not
# under data/.
AUTHORED_EXEMPT = re.compile(
    r"(?:^|/)captures/"
    r"|(?:^|/)data/(?:voice-corpus|source-emails)/"
    r"|(?:^|/)data/reflections/\d[^/]*$"
    r"|(?:^|/)data/(?:networking|job-pipeline|inbox)\.md$"
    r"|(?:^|/)data/(?:profile|goals|professional-identity"
    r"|decisions|accomplishments)\.md$",
    re.IGNORECASE,
)

VALID_SCOPES = ("all", "authored")


def load_table(table_path: Path) -> list[tuple[re.Pattern, str, str]]:
    """Parse the denylist. Raises TableError on anything that is not a usable table.

    Fails closed by construction: every caller treats TableError as a BLOCK. A
    gate that passes when its own policy file is missing is not a gate.
    """
    try:
        raw = table_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise TableError(f"denylist not found: {table_path}")
    except IsADirectoryError:
        raise TableError(f"denylist is a directory, not a file: {table_path}")
    except PermissionError:
        raise TableError(f"denylist not readable: {table_path}")
    except UnicodeDecodeError:
        raise TableError(f"denylist is not valid UTF-8: {table_path}")
    except OSError as exc:
        raise TableError(f"denylist could not be read ({exc.__class__.__name__}): {table_path}")

    rows: list[tuple[re.Pattern, str, str]] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise TableError(
                f"{table_path} line {lineno}: expected 3 tab-separated fields, got {len(parts)}"
            )
        pattern, scope, replacement = (part.strip() for part in parts)
        if not pattern:
            raise TableError(f"{table_path} line {lineno}: empty pattern")
        if scope not in VALID_SCOPES:
            raise TableError(
                f"{table_path} line {lineno}: scope must be one of {VALID_SCOPES}, got {scope!r}"
            )
        if not replacement:
            raise TableError(f"{table_path} line {lineno}: empty replacement")
        try:
            compiled = re.compile(rf"\b{pattern}\b", re.IGNORECASE)
        except re.error as exc:
            raise TableError(f"{table_path} line {lineno}: invalid regex ({exc})")
        rows.append((compiled, scope, replacement))

    if not rows:
        raise TableError(f"{table_path}: no usable rows")
    return rows


def scope_covers(scope: str, path: str) -> bool:
    """Does a row with this scope judge this path?

    "all" reaches everywhere the universal exemptions allow. "authored" additionally
    stands down on the verbatim and Nick-authored trees.
    """
    if scope == "authored":
        return not AUTHORED_EXEMPT.search(path)
    return True


def new_content(tool_name: str, tool_input: dict) -> str:
    """Only the text being ADDED — never the pre-existing file body.

    Edit sends old_string/new_string; blocking on old_string would make an
    existing violation impossible to edit away.
    """
    if tool_name == "Write":
        return tool_input.get("content", "") or ""
    if tool_name == "Edit":
        return tool_input.get("new_string", "") or ""
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits", []) or []
        return "\n".join((e or {}).get("new_string", "") or "" for e in edits)
    return ""


def violations(path: str, content: str,
               table: list[tuple[re.Pattern, str, str]]) -> list[tuple[int, str, str]]:
    """Return (line_no, matched_text, replacement) per blocking hit. Empty = clean."""
    if not path or not content:
        return []
    if not path.lower().endswith(TEXT_SUFFIXES):
        return []
    if EXEMPT_PATH.search(path):
        return []
    if FILE_MARKER.search(content):
        return []

    rows = [row for row in table if scope_covers(row[1], path)]
    if not rows:
        return []

    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(content.splitlines(), start=1):
        if BAN_MARKER.search(line):
            continue
        for pattern, _scope, replacement in rows:
            m = pattern.search(line)
            if m:
                hits.append((i, m.group(0), replacement))
                break
    return hits


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    tool_name = data.get("tool_name", "") or ""
    tool_input = data.get("tool_input", {}) or {}
    if not isinstance(tool_input, dict):
        sys.exit(0)

    path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "") or ""
    content = new_content(tool_name, tool_input)

    # Nothing to judge: do not pay the table-load cost, and do not turn an
    # unrelated tool call into a policy-file error.
    if not path or not content:
        sys.exit(0)

    # The table is loaded AFTER we know there is content to judge, and any
    # failure to load it BLOCKS. Exit 2 deliberately: an uncaught exception
    # would exit 1, which is not the documented blocking verdict.
    try:
        table = load_table(TABLE_PATH)
    except TableError as exc:
        print(
            f"BLOCKED (denylist unreadable): {exc}\n"
            "\n"
            "This hook fails closed. It cannot judge the write without its table,\n"
            "and passing an unjudged write would make the gate decorative.\n"
            "Fix the table, then retry.\n",
            file=sys.stderr,
        )
        sys.exit(2)

    hits = violations(str(path), content, table)
    if not hits:
        sys.exit(0)

    lines = "\n".join(
        f"  line {n}: {txt!r} -> {rep}" for n, txt, rep in hits[:10]
    )
    more = "" if len(hits) <= 10 else f"\n  ... and {len(hits) - 10} more\n"
    print(
        f"BLOCKED (banned phrase): {len(hits)} occurrence(s) in {path}\n"
        f"{lines}{more}\n"
        "\n"
        "Rewrite using the literal phrase on the right. Do not reword around the\n"
        "pattern -- a synonym for the same figure is the same defect.\n"
        "\n"
        "Mannered prose (Nick, 2026-09-03): metaphor and flourish substituted for\n"
        "direct statement. The phrases exist to display the writer, not to convey\n"
        "the idea. They are also imprecise, because a metaphor drags in\n"
        "connotations the writer did not choose. When a literal phrase is\n"
        "available, use it.\n"
        "\n"
        "SCOPE OF THIS CHECK: it matches the enumerated phrases in\n"
        f"{TABLE_PATH.name} and nothing else. It is NOT a mannered-prose\n"
        "detector; manneredness is a semantic property no regex can certify.\n"
        "A clean exit means no LISTED phrase was found, never that the prose is good.\n"
        "\n"
        "Table: tools/mannered-phrases.txt (edit there, not here)\n"
        "Reference: memory/feedback_no_load_bearing_vocabulary.md\n",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
