"""tools/inbox_decision_log.py: every schema refusal, append, supersede, status, and the CLI.

Written 2026-10-27 with S16: the module had no tests, and a mutation check on it found 109
surviving mutants, nearly all in the validator that decides what may enter the log the
applier later executes.
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

SHA = "a" * 64
BLOCKS = [{"block_id": "L00001-a", "producer": "dated_captures", "corpus_sha256": SHA},
          {"block_id": "L00005-b", "producer": "agent_drips", "corpus_sha256": SHA},
          {"block_id": "L00009-c", "producer": "dated_captures", "corpus_sha256": SHA}]


def good(bid="L00001-a", **kw):
    row = {"block_id": bid, "corpus_sha256": SHA, "producer": "dated_captures",
           "mode": "individual", "bulk_rule": None, "destination": "delete",
           "writer_invocation": "", "reason": "noise", "decided_by": "nick",
           "confidence": "high", "residual_flag": False}
    row.update(kw)
    return row


@pytest.fixture
def root(tmp_path):
    p = tmp_path / dlog.BLOCKS_REL
    p.parent.mkdir(parents=True)
    p.write_text("".join(json.dumps(b) + "\n" for b in BLOCKS))
    return tmp_path


def append(root, rows, supersede=False, capsys=None):
    f = root / "rows.json"
    f.write_text(json.dumps(rows))
    dlog.cmd_append(root, f, supersede=supersede)


def refused(root, rows, capsys, supersede=False):
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        append(root, rows, supersede)
    assert e.value.code == 1
    return json.loads(capsys.readouterr().out)["code"]


def logged(root):
    return dlog.load_log(root)


# ── the validator: each refusal, and nothing written ─────────────────────────

@pytest.mark.parametrize("row,code", [
    ({k: v for k, v in good().items() if k != "reason"}, "schema_missing_fields"),
    (good("L09999-x"), "unknown_block"),
    (good(corpus_sha256="b" * 64), "sha_mismatch"),
    (good(producer="agent_drips"), "producer_mismatch"),
    (good(mode="sometimes"), "bad_enum"),
    (good(destination="the-bin"), "bad_enum"),
    (good(confidence="certain"), "bad_enum"),
    (good(decided_by="claude"), "bad_enum"),
    (good(mode="bulk", bulk_rule=""), "bulk_without_rule"),
    (good(mode="individual", bulk_rule="drips"), "individual_with_rule"),
    (good(reason="   "), "empty_reason"),
    (good(destination="job-todo", writer_invocation=""), "missing_invocation"),
    (good(destination="job-todo", writer_invocation='python3 tools/todo_write.py add "<each row>"'),
     "placeholder_invocation"),
])
def test_each_refusal_writes_nothing(root, capsys, row, code):
    assert refused(root, [row], capsys) == code
    assert logged(root) == []


def test_a_bad_row_anywhere_in_the_batch_writes_none_of_it(root, capsys):
    assert refused(root, [good(), good("L00005-b", producer="agent_drips", reason="")],
                   capsys) == "empty_reason"
    assert logged(root) == []


def test_producer_outside_the_known_set_is_refused(root, capsys):
    blocks = root / dlog.BLOCKS_REL
    blocks.write_text(blocks.read_text().replace('"dated_captures"', '"mystery"', 1))
    assert refused(root, [good(producer="mystery")], capsys) == "bad_enum"


def test_archive_and_delete_need_no_invocation_but_routes_do(root, capsys):
    append(root, [good(destination="archive"), good("L00005-b", producer="agent_drips")])
    assert [r["destination"] for r in logged(root)] == ["archive", "delete"]
    append(root, [good("L00009-c", destination="job-todo",
                       writer_invocation="python3 tools/todo_write.py add x")])
    assert logged(root)[-1]["writer_invocation"] == "python3 tools/todo_write.py add x"


def test_rows_file_must_be_a_non_empty_list(root, capsys):
    for bad in ([], {"rows": []}):
        assert refused(root, bad, capsys) == "bad_rows_file"


def test_missing_or_empty_extractor_output_is_refused(tmp_path, root, capsys):
    with pytest.raises(SystemExit):
        dlog.load_blocks(tmp_path / "nowhere")
    assert json.loads(capsys.readouterr().out)["code"] == "blocks_missing"
    (root / dlog.BLOCKS_REL).write_text("")
    with pytest.raises(SystemExit):
        dlog.load_blocks(root)
    assert json.loads(capsys.readouterr().out)["code"] == "blocks_empty"


# ── append: only REQUIRED fields, a stamp, one decision per block ─────────────

def test_append_writes_only_the_schema_and_stamps_decided_at(root, capsys):
    append(root, [{**good(), "scratch": "dropped"}])
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "ok", "appended": 1, "logged_total": 1, "of_blocks": 3}
    row, = logged(root)
    assert set(row) == set(dlog.REQUIRED)
    assert row["decided_at"]


def test_a_given_decided_at_is_kept(root, capsys):
    append(root, [good(decided_at="2026-01-01T00:00:00")])
    assert logged(root)[0]["decided_at"] == "2026-01-01T00:00:00"


def test_a_block_is_decided_once_unless_superseded(root, capsys):
    append(root, [good()])
    assert refused(root, [good(destination="archive")], capsys) == "duplicate_block"
    append(root, [good(destination="archive")], supersede=True)
    assert [r["destination"] for r in logged(root)] == ["delete", "archive"]
    assert [r["destination"] for r in dlog.current_log(root)] == ["archive"]


def test_the_same_block_twice_in_one_batch_is_refused(root, capsys):
    assert refused(root, [good(), good()], capsys) == "duplicate_block"


def test_load_log_and_approvals_skip_blank_lines(root):
    (root / dlog.LOG_REL).write_text(json.dumps(good()) + "\n\n")
    assert len(dlog.load_log(root)) == 1
    (root / dlog.APPROVALS_REL).write_text(json.dumps({"rule_id": "r1"}) + "\n\n")
    assert list(dlog.load_approvals(root)) == ["r1"]


def test_absent_log_and_approvals_read_as_empty(tmp_path):
    assert dlog.load_log(tmp_path) == [] and dlog.load_approvals(tmp_path) == {}


def test_rule_and_cohort_ids():
    assert dlog.rule_id("drips") == dlog.rule_id("drips") != dlog.rule_id("drips.")
    assert len(dlog.rule_id("anything")) == 8
    assert dlog.cohort_id("delete", "high") == "ind:delete:high"


# ── approve: unknown rules and an empty log ──────────────────────────────────

def test_approve_refuses_an_empty_log_and_unknown_rules(root, capsys):
    with pytest.raises(SystemExit):
        dlog.cmd_approve(root, ["x"], "", sign=lambda b: "s")
    assert json.loads(capsys.readouterr().out)["code"] == "empty_log"
    append(root, [good()])
    capsys.readouterr()
    with pytest.raises(SystemExit):
        dlog.cmd_approve(root, ["nope"], "", sign=lambda b: "s")
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == "unknown_rule" and out["known"] == ["ind:delete:high"]
    assert not (root / dlog.APPROVALS_REL).exists()


def test_approve_covers_every_block_of_the_rule(root, capsys):
    append(root, [good(mode="bulk", bulk_rule="drips"),
                  good("L00005-b", producer="agent_drips", mode="bulk", bulk_rule="drips"),
                  good("L00009-c")])
    capsys.readouterr()
    dlog.cmd_approve(root, [dlog.rule_id("drips")], "n", sign=lambda b: "s")
    assert json.loads(capsys.readouterr().out) == {
        "status": "ok", "approved": [dlog.rule_id("drips")], "blocks_covered": 2}
    row = json.loads((root / dlog.APPROVALS_REL).read_text())
    assert row["block_ids"] == ["L00001-a", "L00005-b"] and row["blocks"] == 2


# ── status: counts, and only signed approvals count ──────────────────────────

@pytest.fixture
def signer(tmp_path, monkeypatch):
    k = tmp_path / "k"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(k)], check=True)
    allowed = tmp_path / "allowed"
    allowed.write_text(f"nick {Path(str(k) + '.pub').read_text()}")
    monkeypatch.setattr(sig, "ALLOWED_SIGNERS", allowed)
    return lambda b: sig.sign(b, key_file=k)


def test_status_counts_and_signed_approval(root, capsys, signer):
    append(root, [good(mode="bulk", bulk_rule="drips", residual_flag=True),
                  good("L00005-b", producer="agent_drips", mode="bulk", bulk_rule="drips")])
    dlog.cmd_approve(root, [dlog.rule_id("drips")], "", sign=signer)
    capsys.readouterr()
    dlog.cmd_status(root)
    s = json.loads(capsys.readouterr().out)
    assert (s["blocks"], s["logged"], s["unlogged"], s["unlogged_ids"]) == (3, 2, 1, ["L00009-c"])
    assert (s["bulk"], s["individual"], s["bulk_to_individual"]) == (2, 0, None)
    assert s["by_producer"] == {"dated_captures": 1, "agent_drips": 1}
    assert s["residual_ids"] == ["L00001-a"] and s["residual_count"] == 1
    rule, = s["bulk_rules"]
    assert rule["approved"] is True and rule["approved_by"] == "nick" and rule["blocks"] == 2
    assert s["blocks_under_approved_rules"] == 2 and s["blocks_awaiting_a_decision"] == 0
    assert s["unsigned_approvals_ignored"] == []


def test_status_does_not_count_a_stale_signed_approval(root, capsys, signer):
    """Status reads the same unit check as the applier (798.F1): a block added under a
    signed rule after signing voids the approval here too."""
    append(root, [good(mode="bulk", bulk_rule="drips")])
    dlog.cmd_approve(root, [dlog.rule_id("drips")], "", sign=signer)
    append(root, [good("L00005-b", producer="agent_drips", mode="bulk", bulk_rule="drips")])
    capsys.readouterr()
    dlog.cmd_status(root)
    s = json.loads(capsys.readouterr().out)
    rule, = s["bulk_rules"]
    assert rule["approved"] is False and rule["blocks"] == 2
    assert s["blocks_under_approved_rules"] == 0
    assert s["stale_approvals_ignored"] == [{"rule_id": dlog.rule_id("drips"),
                                             "added": ["L00005-b"], "missing": [],
                                             "changed_command": []}]


def test_status_reports_an_approval_whose_blocks_all_left_its_unit(root, capsys, signer):
    """Status iterates the verified approvals, not the display rows, so an approval with no
    current member is still reported stale, as the applier reports it (inbox-signing r1 F1)."""
    append(root, [good(mode="bulk", bulk_rule="drips")])
    dlog.cmd_approve(root, [dlog.rule_id("drips")], "", sign=signer)
    append(root, [good(mode="bulk", bulk_rule="other")], supersede=True)
    capsys.readouterr()
    dlog.cmd_status(root)
    s = json.loads(capsys.readouterr().out)
    assert s["stale_approvals_ignored"] == [{"rule_id": dlog.rule_id("drips"), "added": [],
                                             "missing": ["L00001-a"], "changed_command": []}]
    assert s["blocks_under_approved_rules"] == 0


def test_status_does_not_count_an_unsigned_approval(root, capsys, signer):
    append(root, [good(), good("L00005-b", producer="agent_drips", mode="bulk", bulk_rule="d")])
    dlog.cmd_approve(root, ["ind:delete:high"], "", sign=lambda b: "forged")
    capsys.readouterr()
    dlog.cmd_status(root)
    s = json.loads(capsys.readouterr().out)
    assert s["unsigned_approvals_ignored"] == ["ind:delete:high"]
    cohort, = s["individual_cohorts"]
    assert cohort["approved"] is False and cohort["approved_by"] is None
    assert s["blocks_under_approved_rules"] == 0 and s["blocks_awaiting_a_decision"] == 2
    assert (s["bulk"], s["individual"], s["bulk_to_individual"]) == (1, 1, 1.0)
    assert s["by_destination"] == {"delete": 2}


# ── the CLI ──────────────────────────────────────────────────────────────────

def cli(root, *args):
    r = subprocess.run([sys.executable, str(TOOLS / "inbox_decision_log.py"), *args,
                        "--repo-root", str(root)], capture_output=True, text=True)
    return r.returncode, r.stdout


def test_cli_routes_each_command(root, tmp_path):
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([good()]))
    assert cli(root, "append", "--rows-file", str(rows))[0] == 0
    code, out = cli(root, "status")
    assert code == 0 and json.loads(out)["logged"] == 1
    code, out = cli(root, "append", "--rows-file", str(rows), "--supersede")
    assert code == 0 and json.loads(out)["appended"] == 1


@pytest.mark.parametrize("args,code", [
    (("append",), "missing_arg"),
    (("append", "--rows-file", "/nonexistent.json"), "rows_file_missing"),
    (("approve",), "missing_arg"),
])
def test_cli_refuses_missing_arguments(root, args, code):
    rc, out = cli(root, *args)
    assert rc == 1 and json.loads(out)["code"] == code


def test_cli_approve_signs_or_writes_nothing(root, tmp_path):
    # HOME points at an empty folder, so ~/.ssh/allowed_signers is absent and signing
    # refuses before any agent is asked: this test can never prompt for Touch ID.
    import os
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([good()]))
    cli(root, "append", "--rows-file", str(rows))
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run([sys.executable, str(TOOLS / "inbox_decision_log.py"), "approve",
                        "--rule", "ind:delete:high", "--note", "n", "--repo-root", str(root)],
                       capture_output=True, text=True, env={**os.environ, "HOME": str(home)})
    assert r.returncode == 1 and json.loads(r.stdout)["code"] == "not_signed"
    assert "no key for principal" in json.loads(r.stdout)["message"]
    assert not (root / dlog.APPROVALS_REL).exists()
