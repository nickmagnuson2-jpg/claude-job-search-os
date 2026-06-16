---
name: Plugin Name
description: One-line description of what this plugin adds
scope: coaching, cv
overrides_difficulty_choice: false
---

<!-- scope options: coaching, cv, or both (comma-separated) -->
<!-- overrides_difficulty_choice: set to true if this plugin's Session Behavior
     replaces the normal/tough difficulty choice entirely. Default is false. -->

# Plugin Name

## When to Activate

<!-- Natural language description of when this plugin should be loaded.
     Claude evaluates this contextually against the target role, job ad, and industry. -->

Activate when the target role or job ad involves: [keywords, industries, or role types].

## Interview Questions

<!-- Additional questions for recruiter screenings and mock interviews.
     These are added to the question pool alongside core questions. -->

- "[Question 1]"
- "[Question 2]"

## Anti-Patterns

<!-- Domain-specific anti-patterns to track during coaching sessions.
     Use a plugin-specific prefix (e.g. X1, X2) to avoid collision with core patterns (1-16). -->

| # | Pattern | Description |
|---|---------|-------------|
| X1 | [Pattern name] | [What it looks like and why it's problematic] |

## Answering Strategies

<!-- Either inline strategies here, or reference separate files:
     See `strategies/my-strategy.md` for the full strategy guide.

     Strategy files should follow the same format as framework/answering-strategies/*.md:
     - Quick Overview section
     - Full strategy with examples
     - When to Use / When NOT to Use -->

## Session Behavior

<!-- Optional: set interviewer persona and coaching style for this plugin.
     Plugins load BEFORE the session type file (step 4 in interview-workflow.md).
     If Session Behavior is defined here, the session type file's default tone
     is skipped -- these instructions become the persona for the session.
     Core workflow steps (question sequencing, progress tracking)
     are unchanged.

     What you can set:
     - Interviewer tone (skeptical, impatient, aggressive, friendly, formal)
     - Coaching style (brutally direct, no encouragement, Socratic, etc.)
     - Pacing (rapid-fire, interrupts long answers, never lets silences sit)
     - Pressure calibration (always push back twice, never accept first response)

     Example:
     - **Interviewer:** Cold and transactional. Cuts off answers longer than
       two sentences. Never says "great" or "interesting." Follows every
       answer with "And what evidence do you have for that?"
     - **Coach:** No positive feedback. Lead with what was wrong and why it
       would lose the role. Deliver the strongest version without softening. -->

## CV Rules

<!-- Rules for CV generation when this plugin is active.
     Examples: additional sections, formatting rules, keyword requirements. -->
