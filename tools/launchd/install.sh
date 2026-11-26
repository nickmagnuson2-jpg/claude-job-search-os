#!/bin/bash
# Install/uninstall the job-search launchd schedules.
#
# Usage:
#   bash tools/launchd/install.sh          # install all jobs (see JOBS below)
#   bash tools/launchd/install.sh uninstall # remove all jobs
#   bash tools/launchd/install.sh status    # show running schedules
#   bash tools/launchd/install.sh arm <job>     # e.g. arm mutation-sweep
#   bash tools/launchd/install.sh disarm <job>  # e.g. disarm mutation-sweep
#
# THE STANDING SET IS THE PLISTS DIRECTLY IN THIS FOLDER. A job that is deliberately not
# running lives in disarmed/, one level down, where neither this script's `install` nor
# the health watchdog (tools/check_automation_health.py, expected_jobs) looks. Until
# 2026-10-06 a disarmed job's plist stayed in this folder, so `install` loaded it again
# and the watchdog alerted every day it was unloaded, and the alert's own advice was to
# run `install`. `arm` and `disarm` move the plist between the two folders and load or
# unload it in the same step, so the folder and launchd cannot disagree.

set -e

# Overridable so the tests can run this against a throwaway tree and a fake launchctl.
HERE="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="${LAUNCHD_SOURCE_DIR:-$HERE}"
TARGET_DIR="${LAUNCHD_TARGET_DIR:-$HOME/Library/LaunchAgents}"
LAUNCHCTL="${LAUNCHCTL:-launchctl}"
DISARMED_DIR="$SOURCE_DIR/disarmed"
PREFIX="com.nickmagnuson.jobsearch."

# The tracked plists are templates (S1, 2026-11-25): no machine's home path is in this public
# repo. __REPO_ROOT__ is the MAIN checkout (an install run from a worktree still points the
# jobs at main), __CLAUDE_PROJECT_DIR__ is its Claude Code project folder and
# __CLAUDE_PROJECT_SLUG__ that folder's name (detector-scan writes "$HOME/.claude/projects/<slug>"), all from the
# existing resolvers (vault_paths.main_checkout, hook_runtime.claude_project_dir). launchd
# expands neither ~ nor $HOME, so the installed copy carries the literal paths.
{ IFS= read -r RENDER_REPO; IFS= read -r RENDER_PROJECT; IFS= read -r RENDER_SLUG; } < <(python3 - "$HERE/../.." <<'PY'
import sys
from pathlib import Path
here = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(here / "tools"))
from hook_runtime import claude_project_dir
from vault_paths import main_checkout
main = main_checkout(here)
print(main, claude_project_dir(main), claude_project_dir(main).name, sep="\n")   # one per line: a path may hold spaces
PY
)
RENDER_REPO="${LAUNCHD_REPO_ROOT:-$RENDER_REPO}"
RENDER_PROJECT="${LAUNCHD_CLAUDE_PROJECT_DIR:-$RENDER_PROJECT}"
RENDER_SLUG="$(basename "$RENDER_PROJECT")"
[ -n "$RENDER_REPO" ] && [ -n "$RENDER_PROJECT" ] && [ -n "$RENDER_SLUG" ] || {
    echo "install.sh: could not resolve the repo root and Claude project folder" >&2; exit 1; }

# The rendered plists log to the main checkout's tools/launchd/logs, so that is where stale
# logs are cleared, even when install runs from a worktree (Grok s1c r2 F1). Overridable for
# tests and scratch renders, which must never touch the real log folder.
LOGS_DIR="${LAUNCHD_LOGS_DIR:-$RENDER_REPO/tools/launchd/logs}"

# render <template> <dest>: the plist with its three placeholders filled in.
render() {
    sed -e "s#__CLAUDE_PROJECT_DIR__#$RENDER_PROJECT#g" -e "s#__CLAUDE_PROJECT_SLUG__#$RENDER_SLUG#g" \
        -e "s#__REPO_ROOT__#$RENDER_REPO#g" "$1" > "$2"
}

# install_one <label>: render the plist into place and (re)load it.
install_one() {
    local label="$1" short="${1#$PREFIX}"
    render "$SOURCE_DIR/$label.plist" "$TARGET_DIR/$label.plist"
    # Clear stale logs before loading: a pre-existing com.apple.macl xattr
    # (stamped under a prior macOS TCC context) makes launchd unable to open
    # the log file, so the job dies at setup with EX_CONFIG (78) and writes
    # nothing -- a silent, total failure. `xattr -c` CANNOT remove macl
    # (SIP/TCC-protected); deleting lets launchd recreate a clean file.
    # Origin 2026-06-15 (all 8 jobs dead ~2wks). See memory
    # feedback_watchdog_must_live_outside_what_it_watches; health is surfaced
    # by tools/check_automation_health.py in /standup.
    rm -f "$LOGS_DIR/$short.log" "$LOGS_DIR/$short.err"
    "$LAUNCHCTL" unload "$TARGET_DIR/$label.plist" 2>/dev/null || true
    "$LAUNCHCTL" load "$TARGET_DIR/$label.plist"
}

# arm / disarm run BEFORE the standing list is built: disarming the only plist in the
# folder, or arming into an empty one, is legitimate and must not trip the empty-list
# refusal below.
case "${1:-install}" in
    arm)
        [ -n "${2:-}" ] || { echo "Usage: $0 arm <job>" >&2; exit 1; }
        label="$PREFIX${2#$PREFIX}"
        if [ ! -f "$DISARMED_DIR/$label.plist" ]; then
            if [ -f "$SOURCE_DIR/$label.plist" ]; then
                echo "$label is already in the standing set; loading it." >&2
            else
                echo "No plist for '$2' in $DISARMED_DIR or $SOURCE_DIR." >&2
                exit 1
            fi
        else
            mv "$DISARMED_DIR/$label.plist" "$SOURCE_DIR/$label.plist"
        fi
        mkdir -p "$TARGET_DIR"
        echo "Arming $label..."
        install_one "$label"
        exit 0
        ;;
    disarm)
        [ -n "${2:-}" ] || { echo "Usage: $0 disarm <job>" >&2; exit 1; }
        label="$PREFIX${2#$PREFIX}"
        if [ ! -f "$SOURCE_DIR/$label.plist" ] && [ ! -f "$DISARMED_DIR/$label.plist" ]; then
            echo "No plist for '$2' in $SOURCE_DIR or $DISARMED_DIR." >&2
            exit 1
        fi
        echo "Disarming $label..."
        "$LAUNCHCTL" unload "$TARGET_DIR/$label.plist" 2>/dev/null || true
        rm -f "$TARGET_DIR/$label.plist"
        if [ -f "$SOURCE_DIR/$label.plist" ]; then
            mkdir -p "$DISARMED_DIR"
            mv "$SOURCE_DIR/$label.plist" "$DISARMED_DIR/$label.plist"
        fi
        exit 0
        ;;
esac

# DERIVED FROM THE DIRECTORY, never hand-listed. On 2026-09-02 this was a hand-maintained
# array of 9 labels while 10 plists sat on disk and 10 jobs were loaded: mutation-sweep had
# been added and never added here, so `install` silently skipped it and `--check` drift
# detection never covered it. Editing its plist changed nothing until someone noticed the
# schedule had not moved. Same failure shape the repo has hit before with hand-listed
# corrupt-file lists -- the list is the thing that rots, so do not keep one.
PLISTS=()
while IFS= read -r _p; do
    PLISTS+=("$(basename "$_p" .plist)")
done < <(find "$SOURCE_DIR" -maxdepth 1 -name 'com.nickmagnuson.jobsearch.*.plist' | sort)

if [ ${#PLISTS[@]} -eq 0 ]; then
    echo "No plists found in $SOURCE_DIR -- refusing to run against an empty list." >&2
    exit 1
fi

case "${1:-install}" in
    install)
        mkdir -p "$TARGET_DIR"
        for label in "${PLISTS[@]}"; do
            echo "Installing $label..."
            install_one "$label"
        done
        echo ""
        echo "Done. Logs will appear in $LOGS_DIR/"
        echo "Run: launchctl list | grep nickmagnuson  -- to verify"
        ;;
    uninstall)
        for label in "${PLISTS[@]}"; do
            echo "Removing $label..."
            "$LAUNCHCTL" unload "$TARGET_DIR/$label.plist" 2>/dev/null || true
            rm -f "$TARGET_DIR/$label.plist"
        done
        echo "Done."
        ;;
    status)
        echo "Loaded schedules:"
        "$LAUNCHCTL" list | grep nickmagnuson || echo "  (none)"
        echo ""
        echo "Plists in $TARGET_DIR:"
        ls -1 "$TARGET_DIR" | grep nickmagnuson || echo "  (none)"
        echo ""
        echo "Disarmed (not installed by this script, not expected by the watchdog):"
        ls -1 "$DISARMED_DIR" 2>/dev/null | grep nickmagnuson || echo "  (none)"
        ;;
    *)
        echo "Usage: $0 {install|uninstall|status|arm <job>|disarm <job>}"
        exit 1
        ;;
esac
