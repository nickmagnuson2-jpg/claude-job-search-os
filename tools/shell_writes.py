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
a block. Replayed over 37,700 past Bash calls on 2026-11-01, it added none that the probed walk
did not also give from the real repo root, and it starts no subprocess.

The cd parsing, the shell probe and apply_cd still live in check_public_pii, which the other
gates already import their write-target parser from. Imported at module level here, and
check_public_pii imports this module inside main(), so neither import is circular.
"""
from __future__ import annotations

import os
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


def _group_body(segment: str) -> str | None:
    """The body of a `( ... )` or `{ ...; }` group, or None for any other segment."""
    s = segment.strip()
    if s[:1] == "(":
        end = s.rfind(")")
    elif s[:1] == "{" and s[1:2].isspace():
        end = s.rfind("}")
    else:
        return None
    return s[1:end] if end > 0 else None


def _walk(pairs, env, cwds, exact):
    """The segment loop of walk(). Returns the directories the shell may be in after it."""
    for segment, before in pairs:
        # A `( ... )` or `{ ...; }` group stays ONE segment in the parser, so a cd inside it
        # was never seen (Codex eic-review r2 F1). Walk its body from here; a cd there only
        # ADDS candidates, and the directories it reaches stay candidates after the group:
        # right for `{ }`, where a cd persists, and only cautious for a subshell. The group
        # itself is still judged from outside, for a redirect such as `(...) > f`.
        body = _group_body(segment)
        if body is not None:
            cwds = yield from _walk(_pii.segments_with_separators(body), env, cwds, False)
            yield segment, env, cwds
            continue
        operand = _pii.pure_cd_operand(segment)
        if operand is not None:
            cwds = _pii.apply_cd(cwds, operand, certain=exact and before <= {";", "\n"})
            continue                                # a bare cd writes nothing
        # `dir=tools; cd "$dir"` moves the shell to every value the command gives dir.
        # Each is ADDED (never certain), so the old directories stay. A variable the
        # command does not assign adds nothing.
        text = _pii.cd_operand_text(segment)
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
    starts = _pii.start_cwds([cwd]) + [str(root), cwd]
    out: list[str] = []
    for segment, env, cwds in walk(command, starts, exact=False):
        cands = list(_pii.extract_write_targets(segment, env)) + copy_destinations(segment)
        out.extend(resolve(cands, cwds))
    return list(dict.fromkeys(out))
