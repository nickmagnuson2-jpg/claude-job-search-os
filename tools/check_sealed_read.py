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
on standard input to a command that treats it as text, as text in a Python program
(stdin or `-c`) whose syntax tree shows the string ends only as output (see
`_PyFlow`), and in the positions listed
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
import ast
import io
import itertools
import json
import os
import re
import string
import sys
import tokenize

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

# The therapy vault in the sibling personal repo. personal/CLAUDE.md labels it SEALED, under
# the same rule as the in-repo sealed tree. Personal-repo sessions read it; a job-search
# session never does. Added 2026-10-27, approved by Nick: "definitely fix the block of
# reading the personal therapy files". In command text it counts only path-shaped (followed by
# `/`, or after `data/`): unlike the in-repo name it is an ordinary English word, and a
# restraint replay over 282 transcripts found about a dozen bare-word false blocks.
THERAPY_NAME = "therapy"
THERAPY_REL = "../personal/data/therapy"
# Unlike the in-repo tree, which moved to a sibling home of its own, the vault still sits
# under readable directories of the personal repo, so a recursive search of either one
# walks into it without spelling its name (cross-model review of 2026-10-27, Codex F1 and
# Grok F1-F3). Both parents are therefore targets in their own right: Grep, Glob or Read
# aimed at one, or a Bash word that is a path to one (`cd`, `grep -r`, `find`, `ls -R`).
# A named file beside the vault, such as ../personal/data/<log>.md, is not a parent.
THERAPY_PARENTS = ("../personal/data", "../personal")   # direct parent first

# The vault's direct parent spelled as a path that ENDS at it: `personal/data`,
# `<anything>/personal/data/`, or `personal/data/*`. A file beside the vault
# (`personal/data/<log>.md`) does not match. The personal repo ROOT is not in this regex:
# a restraint replay found most mentions of it are ordinary personal-repo work (cd, git,
# named files), so it counts only for a command that walks directories (_walks).
# Cross-model review 2026-10-27, Grok round 2 F2: the parents must sit in this regex, where
# the wrapper, assignment and heredoc handling already applies, not only in a whole-word test.
_PARENT_END = r"(?=$|[^\w./-])"
_PARENT_TEXT = r"personal/data/*" + _PARENT_END

# The directory's own name as a whole path component, in any case (this volume is
# case-insensitive). Matched after quote and backslash removal, so a quote or a
# backslash spliced into the name does not hide it. The `data/` parent is not required:
# `cd data && cat project-background/x.md` reads the same files. The sibling home's
# name counts the same way: everything in it is sealed.
_SEALED_RE = re.compile(
    r"(?<![\w-])(?:project-background|therapy(?=/)|(?<=data/)therapy|%s|%s)(?![\w-])(?!\.\w)" % (
        re.escape(SEALED_HOME), _PARENT_TEXT), re.I)
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
    src = _python_source(words, h)
    return src[1] if src and src[0] == "file" and _REPO_TOOL.search(words[src[1]]) else None


def _is_a_sealed_path(word: str) -> bool:
    """Read as ONE path, does this word point under the sealed directory?"""
    parts = [p.lower() for p in os.path.normpath(word).split("/")]
    return SEALED_HOME.lower() in parts or any(
        p in (SEALED_NAME, THERAPY_NAME) and (k == 0 or parts[k - 1] == "data")
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


# --- Python: is the name only text? -------------------------------------------------
#
# A Python program fed on standard input or through `-c` may name the directory as TEXT:
# in a comment, or in a string whose value provably ends up only as output. The proof
# follows the VALUE through the syntax tree, never the shape of the string. Each step
# the value takes must be one listed in `_PyFlow.text`, and the last one a text sink:
# print, a write to an opened file or to stdout/stderr, json.dump, sys.exit, a
# comparison, a bare statement. Anything else it reaches is a read.
#
# A sink is only text if nothing in the program can turn the sink into a process, and
# a variable is only text if nothing can reach it except by its name. Both are enforced
# by POSITIVE lists over the whole program, because a list of dangerous spellings is
# never complete (six reviews found more than twenty ways around them): imports, and the
# attributes used on each module, only from `_PY_MODULE_ATTRS`; builtins only from
# `_PY_BUILTINS` (so no `type()` to build an object whose methods open their argument);
# no name beginning with `__` except `__name__`/`__file__`, and no attribute beginning
# with `_`; no class; no assignment to an attribute; `.format` only on a literal whose
# fields are plain names; and a method is a sink only on a receiver proven to be a file,
# a container or the string itself.
#
# Around it, the Bash call: no process substitution anywhere (it connects output to a
# command without a pipe the hook follows), and the Python command's own output goes
# only to stdout/stderr, a plain file, or fd 1/2 (`_plain_output`). Pipes are already
# followed: `python3 - <<'PY' ... | sh` blocks because the program names the directory.
#
# A program that spells the name anywhere, a comment included, has its paths checked
# too: a path built from pieces ('data/project-' + 'background') is a read the name
# check never sees. `open` is the only way an allowed program reaches the filesystem
# (pathlib is deliberately not allowed: its many ways to build and walk a path defeated
# folding in review), so `open` may appear only as the function of a call, and its path
# must fold to literals (`_static`) none of which names or resolves into the directory
# (`_paths_are_known`).
#
# RESIDUAL, shared with `echo`: a value is not followed through the filesystem. Text
# written to a file or descriptor and then used as a path or a command, by this program
# (write it, read it back, open it), by a later command, or by another process (a FIFO
# made with mknod/mkfifo, a script run later), is not seen.
# `mknod q p; sh < q & echo "cat <dir>/a.md" > q` passes this hook today; the same text
# from Python is no more dangerous. Accidental reads do not take that shape, and a list
# of spellings (mkfifo, mknod, /dev/fd) cannot close it.
#
# RESIDUAL, the interpreter: `python3 -` and `-c` import from the working directory
# first, so a file named like an allowed module (json.py) runs instead of the module.
# The analysis trusts the Python installation and the working directory.

_PY_LEXICAL_OK = {tokenize.COMMENT, tokenize.STRING} | (
    {tokenize.FSTRING_MIDDLE} if hasattr(tokenize, "FSTRING_MIDDLE") else set())
# The modules a program may import, and the only attributes it may use on each. A module
# object exposes the modules IT imported (re.enum.sys.modules, collections._sys), so a
# module name may appear only as `<module>.<listed attribute>`, `from <module> import`
# only names listed, and no attribute anywhere may start with `_`.
_PY_MODULE_ATTRS = {
    "json": {"dumps", "dump", "loads", "load", "JSONDecodeError"},
    "sys": {"argv", "stdout", "stderr", "stdin", "exit", "version", "version_info"},
    "re": {"sub", "subn", "search", "match", "fullmatch", "findall", "finditer", "split",
           "compile", "escape", "IGNORECASE", "I", "MULTILINE", "M", "DOTALL", "S",
           "VERBOSE", "X", "ASCII", "A"},
    "textwrap": {"dedent", "indent", "wrap", "fill", "shorten"},
    "datetime": {"date", "datetime", "timedelta", "timezone", "time"},
    "collections": {"Counter", "defaultdict", "OrderedDict", "deque", "namedtuple"},
    "csv": {"writer", "reader", "DictWriter", "DictReader", "QUOTE_ALL", "QUOTE_MINIMAL"},
    "math": {"ceil", "floor", "sqrt", "log", "log10", "exp", "inf", "pi", "isclose",
             "fsum", "prod"},
    "itertools": {"chain", "groupby", "islice", "product", "combinations", "permutations",
                  "count", "cycle", "repeat", "zip_longest", "accumulate", "starmap",
                  "takewhile", "dropwhile", "pairwise", "batched"},
    "difflib": {"SequenceMatcher", "unified_diff", "ndiff", "get_close_matches",
                "context_diff", "Differ"},
}
_PY_BUILTINS = {"print", "open", "str", "dict", "list", "tuple", "set", "frozenset",
                "len", "sorted", "reversed", "enumerate", "range", "zip", "min", "max",
                "sum", "any", "all", "abs", "round", "int", "float", "bool", "isinstance",
                "repr", "map", "filter", "Exception", "ValueError", "KeyError",
                "TypeError", "IndexError", "OSError", "FileNotFoundError", "RuntimeError",
                "ZeroDivisionError", "AssertionError", "SystemExit", "StopIteration"}
_PY_DUNDERS_OK = {"__name__", "__file__"}
_PY_STR_STEPS = {"join", "format", "strip", "lstrip", "rstrip", "replace", "split",
                 "splitlines", "lower", "upper", "title"}
_PY_VERDICTS = {"startswith", "endswith", "count"}                 # a bool or an int out
_PY_COLLECT = {"append", "extend"}
_PY_CONTAINERS = {"str", "dict", "list", "tuple"}
_PY_IMPORTED = {"json": None, "sys": None, "textwrap": None}
_PLAIN_FILE_OUT = (">", ">>", ">|", "&>", "&>>")
_PY_MAX_VALUES = 256        # a path folding to more possible values than this is unknown
# Bindings recorded as identifier strings, not Name nodes: a match capture, `except as`,
# and type parameters.
_PY_NAMED_BINDINGS = tuple(getattr(ast, k) for k in (
    "MatchAs", "MatchStar", "MatchMapping", "ExceptHandler", "TypeVar", "ParamSpec",
    "TypeVarTuple") if hasattr(ast, k))


def _plain_template(s) -> bool:
    """A str.format template whose fields are plain names or positions: `{0.attr}` and
    `{x[k]}` look values up, so they can reach a variable without spelling it."""
    try:
        return isinstance(s, str) and all(
            f is None or re.fullmatch(r"\w*", f) and "{" not in (spec or "")
            for _, f, spec, _ in string.Formatter().parse(s))
    except ValueError:
        return False


class _PyFlow:
    def __init__(self, tree):
        self.parent = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        self.sources, self.loads, modules = {}, {}, {}
        safe = True
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                if isinstance(n.ctx, ast.Load):
                    self.loads.setdefault(n.id, []).append(n)
                elif isinstance(n.ctx, ast.Store):
                    self.sources.setdefault(n.id, []).append(self._bound_to(n))
                safe = safe and _plain_name(n.id)
            elif isinstance(n, ast.alias):
                self.sources.setdefault((n.asname or n.name).split(".")[0], []).append(n)
                safe = safe and _plain_name(n.name)
            elif isinstance(n, ast.Import):
                safe = safe and all(a.name in _PY_MODULE_ATTRS for a in n.names)
                modules.update({a.asname or a.name: a.name for a in n.names})
            elif isinstance(n, ast.ImportFrom):
                safe = safe and n.level == 0 and n.module in _PY_MODULE_ATTRS and all(
                    a.name in _PY_MODULE_ATTRS[n.module] for a in n.names)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.sources.setdefault(n.name, []).append(None)
            elif isinstance(n, ast.arg):
                self.sources.setdefault(n.arg, []).append(None)
            elif isinstance(n, _PY_NAMED_BINDINGS):
                for k in ("name", "rest"):
                    if isinstance(getattr(n, k, None), str):
                        self.sources.setdefault(getattr(n, k), []).append(None)
            elif isinstance(n, ast.Attribute):
                safe = safe and isinstance(n.ctx, ast.Load) and not n.attr.startswith("_")
                if n.attr in ("format", "format_map"):
                    safe = safe and n.attr == "format" and isinstance(n.value, ast.Constant) \
                        and _plain_template(n.value.value)
            elif isinstance(n, ast.ClassDef):
                safe = False
        # Every name the program uses but never binds is a builtin, from the list, and
        # every use of an imported module is one of its listed attributes.
        self.safe = safe and all(n in self.sources or n in _PY_BUILTINS or n in _PY_DUNDERS_OK
                                 for n in self.loads) and all(
            isinstance(self.parent.get(use), ast.Attribute) and self.parent[use].value is use
            and self.parent[use].attr in _PY_MODULE_ATTRS[mod]
            for name, mod in modules.items() for use in self.loads.get(name, []))
        self.seen = set()

    def _bound_to(self, name):
        """The expression a plain assignment or `with ... as name` gives this name."""
        p = self.parent.get(name)
        if isinstance(p, ast.Assign) and name in p.targets or (
                isinstance(p, ast.AnnAssign) and p.target is name):
            return p.value
        if isinstance(p, ast.withitem) and p.optional_vars is name:
            return p.context_expr
        return None

    def _trusted(self, name: str) -> bool:
        """A builtin never rebound, or a module bound only by its own `import`."""
        for s in self.sources.get(name, []):
            if not (isinstance(s, ast.alias) and s.asname is None and name in _PY_IMPORTED
                    and s.name == name and isinstance(self.parent.get(s), ast.Import)):
                return False
        return True

    def _names_only(self, name: str, test, depth: int = 0) -> bool:
        s = self.sources.get(name, [])
        return depth < 8 and bool(s) and all(
            isinstance(v, ast.expr) and test(v, depth + 1) for v in s)

    def _module(self, e, name: str) -> bool:
        return isinstance(e, ast.Name) and e.id == name and self._trusted(name)

    def _writer(self, e, depth: int = 0) -> bool:
        """An opened file, or sys.stdout / sys.stderr."""
        if isinstance(e, ast.Attribute):
            return e.attr in ("stdout", "stderr") and self._module(e.value, "sys")
        if isinstance(e, ast.Call):
            f = e.func
            return isinstance(f, ast.Name) and f.id == "open" and self._trusted("open")
        return isinstance(e, ast.Name) and self._names_only(e.id, self._writer, depth)

    def _static(self, e, depth: int = 0):
        """The possible values of a string built only from literals, or None."""
        if depth > 8:
            return None
        if isinstance(e, ast.Constant):
            return {e.value} if isinstance(e.value, str) else None
        if isinstance(e, ast.BinOp):
            parts = [self._static(e.left, depth + 1), self._static(e.right, depth + 1)]
            return _combine(parts, "") if isinstance(e.op, ast.Add) else None
        if isinstance(e, ast.JoinedStr):
            return _combine([self._static(v, depth + 1) for v in e.values], "")
        if isinstance(e, ast.FormattedValue):
            return self._static(e.value, depth + 1) if e.format_spec is None \
                and e.conversion == -1 else None
        if isinstance(e, ast.Name):
            vals = [self._static(v, depth + 1) if isinstance(v, ast.expr) else None
                    for v in self.sources.get(e.id) or [None]]
            out = None if None in vals else set().union(*vals)
            return out if out is None or len(out) <= _PY_MAX_VALUES else None
        return None

    def _paths_are_known(self) -> bool:
        """`open` is the only filesystem call an allowed program can make. Every use of
        it is a call whose path folds to literals, none naming or resolving into the
        sealed directory. A program that rebinds `open` gets no exemption."""
        if not self._trusted("open"):
            return False
        for use in self.loads.get("open", []):
            call = self.parent.get(use)
            if not (isinstance(call, ast.Call) and call.func is use):
                return False                    # `o = open`, map(open, ...), [open][0]
            first = call.args[0] if call.args else next(
                (k.value for k in call.keywords if k.arg == "file"), None)
            vals = self._static(first) if first is not None else None
            if vals is None or any(_names_sealed(v) or _under_sealed(_root(), v)
                                   for v in vals):
                return False
        return True

    def _container(self, e, depth: int = 0) -> bool:
        """A list or dict built here, so storing into it reaches nothing else."""
        if isinstance(e, ast.Call):
            return isinstance(e.func, ast.Name) and e.func.id in ("list", "dict") \
                and self._trusted(e.func.id)
        return isinstance(e, (ast.List, ast.Dict)) or (
            isinstance(e, ast.Name) and self._names_only(e.id, self._container, depth))

    def text(self, node) -> bool:
        """Does the value of this expression end up only as text?"""
        p = self.parent.get(node)
        if isinstance(p, (ast.Expr, ast.Compare)):
            return True
        if isinstance(p, ast.Assert):
            return node is p.msg
        if isinstance(p, (ast.JoinedStr, ast.FormattedValue, ast.List, ast.Tuple, ast.Dict)):
            return self.text(p)
        if isinstance(p, ast.BinOp):
            return isinstance(p.op, (ast.Add, ast.Mod)) and self.text(p)
        if isinstance(p, ast.IfExp):
            return node is not p.test and self.text(p)
        if isinstance(p, ast.Subscript):
            return node is p.value and isinstance(p.ctx, ast.Load) and self.text(p)
        if isinstance(p, ast.Attribute):            # a method called on the value
            call = self.parent.get(p)
            if not (isinstance(call, ast.Call) and call.func is p):
                return False
            return p.attr in _PY_VERDICTS or p.attr in _PY_STR_STEPS and self.text(call)
        if isinstance(p, ast.keyword):
            return self._argument(self.parent.get(p), node, p.arg)
        if isinstance(p, ast.Call):
            return node is not p.func and self._argument(p, node, None)
        if isinstance(p, ast.For):
            return node is p.iter and isinstance(p.target, ast.Name) \
                and self._variable_is_text(p.target.id)
        if isinstance(p, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node is p.value:
            targets = p.targets if isinstance(p, ast.Assign) else [p.target]
            return all(self._stored_as_text(t) for t in targets)
        return False

    def _argument(self, call, node, kw) -> bool:
        f = call.func
        if isinstance(f, ast.Name):
            if not self._trusted(f.id):
                return False
            if f.id == "print":
                return kw != "file" and all(
                    k.arg is not None and (k.arg != "file" or self._writer(k.value))
                    for k in call.keywords)
            return f.id in _PY_CONTAINERS and self.text(call)
        if not isinstance(f, ast.Attribute):
            return False
        if f.attr in ("write", "writelines"):
            return self._writer(f.value)
        if f.attr in _PY_COLLECT:
            return isinstance(f.value, ast.Name) and self._container(f.value) \
                and self._variable_is_text(f.value.id)
        if f.attr in ("join", "format"):
            return isinstance(f.value, (ast.Constant, ast.JoinedStr)) and self.text(call)
        if f.attr in ("dedent", "indent") and self._module(f.value, "textwrap"):
            return self.text(call)
        if f.attr == "exit" and self._module(f.value, "sys"):
            return True
        if self._module(f.value, "json"):
            if f.attr == "dumps":
                return self.text(call)
            return f.attr == "dump" and call.args[:1] == [node] and len(call.args) > 1 \
                and self._writer(call.args[1])
        return False

    def _stored_as_text(self, target) -> bool:
        """`x = v`, or `x[k] = v` into a container built here: x must then be text."""
        if isinstance(target, ast.Subscript):
            target = target.value
            if not self._container(target):
                return False
        return isinstance(target, ast.Name) and self._variable_is_text(target.id)

    def _variable_is_text(self, name: str) -> bool:
        """A value stored in this variable: every use of the variable must be text."""
        if name in self.seen:
            return True
        self.seen.add(name)
        for use in self.loads.get(name, []):
            p = self.parent.get(use)
            if isinstance(p, ast.Subscript) and not isinstance(p.ctx, ast.Load):
                continue                            # `x[k] = ...` stores into it
            if isinstance(p, ast.Attribute) and p.attr in _PY_COLLECT and isinstance(
                    self.parent.get(p), ast.Call) and self.parent[p].func is p:
                continue                            # `x.append(...)` adds to it
            if not self.text(use):
                return False
        return True


def _combine(parts, sep: str):
    """Every way to join one value from each part, or None if any part is unknown or
    there would be more than _PY_MAX_VALUES (counted before any are built)."""
    if not parts or any(p is None for p in parts):
        return None
    count = 1
    for p in parts:
        count *= len(p)
    return {sep.join(c) for c in itertools.product(*parts)} if count <= _PY_MAX_VALUES else None


def _plain_name(name: str) -> bool:
    return not name.startswith("__") or name in _PY_DUNDERS_OK


def _python_only_mentions(code: str) -> bool:
    """Does this Python program name the directory only as text? Fails closed: a
    program that does not parse, a naming outside a comment or a string, a string that
    names it as bytes, one whose value is not proven to end as text, and any exception
    while deciding (deep nesting included) is a read."""
    try:
        tree = ast.parse(code)
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
        if sum(_hits(t.string) for t in tokens if t.type in _PY_LEXICAL_OK) != _hits(code):
            return False
        flow = _PyFlow(tree)
        named = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and _names_sealed(str(n.value))]
        # A comment opens nothing, but the program around it can: every program needs
        # the gates and known paths, and every string naming the directory must be text.
        return flow.safe and flow._paths_are_known() and all(
            isinstance(n.value, str) and flow.text(n) for n in named)
    except Exception:
        return False


def _python_source(words: list, h: int):
    """Where a python command takes its program from: ("file", i) for a script at
    words[i], ("code", i) for `-c` code at words[i], ("stdin", None) for `python -` or
    bare python, ("other", None) for -m or a form this does not follow. None when the
    command is not python."""
    if h >= len(words) or not _INTERPRETER.match(os.path.basename(words[h])):
        return None
    j = h + 1
    while j < len(words):
        w = words[j]
        if w in _PY_VALUE_OPTS:
            j += 2
        elif w == "-":
            return ("stdin", None)
        elif w.startswith("-") and not w.startswith("--") and ("c" in w or "m" in w):
            # The first option in a cluster that takes a value consumes the rest:
            # `-uc` is -u -c, but `-mc` is -m c and `-cu` is -c u.
            return ("code", j + 1) if re.fullmatch(r"-[A-BD-LN-VYZa-bd-ln-z]*c", w) \
                else ("other", None)
        elif w.startswith("-"):
            j += 1
        else:
            return ("file", j)
    return ("stdin", None)


def _plain_output(c) -> bool:
    """Does this command write only to stdout/stderr, plain files, or fd 1 and 2? A
    descriptor duplicated from elsewhere, or a target built from a variable, may be a
    process."""
    for r in c.redirections:
        op = r.op.lstrip("0123456789")
        if op in _STDIN_OPS:
            continue
        if op in _PLAIN_FILE_OUT and r.target and not re.search(r"[$`]", r.target):
            continue
        if op == ">&" and r.target in ("1", "2"):
            continue
        return False
    return True


def _no_process_substitution(cmds) -> bool:
    """shell_tokens reads `<(cmd)` and `>(cmd)` as a redirection with no target."""
    return all(r.target for c in cmds for r in c.redirections)


def _stdin_program_only_mentions(c) -> bool:
    """A Python program on standard input whose every naming of the directory is text."""
    if _python_source(c.words, _head_index(c.words)) != ("stdin", None):
        return False
    bodies = [r.target if r.body is None else r.body for r in c.redirections
              if r.op.lstrip("0123456789") in _STDIN_OPS]
    return all(_python_only_mentions(b) for b in bodies if _names_sealed(b))


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


def _mentions(words: list, quoted: list, h: int, piped: bool = False,
              py_ok: bool = False) -> dict:
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
        elif _python_source(words, h) == ("code", i):
            if py_ok and _python_only_mentions(w):
                out[i] = "prose"   # `python -c` code that names it only as text
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


def _command_reads(c, py_ok: bool = False) -> bool:
    words = c.words
    h = _head_index(words)
    if any(_names_sealed(w) for w in words[:h + 1]):
        return True            # an assignment, or the command itself, holds the name
    if any(_names_sealed(r.target) for r in c.redirections
           if r.op.lstrip("0123456789") not in _STDIN_OPS):
        return True
    head = os.path.basename(words[h]) if h < len(words) else ""
    text_only = head in _STDIN_IS_TEXT or _repo_tool_index(words, h) is not None
    if not text_only and ((_stdin_names_sealed(c) and not (py_ok and _plain_output(c)
                                                   and _stdin_program_only_mentions(c)))
                          or (c.fed_by is not None and _passes_the_name_on(c.fed_by))):
        return True            # a shell, an interpreter, xargs or a loop is fed the name
    if _reaches_vault(words, h):
        return True            # into the vault from a directory the call is in, or a walk
                               # of a directory that contains it (THERAPY_PARENTS)
    kinds = _mentions(words, c.quoted, h, c.fed_by is not None, py_ok and _plain_output(c))
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


def _bash_reads_sealed(command: str, depth: int = 0, intent: bool = False,
                       py_ok: bool = False) -> bool:
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
        py_ok = True
        _CWDS.clear()
    _note_cds(cmds)
    py_ok = py_ok and _no_process_substitution(cmds)
    for c in cmds:
        if depth == 0 and _exports_override(c):
            return False       # everything after it in this call is covered
        if _overrides(c):
            continue
        if not (intent and _carries(c)) and _command_reads(c, py_ok):
            return True
        if any(_bash_reads_sealed(s, depth + 1, intent, py_ok)
               for s in list(c.subs) + _wrapped_scripts(c)):
            return True
    return False


_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}


def _wrapped_scripts(c) -> list:
    """The script inside `bash -c '...'` (any -c flag cluster, e.g. -lc) or `eval ...`.
    Grok round 4 F2: a walk of the personal repo root inside a wrapper was never parsed,
    because the script is one quoted word, not a substitution in c.subs."""
    h = _head_index(c.words)
    if h >= len(c.words):
        return []
    head, rest = os.path.basename(c.words[h]), c.words[h + 1:]
    if head == "eval":
        return [" ".join(rest)] if rest else []
    if head in _SHELLS:
        # The word after the first short-flag cluster holding c, wherever it sits: long
        # options and option values may come first (round 5: `bash --login -c`,
        # `bash -o errexit -c`). Over-approximate on purpose; a stray match only parses
        # one more word.
        for i, w in enumerate(rest):
            if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", w):
                return rest[i + 1:i + 2]     # shell_tokens has already removed the quotes
    return []


# Directories a Bash call moves into, besides the repo root, filled by _bash_reads_sealed
# before any word is judged. Grok round 2 F1: `cd .. && grep -r x personal/data` names the
# parent relative to the NEW directory, which resolving from the root alone misses.
_CWDS: list = []


def _note_cds(cmds) -> None:
    """Add every literal `cd`/`pushd` target in these commands to _CWDS, each resolved
    against the directories already known (over-approximate: order is not tracked)."""
    for c in cmds:
        h = _head_index(c.words)
        if h < len(c.words) and c.words[h] in ("cd", "pushd"):
            # The first word after the options: `cd -- dir`, `pushd -q dir` (Codex round 4
            # F2 read the option as the directory). A bare `-` (cd back) is not a target.
            args = c.words[h + 1:]
            while args and args[0].startswith("-"):    # `--`, `-q`, `-P`, and `-` (go back)
                args = args[1:]
            if not args:
                continue
            target = _SHELL_QUOTING.sub("", args[0])
            if "$" in target or "`" in target:
                continue
            here = os.path.realpath(str(_root()))
            for base in [here] + list(_CWDS):
                d = os.path.realpath(os.path.join(base, os.path.expanduser(target)))
                if d not in _CWDS and d != here:
                    _CWDS.append(d)


# Commands that walk a directory tree. Only these reach the vault from the repo root.
_WALKERS = {"find", "du", "tree", "fd", "rg"}
# Commands that copy or archive a tree when given a recursive flag (Codex round 5 F2:
# `tar -cf out.tar ../personal`). tar walks when it creates; the others with -r/-R/-a.
_COPIERS = {"cp", "rsync", "scp", "zip"}


def _walks(words: list, h: int) -> bool:
    """Does this command descend into subdirectories of its targets?"""
    if h >= len(words):
        return False
    head = os.path.basename(words[h])
    flags = [w for w in words[h + 1:] if w.startswith("-")]
    if head in _WALKERS:
        return True
    if head == "tar":
        return any(f == "--create" or (not f.startswith("--") and "c" in f) for f in flags)
    if head in _COPIERS:
        return any(f in ("--recursive", "--archive")
                   or (not f.startswith("--") and any(x in f for x in "rRa")) for f in flags)
    if head in _GREPS:
        return any(f in ("--recursive", "--dereference-recursive") or f.startswith("--directories=r")
                   or (not f.startswith("--") and ("r" in f or "R" in f)) for f in flags)
    if head == "ls":
        return any(not f.startswith("--") and "R" in f or f == "--recursive" for f in flags)
    return False


def _rel_from_root(path: str) -> str:
    here = os.path.realpath(str(_root()))
    return os.path.relpath(path, here).replace(os.sep, "/").lower()


def _walk_targets(words: list, h: int) -> list:
    """The directories a walking command descends from; none named means the current one."""
    head = os.path.basename(words[h])
    if head == "rg" and "--files" in words[h + 1:]:
        # `rg --files [path...]` lists files and takes no pattern (Grok round 4 F1), so
        # every positional is a path.
        targets = [w for w in words[h + 1:] if not w.startswith("-")]
    elif head in _GREPS or head == "rg":
        targets = _grep_layout(words, h, head)[1]
    elif head == "find":
        targets = []
        args = words[h + 1:]
        # find's global options come before the paths (Grok round 4 F3: `find -L dir`).
        # BSD find also takes -E -X -d -s -x, and -f <path> names a path (Grok round 5 F2).
        while args and (re.fullmatch(r"-[HLPEXdsx]+", args[0]) or args[0].startswith("-O")
                        or args[0] in ("-D", "-f")):
            if args[0] == "-f":
                targets += args[1:2]
            args = args[2:] if args[0] in ("-D", "-f") else args[1:]
        for w in args:
            if w.startswith("-") or w in ("(", "!", "\\("):
                break
            targets.append(w)
    elif head == "fd":
        # `fd [pattern] [path...]`: the first positional is what is searched FOR (Codex
        # round 3 F1: `cd ../personal && fd x` read `x` as the path and missed the walk),
        # and an option's value is neither (round 4 F1: `fd -e md x` read `md` as the
        # pattern and `x` as the path).
        targets = _fd_targets(words[h + 1:])
    else:
        targets = [w for w in words[h + 1:] if not w.startswith("-")]
    return targets or ["."]


# fd options that take a value, short and long. Their value is never a pattern or a path,
# except --search-path and --base-directory, whose value IS a directory searched.
_FD_VALUE_OPTS = {"-e", "--extension", "-t", "--type", "-d", "--max-depth", "--min-depth",
                  "--exact-depth", "-E", "--exclude", "-S", "--size", "-c", "--color",
                  "-j", "--threads", "--changed-within", "--change-newer-than", "--newer",
                  "--changed-before", "--change-older-than", "--older", "-o", "--owner",
                  "--max-results", "--ignore-file", "--format", "--path-separator",
                  "--batch-size", "--max-buffer-time", "--and", "--hyperlink"}
_FD_PATH_OPTS = {"--search-path", "--base-directory"}


def _fd_targets(args: list) -> list:
    """The directories `fd` searches: positionals after the pattern, plus --search-path and
    --base-directory values. -x/--exec and -X/--exec-batch end the fd arguments."""
    positional, paths, i = [], [], 0
    while i < len(args):
        w = args[i]
        if w in ("-x", "--exec", "-X", "--exec-batch"):
            break
        name, has_eq, value = w.partition("=")
        if w.startswith("--") and has_eq and name in _FD_PATH_OPTS:
            paths.append(value)
        elif w in _FD_PATH_OPTS:
            paths += args[i + 1:i + 2]
            i += 1
        elif w in _FD_VALUE_OPTS:
            i += 1
        elif not w.startswith("-"):
            positional.append(w)
        i += 1
    return positional[1:] + paths


def _resolves_to(word: str, test) -> bool:
    """Does `word`, as a path from the repo root or from any directory the call cds to,
    satisfy `test(rel)`? A glob is judged by the directory it starts in."""
    w = _SHELL_QUOTING.sub("", word)
    if "$" in w or "`" in w or not w:
        return False
    w = w.split("=", 1)[1] if re.match(r"-[\w-]*=", w) else w
    wild = re.search(r"[*?\[]", w)
    if wild:
        w = os.path.dirname(w[:wild.start()]) or "."
    here = os.path.realpath(str(_root()))
    return any(test(_rel_from_root(os.path.realpath(os.path.join(base, os.path.expanduser(w)))))
               for base in [here] + list(_CWDS))


def _reaches_vault(words: list, h: int) -> bool:
    """A word that resolves into the vault (`cd ../personal/data && ls <vault name>`), or a
    walking command whose target is a directory that contains it (`cd .. && ls -R personal`).
    A walk of another folder in the personal repo, or a named file there, passes."""
    vault = THERAPY_REL.lower()
    if any(_resolves_to(w, lambda r: r == vault or r.startswith(vault + "/"))
           for w in words[h + 1:]):
        return True
    return _walks(words, h) and any(_resolves_to(w, lambda r: r in THERAPY_PARENTS)
                                    for w in _walk_targets(words, h))


def _under_sealed(root: Path, p: str) -> bool:
    """Is p the sealed directory or a path in it, at the old in-tree place or anywhere
    in the sibling home, or the vault, a path in it, or a directory above it in the
    personal repo?"""
    p = os.path.join(str(root), os.path.expanduser(p))   # an absolute p stays as it is
    # Resolved on both sides, so a symlink into the sealed tree, or a second spelling of
    # the same directory (/var and /private/var), is judged by where it leads.
    here = os.path.realpath(str(root))
    rel = os.path.relpath(os.path.realpath(p), here).replace(os.sep, "/").lower()
    home = "../" + (os.path.basename(here) + SEALED_HOME_SUFFIX).lower()
    return (rel == SEALED_REL or rel.startswith(SEALED_REL + "/")
            or rel == home or rel.startswith(home + "/")
            or rel == THERAPY_REL or rel.startswith(THERAPY_REL + "/")
            or rel in THERAPY_PARENTS)


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
        wild = re.search(r"[*?\[]", glob)
        start = os.path.dirname(glob[:wild.start()]) if wild else ""
        targets = [base, os.path.join(base, start)] if tool == "Glob" and start else [base]
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
        f"(kept outside the repo, in ../{SEALED_HOME}/), or the personal therapy vault "
        f"{THERAPY_REL}/ or a directory above it ({', '.join(THERAPY_PARENTS)}).",
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
