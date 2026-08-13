"""Tests for context_file_audit.py — the deterministic half of /trim-context-file.

Why this suite is weighted the way it is: the skill that consumes this script
RELOCATES SECTIONS OUT OF CLAUDE.md, the file that holds every hard rule in the
repo. It was disabled because its safety checks FAILED OPEN SILENTLY — a flag that
did not exist produced an empty artifact, the verification loop iterated that empty
set, found nothing missing, and reported PASS. The governing rule from the skill's
safety gate: "a guard with no failing mode is the same bug wearing a safety vest."

So the load-bearing tests here are the REFUSALS (class ExitCodeContract /
TestFailingModes). A suite that only proves the happy paths is the same bug again.

Everything goes through the real CLI as a subprocess: exit codes are what the skill
gates on, so internal function returns would test the wrong surface.

Exit-code contract under test:
    0 success | 1 not a file | 2 argparse usage | 3 unknown --require capability
    4 --rules found zero normative lines | 5 --emit-blocks would emit zero blocks
    6 --emit-blocks target directory is unsafe to write
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT, TOOLS_DIR

# CFA_SCRIPT lets a mutation harness point this suite at a scratch COPY of the script,
# so mutants can be run without ever writing to the real tools/ file. Defaults to the
# real script; nothing in normal use sets it.
SCRIPT = Path(os.environ.get("CFA_SCRIPT") or (TOOLS_DIR / "context_file_audit.py"))


def _run(*args):
    """Invoke the real CLI. Returns the CompletedProcess (exit code is the contract)."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        cwd=str(REPO_ROOT),
    )


def _write(path: Path, content: str) -> Path:
    """Write bytes-exactly (no dedent, no normalization — verbatim matters here)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Fixtures — built here, never read from the repo's live CLAUDE.md (it changes).
# --------------------------------------------------------------------------

NORMAL = (
    "# Title\n"
    "preamble line\n"
    "\n"
    "## Alpha\n"
    "You must never do this.\n"
    "- see `tools/x.py`\n"
    "\n"
    "## Beta\n"
    "| a | b |\n"
    "| - | - |\n"
    "Always required.\n"
)

NO_RULES = (
    "# Notes\n"
    "\n"
    "## Section One\n"
    "Some text about apples.\n"
    "\n"
    "## Section Two\n"
    "- item one\n"
    "- item two\n"
)

FENCED_BACKTICK = (
    "## Real One\n"
    "text\n"
    "\n"
    "```\n"
    "## Fake Heading In Fence\n"
    "more fenced text\n"
    "```\n"
    "\n"
    "## Real Two\n"
    "tail\n"
)

FENCED_TILDE = (
    "## Real One\n"
    "~~~\n"
    "## Fake Tilde Heading\n"
    "~~~\n"
    "## Real Two\n"
    "tail\n"
)

FENCED_LANG = (
    "## Real One\n"
    "```python\n"
    "# not markdown\n"
    "## Fake Python Heading\n"
    "```\n"
    "## Real Two\n"
    "tail\n"
)

UNCLOSED_FENCE = (
    "## Real One\n"
    "intro\n"
    "```\n"
    "## Swallowed Heading\n"
    "still inside the never-closed fence\n"
)

TRICKY_VERBATIM = (
    "## Table Section\n"
    "| col | col2 |   \n"          # trailing whitespace, deliberate
    "|-----|------|\n"
    "| `a` | b\\|c |\n"
    "\t\n"                          # bare tab line
    "````\n"                        # nested-fence outer
    "```\n"
    "## inner heading\n"
    "```\n"
    "````\n"
    "trailing text with spaces   \n"
    "\n"
    "## Second   \n"                # heading with trailing whitespace
    "done\n"
)

DUPLICATE_NAMES = (
    "## Dupe\n"
    "first body\n"
    "## Dupe\n"
    "second body\n"
    "## Dupe\n"
    "third body\n"
)


@pytest.fixture
def normal_file(tmp_path):
    return _write(tmp_path / "normal.md", NORMAL)


@pytest.fixture
def no_rules_file(tmp_path):
    return _write(tmp_path / "no_rules.md", NO_RULES)


@pytest.fixture
def empty_file(tmp_path):
    return _write(tmp_path / "empty.md", "")


# ==========================================================================
# A. FAILING MODES — the point of the suite. A guard that cannot refuse is
#    not a guard.
# ==========================================================================

class TestFailingModes:

    def test_rules_on_file_with_zero_normative_lines_exits_4(self, no_rules_file):
        """The original bug: an empty baseline that makes a later diff vacuously pass."""
        r = _run(no_rules_file, "--rules")
        assert r.returncode == 4, f"expected refusal, got {r.returncode}: {r.stdout!r}"
        assert "zero normative lines" in r.stderr

    def test_rules_empty_does_not_emit_empty_rules_array_with_exit_0(self, no_rules_file):
        """It must not succeed-with-nothing in either output mode."""
        for extra in ([], ["--json"]):
            r = _run(no_rules_file, "--rules", *extra)
            assert r.returncode == 4
            assert r.stdout.strip() == "", f"emitted an artifact while refusing: {r.stdout!r}"

    def test_rules_empty_json_mode_emits_no_parsable_payload(self, no_rules_file):
        r = _run(no_rules_file, "--rules", "--json")
        assert r.returncode == 4
        with pytest.raises(json.JSONDecodeError):
            json.loads(r.stdout)

    def test_emit_blocks_zero_blocks_exits_5(self, empty_file, tmp_path):
        out = tmp_path / "blocks_never_made"
        r = _run(empty_file, "--emit-blocks", out)
        assert r.returncode == 5, f"expected refusal, got {r.returncode}: {r.stdout!r}"
        assert "zero blocks" in r.stderr

    def test_emit_blocks_zero_blocks_does_not_create_the_directory(self, empty_file, tmp_path):
        """An empty dir would make the skill's per-block diff loop iterate nothing."""
        out = tmp_path / "blocks_never_made"
        r = _run(empty_file, "--emit-blocks", out)
        assert r.returncode == 5
        assert not out.exists(), "refusal still created the output directory"

    def test_emit_blocks_zero_blocks_writes_no_manifest(self, empty_file, tmp_path):
        out = tmp_path / "blocks_never_made"
        _run(empty_file, "--emit-blocks", out)
        assert not (out / "manifest.json").exists()

    def test_require_unknown_capability_exits_3_and_names_it(self):
        r = _run("--require", "rules,teleportation")
        assert r.returncode == 3
        assert "teleportation" in r.stderr
        assert "rules" not in r.stderr.replace("teleportation", ""), \
            "supported capability wrongly reported as missing"

    def test_require_all_unknown_capabilities_exits_3(self):
        r = _run("--require", "no-such-thing")
        assert r.returncode == 3
        assert "no-such-thing" in r.stderr

    def test_require_empty_string_exits_3(self):
        """--require ',,' names nothing; a gate that passes on nothing is no gate."""
        r = _run("--require", ",,")
        assert r.returncode == 3
        assert r.stderr.strip() != ""

    def test_nonexistent_path_exits_1(self, tmp_path):
        r = _run(tmp_path / "does_not_exist.md")
        assert r.returncode == 1
        assert "not a file" in r.stderr

    def test_directory_path_exits_1(self, tmp_path):
        r = _run(tmp_path)
        assert r.returncode == 1
        assert "not a file" in r.stderr

    def test_missing_required_path_exits_2(self):
        r = _run()
        assert r.returncode == 2
        assert "path" in r.stderr.lower()

    def test_unknown_flag_exits_2(self, normal_file):
        r = _run(normal_file, "--not-a-flag")
        assert r.returncode == 2

    def test_rules_on_nonexistent_path_exits_1_not_4(self, tmp_path):
        """Path check must precede the emptiness check, or a typo'd path reads as
        'this file has no rules'."""
        r = _run(tmp_path / "ghost.md", "--rules")
        assert r.returncode == 1

    def test_emit_blocks_on_nonexistent_path_exits_1_and_creates_nothing(self, tmp_path):
        out = tmp_path / "out"
        r = _run(tmp_path / "ghost.md", "--emit-blocks", out)
        assert r.returncode == 1
        assert not out.exists()

    def test_negative_top_is_rejected_rather_than_silently_dropping_a_section(self, normal_file):
        """Was a defect: --top -1 sliced sections[:-1] and returned 2 of 3 with exit 0.
        Silent omission from a size audit is the fail-open shape this script exists to
        prevent."""
        r = _run(normal_file, "--top", "-1", "--json")
        assert r.returncode == 2
        assert r.stdout.strip() == ""
        assert "-1" in r.stderr or ">= 0" in r.stderr

    @pytest.mark.parametrize("bad", ["-1", "-99", "abc", "3.5", ""])
    def test_top_rejects_every_non_nonnegative_int(self, normal_file, bad):
        assert _run(normal_file, "--top", bad).returncode == 2

    def test_top_zero_still_means_all_sections(self, normal_file):
        payload = _sections(normal_file, "--top", "0")
        assert len(payload["sections"]) == 3


# ==========================================================================
# B. FENCE-AWARE SPLITTING — the correctness bug that was fixed.
# ==========================================================================

def _sections(path, *extra):
    r = _run(path, "--json", *extra)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class TestFenceAwareSplitting:

    @pytest.mark.parametrize("name,text,fake", [
        ("backtick", FENCED_BACKTICK, "Fake Heading In Fence"),
        ("tilde", FENCED_TILDE, "Fake Tilde Heading"),
        ("language-tagged", FENCED_LANG, "Fake Python Heading"),
    ])
    def test_headings_inside_fences_are_not_sections(self, tmp_path, name, text, fake):
        p = _write(tmp_path / f"{name}.md", text)
        names = [s["section"] for s in _sections(p)["sections"]]
        assert fake not in names, f"{name} fence leaked a phantom section: {names}"
        assert "Real One" in names and "Real Two" in names

    @pytest.mark.parametrize("name,text", [
        ("backtick", FENCED_BACKTICK), ("tilde", FENCED_TILDE), ("language-tagged", FENCED_LANG),
    ])
    def test_fenced_file_has_exactly_the_real_sections(self, tmp_path, name, text):
        p = _write(tmp_path / f"{name}.md", text)
        names = [s["section"] for s in _sections(p)["sections"]]
        assert sorted(names) == ["Real One", "Real Two"], names

    def test_fenced_heading_stays_inside_its_owning_block(self, tmp_path):
        p = _write(tmp_path / "fenced.md", FENCED_BACKTICK)
        out = tmp_path / "blocks"
        r = _run(p, "--emit-blocks", out, "--json")
        assert r.returncode == 0, r.stderr
        manifest = json.loads(r.stdout)
        real_one = next(b for b in manifest["blocks"] if b["section"] == "Real One")
        body = (out / real_one["file"]).read_text(encoding="utf-8")
        assert "## Fake Heading In Fence" in body

    def test_no_fences_splits_exactly_as_before(self, normal_file):
        """Regression guard: fence-awareness must not change plain-markdown splitting."""
        names = [s["section"] for s in _sections(normal_file)["sections"]]
        assert sorted(names) == ["(preamble)", "Alpha", "Beta"]

    def test_inline_backticks_do_not_open_a_fence(self, tmp_path):
        p = _write(tmp_path / "inline.md", "## One\nuse `a` and ``b``\n## Two\nx\n")
        names = [s["section"] for s in _sections(p)["sections"]]
        assert sorted(names) == ["One", "Two"]

    def test_unclosed_fence_does_not_corrupt_the_file(self, tmp_path):
        """Sane expectation (and CommonMark's): an unclosed fence runs to EOF, so the
        remainder is code, not headings. What must NEVER happen either way is content
        loss — byte accounting has to stay exact."""
        p = _write(tmp_path / "unclosed.md", UNCLOSED_FENCE)
        out = tmp_path / "blocks"
        r = _run(p, "--emit-blocks", out, "--json")
        assert r.returncode == 0, r.stderr
        manifest = json.loads(r.stdout)
        joined = b"".join(
            (out / b["file"]).read_bytes()
            for b in sorted(manifest["blocks"], key=lambda b: b["index"]))
        assert joined == p.read_bytes(), "unclosed fence lost or reordered bytes"
        assert sum(b["bytes"] for b in manifest["blocks"]) == manifest["source_bytes"]

    def test_unclosed_fence_swallows_headings_per_commonmark(self, tmp_path):
        p = _write(tmp_path / "unclosed.md", UNCLOSED_FENCE)
        names = [s["section"] for s in _sections(p)["sections"]]
        assert "Swallowed Heading" not in names
        assert names == ["Real One"]

    def test_indented_four_spaces_is_not_a_fence(self, tmp_path):
        """4-space-indented ``` is an indented code block, not a fence opener; if it
        were treated as one it would swallow every following heading."""
        p = _write(tmp_path / "indent.md", "## One\n    ```\nbody\n## Two\ntail\n")
        names = [s["section"] for s in _sections(p)["sections"]]
        assert sorted(names) == ["One", "Two"], names


# ==========================================================================
# C. BYTE-IDENTITY — the integrity guarantee the relocation depends on.
# ==========================================================================

def _emit(path, outdir):
    r = _run(path, "--emit-blocks", outdir, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class TestByteIdentity:

    @pytest.mark.parametrize("name,text", [
        ("normal", NORMAL),
        ("fenced", FENCED_BACKTICK),
        ("tilde", FENCED_TILDE),
        ("lang", FENCED_LANG),
        ("tricky", TRICKY_VERBATIM),
        ("dupes", DUPLICATE_NAMES),
        ("no-trailing-newline", "## A\nline without final newline"),
        ("heading-first", "## Only\nbody\n"),
        ("no-headings", "just a preamble\nand another line\n"),
    ])
    def test_concatenated_blocks_reproduce_source_byte_for_byte(self, tmp_path, name, text):
        p = _write(tmp_path / f"{name}.md", text)
        out = tmp_path / f"{name}-blocks"
        manifest = _emit(p, out)
        joined = b"".join(
            (out / b["file"]).read_bytes()
            for b in sorted(manifest["blocks"], key=lambda b: b["index"]))
        assert joined == p.read_bytes()

    @pytest.mark.parametrize("name,text", [
        ("normal", NORMAL), ("tricky", TRICKY_VERBATIM), ("dupes", DUPLICATE_NAMES),
    ])
    def test_manifest_sha256_matches_emitted_content(self, tmp_path, name, text):
        p = _write(tmp_path / f"{name}.md", text)
        out = tmp_path / f"{name}-blocks"
        manifest = _emit(p, out)
        for b in manifest["blocks"]:
            raw = (out / b["file"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == b["sha256"], b["file"]
            assert len(raw) == b["bytes"], b["file"]

    @pytest.mark.parametrize("name,text", [
        ("normal", NORMAL), ("tricky", TRICKY_VERBATIM), ("fenced", FENCED_BACKTICK),
    ])
    def test_block_byte_sum_equals_source_bytes(self, tmp_path, name, text):
        p = _write(tmp_path / f"{name}.md", text)
        out = tmp_path / f"{name}-blocks"
        manifest = _emit(p, out)
        assert sum(b["bytes"] for b in manifest["blocks"]) == manifest["source_bytes"]
        assert manifest["source_bytes"] == len(p.read_bytes())
        assert manifest["block_count"] == len(manifest["blocks"])

    def test_duplicate_section_names_get_distinct_files_and_content(self, tmp_path):
        p = _write(tmp_path / "dupes.md", DUPLICATE_NAMES)
        out = tmp_path / "blocks"
        manifest = _emit(p, out)
        assert [b["section"] for b in manifest["blocks"]] == ["Dupe", "Dupe", "Dupe"]
        files = [b["file"] for b in manifest["blocks"]]
        assert len(set(files)) == 3, f"filename collision would silently drop a section: {files}"
        bodies = [(out / f).read_text(encoding="utf-8") for f in files]
        assert len(set(bodies)) == 3, "distinct sections collapsed to identical content"
        assert "first body" in bodies[0] and "second body" in bodies[1] and "third body" in bodies[2]

    def test_tricky_markdown_survives_verbatim(self, tmp_path):
        """Tables, nested fences and trailing whitespace must not be reformatted."""
        p = _write(tmp_path / "tricky.md", TRICKY_VERBATIM)
        out = tmp_path / "blocks"
        manifest = _emit(p, out)
        joined = "".join(
            (out / b["file"]).read_text(encoding="utf-8")
            for b in sorted(manifest["blocks"], key=lambda b: b["index"]))
        assert joined == TRICKY_VERBATIM
        assert "| col | col2 |   \n" in joined, "trailing whitespace was stripped"
        assert "\t\n" in joined, "bare tab line was normalized"
        assert "````\n```\n## inner heading\n```\n````\n" in joined

    def test_unicode_content_byte_accounting(self, tmp_path):
        """bytes, not chars — a multibyte section must not under-report."""
        text = "## Café — naïve\n中文 content ✅\n## Second\nplain\n"
        p = _write(tmp_path / "uni.md", text)
        out = tmp_path / "blocks"
        manifest = _emit(p, out)
        joined = b"".join(
            (out / b["file"]).read_bytes()
            for b in sorted(manifest["blocks"], key=lambda b: b["index"]))
        assert joined == p.read_bytes()
        assert manifest["source_bytes"] == len(text.encode("utf-8"))

    def test_manifest_start_lines_are_1_based_and_ordered(self, tmp_path):
        p = _write(tmp_path / "normal.md", NORMAL)
        out = tmp_path / "blocks"
        manifest = _emit(p, out)
        starts = [b["start_line"] for b in manifest["blocks"]]
        assert starts[0] == 1
        assert starts == sorted(starts)
        src_lines = NORMAL.splitlines()
        for b in manifest["blocks"]:
            emitted = (out / b["file"]).read_text(encoding="utf-8").splitlines()
            expected = src_lines[b["start_line"] - 1: b["start_line"] - 1 + b["line_count"]]
            assert emitted == expected

    def test_emit_blocks_human_output_lists_every_block(self, tmp_path):
        p = _write(tmp_path / "normal.md", NORMAL)
        out = tmp_path / "blocks"
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 0, r.stderr
        assert "3 blocks" in r.stdout
        assert "Alpha" in r.stdout and "Beta" in r.stdout


# ==========================================================================
# D. BACKWARD COMPATIBILITY — existing callers must not break.
# ==========================================================================

PRE_EXISTING_TOP_KEYS = {"file", "total_lines", "total_bytes", "sections"}
PRE_EXISTING_SECTION_KEYS = {
    "section", "lines", "bytes", "share", "rule_density", "lookup_density", "suggestion"}


class TestBackwardCompatibility:

    def test_json_keeps_pre_existing_top_level_keys(self, normal_file):
        payload = _sections(normal_file)
        assert PRE_EXISTING_TOP_KEYS <= set(payload)
        assert payload["file"] == str(normal_file)
        assert payload["total_lines"] == len(NORMAL.splitlines())
        assert payload["total_bytes"] == len(NORMAL.encode("utf-8"))

    def test_json_keeps_pre_existing_section_keys(self, normal_file):
        payload = _sections(normal_file)
        assert payload["sections"]
        for s in payload["sections"]:
            assert PRE_EXISTING_SECTION_KEYS <= set(s), s

    def test_sections_sorted_by_bytes_descending(self, normal_file):
        payload = _sections(normal_file)
        sizes = [s["bytes"] for s in payload["sections"]]
        assert sizes == sorted(sizes, reverse=True)

    def test_top_limits_section_count(self, normal_file):
        payload = _sections(normal_file, "--top", "2")
        assert len(payload["sections"]) == 2
        full = _sections(normal_file)["sections"]
        assert payload["sections"] == full[:2]

    def test_top_larger_than_section_count_is_harmless(self, normal_file):
        payload = _sections(normal_file, "--top", "99")
        assert len(payload["sections"]) == 3

    def test_human_readable_table_renders(self, normal_file):
        r = _run(normal_file)
        assert r.returncode == 0, r.stderr
        assert "loaded every session" in r.stdout
        assert "section / suggestion" in r.stdout
        assert "Alpha" in r.stdout and "Beta" in r.stdout
        assert "->" in r.stdout

    def test_human_readable_top_renders(self, normal_file):
        r = _run(normal_file, "--top", "1")
        assert r.returncode == 0, r.stderr
        assert r.stdout.count("->") == 1

    def test_densities_are_fractions(self, normal_file):
        for s in _sections(normal_file)["sections"]:
            assert 0.0 <= s["rule_density"] <= 1.0
            assert 0.0 <= s["lookup_density"] <= 1.0
            assert 0.0 <= s["share"] <= 1.0

    def test_rules_json_shape(self, normal_file):
        r = _run(normal_file, "--rules", "--json")
        assert r.returncode == 0, r.stderr
        payload = json.loads(r.stdout)
        assert payload["file"] == str(normal_file)
        assert payload["rule_count"] == len(payload["rules"])
        assert payload["rule_count"] > 0
        lines = [x["line"] for x in payload["rules"]]
        assert lines == sorted(lines)
        src = NORMAL.splitlines()
        for x in payload["rules"]:
            assert src[x["line"] - 1] == x["text"], "rule line number or text drifted"

    def test_rules_human_output(self, normal_file):
        r = _run(normal_file, "--rules")
        assert r.returncode == 0, r.stderr
        assert "normative lines" in r.stdout


# ==========================================================================
# H. RULE-DETECTION COVERAGE — assert by CONTENT, never by count.
#
# Why this section exists: two mutants survived an earlier suite — one that made the
# detector skip the preamble, one that dropped the marker word `must`. Every --rules
# test at the time asserted only INTERNAL consistency (rule_count == len(rules), line
# numbers ascending, text matches the source line), so a detector that silently
# narrowed still looked perfectly self-consistent.
#
# `--rules` is a SAMPLE, not a cover, and before/after run the same detector, so a
# systematic omission cancels on both sides of a diff and comes back empty. Byte
# conservation is the load-bearing guarantee; the rules diff is corroborating evidence.
# These tests are what stop that sample from silently shrinking.
#
# Construction rule: every keyword below is the SOLE marker on its line and its line is
# NOT bolded-lead, so deleting any single word from RULE_MARKERS kills exactly one test.
# ==========================================================================

# Preamble content — before the first '## '. A structural-only rule, so a mutant that
# skips the preamble dies here regardless of what happens to the keyword list.
COVERAGE_PREAMBLE_RULE = "- **Sealed vault.** Contents stay on this machine."

# One line per keyword; the keyword is the only marker present.
COVERAGE_KEYWORD_LINES = {
    "never": "Secrets are never checked into the repository.",
    "always": "The nightly job always writes to the private mirror.",
    "must": "Reviewers must sign off on the migration.",
    "do not": "Do not paste customer data into the prompt.",
    "don't": "Don't reuse an expired token.",
    "required": "A signed agreement is required for external contributors.",
    "mandatory": "The grey-area pass is mandatory at phase boundaries.",
    "forbidden": "Sharing the seed corpus externally is forbidden.",
    "hard rule": "This is a hard rule for every session.",
    "before any": "Verify the profile exists before any generative step.",
    "stop and": "If the file is missing, stop and tell the user.",
    "blocking": "Treat a blocking issue as higher priority.",
}

# Bolded-lead items with NO keyword at all — reachable only structurally. Modeled on the
# real em-dash hard rule, which is exactly the shape the keyword list cannot see.
COVERAGE_STRUCTURAL_LINES = [
    "- **No em dashes** in any output Nick will send.",
    "* **Star bullet.** Body text for the star form.",
    "+ **Plus bullet.** Body text for the plus form.",
    "1. **Create a dedicated file** with frontmatter.",
    "2) **Paren-ordered item.** Body text.",
]

# Lines that carry BOTH signals.
COVERAGE_BOTH_LINES = ["- **Never do this.** It breaks the pipeline."]

# Ordinary prose that must NOT be swept in. Widening until everything matches would make
# the skill unusable, so the boundary is pinned from both sides.
COVERAGE_NON_RULES = [
    "The audit table sorts sections by size.",
    "- a plain bullet with no bold lead",
    "- see the **appendix** for details",
    "1. a numbered step with no bold lead",
    "Bytes are the honest unit for this measurement.",
    "**A bold line that is not a list item.**",
]


def _coverage_fixture() -> str:
    parts = ["# Title", COVERAGE_PREAMBLE_RULE, "Ordinary preamble prose about the file.", ""]
    parts += ["## Guidance", ""]
    parts += list(COVERAGE_KEYWORD_LINES.values())
    parts += [""]
    parts += ["## Shapes", ""]
    parts += COVERAGE_STRUCTURAL_LINES + COVERAGE_BOTH_LINES
    parts += [""]
    parts += ["## Prose", ""]
    parts += COVERAGE_NON_RULES
    return "\n".join(parts) + "\n"


COVERAGE_TEXT = _coverage_fixture()


@pytest.fixture
def coverage_file(tmp_path):
    return _write(tmp_path / "coverage.md", COVERAGE_TEXT)


def _captured(path):
    r = _run(path, "--rules", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _captured_text(path) -> set:
    return {x["text"] for x in _captured(path)["rules"]}


class TestRuleDetectionCoverage:

    @pytest.mark.parametrize("keyword", sorted(COVERAGE_KEYWORD_LINES))
    def test_each_keyword_is_captured_by_content(self, coverage_file, keyword):
        """Kills any mutant that deletes a single word from RULE_MARKERS — including
        M12 (`must`), which is an EQUIVALENT mutant on the live CLAUDE.md (verified:
        removing `must` leaves that file's output byte-identical, because all 6 affected
        lines are redundantly covered by other signals). Only a fixture where the keyword
        is the sole marker can kill it."""
        line = COVERAGE_KEYWORD_LINES[keyword]
        assert line in _captured_text(coverage_file), \
            f"keyword {keyword!r} no longer detected; line dropped: {line!r}"

    def test_normative_line_in_the_preamble_is_captured(self, coverage_file):
        """Kills M11 (detector skips the preamble). Structural-only, so it dies on the
        preamble bug even if the keyword list is intact."""
        assert COVERAGE_PREAMBLE_RULE in _captured_text(coverage_file)

    def test_preamble_rule_is_attributed_to_the_preamble_section(self, coverage_file):
        entry = next(x for x in _captured(coverage_file)["rules"]
                     if x["text"] == COVERAGE_PREAMBLE_RULE)
        assert entry["section"] == "(preamble)"
        assert entry["line"] == COVERAGE_TEXT.splitlines().index(COVERAGE_PREAMBLE_RULE) + 1

    @pytest.mark.parametrize("line", COVERAGE_STRUCTURAL_LINES)
    def test_bolded_lead_item_with_no_keyword_is_captured(self, coverage_file, line):
        """The whole reason structural detection exists: this repo's hard rules are
        written as '- **Rule name.** body', and several carry no keyword at all."""
        assert line in _captured_text(coverage_file), \
            f"structural rule not detected: {line!r}"

    @pytest.mark.parametrize("line", COVERAGE_NON_RULES)
    def test_ordinary_prose_is_not_swept_in(self, coverage_file, line):
        """The other side of the boundary. A detector that matches everything is as
        useless as one that matches nothing."""
        assert line not in _captured_text(coverage_file), \
            f"non-normative line wrongly counted as a rule: {line!r}"

    def test_captured_set_is_exactly_the_expected_lines(self, coverage_file):
        """Whole-set equality: catches both silent narrowing and silent widening in one
        assertion, including anything the per-line tests forgot."""
        expected = ({COVERAGE_PREAMBLE_RULE}
                    | set(COVERAGE_KEYWORD_LINES.values())
                    | set(COVERAGE_STRUCTURAL_LINES)
                    | set(COVERAGE_BOTH_LINES))
        got = _captured_text(coverage_file)
        # headings are part of their own section and may legitimately match
        got = {t for t in got if not t.startswith("## ")}
        assert got == expected, (f"missing: {sorted(expected - got)}\n"
                                 f"unexpected: {sorted(got - expected)}")

    def test_rule_count_matches_and_no_line_is_double_counted(self, coverage_file):
        payload = _captured(coverage_file)
        assert payload["rule_count"] == len(payload["rules"])
        lines = [x["line"] for x in payload["rules"]]
        assert len(lines) == len(set(lines)), "a line was recorded twice"

    def test_human_output_also_contains_the_rule_text(self, coverage_file):
        """The non-JSON path must not silently drop what --json reports."""
        r = _run(coverage_file, "--rules")
        assert r.returncode == 0
        for line in [COVERAGE_PREAMBLE_RULE, COVERAGE_KEYWORD_LINES["must"],
                     COVERAGE_STRUCTURAL_LINES[0]]:
            assert line in r.stdout


class TestRuleSignalProvenance:

    def test_keyword_only_line_reports_keyword(self, coverage_file):
        entry = next(x for x in _captured(coverage_file)["rules"]
                     if x["text"] == COVERAGE_KEYWORD_LINES["must"])
        assert entry["signals"] == ["keyword"]

    @pytest.mark.parametrize("line", COVERAGE_STRUCTURAL_LINES)
    def test_structural_only_line_reports_structural(self, coverage_file, line):
        entry = next(x for x in _captured(coverage_file)["rules"] if x["text"] == line)
        assert entry["signals"] == ["structural"], \
            f"{line!r} should be reachable only structurally"

    def test_line_with_both_signals_reports_both_once(self, coverage_file):
        both = COVERAGE_BOTH_LINES[0]
        matches = [x for x in _captured(coverage_file)["rules"] if x["text"] == both]
        assert len(matches) == 1, "a dual-signal line must be recorded once, not twice"
        assert matches[0]["signals"] == ["keyword", "structural"]

    def test_every_rule_carries_a_non_empty_signal_list(self, coverage_file):
        for x in _captured(coverage_file)["rules"]:
            assert x["signals"], x
            assert set(x["signals"]) <= {"keyword", "structural"}, x

    def test_structural_signal_is_not_vacuously_universal(self, coverage_file):
        """If BOLD_LEAD_ITEM widened to match any line, every entry would carry
        'structural' and the signal would stop being evidence of anything."""
        sigs = [x["signals"] for x in _captured(coverage_file)["rules"]]
        assert any(s == ["keyword"] for s in sigs)
        assert any(s == ["structural"] for s in sigs)


class TestRuleCoverageOnLiveClaudeMd:

    @pytest.mark.skipif(not (REPO_ROOT / "CLAUDE.md").is_file(), reason="no CLAUDE.md")
    def test_em_dash_hard_rule_is_captured(self):
        """A named hard rule from the real file. The em-dash rule is the right probe: it
        carries no RULE_MARKERS keyword, so it is reachable ONLY structurally — if
        structural detection regresses, this is the rule that silently disappears from
        the baseline. Matched on a distinctive substring so ordinary editing of the rule
        body does not break the test."""
        payload = _captured(REPO_ROOT / "CLAUDE.md")
        hits = [x for x in payload["rules"] if "em dash" in x["text"].lower()]
        assert hits, "the em-dash hard rule is not in the --rules baseline"
        assert any("structural" in x["signals"] for x in hits)

    @pytest.mark.skipif(not (REPO_ROOT / "CLAUDE.md").is_file(), reason="no CLAUDE.md")
    def test_live_baseline_uses_both_signal_kinds(self):
        """Guards against a regression that quietly reduces the live file to one signal
        — the shape the surviving mutants had."""
        payload = _captured(REPO_ROOT / "CLAUDE.md")
        kinds = {tuple(x["signals"]) for x in payload["rules"]}
        assert ("keyword",) in kinds and ("structural",) in kinds
        assert payload["rule_count"] >= 40, \
            f"live coverage collapsed to {payload['rule_count']} rules"


# ==========================================================================
# I. UNCLOSED-FENCE WARNING (advisory; never changes an exit code)
# ==========================================================================

UNCLOSED_WITH_RULE = (
    "## Real One\n"
    "Secrets are never checked into the repository.\n"
    "```\n"
    "## Swallowed Heading\n"
    "still inside the never-closed fence\n"
)
CLOSED_CONTROL = (
    "## Real One\n"
    "Secrets are never checked into the repository.\n"
    "```\n"
    "## Fenced Heading\n"
    "```\n"
    "## Real Two\n"
    "tail\n"
)


class TestUnclosedFenceWarning:

    @pytest.mark.parametrize("mode", ["audit", "rules", "emit-blocks"])
    def test_warning_fires_on_every_path_without_changing_the_exit_code(self, tmp_path, mode):
        p = _write(tmp_path / "unclosed.md", UNCLOSED_WITH_RULE)
        args = {"audit": ["--json"], "rules": ["--rules", "--json"],
                "emit-blocks": ["--emit-blocks", str(tmp_path / f"b-{mode}"), "--json"]}[mode]
        r = _run(p, *args)
        assert r.returncode == 0, r.stderr
        assert "unclosed code fence" in r.stderr, f"no warning on the {mode} path"
        assert json.loads(r.stdout), "warning suppressed the real output"

    @pytest.mark.parametrize("mode", ["audit", "rules", "emit-blocks"])
    def test_closed_fence_control_does_not_warn(self, tmp_path, mode):
        p = _write(tmp_path / "closed.md", CLOSED_CONTROL)
        args = {"audit": ["--json"], "rules": ["--rules", "--json"],
                "emit-blocks": ["--emit-blocks", str(tmp_path / f"c-{mode}"), "--json"]}[mode]
        r = _run(p, *args)
        assert r.returncode == 0, r.stderr
        assert "unclosed code fence" not in r.stderr

    def test_byte_identity_still_holds_under_the_warning(self, tmp_path):
        """The warning is advisory. The integrity guarantee must be unaffected."""
        p = _write(tmp_path / "unclosed.md", UNCLOSED_WITH_RULE)
        out = tmp_path / "blocks"
        manifest = _emit(p, out)
        joined = b"".join(
            (out / b["file"]).read_bytes()
            for b in sorted(manifest["blocks"], key=lambda b: b["index"]))
        assert joined == p.read_bytes()
        assert sum(b["bytes"] for b in manifest["blocks"]) == manifest["source_bytes"]

    def test_warning_does_not_mask_a_refusal(self, tmp_path, no_rules_file):
        """An unclosed fence in a file with no rules must still exit 4, not 0."""
        p = _write(tmp_path / "no_rules_unclosed.md", "## Section One\napples\n```\n## Fake\n")
        r = _run(p, "--rules")
        assert r.returncode == 4
        assert "unclosed code fence" in r.stderr


# ==========================================================================
# J. --expect-blocks (exit 7)
# ==========================================================================

class TestExpectBlocks:

    def test_correct_count_succeeds(self, tmp_path):
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        r = _run(p, "--emit-blocks", out, "--expect-blocks", "2", "--json")
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout)["block_count"] == 2

    @pytest.mark.parametrize("n", ["1", "3", "0", "99"])
    def test_mismatch_exits_7_and_creates_no_directory(self, tmp_path, n):
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        r = _run(p, "--emit-blocks", out, "--expect-blocks", n)
        assert r.returncode == 7, f"expected 7 for --expect-blocks {n}"
        assert not out.exists(), "mismatch still created the output directory"
        assert "mismatch" in r.stderr
        assert r.stdout.strip() == ""

    def test_mismatch_leaves_a_prior_valid_output_dir_untouched(self, tmp_path):
        """Checked BEFORE the directory is prepared, so an approved-count guard can never
        destroy the output it was guarding."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        first = _emit(p, out)
        before = {f.name: (out / f.name).read_bytes() for f in out.iterdir()}
        r = _run(p, "--emit-blocks", out, "--expect-blocks", "99")
        assert r.returncode == 7
        after = {f.name: (out / f.name).read_bytes() for f in out.iterdir()}
        assert after == before, "a count mismatch cleared the existing blocks"
        assert len(first["blocks"]) == 2

    def test_requires_emit_blocks(self, tmp_path, normal_file):
        r = _run(normal_file, "--expect-blocks", "3")
        assert r.returncode == 2
        assert "--expect-blocks" in r.stderr

    @pytest.mark.parametrize("bad", ["-1", "abc", "1.5"])
    def test_rejects_non_nonnegative_values(self, tmp_path, bad):
        p = _write(tmp_path / "t.md", SMALL)
        r = _run(p, "--emit-blocks", tmp_path / "blocks", "--expect-blocks", bad)
        assert r.returncode == 2

    def test_zero_blocks_check_still_wins(self, tmp_path, empty_file):
        """Exit 5 must beat exit 7: --expect-blocks 0 must not turn an empty artifact
        into a success."""
        r = _run(empty_file, "--emit-blocks", tmp_path / "blocks", "--expect-blocks", "0")
        assert r.returncode == 5

    def test_exit_7_does_not_shadow_lower_codes(self, tmp_path, no_rules_file):
        out = tmp_path / "blocks"
        assert _run(tmp_path / "ghost.md", "--emit-blocks", out,
                    "--expect-blocks", "99").returncode == 1
        assert not out.exists()


# ==========================================================================
# E. CAPABILITY PROBE
# ==========================================================================

class TestCapabilityProbe:

    def test_capabilities_needs_no_path_and_exits_0(self):
        r = _run("--capabilities")
        assert r.returncode == 0, r.stderr
        payload = json.loads(r.stdout)
        assert payload["tool"] == "context_file_audit.py"
        assert isinstance(payload["capability_schema_version"], int)
        assert isinstance(payload["capabilities"], list) and payload["capabilities"]

    def test_capabilities_advertises_the_safety_primitives(self):
        caps = json.loads(_run("--capabilities").stdout)["capabilities"]
        for required in ("rules", "emit-blocks", "fence-aware-sections", "clean-emit-dir",
                         "structural-rules", "rule-signals", "unclosed-fence-warning",
                         "expect-blocks", "capabilities", "require"):
            assert required in caps, f"{required} not advertised: {caps}"

    def test_new_capabilities_are_backed_by_real_behavior(self, tmp_path, coverage_file):
        """An advertised capability with no enforcement is the safety-vest bug again —
        so each new name is checked against an observable behavior, not just the list."""
        caps = json.loads(_run("--capabilities").stdout)["capabilities"]
        payload = _captured(coverage_file)

        assert "structural-rules" in caps
        assert COVERAGE_STRUCTURAL_LINES[0] in {x["text"] for x in payload["rules"]}

        assert "rule-signals" in caps
        assert all(x.get("signals") for x in payload["rules"])

        assert "unclosed-fence-warning" in caps
        p = _write(tmp_path / "unclosed.md", UNCLOSED_WITH_RULE)
        assert "unclosed code fence" in _run(p, "--json").stderr

        assert "expect-blocks" in caps
        s = _write(tmp_path / "t.md", SMALL)
        assert _run(s, "--emit-blocks", tmp_path / "b", "--expect-blocks", "99").returncode == 7

    def test_clean_emit_dir_capability_is_backed_by_real_behavior(self, tmp_path):
        """An advertised capability with no enforcement is the safety-vest bug again."""
        caps = json.loads(_run("--capabilities").stdout)["capabilities"]
        assert "clean-emit-dir" in caps
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "README.md").write_text("mine\n", encoding="utf-8")
        assert _run(p, "--emit-blocks", out).returncode == 6

    def test_require_all_supported_exits_0(self):
        caps = json.loads(_run("--capabilities").stdout)["capabilities"]
        r = _run("--require", ",".join(caps))
        assert r.returncode == 0, r.stderr

    def test_require_needs_no_path(self):
        r = _run("--require", "rules")
        assert r.returncode == 0, r.stderr

    def test_every_advertised_capability_is_individually_accepted(self):
        caps = json.loads(_run("--capabilities").stdout)["capabilities"]
        for cap in caps:
            assert _run("--require", cap).returncode == 0, cap


# ==========================================================================
# F. EMIT-DIR SAFETY (exit 6) — the guard added for defect 1, then hardened.
#
# Two invariants:
#   SUCCESS  -> the directory contains EXACTLY manifest.json plus the manifest's
#               declared blocks. A caller that globs the directory instead of
#               reading the manifest must never pick up a stale block.
#   REFUSAL  -> nothing is deleted, nothing is written. All-or-nothing.
#
# PROVENANCE MODEL (revised after the adversarial probe below found the first
# design destroyed ordinary numbered notes): deletion requires a readable
# manifest.json that DECLARES the file, AND the filename must match the shape this
# script emits. Filename shape alone is a guess; the manifest is evidence.
# ==========================================================================

SMALL = "## A\nx\n## B\ny\n"


def _dir_contents(d: Path) -> set:
    return {f.name for f in d.iterdir()}


def _plant_manifest(outdir: Path, *declared: str) -> None:
    """Write a syntactically valid manifest declaring `declared` as block files."""
    (outdir / "manifest.json").write_text(json.dumps({
        "source": "prior-source.md", "source_bytes": 0,
        "block_count": len(declared),
        "blocks": [{"index": i, "section": n, "file": n, "start_line": 1,
                    "line_count": 1, "bytes": 0, "sha256": ""}
                   for i, n in enumerate(declared)],
    }, indent=2) + "\n", encoding="utf-8")


class TestEmitDirSafety:

    # --- the clearing path (exit 0): manifest-proven prior output ---

    def test_emit_blocks_does_not_leave_stale_files_from_a_prior_run(self, tmp_path):
        """Was a defect: mkdir(exist_ok=True) left older blocks beside the fresh ones.
        The prior run is now proven by its manifest, not guessed from filenames."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "999-stale.md").write_text("stale content from an older run\n", encoding="utf-8")
        _plant_manifest(out, "999-stale.md")
        manifest = _emit(p, out)
        listed = {b["file"] for b in manifest["blocks"]} | {"manifest.json"}
        assert _dir_contents(out) == listed, "stale block survived the re-emit"

    def test_dir_after_success_equals_manifest_exactly(self, tmp_path):
        """THE invariant: a caller that globs the dir sees exactly the manifest's blocks.

        Rewritten: the first version planted the literal text 'stale\\n' as manifest.json,
        which is not valid JSON, so it was silently exercising the corrupt-manifest
        refusal instead of the clear path it meant to test. Corrupt manifests now have
        their own test below."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        stale = ("000-old.md", "001-old.md", "002-old.md", "003-old.md")
        for name in stale:
            (out / name).write_text("stale\n", encoding="utf-8")
        _plant_manifest(out, *stale)
        manifest = _emit(p, out)
        listed = {b["file"] for b in manifest["blocks"]} | {"manifest.json"}
        assert _dir_contents(out) == listed
        assert len(manifest["blocks"]) == 2, "fixture should emit fewer blocks than the stale set"
        for b in manifest["blocks"]:
            assert (out / b["file"]).read_text(encoding="utf-8") != "stale\n"

    def test_clear_works_with_a_manifest_present_normal_rerun(self, tmp_path):
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        first = _emit(p, out)
        assert (out / "manifest.json").is_file()
        second = _emit(p, out)          # idempotent re-run over its own output
        assert second == first
        assert _dir_contents(out) == {b["file"] for b in second["blocks"]} | {"manifest.json"}

    def test_manifest_only_dir_is_accepted(self, tmp_path):
        """A manifest declaring blocks that were all removed by hand: nothing to protect."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        _plant_manifest(out)
        manifest = _emit(p, out)
        assert _dir_contents(out) == {b["file"] for b in manifest["blocks"]} | {"manifest.json"}

    def test_manifest_declaring_an_absent_file_still_succeeds(self, tmp_path):
        """Sane behavior: deletability is decided over the entries that EXIST. A declared
        file someone already deleted leaves nothing to protect and nothing to remove, so
        the run proceeds and the end state still equals the new manifest."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "000-a.md").write_text("stale\n", encoding="utf-8")
        _plant_manifest(out, "000-a.md", "777-ghost.md")
        manifest = _emit(p, out)
        assert _dir_contents(out) == {b["file"] for b in manifest["blocks"]} | {"manifest.json"}
        assert not (out / "777-ghost.md").exists()

    # --- the no-manifest refusal: the resolved tradeoff ---

    def test_no_manifest_refuses_even_for_a_plausible_partial_prior_run(self, tmp_path):
        """RESOLVED TRADEOFF — deliberate, not an accident.

        A crashed prior run can leave block files with no manifest. That directory is
        byte-for-byte indistinguishable from a user's folder of numbered notes
        (001-introduction.md, ...), which an earlier shape-based design destroyed while
        reporting them as "from a previous run" — provenance it had never established.
        Since the costs are asymmetric (refusing is recoverable by deleting the
        directory; deleting someone's notes is not), the no-manifest case now REFUSES.
        The crashed-partial-run cleanup is the user's to do. That cost was accepted
        knowingly; see TestEmitDirAdversarial::test_user_numbered_notes_are_not_destroyed
        for the case this protects."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "000-a.md").write_text("half-written\n", encoding="utf-8")
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 6
        assert (out / "000-a.md").read_text(encoding="utf-8") == "half-written\n"
        assert not (out / "manifest.json").exists()
        assert "manifest.json" in r.stderr
        assert r.stdout.strip() == ""

    # --- corrupt / unusable manifests ---

    @pytest.mark.parametrize("body", [
        '{"blocks": [',                 # truncated
        "not json at all\n",            # garbage
        "",                             # empty
        "[]",                           # valid JSON, wrong shape
        '{"blocks": "nope"}',           # blocks not a list of dicts
        '{"block_count": 2}',           # no blocks key
        "null",
    ])
    def test_corrupt_manifest_refuses_and_all_blocks_survive(self, tmp_path, body):
        """A malformed manifest yields no declared set, so nothing is provably ours."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "000-a.md").write_text("precious\n", encoding="utf-8")
        (out / "001-b.md").write_text("precious too\n", encoding="utf-8")
        (out / "manifest.json").write_text(body, encoding="utf-8")
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 6, f"corrupt manifest {body!r} was accepted"
        assert (out / "000-a.md").read_text(encoding="utf-8") == "precious\n"
        assert (out / "001-b.md").read_text(encoding="utf-8") == "precious too\n"
        assert (out / "manifest.json").read_text(encoding="utf-8") == body

    def test_manifest_that_is_a_directory_refuses(self, tmp_path):
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "manifest.json").mkdir()
        (out / "000-a.md").write_text("precious\n", encoding="utf-8")
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 6
        assert (out / "000-a.md").read_text(encoding="utf-8") == "precious\n"
        assert (out / "manifest.json").is_dir()

    # --- undeclared entries beside a valid manifest ---

    def test_undeclared_block_shaped_file_refuses_and_nothing_is_deleted(self, tmp_path):
        """Deleting it would violate 'never remove what we cannot prove we wrote';
        leaving it would violate the anti-glob invariant. Refusing is the only
        consistent outcome."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "000-a.md").write_text("declared\n", encoding="utf-8")
        (out / "555-extra.md").write_text("undeclared\n", encoding="utf-8")
        _plant_manifest(out, "000-a.md")
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 6
        assert (out / "555-extra.md").read_text(encoding="utf-8") == "undeclared\n"
        assert (out / "000-a.md").read_text(encoding="utf-8") == "declared\n", \
            "declared block was deleted despite the whole run refusing"
        assert "555-extra.md" in r.stderr

    def test_manifest_declaring_a_non_block_shaped_name_refuses_all_or_nothing(self, tmp_path):
        """A hand-edited manifest cannot widen the blast radius. Both halves matter: the
        shape check rejects 'notes.txt', AND the deletable sibling must survive — no
        partially-cleared directory."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "000-a.md").write_text("declared and shaped\n", encoding="utf-8")
        (out / "notes.txt").write_text("hand-written\n", encoding="utf-8")
        _plant_manifest(out, "000-a.md", "notes.txt")
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 6
        assert (out / "notes.txt").read_text(encoding="utf-8") == "hand-written\n"
        assert (out / "000-a.md").read_text(encoding="utf-8") == "declared and shaped\n", \
            "partial clear: a deletable entry was removed before the refusal"
        assert _dir_contents(out) == {"000-a.md", "notes.txt", "manifest.json"}

    def test_manifest_declaring_empty_slug_name_refuses_all_or_nothing(self, tmp_path):
        """The hole the all-or-nothing pass closed: '001-.md' is declared but fails
        BLOCK_FILE (now [a-z0-9-]+), and previously the run exited 0 while leaving it
        behind — breaking the anti-glob invariant it exists to protect."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "000-a.md").write_text("a\n", encoding="utf-8")
        (out / "001-.md").write_text("empty slug\n", encoding="utf-8")
        _plant_manifest(out, "000-a.md", "001-.md")
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 6
        assert _dir_contents(out) == {"000-a.md", "001-.md", "manifest.json"}
        assert (out / "001-.md").read_text(encoding="utf-8") == "empty slug\n"

    # --- the announcement ---

    def test_clear_is_announced_on_stderr_not_silent(self, tmp_path):
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "007-stale.md").write_text("stale\n", encoding="utf-8")
        _plant_manifest(out, "007-stale.md")
        r = _run(p, "--emit-blocks", out, "--json")
        assert r.returncode == 0
        assert "removed" in r.stderr, f"clear was silent: {r.stderr!r}"
        assert str(out) in r.stderr

    def test_clear_message_claims_only_manifest_provenance(self, tmp_path):
        """The old message said files were 'from a previous --emit-blocks run' on the
        strength of a filename regex — a provenance claim the script had not established.
        The claim must now cite the manifest, which is the actual evidence."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "007-stale.md").write_text("stale\n", encoding="utf-8")
        _plant_manifest(out, "007-stale.md")
        r = _run(p, "--emit-blocks", out, "--json")
        assert r.returncode == 0
        assert "manifest.json" in r.stderr, \
            f"clear message does not cite its evidence: {r.stderr!r}"
        assert "previous --emit-blocks run" not in r.stderr

    def test_no_clear_announcement_when_dir_was_absent(self, tmp_path):
        p = _write(tmp_path / "t.md", SMALL)
        r = _run(p, "--emit-blocks", tmp_path / "fresh", "--json")
        assert r.returncode == 0
        assert "cleared" not in r.stderr

    def test_empty_existing_dir_is_accepted(self, tmp_path):
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        assert _run(p, "--emit-blocks", out, "--json").returncode == 0

    # --- the refusal path (exit 6) ---

    @pytest.mark.parametrize("with_manifest", [False, True], ids=["no-manifest", "with-manifest"])
    def test_foreign_file_exits_6_and_destroys_nothing(self, tmp_path, with_manifest):
        """Refused on both routes: no evidence at all, or a manifest that does not
        declare the foreign file. Either way nothing is deleted."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        foreign = out / "README.md"
        foreign.write_text("hand-written notes\n", encoding="utf-8")
        (out / "000-a.md").write_text("also here\n", encoding="utf-8")
        if with_manifest:
            _plant_manifest(out, "000-a.md")
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 6
        assert foreign.read_text(encoding="utf-8") == "hand-written notes\n"
        assert (out / "000-a.md").read_text(encoding="utf-8") == "also here\n", \
            "refusal must delete nothing, not even a declared block-shaped file"
        assert "README.md" in r.stderr
        assert r.stdout.strip() == ""

    @pytest.mark.parametrize("with_manifest", [False, True], ids=["no-manifest", "with-manifest"])
    def test_subdirectory_exits_6_and_subdir_survives(self, tmp_path, with_manifest):
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        (out / "sub").mkdir(parents=True)
        (out / "sub" / "keep.txt").write_text("keep\n", encoding="utf-8")
        if with_manifest:
            _plant_manifest(out, "sub")   # even declared, a dir is not a block file
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 6
        assert (out / "sub" / "keep.txt").read_text(encoding="utf-8") == "keep\n"
        assert "sub" in r.stderr

    def test_target_is_a_regular_file_exits_6_and_file_is_untouched(self, tmp_path):
        p = _write(tmp_path / "t.md", SMALL)
        target = _write(tmp_path / "not_a_dir", "important\n")
        r = _run(p, "--emit-blocks", target)
        assert r.returncode == 6
        assert target.read_text(encoding="utf-8") == "important\n"
        assert "not a directory" in r.stderr

    @pytest.mark.parametrize("with_manifest", [False, True], ids=["no-manifest", "with-manifest"])
    def test_exit_6_is_reported_before_any_write(self, tmp_path, with_manifest):
        """Refusal must be total: no partial clear, no partial emit. Deletability is
        decided for every entry BEFORE the first unlink, so the two deletable blocks
        here must survive alongside the one that blocks the run."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        planted = {"000-a.md": "a\n", "notes.txt": "n\n", "001-b.md": "b\n"}
        for name, body in planted.items():
            (out / name).write_text(body, encoding="utf-8")
        expected = set(planted)
        if with_manifest:
            _plant_manifest(out, "000-a.md", "001-b.md")
            expected |= {"manifest.json"}
        assert _run(p, "--emit-blocks", out).returncode == 6
        assert _dir_contents(out) == expected
        for name, body in planted.items():
            assert (out / name).read_text(encoding="utf-8") == body

    def test_zero_blocks_check_precedes_the_dir_check(self, tmp_path, empty_file):
        """Exit 5 must win over exit 6: with nothing to emit, the dir is irrelevant and
        must not be touched."""
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "README.md").write_text("mine\n", encoding="utf-8")
        r = _run(empty_file, "--emit-blocks", out)
        assert r.returncode == 5
        assert (out / "README.md").read_text(encoding="utf-8") == "mine\n"

    def test_exit_6_does_not_shadow_lower_codes(self, tmp_path, no_rules_file):
        """Codes 0-5 must behave exactly as before the exit-6 guard landed."""
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "README.md").write_text("mine\n", encoding="utf-8")
        # 1 beats 6: bad path is checked before the dir
        assert _run(tmp_path / "ghost.md", "--emit-blocks", out).returncode == 1
        # 2 beats 6: argparse runs first
        assert _run(no_rules_file, "--emit-blocks", out, "--top", "-1").returncode == 2
        # 4 beats 6: --rules short-circuits and never looks at the dir
        assert _run(no_rules_file, "--rules", "--emit-blocks", out).returncode == 4
        assert (out / "README.md").exists()


# ==========================================================================
# G. ADVERSARIAL PROBE of the clearing rule.
#
# History: the first design deleted any file matching ^\d{3,}-[a-z0-9-]*\.md$ on
# filename shape alone. This probe found it destroyed an ordinary folder of numbered
# notes, which drove the redesign to manifest-based provenance. These tests now pin
# where the blast radius stops under the NEW rule (declared AND shaped), so a future
# widening of either half is caught. Re-probed empirically 2026-08-11.
# ==========================================================================

_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


class TestEmitDirAdversarial:

    @pytest.mark.parametrize("name", [
        "01-x.md",              # 2 digits — below the {3,} floor
        "001-Introduction.md",  # uppercase not in [a-z0-9-]
        "001-my_notes.md",      # underscore not in [a-z0-9-]
        "001-x.markdown",       # wrong extension
        "001-x.MD",             # uppercase extension
        "1-intro.md",           # single digit
        "notes-001.md",         # digits not leading
        "001 x.md",             # space
    ])
    def test_near_miss_filenames_are_refused_not_deleted(self, tmp_path, name):
        """Even when a manifest DECLARES the name, the shape check must reject it — a
        hand-edited or corrupted manifest must not be able to widen the blast radius."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / name).write_text("user content\n", encoding="utf-8")
        _plant_manifest(out, name)
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 6, f"{name} was accepted into the clear set"
        assert (out / name).read_text(encoding="utf-8") == "user content\n"

    @pytest.mark.parametrize("name", [
        "001-x.md", "0001-x.md", "00001234-x.md", "999-a-b-c.md", "007-stale.md",
    ])
    def test_block_shaped_names_are_deleted_only_when_declared(self, tmp_path, name):
        """The other half of the boundary: names the OLD design destroyed on sight now
        survive without a manifest, and are removed only with one."""
        p = _write(tmp_path / "t.md", SMALL)
        for declared in (False, True):
            out = tmp_path / f"blocks-{declared}"
            out.mkdir()
            (out / name).write_text("content\n", encoding="utf-8")
            if declared:
                _plant_manifest(out, name)
            r = _run(p, "--emit-blocks", out)
            if declared:
                assert r.returncode == 0, f"{name} declared but not cleared"
                assert not (out / name).exists() or name in {
                    b["file"] for b in json.loads((out / "manifest.json").read_text())["blocks"]}
            else:
                assert r.returncode == 6, f"{name} deleted without provenance"
                assert (out / name).read_text(encoding="utf-8") == "content\n"

    def test_symlink_clear_removes_the_link_not_its_target(self, tmp_path):
        """A declared, block-shaped entry that is really a symlink: is_file() follows the
        link so it is cleared, but unlink() removes the LINK, so the pointed-at file
        survives. No path to destroying data outside the directory."""
        p = _write(tmp_path / "t.md", SMALL)
        target = _write(tmp_path / "target.md", "precious\n")
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "001-x.md").symlink_to(target)
        _plant_manifest(out, "001-x.md")
        assert _run(p, "--emit-blocks", out).returncode == 0
        assert target.read_text(encoding="utf-8") == "precious\n"
        assert not (out / "001-x.md").is_symlink()

    def test_symlink_without_a_manifest_is_refused(self, tmp_path):
        p = _write(tmp_path / "t.md", SMALL)
        target = _write(tmp_path / "target.md", "precious\n")
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "001-x.md").symlink_to(target)
        assert _run(p, "--emit-blocks", out).returncode == 6
        assert (out / "001-x.md").is_symlink()
        assert target.read_text(encoding="utf-8") == "precious\n"

    @pytest.mark.parametrize("kind", ["broken", "to-directory"])
    def test_non_file_symlinks_are_refused(self, tmp_path, kind):
        """is_file() is False for a broken link or a link to a dir, so both land in the
        foreign set and are refused rather than silently unlinked."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        dest = (tmp_path / "nowhere") if kind == "broken" else (tmp_path / "somedir")
        if kind == "to-directory":
            dest.mkdir()
        (out / "001-x.md").symlink_to(dest)
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 6
        assert (out / "001-x.md").is_symlink()

    @pytest.mark.skipif(_ROOT, reason="root ignores file permission bits")
    def test_read_only_declared_block_file_is_still_cleared(self, tmp_path):
        """POSIX unlink() keys off the DIRECTORY's write bit, not the file's, so a
        chmod 444 block file is removed. Not a defect — it is a file the manifest proves
        this script wrote. Without a manifest it is refused like anything else."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        ro = out / "001-x.md"
        ro.write_text("stale\n", encoding="utf-8")
        ro.chmod(0o444)
        _plant_manifest(out, "001-x.md")
        try:
            assert _run(p, "--emit-blocks", out).returncode == 0
            assert not ro.exists()
        finally:
            if ro.exists():
                ro.chmod(0o644)

    # --- formerly-failing defects, now fixed and pinned as positive assertions ---

    def test_user_numbered_notes_are_not_destroyed(self, tmp_path):
        """WAS A DEFECT (data loss). Shape-based clearing deleted an ordinary folder of
        numbered notes and exited 0, reporting them as "from a previous run" — provenance
        it had never established. Now: no manifest, no deletion. This is the case that
        justifies refusing the crashed-partial-run cleanup; see
        TestEmitDirSafety::test_no_manifest_refuses_even_for_a_plausible_partial_prior_run."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "my-book-chapters"
        out.mkdir()
        chapters = {"001-introduction.md": "# Introduction\n", "002-methods.md": "# Methods\n",
                    "0001-preface.md": "# Preface\n", "999-a-b-c.md": "# Appendix\n"}
        for name, body in chapters.items():
            (out / name).write_text(body, encoding="utf-8")
        r = _run(p, "--emit-blocks", out)
        assert r.returncode == 6
        for name, body in chapters.items():
            assert (out / name).read_text(encoding="utf-8") == body
        assert not (out / "manifest.json").exists()

    def test_empty_slug_filename_is_not_a_shape_this_script_emits(self, tmp_path):
        """WAS A DEFECT (over-broad regex, [a-z0-9-]* -> +). '001-.md' has an empty slug,
        a name the emitter can never produce: _slug() falls back to 'section'. It is now
        outside the clear set even when a manifest declares it."""
        p = _write(tmp_path / "t.md", SMALL)
        for declared in (False, True):
            out = tmp_path / f"blocks-{declared}"
            out.mkdir()
            (out / "001-.md").write_text("user content\n", encoding="utf-8")
            if declared:
                _plant_manifest(out, "001-.md")
            r = _run(p, "--emit-blocks", out)
            assert r.returncode == 6, f"001-.md accepted (declared={declared})"
            assert (out / "001-.md").read_text(encoding="utf-8") == "user content\n"

    @pytest.mark.skipif(_ROOT, reason="root ignores directory permission bits")
    def test_unwritable_dir_exits_6_not_a_traceback(self, tmp_path):
        """WAS A DEFECT (exit-code collision). An OSError escaping the clear produced a
        traceback and exit 1 — the documented code for 'path is not a file' — so the
        consuming skill would blame the SOURCE file for an unwritable OUTPUT dir. Now
        wrapped to exit 6."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        (out / "001-x.md").write_text("stale\n", encoding="utf-8")
        _plant_manifest(out, "001-x.md")
        out.chmod(0o555)
        try:
            r = _run(p, "--emit-blocks", out)
            assert "Traceback" not in r.stderr, "unhandled exception instead of an exit code"
            assert r.returncode == 6
            assert r.stdout.strip() == ""
        finally:
            out.chmod(0o755)

    @pytest.mark.skipif(_ROOT, reason="root ignores directory permission bits")
    def test_unwritable_fresh_dir_exits_6_not_a_traceback(self, tmp_path):
        """The emit_blocks() write path needs the same wrapping as the clear path: an
        empty-but-unwritable dir passes preparation and fails at the first write."""
        p = _write(tmp_path / "t.md", SMALL)
        out = tmp_path / "blocks"
        out.mkdir()
        out.chmod(0o555)
        try:
            r = _run(p, "--emit-blocks", out)
            assert "Traceback" not in r.stderr
            assert r.returncode == 6
        finally:
            out.chmod(0o755)

    @pytest.mark.skipif(_ROOT, reason="root ignores directory permission bits")
    def test_unwritable_parent_exits_6_not_a_traceback(self, tmp_path):
        """mkdir() of an absent target under an unwritable parent must also be an exit
        code, not a traceback."""
        p = _write(tmp_path / "t.md", SMALL)
        parent = tmp_path / "locked"
        parent.mkdir()
        parent.chmod(0o555)
        try:
            r = _run(p, "--emit-blocks", parent / "blocks")
            assert "Traceback" not in r.stderr
            assert r.returncode == 6
        finally:
            parent.chmod(0o755)


# ==========================================================================
# Smoke test against the live CLAUDE.md — presence only, no content assertions.
# ==========================================================================

class TestLiveSmoke:

    @pytest.mark.skipif(not (REPO_ROOT / "CLAUDE.md").is_file(), reason="no CLAUDE.md")
    def test_claude_md_audits_and_round_trips(self, tmp_path):
        src = REPO_ROOT / "CLAUDE.md"
        assert _run(src, "--json").returncode == 0
        assert _run(src, "--rules", "--json").returncode == 0
        out = tmp_path / "blocks"
        manifest = _emit(src, out)
        joined = b"".join(
            (out / b["file"]).read_bytes()
            for b in sorted(manifest["blocks"], key=lambda b: b["index"]))
        assert joined == src.read_bytes()
