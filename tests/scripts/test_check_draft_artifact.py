"""The staged-draft artifact gate: a draft carrying an attachment or a non-bio link needs an
ARTIFACT: header that accounts for it.

Each test names the behaviour it protects: the attachment branch, the link branch, the bio
exemption, the CV exemption, both header forms, the log's reader_ask, and the entry point.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_draft_artifact as cda  # noqa: E402

SCRIPT = Path(cda.__file__).resolve()
BIO = ["bio-site.example.com", "github.com/casey-doe", "linkedin.com/in/casey-doe"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    (tmp_path / "output" / "acme").mkdir(parents=True)
    (tmp_path / "tools" / ".sent-artifact-bio.json").write_text(json.dumps({"bio_urls": BIO}))
    return tmp_path


def draft(body="Hi Casey,\n\nThanks.\n", attach=(), artifact=None, subject="Quick note"):
    lines = ["TO: casey@acme.example", f"SUBJECT: {subject}"]
    lines += [f"ATTACH: {a}" for a in attach]
    if artifact is not None:
        lines.append(f"ARTIFACT: {artifact}")
    return "\n".join(lines) + "\nBODY:\n" + body


def log(repo, text, rel="output/acme/BUILD-LOG.md"):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return rel


FILLED = 'reader: Casey Doe, VP Ops\nreader_ask: "Can you show me the intake?" (sources/jd.md:3)\n'


# --- what passes with no header ---------------------------------------------------------

def test_a_plain_text_draft_passes(repo):
    assert cda.decide(draft(), repo) is None


def test_the_bio_line_passes(repo):
    body = "Casey\nbio-site.example.com | github.com/casey-doe | linkedin.com/in/casey-doe\n"
    assert cda.decide(draft(body), repo) is None


def test_a_rendered_cv_attachment_passes(repo):
    (repo / "output" / "acme" / "102626-magnuson.pdf").write_bytes(b"%PDF")
    (repo / "output" / "acme" / "102626-magnuson.yaml").write_text("cv: {}")
    assert cda.decide(draft(attach=["/Users/x/to-send/102626-magnuson.pdf"]), repo) is None


# --- what needs a header ----------------------------------------------------------------

def test_an_attachment_with_no_header_blocks(repo):
    msg = cda.decide(draft(attach=["/tmp/planner.pdf"]), repo)
    assert msg and "BLOCKED check_draft_artifact" in msg and "/tmp/planner.pdf" in msg
    assert "no ARTIFACT: header" in msg


def test_a_cv_named_file_without_its_yaml_blocks(repo):
    (repo / "output" / "acme" / "102626-magnuson.pdf").write_bytes(b"%PDF")
    assert cda.decide(draft(attach=["/tmp/102626-magnuson.pdf"]), repo)


def test_a_custom_domain_link_blocks(repo):
    msg = cda.decide(draft("See https://acme-demo.example.app/plan for the plan.\n"), repo)
    assert msg and "acme-demo.example.app/plan" in msg


def test_a_path_on_a_bio_domain_blocks(repo):
    assert cda.decide(draft("The page: bio-site.example.com/regenerate/103\n"), repo)


def test_a_link_in_the_subject_blocks(repo):
    assert cda.decide(draft(subject="Plan at https://acme-demo.example.app"), repo)


def test_a_link_in_a_header_other_than_subject_is_not_read(repo):
    text = "TO: https://x.example.app\nSUBJECT: hi\nBODY:\nHi\n"
    assert cda.decide(text, repo) is None


def test_trailing_punctuation_does_not_make_a_second_link(repo):
    msg = cda.decide(draft("At https://acme.example.app/plan. And https://acme.example.app/plan\n"), repo)
    assert msg.count("acme.example.app/plan") == 1


# --- the header -------------------------------------------------------------------------

def test_none_with_a_reason_passes(repo):
    text = draft("The posting: https://jobs.acme.example/123\n", artifact="none (job posting link)")
    assert cda.decide(text, repo) is None


def test_none_without_a_reason_blocks(repo):
    text = draft("The posting: https://jobs.acme.example/123\n", artifact="none")
    assert "neither" in cda.decide(text, repo)


def test_none_with_an_empty_reason_blocks(repo):
    assert cda.decide(draft("https://jobs.acme.example/1\n", artifact="none ()"), repo)


def test_a_build_log_with_reader_ask_filled_passes(repo):
    rel = log(repo, FILLED)
    assert cda.decide(draft(attach=["/tmp/planner.pdf"], artifact=rel), repo) is None


def test_a_log_one_level_down_is_accepted(repo):
    rel = log(repo, FILLED, "output/acme/playbook/BUILD-LOG.md")
    assert cda.decide(draft(attach=["/tmp/p.pdf"], artifact=rel), repo) is None


def test_nicks_waiver_in_the_log_passes(repo):
    rel = log(repo, "waived: nick 2026-10-26 (a page for family)\n")
    assert cda.decide(draft(attach=["/tmp/p.pdf"], artifact=rel), repo) is None


def test_a_log_without_reader_ask_blocks(repo):
    rel = log(repo, "# Build log\nreader: someone\n")
    assert "reader_ask is not filled" in cda.decide(draft(attach=["/tmp/p.pdf"], artifact=rel), repo)


def test_a_log_that_does_not_exist_blocks(repo):
    msg = cda.decide(draft(attach=["/tmp/p.pdf"], artifact="output/acme/BUILD-LOG.md"), repo)
    assert "does not exist" in msg


def test_a_path_that_is_not_a_build_log_blocks(repo):
    (repo / "output" / "acme" / "notes.md").write_text(FILLED)
    assert cda.decide(draft(attach=["/tmp/p.pdf"], artifact="output/acme/notes.md"), repo)


def test_the_last_artifact_header_is_the_one_read(repo):
    rel = log(repo, FILLED)
    text = draft(attach=["/tmp/p.pdf"], artifact=rel).replace(
        "ARTIFACT:", "ARTIFACT: none\nARTIFACT:", 1)
    assert cda.decide(text, repo) is None


def test_an_artifact_line_in_the_body_is_not_a_header(repo):
    text = draft("https://acme.example.app\nARTIFACT: none (x)\n")
    assert cda.decide(text, repo)


def test_headers_stop_at_body():
    text = "SUBJECT: hi\nATTACH: /a.pdf\nBODY:\nATTACH: /b.pdf\nSUBJECT: no\n"
    assert cda.headers(text) == (["/a.pdf"], [], " hi")


def test_the_first_subject_line_is_the_subject():
    assert cda.headers("SUBJECT: one\nSUBJECT: two\nBODY:\n")[2] == " one"


def test_an_empty_attach_line_is_ignored(repo):
    assert cda.decide("TO: x@acme.example\nATTACH:   \nBODY:\nHi\n", repo) is None


# --- the shipped entry point ------------------------------------------------------------
#
# The script is COPIED to a tmp tools/ folder so its DRAFT_FILE (beside the script) is a tmp
# file: these tests stage a blocking draft and must never touch the real
# tools/.pending-draft.txt. Every case stages a draft that WOULD block, so a branch that
# fails to return early is seen as an exit 2, not hidden by a missing draft.

OPEN = json.dumps({"tool_name": "Bash", "tool_input": {"command": "python3 tools/open_draft.py"}})


def _run(tmp_path, payload, staged=None, env_extra=None):
    tools = tmp_path / "hook" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    script = tools / SCRIPT.name
    script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    if staged is not None:
        (tools / ".pending-draft.txt").write_text(staged, encoding="utf-8")
    env = {"PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin",
           "PYTHONPATH": str(REPO_ROOT / "tools"), **(env_extra or {})}
    return subprocess.run([sys.executable, str(script)], input=payload, text=True,
                          capture_output=True, env=env, timeout=30)


BLOCKING = draft(attach=["/tmp/planner.pdf"])


def test_cli_blocks_open_draft_on_a_staged_attachment(tmp_path):
    r = _run(tmp_path, OPEN, BLOCKING)
    assert r.returncode == 2 and "BLOCKED check_draft_artifact" in r.stderr


def test_cli_ignores_a_command_that_is_not_open_draft(tmp_path):
    r = _run(tmp_path, json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}), BLOCKING)
    assert r.returncode == 0 and r.stderr == ""


def test_cli_override_allows(tmp_path):
    assert _run(tmp_path, OPEN, BLOCKING, {"DRAFT_OVERRIDE": "1"}).returncode == 0


def test_cli_fails_open_with_no_staged_draft(tmp_path):
    assert _run(tmp_path, OPEN).returncode == 0


def test_main_blocks_on_a_staged_draft_with_an_attachment(tmp_path, monkeypatch):
    staged = tmp_path / "draft.txt"
    staged.write_text(draft(attach=["/tmp/planner.pdf"]), encoding="utf-8")
    monkeypatch.setattr(cda, "DRAFT_FILE", staged)
    monkeypatch.setattr(cda, "read_hook_command", lambda: "python3 tools/open_draft.py")
    with pytest.raises(SystemExit) as e:
        cda.main()
    assert e.value.code == 2


def test_main_allows_a_clean_staged_draft(tmp_path, monkeypatch):
    staged = tmp_path / "draft.txt"
    staged.write_text(draft(), encoding="utf-8")
    monkeypatch.setattr(cda, "DRAFT_FILE", staged)
    monkeypatch.setattr(cda, "read_hook_command", lambda: "python3 tools/open_draft.py")
    with pytest.raises(SystemExit) as e:
        cda.main()
    assert e.value.code == 0


def test_main_fails_open_when_no_draft_is_staged(tmp_path, monkeypatch):
    monkeypatch.setattr(cda, "DRAFT_FILE", tmp_path / "missing.txt")
    monkeypatch.setattr(cda, "read_hook_command", lambda: "python3 tools/open_draft.py")
    with pytest.raises(SystemExit) as e:
        cda.main()
    assert e.value.code == 0


def test_a_log_for_another_reader_does_not_clear_the_draft(repo):
    rel = log(repo, 'reader: Jordan Roe, CTO\nreader_ask: "x y" (s)\n')
    msg = cda.decide(draft(attach=["/tmp/p.pdf"], artifact=rel), repo)
    assert msg and "whose reader is not the person" in msg


def test_the_reader_can_be_named_in_the_greeting(repo):
    rel = log(repo, FILLED)
    text = draft("Hi Casey,\n\nHere it is.\n", attach=["/tmp/p.pdf"], artifact=rel).replace(
        "TO: casey@acme.example", "TO: ops@acme.example")
    assert cda.decide(text, repo) is None


def test_a_log_with_no_reader_line_does_not_clear_the_draft(repo):
    rel = log(repo, 'reader_ask: "x y" (s)\n')
    assert cda.decide(draft(attach=["/tmp/p.pdf"], artifact=rel), repo)


def test_a_first_name_inside_another_word_does_not_match(repo):
    """grok, 2026-10-26: a log for Dan must not clear a draft to jordan@."""
    rel = log(repo, 'reader: Dan Lee, CTO\nreader_ask: "x y" (s)\n')
    text = draft("Hi Jordan,\n", attach=["/tmp/p.pdf"], artifact=rel).replace(
        "TO: casey@acme.example", "TO: jordan@acme.example")
    assert cda.decide(text, repo)


def test_the_last_name_also_matches(repo):
    rel = log(repo, 'reader: Michael Example, CEO\nreader_ask: "x y" (s)\n')
    text = draft("Hi Mike,\n", attach=["/tmp/p.pdf"], artifact=rel).replace(
        "TO: casey@acme.example", "TO: m.example@acme.example")
    assert cda.decide(text, repo) is None

