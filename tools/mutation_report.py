#!/usr/bin/env python3
"""mutation_report.py — turn a mutation sweep into the ranked survivor map.

Every ratio is printed with its denominator AND how that denominator was obtained, and
sweep coverage is stated FIRST, so a partial run can never be read as a complete one.
That is not decoration: the failure this guards against is a real one in this repo, where
a scan of 64 of 883 files was reported as a corpus-wide percentage.

USAGE
    PYTHONIOENCODING=utf-8 python3 tools/mutation_report.py
    PYTHONIOENCODING=utf-8 python3 tools/mutation_report.py --out report_body.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get("MUTATION_REPO_ROOT",
                                Path(__file__).resolve().parents[1])).resolve()
DEFAULT_STATE = REPO_ROOT / "output" / "analysis" / "082626-mutation-baseline"


def build(state_dir: Path) -> str:
    targets = {r["tool"]: r for r in
               json.loads((state_dir / "targets.json").read_text(encoding="utf-8"))}
    rows = [json.loads(l) for l in
            (state_dir / "baseline.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

    auditable = {t: r for t, r in targets.items() if r["mutants"] > 0}
    measured = {r["tool"] for r in rows}
    missing = sorted(set(auditable) - measured)

    for r in rows:
        r["mutants"] = r.get("mutants") or auditable.get(r["tool"], {}).get("mutants", 0)
        r["pct"] = (100 * (r.get("survived") or 0) / r["mutants"]) if r["mutants"] else 0.0
        r["cat"] = "hook" if r.get("h") or r.get("hooked") else (
            "writer" if r.get("w") or r.get("writer") else "other")

    ok = [r for r in rows if str(r.get("status", "")).startswith("UNAUDITED") is False]
    bad = [r for r in rows if str(r.get("status", "")).startswith("UNAUDITED")]
    tot_m = sum(r["mutants"] for r in ok)
    tot_s = sum(r.get("survived") or 0 for r in ok)
    tot_k = sum(r.get("killed") or 0 for r in ok)

    out: list[str] = []
    w = out.append
    w("## Survivor map — mutation survival across the tool corpus\n")
    w(f"**Sweep coverage: {len(rows)} of {len(auditable)} auditable tools measured.** "
      f"Selection is deterministic: every `tools/*.py` with a matching "
      f"`tests/scripts/test_<name>.py` and no `mutation-allow.json` entry "
      f"({len(targets)} selected; `mutation_check.py` self-excludes, leaving "
      f"{len(auditable)} auditable).")
    if missing:
        w(f"\n**{len(missing)} NOT MEASURED — unaudited, not clean:** "
          + ", ".join(f"`{m[6:]}`" for m in missing) + "\n")
    else:
        w("\nAll auditable tools were measured.\n")
    if bad:
        w(f"**{len(bad)} errored or timed out — unaudited, not clean:** "
          + ", ".join(f"`{r['tool'][6:]}` ({r['status']})" for r in bad) + "\n")

    if tot_m:
        w(f"\n**Over the {len(ok)} tools measured cleanly: {tot_s} survivors of {tot_m} "
          f"mutants = {100*tot_s/tot_m:.1f}% survival.** {tot_k} killed. A survivor is a "
          f"decision that was changed with the whole suite still green.\n")

    w("\n### By category\n")
    w("| category | tools | mutants | survivors | survival |")
    w("|---|---|---|---|---|")
    for cat in ("hook", "writer", "other"):
        rs = [r for r in ok if r["cat"] == cat]
        if not rs:
            continue
        m = sum(r["mutants"] for r in rs)
        s = sum(r.get("survived") or 0 for r in rs)
        w(f"| {cat} | {len(rs)} | {m} | {s} | {100*s/m:.0f}% |" if m else
          f"| {cat} | {len(rs)} | 0 | 0 | n/a |")

    w("\n### Ranked worst-first (the work list)\n")
    w("| # | tool | cat | mutants | survivors | survival | tests | mins |")
    w("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(sorted(ok, key=lambda x: -(x.get("survived") or 0)), 1):
        w(f"| {i} | `{r['tool'][6:]}` | {r['cat']} | {r['mutants']} | "
          f"**{r.get('survived')}** | {r['pct']:.0f}% | {r.get('tests')} | "
          f"{r.get('elapsed', 0)/60:.1f} |")

    clean = [r for r in ok if (r.get("survived") or 0) == 0]
    w(f"\n**{len(clean)} tools at zero survivors**"
      + (": " + ", ".join(f"`{r['tool'][6:]}`" for r in clean) if clean else "."))

    iso = [r for r in ok if r.get("isolation_failures")]
    w(f"\n\n**{len(iso)} tools fail `--isolation`** — the test file does not pass when run "
      "ALONE, so it is relying on suite ordering"
      + (": " + ", ".join(f"`{r['tool'][6:]}`" for r in iso) if iso else "."))

    w("\n\n### Crash kills — of the mutants that died, how many died to an assertion\n")
    w("A weak kill means the suite noticed the mutation only because the code CRASHED, "
      "not because a test checked a value. Few survivors plus mostly weak kills is not a "
      "well-tested tool; it is one that crashes readily.\n")
    weak_rows = [r for r in ok if r.get("weak") is not None and (r.get("killed") or 0)]
    if weak_rows:
        w("| tool | killed | weak (crash-only) | share |")
        w("|---|---|---|---|")
        for r in sorted(weak_rows, key=lambda x: -(x.get("weak") or 0))[:15]:
            k, wk = r["killed"], r["weak"]
            w(f"| `{r['tool'][6:]}` | {k} | {wk} | {100*wk/k:.0f}% |")
        tw = sum(r["weak"] for r in weak_rows); tk = sum(r["killed"] for r in weak_rows)
        w(f"\n**{tw} of {tk} kills ({100*tw/tk:.0f}%) were crash-only** across the "
          f"{len(weak_rows)} tools reporting both numbers.")
    w("\n\nThis field was repaired 2026-08-26 (commit a4a07fe); it previously measured "
      "pytest's rendering rather than whether a test asserted, and was conditional on the "
      "compared type. Results measured before that commit are not comparable — see "
      "docs/mutation-baseline-runbook.md. It still over-credits one pattern: a test that "
      "re-raises a subprocess's unexpected exit as AssertionError dresses a crash as an "
      "assertion, which no classifier change can fix.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--out", type=Path, default=None,
                    help="also write the report body to this path")
    args = ap.parse_args(argv)

    if not (args.state_dir / "baseline.jsonl").exists():
        print(f"no sweep results at {args.state_dir}/baseline.jsonl", file=sys.stderr)
        return 1
    body = build(args.state_dir)
    print(body)
    if args.out:
        args.out.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
