"""Guard: the three code shapes that produced the sweep false-cleans must not re-enter.

Companion to `test_sweep_helper.py`. That file proves the helper is correct; this one
proves the repo's own shell-shaped sweep code does not reproduce the defects the helper
exists to replace:

  A. `for x in $LIST` — an unquoted variable as a loop scope. Shell state does not persist
     between Bash tool calls, so $LIST is routinely empty and the loop runs zero times,
     printing nothing and reading as clean.
  B. `grep`/`xargs` handed a bare variable file list instead of literal paths. Same failure,
     one layer down: an empty expansion makes grep read stdin or scan nothing.
  C. Fixed-string/substring sweeping (`grep -F`) outside the one place that opted in. A short
     token then matches inside unrelated words (~190 false hits, 2026-08-12).

VACUITY IS THE RISK HERE. As of 2026-08-13 the tree contains none of these shapes — the real
fires happened in ad-hoc Bash calls, not in committed code — so a plain scan would be green
by construction and would stay green even if the detector broke. Every check therefore runs
a POSITIVE CONTROL first: a synthetic file carrying the bad shape must be caught. A detector
that cannot find the planted violation fails the test regardless of what the repo looks like.

Scope is shell-shaped text only: `*.sh` under tools/, and bash code fences in skill/doc
markdown. Python sweep code is covered by the helper's own contract.
"""
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from sweep import sweep  # noqa: E402  (dogfooding: the guard reports its own denominator)

# A: `for x in $LIST` / `for x in ${LIST}` — positional params and quoted "$@" are fine.
SIG_UNQUOTED_LOOP = re.compile(r"\bfor\s+[A-Za-z_]\w*\s+in\s+\$\{?[A-Za-z_]\w*")
# B: grep/xargs whose file operand is a bare UNQUOTED variable. Quoted spans are stripped
# before matching -- `grep -q "^### $TODAY" "$LOG_FILE"` is correct code, and a regex that
# flags it trains people to disable the guard.
SIG_VAR_FILE_LIST = re.compile(r"\b(grep|rg|xargs)\b[^|\n]*?\s\$\{?[A-Za-z_]\w*")
# C: fixed-string matching in a grep invocation.
SIG_FIXED_STRING = re.compile(r"\bgrep\s+(?:-\w+\s+)*-\w*F\w*\b")

SIGNATURES = {
    "A_unquoted_loop_scope": SIG_UNQUOTED_LOOP,
    "B_variable_file_list": SIG_VAR_FILE_LIST,
    "C_substring_matching": SIG_FIXED_STRING,
}

PLANTED = {
    "A_unquoted_loop_scope": 'for f in $FILES; do grep -n tok "$f"; done',
    "B_variable_file_list": 'grep -rn "token" $TARGETS',
    "C_substring_matching": 'grep -qiF "$tok" file.md',
}

# Deliberate, reviewed exceptions, as (repo-relative path, signature name). Each must say
# why. Empty is the goal, and it is currently achievable — a dead entry here is worse than
# none, because it reads as a known-bad spot that no longer exists.
ALLOWLIST: set[tuple[str, str]] = set()


def shell_shaped_files() -> list[Path]:
    paths: list[Path] = []
    paths += sorted((REPO_ROOT / "tools").rglob("*.sh"))
    paths += sorted((REPO_ROOT / ".claude" / "skills").rglob("*.md"))
    paths += sorted((REPO_ROOT / "docs").rglob("*.md"))
    return [p for p in paths if p.is_file()]


_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def strip_quoted(line: str) -> str:
    """Blank out quoted spans. A `$VAR` inside quotes is a value, not an unquoted scope."""
    return _QUOTED.sub(" ", line)


def bash_text(path: Path) -> str:
    """For markdown, only the bash/sh fences; for .sh, the whole file."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix != ".md":
        return text
    return "\n".join(
        m.group(1) for m in re.finditer(r"```(?:bash|sh|shell)\n(.*?)```", text, re.S)
    )


@pytest.mark.parametrize("name", sorted(SIGNATURES))
def test_detector_catches_a_planted_violation(name, tmp_path):
    """Positive control: without this, a broken detector reads as a clean repo."""
    planted = tmp_path / "planted.sh"
    planted.write_text("#!/bin/bash\n" + PLANTED[name] + "\n", encoding="utf-8")
    assert SIGNATURES[name].search(strip_quoted(bash_text(planted))), (
        f"detector {name} did not catch its own planted violation — it cannot clear the repo"
    )


@pytest.mark.parametrize("name", sorted(SIGNATURES))
def test_repo_is_free_of_the_signature(name):
    files = shell_shaped_files()
    assert files, "found ZERO shell-shaped files — an empty sweep is not a pass"

    pattern = SIGNATURES[name]
    offenders = []
    scanned = 0
    for path in files:
        body = bash_text(path)
        if not body.strip():
            continue
        scanned += 1
        rel = path.relative_to(REPO_ROOT).as_posix()
        if (rel, name) in ALLOWLIST:
            continue
        for lineno, line in enumerate(body.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue  # a comment quoting the shape is not a use of it
            if pattern.search(strip_quoted(line)):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")

    assert scanned > 0, "scanned zero non-empty bodies — the fence extractor stopped matching"
    assert not offenders, (
        f"{name}: {len(offenders)} occurrence(s) across {scanned} scanned file(s):\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_reports_its_denominator():
    """The rule this enforces applies to the enforcer: state what you scanned."""
    files = shell_shaped_files()
    result = sweep(files, "grep", control="`")
    print(f"\n[sweep signature guard] scanned {result['scanned']} shell-shaped files")
    assert result["scanned"] > 0
