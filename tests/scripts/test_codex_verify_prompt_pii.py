"""A review prompt bound for a model with pii_gate set is scanned before it leaves the machine.

Nick, 2026-11-28: Grok may review code, but "make sure that no personal PII is passed to
Grok". The jail already stops the model READING private files; this covers the TEXT we
send it: the target, claims, questions, environment facts and the diff. Only the diff's
added lines are scanned, because removed and context lines are text already on the branch
base (a branch that deletes a name must still be reviewable).
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))

import codex_verify as cv  # noqa: E402

TOKENS = ["Zorpex Quandary", "Blimfield"]


def _prompt(target="review the parser", diff=""):
    block = f"THE CHANGE UNDER REVIEW (git diff, scoped to the named paths):\n```diff\n{diff}\n```" if diff else ""
    return "\n\n".join(p for p in ["Adversarially verify.", f"WHAT TO VERIFY: {target}", block, "REPORT"] if p)


def test_a_token_in_the_prompt_text_is_a_hit():
    assert cv.prompt_pii_hits(_prompt(target="email Zorpex Quandary about it"), TOKENS) == ["Zorpex Quandary"]


def test_a_clean_prompt_has_no_hits():
    assert cv.prompt_pii_hits(_prompt(), TOKENS) == []


def test_a_token_on_an_added_diff_line_is_a_hit():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old = 1\n+who = 'Blimfield'"
    assert cv.prompt_pii_hits(_prompt(diff=diff), TOKENS) == ["Blimfield"]


def test_a_token_only_on_removed_or_context_lines_is_not_a_hit():
    diff = ("--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n owner = 'Blimfield'\n"
            "-name = 'Zorpex Quandary'\n+name = 'the owner'")
    assert cv.prompt_pii_hits(_prompt(diff=diff), TOKENS) == []


def test_review_tokens_add_the_sealed_sibling_name_derived_not_spelled(tmp_path):
    root = tmp_path / "somerepo"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / ".pii-denylist.txt").write_text("Blimfield\n", encoding="utf-8")
    toks = cv.review_pii_tokens(root)
    assert "Blimfield" in toks
    assert "somerepo-sealed" in toks


def test_review_tokens_fail_closed_without_a_denylist(tmp_path):
    root = tmp_path / "somerepo"
    (root / "tools").mkdir(parents=True)
    with pytest.raises(RuntimeError):
        cv.review_pii_tokens(root)


def test_only_grok_carries_the_gate():
    assert cv.MODELS["grok"].pii_gate is True
    assert cv.MODELS["codex"].pii_gate is False
    assert cv.MODELS["fable"].pii_gate is False


def test_run_refuses_before_dispatch_when_the_prompt_has_a_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "build_prompt", lambda *a, **k: _prompt(target="ask Blimfield"))
    monkeypatch.setattr(cv, "review_pii_tokens", lambda root: TOKENS)

    def no_dispatch(*a, **k):
        raise AssertionError("dispatched despite a PII hit")

    monkeypatch.setattr(cv, "jailed_argv", no_dispatch)
    monkeypatch.setattr(cv, "sandbox_policy", no_dispatch)
    with pytest.raises(SystemExit) as e:
        cv.run(tmp_path, "t", [], "", tmp_path / "r.md", None, False, model="grok")
    assert "PII" in str(e.value)
    assert "1 " in str(e.value)


def test_run_print_only_does_not_need_the_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "build_prompt", lambda *a, **k: _prompt(target="ask Blimfield"))

    def no_scan(root):
        raise AssertionError("print-only sends nothing and must not need the denylist")

    monkeypatch.setattr(cv, "review_pii_tokens", no_scan)
    out = cv.run(tmp_path, "t", [], "", tmp_path / "r.md", None, True, model="grok")
    assert out["status"] == "printed"
