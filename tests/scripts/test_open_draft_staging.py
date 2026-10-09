"""Tests for the attachment staging step in tools/open_draft.py.

WHAT THIS PROTECTS. Generated CVs all carry the same kind of filename, so the file for
this draft and the file from an earlier one look alike in a file picker. Staging exists so
that the folder a person drags from holds the files for the current draft and nothing
else. That needs three things: earlier files are gone, the right bytes are there, and a
folder this tool does not own is never emptied.
"""
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import open_draft as od  # noqa: E402


@pytest.fixture
def cv(tmp_path):
    """Two different CVs with the same filename, as on a day with two applications."""
    first = tmp_path / "northwind" / "010126-example.pdf"
    second = tmp_path / "beacon" / "010126-example.pdf"
    for path, content in ((first, b"northwind cv"), (second, b"beacon cv, longer")):
        path.parent.mkdir()
        path.write_bytes(content)
    return first, second


def stage(paths, directory):
    revealed = []
    staged, problems = od.stage_attachments([str(p) for p in paths], str(directory),
                                            reveal=revealed.append)
    return staged, problems, revealed


# --- the folder holds this draft's file and nothing else --------------------------

def test_the_staged_copy_has_the_bytes_of_the_file_the_draft_named(tmp_path, cv):
    first, _ = cv
    staged, problems, _ = stage([first], tmp_path / "to-send")
    assert problems == []
    assert [Path(p).read_bytes() for p in staged] == [b"northwind cv"]


def test_a_second_draft_replaces_the_first_drafts_file(tmp_path, cv):
    first, second = cv
    folder = tmp_path / "to-send"
    stage([first], folder)
    stage([second], folder)
    files = [p for p in folder.iterdir() if p.name != od.STAGING_MARKER]
    assert len(files) == 1
    assert files[0].read_bytes() == b"beacon cv, longer"


def test_a_file_left_by_an_earlier_draft_under_another_name_is_removed(tmp_path, cv):
    first, _ = cv
    folder = tmp_path / "to-send"
    stage([first], folder)
    (folder / "leftover.pdf").write_bytes(b"old")
    other = tmp_path / "other.pdf"
    other.write_bytes(b"new")
    stage([other], folder)
    assert sorted(p.name for p in folder.iterdir() if p.name != od.STAGING_MARKER) == ["other.pdf"]


def test_two_attachments_with_the_same_name_are_both_staged(tmp_path, cv):
    first, second = cv
    folder = tmp_path / "to-send"
    staged, problems, _ = stage([first, second], folder)
    assert problems == []
    assert sorted(Path(p).read_bytes() for p in staged) == [b"beacon cv, longer", b"northwind cv"]


def test_finder_is_pointed_at_the_staged_copy_not_the_source(tmp_path, cv):
    first, _ = cv
    folder = tmp_path / "to-send"
    staged, _, revealed = stage([first], folder)
    assert revealed == [staged[0]]
    assert Path(revealed[0]).parent == folder


# --- a folder this tool does not own is never emptied -----------------------------

def test_an_existing_folder_with_files_and_no_marker_is_left_alone(tmp_path, cv):
    first, _ = cv
    folder = tmp_path / "documents"
    folder.mkdir()
    (folder / "taxes.pdf").write_bytes(b"keep me")
    staged, problems, revealed = stage([first], folder)
    assert staged == [] and revealed == []
    assert len(problems) == 1 and "nothing was staged" in problems[0]
    assert sorted(p.name for p in folder.iterdir()) == ["taxes.pdf"]
    assert (folder / "taxes.pdf").read_bytes() == b"keep me"


def test_an_existing_empty_folder_is_adopted(tmp_path, cv):
    first, _ = cv
    folder = tmp_path / "to-send"
    folder.mkdir()
    staged, problems, _ = stage([first], folder)
    assert problems == [] and len(staged) == 1
    assert "open_draft.py" in (folder / od.STAGING_MARKER).read_text(encoding="utf-8")


def test_a_new_folder_gets_a_marker_saying_what_empties_it(tmp_path, cv):
    first, _ = cv
    folder = tmp_path / "to-send"
    stage([first], folder)
    assert "open_draft.py" in (folder / od.STAGING_MARKER).read_text(encoding="utf-8")


def test_a_subfolder_inside_the_staging_folder_is_not_deleted(tmp_path, cv):
    first, _ = cv
    folder = tmp_path / "to-send"
    stage([first], folder)
    (folder / "keep").mkdir()
    (folder / "keep" / "note.txt").write_text("x", encoding="utf-8")
    stage([first], folder)
    assert (folder / "keep" / "note.txt").read_text(encoding="utf-8") == "x"


# --- a bad attachment is reported, and the draft still goes ahead -------------------

def test_a_missing_attachment_is_reported_and_nothing_is_revealed(tmp_path):
    folder = tmp_path / "to-send"
    staged, problems, revealed = stage([tmp_path / "absent.pdf"], folder)
    assert staged == [] and revealed == []
    assert len(problems) == 1
    assert problems[0].startswith("file not found, not staged:") and "absent.pdf" in problems[0]


def test_a_missing_attachment_still_clears_the_previous_drafts_file(tmp_path, cv):
    first, _ = cv
    folder = tmp_path / "to-send"
    stage([first], folder)
    stage([tmp_path / "absent.pdf"], folder)
    assert [p.name for p in folder.iterdir() if p.name != od.STAGING_MARKER] == []


# --- where the folder is ----------------------------------------------------------

def test_the_override_decides_the_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_DRAFT_STAGING_DIR", str(tmp_path / "elsewhere"))
    assert od.staging_dir() == str(tmp_path / "elsewhere")


def test_without_an_override_the_folder_is_under_documents(monkeypatch):
    monkeypatch.delenv("OPEN_DRAFT_STAGING_DIR", raising=False)
    assert od.staging_dir() == str(Path.home() / "Documents" / "to-send")


@pytest.fixture
def quiet(tmp_path, monkeypatch):
    """Send staging to a throwaway folder and record what Finder would be pointed at."""
    folder = tmp_path / "to-send"
    revealed = []
    monkeypatch.setenv("OPEN_DRAFT_STAGING_DIR", str(folder))
    monkeypatch.setattr(od, "reveal_in_finder", revealed.append)
    return folder, revealed


def test_a_folder_that_cannot_be_created_is_reported_not_raised(tmp_path, cv):
    first, _ = cv
    blocker = tmp_path / "to-send"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    staged, problems, revealed = stage([first], blocker)
    assert staged == [] and revealed == []
    assert len(problems) == 1 and problems[0].startswith("staging failed (")


# --- what the person is told --------------------------------------------------------

def test_report_staging_names_the_folder_the_file_and_its_size(cv, quiet, capsys):
    first, _ = cv
    folder, revealed = quiet
    staged = od.report_staging([str(first)])
    out = capsys.readouterr().out
    assert [Path(p).parent for p in staged] == [folder]
    assert revealed == staged
    assert f"Attachment staged. {folder} now holds only:" in out
    assert "010126-example.pdf  (12 bytes)" in out
    assert "Drag from there." in out


def test_report_staging_warns_when_the_file_is_missing(tmp_path, quiet, capsys):
    staged = od.report_staging([str(tmp_path / "absent.pdf")])
    out = capsys.readouterr().out
    assert staged == []
    assert "WARNING: file not found, not staged:" in out
    assert "Attachment staged" not in out


# --- the draft flows call it ---------------------------------------------------------

def test_a_reply_with_an_attachment_stages_it(cv, quiet, monkeypatch, capsys):
    first, _ = cv
    folder, revealed = quiet
    monkeypatch.setattr(od, "copy_text_to_clipboard", lambda text: True)
    od.handle_reply("casey@example.com", "Re: Following up", "Hi Casey", [str(first)])
    assert (folder / "010126-example.pdf").read_bytes() == b"northwind cv"
    assert revealed == [str(folder / "010126-example.pdf")]


def test_a_reply_with_no_attachment_leaves_the_staging_folder_untouched(cv, quiet, monkeypatch, capsys):
    first, _ = cv
    folder, _ = quiet
    od.stage_attachments([str(first)], str(folder), reveal=lambda path: None)
    monkeypatch.setattr(od, "copy_text_to_clipboard", lambda text: True)
    od.handle_reply("casey@example.com", "Re: Following up", "Hi Casey", [])
    assert (folder / "010126-example.pdf").exists()


def test_a_new_message_with_an_attachment_stages_it(tmp_path, cv, quiet, monkeypatch, capsys):
    first, _ = cv
    folder, _ = quiet
    draft = tmp_path / "pending.txt"
    draft.write_text(f"TO: casey@example.com\nSUBJECT: Following up\nATTACH: {first}\n"
                     f"BODY:\nHi Casey\n", encoding="utf-8")
    opened = []
    monkeypatch.setattr(od, "DRAFT_FILE", str(draft))
    monkeypatch.setattr(od, "copy_file_to_clipboard", lambda path: (True, path))
    monkeypatch.setattr(od.webbrowser, "open", opened.append)
    monkeypatch.setattr(od, "record_draft", lambda: None)
    monkeypatch.setattr(od, "consume_draft", lambda: None)
    od.main()
    assert len(opened) == 1
    assert (folder / "010126-example.pdf").read_bytes() == b"northwind cv"


def test_a_new_message_with_no_attachment_does_not_create_the_staging_folder(tmp_path, quiet, monkeypatch, capsys):
    folder, _ = quiet
    draft = tmp_path / "pending.txt"
    draft.write_text("TO: casey@example.com\nSUBJECT: Following up\nBODY:\nHi Casey\n",
                     encoding="utf-8")
    monkeypatch.setattr(od, "DRAFT_FILE", str(draft))
    monkeypatch.setattr(od.webbrowser, "open", lambda url: None)
    monkeypatch.setattr(od, "record_draft", lambda: None)
    monkeypatch.setattr(od, "consume_draft", lambda: None)
    od.main()
    assert not folder.exists()


def test_finder_is_asked_to_select_the_file(monkeypatch):
    calls = []
    monkeypatch.setattr(od.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    od.reveal_in_finder("/tmp/example.pdf")
    assert calls == [["open", "-R", "/tmp/example.pdf"]]


# --- found by a second-model review, 2026-10-09 ---------------------------------

def test_three_files_whose_fallback_names_collide_are_all_kept(tmp_path):
    """x, 3-x, x: the third file's fallback name is the second file's real name."""
    stage = tmp_path / "stage"
    sources = []
    for folder, name, content in (("a", "x.pdf", b"first"), ("b", "3-x.pdf", b"second"),
                                  ("c", "x.pdf", b"third")):
        path = tmp_path / folder / name
        path.parent.mkdir()
        path.write_bytes(content)
        sources.append(str(path))
    staged, problems = od.stage_attachments(sources, directory=str(stage), reveal=lambda p: None)
    assert problems == []
    assert len(set(staged)) == 3
    assert sorted(Path(p).read_bytes() for p in staged) == [b"first", b"second", b"third"]


def test_reveal_failure_is_reported_not_raised(tmp_path):
    source = tmp_path / "a.pdf"
    source.write_bytes(b"cv")

    def broken_reveal(path):
        raise OSError("no Finder here")

    staged, problems = od.stage_attachments([str(source)], directory=str(tmp_path / "stage"),
                                            reveal=broken_reveal)
    assert len(staged) == 1
    assert any("Finder could not be opened" in p for p in problems)


def test_symlinked_staging_folder_is_refused_and_its_target_untouched(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / od.STAGING_MARKER).write_text("marker", encoding="utf-8")
    (real / "keep.pdf").write_bytes(b"do not delete")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    source = tmp_path / "a.pdf"
    source.write_bytes(b"cv")
    staged, problems = od.stage_attachments([str(source)], directory=str(link),
                                            reveal=lambda p: None)
    assert staged == []
    assert any("symlink" in p for p in problems)
    assert (real / "keep.pdf").read_bytes() == b"do not delete"

