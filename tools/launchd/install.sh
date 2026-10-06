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
SOURCE_DIR="${LAUNCHD_SOURCE_DIR:-/Users/mag/Documents/Obsidian/30-projects/job-search/tools/launchd}"
TARGET_DIR="${LAUNCHD_TARGET_DIR:-$HOME/Library/LaunchAgents}"
LAUNCHCTL="${LAUNCHCTL:-launchctl}"
DISARMED_DIR="$SOURCE_DIR/disarmed"
PREFIX="com.nickmagnuson.jobsearch."

# install_one <label>: copy the plist into place and (re)load it.
install_one() {
    local label="$1" short="${1#$PREFIX}"
    cp "$SOURCE_DIR/$label.plist" "$TARGET_DIR/$label.plist"
    # Clear stale logs before loading: a pre-existing com.apple.macl xattr
    # (stamped under a prior macOS TCC context) makes launchd unable to open
    # the log file, so the job dies at setup with EX_CONFIG (78) and writes
    # nothing -- a silent, total failure. `xattr -c` CANNOT remove macl
    # (SIP/TCC-protected); deleting lets launchd recreate a clean file.
    # Origin 2026-06-15 (all 8 jobs dead ~2wks). See memory
    # feedback_watchdog_must_live_outside_what_it_watches; health is surfaced
    # by tools/check_automation_health.py in /standup.
    rm -f "$SOURCE_DIR/logs/$short.log" "$SOURCE_DIR/logs/$short.err"
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
        echo "Done. Logs will appear in $SOURCE_DIR/logs/"
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
