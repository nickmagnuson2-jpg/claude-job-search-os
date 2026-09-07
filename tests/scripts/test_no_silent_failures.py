#!/usr/bin/env python3
"""Guard against SILENT failures in the automation surface.

The failure class this repo actually suffers from is not "a thing crashes" -- it is
"a thing quietly stops running and nothing says so." Documented instances:

  * 2026-06-15  all 8 launchd jobs died for ~2 weeks (stale xattr on log files after
                a macOS TCC update). The in-job alert could not fire because the job
                never ran.
  * 2026-06-08  a parser read the wrong column index for an unknown duration; unit
                tests stayed GREEN because fixtures encoded the same stale header
                the buggy parser assumed.
  * (undated)   check_screenshot_path.py was built, tested, and never registered in
                settings.json -- it protected nothing until someone noticed.
  * 2026-08-10  every hook command in settings.json was rewritten from an absolute
                path to $CLAUDE_PROJECT_DIR. If that variable does not expand, all
                25 hooks break at once.

Each test below asserts a wiring invariant that, if violated, would otherwise fail
silently. These are cheap structural checks -- they do not execute hook logic.
"""
import json
import os
import plistlib
import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SETTINGS = REPO / ".claude" / "settings.json"

# check_*.py files that are deliberately NOT PreToolUse/PostToolUse hooks.
# Anything added here needs a reason -- the default expectation is that a
# tools/check_*.py is wired into settings.json.
NON_HOOK_CHECKERS = {
    # check_dark_inputs.py is a DIAGNOSTIC, not a PreToolUse hook. It has no single
    # tool call to intercept: it measures whether extracted values can affect the
    # outcomes they feed (discrimination), which requires running the real scorer
    # against live data. Wiring it to Write|Edit would fire it on every keystroke and
    # tell nobody anything.
    #
    # It is NOT unwatched, which is the thing this test actually cares about:
    # test_dark_input_debt_does_not_grow (below) runs it every suite pass and fails if
    # the known-dark set grows. That is the same frozen-allowlist pattern the mutation
    # gates use. Exempting it here without that test would make it exactly the dead
    # checker this file exists to prevent - and would be a fine irony, given the
    # detector was built to catch things that look built and do nothing.
    "check_dark_inputs.py",
    # ALL FIVE OF THE 2026-08-25 "BUILT, NOT YET WIRED" ENTRIES ARE NOW WIRED and have
    # been removed from this set, which is why nothing is listed here any more. Keeping a
    # wired tool in NON_HOOK_CHECKERS is invisible to the test below -- it only flags
    # UNWIRED tools missing from the set -- so the entries would have rotted silently.
    #
    # The precondition they carried was real and is worth restating, because it is the
    # thing that nearly went wrong: "a green test is not evidence, mutation survival is.
    # Wire each one only after its survivors are killed or allowlisted with written
    # reasons." On 2026-09-06 check_zuora_principal_title and check_workflow_scriptpath
    # were wired on a restraint measurement alone (12,990 replayed historical tool calls;
    # 1 block and 21-of-37 respectively, all true positives) BEFORE anyone read this
    # comment. The mutation half was then run and came back clean --
    # check_workflow_scriptpath killed 47 / survived 0, check_zuora_principal_title killed
    # 54 / survived 1, that one survivor an equivalent mutant now allowlisted with a
    # differential-test proof in tools/mutation-allow.json. So the outcome was fine and
    # the sequence was not.
    #
    # The 2026-08-25 counts above (56 survivors across the five) are ARTIFACT NUMBERS.
    # They predate 8efb8d3, which fixed a corruption ratchet that scored mutants KILLED
    # against the very file being mutated. check_workflow_scriptpath went from a recorded
    # 8 survivors to a measured 0 on the same code. Do not cite the old figures.
    #
    # The other three were remeasured on 2026-09-06 too, so all five now have a
    # post-8efb8d3 number. Two are clean and one cannot be measured:
    #   check_pipeline_exit_status        killed 44, survived 0, allowlisted 0 (was "8")
    #   check_scanner_examined_something  killed 61, survived 0, allowlisted 4 (was "18,
    #     9 in _verdict"). Exactly ONE of the four now sits in _verdict, and it is the
    #     redundant `if not real_paths: return None` fast-path -- the loop it guards
    #     already skips every match when real_paths is empty. All four were proven
    #     equivalent by differential test over 11 commands including a path appearing only
    #     inside a quoted span and only inside a heredoc body; reasons are in
    #     tools/mutation-allow.json. The original worry -- a blocking hook decorative
    #     inside its own verdict logic -- does not hold on the current code.
    #   check_banned_phrase               NOT MEASURABLE, and not its own fault. Its own
    #     suite is green (71 passed). mutation_check reports baseline_red because
    #     map_tests over-selects: it maps tests/scripts/test_mutation_report.py on the
    #     strength of ONE line, a fixture at L642 that uses "tools/check_banned_phrase.py"
    #     as sample data. That file has one failing test,
    #     test_the_real_tool_runs_against_the_live_sweep_state, which asserts the live
    #     sweep state is self-consistent and is red during the current baseline churn
    #     (it wanted "175 of" and got "111 of 123"). So an unrelated live-data test blocks
    #     the measurement of a hook it merely mentions. This is the map_tests
    #     over-selection defect MEMORY.md already records. Re-run once the sweep settles.
    # RETIRED 2026-08-25, unwired from settings.json. Its trigger could not be made to
    # work: the original estimator summed every prose duration ("saves ~20 hrs/week",
    # "first 30 days", "sanction 5 days ago") and fired on 18 of 37 in-scope plan docs
    # with absurd totals (1,340h for an onsite prep plan). Corrected to count only
    # annotation shapes -- (4h), table cells, Effort: labels -- it then fired on 0 of 37,
    # because only 9 of those docs carry ANY effort annotation and the largest totals 5.25h.
    # A 49% false-positive rate and a never-fires rate are both useless. The rule it served
    # (feedback_partner_pressuretest_above_10hr) is unaffected and stays at memory tier.
    # Kept, not deleted: tests/scripts/test_check_plan_partner_critique.py pins the
    # corrected estimator, so a future attempt starts from a working one.
    "check_plan_partner_critique.py",
    # Audits the hook stack itself, so it cannot BE a hook: it runs over
    # .claude/settings.json as a whole and asserts a property of every wired tool, which
    # is a suite-time invariant rather than a per-edit decision. Enforced by
    # tests/scripts/test_check_hook_warn_tier.py::test_the_live_hook_stack_is_clean_or_declared,
    # which fails the suite when a new hook is wired warn-only without declaring itself.
    "check_hook_warn_tier.py",
    # Invoked by /standup and by the automation-health launchd job, not as a hook.
    "check_automation_health.py",
    # Run on demand against a specific prep doc. Deliberately NOT a hook: the
    # suppressive-phrasing boundary (check 4) is contextual -- "already sent" is only a
    # defect when it is unbacked by a delivery stamp -- so a regex hook over it would
    # over-fire on every legitimate use, per the warn-vs-block design rule.
    "check_prep_doc.py",
    # Run on demand (and by /audit-pii-style pre-commit flows) over the three
    # framework/ rule docs, all of which are gitignored. A hook cannot fire usefully on
    # files that do not exist in a clean clone, and the check is whole-corpus rather
    # than per-edit.
    "check_doc_precedence.py",
    # The Section F frame-integrity gate. Deliberately NOT a hook: it runs against a
    # whole frame.yaml at defined points in the analysis workflow, and a frame is
    # INCOMPLETE by design for most of its life -- D1 is authored before D2, elements
    # before closure. A per-edit hook would block every intermediate save and would
    # make authoring impossible. It is invoked as a gate before an artifact ships,
    # the same shape as check_prep_doc.py.
    "check_frame_integrity.py",
    # Run on demand against a finished voice-sim probe prompt, before a rep. Not a hook:
    # a probe prompt is INCOMPLETE for most of its authoring life (the probe block is
    # written before the follow-ups, the handshake before either), so a per-edit gate
    # would block every intermediate save. Same gate-before-it-ships shape as
    # check_prep_doc.py and check_frame_integrity.py. Property 8 (position) is not
    # file-checkable at all, so no hook could close this rule anyway.
    "check_probe_prompt.py",
}


def _settings() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def _hook_commands() -> list[str]:
    out = []
    for groups in _settings().get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                cmd = hook.get("command")
                if cmd:
                    out.append(cmd)
    return out


def _scripts_in(cmd: str) -> list[Path]:
    """Resolve every repo script path referenced by a hook command string."""
    paths = []
    for raw in re.findall(r"\$CLAUDE_PROJECT_DIR/(\S+\.py)", cmd):
        paths.append(REPO / raw)
    return paths


# --------------------------------------------------------------------------
# settings.json wiring
# --------------------------------------------------------------------------

def test_settings_json_is_valid():
    """A malformed settings.json disables EVERY hook at once, silently."""
    _settings()  # raises on invalid JSON


def test_every_hook_command_points_at_an_existing_script():
    """A renamed/moved/deleted hook script makes the hook a no-op."""
    missing = []
    for cmd in _hook_commands():
        for p in _scripts_in(cmd):
            if not p.is_file():
                missing.append((cmd, str(p.relative_to(REPO))))
    assert not missing, "hook commands reference non-existent scripts:\n" + "\n".join(
        f"  {path}  <- {cmd}" for cmd, path in missing
    )


def test_every_hook_script_compiles():
    """A syntax error in a hook makes it fail on every invocation."""
    broken = []
    with tempfile.TemporaryDirectory() as td:
        for cmd in _hook_commands():
            for p in _scripts_in(cmd):
                if not p.is_file():
                    continue
                cfile = Path(td) / (p.stem + ".pyc")
                try:
                    py_compile.compile(str(p), doraise=True, cfile=str(cfile))
                except py_compile.PyCompileError as exc:
                    broken.append(f"  {p.relative_to(REPO)}: {exc}")
    assert not broken, "hook scripts fail to compile:\n" + "\n".join(broken)


def test_no_hook_command_hardcodes_an_absolute_home_path():
    """Absolute /Users/<name>/ paths break on any other machine or fork, silently.

    Locked in 2026-08-10 when 25 hardcoded paths were replaced by $CLAUDE_PROJECT_DIR.
    """
    offenders = [c for c in _hook_commands() if "/Users/" in c]
    assert not offenders, "hook commands hardcode an absolute home path:\n" + "\n".join(
        f"  {c}" for c in offenders
    )


def test_project_dir_variable_actually_resolves():
    """$CLAUDE_PROJECT_DIR must expand to this repo when a hook runs.

    Guards the 2026-08-10 migration: if the variable is unset at hook runtime the
    command becomes `python3 /tools/foo.py` and every hook dies at once.
    """
    cmds = [c for c in _hook_commands() if "$CLAUDE_PROJECT_DIR" in c]
    assert cmds, "expected hook commands to use $CLAUDE_PROJECT_DIR"
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(REPO)}
    expanded = subprocess.run(
        ["bash", "-c", 'echo "$CLAUDE_PROJECT_DIR/tools"'],
        capture_output=True, text=True, env=env,
    ).stdout.strip()
    assert expanded == str(REPO / "tools"), f"variable did not expand: {expanded!r}"


def test_a_representative_hook_runs_end_to_end():
    """Execute one real hook the way the harness would, and require a clean exit.

    Catches the case where the command string is well-formed and the file exists,
    but the hook cannot actually run (bad shebang, import error, missing dep).
    """
    target = REPO / "tools" / "check_public_pii.py"
    if not target.is_file():
        pytest.skip("check_public_pii.py not present")
    payload = json.dumps({
        "tool_name": "Write",
        "tool_input": {"file_path": "docs/faq.md", "content": "benign placeholder text"},
    })
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(REPO), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        ["bash", "-c", f'PYTHONIOENCODING=utf-8 python3 "$CLAUDE_PROJECT_DIR/tools/check_public_pii.py"'],
        input=payload, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, (
        f"representative hook did not exit clean (rc={proc.returncode}):\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


# --------------------------------------------------------------------------
# built-but-never-wired
# --------------------------------------------------------------------------

def test_every_checker_is_registered_or_explicitly_exempt():
    """A tools/check_*.py that nothing invokes protects nothing.

    Origin: check_screenshot_path.py shipped built + tested + unregistered.
    """
    registered = SETTINGS.read_text(encoding="utf-8")
    orphans = [
        p.name for p in sorted((REPO / "tools").glob("check_*.py"))
        if p.name not in NON_HOOK_CHECKERS and p.name not in registered
    ]
    assert not orphans, (
        "check_*.py files exist but are not registered in settings.json "
        "(wire them, or add to NON_HOOK_CHECKERS with a reason):\n"
        + "\n".join(f"  {n}" for n in orphans)
    )


# --------------------------------------------------------------------------
# launchd scheduled jobs
# --------------------------------------------------------------------------

def _launchd_script_refs(plist_path):
    """Every .py path referenced by a plist's ProgramArguments.

    Scans INSIDE each argument string rather than matching whole arguments. Every plist
    here invokes `/bin/bash -lc "<compound command>"`, so the script name is a token in
    the middle of one long string, not an argument of its own.

    THE OLD PARSER WAS `arg.endswith(".py")`, which inspected 5 of the 9 real references
    and checked ZERO of them correctly: an argument only ends in `.py` when the script
    takes no flags, so agent-discover-collect, alirohde-triage, automation-health and
    detector-scan (all of which pass arguments) were skipped entirely, while the four
    that matched were matching a whole shell command against `Path.is_file()`. The test
    that exists to catch a moved script would not have caught a moved script. Found
    2026-08-28 when a new plist ending in `tools/mutation_sweep.py` tripped it by
    accident: the first true assertion the check had ever made was a false positive.
    """
    refs = []
    for arg in plistlib.loads(plist_path.read_bytes()).get("ProgramArguments", []):
        if isinstance(arg, str):
            refs.extend(re.findall(r"[\w./@-]+\.py", arg))
    return refs


def test_every_launchd_plist_points_at_an_existing_script():
    """A scheduled job whose script moved fails on a timer, into a log nobody reads."""
    missing = []
    for plist in sorted((REPO / "tools" / "launchd").glob("*.plist")):
        for ref in _launchd_script_refs(plist):
            candidate = Path(ref)
            if not candidate.is_absolute():
                candidate = REPO / ref
            if not candidate.is_file():
                missing.append(f"  {plist.name}: {ref}")
    assert not missing, "launchd jobs reference non-existent scripts:\n" + "\n".join(missing)


def test_launchd_script_ref_parser_sees_every_plist():
    """The parser above is the whole value of the check; a silent miss makes it vacuous.

    Guards the exact regression just fixed: every plist must yield at least one script
    reference. If a future plist shape parses to zero refs, the job it schedules is
    unchecked and this fails loudly instead of passing green.
    """
    unparsed = [p.name for p in sorted((REPO / "tools" / "launchd").glob("*.plist"))
                if not _launchd_script_refs(p)]
    # career-scan invokes a skill, not a script, so it legitimately references no .py.
    unparsed = [n for n in unparsed if "career-scan" not in n]
    assert not unparsed, ("launchd plists yielding no script reference (their job is "
                          "unchecked by the test above):\n  " + "\n  ".join(unparsed))


def test_every_launchd_plist_has_a_schedule():
    """A plist with neither StartInterval nor StartCalendarInterval never fires."""
    unscheduled = []
    for plist in sorted((REPO / "tools" / "launchd").glob("*.plist")):
        data = plistlib.loads(plist.read_bytes())
        if "StartInterval" not in data and "StartCalendarInterval" not in data:
            unscheduled.append(f"  {plist.name}")
    assert not unscheduled, "launchd plists have no schedule:\n" + "\n".join(unscheduled)


# An unattended job runs with nobody reading the output, so a write flag there is a
# mutation nobody reviews. Each entry needs a written reason, same discipline as
# tools/mutation-allow.json and tools/hook-unwired-allow.json: a list without
# justification is how a rule decays into "we thought about it once".
UNATTENDED_WRITE_FLAGS = {
    ("detector_run.py", "--apply"): (
        "Writes `occurrences` on the memory corpus from detector regexes whose PRECISION "
        "is unmeasured. Measured 2026-08-27 over 86 transcripts: sampled detectors matched "
        "any timezone-stamped time, and any number-plus-percent near a noun. Applying those "
        "nightly would inflate the counter that gates promotion work, and the backlog would "
        "fill with noise that looks like evidence. Safe only while the job stays --json. "
        "Do not add --apply until an adjudication step exists."),
}


def test_no_launchd_job_passes_a_corpus_WRITE_flag():
    """The detector-scan job is dry-run by flag, not by design. This makes it by design.

    Nothing structural stops someone adding `--apply` to a plist; it is one word, in a file
    nobody re-reads, feeding a job that runs at 03:20 with no observer. That is the exact
    shape of `feedback_a_stated_intention_is_not_an_action`: the safety lives in a habit
    rather than in a check.
    """
    violations = []
    for plist in sorted((REPO / "tools" / "launchd").glob("*.plist")):
        args = " ".join(a for a in plistlib.loads(plist.read_bytes()).get("ProgramArguments", [])
                        if isinstance(a, str))
        for (script, flag), reason in UNATTENDED_WRITE_FLAGS.items():
            if script in args and re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", args):
                violations.append(f"  {plist.name}: runs {script} with {flag}. {reason}")
    assert not violations, ("unattended launchd jobs carry a corpus-write flag:\n"
                            + "\n".join(violations))


def test_the_write_flag_guard_can_actually_fail(tmp_path):
    """A guard whose forbidden pattern never matches anything is decorative.

    Proves the matcher fires on the exact string it is meant to catch, so a green result
    above means "no plist has it" rather than "the check cannot see it".
    """
    script, flag = next(iter(UNATTENDED_WRITE_FLAGS))
    args = f'cd "$HOME/x" && python3 tools/{script} --memory-dir /m {flag} --json'
    assert re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", args) and script in args
    clean = f'cd "$HOME/x" && python3 tools/{script} --memory-dir /m --json'
    assert not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", clean)


def test_every_unattended_write_flag_entry_carries_a_reason():
    empty = [f"{s} {f}" for (s, f), r in UNATTENDED_WRITE_FLAGS.items() if not str(r).strip()]
    assert not empty, f"entries with no written reason: {empty}"


def exempt_active_mutation_target(changed: list[str]) -> list[str]:
    """Drop the ONE file a mutation run is deliberately rewriting, and nothing else.

    tools/mutation_check.py sets MUTATION_CHECK_TARGET to the repo-relative path of the
    file it is currently mutating, in the env of the pytest subprocesses it spawns.

    WHY (2026-09-06). An ast.unparse mutant IS the corruption signature the ratchet below
    detects, so without this the ratchet failed on the target of EVERY mutant and each
    mutant was scored KILLED regardless of what it changed. Measured that day across 99
    tools in baseline.jsonl: the 5 tools whose mapped tests include this file reported
    exactly 0 survivors (0 of 180 mutants), against 34.1% survival for the other 94. A
    false-perfect score is worse than a red one -- it reads as evidence of protection and
    flows into the corpus statistics unchallenged.

    NARROW ON PURPOSE. Suppressing on MUTATION_CHECK_ACTIVE instead would disable the
    whole ratchet for the duration of a run, and BYSTANDER corruption -- a file mutated
    without a backup, which recover_if_stranded cannot restore -- happens precisely then.
    That is the failure this test was built for: vault_paths.py on 2026-08-19 and again
    2026-08-28, todo_write.py on 2026-09-05.
    """
    active = os.environ.get("MUTATION_CHECK_TARGET")
    if not active:
        return list(changed)
    return [f for f in changed if f != active]


def test_the_target_exemption_removes_the_target(monkeypatch):
    monkeypatch.setenv("MUTATION_CHECK_TARGET", "tools/alpha.py")
    assert exempt_active_mutation_target(["tools/alpha.py"]) == []


def test_the_target_exemption_keeps_bystanders(monkeypatch):
    """The whole point. A second corrupted file during a run must still be caught."""
    monkeypatch.setenv("MUTATION_CHECK_TARGET", "tools/alpha.py")
    kept = exempt_active_mutation_target(["tools/alpha.py", "tools/bystander.py"])
    assert kept == ["tools/bystander.py"]


def test_no_exemption_outside_a_mutation_run(monkeypatch):
    """With no mutation in flight nothing is exempt, or ordinary corruption goes unseen."""
    monkeypatch.delenv("MUTATION_CHECK_TARGET", raising=False)
    changed = ["tools/alpha.py", "tools/bystander.py"]
    assert exempt_active_mutation_target(changed) == changed


def test_mutation_check_actually_sets_the_variable():
    """Contract with the producer. If mutation_check stops setting it, the exemption is
    dead code and every mutation number silently reverts to a fake zero."""
    src = (REPO / "tools" / "mutation_check.py").read_text(encoding="utf-8")
    assert "MUTATION_CHECK_TARGET" in src, (
        "tools/mutation_check.py no longer sets MUTATION_CHECK_TARGET; the exemption in "
        "this file cannot fire and survived=0 results are meaningless again")


def test_no_tracked_tools_file_was_left_ast_unparsed():
    """A file rewritten by ast.unparse and never restored is silently corrupted source.

    THE INCIDENT (found 2026-08-28). `tools/vault_paths.py` sat in the working tree with its
    shebang and coding line stripped and every string re-quoted -- the signature of an
    ast.unparse round-trip. It imported fine and no test failed, so nothing surfaced it.
    `mutation_check.py` already recovers a STRANDED TARGET, but `_restore_in_flight` restores
    only the target and `recover_if_stranded` needs a `.mutation_backup` to exist; a BYSTANDER
    file mutated without a backup is recovered by nothing. Its own docstring records the same
    file being corrupted this way once before, on 2026-08-19.

    The detector is the shebang: 139 of 146 tools/*.py carry `#!/usr/bin/env python3` and an
    unparse drops it. Rather than require it everywhere (7 legitimately lack it), this is a
    RATCHET -- a file that has one in HEAD may not lose it in the working tree.
    """
    ls = subprocess.run(["git", "diff", "--name-only", "--", "tools"],
                        capture_output=True, text=True, cwd=str(REPO))
    # An empty before-set makes this vacuously true, so a failed git call must ABORT rather
    # than read as clean. Per feedback_guard_must_hard_abort_on_empty_input.
    assert ls.returncode == 0, f"git diff failed, so this check proved NOTHING: {ls.stderr}"

    changed = [f for f in ls.stdout.split() if f.endswith(".py")]

    # EXEMPT THE FILE A MUTATION RUN IS DELIBERATELY REWRITING (added 2026-09-06).
    # An ast.unparse mutant IS this corruption signature, so without the exemption this
    # check fails on the target of every single mutant and the mutant is scored KILLED
    # no matter what it changed. Measured that day: 5 of 5 tools mapping to this file
    # reported exactly 0 survivors (0 of 180 mutants) against 34.1% survival for tools
    # that do not map to it. A fake-perfect score is worse than a red one, because it
    # reads as evidence of protection.
    #
    # Exempt the ONE named path, never the whole check: bystander corruption -- a file
    # mutated with no backup, which recover_if_stranded cannot restore -- is the failure
    # this test was built for and it happens DURING a run. Blanket-suppressing on
    # MUTATION_CHECK_ACTIVE would blind it exactly then.
    changed = exempt_active_mutation_target(changed)

    corrupted = []
    for rel in changed:
        head = subprocess.run(["git", "show", f"HEAD:{rel}"],
                              capture_output=True, text=True, cwd=str(REPO))
        if head.returncode != 0:
            continue                                  # newly added file, no HEAD version
        head_first = head.stdout.splitlines()[:1]
        work_first = (REPO / rel).read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        if head_first and head_first[0].startswith("#!") and work_first != head_first:
            corrupted.append(f"  {rel}: HEAD starts {head_first[0]!r}, working tree starts "
                             f"{(work_first or [''])[0]!r}")
    assert not corrupted, (
        "tracked tools/*.py lost their shebang -- the ast.unparse signature of a mutation run "
        "that never restored the file:\n" + "\n".join(corrupted)
        + "\n\nRestore with: git checkout -- <path>")


def test_the_unparse_ratchet_can_actually_fail(tmp_path):
    """Positive control. The comparison must be able to FIRE, or a green run means nothing."""
    head_first = ["#!/usr/bin/env python3"]
    work_first = ['"""vault_paths.py - the one place the root is resolved."""']
    assert head_first[0].startswith("#!") and work_first != head_first
    assert not (head_first[0].startswith("#!") and head_first != head_first)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# Dark-input debt: frozen, and allowed to shrink only.
#
# tools/check_dark_inputs.py exits 2 while any probe is dark. Three are, as of
# 2026-09-02, and they are real unfixed defects rather than detector noise:
#   - _extract_target_industries returns prose sentences no JD can contain
#   - _score_industry_match returns an identical 3.0 for a target-title role and an
#     unrelated one, so 20% of the scoring weight cannot discriminate at all
#   - _score_keyword_overlap returns 0.0 for both, so another 20% is dead too
#
# Asserting exit 0 would mean deleting the tool or widening its thresholds until it
# lied. Asserting the SET does not grow records the debt honestly and blocks new debt.
# Drain this list; do not extend it.
# ---------------------------------------------------------------------------

KNOWN_DARK_PROBES = {
    "scorer._extract_target_industries",
    "scorer._score_industry_match",
    "scorer._score_keyword_overlap",
}


def test_dark_input_debt_does_not_grow():
    """A new dark probe is a new prose->machine boundary that went inert."""
    import json
    import subprocess
    import sys as _sys

    proc = subprocess.run(
        [_sys.executable, str(REPO / "tools" / "check_dark_inputs.py"),
         "--repo-root", str(REPO), "--json"],
        capture_output=True, text=True,
    )
    assert proc.returncode in (0, 2), (
        f"detector crashed (exit {proc.returncode}): {proc.stderr[-400:]}"
    )
    payload = json.loads(proc.stdout)
    dark = {r["name"] for r in payload["results"] if r["verdict"] != "OK"}

    new = dark - KNOWN_DARK_PROBES
    assert not new, (
        "new DARK probe(s) - an extraction went inert, or a consumer stopped being "
        f"able to use it: {sorted(new)}"
    )
    fixed = KNOWN_DARK_PROBES - dark
    assert not fixed, (
        f"these probes are clean now: {sorted(fixed)}. Remove them from "
        "KNOWN_DARK_PROBES so the debt list stays honest and cannot silently refill."
    )
