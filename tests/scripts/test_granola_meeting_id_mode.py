"""Tests for the targeted `--meeting-id` pull mode in tools/granola_auto_debrief.py.

Why the mode exists: a skill that already knows which meeting it wants used to pull
the whole transcript into an LLM's context and have the model re-emit the text into
a payload file. That is both a large token cost and a corruption risk in the one
artifact whose job is verbatim fidelity. `--meeting-id` does the whole pull ->
classify -> persist round trip in one Bash call, with the text never touching a
model.

Invariants under test:
  - happy path persists via granola_save and prints a JSON result
  - --dry-run resolves the destination but writes nothing
  - --force-type / --project override classification and project matching
  - an MCP-shaped UUID produces a clear, actionable error (REST ids are not_<b62>)
  - a persist error yields a non-zero exit

The network helpers are monkeypatched throughout — these tests never touch the
live Granola API. All names/emails here are placeholders; this repo is public.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools import granola_auto_debrief as gad

NOT_ID = "not_EXAMPLEabc123"
UUID_ID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"

NICK = {"name": "Nick Magnuson", "email": "nick@example.com"}
EXTERNAL = {"name": "Robin Placeholder", "email": "robin@example.com"}

SEGMENTS = [
    {"speaker": "Me", "text": "thanks for making time."},
    {"speaker": {"diarization_label": "Speaker A"}, "text": "happy to."},
]


def _stub_fetch(monkeypatch, *, transcript=SEGMENTS, note=None):
    """Stub both REST helpers as they are bound inside granola_auto_debrief."""
    note = note if note is not None else {
        "id": NOT_ID,
        "title": "Intro chat",
        "created_at": "2026-08-06T21:34:00Z",
        "summary_markdown": "## Summary\n\n- talked shop",
        "attendees": [NICK, EXTERNAL],
    }
    monkeypatch.setattr(gad, "fetch_transcript", lambda mid: transcript)
    monkeypatch.setattr(gad, "fetch_note_full", lambda mid: note)
    return note


def _stub_configs(monkeypatch, pconfig=None):
    """Neutralize the gitignored allowlists so tests are hermetic."""
    monkeypatch.setattr(gad, "load_therapy_classifier_config",
                        lambda *a, **k: {"attendees": set(), "names": []})
    monkeypatch.setattr(gad, "load_personal_projects_config",
                        lambda *a, **k: pconfig or {"rules": []})


# --- happy path -----------------------------------------------------------

def test_happy_path_persists_and_returns_json_shape(monkeypatch):
    _stub_fetch(monkeypatch)
    _stub_configs(monkeypatch)

    captured = {}

    def fake_persist(meeting, summary, private_notes, meeting_type, project=None, vault_root=None):
        captured.update(meeting=meeting, summary=summary, meeting_type=meeting_type,
                        project=project)
        return {"status": "ok",
                "transcript_path": "/vault/data/voice-corpus/granola/2026-08-06-1434-intro-chat.md",
                "summary_path": "/vault/data/voice-corpus/granola/2026-08-06-1434-intro-chat-summary.md"}

    monkeypatch.setattr(gad, "persist_via_granola_save", fake_persist)

    out = gad.pull_single_meeting(NOT_ID)

    assert out["status"] == "ok"
    assert out["meeting_id"] == NOT_ID
    assert out["type"] == "networking"
    assert out["project"] is None
    assert out["transcript_path"].endswith("intro-chat.md")
    assert out["summary_path"].endswith("intro-chat-summary.md")
    # The transcript reached persist verbatim as segments — no model in the loop.
    assert captured["meeting"]["transcript"] == SEGMENTS
    assert captured["summary"] == "## Summary\n\n- talked shop"
    assert captured["meeting"]["title"] == "Intro chat"
    # Result is JSON-serializable (the CLI prints it).
    json.dumps(out)


def test_persist_skip_is_reported_and_treated_as_success(monkeypatch):
    """Idempotent re-run: granola_save skips, we surface the existing path."""
    _stub_fetch(monkeypatch)
    _stub_configs(monkeypatch)
    monkeypatch.setattr(gad, "persist_via_granola_save",
                        lambda *a, **k: {"status": "skip", "reason": "meeting-id-exists",
                                         "existing_path": "/vault/already-there.md"})
    out = gad.pull_single_meeting(NOT_ID)
    assert out["status"] == "skip"
    assert out["transcript_path"] == "/vault/already-there.md"


# --- no inbox, no cursor --------------------------------------------------

def test_targeted_pull_never_writes_an_inbox_or_moves_the_cursor(monkeypatch):
    """The two collector side effects this mode must not have."""
    _stub_fetch(monkeypatch)
    _stub_configs(monkeypatch)
    monkeypatch.setattr(gad, "persist_via_granola_save",
                        lambda *a, **k: {"status": "ok", "transcript_path": "/t.md",
                                         "summary_path": "/s.md"})

    def explode(*a, **k):
        raise AssertionError("targeted pull must not touch this")

    monkeypatch.setattr(gad, "prepend_inbox_entries", explode)
    monkeypatch.setattr(gad, "fetch_new_since_last_run", explode)
    monkeypatch.setattr(gad, "record_pending_classification", explode)

    assert gad.pull_single_meeting(NOT_ID)["status"] == "ok"


# --- dry run --------------------------------------------------------------

def test_dry_run_writes_nothing_and_reports_destination(monkeypatch, tmp_path):
    _stub_fetch(monkeypatch)
    _stub_configs(monkeypatch)
    monkeypatch.setattr(gad, "VOICE_CORPUS_DIR", tmp_path / "voice-corpus")
    monkeypatch.setattr(gad, "persist_via_granola_save",
                        lambda *a, **k: pytest.fail("dry-run must not persist"))

    out = gad.pull_single_meeting(NOT_ID, dry_run=True)

    assert out["status"] == "dry-run"
    assert out["transcript_path"].endswith("-intro-chat.md")
    assert out["summary_path"].endswith("-intro-chat-summary.md")
    assert not (tmp_path / "voice-corpus").exists()


# --- overrides ------------------------------------------------------------

def test_force_type_overrides_classification(monkeypatch, tmp_path):
    """A call the classifier would call 'networking' can be forced personal."""
    _stub_fetch(monkeypatch)
    _stub_configs(monkeypatch)
    monkeypatch.setattr(gad, "PERSONAL_VAULT", tmp_path / "personal")
    monkeypatch.setattr(gad, "classify_meeting",
                        lambda *a, **k: pytest.fail("force-type must skip classification"))

    seen = {}

    def fake_persist(meeting, summary, notes, meeting_type, project=None, vault_root=None):
        seen.update(type=meeting_type, project=project, vault=vault_root)
        return {"status": "ok", "transcript_path": "/t.md", "summary_path": "/s.md"}

    monkeypatch.setattr(gad, "persist_via_granola_save", fake_persist)

    out = gad.pull_single_meeting(NOT_ID, force_type="personal", project="side-project")
    assert out["type"] == "personal"
    assert out["project"] == "side-project"
    assert seen["type"] == "personal"
    assert seen["project"] == "side-project"


def test_project_override_beats_the_allowlist_match(monkeypatch):
    pconfig = {"rules": [{"project": "matched-by-config", "attendees": {"robin@example.com"},
                          "names": [], "titles": [], "vault": None}]}
    _stub_fetch(monkeypatch)
    _stub_configs(monkeypatch, pconfig)
    monkeypatch.setattr(gad, "persist_via_granola_save",
                        lambda *a, **k: {"status": "ok", "transcript_path": "/t.md",
                                         "summary_path": "/s.md"})

    auto = gad.pull_single_meeting(NOT_ID)
    assert auto["type"] == "personal" and auto["project"] == "matched-by-config"

    forced = gad.pull_single_meeting(NOT_ID, project="explicit-slug")
    assert forced["project"] == "explicit-slug"


def test_unknown_classification_fails_closed_with_actionable_message(monkeypatch):
    _stub_fetch(monkeypatch, note={"id": NOT_ID, "title": "Untitled",
                                   "created_at": "2026-08-06T21:34:00Z", "attendees": []})
    _stub_configs(monkeypatch)
    monkeypatch.setattr(gad, "persist_via_granola_save",
                        lambda *a, **k: pytest.fail("unknown must not persist"))

    out = gad.pull_single_meeting(NOT_ID)
    assert out["status"] == "error"
    assert out["reason"] == "unclassified"
    assert "--force-type" in out["message"]


# --- id shape -------------------------------------------------------------

def test_uuid_shaped_id_is_rejected_with_a_clear_error(monkeypatch):
    monkeypatch.setattr(gad, "fetch_transcript",
                        lambda mid: pytest.fail("must not hit the API with a UUID"))
    out = gad.pull_single_meeting(UUID_ID)
    assert out["status"] == "error"
    assert out["reason"] == "uuid-shaped-id"
    assert "not_" in out["message"]


def test_empty_meeting_id_is_rejected():
    assert gad.pull_single_meeting("")["reason"] == "missing-meeting-id"


def test_missing_transcript_is_an_error_not_a_silent_empty_save(monkeypatch):
    _stub_fetch(monkeypatch, transcript=[])
    _stub_configs(monkeypatch)
    monkeypatch.setattr(gad, "persist_via_granola_save",
                        lambda *a, **k: pytest.fail("must not persist an empty transcript"))
    out = gad.pull_single_meeting(NOT_ID)
    assert out["status"] == "error" and out["reason"] == "no-transcript"


def test_note_fetch_failure_is_an_error(monkeypatch):
    monkeypatch.setattr(gad, "fetch_transcript", lambda mid: SEGMENTS)

    def boom(mid):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(gad, "fetch_note_full", boom)
    out = gad.pull_single_meeting(NOT_ID)
    assert out["status"] == "error" and out["reason"] == "note-fetch-failed"


# --- destination resolution parity ---------------------------------------

def test_resolve_destination_matches_what_persist_would_use(monkeypatch, tmp_path):
    """dry-run must not report a different path than the real run writes."""
    monkeypatch.setattr(gad, "VOICE_CORPUS_DIR", tmp_path / "vc")
    meeting = {"id": NOT_ID, "title": "Intro chat", "created_at": "2026-08-06T21:34:00Z"}
    dest = gad.resolve_destination(meeting, "networking")
    assert dest["output_path"].parent == tmp_path / "vc"
    assert dest["output_path"].name.endswith("-intro-chat.md")
    assert dest["save_type"] == "networking"


def test_personal_destination_maps_to_general_save_type(tmp_path):
    meeting = {"id": NOT_ID, "title": "Workshop", "created_at": "2026-08-06T21:34:00Z"}
    dest = gad.resolve_destination(meeting, "personal", "side-project", tmp_path / "vault")
    assert dest["save_type"] == "general"
    assert dest["output_path"].parent == tmp_path / "vault" / "data/voice-corpus/granola"
    assert "side-project" in dest["session_desc"]


# --- CLI wiring / exit codes ---------------------------------------------

def _run_cli(args, stub_module: str):
    """Run the CLI in a subprocess with the network helpers stubbed via sitecustomize-ish
    injection: we exec a small driver that patches the module then calls main()."""
    driver = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from tools import granola_auto_debrief as gad\n"
        f"{stub_module}\n"
        f"sys.argv = ['granola_auto_debrief.py'] + {args!r}\n"
        "gad.main()\n"
    )
    return subprocess.run([sys.executable, "-c", driver], capture_output=True, text=True,
                          env={"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"})


CLI_STUBS_OK = (
    "gad.fetch_transcript = lambda mid: [{'speaker': 'Me', 'text': 'hi'}]\n"
    "gad.fetch_note_full = lambda mid: {'id': mid, 'title': 'Intro chat',"
    " 'created_at': '2026-08-06T21:34:00Z', 'summary_markdown': 's',"
    " 'attendees': [{'name': 'Robin Placeholder', 'email': 'robin@example.com'}]}\n"
    "gad.load_therapy_classifier_config = lambda *a, **k: {'attendees': set(), 'names': []}\n"
    "gad.load_personal_projects_config = lambda *a, **k: {'rules': []}\n"
)


def test_cli_dry_run_exits_zero_and_prints_json():
    r = _run_cli(["--meeting-id", NOT_ID, "--dry-run"], CLI_STUBS_OK)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["status"] == "dry-run"
    assert payload["meeting_id"] == NOT_ID
    assert payload["type"] == "networking"


def test_cli_persist_error_exits_non_zero():
    stubs = CLI_STUBS_OK + (
        "gad.persist_via_granola_save = lambda *a, **k: {'status': 'error',"
        " 'reason': 'vault-missing'}\n"
    )
    r = _run_cli(["--meeting-id", NOT_ID], stubs)
    assert r.returncode != 0
    assert json.loads(r.stdout)["status"] == "error"


def test_cli_uuid_id_exits_non_zero():
    r = _run_cli(["--meeting-id", UUID_ID], CLI_STUBS_OK)
    assert r.returncode != 0
    assert json.loads(r.stdout)["reason"] == "uuid-shaped-id"


def test_cli_without_meeting_id_still_runs_the_collector_path():
    """Regression guard: adding the flag must not hijack the default cron mode."""
    stubs = ("gad.fetch_new_since_last_run = lambda *a, **k: []\n")
    r = _run_cli([], stubs)
    assert r.returncode == 0
    assert "No new calls found" in r.stderr
