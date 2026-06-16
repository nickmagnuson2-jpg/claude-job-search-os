#!/bin/bash
# Install/uninstall the job-search launchd schedules.
#
# Usage:
#   bash tools/launchd/install.sh          # install all 4 jobs
#   bash tools/launchd/install.sh uninstall # remove all 4 jobs
#   bash tools/launchd/install.sh status    # show running schedules

set -e

SOURCE_DIR="/Users/mag/Documents/Obsidian/30-projects/job-search/tools/launchd"
TARGET_DIR="$HOME/Library/LaunchAgents"

PLISTS=(
    "com.nickmagnuson.jobsearch.gmail-fetch"
    "com.nickmagnuson.jobsearch.gmail-fetch-personal"
    "com.nickmagnuson.jobsearch.career-scan"
    "com.nickmagnuson.jobsearch.granola-auto-debrief"
    "com.nickmagnuson.jobsearch.alirohde-triage"
)

case "${1:-install}" in
    install)
        mkdir -p "$TARGET_DIR"
        for label in "${PLISTS[@]}"; do
            echo "Installing $label..."
            cp "$SOURCE_DIR/$label.plist" "$TARGET_DIR/$label.plist"
            # Clear stale logs before loading: a pre-existing com.apple.macl xattr
            # (stamped under a prior macOS TCC context) makes launchd unable to open
            # the log file, so the job dies at setup with EX_CONFIG (78) and writes
            # nothing -- a silent, total failure. `xattr -c` CANNOT remove macl
            # (SIP/TCC-protected); deleting lets launchd recreate a clean file.
            # Origin 2026-06-15 (all 8 jobs dead ~2wks). See memory
            # feedback_watchdog_must_live_outside_what_it_watches; health is surfaced
            # by tools/check_automation_health.py in /standup.
            short="${label#com.nickmagnuson.jobsearch.}"
            rm -f "$SOURCE_DIR/logs/$short.log" "$SOURCE_DIR/logs/$short.err"
            launchctl unload "$TARGET_DIR/$label.plist" 2>/dev/null || true
            launchctl load "$TARGET_DIR/$label.plist"
        done
        echo ""
        echo "Done. Logs will appear in $SOURCE_DIR/logs/"
        echo "Run: launchctl list | grep nickmagnuson  -- to verify"
        ;;
    uninstall)
        for label in "${PLISTS[@]}"; do
            echo "Removing $label..."
            launchctl unload "$TARGET_DIR/$label.plist" 2>/dev/null || true
            rm -f "$TARGET_DIR/$label.plist"
        done
        echo "Done."
        ;;
    status)
        echo "Loaded schedules:"
        launchctl list | grep nickmagnuson || echo "  (none)"
        echo ""
        echo "Plists in $TARGET_DIR:"
        ls -1 "$TARGET_DIR" | grep nickmagnuson || echo "  (none)"
        ;;
    *)
        echo "Usage: $0 {install|uninstall|status}"
        exit 1
        ;;
esac
