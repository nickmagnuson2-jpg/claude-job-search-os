# LinkedIn post readout runbook

**Use when:** someone asks how one of your LinkedIn posts performed. The usual case is a company that sponsored or asked for a post and wants reach and engagement after N days. The output is a one-page interactive readout behind a password, plus a short reply email.

**Target time:** under an hour once the design exists. The first build took about four hours, most of it design exploration this runbook now skips.

**The worked example**, with real names, URLs and the step-by-step log, is the dated file in that company's output folder (`output/<company>/MMDDYY-linkedin-post-readout-log.md`). This file holds only the method, because this repo is public.

**The pieces:**

| Piece | Path |
|---|---|
| Tool (parse the export, build the page, write the Vercel folder) | `tools/linkedin_post_readout.py` |
| Page template; edit its CONFIG block through a per-post config file | `framework/templates/linkedin-post-readout.html` |
| Password gate for Vercel | `framework/templates/linkedin-post-readout-middleware.js` |
| Tests: 55, including a Node test that runs the generated password gate. Mutation check on 2026-10-22: 118 of 118 mutants killed, no isolation failures (result saved as `output/analysis/102226-mutation-linkedin-post-readout.json`, gitignored). To rerun: `python3 tools/mutation_check.py tools/linkedin_post_readout.py --tests tests/scripts/test_linkedin_post_readout.py --isolation` | `tests/scripts/test_linkedin_post_readout.py` |
| Per-post config, with real names (gitignored) | `output/<company>/readout-config.json` |

The page is always generated from the template. Change the design in the template, never in a generated page, and every past and future readout picks the change up on its next build.

---

## What the readout shows

- **Breakouts:** one dot per LinkedIn member reached, regrouped by reach and response, seniority, company size, job title, location, industry and company. The dots move with an animation when the view changes.
- **Buyer highlight:** the groups the recipient sells to are the only accent colour. Everything else is grey. One colour means one thing, so nothing else on the page uses the accent.
- **Header:** an excerpt of the post, a link to it, and a links box with any link that LinkedIn does not count. Clicks on a link in the first comment and visits from a company-page tag are not in the export, so the box asks the recipient for their own analytics.
- **What it leaves out:** comparisons with your other posts. A recipient who has never sponsored a post has no baseline, and a comparison "just adds more questions."

## Honesty rules for the data

- LinkedIn reports only its top values per category, as rounded percentages. Every count derived from a share is marked ≈ and is the share times members reached.
- **"<1%" rows have no count and get no dots.** Show them by name with a labelled upper bound only: "fewer than ≈N, counted inside Not broken out", where N is 1% of members reached. The dashed outline drawn for them is that upper bound, not an estimate.
- Every group in a view uses the same dots per unit of length, so lengths compare honestly. Use one row depth per view, set by the largest named group, and let "Not broken out" wrap onto more bands at that depth.
- **Seniority definitions:** LinkedIn maps each member's current job title to a seniority level through its "proprietary taxonomy" and publishes no per-level definitions (LinkedIn Help, "Targeting options for LinkedIn Ads", answer a424655). Quote that; do not invent definitions for Partner, Owner and so on.
- **Profile viewers are people; engagements are actions.** One person can react, comment and repost, so engagements never get member dots. The reach view draws profile viewers and everyone else reached (members reached minus profile viewers) and gives engagements as a number with their breakdown. Never place either inside a demographic group, since LinkedIn does not say who they were.

## Steps

1. **Download the export.** On LinkedIn, open the post's analytics and export. The file is `SinglePostAnalytics_<name>_<id>.xlsx`. Download it on the day the recipient asked for (for example 7 days after posting), and drop it into the session.
2. **Write the config.** For a new recipient, copy the CONFIG block from the template into `output/<company>/readout-config.json`. It is JSON. Fill in:
   - the headline and the post excerpt (verbatim paragraphs)
   - the recipients, the organisation and your name
   - the links, the clicks note and the analytics-box label
   - the buyer lists (seniority levels, company-size buckets, job titles) matching who the recipient sells to, and their display names

   For a refresh of an existing readout, reuse its config unchanged.
3. **Build.** Run `python3 -I tools/linkedin_post_readout.py build output/<company>/readout-config.json <export.xlsx> output/<company>/MMDDYY-linkedin-post-readout.html --vercel-dir output/<company>/<vercel-folder>`.
   - It reads the xlsx with the standard library, because openpyxl is not installed, and shared strings must be read by cell position.
   - It fails loudly if a metric or post field is missing or empty, if a demographic row has an empty or missing cell, if profile viewers exceed members reached, if any of the six demographic categories is missing, or if the config lacks a key, at any level including list items, that the template's CONFIG block defines, or gives it a different type (the template is the only list of keys).
   - The Vercel folder name must be lowercase letters, digits and hyphens, because it becomes the cookie salt and the package name.
   - Every analytics number on the page is computed at runtime from the DATA block it writes.
   - `refresh <export.xlsx> <page.html>` replaces only the DATA block of an existing page. `data <export.xlsx>` prints the parsed export.
4. **Check every view, not just the default one.** Take a headless Chrome screenshot of each arrangement at desktop width and at 400px. In the console, confirm each view's dots add up to members reached. A change that fixes one view can break another; this happened on the first build.
5. **Publish two copies:**
   - **A claude.ai artifact (private)** for the session: publish the page file.
   - **Vercel, password-only.** `--vercel-dir` in step 3 already wrote the folder:
     - `index.html`: the page with a real `<head>` and `noindex`
     - `middleware.js`: one password field compared with the `READOUT_PASSWORD` production environment variable. On success it sets an HttpOnly, Secure cookie for 30 days holding a SHA-256 hash, not the password. The folder name is the cookie salt.
     - `package.json`

     For a new site, run `vercel link --yes --project <folder>` and `vercel env add READOUT_PASSWORD production` once. Then, from the folder, run `vercel deploy --prod --yes` and curl four cases: no cookie gives 401, a wrong password gives 401, the right password gives 303, and a request with the cookie gives 200.
   - Vercel's built-in password protection is a paid add-on, which is why this uses middleware. Do not use Basic auth: it shows a username box.
6. **Draft the reply** with `/draft-email` on the recipient's existing thread, from your own dictated wording. One ask: their traffic from LinkedIn to the page you linked. Keep numbers out of the email body so it does not go stale; the page carries the numbers.
7. **After sending,** log it to networking and the outreach log.

## Design notes (from the first build's critic rounds)

- **Critic loop:** run the `/art-direct` critic loop only if the design changes. Its data-viz references and the two extra critic questions (the page with all text removed, and whether group sizes match the printed numbers) are in `~/.claude/skills/art-direct/references/dataviz-references.md`.
- **Headline share on the chart:** a headline share that appears only in prose is a defect. Put it on the chart, for example a bracket spanning the buyer rows with the combined share and count.
- **Skip next time:** a dashboard template (it constrains a creative page), a three-variant fan-out (the design is settled), and an over-time section.
