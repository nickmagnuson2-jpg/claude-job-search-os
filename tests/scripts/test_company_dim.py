"""Tests for tools/company_dim.py — the company dimension."""
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import company_dim as cd  # noqa: E402


def write_dim(tmp_path, companies):
    p = tmp_path / "companies.yaml"
    p.write_text(yaml.safe_dump({"schema": 1, "companies": companies}), encoding="utf-8")
    return p


ACME = {"canonical": "Acme", "type": "target"}
REC = {"canonical": "Hiring Partners", "type": "recruiter"}


# --- loading and validation ---

def test_load_returns_all_entries(tmp_path):
    assert len(cd.load(write_dim(tmp_path, [ACME, REC]))) == 2


def test_load_rejects_a_missing_file_with_its_own_message(tmp_path):
    """Asserting the MESSAGE, not just the type: read_text() raises FileNotFoundError
    too, so a type-only assertion cannot tell the explicit guard from its absence."""
    with pytest.raises(FileNotFoundError, match="company dimension not found"):
        cd.load(tmp_path / "nope.yaml")


def test_load_refuses_an_empty_dimension(tmp_path):
    """An empty dimension resolves nothing, so every guard downstream passes
    vacuously. That must be an error, not a quiet success."""
    with pytest.raises(ValueError, match="no companies"):
        cd.load(write_dim(tmp_path, []))


def test_load_rejects_an_unknown_type(tmp_path):
    with pytest.raises(ValueError, match="bad type"):
        cd.load(write_dim(tmp_path, [{"canonical": "Acme", "type": "vendor"}]))


def test_load_rejects_an_entry_missing_required_keys(tmp_path):
    with pytest.raises(ValueError, match="missing canonical/type"):
        cd.load(write_dim(tmp_path, [{"canonical": "Acme"}]))


def test_load_rejects_a_name_claimed_by_two_companies(tmp_path):
    """An alias owned by two entries makes resolution order-dependent and silently
    wrong. This is the annotated-cell false-join failure encoded as a schema rule."""
    entries = [{"canonical": "Acme", "type": "target", "aliases": ["Shared"]},
               {"canonical": "Beta", "type": "target", "aliases": ["Shared"]}]
    with pytest.raises(ValueError, match="claimed by both"):
        cd.load(write_dim(tmp_path, entries))


def test_load_rejects_an_alias_colliding_with_another_canonical(tmp_path):
    entries = [{"canonical": "Acme", "type": "target"},
               {"canonical": "Beta", "type": "target", "aliases": ["acme"]}]
    with pytest.raises(ValueError, match="claimed by both"):
        cd.load(write_dim(tmp_path, entries))


# --- resolution ---

def test_resolve_matches_the_canonical_name(tmp_path):
    assert cd.load(write_dim(tmp_path, [ACME])).canonical("Acme") == "Acme"


def test_resolve_is_case_and_whitespace_insensitive(tmp_path):
    dim = cd.load(write_dim(tmp_path, [ACME]))
    for v in ("acme", "ACME", "  Acme  ", "Acme"):
        assert dim.canonical(v) == "Acme"


def test_resolve_matches_an_alias_to_its_canonical(tmp_path):
    """Models the live case: one company written with and without an internal space
    in two different files."""
    dim = cd.load(write_dim(tmp_path, [{"canonical": "AcmeCorp", "type": "target",
                                        "aliases": ["Acme Corp"]}]))
    assert dim.canonical("Acme Corp") == "AcmeCorp"
    assert dim.canonical("acme corp") == "AcmeCorp"


def test_resolve_returns_none_and_never_guesses(tmp_path):
    """The contract: a missing join beats a wrong one. No fuzzy fallback."""
    dim = cd.load(write_dim(tmp_path, [ACME]))
    for v in ("Acme Inc", "Acme Labs", "Acm", "Beta", "", "   "):
        assert dim.resolve(v) is None, f"{v!r} must not resolve to Acme"


def test_resolve_does_not_strip_legal_suffixes(tmp_path):
    """Suffix-stripping is what let 'LLC' normalise to the empty string elsewhere."""
    dim = cd.load(write_dim(tmp_path, [{"canonical": "Acme Inc", "type": "target"}]))
    assert dim.canonical("Acme Inc") == "Acme Inc"
    assert dim.resolve("Acme") is None


def test_type_of_returns_the_declared_type(tmp_path):
    dim = cd.load(write_dim(tmp_path, [ACME, REC]))
    assert dim.type_of("Acme") == "target"
    assert dim.type_of("Hiring Partners") == "recruiter"
    assert dim.type_of("Unknown Co") is None


def test_of_type_filters_and_sorts(tmp_path):
    entries = [{"canonical": "Zeta", "type": "target"},
               {"canonical": "Acme", "type": "target"}, REC]
    dim = cd.load(write_dim(tmp_path, entries))
    assert dim.of_type("target") == ["Acme", "Zeta"]
    assert dim.of_type("recruiter") == ["Hiring Partners"]
    assert dim.of_type("other") == []


def test_type_is_declared_never_inferred(tmp_path):
    """Regression guard: an auto-classifier flagged six real targets as recruiting
    firms because a contact there had 'recruiter' in their title. The dimension
    records a human ruling; nothing infers type at read time."""
    dim = cd.load(write_dim(tmp_path, [{"canonical": "Acme", "type": "target"}]))
    assert dim.type_of("Acme") == "target"


# --- CLI ---

def test_main_resolve_exits_2_on_unknown(tmp_path, capsys):
    p = write_dim(tmp_path, [ACME])
    assert cd.main(["--path", str(p), "--resolve", "Acme"]) == 0
    assert cd.main(["--path", str(p), "--resolve", "Nope"]) == 2
    assert "UNRESOLVED" in capsys.readouterr().out


def test_main_list_type_prints_members(tmp_path, capsys):
    p = write_dim(tmp_path, [ACME, REC])
    assert cd.main(["--path", str(p), "--list-type", "recruiter"]) == 0
    assert capsys.readouterr().out.strip() == "Hiring Partners"


def test_main_reports_a_bad_dimension_as_exit_1(tmp_path, capsys):
    p = write_dim(tmp_path, [{"canonical": "Acme", "type": "vendor"}])
    assert cd.main(["--path", str(p)]) == 1
    assert "ERROR" in capsys.readouterr().err


def test_main_default_prints_the_count(tmp_path, capsys):
    p = write_dim(tmp_path, [ACME, REC])
    assert cd.main(["--path", str(p)]) == 0
    assert "2 companies" in capsys.readouterr().out


# --- the live guard ---

def test_live_dimension_loads_and_covers_every_company_string():
    """The point of the dimension. Fails the moment a new free-text company appears
    in any of the three data files without being added here."""
    path = REPO / "data" / "companies.yaml"
    if not path.exists():
        pytest.skip("company dimension not present in this tree")
    dim = cd.load(path)
    assert len(dim) > 100
    gaps = cd.unresolved(dim, REPO)
    assert gaps == {"job-pipeline.md": [], "outreach-log.md": [], "networking.md": []}, \
        f"unresolved company strings: {gaps}"


# --- unresolved(): a controlled fixture tree, because the live check has zero gaps
#     and therefore cannot distinguish a working detector from a broken one ---

PIPE_HDR = "| Company | Role | Stage | Date Updated | Next Action | CV Used | Notes | URL |"
LOG_HDR = "| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |"
NET_HDR = "| Name | Company | Role | Context | First | Last | Email |"
SEP = "| --- | --- | --- | --- | --- | --- | --- |"


def fake_repo(tmp_path, pipeline=(), outreach=(), networking=()):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "job-pipeline.md").write_text(
        "\n".join(["## Active Pipeline", "", PIPE_HDR, SEP + " --- |",
                   *[f"| {c} | Role | Applied | 2026-09-01 | do | — | — | — |" for c in pipeline]])
        + "\n", encoding="utf-8")
    (d / "outreach-log.md").write_text(
        "\n".join(["# Outreach Log", "", LOG_HDR, SEP,
                   *[f"| 2026-09-01 | follow-up | email | Casey Doe | {c} | subj | Sent |"
                     for c in outreach]]) + "\n", encoding="utf-8")
    (d / "networking.md").write_text(
        "\n".join(["# Networking", "", NET_HDR, SEP,
                   *[f"| Casey Doe | {c} | Role | ctx | 2026-01-01 | 2026-09-01 | — |"
                     for c in networking]]) + "\n", encoding="utf-8")
    return tmp_path


def test_unresolved_is_empty_when_every_string_is_known(tmp_path):
    dim = cd.load(write_dim(tmp_path, [ACME, REC]))
    repo = fake_repo(tmp_path, pipeline=["Acme"], outreach=["Acme"],
                     networking=["Hiring Partners"])
    assert cd.unresolved(dim, repo) == {"job-pipeline.md": [], "outreach-log.md": [],
                                        "networking.md": []}


def test_unresolved_reports_a_gap_in_each_file_separately(tmp_path):
    dim = cd.load(write_dim(tmp_path, [ACME]))
    repo = fake_repo(tmp_path, pipeline=["Acme", "Ghost One"],
                     outreach=["Ghost Two"], networking=["Ghost Three"])
    gaps = cd.unresolved(dim, repo)
    assert gaps["job-pipeline.md"] == ["Ghost One"]
    assert gaps["outreach-log.md"] == ["Ghost Two"]
    assert gaps["networking.md"] == ["Ghost Three"]


def test_unresolved_resolves_through_aliases(tmp_path):
    dim = cd.load(write_dim(tmp_path, [{"canonical": "AcmeCorp", "type": "target",
                                        "aliases": ["Acme Corp"]}]))
    repo = fake_repo(tmp_path, pipeline=["Acme Corp"], outreach=["AcmeCorp"])
    gaps = cd.unresolved(dim, repo)
    assert gaps["job-pipeline.md"] == []
    assert gaps["outreach-log.md"] == []


def test_unresolved_ignores_placeholder_company_cells(tmp_path):
    """A dash placeholder (em dash or hyphen) is an absent value, not an unknown company."""
    dim = cd.load(write_dim(tmp_path, [ACME]))
    repo = fake_repo(tmp_path, outreach=["—", "-", "Acme"], networking=["—", "-", "Acme"])
    gaps = cd.unresolved(dim, repo)
    assert gaps["outreach-log.md"] == []
    assert gaps["networking.md"] == []


def test_unresolved_skips_the_networking_header_row(tmp_path):
    """The header's second cell is the literal word 'Company'; counting it as an
    unknown company would make the guard permanently red."""
    dim = cd.load(write_dim(tmp_path, [ACME]))
    repo = fake_repo(tmp_path, networking=["Acme"])
    assert "Company" not in cd.unresolved(dim, repo)["networking.md"]


def test_unresolved_deduplicates_repeated_gaps(tmp_path):
    dim = cd.load(write_dim(tmp_path, [ACME]))
    repo = fake_repo(tmp_path, outreach=["Ghost", "Ghost", "Ghost"])
    assert cd.unresolved(dim, repo)["outreach-log.md"] == ["Ghost"]


# --- --check CLI path ---

def test_main_check_exits_0_when_clean(tmp_path, capsys):
    p = write_dim(tmp_path, [ACME])
    repo = fake_repo(tmp_path, pipeline=["Acme"])
    assert cd.main(["--path", str(p), "--repo-root", str(repo), "--check"]) == 0
    out = capsys.readouterr().out
    assert "job-pipeline.md: 0 unresolved" in out
    assert "1 companies" in out or "companies," in out


def test_main_check_exits_2_and_names_the_gap(tmp_path, capsys):
    p = write_dim(tmp_path, [ACME])
    repo = fake_repo(tmp_path, pipeline=["Acme", "Ghost Co"])
    assert cd.main(["--path", str(p), "--repo-root", str(repo), "--check"]) == 2
    out = capsys.readouterr().out
    assert "Ghost Co" in out
    assert "job-pipeline.md: 1 unresolved" in out


def test_main_check_reports_the_review_backlog(tmp_path, capsys):
    entries = [ACME, {"canonical": "Unsure Co", "type": "REVIEW"}]
    p = write_dim(tmp_path, entries)
    repo = fake_repo(tmp_path, pipeline=["Acme"])
    assert cd.main(["--path", str(p), "--repo-root", str(repo), "--check"]) == 0
    assert "1 still REVIEW" in capsys.readouterr().out


# --- Mutation survivors, 2026-10-13 ---

def test_unresolved_reads_only_table_rows_of_the_networking_file(tmp_path):
    """A prose line that happens to hold pipes is not a contact row."""
    dim = cd.load(write_dim(tmp_path, [ACME]))
    repo = fake_repo(tmp_path, networking=["Acme"])
    net = repo / "data" / "networking.md"
    net.write_text(net.read_text(encoding="utf-8")
                   + "\nSee also: one | Ghost Co | two | three | four\n", encoding="utf-8")
    assert cd.unresolved(dim, repo)["networking.md"] == []


def test_main_without_check_does_not_run_the_guard(tmp_path, capsys):
    p = write_dim(tmp_path, [ACME])
    repo = fake_repo(tmp_path, pipeline=["Ghost Co"])
    assert cd.main(["--path", str(p), "--repo-root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "unresolved" not in out and "1 companies loaded" in out


# --- sync: the dimension follows the data files (2026-10-13) -------------------
# The live guard above went red every time any session added a company and stayed red
# until the YAML was edited by hand. The writers now call sync after each write.

import os  # noqa: E402
import subprocess  # noqa: E402

TOOLS = REPO / "tools"


def _entries(path):
    doc = cd.yaml.safe_load(path.read_text(encoding="utf-8"))
    return {e["canonical"]: e for e in doc["companies"]}


def _dim_in(repo, entries):
    p = repo / "data" / "companies.yaml"
    p.write_text(cd.yaml.safe_dump({"schema": 1, "companies": entries}, sort_keys=False),
                 encoding="utf-8")
    return p


def test_sync_adds_a_pipeline_company_as_target_and_any_other_as_review(tmp_path):
    repo = fake_repo(tmp_path, pipeline=["Acme", "Ghost Co"],
                     outreach=["Ghost Co", "Lantern Works"],
                     networking=["Lantern Works", "Acme"])
    p = _dim_in(repo, [ACME])
    added = cd.sync(repo, today="2026-10-13")
    assert [(e["canonical"], e["type"], e["seen_in"]) for e in added] == [
        ("Ghost Co", "target", ["pipeline", "outreach"]),
        ("Lantern Works", "REVIEW", ["outreach", "networking"])]
    got = _entries(p)
    assert got["Ghost Co"]["added_by"] == "sync 2026-10-13"
    assert got["Acme"] == ACME, "an existing entry is not touched"
    assert cd.unresolved(cd.load(p), repo) == {
        "job-pipeline.md": [], "outreach-log.md": [], "networking.md": []}


def test_sync_is_idempotent_and_leaves_a_complete_dimension_byte_identical(tmp_path):
    repo = fake_repo(tmp_path, pipeline=["Acme", "Ghost Co"])
    p = _dim_in(repo, [ACME])
    assert len(cd.sync(repo)) == 1
    after = p.read_bytes()
    assert cd.sync(repo) == []
    assert p.read_bytes() == after
    assert not p.with_suffix(".yaml.tmp").exists()


def test_sync_does_not_add_a_string_an_alias_already_resolves(tmp_path):
    repo = fake_repo(tmp_path, pipeline=["Acme Inc"])
    p = _dim_in(repo, [{**ACME, "aliases": ["Acme Inc"]}])
    assert cd.sync(repo) == [] and len(_entries(p)) == 1


def test_sync_without_a_dimension_file_does_nothing(tmp_path):
    repo = fake_repo(tmp_path, pipeline=["Ghost Co"])
    assert cd.sync(repo) == []
    assert not (repo / "data" / "companies.yaml").exists()


def test_sync_refuses_two_new_strings_that_would_collide_and_writes_nothing(tmp_path):
    repo = fake_repo(tmp_path, pipeline=["Ghost Co"], networking=["GHOST CO"])
    p = _dim_in(repo, [ACME])
    before = p.read_bytes()
    with pytest.raises(ValueError, match="claimed by both"):
        cd.sync(repo)
    assert p.read_bytes() == before


def test_main_sync_reports_what_it_added(tmp_path, capsys):
    repo = fake_repo(tmp_path, pipeline=["Acme", "Ghost Co"])
    p = _dim_in(repo, [ACME])
    assert cd.main(["--path", str(p), "--repo-root", str(repo), "--sync"]) == 0
    out = capsys.readouterr().out
    assert "added 'Ghost Co' as target (seen in pipeline)" in out and "1 added" in out
    assert cd.main(["--path", str(p), "--repo-root", str(repo), "--sync"]) == 0
    assert "0 added" in capsys.readouterr().out


def test_main_sync_reports_a_collision_as_exit_1(tmp_path, capsys):
    repo = fake_repo(tmp_path, pipeline=["Ghost Co"], networking=["GHOST CO"])
    p = _dim_in(repo, [ACME])
    assert cd.main(["--path", str(p), "--repo-root", str(repo), "--sync"]) == 1
    assert "claimed by both" in capsys.readouterr().err


def test_sync_after_write_never_raises_and_says_what_to_run(tmp_path, capsys):
    repo = fake_repo(tmp_path, pipeline=["Ghost Co"], networking=["GHOST CO"])
    _dim_in(repo, [ACME])
    cd.sync_after_write(repo / "data" / "job-pipeline.md")
    err = capsys.readouterr().err
    assert "WARNING: company dimension not updated" in err and "--sync" in err


def test_sync_after_write_names_each_company_it_added(tmp_path, capsys):
    repo = fake_repo(tmp_path, pipeline=["Acme", "Ghost Co"])
    _dim_in(repo, [ACME])
    cd.sync_after_write(repo / "data" / "job-pipeline.md")
    assert "company dimension: added 'Ghost Co' as target" in capsys.readouterr().err
    cd.sync_after_write(repo / "data" / "job-pipeline.md")
    assert capsys.readouterr().err == ""


def _tool(repo, script, *args):
    return subprocess.run([sys.executable, str(TOOLS / script), "--repo-root", str(repo), *args],
                          capture_output=True, text=True)


def test_pipe_write_add_puts_the_new_company_in_the_dimension(tmp_path):
    repo = fake_repo(tmp_path, pipeline=["Acme"])
    p = _dim_in(repo, [ACME])
    r = _tool(repo, "pipe_write.py", "add", "Northwind Systems", "Operations Lead")
    assert r.returncode == 0, r.stderr
    assert _entries(p)["Northwind Systems"]["type"] == "target"
    assert "added 'Northwind Systems' as target" in r.stderr


def test_networking_write_add_puts_the_new_company_in_the_dimension(tmp_path):
    repo = fake_repo(tmp_path, networking=["Acme"])
    net = repo / "data" / "networking.md"
    net.write_text("# Networking\n\n## Contacts\n\n" + NET_HDR + "\n" + SEP + "\n"
                   "| Casey Doe | Acme | Role | peer | 2026-01-01 | 2026-09-01 | — |\n\n"
                   "## Interaction Log\n", encoding="utf-8")
    p = _dim_in(repo, [ACME])
    r = _tool(repo, "networking_write.py", "add", "Jordan Sample", "--company", "Lantern Works")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _entries(p)["Lantern Works"]["type"] == "REVIEW"


def test_a_writer_in_a_tree_with_no_dimension_file_still_writes_quietly(tmp_path):
    repo = fake_repo(tmp_path, pipeline=["Acme"])
    r = _tool(repo, "pipe_write.py", "add", "Northwind Systems", "Operations Lead")
    assert r.returncode == 0 and r.stderr == ""
    assert "Northwind Systems" in (repo / "data" / "job-pipeline.md").read_text(encoding="utf-8")


def test_sync_with_nothing_to_add_does_not_rewrite_a_hand_edited_file(tmp_path):
    """A rewrite would drop the comment: YAML comments do not survive a load and dump."""
    repo = fake_repo(tmp_path, pipeline=["Acme"])
    p = _dim_in(repo, [ACME])
    p.write_text("# reviewed by hand\n" + p.read_text(encoding="utf-8"), encoding="utf-8")
    before = p.read_bytes()
    assert cd.sync(repo) == []
    assert p.read_bytes() == before


@pytest.mark.parametrize("script,args", [
    ("pipe_write.py", ["add", "Northwind Systems", "Operations Lead"]),
    ("networking_write.py", ["add", "Jordan Sample", "--company", "Lantern Works"]),
])
def test_a_writer_without_a_yaml_reader_still_writes_and_says_the_dimension_is_behind(
        tmp_path, script, args):
    repo = fake_repo(tmp_path, pipeline=["Acme"], networking=["Acme"])
    net = repo / "data" / "networking.md"
    net.write_text("# Networking\n\n## Contacts\n\n" + NET_HDR + "\n" + SEP + "\n"
                   "| Casey Doe | Acme | Role | peer | 2026-01-01 | 2026-09-01 | — |\n\n"
                   "## Interaction Log\n", encoding="utf-8")
    _dim_in(repo, [ACME])
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "yaml.py").write_text("raise ImportError('no yaml in this interpreter')\n",
                                    encoding="utf-8")
    r = subprocess.run([sys.executable, str(TOOLS / script), "--repo-root", str(repo), *args],
                       capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(shadow)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNING: company dimension not updated (no yaml in this interpreter)" in r.stderr
    written = (repo / "data" / ("job-pipeline.md" if script == "pipe_write.py"
                                else "networking.md")).read_text(encoding="utf-8")
    assert args[1] in written


def test_sync_holds_an_exclusive_lock_while_it_reads_and_writes(tmp_path, monkeypatch):
    """Review 308: unlocked, two syncs read the same file and one addition was lost."""
    repo = fake_repo(tmp_path, pipeline=["Acme", "Ghost Co"])
    p = _dim_in(repo, [ACME])
    events = []
    real_flock, real_locked = cd.fcntl.flock, cd._sync_locked

    def flock(fh, mode):
        events.append(("lock", mode == cd.fcntl.LOCK_EX, Path(fh.name).name))
        return real_flock(fh, mode)

    def locked(*a, **k):
        events.append(("work",))
        return real_locked(*a, **k)
    monkeypatch.setattr(cd.fcntl, "flock", flock)
    monkeypatch.setattr(cd, "_sync_locked", locked)
    assert len(cd.sync(repo)) == 1
    assert events == [("lock", True, "companies.yaml.lock"), ("work",)]


def test_many_syncs_at_once_add_each_company_exactly_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    repo = fake_repo(tmp_path, pipeline=["Acme", "Ghost Co", "Lantern Works", "Northwind Systems"])
    p = _dim_in(repo, [ACME])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: len(cd.sync(repo)), range(16)))
    assert sorted(results) == [0] * 15 + [3]
    assert sorted(_entries(p)) == ["Acme", "Ghost Co", "Lantern Works", "Northwind Systems"]
