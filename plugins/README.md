# Plugins

Drop-in extensions for interview coaching and CV generation. Each plugin is a self-contained directory with a `plugin.md` manifest.

## How It Works

1. Each subdirectory here with a `plugin.md` file is a plugin
2. The framework discovers and loads matching plugins automatically at session start
3. Plugin content (questions, anti-patterns, strategies, CV rules) merges with the core framework
4. Session behavior overrides (interviewer tone, coaching style) layer on top of defaults

By default, all plugins are active. Activation can be scoped per-plugin using a `## When to Activate` section (evaluated contextually against the job ad and role), or globally using `data/plugin-activation.md`.

## Creating and Configuring Plugins

See [docs/customization.md](../docs/customization.md#plugins) for the full guide: creating plugins, writing activation criteria, extension points, anti-pattern numbering, and activation control.

**Quick version:** copy `framework/templates/plugin.md` into a new directory here and fill in the sections.

## Git Conventions

This directory is version-controlled. Plugins you add here will be committed with the rest of the repo. If a specific plugin contains sensitive content, add it to `.gitignore` individually.

**Test plugins:** Directories prefixed with `test-` (e.g. `plugins/test-gentle-mode/`) are gitignored by default. Use this prefix for personal experiments, one-off session tweaks, or plugins you don't want committed.

## Example

See `examples/plugins/fintech/` for a complete example plugin demonstrating all extension points.
