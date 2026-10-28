#!/usr/bin/env python3
"""
check_frozen_file.py -- PreToolUse hook for Claude Code (Write|Edit|MultiEdit|NotebookEdit).

Blocks a write to an existing file that has no owner-write bit.

Why this exists:
  A build freezes its shipped files with `chmod a-w` and a snapshot folder, so that after the
  freeze only logged changes happen. On 2026-10-27 the Edit tool wrote a frozen file
  anyway: Claude Code's file tools do not stop at the permission bit, so the file silently
  differed from its freeze snapshot and the change was logged only after the fact. A frozen
  file changing is a decision, so the way through is the explicit unlock, which the log records:

      chmod u+w <file>   # then edit, then
      chmod a-w <file>   # and write a reopening Entry in the BUILD-LOG

What blocks: tool is Write, Edit, MultiEdit or NotebookEdit; the target exists, is a regular
file, and lacks S_IWUSR. The owner bit decides because it is the one a freeze removes.
What passes: a new file, a writable file, a directory, any other tool, a malformed payload
(fail open, the policy of every hook here). A payload cut off at the stdin cap is NOT treated
as malformed: its tool and path are read from the raw prefix, so a large Write still checks. Bash writes are out of scope: a Bash `>` or Python
write to a read-only file already fails at the OS, and a `sed -i` rename is not covered.

Exit codes:
  0 -- allowed / not applicable / fail-open
  2 -- the target is frozen
"""
import json
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_runtime import read_payload  # noqa: E402

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def is_frozen(path: Path) -> bool:
    """True when `path` is an existing regular file without the owner-write bit."""
    try:
        st = path.stat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not (st.st_mode & stat.S_IWUSR)


TOO_LARGE = "<target past the stdin cap>"
RAW_TOOL_RE = re.compile(r'"tool_name"\s*:\s*"([^"\\]+)"')
RAW_PATH_RE = re.compile(r'"(?:file_path|notebook_path)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def target_of(p) -> tuple[str, str]:
    """(tool name, target path) from a read payload, or from the raw prefix of one cut
    off at the stdin cap. A Write past the cap is still a write (review 734.F3 / 735.F4),
    and NotebookEdit names its target `notebook_path` (review 734.F2 / 735.F3)."""
    if p.ok:
        ti = p.tool_input
        return p.tool_name, ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
    if not p.truncated:
        return "", ""
    tool, path = RAW_TOOL_RE.search(p.raw), RAW_PATH_RE.search(p.raw)
    if tool and not path:
        # The path lies past the cap, so the target cannot be seen. A write this large is
        # rare; blocking it costs one Bash write, waving it through could rewrite a frozen
        # file (review 736.F2 / 737.F2).
        return tool.group(1), TOO_LARGE
    if not (tool and path):
        return "", ""
    try:
        return tool.group(1), json.loads(f'"{path.group(1)}"')
    except ValueError:
        return "", ""


def main() -> None:
    p = read_payload()
    tool, path = target_of(p)
    if tool not in WRITE_TOOLS or not path:
        sys.exit(0)  # fail open: a malformed payload or no target is not evidence of a freeze
    if path == TOO_LARGE:
        print(f"BLOCKED check_frozen_file: this {tool} payload is too large to read its target, "
              "so it cannot be checked against a freeze. Write the file through Bash "
              "(python3 or cat), which the OS blocks on a read-only file.", file=sys.stderr)
        sys.exit(2)
    target = Path(path)
    if not target.is_absolute():
        target = Path((p.cwd if p.ok else "") or os.getcwd()) / target
    if not is_frozen(target):
        sys.exit(0)
    print(
        f"BLOCKED check_frozen_file: {target} is frozen (read-only, no owner-write bit).\n\n"
        "A frozen file changes only by an explicit, logged unlock:\n"
        f"  chmod u+w '{target}'\n"
        "  ...edit...\n"
        f"  chmod a-w '{target}'\n"
        "then add a reopening Entry to the build's BUILD-LOG (what changed, why, which rep or\n"
        "review exposed it). If the freeze is over, unlock it for good with chmod u+w.\n"
        "Rule: framework/artifact-workflow.md section 4 V2 (AR28, AR34).",
        file=sys.stderr,
    )
    sys.exit(2)

if __name__ == "__main__":
    main()
