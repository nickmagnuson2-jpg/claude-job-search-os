# Self-Improvement Loop: origins and anti-patterns

> **This file deliberately does NOT restate the protocol.** The protocol lives in exactly one
> place, `CLAUDE.md` `## Self-Improvement Loop`, which is auto-loaded every session. This file
> holds only what that section does not: the **origin incidents** behind four of its steps, the
> **anti-pattern list**, and the supporting citations.
>
> **Why the split is shaped this way.** On 2026-08-18 the protocol was compressed in CLAUDE.md
> (9,939 to 7,613 bytes) to hold that always-loaded file under its 40 KB budget. The first design
> kept a full verbatim copy here. That was rejected: **two copies of this exact protocol have
> already drifted once**, and it cost 384 of 403 `feedback_*.md` files being invisible to the
> promotion detector for a month (see `tests/scripts/test_memory_frontmatter_schema.py`). The fix
> is not a reminder to update both. It is having nothing to keep in sync.
>
> **The verbatim pre-compression original is in git**, not here: `git show 67e0ada:CLAUDE.md`.
> Version control is a better archive than a second copy, because it cannot drift.
>
> **Read this file when** you want the incident behind a step, are deciding whether a step applies
> to an edge case, or are auditing whether the loop is working. **Do not read it for the
> procedure** — go to CLAUDE.md for that.

---

## 1. Origin incidents, keyed to the step they justify

Each of these is quoted from the pre-compression text. CLAUDE.md keeps a one-clause version of
each; the full trace is here.

### Step 1, the mandatory frontmatter keys

> Origin: this instruction previously specified only `name`/`description`/`metadata.type`,
> disagreeing with the `lessons-learned` skill, so **384 of 403 `feedback_*.md` files (95%) are
> invisible to the scanner** — 69 of them written in the month *after* the gap was first
> discovered. Fixed 2026-08-13; see `output/analysis/081326-memory-hygiene-project.md`.

**Why it matters beyond the number:** the root cause was a *doc contradiction*, not a lapse. The
writer and the reader of the schema disagreed, and the more commonly-followed authority emitted the
invisible variant. That is the same failure mode this file's existence is designed to avoid.

### Step 3, trace to source before building a guard

> Origin: 2026-06-02 — `check_bare_python.py` was built to block bare `python`, but 13 skill docs
> + tool docstrings still *prescribed* it; the hook tripped on every skill run until the docs were
> swept. "Patch the script" was misread as "build a guard" instead of "fix what emits the input."

### Step 3, back-propagate to existing artifacts

> Skipping this is how a rule sits "captured" for weeks while broken artifacts accumulate (origin:
> the python3 rule existed while 13 docs violated it; `audit_rule_violations.py` built 2026-06-02
> to close this gap).

### Step 3, name the target surface and when Nick last ran it

> Origin: 2026-08-13, two promotions landed on `/weekly-review` Step 5c and `/memory-refresh`
> Step 3a hours before Nick said *"I dont run weekly review reliabely"* — correct content, dead
> surface. See `memory/feedback_verify_the_surface_fires_before_anchoring_to_it.md` and
> `memory/user_nick_invokes_standup_not_weekly_review.md`.

### Step 3, the hard-rule promotion bar

The worked example of a rule that met the bar: the 2026-05-20 "never draft email bodies inline"
rule, now a CLAUDE.md Hard Rule.

---

## 2. Anti-patterns

> **NON-AUTHORITATIVE.** Every bullet here is a negative restatement of a directive that lives in
> CLAUDE.md. They are kept because the negative framing teaches something the positive form does
> not, but **they are not the rule** — if one ever contradicts CLAUDE.md, CLAUDE.md wins and this
> bullet is stale. A blind two-agent enumeration on 2026-08-18 confirmed each maps to a directive
> that survives in the compressed protocol; the mapping is in the table below.

- **Single-tier capture.** Dropping a row in lessons.md and moving on — without an auto-memory file, the rule is unfindable next session.
- **Build now, no gate.** Adding a "Build skill update by 5/25" todo with no occurrence trigger — drifts indefinitely or fires prematurely.
- **Skip the wikilinks.** A memory file with no `[[connections]]` doesn't surface during recall on related topics; isolates the rule.
- **Tier confusion.** Treating a tonal voice nudge as a hard-rule candidate, or a project-tier safety rule as a memory-only note. The tier ladder belongs in the file itself so the next promotion is obvious.
- **Symptom-guard over source-fix.** Building a hook to block a bad pattern without fixing the doc/skill/template that prescribes it. The guard then trips forever on a problem you could have deleted. Always trace to source first (Step 3).
- **Forward-guard, no backward-sweep.** Capturing/enforcing a rule for the future while leaving existing violations in place. A rule isn't landed until the artifacts that already break it are swept — run `audit_rule_violations.py` for greppable rules.

| Anti-pattern | Restates which CLAUDE.md directive |
|---|---|
| Single-tier capture | Run the full tiered protocol; create the auto-memory file |
| Build now, no gate | The REOPEN gate is mandatory; "Build by [date]" is the failure mode |
| Skip the wikilinks | Body must carry Connections, linked liberally |
| Tier confusion | The tier ladder determines where the rule lives |
| Tier ladder in the file | Body must carry a Tier ladder section |
| Symptom-guard over source-fix | Trace to source before building a guard |
| Forward-guard, no backward-sweep | Back-propagate to existing artifacts |

---

## 3. The non-overlap invariant

**This file must never contain a directive.** If you find yourself writing "do X" or "always Y"
here, it belongs in CLAUDE.md instead, and putting it in both is how the 384-of-403 failure
happened.

The invariant is what makes drift structurally impossible rather than a thing to remember:
CLAUDE.md holds the directives, this file holds the justification, and neither restates the other.
The one deliberate exception is section 2, which is explicitly marked non-authoritative and
mapped line-by-line to its source directive above.
