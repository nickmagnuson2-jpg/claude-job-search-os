"""tools/facts.py: one record per fact, resolve() source-first then date, get exit codes, sweep.

KB pilot builds 1-2 (output/kb-research/112626-KB-S5-vs-S5B-comparison.md section 3).
Every name, employer and title here is a placeholder.
"""
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import facts  # noqa: E402

SCRIPT = REPO / "tools" / "facts.py"

TITLE = ('<!-- fact: role_title.acme | as_of: 2026-10-01 | source: nick "use the full title" '
         '| forms: cv=Chief of Widgets, Gadgets; prose=Chief of Widgets, reporting to the CEO '
         '| rule: full title everywhere | banned: "Widget Intern"; "Engagement Lead" generic '
         '| supersedes: "Widget Boss" until cv 2026-09-01, prose 2026-10-01, source nick -->')


def rec(source="nick \"x\"", as_of="2026-01-01", value="A", key="k"):
    body = f"{key} | as_of: {as_of} | source: {source} | forms: cv={value}"
    return facts.parse_record(body)


# ---------------------------------------------------------------- resolve: the owner's ruling

def test_agent_value_never_beats_owner_value_even_when_newer():
    owner = rec('nick "my words"', "2026-01-01", "Owner Title")
    agent = rec("agent extracted", "2026-11-01", "Agent Title")
    assert facts.resolve([agent, owner]).value() == "Owner Title"
    assert facts.resolve([owner, agent]).value() == "Owner Title"


def test_owner_file_beats_agent_and_owner_words_beat_owner_file():
    words = rec('nick "said"', "2026-01-01", "W")
    copied = rec("nick_file data/reflections/x.md", "2026-06-01", "F")
    agent = rec("agent", "2026-11-01", "G")
    assert facts.resolve([agent, copied]).value() == "F"
    assert facts.resolve([agent, copied, words]).value() == "W"


def test_within_a_rank_the_later_date_wins():
    old = rec('nick "a"', "2026-01-01", "Old")
    new = rec('nick "b"', "2026-02-01", "New")
    assert facts.resolve([old, new]).value() == "New"
    assert facts.resolve([new, old]).value() == "New"


def test_same_rank_same_date_different_values_is_a_conflict():
    a = rec('nick "a"', "2026-01-01", "One")
    b = rec('nick "b"', "2026-01-01", "Two")
    with pytest.raises(facts.Conflict):
        facts.resolve([a, b])
    assert facts.resolve([a, rec('nick "c"', "2026-01-01", "One")]).value() == "One"


def test_resolve_of_nothing_is_none():
    assert facts.resolve([]) is None


# ---------------------------------------------------------------- parsing

def test_full_record_parses_with_semicolons_inside_a_form():
    [r], errs = facts.scan_text(f"- **Role:** x\n  {TITLE}\n", "data/projects/acme.md")
    assert errs == []
    assert r.key == "role_title.acme" and r.line == 2
    assert r.forms == {"cv": "Chief of Widgets, Gadgets",
                       "prose": "Chief of Widgets, reporting to the CEO"}
    assert r.source_kind == "nick" and r.source_detail == "use the full title"
    assert r.banned == [("Widget Intern", False), ("Engagement Lead", True)]
    [s] = r.supersedes
    assert s.text == "Widget Boss" and s.source == "nick"
    assert s.until == {"cv": date(2026, 9, 1), "prose": date(2026, 10, 1)}


def test_a_form_value_may_carry_its_own_semicolon():
    r = facts.parse_record("k | as_of: 2026-01-01 | source: agent | "
                           "forms: cv=Lead (2018); Analyst (2017); spoken=lead")
    assert r.forms == {"cv": "Lead (2018); Analyst (2017)", "spoken": "lead"}


@pytest.mark.parametrize("body,why", [
    ("k | as_of: 2026-1-01 | source: agent | forms: cv=A", "not YYYY-MM-DD"),
    ("k | as_of: 2026-13-01 | source: agent | forms: cv=A", "month"),
    ("k | as_of: 2026-01-01 | source: nick | forms: cv=A", "quotes"),
    ("k | as_of: 2026-01-01 | source: nick \"\" | forms: cv=A", "quotes"),
    ("k | as_of: 2026-01-01 | source: nick_file | forms: cv=A", "path"),
    ("k | as_of: 2026-01-01 | source: someone | forms: cv=A", "nick, nick_file or agent"),
    ("k | as_of: 2026-01-01 | source: agent | forms: A", "audience=value"),
    ("k | as_of: 2026-01-01 | source: agent", "no forms"),
    ("k | source: agent | forms: cv=A", "no as_of"),
    ("k | as_of: 2026-01-01 | forms: cv=A", "no source"),
    ("k | as_of: 2026-01-01 | source: agent | forms: cv=A | colour: red", "unknown part"),
    ("k | as_of: 2026-01-01 | as_of: 2026-01-02 | source: agent | forms: cv=A", "twice"),
    ("two words | as_of: 2026-01-01 | source: agent | forms: cv=A", "bad key"),
    ("k | as_of: 2026-01-01 | source: agent | forms: cv=A | banned: Intern", "quoted"),
    ("k | as_of: 2026-01-01 | source: agent | forms: cv=A | supersedes: \"X\"", "until"),
    ("k | as_of: 2026-01-01 | source: agent | forms: cv=A | supersedes: \"X\" until soon",
     "YYYY-MM-DD"),
    ("k | as_of: 2026-01-01 | source: agent | forms: cv=A | supersedes: \"X\" until "
     "2026-01-01, source boss", "unknown source"),
])
def test_malformed_records_are_refused(body, why):
    with pytest.raises(facts.Malformed, match=why):
        facts.parse_record(body)


def test_a_record_that_does_not_close_on_its_line_is_an_error():
    recs, errs = facts.scan_text("<!-- fact: k | as_of: 2026-01-01\n | source: agent -->", "f.md")
    assert recs == [] and len(errs) == 1 and "does not close" in errs[0]


def test_ordinary_comments_are_not_records():
    recs, errs = facts.scan_text("- **Role:** x <!-- CHANGED 2026-10-21 per owner -->", "f.md")
    assert recs == [] and errs == []


# ---------------------------------------------------------------- homes and get

def make_repo(root: Path, record: str = TITLE, owner: str = "Pat Example") -> Path:
    (root / "data/projects").mkdir(parents=True)
    (root / "data/projects/acme.md").write_text(f"# Acme\n\n- **Role:** Chief of Widgets\n  {record}\n")
    (root / "data/profile.md").write_text(f"# Profile\n\n- **Name:** {owner}\n")
    (root / "data/goals.md").write_text("# Goals\n")
    return root


def run(root, *args):
    return subprocess.run([sys.executable, str(SCRIPT), "--repo-root", str(root), *args],
                          capture_output=True, text=True,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_get_prints_the_value_for_the_audience_with_exit_0(tmp_path):
    root = make_repo(tmp_path / "repo")
    r = run(root, "get", "role_title.acme", "--audience", "prose")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "Chief of Widgets, reporting to the CEO"


def test_get_json_names_the_home_line(tmp_path):
    root = make_repo(tmp_path / "repo")
    r = run(root, "get", "role_title.acme", "--json")
    assert r.returncode == 0 and '"home": "data/projects/acme.md:4"' in r.stdout


def test_get_unknown_key_exits_3_and_prints_no_value(tmp_path):
    root = make_repo(tmp_path / "repo")
    r = run(root, "get", "role_title.other")
    assert r.returncode == 3 and r.stdout == ""
    assert "NO RECORD" in r.stderr and "not evidence" in r.stderr


def test_get_missing_audience_exits_3(tmp_path):
    root = make_repo(tmp_path / "repo")
    r = run(root, "get", "role_title.acme", "--audience", "spoken")
    assert r.returncode == 3 and r.stdout == ""


def test_get_fails_closed_when_a_home_is_missing(tmp_path):
    root = make_repo(tmp_path / "repo")
    (root / "data/goals.md").unlink()
    r = run(root, "get", "role_title.acme")
    assert r.returncode == 2 and r.stdout == ""


def test_get_fails_closed_when_projects_folder_is_empty(tmp_path):
    root = make_repo(tmp_path / "repo")
    (root / "data/projects/acme.md").unlink()
    assert run(root, "get", "role_title.acme").returncode == 2


def test_get_fails_closed_on_any_malformed_record(tmp_path):
    root = make_repo(tmp_path / "repo")
    with open(root / "data/goals.md", "a") as fh:
        fh.write("<!-- fact: broken | as_of: soon | source: agent | forms: cv=x -->\n")
    r = run(root, "get", "role_title.acme")
    assert r.returncode == 2 and r.stdout == ""


def test_get_refuses_a_conflict(tmp_path):
    root = make_repo(tmp_path / "repo")
    with open(root / "data/profile.md", "a") as fh:
        fh.write('<!-- fact: role_title.acme | as_of: 2026-10-01 | source: nick "other" '
                 '| forms: cv=Different -->\n')
    assert run(root, "get", "role_title.acme").returncode == 2


def test_get_from_a_records_file_ignores_the_homes(tmp_path):
    rf = tmp_path / "records.md"
    rf.write_text(f"list\n\n```\n{TITLE}\n```\n")
    r = run(tmp_path / "nowhere", "--records", str(rf), "get", "role_title.acme", "--audience", "cv")
    assert r.returncode == 0 and r.stdout.strip() == "Chief of Widgets, Gadgets"


def test_validate_exit_codes(tmp_path):
    root = make_repo(tmp_path / "repo")
    assert run(root, "validate").returncode == 0
    with open(root / "data/goals.md", "a") as fh:
        fh.write("<!-- fact: broken | as_of: soon | source: agent | forms: cv=x -->\n")
    assert run(root, "validate").returncode == 1
    (root / "data/goals.md").unlink()
    assert run(root, "validate").returncode == 2


# ---------------------------------------------------------------- judging text

@pytest.fixture
def cur():
    [r], _ = facts.scan_text(TITLE)
    return {r.key: r}


def counted(text, cur, path="output/acme/112026-acme-prep.md", aud="prose",
            as_at=date(2026, 11, 27), owner="Pat Example"):
    return [h for h in facts.judge_text(text, cur, path=path, audience=aud, as_at=as_at,
                                        owner=owner) if h["held"] is None]


def test_banned_form_counts(cur):
    [h] = counted("Pat was the Widget Intern at Acme.", cur)
    assert h["kind"] == "banned" and h["key"] == "role_title.acme"


def test_guardrail_line_is_held(cur):
    assert counted("Never write Widget Intern for this role.", cur) == []


def test_override_needs_a_reason(cur):
    assert counted("Pat, Widget Intern <!-- fact-ok: archived copy -->", cur) == []
    assert len(counted("Pat, Widget Intern <!-- fact-ok:  -->", cur)) == 1


def test_superseded_form_counts_only_after_its_until_date_for_the_audience(cur):
    line = "Pat served as Widget Boss at Acme."
    assert counted(line, cur, aud="prose", as_at=date(2026, 9, 15)) == []
    assert len(counted(line, cur, aud="cv", as_at=date(2026, 9, 15))) == 1
    assert len(counted(line, cur, aud="prose", as_at=date(2026, 10, 2))) == 1


def test_generic_form_counts_only_when_anchored_to_the_owner(cur):
    other = "Their Engagement Lead, Sam Other, joined the call."
    assert counted(other, cur) == []
    assert len(counted("Pat Example was an Engagement Lead.", cur)) == 1
    assert len(counted("I was an Engagement Lead there.", cur,
                       path="output/acme/112026-acme-cold-outreach.md")) == 1
    assert counted("I met their Engagement Lead.", cur, path="output/acme/acme.md") == []
    assert len(counted("  - position: Engagement Lead", cur, path="output/cv/x.yaml",
                       aud="cv")) == 1


def test_form_matches_on_word_boundaries_not_inside_words(cur):
    assert counted("Pat was a Widget Interns-only mentor.", cur) == []
    assert len(counted("Pat, widget   intern, Acme", cur)) == 1


# ---------------------------------------------------------------- sweep (build 2)

def make_sweep_repo(tmp_path) -> Path:
    root = make_repo(tmp_path / "repo")
    (root / "data/company-notes").mkdir()
    (root / "data/company-notes/acme.md").write_text("Pat was the Widget Intern.\n")
    (root / "coaching/coached-answers").mkdir(parents=True)
    (root / "coaching/coached-answers/pitch.md").write_text("I was the Widget Intern.\n")
    out = root / "output"
    (out / "acme").mkdir(parents=True)
    (out / "acme/acme.md").write_text("Pat Example, Widget Intern.\n")                 # dossier
    (out / "acme/112026-acme-prep.md").write_text("Pat was Widget Intern.\n")          # prep
    (out / "acme/112026-acme-cover-letter.md").write_text("Fine text.\n")
    (out / "acme/090126-acme-cheat-sheet.md").write_text("Pat, Widget Boss.\n")        # pre-ruling
    (out / "cv").mkdir()
    (out / "cv/112026-example.yaml").write_text("  - position: Widget Intern\n")       # CV
    (out / "analysis").mkdir()
    (out / "analysis/112026-acme-prep.md").write_text("Widget Intern\n")             # excluded
    return root


def test_sweep_lists_a_seeded_hit_in_every_artifact_type(tmp_path):
    root = make_sweep_repo(tmp_path)
    rep = facts.sweep(root, date(2026, 11, 27))
    art = {h["path"] for h in rep["scopes"]["artifacts"]["hits"]}
    assert art == {"output/acme/acme.md", "output/acme/112026-acme-prep.md",
                   "output/cv/112026-example.yaml", "coaching/coached-answers/pitch.md"}
    copies = {h["path"] for h in rep["scopes"]["copies"]["hits"]}
    assert "data/company-notes/acme.md" in copies
    assert rep["drift"] is True


def test_sweep_dates_defects_by_file_but_counts_next_edit_blocks_as_of_today(tmp_path):
    root = make_sweep_repo(tmp_path)
    rep = facts.sweep(root, date(2026, 11, 27))["scopes"]["artifacts"]
    pre = "output/acme/090126-acme-cheat-sheet.md"
    assert pre not in {h["path"] for h in rep["hits"]}          # correct when written
    assert pre in rep["would_block_on_next_edit"]               # but reusing it would block
    assert "output/acme/112026-acme-cover-letter.md" not in rep["would_block_on_next_edit"]


def test_sweep_excludes_output_analysis(tmp_path):
    root = make_sweep_repo(tmp_path)
    rep = facts.sweep(root, date(2026, 11, 27))
    assert not any(h["path"].startswith("output/analysis/")
                   for h in rep["scopes"]["artifacts"]["hits"])


def test_sweep_fails_closed_on_an_empty_artifact_scope(tmp_path):
    root = make_repo(tmp_path / "repo")
    (root / "data/company-notes").mkdir()
    (root / "data/company-notes/acme.md").write_text("ok\n")
    with pytest.raises(facts.Unreadable, match="artifacts is empty"):
        facts.sweep(root, date(2026, 11, 27))
    r = run(root, "sweep")
    assert r.returncode == 2


def test_sweep_fails_closed_with_no_records(tmp_path):
    root = make_sweep_repo(tmp_path)
    (root / "data/projects/acme.md").write_text("# Acme, no record\n")
    with pytest.raises(facts.Unreadable, match="no fact records"):
        facts.sweep(root, date(2026, 11, 27))


def test_sweep_never_reaches_the_sealed_sibling_via_a_symlinked_artifact(tmp_path):
    root = make_sweep_repo(tmp_path)
    sealed = tmp_path / "repo-sealed"
    sealed.mkdir()
    (sealed / "secret-prep.md").write_text("sealed text\n")
    os.symlink(sealed / "secret-prep.md", root / "output/acme/112126-acme-prep.md")
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.sweep(root, date(2026, 11, 27))


def test_sweep_never_reaches_the_sealed_sibling_via_an_extra_copy(tmp_path):
    root = make_sweep_repo(tmp_path)
    (tmp_path / "repo-sealed").mkdir()
    (tmp_path / "repo-sealed/x.md").write_text("sealed\n")
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.sweep(root, date(2026, 11, 27), extra_copies=[str(tmp_path / "repo-sealed/x.md")])
    r = run(root, "sweep", "--copy", str(tmp_path / "repo-sealed/x.md"))
    assert r.returncode == 2 and r.stdout == ""


def test_sweep_never_reaches_the_in_repo_sealed_tree(tmp_path):
    root = make_sweep_repo(tmp_path)
    (root / "data/project-background").mkdir()
    (root / "data/project-background/x.md").write_text("sealed\n")
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.sweep(root, date(2026, 11, 27), extra_copies=["data/project-background/x.md"])


def test_sweep_cli_exit_1_on_drift_and_0_when_clean(tmp_path):
    root = make_sweep_repo(tmp_path)
    assert run(root, "sweep", "--today", "2026-11-27").returncode == 1
    for p in [root / "data/company-notes/acme.md", root / "coaching/coached-answers/pitch.md",
              root / "output/acme/acme.md", root / "output/acme/112026-acme-prep.md",
              root / "output/cv/112026-example.yaml"]:
        p.write_text("clean\n")
    r = run(root, "sweep", "--today", "2026-11-27")
    assert r.returncode == 0, r.stdout


def test_nightly_never_raises_and_records_drift(tmp_path):
    root = make_repo(tmp_path / "repo")
    rep = facts.nightly(root, date(2026, 11, 27))
    assert rep["drift"] is True and "error" in rep
    rep = facts.nightly(tmp_path / "missing", date(2026, 11, 27))
    assert rep["drift"] is True


def test_file_date_reads_the_mmddyy_prefix(tmp_path):
    p = tmp_path / "090126-x-prep.md"
    p.write_text("x")
    assert facts.file_date(p) == date(2026, 9, 1)


def test_a_form_banned_by_two_records_is_one_hit_per_line():
    a = facts.parse_record('a | as_of: 2026-01-01 | source: nick "x" | forms: cv=A | banned: "Widget Intern"')
    b = facts.parse_record('b | as_of: 2026-01-01 | source: nick "x" | forms: cv=B | banned: "Widget Intern"')
    hits = facts.judge_text("Pat, Widget Intern\nPat, Widget Intern", {"a": a, "b": b},
                            path="output/x/112026-x-prep.md", audience="prose",
                            as_at=date(2026, 11, 27), owner="Pat Example")
    assert [h["line"] for h in hits] == [1, 2]


def test_a_line_naming_a_related_records_current_form_is_documenting_the_fix():
    title = facts.parse_record('role_title.acme | as_of: 2026-01-01 | source: nick "x" | '
                               'forms: cv=Chief of Widgets | banned: "Widget Intern"')
    boss = facts.parse_record('reports_to.acme | as_of: 2026-01-01 | source: nick "x" | '
                              'forms: cv=reporting to the CEO; abbr=CWO')
    other = facts.parse_record('reports_to.zeta | as_of: 2026-01-01 | source: nick "x" | '
                               'forms: cv=Zeta Boss')
    recs = {r.key: r for r in (title, boss, other)}
    kw = dict(path="output/x/112026-x-prep.md", audience="prose", as_at=date(2026, 11, 27),
              owner="Pat Example")
    held = facts.judge_text("| Widget Intern | Say CWO |", recs, **kw)
    assert [h["held"] for h in held] == ["guardrail"]
    counted = facts.judge_text("| Widget Intern | Say Zeta Boss |", recs, **kw)
    assert [h["held"] for h in counted] == [None]


def test_a_current_name_inside_the_stale_form_does_not_excuse_it():
    title = facts.parse_record('role_title.acme | as_of: 2026-11-26 | source: nick "x" | '
                               'forms: cv=Chief of Widgets | supersedes: "Aide to the Chief Widget '
                               'Officer" until 2026-11-26, source nick')
    boss = facts.parse_record('reports_to.acme | as_of: 2026-01-01 | source: nick "x" | '
                              'forms: name=Chief Widget Officer')
    hits = facts.judge_text("Pat was Aide to the Chief Widget Officer.",
                            {r.key: r for r in (title, boss)}, path="output/x/112726-x-prep.md",
                            audience="prose", as_at=date(2026, 11, 27), owner="Pat Example")
    assert [h["held"] for h in hits] == [None]


def test_nightly_short_keeps_counts_and_drops_hit_text(tmp_path):
    root = make_sweep_repo(tmp_path)
    rep = facts.nightly(root, date(2026, 11, 27), short=True)
    art = rep["scopes"]["artifacts"]
    assert art["hits"] == 4 and art["files"] >= 6
    assert all(set(h) == {"path", "line", "key", "kind"} for h in art["first_hits"])
    assert rep["drift"] is True


# ---------------------------------------------------------------- Codex r1 F1 (P0), recount defect

def _two(order):
    # The generic (held) record sorts FIRST when order is True and LAST when False, so the
    # key-sorted iteration in judge_text really meets both orders (Fable F11).
    gk, sk = ("a", "b") if order else ("b", "a")
    gen = facts.parse_record(f'{gk} | as_of: 2026-01-01 | source: nick "x" | forms: cv=A | '
                             'banned: "Widget Lead" generic')
    strict = facts.parse_record(f'{sk} | as_of: 2026-01-01 | source: nick "x" | forms: cv=B | '
                                'banned: "Widget Lead"')
    recs = [gen, strict] if order else [strict, gen]
    return facts.judge_text("Their Widget Lead joined.", {r.key: r for r in recs},
                            path="output/x/x.md", audience="prose", as_at=date(2026, 11, 27),
                            owner="Pat Example")


@pytest.mark.parametrize("order", [True, False])
def test_a_counted_duplicate_wins_over_a_held_one_whatever_the_record_order(order):
    hits = _two(order)
    assert [(h["line"], h["held"]) for h in hits] == [(1, None)]


@pytest.mark.parametrize("word,line", [
    ("no", "I left Acme now, as Widget Intern."),
    ("not", "Pat was nothing short of Widget Intern."),
    ("never", "Pat, Widget Intern, nevertheless shipped."),
    ("was", "Pat was Widget Intern."),
])
def test_guardrail_words_match_whole_words_only(cur, word, line):
    assert len(counted(line, cur)) == 1, word


@pytest.mark.parametrize("line", [
    "No Widget Intern on any CV.", "Pat is not the Widget Intern.",
    'The title was "Widget Intern" before the fix.', "Don't write Widget Intern.",
])
def test_guardrail_words_still_hold_as_whole_words(cur, line):
    assert counted(line, cur) == []


# ---------------------------------------------------------------- Codex r1 F2, F3, F4 (org ruling)

def test_a_closed_record_followed_by_an_unclosed_marker_is_an_error():
    line = ("<!-- fact: k | as_of: 2026-01-01 | source: agent | forms: cv=A --> "
            "<!-- fact: broken")
    recs, errs = facts.scan_text(line, "f.md")
    assert len(errs) == 1 and "does not close" in errs[0]


def test_get_fails_closed_on_a_trailing_unclosed_marker(tmp_path):
    root = make_repo(tmp_path / "repo")
    with open(root / "data/goals.md", "a") as fh:
        fh.write("<!-- fact: k | as_of: 2026-01-01 | source: agent | forms: cv=A --> "
                 "<!-- fact: broken\n")
    r = run(root, "get", "k")
    assert r.returncode == 2 and r.stdout == ""


def test_sweep_fails_closed_when_one_file_of_a_scope_is_unreadable(tmp_path):
    root = make_sweep_repo(tmp_path)
    (tmp_path / "a-dir.md").mkdir()
    with pytest.raises(facts.Unreadable, match="could not be read"):
        facts.sweep(root, date(2026, 11, 27), extra_copies=[str(tmp_path / "a-dir.md")])
    r = run(root, "sweep", "--copy", str(tmp_path / "a-dir.md"))
    assert r.returncode == 2 and r.stdout == ""


def test_sweep_fails_closed_on_a_missing_extra_copy(tmp_path):
    root = make_sweep_repo(tmp_path)
    with pytest.raises(facts.Unreadable, match="could not be read"):
        facts.sweep(root, date(2026, 11, 27), extra_copies=[str(tmp_path / "missing.md")])


def test_a_supersedes_date_for_one_audience_does_not_apply_to_another():
    r = facts.parse_record('k | as_of: 2026-10-01 | source: nick "x" | forms: cv=New | '
                           'supersedes: "Old Title" until cv 2026-09-01, source nick')
    kw = dict(path="output/x/112726-x-prep.md", as_at=date(2026, 11, 27), owner="Pat Example")
    assert facts.judge_text("Pat, Old Title.", {"k": r}, audience="prose", **kw) == []
    assert len(facts.judge_text("Pat, Old Title.", {"k": r}, audience="cv", **kw)) == 1


def test_sweep_fails_closed_on_an_invalid_utf8_scope_file(tmp_path):
    root = make_sweep_repo(tmp_path)
    (root / "output/acme/112026-acme-prep.md").write_bytes(b"Pat \xff\xfe Widget Intern\n")
    with pytest.raises(facts.Unreadable, match="could not be read"):
        facts.sweep(root, date(2026, 11, 27))



# ---------------------------------------------------------------- Fable F3, F4, F7

def test_records_file_under_the_sealed_sibling_is_refused(tmp_path):
    root = make_sweep_repo(tmp_path)
    sealed = tmp_path / "repo-sealed"
    sealed.mkdir()
    rf = sealed / "records.md"
    rf.write_text(TITLE + "\n")
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.load(root, rf)
    for args in (["get", "role_title.acme"], ["validate"], ["sweep"]):
        r = run(root, "--records", str(rf), *args)
        assert r.returncode == 2 and r.stdout == "", args


def test_a_home_symlinked_into_the_sealed_sibling_is_refused(tmp_path):
    root = make_sweep_repo(tmp_path)
    sealed = tmp_path / "repo-sealed"
    sealed.mkdir()
    (sealed / "x.md").write_text(TITLE + "\n")
    os.symlink(sealed / "x.md", root / "data/projects/linked.md")
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.load(root)
    assert run(root, "get", "role_title.acme").returncode == 2


def test_sweep_exits_2_with_no_traceback_on_a_records_conflict(tmp_path):
    root = make_sweep_repo(tmp_path)
    with open(root / "data/profile.md", "a") as fh:
        fh.write('<!-- fact: role_title.acme | as_of: 2026-10-01 | source: nick "other" '
                 '| forms: cv=Different -->\n')     # same rank and date as TITLE: a conflict
    r = run(root, "sweep")
    assert r.returncode == 2 and r.stdout == ""
    assert "Traceback" not in r.stderr and "disagree" in r.stderr


def test_coached_answers_are_judged_once_as_artifacts_not_also_as_copies(tmp_path):
    root = make_sweep_repo(tmp_path)
    rep = facts.sweep(root, date(2026, 11, 27))
    copies = {h["path"] for h in rep["scopes"]["copies"]["hits"]}
    arts = {h["path"] for h in rep["scopes"]["artifacts"]["hits"]}
    assert "coaching/coached-answers/pitch.md" in arts
    assert "coaching/coached-answers/pitch.md" not in copies


# ---------------------------------------------------------------- Fable narrow 5.F1, 5.F2

def test_a_live_coached_answer_with_a_superseded_form_is_drift_whatever_its_file_date(tmp_path):
    root = make_sweep_repo(tmp_path)
    pitch = root / "coaching/coached-answers/pitch.md"
    pitch.write_text("I was Widget Boss there.\n")
    old = 1756684800          # 2025-09-01, before the form's until date
    os.utime(pitch, (old, old))
    rep = facts.sweep(root, date(2026, 11, 27))["scopes"]["artifacts"]
    hits = [h for h in rep["hits"] if h["path"] == "coaching/coached-answers/pitch.md"]
    assert [h["kind"] for h in hits] == ["superseded"]
    # A dated artifact of the same age is still judged at its own date (not a defect).
    assert "output/acme/090126-acme-cheat-sheet.md" not in {h["path"] for h in rep["hits"]}


def make_worktree(tmp_path) -> tuple:
    """A main checkout with a linked worktree, as git lays them out (a .git FILE in the
    worktree pointing at <main>/.git/worktrees/<name>)."""
    main = tmp_path / "repo"
    (main / ".git/worktrees/wt").mkdir(parents=True)
    wt = tmp_path / "wt"
    make_sweep_repo(tmp_path / "wtbuild")
    (tmp_path / "wtbuild/repo").rename(wt)
    (wt / ".git").write_text(f"gitdir: {main}/.git/worktrees/wt\n")
    return main, wt


def test_from_a_worktree_the_main_checkouts_in_repo_sealed_tree_is_refused(tmp_path):
    main, wt = make_worktree(tmp_path)
    sealed = main / "data/project-background"
    sealed.mkdir(parents=True)
    rf = sealed / "records.md"
    rf.write_text(TITLE + "\n")
    assert sealed.resolve() in facts.sealed_roots(wt)
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.load(wt, rf)
    r = run(wt, "--records", str(rf), "get", "role_title.acme")
    assert r.returncode == 2 and r.stdout == ""


def test_a_profile_symlinked_into_a_sealed_tree_is_never_read_for_the_owner_name(tmp_path):
    # Codex follow-up F1 (P0): in --records mode the homes are not loaded, so owner_name()
    # was the only read of data/profile.md, and it had no sealed guard.
    main, wt = make_worktree(tmp_path)
    sealed = main / "data/project-background"
    sealed.mkdir(parents=True)
    (sealed / "profile.md").write_text("# Profile\n\n- **Name:** Sealed Name\n")
    (wt / "data/profile.md").unlink()
    os.symlink(sealed / "profile.md", wt / "data/profile.md")
    rf = tmp_path / "records.md"
    rf.write_text(TITLE + "\n")
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.owner_name(wt)
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.sweep(wt, date(2026, 11, 27), rf)
    r = run(wt, "--records", str(rf), "sweep")
    assert r.returncode == 2 and r.stdout == "" and "Sealed Name" not in r.stderr


# ---------------------------------------------------------------- Fable 7.F1, 7.F2 (org ruling)

def _case_insensitive_fs(p: Path) -> bool:
    probe = p / "CaseProbe"
    probe.write_text("x")
    return (p / "caseprobe").exists()


def test_a_sealed_root_spelled_in_another_case_is_refused(tmp_path):
    if not _case_insensitive_fs(tmp_path):
        pytest.skip("case-variant paths only reach the file on a case-insensitive filesystem")
    root = make_sweep_repo(tmp_path)
    (tmp_path / "repo-sealed").mkdir()
    (tmp_path / "repo-sealed/x.md").write_text("sealed\n")
    variant = tmp_path / "REPO-Sealed" / "x.md"
    assert variant.exists()
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.sweep(root, date(2026, 11, 27), extra_copies=[str(variant)])


def _vault(tmp_path, root, rels):
    vault = tmp_path / "vault"
    (vault / "data/private").mkdir(parents=True)
    (vault / "data/open").mkdir(parents=True)
    (vault / "data/private/x.md").write_text("vault sealed\n")
    (vault / "data/open/y.md").write_text("Pat, fine.\n")
    (root / "tools").mkdir(exist_ok=True)
    (root / "tools/.personal-vault.conf").write_text(f"{vault}\n")
    if rels is not None:
        (root / "tools/.sealed-folders.conf").write_text(rels)
    return vault


def test_a_copy_under_a_vault_sealed_folder_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("PERSONAL_VAULT_ROOT", raising=False)
    root = make_sweep_repo(tmp_path)
    vault = _vault(tmp_path, root, "data/private\n")
    assert (vault / "data/private").resolve() in facts.sealed_roots(root)
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.sweep(root, date(2026, 11, 27), extra_copies=[str(vault / "data/private/x.md")])
    facts.sweep(root, date(2026, 11, 27), extra_copies=[str(vault / "data/open/y.md")])


def test_with_no_sealed_folder_list_the_whole_vault_data_is_sealed(tmp_path, monkeypatch):
    monkeypatch.delenv("PERSONAL_VAULT_ROOT", raising=False)
    root = make_sweep_repo(tmp_path)
    vault = _vault(tmp_path, root, None)
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.sweep(root, date(2026, 11, 27), extra_copies=[str(vault / "data/open/y.md")])


def test_a_malformed_sealed_folder_list_fails_closed_too(tmp_path, monkeypatch):
    monkeypatch.delenv("PERSONAL_VAULT_ROOT", raising=False)
    root = make_sweep_repo(tmp_path)
    vault = _vault(tmp_path, root, "../outside\n")
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.sweep(root, date(2026, 11, 27), extra_copies=[str(vault / "data/open/y.md")])


def test_a_configured_vault_folder_missing_on_disk_fails_closed(tmp_path, monkeypatch):
    # Codex final round F1 (P0): check_sealed_read treats a listed folder that does not exist
    # as "cannot tell which folder is sealed" and seals the vault's whole data/.
    monkeypatch.delenv("PERSONAL_VAULT_ROOT", raising=False)
    root = make_sweep_repo(tmp_path)
    vault = _vault(tmp_path, root, "data/privte\n")     # a typo: no such folder
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.sweep(root, date(2026, 11, 27), extra_copies=[str(vault / "data/open/y.md")])


def test_a_listed_vault_folder_outside_data_stays_sealed_when_another_is_missing(tmp_path, monkeypatch):
    # Fable final 10.F1: the fallback must ADD the vault's data/ to the listed folders, as
    # check_sealed_read does, never replace them.
    monkeypatch.delenv("PERSONAL_VAULT_ROOT", raising=False)
    root = make_sweep_repo(tmp_path)
    vault = _vault(tmp_path, root, "notes/private\ndata/typo\n")
    (vault / "notes/private").mkdir(parents=True)
    (vault / "notes/private/z.md").write_text("vault sealed\n")
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.sweep(root, date(2026, 11, 27), extra_copies=[str(vault / "notes/private/z.md")])


def test_facts_takes_its_sealed_roots_from_the_hook_not_a_copy(tmp_path, monkeypatch):
    # One source of truth (org dc ruling): with no vault configured, the hook fails closed to
    # ../personal/data, and facts.py must too (Fable final 10.F2).
    monkeypatch.delenv("PERSONAL_VAULT_ROOT", raising=False)
    root = make_sweep_repo(tmp_path)
    (tmp_path / "personal/data").mkdir(parents=True)
    (tmp_path / "personal/data/n.md").write_text("vault\n")
    with pytest.raises(facts.Unreadable, match="sealed"):
        facts.sweep(root, date(2026, 11, 27), extra_copies=[str(tmp_path / "personal/data/n.md")])
    assert not hasattr(facts, "_vault_sealed")
