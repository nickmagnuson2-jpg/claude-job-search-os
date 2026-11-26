"""Every tool that imports a shared sibling module (md_table, hook_runtime) must also
import as `tools.<name>`, not only as a script.

Run as a script, Python puts tools/ first on sys.path, so `from md_table import ...`
resolves on its own. Imported as part of the `tools` package (a test doing
`from tools.granola_save import ...`, or a tool doing `from tools import vault_paths`)
it does not. On 2026-10-21 ten tools lost that ability in one refactor: the line that
puts tools/ on sys.path looked redundant, a mutation run confirmed no test noticed its
removal, and a cross-model review caught it. Each tool is imported in a fresh process,
so one tool's sys.path change cannot rescue another.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = re.compile(r"^from (?:tools\.)?(md_table|hook_runtime|slug) import ", re.M)

# Listed by name, not only discovered: mutation_check selects a test file for a tool
# when the file names that tool, so this list is what makes the sys.path line in each
# of these tools a mutant this test can kill. The scan below keeps the list honest.
TOOLS = (
    "act_apply",
    "act_classify",
    "agent_core",
    "attention",
    "backfill_transcript_failures",
    "build_check",
    "check_ats_raw_fetch",
    "check_banned_phrase",
    "check_bare_python",
    "check_build_log_present",
    "check_calendar_mcp",
    "check_changelog_currency",
    "check_data_projects",
    "check_derived_ratio",
    "check_draft_voice",
    "check_edit_safety",
    "check_eic_review",
    "check_email_via_skill",
    "check_frozen_file",
    "check_gmail_read_mcp",
    "check_guard_edit_approval",
    "check_handoff_producer",
    "check_heredoc_quoting",
    "check_lessons_write",
    "check_living_log_purity",
    "check_memory_frontmatter_write",
    "check_module_imports",
    "check_network_git",
    "check_new_toplevel_dir_protected",
    "check_no_confabulation",
    "check_pipe_close_via_update",
    "check_pipeline_exit_status",
    "check_plan_partner_critique",
    "check_poller_self_match",
    "check_prep_doc_format",
    "check_public_pii",
    "check_replace_all_safety",
    "check_scanner_examined_something",
    "check_screenshot_path",
    "check_script_error_logged",
    "check_sealed_read",
    "check_sendmessage_checkin",
    "check_stale_file_claim",
    "check_status_query_verification",
    "check_table_integrity",
    "check_todo_write_kwargs",
    "check_unquoted_glob",
    "check_voice_pure",
    "check_workflow_scriptpath",
    "check_workstream_heading_time",
    "check_zsh_word_split",
    "check_zuora_principal_title",
    "context_file_audit",
    "daily_stoic",
    "dossier_freshness",
    "draft_capture",
    "fetch_transcript",
    "fetch_x_source",
    "frame_write",
    "gen_pii_denylist",
    "gmail_fetch",
    "granola_auto_debrief",
    "granola_save",
    "inbox_extract",
    "lessons_write",
    "living_log_append",
    "memory_usage",
    "memory_write",
    "my_world_synthesis",
    "networking_followup",
    "networking_read",
    "networking_write",
    "note_divergence",
    "outreach_pending",
    "person_write",
    "personal_todo_write",
    "pipe_read",
    "pipe_write",
    "pipeline_staleness",
    "reader_review",
    "remember_apply",
    "remember_classify",
    "run_metrics",
    "scan_jobs",
    "scan_transcript_failures",
    "source_check",
    "standup_collect",
    "todo_daily_metrics",
    "todo_write",
    "voice_extract",
    "wispr_pull",
    "workstream_probe",
)


def test_every_shared_module_user_is_listed():
    found = sorted(p.stem for p in (REPO / "tools").glob("*.py")
                   if SHARED.search(p.read_text(encoding="utf-8")))
    assert sorted(TOOLS) == found, "add or remove names in TOOLS to match"


@pytest.mark.parametrize("name", TOOLS)
def test_tool_imports_as_part_of_the_tools_package(name):
    r = subprocess.run([sys.executable, "-c", f"import tools.{name}"], cwd=REPO,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]


# --- every tools/*.py (overnight-112526 S2, 2026-11-25) ------------------------------
# feedback_redundant_means_no_caller_on_any_load_path: 4 fires. The list above covers only
# tools that import md_table / hook_runtime / slug; a deletion that breaks `import tools.X`
# anywhere else passed the suite (fire 4: a sys.path line in run_metrics.py). This test
# imports EVERY tools/*.py as a package. Measured 2026-11-25 at e0d56d99: 18 of 246 fail;
# S2b (s2b-imports-1125) fixed 17 of them, see FIXED_1125.
# They are listed with the reason, not fixed here (S2 owns only this test); fixing one makes
# test_known_unimportable_list_is_not_stale fail, and the fix is to delete its line.
# Each value is (reason, a fragment the import's stderr must contain): the stale-list test
# checks the documented failure, not any failure, so a listed tool that swaps its known
# failure for a new one still fails the suite (Codex S2 pre-commit F2).
def _sibling(mod):
    return ("imports a sibling module with no sys.path setup, so the package import fails",
            f"No module named '{mod}'")


KNOWN_UNIMPORTABLE = {
    # Guard infrastructure (check_guard_edit_approval GUARD_PATTERNS + HOOK_LIBRARIES): its
    # fix is a staged install under Nick's approval, not an ordinary edit (S2b, 2026-11-25).
    "calendar_approval": _sibling("approval_signing"),
}
ALL_TOOLS = sorted(p.stem for p in (REPO / "tools").glob("*.py"))


def _imports(name: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", f"import tools.{name}"], cwd=REPO,
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


def test_known_unimportable_names_are_real_tools():
    assert sorted(set(KNOWN_UNIMPORTABLE) - set(ALL_TOOLS)) == []


@pytest.mark.parametrize("name", [n for n in ALL_TOOLS if n not in KNOWN_UNIMPORTABLE])
def test_every_tool_imports_as_part_of_the_tools_package(name):
    r = _imports(name)
    assert r.returncode == 0, r.stderr[-800:]


@pytest.mark.parametrize("name", sorted(KNOWN_UNIMPORTABLE))
def test_known_unimportable_list_is_not_stale(name):
    """A listed tool that now imports must leave the list, or the list hides the next break;
    one that fails for a DIFFERENT reason is a new break, not the listed one."""
    r = _imports(name)
    assert r.returncode != 0, f"tools.{name} imports now: delete it from KNOWN_UNIMPORTABLE"
    expected = KNOWN_UNIMPORTABLE[name][1]
    assert expected in r.stderr, f"tools.{name} fails, but not with {expected!r}: {r.stderr[-400:]}"



# The 17 tools S2b fixed (2026-11-25), NAMED so that mutation_check maps this file to each
# one: it selects a test file only when the file names the tool, and the glob test above
# names none of them. Each had no sys.path setup before its sibling import (conflict_diff
# parsed argv at import). Runtime-redundant with the glob test; kept for the mapping.
FIXED_1125 = (
    "booking_tracker",
    "calendar_fetch",
    "calendar_write",
    "conflict_diff",
    "deck_to_pdf",
    "extract_decisions",
    "fit_chart_viewbox",
    "job_quiesce",
    "launch_swarm",
    "memory_usage",
    "merge_chain",
    "mutation_report",
    "mutation_trend",
    "provenance_probe",
    "render_slides",
    "sf_property_records",
    "slide_check",
)


def test_fixed_1125_names_are_real_and_not_listed_unimportable():
    assert sorted(set(FIXED_1125) - set(ALL_TOOLS)) == []
    assert set(FIXED_1125).isdisjoint(KNOWN_UNIMPORTABLE)


@pytest.mark.parametrize("name", FIXED_1125)
def test_fixed_1125_tool_imports_as_part_of_the_tools_package(name):
    r = _imports(name)
    assert r.returncode == 0, r.stderr[-800:]
