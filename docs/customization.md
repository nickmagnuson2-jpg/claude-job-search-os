Last updated: 2026-02-12

# Customization

How to extend the framework beyond its defaults: plugins, CV format rules, and coaching behaviour.

## Plugins

Plugins are the primary extension mechanism. A plugin is a self-contained directory in `plugins/` with a `plugin.md` manifest that the framework discovers and loads automatically.

### Creating a plugin

1. Create a directory: `plugins/your-plugin-name/`
2. Copy `framework/templates/plugin.md` into it and fill in the sections
3. Done. No registration, no edits to CLAUDE.md.

The manifest has a YAML frontmatter block and markdown sections:

```yaml
---
name: Fintech
description: Interview coaching and CV tailoring for fintech roles
scope: coaching, cv
overrides_difficulty_choice: false
---
```

**Scope** controls where the plugin is active:
- `coaching` -- interview sessions (recruiter screening, mock interview, full simulation, voice debrief)
- `cv` -- resume generation
- `coaching, cv` -- both

### Activation criteria

The `## When to Activate` section in a plugin manifest is a natural-language description that Claude evaluates contextually. There is no keyword matching engine -- Claude reads the criteria and decides whether the current task matches.

**What gets matched against:**
- Job ad text (if provided)
- Target role title and description
- Industry (from the job ad or stated by the user)
- Technologies mentioned in the role requirements
- Session type (recruiter screening, mock interview, full simulation, voice export)

**Writing good criteria:**

```markdown
## When to Activate

Activate when the target role or job ad involves: fintech, payments, banking,
financial services, PSD2, regulatory compliance, or transaction processing.
```

Be specific enough to avoid false positives, broad enough to catch relevant roles. "Activate for all roles" is valid for behavioral plugins (e.g. a stress-test mode) that aren't industry-specific.

**When multiple plugins match:** All matching plugins load. Their content sections merge (questions from both plugins enter the pool, anti-patterns from both get tracked). If multiple plugins have conflicting session behavior modifiers, the result is unpredictable -- avoid this by keeping behavioral plugins scoped narrowly or using `data/plugin-activation.md` to ensure only one behavioral plugin is active at a time.

### What a plugin can add

| Extension point | Where it goes | How it integrates |
|---|---|---|
| Interview questions | `## Interview Questions` in plugin.md (or separate `questions.md`) | Added to the question pool as the lowest-priority tier |
| Anti-patterns | `## Anti-Patterns` in plugin.md (or separate `anti-patterns.md`) | Tracked alongside core anti-patterns during sessions |
| Answering strategies | `## Answering Strategies` in plugin.md, or files in `strategies/` | Available as additional evaluation lenses during coaching |
| CV rules | `## CV Rules` in plugin.md (or separate `cv-rules.md`) | Applied alongside core quality standards during CV generation |
| Session behavior | `## Session Behavior` in plugin.md | Layers on top of (or overrides) default interviewer persona and coaching style |

**Content sections** (questions, anti-patterns, strategies, CV rules) are additive -- they add to the core set, they don't remove from it. A plugin cannot disable a core anti-pattern or suppress a core CV quality check.

**Session behavior** is an override by design. A plugin that says "no positive feedback" replaces the default coaching tone for that session. This is intentional -- it's how you create stress-test modes, brutal-honesty coaches, or hostile interviewer personas. For example, a mean-mode plugin that sets the interviewer to hostile and impatient produces exchanges like:

> **Recruiter** *(impatient)*: Right. I see you've been freelancing since 2019. Now you're applying for a permanent position. Why the switch?
>
> **Candidate:** I want more regular working hours and the chance to go deep on one topic, instead of seeing something new every two to three months.
>
> *(Short pause)*
>
> **Recruiter:** Regular working hours. For an L3 position with on-call rotation. You did read the job posting, right?

Without the plugin, the default recruiter is professional and direct but not hostile. The plugin overrides the tone section of the recruiter persona.

**`overrides_difficulty_choice`** controls whether the plugin replaces the normal/tough mode selection at session start. When set to `true`, the framework skips the "Normal or tough round?" question and uses the plugin's Session Behavior to define session intensity instead. Default is `false`. Set this to `true` for any plugin whose Session Behavior defines the overall interviewer tone and pressure level (e.g. stress-test, mean-mode, or gentle-mode plugins).

Since this is a prompt-driven system, there is no technical enforcement boundary. A plugin *could* include instructions that contradict core workflows. If something breaks, disable the plugin via `data/plugin-activation.md`.

### Plugin structure

Minimal (everything in one file):
```
plugins/my-plugin/
└── plugin.md
```

Expanded (separate files for larger plugins):
```
plugins/my-plugin/
├── plugin.md              # Manifest + integration rules
├── questions.md           # Interview question bank
├── anti-patterns.md       # Domain-specific anti-patterns
├── cv-rules.md            # CV formatting/section rules
└── strategies/            # Answering strategy files
    └── my-strategy.md
```

### Anti-pattern numbering

Core anti-patterns use numbers 1-16. Plugin anti-patterns should use a letter prefix to avoid collisions:

| Plugin | Prefix | Example |
|---|---|---|
| Fintech | F1, F2, ... | F1: Compliance hand-wave |
| Academic | A1, A2, ... | A1: Publication name-dropping |
| Your plugin | X1, X2, ... | (default prefix in the template) |

### Activation control

By default, all plugins in `plugins/` are active. To change this, copy `framework/templates/plugin-activation.md` to `data/plugin-activation.md` and edit it:

| Mode | Behaviour |
|---|---|
| `all` (default) | Every plugin in `plugins/` is active |
| `blocklist` | All active except those listed under `## Disabled` |
| `allowlist` | Only those listed under `## Enabled` are active |

### Example plugin

See `examples/plugins/fintech/` for a complete example with interview questions, four anti-patterns, a regulatory framing strategy, and CV rules for financial services roles.

---

## CV format rules

The framework ships with four regional CV formats in `framework/style-guidelines.md`: International (default), US, UK, and DACH. To customize:

**Adjusting an existing format:** Edit the relevant section in [style-guidelines.md](../framework/style-guidelines.md) directly. For example, if your market expects a specific section ordering or different length conventions.

**Adding a new regional format:** Add a new `### Format Name` section to style-guidelines.md following the existing pattern. Include guidance on:
- Section ordering and emphasis
- What personal information to include or exclude
- Length conventions
- Language and formality expectations
- Any market-specific requirements

**Domain-specific CV rules** (e.g. "always include publications" or "lead with security certifications") belong in a plugin's `## CV Rules` section, not in the global style guidelines. This keeps format conventions separate from domain expertise.

---

## Coaching behaviour

### Coached answers

[coaching/coached-answers.md](../coaching/coached-answers.md) stores refined phrasings that evolve across sessions. You can edit this file directly to:
- Pre-load answers for questions you expect
- Refine phrasings the coaching sessions generated
- Remove answers that no longer reflect your current positioning

These answers are used as evaluation benchmarks during debriefs, not as scripts. The coaching system compares your live answers against them to measure improvement.

### Pressure points

[coaching/pressure-points.md](../coaching/pressure-points.md) lists specific areas that tough-mode sessions target. Adding entries here makes future tough-mode sessions probe those topics harder. Removing entries makes sessions stop targeting them.

### Anti-pattern tracker

[coaching/anti-pattern-tracker.md](../coaching/anti-pattern-tracker.md) tracks your anti-pattern trends over time. You generally shouldn't need to edit this manually (sessions update it automatically), but you can:
- Mark patterns as resolved if you've fixed them outside of sessions
- Add notes about context that triggered a pattern
- Reset counts if you want a fresh baseline
