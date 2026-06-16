#!/bin/bash
# Nightly checkout nudge — if today's daily-log entry is missing, fire a macOS notification.
#
# Triggered by launchd plist com.nickmagnuson.jobsearch.checkout-nudge at ~9pm.
# Reads data/job-todos-daily-log.md and checks for "### YYYY-MM-DD" header matching today.
# If missing, posts a desktop notification reminding Nick to run /checkout.

set -e

REPO_ROOT="/Users/mag/Documents/Obsidian/30-projects/job-search"
LOG_FILE="$REPO_ROOT/data/job-todos-daily-log.md"
TODAY=$(date +%Y-%m-%d)

if [ ! -f "$LOG_FILE" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') | daily log file missing: $LOG_FILE"
    osascript -e 'display notification "Daily log file missing." with title "Checkout nudge"'
    exit 0
fi

if grep -q "^### $TODAY" "$LOG_FILE"; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') | checkout already logged for $TODAY — no nudge."
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') | no checkout entry for $TODAY — firing nudge."
osascript -e 'display notification "No /checkout entry for today. Run /checkout in Claude Code to close out the day." with title "Job-search checkout nudge" sound name "Glass"'
