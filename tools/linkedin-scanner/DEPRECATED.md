# DEPRECATED — retired 2026-07-15

This Selenium-based LinkedIn scanner is **no longer used**. It was replaced by
[`tools/contact_finder.py`](../contact_finder.py) + the `/scan-contacts` skill.

## Why it was retired

- Required your LinkedIn **password in plaintext `.env`** (`LINKEDIN_EMAIL` /
  `LINKEDIN_PASSWORD`).
- **Violated LinkedIn ToS** and risked an account ban (automated scraping).
- Brittle by construction — broke on every LinkedIn DOM change and hit 2FA/CAPTCHA
  on first run. It never produced a stored result (empty cache).

## What replaced it

`tools/contact_finder.py` uses **Exa search** (no login, no password, no ban
risk) for deterministic acquisition, and the `/scan-contacts` skill does the
session-side evidence-gate + ranking. Same goal (find + rank people to
cold-email), safer means.

```bash
PYTHONIOENCODING=utf-8 python3 tools/contact_finder.py --company "Acme AI" --num 20
```

The old code is kept (not deleted) for reference only. Do not wire it back in.
