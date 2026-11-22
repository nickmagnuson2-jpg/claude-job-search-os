"""memory_frontmatter.repair(): the one path for a structural frontmatter repair.

Org 72 (2026-11-22): Nick ruled nothing is hand-edited, and write() cannot repair a
malformed block or a commented line. Each success case mirrors one class in
output/memory-hygiene/112226-malformed-frontmatter-proposals.md. Fixtures are synthetic
(public repo).
"""
import datetime as dt
import hashlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import memory_frontmatter as mf  # noqa: E402

BODY = "\n**Rule:** an example.\n\n**Why:** because.\n"
NOTE = "> 2026-11-22 note (repair 11): moved verbatim from a YAML comment: gate tripped"


def _file(tmp_path, block, body=BODY, name="feedback_example.md"):
    p = tmp_path / name
    p.write_text("---\n" + block + "---\n" + body, encoding="utf-8")
    return p


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _claude(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")


# The 5 proposal classes, as synthetic blocks: (old block, new block, intended, note)
RAW = ('name: x\ndescription: "Merged" answers only for code\nmetadata:\n  promoted: no\n',
       'name: x\ndescription: \'"Merged" answers only for code\'\nmetadata:\n  promoted: no\n',
       {"description": '"Merged" answers only for code'}, None)
OUTER = ('name: x\nmetadata:\n  promoted: "no -- the "please" phrase"\n  occurrences: 2\n',
         'name: x\nmetadata:\n  promoted: \'no -- the "please" phrase\'\n  occurrences: 2\n',
         {"promoted": 'no -- the "please" phrase'}, None)
DUPLICATE = ('name: x\nmetadata:\n  promoted_date: 2026-09-07\n  exit_path: exit1\n  last_cited: 2026-10-22\n'
             '  promoted_date: 2026-09-07\n  exit_path: exit1\n',
             'name: x\nmetadata:\n  promoted_date: 2026-09-07\n  exit_path: exit1\n  last_cited: 2026-10-22\n',
             {"promoted_date": "2026-09-07", "exit_path": "exit1"}, None)
VOCAB = ('name: x\nmetadata:\n  promoted: n/a -- reference\n',
         "name: x\nmetadata:\n  promoted: 'superseded by ruling 2026-11-22 (earlier value: n/a -- reference)'\n",
         {"promoted": "superseded by ruling 2026-11-22 (earlier value: n/a -- reference)"}, None)
COMMENT = ('name: x\nmetadata:\n  promoted: "no"  # gate tripped\n  last_cited: 2026-11-03\n',
           'name: x\nmetadata:\n  promoted: "no"\n  last_cited: 2026-11-03\n',
           {"promoted": "no"}, NOTE)


@pytest.mark.parametrize("case", [RAW, OUTER, DUPLICATE, VOCAB, COMMENT],
                         ids=["raw", "outer", "duplicate", "vocab", "comment"])
def test_each_proposal_class_repairs(tmp_path, case):
    old, new, intended, note = case
    p = _file(tmp_path, old)
    p.chmod(0o640)
    fields = mf.repair(p, expect_sha256=_sha(p), new_block=new, intended=intended, body_note=note)
    for k, v in intended.items():
        assert fields[k] == v
    text = p.read_text(encoding="utf-8")
    expected_body = ("\n" + note + "\n" + BODY) if note else BODY
    assert text == "---\n" + new + "---\n" + expected_body
    assert p.stat().st_mode & 0o777 == 0o640


def test_refuses_without_claudecode(tmp_path, monkeypatch):
    p = _file(tmp_path, RAW[0])
    before = p.read_bytes()
    for env in (None, "0"):
        if env is None:
            monkeypatch.delenv("CLAUDECODE", raising=False)
        else:
            monkeypatch.setenv("CLAUDECODE", env)
        with pytest.raises(mf.WriteRefused) as e:
            mf.repair(p, expect_sha256=_sha(p), new_block=RAW[1], intended=RAW[2])
        assert "CLAUDECODE" in str(e.value)
    assert p.read_bytes() == before


def test_refuses_a_sha_mismatch(tmp_path):
    p = _file(tmp_path, RAW[0])
    before = p.read_bytes()
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256="0" * 64, new_block=RAW[1], intended=RAW[2])
    assert "sha256" in str(e.value)
    assert p.read_bytes() == before


def test_refuses_a_symlink(tmp_path):
    target = _file(tmp_path, RAW[0])
    alias = tmp_path / "alias.md"
    alias.symlink_to(target)
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(alias, expect_sha256=_sha(target), new_block=RAW[1], intended=RAW[2])
    assert "symlink" in str(e.value)
    assert alias.is_symlink() and target.read_text(encoding="utf-8").startswith("---\n" + RAW[0])


def test_refuses_a_file_without_frontmatter(tmp_path):
    p = tmp_path / "feedback_x.md"
    p.write_text("body only\n", encoding="utf-8")
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256=_sha(p), new_block="name: x\n", intended={"name": "x"})
    assert "frontmatter" in str(e.value)


@pytest.mark.parametrize("bad,code", [
    ('name: x\ndescription: "Merged" answers\n', "yaml_error"),
    ("name: x\nname: y\n", "duplicate_key"),
    ("- a\n- b\n", "not_a_mapping"),
])
def test_refuses_a_new_block_that_fails_the_strict_read(tmp_path, bad, code):
    p = _file(tmp_path, RAW[0])
    before = p.read_bytes()
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256=_sha(p), new_block=bad, intended={"name": "x"})
    assert code in str(e.value)
    assert p.read_bytes() == before


def test_refuses_an_intended_key_that_reads_back_different(tmp_path):
    p = _file(tmp_path, RAW[0])
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256=_sha(p), new_block=RAW[1],
                  intended={"description": "something else"})
    assert "description" in str(e.value)


@pytest.mark.parametrize("new", [
    RAW[1].replace("promoted: no", "promoted: partial"),             # quiet edit to another key
    RAW[1] + "  reopen_at: 3\n",                                      # added line
    RAW[1].replace("name: x\n", ""),                                  # removed line
    RAW[1].replace("metadata:", "metadata:  "),                       # whitespace-only change
])
def test_refuses_a_change_outside_the_intended_spans(tmp_path, new):
    p = _file(tmp_path, RAW[0])
    before = p.read_bytes()
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256=_sha(p), new_block=new, intended=RAW[2])
    assert "outside" in str(e.value)
    assert p.read_bytes() == before


def test_a_multiline_intended_span_is_replaced_whole(tmp_path):
    # Org 72 addition (b): an intended key whose span is a block scalar.
    old = 'name: x\nreopen_gate: >-\n  3rd "fire": build\n  the check\nmetadata:\n  promoted: no\n'
    new = "name: x\nreopen_gate: '3rd \"fire\": build the check'\nmetadata:\n  promoted: no\n"
    p = _file(tmp_path, old)
    fields = mf.repair(p, expect_sha256=_sha(p), new_block=new,
                       intended={"reopen_gate": '3rd "fire": build the check'})
    assert fields["reopen_gate"] == '3rd "fire": build the check'
    # and a block-scalar span cannot be used to smuggle in a change to the next key
    p2 = _file(tmp_path, old, name="feedback_two.md")
    smuggle = new.replace("promoted: no", "promoted: yes")
    with pytest.raises(mf.WriteRefused):
        mf.repair(p2, expect_sha256=_sha(p2), new_block=smuggle,
                  intended={"reopen_gate": '3rd "fire": build the check'})


def test_refuses_intended_occurrences(tmp_path):
    old = "name: x\nmetadata:\n  occurrences: 2\n"
    p = _file(tmp_path, old)
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256=_sha(p), new_block="name: x\nmetadata:\n  occurrences: 3\n",
                  intended={"occurrences": 3})
    assert "occurrences" in str(e.value)


@pytest.mark.parametrize("value", ["Encoded somewhere", "superseded", "merged into nothing_here"])
def test_refuses_an_intended_state_defect(tmp_path, value):
    p = _file(tmp_path, VOCAB[0])
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256=_sha(p), new_block=f"name: x\nmetadata:\n  promoted: '{value}'\n",
                  intended={"promoted": value}, memory_dir=tmp_path)
    assert "promoted" in str(e.value)


def test_a_dropped_comment_must_travel_in_the_note(tmp_path):
    old, new, intended, _ = COMMENT
    p = _file(tmp_path, old)
    before = p.read_bytes()
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256=_sha(p), new_block=new, intended=intended)
    assert "comment" in str(e.value)
    with pytest.raises(mf.WriteRefused):
        mf.repair(p, expect_sha256=_sha(p), new_block=new, intended=intended,
                  body_note="> 2026-11-22 note: something unrelated")
    assert p.read_bytes() == before


@pytest.mark.parametrize("note,why", [
    ("   ", "blank"),
    ("> a note with no date", "date"),
    ("> 2026-11-22 first line\n> second line", "line"),
    ("**Rule:** an example.", "already"),
])
def test_refuses_a_bad_note(tmp_path, note, why):
    p = _file(tmp_path, RAW[0], body=BODY + "\n> 2026-11-22 already here\n")
    if why == "already":
        note = "> 2026-11-22 already here"
    before = p.read_bytes()
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256=_sha(p), new_block=RAW[1], intended=RAW[2], body_note=note)
    assert why in str(e.value)
    assert p.read_bytes() == before


def test_failed_replace_leaves_no_temp_file(tmp_path, monkeypatch):
    p = _file(tmp_path, RAW[0])
    before = p.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(mf.os, "replace", boom)
    with pytest.raises(OSError):
        mf.repair(p, expect_sha256=_sha(p), new_block=RAW[1], intended=RAW[2])
    assert sorted(x.name for x in tmp_path.iterdir()) == ["feedback_example.md"]
    assert p.read_bytes() == before


def test_a_comment_in_a_block_the_scanner_cannot_read_must_still_travel(tmp_path):
    # An unterminated quote fails in the scanner itself, so the comment is found by the
    # fallback ("#" after whitespace); dropping it still needs the note.
    old = 'name: x\ndescription: "unterminated  # keep this text\nmetadata:\n  promoted: no\n'
    new = "name: x\ndescription: 'unterminated'\nmetadata:\n  promoted: no\n"
    p = _file(tmp_path, old)
    before = p.read_bytes()
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256=_sha(p), new_block=new, intended={"description": "unterminated"})
    assert "keep this text" in str(e.value)
    assert p.read_bytes() == before
    mf.repair(p, expect_sha256=_sha(p), new_block=new, intended={"description": "unterminated"},
              body_note="> 2026-11-22 note: moved from a comment: keep this text")
    assert p.read_text(encoding="utf-8").count("keep this text") == 1


# ------------------------------------------------------------------ Codex r1 on learning-repair-1122

def test_an_intended_span_cannot_absorb_other_keys(tmp_path):
    # F1 (P0): in a malformed block, more-indented key-shaped lines after an intended key
    # were inside its span and were deleted with it.
    old = "name: x\ndescription: broken\n  hidden: lost\n  promoted: no\n"
    p = _file(tmp_path, old)
    before = p.read_bytes()
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256=_sha(p), new_block="name: x\ndescription: fixed\n",
                  intended={"description": "fixed"})
    assert "hidden" in str(e.value)
    assert p.read_bytes() == before


def test_block_scalar_content_shaped_like_a_key_is_still_text(tmp_path):
    old = "name: x\nreopen_gate: |\n  note: this line is text\n  more text\n"
    new = "name: x\nreopen_gate: 'note: this line is text more text'\n"
    p = _file(tmp_path, old)
    mf.repair(p, expect_sha256=_sha(p), new_block=new,
              intended={"reopen_gate": "note: this line is text more text"})


def test_a_glued_comment_in_an_unscannable_block_must_travel(tmp_path):
    # F2 (P0): the scanner fallback only saw "#" after whitespace.
    old = 'name: x\ndescription: "unterminated\nmetadata:\n  promoted: "no"# secret comment\n'
    new = "name: x\ndescription: 'unterminated'\nmetadata:\n  promoted: 'no'\n"
    p = _file(tmp_path, old)
    before = p.read_bytes()
    with pytest.raises(mf.WriteRefused) as e:
        mf.repair(p, expect_sha256=_sha(p), new_block=new,
                  intended={"description": "unterminated", "promoted": "no"})
    assert "secret comment" in str(e.value)
    assert p.read_bytes() == before


@pytest.mark.parametrize("gap", ["\n", "    # a comment first\n"])
def test_metadata_indent_skips_a_blank_or_comment_first_line(tmp_path, gap, monkeypatch):
    # F3: child indent came from the next physical line, so a blank or comment line made
    # a valid 4-space block unwritable.
    monkeypatch.setenv("CLAUDECODE", "1")
    text = "---\nname: x\nmetadata:\n" + gap + "    occurrences: 2\n    promoted: no\n---\nbody\n"
    p = tmp_path / "feedback_x.md"
    p.write_text(text, encoding="utf-8")
    mf.write(p, {"promoted": "partial -- half"})
    assert mf.read_file(p)[0]["promoted"] == "partial -- half"
    mf.record_fire(p, "2026-11-22 a fire")
    assert mf.read_file(p)[0]["occurrences"] == 3
    assert gap in p.read_text(encoding="utf-8")


def test_a_plain_continuation_line_in_an_intended_span_is_fine(tmp_path):
    old = "name: x\ndescription: a value that\n  wraps onto a second line\nmetadata:\n  promoted: no\n"
    new = "name: x\ndescription: a value that wraps onto a second line\nmetadata:\n  promoted: no\n"
    p = _file(tmp_path, old)
    mf.repair(p, expect_sha256=_sha(p), new_block=new,
              intended={"description": "a value that wraps onto a second line"})


def test_an_absorbed_key_that_is_itself_intended_is_allowed(tmp_path):
    old = "name: x\ndescription: broken\n  hidden: kept\n"
    new = "name: x\ndescription: fixed\nhidden: kept\n"
    p = _file(tmp_path, old)
    fields = mf.repair(p, expect_sha256=_sha(p), new_block=new,
                       intended={"description": "fixed", "hidden": "kept"})
    assert fields["hidden"] == "kept"


def test_a_hash_inside_a_quoted_value_of_a_readable_block_needs_no_note(tmp_path):
    old = 'name: x\nmetadata:\n  promoted: "n/a -- see PR #61"\n'
    new = "name: x\nmetadata:\n  promoted: 'superseded by ruling 2026-11-22 (earlier value: n/a -- see PR #61)'\n"
    p = _file(tmp_path, old)
    mf.repair(p, expect_sha256=_sha(p), new_block=new,
              intended={"promoted": "superseded by ruling 2026-11-22 (earlier value: n/a -- see PR #61)"})
