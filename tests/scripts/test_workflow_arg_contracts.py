"""Every arg a workflow ADVERTISES must be one the code actually READS, and vice versa.

Origin 2026-08-21. Three separate defects of this exact shape landed in one day:
  - plan-hardening.js advertised `registerPath` and `outPath` in its meta; neither was read.
    The contract documented behavior that did not exist.
  - plan-hardening.js's meta LOST `targets` in a rewrite while the code still read it — and
    that arg silently bypasses the S2 target-generation stage.
  - extract-verify.js documented a `date` arg it never read.
  - research-audit.js read `cfg.date` with no default and no guard, so omitting it wrote
    `undefined-best-practices-audit.md` — a silent success with a corrupt filename.

Per this repo's enforcement-tier law: a declarative field with no reader is documentation, not
a contract. This test is the reader.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WF_DIR = ROOT / ".claude" / "workflows"

# Args read via cfg.X but deliberately NOT part of the advertised surface, with the reason.
# An entry here is an exemption and needs a justification, same contract as mutation-allow.json.
INTERNAL = {
    "plan-hardening.js": {
        # documented in meta.whenToUse prose rather than as a bare `name:` line
    },
}


def workflow_files():
    return sorted(p for p in WF_DIR.glob("*.js"))


def read_args(src):
    """Args the code actually consumes: every cfg.<name> dereference."""
    return set(re.findall(r"\bcfg\.([A-Za-z_][A-Za-z0-9_]*)", src))


def documented_args(src):
    """Args the file advertises: `//   name:` / `name?:` header lines, plus names in meta.whenToUse."""
    doc = set()
    for m in re.finditer(r"^//\s{2,}([A-Za-z_][A-Za-z0-9_]*)\??:", src, re.M):
        doc.add(m.group(1))
    meta = re.search(r"whenToUse:\s*'(.*?)',\s*$", src, re.S | re.M)
    if meta:
        for m in re.finditer(r"\b([a-z][A-Za-z0-9_]*)\?", meta.group(1)):
            doc.add(m.group(1))
        for m in re.finditer(r"\b(planText|planPath|context)\b", meta.group(1)):
            doc.add(m.group(1))
    return doc


def test_workflow_dir_is_not_empty():
    """Guard against the whole suite passing vacuously on a bad glob."""
    assert len(workflow_files()) >= 3, [p.name for p in workflow_files()]


@pytest.mark.parametrize("wf", workflow_files(), ids=lambda p: p.name)
def test_every_documented_arg_is_actually_read(wf):
    """An advertised arg the code ignores is a contract documenting behavior that does not exist."""
    src = wf.read_text()
    undocumented_readers = documented_args(src) - read_args(src)
    assert not undocumented_readers, (
        f"{wf.name} advertises {sorted(undocumented_readers)} but never reads them via cfg. "
        "Either read the arg or stop advertising it."
    )


@pytest.mark.parametrize("wf", workflow_files(), ids=lambda p: p.name)
def test_every_read_arg_is_documented(wf):
    """An arg the code reads but does not advertise is an undocumented lever.

    `targets` in plan-hardening.js is the cautionary case: reading it skips an entire
    documented stage, and it was missing from the contract for hours.
    """
    src = wf.read_text()
    exempt = INTERNAL.get(wf.name, {})
    undocumented = read_args(src) - documented_args(src) - set(exempt)
    assert not undocumented, (
        f"{wf.name} reads {sorted(undocumented)} but does not advertise them. "
        f"Document each in the args header/meta, or add it to INTERNAL with a written reason."
    )


def test_research_audit_fails_loud_without_date():
    """date lands in the output filename and has no default; omitting it wrote `undefined-...md`."""
    src = (WF_DIR / "research-audit.js").read_text()
    assert "args.date is required" in src
    # anchor on the interpolation itself: the string "best-practices-audit.md" also appears in
    # the meta description near the top of the file, which is not where the filename is built.
    build = src.index("${cfg.date}-best-practices-audit.md")
    assert src.index("if (!cfg.date)") < build, \
        "the date guard must run BEFORE the filename is built"
