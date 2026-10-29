"""End to end: tools/inbox_drain_apply.py on a three-block fixture inbox.

The applier is the drain's only irreversible step and had no tests before S16
(2026-10-27). Each refusal below leaves the inbox byte-identical; the happy path removes
exactly the approved blocks, routes through a fake writer, and records execution.
Approvals are signed with a throwaway key (no Touch ID).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import approval_signing as sig
import inbox_decision_log as dlog
import inbox_drain_apply as apply

INBOX = """# Inbox

## 2026-10-01 drip one
promo text

## 2026-10-02 idea for a todo
call the dentist

## 2026-10-03 keep me
unapproved block
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "data" / "inbox.md").write_text(INBOX)
    # A fake writer: records its argv so a test can see what ran.
    (root / "tools" / "todo_write.py").write_text(
        "import sys, json, pathlib\n"
        "p = pathlib.Path('routed.jsonl')\n"
        "p.open('a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "sys.exit(int(pathlib.Path('writer_rc').read_text()) if pathlib.Path('writer_rc').exists() else 0)\n")
    subprocess.run([sys.executable, str(TOOLS / "inbox_extract.py"), "--repo-root", str(root),
                    "--out", "output/career-scan/inbox-blocks.jsonl"], check=True,
                   capture_output=True, cwd=root)
    key = tmp_path / "k"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f"nick {Path(str(key) + '.pub').read_text()}")
    monkeypatch.setattr(sig, "ALLOWED_SIGNERS", allowed)
    blocks = [json.loads(l) for l in
              (root / "output/career-scan/inbox-blocks.jsonl").read_text().splitlines()]
    return {"root": root, "key": key, "blocks": blocks}


def _decide(repo, todo_invocation='PYTHONIOENCODING=utf-8 python3 tools/todo_write.py add "call the dentist" --repo-root .'):
    b = repo["blocks"]
    base = {"corpus_sha256": b[0]["corpus_sha256"], "decided_at": "2026-10-27T23:00:00",
            "reason": "r", "decided_by": "nick", "confidence": "high", "residual_flag": False}
    rows = [
        {**base, "block_id": b[0]["block_id"], "producer": b[0]["producer"], "mode": "bulk",
         "bulk_rule": "drips", "destination": "delete", "writer_invocation": ""},
        {**base, "block_id": b[1]["block_id"], "producer": b[1]["producer"], "mode": "bulk",
         "bulk_rule": "todos", "destination": "job-todo", "writer_invocation": todo_invocation},
        {**base, "block_id": b[2]["block_id"], "producer": b[2]["producer"], "mode": "bulk",
         "bulk_rule": "keep", "destination": "delete", "writer_invocation": ""},
    ]
    p = repo["root"] / dlog.LOG_REL
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _approve(repo, rules=("drips", "todos"), signed=True):
    root = repo["root"]
    for rule in rules:
        dlog.cmd_approve(root, [dlog.rule_id(rule)], "fixture",
                         sign=(lambda d: sig.sign(d, key_file=repo["key"])) if signed
                         else (lambda d: "not a signature"))


def _run(repo, monkeypatch, capsys, *flags):
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["inbox_drain_apply.py", "--repo-root", str(repo["root"]), *flags])
    monkeypatch.chdir(repo["root"])
    code = 0
    try:
        apply.main()
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out
    return code, json.loads(out[out.index("{"):])


def _inbox(repo):
    return (repo["root"] / "data" / "inbox.md").read_text()


def test_dry_run_plans_and_writes_nothing(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo)
    code, out = _run(repo, monkeypatch, capsys)
    assert code == 0 and out["mode"] == "dry-run"
    assert out["blocks_to_remove"] == 2 and out["blocks_to_route_then_remove"] == 1
    assert out["live_blocks_before"] == 3 and out["live_blocks_after_expected"] == 1
    assert out["unsigned_approvals_ignored"] == []
    assert out["routing_commands"] == [
        'PYTHONIOENCODING=utf-8 python3 tools/todo_write.py add "call the dentist" --repo-root .']
    assert _inbox(repo) == INBOX
    assert not (repo["root"] / "routed.jsonl").exists()


def test_apply_routes_then_removes_exactly_the_approved_blocks(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo)
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 0 and out["mode"] == "APPLY"
    text = _inbox(repo)
    assert "keep me" in text and "drip one" not in text and "dentist" not in text
    assert out["live_blocks_after"] == 1
    routed = (repo["root"] / "routed.jsonl").read_text().splitlines()
    assert [json.loads(l) for l in routed] == [["add", "call the dentist", "--repo-root", "."]]
    assert out["routed"][0]["rc"] == 0
    executed = (repo["root"] / apply.EXECUTED_REL).read_text()
    assert repo["blocks"][0]["block_id"] in executed and repo["blocks"][1]["block_id"] in executed


def test_a_second_run_skips_what_already_ran(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo)
    _run(repo, monkeypatch, capsys, "--apply")
    after = _inbox(repo)
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 1 and out["code"] == "nothing_approved"
    assert _inbox(repo) == after
    assert len((repo["root"] / "routed.jsonl").read_text().splitlines()) == 1


def test_unsigned_approvals_do_nothing(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo, signed=False)
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 1 and out["code"] == "nothing_approved"
    assert sorted(out["unsigned_approvals_ignored"]) == sorted(
        dlog.rule_id(r) for r in ("drips", "todos"))
    assert _inbox(repo) == INBOX


def test_only_the_signed_rule_runs(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo, rules=("drips",))
    _approve(repo, rules=("todos",), signed=False)
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 0 and out["blocks_to_remove"] == 1
    assert "dentist" in _inbox(repo) and "drip one" not in _inbox(repo)
    assert out["unsigned_approvals_ignored"] == [dlog.rule_id("todos")]


def test_a_block_decided_under_the_rule_after_signing_is_not_applied(repo, monkeypatch, capsys):
    """The signature covers the rule's block_ids, not the rule (final-g4 F1, 2026-10-28)."""
    _decide(repo)
    _approve(repo, rules=("drips", "todos"))
    # Re-decide the kept block under the already-signed "drips" rule, after the signature.
    p = repo["root"] / dlog.LOG_REL
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    p.write_text(p.read_text() + json.dumps({**rows[2], "bulk_rule": "drips"}) + "\n")
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    # The whole "drips" approval is void; only "todos" runs.
    assert code == 0 and out["blocks_to_remove"] == 1
    assert out["stale_approvals_ignored"] == [{"rule_id": dlog.rule_id("drips"),
                                               "added": [repo["blocks"][2]["block_id"]],
                                               "missing": [], "changed_command": []}]
    assert "keep me" in _inbox(repo) and "drip one" in _inbox(repo)
    assert "dentist" not in _inbox(repo)


def test_nothing_signed_reports_the_blocks_it_left_out(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo, rules=("drips",))
    p = repo["root"] / dlog.LOG_REL
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    # The one signed block now sits under another rule, and a new one joins "drips".
    p.write_text(p.read_text() + json.dumps({**rows[0], "bulk_rule": "keep"}) + "\n"
                 + json.dumps({**rows[2], "bulk_rule": "drips"}) + "\n")
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 1 and out["code"] == "nothing_approved"
    assert out["stale_approvals_ignored"] == [{"rule_id": dlog.rule_id("drips"),
                                               "added": [repo["blocks"][2]["block_id"]],
                                               "missing": [repo["blocks"][0]["block_id"]], "changed_command": []}]
    assert _inbox(repo) == INBOX


def test_a_signed_block_moved_to_another_rule_voids_the_rest(repo, monkeypatch, capsys):
    """A subset of a signed unit is not what was signed (Codex final-r2-g4 F1)."""
    _decide(repo)
    p = repo["root"] / dlog.LOG_REL
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    # "drips" covers two blocks at signing time.
    p.write_text(p.read_text() + json.dumps({**rows[2], "bulk_rule": "drips"}) + "\n")
    _approve(repo, rules=("drips",))
    # Then one of them is re-decided under another rule.
    p.write_text(p.read_text() + json.dumps({**rows[2], "bulk_rule": "keep"}) + "\n")
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 1 and out["code"] == "nothing_approved"
    assert out["stale_approvals_ignored"] == [{"rule_id": dlog.rule_id("drips"), "added": [],
                                               "missing": [repo["blocks"][2]["block_id"]], "changed_command": []}]
    assert _inbox(repo) == INBOX


@pytest.mark.parametrize("flags", [(), ("--apply",)])
def test_a_command_outside_the_allowlist_stops_everything(repo, monkeypatch, capsys, flags):
    _decide(repo, todo_invocation='python3 tools/todo_write.py add x; touch pwned')
    _approve(repo)
    code, out = _run(repo, monkeypatch, capsys, *flags)
    assert code == 1 and out["code"] == "invocation_refused"
    assert out["refused"][0]["block_id"] == repo["blocks"][1]["block_id"]
    assert _inbox(repo) == INBOX
    assert not (repo["root"] / "routed.jsonl").exists()
    assert not (repo["root"] / "pwned").exists()


def test_a_failed_route_leaves_the_inbox_untouched(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo)
    (repo["root"] / "writer_rc").write_text("4")
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 1 and out["code"] == "routing_failed"
    assert out["results"][0]["rc"] == 4
    assert _inbox(repo) == INBOX
    assert not (repo["root"] / apply.EXECUTED_REL).exists()


def test_a_block_that_arrived_after_extraction_aborts(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo)
    p = repo["root"] / "data" / "inbox.md"
    # Collectors PREPEND; appending would also change the last block's body.
    p.write_text(INBOX.replace("# Inbox\n\n", "# Inbox\n\n## 2026-10-04 new arrival\nfresh\n\n"))
    before = p.read_text()
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 1 and out["code"] == "corpus_drift"
    assert out["arrival_count"] == 1
    assert p.read_text() == before


def test_an_approved_block_that_is_gone_aborts(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo)
    p = repo["root"] / "data" / "inbox.md"
    p.write_text(INBOX.replace("## 2026-10-01 drip one\npromo text\n\n", ""))
    before = p.read_text()
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 1 and out["code"] == "block_not_found"
    assert out["missing"] == [repo["blocks"][0]["block_id"]]
    assert p.read_text() == before


def test_archive_is_kept_in_place(repo, monkeypatch, capsys):
    _decide(repo)
    log = repo["root"] / dlog.LOG_REL
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    rows[0]["destination"] = "archive"
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    _approve(repo)
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 0
    assert out["blocks_preserved_in_place"] == [repo["blocks"][0]["block_id"]]
    assert "drip one" in _inbox(repo) and "dentist" not in _inbox(repo)


def test_missing_inbox_fails(repo, monkeypatch, capsys):
    (repo["root"] / "data" / "inbox.md").unlink()
    code, out = _run(repo, monkeypatch, capsys)
    assert code == 1 and out["code"] == "file_not_found"


def test_individual_rows_are_approved_by_cohort(repo, monkeypatch, capsys):
    _decide(repo)
    log = repo["root"] / dlog.LOG_REL
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    rows[0].update(mode="individual", bulk_rule=None)
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    dlog.cmd_approve(repo["root"], [dlog.cohort_id("delete", "high")], "c",
                     sign=lambda d: sig.sign(d, key_file=repo["key"]))
    code, out = _run(repo, monkeypatch, capsys)
    assert code == 0 and out["blocks_to_remove"] == 1
    assert out["rules"][0]["rule_id"] == "ind:delete:high"


def test_a_later_batch_reports_what_an_earlier_one_ran(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo, rules=("drips", "keep"))     # two run now, one later: asymmetric on purpose
    _run(repo, monkeypatch, capsys, "--apply")
    ex = repo["root"] / apply.EXECUTED_REL
    ex.write_text(ex.read_text() + "\n")      # a blank line in the record is tolerated
    _approve(repo, rules=("todos",))
    code, out = _run(repo, monkeypatch, capsys)
    assert code == 0
    assert out["already_executed_skipped"] == 2 and out["blocks_to_remove"] == 1


def test_a_splice_that_removes_the_wrong_amount_is_reported(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo)
    monkeypatch.setattr(apply.inbox_lock, "atomic_update", lambda path, fn: {"stubbed": True})
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 1 and out["code"] == "postcondition_failed"
    assert (out["before"], out["after"], out["removed_requested"]) == (3, 3, 2)


def test_script_entry_point_runs_main(tmp_path):
    r = subprocess.run([sys.executable, str(TOOLS / "inbox_drain_apply.py"), "--repo-root",
                        str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 1 and '"file_not_found"' in r.stdout


def test_an_approval_whose_blocks_all_left_the_rule_is_reported_stale(repo, monkeypatch, capsys):
    _decide(repo)
    _approve(repo, rules=("drips",))
    p = repo["root"] / dlog.LOG_REL
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    p.write_text(p.read_text() + json.dumps({**rows[0], "bulk_rule": "keep"}) + "\n")
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 1 and out["code"] == "nothing_approved"
    assert out["stale_approvals_ignored"] == [{"rule_id": dlog.rule_id("drips"), "added": [],
                                               "missing": [repo["blocks"][0]["block_id"]], "changed_command": []}]


def test_a_signed_block_re_decided_with_a_new_command_voids_the_approval(repo, monkeypatch, capsys):
    """The signature covers each block's command, not only its id (788.F2, 2026-10-28)."""
    _decide(repo)
    _approve(repo, rules=("todos",))
    p = repo["root"] / dlog.LOG_REL
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    # Same block, same rule, a different command after signing.
    p.write_text(p.read_text() + json.dumps(
        {**rows[1], "writer_invocation": 'PYTHONIOENCODING=utf-8 python3 tools/todo_write.py add "something else" --repo-root .'}) + "\n")
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 1 and out["code"] == "nothing_approved"
    assert out["stale_approvals_ignored"] == [{"rule_id": dlog.rule_id("todos"), "added": [],
                                               "missing": [],
                                               "changed_command": [repo["blocks"][1]["block_id"]]}]
    assert _inbox(repo) == INBOX
    assert not (repo["root"] / "routed.jsonl").exists()


def test_the_signature_covers_the_commands(repo):
    """Editing a stored command breaks the signature itself, not only the unit check."""
    _decide(repo)
    _approve(repo, rules=("todos",))
    row = json.loads((repo["root"] / dlog.APPROVALS_REL).read_text().splitlines()[-1])
    bid = repo["blocks"][1]["block_id"]
    assert row["block_commands"] == {bid: {"destination": "job-todo", "command":
        'PYTHONIOENCODING=utf-8 python3 tools/todo_write.py add "call the dentist" --repo-root .'}}
    assert sig.verify(sig.payload(row), row["signature"], sig.ALLOWED_SIGNERS)
    row["block_commands"][bid]["command"] = "rm -rf ."
    assert not sig.verify(sig.payload(row), row["signature"], sig.ALLOWED_SIGNERS)


def test_an_approval_signed_without_commands_is_stale():
    a = {"rule_id": "r", "block_ids": ["b1"], "block_commands": None}
    rows = [{"block_id": "b1", "destination": "archive", "writer_invocation": ""}]
    assert apply.approval_staleness(a, rows) == {"rule_id": "r", "added": [], "missing": [],
                                                 "changed_command": ["b1"]}
    ok = {**a, "block_commands": {"b1": {"destination": "archive", "command": ""}}}
    assert apply.approval_staleness(ok, rows) is None
    # Same empty command, different destination: an archive re-decided as a delete.
    assert apply.approval_staleness(ok, [{**rows[0], "destination": "delete"}])["changed_command"] == ["b1"]


def test_a_signed_block_re_decided_to_another_destination_voids_the_approval(repo, monkeypatch, capsys):
    """Two decisions with no command differ only in destination (Grok inbox-signing r1 F2)."""
    _decide(repo)
    _approve(repo, rules=("drips",))
    p = repo["root"] / dlog.LOG_REL
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    p.write_text(p.read_text() + json.dumps({**rows[0], "destination": "archive"}) + "\n")
    code, out = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 1 and out["code"] == "nothing_approved"
    assert out["stale_approvals_ignored"][0]["changed_command"] == [repo["blocks"][0]["block_id"]]
    assert _inbox(repo) == INBOX


def test_status_reports_execution_from_the_applier_log(repo, monkeypatch, capsys):
    """Status reads the executed log the applier writes, not the approval row's flag,
    which is never updated (Codex inbox-signing r2 F1)."""
    _decide(repo)
    _approve(repo, rules=("drips",))
    capsys.readouterr()
    dlog.cmd_status(repo["root"])
    before = json.loads(capsys.readouterr().out)
    assert before["executed_any"] is False
    code, _ = _run(repo, monkeypatch, capsys, "--apply")
    assert code == 0
    dlog.cmd_status(repo["root"])
    after = json.loads(capsys.readouterr().out)
    rule = next(e for e in after["bulk_rules"] if e["rule_id"] == dlog.rule_id("drips"))
    assert rule["executed"] is True and after["executed_any"] is True
