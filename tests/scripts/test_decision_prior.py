"""Tests for decision_write.py's prior-ruling gate (Build Q): `add` refuses a question the
owner already answered, citing the line, unless --why-not-answered gives a reason.

Generic fixtures only (public repo). Each REPEATS row is a synthetic stand-in for one kind of
prior ruling: a policy line, a company-specific line, a defined term; plus a withdrawn company
known by a second name.
"""
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

import decision_write as dw  # noqa: E402

GOALS = """# Goals

## Target Criteria

**High conviction, defined (2026-01-01, approved by Owner):** "a four-part test for a target."
**No quota (2026-01-01, approved by Owner):** some account work is in without a quota.
  - **Expansion metric (2026-01-09, Owner):** "an expansion metric is acceptable."
- Not pursuing: PE turnarounds, pure research roles
- A commute within easy train reach passes (Owner, 2026-01-04).
- Weekend on-call: one weekend a month is fine; every weekend is out (Owner, 2026-01-02).
- **Widgetco role (2026-01-03, Owner):** "100 is my floor."
"""

PIPELINE = """# Pipeline

| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |
|---|---|---|---|---|---|---|---|
| Formerco | Forward Deployed PM | Withdrawn | 2026-01-07 | - | - | withdrew | - |
| Livelyco | Strategist | Applied 2026-01-08 | 2026-01-08 | wait | - | - | - |
| Summit | Ops Lead | Closed - they passed | 2026-01-06 | - | - | - | - |
| Twiceco | Second Seat | Round 2 complete | 2026-01-09 | wait | - | - | - |
| Twiceco | First Seat | Rejected | 2026-01-02 | - | - | - | - |
"""

COMPANIES_NO_ALIAS = """schema: 1
companies:
- canonical: Formerco
  type: target
- canonical: Livelyco
  type: target
- canonical: Summit
  type: target
"""

COMPANIES_ALIAS = COMPANIES_NO_ALIAS.replace(
    "- canonical: Formerco\n  type: target\n",
    "- canonical: Formerco\n  type: target\n  aliases:\n  - Newname\n")

# (topic key, patterns, anchor in GOALS, a question that repeats the ruling)
REPEATS = [
    ("every-weekend", [r"\bevery weekend\b", r"\bweekend on[- ]call\b"], "every weekend is out",
     "Examplecorp wants every weekend on call. Withdraw politely?"),
    ("widgetco-floor", [r"Widgetco.*\b(floor|walk[- ]away)\b"], "Widgetco role",
     "Widgetco: your offer-stage walk-away and package?"),
    ("easy-train", [r"\btrain\b"], "easy train reach",
     "Does a ferry ride count as easy train reach?"),
    ("pe-turnarounds", [r"(?-i:\bPE\b).{0,20}turnaround", r"private equity"], "PE turnarounds",
     "Is a PE turnaround seat in scope?"),
    ("high-conviction", [r"high[- ]conviction"], "High conviction, defined",
     "Sampleco BizOps Lead: high conviction?"),
    ("expansion-metric", [r"expansion metric"], "Expansion metric",
     "Name an expansion metric you would accept?"),
]


@pytest.fixture
def main(tmp_path):
    root = tmp_path / "main"
    (root / ".git").mkdir(parents=True)
    ws = root / "data" / "workstreams"
    ws.mkdir(parents=True)
    for name in ("alpha-work", "orchestration"):
        (ws / f"{name}.md").write_text(f"# Workstream: {name}\n", encoding="utf-8")
    (root / "data" / "goals.md").write_text(GOALS, encoding="utf-8")
    (root / "data" / "job-pipeline.md").write_text(PIPELINE, encoding="utf-8")
    (root / "data" / "companies.yaml").write_text(COMPANIES_NO_ALIAS, encoding="utf-8")
    return root


def run(root, capsys, *argv):
    code = dw.main(["--repo-root", str(root), *argv])
    out, _ = capsys.readouterr()
    return code, (json.loads(out) if out.strip().startswith("{") else None), out


def add(root, capsys, question, *extra):
    return run(root, capsys, "add", "--owner", "alpha-work", "--class", "scope",
               "--question", question, "--option", "a: yes", "--option", "b: no",
               "--acts", "the owner acts", "--predict", "a", "--predict-reason", "r", *extra)


def topic_add(root, capsys, key, patterns, anchor, file="data/goals.md", *extra):
    args = ["topic-add", "--key", key, "--file", file, "--anchor", anchor]
    for pat in patterns:
        args += ["--pattern", pat]
    return run(root, capsys, *args, *extra)


def events(root, name="questions.jsonl"):
    p = root / "data" / "run-manifest" / name
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def goals_line(root, anchor):
    lines = (root / "data" / "goals.md").read_text(encoding="utf-8").splitlines()
    return next(n for n, l in enumerate(lines, 1) if anchor in l)


def seed_all(root, capsys):
    for key, pats, anchor, _ in REPEATS:
        code, data, out = topic_add(root, capsys, key, pats, anchor)
        assert code == 0, out


# ------------------------------------------------------------------ topic-add
def test_topic_add_records_a_sealed_topic(main, capsys):
    code, data, _ = topic_add(main, capsys, "every-weekend", [r"\bevery weekend\b"], "every weekend is out")
    assert code == 0
    assert data["topic"]["line"] == goals_line(main, "every weekend is out")
    (ev,) = events(main, "ruling-topics.jsonl")
    assert ev["type"] == "topic" and ev["key"] == "every-weekend"
    assert ev["record_hash"] == dw.record_hash(ev)


@pytest.mark.parametrize("key,pats,anchor,file,code", [
    ("Bad Key", [r"x"], "every weekend is out", "data/goals.md", "bad_topic_key"),
    ("k", [r"(unclosed"], "every weekend is out", "data/goals.md", "bad_pattern"),
    ("k", [r"x*"], "every weekend is out", "data/goals.md", "bad_pattern"),      # matches ""
    ("k", [], "every weekend is out", "data/goals.md", "no_pattern"),
    ("k", [r"x"], "no such text", "data/goals.md", "anchor_not_found"),
    ("k", [r"x"], "(Owner, 2026-01-0", "data/goals.md", "anchor_ambiguous"),
    ("k", [r"x"], "x", "../outside.md", "cite_outside"),
    ("k", [r"x"], "x", "data/missing.md", "cite_missing"),
])
def test_topic_add_refuses_a_bad_topic(main, capsys, key, pats, anchor, file, code):
    (main.parent / "outside.md").write_text("x\n", encoding="utf-8")
    rc, data, _ = topic_add(main, capsys, key, pats, anchor, file)
    assert rc == 1 and data["code"] == code
    assert events(main, "ruling-topics.jsonl") == []


def test_topic_add_refuses_an_existing_key_unless_replace(main, capsys):
    topic_add(main, capsys, "k", [r"weekend"], "every weekend is out")
    rc, data, _ = topic_add(main, capsys, "k", [r"seven"], "every weekend is out")
    assert rc == 1 and data["code"] == "topic_exists"
    rc, _, _ = topic_add(main, capsys, "k", [r"seven"], "every weekend is out", "data/goals.md", "--replace")
    assert rc == 0
    rc, data, _ = add(main, capsys, "Seven of them?")
    assert rc == 1 and data["code"] == "already_answered"
    rc, _, _ = add(main, capsys, "Is a weekend enough?")          # the replaced pattern is gone
    assert rc == 0


# ------------------------------------------------------------------ add: the seven repeats
@pytest.mark.parametrize("key,pats,anchor,question", REPEATS, ids=[r[0] for r in REPEATS])
def test_repeat_is_refused_with_the_ruling_text(main, capsys, key, pats, anchor, question):
    seed_all(main, capsys)
    rc, data, _ = add(main, capsys, question)
    assert rc == 1 and data["code"] == "already_answered"
    hit = next(a for a in data["answers"] if a["topic"] == key)
    assert hit["file"] == "data/goals.md"
    assert hit["line"] == goals_line(main, anchor)
    assert anchor in hit["text"]                           # the answer itself, not only a pointer
    assert "--why-not-answered" in data["message"]
    assert [e for e in events(main) if e.get("type") == "asked"] == []
    assert events(main, "predictions.jsonl") == []      # Grok r1 claim 1: no prediction either
    cited = [e for e in events(main) if e.get("type") == "cited"]
    assert key in {e["topic"] for e in cited}
    assert all(e["record_hash"] == dw.record_hash(e) for e in cited)


def test_withdrawn_company_is_not_matched_without_its_alias(main, capsys):
    rc, _, out = add(main, capsys, "Newname, Forward Deployed PM: apply?")
    assert rc == 0, out


def test_withdrawn_company_is_matched_through_its_alias(main, capsys):
    (main / "data" / "companies.yaml").write_text(COMPANIES_ALIAS, encoding="utf-8")
    rc, data, _ = add(main, capsys, "Newname, Forward Deployed PM: apply?")
    assert rc == 1 and data["code"] == "already_answered"
    (hit,) = data["answers"]
    assert hit["file"] == "data/job-pipeline.md" and hit["line"] == 5
    assert "Withdrawn" in hit["text"]
    assert hit["topic"] == "company:Formerco"


def test_canonical_name_of_a_closed_company_is_matched(main, capsys):
    rc, data, _ = add(main, capsys, "Summit, Ops Lead: try again?")
    assert rc == 1 and data["answers"][0]["topic"] == "company:Summit"


def test_company_with_a_live_row_is_not_a_ruling(main, capsys):
    rc, _, out = add(main, capsys, "Twiceco: nudge on Monday?")
    assert rc == 0, out


def test_cite_is_the_terminal_row_not_the_first_row_of_the_company(main, capsys):
    p = main / "data" / "job-pipeline.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "| Twiceco | Second Seat | Round 2 complete |", "| Twiceco | Second Seat | Withdrawn |"),
        encoding="utf-8")
    rc, data, _ = add(main, capsys, "Twiceco again?")
    assert rc == 1
    assert sorted(a["line"] for a in data["answers"]) == [8, 9]
    assert all(a["topic"] == "company:Twiceco" for a in data["answers"])


def test_company_with_a_live_row_under_an_alias_is_not_a_ruling(main, capsys):
    """Codex r1 F1: rows are grouped by canonical company, not raw spelling."""
    (main / "data" / "companies.yaml").write_text(COMPANIES_ALIAS, encoding="utf-8")
    p = main / "data" / "job-pipeline.md"
    p.write_text(p.read_text(encoding="utf-8")
                 + "| Newname | Second Seat | Interviewing | 2026-01-10 | prep | - | - | - |\n",
                 encoding="utf-8")
    for q in ("Newname: next interview?", "Formerco again?"):
        rc, _, out = add(main, capsys, q)
        assert rc == 0, out


def test_a_damaged_company_file_does_not_break_add(main, capsys):
    """Codex r1 F3: an unreadable dimension falls back to raw pipeline names, with a warning."""
    (main / "data" / "companies.yaml").write_text("schema: 1\ncompanies: []\n", encoding="utf-8")
    rc, data, out = add(main, capsys, "Ship part one first?")
    assert rc == 0, out
    assert any("companies.yaml" in w for w in data["warnings"])
    rc, data, _ = add(main, capsys, "Formerco again?")
    assert rc == 1 and data["answers"][0]["topic"] == "company:Formerco"
    assert any("companies.yaml" in w for w in data["warnings"])


def test_an_unloaded_company_file_is_not_read_when_nothing_is_terminal(main, capsys, monkeypatch):
    p = main / "data" / "job-pipeline.md"
    p.write_text(PIPELINE.split("| Formerco")[0]
                 + "| Livelyco | Strategist | Applied 2026-01-08 | 2026-01-08 | wait | - | - | - |\n",
                 encoding="utf-8")
    import company_dim
    monkeypatch.setattr(company_dim, "load", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read")))
    rc, data, out = add(main, capsys, "Ship part one first?")
    assert rc == 0, out
    assert "warnings" not in data


def test_active_company_is_not_a_ruling(main, capsys):
    rc, _, out = add(main, capsys, "Livelyco: nudge on Monday?")
    assert rc == 0, out


def test_company_match_is_case_sensitive_and_whole_word(main, capsys):
    rc, _, out = add(main, capsys, "Reach the summit of the list, or Summitry?")
    assert rc == 0, out


def test_company_match_works_without_the_company_file(main, capsys):
    (main / "data" / "companies.yaml").unlink()
    rc, data, _ = add(main, capsys, "Formerco again?")
    assert rc == 1 and data["answers"][0]["topic"] == "company:Formerco"
    assert "warnings" not in data                  # a missing file is a normal state, not damage


def test_cite_follows_the_line_when_lines_are_inserted_above(main, capsys):
    seed_all(main, capsys)
    g = main / "data" / "goals.md"
    g.write_text("# Goals\n\nnew line one\nnew line two\n" + g.read_text(encoding="utf-8")[8:],
                 encoding="utf-8")
    rc, data, _ = add(main, capsys, "Is every weekend OK?")
    assert data["answers"][0]["line"] == goals_line(main, "every weekend is out")


def test_patterns_ignore_case_but_an_acronym_can_opt_out(main, capsys):
    seed_all(main, capsys)
    rc, data, _ = add(main, capsys, "EXPANSION METRIC ok?")
    assert rc == 1 and data["answers"][0]["topic"] == "expansion-metric"
    rc, _, out = add(main, capsys, "Is the pe turnaround of the team done?")
    assert rc == 0, out


def test_unrelated_question_passes(main, capsys):
    seed_all(main, capsys)
    rc, _, out = add(main, capsys, "Ship part one first?")
    assert rc == 0, out


def test_why_not_answered_adds_the_question_and_records_the_reason(main, capsys):
    seed_all(main, capsys)
    rc, data, _ = add(main, capsys, "Does a ferry ride count as easy train reach?",
                      "--why-not-answered", "the ruling covers trains, not ferries")
    assert rc == 0
    (asked,) = [e for e in events(main) if e.get("type") == "asked"]
    assert asked["not_answered"] == {"topics": ["easy-train"],
                                     "reason": "the ruling covers trains, not ferries"}
    assert asked["record_hash"] == dw.record_hash(asked)
    assert data["not_answered"]["topics"] == ["easy-train"]
    assert [e for e in events(main) if e.get("type") == "cited"] == []


def test_a_question_with_no_prior_ruling_carries_no_override_field(main, capsys):
    seed_all(main, capsys)
    rc, data, _ = add(main, capsys, "Ship part one first?", "--why-not-answered", "no ruling exists")
    assert rc == 0 and "not_answered" not in data
    (asked,) = [e for e in events(main) if e.get("type") == "asked"]
    assert "not_answered" not in asked


def test_blank_why_not_answered_is_refused(main, capsys):
    seed_all(main, capsys)
    rc, data, _ = add(main, capsys, "Is every weekend OK?", "--why-not-answered", "  ")
    assert rc == 1 and data["code"] == "blank_field"


@pytest.mark.parametrize("dry", [False, True])
def test_a_corrupt_queue_refuses_a_matching_add_and_writes_nothing(main, capsys, dry):
    """Grok r1 F1: T8 (a corrupt queue refuses every command) holds on the citing path too."""
    add(main, capsys, "Ship part one first?")
    q = main / "data" / "run-manifest" / "questions.jsonl"
    q.write_text(q.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    before = q.read_bytes()
    rc, data, _ = add(main, capsys, "Formerco again?", *(["--dry-run"] if dry else []))
    assert rc == 1 and data["code"] == "corrupt_queue"
    assert q.read_bytes() == before


def test_one_override_of_a_two_row_company_counts_once(main, capsys):
    """Grok r1 F2: the override names each topic once, however many rows it cites."""
    p = main / "data" / "job-pipeline.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "| Twiceco | Second Seat | Round 2 complete |", "| Twiceco | Second Seat | Withdrawn |"),
        encoding="utf-8")
    rc, data, _ = add(main, capsys, "Twiceco again?", "--why-not-answered", "a new seat opened")
    assert rc == 0 and data["not_answered"]["topics"] == ["company:Twiceco"]
    _, data, _ = run(main, capsys, "check")
    assert data["overrides"] == {"company:Twiceco": 1}


def test_dry_run_refusal_records_no_citation(main, capsys):
    seed_all(main, capsys)
    rc, data, _ = add(main, capsys, "Is every weekend OK?", "--dry-run")
    assert rc == 1 and data["code"] == "already_answered"
    assert events(main) == []


def test_stale_cite_refuses_loudly(main, capsys):
    seed_all(main, capsys)
    g = main / "data" / "goals.md"
    g.write_text(g.read_text(encoding="utf-8").replace("every weekend is out", "weekends are out"),
                 encoding="utf-8")
    rc, data, _ = add(main, capsys, "Is every weekend OK?")
    assert rc == 1 and data["code"] == "topic_cite_stale"


def test_queue_commands_still_work_with_cited_events(main, capsys):
    seed_all(main, capsys)
    add(main, capsys, "Is every weekend OK?")
    rc, data, _ = run(main, capsys, "list", "--json")
    assert rc == 0 and data["open"] == []
    rc, data, _ = run(main, capsys, "check")
    assert rc == 0, data


# ------------------------------------------------------------------ resolve --topic: the ladder's first rung
def rule(root, line):
    p = root / "data" / "workstreams" / "alpha-work.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n" + line + "\n", encoding="utf-8")


def test_resolve_with_topic_catches_the_next_asking(main, capsys):
    add(main, capsys, "Use the staging bucket for exports?")
    rule(main, "RULING Q1 | option: a | reason: staging is fine | window: s1")
    rc, data, out = run(main, capsys, "resolve", "Q1", "--same-reason", "--topic", "staging-bucket",
                        "--pattern", r"staging bucket")
    assert rc == 0, out
    assert data["topic"]["file"] == "data/workstreams/alpha-work.md"
    rc, data, _ = add(main, capsys, "Export to the staging bucket again?")
    assert rc == 1 and data["code"] == "already_answered"
    assert data["answers"][0]["text"].startswith("RULING Q1 |")


def test_resolve_topic_needs_a_pattern(main, capsys):
    add(main, capsys, "Use the staging bucket for exports?")
    rule(main, "RULING Q1 | option: a | reason: staging is fine | window: s1")
    rc, data, _ = run(main, capsys, "resolve", "Q1", "--same-reason", "--topic", "staging-bucket")
    assert rc == 1 and data["code"] == "no_pattern"
    assert [e for e in events(main) if e.get("type") == "resolved"] == []


def test_resolve_pattern_without_topic_is_refused(main, capsys):
    add(main, capsys, "Use the staging bucket for exports?")
    rule(main, "RULING Q1 | option: a | reason: staging is fine | window: s1")
    rc, data, _ = run(main, capsys, "resolve", "Q1", "--same-reason", "--pattern", r"staging bucket")
    assert rc == 1 and data["code"] == "pattern_without_topic"
    assert [e for e in events(main) if e.get("type") == "resolved"] == []


def test_resolve_with_a_bad_topic_pattern_writes_nothing(main, capsys):
    add(main, capsys, "Use the staging bucket for exports?")
    rule(main, "RULING Q1 | option: a | reason: staging is fine | window: s1")
    rc, data, _ = run(main, capsys, "resolve", "Q1", "--same-reason", "--topic", "staging-bucket",
                      "--pattern", "(unclosed")
    assert rc == 1 and data["code"] == "bad_pattern"
    assert [e for e in events(main) if e.get("type") == "resolved"] == []
    assert events(main, "ruling-topics.jsonl") == []


def test_ruling_q1_anchor_does_not_match_q12(main, capsys):
    rule(main, "RULING Q12 | option: a | reason: other | window: s1")
    add(main, capsys, "Use the staging bucket for exports?")
    rule(main, "RULING Q1 | option: a | reason: staging is fine | window: s1")
    rc, data, out = run(main, capsys, "resolve", "Q1", "--same-reason", "--topic", "staging-bucket",
                        "--pattern", r"staging bucket")
    assert rc == 0, out


# ------------------------------------------------------------------ check: stale cites, PROMOTE, overrides
def cite_n(root, capsys, n, question):
    for _ in range(n):
        rc, data, _ = add(root, capsys, question)
        assert data["code"] == "already_answered"


def test_check_lists_promote_on_the_third_citation_outside_goals(main, capsys):
    rule(main, "Owner ruled: exports go to the staging bucket.")
    topic_add(main, capsys, "staging-bucket", [r"staging bucket"], "exports go to the staging bucket",
              "data/workstreams/alpha-work.md")
    cite_n(main, capsys, 2, "Staging bucket for exports?")
    _, data, _ = run(main, capsys, "check")
    assert data["promote"] == []
    cite_n(main, capsys, 1, "Staging bucket for exports?")
    rc, data, _ = run(main, capsys, "check")
    assert rc == 0
    (p,) = data["promote"]
    assert p["topic"] == "staging-bucket" and p["citations"] == 3
    assert p["file"] == "data/workstreams/alpha-work.md"


def test_check_never_promotes_a_goals_ruling(main, capsys):
    seed_all(main, capsys)
    cite_n(main, capsys, 4, "Is every weekend OK?")
    _, data, _ = run(main, capsys, "check")
    assert data["promote"] == []


def test_a_goals_cite_spelled_with_dotdot_is_stored_plainly_and_never_promoted(main, capsys):
    """Codex r1 F2: the stored cite path is the resolved repo-relative path."""
    rc, data, _ = topic_add(main, capsys, "expansion-metric", [r"expansion metric"],
                            "Expansion metric", "data/workstreams/../goals.md")
    assert rc == 0
    (ev,) = events(main, "ruling-topics.jsonl")
    assert ev["file"] == "data/goals.md"
    cite_n(main, capsys, 3, "An expansion metric?")
    _, data, _ = run(main, capsys, "check")
    assert data["promote"] == []


def test_check_never_promotes_a_company_row(main, capsys):
    cite_n(main, capsys, 4, "Formerco again?")
    _, data, _ = run(main, capsys, "check")
    assert data["promote"] == []


def test_citation_count_survives_a_topic_replace(main, capsys):
    rule(main, "Owner ruled: exports go to the staging bucket.")
    cite = ("data/workstreams/alpha-work.md",)
    topic_add(main, capsys, "staging-bucket", [r"staging bucket"], "exports go to the staging bucket", *cite)
    cite_n(main, capsys, 2, "Staging bucket for exports?")
    topic_add(main, capsys, "staging-bucket", [r"staging"], "exports go to the staging bucket", *cite,
              "--replace")
    cite_n(main, capsys, 1, "Staging again?")
    _, data, _ = run(main, capsys, "check")
    assert data["promote"][0]["citations"] == 3


def test_check_reports_a_stale_cite_as_a_problem(main, capsys):
    seed_all(main, capsys)
    g = main / "data" / "goals.md"
    g.write_text(g.read_text(encoding="utf-8").replace("every weekend is out", "weekends are out"), encoding="utf-8")
    rc, data, _ = run(main, capsys, "check")
    assert rc == 1
    assert any("every-weekend" in p and "anchor" in p for p in data["problems"])


def test_check_counts_overrides_per_topic(main, capsys):
    seed_all(main, capsys)
    add(main, capsys, "Ferry and easy train reach?", "--why-not-answered", "a ferry is not a train")
    _, data, _ = run(main, capsys, "check")
    assert data["overrides"] == {"easy-train": 1}


def test_check_sees_an_edited_topic_record(main, capsys):
    seed_all(main, capsys)
    p = main / "data" / "run-manifest" / "ruling-topics.jsonl"
    p.write_text(p.read_text(encoding="utf-8").replace("every weekend is out", "every weekend is"), encoding="utf-8")
    rc, data, _ = run(main, capsys, "check")
    assert rc == 1
    assert any("edited outside the tool" in p for p in data["problems"])
