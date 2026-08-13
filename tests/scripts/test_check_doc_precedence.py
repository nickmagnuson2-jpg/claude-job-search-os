"""W2 — content-rules must beat voice-reference, and a checker must prove it.

`X rather than Y` sat in voice-reference's "Patterns to ADD" as corpus-validated while
content-rules B7 banned the same construction by name. A drafting pass with both loaded
took the one framed as validated and shipped a phrase Nick had cut 14 days earlier from
an email to the same recipient. Neither file was wrong alone; nothing compared them.

These fixtures are synthetic and placeholder-worded. The REAL files are gitignored, so
their validation is Tier 2 (`--validate-local`), never CI.
"""
import json

from conftest import FIXTURES_DIR, run_script_raw

W2 = FIXTURES_DIR / "w2"


def check(rules_yaml: str, voice: str, rules_md: str = "conflict-rules.md") -> tuple[int, dict]:
    proc = run_script_raw(
        "check_doc_precedence.py",
        "--content-rules-yaml", str(W2 / rules_yaml),
        "--content-rules-md", str(W2 / rules_md),
        "--voice-reference", str(W2 / voice),
    )
    assert proc.stdout.strip(), f"no stdout; stderr={proc.stderr}"
    return proc.returncode, json.loads(proc.stdout)


def test_historical_regression_is_flagged():
    """B7 bans the construction; voice-reference ADDs it. This is the motivating pair."""
    code, report = check("conflict-rules.yaml", "conflict-voice.md")
    assert code == 1
    assert len(report["conflicts"]) == 1
    conflict = report["conflicts"][0]
    assert conflict["content_rule_id"] == "B7"
    assert conflict["phrase"] == "X rather than Y"
    assert conflict["direction"] == "banned_subsumes_add"


def test_construction_subsumes_a_literal_add():
    """Set equality would miss this: neither string equals the other."""
    code, report = check("conflict-rules.yaml", "literal-add-voice.md")
    assert code == 1
    assert report["conflicts"][0]["direction"] == "banned_subsumes_add"


def test_unmarked_prose_is_never_scanned():
    """"ask what days work rather than sending a Calendly" is ordinary prose.

    The boundary rule in voice-reference explicitly permits it. Registering `rather
    than` as a bare literal would fire on ~10 benign uses and the checker would be
    ignored within a week — which is why it is only ever a construction.
    """
    code, report = check("conflict-rules.yaml", "unmarked-prose-voice.md")
    assert code == 0, report["conflicts"]
    assert report["conflicts"] == []


def test_clean_pair_passes():
    code, report = check("clean-rules.yaml", "clean-voice.md", rules_md="clean-rules.md")
    assert code == 0
    assert report["conflicts"] == []
    assert report["banned_count"] == 1


def test_retired_markers_are_ignored():
    """A RETIRED entry is the resolution, not a live conflict."""
    code, report = check("conflict-rules.yaml", "clean-voice.md")
    assert code == 0, report["conflicts"]


def test_normalization_case_and_trailing_punctuation():
    code, report = check("conflict-rules.yaml", "normalize-voice.md")
    assert code == 1
    assert report["conflicts"][0]["phrase"] == "X rather than Y"


def test_self_contradiction_within_one_rule_is_flagged():
    code, report = check("self-contradiction-rules.yaml", "clean-voice.md")
    assert code == 1
    assert report["conflicts"][0]["kind"] == "content_rules_self_contradiction"
    assert report["conflicts"][0]["content_rule_id"] == "C1"


def test_malformed_marker_is_a_parse_error_not_a_silent_skip():
    code, report = check("clean-rules.yaml", "malformed-voice.md", rules_md="clean-rules.md")
    assert code == 1
    assert report["status"] == "PARSE_ERROR"
    assert "scope=" in report["error"]


def test_missing_gitignored_files_skip_loudly(tmp_path):
    proc = run_script_raw(
        "check_doc_precedence.py",
        "--content-rules-yaml", str(tmp_path / "absent.yaml"),
        "--content-rules-md", str(tmp_path / "absent.md"),
        "--voice-reference", str(tmp_path / "absent-voice.md"),
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["status"] == "SKIPPED"
    assert "absent" in payload["reason"]


def test_bare_validate_local_parses():
    proc = run_script_raw("check_doc_precedence.py", "--validate-local")
    assert proc.returncode != 2, proc.stderr
    payload = json.loads(proc.stdout)
    assert "status" in payload
