"""Tests for tools/granola_save.py."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "granola_save.py"


# --- capture-time attribution warning (2026-08-25) --------------------------
# Granola emits Me/Them only when system audio and the mic are on separate channels. On a
# speakerphone call every voice lands on the mic and the whole conversation is attributed to
# the owner. A 2026-06-24 call sat in the corpus for two months that way and was ranked 4th
# on a filler table before anyone noticed. Warn at capture, where the setup is still fixable.

def _write(tmp_path, transcript):
    import subprocess, sys, json as _json
    payload = {"meeting_id": "x", "title": "t", "captured": "2026-08-25 10:00",
               "transcript": transcript, "summary": "s", "type": "recruiter",
               "session_desc": "d"}
    inp = tmp_path / "in.json"
    inp.write_text(_json.dumps(payload), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "write", "--output", str(tmp_path / "o.md"),
         "--input", str(inp)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return _json.loads(r.stdout)


def test_collapsed_channel_warns_at_capture(tmp_path):
    out = _write(tmp_path, "Me: " + "word " * 200)
    assert "attribution_warning" in out
    assert "NOT SEPARATED" in out["attribution_warning"]


def test_healthy_two_speaker_capture_does_not_warn(tmp_path):
    out = _write(tmp_path, "Me: " + "word " * 200 + " Them: " + "reply " * 150)
    assert "attribution_warning" not in out


def test_unlabelled_transcript_warns_about_zero_turns(tmp_path):
    out = _write(tmp_path, "no speaker labels at all " * 50)
    assert "attribution_warning" in out
    assert "zero turns" in out["attribution_warning"]


def test_warning_never_blocks_the_write(tmp_path):
    """The warning is advisory. A collapsed transcript is still valuable CONTENT and must
    still be persisted -- refusing to save it would lose the call to protect a metric."""
    out = _write(tmp_path, "Me: " + "word " * 200)
    assert out["status"] == "ok"
    assert (tmp_path / "o.md").exists()


def test_transcript_with_labels_but_no_words_does_not_crash(tmp_path):
    """Pins the `if not total` guard in attribution_warning.

    Labels present, zero words behind them: a real shape when a call connects and nothing is
    said. Without the guard this divides by zero and takes down the whole save, losing the
    transcript to protect a metric -- the exact inversion of what the warning is for."""
    out = _write(tmp_path, "Me:  Them:  Me:  Them: ")
    assert out["status"] == "ok"
    assert (tmp_path / "o.md").exists()



def test_named_channel_capture_does_not_warn(tmp_path):
    """`System audio (Name):` is a separated channel, not a collapsed one. Before
    2026-10-01 this label was unread and the tool reported the counterpart at 0.0% of
    the words on a transcript with two clearly labelled channels."""
    transcript = ("Microphone: " + "word " * 200 +
                  "\n\nSystem audio (Jane Doe): " + "reply " * 150)
    out = _write(tmp_path, transcript)
    assert "attribution_warning" not in out


# --- the sealed summary boundary (2026-11-02, a mutation survivor on a renamed line) ---

def test_only_a_sealed_summary_carries_the_sealed_boundary():
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import granola_save as gs
    base = {"captured": "2026-11-01T10:00", "session_desc": "x", "title": "t", "summary": "s",
            "meeting_id": "not_x"}
    sealed = gs.summary_body({**base, "type": "sealed"}, "t-transcript")
    general = gs.summary_body({**base, "type": "general"}, "t-transcript")
    assert sealed.startswith("> **SEALED.**") and "[[t-transcript]]" not in sealed.split("\n")[0]
    assert not general.startswith("> **SEALED.**") and "[[t-transcript]]" in general


# --- review round 1 (Codex g2a F1 P0, F3; Fable 8.F7/9.F7/10.F8), 2026-11-02 ---------------
# A sealed transcript must never land inside the repo (a public repo, backed up elsewhere),
# whatever --output says; and a type the tool does not know refuses before any directory is
# made.

sys.path.insert(0, str(REPO_ROOT / "tools"))
import granola_save as gs  # noqa: E402


def test_a_sealed_output_inside_the_repo_is_refused(tmp_path):
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    assert gs.output_refusal(repo / "data" / "t.md", "sealed", repo)
    assert gs.output_refusal(repo / "a" / ".." / "t.md", "sealed", repo)
    link = tmp_path / "into-repo"
    link.symlink_to(repo / "data")
    assert gs.output_refusal(link / "t.md", "sealed", repo)      # followed through a symlink
    sealed = tmp_path / "vault" / "sealed"
    sealed.mkdir(parents=True)
    assert gs.output_refusal(sealed / "t.md", "sealed", repo, sealed_folder=sealed) is None


def test_a_sealed_output_outside_the_sealed_folder_is_refused(tmp_path, monkeypatch):
    # Strict (Codex r2 F1, Fable 18.F3, 19.F5; Nick 2026-11-02): only the sealed folder.
    repo, sealed = tmp_path / "repo", tmp_path / "vault" / "sealed"
    repo.mkdir()
    sealed.mkdir(parents=True)
    assert "only be written inside the sealed folder" in gs.output_refusal(
        tmp_path / "vault" / "elsewhere" / "t.md", "sealed", repo, sealed_folder=sealed)
    def unconfigured():
        raise gs.vault_paths.SealedFolderMissing()
    monkeypatch.setattr(gs.vault_paths, "sealed_dir", unconfigured)   # not the real config
    assert "no sealed folder is configured" in gs.output_refusal(
        tmp_path / "anywhere" / "t.md", "sealed", repo)        # default: from the config


def test_only_sealed_is_held_outside_the_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for type_ in ("recruiter", "networking", "general"):
        assert gs.output_refusal(repo / "data" / "t.md", type_, repo) is None, type_


def test_an_unknown_type_is_refused(tmp_path):
    assert "retired-type" in gs.output_refusal(tmp_path / "t.md", "retired-type", tmp_path)


def _write_raw(tmp_path, type_, output):
    import subprocess, json as _json
    payload = {"meeting_id": "x", "title": "t", "captured": "2026-08-25 10:00",
               "transcript": "Me: hi Them: hello", "summary": "s", "type": type_,
               "session_desc": "d"}
    inp = tmp_path / "in.json"
    inp.write_text(_json.dumps(payload), encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), "write", "--output", str(output),
                           "--input", str(inp)], capture_output=True, text=True)


def test_write_refuses_a_sealed_payload_aimed_into_the_repo_before_making_anything():
    import shutil, tempfile
    target = REPO_ROOT / "output" / "zz-sealed-refusal-test" / "t.md"
    try:
        r = _write_raw(Path(tempfile.mkdtemp()), "sealed", target)
        assert r.returncode == 2
        assert not target.parent.exists()
    finally:                       # a regression must not leave a sealed-shaped file behind
        shutil.rmtree(target.parent, ignore_errors=True)


def test_write_refuses_a_stale_type_before_making_its_directory(tmp_path):
    target = tmp_path / "new-dir" / "t.md"
    r = _write_raw(tmp_path, "retired-type", target)
    assert r.returncode == 2 and "retired-type" in r.stdout
    assert not target.parent.exists()


def test_a_sealed_folder_configured_inside_the_repo_is_still_refused(tmp_path):
    """The repo check comes before the sealed-folder check, so a sealed folder set by mistake
    to a path inside the repo cannot route a transcript into it (q22 survivor, 2026-11-03)."""
    repo = tmp_path / "repo"
    inside = repo / "data" / "sealed"
    inside.mkdir(parents=True)
    refusal = gs.output_refusal(inside / "t.md", "sealed", repo, sealed_folder=inside)
    assert refusal and "inside the repo" in refusal


def test_a_final_symlink_inside_the_repo_aimed_at_the_sealed_folder_is_refused(tmp_path):
    """Codex r7 g1c/g3a F1 (P0): os.replace swaps a file in AT the link, so the write lands
    where the link sits (inside the repo), not where it points. Judge the resolved parent
    plus the name, which is where the write actually lands; the summary shares that parent."""
    repo, sealed = tmp_path / "repo", tmp_path / "vault" / "sealed"
    (repo / "data").mkdir(parents=True)
    sealed.mkdir(parents=True)
    (sealed / "t.md").write_text("old", encoding="utf-8")
    link = repo / "data" / "t.md"
    link.symlink_to(sealed / "t.md")
    refusal = gs.output_refusal(link, "sealed", repo, sealed_folder=sealed)
    assert refusal and "inside the repo" in refusal
    dangling = repo / "data" / "new.md"
    dangling.symlink_to(sealed / "new.md")             # a link to a file not yet written
    assert gs.output_refusal(dangling, "sealed", repo, sealed_folder=sealed)


def test_a_link_inside_the_sealed_folder_is_judged_by_where_it_sits(tmp_path):
    """The mirror case: a link in the sealed folder pointing into the repo is replaced in
    place, so the transcript stays in the sealed folder and the write is allowed."""
    repo, sealed = tmp_path / "repo", tmp_path / "vault" / "sealed"
    (repo / "data").mkdir(parents=True)
    sealed.mkdir(parents=True)
    (repo / "data" / "x.md").write_text("x", encoding="utf-8")
    link = sealed / "t.md"
    link.symlink_to(repo / "data" / "x.md")
    assert gs.output_refusal(link, "sealed", repo, sealed_folder=sealed) is None


def test_a_sealed_output_with_no_file_name_is_refused(tmp_path):
    sealed = tmp_path / "vault" / "sealed"
    (sealed / "sub").mkdir(parents=True)
    assert "needs a file name" in gs.output_refusal(sealed / "sub" / "..", "sealed",
                                                    tmp_path / "repo", sealed_folder=sealed)
    # pathlib folds `sealed/.` into `sealed` itself, which the folder check already refuses.
    assert gs.output_refusal(sealed / ".", "sealed", tmp_path / "repo", sealed_folder=sealed)
