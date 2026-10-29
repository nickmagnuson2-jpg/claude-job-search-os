---
name: check
description: Midday "where am I" read, any time of day. Today's sends, replies logged, calls, applications, pipeline moves and to-dos closed, counted from the logs by a script, with the week so far against the weekly targets. Read-only, in chat. Use when Nick says "/check", "where am I today", "how's my day going".
argument-hint: "[YYYY-MM-DD]"
user-invocable: true
allowed-tools: Bash(PYTHONIOENCODING=utf-8 python3 tools/day_check.py:*)
---

# Check: where am I today

Read-only. Writes nothing, logs nothing. `/checkout` is the end-of-day close-out that writes
the daily log; this skill can run any number of times a day. The morning page shows the same
counts from the same script, so the two cannot disagree.

## Step 1: run the script (the only source of numbers)

```bash
PYTHONIOENCODING=utf-8 python3 tools/day_check.py --repo-root . --json [--target-date YYYY-MM-DD]
```

`$ARGUMENTS`, if given, is the target date. Otherwise the script uses today.

Every number you print comes from this output. Do not count anything yourself, do not read
the log files to "check" a number, and do not estimate a count the script reports as null.

## Step 2: print it

One short block, in this order: Sends, Replies logged, Calls, Applications, Pipeline moves,
To-dos closed. For each: today, then the week so far (Monday to today). Where `targets` has an
entry, show it with its period (today only weekly targets exist, in `data/goals.md` Success
Metrics). Do not turn a weekly target into a daily one.

- Under Applications, when `counts.applications_unconfirmed.today` is above 0, add one line:
  "plus N row(s) moved to Applied today with no applied date: confirm". Never add it to the count.
- A count of `null` prints as "unknown", followed by the source error from `sources`. Never as 0.
- Exit code 1 means at least one source was unreadable; say which.

## Step 3: one line, no more

At most one plain sentence on the day so far, only from the numbers (for example, the week's
applications against the weekly target). No advice list, no plan. If Nick wants a plan, that is
`/standup` or `/checkout`.

## What the counts mean

The rules live in the docstring of `tools/day_check.py`. Two worth saying when a number looks
off: an application counts only when a pipeline row says "Applied <date>", and a send counts a
person once per day across `data/outreach-log.md` and `data/networking.md`.
