#!/bin/bash
# Backup private job search data to a PRIVATE git remote.
# Tracks: data/, output/, coaching/, memory/ (repo-local + canonical sidecar mirror +
#         global-claude-mirror of ~/.claude hooks/skills/settings.json),
#         inbox/, _archive/, private framework/*.md docs, private tools/ config,
#         .claude/skills/scan-jobs/cache.md
#
# This repo is PUBLIC, so the private remote's name, account, and overlay git-dir
# are NOT hardcoded here. They load from the gitignored tools/.private-backup.conf:
#
#     PRIVATE_GIT_DIR="$HOME/.your-private-git"
#     PRIVATE_REPO_URL="https://github.com/<account>/<private-repo>"
#
# Missing config is a HARD failure, not a silent skip: a backup that appears to run
# but pushes nowhere is worse than no backup at all.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_TREE="$(dirname "$SCRIPT_DIR")"
CONF="$SCRIPT_DIR/.private-backup.conf"

if [ ! -f "$CONF" ]; then
  echo "ERROR: $CONF not found." >&2
  echo "Private backup is not configured on this machine. Create it with:" >&2
  echo '  PRIVATE_GIT_DIR="$HOME/.your-private-git"' >&2
  echo '  PRIVATE_REPO_URL="https://github.com/<account>/<private-repo>"' >&2
  exit 1
fi
# shellcheck source=/dev/null
. "$CONF"

if [ -z "$PRIVATE_GIT_DIR" ]; then
  echo "ERROR: PRIVATE_GIT_DIR unset in $CONF" >&2
  exit 1
fi

GIT_DIR="$PRIVATE_GIT_DIR"
# Claude Code's project-memory dir slug is the work-tree path with '/' replaced by '-'.
# Derived rather than hardcoded so this carries no username.
CANONICAL_MEMORY="$HOME/.claude/projects/$(printf '%s' "$WORK_TREE" | tr '/' '-')/memory"

GIT="git --git-dir=$GIT_DIR --work-tree=$WORK_TREE"

# The canonical auto-memory sidecar lives outside the repo entirely (Claude Code's
# actual session-loaded memory dir), so it's invisible to the git add below unless
# mirrored in first. rsync into a repo-local subfolder (gitignored from the PUBLIC
# repo like the rest of memory/) so the existing add/commit/push picks it up as-is.
if [ -d "$CANONICAL_MEMORY" ]; then
  echo "Mirroring canonical memory sidecar..."
  mkdir -p "$WORK_TREE/memory/canonical-sidecar"
  rsync -a --delete "$CANONICAL_MEMORY/" "$WORK_TREE/memory/canonical-sidecar/"
else
  echo "WARNING: canonical memory sidecar not found at $CANONICAL_MEMORY" >&2
fi

# The global Claude config -- hooks, skills, and the settings.json that WIRES them -- lives
# in ~/.claude and was covered by nothing until 2026-08-13. Restoring 145MB of data while
# losing the machinery that produces it is not a backup: `lessons-learned` is the skill that
# writes the memory corpus, and `memory-last-cited-stamp.js` is the instrument behind the
# whole demotion signal.
#
# Mirrored UNDER memory/ deliberately. A new top-level directory would NOT be gitignored by
# the public repo (verified with `git check-ignore`), and these files carry real contact and
# company names -- so a top-level mirror would publish them. `/memory/` is already ignored
# (.gitignore line 48), so this inherits protection that exists and is tested, rather than
# depending on a new ignore rule staying correct forever.
#
# Everything is mirrored, including the ~26 gsd-* plugin files. Filtering to "just the custom
# ones" means inferring provenance from a filename prefix, and a NEW custom hook that did not
# match the filter would silently never be backed up. The bytes (~1.7MB against ~145MB) are
# free; the filter is the risk. See feedback_shape_is_not_provenance_for_destructive_ops.
GLOBAL_CLAUDE="$HOME/.claude"
GLOBAL_MIRROR="$WORK_TREE/memory/global-claude-mirror"
if [ -d "$GLOBAL_CLAUDE" ]; then
  echo "Mirroring global Claude config (hooks, skills, settings)..."
  mkdir -p "$GLOBAL_MIRROR"
  for sub in hooks skills; do
    if [ -d "$GLOBAL_CLAUDE/$sub" ]; then
      mkdir -p "$GLOBAL_MIRROR/$sub"
      rsync -a --delete "$GLOBAL_CLAUDE/$sub/" "$GLOBAL_MIRROR/$sub/"
    else
      echo "WARNING: $GLOBAL_CLAUDE/$sub not found -- not mirrored" >&2
    fi
  done
  if [ -f "$GLOBAL_CLAUDE/settings.json" ]; then
    cp "$GLOBAL_CLAUDE/settings.json" "$GLOBAL_MIRROR/settings.json"
  else
    echo "WARNING: $GLOBAL_CLAUDE/settings.json not found -- hooks would restore unwired" >&2
  fi
else
  echo "WARNING: $GLOBAL_CLAUDE not found -- global Claude config NOT backed up" >&2
fi

echo "Staging changes..."
$GIT add --force \
  "$WORK_TREE/data/" \
  "$WORK_TREE/output/" \
  "$WORK_TREE/coaching/" \
  "$WORK_TREE/memory/" \
  "$WORK_TREE/inbox/" \
  "$WORK_TREE/_archive/" \
  "$WORK_TREE/framework/voice-reference.md" \
  "$WORK_TREE/framework/slide-craft-mckinsey.md" \
  "$WORK_TREE/framework/problem-solving-mckinsey.md" \
  "$WORK_TREE/framework/personal-vs-job-os-architecture.md" \
  "$WORK_TREE/framework/two-tier-capture.md" \
  "$WORK_TREE/framework/voice-pure-dictation.md" \
  "$WORK_TREE/framework/verification-umbrella.md" \
  "$WORK_TREE/framework/interview-prep-discipline.md" \
  "$WORK_TREE/framework/overnight-queue-design.md" \
  "$WORK_TREE/framework/outreach-guide.md" \
  "$WORK_TREE/framework/content-rules.md" \
  "$WORK_TREE/framework/content-rules.yaml" \
  "$WORK_TREE/.claude/skills/scan-jobs/cache.md" \
  "$WORK_TREE/tools/generate_sidebiz_pitch.py" \
  "$WORK_TREE/tools/generate_sidebiz_model.py" \
  "$WORK_TREE/tools/.owner-identity.txt" \
  "$WORK_TREE/tools/.private-backup.conf" \
  2>/dev/null

echo "Committing..."
$GIT commit -m "Backup $(date '+%Y-%m-%d %H:%M')" --allow-empty

echo "Pushing..."
$GIT push origin master

echo "Done. Data backed up to ${PRIVATE_REPO_URL:-the private remote}."
