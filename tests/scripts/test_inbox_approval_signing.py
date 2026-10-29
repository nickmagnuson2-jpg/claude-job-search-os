"""S16 (2026-10-27): inbox-drain approvals are Touch ID signatures, and routing commands
run from an allowlist without a shell.

Signing uses a throwaway ed25519 key made in tmp_path, passed as `key_file`, so no test
touches the Secretive agent or needs Nick's fingerprint. Verification is the real
`ssh-keygen -Y verify`.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import approval_signing as sig
import inbox_decision_log as dlog
import inbox_drain_apply as apply


def _key(tmp_path, name):
    k = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(k)],
                   check=True)
    return k


@pytest.fixture
def keys(tmp_path):
    nick, other = _key(tmp_path, "nick"), _key(tmp_path, "other")
    allowed = tmp_path / "allowed_signers"
    allowed.write_text("# comment line\n"
                       f'nick namespaces="{sig.NAMESPACE}" {Path(str(nick) + ".pub").read_text()}')
    return {"nick": nick, "other": other, "allowed": allowed}


ROW = {"rule_id": "abcd1234", "block_ids": ["L1", "L2"], "approved_at": "2026-10-27T23:00:00",
       "approved_by": "nick", "note": "drips", "blocks": 2, "executed": False}


# ── sign / verify ─────────────────────────────────────────────────────────────

def test_a_signature_by_nicks_key_verifies(keys):
    s = sig.sign(sig.payload(ROW), key_file=keys["nick"])
    assert sig.verify(sig.payload(ROW), s, keys["allowed"]) is True


def test_changing_a_signed_field_breaks_the_signature(keys):
    s = sig.sign(sig.payload(ROW), key_file=keys["nick"])
    for field, value in (("block_ids", ["L1", "L2", "L3"]), ("rule_id", "ffff0000"),
                         ("note", "x"), ("approved_by", "claude"), ("approved_at", "2026-10-28")):
        assert sig.verify(sig.payload({**ROW, field: value}), s, keys["allowed"]) is False, field


def test_unsigned_fields_do_not_break_it(keys):
    s = sig.sign(sig.payload(ROW), key_file=keys["nick"])
    assert sig.verify(sig.payload({**ROW, "executed": True, "blocks": 9}), s, keys["allowed"])


def test_a_key_not_in_allowed_signers_is_refused(keys):
    s = sig.sign(sig.payload(ROW), key_file=keys["other"])
    assert sig.verify(sig.payload(ROW), s, keys["allowed"]) is False


def test_a_signature_in_another_namespace_is_refused(keys, tmp_path):
    msg = tmp_path / "m"
    msg.write_bytes(sig.payload(ROW))
    subprocess.run(["ssh-keygen", "-Y", "sign", "-n", "git", "-f", str(keys["nick"]), str(msg)],
                   check=True, capture_output=True)
    other_ns = Path(str(msg) + ".sig").read_text()
    allowed = keys["allowed"]
    allowed.write_text(f'nick {Path(str(keys["nick"]) + ".pub").read_text()}')   # any namespace
    assert sig.verify(sig.payload(ROW), other_ns, allowed) is False


def test_the_key_listed_for_another_principal_is_refused(keys):
    keys["allowed"].write_text(f'claude {Path(str(keys["nick"]) + ".pub").read_text()}')
    s = sig.sign(sig.payload(ROW), key_file=keys["nick"])
    assert sig.verify(sig.payload(ROW), s, keys["allowed"]) is False


def test_missing_signature_or_signers_file_is_refused(keys, tmp_path):
    s = sig.sign(sig.payload(ROW), key_file=keys["nick"])
    assert sig.verify(sig.payload(ROW), None, keys["allowed"]) is False
    assert sig.verify(sig.payload(ROW), "", keys["allowed"]) is False
    assert sig.verify(sig.payload(ROW), s, tmp_path / "absent") is False


def test_payload_is_order_independent_and_names_the_signed_fields():
    assert sig.payload(ROW) == sig.payload(dict(reversed(list(ROW.items()))))
    assert set(json.loads(sig.payload(ROW))) == set(sig.SIGNED_FIELDS)


def test_principal_key_reads_the_listed_key(keys, tmp_path):
    pub = Path(str(keys["nick"]) + ".pub").read_text().split()
    assert sig.principal_key(keys["allowed"]) == f"{pub[0]} {pub[1]}"
    multi = tmp_path / "multi"
    multi.write_text(f"# nick ssh-ed25519 AAAAfake\nalice,nick {pub[0]} {pub[1]}\n")
    assert sig.principal_key(multi) == f"{pub[0]} {pub[1]}"
    assert sig.principal_key(tmp_path / "absent") is None
    multi.write_text(f"alice {pub[0]} {pub[1]}\n")
    assert sig.principal_key(multi) is None


def test_agent_signing_without_a_listed_key_raises(tmp_path):
    with pytest.raises(RuntimeError, match="no key for principal"):
        sig.sign(b"x", allowed_signers=tmp_path / "absent")


# ── the applier counts only signed approvals ──────────────────────────────────

def _approvals(root, rows):
    p = root / dlog.APPROVALS_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_applier_ignores_unsigned_and_stranger_signed_approvals(keys, tmp_path):
    good = {**ROW, "signature": sig.sign(sig.payload(ROW), key_file=keys["nick"])}
    unsigned = {**ROW, "rule_id": "00000001"}
    r2 = {**ROW, "rule_id": "00000002"}
    stranger = {**r2, "signature": sig.sign(sig.payload(r2), key_file=keys["other"])}
    _approvals(tmp_path, [good, unsigned, stranger])
    ok, ignored = apply.signed_approvals(tmp_path, keys["allowed"])
    assert list(ok) == ["abcd1234"]
    assert sorted(ignored) == ["00000001", "00000002"]


def test_a_later_unsigned_row_replaces_a_signed_one(keys, tmp_path):
    # load_approvals keeps the last row per rule; an unsigned re-approval must not
    # inherit the earlier signature.
    good = {**ROW, "signature": sig.sign(sig.payload(ROW), key_file=keys["nick"])}
    _approvals(tmp_path, [good, {**ROW, "note": "edited"}])
    ok, ignored = apply.signed_approvals(tmp_path, keys["allowed"])
    assert ok == {} and ignored == ["abcd1234"]


# ── approve signs before it writes ────────────────────────────────────────────

def _log(root):
    p = root / dlog.LOG_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps({"block_id": b, "mode": "bulk", "bulk_rule": "drips",
                                     "destination": "delete", "confidence": "high"}) + "\n"
                         for b in ("L1", "L2")))
    return dlog.rule_id("drips")


def test_approve_writes_a_row_that_verifies(keys, tmp_path, capsys):
    rid = _log(tmp_path)
    dlog.cmd_approve(tmp_path, [rid], "ok", sign=lambda b: sig.sign(b, key_file=keys["nick"]))
    row = json.loads((tmp_path / dlog.APPROVALS_REL).read_text())
    assert row["approved_by"] == "nick" and row["block_ids"] == ["L1", "L2"]
    assert sig.verify(sig.payload(row), row["signature"], keys["allowed"])
    assert list(apply.signed_approvals(tmp_path, keys["allowed"])[0]) == [rid]


def test_declined_touch_id_writes_nothing(tmp_path, capsys):
    rid = _log(tmp_path)

    def declined(_):
        raise RuntimeError("user cancelled")
    with pytest.raises(SystemExit):
        dlog.cmd_approve(tmp_path, [rid], "ok", sign=declined)
    assert not (tmp_path / dlog.APPROVALS_REL).exists()
    assert "not_signed" in capsys.readouterr().out


def test_approve_has_no_approver_flag():
    r = subprocess.run([sys.executable, str(Path(dlog.__file__)), "approve", "--rule", "x",
                        "--approver", "nick"], capture_output=True, text=True)
    assert r.returncode != 0 and "--approver" in r.stderr


# ── routing commands: allowlist, no shell ─────────────────────────────────────

REAL = [  # shapes taken from the live decision log, 2026-10-27
    'PYTHONIOENCODING=utf-8 python3 tools/todo_write.py add "Tag; log | analyse > ideas" --repo-root .',
    'PYTHONIOENCODING=utf-8 python3 tools/personal_todo_write.py add "2026-05-04 | Renew library card" --repo-root .',
    'PYTHONIOENCODING=utf-8 python3 tools/act_apply.py target-add --company "Acme" --repo-root . '
    '&& PYTHONIOENCODING=utf-8 python3 tools/act_apply.py notes-add --company "Acme" --repo-root .',
]


@pytest.mark.parametrize("cmd", REAL)
def test_logged_command_shapes_are_accepted(cmd, tmp_path):
    pieces = apply.parse_invocation(cmd, tmp_path)
    for argv, env in pieces:
        assert argv[0] == sys.executable
        assert argv[1] == str(tmp_path.resolve() / "tools" / Path(argv[1]).name)
        assert env == {"PYTHONIOENCODING": "utf-8"}
    assert len(pieces) == cmd.count("&&") + 1


def test_quoted_operators_stay_inside_their_argument(tmp_path):
    (argv, _), = apply.parse_invocation(REAL[0], tmp_path)
    assert "Tag; log | analyse > ideas" in argv


@pytest.mark.parametrize("cmd,why", [
    ("python3 tools/todo_write.py add x; rm -rf ~", "operator"),
    ("python3 tools/todo_write.py add x | sh", "operator"),
    ("python3 tools/todo_write.py add x > data/goals.md", "operator"),
    ("python3 tools/todo_write.py add $(whoami)", "operator"),
    ("python3 tools/todo_write.py add x || true", "operator"),
    ("SEAL_READ_OK=1 python3 tools/todo_write.py add x", "environment"),
    ("GUARD_EDIT_APPROVED=1 python3 tools/todo_write.py add x", "environment"),
    ("python tools/todo_write.py add x", "must be"),
    ("git push", "must be"),
    ("python3 -c 'print(1)'", "not an allowed writer"),
    ("python3 tools/check_sealed_read.py", "not an allowed writer"),
    ("python3 tools/../tools/todo_write.py add x", "not an allowed writer"),
    ("python3 other/todo_write.py add x", "not an allowed writer"),
    ("python3 tools/pipe_write.py update --stage Applied", "Applied"),
    ('python3 tools/act_apply.py target-add --company "<each row in x.md>"', "placeholder"),
    ("python3 tools/todo_write.py add x --repo-root /tmp", "outside the repo"),
    ("python3 tools/todo_write.py add x --repo-root data", "must be this repo"),
    ("python3 tools/todo_write.py add ../personal/data/x.md", "outside the repo"),
    ("python3 tools/todo_write.py add ~/x", "outside the repo"),
    ("python3 tools/todo_write.py add x && git push", "must be"),
    ("python3 tools/todo_write.py add 'unterminated", "cannot parse"),
    ("", "must be"),
])
def test_commands_outside_the_allowlist_are_refused(cmd, why, tmp_path):
    with pytest.raises(apply.Refused, match=why):
        apply.parse_invocation(cmd, tmp_path)


def test_an_absolute_path_inside_the_repo_is_allowed(tmp_path):
    cmd = f"python3 tools/todo_write.py add {tmp_path}/data/x.md --repo-root {tmp_path}"
    assert len(apply.parse_invocation(cmd, tmp_path)) == 1


def test_run_invocation_uses_no_shell_and_stops_at_the_first_failure(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "todo_write.py").write_text(
        "import sys, pathlib\n"
        "pathlib.Path(sys.argv[1]).write_text(sys.argv[2])\n")
    (tools / "act_apply.py").write_text("import sys; sys.exit(3)\n")
    cmd = ('python3 tools/todo_write.py first "x; touch pwned" && python3 tools/act_apply.py '
           '&& python3 tools/todo_write.py third y')
    proc = apply.run_invocation(cmd, tmp_path)
    assert proc.returncode == 3
    assert (tmp_path / "first").read_text() == "x; touch pwned"
    assert not (tmp_path / "pwned").exists()
    assert not (tmp_path / "third").exists()


def test_run_invocation_passes_only_the_parsed_env(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "todo_write.py").write_text(
        "import os, pathlib\npathlib.Path('enc').write_text(os.environ.get('PYTHONIOENCODING',''))\n")
    monkeypatch.setenv("PYTHONIOENCODING", "latin-1")
    proc = apply.run_invocation("PYTHONIOENCODING=utf-8 python3 tools/todo_write.py", tmp_path)
    assert proc.returncode == 0
    assert (tmp_path / "enc").read_text() == "utf-8"


# ── the production path: signing through an agent, as Secretive does ─────────

@pytest.fixture
def agent():
    # A short directory: macOS caps a Unix socket path at 104 bytes, and pytest's
    # tmp_path is longer than that.
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="ag", dir="/tmp")
    sock = Path(d) / "s"
    out = subprocess.run(["ssh-agent", "-a", str(sock), "-s"], capture_output=True, text=True,
                         check=True).stdout
    pid = out.split("SSH_AGENT_PID=")[1].split(";")[0]
    yield sock
    subprocess.run(["kill", pid])
    shutil.rmtree(d, ignore_errors=True)


def _add(sock, key):
    import os
    subprocess.run(["ssh-add", "-q", str(key)], check=True, capture_output=True,
                   env={**os.environ, "SSH_AUTH_SOCK": str(sock)})


def test_signing_through_the_agent_with_the_listed_public_key(keys, agent):
    _add(agent, keys["nick"])
    s = sig.sign(sig.payload(ROW), allowed_signers=keys["allowed"], agent_socket=agent)
    assert sig.verify(sig.payload(ROW), s, keys["allowed"])


def test_agent_without_the_listed_key_cannot_sign(keys, agent):
    _add(agent, keys["other"])
    with pytest.raises(RuntimeError, match="signing failed"):
        sig.sign(sig.payload(ROW), allowed_signers=keys["allowed"], agent_socket=agent)


def test_signer_line_names_nick_and_the_namespace(keys, agent):
    _add(agent, keys["nick"])
    line = sig.signer_line(agent)
    pub = Path(str(keys["nick"]) + ".pub").read_text().split()
    assert line == (f'nick namespaces="{sig.NAMESPACE},{sig.CALENDAR_NAMESPACE}" '
                    f'{pub[0]} {pub[1]}')


def test_the_produced_line_verifies_both_namespaces_and_no_other(keys, agent, tmp_path):
    """The calendar gate signs under its own namespace. A line naming only the inbox one
    (the first version of signer_line, 2026-10-27) refused every calendar signature."""
    _add(agent, keys["nick"])
    allowed = tmp_path / "produced_signers"
    allowed.write_text(sig.signer_line(agent) + "\n")
    data = b'{"tool_name":"x"}'
    for ns in sig.NAMESPACES:
        s = sig.sign(data, key_file=keys["nick"], namespace=ns)
        assert sig.verify(data, s, allowed, namespace=ns) is True, ns
    cal = sig.sign(data, key_file=keys["nick"], namespace=sig.CALENDAR_NAMESPACE)
    assert sig.verify(data, cal, allowed) is False          # no cross-namespace replay
    git = sig.sign(data, key_file=keys["nick"], namespace="git")
    assert sig.verify(data, git, allowed, namespace="git") is False


def test_the_old_inbox_only_line_refuses_a_calendar_signature(keys):
    data = b"{}"
    s = sig.sign(data, key_file=keys["nick"], namespace=sig.CALENDAR_NAMESPACE)
    assert sig.verify(data, s, keys["allowed"], namespace=sig.CALENDAR_NAMESPACE) is False


def test_signer_line_refuses_an_agent_with_two_keys(keys, agent):
    _add(agent, keys["nick"])
    _add(agent, keys["other"])
    with pytest.raises(RuntimeError, match="exactly one key"):
        sig.signer_line(agent)


def test_cli_prints_usage_on_unknown_args(capsys):
    assert sig.main(["bogus"]) == 2
    assert "signer-line" in capsys.readouterr().out


def test_cli_signer_line_prints_the_line_and_exits_0(monkeypatch, capsys):
    monkeypatch.setattr(sig, "signer_line", lambda: "nick k")
    assert sig.main(["signer-line"]) == 0
    assert capsys.readouterr().out == "nick k\n"


def test_cli_reads_sys_argv_when_called_without_args(monkeypatch, capsys):
    monkeypatch.setattr(sig, "signer_line", lambda: "nick k")
    monkeypatch.setattr(sys, "argv", ["approval_signing.py", "signer-line"])
    assert sig.main() == 0
    assert "nick k" in capsys.readouterr().out


def test_cli_usage_starts_with_the_docstring_title(capsys):
    assert sig.main([]) == 2
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("approval_signing.py")


def test_script_entry_point_exits_with_main_code():
    r = subprocess.run([sys.executable, str(Path(sig.__file__)), "bogus"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "signer-line" in r.stdout


def test_ordinary_text_that_contains_dots_is_not_path_checked(tmp_path):
    # Only words that START like a path (/, ~, ..) or follow --repo-root are resolved;
    # a todo's text is text even when it contains `../`.
    cmd = 'python3 tools/todo_write.py add "see notes/../../../elsewhere" --repo-root .'
    assert len(apply.parse_invocation(cmd, tmp_path)) == 1
