# /scan-contacts — Manual Integration Testing Checklist

> For automated unit and parser tests, run:
> `PYTHONIOENCODING=utf-8 python -m pytest tests/scripts/ -v`
>
> This checklist covers what automated tests cannot reach:
> live LinkedIn auth, browser scraping, LLM ranking, and the
> networking.md write path.
>
> Mark each item `[x]` when verified. Date and sign at the bottom.

---

## Skill: `/scan-contacts`

**Script used:** `tools/linkedin-scanner/scan.py` (via skill wrapper)
**Date tested:** YYYY-MM-DD
**Tested by:** _______________

---

## 1. Environment Setup

- [ ] `tools/linkedin-scanner/` has a `.env` file (or repo-root `.env`) with:
  - `LINKEDIN_EMAIL=your@email.com`
  - `LINKEDIN_PASSWORD=yourpassword`
  - `ANTHROPIC_API_KEY=sk-ant-...`
- [ ] Dependencies installed: `pip install -r tools/linkedin-scanner/requirements.txt`
- [ ] No import errors: `python tools/linkedin-scanner/scan.py --help` exits 0 and prints usage

---

## 2. First-Run LinkedIn Auth

- [ ] Run `/scan-contacts "Amae Health" 3` (or `python scan.py --company "Amae Health" --num 3`)
- [ ] Chrome window opens and navigates to LinkedIn login
- [ ] Login completes successfully (manual login or credentials auto-filled)
  - If LinkedIn prompts for a verification code: enter it, then let the scan continue
- [ ] Cookie file saved at `tools/linkedin-scanner/src/cache/scraper_cookie.pkl`
- [ ] Scan continues past login and retrieves at least one profile

---

## 3. Cookie Reuse (Second Run)

- [ ] Run the same command a second time
- [ ] Output includes: `"Successfully logged in to LinkedIn using cookie"`
- [ ] Login page is **not** shown again
- [ ] Scan completes faster than the first run

---

## 4. Basic Scan Smoke Test

- [ ] `/scan-contacts "Amae Health" 5` completes without crashing
- [ ] Output JSON is valid (copy into `python -c "import json,sys; json.load(sys.stdin)"`)
- [ ] Output contains at least 1 ranked profile object with:
  - `name`, `url`, `headline`, `rank.aggregate_rating` fields present
- [ ] Results are sorted by `aggregate_rating` descending (highest score first)

---

## 5. Ranking Sanity Check

- [ ] Run on a company where you know the leadership (e.g., a company you researched)
- [ ] CEO / Founder scores `role_proximity` 9–10
- [ ] Individual contributor (engineer, designer) scores `role_proximity` 1–4
- [ ] A 1st-degree connection scores `connectedness` 7–10
- [ ] A 3rd-degree connection scores `connectedness` 1

---

## 6. Cache Validation

- [ ] Run the same company scan twice
- [ ] Second run prints `"Loading cached profile for ..."` for profiles already scraped
- [ ] Second run completes faster than the first (HTML not re-fetched)
- [ ] Cache files exist at `tools/linkedin-scanner/src/cache/`:
  - `parsed_profiles.json` — parser-level cache
  - `ranked_profiles.json` — LLM-ranked cache

---

## 7. networking.md Write

- [ ] After the scan, select a contact from the ranked output when prompted
- [ ] Open `data/networking.md` and verify a new row was added with:
  - Name matching the selected contact
  - Company column set to the scanned company
  - Role column set to the contact's headline/position
  - Relationship = `target`
  - Source = `linkedin-scan`
  - Notes containing the LinkedIn URL and aggregate score
- [ ] No existing rows in `data/networking.md` were modified or deleted

---

## 8. End-to-End Pipeline (Optional)

- [ ] After the networking.md write, run `/cold-outreach "[contact name]" "[company]"`
- [ ] Skill reads the new contact from `data/networking.md` correctly
- [ ] Draft email references the contact's role and company accurately
- [ ] Outreach log updated in `data/outreach-log.md`

---

## Known Issues / Notes

> Add issues encountered during testing here.

- Selenium version must match installed Chrome — if Chrome auto-updated, run:
  `pip install --upgrade webdriver-manager` and re-test auth flow
- LinkedIn may trigger CAPTCHA on first login from a new IP — manual completion required
- If `ranked_profiles.json` grows stale (prompt changed), run with `--no-cache` flag once

---

## Automated Test Reference

| Layer | File | Tests | Requires credentials? |
|-------|------|-------|-----------------------|
| Unit | `tests/scripts/test_linkedin_scanner_unit.py` | 13 | No |
| Parser | `tests/scripts/test_linkedin_scanner_parser.py` | 15 | No |
| CLI | `tests/scripts/test_linkedin_scanner_scan.py` | 9 | No |
| Integration | This checklist | 8 scenarios | Yes |
