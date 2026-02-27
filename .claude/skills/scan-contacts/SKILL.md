---
name: scan-contacts
description: Scan LinkedIn for contacts at a target company and rank them for outreach — uses LinkedIn scraper + Claude to score by role proximity, education, network connectedness, and industry fit
argument-hint: "<company-name> [count]"
user-invocable: true
allowed-tools: Read(*), Glob(data/*), Write(data/networking.md), Bash(python tools/linkedin-scanner/scan.py*)
---

# Scan Contacts — LinkedIn Outreach Target Finder

Searches LinkedIn for employees at a target company, scrapes their profiles, and ranks them using Claude to identify the best people to reach out to for a CoS/Strategy Ops job search. Ranks on four dimensions: role proximity (hiring decision authority), education, network connectedness, and industry fit.

Use this after researching a company with `/research-company` to find who to cold-message.

## Arguments

- `$ARGUMENTS` (required): At minimum a company name.
  - **Company name** (required): The company to scan (quoted if multi-word)
  - **Count** (optional): Number of profiles to scan. Default: 20.

Examples:
- `/scan-contacts "Amae Health"` — scan 20 profiles at Amae Health
- `/scan-contacts "Spring Health" 30` — scan 30 profiles
- `/scan-contacts Stripe 15`

## Prerequisites

This skill runs a Python tool via Bash. Before running, the following must be set in `.env` at the repo root:
- `LINKEDIN_EMAIL` — your LinkedIn login email
- `LINKEDIN_PASSWORD` — your LinkedIn password
- `ANTHROPIC_API_KEY` — your Anthropic API key

Chrome must be installed. First run may require email/SMS verification on LinkedIn.

## Instructions

### Step 1: Parse Arguments

Parse `$ARGUMENTS`:
- **Company name**: first quoted string or unquoted text before a number
- **Count**: optional integer, default 20

If no arguments, display usage:
```
Usage: /scan-contacts <company-name> [count]

Examples:
  /scan-contacts "Amae Health"
  /scan-contacts "Spring Health" 30
  /scan-contacts Stripe 15
```

### Step 2: Profile Guard

Check that `data/profile.md` and `data/goals.md` exist and are not empty/placeholder-only. If either is missing or empty, stop and display:

```
⚠️ data/profile.md or data/goals.md is missing. Run /import-cv first or fill in your profile before scanning contacts.
```

### Step 3: Run the Scanner

Run the scanner tool via Bash:

```bash
PYTHONIOENCODING=utf-8 python tools/linkedin-scanner/scan.py --company "<company_name>" --num <count> --output-format json
```

- If the command fails with a missing env var message, display the error and stop with instructions to populate `.env`.
- If Selenium can't find Chrome, display: "Chrome not found. Install Chrome and chromedriver, or run `pip install webdriver-manager` in `tools/linkedin-scanner/`."
- If the command succeeds, parse the JSON output.

### Step 4: Display Ranked Results

Display a ranked table. Filter out records with an `error` field (display those separately at the end with a count). For successful records:

```
## LinkedIn Contacts — [Company Name]
Scanned: N profiles | Ranked: M | Errors: E

Rank | Name | Role | Deg | Mut | ProxScore | EduScore | NetScore | FitScore | **Total** | LinkedIn
-----|------|------|-----|-----|-----------|----------|----------|----------|-----------|--------
  1  | Jane Smith | VP of Operations | 1st | 12 | 8 | 7 | 8 | 8 | **31** | linkedin.com/in/janesmith
  2  | Tom Lee | COO | 2nd | 3 | 9 | 8 | 3 | 8 | **28** | ...
  ...
```

Column key:
- **ProxScore** = role_proximity (decision authority for CoS hire)
- **EduScore** = education (school prestige)
- **NetScore** = connectedness (1st/2nd/3rd + mutuals)
- **FitScore** = industry_fit (sector alignment)
- **Total** = aggregate_rating (max 40)

Also display the top contact's `overall_rating` score and a note: "overall_rating is Claude's holistic recommendation (1–10)."

Below the table, show the **Top 3 recommended contacts** with one sentence each on why they ranked high (synthesized from their scores and headline).

### Step 5: Offer to Add Contacts to Networking

Ask:
```
Add any of these contacts to data/networking.md?
Enter names or numbers (comma-separated), or press Enter to skip:
```

Wait for user input. If the user selects contacts:

1. Read `data/networking.md`
2. For each selected contact, add a row to the Contacts table using this format:
   - **Name**: from scan results
   - **Company**: the scanned company
   - **Role/Title**: from headline (or "—" if blank)
   - **Relationship**: `target` (this is a new cold contact)
   - **Source**: `linkedin-scan`
   - **Last Interaction**: `—`
   - **Notes**: Include LinkedIn URL + degree/mutuals + top scores. Format: `[LinkedIn](url) | Deg: X | Mut: Y | Score: Z/40`
3. Write the updated `data/networking.md`
4. Confirm: "Added N contact(s) to data/networking.md."

### Step 6: Suggest Next Action

Display:
```
## Next Steps

Top contacts to reach out to:
1. [Name] — [Role] ([score]/40) → /cold-outreach "[Name]" "[Company]"
2. [Name] — [Role] ([score]/40) → /cold-outreach "[Name]" "[Company]"
3. [Name] — [Role] ([score]/40) → /cold-outreach "[Name]" "[Company]"

Run /research-company "[Company]" first if you haven't already — outreach lands better with a dossier loaded.
```

## Score Interpretation Guide

| Total (out of 40) | Recommendation |
|---|---|
| 32–40 | Priority — reach out immediately |
| 24–31 | Strong — good outreach target |
| 16–23 | Moderate — consider if others are exhausted |
| < 16 | Low — skip unless you have a specific reason |

## Caching

The scanner caches profiles in `tools/linkedin-scanner/src/cache/` — subsequent runs on the same company re-use parsed and ranked profiles unless the cache is cleared. This makes re-runs fast.
