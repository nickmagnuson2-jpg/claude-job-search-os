#!/usr/bin/env python3
"""Apply manual overrides to the first-pass family assignment and emit the final
roster + counts + invariant check. Reusable on pickup: edit OVERRIDES and re-run.

Run: PYTHONIOENCODING=utf-8 python3 tools/memory_family_finalize.py
Reads /tmp/family_rosters.json (from memory_family_assign.py).
Writes /tmp/family_rosters_final.json.
"""
import json

# Manual resolution of the 56 REVIEW items (ties / zero-score). Judgment calls
# made 2026-06-04 with full index-line context. Items tagged [FLAG] in the
# handoff are the genuinely cross-cutting ones to re-confirm on pickup.
OVERRIDES = {
    "feedback_attribute_data_to_source_of_truth_owner.md": "verification",
    "feedback_autonomous_runbook_not_narrated_phases.md": "multi_agent",
    "feedback_background_agent_collision_discipline.md": "multi_agent",
    "feedback_casual_register_proper_capitalization.md": "banned_vocab",
    "feedback_check_existing_artifacts_before_drafting.md": "correction_discipline",
    "feedback_count_failure_family_n_for_promotion.md": "memory_loop",
    "feedback_cross_context_name_conflation.md": "verification",
    "feedback_cross_reference_personal_network_before_cold_classification.md": "research_dossier",
    "feedback_cv_one_page_default_no_skills_fluff.md": "application_craft",  # FLAG
    "feedback_deep_coaching_facilitation_pattern.md": "interview_prep",
    "feedback_default_to_canonical_spelling_when_memory_flags_wispr_rendering.md": "capture_routing",
    "feedback_dev_jargon_to_ceo_context.md": "outreach_craft",
    "feedback_draft_email_non_job_search_pathway.md": "outreach_craft",
    "feedback_email_batch_to_index_plus_archives.md": "memory_loop",
    "feedback_email_hook_allowlist_too_narrow.md": "hook_script",
    "feedback_ex_colleague_readability_bar.md": "application_craft",  # FLAG
    "feedback_excitement_compresses_time.md": "outreach_craft",
    "feedback_excluded_folder_still_linkable.md": "capture_routing",
    "feedback_finder_open_for_attachment.md": "outreach_craft",
    "feedback_friction_log_hook_blind_spots.md": "hook_script",
    "feedback_gh_pr_create_fork_default.md": "hook_script",
    "feedback_honor_fatigue_without_dropping_quality.md": "correction_discipline",
    "feedback_index_decay_needs_maintenance_trigger.md": "build_gating",
    "feedback_judgment_as_wedge.md": "build_gating",
    "feedback_ledger_dodge_closers_in_header.md": "memory_loop",
    "feedback_long_form_generation_bypasses_hard_rules.md": "banned_vocab",
    "feedback_memory_index_entry_required_at_write_time.md": "memory_loop",
    "feedback_morning_starter_doc_pattern.md": "build_gating",  # FLAG (workflow pattern)
    "feedback_multipass_independent_review.md": "multi_agent",
    "feedback_no_confabulation_in_agent_prompts.md": "verification",
    "feedback_no_confabulation_in_corpus_synthesis.md": "verification",
    "feedback_no_em_dashes.md": "banned_vocab",
    "feedback_no_if_you_have_a_sec_scaffold.md": "banned_vocab",
    "feedback_no_product_docs_to_employees.md": "outreach_craft",
    "feedback_partner_critique_doesnt_read_user_context_files.md": "multi_agent",
    "feedback_print_prep_pdfs.md": "interview_prep",
    "feedback_project_files_overclaim_vector.md": "application_craft",
    "feedback_procrastination_interrupt_near_commitment.md": "build_gating",  # FLAG (Nick-state)
    "feedback_query_source_db_not_pull_window.md": "capture_routing",
    "feedback_read_before_edit_active_session.md": "hook_script",
    "feedback_reflection_wiki_links.md": "capture_routing",
    "feedback_reglob_newest_races_against_pinned_target.md": "hook_script",
    "feedback_remember_deferred_goes_to_inbox.md": "capture_routing",
    "feedback_sibling_skill_audit_for_systemic_defects.md": "correction_discipline",
    "feedback_skill_workflows_close_trigger_todos.md": "build_gating",
    "feedback_source_inventory_before_composing_v1.md": "correction_discipline",
    "feedback_spec_before_rebuild_designed_artifacts.md": "build_gating",
    "feedback_substance_defer_vs_hypothesize_and_invite.md": "interview_prep",
    "feedback_surface_state_ledger_high_velocity.md": "correction_discipline",  # FLAG
    "feedback_timeboxed_prep_compression.md": "interview_prep",
    "feedback_todos_full_file_read_required.md": "hook_script",
    "feedback_update_everywhere_targets_authoritative_not_factual_enumerations.md": "correction_discipline",
    "feedback_verify_agent_research_before_propagation.md": "verification",
    "feedback_verify_repo_root_isolation_before_trusting_build.md": "hook_script",
    "feedback_voice_anchor_pass_at_iteration_3.md": "voice_pure_generation",
    "feedback_voice_hook_mechanical_not_tonal.md": "banned_vocab",
    "feedback_wire_read_consumers_with_write_path.md": "build_gating",
}

DOC = {
    "verification": "framework/verification-umbrella.md (EXISTS - add roster)",
    "voice_pure_generation": "framework/rule-families/voice-pure-generation.md (NEW)",
    "banned_vocab": "framework/style-guidelines.md (EXISTS - add roster)",
    "outreach_craft": "framework/rule-families/outreach-craft.md (NEW)",
    "multi_agent": "framework/rule-families/multi-agent-orchestration.md (NEW)",
    "hook_script": "tools/HOOK_AUTHORING.md (EXISTS - add roster)",
    "build_gating": "framework/rule-families/build-decision-gating.md (NEW)",
    "interview_prep": "framework/interview-workflow.md (EXISTS - add roster)",
    "research_dossier": "framework/rule-families/research-dossier-craft.md (NEW)",
    "capture_routing": "framework/rule-families/capture-routing.md (NEW)",
    "correction_discipline": "framework/rule-families/correction-discipline.md (NEW)",
    "memory_loop": "framework/rule-families/memory-loop-discipline.md (NEW)",
    "application_craft": "framework/rule-families/application-craft.md (NEW)",
}

data = json.load(open("/tmp/family_rosters.json"))
rosters = {k: list(v) for k, v in data["rosters"].items()}
rosters.setdefault("application_craft", [])
review = data["review"]

# Overrides are authoritative: remove the file from any first-pass bucket, then place it.
for f, fam in OVERRIDES.items():
    for k in list(rosters):
        if f in rosters[k]:
            rosters[k].remove(f)
    rosters.setdefault(fam, []).append(f)

# Any REVIEW item without an explicit override is unresolved.
for f in review:
    if f not in OVERRIDES:
        rosters.setdefault("UNRESOLVED", []).append(f)

total = sum(len(v) for v in rosters.values())
print(f"final assigned: {total}  (target feedback total: 223)")
print(f"invariant 223: {total == 223}")
print()
for k in list(DOC) + [x for x in rosters if x not in DOC]:
    if k in rosters:
        print(f"  {k:24s} {len(rosters[k]):3d}   {DOC.get(k, '?')}")

json.dump(rosters, open("/tmp/family_rosters_final.json", "w"), indent=2)
print("\nwrote /tmp/family_rosters_final.json")
