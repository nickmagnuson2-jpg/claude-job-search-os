#!/bin/bash
# Backup private job search data to GitHub
# Tracks: data/, output/, coaching/, memory/ (repo-local + canonical sidecar mirror),
#         inbox/, _archive/, private framework/*.md docs, .claude/skills/scan-jobs/cache.md
# Repo: https://github.com/nickmagnuson2-jpg/nick-job-search-data

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_TREE="$(dirname "$SCRIPT_DIR")"
GIT_DIR="$HOME/.nick-private-git"
CANONICAL_MEMORY="$HOME/.claude/projects/-Users-mag-Documents-Obsidian-30-projects-job-search/memory"

GIT="git --git-dir=$GIT_DIR --work-tree=$WORK_TREE"

# The canonical auto-memory sidecar lives outside the repo entirely (Claude Code's
# actual session-loaded memory dir), so it's invisible to the git add below unless
# mirrored in first. rsync into a repo-local subfolder (gitignored from the PUBLIC
# repo like the rest of memory/) so the existing add/commit/push picks it up as-is.
if [ -d "$CANONICAL_MEMORY" ]; then
  echo "Mirroring canonical memory sidecar..."
  mkdir -p "$WORK_TREE/memory/canonical-sidecar"
  rsync -a --delete "$CANONICAL_MEMORY/" "$WORK_TREE/memory/canonical-sidecar/"
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
  "$WORK_TREE/.claude/skills/scan-jobs/cache.md" \
  "$WORK_TREE/tools/generate_sidebiz_pitch.py" \
  "$WORK_TREE/tools/generate_sidebiz_model.py" \
  2>/dev/null

echo "Committing..."
$GIT commit -m "Backup $(date '+%Y-%m-%d %H:%M')" --allow-empty

echo "Pushing to GitHub..."
$GIT push origin master

echo "Done. Data backed up to https://github.com/nickmagnuson2-jpg/nick-job-search-data"
