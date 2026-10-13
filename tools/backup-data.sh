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
# One `git add` per path. Until 2026-10-13 all of these went to a single `git add` with
# its errors discarded, and git stages NOTHING when one pathspec matches no file: delete
# or rename any one path below and every later backup committed an empty change and
# still printed "Done". A path that cannot be staged is now named, and the rest go in.
STAGE_PATHS=(
  "$WORK_TREE/data/"
  "$WORK_TREE/output/"
  "$WORK_TREE/coaching/"
  "$WORK_TREE/memory/"
  "$WORK_TREE/inbox/"
  "$WORK_TREE/frames/"
  "$WORK_TREE/_archive/"
  "$WORK_TREE/framework/voice-reference.md"
  "$WORK_TREE/framework/slide-craft-mckinsey.md"
  "$WORK_TREE/framework/problem-solving-mckinsey.md"
  "$WORK_TREE/framework/personal-vs-job-os-architecture.md"
  "$WORK_TREE/framework/two-tier-capture.md"
  "$WORK_TREE/framework/voice-pure-dictation.md"
  "$WORK_TREE/framework/verification-umbrella.md"
  "$WORK_TREE/framework/interview-prep-discipline.md"
  "$WORK_TREE/framework/overnight-queue-design.md"
  "$WORK_TREE/framework/outreach-guide.md"
  "$WORK_TREE/framework/content-rules.md"
  "$WORK_TREE/framework/content-rules.yaml"
  "$WORK_TREE/.claude/skills/scan-jobs/cache.md"
  "$WORK_TREE/tools/generate_sidebiz_pitch.py"
  "$WORK_TREE/tools/generate_sidebiz_model.py"
  "$WORK_TREE/tools/.owner-identity.txt"
  "$WORK_TREE/tools/.private-backup.conf"
  "$WORK_TREE/tools/.cross-model-ledger.jsonl"
  "$WORK_TREE/tools/.personal-recipients.txt"
)
not_staged=0
for path in "${STAGE_PATHS[@]}"; do
  if ! $GIT add --force "$path" 2>/dev/null; then
    echo "WARNING: not staged (missing, or git refused it): $path" >&2
    not_staged=$((not_staged + 1))
  fi
done
if [ "$not_staged" -eq "${#STAGE_PATHS[@]}" ]; then
  echo "ERROR: none of the ${#STAGE_PATHS[@]} backup paths could be staged. Nothing was committed." >&2
  exit 1
fi

# The sealed tree lives OUTSIDE the work tree since 2026-10-13, in a sibling directory
# named after the repo ("<repo>-sealed"), so a recursive read of the repo cannot reach
# it. tools/check_sealed_read.py derives the same path. Inside that sibling it keeps its
# old relative path, so staging it with the sibling as the work tree writes the SAME
# index paths the files always had: the `add data/` above stages them as deleted (they
# are gone from this tree) and this add puts them back.
#
# Never mirror it into the work tree the way memory/ is mirrored. A copy inside the repo
# is reachable by a recursive read again, which is what the move exists to prevent.
SEALED_HOME="${SEALED_HOME:-$(dirname "$WORK_TREE")/$(basename "$WORK_TREE")-sealed}"
SEALED_REL="data/project-background"
if [ -d "$SEALED_HOME/$SEALED_REL" ]; then
  echo "Staging the sealed tree from $SEALED_HOME ..."
  if ! git --git-dir="$GIT_DIR" --work-tree="$SEALED_HOME" add --force "$SEALED_HOME/$SEALED_REL/"; then
    echo "ERROR: could not stage the sealed tree from $SEALED_HOME. Nothing was committed." >&2
    $GIT reset -q -- ":/$SEALED_REL"
    exit 1
  fi
fi

# Refuse to commit the deletion of ANY sealed file. A missing, half-restored or
# unmounted sibling shows up here as sealed paths staged for deletion: the `add data/`
# above staged every one of them as deleted, and only the files the sibling still holds
# were put back. These are the least recoverable files in the backup, so a deletion has
# to be asked for by name: SEALED_ALLOW_DELETE=1 bash tools/backup-data.sh
sealed_staged=$($GIT ls-files --cached -- ":/$SEALED_REL" | wc -l | tr -d ' ')
sealed_deleted=$($GIT diff --cached --name-only --diff-filter=D HEAD -- ":/$SEALED_REL" 2>/dev/null | wc -l | tr -d ' ')
echo "Sealed tree: $sealed_staged files staged, $sealed_deleted staged for deletion."
if [ "$sealed_deleted" -gt 0 ] && [ "${SEALED_ALLOW_DELETE:-}" != "1" ]; then
  echo "ERROR: $sealed_deleted sealed file(s) in the last backup are missing now." >&2
  echo "       Expected them in $SEALED_HOME/$SEALED_REL (or in the work tree)." >&2
  echo "       Nothing was committed. Restore them, set SEALED_HOME, or, if you deleted" >&2
  echo "       them on purpose, rerun with SEALED_ALLOW_DELETE=1." >&2
  $GIT reset -q -- ":/$SEALED_REL"
  exit 1
fi

# RESTORING: this git dir's work tree is the repo, so `checkout` or `restore` of the
# sealed paths with the plain $GIT above would write them back INSIDE the repo. Restore
# them with the sibling as the work tree instead:
#   git --git-dir="$GIT_DIR" --work-tree="$SEALED_HOME" checkout HEAD -- data/project-background

# A failed commit or push used to fall through to "Done" at exit 0.
echo "Committing..."
if ! $GIT commit -m "Backup $(date '+%Y-%m-%d %H:%M')" --allow-empty; then
  echo "ERROR: the backup commit failed. Nothing was pushed." >&2
  exit 1
fi

echo "Pushing..."
if ! $GIT push origin master; then
  echo "ERROR: the push failed. The backup is committed locally and is NOT on the remote." >&2
  exit 1
fi

echo "Done. Data backed up to ${PRIVATE_REPO_URL:-the private remote}."
