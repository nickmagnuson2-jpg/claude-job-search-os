"""The build-log gate: a build file under output/<slug>/ waits for a BUILD-LOG with reader_ask.

Each test names the behaviour it protects, so a mutant that removes the behaviour has a test
that dies: the edit branch, the Bash branch, the reader_ask check, the dossier check, the
waiver, the placeholder rejection, each exemption, and the shipped entry point.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_build_log_present as cbl  # noqa: E402

SCRIPT = Path(cbl.__file__).resolve()

FILLED = 'reader_ask: "Can you show me how this would work on our intake?" (sources/jd.md:12)\n'
PLACEHOLDER = 'reader_ask: "<verbatim>" (<transcript file:line>)\n'


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A company folder WITH its dossier, so each test varies one thing."""
    (tmp_path / "output" / "acme").mkdir(parents=True)
    (tmp_path / "output" / "acme" / "acme.md").write_text("# Acme\n", encoding="utf-8")
    return tmp_path


def put(repo: Path, rel: str, body: str = "") -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def write(repo: Path, rel: str) -> str | None:
    return cbl.decide("Write", {"file_path": rel, "content": "<html></html>"}, root=repo)


# --- the block ----------------------------------------------------------------

def test_a_new_page_with_no_log_is_blocked(repo):
    msg = write(repo, "output/acme/102626-demo.html")
    assert msg and "BLOCKED check_build_log_present" in msg
    assert "output/acme/102626-demo.html" in msg


def test_an_edit_to_an_existing_page_with_no_log_is_blocked(repo):
    put(repo, "output/acme/page.html", "<html></html>")
    msg = cbl.decide("Edit", {"file_path": "output/acme/page.html", "old_string": "a",
                              "new_string": "b"}, root=repo)
    assert msg and "BLOCKED check_build_log_present" in msg


def test_multiedit_and_notebookedit_are_gated(repo):
    assert cbl.decide("MultiEdit", {"file_path": "output/acme/app.js", "edits": []}, root=repo)
    assert cbl.decide("NotebookEdit", {"notebook_path": "output/acme/model.ipynb"}, root=repo)


def test_notebookedit_reads_notebook_path_not_file_path(repo):
    assert cbl.decide("NotebookEdit", {"file_path": "output/acme/model.ipynb"},
                      root=repo) is None


def test_an_absolute_path_inside_the_repo_is_gated(repo):
    assert write(repo, str(repo / "output" / "acme" / "deck.pptx"))


@pytest.mark.parametrize("ext", sorted(cbl.BUILD_EXT))
def test_every_build_extension_is_gated(repo, ext):
    assert write(repo, f"output/acme/x{ext}")


# --- the log clears it --------------------------------------------------------

def test_a_log_with_reader_ask_filled_clears_it(repo):
    put(repo, "output/acme/BUILD-LOG.md", "# Build\n" + FILLED)
    assert write(repo, "output/acme/102626-demo.html") is None


def test_a_log_one_level_down_clears_it(repo):
    put(repo, "output/acme/playbook/BUILD-LOG.md", FILLED)
    assert write(repo, "output/acme/playbook/design/index.html") is None


def test_nicks_waiver_clears_it(repo):
    put(repo, "output/acme/BUILD-LOG.md", "waived: nick 2026-10-26 (log predates the template)\n")
    assert write(repo, "output/acme/page.html") is None


def test_a_log_without_reader_ask_does_not_clear_it(repo):
    put(repo, "output/acme/BUILD-LOG.md", "# Build\nreader: someone\n")
    assert write(repo, "output/acme/page.html")


def test_the_template_placeholder_does_not_count_as_filled(repo):
    put(repo, "output/acme/BUILD-LOG.md", PLACEHOLDER)
    assert write(repo, "output/acme/page.html")


def test_an_empty_quoted_reader_ask_does_not_count(repo):
    put(repo, "output/acme/BUILD-LOG.md", 'reader_ask: "" (x)\n')
    assert write(repo, "output/acme/page.html")


def test_a_waiver_without_a_date_does_not_count(repo):
    put(repo, "output/acme/BUILD-LOG.md", "waived: nick\n")
    assert write(repo, "output/acme/page.html")


def test_a_folder_named_BUILD_LOG_is_skipped_not_read(repo):
    (repo / "output" / "acme" / "BUILD-LOG.md").mkdir()
    assert write(repo, "output/acme/page.html")


def test_a_company_folder_that_does_not_exist_yet_is_blocked(repo):
    msg = write(repo, "output/newco/page.html")
    assert "before its BUILD-LOG and dossier." in msg


def test_a_log_in_another_folder_does_not_clear_this_one(repo):
    put(repo, "output/other/BUILD-LOG.md", FILLED)
    assert write(repo, "output/acme/page.html")


# --- what is not gated --------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "output/acme/acme.md",                        # Markdown is out of scope
    "output/acme/102626-prep.pdf",                # PDF is out of scope
    "output/acme/sources/jd.html",                # a saved source, not a build
    "output/acme/scripts/fetch.py",               # a helper script
    "output/acme/data/clean.py",
    "output/acme/tests/test_x.py",
    "output/analysis/scratch.html",               # work folders
    "output/draft-captures/x/page.html",
    "output/artifact-gate/x.py",
    "output/application-workflow/x.html",
    "output/codify-102326/scripts/p0_replay.py",
    "output/codify-102326/page.html",             # the prefix, not the .py carve-out
    "output/table-audit/table_audit.py",          # live work folders (grok F2)
    "output/mutation-state/x.py",
    "output/rendercv_output/CV.html",             # the CV renderer's folder (grok round 8)
    "output/flat-one-off.html",                   # flat output/, no slug folder
    "tools/check_x.py",                           # outside output/
])
def test_paths_outside_the_gate_pass(repo, rel):
    assert write(repo, rel) is None


def test_a_build_file_under_scripts_that_is_not_python_is_still_gated(repo):
    assert write(repo, "output/acme/scripts/page.html")


def test_other_tools_are_ignored(repo):
    assert cbl.decide("Read", {"file_path": "output/acme/page.html"}, root=repo) is None
    assert cbl.decide("Read", {"notebook_path": "output/acme/m.ipynb"}, root=repo) is None
    assert cbl.decide("Write", "not a dict", root=repo) is None


# --- Bash: the same target parser check_handoff_producer uses -----------------

@pytest.mark.parametrize("command", [
    "echo '<html>' > output/acme/page.html",
    "cat x | tee output/acme/app.js",
    "cp /tmp/deck.pptx output/acme/deck.pptx",
    "mv /tmp/model.xlsx output/acme/model.xlsx",
    "sed -i '' 's/a/b/' output/acme/page.html",
    # a copy into a folder lands the file under its own name (codex and grok F1)
    "cp /tmp/page.html output/acme/",
    "cp /tmp/page.html output/acme",
    "cp /tmp/notes.md /tmp/app.js output/acme/",
    "mv /tmp/deck.pptx output/acme/",
    "cp -t output/acme /tmp/page.html",
    "cp --target-directory=output/acme /tmp/page.html",
    "install -m 644 /tmp/page.html output/acme/",
    'cp "/tmp/my page.html" output/acme/',           # quoted name with a space (codex round 8)
    "cp '/tmp/my page.html' output/acme/",
    'rsync "/tmp/my page.html" output/acme/',
    "rsync -t /tmp/page.html output/acme/",          # rsync -t is --times (grok round 9)
    "rsync -av -t /tmp/page.html output/acme/",
])
def test_bash_writes_are_gated(repo, command):
    msg = cbl.decide("Bash", {"command": command}, root=repo)
    assert msg and "BLOCKED check_build_log_present" in msg


@pytest.mark.parametrize("command", [
    "cat output/acme/page.html",
    "grep -r 'page.html' output/acme/",
    "echo hi > output/acme/notes.md",
    "python3 output/acme/scripts/build.py",
    "cp /tmp/notes.md output/acme/",
    "cp /tmp/a.md /tmp/b.pdf output/acme/",
    "cp $SRC output/acme/",
    "rsync --exclude page.html /tmp/notes.md output/acme/",   # an option's argument (round 8)
])
def test_bash_reads_and_non_build_writes_pass(repo, command):
    assert cbl.decide("Bash", {"command": command}, root=repo) is None


def test_a_build_target_after_a_non_build_target_is_still_seen(repo):
    cmd = "echo a > output/acme/notes.md && echo b > output/acme/page.html"
    assert cbl.decide("Bash", {"command": cmd}, root=repo)


def test_a_build_target_after_a_waived_folder_is_still_seen(repo):
    put(repo, "output/fam/BUILD-LOG.md", "waived: nick 2026-10-26 (x)\n")
    cmd = "echo a > output/fam/page.html && echo b > output/acme/page.html"
    assert cbl.decide("Bash", {"command": cmd}, root=repo)


def test_a_copy_into_an_existing_dotted_folder_is_gated(repo):
    (repo / "output" / "acme" / "v1.0").mkdir()
    cmd = f"cp /tmp/page.html {repo}/output/acme/v1.0"
    assert cbl.decide("Bash", {"command": cmd}, root=repo)


def test_a_folder_copied_into_a_company_folder_is_gated(repo, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "page.html").write_text("x", encoding="utf-8")
    cmd = f"rsync -a {site}/ {repo}/output/acme/"
    msg = cbl.decide("Bash", {"command": cmd}, root=repo)
    assert msg and "page.html" in msg


def test_bash_write_passes_once_the_log_exists(repo):
    put(repo, "output/acme/BUILD-LOG.md", FILLED)
    assert cbl.decide("Bash", {"command": "echo x > output/acme/page.html"}, root=repo) is None


# --- the dossier ---------------------------------------------------------------

def test_a_log_without_a_dossier_still_blocks(repo):
    (repo / "output" / "acme" / "acme.md").unlink()
    put(repo, "output/acme/BUILD-LOG.md", FILLED)
    msg = write(repo, "output/acme/page.html")
    assert msg and "before its dossier." in msg
    assert "No dossier at output/acme/acme.md" in msg
    assert "No BUILD-LOG" not in msg


def test_no_log_and_no_dossier_names_both(repo):
    (repo / "output" / "acme" / "acme.md").unlink()
    msg = write(repo, "output/acme/page.html")
    assert "before its BUILD-LOG and dossier." in msg
    assert "No BUILD-LOG" in msg and "No dossier" in msg


def test_a_log_and_no_dossier_message_omits_the_log_reason(repo):
    msg = write(repo, "output/acme/page.html")
    assert "before its BUILD-LOG." in msg and "No dossier" not in msg


def test_a_dossier_that_is_a_directory_does_not_count(repo):
    (repo / "output" / "acme" / "acme.md").unlink()
    (repo / "output" / "acme" / "acme.md").mkdir()
    put(repo, "output/acme/BUILD-LOG.md", FILLED)
    assert write(repo, "output/acme/page.html")


def test_the_waiver_clears_a_missing_dossier_too(repo):
    (repo / "output" / "acme" / "acme.md").unlink()
    put(repo, "output/acme/BUILD-LOG.md", "waived: nick 2026-10-26 (a page for family)\n")
    assert write(repo, "output/acme/page.html") is None


def test_a_waiver_above_wins_over_a_filled_log_below(repo):
    (repo / "output" / "acme" / "acme.md").unlink()
    put(repo, "output/acme/a/BUILD-LOG.md", FILLED)
    put(repo, "output/acme/BUILD-LOG.md", "waived: nick 2026-10-26 (x)\n")
    assert write(repo, "output/acme/a/page.html") is None


def test_a_sibling_builds_log_does_not_clear_a_new_build(repo):
    """codex and grok, 2026-10-26: an earlier build's log must not unlock a different one."""
    put(repo, "output/acme/v1/BUILD-LOG.md", FILLED)
    assert write(repo, "output/acme/v2/page.html")


def test_a_log_in_a_subfolder_does_not_clear_a_file_above_it(repo):
    put(repo, "output/acme/v1/BUILD-LOG.md", FILLED)
    assert write(repo, "output/acme/page.html")


# --- build_slug, directly -----------------------------------------------------

def test_build_slug_returns_the_company_folder():
    assert cbl.build_slug("output/acme/playbook/design/index.html") == "acme"
    assert cbl.build_slug("./output/acme/x.HTML") == "acme"
    assert cbl.build_slug("output/acme/scripts/x.py") is None


# --- the shipped entry point --------------------------------------------------

def _run(payload, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload) if not
                          isinstance(payload, str) else payload, text=True,
                          capture_output=True, cwd=cwd, timeout=30)


def test_main_blocks_with_exit_2_on_a_real_build_path(tmp_path):
    # The real repo root: a folder name that cannot exist there has no log.
    r = _run({"tool_name": "Write", "tool_input": {
        "file_path": "output/zz-no-such-company-zz/page.html", "content": "x"}}, tmp_path)
    assert r.returncode == 2
    assert "BLOCKED check_build_log_present" in r.stderr


def test_main_allows_a_non_build_write(tmp_path):
    r = _run({"tool_name": "Write", "tool_input": {
        "file_path": "output/zz-no-such-company-zz/notes.md", "content": "x"}}, tmp_path)
    assert r.returncode == 0, r.stderr


def test_main_fails_open_on_bad_json(tmp_path):
    r = _run("not json", tmp_path)
    assert r.returncode == 0


def test_main_blocks_a_truncated_payload(tmp_path):
    """Failing open here would be a silent bypass: oversize the payload and nothing is read."""
    huge = {"tool_name": "Write", "tool_input": {"file_path": "output/x/page.html",
                                                 "content": "x" * (2 * 1024 * 1024)}}
    r = _run(huge, tmp_path)
    assert r.returncode == 2
    assert "payload was truncated" in r.stderr


def test_a_log_above_the_company_folder_does_not_count(repo):
    put(repo, "output/BUILD-LOG.md", FILLED)
    put(repo, "BUILD-LOG.md", FILLED)
    assert write(repo, "output/acme/page.html")

