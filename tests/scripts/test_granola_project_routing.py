"""Tests for personal-OS project routing in tools/granola_auto_debrief.py.

Before this, the auto-debrief cron had no project awareness: every classifiable
call posted its debrief snippet to job-search/data/inbox.md and persisted its raw
transcript to job-search/data/voice-corpus/granola/, including personal-OS calls
that belong in the personal vault (side-business partner calls, contractor
meetings, family logistics). Two side-business transcripts had to be relocated by hand
on 2026-08-06.

Routing rules under test:
  therapy   -> sealed vault, never inbox            (pre-existing, must not regress)
  personal  -> personal vault corpus + personal inbox, tagged with a project slug
  networking-> job-search corpus + job-search inbox (pre-existing, must not regress)
  unknown   -> fail closed, persist nowhere         (pre-existing, must not regress)

The personal allowlist is gitignored (tools/.personal-projects.txt) because it
holds real names; these tests build configs inline instead.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.granola_auto_debrief import (
    classify_meeting,
    load_personal_projects_config,
    match_personal_project,
    format_inbox_entry,
)

PARTNER_CONFIG = {
    "rules": [
        {
            "project": "side-project",
            "attendees": {"robin@example.com"},
            "names": ["Robin Placeholder"],
            "titles": ["side project"],
        },
        {
            "project": "woodworking-desk",
            "attendees": set(),
            "names": ["Casey Carpenter"],
            "titles": ["desk build"],
        },
    ]
}

EMPTY_THERAPY = {"attendees": set(), "names": []}


# --- config loading -------------------------------------------------------

def test_load_config_parses_project_blocks(tmp_path):
    cfg = tmp_path / ".personal-projects.txt"
    cfg.write_text(
        "# comment line\n"
        "\n"
        "project: side-project\n"
        "attendee: robin@example.com\n"
        "name: Robin Placeholder\n"
        "title: side project\n"
        "\n"
        "project: upstairs-move\n"
        "name: Pat Contractor\n",
        encoding="utf-8",
    )
    out = load_personal_projects_config(cfg)
    assert [r["project"] for r in out["rules"]] == ["side-project", "upstairs-move"]
    assert out["rules"][0]["attendees"] == {"robin@example.com"}
    assert out["rules"][0]["names"] == ["Robin Placeholder"]
    assert out["rules"][0]["titles"] == ["side project"]
    assert out["rules"][1]["names"] == ["Pat Contractor"]


def test_missing_config_yields_no_rules(tmp_path):
    out = load_personal_projects_config(tmp_path / "nope.txt")
    assert out["rules"] == []


def test_directives_before_any_project_are_ignored(tmp_path):
    """A stray directive with no enclosing 'project:' must not crash or leak."""
    cfg = tmp_path / ".personal-projects.txt"
    cfg.write_text("name: Orphan Person\nproject: real\nname: Real Person\n", encoding="utf-8")
    out = load_personal_projects_config(cfg)
    assert len(out["rules"]) == 1
    assert out["rules"][0]["names"] == ["Real Person"]


# --- project matching -----------------------------------------------------

def test_match_by_attendee_email():
    meeting = {"title": "Catch up", "attendees": [{"name": "Robin", "email": "robin@example.com"}]}
    assert match_personal_project(meeting, PARTNER_CONFIG) == "side-project"


def test_match_by_title_keyword():
    meeting = {"title": "Side Project working session", "attendees": []}
    assert match_personal_project(meeting, PARTNER_CONFIG) == "side-project"


def test_match_by_transcript_name_when_no_attendees():
    """The in-person case: Granola lists only Nick, the counterpart is in the text."""
    meeting = {
        "title": "Robin ",
        "attendees": [],
        "transcript": "Speaker A: hey. Speaker B: Robin Placeholder here, so three sessions.",
    }
    assert match_personal_project(meeting, PARTNER_CONFIG) == "side-project"


def test_no_match_returns_none():
    meeting = {"title": "ActiveCo onsite", "attendees": [{"name": "Recruiter", "email": "r@activeco.example"}]}
    assert match_personal_project(meeting, PARTNER_CONFIG) is None


def test_first_matching_rule_wins():
    meeting = {"title": "side project + desk build", "attendees": []}
    assert match_personal_project(meeting, PARTNER_CONFIG) == "side-project"


# --- classification -------------------------------------------------------

def test_personal_call_with_attendee_classifies_personal():
    meeting = {"title": "Check in", "attendees": [{"name": "Robin", "email": "robin@example.com"}]}
    assert classify_meeting(meeting, EMPTY_THERAPY, PARTNER_CONFIG) == "personal"


def test_in_person_personal_call_is_rescued_from_unknown():
    """The 2026-08-06 regression case.

    Granola listed only Nick, so the old classifier fail-closed to 'unknown' and
    dropped the call entirely. With a project match it must route to 'personal'.
    """
    meeting = {
        "title": "Robin ",
        "attendees": [],
        "transcript": "Speaker B: Robin Placeholder. three sessions. the payout from the platform was 120.",
    }
    assert classify_meeting(meeting, EMPTY_THERAPY, PARTNER_CONFIG) == "personal"


def test_therapy_still_wins_over_personal():
    """Safety ordering: a therapy signal must never be downgraded to personal."""
    therapy_cfg = {"attendees": {"doc@example.com"}, "names": []}
    meeting = {
        "title": "Side Project sync",
        "attendees": [{"name": "Doc", "email": "doc@example.com"}],
    }
    assert classify_meeting(meeting, therapy_cfg, PARTNER_CONFIG) == "therapy"


def test_therapy_title_keyword_still_wins_over_personal():
    meeting = {"title": "therapy session", "attendees": [{"name": "S", "email": "robin@example.com"}]}
    assert classify_meeting(meeting, EMPTY_THERAPY, PARTNER_CONFIG) == "therapy"


def test_therapy_transcript_name_still_wins_in_person():
    therapy_cfg = {"attendees": set(), "names": ["Marcus Therapist"]}
    meeting = {
        "title": "Side Project",
        "attendees": [],
        "transcript": "Marcus Therapist: how did that land for you?",
    }
    assert classify_meeting(meeting, therapy_cfg, PARTNER_CONFIG) == "therapy"


def test_job_search_call_still_networking():
    meeting = {"title": "ActiveCo screen", "attendees": [
        {"name": "Nick Magnuson", "email": "nick@example.com"},
        {"name": "Recruiter", "email": "r@activeco.example"},
    ]}
    assert classify_meeting(meeting, EMPTY_THERAPY, PARTNER_CONFIG) == "networking"


def test_unknown_still_fails_closed_without_project_match():
    meeting = {"title": "Untitled", "attendees": [], "transcript": "Speaker A: hello."}
    assert classify_meeting(meeting, EMPTY_THERAPY, PARTNER_CONFIG) == "unknown"


def test_empty_personal_config_preserves_legacy_behavior():
    """No allowlist file => byte-identical routing to before this feature."""
    meeting = {"title": "Robin ", "attendees": [], "transcript": "Speaker B: three sessions."}
    assert classify_meeting(meeting, EMPTY_THERAPY, {"rules": []}) == "unknown"


def test_classify_meeting_personal_config_defaults_to_none():
    """Called with the legacy 2-arg signature, nothing may break."""
    meeting = {"title": "ActiveCo screen", "attendees": [
        {"name": "Recruiter", "email": "r@activeco.example"},
    ]}
    assert classify_meeting(meeting, EMPTY_THERAPY) == "networking"


# --- inbox entry ----------------------------------------------------------

def test_personal_entry_points_at_meeting_skill_not_debrief():
    meeting = {"title": "Robin ", "created_at": "2026-08-06T21:34:00Z"}
    analysis = {"filler_counts": {}, "qa_pairs": [], "total_questions": 0, "talk_ratio": 0.5}
    entry = format_inbox_entry(meeting, analysis, meeting_type="personal", project="side-project")
    assert "Run `/meeting`" in entry
    assert "side-project" in entry
    assert "#personal" in entry
    # /debrief may appear, but only as an explicit warning against it — never as
    # the call to action. That warning is the point: the 2026-08-06 failure was
    # reaching for /debrief on a partner call.
    assert "Run `/debrief`" not in entry
    assert "do not run `/debrief`" in entry


def test_personal_entry_without_project_slug_degrades_gracefully():
    meeting = {"title": "Some call", "created_at": "2026-08-06T21:34:00Z"}
    analysis = {"filler_counts": {}, "qa_pairs": [], "total_questions": 0, "talk_ratio": 0.5}
    entry = format_inbox_entry(meeting, analysis, meeting_type="personal")
    assert "unassigned" in entry
    assert "Run `/meeting`" in entry


def test_networking_entry_still_points_at_debrief():
    meeting = {"title": "ActiveCo screen", "created_at": "2026-08-06T21:34:00Z"}
    analysis = {"filler_counts": {}, "qa_pairs": [], "total_questions": 0, "talk_ratio": 0.5}
    entry = format_inbox_entry(meeting, analysis)
    assert "/debrief" in entry
    assert "/meeting" not in entry


# --- speaker label rendering ---------------------------------------------

def test_speaker_label_handles_dict_form():
    """Granola REST sometimes returns speaker as a dict; the old code stringified
    the whole dict into every transcript line."""
    from tools.granola_auto_debrief import _speaker_label
    seg = {"speaker": {"source": "microphone", "diarization_label": "Speaker A"}, "text": "hi"}
    assert _speaker_label(seg) == "Speaker A"


def test_speaker_label_handles_plain_string():
    from tools.granola_auto_debrief import _speaker_label
    assert _speaker_label({"speaker": "Me", "text": "hi"}) == "Me"


def test_speaker_label_falls_back():
    from tools.granola_auto_debrief import _speaker_label
    assert _speaker_label({"text": "hi"}) == "Speaker"
    assert _speaker_label({"speaker": {}, "text": "hi"}) == "Speaker"


def test_speaker_label_maps_bare_source_to_me_them():
    """SUPERSEDES the old assertion that a bare source rendered as "microphone".

    That test encoded the pre-2026-08-24 behavior. A corpus-wide count that day
    found 8,330 lines carrying `source` with no `attribution`, plus 814 carrying
    both -- and in every one of the 814, microphone pairs with me and speaker
    pairs with them. The channel is translatable to a person; emitting the raw
    channel name leaves the transcript unparseable by every downstream consumer.
    """
    from tools.granola_auto_debrief import _speaker_label
    assert _speaker_label({"speaker": {"source": "microphone"}}) == "Me"
    assert _speaker_label({"speaker": {"source": "speaker"}}) == "Them"


def test_speaker_label_diarization_outranks_bare_source():
    """A real diarization label is more specific than the channel; keep it."""
    from tools.granola_auto_debrief import _speaker_label
    seg = {"speaker": {"source": "microphone", "diarization_label": "Speaker A"}}
    assert _speaker_label(seg) == "Speaker A"


def test_speaker_label_unknown_source_is_not_silently_me_or_them():
    from tools.granola_auto_debrief import _speaker_label
    assert _speaker_label({"speaker": {"source": "bluetooth"}}) == "bluetooth"
    assert _speaker_label({"speaker": {}}) == "Speaker"


def test_speaker_label_maps_attribution_to_me_them():
    """The shape Granola REST actually returns, measured 2026-08-24.

    Two live transcripts were saved with 58 lines labelled
    ``{'source': 'microphone', 'attribution': 'me'}``. Reading `source` first
    yields "microphone"/"speaker" -- better than a stringified dict, still wrong,
    and still unparseable by every downstream consumer, which splits on Me:/Them:.
    `attribution` is the field that actually carries who spoke.
    """
    from tools.granola_auto_debrief import _speaker_label
    assert _speaker_label({"speaker": {"source": "microphone", "attribution": "me"}}) == "Me"
    assert _speaker_label({"speaker": {"source": "speaker", "attribution": "them"}}) == "Them"


def test_speaker_label_attribution_outranks_source():
    """`source` is the audio channel, `attribution` is the person. Person wins."""
    from tools.granola_auto_debrief import _speaker_label
    seg = {"speaker": {"source": "microphone", "attribution": "them"}}
    assert _speaker_label(seg) == "Them"


def test_speaker_label_attribution_is_case_insensitive():
    from tools.granola_auto_debrief import _speaker_label
    assert _speaker_label({"speaker": {"attribution": "ME"}}) == "Me"
    assert _speaker_label({"speaker": {"attribution": "Them"}}) == "Them"


def test_speaker_label_unknown_attribution_does_not_win():
    """An attribution value that is not me/them must not be emitted raw.

    It falls through to the diarization label, then to the channel mapping --
    an unrecognised attribution does not stop `microphone` from still meaning Me.
    """
    from tools.granola_auto_debrief import _speaker_label
    assert _speaker_label({"speaker": {"attribution": "unknown", "source": "microphone"}}) == "Me"
    assert _speaker_label(
        {"speaker": {"attribution": "unknown", "diarization_label": "Speaker A"}}
    ) == "Speaker A"
    # and with nothing else to go on, it must not invent a person
    assert _speaker_label({"speaker": {"attribution": "unknown"}}) == "Speaker"


def test_granola_cli_renders_labels_through_the_shared_helper():
    """`granola_cli.py pull` had its own copy of the label logic and never got
    the fix, so the CLI path kept writing raw dicts into the voice corpus while
    the auto-debrief path was clean. Single source of truth: the CLI must call
    the shared helper, not re-derive the label.
    """
    import inspect
    from tools import granola_cli

    pull_src = inspect.getsource(granola_cli.cmd_pull)
    assert "_render_transcript" in pull_src, \
        "cmd_pull must delegate transcript rendering, not inline it"
    assert "seg.get('speaker'" not in pull_src and 'seg.get("speaker"' not in pull_src, \
        "cmd_pull must not re-derive the speaker label inline"

    render_src = inspect.getsource(granola_cli._render_transcript)
    assert "_speaker_label" in render_src, \
        "_render_transcript must use the shared _speaker_label helper"
    assert "seg.get('speaker'" not in render_src and 'seg.get("speaker"' not in render_src, \
        "_render_transcript must not re-derive the speaker label either"


def test_granola_cli_pull_produces_me_them_for_attribution_dicts():
    """End-to-end on the rendering expression the CLI actually uses."""
    from tools.granola_cli import _render_transcript
    segs = [
        {"speaker": {"source": "microphone", "attribution": "me"}, "text": "hello"},
        {"speaker": {"source": "speaker", "attribution": "them"}, "text": "hi back"},
    ]
    out = _render_transcript(segs)
    assert out == "Me: hello\nThem: hi back"
    assert "{" not in out and "attribution" not in out


# --- sealed-material leak regression (adversarial review F1) ---------------

NICK = {"name": "Nick Magnuson", "email": "nick@example.com"}
THERAPY_NAMED = {"attendees": {"doc@example.com"}, "names": ["Marcus Therapist"]}
TITLE_RULE = {"rules": [{"project": "side-project", "attendees": set(),
                         "names": [], "titles": ["studio", "side project"]}]}


def test_solo_attendee_therapy_is_not_routed_personal():
    """Granola listing ONLY Nick must not skip the therapist transcript scan.

    The scan used to be gated on `not attendees`, so a self-created calendar
    event (attendees == [Nick]) fell through to the project matcher. With a
    project keyword in the auto-generated title, sealed therapy classified as
    'personal' and would have been written to the project folder, the personal
    inbox, to-dos, and an explicitly external-facing takeaways section.
    """
    m = {"title": "the side project and stress", "attendees": [NICK],
         "transcript": "Marcus Therapist: how did that land for you?"}
    assert classify_meeting(m, THERAPY_NAMED, TITLE_RULE) == "therapy"


def test_no_attendee_therapy_still_sealed_with_project_keyword_present():
    m = {"title": "the side project and stress", "attendees": [],
         "transcript": "Marcus Therapist: say more about that."}
    assert classify_meeting(m, THERAPY_NAMED, TITLE_RULE) == "therapy"


def test_real_multiparty_call_not_mis_sealed_by_passing_mention():
    """A call with a real external party must never be sealed just because a
    therapist's name is spoken in passing."""
    m = {"title": "vendor sync", "attendees": [NICK, {"name": "Vendor", "email": "v@vendor.com"}],
         "transcript": "Speaker A: my friend Marcus Therapist recommended this place."}
    assert classify_meeting(m, THERAPY_NAMED, TITLE_RULE) == "networking"


def test_solo_attendee_personal_call_still_routes_personal():
    """The rescue must survive the fix: no therapy signal, title match -> personal."""
    m = {"title": "side project working session", "attendees": [NICK],
         "transcript": "Speaker A: three sessions."}
    assert classify_meeting(m, THERAPY_NAMED, TITLE_RULE) == "personal"


def test_solo_attendee_no_signal_still_unknown():
    m = {"title": "untitled", "attendees": [NICK], "transcript": "Speaker A: hello."}
    assert classify_meeting(m, THERAPY_NAMED, TITLE_RULE) == "unknown"


# --- vault seam + durability (review items) --------------------------------

def test_vault_directive_parsed_and_resolved(tmp_path):
    from tools.granola_auto_debrief import vault_for_project, personal_vault
    cfg = tmp_path / "p.txt"
    cfg.write_text("project: work-acme\nvault: ~/Documents/Obsidian/30-projects/work\n"
                   "title: standup\n\nproject: side-project\ntitle: side project\n", encoding="utf-8")
    out = load_personal_projects_config(cfg)
    assert out["rules"][0]["vault"].endswith("30-projects/work")
    assert "~" not in out["rules"][0]["vault"]          # expanded
    assert out["rules"][0]["titles"] == ["standup"]     # vault line not swallowed as a title
    assert vault_for_project("work-acme", out).name == "work"
    assert vault_for_project("side-project", out) == personal_vault()   # default
    assert vault_for_project("nonexistent", out) == personal_vault()


def test_session_with_no_longer_seals_business_calls():
    """'Strategy session with the contractor' must not be sealed as therapy."""
    m = {"title": "Strategy session with the contractor", "attendees": [NICK],
         "transcript": "Speaker A: about the quote."}
    assert classify_meeting(m, EMPTY_THERAPY, {"rules": []}) != "therapy"


def test_explicit_therapy_keywords_still_seal():
    for t in ("therapy check-in", "psychiatrist appt", "counseling session"):
        m = {"title": t, "attendees": [NICK], "transcript": "x"}
        assert classify_meeting(m, EMPTY_THERAPY, {"rules": []}) == "therapy", t


def test_pending_classification_is_durable_and_deduped(tmp_path):
    from tools.granola_auto_debrief import record_pending_classification
    f = tmp_path / "pending.json"
    record_pending_classification([{"id": "a", "title": "One"}], f)
    record_pending_classification([{"id": "a", "title": "One"},
                                   {"id": "b", "title": "Two"}], f)
    import json as j
    rows = j.loads(f.read_text(encoding="utf-8"))
    assert [r["id"] for r in rows] == ["a", "b"]
    assert all("flagged_at" in r for r in rows)


def test_pending_classification_survives_corrupt_file(tmp_path):
    from tools.granola_auto_debrief import record_pending_classification
    f = tmp_path / "pending.json"
    f.write_text("{not json", encoding="utf-8")
    record_pending_classification([{"id": "a", "title": "One"}], f)
    import json as j
    assert [r["id"] for r in j.loads(f.read_text(encoding="utf-8"))] == ["a"]


def test_meeting_id_dedup_across_different_filenames(tmp_path):
    from tools.granola_save import find_existing_by_meeting_id
    (tmp_path / "2026-08-06-1434-long-descriptive-slug.md").write_text(
        "# x\n\n**Granola meeting ID:** `abc-123`\n", encoding="utf-8")
    (tmp_path / "2026-08-06-1434-long-descriptive-slug-summary.md").write_text(
        "**Granola meeting ID:** `abc-123`\n", encoding="utf-8")
    hit = find_existing_by_meeting_id(tmp_path, "abc-123")
    assert hit is not None and not hit.name.endswith("-summary.md")
    assert find_existing_by_meeting_id(tmp_path, "other-id") is None
    assert find_existing_by_meeting_id(tmp_path, "") is None


def test_therapist_first_name_as_passing_mention_does_not_seal():
    """2026-08-07 regression: a therapist's first name appearing as an ordinary
    third party in a business transcript sealed the whole call into the therapy
    vault. First-name evidence now requires a speaker-label position."""
    cfg = {"attendees": set(), "names": ["Robin Therapist"]}
    m = {"title": "side project working session", "attendees": [NICK],
         "transcript": "Speaker B: Robin wants to teach every Friday. Speaker A: nice."}
    assert classify_meeting(m, cfg, TITLE_RULE) == "personal"


def test_therapist_first_name_at_speaker_position_still_seals():
    cfg = {"attendees": set(), "names": ["Robin Therapist"]}
    m = {"title": "reflections", "attendees": [NICK],
         "transcript": "Me: it was a hard week.\nRobin: tell me more about that."}
    assert classify_meeting(m, cfg, TITLE_RULE) == "therapy"


def test_therapist_full_name_anywhere_still_seals():
    cfg = {"attendees": set(), "names": ["Robin Therapist"]}
    m = {"title": "side project working session", "attendees": [NICK],
         "transcript": "Speaker A: notes from my talk with Robin Therapist today."}
    assert classify_meeting(m, cfg, TITLE_RULE) == "therapy"


def test_vault_guard_names_the_vault_not_its_data_dir(tmp_path, monkeypatch, capsys):
    """The guard must report the vault root. Parent-walking got this wrong for
    personal calls (<vault>/data/voice-corpus/granola is one level deeper than
    <vault>/data/therapy), naming <vault>/data instead."""
    import tools.granola_auto_debrief as g
    missing = tmp_path / "not-mounted-vault"
    res = g.persist_via_granola_save(
        {"id": "not_x", "title": "Sync", "created_at": "2026-08-06T21:34:00Z", "transcript": "hi"},
        "summary", "", "personal", "side-project", missing)
    assert res["status"] == "error" and res["reason"] == "vault-missing"
    assert res["expected_vault"] == str(missing)
    assert "data" not in Path(res["expected_vault"]).name
