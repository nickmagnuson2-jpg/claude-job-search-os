"""Tests for tools/scorer_eval.py — the read-only calibration outcome resolver.

Covers the 4-class weak-label taxonomy (engaged=positive / rejected-early=negative /
nick-declined=excluded / unlabeled) and the cross-tool parity guard that pins the
local pipeline row-split to pipe_read's active-row count (fable-audit Theme 2 lesson).
"""
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from tools import scorer_eval as se


# --- company outcome taxonomy ----------------------------------------------

def test_engaged_stage_is_positive():
    assert se.classify_company_outcome("Phone Screen") == se.POSITIVE
    assert se.classify_company_outcome("Interview - onsite loop") == se.POSITIVE
    assert se.classify_company_outcome("Offer") == se.POSITIVE


def test_engagement_recovered_from_notes_when_stage_is_terminal():
    # A company that interviewed then got rejected: Stage lost it, Notes kept it.
    assert se.classify_company_outcome(
        "Closed - rejected", "Recruiter screen passed 3/10; onsite 3/20") == se.POSITIVE


def test_notes_product_description_is_not_engagement():
    # Bare description words must NOT count as a process event.
    assert se.classify_company_outcome(
        "Considered - passed (self)", "B2B deal-pricing CPQ; we offer a use case tool") == se.NICK_DECLINED


def test_nick_self_pass_is_excluded_not_negative():
    assert se.classify_company_outcome("Considered - passed (self, 7/7)") == se.NICK_DECLINED
    assert se.classify_company_outcome("Withdrawn", "Applied via Ashby; GTM role") == se.NICK_DECLINED


def test_terminal_without_engagement_is_negative():
    assert se.classify_company_outcome("Closed - they passed") == se.NEGATIVE
    assert se.classify_company_outcome("Closed - not a fit") == se.NEGATIVE


def test_backlog_and_pending_are_unlabeled():
    assert se.classify_company_outcome("Researching") == se.UNLABELED
    assert se.classify_company_outcome("To Evaluate") == se.UNLABELED
    assert se.classify_company_outcome("Applied 2026-07-08 via Paraform") == se.UNLABELED


def test_engaged_then_nick_withdrew_stays_positive():
    # Priority: mutual engagement wins over a later self-withdrawal.
    assert se.classify_company_outcome("Withdrawn", "Executive screen scheduled 6/15") == se.POSITIVE


# --- contact outcome taxonomy ----------------------------------------------

def test_contact_reply_is_positive():
    assert se.classify_contact_outcome("Replied") == se.POSITIVE


def test_contact_sent_or_noreply_is_negative():
    assert se.classify_contact_outcome("Sent") == se.NEGATIVE
    assert se.classify_contact_outcome("No reply") == se.NEGATIVE


def test_contact_drafted_is_unlabeled():
    assert se.classify_contact_outcome("Drafted") == se.UNLABELED


# --- tally excludes nick-declined + unlabeled from resolved -----------------

def test_resolved_excludes_declined_and_unlabeled():
    rows = [
        {"label": se.POSITIVE}, {"label": se.POSITIVE},
        {"label": se.NEGATIVE},
        {"label": se.NICK_DECLINED}, {"label": se.NICK_DECLINED},
        {"label": se.UNLABELED},
    ]
    t = se._tally(rows)
    assert t["resolved"] == 3           # 2 pos + 1 neg
    assert t["total"] == 6
    assert t["nick-declined"] == 2


# --- cross-tool parity: local row-split vs pipe_read active count -----------

def test_verified_ledger_overrides_deterministic(tmp_path):
    """A verified ledger label wins over the deterministic stage classification."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-pipeline.md").write_text(
        "| Company | Role | Stage | Date | Next | CV | Notes | URL |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| Acme | PM | Closed - rejected | 1/1 | - | - | applied then closed | u |\n",
        encoding="utf-8",
    )
    caldir = tmp_path / "data" / "calibration"
    caldir.mkdir()
    # Deterministic on 'Closed - rejected' (no engagement) = negative; ledger says engaged.
    (caldir / "company-outcomes.json").write_text(
        json.dumps([{"entity": "Acme", "final_label": "engaged"}]), encoding="utf-8",
    )
    import importlib
    import tools.scorer_eval as se_mod
    importlib.reload(se_mod)
    rows = se_mod.resolve_company_outcomes(tmp_path)
    acme = [r for r in rows if r["entity"] == "Acme"][0]
    assert acme["label"] == se_mod.POSITIVE          # engaged -> positive (ledger wins)
    assert acme["deterministic_label"] == se_mod.NEGATIVE
    assert acme["source"] == "verified"


def test_no_ledger_falls_back_to_deterministic(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "job-pipeline.md").write_text(
        "| Company | Role | Stage | Date | Next | CV | Notes | URL |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| Beta | PM | Phone Screen | 1/1 | - | - | - | u |\n",
        encoding="utf-8",
    )
    rows = se.resolve_company_outcomes(tmp_path)
    beta = [r for r in rows if r["entity"] == "Beta"][0]
    assert beta["source"] == "deterministic"
    assert beta["label"] == se.POSITIVE


def test_ledger_label_map_covers_taxonomy():
    assert set(se.LEDGER_LABEL_MAP) == {"engaged", "rejected-early", "nick-declined", "unlabeled"}
    assert se.LEDGER_LABEL_MAP["engaged"] == se.POSITIVE
    assert se.LEDGER_LABEL_MAP["rejected-early"] == se.NEGATIVE


def test_pipeline_row_split_parity_with_pipe_read():
    """The local all-rows split, restricted to non-terminal rows, must match
    pipe_read's active_entries count on the live pipeline — so the two parsers
    can't silently diverge (Theme 2 lesson)."""
    from tools import pipe_read, stage_vocab
    path = REPO_ROOT / "data" / "job-pipeline.md"
    if not path.exists():
        import pytest
        pytest.skip("live pipeline not present in this env")
    content = path.read_text(encoding="utf-8")
    pr = pipe_read.parse_pipeline(content, date.today())
    pr_active = len(pr["active_entries"])
    my_nonterminal = sum(
        1 for _c, s, _n in se._pipeline_rows(content) if not stage_vocab.is_terminal_stage(s)
    )
    assert my_nonterminal == pr_active, (my_nonterminal, pr_active)
