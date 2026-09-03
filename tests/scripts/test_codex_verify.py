"""The cross-model wrapper: prompt assembly, finding extraction, ledger landing.

WHY THIS FILE EXISTS
--------------------
Two failure modes have already cost real work and are pinned here.

1. `-o` captures Codex's closing CHAT MESSAGE, not its artifact. On 2026-09-02 that
   returned a 6-line summary while the real 318-line plan went to a path Codex chose.
   So the prompt MUST instruct it to write the file itself.
2. A sandboxed model's environment claims are false in the same confident register as
   its true findings ("automation is off, launchctl returned zero jobs" -- all ten were
   loaded). The wrapper answers those questions in the real shell and pastes the answers
   in, so the model never has to guess. If that injection breaks, the discount rule goes
   back to being something a human has to remember, which is where it failed before.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools import codex_verify as cv  # noqa: E402
from tools import cross_model_gate as gate  # noqa: E402


# --- the prompt ---------------------------------------------------------------

def _prompt(tmp_path, target="the drain fixes", question="", prior=None):
    return cv.build_prompt(tmp_path, target, question, tmp_path / "r.md", prior)


def test_the_prompt_tells_codex_to_write_the_file_itself(tmp_path):
    """The -o trap. Without this instruction the artifact is lost every run."""
    p = _prompt(tmp_path)
    assert "Write the FILE yourself" in p
    assert str(tmp_path / "r.md") in p


def test_the_prompt_carries_the_sandbox_warning(tmp_path):
    p = _prompt(tmp_path)
    assert "make NO claims about live environment state" in p
    assert "launchctl returned zero jobs" in p, (
        "the warning must cite the concrete false finding; an abstract caution is the "
        "kind of prose this project has measured converting at zero")


def test_the_warning_precedes_the_facts_it_points_at(tmp_path):
    """It says 'given below'. A prompt whose own cross-reference is backwards invites
    the model to go probing anyway."""
    p = _prompt(tmp_path)
    assert p.index("make NO claims") < p.index("ESTABLISHED ENVIRONMENT FACTS")


def test_environment_facts_are_gathered_in_the_real_shell(tmp_path):
    """THE FIX FOR THE SANDBOX PROBLEM. Not 'trust the model less' -- 'stop asking it
    questions its isolation guarantees it will answer wrongly'."""
    facts = cv.gather_env_facts(REPO_ROOT)
    assert "loaded launchd jobs:" in facts
    assert "git HEAD:" in facts
    # The probe ran here, so it reports the real machine, not a sandbox.
    assert "<empty>" not in facts.split("git HEAD:")[1][:80]


def test_a_failing_probe_does_not_break_the_prompt(tmp_path, monkeypatch):
    """A wrapper that dies because one probe failed is worse than one that says so."""
    monkeypatch.setattr(cv, "ENV_PROBES", [("bogus", "exit 7 && echo nope")])
    facts = cv.gather_env_facts(tmp_path)
    assert "bogus" in facts


def test_the_prompt_demands_a_machine_readable_block(tmp_path):
    p = _prompt(tmp_path)
    assert "## FINDINGS (machine-readable)" in p
    assert "reaches nobody" in p


def test_a_prior_report_is_referenced_when_given(tmp_path):
    """Round three's value came from making it check its OWN prior review rather than
    re-derive it."""
    p = _prompt(tmp_path, prior="output/analysis/prior.md")
    assert "output/analysis/prior.md" in p and "prior review" in p


def test_no_prior_no_dangling_reference(tmp_path):
    assert "prior review" not in _prompt(tmp_path)


def test_the_prompt_asks_for_defects_not_agreement(tmp_path):
    """Measured: 'what do you think of this framing' returns thoughtful agreement,
    which is worth nothing. A falsifiable target broke the framing in one pass."""
    p = _prompt(tmp_path).lower()
    assert "assume it is wrong" in p
    assert "agreement is worth nothing" in p


# --- finding extraction -------------------------------------------------------

def _report(tmp_path, body):
    r = tmp_path / "r.md"
    r.write_text(body, encoding="utf-8")
    return r


def test_findings_are_parsed_from_the_marked_block(tmp_path):
    r = _report(tmp_path, 'prose\n\n## FINDINGS (machine-readable)\n'
                          '[{"id":"F1","severity":"P0","summary":"ack races a scan"}]\n')
    got = cv.parse_findings(r)
    assert len(got) == 1
    assert got[0]["id"] == "F1" and got[0]["severity"] == "P0"


def test_a_parsed_finding_starts_UNDISPOSITIONED(tmp_path):
    """The drain lesson. A finding that arrives already resolved is a finding nobody
    ever looks at."""
    r = _report(tmp_path, '## FINDINGS (machine-readable)\n[{"summary":"x"}]')
    assert cv.parse_findings(r)[0]["disposition"] is None


def test_nested_brackets_inside_the_array_do_not_truncate_it(tmp_path):
    r = _report(tmp_path, '## FINDINGS (machine-readable)\n'
                          '[{"summary":"see foo[0] and bar[1]","id":"F1"},'
                          '{"summary":"second","id":"F2"}]')
    assert [f["id"] for f in cv.parse_findings(r)] == ["F1", "F2"]


def test_prose_after_the_array_is_ignored(tmp_path):
    r = _report(tmp_path, '## FINDINGS (machine-readable)\n[{"summary":"x"}]\n\n'
                          'Some closing thoughts with a stray ] bracket.')
    assert len(cv.parse_findings(r)) == 1


@pytest.mark.parametrize("body", [
    "no marker at all",
    "## FINDINGS (machine-readable)\nbut no array",
    "## FINDINGS (machine-readable)\n[not, valid, json",
    "## FINDINGS (machine-readable)\n[{}]",
])
def test_malformed_findings_yield_nothing_rather_than_raising(tmp_path, body):
    assert cv.parse_findings(_report(tmp_path, body)) == []


def test_a_missing_report_yields_no_findings(tmp_path):
    assert cv.parse_findings(tmp_path / "absent.md") == []


# --- landing in the ledger ----------------------------------------------------

def test_a_run_appends_a_ledger_row_the_gate_can_read(tmp_path, monkeypatch):
    """THE CONSUMER. Nick, 2026-09-03: 'make sure the output actually feeds into
    something else.' A report with no ledger row is the career-scan drain again."""
    report = tmp_path / "out" / "r.md"

    def fake_codex(cmd, **kw):
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text('## FINDINGS (machine-readable)\n'
                          '[{"id":"F1","severity":"P0","summary":"real defect"}]',
                          encoding="utf-8")
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "closing message", "")

    monkeypatch.setattr(cv.subprocess, "run", fake_codex)
    out = cv.run(tmp_path, "the drain", ["tools/scanner.py"], "", report, None, False)
    assert out["status"] == "ok" and out["open_findings"] == 1

    rows = gate.read_ledger(tmp_path)
    assert len(rows) == 1
    assert rows[0]["paths"] == ["tools/scanner.py"] and rows[0]["waived"] is False
    # ...and it now clears the gate for exactly that path.
    assert gate.check(tmp_path, [("tools/scanner.py", 200, 40)], since=0).blocked is False


def test_a_run_that_writes_no_report_is_not_recorded_as_success(tmp_path, monkeypatch):
    """Codex answering in chat instead of writing the file is the -o trap recurring.
    It must not read as a completed verification."""
    import subprocess
    monkeypatch.setattr(cv.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    out = cv.run(tmp_path, "t", ["tools/x.py"], "", tmp_path / "never.md", None, False)
    assert out["status"] == "no_report_written"


def test_print_only_calls_nothing(tmp_path, monkeypatch):
    def explode(*a, **k):
        raise AssertionError("--print-only must not invoke codex")
    monkeypatch.setattr(cv.subprocess, "run", explode)
    # gather_env_facts legitimately shells out; stub it so the guard is unambiguous.
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "FACTS")
    out = cv.run(tmp_path, "t", [], "", tmp_path / "r.md", None, True)
    assert out["status"] == "printed"
    assert not (tmp_path / "r.md").exists()


# ---------------------------------------------------------------------------
# HOW MUCH TO FEED IT. Nick, 2026-09-03: "I want to make sure that codex is fed the
# right amount of information to validate."
#
# The evidence across four real runs says the payload that earns its keep is: the
# NUMBERED CLAIMS, the DIFF of what changed, the prior review, and -- the single
# highest-value ingredient, from the 2026-09-03 run -- MY OWN KNOWN ERRORS, which let
# it say which of my conclusions rested on contaminated evidence. It corrected four of
# seven claims that way. "What do you think of this framing" returns agreement.
#
# And two modes with OPPOSITE information rules, enforced here rather than remembered:
#   verify  -- check THIS work. Anchoring is required; independence comes from being a
#              different model, not from ignorance.
#   diverge -- an independent take on the same goal. Per the anti-anchoring rule, an
#              agent that has seen the artifact drifts toward it even when told not to,
#              and the contamination is SILENT. So the wrapper refuses to attach it.
# ---------------------------------------------------------------------------

def test_verify_mode_attaches_the_diff(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "FACTS")
    monkeypatch.setattr(cv, "gather_diff", lambda root, paths, cap: "--- a/x.py\n+added")
    p = cv.build_prompt(tmp_path, "t", "", tmp_path / "r.md", None,
                        mode="verify", paths=["x.py"], claims=[], known_errors="")
    assert "+added" in p


def test_verify_mode_numbers_the_claims(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "FACTS")
    monkeypatch.setattr(cv, "gather_diff", lambda root, paths, cap: "")
    p = cv.build_prompt(tmp_path, "t", "", tmp_path / "r.md", None, mode="verify",
                        paths=[], claims=["the queue is a pending log",
                                          "no role can be lost"], known_errors="")
    assert "1. the queue is a pending log" in p
    assert "2. no role can be lost" in p
    assert "WRONG" in p


def test_verify_mode_carries_my_own_known_errors(tmp_path, monkeypatch):
    """The highest-value ingredient measured so far. Telling it what I already got
    wrong let it identify which surviving conclusions rested on bad evidence."""
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "FACTS")
    monkeypatch.setattr(cv, "gather_diff", lambda root, paths, cap: "")
    p = cv.build_prompt(tmp_path, "t", "", tmp_path / "r.md", None, mode="verify",
                        paths=[], claims=[], known_errors="I ran with a stripped env")
    assert "stripped env" in p
    assert "calibrate" in p.lower()


def test_diverge_mode_REFUSES_the_diff_and_the_claims(tmp_path, monkeypatch):
    """Structural anti-anchoring. The contamination is silent -- output LOOKS
    independent while drifting toward the original -- so this cannot be a reminder."""
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "FACTS")
    monkeypatch.setattr(cv, "gather_diff",
                        lambda root, paths, cap: "SHOULD NOT APPEAR")
    p = cv.build_prompt(tmp_path, "reach the goal", "", tmp_path / "r.md", None,
                        mode="diverge", paths=["x.py"],
                        claims=["my existing conclusion"], known_errors="")
    assert "SHOULD NOT APPEAR" not in p
    assert "my existing conclusion" not in p
    assert "independent" in p.lower()


def test_diverge_mode_still_gets_the_goal_and_the_sandbox_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "FACTS")
    p = cv.build_prompt(tmp_path, "build a role drain", "", tmp_path / "r.md", None,
                        mode="diverge", paths=[], claims=[], known_errors="")
    assert "build a role drain" in p
    assert "make NO claims about live environment state" in p


def test_an_oversized_diff_is_truncated_LOUDLY(tmp_path):
    """A silently truncated diff is a review of something other than the work. Say so
    in the prompt, or the model reports on half a change as though it were whole."""
    big = "\n".join(f"+line {i}" for i in range(5000))
    out = cv.truncate_diff(big, cap=100)
    assert out.count("\n") < 200
    assert "TRUNCATED" in out
    assert "5000" in out or "4900" in out


def test_a_diff_within_the_cap_is_untouched(tmp_path):
    small = "\n".join(f"+line {i}" for i in range(10))
    assert cv.truncate_diff(small, cap=100) == small


def _tiny_repo(tmp_path):
    """A real git repo, because `git diff` is the thing under test and stubbing it
    would leave the only code path that matters unexercised."""
    import subprocess
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    def git(*a):
        return subprocess.run(["git"] + list(a), cwd=str(tmp_path), env=env,
                              capture_output=True, text=True)
    git("init", "-q")
    (tmp_path / "a.py").write_text("original\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("other\n", encoding="utf-8")
    git("add", "-A"); git("commit", "-qm", "base")
    (tmp_path / "a.py").write_text("CHANGED_A\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("CHANGED_B\n", encoding="utf-8")
    return tmp_path


def test_gather_diff_returns_the_real_diff(tmp_path):
    got = cv.gather_diff(_tiny_repo(tmp_path), ["a.py"], cap=400)
    assert "CHANGED_A" in got, "the diff never reached the prompt"


def test_gather_diff_is_scoped_to_the_named_paths(tmp_path):
    """Feeding the whole repo's diff buries the signal it is supposed to surface."""
    got = cv.gather_diff(_tiny_repo(tmp_path), ["a.py"], cap=400)
    assert "CHANGED_B" not in got, "an unnamed path leaked into the payload"


def test_an_unknown_mode_is_refused(tmp_path):
    with pytest.raises(ValueError):
        cv.build_prompt(tmp_path, "t", "", tmp_path / "r.md", None,
                        mode="whatever", paths=[], claims=[], known_errors="")


# --- the CLI ------------------------------------------------------------------

def _cli(args, tmp_path):
    import subprocess
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "codex_verify.py"),
         "--repo-root", str(tmp_path)] + args,
        capture_output=True, text=True,
        env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"})


def test_cli_print_only_emits_the_prompt_and_spends_nothing(tmp_path):
    r = _cli(["--target", "a thing", "--print-only",
              "--claim", "claim one"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "1. claim one" in r.stdout
    assert "Assume it is wrong" in r.stdout


def test_cli_diverge_mode_withholds_the_claims(tmp_path):
    r = _cli(["--target", "a goal", "--print-only", "--mode", "diverge",
              "--claim", "SHOULD NOT APPEAR"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "SHOULD NOT APPEAR" not in r.stdout
    assert "independent" in r.stdout.lower()


def test_cli_requires_a_target(tmp_path):
    assert _cli(["--print-only"], tmp_path).returncode != 0


def test_cli_names_the_report_after_the_target_and_the_date(tmp_path):
    from datetime import datetime
    r = _cli(["--target", "The Drain Fixes", "--print-only"], tmp_path)
    assert f"{datetime.now().strftime('%m%d%y')}-codex-the-drain-fixes.md" in r.stdout


def test_a_reported_run_that_wrote_nothing_exits_nonzero(tmp_path, monkeypatch):
    """Exit status is what a caller or a skill branches on. 'ok' for a run that
    produced no artifact is the -o trap reported as success."""
    import subprocess
    monkeypatch.setattr(cv.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "FACTS")
    rc = cv.main(["--target", "t", "--repo-root", str(tmp_path),
                  "--report", str(tmp_path / "never.md")])
    assert rc == 1


# --- prompt hygiene (surviving-mutant coverage) -------------------------------

def _p(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "FACTS")
    monkeypatch.setattr(cv, "gather_diff", lambda root, paths, cap: kw.pop("diff", ""))
    base = dict(mode="verify", paths=[], claims=[], known_errors="")
    base.update(kw)
    return cv.build_prompt(tmp_path, "t", kw.pop("question", "") if False else
                           base.pop("question", ""), tmp_path / "r.md", None, **base)


def test_no_empty_claims_section_when_there_are_no_claims(tmp_path, monkeypatch):
    """An empty labelled section is noise that trains the reader to skim the prompt."""
    assert "CLAIMS TO CHECK" not in _p(tmp_path, monkeypatch)


def test_no_empty_known_errors_section(tmp_path, monkeypatch):
    assert "MISTAKES I HAVE ALREADY MADE" not in _p(tmp_path, monkeypatch)


def test_no_empty_diff_block(tmp_path, monkeypatch):
    assert "```diff" not in _p(tmp_path, monkeypatch)


def test_a_question_is_included_when_given(tmp_path, monkeypatch):
    p = _p(tmp_path, monkeypatch, question="does the ack race a scan?")
    assert "does the ack race a scan?" in p


def test_no_empty_question_section(tmp_path, monkeypatch):
    assert "SPECIFIC QUESTIONS" not in _p(tmp_path, monkeypatch)


def test_gather_diff_returns_empty_for_no_paths(tmp_path):
    assert cv.gather_diff(tmp_path, [], cap=10) == ""


def test_gather_diff_survives_a_git_failure(tmp_path):
    """tmp_path is not a git repo; the wrapper must degrade, not abort the run."""
    assert cv.gather_diff(tmp_path, ["x.py"], cap=10) == ""


def test_the_report_directory_is_created(tmp_path, monkeypatch):
    """Codex is told to write to this path; if its parent does not exist the run
    silently produces nothing and the ledger records a verification that never was."""
    import subprocess
    deep = tmp_path / "a" / "b" / "r.md"

    def fake(cmd, **kw):
        deep.write_text("## FINDINGS (machine-readable)\n[]", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cv.subprocess, "run", fake)
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "FACTS")
    out = cv.run(tmp_path, "t", [], "", deep, None, False)
    assert out["status"] == "ok"


def test_an_explicit_report_path_is_honoured(tmp_path, monkeypatch, capsys):
    """rc == 0 alone would pass even if --report were ignored and the dated default
    used instead -- so assert the path Codex is actually TOLD to write to."""
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "FACTS")
    chosen = tmp_path / "chosen.md"
    rc = cv.main(["--target", "t", "--repo-root", str(tmp_path), "--print-only",
                  "--report", str(chosen)])
    out = capsys.readouterr().out
    assert rc == 0
    assert str(chosen) in out
    assert "-codex-t.md" not in out, "the dated default overrode --report"


def test_the_cli_emits_json_on_a_real_run(tmp_path, monkeypatch, capsys):
    import subprocess
    monkeypatch.setattr(cv, "gather_env_facts", lambda root: "FACTS")
    monkeypatch.setattr(cv.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    cv.main(["--target", "t", "--repo-root", str(tmp_path),
             "--report", str(tmp_path / "r.md")])
    assert json.loads(capsys.readouterr().out)["status"] == "no_report_written"
