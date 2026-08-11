# Inbox drain design

Last updated: 2026-08-10

**BLUF: `data/inbox.md` does not need a bigger drain. It needs one producer to stop
writing to it.** Career-scan output is 68% of the file and regrows at roughly 91 role
blocks per day. Redirecting that single producer removes 3,276 of 4,765 lines and stops
the regrowth. The drain is still needed, but it is the smaller half of the fix.

This supersedes the `data/inbox.md` sections of
`2026-08-10-top-of-funnel-wiring-design.md`. See "What this supersedes" below for what
survives from that spec.

---

## 1. The measurement

Taken 2026-08-10 against the live file. The prior spec described this backlog as
"~108 human `/remember` captures" and designed a bucketing workflow around them. That
framing targets 18% of the file.

| Kind | Blocks | Lines | Share |
|---|---|---|---|
| Career scan results | 34 | 3,276 | 68% |
| Dated human captures | 83 | 870 | 18% |
| Other | 25 | 557 | 11% |
| Agent drips | 8 | 54 | 1% |

Age of the dated human captures:

| Age | Count |
|---|---|
| 0-14 days | 1 |
| 31-90 days | 35 |
| >90 days | 47 |

Two facts drive every decision below:

1. **The bulk is machine-generated report content, not captures.** A career-scan block
   is a dated report whose contents expire on their own; job links go dead. It was never
   something to "route."
2. **The human backlog is almost entirely cold.** 82 of 83 captures are over 31 days
   old. Whatever they were, they have not been urgent for a month.

---

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Career scan writes to `output/career-scan/MMDDYY.md`, never to `data/inbox.md` | Removes 68% and stops regrowth with one function change. A report is not a capture. |
| D2 | Routed blocks move to `data/inbox-archive.md`, never hard-deleted | The inbox actually shrinks, and a mis-route stays recoverable by hand. |
| D3 | The 82 stale captures are bucketed by age and type, batch-approved by the user | Fast, and nothing is routed unreviewed. Batch granularity is the user's explicit choice. |
| D4 | The drain lives in `/act`, not a new skill | One consumer for both inboxes. Reuses the existing classify, preview, approve, route flow and its approval UX. |
| D5 | The drain writes through today's YAML interfaces, not the unbuilt JSONL ones | `scan-targets.jsonl` and `networking.jsonl` do not exist. `act_apply.py target-add` and `networking_write.py` do. Waiting on the migration means the inbox grows meanwhile. |
| D6 | All inbox mutation goes through `inbox_lock.atomic_update` | Three launchd jobs write this file on schedules that cluster. The lock already exists and `write_inbox` already uses it. |
| D7 | Blocks without a recognised marker stay put and are reported, never guessed at | An unroutable block is a signal that a producer is unmarked, not a thing to discard. |

---

## 3. Components

### 3.1 Producer fix (D1)

Single choke point: `tools/career_scanner/scanner.py::write_inbox` (line 209). It
currently calls `inbox_lock.prepend_entries(data/inbox.md, entry)`.

Change it to write `output/career-scan/MMDDYY.md`, one file per run, containing the
same `format_inbox_entry` output. No lock needed: one writer, one dated file, no shared
document.

**Nothing is lost, but the surfacing has to move with it.** Today the only reason those
roles are visible is that they sit in the inbox. After D1:

- `/standup` reads the newest `output/career-scan/*.md` and surfaces the top-scoring
  roles from it.
- `/act` offers the newest scan file as a bucket, the same way it offers `inbox/` files.

**This is load-bearing and must ship in the same change as D1.** Moving the producer
without moving the surfacing silently turns off a live lead source. That is the same
failure shape as the `deployment-leads` preset in the handoff traps: a component that
keeps running while nothing consumes it.

The 34 existing career-scan blocks are extracted to backdated files under
`output/career-scan/` so history is preserved, then removed from the inbox.

**Retention: none for now.** Auto-expiry was offered and not chosen. These files are
small, out of the read path, and in `output/` which is already excluded from the vault.
Revisit if the directory passes ~200 files.

### 3.2 The drain (D4)

Extends `/act`. `act_classify.py` gains a second source: parsed blocks from
`data/inbox.md` alongside the existing `inbox/` directory files.

Block recognition, by marker:

| Block type | Marker | Destination |
|---|---|---|
| Agent drip, company | `<!-- review-gated -->` + `(company)` in header | `act_apply.py target-add` (per P2), optional `pipeline-add` |
| Agent drip, person | `<!-- review-gated -->` + `(person)` in header | `networking_write.py add` |
| Career scan | `## Career Scan Results` | Nothing. Should not exist after D1; if present, report as a producer regression |
| Dated human capture | `^## \d{4}-\d{2}-\d{2}` | Routed via existing `/remember` destinations, or archived |
| Unrecognised | none | **Left in place and reported** (D7) |

The eight existing `review-gated` blocks carry the comment
`accept via /act or /networking`, which today points at a workflow that does not exist.
This change makes that comment true. That is the smallest possible first slice and
should be the first thing that works end to end.

### 3.3 Archive on route (D2)

`data/inbox-archive.md`, append-only, newest first, each block stamped with the date
routed and its destination:

```markdown
## 2026-08-10 | routed to scan-targets.yaml
<!-- original capture: 2026-05-14 -->
[original block verbatim]
```

Mutation of both files happens inside **one** `inbox_lock.atomic_update` transform, so
a crash between "removed from inbox" and "written to archive" cannot lose a block.

Archive is never read by any skill. It exists for recovery, not for workflow. It is
bounded accretion by design: out of the read path, unlike the inbox.

### 3.4 Backlog triage (D3)

A script buckets the 83 dated captures by age and inferred type, prints counts plus
samples per bucket, and takes a per-bucket approve or reject from the user. Approved
buckets are archived in one locked transform.

The script proposes; it never routes unreviewed. Its output is a preview, and the user
approves per bucket.

---

## 4. What this supersedes

From `2026-08-10-top-of-funnel-wiring-design.md`:

| Element | Status |
|---|---|
| "~108 human `/remember` captures" as the framing of the backlog | **Superseded.** Measured: 83 captures, 870 lines, 18% of the file. The bulk is career-scan output. |
| Drain destinations `scan-targets.jsonl` / `networking.jsonl` | **Superseded** by D5. Retarget to the YAML path shipped 2026-08-10. Revisit if and when ADR-1 lands. |
| Career-scan blocks drained by `/act` | **Superseded** by D1. They should not be in the file at all. |
| D1 in that spec: "extend `/act` to drain `data/inbox.md`" | **Survives.** Reaffirmed here as D4; the reasoning was never pool-dependent. |
| "Any component added here must name its consumer" | **Survives, and is the core principle.** It is exactly what D1's surfacing requirement enforces. |
| Target-pool schema, `ats` null plus `outreach` true case | **Survives, already shipped** as YAML in commit `7f034a4`. |

---

## 5. Build order

Each step is independently shippable and leaves the system working.

1. **Extract the 34 career-scan blocks** to backdated `output/career-scan/` files.
   Read-only against the inbox; verify extraction before anything is removed.
2. **Producer fix plus surfacing** (D1). `write_inbox` retargets; `/standup` and `/act`
   read the newest scan file. Ship together, never apart.
3. **Remove the 34 blocks** from `data/inbox.md` under the lock. File drops to ~1,489
   lines.
4. **Archive plumbing** (D2, D3.3). The locked two-file transform, with tests.
5. **Drain the 8 review-gated blocks** (D4). Smallest real slice; makes the existing
   `accept via /act` comment true.
6. **Backlog triage** (D3) for the 83 dated captures.
7. **Unrecognised-block report** (D7) surfaces whatever is left in the 25 "other" blocks.

Steps 1 to 3 alone take the file from 4,765 to ~1,489 lines and stop the growth. Steps
4 onward are the actual drain.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| **Producer moved, surfacing not moved.** Career scan keeps running, nothing reads it, a live lead source goes dark silently. | D1 ships the `/standup` and `/act` surfacing in the same change. A test asserts the newest scan file is reachable from at least one consumer. |
| **A concurrent launchd write during a drain.** Three jobs write this file on clustering schedules. | D6: every mutation through `inbox_lock.atomic_update`, which already detects concurrent modification. |
| **Partial route: destination written, block not archived, or vice versa.** | Both files mutated in one transform. Destination write must return ok before the transform commits. |
| **Batch approval archives something that mattered** (accepted risk of D3). | D2 means archived, not deleted. Recovery is a hand edit of `inbox-archive.md`. |
| **Silent drain failure reports success.** The failure mode of `trim-context-file` in P6: verification that iterates an empty set and reports PASS. | Every step asserts non-empty inputs and reports counts moved. A drain that moves zero blocks reports zero, loudly, and is not a pass. |
| **Extraction loses content on the way to `output/career-scan/`.** | Step 1 is read-only and verified by line-count and block-count reconciliation before step 3 removes anything. |

---

## 7. Out of scope

- The JSONL migration (ADR-1). D5 deliberately builds against today's interfaces.
- `inbox/` directory handling. Already works; unchanged.
- Retention or expiry for `output/career-scan/`. Offered, not chosen. Revisit at ~200 files.
- The `deployment-leads` preset staleness (handoff trap). Related shape, separate fix.

---

## 8. Why this compounds

The test for a compounding change is whether output feeds back as reduced input.

- **D1 reduces input.** The largest producer stops feeding the file. This is the only
  change here that makes future triage cheaper rather than faster.
- **D2 bounds the residue.** The archive grows, but out of the read path, so it does not
  make the next drain heavier.
- **D7 surfaces unmarked producers** instead of silently absorbing them, so the next
  unnamed producer is caught rather than accumulated.

The drain itself (D4) does not compound. It is maintenance, and it will need running
again. That is the correct division: fix the producer once, run the drain periodically.
