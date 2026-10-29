"""Tests for tools/sent_artifact_check.py (proposal 1c-D): flag artifacts sent by Gmail with no
record. Fixture messages are dicts in the shape Gmail's messages.get(format="full") returns.
No network: the service is a fake object. Placeholders only (public repo).
"""
import base64
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
import sent_artifact_check as sac  # noqa: E402

BIO = ["bio-site.example.com", "github.com/casey-doe", "linkedin.com/in/casey-doe"]
TS = int(datetime(2026, 10, 20, 12, 0).timestamp() * 1000)
DAY = "2026-10-20"


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def leaf(mime, text):
    return {"mimeType": mime, "filename": "", "body": {"data": b64(text)}}


def attach(name, mime="application/pdf"):
    return {"mimeType": mime, "filename": name, "body": {"attachmentId": "att1", "size": 10}}


def message(*parts, mid="m1", to="Casey Doe <casey@acme.example>", subject="Hello",
            ts=TS):
    headers = [{"name": "To", "value": to}, {"name": "Subject", "value": subject}]
    return {"id": mid, "internalDate": str(ts),
            "payload": {"mimeType": "multipart/mixed", "headers": headers, "filename": "",
                        "body": {}, "parts": list(parts)}}


def alt(plain, html):
    return {"mimeType": "multipart/alternative", "filename": "", "body": {},
            "parts": [leaf("text/plain", plain), leaf("text/html", html)]}


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "tools" / sac.BIO_NAME).write_text(json.dumps({"bio_urls": BIO}))
    return tmp_path


def ctx_for(repo):
    return sac.build_context(repo)


def check(repo, msg):
    return sac.classify(msg, ctx_for(repo))


BIO_LINE = "Casey Doe\nbio-site.example.com | github.com/casey-doe | linkedin.com/in/casey-doe"


# ---- the spec fixtures (line 47) -------------------------------------------------------

def test_view_the_planner_link_only_in_html_part_flags(repo):
    msg = message(alt("Hi Acme team,\nView the planner\nThanks",
                      '<p>Hi Acme team,</p><a href="https://planner.acme-demo.app/q3">'
                      'View the planner</a>'))
    flag = check(repo, msg)
    assert flag is not None
    assert flag["urls"] == ["https://planner.acme-demo.app/q3"]
    assert flag["to"] == ["casey@acme.example"] and flag["date"] == DAY
    assert flag["subject"] == "Hello" and flag["id"] == "m1"


def test_bio_domain_with_a_path_flags(repo):
    msg = message(leaf("text/plain", "See bio-site.example.com/regenerate/103 for it"))
    assert check(repo, msg)["urls"] == ["bio-site.example.com/regenerate/103"]


def test_pdf_attachment_flags(repo):
    msg = message(leaf("text/plain", "Attached."), attach("acme-planner.pdf"))
    flag = check(repo, msg)
    assert flag["attachments"] == ["acme-planner.pdf"] and flag["urls"] == []


def write_cv(repo, name, yaml=True, sub="acme"):
    d = repo / "output" / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"%PDF")
    if yaml:
        (d / name).with_suffix(".yaml").write_text("cv: {}")


def test_cv_attachment_with_its_yaml_passes(repo):
    write_cv(repo, "102026-magnuson.pdf")
    write_cv(repo, "102026-magnuson-v2.pdf", sub="other")
    assert check(repo, message(leaf("text/plain", BIO_LINE), attach("102026-magnuson.pdf"))) is None
    assert check(repo, message(attach("102026-magnuson-v2.pdf"))) is None


def test_cv_named_attachment_without_yaml_flags(repo):
    write_cv(repo, "102026-magnuson.pdf", yaml=False)
    flag = check(repo, message(attach("102026-magnuson.pdf")))
    assert flag["attachments"] == ["102026-magnuson.pdf"]


def test_cv_name_with_no_file_under_output_flags(repo):
    assert check(repo, message(attach("102026-magnuson.pdf"))) is not None


def test_cv_yaml_must_sit_beside_the_pdf(repo):
    write_cv(repo, "102026-magnuson.pdf", yaml=False, sub="a")
    (repo / "output" / "b").mkdir()
    (repo / "output" / "b" / "102026-magnuson.yaml").write_text("cv: {}")
    assert check(repo, message(attach("102026-magnuson.pdf"))) is not None


@pytest.mark.parametrize("name", ["102026-magnuson-deck.pdf", "1020-magnuson.pdf",
                                  "102026-magnuson.html", "x102026-magnuson.pdf",
                                  "102026-magnuson-v.pdf"])
def test_cv_rule_is_exact_name_only(repo, name):
    write_cv(repo, name)
    assert check(repo, message(attach(name))) is not None


def test_cv_pass_does_not_cover_another_item_on_the_same_message(repo):
    write_cv(repo, "102026-magnuson.pdf")
    flag = check(repo, message(leaf("text/plain", "and https://acme-demo.app/x"),
                               attach("102026-magnuson.pdf")))
    assert flag["attachments"] == [] and flag["urls"] == ["https://acme-demo.app/x"]


def test_reply_in_attachment_thread_with_no_attachment_of_its_own_passes(repo):
    plain = ("Thanks, will do.\n\nOn Mon, Oct 19, 2026 at 3:14 PM Casey Doe <\n"
             "casey@acme.example> wrote:\n> see https://acme.example/careers/42\n")
    html = ('<div>Thanks, will do.</div><div class="gmail_quote"><div class="gmail_attr">'
            'On Mon, Casey Doe wrote:</div><blockquote class="gmail_quote">'
            '<a href="https://acme.example/careers/42">posting</a><br>'
            'https://acme.example/careers/42</blockquote></div>')
    msg = message(alt(plain, html), subject="Re: planner")
    assert check(repo, msg) is None


def test_text_after_a_reply_quote_closes_is_still_scanned(repo):
    html = ('<div class="gmail_quote"><blockquote><br>quoted</blockquote></div>'
            '<p><a href="https://acme-demo.app/after">new</a></p>')
    assert check(repo, message(leaf("text/html", html)))["urls"] == ["https://acme-demo.app/after"]


def test_plain_quote_marker_lines_are_skipped_but_new_text_is_not(repo):
    plain = "New: https://acme-demo.app/new\n> old https://acme.example/old\n"
    assert check(repo, message(leaf("text/plain", plain)))["urls"] == ["https://acme-demo.app/new"]


def test_quote_after_on_wrote_line_is_skipped_even_without_markers(repo):
    plain = ("Sounds good.\n\nOn Mon, Oct 19, 2026 at 3:14 PM Casey Doe wrote:\n"
             "see https://acme.example/old\n")
    assert check(repo, message(leaf("text/plain", plain))) is None


def test_visible_html_text_url_without_href_flags(repo):
    html = "<p>see https://acme-demo.app/t today</p>"
    assert check(repo, message(leaf("text/html", html)))["urls"] == ["https://acme-demo.app/t"]


def test_trailing_html_text_with_no_closing_tag_is_read(repo):
    # HTMLParser holds back trailing text that ends in an unterminated "&..." until close().
    html = "<div>x</div>see https://acme-demo.app/end?a=1&b"
    assert check(repo, message(leaf("text/html", html)))["urls"] == ["https://acme-demo.app/end?a=1&b"]


def test_attached_text_file_is_an_attachment_not_a_body(repo):
    part = {"mimeType": "text/plain", "filename": "notes.txt",
            "body": {"data": b64("https://acme-demo.app/inside")}}
    flag = check(repo, message(part))
    assert flag["attachments"] == ["notes.txt"] and flag["urls"] == []


def test_text_leaf_with_empty_body_is_skipped(repo):
    part = {"mimeType": "text/plain", "filename": "", "body": {"size": 0}}
    assert check(repo, message(part)) is None


def test_forwarded_content_is_scanned(repo):
    plain = ("FYI\n---------- Forwarded message ---------\nOn Mon Casey wrote:\n"
             "> https://acme-demo.app/fwd\n")
    html = ('<div class="gmail_quote">---------- Forwarded message ---------'
            '<a href="https://acme-demo.app/fwd">x</a></div>')
    assert check(repo, message(leaf("text/plain", plain)))["urls"] == ["https://acme-demo.app/fwd"]
    assert check(repo, message(leaf("text/html", html)))["urls"] == ["https://acme-demo.app/fwd"]


def test_custom_domain_link_flags(repo):
    msg = message(leaf("text/plain", "Here it is: https://casey-for-acme.com/brief"))
    assert check(repo, msg)["urls"] == ["https://casey-for-acme.com/brief"]


def test_bare_bio_line_passes(repo):
    html = ('<div>Casey Doe<br><a href="http://bio-site.example.com/">bio-site.example.com</a>'
            ' | <a href="https://www.github.com/casey-doe">github.com/casey-doe</a> | '
            '<a href="https://linkedin.com/in/casey-doe/">linkedin.com/in/casey-doe</a></div>')
    assert check(repo, message(alt(BIO_LINE, html))) is None


def test_ack_file_match_passes(repo):
    (repo / "data").mkdir()
    (repo / "data" / "sent-artifact-ack.md").write_text(
        "# Acks\n\n- m9 | demo link Nick approved\nm7 |   \n# m5 | a heading, not an ack\n")
    msg = message(leaf("text/plain", "https://acme-demo.app/x"), mid="m9")
    assert check(repo, msg) is None
    # an ack line with no reason does not count
    assert check(repo, message(leaf("text/plain", "https://acme-demo.app/x"), mid="m7"))
    assert check(repo, message(leaf("text/plain", "https://acme-demo.app/x"), mid="m8"))
    assert check(repo, message(leaf("text/plain", "https://acme-demo.app/x"), mid="m5"))


def test_missing_ack_file_is_empty_and_not_created(repo):
    assert sac.load_ack(repo) == set()
    assert not (repo / "data" / "sent-artifact-ack.md").exists()


def build_log(repo, body, sub="acme/planner"):
    d = repo / "output" / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / "BUILD-LOG.md").write_text("# Build log\n\nreader: Casey Doe\n"
                                    "deploy_verify: https://acme-demo.app/x\n" + body)


def test_build_log_sent_pointer_match_passes(repo):
    (repo / "data").mkdir()
    (repo / "data" / "outreach-log.md").write_text(
        "| Date | Skill | Channel | Recipient | Company | Summary | Status |\n"
        f"| {DAY} | follow-up | email | Casey Doe | Acme | planner | Sent |\n")
    build_log(repo, "sent: data/outreach-log.md:2\n")
    msg = message(leaf("text/plain", "https://acme-demo.app/x"))
    assert check(repo, msg) is None


def test_build_log_direct_sent_line_matches_by_address(repo):
    build_log(repo, f"- **sent:** {DAY} to casey@acme.example\n")
    msg = message(leaf("text/plain", "https://acme-demo.app/x"), to="casey@acme.example")
    assert check(repo, msg) is None


@pytest.mark.parametrize("body", [
    "sent: 2026-10-19 Casey Doe\n",                      # wrong date
    f"sent: {DAY} Jordan Roe\n",                         # wrong recipient
    f"reader_ask: {DAY} Casey Doe\n",                     # not a sent: line
    "sent: data/outreach-log.md:99\n",                   # pointer past the end
])
def test_build_log_without_matching_sent_line_flags(repo, body):
    build_log(repo, body)
    assert check(repo, message(leaf("text/plain", "https://acme-demo.app/x"))) is not None


def test_a_logged_send_explains_only_the_items_its_log_names(repo):
    """One logged send must not excuse every other artifact sent to that person that day."""
    build_log(repo, f"sent: {DAY} Casey Doe\n")
    msg = message(leaf("text/plain", "https://acme-demo.app/x and https://other-build.app/y"),
                  attach("unlogged.pdf"))
    flag = check(repo, msg)
    assert flag["urls"] == ["https://other-build.app/y"]
    assert flag["attachments"] == ["unlogged.pdf"]


def test_a_log_naming_the_attachment_explains_it(repo):
    build_log(repo, f"sent: {DAY} Casey Doe\nmessage: attached planner.pdf\n")
    assert check(repo, message(leaf("text/plain", "hi"), attach("planner.pdf"))) is None


def test_build_log_matches_a_cc_recipient(repo):
    build_log(repo, f"sent: {DAY} jordan@acme.example\n")
    msg = message(leaf("text/plain", "https://acme-demo.app/x"))
    msg["payload"]["headers"].append({"name": "Cc", "value": "Jordan Roe <jordan@acme.example>"})
    assert check(repo, msg) is None


# ---- extraction details ----------------------------------------------------------------

def test_urls_are_deduplicated_across_parts(repo):
    msg = message(alt("go https://acme-demo.app/x.", '<a href="https://acme-demo.app/x/">x</a>'))
    assert check(repo, msg)["urls"] == ["https://acme-demo.app/x"]


def test_email_addresses_are_not_read_as_domains(repo):
    assert check(repo, message(leaf("text/plain", "write to casey@acme.com today"))) is None


def test_mailto_href_is_not_a_url(repo):
    assert check(repo, message(leaf("text/html", '<a href="mailto:casey@acme.example">m</a>'))) is None


def test_www_and_bare_domains_are_found(repo):
    flag = check(repo, message(leaf("text/plain", "www.acme-demo.example/x and acme-demo.app")))
    assert flag["urls"] == ["www.acme-demo.example/x", "acme-demo.app"]


def test_bio_url_with_query_is_not_exempt(repo):
    assert check(repo, message(leaf("text/plain", "https://linkedin.com/in/casey-doe?x=1")))


def test_large_body_is_fetched_by_attachment_id(repo):
    part = {"mimeType": "text/html", "filename": "", "body": {"attachmentId": "big"}}
    seen = []

    def fetch(aid):
        seen.append(aid)
        return b64('<a href="https://acme-demo.app/big">x</a>')
    flag = sac.classify(message(part), ctx_for(repo), fetch)
    assert seen == ["big"] and flag["urls"] == ["https://acme-demo.app/big"]


def test_non_text_leaf_without_filename_is_ignored(repo):
    part = {"mimeType": "text/calendar", "filename": "", "body": {"data": b64("https://a.com")}}
    assert check(repo, message(part)) is None


def test_missing_bio_config_exempts_nothing_and_reports_it(repo):
    (repo / "tools" / sac.BIO_NAME).unlink()
    ctx = ctx_for(repo)
    assert ctx["bio"] == set() and ctx["errors"]
    assert sac.classify(message(leaf("text/plain", BIO_LINE)), ctx) is not None


def test_normalize_url():
    assert sac.normalize_url("HTTPS://WWW.Acme.COM/Path/") == "acme.com/Path"
    assert sac.normalize_url("acme.com.") == "acme.com"


# ---- scan, state, CLI ------------------------------------------------------------------

class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self):
        if isinstance(self.v, Exception):
            raise self.v
        return self.v


class FakeService:
    def __init__(self, pages, msgs):
        self.pages, self.msgs, self.list_calls = pages, msgs, []
        self.bodies = {}

    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return self

    def list(self, **kw):
        self.list_calls.append(kw)
        if len(self.list_calls) > 10:
            raise RuntimeError("pagination never ended")
        return _Exec(self.pages[kw.get("pageToken")])

    def get(self, userId, id, format=None, messageId=None):
        if messageId:
            return _Exec({"data": b64(self.bodies[(messageId, id)])})
        return _Exec(self.msgs[id])


def test_scan_pages_reads_sent_only_and_keeps_going_after_an_error(repo):
    flagged = message(attach("acme.pdf"), mid="a")
    svc = FakeService({None: {"messages": [{"id": "a"}, {"id": "bad"}], "nextPageToken": "p2"},
                       "p2": {"messages": [{"id": "c"}]}},
                      {"a": flagged, "bad": RuntimeError("boom"),
                       "c": message(leaf("text/plain", "thanks"), mid="c")})
    out = sac.scan(svc, ctx_for(repo), "2026-10-01")
    assert out["scanned"] == 3 and [f["id"] for f in out["flags"]] == ["a"]
    assert out["since"] == "2026-10-01"
    assert len(out["errors"]) == 1 and "bad" in out["errors"][0]
    assert all(kw["labelIds"] == ["SENT"] for kw in svc.list_calls)
    # The reach line states the population and every exclusion (tools/count_report.py).
    assert "3" in out["reach"] and "1" in out["reach"]
    assert "message read failed" in out["reach"] and "explained or nothing to check" in out["reach"]
    after = int(datetime(2026, 10, 1).timestamp())
    assert svc.list_calls[0]["q"] == f"after:{after}"
    assert "pageToken" not in svc.list_calls[0]
    assert svc.list_calls[1]["pageToken"] == "p2" and len(svc.list_calls) == 2


def test_scan_fetches_a_large_body_by_attachment_id(repo):
    part = {"mimeType": "text/html", "filename": "", "body": {"attachmentId": "big"}}
    svc = FakeService({None: {"messages": [{"id": "a"}]}}, {"a": message(part, mid="a")})
    svc.bodies[("a", "big")] = '<a href="https://acme-demo.app/big">x</a>'
    out = sac.scan(svc, ctx_for(repo), "2026-10-01")
    assert out["flags"][0]["urls"] == ["https://acme-demo.app/big"]


def test_state_default_since_and_save(tmp_path):
    p = tmp_path / "s.json"
    today = date(2026, 10, 26)
    assert sac.default_since(p, today) == "2026-10-19"
    sac.save_state(p, today, [])
    assert sac.default_since(p, today) == "2026-10-26"
    sac.save_state(p, today, [{"date": "2026-10-22"}, {"date": "2026-10-24"}])
    assert sac.default_since(p, today) == "2026-10-22"
    assert json.loads(p.read_text())["last_run"] == "2026-10-26"


def _run_main(monkeypatch, repo, svc, *args):
    monkeypatch.setattr(sac, "connect", lambda tools_dir, persist=True: svc)
    return sac.main(["--repo-root", str(repo), *args])


def test_main_json_output_and_state_written(repo, monkeypatch, capsys):
    svc = FakeService({None: {"messages": [{"id": "a"}]}}, {"a": message(attach("x.pdf"), mid="a")})
    rc = _run_main(monkeypatch, repo, svc, "--since", "2026-10-01", "--json")
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert set(out) == {"since", "scanned", "reach", "flags", "errors"}
    assert out["flags"][0]["attachments"] == ["x.pdf"]
    assert json.loads((repo / "tools" / sac.STATE_NAME).read_text())["next_since"] == DAY


def test_main_no_state_leaves_state_alone(repo, monkeypatch, capsys):
    svc = FakeService({None: {"messages": []}}, {})
    assert _run_main(monkeypatch, repo, svc, "--since", "2026-10-01", "--no-state") == 0
    assert not (repo / "tools" / sac.STATE_NAME).exists()
    assert "0 scanned" in capsys.readouterr().out


def test_main_uses_state_since_by_default(repo, monkeypatch, capsys):
    (repo / "tools" / sac.STATE_NAME).write_text(json.dumps({"next_since": "2026-09-30"}))
    svc = FakeService({None: {"messages": []}}, {})
    _run_main(monkeypatch, repo, svc, "--json")
    assert json.loads(capsys.readouterr().out)["since"] == "2026-09-30"


def test_main_human_output_lists_flags(repo, monkeypatch, capsys):
    svc = FakeService({None: {"messages": [{"id": "a"}]}},
                      {"a": message(leaf("text/plain", "https://acme-demo.app/z"), mid="a")})
    assert _run_main(monkeypatch, repo, svc, "--since", "2026-10-01") == 0
    out = capsys.readouterr().out
    assert "1 flag(s)" in out and "https://acme-demo.app/z" in out and "casey@acme.example" in out


def test_main_exits_1_when_a_message_errored(repo, monkeypatch, capsys):
    svc = FakeService({None: {"messages": [{"id": "bad"}]}}, {"bad": RuntimeError("x")})
    assert _run_main(monkeypatch, repo, svc, "--since", "2026-10-01", "--json") == 1


@pytest.mark.parametrize("exc", [SystemExit(1), RuntimeError("invalid_grant")])
def test_auth_failure_exits_nonzero_and_never_prints_zero_flags(repo, monkeypatch, capsys, exc):
    def boom(tools_dir, persist=True):
        raise exc
    monkeypatch.setattr(sac, "connect", boom)
    rc = sac.main(["--repo-root", str(repo), "--json"])
    cap = capsys.readouterr()
    assert rc == 2 and cap.out == "" and "ERROR" in cap.err
    assert not (repo / "tools" / sac.STATE_NAME).exists()


def test_bad_since_is_rejected_before_any_connection(repo, monkeypatch, capsys):
    def never(tools_dir, persist=True):
        raise AssertionError("connected")
    monkeypatch.setattr(sac, "connect", never)
    assert sac.main(["--repo-root", str(repo), "--since", "10/01/2026"]) == 2
    assert "--since must be YYYY-MM-DD" in capsys.readouterr().err


def test_script_entry_point_exits_nonzero(repo):
    import subprocess
    r = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "sent_artifact_check.py"),
                        "--repo-root", str(repo), "--since", "bad"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and r.stdout == "" and "--since" in r.stderr


def test_main_human_output_lists_errors(repo, monkeypatch, capsys):
    svc = FakeService({None: {"messages": [{"id": "bad"}]}}, {"bad": RuntimeError("kaput")})
    _run_main(monkeypatch, repo, svc, "--since", "2026-10-01")
    assert "error: bad: RuntimeError: kaput" in capsys.readouterr().out


def test_connect_uses_gmail_fetch_creds(monkeypatch):
    import types
    calls = {}
    fake = types.ModuleType("googleapiclient.discovery")
    fake.build = lambda name, ver, credentials: calls.setdefault("b", (name, ver, credentials))
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", fake)
    monkeypatch.setattr(sac, "get_or_refresh_creds", lambda d: ("creds", d))
    assert sac.connect(Path("/t")) == ("gmail", "v1", ("creds", Path("/t")))


def test_imports_gmail_helpers_rather_than_copying_them():
    import gmail_attachments
    import gmail_fetch
    assert sac.walk_parts is gmail_attachments.walk_parts
    assert sac.get_or_refresh_creds is gmail_fetch.get_or_refresh_creds
    assert sac._extract_header is gmail_fetch._extract_header


# ---- images pasted into the body are not attachments (2026-10-26 replay) ---------------

def image(name, cid=None, disposition="attachment"):
    headers = [{"name": "Content-Disposition", "value": f'{disposition}; filename="{name}"'}]
    if cid:
        headers.append({"name": "Content-ID", "value": f"<{cid}>"})
    return {"mimeType": "image/png", "filename": name, "headers": headers,
            "body": {"attachmentId": "img1", "size": 10}}


def test_an_image_the_html_body_shows_by_cid_is_not_an_attachment(repo):
    """Gmail marks a pasted image `attachment`; the cid: reference in the HTML is the tell."""
    msg = message(alt("Here is the view.", '<p>Here is the view.</p><img src="cid:ii_abc">'),
                  image("Screenshot.png", cid="ii_abc"))
    assert check(repo, msg) is None


def test_an_attached_image_the_body_does_not_show_still_flags(repo):
    msg = message(alt("Attached.", "<p>Attached.</p>"), image("mockup.png", cid="f_xyz"))
    assert check(repo, msg)["attachments"] == ["mockup.png"]


def test_an_image_with_no_content_id_still_flags(repo):
    msg = message(alt("Attached.", '<p>Attached.</p><img src="cid:other">'), image("mockup.png"))
    assert check(repo, msg)["attachments"] == ["mockup.png"]


def test_a_cid_reference_in_plain_text_does_not_hide_an_image(repo):
    """Only the HTML part renders an image; a cid: string in the plain part shows nothing."""
    msg = message(leaf("text/plain", "see cid:ii_abc"), image("mockup.png", cid="ii_abc"))
    assert check(repo, msg)["attachments"] == ["mockup.png"]


def test_a_pdf_with_a_referenced_content_id_still_flags(repo):
    part = image("planner.pdf", cid="ii_pdf")
    part["mimeType"] = "application/pdf"
    msg = message(alt("x", '<img src="cid:ii_pdf">'), part)
    assert check(repo, msg)["attachments"] == ["planner.pdf"]


def test_content_id_matching_ignores_case_and_brackets():
    assert sac.content_id({"headers": [{"name": "Content-ID", "value": "<II_Abc>"}]}) == "ii_abc"
    assert sac.content_id({"headers": []}) == ""


def test_an_attachment_is_never_downloaded_to_look_for_links(repo):
    """Only text parts are read for links; fetching a PDF's bytes would be wasted Gmail calls."""
    calls = []
    msg = message(alt("See attached.", "<p>See attached.</p>"), attach("planner.pdf"))
    sac.classify(msg, ctx_for(repo), fetch_body=lambda aid: calls.append(aid) or "")
    assert calls == []


def test_a_non_image_part_is_never_inline():
    assert sac.is_inline_image({"mimeType": "application/pdf",
                                "headers": [{"name": "Content-ID", "value": "<x>"}]}, {"x"}) is False


def test_a_logged_url_matches_whatever_its_letter_case(repo):
    """Round 12 (702.F4): the log names the link in lower case, the message in mixed case."""
    build_log(repo, f"sent: {DAY} Casey Doe\n")
    assert check(repo, message(leaf("text/plain", "https://Acme-Demo.app/X"))) is None



# --- --no-state writes nothing, including the Gmail token (S4 review, 102926 grok F1) ----

def test_main_passes_persist_from_no_state(repo, monkeypatch, capsys):
    seen = []
    svc = FakeService({None: {"messages": []}}, {})

    def fake(tools_dir, persist=True):
        seen.append(persist)
        return svc
    monkeypatch.setattr(sac, "connect", fake)
    sac.main(["--repo-root", str(repo), "--since", "2026-10-01", "--no-state"])
    sac.main(["--repo-root", str(repo), "--since", "2026-10-01"])
    assert seen == [False, True]


def _fake_google(monkeypatch, creds):
    import types
    oc = types.ModuleType("google.oauth2.credentials")
    oc.Credentials = types.SimpleNamespace(from_authorized_user_file=lambda path, scopes: creds)
    tr = types.ModuleType("google.auth.transport.requests")
    tr.Request = lambda: "request"
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", oc)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", tr)


class FakeCreds:
    def __init__(self, valid, expired=True, refresh_token="r"):
        self.valid, self.expired, self.refresh_token = valid, expired, refresh_token
        self.refreshed = []

    def refresh(self, req):
        self.refreshed.append(req)


def test_read_only_creds_refreshes_in_memory_and_leaves_the_token_file(tmp_path, monkeypatch):
    tok = tmp_path / "gmail_token.json"
    tok.write_text('{"token": "old"}')
    before = tok.stat().st_mtime_ns
    creds = FakeCreds(valid=False)
    _fake_google(monkeypatch, creds)
    assert sac.read_only_creds(tmp_path) is creds
    assert creds.refreshed == ["request"]
    assert tok.read_text() == '{"token": "old"}' and tok.stat().st_mtime_ns == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gmail_token.json"]


def test_read_only_creds_does_not_refresh_a_valid_token(tmp_path, monkeypatch):
    (tmp_path / "gmail_token.json").write_text("{}")
    creds = FakeCreds(valid=True)
    _fake_google(monkeypatch, creds)
    assert sac.read_only_creds(tmp_path) is creds and creds.refreshed == []


@pytest.mark.parametrize("creds", [FakeCreds(valid=False, expired=False),
                                   FakeCreds(valid=False, refresh_token=None)])
def test_read_only_creds_raises_and_writes_no_alert(tmp_path, monkeypatch, creds):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "gmail_token.json").write_text("{}")
    _fake_google(monkeypatch, creds)
    with pytest.raises(RuntimeError, match="cannot be refreshed"):
        sac.read_only_creds(tmp_path / "tools")
    assert not (tmp_path / "inbox").exists() and creds.refreshed == []


def test_read_only_creds_without_a_token_raises(tmp_path, monkeypatch):
    _fake_google(monkeypatch, FakeCreds(valid=True))
    with pytest.raises(RuntimeError, match="no Gmail token"):
        sac.read_only_creds(tmp_path)


def test_connect_read_only_never_uses_the_writing_helper(monkeypatch):
    import types
    fake = types.ModuleType("googleapiclient.discovery")
    fake.build = lambda name, ver, credentials: (name, ver, credentials)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", fake)

    def writing(d):
        raise AssertionError("get_or_refresh_creds writes the token")
    monkeypatch.setattr(sac, "get_or_refresh_creds", writing)
    monkeypatch.setattr(sac, "read_only_creds", lambda d: ("ro", d))
    assert sac.connect(Path("/t"), persist=False) == ("gmail", "v1", ("ro", Path("/t")))
