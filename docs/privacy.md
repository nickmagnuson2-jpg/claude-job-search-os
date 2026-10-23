Last updated: 2026-10-23

# Privacy & Data Handling

This framework stores sensitive career data locally. This document explains what goes where, what's protected by default, and what to watch out for.

## What's gitignored

The `.gitignore` excludes all personal data directories by default:

| Path | Contains | Gitignored |
|---|---|---|
| `data/` | Profile, projects, skills, certifications, education, companies, professional identity, contacts and interaction log (`networking.md`), applications (`job-pipeline.md`) | Yes |
| `data/project-background/` | Private notes you would never put on a CV | Not in the repo at all: kept in the sibling `<repo>-sealed/data/project-background/` |
| `coaching/` | Coached answers, anti-pattern tracking, progress scorecards | Yes |
| `output/` | Generated CVs, cheat sheets, review reports | Yes |
| `files/` | PDF input files (CVs you import) | Yes |
| `inbox/` | Raw capture zone (emails, nudges, voice notes awaiting routing) | Yes |
| `memory/` | Claude's persistent session memory (auto-memory files, `MEMORY.md` router + any `index-<topic>.md` shards) | Yes |

`framework/`, `plugins/`, `docs/`, `tools/`, `.claude/skills/`, `examples/`, and `CLAUDE.md` hold methodology and automation. Some files inside them are gitignored because they hold personal config (the PII denylist, backup config, your voice corpus). Run `git check-ignore -v <file>` before adding anything new under them. (A few `framework/*.md` methodology docs are author-private and gitignored in the source repo; they ship as copy-into-place starters under `examples/framework/`. Your own filled-in versions stay gitignored the same way.)

## What stays local vs. what's shared

> **See also:** the Profile guard rule in CLAUDE.md Hard Rules. CLAUDE.md instructs skills to stop when profile or goals are missing. This is an instruction, not a hook, and `/generate-cv` proceeds without `profile.md`.

```
Safe to push (methodology)          Private (your data)
─────────────────────────           ───────────────────
CLAUDE.md                           data/*
framework/**                        coaching/**
plugins/**                          output/**
.claude/skills/**                   files/**
docs/**                             inbox/*
tools/**                            memory/*
examples/**
```

If you fork or clone this repo publicly, the gitignore protections apply automatically. Someone cloning your fork gets the framework, not your data. This holds only if personal files were never committed. A clone gets the full history.

## Versioning personal data in a private repo

If you use a private repository and want git history for your career data, you can remove the gitignore lines for `data/` and `coaching/`. The `.gitignore` file has a comment explaining this:

```
# Personal data - ignored by default to prevent accidental exposure.
# If you use a private repo and want to version your data, remove these lines.
/data/
/coaching/
```

Before doing this, verify your repo is private. A public repo with these lines removed exposes everything.

## Sensitive content tiers

Not all personal data is equally sensitive:

1. **Contact and compensation** (`data/profile.md`) - Name, email, phone, rates, salary expectations. Highest sensitivity.
2. **Project background** (`data/project-background/`) - Private notes you would never put on a CV. Kept outside the repo entirely.
3. **Professional data** (`data/projects/`, `data/skills.md`, etc.) - Work history, skills, certifications. The same information that appears on a CV, but in richer detail.
4. **Generated output** (`output/`) - Tailored CVs and reports. Contains personal data but is designed for sharing with specific recipients.

## What Claude sees during a session

When you run a session, Claude Code reads your local files to generate CVs and provide coaching. This data is sent to Anthropic's API for processing. It is subject to [Anthropic's privacy policy](https://www.anthropic.com/legal/privacy) and data handling practices.

Key points:

- Claude Code can read any file in the repo. CLAUDE.md routes which files skills load, but that is guidance, not an access control. Connected services (Exa, Granola, Gmail) also receive or read data.
- Data retention depends on how you authenticate Claude Code:
  - **API key:** Anthropic retains inputs/outputs for up to 30 days for safety, does not train on them
  - **Pro/Max subscription:** Conversations may be stored per Anthropic's consumer terms, and retention policies differ from the API
- Review [Anthropic's privacy policy](https://www.anthropic.com/privacy) for current details on your plan type

## Forking and cloning safely

**Forking a public repo:** The gitignore ensures `data/`, `coaching/`, `output/`, and `files/` are excluded. Your fork will contain only the framework. Populate it with your own data locally. This holds only if personal files were never committed. A clone gets the full history.

**Switching from private to public:** Before making a repo public, verify that no personal data was ever committed. Gitignore only prevents future commits. If you previously committed data files, they remain in git history. Use `git log --all --name-only` to audit, and if needed, rewrite history with `git filter-repo` before going public. After rewriting, force-push; GitHub can still serve old commits from cached views, forks and pull requests until GitHub Support purges them.

**Cloning someone else's repo:** You get the framework only. Run `/import-cv` with your own CV to populate the data layer.

## The PII gate (automated public-file protection)

Beyond gitignore (which keeps your data directories out of git entirely), a second layer guards the *public* files you do commit:

- **`check_public_pii.py`**: a PreToolUse hook that blocks Claude's writes to tracked files when they contain a name on your denylist. It does nothing until you build the denylist (`python3 tools/gen_pii_denylist.py`), and it does not check commits you make by hand. Regenerate the denylist after adding contacts. Also install the pre-push gate once per clone: `bash tools/hooks/install.sh`.
- **`/audit-pii`**: run it before committing or pushing any public-file change. It refreshes the denylist, runs the deterministic scan, then dispatches a semantic reviewer that catches new real names the denylist hasn't learned yet.

## What a push actually exposes

- If your data was **never committed**, pushing a public fork exposes only the framework. Your `data/`, `coaching/`, `output/`, and `files/` are gitignored and absent.
- If you **removed the gitignore lines**, or **committed data before the gitignore was in place**, pushing exposes that data permanently in git history, even if you later delete the files. Audit with `git log --all --name-only` and rewrite history with `git filter-repo` before going public. After rewriting, force-push; GitHub can still serve old commits from cached views, forks and pull requests until GitHub Support purges them.

## Checklist before pushing

- [ ] `git ls-files data/ coaching/ output/ files/ inbox/ memory/` prints nothing
- [ ] No files in `data/`, `coaching/`, `output/`, `files/`, `inbox/`, or `memory/` are staged
- [ ] No personal data was accidentally added to framework files or CLAUDE.md
- [ ] Run `/audit-pii` if you changed any public file (skills, docs, framework, tool code)
- [ ] If you modified `.gitignore`, you didn't remove the personal data exclusions on a public repo
