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


def _guarded_target(path: str, cwd: str = "", env: dict | None = None) -> bool:
    """A guard file of THIS repo. Copies into another checkout (a scratchpad worktree
    for a mutation run, `cp x $W/tools/check_y.py`) are not guard edits: the replay
    of 88db514's review fixes flagged exactly that. A path that cannot be resolved
    (an unknown $VAR) is judged as written, which blocks."""
    if not (is_guarded(path) or ("/" not in path and bool(_GUARD_BASENAME.match(path)))):
        return False
    from check_public_pii import _expand_vars
    candidates = (_expand_vars(path, env or {}) or [path]) if "$" in path else [path]
    for c in candidates:
        if "$" in c:
            return True
        full = os.path.abspath(os.path.join(cwd or os.getcwd(), os.path.expanduser(c)))
        if full == _REPO_ROOT or full.startswith(_REPO_ROOT + os.sep):
            return True
    return False


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


def _script_rewrites_a_guard(text: str) -> bool:
    """An interpreter script that names a guard path and calls a write. A heuristic:
    it cannot see a path built from parts (`Path("tools") / name`)."""
    return bool(_GUARD_IN_TEXT.search(text)) and bool(_WRITE_CALL.search(text))


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
    env = command_assignments(command)
    hits = [t for t in extract_write_targets(command, env) if _guarded_target(t, cwd, env)]
    for cmd in simple_commands(command):
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
            elif name == "git" and rest[:1] and rest[0] in ("checkout", "restore", "rm",
                                                             "mv", "stash"):
                paths = _non_options(rest[1:])
            elif name in ("perl", "ruby") and any(a.startswith("-i") or a.startswith("-pi")
                                                  for a in rest):
                paths = _non_options(rest)
            hits += [p for p in paths if _guarded_target(p, cwd, env)]
            for text in scripts_run_by(cmd, i, None):
                hits += bash_guard_writes(text, cwd, _depth + 1)
            if name in _SHELLS:
                # `bash patch.sh`: the file's commands run too (Grok review of
                # 88db514, F3: the scratchpad incident with a shell script).
                for script in _shell_script_files(rest):
                    hits += bash_guard_writes(_read_script(script, cwd), cwd, _depth + 1)
            if _INTERPRETER.match(name):
                texts = list(_stdin_texts(cmd))
                for k, a in enumerate(rest):
                    if a in ("-c", "-e", "-E") and k + 1 < len(rest):
                        texts.append(rest[k + 1])
                        break
                    if a == "-m":
                        break
                    if not a.startswith("-"):
                        texts.append(_read_script(a, cwd))
                        break
                if any(_script_rewrites_a_guard(t) for t in texts):
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
