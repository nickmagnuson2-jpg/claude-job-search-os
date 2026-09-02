#!/bin/bash
# Install/uninstall the job-search launchd schedules.
#
# Usage:
#   bash tools/launchd/install.sh          # install all jobs (see JOBS below)
#   bash tools/launchd/install.sh uninstall # remove all jobs
#   bash tools/launchd/install.sh status    # show running schedules

set -e

SOURCE_DIR="/Users/mag/Documents/Obsidian/30-projects/job-search/tools/launchd"
TARGET_DIR="$HOME/Library/LaunchAgents"

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
