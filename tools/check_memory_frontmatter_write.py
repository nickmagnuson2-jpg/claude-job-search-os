#!/usr/bin/env python3
"""check_memory_frontmatter_write.py -- PreToolUse hook (Write|Edit|MultiEdit, and Bash).

BLOCKS a write to a memory rule file (the live memory tier of this repo's main checkout)
that would leave its frontmatter invalid or change its state outside the sanctioned
writers. Built 2026-11-22 as B2 of the learning-loop build (org job-search-68).
WIRING: this hook runs only once .claude/settings.json registers it (PreToolUse,
Write|Edit|MultiEdit|Bash); tests/scripts/test_b2_hooks_registered.py checks that.

WHY. The state matrix (output/memory-hygiene/112226-state-matrix.md) gives every memory
state one typed field and one validating writer, memory_frontmatter.write(). A replay of
every session transcript (output/memory-hygiene/112226-b2-replay-corpus.jsonl) found the
fields written five ways, none validated: Write and Edit, heredoc creates and appends,
`sed -i`, and inline Python. Files whose frontmatter no strict reader can parse were
written exactly that way.

WHAT IT CHECKS (policy; the reader and validator are memory_frontmatter.check_text):
  Write / Edit / MultiEdit   the file as it would be after the call (an Edit is applied to
                             the current text) must pass check_text(new, current). An
                             existing file's `occurrences` may not change here: a fire is
                             recorded with `memory_write.py bump`, the only path that
                             changes a count (memory_frontmatter.record_fire). A create may
                             set the initial value.
  Bash                       per write target in a memory rule file:
    heredoc / echo / printf create (`>`)  validated like a Write.
    append (`>>`)                         allowed unless the text opens a frontmatter block
                                          or sets a state key on a line of its own.
    anything else                         blocked: `sed -i`, `tee`, `dd`, a redirect from a
                                          program, cp/mv INTO the folder, and an interpreter
                                          (python, node, perl, ruby) whose script names a
                                          memory rule file and contains a write call.
                                          Route: memory_write.py set / bump, or Edit.
  A script FILE under this repo's tools/ is reviewed code and is not judged here.

SCOPE. `*.md` directly in the live memory folder, except MEMORY.md, index-*, archive-*
and promotion-backlog*, which carry no rule frontmatter. Reads, `ln`, and copies OUT of
the folder are never blocked. tests/ fixtures and other folders are out of scope.

ADVERSARY. The agent. It can edit this file or its settings entry (both behind
check_guard_edit_approval), assemble a path the parser cannot see, or run a script
elsewhere that writes the file. This is a TRIPWIRE for the accidental case, not a
boundary. Measured on the replay: see the B2 entries in data/workstreams/memory-hygiene.md.

Fail policy: an unreadable payload exits 0 (open). A payload truncated at the intake cap
exits 2 when its raw text names a memory rule path, because the guard could not see it.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload, claude_project_dir  # noqa: E402
from vault_paths import main_checkout  # noqa: E402
from shell_tokens import simple_commands, tokenize  # noqa: E402
from check_public_pii import extract_write_targets, command_assignments, _expand_vars  # noqa: E402
import memory_frontmatter as mf  # noqa: E402

TOOLS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
# MEMORY_HOOK_REPO: tests and the staged copy only (the installed hook lives in tools/).
REPO_ROOT = Path(os.environ.get("MEMORY_HOOK_REPO") or main_checkout(TOOLS_DIR.parent))
HOME = os.path.expanduser("~")

NAME = "check_memory_frontmatter_write"
ROUTE = ("route: `memory_write.py set <file> key=value` for a state field, "
         "`memory_write.py bump <file> --fire \"<what happened, YYYY-MM-DD>\"` for a fire, "
         "or the Edit/Write tool for prose")

STATE_KEYS = ("promoted", "occurrences", "reopen_gate", "reopen_at", "exit_path", "terminal",
              "last_cited", "promoted_date", "exit1_gate", "exit1_evidence", "skill_file",
              "skill_step", "detector_signature")
_STATE_LINE = re.compile(r"^\s*(" + "|".join(STATE_KEYS) + r")\s*:", re.M)
_NOT_RULE_FILE = re.compile(r"^(MEMORY\.md|index-.*|archive-.*|promotion-backlog.*)$")
INTERPRETERS = frozenset({"python", "python3", "node", "perl", "ruby"})
_INTERP_VERSIONED = re.compile(r"^python3\.\d+$")
# Writes whose destination a regex cannot see: always treated as touching the file.
_OPAQUE_WRITE = re.compile(r"os\.replace|os\.rename|shutil\.(copy|move)|\.rename\(|"
                           r"writeFileSync|writeFile\(|appendFileSync")
_OPEN_FOR_WRITE = re.compile(r"open\(\s*([^,()]*(?:\([^()]*\))?[^,()]*)\s*,\s*['\"][wax+]")
_WRITE_METHOD = re.compile(r"((?:Path\(\s*['\"][^'\"]*['\"]\s*\))|[\w.\[\]]+)\.write_(?:text|bytes)\(")
_NAME_ASSIGN = re.compile(r"^[ \t]*([A-Za-z_]\w*)[ \t]*=[ \t]*(['\"])([^'\"\n]+)\2[ \t]*$", re.M)
_LITERAL = re.compile(r"\s*(?:Path\(\s*)?(['\"])([^'\"]+)\1\s*\)?\s*")
_COPIERS = frozenset({"cp", "mv", "install", "rsync"})
_COMPOUND_PREFIX = frozenset({"do", "then", "else", "elif", "!", "{", "time"})


def memory_dir() -> Path:
    override = os.environ.get("MEMORY_HOOK_DIR")          # tests only
    return Path(override) if override else claude_project_dir(REPO_ROOT) / "memory"


def _norm(path: str, cwd: str = "") -> str:
    p = path.replace("\\", "/")
    if p.startswith("~/"):
        p = HOME + p[1:]
    else:                            # `$HOME/...` arrives already expanded by _expand_vars
        p = os.path.join(cwd, p)     # an absolute p, or an empty cwd, leaves p as it is
    return os.path.normpath(p) if p else p


def in_scope(path: str, cwd: str = "") -> bool:
    """A memory rule file: an .md directly in the live memory folder, not an index."""
    p = Path(_norm(path, cwd))      # "" normalises to "", whose parent is "."
    return (p.parent == memory_dir() and p.suffix == ".md"
            and not _NOT_RULE_FILE.match(p.name))


def _names_scope(text: str) -> bool:
    """The text names a memory rule file by path (absolute, ~ or $HOME form)."""
    md = str(memory_dir())
    tail = md[len(HOME):] if md.startswith(HOME) else md
    pat = (re.escape(md) + r"/([\w.\-]+\.md)|(?:~|\$HOME|\$\{HOME\})"
           + re.escape(tail) + r"/([\w.\-]+\.md)")
    return any(not _NOT_RULE_FILE.match(m.group(1) or m.group(2))
               for m in re.finditer(pat, text))


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _occurrences(text: str | None):
    if text is None:
        return None
    fields, defect = mf.read(text)
    return None if defect else fields.get("occurrences")


def judge_text(path: Path, new: str) -> list[str]:
    """Reasons the file must not become `new`; [] when it may."""
    old = _read(path)
    reasons = []
    # The count's TYPE (a create's `occurrences: two`, Grok confirmation F2) is checked by
    # learning_state.validate_fields through mf.check_text below: one definition (67539428).
    before, after = _occurrences(old), _occurrences(new)
    if before is not None and after != before:
        reasons.append(f"occurrences: {before} -> {after} is a count change; record "
                       f"a fire with `memory_write.py bump {path.name} --fire \"...\"`")
    reasons += mf.check_text(new, old, memory_dir=memory_dir(), repo_root=REPO_ROOT)
    return reasons


def apply_edits(text: str, edits: list) -> str | None:
    """The text after Edit/MultiEdit replacements; None when an old_string is absent (the
    tool refuses that call itself, so there is nothing for this hook to judge)."""
    for e in edits:
        old, new = e.get("old_string", ""), e.get("new_string", "")
        if not old or old not in text:
            return None
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


@dataclass
class Action:
    target: str
    shape: str            # create | append | blocked:<why>
    content: str = ""


def script_writes(inline: str, cwd: str, scope, names_scope) -> bool:
    """True when an interpreter script may write a file `scope` accepts. A write whose
    target is a string literal is judged by that literal; a write through a variable, a
    rename or a copy cannot be resolved here and counts as a write to the file (the script
    already names it). A script that only reads the file and writes a literal path
    elsewhere is allowed."""
    if _OPAQUE_WRITE.search(inline):
        return True
    targets = _OPEN_FOR_WRITE.findall(inline) + _WRITE_METHOD.findall(inline)
    if not targets:
        return bool(re.search(r"\.write\(", inline))
    # A name bound once to a string literal is judged by that literal (Grok r1 F3: a copy
    # out through `out = '/tmp/x'` was blocked). A name bound more than once, or not to a
    # literal, stays opaque.
    binds: dict[str, list[str]] = {}
    for m in _NAME_ASSIGN.finditer(inline):
        binds.setdefault(m.group(1), []).append(m.group(3))
    paths = []
    for t in targets:
        lit = _LITERAL.fullmatch(t)
        if lit:
            paths.append(lit.group(2))
        elif len(binds.get(t.strip(), [])) == 1 and not re.search(
                r"\b" + re.escape(t.strip()) + r"\s*(\+=|\.|\[)", inline):
            paths.append(binds[t.strip()][0])
        else:
            return True
    return any(scope(_norm(p, cwd)) or names_scope(p) for p in paths)


_PRINTF_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"'}


def printf_text(words: list[str]) -> str | None:
    """What `printf FORMAT [ARG...]` prints, for the subset this hook can be sure of: %s and
    %% conversions and the \\n \\t \\\\ escapes, the format reused while arguments remain
    (as the shell does). None for anything else (another conversion, an option, a backslash
    escape outside that set): the caller treats the write as opaque (Codex r1 F3)."""
    if not words or words[0].startswith("-"):
        return None
    fmt, args = words[0], list(words[1:])
    pieces, i = [], 0
    while i < len(fmt):
        c = fmt[i]
        if c == "\\":
            nxt = fmt[i + 1:i + 2]
            if nxt not in _PRINTF_ESCAPES:
                return None
            pieces.append(("lit", _PRINTF_ESCAPES[nxt]))
            i += 2
        elif c == "%":
            nxt = fmt[i + 1:i + 2]
            if nxt == "%":
                pieces.append(("lit", "%"))
            elif nxt == "s":
                pieces.append(("arg", ""))
            else:
                return None
            i += 2
        else:
            pieces.append(("lit", c))
            i += 1
    n_args = sum(1 for k, _ in pieces if k == "arg")
    out = []
    while True:
        for kind, text in pieces:
            out.append(text if kind == "lit" else (args.pop(0) if args else ""))
        if not args or n_args == 0:
            break
    return "".join(out)


def _expand(word: str, env: dict) -> list[str]:
    return (_expand_vars(word, env) or [word]) if "$" in word else [word]


def _literal_texts(command: str) -> set[str]:
    """Texts the shell will NOT expand: quoted-delimiter heredoc bodies, and words in which
    every `$` and backtick sits inside a single-quoted span (replay 2026-10-21: a printf
    format mixing '...' and "'" segments, with backticks inside the single quotes)."""
    # A text is literal only if EVERY token carrying it is: `'$V'` and `"$V"` decode to the
    # same text, and the simple-command words keep only the text (Grok confirmation F1).
    literal: dict[str, bool] = {}
    for t in tokenize(command):
        if t.kind == "heredoc":
            this = bool(t.quoted)
        else:   # literal when no `$` or backtick sits outside '...'
            sq = [(a, b) for k, a, b in t.spans if k == "sq"]
            active = [i for i in range(t.start, t.end) if command[i] in "$`"]
            this = all(any(a <= i < b for a, b in sq) for i in active)
        literal[t.text] = literal.get(t.text, True) and this
    return {text for text, ok in literal.items() if ok}


def expanded_content(content: str, literal: bool, env: dict) -> str | None:
    """The text the shell will write. A `$` or backtick outside a literal is expanded from
    the command's own assignments; None when that does not resolve to exactly one text
    (Codex confirmation F1: `V='  promoted: x'; printf '%s\\n' "$V" >> f` was judged on
    the literal `$V`)."""
    if literal or ("$" not in content and "`" not in content):
        return content
    if "`" in content or "$(" in content:
        return None
    values = _expand_vars(content, env)
    return values[0] if len(values) == 1 else None


def bash_actions(command: str, cwd: str, scope, names_scope) -> list[Action]:
    """Every write this command makes to a path `scope` accepts, with its shape.
    `names_scope(text)` says whether script text names such a path."""
    env = command_assignments(command)
    literals = _literal_texts(command)
    out: list[Action] = []
    handled: set[str] = set()
    for sc in simple_commands(command):
        words = list(sc.words)
        while words and words[0] in _COMPOUND_PREFIX:      # `do printf ...`, `then cat ...`
            words.pop(0)
        prog = os.path.basename(words[0]) if words else ""
        body = next((r.body for r in sc.redirections
                     if r.op in ("<<", "<<-") and r.body is not None), None)
        here_str = next((r.target for r in sc.redirections if r.op == "<<<"), None)
        if prog in INTERPRETERS or _INTERP_VERSIONED.match(prog):
            args = words[1:]
            inline = (body or "") + (here_str or "")
            if "-c" in args or "-e" in args:
                inline += " " + " ".join(args)
            else:
                script = next((w for w in args if not w.startswith("-")), None)
                if script:
                    sf = Path(_norm(script, cwd))
                    if sf.parent not in (TOOLS_DIR, REPO_ROOT / "tools"):
                        inline += _read(sf) or ""
            # An argument naming a rule file, or the memory folder itself (a script that
            # joins the folder with a file name it holds: replay 2026-10-14, `python3 - "$M"`).
            arg_hits = any(scope(_norm(x, cwd)) or scope(os.path.join(_norm(x, cwd), "x.md"))
                           for w in args for x in _expand(w, env))
            if inline and (names_scope(inline) or arg_hits) and script_writes(inline, cwd, scope,
                                                                               names_scope):
                out.append(Action("(script)", f"blocked:a {prog} script that writes the file"))
        if prog in _COPIERS and len(words) > 1:
            for d in _expand(words[-1], env):
                nd = _norm(d, cwd)
                into_dir = Path(nd) == memory_dir() and prog != "rsync"
                if scope(nd) or into_dir:
                    out.append(Action(nd, f"blocked:{prog} into the memory folder"))
        for r in sc.redirections:
            if r.op not in (">", ">>", ">|"):
                continue
            for t in _expand(r.target, env):
                nt = _norm(t, cwd)
                if not scope(nt):
                    continue
                handled.add(nt)
                content = None
                if prog == "cat" and body is not None:
                    content = expanded_content(body, body in literals, env)
                elif prog == "echo" and len(words) > 1:
                    args = [w for w in words[1:] if not re.match(r"^-[neE]+$", w)]
                    args = [expanded_content(w, w in literals, env) for w in args]
                    if None not in args:
                        content = " ".join(args).replace("\\n", "\n")
                elif prog == "printf":
                    args = [expanded_content(w, w in literals, env) for w in words[1:]]
                    content = None if None in args else printf_text(args)
                if content is None:
                    out.append(Action(nt, f"blocked:a redirect from `{prog or 'a group'}`"))
                    continue
                out.append(Action(nt, "append" if r.op == ">>" else "create", content))
    for t in extract_write_targets(command):
        nt = _norm(t, cwd)
        if scope(nt) and nt not in handled:
            out.append(Action(nt, "blocked:an in-place writer (sed -i, tee, dd)"))
    return out


def judge_bash(command: str, cwd: str) -> list[str]:
    reasons = []
    for a in bash_actions(command, cwd, in_scope, _names_scope):
        name = Path(a.target).name
        if a.shape.startswith("blocked:"):
            reasons.append(f"{name}: {a.shape[len('blocked:'):]}")
        elif a.shape == "create":
            reasons += [f"{name}: {r}" for r in judge_text(Path(a.target), a.content)]
        elif _STATE_LINE.search(a.content):
            # A `---` line in an append is a markdown divider in the body, not frontmatter
            # (frontmatter exists only at the top of the file): 9 legitimate supplements in
            # the replay open with one. A state-key line is refused because the line-based
            # readers still in use until the cutover would read it as the field.
            reasons.append(f"{name}: an append may not set a state key on a line of its own")
    return reasons


def text_after(tool: str, ti: dict, current: str | None) -> str | None:
    """The file text a Write/Edit/MultiEdit call would leave; None when there is nothing to
    judge (an edit to a missing file, or an old_string the tool will not find)."""
    if tool == "Write":
        return ti.get("content", "")
    edits = ti.get("edits") if tool == "MultiEdit" else [ti]
    return None if current is None else apply_edits(current, edits if isinstance(edits, list) else [])


def judge(p) -> list[str]:
    """Every reason to block this call; [] to allow. One exit, so the mutation key of an
    empty return is not shared between paths (org 72, 2026-11-22)."""
    tool, ti = p.tool_name, p.tool_input
    reasons: list[str] = []
    if tool == "Bash":
        reasons = judge_bash(p.command, p.cwd)
    elif tool in ("Write", "Edit", "MultiEdit") and in_scope(p.file_path, p.cwd):
        target = Path(_norm(p.file_path, p.cwd))
        new = text_after(tool, ti, _read(target))
        if new is not None:
            reasons = [f"{target.name}: {r}" for r in judge_text(target, new)]
    return reasons


def main() -> None:
    p = read_payload()
    if not p.ok:
        if p.truncated and _names_scope(p.raw):
            sys.stderr.write(f"BLOCKED by {NAME}: the payload was cut off at the intake cap, "
                             "so a write to a memory rule file could not be checked\n")
            sys.exit(2)
        sys.exit(0)          # unreadable payload: fail open (policy)
    reasons = judge(p)
    if reasons:
        sys.stderr.write(f"BLOCKED by {NAME}: memory rule frontmatter\n"
                         + "".join(f"  - {r}\n" for r in reasons) + f"  {ROUTE}\n")
        sys.exit(2)
    # Falling off the end exits 0. No explicit sys.exit(0) here: it would share a mutation
    # key with the fail-open exit above (same function, same text), so an allowlist entry
    # for this equivalent mutant would also cover that one (org 72, 2026-11-22).


if __name__ == "__main__":
    main()
