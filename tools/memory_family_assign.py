#!/usr/bin/env python3
"""First-pass assignment of feedback memory files to consolidation families.

Keyword-scores each feedback file (filename + description + existing index line)
against 12 family keyword sets, picks the top-scoring family. Ties / zero-score
land in REVIEW for manual correction. Output: JSON {family: [files], ...} +
a REVIEW bucket + an invariant check (assigned + review == total feedback).

Read-only. Run after memory_index_partition.py.
Run: PYTHONIOENCODING=utf-8 python3 tools/memory_family_assign.py /tmp/mem_partition.json
"""
import json
import re
import sys

# family_key -> (doc_path, keyword list). Order matters only for tie-break (earlier wins).
FAMILIES = {
    "verification": ("framework/verification-umbrella.md", [
        "verif", "confabulat", "absence", "audit", "doc_tracked", "doc-tracked",
        "tool-tracked", "auto_capture", "auto-capture", "subagent_report",
        "quantitative", "bio_facts", "recall_against", "doc_vs_reality",
        "claiming_done", "behavioral_data", "memory_as_pointer", "precheck_plans",
        "gap_audit", "mixed_source", "disambiguate", "liveness", "employment_status",
        "applied_status", "counterparty_causality", "similar_companies", "dedup_telemetry",
        "pulled_corpus", "no_synthetic", "fabricat",
    ]),
    "voice_pure_generation": ("framework/rule-families/voice-pure-generation.md", [
        "voice_pure", "voice-pure", "dictat", "give_nick_beats", "truth_check",
        "false_voice", "polish", "minimize_polish", "diff_minimal", "qa_paste",
        "generative_spine", "voice_asset", "import_interviewer", "blockquote",
        "partner_mode_silent", "anchor_review", "two_tier", "draft_zero",
        "clean_dont_polish", "proactively_surface_corpus",
    ]),
    "banned_vocab": ("framework/style-guidelines.md", [
        "em_dash", "em-dash", "load_bearing", "load-bearing", "if_you_have_a_sec",
        "shape_of_role", "casual_register", "address_wife", "voice_hook_mechanical",
        "canonical_spelling", "speculatively_change", "no_load_bearing",
    ]),
    "outreach_craft": ("framework/rule-families/outreach-craft.md", [
        "outreach", "speculative_framing", "dev_jargon", "soften_default",
        "graceful_acknowledg", "product_docs", "one_ask", "first_person_voice",
        "forwardable", "culture_callback", "content_overlap", "read_full_thread",
        "substance_defer", "polished_problem_solution", "follow_up_with_recruiters",
        "free_outreach", "warm_thread", "skin_in_game", "no_speculative", "email_paste",
        "soft_signal", "gut_signal", "excitement_compresses", "gmail_compose",
        "no_false_voice_provenance",
    ]),
    "multi_agent": ("framework/rule-families/multi-agent-orchestration.md", [
        "partner_review", "partner_pressuretest", "partner_critique", "multipass",
        "synthesis_prompts", "agent_prompts", "parallel_agent", "background_agent",
        "subagent_shared_tree", "subagent_denial", "audit_nick_input",
        "batch_person", "hold_controlled_spec", "hold_ab_spec", "ab_test",
        "recalibrate_parallel", "no_confabulation_in_agent",
    ]),
    "hook_script": ("tools/HOOK_AUTHORING.md", [
        "hook", "command_hook", "warn_vs_block", "self_trigger", "smoke_test",
        "wrong_oracle", "passing_test", "patch_to_green", "classifier",
        "replace_all", "calibration_dependent", "meta_tool", "inventory_infrastructure",
        "restructure_over_hook", "agent_infra", "repo_root_isolation",
        "derive_parameters", "plan_tests", "sanctioned_tools", "atomic_script",
        "settings_json", "bash_", "macos", "todo_write_kwargs", "bare_python",
        "hand_crafted_fixtures", "smoke",
    ]),
    "build_gating": ("framework/rule-families/build-decision-gating.md", [
        "infrastructure_during", "demand_signal", "dopamine", "disposable_first",
        "recovery_vs_automation", "surface_demand", "defer_todos", "reopen",
        "durable_means", "optimization_criterion", "challenge_delivery",
        "index_decay", "charter_drift", "dont_offer_deferral", "name_the_gap",
        "clarify_implementer", "judgment_as_wedge", "friction_log", "friction-log",
        "grey_area_provenance", "habit_stacking", "wait_for_outcome",
        "name_optimization", "wire_read_consumers", "count_failure_family",
    ]),
    "interview_prep": ("framework/interview-workflow.md", [
        "pei_depth", "case_practice", "calibrate_drills", "own_coaching",
        "pre_name_self_doubt", "discovery_call", "mbb", "timeboxed_prep",
        "carry_vs_ask", "prebake_comp", "comp_above_floor", "print_prep",
        "deep_coaching", "spectrum_default", "self_articulation", "drill",
        "calibration_dependent_builds",  # ambiguous - may move
    ]),
    "research_dossier": ("framework/rule-families/research-dossier-craft.md", [
        "research_source", "route_research", "social_context_research",
        "pr_content", "additive_refresh", "no_external_research", "idiographic",
        "shortlist", "research_subagents", "industry", "dossier",
    ]),
    "capture_routing": ("framework/rule-families/capture-routing.md", [
        "reflection_event_date", "therapy_adjacent", "sealed_unlock",
        "reflections_in_two", "excluded_folder", "wispr", "routing",
        "cross_context_name", "canonical_filename", "query_source_db",
        "reglob_newest", "reflection_wiki", "remember_deferred", "persist_granola",
        "granola", "two_tier_capture", "no_batch_write",
    ]),
    "correction_discipline": ("framework/rule-families/correction-discipline.md", [
        "push_back", "grey_area", "narrow_scope", "directed_fix", "reframe_not_pivot",
        "update_everywhere", "clone_template", "sibling_skill_audit", "honor_fatigue",
        "proportional_process", "clarify_doc_audience", "iterative_context",
        "source_inventory", "check_existing_artifacts", "compound_instruction",
        "long_form", "default_narrow", "reframe", "hold_controlled",
        "search_destination", "ask_before_searching",
    ]),
    "memory_loop": ("framework/rule-families/memory-loop-discipline.md", [
        "memory_index_entry", "rich_context_park", "email_batch_to_index",
        "lessons", "memory_directory",
    ]),
}


def score(text, keywords):
    t = text.lower()
    return sum(1 for kw in keywords if kw.lower() in t)


def main():
    data = json.load(open(sys.argv[1]))
    fb = [e for e in data["entries"] if e["type"] == "feedback"]

    rosters = {k: [] for k in FAMILIES}
    review = []
    for e in fb:
        blob = e["file"] + " " + (e.get("description") or "") + " " + (e.get("existing_line") or "")
        scores = {k: score(blob, kws) for k, (_, kws) in FAMILIES.items()}
        best = max(scores.values())
        winners = [k for k, v in scores.items() if v == best and v > 0]
        if best == 0 or len(winners) > 1:
            review.append({"file": e["file"], "top": sorted(
                [(k, v) for k, v in scores.items() if v > 0], key=lambda x: -x[1])[:3],
                "desc": (e.get("description") or "")[:90]})
        else:
            rosters[winners[0]].append(e["file"])

    assigned = sum(len(v) for v in rosters.values())
    print(f"feedback total: {len(fb)}")
    print(f"assigned: {assigned}   REVIEW (ambiguous/zero): {len(review)}")
    print(f"invariant assigned+review == total: {assigned + len(review) == len(fb)}")
    print()
    for k, (doc, _) in FAMILIES.items():
        print(f"  {k:24s} {len(rosters[k]):3d}   -> {doc}")
    print()
    print("=== REVIEW bucket (need manual assignment) ===")
    for r in review:
        tops = ", ".join(f"{k}:{v}" for k, v in r["top"]) or "(none)"
        print(f"- {r['file']}")
        print(f"    top: {tops}")
        print(f"    {r['desc']}")

    json.dump({"rosters": rosters, "review": [r["file"] for r in review]},
              open("/tmp/family_rosters.json", "w"), indent=2)
    print("\nwrote /tmp/family_rosters.json")


if __name__ == "__main__":
    main()
