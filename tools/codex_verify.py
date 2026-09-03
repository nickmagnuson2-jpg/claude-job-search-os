#!/usr/bin/env python3
"""codex_verify.py -- run a second model against work that matters, and land the result.

WHY THIS EXISTS
---------------
Measured over three runs on 2026-09-02 and one on 2026-09-03, Codex found something the
author could not see every single time: three P0s in a plan, a counterexample that broke
a framing already built into a shipped tool, a silent-loss defect that the FIX for a
silent-loss defect had rebuilt, and four wrong claims in an analysis Claude was about to
report as fact. A different model is the strong form of anti-anchoring -- a fresh context
of the SAME model repeats the same failure modes.

THE SANDBOX, AND WHY WE DO NOT LOOSEN IT
----------------------------------------
`--sandbox workspace-write` is a LOCAL execution policy on the shell commands the model
runs on this machine. We need write access for one reason only: so Codex writes its own
report, because `-o` captures the closing chat message and NOT the artifact (that cost a
318-line plan on the first run).

The price of that sandbox is that Codex is cut off from the network and from the user's
session. On 2026-09-02 its LEAD finding was "automation is off, launchctl returned zero
jobs" -- false, an artifact of its own isolation, stated first, with cited evidence, in
a register indistinguishable from its true findings.

The fix is NOT `danger-full-access`, which would hand an external model unrestricted
shell on this machine to buy a launchctl reading. The fix is to stop asking it
environment questions: this wrapper runs the environment probes HERE, in the real shell,
and pastes the answers into the prompt as established facts. Its isolation then stops
mattering, and the discount rule becomes structural instead of something to remember.

THE OUTPUT HAS A CONSUMER
-------------------------
Nick, 2026-09-03: "I want to make sure that we don't fall into the same trap as
yesterday, making sure that the output there actually feeds into something else." That
trap is precise: the career scanner scored ~30 roles a night for three weeks into a file
nothing read. So this tool does not merely write a report. Every run appends a ledger
row that (a) tools/cross_model_gate.py reads to clear or block a push, and (b) carries
findings whose disposition /standup surfaces until someone resolves them.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools import cross_model_gate as gate  # noqa: E402

# Probes run HERE and pasted in as facts, so the model never needs to ask. Each one is a
# question a sandboxed model has already answered wrongly, or would.
ENV_PROBES = [
    ("loaded launchd jobs", "launchctl list | grep -c jobsearch || true"),
    ("git HEAD", "git log --oneline -1"),
    ("working tree", "git status --short | head -20 || true"),
    ("python", "python3 --version"),
]

SANDBOX_WARNING = """\
CONSTRAINT -- READ THIS BEFORE ANY ENVIRONMENT CLAIM. You are running under a sandbox
that cuts you off from the network and from the user's login session. A previous run of
yours reported "automation is off, launchctl returned zero jobs" as its LEAD finding and
it was FALSE: an artifact of your own isolation, not a fact about the machine.

Therefore: make NO claims about live environment state -- services, scheduler, network,
whether a job is loaded, whether a remote is reachable. Any command you run that probes
those returns a value about YOUR SANDBOX, not about the system. The facts you need have
been gathered in the real shell and are given below; treat them as authoritative and do
not re-derive them. Reason about code, recorded data, and dataflow."""

REPORT_RULES = """\
OUTPUT RULES.
1. Write your report to {report}. Write the FILE yourself; do not rely on your closing
   message, which is captured separately and discarded.
2. End the file with a section titled exactly "## FINDINGS (machine-readable)" holding a
   JSON array. One object per finding: {{"id": "F1", "severity": "P0|P1|P2",
   "summary": "<one sentence>"}}. This array is parsed. A finding you state only in prose
   reaches nobody.
3. Priority-order the findings. Say which of my claims are WRONG, which are UNPROVEN, and
   which are right for the wrong reason. Assume I am wrong and try to prove it.
   Agreement is worth nothing; a defect I can act on is worth everything.
Do not ask questions."""


def gather_env_facts(repo_root: Path) -> str:
    """Answer the environment questions in the REAL shell, once, up front."""
    lines = ["ESTABLISHED ENVIRONMENT FACTS (gathered in the real shell, authoritative):"]
    for label, cmd in ENV_PROBES:
        try:
            out = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                 cwd=str(repo_root), timeout=30).stdout.strip()
        except (subprocess.SubprocessError, OSError) as exc:
            out = f"<probe failed: {exc}>"
        lines.append(f"  {label}: {out or '<empty>'}")
    return "\n".join(lines)


# A diff larger than this buries the signal it exists to surface. Truncation is LOUD:
# a silently shortened diff produces a review of something other than the work.
MAX_DIFF_LINES = 800

CLAIMS_HEADER = """\
CLAIMS TO CHECK. For each, say whether it is WRONG, UNPROVEN, or right for the wrong
reason. Numbered so your reply can address them individually:"""

KNOWN_ERRORS_HEADER = """\
MISTAKES I HAVE ALREADY MADE ON THIS WORK, so you can calibrate how much to trust me,
and so you can tell me which of my surviving conclusions rest on contaminated evidence:"""

DIVERGE_FRAMING = """\
Do NOT review an existing solution. Produce your own independent answer to the goal
above, from scratch. You are deliberately not being shown the existing work, the diff,
or any conclusions already reached: an agent that has seen the original anchors to it
even when told not to, and that contamination is silent -- the output looks independent
while drifting toward what it saw. The DIVERGENCE between your answer and the existing
one is the product. Convergence on everything would mean this run bought nothing."""


def truncate_diff(diff: str, cap: int = MAX_DIFF_LINES) -> str:
    lines = diff.splitlines()
    if len(lines) <= cap:
        return diff
    kept = "\n".join(lines[:cap])
    return (f"{kept}\n\n[DIFF TRUNCATED: showing {cap} of {len(lines)} lines. You are "
            f"NOT seeing the whole change; say so if a conclusion needs the rest.]")


def gather_diff(repo_root: Path, paths: list[str], cap: int = MAX_DIFF_LINES) -> str:
    """The working diff for exactly the named paths, never the whole repo."""
    if not paths:
        return ""
    try:
        proc = subprocess.run(["git", "diff", "HEAD", "--"] + list(paths),
                              capture_output=True, text=True,
                              cwd=str(repo_root), timeout=60)
    except (subprocess.SubprocessError, OSError):
        return ""
    return truncate_diff(proc.stdout, cap) if proc.stdout.strip() else ""


def build_prompt(repo_root: Path, target: str, question: str, report: Path,
                 prior: str | None, mode: str = "verify",
                 paths: list[str] | None = None,
                 claims: list[str] | None = None,
                 known_errors: str = "") -> str:
    if mode not in ("verify", "diverge"):
        raise ValueError(f"unknown mode {mode!r}; expected 'verify' or 'diverge'")
    paths = list(paths or [])
    claims = list(claims or [])

    if mode == "diverge":
        # Anti-anchoring is STRUCTURAL here, not advisory: the diff and the claims are
        # never assembled, so they cannot leak in by a caller's oversight.
        return "\n\n".join([
            "Produce an independent answer. Do not agree with anyone; there is nobody "
            "to agree with.",
            f"Repo: {repo_root}",
            f"GOAL: {target}",
            DIVERGE_FRAMING,
            *( [f"SPECIFIC QUESTIONS:\n{question}"] if question else [] ),
            SANDBOX_WARNING,
            gather_env_facts(repo_root),
            REPORT_RULES.format(report=report),
        ])

    parts = [
        "Adversarially verify the work described below. Assume it is wrong and try to "
        "prove it. I want defects, not a summary. Agreement is worth nothing to me; a "
        "defect I can act on is worth everything.",
        f"Repo: {repo_root}",
        f"WHAT TO VERIFY: {target}",
    ]
    if claims:
        numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
        parts.append(f"{CLAIMS_HEADER}\n{numbered}")
    if known_errors:
        parts.append(f"{KNOWN_ERRORS_HEADER}\n{known_errors}")
    diff = gather_diff(repo_root, paths, MAX_DIFF_LINES)
    if diff:
        parts.append(f"THE CHANGE UNDER REVIEW (git diff, scoped to the named paths):\n"
                     f"```diff\n{diff}\n```")
    if prior:
        parts.append(
            f"Read {prior} FIRST -- that is your own prior review of this work. Check "
            f"whether what it raised was actually fixed, and whether the fixes "
            f"introduced new defects. Do not merely re-derive it.")
    if question:
        parts.append(f"SPECIFIC QUESTIONS:\n{question}")
    # Warning FIRST, then the facts it points at: it says "given below", and a prompt
    # whose own cross-reference is backwards invites the model to go probing anyway.
    parts.append(SANDBOX_WARNING)
    parts.append(gather_env_facts(repo_root))
    parts.append(REPORT_RULES.format(report=report))
    return "\n\n".join(parts)


def parse_findings(report: Path) -> list[dict]:
    """Pull the machine-readable block. Prose-only findings reach nobody by design."""
    if not report.is_file():
        return []
    text = report.read_text(encoding="utf-8")
    marker = "## FINDINGS (machine-readable)"
    if marker not in text:
        return []
    tail = text.split(marker, 1)[1]
    start = tail.find("[")
    if start < 0:
        return []
    depth, end = 0, None
    for i, ch in enumerate(tail[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return []
    try:
        items = json.loads(tail[start:end])
    except json.JSONDecodeError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and it.get("summary"):
            out.append({"id": str(it.get("id") or f"F{len(out) + 1}"),
                        "severity": str(it.get("severity") or "P2"),
                        "summary": str(it["summary"]),
                        # Unset on purpose. An undispositioned finding is what /standup
                        # keeps surfacing, so a report cannot be quietly filed away.
                        "disposition": None})
    return out


def run(repo_root: Path, target: str, paths: list[str], question: str,
        report: Path, prior: str | None, print_only: bool,
        mode: str = "verify", claims: list[str] | None = None,
        known_errors: str = "") -> dict:
    prompt = build_prompt(repo_root, target, question, report, prior,
                          mode=mode, paths=paths, claims=claims,
                          known_errors=known_errors)
    if print_only:
        print(prompt)
        return {"status": "printed", "report": str(report)}

    report.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "-"],
        input=prompt, capture_output=True, text=True, cwd=str(repo_root))

    findings = parse_findings(report)
    row = {
        "recorded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": target,
        "report": str(report.relative_to(repo_root)) if report.is_absolute()
                  and str(report).startswith(str(repo_root)) else str(report),
        "paths": list(paths),
        "findings": findings,
        "waived": False,
        "rc": proc.returncode,
        "report_written": report.is_file(),
    }
    gate.append_row(repo_root, row)
    return {"status": "ok" if report.is_file() else "no_report_written",
            "rc": proc.returncode, "report": row["report"],
            "findings": len(findings),
            "open_findings": len([f for f in findings if not f["disposition"]]),
            "stderr_tail": proc.stderr[-500:] if proc.returncode else ""}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", required=True, help="what Codex should verify")
    ap.add_argument("--paths", nargs="*", default=[],
                    help="repo paths this verification covers; the gate matches on these")
    ap.add_argument("--question", default="", help="specific questions to press on")
    ap.add_argument("--prior", default=None, help="a previous Codex report to check against")
    ap.add_argument("--report", default=None, help="where Codex writes (default: dated)")
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--print-only", action="store_true",
                    help="emit the assembled prompt and exit without calling Codex")
    ap.add_argument("--mode", choices=("verify", "diverge"), default="verify",
                    help="verify: check THIS work, diff and claims attached. "
                         "diverge: an independent answer to the same goal, with the "
                         "artifact deliberately withheld (anti-anchoring)")
    ap.add_argument("--claim", action="append", dest="claims", default=[],
                    metavar="CLAIM",
                    help="a claim to check; repeatable. Numbered in the prompt")
    ap.add_argument("--known-errors", default="",
                    help="mistakes already made on this work, so it can say which "
                         "conclusions rest on contaminated evidence")
    args = ap.parse_args(argv)

    root = Path(args.repo_root)
    if args.report:
        report = Path(args.report)
    else:
        slug = "".join(c if c.isalnum() else "-" for c in args.target.lower())[:40]
        slug = "-".join(p for p in slug.split("-") if p)
        report = (root / "output" / "analysis" /
                  f"{datetime.now().strftime('%m%d%y')}-codex-{slug}.md")

    out = run(root, args.target, args.paths, args.question, report,
              args.prior, args.print_only, mode=args.mode, claims=args.claims,
              known_errors=args.known_errors)
    print(json.dumps(out, indent=2))
    return 0 if out.get("status") in ("ok", "printed") else 1


if __name__ == "__main__":
    sys.exit(main())
