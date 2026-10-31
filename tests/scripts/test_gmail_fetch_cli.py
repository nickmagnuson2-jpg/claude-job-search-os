"""Tests for the CLI/orchestration layer of tools/gmail_fetch.py.

main() is driven in-process. Every collaborator that would touch Gmail, OAuth
tokens, or macOS notifications is replaced with a fake:
  - get_or_refresh_creds, check_token_expiry, _seed_history_id,
    fetch_new_messages, fetch_labeled_messages, search_messages, list_labels
  - googleapiclient.discovery is a fake module in sys.modules
  - subprocess.run is a recorder (no osascript ever runs)
All file writes go under tmp_path.
"""
import base64
import json
import os
import subprocess
import sys
import time
import types
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import gmail_fetch as gf


# ── Fixtures ──────────────────────────────────────────────────────────────────

SERVICE = object()


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _msg(msg_id="m1", sender="Northwind Talent <talent@example.com>",
         subject="Role update", body="Hello from Northwind"):
    return {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Mon, 26 Oct 2026 10:00:00 -0700"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64(body)},
        },
    }


@pytest.fixture(autouse=True)
def no_real_subprocess(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


class Env:
    """Records every collaborator call made by main()."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.mp = monkeypatch
        self.repo = tmp_path / "repo"
        (self.repo / "tools").mkdir(parents=True)
        self.inbox = self.repo / "inbox"
        self.state_path = self.repo / "tools" / ".gmail_state.json"
        self.calls = {k: [] for k in (
            "creds", "build", "expiry", "seed", "fetch_new", "fetch_labeled",
            "search", "list_labels", "notify")}
        self.new_messages = []
        self.labeled_messages = []
        self.labels = [{"id": "Label_1", "name": "Contoso", "type": "user"}]

        monkeypatch.delenv("GMAIL_PERSONAL_LABEL_ID", raising=False)
        monkeypatch.delenv("GMAIL_JOB_SEARCH_LABEL_ID", raising=False)
        monkeypatch.setattr(gf, "GMAIL_LABELS_CONF", tmp_path / "absent-labels.conf")

        monkeypatch.setattr(gf, "get_or_refresh_creds",
                            lambda tools_dir, auth_mode=False:
                            self.calls["creds"].append((tools_dir, auth_mode)) or "CREDS")
        monkeypatch.setattr(gf, "check_token_expiry",
                            lambda token_path, inbox_dir:
                            self.calls["expiry"].append((token_path, inbox_dir)) or False)
        monkeypatch.setattr(gf, "_seed_history_id",
                            lambda service, count=50:
                            self.calls["seed"].append((service, count)) or "H-SEED")

        def fetch_new(service, state, label_id=None):
            self.calls["fetch_new"].append((service, dict(state), label_id))
            return list(self.new_messages)

        def fetch_labeled(service, label_id, since_date=None, max_messages=None):
            self.calls["fetch_labeled"].append((service, label_id, since_date, max_messages))
            return list(self.labeled_messages)

        def search(service, query, label_id, max_results, show_body):
            self.calls["search"].append(dict(service=service, query=query, label_id=label_id,
                                             max_results=max_results, show_body=show_body))

        def list_labels(service):
            self.calls["list_labels"].append(service)
            return self.labels

        monkeypatch.setattr(gf, "fetch_new_messages", fetch_new)
        monkeypatch.setattr(gf, "fetch_labeled_messages", fetch_labeled)
        monkeypatch.setattr(gf, "search_messages", search)
        monkeypatch.setattr(gf, "list_labels", list_labels)
        monkeypatch.setattr(gf, "_notify_new_emails",
                            lambda written, senders:
                            self.calls["notify"].append((written, list(senders))))

        fake_discovery = types.ModuleType("googleapiclient.discovery")

        def build(name, version, credentials=None):
            self.calls["build"].append((name, version, credentials))
            return SERVICE

        fake_discovery.build = build
        monkeypatch.setitem(sys.modules, "googleapiclient", types.ModuleType("googleapiclient"))
        monkeypatch.setitem(sys.modules, "googleapiclient.discovery", fake_discovery)

    def break_googleapiclient(self):
        self.mp.setitem(sys.modules, "googleapiclient.discovery", None)

    def write_state(self, state):
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def read_state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def run(self, *argv):
        self.mp.setattr(sys, "argv", ["gmail_fetch.py", "--repo-root", str(self.repo), *argv])
        gf.main()

    def run_exit(self, *argv):
        with pytest.raises(SystemExit) as exc:
            self.run(*argv)
        return exc.value.code


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


# ── _load_state / _save_state ─────────────────────────────────────────────────

def test_load_state_missing_file_is_empty_dict(tmp_path):
    result = gf._load_state(tmp_path / "nope.json")
    assert result == {} and isinstance(result, dict)


def test_load_state_reads_existing_file(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"historyId": "42"}), encoding="utf-8")
    assert gf._load_state(p) == {"historyId": "42"}


def test_load_state_corrupt_json_is_empty_dict(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json", encoding="utf-8")
    result = gf._load_state(p)
    assert result == {} and isinstance(result, dict)


def test_save_state_round_trips(tmp_path):
    p = tmp_path / "s.json"
    gf._save_state(p, {"historyId": "7", "retry_ids": {"m1": 1}})
    assert json.loads(p.read_text(encoding="utf-8")) == {"historyId": "7", "retry_ids": {"m1": 1}}


# ── cleanup_old_inbox_files ───────────────────────────────────────────────────

def _old_gmail_file(path: Path):
    path.write_text('<email-content source="gmail" sanitized="true">x</email-content>',
                    encoding="utf-8")
    old = time.time() - 72 * 3600
    os.utime(path, (old, old))


def test_cleanup_skips_unreadable_entry_and_keeps_going(tmp_path):
    # A directory named *.md raises OSError on read_text; it must be skipped,
    # not crash the loop, and later files are still processed.
    (tmp_path / "a-unreadable.md").mkdir()
    _old_gmail_file(tmp_path / "b-old.md")
    assert gf.cleanup_old_inbox_files(tmp_path, hours=48) == 1
    assert not (tmp_path / "b-old.md").exists()


def test_cleanup_skips_file_whose_mtime_cannot_be_read(tmp_path, monkeypatch):
    _old_gmail_file(tmp_path / "a-old.md")

    class BadStamp(datetime):
        @classmethod
        def fromtimestamp(cls, *a, **k):
            raise OSError("stat failed")

    monkeypatch.setattr(gf, "datetime", BadStamp)
    assert gf.cleanup_old_inbox_files(tmp_path, hours=48) == 0
    assert (tmp_path / "a-old.md").exists()


# ── _clear_auth_failure_alert ─────────────────────────────────────────────────

def test_clear_alert_deletes_existing_file_and_reports_it(tmp_path, capsys):
    alert = tmp_path / "GMAIL-AUTH-FAILURE.md"
    alert.write_text("broken", encoding="utf-8")
    gf._clear_auth_failure_alert(tmp_path)
    assert not alert.exists()
    out = capsys.readouterr()
    assert "Cleared stale auth-failure alert: GMAIL-AUTH-FAILURE.md" in out.out
    assert out.err == ""


def test_clear_alert_absent_file_is_silent(tmp_path, capsys):
    gf._clear_auth_failure_alert(tmp_path)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_clear_alert_unlink_failure_warns_on_stderr(tmp_path, capsys):
    (tmp_path / "GMAIL-AUTH-FAILURE.md").mkdir()  # unlink() on a dir raises OSError
    gf._clear_auth_failure_alert(tmp_path)
    out = capsys.readouterr()
    assert "Warning: could not clear GMAIL-AUTH-FAILURE.md" in out.err
    assert "Cleared" not in out.out


# ── _emit_auth_failure_alert ──────────────────────────────────────────────────

def test_emit_alert_creates_inbox_writes_file_and_notifies(tmp_path, no_real_subprocess):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    gf._emit_auth_failure_alert(tools_dir, "invalid_grant: token revoked")
    alert = tmp_path / "inbox" / "GMAIL-AUTH-FAILURE.md"
    assert alert.exists()
    text = alert.read_text(encoding="utf-8")
    assert "invalid_grant: token revoked" in text
    assert "--auth" in text
    assert len(no_real_subprocess) == 1
    cmd = no_real_subprocess[0][0][0]
    assert cmd[0] == "osascript"
    assert "Gmail Auth FAILED" in cmd[2]


# ── _notify_new_emails ────────────────────────────────────────────────────────

def _noon():
    return datetime(2026, 10, 26, 12, 0)


def _body(calls):
    assert len(calls) == 1
    return calls[0][0][0][2]


def test_notify_zero_written_sends_nothing(no_real_subprocess):
    gf._notify_new_emails(0, [], now=_noon())
    assert no_real_subprocess == []


def test_notify_single_email_singular_noun_and_no_plus(no_real_subprocess):
    gf._notify_new_emails(1, ["Northwind Talent <talent@example.com>"], now=_noon())
    body = _body(no_real_subprocess)
    assert "1 new job email (Northwind Talent) - run /act to triage" in body
    assert no_real_subprocess[0][0][0][0] == "osascript"


def test_notify_multiple_senders_plural_and_plus_count(no_real_subprocess):
    gf._notify_new_emails(3, ["Northwind Talent <a@example.com>", "b@example.com",
                              "Contoso <c@example.com>"], now=_noon())
    assert "3 new job emails (Northwind Talent +2)" in _body(no_real_subprocess)


@pytest.mark.parametrize("hour,fires", [(7, False), (8, True), (12, True), (20, True),
                                        (21, False), (23, False)])
def test_notify_respects_quiet_hours(hour, fires, no_real_subprocess):
    gf._notify_new_emails(2, ["a@example.com"], now=datetime(2026, 10, 26, hour, 30))
    assert (len(no_real_subprocess) == 1) is fires


# ── main(): argument guards ───────────────────────────────────────────────────

def test_job_search_label_resolves_configured_label(env, monkeypatch):
    monkeypatch.setenv("GMAIL_JOB_SEARCH_LABEL_ID", "Label_JS")
    env.run("--job-search-label", "--search", "from:example.com")
    assert env.calls["search"][0]["label_id"] == "Label_JS"


# ── main(): --auth ────────────────────────────────────────────────────────────

def test_auth_mode_seeds_and_saves_state(env, capsys):
    env.run("--auth")
    assert env.calls["creds"] == [(env.repo / "tools", True)]
    assert env.calls["seed"] == [(SERVICE, 50)]
    assert env.read_state()["historyId"] == "H-SEED"
    assert env.calls["fetch_new"] == []
    out = capsys.readouterr().out
    assert "Auth successful. Seeding historyId" in out
    assert f"State saved: {env.state_path} (historyId=H-SEED)" in out
    assert "Setup complete. Schedule via launchd" in out


@pytest.mark.parametrize("argv", [["--auth"], ["--list-labels"], ["--search", "x"],
                                  ["--backfill", "--label-id", "Label_X"], []])
def test_missing_googleapiclient_exits_1_with_message(env, capsys, argv):
    env.write_state({"historyId": "100"})
    env.break_googleapiclient()
    assert env.run_exit(*argv) == 1
    assert "ERROR: googleapiclient not installed." in capsys.readouterr().err


# ── main(): --list-labels ─────────────────────────────────────────────────────

def test_list_labels_prints_json_and_does_not_sync(env, capsys):
    env.write_state({"historyId": "100"})
    env.run("--list-labels")
    assert env.calls["list_labels"] == [SERVICE]
    assert json.loads(capsys.readouterr().out) == env.labels
    assert env.calls["fetch_new"] == []
    assert env.read_state() == {"historyId": "100"}


# ── main(): --search ──────────────────────────────────────────────────────────

def test_search_with_label_id_and_body(env):
    env.write_state({"historyId": "100"})
    env.run("--search", "subject:offer", "--label-id", "Label_X", "--body", "--max", "5")
    assert env.calls["search"] == [dict(service=SERVICE, query="subject:offer",
                                        label_id="Label_X", max_results=5, show_body=True)]
    assert env.calls["fetch_new"] == []
    assert env.read_state() == {"historyId": "100"}


def test_search_all_mail_drops_label_scope(env):
    env.run("--search", "x", "--label-id", "Label_X", "--all-mail")
    assert env.calls["search"][0]["label_id"] is None
    assert env.calls["search"][0]["show_body"] is False


def test_search_defaults_to_job_search_label(env, monkeypatch):
    monkeypatch.setenv("GMAIL_JOB_SEARCH_LABEL_ID", "Label_JS")
    env.run("--search", "x")
    assert env.calls["search"][0]["label_id"] == "Label_JS"
    assert env.calls["search"][0]["max_results"] == 25


# ── main(): --backfill ────────────────────────────────────────────────────────

def test_backfill_without_label_id_exits_1(env, capsys):
    assert env.run_exit("--backfill") == 1
    assert "ERROR: --backfill requires --label-id." in capsys.readouterr().err
    assert env.calls["fetch_labeled"] == []
    assert env.calls["creds"] == []


def test_backfill_writes_files_and_leaves_state_alone(env, capsys):
    env.write_state({"historyId": "100"})
    env.labeled_messages = [_msg("m1"), {"id": "bad", "payload": None}]
    env.run("--backfill", "--label-id", "Label_X", "--since", "2026-01-01", "--max", "3")
    assert env.calls["fetch_labeled"] == [(SERVICE, "Label_X", "2026-01-01", 3)]
    assert env.calls["fetch_new"] == []
    files = list(env.inbox.glob("*.md"))
    assert len(files) == 1
    out = capsys.readouterr()
    assert f"  Wrote: {files[0].name}" in out.out
    assert "Backfill done. Wrote 1 inbox file(s). Forward-sync state unchanged." in out.out
    assert "Warning: could not process message bad" in out.err
    assert env.read_state() == {"historyId": "100"}


def test_backfill_dry_run_writes_nothing(env, capsys):
    env.labeled_messages = [_msg("m1")]
    env.run("--backfill", "--label-id", "Label_X", "--dry-run")
    assert not env.inbox.exists() or list(env.inbox.glob("*.md")) == []
    out = capsys.readouterr().out
    assert "Backfill done. Wrote 0 inbox file(s)." in out
    assert "  Wrote: " not in out


# ── main(): sync mode ─────────────────────────────────────────────────────────

def test_sync_writes_messages_saves_state_and_notifies(env, capsys):
    env.write_state({"historyId": "100"})
    alert = env.inbox / "GMAIL-AUTH-FAILURE.md"
    env.inbox.mkdir()
    alert.write_text("stale", encoding="utf-8")
    env.new_messages = [_msg("m1"), _msg("m2", sender="Contoso <hr@example.com>",
                                         subject="Next steps")]
    env.run("--label-id", "Label_X")

    assert env.calls["expiry"] == [(env.repo / "tools" / "gmail_token.json", env.inbox)]
    assert env.calls["creds"] == [(env.repo / "tools", False)]
    assert env.calls["seed"] == []
    assert env.calls["search"] == [] and env.calls["fetch_labeled"] == []
    assert env.calls["list_labels"] == []
    assert env.calls["fetch_new"][0][0] is SERVICE
    assert env.calls["fetch_new"][0][2] == "Label_X"
    assert not alert.exists()
    assert len(list(env.inbox.glob("*.md"))) == 2
    state = env.read_state()
    assert state["historyId"] == "100" and "last_refresh" in state
    assert env.calls["notify"] == [(2, ["Northwind Talent <talent@example.com>",
                                        "Contoso <hr@example.com>"])]
    out = capsys.readouterr().out
    assert "Found 2 new message(s)." in out
    assert "Done. Wrote 2 inbox file(s)." in out
    assert "Cleaned up" not in out


def test_sync_cleans_up_old_gmail_files_and_reports_count(env, capsys):
    env.write_state({"historyId": "100"})
    env.inbox.mkdir()
    _old_gmail_file(env.inbox / "old.md")
    env.run()
    assert not (env.inbox / "old.md").exists()
    assert "Cleaned up 1 old Gmail inbox file(s)." in capsys.readouterr().out


def test_sync_dry_run_touches_no_state_alert_or_notification(env, capsys):
    env.write_state({"historyId": "100"})
    env.inbox.mkdir()
    alert = env.inbox / "GMAIL-AUTH-FAILURE.md"
    alert.write_text("stale", encoding="utf-8")
    env.new_messages = [_msg("m1")]
    env.run("--dry-run")
    assert alert.exists()
    assert env.read_state() == {"historyId": "100"}
    assert env.calls["notify"] == []
    assert list(env.inbox.glob("*.md")) == [alert]
    assert "Done. Wrote 0 inbox file(s)." in capsys.readouterr().out


def test_sync_without_history_id_seeds_state_and_stops(env, capsys):
    state_file = env.tmp / "nested" / "dir" / "personal_state.json"
    env.new_messages = [_msg("m1")]
    env.run("--state-file", str(state_file))
    assert json.loads(state_file.read_text(encoding="utf-8"))["historyId"] == "H-SEED"
    assert env.calls["seed"] == [(SERVICE, 50)]
    assert env.calls["fetch_new"] == []
    assert env.calls["notify"] == []
    err = capsys.readouterr().err
    assert "No historyId in personal_state.json — auto-seeding" in err
    assert f"Seeded {state_file} with historyId=H-SEED." in err


def test_sync_without_history_id_dry_run_does_not_save(env, capsys):
    env.run("--dry-run")
    assert env.calls["seed"] == [(SERVICE, 50)]
    assert not env.state_path.exists()
    assert env.calls["fetch_new"] == []
