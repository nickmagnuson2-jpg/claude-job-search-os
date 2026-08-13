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
dst="$root/.git/hooks"
check_only=0
[ "${1:-}" = "--check" ] && check_only=1

status=0
for hook in "$src"/*; do
  name="$(basename "$hook")"
  [ "$name" = "install.sh" ] && continue
  target="$dst/$name"
  if [ "$check_only" -eq 1 ]; then
    if [ ! -f "$target" ]; then
      echo "MISSING: $name is not installed in .git/hooks/"; status=1
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
