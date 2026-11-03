"""shell_writes.py - where a Bash command writes, with the shell's working directory tracked.

WHY THIS EXISTS (2026-11-01, review finding J, approved by Nick the same day). A relative write
target means nothing until you know the directory the shell is in when the write runs. Three
gates judge Bash writes. check_public_pii tracked the directory (a `cd` earlier in the
command, see its apply_cd); check_eic_review and check_handoff_producer did not, so
`cd output/linkedin-profile/voice-sources && echo x > a-published.md` passed the EIC gate
while the same write by its full path was blocked. Patching each hook would leave three
copies to drift, so the walk lives here and every gate calls it.

MECHANISM, NOT POLICY. This module answers "which files might this command write, from which
directories". What counts as a violation stays in each hook.

TWO CALLERS, TWO PRECISIONS. check_public_pii calls walk() with the starting directories it
trusts and lets each `cd` be confirmed by a shell probe, so a cd that certainly moved the shell
REPLACES the candidates. The blocking gates (check_eic_review, check_handoff_producer) call
bash_write_targets(), which skips the probe: every cd only ADDS candidates, so their answer is a
superset, and it starts from the repo root and the payload's cwd as well. A superset can only add
a block, and it starts no subprocess. Each change to the walk is replayed over the real Bash
history before it lands; the counts are in the commit messages, not here, so this docstring
cannot go stale (Fable r4 F7).

The cd parsing, the shell probe and apply_cd still live in check_public_pii, which the other
gates already import their write-target parser from. Imported at module level here, and
check_public_pii imports this module inside main(), so neither import is circular.
"""
from __future__ import annotations

import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_public_pii as _pii  # noqa: E402


def walk(command: str, starts, exact: bool | None = None):
    """Yield (segment, env, cwds) for each segment of `command` that is not a bare cd.

    `cwds` is every directory the shell could be in when that segment runs. It begins as the
    absolute directories in `starts`, and each `cd` moves or widens it
    (check_public_pii.apply_cd). `env` holds the values the command assigns to shell
    variables, for the target extractor.

    `exact=None` asks check_public_pii.cwd_tracking_is_exact, which starts a shell to confirm
    each `cd` lands where it reads; only then does a cd REPLACE the candidates. `exact=False`
    skips that probe, so every cd only adds candidates.
    """
    env = _pii.command_assignments(command)
    pairs = _pii.segments_with_separators(command)
    cwds = list(dict.fromkeys(os.path.normpath(str(d)) for d in starts
                              if d and os.path.isabs(str(d))))
    if exact is None:
        exact = _pii.cwd_tracking_is_exact(command, pairs, cwds)
    yield from _walk(pairs, env, cwds, exact)


# Words that can open a segment without changing what it does: `! (...)`, `time {...}`, and
# the bodies of if/for/while (`then cd x`, `do cd x`, `else cd x`, `elif cd x`). Codex r3 F1,
# Fable r3 F1, Fable r4 F3. Wrappers (`env -i cp`) are found by _wrapped_copies instead.
_PREFIX = re.compile(r"^\s*(?:(?:!|time(?:\s+-p)?|then|do|else|elif)\s+)+")
# A redirect to /dev/null or to another fd on a cd changes nothing about where it lands.
# Covers >, 2>, &> and >& to /dev/null, and >&N (Codex r4 F2, Fable r4 F2).
_QUIET = re.compile(r"(?:\s+\d*|\s*&|\s*)>{1,2}(?:&\d|&?\s*/dev/null)")
_COMPOUND = re.compile(r"(if|for|select|while|until|case)\b")       # select: Fable r5 F3
_COMPOUND_END = {"if": "fi", "for": "done", "select": "done", "while": "done", "until": "done",
                 "case": "esac"}
# `case WORD in`, and an arm's `pattern)` label, which would hide a cd that opens the arm
# (Codex r4 F1, Fable r4 F1).
_CASE_HEAD = re.compile(r"^\s*\S+\s+in\b")
_CASE_LABEL = re.compile(r"^\s*\(?(?:\\.|[^();\\])*\)")    # `a\))`: an escaped ) (Codex r5)
_MAX_DEPTH = 32       # deeper groups are judged as one segment; recursion must not crash a hook


def _plain(segment: str) -> str:
    """The segment with leading `!`, `time`, `then`, `do` or `else` removed."""
    return _PREFIX.sub("", segment)


def _cd_segment(segment: str) -> str:
    """The segment as a plain `cd`: pushd read as cd, /dev/null redirects dropped."""
    seg = _QUIET.sub("", _plain(segment)).strip()
    return "cd" + seg[5:] if seg.startswith("pushd ") else seg


def _group_body(segment: str) -> str | None:
    """The body of a `( ... )` or `{ ...; }` group, or None for any other segment."""
    s = _plain(segment).strip()
    if s[:1] == "(":
        end = s.rfind(")")
    elif s[:1] == "{" and s[1:2].isspace():
        end = s.rfind("}")
    else:
        # if/for/while/until/case are also kept whole by the parser (Fable r3 F1).
        m = _COMPOUND.match(s)
        if not m:
            return None
        end = s.rfind(_COMPOUND_END[m.group(1)])
        if end <= m.end():
            return None
        body = s[m.end():end]
        if m.group(1) == "case":
            arms = re.split(r";;&?|;&", _CASE_HEAD.sub("", body, count=1))
            body = "; ".join(_CASE_LABEL.sub("", arm, count=1) for arm in arms)
        return body
    return s[1:end] if end > 0 else None


def _walk(pairs, env, cwds, exact, depth=0):
    """The segment loop of walk(). Returns the directories the shell may be in after it."""
    for segment, before in pairs:
        # A `( ... )` or `{ ...; }` group stays ONE segment in the parser, so a cd inside it
        # was never seen (Codex eic-review r2 F1). Walk its body from here; a cd there only
        # ADDS candidates, and the directories it reaches stay candidates after the group:
        # right for `{ }`, where a cd persists, and only cautious for a subshell. The group
        # itself is still judged from outside, for a redirect such as `(...) > f`.
        body = _group_body(segment) if depth < _MAX_DEPTH else None
        if body is not None:
            inner = yield from _walk(_pii.segments_with_separators(body), env, cwds, False,
                                     depth + 1)
            # Everything but a ( ) subshell runs in this shell, so a cd in it persists;
            # a subshell's does not (Fable r3 F4).
            if _plain(segment).strip()[:1] != "(":
                cwds = inner
            yield segment, env, cwds
            continue
        cd_seg = _cd_segment(segment)
        operand = _pii.pure_cd_operand(cd_seg)
        if operand is not None:
            cwds = _pii.apply_cd(cwds, operand, certain=exact and before <= {";", "\n"})
            continue                                # a bare cd writes nothing
        # `dir=tools; cd "$dir"` moves the shell to every value the command gives dir.
        # Each is ADDED (never certain), so the old directories stay. A variable the
        # command does not assign adds nothing.
        text = _pii.cd_operand_text(cd_seg)
        if text is not None and text.startswith("~"):
            # `cd ~/x`: the redirect extractor expands ~, so the cd must too (Fable r3 F2).
            cwds = _pii.apply_cd(cwds, os.path.expanduser(text), certain=False)
            continue
        if text is not None and _pii._VAR_REF.search(text):
            for value in _pii._expand_vars(text, env):
                if (value and value[0] not in "-+"
                        and not _pii._CD_OPERAND_UNSAFE.search(value)):
                    cwds = _pii.apply_cd(cwds, value, certain=False)
            continue
        # When tracking is not exact no cd is `certain`, so apply_cd only ever adds
        # candidates and the starting directories stay among them.
        yield segment, env, cwds
    return cwds


def resolve(candidates, cwds) -> list[str]:
    """Each candidate as a path: an absolute one as is, a relative one joined to every cwd."""
    out: list[str] = []
    for cand in candidates:
        if not cand:
            continue
        out.extend([cand] if os.path.isabs(cand) else [os.path.join(c, cand) for c in cwds])
    return list(dict.fromkeys(out))


def _wrapped_copies(segment: str, copy_destinations) -> list[str]:
    """Destinations of a cp/mv/rsync/install run through a wrapper (`env -i cp`,
    `command -- cp`, `exec -a x mv`, `sudo -u u cp`). Where the wrapped command starts is
    check_public_pii's _all_command_positions, the one parser of wrapper options, so the
    copy gates and the PII gate agree on it (Codex r7 29.F1, r8; Fable r7 35.F3)."""
    out: list[str] = []
    for cmd in _pii.simple_commands(segment):
        words = cmd.words
        # copy_destinations reads a copy only at the start, so every command position can
        # be handed over as is; the ones that are not cp/mv/rsync/install return nothing.
        for k in sorted(_pii._all_command_positions(words)):
            out += copy_destinations(" ".join(shlex.quote(w) for w in words[k:]))
    return out


def bash_write_targets(command: str, cwd: str, root) -> list[str]:
    """Every path `command` may write, from every directory the shell may be in: where the
    hook runs, the payload's session `cwd`, the repo `root`, and wherever a cd adds (no probe;
    see the module docstring).

    Redirects, tee, dd and sed -i come from check_public_pii.extract_write_targets, and
    cp/mv/rsync/install destinations from check_handoff_producer.copy_destinations, applied
    per segment. Every Bash write gate reads its targets from here (check_eic_review,
    check_handoff_producer, check_build_log_present; check_public_pii uses walk()).
    """
    # Imported here: check_handoff_producer imports this module at its top.
    from check_handoff_producer import copy_destinations
    out: list[str] = []
    for segment, env, cwds in walk(command, [os.getcwd(), str(root), cwd], exact=False):
        # _plain: a write that opens an if/for/else body (`then cp a b`) is still a write
        # (Fable r5 F1).
        segment = _plain(segment)
        cands = (list(_pii.extract_write_targets(segment, env)) + copy_destinations(segment)
                 + _wrapped_copies(segment, copy_destinations))
        out.extend(resolve(cands, cwds))
    return list(dict.fromkeys(out))
