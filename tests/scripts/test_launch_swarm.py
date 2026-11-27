"""Tests for tools/launch_swarm.py: an overnight swarm launches only from a brief that passes
brief_check, and each session's prompt is written by the launcher.

Spec: output/overnight-112126/DECISIONS.md row C1; org rulings 2026-11-23 (three checks from
that morning's fires: brief org vs launch org, a log file per section plus the logging block
in the prompt, NEEDS-NICK.md before any window). Template: framework/templates/overnight-brief.md.
Every fixture is synthetic (session names proj-org, proj-standby).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import launch_swarm as ls  # noqa: E402

TEMPLATE = ROOT / "framework" / "templates" / "overnight-brief.md"


def filled_template() -> str:
    """The shipped template with every placeholder filled the way an org would fill it."""
    t = TEMPLATE.read_text()
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = t.replace("<org-session-name>", "proj-org").replace("<standby-session-name>", "proj-standby")
    t = t.replace("<MMDDYY>", "010126").replace("<MMDD>", "0101").replace("YYYY-MM-DD", "2026-01-01", 2)  # title and date lines; rule 12 keeps the format text
    t = t.replace("<conversations / applications / speed>", "speed")  # Codex r1 F2: a real value, not "filled"
    t = t.replace("| <S1> |", "| S1 |")  # Codex r2 F1: a backlog row names a real section
    t = re.sub(r"<[^<>\n]*>", "filled", t)
    return t


@pytest.fixture
def run_dir(tmp_path):
    d = tmp_path / "output" / "overnight-010126"
    d.mkdir(parents=True)
    (d / "NEEDS-NICK.md").write_text("# Needs Nick\n")
    return d


def ledger_file(root: Path) -> Path:
    return root / "tools" / ".cross-model-ledger.jsonl"


def framed(text: str) -> str:
    """A review target the way /verify inlines a brief (ledger 1323-1332)."""
    return "Plan review of the brief. BAR: ... ===BRIEF START===\n" + text + "\n===BRIEF END==="


def add_row(root: Path, brief: str, model: str = "codex", family: str = "openai", drop=(),
            frame: bool = True, **extra) -> int:
    """Append one ledger row reviewing `brief`; returns its run number (finding_write's numbering)."""
    target = framed(brief) if frame else brief
    import json
    f = ledger_file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    row = {"recorded": "2026-01-01T00:00:00+00:00", "target": target, "paths": [], "findings": [],
           "waived": False, "model": model, "family": family, "rc": 0, "report_written": True,
           "verified": True, "tree": None}
    row.update(extra)
    for k in drop:
        row.pop(k)
    with f.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return len([ln for ln in f.read_text().splitlines() if ln.strip()])


def review(root: Path, text: str) -> None:
    """A Codex and a Grok review of this exact text."""
    add_row(root, text, "codex", "openai")
    add_row(root, text, "grok", "xai")


@pytest.fixture(autouse=True)
def tmp_ledger(tmp_path, monkeypatch):
    """brief_check's default ledger is the test's tmp root, never the real one."""
    monkeypatch.setattr(ls, "LEDGER_ROOT", tmp_path)
    return tmp_path


def write_brief(run_dir: Path, text: str, reviewed: bool = True) -> Path:
    p = run_dir / "brief.md"
    p.write_text(text)
    if reviewed:
        review(run_dir.parents[1], text)
    return p


# ---------------------------------------------------------------- the template itself

def test_the_shipped_template_once_filled_passes(run_dir):
    p = write_brief(run_dir, filled_template())
    assert ls.brief_check(p, org="proj-org") == []


def test_the_shipped_template_unfilled_fails_on_placeholders(run_dir):
    p = write_brief(run_dir, TEMPLATE.read_text().replace("<org-session-name>", "proj-org"))
    problems = ls.brief_check(p, org="proj-org")
    assert any("placeholder" in x for x in problems)


def test_the_template_carries_rules_1_to_18():
    body = TEMPLATE.read_text().split("## Rules for every session", 1)[1].split("\n## ", 1)[0]
    assert [int(n) for n in re.findall(r"^(\d+)\. ", body, re.M)] == list(range(1, 19))


def test_the_template_says_adoption_is_not_enforced():
    assert "ADOPTION IS NOT ENFORCED" in TEMPLATE.read_text()


def test_the_logging_block_lives_in_the_launcher_not_the_template():
    assert ls.LOGGING_BLOCK_MARK not in TEMPLATE.read_text()


# ---------------------------------------------------------------- org's three checks

def test_brief_org_must_equal_launch_org(run_dir):
    p = write_brief(run_dir, filled_template())
    problems = ls.brief_check(p, org="proj-other")
    assert any("Organizer" in x and "proj-other" in x for x in problems)


def test_missing_organizer_line_fails(run_dir):
    p = write_brief(run_dir, filled_template().replace("Organizer: **proj-org**", "Lead: proj-org"))
    assert any("Organizer" in x for x in ls.brief_check(p, org="proj-org"))


def test_a_section_without_a_log_file_fails(run_dir):
    t = re.sub(r"^- \*\*Log file:\*\*.*\n", "", filled_template(), flags=re.M)
    p = write_brief(run_dir, t)
    assert any("S1" in x and "Log file" in x for x in ls.brief_check(p, org="proj-org"))


def test_a_log_file_must_be_a_backticked_path(run_dir):
    t = re.sub(r"^- \*\*Log file:\*\*.*$", "- **Log file:** the usual one", filled_template(), flags=re.M)
    p = write_brief(run_dir, t)
    assert any("Log file" in x for x in ls.brief_check(p, org="proj-org"))


def test_missing_needs_nick_file_fails(run_dir):
    (run_dir / "NEEDS-NICK.md").unlink()
    p = write_brief(run_dir, filled_template())
    assert any("NEEDS-NICK.md" in x for x in ls.brief_check(p, org="proj-org"))


def test_the_prompt_carries_the_logging_block_with_the_sections_log_file(run_dir):
    p = write_brief(run_dir, filled_template())
    brief = ls.parse_brief(p.read_text())
    prompt = ls.section_prompt(brief.sections[0], org="proj-org", brief_rel="output/overnight-010126/brief.md")
    assert ls.LOGGING_BLOCK_MARK in prompt
    assert brief.sections[0].log_file in prompt
    assert "proj-org" in prompt
    assert "| S1:" in prompt


def test_the_prompt_carries_the_sections_workarounds_line(run_dir):
    """A workaround needs a filed source fix in the same report (Nick, 2026-11-25: "always
    fix at the source"); the line reaches every session in its own dispatch text."""
    p = write_brief(run_dir, filled_template())
    brief = ls.parse_brief(p.read_text())
    assert brief.sections[0].workarounds
    prompt = ls.section_prompt(brief.sections[0], org="proj-org", brief_rel="output/overnight-010126/brief.md")
    assert f"Workarounds: {brief.sections[0].workarounds}" in prompt


def test_the_prompt_routes_logging_to_the_writer_not_cat(run_dir):
    """Router fix (Nick via org dc, 2026-11-27; red team E row 1): this prompt reached every
    section and said "append only, with cat >>", so sessions hand-wrote 1,953 workstream
    sections in 30 days while tools/workstream_write.py add-section existed. The prompt must
    name the writer command for the section's own log file and never offer cat >>."""
    p = write_brief(run_dir, filled_template())
    brief = ls.parse_brief(p.read_text())
    sec = brief.sections[0]
    prompt = ls.section_prompt(sec, org="proj-org", brief_rel="output/overnight-010126/brief.md")
    assert f"tools/workstream_write.py add-section {sec.log_file}" in prompt
    assert f"--title '{sec.id}: <what>' --body-file -" in prompt
    assert "cat >>" not in prompt


def test_the_template_logging_rule_routes_to_the_writer_not_cat():
    """The same router in the brief template's rule 12, which every brief copies."""
    rule12 = next(l for l in TEMPLATE.read_text(encoding="utf-8").splitlines()
                  if l.startswith("12. **Logging:**"))
    assert "tools/workstream_write.py add-section" in rule12
    assert "cat >>" not in rule12


# Fable 11-27 F1/F4/F5: a double-quoted --body or --title let the shell expand $ and
# backticks, and the writer recorded the altered text with exit 0. Real entries carry both
# (Exa spend "$0.40", `date`), so the execution tests type them.
TITLE = "probe $HOME `echo x`"
ENTRY = "spent $0.40, see `date` and $(echo y)"


def _run_logged(cmd: str, tmp_path, log_rel: str) -> str:
    """Run a printed logging command for real, against a temp workstream, and return it."""
    import subprocess
    target = tmp_path / "data" / "workstreams" / "probe.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Probe\n", encoding="utf-8")
    cmd = cmd.replace(log_rel, str(target))
    r = subprocess.run(["bash", "-c", cmd], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return target.read_text(encoding="utf-8")


def test_the_prompts_logging_command_runs_as_written(run_dir, tmp_path):
    """Codex 11-27 F1: the command used to print the heredoc on one line ("<<'EOF' ... EOF"),
    which fails when run literally. The block a section receives must run as printed,
    once its placeholders are filled, and land a clock-stamped entry."""
    p = write_brief(run_dir, filled_template())
    sec = ls.parse_brief(p.read_text()).sections[0]
    lines = ls.section_prompt(sec, org="proj-org", brief_rel="b").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("PYTHONIOENCODING=utf-8 python3 tools/workstream_write.py"))
    end = lines.index("EOF", start)
    block = "\n".join(lines[start:end + 1]).replace("<what>", TITLE).replace("<entry lines>", ENTRY)
    text = _run_logged(block, tmp_path, sec.log_file)
    assert re.search(rf"^## \d{{4}}-\d\d-\d\d \d\d:\d\d by `date` \| {re.escape(sec.id + ': ' + TITLE)}$",
                     text, re.M), text
    assert ENTRY in text, "the entry must land exactly as typed, $ and backticks included"


def test_the_templates_logging_command_runs_as_written(tmp_path):
    rule12 = next(l for l in TEMPLATE.read_text(encoding="utf-8").splitlines()
                  if l.startswith("12. **Logging:**"))
    cmd = re.search(r"`(PYTHONIOENCODING=utf-8 python3 tools/workstream_write\.py [^`]+)`", rule12).group(1)
    cmd = cmd.replace("<X>: <what>", "L9: " + TITLE) + "\n" + ENTRY + "\nEOF"
    text = _run_logged(cmd, tmp_path, "<log file>")
    assert re.search(rf"^## \d{{4}}-\d\d-\d\d \d\d:\d\d by `date` \| {re.escape('L9: ' + TITLE)}$",
                     text, re.M), text
    assert ENTRY in text, "the entry must land exactly as typed, $ and backticks included"


# ---------------------------------------------------------------- structure

def test_backlog_must_come_before_sections(run_dir):
    t = filled_template()
    backlog = t[t.index("## Ranked backlog"):t.index("## Rules checked")]
    t = t.replace(backlog, "")
    t = t.replace("## Org work", backlog + "## Org work")
    p = write_brief(run_dir, t)
    assert any("Ranked backlog" in x for x in ls.brief_check(p, org="proj-org"))


@pytest.mark.parametrize("heading", ["## Ranked backlog", "## Rules for every session", "## Sections",
                                     "## Close-out: run metrics"])
def test_each_required_heading_is_checked(run_dir, heading):
    p = write_brief(run_dir, filled_template().replace(heading, "## Something else"))
    assert any(heading.lstrip("# ") in x for x in ls.brief_check(p, org="proj-org"))


def test_a_missing_rule_number_fails(run_dir):
    p = write_brief(run_dir, filled_template().replace("\n16. ", "\nsixteen. "))
    assert any("rule 16" in x for x in ls.brief_check(p, org="proj-org"))


def test_standby_org_is_required(run_dir):
    t = re.sub(r"^Standby org:.*\n", "", filled_template(), flags=re.M)
    p = write_brief(run_dir, t)
    assert any("Standby org" in x for x in ls.brief_check(p, org="proj-org"))


@pytest.mark.parametrize("field", ["Owns", "Done when", "Workarounds"])
def test_section_owns_and_done_when_are_required(run_dir, field):
    t = re.sub(rf"^- \*\*{field}:\*\*.*\n", "", filled_template(), flags=re.M)
    p = write_brief(run_dir, t)
    assert any("S1" in x and field in x for x in ls.brief_check(p, org="proj-org"))


def test_section_heading_must_name_what_it_moves(run_dir):
    t = re.sub(r"^### S1: (.*) \| moves: .*$", r"### S1: \1", filled_template(), flags=re.M)
    p = write_brief(run_dir, t)
    assert any("S1" in x and "moves" in x for x in ls.brief_check(p, org="proj-org"))


def test_no_sections_fails(run_dir):
    t = re.sub(r"^### S1:.*?(?=^## )", "", filled_template(), flags=re.S | re.M)
    p = write_brief(run_dir, t)
    assert any("no sections" in x for x in ls.brief_check(p, org="proj-org"))


def test_two_sections_owning_the_same_path_fail(run_dir):
    t = filled_template()
    block = t[t.index("### S1:"):t.index("## Org work")]
    t = t.replace(block, block + block.replace("### S1:", "### S2:"))
    t = re.sub(r"(- \*\*Owns:\*\*) filled", r"\1 `output/x/a.md`", t)
    p = write_brief(run_dir, t)
    assert any("output/x/a.md" in x and "S1" in x and "S2" in x for x in ls.brief_check(p, org="proj-org"))


def test_parse_reads_sections_in_order(run_dir):
    t = filled_template()
    block = t[t.index("### S1:"):t.index("## Org work")]
    t = t.replace(block, block + block.replace("### S1:", "### L1:"))
    b = ls.parse_brief(t)
    assert [s.id for s in b.sections] == ["S1", "L1"]
    assert b.org == "proj-org"


# ---------------------------------------------------------------- CLI

def test_cli_refuses_a_failing_brief_and_opens_nothing(run_dir, capsys, monkeypatch):
    opened = []
    monkeypatch.setattr(ls, "open_window", lambda cmd: opened.append(cmd))
    p = write_brief(run_dir, filled_template())
    rc = ls.main([str(p), "--org", "proj-wrong"])
    assert rc == 2
    assert opened == []
    assert "Organizer" in capsys.readouterr().err


def test_cli_dry_run_writes_prompts_and_opens_nothing(run_dir, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(ls, "open_window", lambda cmd: opened.append(cmd))
    p = write_brief(run_dir, filled_template())
    rc = ls.main([str(p), "--org", "proj-org", "--dry-run", "--repo", str(run_dir.parents[1])])
    assert rc == 0
    assert opened == []
    assert (run_dir / "prompts" / "S1.txt").exists()
    assert "[dry-run] S1" in capsys.readouterr().out


def test_cli_opens_one_window_per_named_section(run_dir, monkeypatch):
    opened = []
    monkeypatch.setattr(ls, "open_window", lambda cmd: opened.append(cmd))
    monkeypatch.setattr(ls.time, "sleep", lambda s: None)
    t = filled_template()
    block = t[t.index("### S1:"):t.index("## Org work")]
    t = t.replace(block, block + block.replace("### S1:", "### S2:").replace("filled", "other", 1))
    p = write_brief(run_dir, t)
    rc = ls.main([str(p), "--org", "proj-org", "--repo", str(run_dir.parents[1]), "S2"])
    assert rc == 0
    assert len(opened) == 1 and "S2.txt" in opened[0]


def test_cli_unknown_section_is_refused(run_dir, monkeypatch, capsys):
    monkeypatch.setattr(ls, "open_window", lambda cmd: None)
    p = write_brief(run_dir, filled_template())
    assert ls.main([str(p), "--org", "proj-org", "--repo", str(run_dir.parents[1]), "S9"]) == 2
    assert "S9" in capsys.readouterr().err


# ---------------------------------------------------------------- Codex r1 findings (C1)

def test_an_empty_ranked_backlog_is_refused(run_dir):
    # F1
    t = filled_template()
    t = t[:t.index("## Ranked backlog")] + "## Ranked backlog\n\n" + t[t.index("## Rules checked"):]
    p = write_brief(run_dir, t)
    assert any("Ranked backlog" in x and "row" in x for x in ls.brief_check(p, org="proj-org"))


@pytest.mark.parametrize("moves", ["banana", "filled", "speed, banana"])
def test_moves_outside_the_three_values_is_refused(run_dir, moves):
    # F2
    t = re.sub(r"^(### S1: .*\| moves: ).*$", rf"\g<1>{moves}", filled_template(), flags=re.M)
    p = write_brief(run_dir, t)
    assert any("S1" in x and "moves" in x for x in ls.brief_check(p, org="proj-org"))


@pytest.mark.parametrize("moves", ["speed", "conversations + speed", "applications, conversations"])
def test_moves_with_one_or_more_allowed_values_passes(run_dir, moves):
    t = re.sub(r"^(### S1: .*\| moves: ).*$", rf"\g<1>{moves}", filled_template(), flags=re.M)
    p = write_brief(run_dir, t)
    assert ls.brief_check(p, org="proj-org") == []



def test_fenced_fake_rules_do_not_satisfy_the_rules(run_dir):
    # F4
    t = filled_template()
    block = t[t.index("## Rules for every session"):t.index("## Sections")]
    fake = "## Rules for every session\n\n```\n" + "".join(f"{n}. fake\n" for n in range(1, 19)) + "```\n\n"
    p = write_brief(run_dir, t.replace(block, fake))
    assert any("rule 1 " in x or "rule 1 is" in x for x in ls.brief_check(p, org="proj-org"))


def test_a_rule_whose_text_differs_from_the_template_is_refused(run_dir):
    # F4, upstream: the template is the one copy of the rules
    t = filled_template().replace("**Exa: at most $2 per session**", "**Exa: spend what you like**")
    p = write_brief(run_dir, t)
    assert any("rule 9" in x and "template" in x for x in ls.brief_check(p, org="proj-org"))


# ---------------------------------------------------------------- mutation r1 survivors (1586c717)

def test_missing_organizer_line_names_the_missing_line(run_dir):
    p = write_brief(run_dir, filled_template().replace("Organizer: **proj-org**", "Lead: proj-org"))
    problems = ls.brief_check(p, org="proj-org")
    assert "no 'Organizer: **<name>**' line" in problems
    assert not any("names None" in x for x in problems)


def test_missing_needs_nick_line_is_refused(run_dir):
    t = re.sub(r"^Needs Nick:.*\n", "", filled_template(), flags=re.M)
    p = write_brief(run_dir, t)
    assert "no 'Needs Nick: `<path>`' line" in ls.brief_check(p, org="proj-org")


def test_a_missing_rule_is_reported_as_missing(run_dir):
    p = write_brief(run_dir, filled_template().replace("\n16. ", "\nsixteen. "))
    problems = ls.brief_check(p, org="proj-org")
    assert "rule 16 is missing under '## Rules for every session'" in problems
    assert not any(x.startswith("rule 16 does not match") for x in problems)


def test_one_section_owning_a_path_alone_passes(run_dir):
    t = re.sub(r"(- \*\*Owns:\*\*) filled", r"\1 `output/x/only.md`", filled_template())
    p = write_brief(run_dir, t)
    assert ls.brief_check(p, org="proj-org") == []


def test_open_window_asks_terminal_to_run_the_command(monkeypatch):
    calls = []
    monkeypatch.setattr(ls.subprocess, "run", lambda args, **kw: calls.append(args))
    ls.open_window("echo hi")
    assert calls and calls[0][0] == "osascript" and calls[0][-1] == "echo hi"


def test_cli_refusal_says_it_is_refusing(run_dir, capsys, monkeypatch):
    monkeypatch.setattr(ls, "open_window", lambda cmd: None)
    p = write_brief(run_dir, filled_template())
    ls.main([str(p), "--org", "proj-wrong"])
    assert "refusing to launch" in capsys.readouterr().err


def test_cli_reports_each_opened_window_and_paces_them(run_dir, monkeypatch, capsys):
    sleeps = []
    monkeypatch.setattr(ls, "open_window", lambda cmd: None)
    monkeypatch.setattr(ls.time, "sleep", lambda s: sleeps.append(s))
    p = write_brief(run_dir, filled_template())
    assert ls.main([str(p), "--org", "proj-org", "--repo", str(run_dir.parents[1])]) == 0
    assert "opened S1" in capsys.readouterr().out
    assert sleeps == [2]


# ---------------------------------------------------------------- Codex r2 F1 (P0): backlog rows are validated

def _backlog_row(t, row):
    return re.sub(r"^\| 1 \|.*$", row, t, count=1, flags=re.M)


def test_the_filled_template_backlog_row_is_valid(run_dir):
    t = _backlog_row(filled_template(), "| 1 | an item | speed | `output/x/y.md:3` | S1 |")
    p = write_brief(run_dir, t)
    assert ls.brief_check(p, org="proj-org") == []


@pytest.mark.parametrize("row, why", [
    ("| 1 | an item | banana | `output/x/y.md:3` | S1 |", "moves"),
    ("| 1 | an item | speed |  | S1 |", "evidence"),
    ("| 1 | an item | speed | `output/x/y.md:3` | S9 |", "S9"),
])
def test_an_invalid_backlog_row_is_refused(run_dir, row, why):
    p = write_brief(run_dir, _backlog_row(filled_template(), row))
    problems = ls.brief_check(p, org="proj-org")
    assert any("backlog row 1" in x and why in x for x in problems)


# ---------------------------------------------------------------- Codex r3 F1 (P0): every table row in the backlog is checked

@pytest.mark.parametrize("row", [
    "|  | an item | banana | `output/x/y.md:3` | S1 |",
    "| first | an item | speed | `output/x/y.md:3` | S9 |",
    "| 1. | an item | speed |  | S1 |",
])
def test_a_backlog_row_without_a_numeric_rank_is_refused(run_dir, row):
    t = filled_template().replace("| 1 | filled | speed |", row + "\n| 1 | filled | speed |", 1)
    p = write_brief(run_dir, t)
    assert any("backlog row" in x and "rank" in x for x in ls.brief_check(p, org="proj-org"))


def test_the_backlog_header_and_separator_rows_are_not_checked_as_items(run_dir):
    assert ls.brief_check(write_brief(run_dir, filled_template()), org="proj-org") == []


# ---------------------------------------------------------------- rules 4 and 17 additions (2026-11-23) travel by rule text

@pytest.mark.parametrize("n, sentence", [
    (4, "each agent gets an explicit private scratch path"),
    (17, "Only a P0 gets a fix; a P1 is parked with a reason unless org or Nick rules otherwise."),
])
def test_a_brief_missing_a_rule_addition_is_refused(run_dir, n, sentence):
    t = filled_template()
    assert sentence in t
    p = write_brief(run_dir, t.replace(sentence, "", 1))
    assert any(x.startswith(f"rule {n} does not match") for x in ls.brief_check(p, org="proj-org"))


# ---------------------------------------------------------------- the window command's env
# Cause of the 11-23 missing transcripts (orchestration.md 2026-11-24 17:38, S1): the windows
# inherited a parent Claude session's env, so each claude ran as a child session and wrote no
# transcript. These tests run the exact line main() hands to Terminal, with a fake `claude`
# first on PATH that records its own env and argv.
PARENT_ENV = {
    "CLAUDECODE": "1",
    "CLAUDE_CODE_CHILD_SESSION": "1",
    "CLAUDE_CODE_SESSION_ID": "00000000-0000-0000-0000-000000000000",
    "CLAUDE_PID": "12345",
    "CLAUDE_SOME_FUTURE_VAR": "x",
    "AI_AGENT": "claude-code",
}


def _run_window_line(run_dir, monkeypatch, shell):
    import os
    import shutil
    import subprocess
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    opened = []
    monkeypatch.setattr(ls, "open_window", lambda cmd: opened.append(cmd))
    monkeypatch.setattr(ls.time, "sleep", lambda s: None)
    p = write_brief(run_dir, filled_template())
    repo = run_dir.parents[1]
    assert ls.main([str(p), "--org", "proj-org", "--repo", str(repo)]) == 0
    assert len(opened) == 1
    bindir = run_dir / "bin"
    bindir.mkdir()
    out = run_dir / "child-env.txt"
    fake = bindir / "claude"
    fake.write_text(f'#!/bin/sh\npwd > "{out}"\nprintf "%s\\n" "$1" >> "{out}"\nenv >> "{out}"\n')
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE") and k != "AI_AGENT"}
    env.update(PARENT_ENV, PATH=f"{bindir}:{os.environ['PATH']}", KEEP_ME="kept")
    subprocess.run([shell, "-c", opened[0]], env=env, check=True, timeout=30)
    return out.read_text().splitlines(), repo


@pytest.mark.parametrize("shell", ["zsh", "bash"])
def test_the_window_line_drops_every_parent_claude_var(run_dir, monkeypatch, shell):
    lines, repo = _run_window_line(run_dir, monkeypatch, shell)
    names = {ln.split("=", 1)[0] for ln in lines if "=" in ln}
    assert not names & set(PARENT_ENV), f"leaked into claude: {sorted(names & set(PARENT_ENV))}"
    assert "KEEP_ME=kept" in lines  # only the Claude vars go, not the whole env


def test_the_window_line_still_cds_to_the_repo_and_passes_the_prompt(run_dir, monkeypatch):
    lines, repo = _run_window_line(run_dir, monkeypatch, "zsh")
    assert Path(lines[0]).resolve() == repo.resolve()
    assert lines[1].startswith("You are S1 in the swarm. Org is proj-org.")


def test_a_failed_cd_does_not_start_claude(tmp_path):
    import subprocess
    out = tmp_path / "ran"
    line = ls.window_command(tmp_path / "missing", tmp_path / "p.txt", program=f"touch '{out}' #")
    subprocess.run(["zsh", "-c", line], capture_output=True, timeout=30)
    assert not out.exists()


# ---------------------------------------------------------------- the review check
# 2026-11-24: a brief passed brief_check while Codex and Grok both carried the same P0 in it
# (ledger 1323/1324 F1). Org ruling 2026-11-24: match on the brief's current text, both
# families, no open finding on the covering rows; earlier-text open P0s print as a WARNING.
def review_problems(problems):
    return [p for p in problems if "review" in p or "open finding" in p or "ledger" in p]


def test_a_brief_with_no_review_is_refused(run_dir):
    p = write_brief(run_dir, filled_template(), reviewed=False)
    probs = ls.brief_check(p, org="proj-org")
    assert any("no codex review" in x for x in probs)
    assert any("no grok review" in x for x in probs)


def test_a_brief_edited_by_one_character_after_review_is_refused(run_dir):
    t = filled_template()
    p = write_brief(run_dir, t)
    assert ls.brief_check(p, org="proj-org") == []
    i = t.index("Organizer:") - 2  # a character outside every structural check
    p.write_text(t[:i] + ("X" if t[i] != "X" else "Y") + t[i + 1:])
    probs = ls.brief_check(p, org="proj-org")
    assert any("no codex review" in x for x in probs) and any("no grok review" in x for x in probs)
    assert len(review_problems(probs)) == len(probs)  # refused for the review, nothing else


def test_an_open_finding_on_a_covering_review_is_refused_and_named(run_dir, tmp_ledger):
    t = filled_template()
    p = write_brief(run_dir, t)
    n = add_row(tmp_ledger, t, "codex", "openai",
                findings=[{"id": "F1", "severity": "P0", "summary": "x"},
                          {"id": "F2", "severity": "P1", "summary": "y", "disposition": "parked"}])
    probs = ls.brief_check(p, org="proj-org")
    assert probs == [f"open finding {n}.F1 (P0, codex) on a review of this brief; "
                     f"disposition it with finding_write.py set"]
    assert ls.review_warnings(t, tmp_ledger) == []  # it blocks; it is not also a warning


def test_dispositioned_findings_on_both_reviews_pass(run_dir, tmp_ledger):
    t = filled_template()
    p = write_brief(run_dir, t, reviewed=False)
    add_row(tmp_ledger, t, "codex", "openai", findings=[{"id": "F1", "severity": "P0", "disposition": "fixed"}])
    add_row(tmp_ledger, t, "grok", "xai", findings=[{"id": "F1", "severity": "P2", "disposition": "rejected"}])
    assert ls.brief_check(p, org="proj-org") == []


@pytest.mark.parametrize("model,family,missing", [("codex", "openai", "grok"), ("grok", "xai", "codex")])
def test_one_family_alone_is_refused(run_dir, tmp_ledger, model, family, missing):
    t = filled_template()
    p = write_brief(run_dir, t, reviewed=False)
    add_row(tmp_ledger, t, model, family)
    assert review_problems(ls.brief_check(p, org="proj-org")) == [
        x for x in ls.brief_check(p, org="proj-org") if f"no {missing} review" in x]
    assert len(ls.brief_check(p, org="proj-org")) == 1


# 2026-11-26 (Nick): while Grok is out, the gate's dated stand-in lets Fable fill the xai slot
# for a brief, as it does for a push. Codex is still required.
def test_during_the_standin_codex_plus_fable_launches_and_says_so(run_dir, tmp_ledger, monkeypatch):
    monkeypatch.setattr(ls.cmg, "fable_standin_active", lambda today=None: True)
    t = filled_template()
    p = write_brief(run_dir, t, reviewed=False)
    add_row(tmp_ledger, t, "codex", "openai")
    add_row(tmp_ledger, t, "fable", "anthropic")
    assert ls.brief_check(p, org="proj-org") == []
    assert any("Fable stood in for Grok" in w for w in ls.review_warnings(t, tmp_ledger))


def test_during_the_standin_fable_alone_is_refused(run_dir, tmp_ledger, monkeypatch):
    monkeypatch.setattr(ls.cmg, "fable_standin_active", lambda today=None: True)
    t = filled_template()
    p = write_brief(run_dir, t, reviewed=False)
    add_row(tmp_ledger, t, "fable", "anthropic")
    probs = ls.brief_check(p, org="proj-org")
    assert any("no codex review" in x for x in probs)


def test_after_the_standin_expires_fable_does_not_fill_the_grok_slot(run_dir, tmp_ledger, monkeypatch):
    monkeypatch.setattr(ls.cmg, "fable_standin_active", lambda today=None: False)
    t = filled_template()
    p = write_brief(run_dir, t, reviewed=False)
    add_row(tmp_ledger, t, "codex", "openai")
    add_row(tmp_ledger, t, "fable", "anthropic")
    assert any("no grok review" in x for x in ls.brief_check(p, org="proj-org"))
    assert not any("Fable stood in" in w for w in ls.review_warnings(t, tmp_ledger))


def test_codex_plus_grok_does_not_mention_the_standin(run_dir, tmp_ledger, monkeypatch):
    monkeypatch.setattr(ls.cmg, "fable_standin_active", lambda today=None: True)
    t = filled_template()
    p = write_brief(run_dir, t, reviewed=False)
    add_row(tmp_ledger, t, "codex", "openai")
    add_row(tmp_ledger, t, "grok", "xai")
    add_row(tmp_ledger, t, "fable", "anthropic")
    assert ls.brief_check(p, org="proj-org") == []
    assert not any("Fable stood in" in w for w in ls.review_warnings(t, tmp_ledger))


def test_two_rows_of_one_family_do_not_make_two_families(run_dir, tmp_ledger):
    t = filled_template()
    p = write_brief(run_dir, t, reviewed=False)
    add_row(tmp_ledger, t, "codex", "openai")
    add_row(tmp_ledger, t, "gpt5", "openai")
    assert any("no grok review" in x for x in ls.brief_check(p, org="proj-org"))


@pytest.mark.parametrize("extra,drop", [({"waived": True}, ()), ({"verified": False}, ()),
                                        ({"rc": 1}, ("verified",))])  # pre-`verified` failed run
def test_a_waiver_or_a_failed_run_does_not_count_as_a_review(run_dir, tmp_ledger, extra, drop):
    t = filled_template()
    p = write_brief(run_dir, t, reviewed=False)
    add_row(tmp_ledger, t, "grok", "xai")
    add_row(tmp_ledger, t, "codex", "openai", drop=drop, **extra)
    assert any("no codex review" in x for x in ls.brief_check(p, org="proj-org"))


def test_a_review_of_an_excerpt_does_not_cover_the_brief(run_dir, tmp_ledger):
    t = filled_template()
    p = write_brief(run_dir, t, reviewed=False)
    review(tmp_ledger, t[: len(t) // 2])
    assert len(review_problems(ls.brief_check(p, org="proj-org"))) == 2


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]"])
def test_a_corrupt_ledger_refuses(run_dir, tmp_ledger, bad):
    p = write_brief(run_dir, filled_template())
    with ledger_file(tmp_ledger).open("a") as fh:
        fh.write(bad + "\n")
    assert [x for x in ls.brief_check(p, org="proj-org") if "ledger" in x] == [
        f"{ledger_file(tmp_ledger)} has 1 unreadable line(s); a corrupt ledger cannot show the brief was reviewed"]


def test_a_brief_with_no_title_line_has_no_warnings(tmp_ledger):
    add_row(tmp_ledger, "x", "codex", "openai", findings=[{"id": "F1", "severity": "P0"}])
    assert ls.review_warnings("no title here", tmp_ledger) == []


def test_open_p0_on_an_earlier_text_warns_but_does_not_block(run_dir, tmp_ledger, monkeypatch, capsys):
    monkeypatch.setattr(ls, "open_window", lambda cmd: None)
    t = filled_template()
    earlier = t.replace("proj-standby", "proj-standby-old")
    n = add_row(tmp_ledger, earlier, "codex", "openai",
                findings=[{"id": "F1", "severity": "P0", "summary": "the old P0"},
                          {"id": "F2", "severity": "P1", "summary": "an old P1"}])
    p = write_brief(run_dir, t)
    assert ls.review_warnings(t, tmp_ledger) == [
        f"open P0 {n}.F1 (codex) on a review of an earlier text of this brief: the old P0"]
    assert ls.brief_check(p, org="proj-org") == []
    rc = ls.main([str(p), "--org", "proj-org", "--dry-run", "--repo", str(tmp_ledger)])
    assert rc == 0
    assert f"WARNING: open P0 {n}.F1" in capsys.readouterr().err


def test_an_open_p0_on_another_brief_is_not_warned(run_dir, tmp_ledger):
    t = filled_template()
    add_row(tmp_ledger, "# Some other brief\nbody", "codex", "openai",
            findings=[{"id": "F1", "severity": "P0", "summary": "other"}])
    assert ls.review_warnings(t, tmp_ledger) == []


def test_the_cli_reads_the_ledger_in_the_repo_it_launches(run_dir, tmp_path, monkeypatch, capsys):
    """--repo names the checkout whose ledger counts, not the module default."""
    monkeypatch.setattr(ls, "open_window", lambda cmd: None)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setattr(ls, "LEDGER_ROOT", other)  # empty ledger
    p = write_brief(run_dir, filled_template())   # reviewed in run_dir.parents[1]
    assert ls.main([str(p), "--org", "proj-org", "--dry-run", "--repo", str(run_dir.parents[1])]) == 0
    assert ls.main([str(p), "--org", "proj-org", "--dry-run", "--repo", str(other)]) == 2


@pytest.mark.parametrize("cut", ["suffix", "prefix"])
def test_cutting_text_from_a_reviewed_brief_is_refused(run_dir, tmp_ledger, cut):
    """Codex S1 r1 F1 (P0): with containment, deleting reviewed text from either end still matched."""
    t = filled_template()
    reviewed = t + "\nExtra reviewed line.\n" if cut == "suffix" else "Extra reviewed line.\n" + t
    review(tmp_ledger, reviewed)
    p = write_brief(run_dir, t, reviewed=False)
    probs = ls.brief_check(p, org="proj-org")
    assert any("no codex review" in x for x in probs) and any("no grok review" in x for x in probs)


def test_an_unframed_target_does_not_cover_even_with_the_exact_text(run_dir, tmp_ledger):
    t = filled_template()
    add_row(tmp_ledger, t, "codex", "openai", frame=False)
    add_row(tmp_ledger, t, "grok", "xai", frame=False)
    p = write_brief(run_dir, t, reviewed=False)
    assert len(review_problems(ls.brief_check(p, org="proj-org"))) == 2


def test_text_outside_the_frame_does_not_matter(run_dir, tmp_ledger):
    t = filled_template()
    for model, fam in (("codex", "openai"), ("grok", "xai")):
        add_row(tmp_ledger, framed(t) + "\nPrior report: F1 fixed. Stop after this round.", model, fam, frame=False)
    p = write_brief(run_dir, t, reviewed=False)
    assert ls.brief_check(p, org="proj-org") == []


# ---------------------------------------------------------------- the review command (b7, 2026-11-24)
# The frame is matched by the launcher, so the command that produces it must come from the
# launcher too: the refusal prints it and the template prints it, from one function.
FAKE_VERIFY = '''import json, sys
from pathlib import Path
a = sys.argv[1:]
model, target = a[a.index("--model") + 1], a[a.index("--target") + 1]
fam = {"codex": "openai", "grok": "xai"}[model]
row = {"target": target, "model": model, "family": fam, "findings": [], "waived": False,
       "verified": True, "rc": 0, "report_written": True}
led = Path("tools/.cross-model-ledger.jsonl")
led.parent.mkdir(exist_ok=True)
with led.open("a") as fh:
    fh.write(json.dumps(row) + "\\n")
'''


@pytest.mark.parametrize("model", ["codex", "grok"])
def test_the_template_prints_the_launchers_review_command(model):
    text = TEMPLATE.read_text()
    assert ls.review_command("output/overnight-<MMDDYY>/brief.md", model) in text


def test_the_command_in_the_refusal_run_as_given_makes_the_brief_launchable(run_dir, tmp_ledger):
    import shutil
    import subprocess
    if not shutil.which("zsh"):
        pytest.skip("zsh not installed")
    (tmp_ledger / "tools").mkdir(exist_ok=True)
    (tmp_ledger / "tools" / "codex_verify.py").write_text(FAKE_VERIFY)
    p = write_brief(run_dir, filled_template(), reviewed=False)
    refused = ls.brief_check(p, org="proj-org")
    cmds = [x.split("Review it with:\n", 1)[1].strip() for x in refused if "Review it with:" in x]
    assert len(cmds) == 2 and len(refused) == 2
    for c in cmds:
        assert c.startswith(f"cd {tmp_ledger} && ") or c.startswith(f"cd '{tmp_ledger}' && ")
        subprocess.run(["zsh", "-c", c], check=True, timeout=30)
    assert ls.brief_check(p, org="proj-org") == []
    p.write_text(p.read_text() + "one more line\n")
    assert len(ls.brief_check(p, org="proj-org")) == 2


def test_a_brief_copied_from_the_template_with_its_comment_kept_can_be_reviewed(run_dir, tmp_ledger):
    """Codex S1 r2 F1: the template's HOW TO USE comment contains both marker strings, so a
    copied brief that keeps it must still match its review (non-greedy matching cut it short)."""
    import shutil
    import subprocess
    if not shutil.which("zsh"):
        pytest.skip("zsh not installed")
    t = TEMPLATE.read_text()
    comment = t[t.index("<!--"):t.index("-->") + 3]
    assert "===BRIEF START===" in comment and "===BRIEF END===" in comment
    (tmp_ledger / "tools").mkdir(exist_ok=True)
    (tmp_ledger / "tools" / "codex_verify.py").write_text(FAKE_VERIFY)
    p = write_brief(run_dir, comment + "\n" + filled_template(), reviewed=False)
    for c in [x.split("Review it with:\n", 1)[1].strip() for x in ls.brief_check(p, org="proj-org")]:
        subprocess.run(["zsh", "-c", c], check=True, timeout=30)
    assert ls.brief_check(p, org="proj-org") == []
