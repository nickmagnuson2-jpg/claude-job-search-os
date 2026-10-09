"""Tests for tools/records_lookup.py.

These exist because of a specific failure (2026-10-09): "is this firm in my records?" was
answered with an ad-hoc grep whose pattern could not match (an optional-space regex run as a
basic regex), the empty result was reported as an absence, and the name was in the records.
The properties under test are therefore: a query finds its target however either side is
spaced, cased or punctuated; a query can contain regex characters without changing what it
finds; and a negative ALWAYS carries the scope it was computed over, including any core
source that was missing.

All fixtures use invented names. This file is public.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import records_lookup as rl  # noqa: E402

SCRIPT = REPO_ROOT / "tools" / "records_lookup.py"


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / "data" / "people").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "company-notes").mkdir()
    (tmp_path / "data" / "project-background").mkdir()
    (tmp_path / "output" / "northwind").mkdir(parents=True)
    (tmp_path / "output" / "harbor-labs").mkdir()
    (tmp_path / "coaching" / "progress").mkdir(parents=True)
    (tmp_path / "data" / "networking.md").write_text(
        "| Name | Company |\n| Jordan Lee | NorthWind | investor |\n| Sam Ortiz | Harbor Labs |\n",
        encoding="utf-8")
    (tmp_path / "data" / "job-pipeline.md").write_text(
        "| Harbor Labs | Operations Lead | Applied |\n", encoding="utf-8")
    (tmp_path / "data" / "job-todos.md").write_text(
        "| Follow up: Sam Ortiz | Med |\n", encoding="utf-8")
    (tmp_path / "data" / "people" / "jordan-lee.md").write_text(
        "# Jordan Lee\nMet at a panel.\n", encoding="utf-8")
    (tmp_path / "data" / "company-notes" / "harbor-labs.md").write_text(
        "Renée Dubois runs deployments.\n", encoding="utf-8")
    (tmp_path / "data" / "project-background" / "sealed.md").write_text(
        "Quillfeather is discussed here.\n", encoding="utf-8")
    (tmp_path / "output" / "northwind" / "northwind.md").write_text(
        "# North-Wind dossier\n", encoding="utf-8")
    (tmp_path / "output" / "northwind" / "010126-prep.md").write_text(
        "Prep mentions Pemberton Vale once.\n", encoding="utf-8")
    (tmp_path / "coaching" / "progress" / "call.md").write_text(
        "Debrief of the Pemberton Vale call.\n", encoding="utf-8")
    return tmp_path


def term(result: dict, index: int = 0) -> dict:
    return result["terms"][index]


# --- normalization: the property the original failure lacked -----------------

def test_spaced_query_finds_joined_word(tmp_path):
    result = rl.search(make_repo(tmp_path), ["north wind"])
    files = term(result)["files_with_hits"]
    assert "data/networking.md" in files


def test_joined_query_finds_hyphenated_text(tmp_path):
    result = rl.search(make_repo(tmp_path), ["northwind"])
    assert "output/northwind/northwind.md" in term(result)["files_with_hits"]


def test_regex_characters_in_the_query_are_inert(tmp_path):
    """The literal 2026-10-09 failure: an optional-space pattern must still find the name."""
    repo = make_repo(tmp_path)
    plain = rl.search(repo, ["northwind"])
    regexy = rl.search(repo, ["north ?wind"])
    assert term(regexy)["normalized"] == "northwind"
    assert term(regexy)["hit_count"] == term(plain)["hit_count"] > 0


def test_case_is_ignored(tmp_path):
    assert term(rl.search(make_repo(tmp_path), ["JORDAN LEE"]))["hit_count"] >= 2


def test_accents_are_folded_both_ways(tmp_path):
    result = rl.search(make_repo(tmp_path), ["Renee Dubois"])
    assert term(result)["files_with_hits"] == ["data/company-notes/harbor-labs.md"]


def test_hit_reports_file_line_and_text(tmp_path):
    hit = term(rl.search(make_repo(tmp_path), ["Sam Ortiz"]))["hits"][0]
    assert hit["file"] == "data/networking.md"
    assert hit["line"] == 3
    assert "Sam Ortiz" in hit["text"]


# --- scope: what was searched, and what was not -------------------------------

def test_output_folder_names_are_searched(tmp_path):
    result = rl.search(make_repo(tmp_path), ["harbor labs"])
    assert term(result)["folders_with_hits"] == ["output/harbor-labs/"]
    assert result["scope"]["output_folders_searched"] == 2


def test_default_scope_excludes_non_dossier_output_files(tmp_path):
    result = rl.search(make_repo(tmp_path), ["Pemberton Vale"])
    assert term(result)["hit_count"] == 0


def test_deep_adds_output_and_coaching_files(tmp_path):
    result = rl.search(make_repo(tmp_path), ["Pemberton Vale"], deep=True)
    assert term(result)["files_with_hits"] == [
        "coaching/progress/call.md", "output/northwind/010126-prep.md"]


def test_sealed_directory_is_never_searched_even_deep(tmp_path):
    result = rl.search(make_repo(tmp_path), ["Quillfeather"], deep=True)
    assert term(result)["hit_count"] == 0


def test_negative_claim_names_its_scope(tmp_path):
    result = rl.search(make_repo(tmp_path), ["Zephyrine"])
    claim = term(result)["citable_claim"]
    assert term(result)["hit_count"] == 0
    assert "searched exactly 6 file(s)" in claim
    assert "nothing else was searched" in claim
    assert "--deep adds" in claim  # a default run says how to widen it
    assert result["scope"]["files_searched"] == 6


def test_deep_negative_does_not_claim_output_was_skipped(tmp_path):
    claim = term(rl.search(make_repo(tmp_path), ["Zephyrine"], deep=True))["citable_claim"]
    assert "--deep adds" not in claim
    assert "nothing else was searched" in claim


def test_missing_core_source_is_reported_not_silent(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "data" / "networking.md").unlink()
    result = rl.search(repo, ["Jordan Lee"])
    assert result["scope"]["missing_core_sources"] == ["data/networking.md"]
    assert "CORE SOURCE MISSING" in term(result)["citable_claim"]


def test_complete_scope_does_not_cry_missing(tmp_path):
    result = rl.search(make_repo(tmp_path), ["Jordan Lee"])
    assert result["scope"]["missing_core_sources"] == []
    assert "CORE SOURCE MISSING" not in term(result)["citable_claim"]


def test_sealed_folder_inside_a_searched_tree_is_skipped(tmp_path):
    """The sealed name can appear under output/ or coaching/, where --deep WOULD reach it."""
    repo = make_repo(tmp_path)
    for parent in ("output", "coaching"):
        sealed = repo / parent / "project-background"
        sealed.mkdir()
        (sealed / "notes.md").write_text("Quillfeather again.\n", encoding="utf-8")
    (repo / "output" / "northwind" / "open.md").write_text("Quillfeather in the open.\n",
                                                           encoding="utf-8")
    result = rl.search(repo, ["Quillfeather"], deep=True)
    assert term(result)["files_with_hits"] == ["output/northwind/open.md"]


def test_sealed_folder_name_is_not_matched_or_counted(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "output" / "project-background").mkdir()
    result = rl.search(repo, ["project background"])
    assert term(result)["hit_count"] == 0
    assert result["scope"]["output_folders_searched"] == 2


def test_loose_file_under_output_is_not_a_folder(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "output" / "zephyrine-notes.md").write_text("unrelated\n", encoding="utf-8")
    result = rl.search(repo, ["zephyrine notes"])
    assert term(result)["hit_count"] == 0
    assert result["scope"]["output_folders_searched"] == 2


def test_no_output_directory_is_survivable(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "networking.md").write_text("| Jordan Lee |\n", encoding="utf-8")
    result = rl.search(tmp_path, ["Jordan Lee"])
    assert term(result)["hit_count"] == 1
    assert result["scope"]["output_folders_searched"] == 0


def test_deep_does_not_double_count_a_file_two_sources_reach(tmp_path):
    """The company dossier is in the default scope AND under output/**; count it once."""
    repo = make_repo(tmp_path)
    shallow = rl.search(repo, ["North-Wind dossier"])
    deep = rl.search(repo, ["North-Wind dossier"], deep=True)
    assert term(shallow)["hit_count"] == 1
    assert term(deep)["hit_count"] == 1
    assert deep["scope"]["files_searched"] == 8


def test_long_line_is_cut_and_marked(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "data" / "inbox.md").write_text("Zephyrine " + "x" * 400 + "\n", encoding="utf-8")
    text = term(rl.search(repo, ["Zephyrine"]))["hits"][0]["text"]
    assert text.endswith("...")
    assert len(text) == rl.EXCERPT_CHARS + 3


def test_short_line_is_not_marked_as_cut(tmp_path):
    text = term(rl.search(make_repo(tmp_path), ["Sam Ortiz"]))["hits"][0]["text"]
    assert not text.endswith("...")


def test_line_exactly_at_the_limit_is_not_cut(tmp_path):
    repo = make_repo(tmp_path)
    line = "Zephyrine" + "x" * (rl.EXCERPT_CHARS - 9)
    (repo / "data" / "inbox.md").write_text(line + "\n", encoding="utf-8")
    assert term(rl.search(repo, ["Zephyrine"]))["hits"][0]["text"] == line


def test_positive_claim_counts_lines_and_files(tmp_path):
    claim = term(rl.search(make_repo(tmp_path), ["Sam Ortiz"]))["citable_claim"]
    assert claim.startswith('"samortiz" matches 2 line(s) in 2 file(s) and 0 output folder name(s)')
    assert "no match for" not in claim


def test_negative_claim_does_not_read_as_a_hit(tmp_path):
    claim = term(rl.search(make_repo(tmp_path), ["Zephyrine"]))["citable_claim"]
    assert claim.startswith('no match for "zephyrine"')
    assert "and 2 output folder names" in claim


# --- found by a second-model review, 2026-10-09: input classes the first suite never tried ---

@pytest.mark.parametrize("stored,query", [
    ("Łódź Partners", "Lodz Partners"), ("Lodz Partners", "Łódź Partners"),
    ("Søren Ægir", "Soren Aegir"), ("Soren Aegir", "Søren Ægir"),
    ("Straße Œuvre", "Strasse Oeuvre"), ("Þór Đorđe", "Thor Dorde"),
])
def test_letters_that_do_not_decompose_still_match(tmp_path, stored, query):
    repo = make_repo(tmp_path)
    (repo / "data" / "inbox.md").write_text(stored + "\n", encoding="utf-8")
    assert term(rl.search(repo, [query]))["line_hits"] == 1


def test_folder_hit_is_reported_as_a_folder_not_a_line(tmp_path):
    result = term(rl.search(make_repo(tmp_path), ["harbor labs"]))
    assert result["folder_hits"] == 1
    assert result["line_hits"] == 2
    assert result["hit_count"] == 3
    folder = [h for h in result["hits"] if h["kind"] == "folder"]
    assert folder == [{"kind": "folder", "file": "output/harbor-labs/", "line": 0,
                       "text": "output/harbor-labs/"}]
    assert result["citable_claim"].startswith(
        '"harborlabs" matches 2 line(s) in 2 file(s) and 1 output folder name(s)')
    assert "output/harbor-labs/" not in result["files_with_hits"]


def test_claim_lists_what_was_searched_and_makes_no_claim_beyond_it(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "data" / "misc.md").write_text("Zephyrine lives here, outside every source.\n",
                                           encoding="utf-8")
    result = term(rl.search(repo, ["Zephyrine"]))
    assert result["hit_count"] == 0
    assert "roster (1), pipeline (1), todos (1), inbox (0)" in result["citable_claim"]
    assert "nothing else was searched" in result["citable_claim"]
    assert "not searched" not in result["citable_claim"]


def test_symlink_into_the_sealed_folder_is_not_followed(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "output" / "northwind" / "alias.md").symlink_to(
        repo / "data" / "project-background" / "sealed.md")
    result = rl.search(repo, ["Quillfeather"], deep=True)
    assert term(result)["hit_count"] == 0


def test_same_file_through_a_symlink_is_counted_once(tmp_path):
    repo = make_repo(tmp_path)
    before = rl.search(repo, ["Met at a panel"])
    (repo / "data" / "people" / "alias.md").symlink_to(repo / "data" / "people" / "jordan-lee.md")
    after = rl.search(repo, ["Met at a panel"])
    assert term(before)["hit_count"] == term(after)["hit_count"] == 1
    assert after["scope"]["files_searched"] == before["scope"]["files_searched"]


def test_repeated_query_is_one_query(tmp_path):
    result = rl.search(make_repo(tmp_path), ["Sam Ortiz", "sam-ortiz", "SAM ORTIZ"])
    assert len(result["terms"]) == 1
    assert term(result)["hit_count"] == 2
    assert len(term(result)["hits"]) == 2


# --- second review round, 2026-10-09 -------------------------------------------

@pytest.mark.parametrize("stored,query", [
    ("Ƶygmunt Ħal", "Zygmunt Hal"), ("Zygmunt Hal", "Ƶygmunt Ħal"), ("Ŧomas Ɨvan", "Tomas Ivan"),
])
def test_stroked_letters_fold_by_rule_not_by_list(tmp_path, stored, query):
    repo = make_repo(tmp_path)
    (repo / "data" / "inbox.md").write_text(stored + "\n", encoding="utf-8")
    assert term(rl.search(repo, [query]))["line_hits"] == 1


def test_non_latin_text_is_left_alone(tmp_path):
    assert rl.normalize("Ωmega Жук") == "ωmegaжук"


@pytest.mark.parametrize("deep", [False, True])
def test_per_source_counts_sum_to_the_total(tmp_path, deep):
    result = rl.search(make_repo(tmp_path), ["Zephyrine"], deep=deep)
    scope = result["scope"]
    assert sum(s["files"] for s in scope["sources"]) == scope["files_searched"]


def test_overlapping_source_reports_only_what_it_added(tmp_path):
    result = rl.search(make_repo(tmp_path), ["Zephyrine"], deep=True)
    by_name = {s["source"]: s for s in result["scope"]["sources"]}
    assert by_name["company dossiers"]["files"] == 1
    assert by_name["all output files"]["matched"] == 2
    assert by_name["all output files"]["files"] == 1  # the dossier was already counted
    assert "all output files (1)" in term(result)["citable_claim"]


def test_hard_link_to_a_searched_file_is_counted_once(tmp_path):
    import os
    repo = make_repo(tmp_path)
    os.link(repo / "data" / "people" / "jordan-lee.md", repo / "data" / "people" / "twin.md")
    result = rl.search(repo, ["Met at a panel"])
    assert term(result)["hit_count"] == 1
    assert result["scope"]["files_searched"] == 6


def test_symlink_pointing_outside_the_repo_is_not_followed(tmp_path):
    repo = make_repo(tmp_path / "repo")
    outside = tmp_path / "elsewhere.md"
    outside.write_text("Zephyrine is outside the repository.\n", encoding="utf-8")
    (repo / "data" / "people" / "leak.md").symlink_to(outside)
    result = rl.search(repo, ["Zephyrine"])
    assert term(result)["hit_count"] == 0
    assert result["scope"]["files_searched"] == 6


# --- guards against a query that cannot mean anything -------------------------

@pytest.mark.parametrize("bad", ["", "  ", "?", "a.", "x-y"])
def test_too_short_term_is_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        rl.search(make_repo(tmp_path), [bad])


def test_three_character_term_is_accepted(tmp_path):
    assert term(rl.search(make_repo(tmp_path), ["sam"]))["hit_count"] >= 1


# --- multiple terms and truncation ---------------------------------------------

def test_terms_are_reported_separately(tmp_path):
    result = rl.search(make_repo(tmp_path), ["Jordan Lee", "Zephyrine"])
    assert [t["hit_count"] > 0 for t in result["terms"]] == [True, False]


def test_hit_count_is_the_full_count_when_truncated(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "data" / "inbox.md").write_text("Harbor Labs note\n" * 9, encoding="utf-8")
    result = rl.search(repo, ["harbor labs"], max_hits=3)
    assert len(term(result)["hits"]) == 3
    assert term(result)["hit_count"] >= 9
    assert term(result)["hits_truncated"] is True


def test_not_truncated_flag_when_under_the_cap(tmp_path):
    assert term(rl.search(make_repo(tmp_path), ["Jordan Lee"]))["hits_truncated"] is False


# --- the command line ---------------------------------------------------------

def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--repo-root", str(repo), *args],
                          capture_output=True, text=True)


def test_cli_exit_0_on_hit(tmp_path):
    proc = run_cli(make_repo(tmp_path), "north ?wind")
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["terms"][0]["hit_count"] > 0


def test_cli_exit_1_on_no_hit(tmp_path):
    proc = run_cli(make_repo(tmp_path), "Zephyrine")
    assert proc.returncode == 1
    assert "no match for" in json.loads(proc.stdout)["terms"][0]["citable_claim"]


def test_cli_exit_2_on_unusable_term(tmp_path):
    proc = run_cli(make_repo(tmp_path), "?")
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["status"] == "error"


def test_cli_deep_flag_reaches_search(tmp_path):
    proc = run_cli(make_repo(tmp_path), "Pemberton Vale", "--deep")
    assert proc.returncode == 0
