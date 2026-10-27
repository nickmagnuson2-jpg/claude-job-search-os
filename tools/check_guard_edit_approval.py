#!/usr/bin/env python3
"""check_guard_edit_approval.py — PreToolUse hook (Write|Edit|MultiEdit, and Bash).

BLOCKS an edit to guard infrastructure — `tools/check_*.py`, the hook libraries they
decide with (HOOK_LIBRARIES: shell_tokens, hook_runtime, hook_command_lint), and
`.claude/settings.json` — so that changing a
guard is always a deliberate, approved act rather than something that happens in
the same motion as being blocked by one.

WHY THIS EXISTS (2 fires, gate tripped 2026-08-14):
  2026-07-28  `check_email_via_skill.py` blocked an Edit to voice-reference.md.
              The allowlist gap was real; Claude added the entry, then surfaced it
              after. Nick: "keep it but never again."
  2026-08-14  `check_todo_write_kwargs.py` blocked the new `update` subcommand's
              flags. Claude edited the guard in the same turn and reported after,
              reasoning that the guard's CONTRACT had legitimately changed because
              Nick had just approved building `update`. That is a better argument
              than 7/28's "the fix is obviously correct" -- and it is still not
              approval. It is also more dangerous, because a genuinely-changed
              contract makes the edit feel like maintenance rather than
              self-unblocking.

  The mechanical tell both times: editing a `tools/check_*.py` in the same turn
  that one blocked you. Nothing else about the situation matters, which is exactly
  what makes it hook-able.

WHAT THIS HOOK CAN AND CANNOT DO -- read before trusting it:
  It CANNOT detect intent. A legitimate guard build and a self-unblocking edit are
  byte-identical at the tool-call layer. What it does is convert a silent act into
  a deliberate one: the block forces a stop, and the only way past is an override
  that the agent must consciously set, which is the moment to ask Nick instead.

  It is therefore bypassable by the actor it constrains. That is a real weakness
  and it is NOT a reason to skip it: per feedback_llm_self_policing_fails the tier
  ladder is memory < skill < hook < independent reviewer, and this moves the rule
  up one rung from a memory file that had already failed twice. An override used
  without asking is itself a rule violation (see the rule's "How to apply" step 3),
  not a loophole the design endorses.

BLOCK tier (exit 2), per feedback_warn_vs_block_hook_design: a PreToolUse exit-0
WARN is never surfaced by Claude Code, so a WARN here would warn into a void. The
memory file's originally-named WARN target was stale and is explicitly superseded.

FALSE-POSITIVE SURFACE (content hook => PATH SCOPE, per tools/HOOK_AUTHORING.md):
  - `tests/` is excluded. A hook that judges its own fixtures makes the suite
    unrunnable (origin: check_prep_doc_format.py, 2026-08-12).
  - `tests/scripts/test_check_*.py` are tests, not guards. Excluded by the same rule.
  - Docs about hooks (`tools/HOOK_AUTHORING.md`) are not guards. Not matched.
  - Creating a NEW guard is still matched, deliberately. Building a guard is normal
    work, but it is work Nick should know is happening, and the override makes it
    one keystroke rather than a debate.

Override (only after explicit approval in this session):
  GUARD_EDIT_APPROVED=1
  For Bash, put it IN the command (`GUARD_EDIT_APPROVED=1 python3 patch.py`): the hook
  process does not inherit the agent's environment, and the transcript then records
  the approval. An Edit/Write cannot carry it, so an approved edit goes through Bash.

BASH (added 2026-10-01, Nick's approval). Until then a Bash command could rewrite any
guard unchecked, and a scratchpad patch script did exactly that to shell_tokens.py.
See bash_guard_writes for what is covered. It is a heuristic over the command text and
any script file it runs: a path assembled from parts, or a write through a tool under
tools/, is not seen.
"""
import fnmatch
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402

# Libraries a guard decides WITH. Changing one changes every guard built on it, so it
# is guarded like the guards (Nick, 2026-10-01: shell_tokens.py was edited unapproved
# because it was not listed). tests/.../test_every_tools_module_a_guard_imports_is_
# classified fails when a check_*.py imports a tools module in neither set.
HOOK_LIBRARIES = ("shell_tokens", "hook_runtime", "hook_command_lint")
# Imported by a guard but deliberately NOT guarded (Nick's call, 2026-10-01: "just the
# hook libraries"). Each is domain logic or tooling that ordinary work edits.
NOT_HOOK_LIBRARIES = {
    "stage_vocab": "pipeline stage vocabulary; domain logic shared with data tools",
    "artifact_vocab": "artifact naming vocabulary; domain logic",
    "prep_doc_parse": "prep-doc parser shared with the prep skill",
    "proof_domains": "domain list for proof checks; data, edited in ordinary work",
    "slide_check": "slide render checker, also a standalone tool",
    "chrome_runner": "headless Chrome wrapper used by render tools",
    "job_quiesce": "launchd job coordination, not a decision library",
    "mutation_state": "mutation_check bookkeeping, not a decision library",
    "friction_surface": "friction-ledger surface/nature derivation shared with the "
                        "transcript scanners; decides what gets logged, never blocks",
    "build_check": "BUILD-LOG parser and /standup report; its ask_state() is read by "
                   "check_build_log_present and check_draft_artifact, but it is domain "
                   "logic edited with the artifact workflow (2026-10-26)",
    "sent_artifact_check": "Sent-mail scan; check_draft_artifact imports its URL, bio "
                           "and CV rules so the two agree; domain logic (2026-10-26)",
    "gmail_fetch": "Gmail OAuth and fetch tooling, imported via sent_artifact_check",
    "gmail_attachments": "Gmail MIME part walker, imported via sent_artifact_check",
    "slug": "slug helper, imported via the Gmail tooling",
    "vault_paths": "vault path helper, imported via the Gmail tooling",
    "count_report": "reach-line formatter for counted output, imported via build_check "
                    "and sent_artifact_check; formatting, never a decision",
}

# Guard infrastructure. Repo-relative, matched against the tail of the path so an
# absolute path from the tool payload still resolves.
GUARD_PATTERNS = (
    re.compile(r"(^|/)tools/check_[A-Za-z0-9_]+\.py$"),
    re.compile(r"(^|/)tools/(" + "|".join(HOOK_LIBRARIES) + r")\.py$"),
    re.compile(r"(^|/)\.claude/settings(\.local)?\.json$"),
)
# A bare file name, after `cd tools` (`sed -i ... shell_tokens.py`).
_GUARD_BASENAME = re.compile(r"^(check_[A-Za-z0-9_]+|" + "|".join(HOOK_LIBRARIES) + r")\.py$")
# A guard path as a whole string literal in a script ('tools/check_x.py', also with a
# directory before it). A literal, not a mention: a script writing a note whose prose
# names a guard was 2 of the first 6 replay blocks sampled (2026-10-01).
_GUARD_IN_TEXT = re.compile(r"""['"](?:[^'"\s]*/)?(?:tools/(?:check_[A-Za-z0-9_]+|"""
                            + "|".join(HOOK_LIBRARIES)
                            + r""")\.py|\.claude/settings(?:\.local)?\.json)['"]""")
# A call that writes, renames or deletes a file, in Python, Perl, Ruby or Node.
_WRITE_CALL = re.compile(
    r"write_text|write_bytes|\.write\(|writeFileSync|writeFile\(|"
    r"open\([^)]*['\"][wa]b?\+?['\"]|shutil\.(?:copy\w*|move)|"
    r"os\.(?:replace|rename|remove|unlink|truncate)|\.unlink\(|\.rename\(|"
    r"File\.write|unlinkSync|renameSync")
_INTERPRETER = re.compile(r"^(?:python[0-9.]*|perl|ruby|node)$")
_APPROVAL_WORD = "GUARD_EDIT_APPROVED=1"
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_MAX_SCRIPT_BYTES = 2_000_000

# Path scope exclusions. A content hook that judges its own fixtures cannot be tested.
EXCLUDE_PATTERNS = (
    re.compile(r"(^|/)tests/"),
    re.compile(r"(^|/)fixtures?/"),
)

OVERRIDE_ENV = "GUARD_EDIT_APPROVED"

MESSAGE = """BLOCKED: this edits guard infrastructure ({path}).

Changing a guard is the guard owner's call, not the actor's. Two prior fires
(2026-07-28, 2026-08-14) were both "the fix is correct" -- and both were correct,
and both were still the wrong sequence.

If a guard just blocked you and you are here to fix it, STOP and do this instead:
  1. Tell Nick what was blocked, why the hook fired, and whether it is a true
     positive or a genuine gap in the guard's contract.
  2. Name the proposed change.
  3. WAIT for his yes.
  4. Then re-run with {env}=1 prefixed.

A guard's contract legitimately changing (because Nick approved something the
guard does not know about yet) is a reason to ask FASTER, not a reason to skip
asking -- that shape is exactly the 2026-08-14 fire.

If this is a false positive -- you are editing something that is not a guard --
log it so the FP is visible, since PreToolUse blocks are invisible to the
auto-logger:
  PYTHONIOENCODING=utf-8 python3 tools/friction_log.py append \\
      check_guard_edit_approval.py "FP: <what got wrongly blocked>"

Rule: memory/feedback_never_modify_guard_hook_to_unblock_self.md (2 fires)
"""


def is_guarded(path: str) -> bool:
    """True when `path` is guard infrastructure and not a test/fixture."""
    if not path:
        return False
    p = path.replace("\\", "/")
    if any(x.search(p) for x in EXCLUDE_PATTERNS):
        return False
    return any(g.search(p) for g in GUARD_PATTERNS)


_REPO_ROOT = os.path.dirname(_TOOLS_DIR)


def _guard_files() -> list[str]:
    """Repo-relative paths of the guard files that exist."""
    return ([f"tools/{n}" for n in sorted(os.listdir(_TOOLS_DIR)) if _GUARD_BASENAME.match(n)]
            + [".claude/settings.json", ".claude/settings.local.json"])


def _guarded_target(path: str, cwd: str = "", env: dict | None = None) -> bool:
    """A path that reaches a guard file of THIS repo: the file itself, a glob that
    matches one (`rm tools/check_*.py`), a directory holding one (`rm -rf tools`,
    `git restore .`), or a brace form (`mv tools/shell_tokens.py{,.bak}`): Codex and
    Grok reviews of b9cc6a5.

    Copies into another checkout (a scratchpad worktree for a mutation run,
    `cp x $W/tools/check_y.py`) are not guard edits: the replay of 88db514's review
    fixes flagged exactly that. A path that cannot be resolved (an unknown $VAR) is
    judged as written, which blocks."""
    from check_public_pii import _expand_vars
    candidates = (_expand_vars(path, env or {}) or [path]) if "$" in path else [path]
    for c in candidates:
        # A brace expansion (`{,.bak}`, `{a,b}`: a comma inside) expands to the word
        # without it, among others. `{}` is not one: it is find's placeholder, and
        # stripping it named the repo root (replay of this change, 2026-10-01).
        for form in dict.fromkeys([c, re.sub(r"\{[^{}]*,[^{}]*\}", "", c)]):
            if form and _names_a_guard(form, cwd):
                return True
    return False


def _names_a_guard(c: str, cwd: str) -> bool:
    if "$" in c:                                  # unresolved: judged as written
        return is_guarded(c)
    full = os.path.abspath(os.path.join(cwd or os.getcwd(), os.path.expanduser(c)))
    if full != _REPO_ROOT and not full.startswith(_REPO_ROOT + os.sep):
        return False                              # another checkout's copy
    rel = os.path.relpath(full, _REPO_ROOT)
    if any(ch in rel for ch in "*?["):
        return any(fnmatch.fnmatchcase(f, rel) for f in _guard_files())
    if os.path.isdir(full):
        return rel == "." or any(f.startswith(rel + "/") for f in _guard_files())
    return is_guarded(rel) or ("/" not in c and bool(_GUARD_BASENAME.match(c)))


def _non_options(args: list[str]) -> list[str]:
    return [a for a in args if a and not a.startswith("-")]


def approved_in_command(command: str) -> bool:
    """True if a simple command in `command` ASSIGNS GUARD_EDIT_APPROVED=1, as a
    prefix (`GUARD_EDIT_APPROVED=1 python3 patch.py`) or via export. The text alone
    is not approval: `echo GUARD_EDIT_APPROVED=1` and a heredoc body carrying it
    approved before, and so did `=0` (Codex and Grok reviews of 88db514)."""
    from shell_tokens import simple_commands
    for cmd in simple_commands(command):
        words = cmd.words
        if words[:1] == ["export"]:
            words = words[1:]
        for w in words:
            if w == _APPROVAL_WORD:
                return True
            if not re.match(r"[A-Za-z_][A-Za-z0-9_]*=", w):
                break
    return False


def _copy_like_paths(name: str, rest: list[str], cwd: str) -> list[str]:
    """Paths cp/mv/install/ln change: the destination (DIR/<source name> when it is a
    directory or named by -t), and for mv the sources too, since they are removed
    (Codex and Grok reviews of 88db514: `cp -t tools x`, `mv tools/shell_tokens.py /tmp`).
    """
    target_dir = None
    operands: list[str] = []
    k = 0
    while k < len(rest):
        a = rest[k]
        if a in ("-t", "--target-directory") and k + 1 < len(rest):
            target_dir = rest[k + 1]
            k += 2
            continue
        if a.startswith("--target-directory="):
            target_dir = a.split("=", 1)[1]
        elif a.startswith("-t") and len(a) > 2:
            target_dir = a[2:]
        elif a and not a.startswith("-"):
            operands.append(a)
        k += 1
    if target_dir is not None:
        sources, dests = operands, [target_dir]
    else:
        sources, dests = operands[:-1], operands[-1:]
    out: list[str] = []
    for d in dests:
        is_dir = target_dir is not None or d.endswith("/") or os.path.isdir(
            os.path.join(cwd, os.path.expanduser(d)))
        out += [os.path.join(d, os.path.basename(src)) for src in sources] if is_dir else [d]
    if name in ("mv", "gmv"):
        out += sources
    return out


def _shell_script_files(rest: list[str]) -> list[str]:
    """Words after a shell that may be script files it runs (`bash patch.sh`): every
    non-option word, unless -c or -s makes them arguments. Over-approximates on
    purpose (`bash -o pipefail x.sh` also tries `pipefail`, which does not exist and
    reads as empty); _read_script skips repo tools and missing files."""
    for a in rest:
        if a.startswith("-") and not a.startswith("--") and ("c" in a[1:] or "s" in a[1:]):
            return []
    return [a for a in rest if not a.startswith(("-", "+"))]


def _script_rewrites_a_guard(text: str, cwd: str = ".", argv: list[str] | None = None) -> bool:
    """An interpreter script that names a guard path and calls a write. A heuristic:
    it cannot see a path built from parts (`Path("tools") / name`). For a Python script
    (argv given) the write targets are then resolved, and a script whose writes all
    land outside the guards passes (`_python_rewrites_a_guard`)."""
    if not (_GUARD_IN_TEXT.search(text) and _WRITE_CALL.search(text)):
        return False
    return argv is None or _python_rewrites_a_guard(text, cwd, argv)


# --- Python scripts: where do the writes go? ----------------------------------------
#
# The heuristic above blocks a script that NAMES a guard and WRITES anything, so a script
# that reads, hashes or runs a guard and writes its result elsewhere was blocked (friction
# ledger: 3 fires, script-patch rung, 2026-10-21). For Python the hook now finds every
# write the script makes and folds its target path through literals, simple variables,
# os.path.join, Path(...) and sys.argv (from the Bash words). Those are the only forms
# the 15 replayed false positives this change clears needed: f-strings, `+`, .parent,
# joinpath and function parameters were switched off one at a time, cleared none of the
# 15 by themselves, and were removed (each was a way to mis-fold a path, and an unfolded
# path just blocks; scripts using them stay blocked, as before). The script still blocks when any target may be a
# guard: it resolves to one, or its last component is unknown or guard-shaped (an
# unknown part may be absolute or hold '..', so a known directory proves nothing). It also blocks when it
# cannot be analysed: it does not parse; it imports a module outside _PY_SAFE_MODULES
# (io.FileIO, fileinput's in-place mode and the like write files this list does not
# name, so the analysis applies only where its write list is complete) or uses `import *`;
# it reaches code or names dynamically (exec, getattr, sys.modules, a dunder attribute, a
# module held in a variable); it changes the working directory; or it refers to a write
# function without calling it (`w = os.replace`), passes arguments it cannot see
# (`*args`, `**kw`), or rewrites sys.argv.
#
# CONTRACT. Like the rest of this hook, this is a heuristic, not a proof: it is built
# so that a script the old heuristic examined still blocks when it edits a guard the
# ORDINARY way (open, write_text, shutil, os.replace, a command run through subprocess,
# a link), and it closes the cheap evasions
# reviews found. It is not proof against a script written to evade it (the hook already
# misses `Path("tools") / name`), consistent with WHAT THIS HOOK CAN AND CANNOT DO above.
# It turns blocks into allows, with one exception: `python3 -W x patch.py` now reads
# patch.py, which the old option loop mistook for `x`.
#
# NOT COVERED, pre-existing: a script reaches this analysis only when the old
# pre-filter fires (a guard path as a whole quoted string AND a _WRITE_CALL token), so
# `subprocess.run(['rm', G])` or `os.system("sed -i ... G")` alone is never examined.
# Widening the pre-filter to every write the analysis knows was measured on 3,472
# replayed commands (2026-10-21): 63 newly blocked, none of which wrote a guard. That
# trade is a separate decision, parked as a todo.

_UNKNOWN = "\0"
_GUARD_HIT = "\0guard"            # a command that edits a guard; never a real path
_READ_ONLY_COMMANDS = {"ls", "cat", "grep", "rg", "head", "tail", "wc", "echo", "printf",
                       "diff", "stat", "du", "file", "which", "pwd", "date", "true"}
_READ_ONLY_GIT = {"status", "diff", "log", "show", "rev-parse", "ls-files", "ls-remote",
                  "branch", "remote", "blame", "grep"}
# Modules whose every file-writing call the analysis below knows. Anything else imported
# leaves the script to the heuristic, which blocks.
_PY_SAFE_MODULES = {
    "os", "os.path", "posixpath", "shutil", "pathlib", "subprocess", "sys", "json", "re",
    "hashlib", "datetime", "time", "collections", "itertools", "functools", "math",
    "textwrap", "difflib", "csv", "glob", "fnmatch", "string", "typing",
    "statistics", "base64", "uuid", "random", "copy", "enum", "ast", "tokenize", "shlex",
    "dataclasses", "pprint", "traceback", "__future__", "warnings", "stat", "errno",
    "tomllib"}
# An attribute with one of these names is a module reached through another module
# (shutil.os, os.path.os), which the qualified-name tables would not recognise.
_PY_MODULE_ATTRS = {"os", "sys", "shutil", "subprocess", "pathlib", "io", "builtins",
                    "posix", "nt", "importlib"}
_PY_MAX_VALUES = 64
# Write functions by qualified name, and which positional arguments are paths they change.
_PY_WRITE_FUNCS = {
    "os.replace": (0, 1), "os.rename": (0, 1), "os.renames": (0, 1), "os.remove": (0,),
    "os.unlink": (0,), "os.truncate": (0,), "os.rmdir": (0,), "os.removedirs": (0,),
    # A link makes a later write to either end reach the other.
    "os.link": (0, 1), "os.symlink": (0, 1), "os.open": (0,), "os.chmod": (0,),
    "shutil.copy": (1,), "shutil.copy2": (1,), "shutil.copyfile": (1,),
    "shutil.copytree": (1,), "shutil.copymode": (1,), "shutil.copystat": (1,),
    "shutil.move": (0, 1), "shutil.rmtree": (0,), "shutil.unpack_archive": (1,),
    "shutil.make_archive": (0,),
}
_PY_OPENERS = {"open"}
_PY_RUNNERS = {"subprocess.run", "subprocess.call", "subprocess.check_call",
               "subprocess.check_output", "subprocess.Popen", "subprocess.getoutput",
               "subprocess.getstatusoutput", "os.system", "os.popen"}
_PY_DYNAMIC = {"exec", "eval", "compile", "getattr", "setattr", "delattr", "__import__",
               "globals", "locals", "vars", "breakpoint", "sys.modules", "os.chdir",
               "os.fchdir", "os.chroot"}
_PY_DYNAMIC_PREFIXES = ("os.exec", "os.spawn", "os.posix_spawn", "os.fork")
# Path methods: those that change the receiver, and those that move it to an argument.
_PY_PATH_WRITES = {"write_text", "write_bytes", "touch", "unlink", "rmdir", "chmod"}
# The receiver and the single argument: a move, a copy, or a link between the two.
_PY_PATH_MOVES = {"rename", "replace", "copy", "copy_into", "move", "move_into",
                  "symlink_to", "hardlink_to"}
_PY_PATH_CTORS = {"pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath",
                  "os.path.join", "posixpath.join"}


def _join(parts: list[set]) -> set:
    """Every way to join one value from each part, os.path.join style."""
    import itertools
    import posixpath
    count = 1
    for p in parts:
        count *= len(p)
    if not parts or count > _PY_MAX_VALUES:
        return {_UNKNOWN}
    return {posixpath.join(*c) for c in itertools.product(*parts)}


class _PyWrites:
    def __init__(self, tree, argv: list[str]):
        import ast
        self.ast, self.argv, self.tree = ast, argv, tree
        self.parent = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        self.imports: dict[str, str] = {}
        self.values: dict[str, list] = {}
        self.modules: set[str] = set()                 # local names bound to a module
        self.unsafe = False
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.unsafe |= a.name not in _PY_SAFE_MODULES
                    local = a.asname or a.name.split(".")[0]
                    self.imports[local] = a.name if a.asname else a.name.split(".")[0]
                    self.modules.add(local)
            elif isinstance(n, ast.ImportFrom):
                self.unsafe |= n.level != 0 or n.module not in _PY_SAFE_MODULES
                for a in n.names:
                    self.unsafe |= a.name == "*"
                    self.imports[a.asname or a.name] = f"{n.module}.{a.name}"
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        self.values.setdefault(t.id, []).append(n.value)
            elif isinstance(n, ast.arg):
                self.values.setdefault(n.arg, []).append(None)    # a parameter: unknown
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and not isinstance(
                    self.parent.get(n), ast.Assign):
                self.values.setdefault(n.id, []).append(None)     # a loop, a with: unknown

    def qual(self, e) -> str:
        """`os.path.join`, `shutil.copy` (also through `from shutil import copy`), or ''."""
        ast = self.ast
        if isinstance(e, ast.Name):
            return self.imports.get(e.id, e.id)
        if isinstance(e, ast.Attribute):
            base = self.qual(e.value)
            return f"{base}.{e.attr}" if base else ""
        return ""

    def fold(self, e, depth: int = 0) -> set:
        """The possible string values of a path expression; _UNKNOWN marks unknown parts."""
        ast = self.ast
        if e is None or depth > 12:
            return {_UNKNOWN}
        d = depth + 1
        if isinstance(e, ast.Constant):
            return {e.value} if isinstance(e.value, str) else {_UNKNOWN}
        if isinstance(e, ast.Name):
            return self._fold_name(e.id, d)
        if isinstance(e, ast.Subscript) and self.qual(e.value) == "sys.argv" \
                and isinstance(e.slice, ast.Constant) and isinstance(e.slice.value, int):
            i = e.slice.value
            return {self.argv[i]} if 0 <= i < len(self.argv) else {_UNKNOWN}
        if isinstance(e, ast.Call) and self.qual(e.func) in _PY_PATH_CTORS and e.args \
                and not e.keywords:
            return _join([self.fold(a, d) for a in e.args])
        return {_UNKNOWN}

    def _fold_name(self, name: str, d: int) -> set:
        """Every value a name can hold: the union over its plain assignments. A name
        bound any other way (a parameter, a loop, `with`) is unknown."""
        sources = self.values.get(name, [])
        if not sources or None in sources:
            return {_UNKNOWN}
        out = set().union(*(self.fold(v, d) for v in sources))
        return out if len(out) <= _PY_MAX_VALUES else {_UNKNOWN}

    def _write_mode(self, mode) -> bool:
        ast = self.ast
        return not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                    and not set(mode.value) & set("wax+"))

    def targets(self, cwd: str):
        """The folded path of every write, or None when the script cannot be analysed."""
        ast = self.ast
        out: list[str] = []
        if self.unsafe:
            return None
        for n in ast.walk(self.tree):
            name = n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", "")
            if name.startswith("__") and name not in ("__name__", "__file__"):
                return None                           # __builtins__, os.__dict__[...]
            if isinstance(n, ast.Attribute) and n.attr in _PY_MODULE_ATTRS \
                    and self.qual(n) not in ("os.path",):
                return None                           # shutil.os.replace(...)
            if isinstance(n, ast.Name) and n.id in self.modules and not (
                    isinstance(self.parent.get(n), ast.Attribute)
                    and self.parent[n].value is n):
                return None                           # `o = os`: a module as a value
            q = self.qual(n)                          # "" for anything but a name or attribute
            if q in _PY_DYNAMIC or q.startswith(_PY_DYNAMIC_PREFIXES):
                return None
            p = self.parent.get(n)
            if q == "sys.argv" and not (
                    isinstance(p, ast.Subscript) and p.value is n
                    and isinstance(p.ctx, ast.Load) and isinstance(p.slice, ast.Constant)):
                return None                           # sys.argv changed or passed around
            called = isinstance(p, ast.Call) and p.func is n
            writes = q in _PY_WRITE_FUNCS or q in _PY_OPENERS or q in _PY_RUNNERS or (
                isinstance(n, ast.Attribute)
                and n.attr in _PY_PATH_WRITES | _PY_PATH_MOVES | {"open"})
            if writes and not called:
                return None                           # `w = os.replace`: not followed
            if not isinstance(n, ast.Call):
                continue
            q, f = self.qual(n.func), n.func
            kw = {k.arg: k.value for k in n.keywords}
            method = f.attr if isinstance(f, ast.Attribute) else ""
            if (q in _PY_OPENERS or q in _PY_WRITE_FUNCS or q in _PY_RUNNERS
                    or method in _PY_PATH_WRITES | _PY_PATH_MOVES | {"open"}) and (
                    None in kw or any(isinstance(a, ast.Starred) for a in n.args)):
                return None                           # open(*a), open(p, **kw): unseen
            if q in _PY_OPENERS:
                mode = n.args[1] if len(n.args) > 1 else kw.get("mode")
                if mode is not None and self._write_mode(mode):    # no mode: read
                    out += self.fold(n.args[0] if n.args else kw.get("file"))
            elif q in _PY_WRITE_FUNCS:
                for i in _PY_WRITE_FUNCS[q]:
                    out += self.fold(n.args[i] if i < len(n.args) else None)
            elif q in _PY_RUNNERS:
                # cwd= moves relative paths, executable= replaces the program.
                out += [_UNKNOWN] if kw.keys() & {"cwd", "executable"} else self._run_targets(
                    n.args[0] if n.args else kw.get("args"), cwd,
                    fed=bool(kw.keys() & {"input", "stdin", "shell"}))
            elif not method or isinstance(f.value, ast.Name) and f.value.id in self.modules:
                pass                                  # a module function: the tables above
            elif method in _PY_PATH_WRITES:
                out += self.fold(f.value)
            elif method in _PY_PATH_MOVES and (method != "replace"      # str.replace takes two
                                               or len(n.args) + len(n.keywords) == 1):
                dest = n.args[:1] or [k.value for k in n.keywords
                                      if k.arg in ("target", "target_dir")]
                if dest:                              # d.copy(): a dict, not a path
                    out += self.fold(f.value) | self.fold(dest[0])
            elif method == "open":
                mode = n.args[0] if n.args else kw.get("mode")
                if mode is not None and self._write_mode(mode):
                    out += self.fold(f.value)
        return out

    def _run_targets(self, cmd, cwd: str, fed: bool = False) -> list[str]:
        """A command the script runs: the guards its shell form writes, a guard it only
        runs (`python3 tools/check_x.py`), or _UNKNOWN when it cannot be read. A child
        fed on stdin (input=, stdin=) may be fed a program, so only this repo's own
        guard may take input."""
        ast = self.ast
        if isinstance(cmd, (ast.List, ast.Tuple)):
            parts = [self.fold(x) for x in cmd.elts]
            if len(parts) >= 2 and all(re.match(r"(?:.*/)?python[0-9.]*$", v) for v in parts[0]) \
                    and all(_UNKNOWN not in v and _guarded_target(v, cwd) for v in parts[1]):
                return []                             # running this repo's guard is not editing it
            # Any other command passes only if its program is on a read-only list: a
            # formatter, `git apply` or `patch` rewrites files the Bash scan cannot see
            # (pre-push review 2026-10-21), so an allow-list, not more writer names.
            if fed or not all(len(p) == 1 for p in parts):
                return [_UNKNOWN]
            words = [next(iter(p)) for p in parts]
            head = os.path.basename(words[0])
            sub = words[1] if len(words) > 1 else ""
            read_only = head in _READ_ONLY_COMMANDS or (head == "git" and sub in _READ_ONLY_GIT)
            return [""] if read_only and _UNKNOWN not in "".join(words) else [_UNKNOWN]
        return [_UNKNOWN]          # a shell string (os.system, shell=True): not followed


def _may_be_guard(value: str, cwd: str) -> bool:
    """Could this folded write target be a guard file of this repo? (_GUARD_HIT holds
    _UNKNOWN in its name, so it is caught below.)"""
    if _UNKNOWN not in value:
        return _guarded_target(value, cwd)
    base = value.rsplit("/", 1)[-1]
    return _UNKNOWN in base or bool(_GUARD_BASENAME.match(base)) or base in (
        "settings.json", "settings.local.json", "tools", ".claude", ".", "..", "")


def _python_rewrites_a_guard(text: str, cwd: str, argv: list[str]) -> bool:
    import ast
    try:
        tree = ast.parse(text)
        targets = _PyWrites(tree, argv).targets(cwd)
    except (SyntaxError, ValueError, RecursionError):
        return True
    return targets is None or any(_may_be_guard(t, cwd) for t in targets)


def _read_script(path: str, cwd: str) -> str:
    """A script file an interpreter runs, unless it is a repo tool (mutation_check.py
    rewrites guards by design, and tools/ is reviewed code)."""
    full = os.path.abspath(os.path.join(cwd, os.path.expanduser(path)))
    if os.path.dirname(full) == _TOOLS_DIR or not os.path.isfile(full):
        return ""
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            return fh.read(_MAX_SCRIPT_BYTES)
    except OSError:
        return ""


def _one_value(word: str, env: dict) -> str:
    """A Bash word as one string: its single possible expansion, else _UNKNOWN. A
    command substitution comes back from _expand_vars unexpanded, so the `[$`]` test
    catches it, and an unset variable expands to nothing."""
    from check_public_pii import _expand_vars
    vals = set(_expand_vars(word, env))
    return vals.pop() if len(vals) == 1 and not re.search(r"[$`]", next(iter(vals))) \
        else _UNKNOWN


def bash_guard_writes(command: str, cwd: str = ".", _depth: int = 0) -> list[str]:
    """Guard files a Bash command would write, rename, delete or restore.

    Covers redirects, tee, sed -i, dd (via check_public_pii.extract_write_targets),
    cp/mv/install/ln destinations, rm/unlink/truncate, git checkout/restore/rm/mv,
    perl/ruby -i, and an interpreter script (inline, on stdin, or a file outside
    tools/) that names a guard path and calls a write. Command strings a shell runs
    (`sh -c`, stdin, `$( )`) are followed. Added 2026-10-01: Bash was outside this
    hook entirely, and a scratchpad patch script rewrote shell_tokens.py unapproved.
    """
    from check_public_pii import (_SHELLS, _all_command_positions, _stdin_texts,
                                  command_assignments, extract_write_targets,
                                  scripts_run_by)
    from shell_tokens import simple_commands
    if _depth > 8:
        return []
    from shell_tokens import tokenize as sh_tokenize
    env = command_assignments(command)
    hits = [t for t in extract_write_targets(command, env) if _guarded_target(t, cwd, env)]
    # Every directory a later Python program may run in: the start, plus each `cd` seen
    # (kept alongside the old ones, since a cd can fail). None: a cd we cannot resolve.
    cwds: list[str] | None = [cwd]
    try:
        literal = {t.text for t in sh_tokenize(command) if t.kind == "heredoc" and t.quoted}
    except Exception:
        literal = set()
    for cmd in simple_commands(command):
        lead = [w for w in cmd.words if not re.match(r"[A-Za-z_]\w*=", w)]
        if lead[:1] in (["cd"], ["pushd"]) and cwds is not None:
            to = _one_value(lead[1], env) if len(lead) > 1 else _UNKNOWN
            cwds = None if _UNKNOWN in to else list(dict.fromkeys(
                cwds + [os.path.abspath(os.path.join(c, os.path.expanduser(to))) for c in cwds]))
            if cwds is not None and len(cwds) > 8:
                cwds = None                           # too many places: the heuristic
        for inner in cmd.subs:
            hits += bash_guard_writes(inner, cwd, _depth + 1)
        words = cmd.words
        for i in sorted(_all_command_positions(words)):
            name = os.path.basename(words[i])
            rest = words[i + 1:]
            paths: list[str] = []
            if name in ("cp", "mv", "install", "ln", "gcp", "gmv"):
                paths = _copy_like_paths(name, rest, cwd)
            elif name in ("rm", "unlink", "truncate", "shred"):
                paths = _non_options(rest)
            elif name == "git":
                # Global options come first (`git -C . restore`, `git --no-pager
                # checkout`: Grok review of 2ffecb1, F1).
                g = 0
                while g < len(rest) and rest[g].startswith("-"):
                    g += 2 if rest[g] in ("-C", "-c", "--git-dir", "--work-tree") else 1
                if rest[g:g + 1] and rest[g] in ("checkout", "restore", "rm", "mv", "stash"):
                    paths = _non_options(rest[g + 1:])
            elif name in ("perl", "ruby") and any(a.startswith("-i") or a.startswith("-pi")
                                                  for a in rest):
                paths = _non_options(rest)
            hits += [p for p in paths if _guarded_target(p, cwd, env)]
            # env: a variable-held string runs too (`c='rm ...'; eval "$c"`, Grok
            # review of 2ffecb1, F3).
            for text in scripts_run_by(cmd, i, env):
                hits += bash_guard_writes(text, cwd, _depth + 1)
            # `bash patch.sh` and `source patch.sh`: the file's commands run too
            # (Grok reviews of 88db514 F3 and 2ffecb1 F4).
            scripts = (_shell_script_files(rest) if name in _SHELLS
                       else rest[:1] if name in ("source", ".") else [])
            for script in scripts:
                for c in cwds or [cwd]:               # the directory a cd moved to
                    hits += bash_guard_writes(_read_script(script, c), c, _depth + 1)
            if _INTERPRETER.match(name):
                texts = list(_stdin_texts(cmd))
                # Bash expands $ and backticks in an unquoted heredoc, a here-string and
                # -c, so a program from there is analysed only if it has none. A heredoc
                # whose delimiter was quoted is literal (the tokenizer's own flag).
                expandable = [t for t in texts if t not in literal]
                argv = ["-"] + rest                   # what sys.argv will hold
                skip = False
                for k, a in enumerate(rest):
                    # -c, or a cluster ending in it (`python3 -uc`, `perl -pe`):
                    # Grok review of b9cc6a5, F4.
                    code_flags = "c" if name.startswith("python") else "ceE"  # python -E is a flag
                    if a[:1] == "-" and a[1:2] != "-" and a[-1] in code_flags \
                            and not (name.startswith("python") and a[1:2] in "WX") \
                            and k + 1 < len(rest):
                        texts.append(rest[k + 1])
                        expandable.append(rest[k + 1])
                        argv = ["-c"] + rest[k + 2:]
                        break
                    if a == "-m":
                        break
                    if skip:
                        skip = False
                        continue
                    if name.startswith("python") and a in ("-W", "-X"):
                        skip = True                   # its value is not the script
                    if a == "-":
                        argv = rest[k:]
                        break
                    if not a.startswith("-"):
                        texts += [_read_script(a, c) for c in cwds or [cwd]]
                        argv = rest[k:]
                        break
                py_argv = [_one_value(w, env) for w in argv] if name.startswith("python") \
                    else None
                if any(_script_rewrites_a_guard(
                        t, c, None if cwds is None or t in expandable and re.search(r"[$`]", t)
                        else py_argv)
                        for t in texts for c in (cwds or [cwd])):
                    hits.append(f"{name} script naming a guard path")
    return list(dict.fromkeys(hits))


def main() -> None:
    # Fail open on anything malformed: a guard that crashes blocks all work.
    p = read_payload()
    if not p.ok:
        sys.exit(0)

    if os.environ.get(OVERRIDE_ENV):
        sys.exit(0)

    if p.tool_name == "Bash":
        ti = p.data.get("tool_input") if isinstance(p.data, dict) else None
        command = ti.get("command", "") if isinstance(ti, dict) else ""
        if not isinstance(command, str) or not command:
            sys.exit(0)
        # Approval is visible in the command itself, so the transcript records it.
        if approved_in_command(command):
            sys.exit(0)
        cwd = p.data.get("cwd") or os.getcwd()
        try:
            hits = bash_guard_writes(command, cwd if isinstance(cwd, str) else ".")
        except Exception as e:                    # fail open, like every other path
            print(f"check_guard_edit_approval.py error (allowing): {e}", file=sys.stderr)
            sys.exit(0)
        if hits:
            print(MESSAGE.format(path=", ".join(hits), env=OVERRIDE_ENV), file=sys.stderr)
        sys.exit(2 if hits else 0)

    if p.tool_name not in ("Write", "Edit", "MultiEdit"):
        sys.exit(0)

    path = p.file_path

    if not is_guarded(path):
        sys.exit(0)

    print(MESSAGE.format(path=path, env=OVERRIDE_ENV), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
