"""Codify #3 (2026-11-23): an EQUIVALENT reason must engage a NOT EQUIVALENT precedent.

The bug class: a session writes "Equivalent" for a mutant whose identical line shape is
already recorded elsewhere in tools/mutation-allow.json as "NOT EQUIVALENT: real
survivor". 3 fires on 2026-11-23 (S4 twice, S6 once), 5 false reasons; each cost a
review round. S4's case: check_draft_pleasantry.py::main::DROP_CALL::db837ff3 called
equivalent while check_draft_voice.py had the same key suffix labelled NOT EQUIVALENT.

Shape, chosen from the data (output/overnight-112226/S2-codify-report.md): same mutation
operator and same line hash, any file. A precedent is a reason that STARTS with a NOT
EQUIVALENT label (4 at b2495952); a claim is any reason mentioning EQUIVALENT that is not
so labelled (280). Refusing outright would refuse 7 existing claims (the bare
`sys.exit(0)` collision), so the writer demands --differs-from "<precedent key>: <why
this site differs>" and records it in the reason. Grok r1 (F1-F3) showed a whole-string
classifier misread EQUIVALENT reasons that mention a sibling being "NOT equivalent".
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import TOOLS_DIR

sys.path.insert(0, str(TOOLS_DIR))
import allowlist_write as aw  # noqa: E402

PRECEDENT = "tools/check_draft_voice.py::main::DROP_CALL::db837ff3"
SAME_SHAPE = "tools/check_draft_pleasantry.py::main::DROP_CALL::db837ff3"
OTHER_OP = "tools/check_draft_pleasantry.py::main::IF_FALSE::db837ff3"
OTHER_HASH = "tools/check_draft_pleasantry.py::main::DROP_CALL::0badc0de"
EQ_SIBLING = "tools/zzz.py::main::DROP_CALL::11112222"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    aw.allow_path(tmp_path).write_text(json.dumps({
        PRECEDENT: "NOT EQUIVALENT: real survivor (exists/read_text race).",
        "tools/yyy.py::main::DROP_CALL::11112222": "EQUIVALENT: trailing exit.",
    }, indent=2) + "\n", encoding="utf-8")
    return tmp_path


def _entries(repo):
    return json.loads(aw.allow_path(repo).read_text(encoding="utf-8"))


# --- classification ------------------------------------------------------------------
# (label, claims) per reason. The corpus-shaped rows are the Grok r1 F1 cases: an
# EQUIVALENT reason that later says a sibling was NOT equivalent.

@pytest.mark.parametrize("reason,label,claims", [
    ("NOT EQUIVALENT: real survivor", True, False),
    ("  Not equivalent, kept as a speed guard", True, False),
    ("NOT AN EQUIVALENT mutant", True, False),
    ("non-equivalent in one state", True, False),
    ("EQUIVALENT MUTANT. main() returns", False, True),
    ("Equivalent: dropping the sys.exit(0)", False, True),
    ("Structurally equivalent `continue` statements. The branches that were NOT "
     "equivalent were killed.", False, True),
    ("EQUIVALENT MUTANT for the exit under one guard; the identical exit under the "
     "other guard is NOT equivalent and is killed.", False, True),
    ("Drops the call; equivalent because the next guard exits 0 too", False, True),
    ("UNREACHABLE BY CONSTRUCTION under the runner", False, False),
    ("Drops `out.append(x)`: a real survivor, kept on purpose", False, False),
])
def test_label_and_claim_classification(reason, label, claims):
    assert aw.is_not_equivalent_label(reason) is label
    assert aw.claims_equivalent(reason) is claims


def test_an_equivalent_reason_mentioning_not_equivalent_is_not_a_precedent(repo):
    data = _entries(repo)
    data["tools/aaa.py::f::DROP_CALL::db837ff3"] = (
        "EQUIVALENT MUTANT here; the copy in another hook is NOT equivalent.")
    assert aw.not_equivalent_precedents(data, SAME_SHAPE) == [PRECEDENT]


# --- the precedent lookup ------------------------------------------------------------

def test_precedent_is_same_op_and_hash_in_another_key(repo):
    assert aw.not_equivalent_precedents(_entries(repo), SAME_SHAPE) == [PRECEDENT]


@pytest.mark.parametrize("key", [OTHER_OP, OTHER_HASH, EQ_SIBLING])
def test_no_precedent_for_a_different_op_hash_or_an_equivalent_sibling(repo, key):
    assert aw.not_equivalent_precedents(_entries(repo), key) == []


def test_a_key_is_not_its_own_precedent(repo):
    assert aw.not_equivalent_precedents(_entries(repo), PRECEDENT) == []


# --- add() ---------------------------------------------------------------------------

def test_equivalent_against_a_precedent_is_refused_and_names_it(repo):
    before = aw.allow_path(repo).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="is called EQUIVALENT") as exc:
        aw.add(repo, SAME_SHAPE, "Equivalent: dropping the exit falls through")
    assert PRECEDENT in str(exc.value)
    assert "--differs-from" in str(exc.value)
    assert aw.allow_path(repo).read_text(encoding="utf-8") == before


def test_equivalent_with_differs_from_is_recorded_in_the_reason(repo):
    out = aw.add(repo, SAME_SHAPE, "Equivalent: no read follows the exit here",
                 differs_from=f"{PRECEDENT}: this hook never calls read_text after exists()")
    assert out["action"] == "added"
    reason = _entries(repo)[SAME_SHAPE]
    assert reason.startswith("Equivalent: no read follows the exit here")
    assert f"DIFFERS FROM {PRECEDENT}: this hook never calls read_text after exists()" in reason


@pytest.mark.parametrize("differs,message", [
    ("tools/other.py::main::DROP_CALL::db837ff3: wrong key named", "must start with one of"),
    (f"{PRECEDENT}", "must start with one of"),
    ("   ", "must start with one of"),
    (f"{PRECEDENT}:   ", "needs a reason after the precedent key"),
])
def test_differs_from_must_name_the_precedent_and_give_a_reason(repo, differs, message):
    before = aw.allow_path(repo).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        aw.add(repo, SAME_SHAPE, "Equivalent: x", differs_from=differs)
    assert aw.allow_path(repo).read_text(encoding="utf-8") == before


def test_differs_from_without_a_precedent_is_refused(repo):
    """A vacuous citation would read as engagement with a precedent that does not exist."""
    before = aw.allow_path(repo).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="no NOT EQUIVALENT precedent"):
        aw.add(repo, OTHER_HASH, "Equivalent: x", differs_from=f"{PRECEDENT}: y")
    assert aw.allow_path(repo).read_text(encoding="utf-8") == before


@pytest.mark.parametrize("why", [
    "NOT EQUIVALENT: real survivor here too",
    "UNREACHABLE BY CONSTRUCTION under the runner",
])
def test_non_equivalent_reasons_need_no_differs_from(repo, why):
    assert aw.add(repo, SAME_SHAPE, why)["action"] == "added"


def test_equivalent_with_no_precedent_needs_no_differs_from(repo):
    assert aw.add(repo, OTHER_HASH, "EQUIVALENT: trailing exit")["action"] == "added"


def test_mixed_phrasing_cannot_skip_the_check(repo):
    """Grok r1 F3: 'Equivalent ...; X is NOT EQUIVALENT ...' was read as not_equivalent and
    accepted with no --differs-from, which is the natural retry after a refusal."""
    before = aw.allow_path(repo).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="is called EQUIVALENT"):
        aw.add(repo, SAME_SHAPE, "Equivalent: no race here; check_draft_voice is NOT "
                                 "EQUIVALENT because of exists/read_text")
    assert aw.allow_path(repo).read_text(encoding="utf-8") == before


def test_a_bare_line_collision_with_only_equivalent_siblings_is_not_refused(repo):
    """Grok r1 F2: siblings that are EQUIVALENT but mention a NOT-equivalent branch are not
    precedents, so a new `continue` claim with only such siblings goes straight in."""
    k = "tools/new.py::loop::DROP_CONTINUE::eee0bbba"
    data = _entries(repo)
    data["tools/ll.py::read::DROP_CONTINUE::eee0bbba"] = (
        "Structurally equivalent `continue`. The branches that were NOT equivalent were killed.")
    aw.allow_path(repo).write_text(aw.serialize(data), encoding="utf-8")
    assert aw.add(repo, k, "EQUIVALENT MUTANT. continue is last in the branch")["action"] == "added"


def test_a_recorded_differs_from_does_not_become_a_precedent(repo):
    """Grok r1: the stored '<why> DIFFERS FROM <key>: <because>' may say NOT EQUIVALENT in
    its because-part; it still leads with the claim, so it must not gate the next sibling."""
    aw.add(repo, SAME_SHAPE, "Equivalent: no read here",
           differs_from=f"{PRECEDENT}: that hook is NOT EQUIVALENT because it reads after")
    assert aw.not_equivalent_precedents(_entries(repo), "tools/q.py::main::DROP_CALL::db837ff3") \
        == [PRECEDENT]


def test_force_replacement_is_checked_too(repo):
    aw.add(repo, SAME_SHAPE, "NOT EQUIVALENT: first reason")
    with pytest.raises(ValueError, match="--differs-from"):
        aw.add(repo, SAME_SHAPE, "Equivalent after all", force=True)
    assert _entries(repo)[SAME_SHAPE] == "NOT EQUIVALENT: first reason"


# --- CLI -----------------------------------------------------------------------------

def _cli(repo, *args):
    return subprocess.run(
        [sys.executable, "-I", str(TOOLS_DIR / "allowlist_write.py"), "--repo-root", str(repo), *args],
        capture_output=True, text=True)


def test_cli_refuses_with_exit_2_and_names_the_precedent(repo):
    r = _cli(repo, "add", SAME_SHAPE, "--why", "Equivalent: x")
    assert r.returncode == 2
    assert PRECEDENT in r.stderr
    assert SAME_SHAPE not in _entries(repo)


def test_cli_accepts_differs_from(repo):
    r = _cli(repo, "add", SAME_SHAPE, "--why", "Equivalent: x",
             "--differs-from", f"{PRECEDENT}: no read_text after the check here")
    assert r.returncode == 0, r.stderr
    assert "DIFFERS FROM" in _entries(repo)[SAME_SHAPE]

