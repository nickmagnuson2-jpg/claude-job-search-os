#!/usr/bin/env python3
"""hook_runtime.py — the one correct way to take a hook payload off stdin.

WHY THIS EXISTS. Measured 2026-09-07: 33 of 41 `tools/check_*.py` read the hook
payload themselves, in 13 normalized variants (largest cluster 11 files, byte-identical
after whitespace normalization). Those variants are not 13 considered designs. They are
ONE design implemented 13 times with different subsets of the necessary features:

    try/except around the parse ......... 33 / 33
    fail-open on bad input .............. 33 / 33 (via four different mechanics)
    bounded read (deadline + volume cap) .. 1 / 33
    `stop_hook_active` recursion guard .... 3 / 33
    argv examined before reading stdin .... 5 / 33

So the variation was not carrying meaning, it was carrying bugs. Adopting this module
raises a hook to the best-known intake in one move; the alternative is 33 copies that
must be kept in sync by prose, which CLAUDE.md's enforcement-tier rule says converts at
zero. `from hook_runtime import read_payload` is a dependency the interpreter enforces:
you cannot forget to import, because the code does not run.

MECHANISM / POLICY BOUNDARY — the reason this module is small and stays small.

    Mechanism (here):  read stdin safely, bound it, parse it, expose the fields.
                       Every consumer wants the SAME answer.
    Policy (the hook): what counts as a violation, and what to do when the payload
                       is unreadable. Consumers want DIFFERENT answers.

Therefore `read_payload()` NEVER calls `sys.exit`, never prints, and never decides.
It returns a `Payload` and the caller acts. This is deliberate: most hooks fail open,
but at least one (`check_draft_voice.py`) documents that it must fail CONSERVATIVE, and
a module that exits on the caller's behalf would have silently flipped that hook's
contract. Extracting the exit call would be extracting policy.

THE BOUNDED READ is ported verbatim in behaviour from
`check_status_query_verification.read_stdin_bounded`, which is the only bounded reader
in the repo (93 tests, mutation clean) and which is UNWIRED ON PURPOSE, so today it
protects nothing. Four hardening decisions from the 2026-09-07 cross-model review are
necessary and must not be "simplified" back out:

  F1  `sys.stdin.read()` waits for EOF. A producer that writes, flushes, and RETAINS
      the pipe never sends EOF, so the hook stalls until the host kills it. Measured:
      a 300-second live hang. A DEADLINE fixes this; a size cap does not, because the
      problem is waiting, not volume.
  F3  The volume ceiling is enforced AT THE REQUEST (`want = min(...)`), not audited
      after the fact. Asking for a full 64 KiB and checking the total afterwards lets a
      partial read de-align the count from the chunk boundary, so a later read can cross
      the ceiling by up to 65,535 bytes. Reproduced at 1,048,577 bytes.
  F4  There is NO unbounded fallback. An earlier version degraded to `sys.stdin.read()`
      when `select` could not poll the fd, which abandoned the contract at exactly the
      moment it was needed. A bounded reader that cannot poll fails open on what it has.
  --  On the way out we return the chunks already collected rather than discarding them.

Stdlib only. Pure except for the stdin read itself.
"""
from __future__ import annotations

import json
import os
import select
import sys
import time
from dataclasses import dataclass, field
from typing import Any

# A hook payload is a prompt or a tool call, not a data stream: milliseconds in the
# real case. 2 seconds is ~1000x headroom and well under the host's own timeout.
STDIN_DEADLINE_S = 2.0

# Bounds VOLUME, which is a different failure mode from the deadline: a producer
# flooding a pipe for two seconds delivers hundreds of megabytes, and the join/decode/
# parse downstream then costs far more than the deadline saved.
MAX_STDIN_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Payload:
    """A hook payload that has been read and parsed, or an explanation of why not.

    `ok` is the ONLY thing a caller should branch on for validity. It is False for an
    empty stdin, malformed JSON, and a top-level JSON value that is not an object --
    three conditions that were handled inconsistently across the 33 copies (several
    treated a bare `[]` or `"str"` as a dict and relied on `.get` raising).
    """

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    error: str | None = None
    truncated: bool = False
    """True when stdin hit the volume ceiling or the deadline, so the payload was cut
    off mid-stream rather than arriving malformed.

    THIS IS NOT THE SAME CONDITION AS `ok == False` AND MUST NOT BE COLLAPSED INTO IT.
    Malformed JSON means the producer sent garbage, and failing open is defensible.
    Truncation means the guard COULD NOT SEE the content it exists to inspect, and for
    a BLOCK-tier guard failing open there is a silent bypass: write a payload above the
    ceiling and the check passes without ever reading it.

    Origin: 2026-09-14 cross-model verification (F1). Before the payload-intake
    extraction, 32 of 33 hooks used an unbounded `json.load(sys.stdin)`. The shared
    reader introduced a 1 MiB cap, and a truncated payload failed to parse, which every
    consumer treated as ordinary malformed input and allowed. A large file write could
    therefore bypass the public-repo PII gate. Consumers that BLOCK must branch on this
    flag; see `check_public_pii.py` for the reference handling.
    """

    def _require_ok(self) -> None:
        """Reading a field off an unreadable payload is a bug in the CALLER.

        WHY THIS RAISES INSTEAD OF RETURNING "". Measured on the 7 pilot adopters
        2026-09-07: with a silently-empty payload, deleting a hook's
        `if not p.ok: sys.exit(0)` changed nothing observable -- every one of those
        hooks already exits 0 when the extracted field is empty, so the guard was
        defence in depth that no test could distinguish. Mutation testing scored it
        as a survivor in all 7, and the only remedies on offer were to allowlist
        ~3 equivalent mutants per hook (~100 entries across 33 sites) or to drop the
        guard and let fail-open become implicit -- in a repo that runs a
        test_no_silent_failures suite.

        Raising makes the guard OBSERVABLE: skip the `.ok` check and the hook dies on
        the first field access instead of quietly judging a payload it never read. So
        the check now changes behaviour, its mutants die, and "did this consumer really
        adopt the contract" becomes a behavioural question rather than a grep for an
        import line.

        `data`, `raw` and `error` stay readable -- a caller must be able to inspect
        what went wrong.
        """
        if not self.ok:
            raise ValueError(
                f"hook payload was not read ({self.error}); check `.ok` and apply your "
                "own fail-open or fail-conservative policy before reading fields")

    @property
    def tool_name(self) -> str:
        self._require_ok()
        return self.data.get("tool_name") or ""

    @property
    def tool_input(self) -> dict[str, Any]:
        self._require_ok()
        ti = self.data.get("tool_input")
        return ti if isinstance(ti, dict) else {}

    @property
    def stop_hook_active(self) -> bool:
        """True when we are already inside our own Stop chain.

        Reading the flag is mechanism (every consumer wants the same answer). ACTING on
        it stays with the caller, because a PreToolUse hook has no such chain to guard.
        """
        self._require_ok()
        return self.data.get("stop_hook_active") is True

    @property
    def file_path(self) -> str:
        # No _require_ok() here: this reads through `tool_input`, which gates.
        # A second call is unreachable and mutation-invisible (survivors, 2026-09-07).
        ti = self.tool_input
        return ti.get("file_path") or ti.get("path") or ""

    @property
    def command(self) -> str:
        # No _require_ok() here: this reads through `tool_input`, which gates.
        # A second call is unreachable and mutation-invisible (survivors, 2026-09-07).
        return self.tool_input.get("command") or ""

    @property
    def content(self) -> str:
        """Write `content` or Edit `new_string`, whichever this call carries.

        This exact `or` chain appeared independently in 5 hooks. It is here so the next
        one does not have to rediscover that an Edit has no `content` key.
        """
        # No _require_ok() here: this reads through `tool_input`, which gates.
        # A second call is unreachable and mutation-invisible (survivors, 2026-09-07).
        ti = self.tool_input
        return ti.get("content") or ti.get("new_string") or ""

    @property
    def cwd(self) -> str:
        self._require_ok()
        return self.data.get("cwd") or ""


def read_stdin_bounded_ex(deadline_s: float = STDIN_DEADLINE_S,
                          max_bytes: int = MAX_STDIN_BYTES) -> tuple[str, bool]:
    """Read stdin until EOF, the deadline, or the volume ceiling -- whichever is first.

    Never raises. Returns (text, truncated). `truncated` is True when a bound stopped
    the read before EOF, which the caller MUST be able to distinguish from a clean read:
    see the `Payload.truncated` docstring for the silent-bypass this prevents.
    """
    try:
        fd = sys.stdin.fileno()
    except Exception:
        return "", False  # F4: no unbounded fallback.

    chunks: list[bytes] = []
    total = 0
    truncated = False
    end = time.monotonic() + deadline_s
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            truncated = True  # deadline, not EOF
            break
        try:
            ready, _, _ = select.select([fd], [], [], remaining)
        except (OSError, ValueError):
            break  # F4 again: return what we have, do not discard it.
        if not ready:
            truncated = True  # deadline, not EOF
            break
        # F3: the ceiling is enforced AT THE REQUEST. The `total >= max_bytes` break
        # below makes `want` unreachable at 0 for any positive max_bytes, so there is
        # no second guard here -- a `if want <= 0: break` was removed as dead code
        # (mutation-proven: no test could distinguish it, because os.read(fd, 0)
        # returns b"" and takes the EOF branch to the same result).
        want = min(65536, max_bytes - total)
        try:
            chunk = os.read(fd, want)
        except (OSError, ValueError):
            break
        if not chunk:
            break  # EOF, the normal path.
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            truncated = True  # volume ceiling, not EOF
            break
    return b"".join(chunks).decode("utf-8", "replace"), truncated


def read_stdin_bounded(deadline_s: float = STDIN_DEADLINE_S,
                       max_bytes: int = MAX_STDIN_BYTES) -> str:
    """The original str-returning contract, unchanged, kept for existing callers.

    New code that needs to know whether a bound cut the read short calls
    `read_stdin_bounded_ex()` and gets (text, truncated). This wrapper exists because
    changing a published return type in place is how callers break silently, which is
    a documented failure mode in this repo.
    """
    text, _ = read_stdin_bounded_ex(deadline_s=deadline_s, max_bytes=max_bytes)
    return text


def read_payload(deadline_s: float = STDIN_DEADLINE_S,
                 max_bytes: int = MAX_STDIN_BYTES) -> Payload:
    """Read and parse the hook payload from stdin. NEVER exits, prints, or decides.

    The caller inspects `.ok` and applies its own policy:

        p = read_payload()
        if not p.ok:
            sys.exit(0)          # fail open -- what 33 of 33 do today
        if p.stop_hook_active:
            sys.exit(0)          # Stop hooks only
    """
    raw, truncated = read_stdin_bounded_ex(deadline_s=deadline_s, max_bytes=max_bytes)
    if not raw.strip():
        return Payload(ok=False, raw=raw, error="empty stdin", truncated=truncated)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        if truncated:
            return Payload(ok=False, raw=raw, truncated=True,
                           error=f"payload TRUNCATED at a read bound, then unparseable: {exc}")
        return Payload(ok=False, raw=raw, error=f"malformed JSON: {exc}")
    if not isinstance(data, dict):
        return Payload(ok=False, raw=raw, truncated=truncated,
                       error=f"payload is {type(data).__name__}, not an object")
    return Payload(ok=True, data=data, raw=raw, truncated=truncated)
