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
    "check_banned_phrase",
    "check_bare_python",
    "check_changelog_currency",
    "check_data_projects",
    "check_derived_ratio",
    "check_draft_voice",
    "check_edit_after_mutation",
    "check_edit_safety",
    "check_email_via_skill",
    "check_gmail_read_mcp",
    "check_guard_edit_approval",
    "check_handoff_producer",
    "check_heredoc_quoting",
    "check_living_log_purity",
    "check_module_imports",
    "check_new_toplevel_dir_protected",
    "check_no_confabulation",
    "check_pipe_close_via_update",
    "check_pipeline_exit_status",
    "check_plan_partner_critique",
    "check_prep_doc_format",
    "check_public_pii",
    "check_replace_all_safety",
    "check_save_claims",
    "check_scanner_examined_something",
    "check_screenshot_path",
    "check_script_error_logged",
    "check_sealed_read",
    "check_stale_file_claim",
    "check_status_query_verification",
    "check_table_integrity",
    "check_todo_write_kwargs",
    "check_unquoted_glob",
    "check_voice_pure",
    "check_workflow_scriptpath",
    "check_zsh_word_split",
    "check_zuora_principal_title",
    "context_file_audit",
    "daily_stoic",
    "dossier_freshness",
    "fetch_transcript",
    "fetch_x_source",
    "frame_write",
    "gen_pii_denylist",
    "gmail_fetch",
    "granola_auto_debrief",
    "granola_save",
    "inbox_extract",
    "living_log_append",
    "memory_index_partition",
    "migrate_todos_column_drift",
    "my_world_synthesis",
    "networking_read",
    "networking_write",
    "note_divergence",
    "person_write",
    "personal_todo_write",
    "pipe_read",
    "pipe_write",
    "reader_review",
    "remember_apply",
    "remember_classify",
    "scan_jobs",
    "scan_transcript_failures",
    "todo_daily_metrics",
    "todo_write",
    "voice_extract",
    "webset_discover",
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
