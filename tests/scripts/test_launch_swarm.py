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


def write_brief(run_dir: Path, text: str) -> Path:
    p = run_dir / "brief.md"
    p.write_text(text)
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


@pytest.mark.parametrize("field", ["Owns", "Done when"])
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
