"""A hook that writes to stderr and exits 0 reaches nobody.

The load-carrying test here is test_a_helper_returning_2_counts_as_blocking: the first
version of this audit looked only at sys.exit() arguments and main()'s return values, and
flagged check_prep_doc_format.py -- a correctly-wired blocking hook that returns 2 from a
helper. A guard that cries wolf on a correct hook is the thing everyone routes around, so
the false-positive case is pinned as hard as the true-positive one.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_hook_warn_tier as hw  # noqa: E402


def settings(tmp_path: Path, *, event="PreToolUse", tools=("check_x.py",), matcher="Write"):
    cmds = [{"type": "command",
             "command": f"PYTHONIOENCODING=utf-8 python3 $CLAUDE_PROJECT_DIR/tools/{t}"}
            for t in tools]
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {event: [{"matcher": matcher, "hooks": cmds}]}}),
                 encoding="utf-8")
    return p


STDERR_ONLY = "import sys\ndef main():\n    print('hi', file=sys.stderr)\n    return 0\n"
STDERR_EXIT2 = "import sys\ndef main():\n    print('hi', file=sys.stderr)\n    sys.exit(2)\n"
STDERR_HELPER_RETURNS_2 = (
    "import sys\n"
    "def decide(x):\n"
    "    print('blocked', file=sys.stderr)\n"
    "    return 2\n"
    "def main():\n"
    "    return decide(1)\n"
)
SILENT_TOOL = "def main():\n    return 0\n"
STDERR_WRITE_METHOD = "import sys\ndef main():\n    sys.stderr.write('x')\n    return 0\n"


def tool(tmp_path: Path, name: str, src: str) -> Path:
    d = tmp_path / "tools"
    d.mkdir(exist_ok=True)
    (d / name).write_text(src, encoding="utf-8")
    return d


# ---------------------------------------------------------------- the core property

def test_a_wired_hook_that_writes_stderr_and_cannot_exit_2_is_a_violation(tmp_path):
    s = settings(tmp_path)
    tools = tool(tmp_path, "check_x.py", STDERR_ONLY)
    r = hw.audit(s, tools, {})
    assert not r["ok"]
    assert any("cannot" in v and "check_x.py" in v for v in r["violations"])


def test_a_hook_that_exits_2_is_clean(tmp_path):
    s = settings(tmp_path)
    tools = tool(tmp_path, "check_x.py", STDERR_EXIT2)
    assert hw.audit(s, tools, {})["ok"]


def test_a_helper_returning_2_counts_as_blocking(tmp_path):
    """The false positive that killed the first version of this check."""
    s = settings(tmp_path)
    tools = tool(tmp_path, "check_x.py", STDERR_HELPER_RETURNS_2)
    r = hw.audit(s, tools, {})
    assert r["ok"], (
        "a hook blocking via `return 2` from a helper is correctly wired; flagging it "
        "makes the guard cry wolf on real gates"
    )


def test_a_hook_that_never_writes_stderr_is_not_flagged(tmp_path):
    s = settings(tmp_path)
    tools = tool(tmp_path, "check_x.py", SILENT_TOOL)
    r = hw.audit(s, tools, {})
    assert r["ok"] and r["silent"] == []


def test_sys_stderr_write_is_detected_not_only_print(tmp_path):
    s = settings(tmp_path)
    tools = tool(tmp_path, "check_x.py", STDERR_WRITE_METHOD)
    assert not hw.audit(s, tools, {})["ok"]


# ---------------------------------------------------------------- allowlist discipline

def test_an_allowlisted_hook_passes_but_is_still_reported_as_silent(tmp_path):
    s = settings(tmp_path)
    tools = tool(tmp_path, "check_x.py", STDERR_ONLY)
    r = hw.audit(s, tools, {"check_x.py": "logger, its real output is a file"})
    assert r["ok"]
    assert r["silent"] == [{"tool": "check_x.py", "events": ["PreToolUse"], "allowlisted": True}], (
        "a declared warn-only hook must stay visible in the report, or the allowlist "
        "becomes a place things disappear into"
    )


def test_an_allowlist_entry_with_an_empty_reason_is_a_violation(tmp_path):
    s = settings(tmp_path)
    tools = tool(tmp_path, "check_x.py", STDERR_ONLY)
    r = hw.audit(s, tools, {"check_x.py": "   "})
    assert any("empty reason" in v for v in r["violations"])


def test_a_stale_allowlist_entry_for_an_unwired_hook_is_a_violation(tmp_path):
    s = settings(tmp_path)
    tools = tool(tmp_path, "check_x.py", STDERR_EXIT2)
    r = hw.audit(s, tools, {"check_gone.py": "was warn-only once"})
    assert any("not wired" in v and "check_gone.py" in v for v in r["violations"])


def test_a_wired_hook_missing_from_disk_is_a_violation(tmp_path):
    s = settings(tmp_path, tools=("check_absent.py",))
    (tmp_path / "tools").mkdir(exist_ok=True)
    r = hw.audit(s, tmp_path / "tools", {})
    assert any("not present" in v for v in r["violations"])


# ---------------------------------------------------------------- wiring discovery

def test_hooks_are_found_across_every_event_and_group():
    s = {"hooks": {
        "PreToolUse": [{"matcher": "Write", "hooks": [{"command": "python3 tools/a.py"}]},
                       {"matcher": "Bash", "hooks": [{"command": "python3 tools/b.py"}]}],
        "Stop": [{"hooks": [{"command": "python3 tools/a.py"}]}],
    }}
    w = hw.wired_hooks(s)
    assert w["a.py"] == {"PreToolUse", "Stop"}, "the same tool on two events must record both"
    assert w["b.py"] == {"Bash"} or w["b.py"] == {"PreToolUse"}


def test_wired_hooks_survives_a_settings_file_with_no_hooks():
    assert hw.wired_hooks({}) == {}


def test_an_empty_scan_is_an_error_not_a_clean_bill(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"hooks": {}}', encoding="utf-8")
    with pytest.raises(ValueError):
        hw.audit(p, tmp_path, {})


def test_load_allow_rejects_a_non_object(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("[1,2]", encoding="utf-8")
    with pytest.raises(ValueError):
        hw.load_allow(p)


def test_load_allow_on_a_missing_file_is_empty(tmp_path):
    assert hw.load_allow(tmp_path / "nope.json") == {}


# ---------------------------------------------------------------- CLI

def test_main_blocks_with_exit_2(tmp_path, capsys):
    s = settings(tmp_path)
    tool(tmp_path, "check_x.py", STDERR_ONLY)
    a = tmp_path / "a.json"; a.write_text("{}", encoding="utf-8")
    rc = hw.main(["--settings", str(s), "--tools-dir", str(tmp_path / "tools"), "--allow", str(a)])
    assert rc == 2
    assert "VIOLATION" in capsys.readouterr().out


def test_main_returns_0_when_clean(tmp_path):
    s = settings(tmp_path)
    tool(tmp_path, "check_x.py", STDERR_EXIT2)
    a = tmp_path / "a.json"; a.write_text("{}", encoding="utf-8")
    assert hw.main(["--settings", str(s), "--tools-dir", str(tmp_path / "tools"),
                    "--allow", str(a)]) == 0


def test_main_reports_a_missing_settings_file(tmp_path, capsys):
    rc = hw.main(["--settings", str(tmp_path / "nope.json")])
    assert rc == 1
    assert "settings file not found" in capsys.readouterr().err


def test_main_reports_malformed_json(tmp_path, capsys):
    s = tmp_path / "settings.json"
    s.write_text("{not json", encoding="utf-8")
    assert hw.main(["--settings", str(s), "--tools-dir", str(tmp_path)]) == 1
    assert capsys.readouterr().err.strip()


def test_summary_line_counts_declared_warn_only(tmp_path, capsys):
    s = settings(tmp_path)
    tool(tmp_path, "check_x.py", STDERR_ONLY)
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"check_x.py": "logger"}), encoding="utf-8")
    hw.main(["--settings", str(s), "--tools-dir", str(tmp_path / "tools"), "--allow", str(a)])
    out = capsys.readouterr().out
    assert "wired 1" in out and "stderr-only 1 (1 declared warn-only)" in out


def test_json_output_is_parseable(tmp_path, capsys):
    s = settings(tmp_path)
    tool(tmp_path, "check_x.py", STDERR_EXIT2)
    a = tmp_path / "a.json"; a.write_text("{}", encoding="utf-8")
    hw.main(["--settings", str(s), "--tools-dir", str(tmp_path / "tools"),
             "--allow", str(a), "--json"])
    assert json.loads(capsys.readouterr().out)["ok"] is True


# ---------------------------------------------------------------- the live repo

def test_the_live_hook_stack_is_clean_or_declared():
    """The real settings.json. Fails when a new hook is wired warn-only without saying so.

    Scoped to the WARN tier deliberately: the unwired-gate dimension is a separate, currently
    unresolved defect and is asserted by the ratchet below. Folding them together would let a
    fix to one mask a regression in the other.
    """
    r = hw.audit(hw.DEFAULT_SETTINGS, REPO_ROOT / "tools", hw.load_allow(hw.DEFAULT_ALLOW),
                 unwired_allow={g: "not under test here"
                                for g in [x["tool"] for x in hw.unwired_gates(
                                    REPO_ROOT / "tools",
                                    hw.wired_hooks(json.loads(
                                        hw.DEFAULT_SETTINGS.read_text(encoding="utf-8"))),
                                    {})]})
    assert r["violations"] == []
    assert r["checked"] >= 20, "the audit should be seeing the whole wired stack"


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN OPEN DEFECT, measured 2026-08-27: six tools/check_*.py declare themselves hooks "
    "on their docstring summary line, carry passing suites, and are wired in NO settings "
    "file -- check_banned_phrase, check_pipeline_exit_status, check_plan_partner_critique, "
    "check_scanner_examined_something, check_workflow_scriptpath, check_zuora_principal_title. "
    "strict=True makes this a RATCHET: once they are wired or declared CLI-only with a written "
    "reason, this XPASSes and the suite fails until the marker is deleted, so the defect cannot "
    "be silently re-normalised."))
def test_the_live_stack_has_no_undeclared_unwired_gates():
    r = hw.audit(hw.DEFAULT_SETTINGS, REPO_ROOT / "tools", hw.load_allow(hw.DEFAULT_ALLOW),
                 extra_settings=hw.DEFAULT_EXTRA_SETTINGS,
                 unwired_allow=hw.load_allow(hw.DEFAULT_UNWIRED_ALLOW))
    assert [v for v in r["violations"] if "wired in NO settings file" in v] == []


def test_every_live_allowlist_entry_carries_a_reason():
    allow = hw.load_allow(hw.DEFAULT_ALLOW)
    assert allow, "the allow file should declare the known warn-only hooks"
    assert [k for k, v in allow.items() if not str(v).strip()] == []


# ---------------------------------------------------------------- mutation-driven gaps
# Every case below was added because a mutant survived at that exact spot. The
# writes_stderr one was a genuine hole, not a test gap: keying on the callee name `print`
# meant a hook using any logging wrapper with file=sys.stderr read as writing nothing.

def test_a_non_print_callee_with_file_stderr_still_counts_as_writing(tmp_path):
    s = settings(tmp_path)
    tools = tool(tmp_path, "check_x.py",
                 "import sys\ndef log(m, file=None):\n    pass\n"
                 "def main():\n    log('x', file=sys.stderr)\n    return 0\n")
    assert not hw.audit(s, tools, {})["ok"], (
        "a logging wrapper taking file=sys.stderr writes to stderr just as print does"
    )


def test_a_call_with_an_unrelated_kwarg_is_not_a_stderr_write():
    import ast as _ast
    assert hw.writes_stderr(_ast.parse("open('f', mode='w')\n")) is False


def test_writes_stderr_returns_a_real_false_not_none():
    import ast as _ast
    assert hw.writes_stderr(_ast.parse("x = 1\n")) is False


def test_can_block_returns_a_real_false_not_none():
    import ast as _ast
    assert hw.can_block(_ast.parse("import sys\nsys.exit(0)\n")) is False


def test_sys_exit_0_does_not_count_as_blocking(tmp_path):
    """Kills the mutant treating any exit argument as a 2."""
    s = settings(tmp_path)
    tools = tool(tmp_path, "check_x.py",
                 "import sys\ndef main():\n    print('x', file=sys.stderr)\n    sys.exit(0)\n")
    r = hw.audit(s, tools, {})
    assert not r["ok"], "exit 0 is exactly the silent case this audit exists to catch"


def test_a_nonliteral_exit_code_does_not_count_as_blocking():
    import ast as _ast
    assert hw.can_block(_ast.parse("import sys\ncode = 2\nsys.exit(code)\n")) is False, (
        "a dynamic exit code cannot be proven to reach 2; the audit must not assume it does"
    )


def test_a_malformed_event_value_does_not_crash_discovery():
    """settings.json hand-edited to a string instead of a list of groups."""
    assert hw.wired_hooks({"hooks": {"PreToolUse": "python3 tools/a.py"}}) == {}



# ------------------------------------------------- unwired gates: a gate wired to nothing
#
# `wired_hooks` builds the audited population FROM settings.json, so a hook wired nowhere
# was never in the denominator. `wired 28 | checked 28` read clean while 6 tested gates
# guarded nothing.

DECLARED_HOOK = '"""check_thing.py -- PreToolUse hook for Bash."""\nimport sys\n'
NO_DECLARATION = '"""check_thing.py -- a command line report."""\nimport sys\n'


def test_a_declared_hook_wired_nowhere_is_a_violation(tmp_path):
    s = settings(tmp_path)
    tool(tmp_path, "check_x.py", STDERR_EXIT2)
    tool(tmp_path, "check_orphan.py", DECLARED_HOOK)
    r = hw.audit(s, tmp_path / "tools", {})
    assert any("check_orphan.py" in v and "wired in NO settings file" in v
               for v in r["violations"])


def test_a_tool_that_does_not_declare_a_hook_role_is_not_flagged(tmp_path):
    s = settings(tmp_path)
    tool(tmp_path, "check_x.py", STDERR_EXIT2)
    tool(tmp_path, "check_cli.py", NO_DECLARATION)
    assert hw.audit(s, tmp_path / "tools", {})["violations"] == []


def test_a_hook_wired_only_in_an_EXTRA_settings_file_is_not_called_unwired(tmp_path):
    """The correctness requirement. A single-file scan manufactures a false positive on a
    correctly-installed gate, and a guard that cries wolf is one everyone routes around."""
    s = settings(tmp_path)
    tool(tmp_path, "check_x.py", STDERR_EXIT2)
    tool(tmp_path, "check_orphan.py", DECLARED_HOOK)
    local = tmp_path / "local.json"
    local.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"command": "python3 tools/check_orphan.py"}]}]}}),
        encoding="utf-8")
    r = hw.audit(s, tmp_path / "tools", {}, extra_settings=[local])
    assert r["violations"] == []


def test_a_missing_extra_settings_file_is_skipped_not_fatal(tmp_path):
    s = settings(tmp_path)
    tool(tmp_path, "check_x.py", STDERR_EXIT2)
    r = hw.audit(s, tmp_path / "tools", {}, extra_settings=[tmp_path / "absent.json"])
    assert r["ok"] is True


def test_an_unwired_gate_declared_CLI_only_is_allowed(tmp_path):
    s = settings(tmp_path)
    tool(tmp_path, "check_x.py", STDERR_EXIT2)
    tool(tmp_path, "check_orphan.py", DECLARED_HOOK)
    r = hw.audit(s, tmp_path / "tools", {},
                 unwired_allow={"check_orphan.py": "run by hand from /audit-pii"})
    assert r["violations"] == []


def test_an_unwired_allowlist_entry_with_an_empty_reason_fails(tmp_path):
    s = settings(tmp_path)
    tool(tmp_path, "check_x.py", STDERR_EXIT2)
    tool(tmp_path, "check_orphan.py", DECLARED_HOOK)
    r = hw.audit(s, tmp_path / "tools", {}, unwired_allow={"check_orphan.py": "   "})
    assert any("empty reason" in v for v in r["violations"])


def test_declaration_is_read_from_the_summary_line_only():
    """The self-trigger case. This module's own docstring quotes another tool's Stop-hook
    role while discussing it; searching the whole docstring flagged this file as an unwired
    Stop hook. Real hooks declare on line 0."""
    assert hw.declared_hook_role('"""x.py -- PreToolUse hook for Bash."""') == "PreToolUse"
    assert hw.declared_hook_role(
        '"""x.py -- a report.\n\nBackground: check_other.py, a Stop hook, was silent."""') is None


def test_a_file_with_no_docstring_declares_nothing():
    assert hw.declared_hook_role("import sys\n") is None


def test_unparseable_source_declares_nothing_rather_than_crashing():
    assert hw.declared_hook_role("def (((") is None
