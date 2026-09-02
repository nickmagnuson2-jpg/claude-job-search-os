"""#personal captures route to the personal-OS vault, never into the job-search repo.

Built 2026-08-19 (todo #10). Before this, a personal note had no route of its own and
landed in the job-search inbox.

THE PROPERTY THAT MATTERS is not "it reaches the vault" but "it reaches the vault AND
NOTHING ELSE." Classification is multi-destination by design — one note can legitimately
write to networking.md and a company note at once — so without an explicit short-circuit
a personal note mentioning a known contact or company would be filed in the vault *and*
copied into the job-search repo. "Route it elsewhere" and "also keep a copy here" are
different requests, and only the first was made.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import remember_classify as rc  # noqa: E402


def _classify(note: str) -> dict:
    r = subprocess.run(
        [sys.executable, str(TOOLS / "remember_classify.py"),
         "--note", note, "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# --- tag detection ----------------------------------------------------------

@pytest.mark.parametrize("note", [
    "#personal call the dentist",
    "call the dentist #personal",
    "mid #personal sentence",
    "#PERSONAL shouting",
])
def test_tag_is_detected(note):
    assert rc.is_personal_capture(note)


@pytest.mark.parametrize("note", [
    "a normal job search note",
    "##personal is a heading, not a tag",
    "see http://x.com/page#personal — a URL fragment",
    "personal without the hash",
    "impersonal note",
])
def test_non_tags_are_not_detected(note):
    assert not rc.is_personal_capture(note)


# --- routing ----------------------------------------------------------------

def test_personal_note_routes_only_to_the_vault():
    d = _classify("#personal book the vet appointment")
    assert [x["type"] for x in d["destinations"]] == ["personal_capture"]


def test_personal_short_circuits_even_with_job_search_signals():
    """THE regression test. Decision keywords would normally produce a `decision`
    destination writing into data/decisions.md; the tag must suppress that."""
    d = _classify("#personal decided to prioritize family time over evening work")
    types = [x["type"] for x in d["destinations"]]
    assert types == ["personal_capture"], f"leaked into job-search destinations: {types}"


def test_untagged_note_still_routes_normally():
    """The short-circuit must not swallow ordinary captures."""
    d = _classify("decided to prioritize climate roles")
    assert "personal_capture" not in [x["type"] for x in d["destinations"]]
    assert d["destinations"], "normal routing must still produce a destination"


def test_destination_never_names_the_private_vault_path():
    """The JSON is echoed into the session transcript; the real path must stay out."""
    d = _classify("#personal something")
    blob = json.dumps(d)
    assert "<personal-vault>" in blob
    assert "Obsidian" not in blob and "/Users/" not in blob


# --- apply side -------------------------------------------------------------

def test_apply_writes_nothing_into_the_repo(tmp_path):
    """Dry-run proves the only would-mutate target is the vault placeholder."""
    dest = json.dumps([{"type": "personal_capture",
                        "file": "<personal-vault>/data/inbox.md", "entity": None}])
    r = subprocess.run(
        [sys.executable, str(TOOLS / "remember_apply.py"), "--note", "#personal x",
         "--destinations", dest, "--repo-root", str(tmp_path), "--dry-run"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["would_mutate"] == ["<personal-vault>/data/inbox.md"]


def test_apply_fails_loudly_when_vault_unconfigured(tmp_path, monkeypatch):
    """No silent fallback: writing personal content into the job-search repo is the
    exact outcome this routing exists to prevent."""
    sys.path.insert(0, str(TOOLS))
    import remember_apply as ra
    import vault_paths as vp
    monkeypatch.delenv(vp.ENV_VAR, raising=False)
    monkeypatch.setattr(vp, "DEFAULT_CONFIG", tmp_path / "absent.conf")
    res = ra.apply_personal_capture("#personal x", {}, tmp_path)
    assert res["status"] == "error"
    assert res["code"] == "vault_unconfigured"
    assert not list(tmp_path.rglob("*.md")), "nothing may be written on the failure path"
