#!/bin/bash
# Backup private job search data to GitHub
# Tracks: data/, output/, coaching/, memory/, inbox/, _archive/, private framework/*.md docs, .claude/skills/scan-jobs/cache.md
# Repo: https://github.com/nickmagnuson2-jpg/nick-job-search-data

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_TREE="$(dirname "$SCRIPT_DIR")"
GIT_DIR="$HOME/.nick-private-git"

GIT="git --git-dir=$GIT_DIR --work-tree=$WORK_TREE"

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
