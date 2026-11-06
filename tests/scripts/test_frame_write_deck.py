"""The frame's `deck` field: frame_write passes it to the checker as --deck.

WHY. F15 reads what a surface PRINTS and needs the rendered deck. frame_write ran the
checker without one, so F15 was CANNOT_RUN on every governed write (closeout build row
B6). These tests pin three things: a declared deck makes F15 actually execute, a relative
path resolves against the frame's directory and not the caller's cwd, and a declared deck
that names nothing REFUSES the write and leaves the frame byte-identical.

Fixtures are synthetic and generic (public repo).
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "frame_write.py"

DECK = """<html><body>
<div class="slide"><h1>Widgets shipped 3,142 units</h1></div>
</body></html>"""

# Prints a number no fact carries, so F15 executes and FAILS.
DECK_UNDECLARED = """<html><body>
<div class="slide"><h1>Widgets shipped 3,142 units and 7,777 returns</h1></div>
</body></html>"""


def base_frame(deck=None):
    d = {
        "schema_version": 3,
        "version": 1,
        "locked": False,
        "engagement": "acme",
        "facts": {"f1": {"text": "Widgets shipped 3,142 units", "tier": "A",
                         "first_seen": 1}},
        "elements": [{"id": "e1", "name": "volume", "name_surface": "slide-1",
                      "measure": "units shipped", "measure_surface": "slide-1",
                      "because": ["f1"], "inputs": ["i_vol"], "first_seen": 1}],
        "closure": "One element, because the constraint is one investment.",
        "exclusions": [{"element": "signal", "reason": "a preference, not a property"}],
    }
    if deck is not None:
        d["deck"] = deck
    return d


def write_frame(tmp_path, data):
    p = tmp_path / "frame.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return p


def write_deck(tmp_path, body=DECK, name="deck.html"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def run(*args, cwd=None):
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                       capture_output=True, text=True, cwd=cwd,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        return r.returncode, json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.returncode, {"_raw": r.stdout, "_err": r.stderr}


def md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


def f15_state(frame_path):
    return yaml.safe_load(frame_path.read_text())["check_log"][-1]["states"]["F15"]


def test_without_a_deck_F15_is_cannot_run(tmp_path):
    """The baseline this build changes. If it ever reads PASS without a deck, the
    other tests below no longer prove anything."""
    f = write_frame(tmp_path, base_frame())
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x")
    assert code == 0, res
    assert f15_state(f) == "CANNOT_RUN"
    assert res["deck"] is None


def test_a_declared_relative_deck_makes_F15_execute(tmp_path):
    """Run from a cwd that is NOT the frame's directory: a relative deck resolved against
    the cwd would not exist and the write would be refused."""
    write_deck(tmp_path)
    f = write_frame(tmp_path, base_frame("deck.html"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x",
                    cwd=elsewhere)
    assert code == 0, res
    assert f15_state(f) == "PASS"
    assert res["deck"] == str(tmp_path / "deck.html")


def test_an_absolute_deck_is_passed_as_given(tmp_path):
    (tmp_path / "other").mkdir()
    (tmp_path / "eng").mkdir()
    deck = write_deck(tmp_path / "other")
    f = write_frame(tmp_path / "eng", base_frame(str(deck)))
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x")
    assert code == 0, res
    assert f15_state(f) == "PASS"
    assert res["deck"] == str(deck)


def test_F15_failing_on_the_deck_does_not_refuse_the_write(tmp_path):
    """A rule FAIL is what the gate is for; only structural errors refuse. The FAIL must
    reach the record, which is the point of passing the deck at all."""
    write_deck(tmp_path, DECK_UNDECLARED)
    f = write_frame(tmp_path, base_frame("deck.html"))
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x")
    assert code == 0, res
    assert f15_state(f) == "FAIL"
    assert res["counts"]["fail"] >= 1


def test_the_deck_can_be_declared_by_set(tmp_path):
    write_deck(tmp_path)
    f = write_frame(tmp_path, base_frame())
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "deck=deck.html")
    assert code == 0, res
    assert yaml.safe_load(f.read_text())["deck"] == "deck.html"
    assert f15_state(f) == "PASS"


def test_a_missing_deck_refuses_and_writes_nothing(tmp_path):
    f = write_frame(tmp_path, base_frame())
    before = md5(f)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "deck=gone.html")
    assert code == 1, res
    assert "not a file" in res["message"] and "Nothing was written" in res["message"]
    assert res["resolved"] == str(tmp_path / "gone.html")
    assert md5(f) == before
    assert not (tmp_path / "frame.v1.yaml").exists()
    assert not (tmp_path / "frame.yaml.candidate").exists()


def test_a_directory_is_not_a_deck(tmp_path):
    (tmp_path / "slides").mkdir()
    f = write_frame(tmp_path, base_frame())
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "deck=slides")
    assert code == 1, res
    assert "not a file" in res["message"]


def test_a_blank_deck_refuses(tmp_path):
    f = write_frame(tmp_path, base_frame())
    before = md5(f)
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "deck=   ")
    assert code == 1, res
    assert "blank or not a string" in res["message"]
    assert md5(f) == before


def test_a_non_string_deck_refuses(tmp_path):
    f = write_frame(tmp_path, base_frame())
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps({"deck": 5}), encoding="utf-8")
    code, res = run("patch", "--frame", f, "--expect-version", 1, "--json", payload)
    assert code == 1, res
    assert "blank or not a string" in res["message"]


def test_a_frame_already_naming_a_missing_deck_cannot_be_written(tmp_path):
    """The check reads the CANDIDATE, so a deck that disappeared after it was declared
    blocks every later write until the field is corrected or removed."""
    f = write_frame(tmp_path, base_frame("deck.html"))
    code, res = run("set", "--frame", f, "--expect-version", 1, "--field", "engagement=x")
    assert code == 1, res
    assert "not a file" in res["message"]


def test_removing_the_deck_with_null_returns_F15_to_cannot_run(tmp_path):
    f = write_frame(tmp_path, base_frame("deck.html"))   # deck file does not exist
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps({"deck": None}), encoding="utf-8")
    code, res = run("patch", "--frame", f, "--expect-version", 1, "--json", payload)
    assert code == 0, res
    assert f15_state(f) == "CANNOT_RUN"
    assert res["deck"] is None
