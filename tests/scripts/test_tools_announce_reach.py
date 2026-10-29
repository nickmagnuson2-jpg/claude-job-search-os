"""Every tools/*.py that prints a count must announce its reach, or say in writing why not.

WHY (2026-10-23). The Hard Rule "Name the scope in the same sentence as the conclusion" fired
38 times, mostly on instrument reach. tools/count_report.py prints a count together with its
population and named exclusions, and refuses counts that do not conserve. This test keeps
NEW tools from printing bare counts.

DETECTOR DEFINITION (a heuristic over the AST, stated so its misses are known). A top-level
tools/*.py file "prints counts" when it contains either
  (a) an f-string with at least one interpolated value whose literal text contains a count
      word (found, total, count(s), scanned, matched, records, files, rows, items, of) or a
      `%`, or that interpolates a `len(...)` call, uses a percent format spec (`:.1%`), or
      has `{a}/{b}`; or
  (b) a `print(...)` call that passes a `len(...)` call, or passes a string containing a
      count word next to a non-literal argument (`print("Found", n, "records")`).
It flags f-strings that are never printed (error messages, file writes), and it misses
counts built with `%`/`.format()` or printed through a helper other than `print`. A file
that does not parse is flagged. It does not look in tools/ subdirectories.
tools/count_report.py itself is exempt. Widened 2026-10-23 after codex review F3 showed the
first, line-regex version passed `print(f"Found {count} records")`.

A flagged tool passes when it imports count_report (`import count_report` /
`from count_report import ...`, found by AST) or names `count_report.py` in a string
(a subprocess call), or when tools/count-report-allow.json maps its path to a non-empty
written reason. An empty reason fails, the same discipline as tools/mutation-allow.json.

GRANDFATHERING. Every tool flagged on 2026-10-23 was entered with GRANDFATHER_REASON;
there were 133 of them (gitignored private tools excluded), pinned by name in GRANDFATHERED. An entry carrying that reason
must be in the pinned set, so a new tool cannot take an old tool's place, and an entry
whose tool is gone or no longer flagged fails as stale, so the list only shrinks.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"
ALLOW_NAME = "count-report-allow.json"
HELPER = "count_report.py"
GRANDFATHER_REASON = "pre-existing at 2026-10-23, convert when next touched"
# The exact set flagged on 2026-10-23. Entries carrying GRANDFATHER_REASON must be a SUBSET
# of this, so the list can only lose members: a count cap alone let an old entry be swapped
# for a new tool while every check passed (codex review F6, 2026-10-23).
GRANDFATHERED = frozenset([
    "tools/act_apply.py",
    "tools/agent_discover.py",
    "tools/alirohde_nudge.py",
    "tools/allowlist_write.py",
    "tools/apply_memory_verdicts.py",
    "tools/attention.py",
    "tools/backfill_memory_schema.py",
    "tools/backfill_transcript_failures.py",
    "tools/blind_canary.py",
    "tools/calibration_review.py",
    "tools/call_analyzer.py",
    "tools/check_automation_health.py",
    "tools/check_banned_phrase.py",
    "tools/check_changelog_currency.py",
    "tools/check_dark_inputs.py",
    "tools/check_deck_craft.py",
    "tools/check_deck_geometry.py",
    "tools/check_doc_precedence.py",
    "tools/check_draft_voice.py",
    "tools/check_draft_weekday.py",
    "tools/check_edit_safety.py",
    "tools/check_email_via_skill.py",
    "tools/check_frame_integrity.py",
    "tools/check_handoff_producer.py",
    "tools/check_heredoc_quoting.py",
    "tools/check_hook_warn_tier.py",
    "tools/check_pipe_close_via_update.py",
    "tools/check_plan_partner_critique.py",
    "tools/check_prep_doc_format.py",
    "tools/check_probe_prompt.py",
    "tools/check_public_pii.py",
    "tools/check_replace_all_safety.py",
    "tools/check_save_claims.py",
    "tools/check_scanner_examined_something.py",
    "tools/check_script_error_logged.py",
    "tools/check_stale_file_claim.py",
    "tools/check_table_integrity.py",
    "tools/check_unquoted_glob.py",
    "tools/check_zuora_principal_title.py",
    "tools/coaching_metrics.py",
    "tools/codex_verify.py",
    "tools/company_dim.py",
    "tools/concurrent_commit_guard.py",
    "tools/conflict_diff.py",
    "tools/contact_finder.py",
    "tools/context_file_audit.py",
    "tools/convert_pdfs.py",
    "tools/cross_model_gate.py",
    "tools/cv_merge_theme.py",
    "tools/db_sync.py",
    "tools/detector_probe.py",
    "tools/detector_run.py",
    "tools/draft_capture.py",
    "tools/duet_snapshot.py",
    "tools/engagement_runs.py",
    "tools/exa_search.py",
    "tools/extract_decisions.py",
    "tools/fetch_source_email.py",
    "tools/fetch_transcript.py",
    "tools/fetch_x_source.py",
    "tools/filler_baseline.py",
    "tools/finding_write.py",
    "tools/frame_write.py",
    "tools/friction_log.py",
    "tools/gen_pii_denylist.py",
    "tools/gmail_attachments.py",
    "tools/gmail_fetch.py",
    "tools/granola_auto_debrief.py",
    "tools/granola_cli.py",
    "tools/granola_fetch.py",
    "tools/granola_save.py",
    "tools/inbox_census.py",
    "tools/inbox_decision_log.py",
    "tools/inbox_drain_apply.py",
    "tools/inbox_extract.py",
    "tools/inbox_lock.py",
    "tools/job_quiesce.py",
    "tools/ledger_diff.py",
    "tools/linkedin_post_readout.py",
    "tools/living_log_append.py",
    "tools/log_tool_failure.py",
    "tools/md_table.py",
    "tools/md_to_pdf_doc.py",
    "tools/memory_prepass.py",
    "tools/merge_mining_verdicts.py",
    "tools/mutation_check.py",
    "tools/mutation_report.py",
    "tools/mutation_state.py",
    "tools/mutation_sweep.py",
    "tools/networking_write.py",
    "tools/note_divergence.py",
    "tools/open_draft.py",
    "tools/outreach_pending.py",
    "tools/outreach_status.py",
    "tools/person_history.py",
    "tools/person_write.py",
    "tools/personal_todo_write.py",
    "tools/pipe_write.py",
    "tools/pipeline_reconcile.py",
    "tools/population_probe.py",
    "tools/prepush_pii_guard.py",
    "tools/projects_to_yaml.py",
    "tools/promotion_schema.py",
    "tools/provenance_probe.py",
    "tools/reader_review.py",
    "tools/records_lookup.py",
    "tools/recruiter_views.py",
    "tools/rederivation_log.py",
    "tools/remember_apply.py",
    "tools/render_slides.py",
    "tools/role_queue_read.py",
    "tools/scan_jobs.py",
    "tools/scan_promotion_candidates.py",
    "tools/scorer_eval.py",
    "tools/scoring_probe.py",
    "tools/slide_check.py",
    "tools/source_check.py",
    "tools/source_corrections.py",
    "tools/stream_pipeline.py",
    "tools/sweep.py",
    "tools/todo_write.py",
    "tools/validate_run.py",
    "tools/voice_corpus_audit.py",
    "tools/voice_extract.py",
    "tools/voice_features.py",
    "tools/webset_discover.py",
    "tools/wispr_pull.py",
    "tools/workstream_probe.py",
    "tools/workstream_state.py",
])
GRANDFATHERED_AT_CREATION = len(GRANDFATHERED)  # 133

COUNT_WORDS = re.compile(
    r"\b(?:found|total|count|counts|scanned|matched|records|files|rows|items|of)\b", re.I)


def _is_count_call(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "len"
               for n in ast.walk(node))


def _fstring_counts(node: ast.JoinedStr) -> bool:
    values = [v for v in node.values if isinstance(v, ast.FormattedValue)]
    if not values:
        return False
    text = "".join(v.value for v in node.values
                   if isinstance(v, ast.Constant) and isinstance(v.value, str))
    if COUNT_WORDS.search(text) or text.lstrip().startswith("%") or "%" in text:
        return True
    for v in values:
        if _is_count_call(v.value):
            return True
        spec = v.format_spec
        if spec is not None and any(isinstance(c, ast.Constant) and "%" in str(c.value)
                                    for c in spec.values):
            return True
    for a, b, c in zip(node.values, node.values[1:], node.values[2:]):
        if (isinstance(a, ast.FormattedValue) and isinstance(c, ast.FormattedValue)
                and isinstance(b, ast.Constant) and b.value.strip() == "/"):
            return True
    return False


def _print_counts(node: ast.Call) -> bool:
    args = node.args
    if any(_is_count_call(a) for a in args):
        return True
    words = any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                and COUNT_WORDS.search(a.value) for a in args)
    values = any(not isinstance(a, (ast.Constant, ast.JoinedStr)) for a in args)
    return words and values


def prints_counts(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return True     # unparseable: flag it, so it needs a reason instead of passing unseen
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and _fstring_counts(node):
            return True
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print" and _print_counts(node)):
            return True
    return False


def uses_helper(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "count_report" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "count_report":
            return True
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and HELPER in node.value):
            return True
    return False


def _gitignored(paths: list[Path]) -> set[Path]:
    """The subset of `paths` git ignores. A gitignored tool never reaches the public
    repo, so it is outside this gate; outside a git repo nothing counts as ignored."""
    if not paths:
        return set()
    r = subprocess.run(["git", "-C", str(paths[0].parent), "check-ignore", "--stdin"],
                       input="\n".join(str(p) for p in paths), capture_output=True, text=True)
    if r.returncode not in (0, 1):
        return set()
    return {Path(line) for line in r.stdout.splitlines() if line}


def flagged_tools(tools_dir: Path) -> list[str]:
    """Paths (relative, `tools/x.py`) of tools that print counts without the helper.
    Gitignored tools are skipped: they are private and a fresh clone does not have them,
    so an allowlist entry for one would read as stale there."""
    out = []
    candidates = sorted(tools_dir.glob("*.py"))
    ignored = _gitignored(candidates)
    for p in candidates:
        if p.name == HELPER or p in ignored:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if prints_counts(text) and not uses_helper(text):
            out.append(f"tools/{p.name}")
    return out


def load_allow(tools_dir: Path) -> dict:
    path = tools_dir / ALLOW_NAME
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} must be a JSON object"
    return data


def violations(tools_dir: Path) -> list[str]:
    allow = load_allow(tools_dir)
    bad = []
    for rel in flagged_tools(tools_dir):
        reason = allow.get(rel)
        if reason is None:
            bad.append(f"{rel}: prints counts without count_report and is not allowlisted")
        elif not str(reason).strip():
            bad.append(f"{rel}: allowlisted with an empty reason")
    return bad


# --- the real tree -------------------------------------------------------------------

def test_every_count_printing_tool_uses_helper_or_has_reason():
    bad = violations(TOOLS_DIR)
    assert not bad, (
        "Tools print counts without stating their reach. Use tools/count_report.py "
        "(report(scope, population, included, excluded)), or add the path to "
        f"tools/{ALLOW_NAME} with a written reason:\n  " + "\n  ".join(bad))


def test_allowlist_has_no_stale_entries():
    flagged = set(flagged_tools(TOOLS_DIR))
    stale = [k for k in load_allow(TOOLS_DIR) if k not in flagged]
    assert not stale, f"remove from tools/{ALLOW_NAME} (converted or deleted): {stale}"


def grandfather_violations(allow: dict, pinned: frozenset = GRANDFATHERED) -> list[str]:
    return sorted(k for k, v in allow.items() if v == GRANDFATHER_REASON and k not in pinned)


def test_grandfather_reason_is_not_reused_for_new_tools():
    bad = grandfather_violations(load_allow(TOOLS_DIR))
    assert not bad, (
        f"not in the 2026-10-23 grandfathered set, so they need the helper or their own "
        f"reason: {bad}")


def test_swapped_grandfather_entry_fails():
    pinned = frozenset(["tools/old_a.py", "tools/old_b.py"])
    swapped = {"tools/old_a.py": GRANDFATHER_REASON, "tools/new_c.py": GRANDFATHER_REASON}
    assert grandfather_violations(swapped, pinned) == ["tools/new_c.py"]
    assert grandfather_violations({"tools/old_a.py": GRANDFATHER_REASON}, pinned) == []
    assert grandfather_violations({"tools/new_c.py": "own written reason"}, pinned) == []


# --- the detector catches what it should --------------------------------------------

BARE = 'items = [1, 2]\nprint(f"{len(items)} items found")\n'


def _tree(tmp_path: Path, files: dict[str, str], allow: dict | None = None) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    for name, body in files.items():
        (tools / name).write_text(body, encoding="utf-8")
    if allow is not None:
        (tools / ALLOW_NAME).write_text(json.dumps(allow), encoding="utf-8")
    return tools


def test_new_bare_count_tool_is_caught(tmp_path):
    tools = _tree(tmp_path, {"new_tool.py": BARE})
    assert violations(tools) == [
        "tools/new_tool.py: prints counts without count_report and is not allowlisted"]


def test_empty_reason_fails(tmp_path):
    tools = _tree(tmp_path, {"new_tool.py": BARE}, {"tools/new_tool.py": "  "})
    assert violations(tools) == ["tools/new_tool.py: allowlisted with an empty reason"]


def test_written_reason_passes(tmp_path):
    tools = _tree(tmp_path, {"new_tool.py": BARE}, {"tools/new_tool.py": "prints a size"})
    assert violations(tools) == []


@pytest.mark.parametrize("body", [
    "import count_report\n" + BARE,
    "from count_report import report\n" + BARE,
    'import subprocess\nsubprocess.run(["python3", "tools/count_report.py", "files", "x"])\n'
    + BARE,
])
def test_tool_using_helper_passes(tmp_path, body):
    tools = _tree(tmp_path, {"new_tool.py": body})
    assert violations(tools) == []


def test_comment_mentioning_helper_does_not_pass(tmp_path):
    tools = _tree(tmp_path, {"new_tool.py": "# see count_report.py\n" + BARE})
    assert len(violations(tools)) == 1


@pytest.mark.parametrize("body", [
    'count = 3\nprint(f"Found {count} records")\n',
    'count = 3\nprint("Found", count, "records")\n',
])
def test_ordinary_count_output_is_caught(tmp_path, body):
    """codex review F3: both forms passed the first, line-regex detector."""
    tools = _tree(tmp_path, {"new_tool.py": body})
    assert violations(tools) == [
        "tools/new_tool.py: prints counts without count_report and is not allowlisted"]


@pytest.mark.parametrize("line,flag", [
    ('print(f"{len(xs)} rows")', True),
    ('print(f"Wrote {path}")', False),
    ('print("total", "rows")', False),
    ('print("Wrote", path)', False),
    ('def f(:', True),
    ('msg = f"{a} of {b} done"', True),
    ('print(f"{a}/{b}")', True),
    ('print(f"{r:.1%}")', True),
    ('print(f"{pct}% done")', True),
    ('print("hello")', False),
    ('n = len(xs)', False),
    ('# print(f"{len(xs)}")', False),
])
def test_detector_definition(line, flag):
    assert prints_counts(line) is flag


def test_helper_itself_is_exempt(tmp_path):
    tools = _tree(tmp_path, {HELPER: BARE})
    assert flagged_tools(tools) == []


def test_gitignored_tool_is_outside_the_gate(tmp_path):
    """A private tool git ignores never reaches the public repo, so it is not flagged;
    the same file un-ignored is."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    body = 'items = [1, 2]\nprint(f"Found {len(items)} records")\n'
    (tools / "private_tool.py").write_text(body, encoding="utf-8")
    (tools / "public_tool.py").write_text(body, encoding="utf-8")
    (tmp_path / ".gitignore").write_text("tools/private_tool.py\n", encoding="utf-8")
    assert flagged_tools(tools) == ["tools/public_tool.py"]
