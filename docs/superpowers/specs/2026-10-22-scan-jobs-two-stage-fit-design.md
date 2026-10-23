# /scan-jobs two-stage fit: rough cut, then a company field read

Last updated: 2026-10-22 · Status: approved design, not built

## Why

`tools/scan_jobs.py` scores roles from the posting text with the career scanner's scorer.
On the owner's labeled roles that scorer reaches AUC 0.61, and Jev on the same text 0.57-0.65.
The reasons behind the owner's not-fit verdicts are mostly absent from a posting: whether the
function already exists (a team to learn from), company stage, office policy, and comp. Two of
those live only at the company level. A role-text judge therefore has a low ceiling, whichever
model it uses.

A second finding: the earlier 72-role test mixed hand verdicts with outcome labels derived from
pipeline stage, and several "not-fit" records are really "no response" (market response, not a
fit verdict). The clean test set is the hand-labeled `fit_verdict` records with no-response
cases removed.

## Design

### Stage 1: rough cut (deterministic, in `scan_jobs.py assess`)

Drops a role only on a clear violation, and records which rule fired:

| Rule | Source of the threshold | Missing data |
|---|---|---|
| Location fails the nightly scanner's rule (`scanner.geo_ok`) | existing code | passes |
| Posted base below the comp floor | `data/goals.md` (comp facts live only there) | passes |
| Posted in-office days above the owner's maximum | `data/goals.md` | passes |
| Role is customer success, quota-carrying sales, or pure engineering (title plus `fit-spec.yaml` scope words) | `data/calibration/fit-spec.yaml` | passes |

Chief of Staff roles are NOT cut (owner decision 2026-10-22: an area of curiosity). They go to stage 2.

`goals.md` states the comp floor but, as of 2026-10-22, no maximum number of in-office days (it
says "in-person or SF-hybrid"). Until the owner adds that number, the in-office rule never fires;
the code reports it as disabled rather than guessing a value.

### Stage 2: company field read (survivors only, cached per company)

Six fields per company, each with a source URL or `unknown`:

1. Stage and funding
2. Headcount, and whether the function already exists (team to learn from)
3. Office policy
4. Comp band
5. AI-nativeness
6. Sector

- **Sources:** an `output/<slug>/` dossier under 14 days old is reused. Otherwise run a small
  number of targeted searches through the existing Exa wrapper, not a full `/research-company`.
- **Judging:** rules in code score the hard facts (stage band, office days, comp). Jev answers
  two yes/no (Noul) questions over posting plus fields: "the seat is really customer success or
  sales" and "an existing function or team to learn from is present". Code combines everything
  with weights stored in `fit-spec.yaml`.
- **Unknown fields score neutral.** They never count as a pass or a fail.
- The field read is cached per company, so several roles at one company cost one read.

### Output

A ranked table: role, company, score, and the six fields, each with its source link. The owner
chooses which rows get a full `/research-company` dossier. Nothing runs a dossier automatically.

## Proof before wiring

1. Build field reads for the hand-labeled companies (`fit_verdict` fit or not-fit, no-response
   cases removed; about 30).
2. Gate: **AUC >= 0.75**, and **the stage-1 cut drops none of the hand-labeled fits**.
3. Below the gate: report per-field separation (which field misled the score) and stop. Nothing
   goes into `/scan-jobs` until the gate passes.

The eval set names real companies and stays in gitignored `output/`. Only generic code,
fixtures with placeholder names, and this spec are public.

## Out of scope

- A trained model: about 30 clean labels is too few.
- Automatic full dossiers.
- New search or fetch infrastructure: the Exa wrapper and the ATS parsers already exist.

## Open questions resolved

| Question | Decision |
|---|---|
| Which knockouts are hard | location, posted base below floor, posted in-office days above max, and the CS / quota-sales / pure-engineering role shapes. Chief of Staff is not cut. |
| Stage-2 output | ranked list with a one-line read per field, sources linked |
| Bar before wiring | AUC >= 0.75 on hand labels; no hand-labeled fit cut by stage 1 |
