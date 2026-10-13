#!/usr/bin/env python3
"""
check_sealed_read.py — PreToolUse hook (Read|Grep|Glob|Bash).

Defense-in-depth for the sealed vault. `data/project-background/` is SEALED. Since
2026-10-13 it lives OUTSIDE the repository tree, in a sibling directory named after the
repo: `<repo>-sealed/data/project-background/`. A recursive read of the repo or of its
`data/` therefore no longer reaches it. Both that sibling (anything in it) and the old
in-tree path are guarded. Its
contents must never appear in any external-facing artifact (CVs, cover letters,
recruiter prep, voice exports, networking notes). The seal is already enforced by
a CLAUDE.md hard rule, by .gitignore (it can't reach the public repo), and by a
`sealed: true` header on every file. This hook adds a fourth layer: it BLOCKS a
tool call that reads under the sealed directory, so sealed content cannot be
pulled into context (and from there into output) without a deliberate override.

Why BLOCK, not WARN: per tools/HOOK_AUTHORING.md, a PreToolUse exit-0+stderr "warn"
is invisible to Claude Code. A visible signal must be exit 2. To keep intentional
maintenance workable there is an explicit override:
  - session-wide:   set SEAL_READ_OK=1 in the environment Claude Code runs in, or
  - one Bash call:  prefix the READING command with SEAL_READ_OK=1, or put
                    `export SEAL_READ_OK=1` before the reads in the call
The prefix covers the command it sits on, and the export covers what comes after it.
Neither authorizes a read that spells the name earlier in the call or beside it.

A Bash command is blocked when it READS the directory, not when it only NAMES it.
The command is split with tools/shell_tokens.py and every place the name appears is
classified. The default is "read": the name passes only in a comment, in text handed
on standard input to a command that treats it as text, and in the positions listed
under `_mentions` (a commit message, echoed prose, a prose argument to a repo tool, a
search pattern, an exclusion), and only when nothing downstream turns that text into
a read. A passing mention adds no read: the same command with the name removed would
open the same files.

WHAT THIS DOES NOT STOP
    A read that never spells the name: a recursive search of a directory ABOVE the
    repo (`grep -r term ..`), a glob such as `../*-seal*`, or a name assembled from
    variables. Those reach the directory and pass this hook. A recursive search of the
    repo root or of `data/` used to be on this list; moving the directory out of the
    tree closed it. A copy put back inside the tree reopens it.

Exit 2 = block. Any other path = allow. Fails OPEN on a payload it cannot read; a Bash
command that names the directory and cannot be parsed is BLOCKED.
Wired via .claude/settings.json PreToolUse on Read|Grep|Glob|Bash.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402
from shell_tokens import simple_commands  # noqa: E402
from pathlib import Path

SEALED_REL = "data/project-background"
SEALED_NAME = "project-background"


def _root() -> Path:
    env = os.environ.get("SEAL_REPO_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parent.parent


# The sibling directory that holds the sealed tree: the repo's own name plus this
# suffix, beside the repo. tools/backup-data.sh derives the same path.
SEALED_HOME_SUFFIX = "-sealed"
SEALED_HOME = _root().name + SEALED_HOME_SUFFIX

# The directory's own name as a whole path component, in any case (this volume is
# case-insensitive). Matched after quote and backslash removal, so a quote or a
# backslash spliced into the name does not hide it. The `data/` parent is not required:
# `cd data && cat project-background/x.md` reads the same files. The sibling home's
# name counts the same way: everything in it is sealed.
_SEALED_RE = re.compile(
    r"(?<![\w-])(?:project-background|%s)(?![\w-])(?!\.\w)" % re.escape(SEALED_HOME), re.I)
_SHELL_QUOTING = re.compile(r"""["'\\]""")

# The inline override, as a whole word. It counts only as an assignment in front of the
# command that reads, or as `export SEAL_READ_OK=1` opening the call. A substring test
# accepted it inside a comment, a quoted string, a heredoc body or a longer word; a
# position test accepted it after the read, or in a branch that never ran.
OVERRIDE_WORD = "SEAL_READ_OK=1"


def _hits(text: str) -> int:
    return len(_SEALED_RE.findall(_SHELL_QUOTING.sub("", text))) if text else 0


def _names_sealed(text: str) -> bool:
    """Does this command text or glob spell the sealed directory, in any form?"""
    return _hits(text) > 0


# --- Bash: is the name read, or only mentioned? -------------------------------------

_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
# Words that precede the command a shell runs and are not the command itself.
_LEADERS = {"then", "do", "else", "elif", "if", "while", "until", "{", "!", "time",
            "env", "command", "nohup"}
# Their arguments are printed, never opened.
_PRINTERS = {"echo", "printf"}
# Commands that treat standard input as text and do not run it or open what it names.
_STDIN_IS_TEXT = {"cat", "tee", "git", "gh", "grep", "egrep", "fgrep", "rg", "wc",
                  "head", "tail", "sort", "uniq", "tr", "cut", "pbcopy", "jq", "nl",
                  "fold", "column"}
_GREPS = {"grep", "egrep", "fgrep", "rg"}
_EXCLUDERS = _GREPS | {"rsync", "tar", "diff", "du"}
_EXCLUDE_FLAGS = ("--exclude-dir", "--exclude")
_RG_GLOB_FLAGS = ("-g", "--glob", "--iglob")
# Options whose value is the NEXT word. Kept to the ones every grep and rg agree on:
# skipping a word that is not a value would make a path look like the pattern.
_GREP_VALUE_OPTS = {"-m", "-A", "-B", "-C", "--include", "--exclude", "--exclude-dir"}
_RG_VALUE_OPTS = {"-g", "-t", "-T", "--glob", "--iglob", "--type", "--type-not"}
_FIND_PATH_TESTS = {"-path", "-ipath", "-wholename", "-iwholename"}
_FIND_NAME_TESTS = _FIND_PATH_TESTS | {"-name", "-iname"}
_INTERPRETER = re.compile(r"python(?:\d+(?:\.\d+)?)?$")
_PY_VALUE_OPTS = {"-W", "-X"}
_REPO_TOOL = re.compile(r"(?:^|/)tools/[\w.-]+\.py$")
_GIT_VALUE_OPTS = {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}
_STDIN_OPS = ("<<", "<<-", "<<<")


def _head_index(words: list) -> int:
    i = 0
    while i < len(words) and (words[i] in _LEADERS or _ASSIGNMENT.match(words[i])):
        i += 1
    return i


def _repo_tool_index(words: list, h: int):
    """Index of the script word when the command runs a `tools/*.py` script, directly
    or through python. None for `python -c`, `python -m`, `python -` and bare python."""
    if h >= len(words):
        return None
    if _REPO_TOOL.search(words[h]):
        return h
    if not _INTERPRETER.match(os.path.basename(words[h])):
        return None
    j = h + 1
    while j < len(words):
        w = words[j]
        if w in _PY_VALUE_OPTS:
            j += 2
        elif w.startswith("-"):
            if w == "-" or not w.startswith("--") and ("c" in w or "m" in w):
                return None
            j += 1
        else:
            return j if _REPO_TOOL.search(w) else None
    return None


def _is_a_sealed_path(word: str) -> bool:
    """Read as ONE path, does this word point under the sealed directory?"""
    parts = [p.lower() for p in os.path.normpath(word).split("/")]
    return SEALED_HOME.lower() in parts or any(
        p == SEALED_NAME and (k == 0 or parts[k - 1] == "data")
        for k, p in enumerate(parts))


def _is_prose(word: str, quoted: bool) -> bool:
    """A quoted argument in which the name sits mid-sentence. A program handed this as
    one path opens something else: the path component before the name holds the words
    that came before it."""
    if not quoted or _is_a_sealed_path(word):
        return False
    for m in _SEALED_RE.finditer(word):
        start = m.start()
        while start > 0 and not word[start - 1].isspace():
            start -= 1
        if start == 0:
            return False       # the run of path characters holding the name opens the word
    return True


def _git_subcommand(words: list, h: int) -> str:
    j = h + 1
    while j < len(words):
        if words[j] in _GIT_VALUE_OPTS:
            j += 2
        elif words[j].startswith("-"):
            j += 1
        else:
            return words[j]
    return ""


def _grep_layout(words: list, h: int, head: str):
    """(indexes of the words searched FOR, the words searched IN, recursive?). With -e
    or -f the first positional word is a path, so it is a target and not a pattern."""
    out, explicit, first, j, opts = set(), False, None, h + 1, True
    positional, recursive = [], head == "rg"
    while j < len(words):
        w = words[j]
        if opts and w == "--":
            opts = False
        elif opts and w.startswith("--"):
            if w == "--regexp":
                explicit = True
                out.add(j + 1)
                j += 1
            elif w.startswith("--regexp="):
                explicit = True
                out.add(j)
            elif w == "--file" or w.startswith("--file="):
                explicit = True
                j += w == "--file"
            elif w in _GREP_VALUE_OPTS or (head == "rg" and w in _RG_VALUE_OPTS):
                j += 1
            elif w in ("--recursive", "--dereference-recursive"):
                recursive = True
        elif opts and w.startswith("-") and len(w) > 1:
            recursive = recursive or "r" in w[1:] or "R" in w[1:]
            if "e" in w[1:] or "f" in w[1:]:
                explicit = True
                if w.endswith("e"):
                    out.add(j + 1)
                if w[-1] in "ef":
                    j += 1
            elif w in _GREP_VALUE_OPTS or (head == "rg" and w in _RG_VALUE_OPTS):
                j += 1
        else:
            positional.append(j)
            first = j if first is None else first
        j += 1
    if not explicit and first is not None:
        out.add(first)
    return out, [words[k] for k in positional if k not in out], recursive


def _contains_sealed(target: str) -> bool:
    """Is this search target the sealed directory, or a directory above it?"""
    if "$" in target or "`" in target:
        return True            # not a fixed path; assume the worst
    wild = re.search(r"[*?\[]", target)
    if wild:                   # a glob matches at or below the directory it starts in
        target = os.path.dirname(target[:wild.start()]) or "."
    root = str(_root())
    path = os.path.normpath(os.path.join(root, os.path.expanduser(target)))
    sealed = os.path.join(root, SEALED_REL)
    return path == "/" or (sealed + "/").lower().startswith(path.rstrip("/").lower() + "/")


def _search_reaches_sealed(words: list, h: int, head: str, piped: bool) -> bool:
    """A search FOR the name must not also search IN the directory. Without this,
    `grep -r <name> .` passed as a mention while reading every file under the root."""
    _, targets, recursive = _grep_layout(words, h, head)
    if not targets:
        return recursive and not piped      # no target: the current directory
    return any(_contains_sealed(t) for t in targets)


_WHOLE_DIRECTORY = re.compile(r"project-background(?:/\*{0,2}|\*)?$", re.I)


def _find_excludes(words: list, i: int, h: int) -> bool:
    """words[i] is a `-path` value. True when find is told to leave the WHOLE directory
    out of its results: negated, or pruned (alone or inside a group of alternatives).
    A negation still walks the directory; it prints nothing from it."""
    if words[i - 1] not in _FIND_PATH_TESTS or not _WHOLE_DIRECTORY.search(words[i]):
        return False
    if i - 2 > h and words[i - 2] in ("-not", "!"):
        return True
    j = i + 1
    while j < len(words):
        if words[j] in ("-o", "-or", ")"):
            j += 1
        elif words[j] in _FIND_NAME_TESTS and j + 1 < len(words):
            j += 2
        else:
            break
    return j < len(words) and words[j] == "-prune"


def _mentions(words: list, quoted: list, h: int, piped: bool = False) -> dict:
    """{word index: kind} for each word that names the directory without reading it.
    Kinds: "prose" and "pattern" put the name on standard output or leave it in text;
    "message" and "exclusion" do not."""
    out = {}
    if h >= len(words):
        return out
    head = os.path.basename(words[h])
    named = [i for i in range(h + 1, len(words)) if _names_sealed(words[i])]
    tool = _repo_tool_index(words, h)
    patterns = set()
    if head in _GREPS and not _search_reaches_sealed(words, h, head, piped):
        patterns = _grep_layout(words, h, head)[0]
    committing = head == "git" and _git_subcommand(words, h) == "commit"
    for i in named:
        w, prev = words[i], words[i - 1]
        if head in _PRINTERS:
            if quoted[i] and re.search(r"\s", w):
                out[i] = "prose"
        elif committing:
            if re.fullmatch(r"-[A-Za-z]*m|--message", prev) or w.startswith("--message="):
                out[i] = "message"
        elif tool is not None:
            if i > tool and _is_prose(w, quoted[i]):
                out[i] = "prose"
        elif head == "find":
            if _find_excludes(words, i, h):
                out[i] = "exclusion"
        elif head in _EXCLUDERS:
            if w.startswith(tuple(f + "=" for f in _EXCLUDE_FLAGS)) or prev in _EXCLUDE_FLAGS:
                out[i] = "exclusion"
            elif head == "rg" and (prev in _RG_GLOB_FLAGS and w.startswith("!")
                                   or re.match(r"--i?glob=!", w)):
                out[i] = "exclusion"
            elif i in patterns:
                out[i] = "pattern"
    return out


def _stdin_names_sealed(c) -> bool:
    return any(_names_sealed(r.target if r.body is None else r.body)
               for r in c.redirections if r.op.lstrip("0123456789") in _STDIN_OPS)


def _passes_the_name_on(c) -> bool:
    """Can this command's output carry the name to whatever it is piped into?"""
    h = _head_index(c.words)
    if OVERRIDE_WORD in c.words[:h]:
        return False           # an overridden read may feed the rest of its pipeline
    kinds = _mentions(c.words, c.quoted, h)     # only exclusions matter here
    if any(_names_sealed(w) and kinds.get(i) != "exclusion" for i, w in enumerate(c.words)):
        return True
    return _stdin_names_sealed(c) or (c.fed_by is not None and _passes_the_name_on(c.fed_by))


def _command_reads(c) -> bool:
    words = c.words
    h = _head_index(words)
    if any(_names_sealed(w) for w in words[:h + 1]):
        return True            # an assignment, or the command itself, holds the name
    if any(_names_sealed(r.target) for r in c.redirections
           if r.op.lstrip("0123456789") not in _STDIN_OPS):
        return True
    head = os.path.basename(words[h]) if h < len(words) else ""
    text_only = head in _STDIN_IS_TEXT or _repo_tool_index(words, h) is not None
    if not text_only and (_stdin_names_sealed(c)
                          or (c.fed_by is not None and _passes_the_name_on(c.fed_by))):
        return True            # a shell, an interpreter, xargs or a loop is fed the name
    kinds = _mentions(words, c.quoted, h, c.fed_by is not None)
    return any(_names_sealed(w) and i not in kinds for i, w in enumerate(words) if i > h)


# Commands that only carry the name forward (into the working directory or a variable)
# and open nothing themselves. What follows them cannot be checked word by word.
_CARRIERS = {"cd", "pushd", "export", "local", "declare", "typeset", "readonly"}


def _overrides(c) -> bool:
    return OVERRIDE_WORD in c.words[:_head_index(c.words)]


def _exports_override(c) -> bool:
    return c.words[:1] == ["export"] and OVERRIDE_WORD in c.words[1:]


def _carries(c) -> bool:
    h = _head_index(c.words)
    return h == len(c.words) or c.words[h] in _CARRIERS


def _parse(command: str, depth: int):
    try:
        return simple_commands(command) if depth <= 8 else None
    except Exception:
        return None


def _any_override(cmds, depth: int = 0) -> bool:
    return any(_overrides(c) or _exports_override(c)
               or any(_any_override(_parse(s, depth + 1) or [], depth + 1) for s in c.subs)
               for c in cmds)


def _bash_reads_sealed(command: str, depth: int = 0, intent: bool = False) -> bool:
    """True when some command in this call reads the directory without an override.

    A command that spells the name needs the override in front of IT, or an
    `export SEAL_READ_OK=1` command earlier in the call. The one exception is a carrier
    (`cd <dir>`, `VAR=<dir>`): it passes when some command of the call is overridden,
    because the reads that follow it do not spell the name at all.
    """
    cmds = _parse(command, depth)
    if cmds is None:
        return _names_sealed(command)   # it cannot be parsed: naming the directory blocks
    if depth == 0:
        intent = _any_override(cmds)
    for c in cmds:
        if depth == 0 and _exports_override(c):
            return False       # everything after it in this call is covered
        if _overrides(c):
            continue
        if not (intent and _carries(c)) and _command_reads(c):
            return True
        if any(_bash_reads_sealed(s, depth + 1, intent) for s in c.subs):
            return True
    return False


def _under_sealed(root: Path, p: str) -> bool:
    """Is p the sealed directory or a path in it, at the old in-tree place or anywhere
    in the sibling home?"""
    p = os.path.join(str(root), os.path.expanduser(p))   # an absolute p stays as it is
    # Resolved on both sides, so a symlink into the sealed tree, or a second spelling of
    # the same directory (/var and /private/var), is judged by where it leads.
    here = os.path.realpath(str(root))
    rel = os.path.relpath(os.path.realpath(p), here).replace(os.sep, "/").lower()
    home = "../" + (os.path.basename(here) + SEALED_HOME_SUFFIX).lower()
    return (rel == SEALED_REL or rel.startswith(SEALED_REL + "/")
            or rel == home or rel.startswith(home + "/"))


def _overridden() -> bool:
    """The session-wide override. The per-command one is read in _bash_reads_sealed."""
    return os.environ.get("SEAL_READ_OK") == "1"


def main() -> None:
    p = read_payload()
    if not p.ok:
        return
    data = p.data

    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    root = _root()

    targets = []
    if tool == "Read":
        targets = [ti.get("file_path", "")]
    elif tool in ("Grep", "Glob"):
        # A glob that points into the sealed directory reads it as surely as `path`
        # does. Grep's `pattern` is the text searched FOR and is not a target.
        base = ti.get("path", "") or ""
        glob = (ti.get("pattern", "") if tool == "Glob" else ti.get("glob", "")) or ""
        targets = [base]
        if _names_sealed(glob) or _names_sealed(os.path.join(base, glob)):
            targets = [SEALED_REL]
    elif tool == "Bash":
        command = ti.get("command", "") or ""
        if _bash_reads_sealed(command):
            targets = [SEALED_REL]
    else:
        return

    hit = None
    for t in targets:
        if _under_sealed(root, t):
            hit = t
            break

    if not hit or _overridden():
        return

    msg = [
        f"BLOCKED: this {tool} targets the SEALED directory {SEALED_REL}/ "
        f"(kept outside the repo, in ../{SEALED_HOME}/).",
        "",
        "Sealed content (Zuora/McKinsey raw materials, personal captures) must never",
        "enter context that could reach an external-facing artifact (CV, cover letter,",
        "recruiter prep, voice export, networking note).",
        "",
        "If this is intentional sealed-file maintenance, override:",
        "  - one command:    prefix the reading command with  SEAL_READ_OK=1",
        "  - rest of a call: put  export SEAL_READ_OK=1  before the reads",
        "",
        "If this block is wrong, log it:",
        '  PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append check_sealed_read.py "FP: <what>"',
    ]
    print("\n".join(msg), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
