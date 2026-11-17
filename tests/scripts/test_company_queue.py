"""tools/company_queue.py: the discovery queue (funnel-intake decisions, Nick 2026-11-06).

What must hold:
  * discovery output is queued once, and a decided company is never queued again;
  * /standup's read never consumes and never raises;
  * accept runs the board check and writes a scan target through act_apply
    (board found -> ats + slug, none -> outreach-only); reject records a rejection;
  * a failed act_apply write leaves the company in the queue;
  * the one-time inbox migration moves the Agent-drip blocks and nothing else.
"""
import json
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

import tools.company_queue as cq

CO = "company"


def _cands(*names, score=5):
    return [{"name": n, "description": f"{n} does things", "score": score} for n in names]


def test_add_queues_once_and_skips_repeats(tmp_path):
    assert cq.add(tmp_path, "lane-a", CO, _cands("Acme", "Beacon"), "2026-11-16") == \
        {"added": 2, "skipped": 0}
    assert cq.add(tmp_path, "lane-b", CO, _cands("ACME", "Northwind"), "2026-11-17") == \
        {"added": 1, "skipped": 1}
    names = [e["name"] for e in cq.load(tmp_path)["pending"]]
    assert names == ["Acme", "Beacon", "Northwind"]


def test_a_decided_company_is_never_queued_again(tmp_path):
    cq.add(tmp_path, "lane-a", CO, _cands("Acme"), "2026-11-16")
    cq.decide(tmp_path, "Acme", "reject", runner=lambda root, args: {"status": "ok"},
              detector=lambda n: None)
    assert cq.add(tmp_path, "lane-a", CO, _cands("Acme"), "2026-11-23") == \
        {"added": 0, "skipped": 1}
    assert cq.load(tmp_path)["pending"] == []


def test_a_person_and_a_company_with_the_same_name_are_different_entries(tmp_path):
    cq.add(tmp_path, "p", "person", [{"name": "Casey Doe", "company": "Acme"}], "2026-11-16")
    cq.add(tmp_path, "c", CO, _cands("Casey Doe"), "2026-11-16")
    assert len(cq.load(tmp_path)["pending"]) == 2


def test_read_orders_by_score_counts_people_and_does_not_consume(tmp_path):
    cq.add(tmp_path, "a", CO, _cands("Low", score=3) + _cands("High", score=8), "2026-11-16")
    cq.add(tmp_path, "p", "person", [{"name": "Casey Doe", "company": "Acme"}], "2026-11-10")
    out = cq.read(tmp_path, top=4)
    assert [c["name"] for c in out["companies"]] == ["High", "Low"]
    assert (out["pending_companies"], out["pending_people"]) == (2, 1)
    assert out["oldest_first_seen"] == "2026-11-10"
    assert cq.read(tmp_path)["pending_companies"] == 2, "reading must not consume"


def test_read_never_raises_on_a_missing_or_broken_queue(tmp_path):
    assert cq.read(tmp_path)["exists"] is False
    p = cq.queue_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("[1, 2]", encoding="utf-8")
    out = cq.read(tmp_path)
    assert out["exists"] is False and out["pending_companies"] is None


def test_slug_candidates_cover_the_common_board_spellings():
    s = cq.slug_candidates("Acme AI")
    for want in ("acmeai", "acme-ai", "acme"):
        assert want in s
    assert cq.slug_candidates("Beacon.ai")[0] == "beaconai"


def test_detect_board_returns_the_first_board_with_postings():
    calls = []

    def empty(slug):
        calls.append(("ashby", slug))
        return []

    def broken(slug):
        raise RuntimeError("timeout")

    def found(slug):
        return [{"title": "x"}] if slug == "acme" else []

    board = cq.detect_board("Acme AI", {"ashby": empty, "lever": broken, "greenhouse": found})
    assert board == {"ats": "greenhouse", "slug": "acme", "n_roles": 1}
    assert cq.detect_board("Acme", {"ashby": empty}) is None


def _decide(tmp_path, decision, board=None, result=None, **kw):
    cq.add(tmp_path, "lane-a", CO, _cands("Acme"), "2026-11-16")
    seen = []

    def runner(root, args):
        seen.append(args)
        return result or {"status": "ok"}
    out = cq.decide(tmp_path, "acme", decision, detector=lambda n: board, runner=runner, **kw)
    return out, seen


def test_accept_with_a_board_adds_a_scan_target_and_moves_the_entry(tmp_path):
    out, seen = _decide(tmp_path, "accept", board={"ats": "ashby", "slug": "acme", "n_roles": 3},
                        lane="a")
    assert out["status"] == "ok"
    args = seen[0]
    assert args[:2] == ["target-add", "Acme"]
    assert args[args.index("--ats") + 1] == "ashby" and args[args.index("--slug") + 1] == "acme"
    assert "--outreach" not in args and args[args.index("--lane") + 1] == "a"
    data = cq.load(tmp_path)
    assert data["pending"] == [] and data["decided"][0]["decision"] == "accept"
    assert data["decided"][0]["board"]["ats"] == "ashby"


def test_accept_without_a_board_adds_an_outreach_only_target(tmp_path):
    _, seen = _decide(tmp_path, "accept", board=None)
    assert "--outreach" in seen[0] and "--ats" not in seen[0]


def test_reject_records_a_rejection_with_its_reason(tmp_path):
    out, seen = _decide(tmp_path, "reject", reason="Peninsula only")
    assert seen[0][:2] == ["target-reject", "Acme"]
    assert seen[0][seen[0].index("--reason") + 1] == "Peninsula only"
    assert cq.load(tmp_path)["decided"][0]["reason"] == "Peninsula only"


def test_a_failed_write_leaves_the_company_queued(tmp_path):
    out, _ = _decide(tmp_path, "accept", result={"status": "error", "message": "boom"})
    assert out["status"] == "error" and out["code"] == "act_apply_failed"
    assert [e["name"] for e in cq.load(tmp_path)["pending"]] == ["Acme"]
    assert cq.load(tmp_path)["decided"] == []


def test_dry_run_writes_nothing(tmp_path):
    out, seen = _decide(tmp_path, "accept", dry_run=True)
    assert out["dry_run"] is True and seen == []
    assert len(cq.load(tmp_path)["pending"]) == 1


def test_an_unknown_name_is_reported_not_guessed(tmp_path):
    cq.add(tmp_path, "a", CO, _cands("Acme"), "2026-11-16")
    out = cq.decide(tmp_path, "Acm", "accept", detector=lambda n: None,
                    runner=lambda r, a: pytest.fail("must not write"))
    assert out["code"] == "not_found"


def test_accept_and_reject_write_scan_targets_through_the_real_act_apply(tmp_path):
    (tmp_path / "data").mkdir()
    targets = tmp_path / "data" / "scan-targets.yaml"
    targets.write_text("companies:\n  - name: Existing Co\n    active: true\n", encoding="utf-8")
    cq.add(tmp_path, "lane-a", CO, _cands("Acme", "Beacon"), "2026-11-16")
    ok = cq.decide(tmp_path, "Acme", "accept",
                   detector=lambda n: {"ats": "greenhouse", "slug": "acme", "n_roles": 2})
    assert ok["status"] == "ok", ok
    rej = cq.decide(tmp_path, "Beacon", "reject", reason="no SF office", detector=lambda n: None)
    assert rej["status"] == "ok", rej
    doc = yaml.safe_load(targets.read_text(encoding="utf-8"))
    added = [c for c in doc["companies"] if c["name"] == "Acme"][0]
    assert (added["ats"], added["slug"]) == ("greenhouse", "acme")
    assert [r["name"] for r in doc["rejected"]] == ["Beacon"]


INBOX = """# Inbox

## 2026-11-16 | Agent drip: lane-a (company)
<!-- review-gated: accept via /act or /networking -->
- **Acme** - builds agents for field services (score 7)
- **Beacon** - claims automation (score 5) [location unverified] [possible match: Beacon AI in pipeline]

## 2026-11-15 | A note Nick wrote
- keep this

## 2026-11-16 | Agent drip: deployment-leads (person)
<!-- review-gated: accept via /act or /networking -->
- **Casey Doe** - Head of Deployment @ Northwind · San Francisco
"""


def test_parse_inbox_blocks_reads_companies_and_people():
    entries, spans = cq.parse_inbox_blocks(INBOX)
    assert len(spans) == 2
    comps = [e for e in entries if e["entity"] == "company"]
    assert [(c["name"], c["score"]) for c in comps] == [("Acme", 7), ("Beacon", 5)]
    assert comps[1]["possible_match"] == "Beacon AI"
    [p] = [e for e in entries if e["entity"] == "person"]
    assert (p["name"], p["company"], p["location"]) == ("Casey Doe", "Northwind", "San Francisco")


def test_import_inbox_moves_only_the_drip_blocks(tmp_path):
    inbox = tmp_path / "inbox.md"
    inbox.write_text(INBOX, encoding="utf-8")
    out = cq.import_inbox(tmp_path, inbox, remove=True)
    assert (out["blocks"], out["entries"], out["added"], out["blocks_removed"]) == (2, 3, 3, 2)
    left = inbox.read_text(encoding="utf-8")
    assert "Agent drip" not in left and "keep this" in left and left.startswith("# Inbox")
    again = cq.import_inbox(tmp_path, inbox, remove=True)
    assert again["entries"] == 0


def test_import_without_remove_leaves_the_inbox_alone(tmp_path):
    inbox = tmp_path / "inbox.md"
    inbox.write_text(INBOX, encoding="utf-8")
    cq.import_inbox(tmp_path, inbox, remove=False)
    assert inbox.read_text(encoding="utf-8") == INBOX
    assert len(cq.load(tmp_path)["pending"]) == 3


# --- edges and the CLI (added after the first mutation run) ----------------------------

def test_people_at_different_companies_are_different_entries(tmp_path):
    cq.add(tmp_path, "p", "person", [{"name": "Casey Doe", "company": "Acme"},
                                      {"name": "Casey Doe", "company": "Beacon"},
                                      {"name": "Casey Doe", "company": "Acme"}], "2026-11-16")
    assert [e["company"] for e in cq.load(tmp_path)["pending"]] == ["Acme", "Beacon"]
    assert cq.entry_key({"entity": "person", "name": "A B", "company": "C"}) == "person:a b|c"


def test_nameless_rows_and_duplicates_within_one_batch_are_dropped(tmp_path):
    r = cq.add(tmp_path, "a", CO, [{"name": ""}, "junk", {"name": "Acme"}, {"name": "acme"}],
               "2026-11-16")
    assert r == {"added": 1, "skipped": 1}


def test_a_queue_file_missing_its_lists_still_reads(tmp_path):
    p = cq.queue_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("{}", encoding="utf-8")
    assert cq.read(tmp_path)["pending_companies"] == 0
    assert cq.load(tmp_path) == {"pending": [], "decided": []}


def test_slug_candidates_try_the_ai_suffix_for_a_bare_name():
    assert cq.slug_candidates("Beacon") == ["beacon", "beaconai"]


def test_reject_without_reason_or_lane_passes_neither_flag(tmp_path):
    _, seen = _decide(tmp_path, "reject")
    assert seen[0] == ["target-reject", "Acme"]


def test_an_unknown_decision_writes_nothing(tmp_path):
    cq.add(tmp_path, "a", CO, _cands("Acme"), "2026-11-16")
    out = cq.decide(tmp_path, "Acme", "maybe", detector=lambda n: None,
                    runner=lambda r, a: pytest.fail("must not write"))
    assert out == {"status": "error", "code": "bad_decision", "message": "maybe"}


def test_act_apply_output_that_is_not_json_is_an_error(tmp_path, monkeypatch):
    class P:
        stdout, stderr = "", "Traceback: boom"
    monkeypatch.setattr(cq.subprocess, "run", lambda *a, **k: P())
    assert cq._act_apply(tmp_path, ["target-add", "X"]) == \
        {"status": "error", "message": "Traceback: boom"}


def test_the_default_fetchers_are_the_three_public_boards():
    assert sorted(cq._default_fetchers()) == ["ashby", "greenhouse", "lever"]


def _cli(tmp_path, *args):
    import subprocess
    import sys
    script = Path(__file__).resolve().parents[2] / "tools" / "company_queue.py"
    p = subprocess.run([sys.executable, str(script), "--repo-root", str(tmp_path), *args],
                       capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout)


def test_cli_read_decide_and_import(tmp_path):
    inbox = tmp_path / "inbox.md"
    inbox.write_text(INBOX, encoding="utf-8")
    code, out = _cli(tmp_path, "import-inbox", "--inbox", str(inbox), "--remove")
    assert code == 0 and out["blocks_removed"] == 2
    code, out = _cli(tmp_path, "read", "--top", "1")
    assert code == 0 and [c["name"] for c in out["companies"]] == ["Acme"]
    code, out = _cli(tmp_path, "decide", "--name", "Acme", "--reject", "--reason", "r",
                     "--lane", "a", "--dry-run")
    assert code == 0 and out["would_run"] == ["target-reject", "Acme", "--reason", "r", "--lane", "a"]
    code, out = _cli(tmp_path, "decide", "--name", "Nope", "--accept")
    assert code == 1 and out["code"] == "not_found"
