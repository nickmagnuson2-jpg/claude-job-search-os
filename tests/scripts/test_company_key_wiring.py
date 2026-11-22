"""company-key-1121: every known-company check imports tools/company_key.py, and they agree.

Ruling (org c0, 2026-11-21), by whether a wrong merge is visible to Nick:
- visible (pipeline_history: the held-back skipped list and the labels): the strict key
  auto-resolves;
- discovery (agent_core.filter_known, company_queue.add, act_apply target-add/reject, and
  pipeline_reconcile, whose resolved names agent_collect drops before they reach the
  queue): only an exact normalize_name match counts as known; a key match is KEPT and
  tagged "possible duplicate of <name>".

Names are invented (public repo). Pair shapes come from the S9 audit (see
test_company_key.py).
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import agent_core  # noqa: E402
import company_key as ck  # noqa: E402
import company_queue as cq  # noqa: E402
import pipeline_reconcile as pr  # noqa: E402
from tools import pipeline_history as ph  # noqa: E402

from test_company_key import (AI_SUFFIX_DIFFERENT, DIFFERENT, LOOSE_ONLY_SAME,  # noqa: E402
                              NEITHER_SAME, STRICT_SAME)

# Exact pairs (normalize_name equal): the first two share the legacy ASCII-only stored key,
# the last two do not (accent folding), so the exact branch of every parity test runs both
# ways (Codex r2 F2).
EXACT_PAIRS = [("Acme Co.", "acme co"), ("Acme  Labs", "Acme-Labs"),
               ("Café Nova", "Cafe Nova"), ("Brüne Works", "Brune Works")]
ALL_PAIRS = (EXACT_PAIRS + STRICT_SAME + LOOSE_ONLY_SAME + NEITHER_SAME + DIFFERENT
             + [AI_SUFFIX_DIFFERENT])


def test_the_parity_table_exercises_the_exact_branch_both_ways():
    assert all(ck.exact(a, b) for a, b in EXACT_PAIRS)
    same_key = [cq.entry_key({"name": a}) == cq.entry_key({"name": b}) for a, b in EXACT_PAIRS]
    assert same_key == [True, True, False, False]
CO = "company"


# --- single source: each matcher imports company_key, none keeps a private copy ---------

USERS = ["pipeline_history.py", "pipeline_reconcile.py", "agent_core.py",
         "company_queue.py", "act_apply.py"]


@pytest.mark.parametrize("fname", USERS)
def test_every_known_company_check_imports_company_key(fname):
    tree = ast.parse((ROOT / "tools" / fname).read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods & {"company_key", "tools.company_key"}, f"{fname} must import company_key"


# --- parity: every tool answers the pair table the way its tier says ---------------------

@pytest.mark.parametrize("a,b", ALL_PAIRS)
def test_pipeline_history_resolves_with_the_strict_key(a, b):
    assert ph.same_company(a, b) is ck.same_company(a, b)


@pytest.mark.parametrize("a,b", ALL_PAIRS)
def test_filter_known_drops_only_exact_and_tags_the_loose_key(a, b):
    known = agent_core.KnownNames.from_names([a])
    out = agent_core.filter_known([{"name": b}], known)
    if ck.exact(a, b):
        assert out == []
    else:
        assert [c["name"] for c in out] == [b]
        assert out[0].get("possible_duplicate") == (a if ck.possible_duplicate(a, b) else None)


@pytest.mark.parametrize("a,b", ALL_PAIRS)
def test_company_queue_add_skips_only_exact_and_tags_the_loose_key(tmp_path, a, b):
    cq.add(tmp_path, "p", CO, [{"name": a}], "2026-11-21")
    r = cq.add(tmp_path, "p", CO, [{"name": b}], "2026-11-22")
    pend = cq.load(tmp_path)["pending"]
    if ck.exact(a, b):
        assert r == {"added": 0, "skipped": 1}
    else:
        assert r == {"added": 1, "skipped": 0}
        assert pend[-1].get("possible_duplicate") == (a if ck.possible_duplicate(a, b) else None)


@pytest.mark.parametrize("a,b", ALL_PAIRS)
def test_pipeline_reconcile_resolves_only_exact_and_flags_the_loose_key(tmp_path, a, b):
    p = tmp_path / "job-pipeline.md"
    p.write_text("| Company | Role | Stage | Date Updated |\n|---|---|---|---|\n"
                 f"| {a} | Ops Lead | Rejected | 2026-10-01 |\n", encoding="utf-8")
    out = pr.resolve([b], p)
    if ck.exact(a, b):
        assert out[pr.STATE_TERMINAL] and out[pr.STATE_TERMINAL][0]["name"] == b
        return
    assert out[pr.STATE_TERMINAL] == []
    flagged = [c for e in out[pr.STATE_AMBIGUOUS] for c in e["candidates"] if c.get("key_match")]
    assert bool(flagged) is ck.possible_duplicate(a, b)
    if flagged:
        assert flagged[0]["pipeline_company"] == a


# --- discovery details ---------------------------------------------------------------------

def test_load_known_names_keeps_display_names_for_the_tag(tmp_path):
    t = tmp_path / "scan-targets.yaml"
    t.write_text("companies:\n  - name: Orchalo Labs (Sparrix)\nrejected:\n  - name: Signato AI\n",
                 encoding="utf-8")
    known = agent_core.load_known_names([str(t)])
    assert known == {"orchalo labs sparrix", "signato ai"}   # still a plain exact set
    out = agent_core.filter_known([{"name": "Sparrix"}, {"name": "Signato"},
                                   {"name": "signato ai"}, {"name": "Brand New"}], known)
    assert [(c["name"], c.get("possible_duplicate")) for c in out] == [
        ("Sparrix", "Orchalo Labs (Sparrix)"), ("Signato", "Signato AI"), ("Brand New", None)]


def test_filter_known_accepts_a_plain_set(tmp_path):
    """Old callers pass a plain set of normalized names; that still works."""
    out = agent_core.filter_known([{"name": "Signato"}, {"name": "signato ai"}], {"signato ai"})
    assert [(c["name"], c.get("possible_duplicate")) for c in out] == [("Signato", "signato ai")]


def test_filter_known_does_not_mutate_its_input():
    cand = {"name": "Signato"}
    agent_core.filter_known([cand], agent_core.KnownNames.from_names(["Signato AI"]))
    assert "possible_duplicate" not in cand


def test_company_queue_keeps_the_stored_key_format(tmp_path):
    """G2: stored keys are persisted; their format must not change."""
    cq.add(tmp_path, "p", CO, [{"name": "Signato AI"}], "2026-11-21")
    assert cq.load(tmp_path)["pending"][0]["key"] == "company:signato ai"


def test_company_queue_tags_against_decided_entries_too(tmp_path):
    cq.add(tmp_path, "p", CO, [{"name": "Kestrix"}], "2026-11-20")
    cq.decide(tmp_path, "Kestrix", "reject", runner=lambda root, args: {"status": "ok"},
              detector=lambda n: None)
    assert cq.add(tmp_path, "p", CO, [{"name": "Kestrix AI"}], "2026-11-21") == \
        {"added": 1, "skipped": 0}
    assert cq.load(tmp_path)["pending"][-1]["possible_duplicate"] == "Kestrix"


def test_company_queue_keeps_a_tag_the_collector_already_set(tmp_path):
    cq.add(tmp_path, "p", CO, [{"name": "Kestrix"}], "2026-11-20")
    cq.add(tmp_path, "p", CO, [{"name": "Kestrix AI", "possible_duplicate": "Kestrix (targets)"}],
           "2026-11-21")
    assert cq.load(tmp_path)["pending"][-1]["possible_duplicate"] == "Kestrix (targets)"


def test_company_queue_read_shows_the_tag(tmp_path):
    cq.add(tmp_path, "p", CO, [{"name": "Kestrix"}, {"name": "Kestrix AI"}], "2026-11-21")
    rows = cq.read(tmp_path, top=5)["companies"]
    assert [(r["name"], r.get("possible_duplicate")) for r in rows] == [
        ("Kestrix", None), ("Kestrix AI", "Kestrix")]


def test_company_queue_people_are_untouched(tmp_path):
    """People are never tagged, whether the twin arrives in the same call or a later one."""
    people = [{"name": "Casey Doe", "company": "Kestrix"}, {"name": "Casey Doe", "company": "Kestrix AI"}]
    assert cq.add(tmp_path, "p", "person", people, "2026-11-21") == {"added": 2, "skipped": 0}
    r = cq.add(tmp_path, "p", "person", [{"name": "Casey Doe", "company": "Kestrixwood"}],
               "2026-11-22")
    assert r == {"added": 1, "skipped": 0}
    assert not any("possible_duplicate" in e for e in cq.load(tmp_path)["pending"])


def test_known_names_never_holds_a_blank_name():
    known = agent_core.KnownNames.from_names(["", "-", "   ", None, "Signato AI"])
    assert known == {"signato ai"}
    assert agent_core.filter_known([{"name": "-"}], known) == [{"name": "-"}]


# --- Codex r1 F1/F2 (2026-11-21): the legacy ASCII-only key is not an identity ----------
# company_queue's stored key and agent_core._norm_name drop every non-ASCII letter, so
# "Møller" and "Möller" collide there ("m ller") while normalize_name keeps them apart
# ("m ller" vs "moller"). A legacy-key collision alone must never drop a company.

def test_company_queue_keeps_a_legacy_key_collision_that_is_not_exact(tmp_path):
    cq.add(tmp_path, "p", CO, [{"name": "Møller"}], "2026-11-21")
    r = cq.add(tmp_path, "p", CO, [{"name": "Möller"}], "2026-11-22")
    assert r == {"added": 1, "skipped": 0}
    pend = cq.load(tmp_path)["pending"]
    assert [e["name"] for e in pend] == ["Møller", "Möller"]
    assert pend[-1]["possible_duplicate"] == "Møller"
    assert pend[-1]["key"] == pend[0]["key"] == "company:m ller"   # stored format unchanged


def test_company_queue_still_skips_an_exact_repeat_whose_legacy_key_matches(tmp_path):
    cq.add(tmp_path, "p", CO, [{"name": "Möller"}], "2026-11-21")
    assert cq.add(tmp_path, "p", CO, [{"name": "möller"}], "2026-11-22") == \
        {"added": 0, "skipped": 1}


def test_company_queue_people_keep_the_legacy_key_skip(tmp_path):
    """People were not in scope: their stored-key skip is unchanged."""
    cq.add(tmp_path, "p", "person", [{"name": "Casey Møller", "company": "Acme"}], "2026-11-21")
    assert cq.add(tmp_path, "p", "person", [{"name": "Casey Möller", "company": "Acme"}],
                  "2026-11-22") == {"added": 0, "skipped": 1}


def test_filter_known_keeps_a_name_only_the_legacy_normalizer_merged():
    """Behavior change, safe direction: the old _norm_name dropped "Möller" as known when
    "Møller" was; exact normalize_name does not, so it is kept."""
    assert agent_core._norm_name("Møller") == agent_core._norm_name("Möller")
    known = agent_core.KnownNames.from_names(["Møller"])
    assert [c["name"] for c in agent_core.filter_known([{"name": "Möller"}], known)] == ["Möller"]


def test_company_queue_tags_a_legacy_key_collision_within_one_call(tmp_path):
    assert cq.add(tmp_path, "p", CO, [{"name": "Møller"}, {"name": "Möller"}],
                  "2026-11-21") == {"added": 2, "skipped": 0}
    assert cq.load(tmp_path)["pending"][-1]["possible_duplicate"] == "Møller"


# --- Grok r1 F1 (2026-11-21): decide and ack_empty must not use the legacy key either ----
# add() keeps "Møller" and "Möller" as two pending rows sharing one stored key, so a
# lookup or a move by that key would decide (or delete) the wrong company.

def _twins(tmp_path):
    cq.add(tmp_path, "p", CO, [{"name": "Møller"}, {"name": "Möller"}], "2026-11-21")


def test_decide_moves_only_the_company_it_matched(tmp_path):
    _twins(tmp_path)
    calls = []
    out = cq.decide(tmp_path, "Møller", "accept", detector=lambda n: None,
                    runner=lambda root, args: calls.append(args) or {"status": "ok", "added": True})
    assert out["status"] == "ok" and out["name"] == "Møller"
    assert calls[0][:2] == ["target-add", "Møller"]
    q = cq.load(tmp_path)
    assert [e["name"] for e in q["pending"]] == ["Möller"]
    assert [e["name"] for e in q["decided"]] == ["Møller"]


def test_decide_finds_the_named_twin_not_the_first_key_match(tmp_path):
    _twins(tmp_path)
    calls = []
    cq.decide(tmp_path, "Möller", "reject", detector=lambda n: None,
              runner=lambda root, args: calls.append(args) or {"status": "ok", "added": True})
    assert calls[0][:2] == ["target-reject", "Möller"]
    q = cq.load(tmp_path)
    assert [e["name"] for e in q["pending"]] == ["Møller"]
    assert [e["name"] for e in q["decided"]] == ["Möller"]


def test_decide_still_finds_a_company_by_an_exact_variant_of_its_name(tmp_path):
    cq.add(tmp_path, "p", CO, [{"name": "Café Nova"}], "2026-11-21")
    out = cq.decide(tmp_path, "cafe nova", "reject", detector=lambda n: None,
                    runner=lambda root, args: {"status": "ok", "added": True})
    assert out["status"] == "ok" and out["name"] == "Café Nova"
    assert cq.load(tmp_path)["pending"] == []


def test_ack_empty_clears_only_the_named_twin(tmp_path):
    _twins(tmp_path)
    empty = lambda n: {"ats": "ashby", "slug": "x", "empty": True}  # noqa: E731
    ok = lambda root, args: {"status": "ok", "added": True}  # noqa: E731
    cq.decide(tmp_path, "Møller", "accept", detector=empty, runner=ok)
    cq.decide(tmp_path, "Möller", "accept", detector=empty, runner=ok)
    assert cq.ack_empty(tmp_path, "Möller")["status"] == "ok"
    flags = {e["name"]: e["empty_board"]["acked_on"] for e in cq.load(tmp_path)["decided"]}
    assert flags["Møller"] is None and flags["Möller"] is not None
