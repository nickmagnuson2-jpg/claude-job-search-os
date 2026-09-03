#!/bin/bash
# Install this repo's tracked git hooks into .git/hooks/.
#
# Why this exists: .git/hooks/ is NOT version-controlled. The pre-push PII gate
# was therefore invisible to a fresh clone, which silently had no gate at all on
# a PUBLIC repo. tools/prepush_pii_guard.py was tracked; the hook that CALLS it
# was not, so the guard existed and never ran.
#
# Usage:  bash tools/hooks/install.sh [--check]
#   --check  verify installed hooks match the tracked copies; exit 1 if not.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
src="$root/tools/hooks"

# THE PATH GIT WILL ACTUALLY USE, not the one we assume. `core.hooksPath` overrides
# .git/hooks entirely, and this repo runs an overlay GIT_DIR arrangement, so the two
# can and did diverge. Until 2026-09-03 this script hardcoded "$root/.git/hooks":
# it installed there, --check verified there, and it reported "ok: pre-push" while
# core.hooksPath pointed at a directory that DID NOT EXIST. Net effect: no pre-push
# hook ran at all -- on a PUBLIC repo whose only push-time PII gate lives in one --
# and the checker that existed to catch exactly this said everything was fine.
# Found by cross-model review, 2026-09-03.
dst="$(git rev-parse --git-path hooks)"
configured="$(git config --get core.hooksPath || true)"

if [ -n "$configured" ] && [ ! -d "$dst" ]; then
  echo "FATAL: core.hooksPath is set to '$configured' but that directory does not exist." >&2
  echo "       Git runs NO hooks in this state, so every push-time gate is silently off." >&2
  echo "       Fix one of:" >&2
  echo "         git config --local --unset core.hooksPath   # use \$root/.git/hooks" >&2
  echo "         mkdir -p '$configured'                      # keep the configured path" >&2
  exit 1
fi
mkdir -p "$dst"
check_only=0
[ "${1:-}" = "--check" ] && check_only=1

status=0
for hook in "$src"/*; do
  name="$(basename "$hook")"
  [ "$name" = "install.sh" ] && continue
  target="$dst/$name"
  if [ "$check_only" -eq 1 ]; then
    if [ ! -f "$target" ]; then
      echo "MISSING: $name is not installed in $dst"; status=1
    elif ! cmp -s "$hook" "$target"; then
      echo "DRIFTED: $target differs from the tracked $hook"; status=1
    else
      echo "ok: $name"
    fi
  else
    install -m 0755 "$hook" "$target"
    echo "installed: $name -> $target"
  fi
done
exit $status
