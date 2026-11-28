"""A review prompt bound for a model with pii_gate set is gated before it leaves the machine.

Nick, 2026-11-28: Grok may review code, but "make sure that no personal PII is passed to
Grok". New text (target, claims, questions, every non-base diff line) BLOCKS on a denylist
token or a pipeline company word; base lines (removed, unchanged) are REDACTED in the prompt
that is sent. A PII-gated model's jail also denies output/ reads.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))

import codex_verify as cv  # noqa: E402

TOKENS = ["Zorpex Quandary", "Blimfield"]
WORDS = ["Lantern"]


def _built(monkeypatch, tmp_path, diff="", target="review the parser", question=""):
    """A prompt from the real build_prompt, so the header the gate keys on cannot drift."""
    monkeypatch.setattr(cv, "gather_diff", lambda *a, **k: diff)
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "ENV FACTS")
    return cv.build_prompt(tmp_path, target, question, tmp_path / "r.md", None,
                           paths=["x.py"] if diff else [])


def test_a_token_in_the_target_blocks(monkeypatch, tmp_path):
    p = _built(monkeypatch, tmp_path, target="email Zorpex Quandary about it")
    assert cv.gate_prompt(p, TOKENS, WORDS)[1] == ["Zorpex Quandary"]


def test_a_clean_prompt_passes_unchanged(monkeypatch, tmp_path):
    p = _built(monkeypatch, tmp_path, diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2")
    sent, hits = cv.gate_prompt(p, TOKENS, WORDS)
    assert hits == [] and sent == p


def test_a_token_on_an_added_line_blocks(monkeypatch, tmp_path):
    p = _built(monkeypatch, tmp_path, diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old = 1\n+who = 'Blimfield'")
    assert cv.gate_prompt(p, TOKENS, WORDS)[1] == ["Blimfield"]


def test_an_added_line_starting_with_two_plus_signs_is_scanned(monkeypatch, tmp_path):
    p = _built(monkeypatch, tmp_path, diff="--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n-x;\n++i; // Blimfield")
    assert cv.gate_prompt(p, TOKENS, WORDS)[1] == ["Blimfield"]


def test_a_new_file_name_blocks(monkeypatch, tmp_path):
    diff = ("diff --git a/output/blimfield.md b/output/Blimfield.md\nnew file mode 100644\n"
            "--- /dev/null\n+++ b/output/Blimfield.md\n@@ -0,0 +1 @@\n+hello")
    assert "Blimfield" in cv.gate_prompt(_built(monkeypatch, tmp_path, diff=diff), TOKENS, WORDS)[1]


def test_base_lines_are_redacted_in_the_sent_prompt_not_excused(monkeypatch, tmp_path):
    diff = ("--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n owner = 'Blimfield'\n"
            "-name = 'Zorpex Quandary'\n+name = 'the owner'")
    sent, hits = cv.gate_prompt(_built(monkeypatch, tmp_path, diff=diff), TOKENS, WORDS)
    assert hits == []
    assert "Blimfield" not in sent and "Zorpex Quandary" not in sent
    assert sent.count("[redacted]") == 2


def test_a_lookalike_diff_fence_in_the_question_is_not_excused(monkeypatch, tmp_path):
    q = "see this:\n```diff\n owner = 'Blimfield'\n```"
    p = _built(monkeypatch, tmp_path, question=q,
               diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2")
    assert cv.gate_prompt(p, TOKENS, WORDS)[1] == ["Blimfield"]


def test_a_copy_of_the_real_header_in_the_question_makes_everything_new_text(monkeypatch, tmp_path):
    q = cv.DIFF_HEADER + " owner = 'Blimfield'\n```"
    p = _built(monkeypatch, tmp_path, question=q,
               diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2")
    assert cv.gate_prompt(p, TOKENS, WORDS)[1] == ["Blimfield"]


def test_a_header_with_no_closing_fence_makes_everything_new_text():
    # mutation r2 survivor: _split_diff_block's `if end < 0` guard
    p = "WHAT TO VERIFY: x\n\n" + cv.DIFF_HEADER + " owner = 'Blimfield'\n more"
    assert cv.gate_prompt(p, TOKENS, WORDS)[1] == ["Blimfield"]


def test_a_pipeline_company_word_used_as_a_name_blocks(monkeypatch, tmp_path):
    p = _built(monkeypatch, tmp_path, target="review the outreach to Lantern before Monday")
    assert cv.gate_prompt(p, TOKENS, WORDS)[1] == ["Lantern"]


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


def test_a_gated_jail_denies_output_and_reopens_only_the_prior(tmp_path):
    prior = tmp_path / "output" / "analysis" / "prior.md"
    pol = cv.sandbox_policy(tmp_path, deny_output=True, read_allow=[prior])
    deny = f'(deny file-read* (subpath "{tmp_path.resolve()}/output"))'
    allow = f'(allow file-read* (literal "{prior.resolve()}"))'
    assert deny in pol and allow in pol
    assert pol.index(deny) > pol.index(f'(allow file-read* (subpath "{tmp_path.resolve()}/output/analysis"))')
    assert pol.index(allow) > pol.index(deny)
    assert deny not in cv.sandbox_policy(tmp_path)


def test_run_refuses_before_dispatch_when_new_text_has_a_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "build_prompt", lambda *a, **k: "WHAT TO VERIFY: ask Blimfield")
    monkeypatch.setattr(cv, "review_pii_tokens", lambda root: TOKENS)
    monkeypatch.setattr(cv, "review_target_words", lambda root: WORDS)

    def no_dispatch(*a, **k):
        raise AssertionError("dispatched despite a PII hit")

    monkeypatch.setattr(cv, "jailed_argv", no_dispatch)
    monkeypatch.setattr(cv, "sandbox_policy", no_dispatch)
    with pytest.raises(SystemExit) as e:
        cv.run(tmp_path, "t", [], "", tmp_path / "r.md", None, False, model="grok")
    assert "BLOCKED" in str(e.value) and "1 " in str(e.value)


def test_run_print_only_does_not_need_the_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "build_prompt", lambda *a, **k: "WHAT TO VERIFY: ask Blimfield")

    def no_scan(root):
        raise AssertionError("print-only sends nothing and must not need the denylist")

    monkeypatch.setattr(cv, "review_pii_tokens", no_scan)
    out = cv.run(tmp_path, "t", [], "", tmp_path / "r.md", None, True, model="grok")
    assert out["status"] == "printed"
