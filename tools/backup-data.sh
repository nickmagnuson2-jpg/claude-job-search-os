#!/bin/bash
# Backup private job search data to GitHub
# Tracks: data/, output/, coaching/, memory/, inbox/
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
  "$WORK_TREE/inbox/" 2>/dev/null

echo "Committing..."
$GIT commit -m "Backup $(date '+%Y-%m-%d %H:%M')" --allow-empty

echo "Pushing to GitHub..."
$GIT push origin master

echo "Done. Data backed up to https://github.com/nickmagnuson2-jpg/nick-job-search-data"
