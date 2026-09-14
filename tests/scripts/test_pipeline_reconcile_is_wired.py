"""Adoption gate for tools/pipeline_reconcile.py.

READ THIS BEFORE TRUSTING IT. This file is a PRESENCE check, and presence is a weaker
property than execution. It proves the two skills that author company shortlists name the
tool and mark the step mandatory. It does NOT prove the step runs, that its output reaches
the dossier, or that a closed company is actually excluded from the final ranking -- a
Markdown skill is instructions for a model, and no pytest can execute it.

That limitation is named here on purpose rather than left for a reader to discover, because
this repo has a recorded failure of exactly this shape: a guard whose own inputs died while
its tests stayed green (`CLAUDE.md`, the mutation-survival Hard Rule). Cross-model review on
2026-09-14 raised the same objection against an earlier draft of this file.

What IS executable, and is covered in test_pipeline_reconcile.py:
  - the tool's classification, on synthetic fixtures AND against the live pipeline
  - its nonzero exit when a name is already tracked, which is what makes a skipped step
    visible in a transcript rather than silent
  - the exact markdown block the skill is told to paste

Extraction enforces non-drift; it does not enforce adoption. This file is the adoption half,
and it is the weakest link in the chain by construction. Treat a green run here as "the
instruction is present", never as "the reconciliation happened".
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every skill whose output includes a list of companies Nick might act on.
AUTHORING_SKILLS = (
    ".claude/skills/research-company/SKILL.md",
    ".claude/skills/discover-companies/SKILL.md",
)


@pytest.mark.parametrize("rel", AUTHORING_SKILLS)
def test_the_skill_names_the_reconciler(rel):
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "pipeline_reconcile.py" in text, (
        f"{rel} authors a company shortlist but never invokes the pipeline reconciler. "
        "An agent-produced shortlist has reached Nick with already-closed companies "
        "ranked #1 twice; this step is what catches that.")


@pytest.mark.parametrize("rel", AUTHORING_SKILLS)
def test_the_step_is_marked_mandatory(rel):
    """A step an agent may skip under time pressure is not a step.

    Both recorded fires happened while attention was on a different primary target, which
    is exactly when an optional-sounding instruction gets dropped.
    """
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    window = text[max(0, text.find("pipeline_reconcile.py") - 1200):]
    assert "MANDATORY" in window, (
        f"{rel} mentions the reconciler but does not mark the step mandatory")


@pytest.mark.parametrize("rel", AUTHORING_SKILLS)
def test_the_skill_says_exit_2_is_not_an_all_clear(rel):
    """The false-zero channel, spelled out where the caller will read it.

    An unreadable pipeline must never be summarized as 'nothing matched'. That confusion
    is the defect one level down from the one this tool fixes.
    """
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "all-clear" in text or "not an all-clear" in text, (
        f"{rel} does not tell the caller that exit 2 is a failure, not a clean result")


def test_research_company_tells_the_caller_to_put_it_in_the_artifact():
    """Both fires were relayed in chat only. The dossier is what a later session reads."""
    text = (REPO_ROOT / ".claude/skills/research-company/SKILL.md").read_text(encoding="utf-8")
    assert "into the dossier" in text or "in the artifact" in text


@pytest.mark.parametrize("rel", AUTHORING_SKILLS)
def test_the_skill_forbids_silently_dropping_a_closed_company(rel):
    """Annotate, never suppress.

    Suppression would trade this defect for a worse one: a company worth re-approaching
    after a long gap would vanish from the decision surface with no trace, and unlike the
    current failure there would be nothing for a reader to notice.
    """
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    window = text[max(0, text.find("pipeline_reconcile.py") - 1200):]
    assert "Never exclude" in window or "never silently drop" in window.lower(), (
        f"{rel} does not tell the caller to keep a closed company visible")


def test_the_reconciler_exists_and_is_not_in_the_hook_namespace():
    """`tools/check_*.py` is this repo's PreToolUse hook namespace.

    Naming a non-hook library `check_*` puts it in the hook census, makes it read as
    unwired, and forces a NON_HOOK_CHECKERS entry to explain a naming mistake. The first
    draft was named `check_closed_rows.py` and `check_guard_edit_approval.py` blocked it.
    """
    assert (REPO_ROOT / "tools" / "pipeline_reconcile.py").is_file()
    assert not (REPO_ROOT / "tools" / "check_closed_rows.py").exists(), (
        "the reconciler must not live in the check_* hook namespace")
