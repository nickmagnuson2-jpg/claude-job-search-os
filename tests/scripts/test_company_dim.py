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
    """An em-dash placeholder is an absent value, not an unknown company."""
    dim = cd.load(write_dim(tmp_path, [ACME]))
    repo = fake_repo(tmp_path, outreach=["—", "Acme"], networking=["—", "Acme"])
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
