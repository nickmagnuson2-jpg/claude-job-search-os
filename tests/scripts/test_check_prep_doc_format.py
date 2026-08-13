"""PreToolUse hook: the mechanical subset of the prep-doc checks.

Per tools/HOOK_AUTHORING.md, every hook test carries BOTH block cases AND the clean
cases that describe its false-positive surface. For a Bash hook that surface is
command position; for this content hook it is PATH SCOPE — docs that merely discuss
the stamp format, and the test fixtures that deliberately contain broken ones.

The suite-breaking case is real and covered: tests/fixtures/w4/output/acme-corp/
holds files matching output/<slug>/*prep*.md, and tests/fixtures/w4/ holds fixtures
with deliberately malformed stamps. A hook that blocked its own fixtures would make
the W4 suite unrunnable.
"""
import json
import subprocess
import sys

import pytest

from conftest import TOOLS_DIR

HOOK = TOOLS_DIR / "check_prep_doc_format.py"

PREP_PATH = "output/acme-corp/081226-prep.md"

GOOD_STAMP = ("<!-- outreach_status: recipient=jane doe artifact=cv delivered=true "
              "as_of=2026-08-12 tool_version=2 -->")
V1_STAMP = ("<!-- outreach_status: recipient=jane doe delivered=true "
            "as_of=2026-08-12 tool_version=1 -->")

PROOFS_OK = ("**Primary proof** (domain: customer-ops): rebuilt the onboarding path.\n"
             "**Reserve proof** (domain: product-analytics): stood up the funnel.\n")
PROOFS_COLLAPSE = ("**Primary proof** (domain: customer-experience): rebuilt onboarding.\n"
                   "**Reserve proof** (domain: customer-ops): ran support deflection.\n")
PROOFS_BAD_TAG = ("**Primary proof** (domain: vibes): rebuilt onboarding.\n"
                  "**Reserve proof** (domain: product-analytics): stood up the funnel.\n")


def run(tool_name: str, file_path: str, **payload) -> subprocess.CompletedProcess:
    body = {"tool_name": tool_name, "tool_input": {"file_path": file_path, **payload}}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(body), capture_output=True, text=True, encoding="utf-8",
    )


def write(content: str, path: str = PREP_PATH) -> subprocess.CompletedProcess:
    return run("Write", path, content=content)


# --------------------------------------------------------------- blocks


def test_blocks_a_v1_stamp():
    proc = write(f"# Prep\n\n{PROOFS_OK}\n# Logistics\n\n{V1_STAMP}\n")
    assert proc.returncode == 2
    assert "Malformed outreach_status stamp" in proc.stderr
    assert "regenerate it" in proc.stderr


def test_blocks_a_structurally_broken_stamp():
    broken = "<!-- outreach_status: recipient=jane doe artifact=cv -->"
    proc = write(f"# Prep\n\n{PROOFS_OK}\n{broken}\n")
    assert proc.returncode == 2


def test_blocks_collapsing_domains():
    proc = write(f"# Prep\n\n{PROOFS_COLLAPSE}\n")
    assert proc.returncode == 2
    assert "one domain wearing two labels" in proc.stderr
    assert "do NOT relabel" in proc.stderr


def test_blocks_an_invalid_domain_tag():
    proc = write(f"# Prep\n\n{PROOFS_BAD_TAG}\n")
    assert proc.returncode == 2
    assert "not in the closed enum" in proc.stderr
    assert "customer-ops" in proc.stderr  # the valid list is printed


def test_blocks_on_an_edit_fragment_carrying_both_proof_lines():
    proc = run("Edit", PREP_PATH, new_string=PROOFS_COLLAPSE)
    assert proc.returncode == 2


def test_block_message_names_the_hook_and_the_fp_log_path():
    proc = write(f"# Prep\n\n{PROOFS_COLLAPSE}\n")
    assert "check_prep_doc_format.py" in proc.stderr
    assert "friction_log.py" in proc.stderr


# --------------------------------------------------------------- clean


def test_allows_a_compliant_doc():
    proc = write(f"# Prep\n\n{PROOFS_OK}\n# Logistics\n\n{GOOD_STAMP}\n")
    assert proc.returncode == 0, proc.stderr


def test_allows_a_doc_mid_authoring_with_only_a_primary_proof():
    """Blocking a missing reserve would fire on every normal incremental write."""
    proc = write("# Prep\n\n**Primary proof** (domain: customer-ops): rebuilt onboarding.\n")
    assert proc.returncode == 0, proc.stderr


def test_allows_suppressive_phrasing_without_a_stamp():
    """Contextual — deliberately left to check_prep_doc.py Step 6a, not this hook."""
    proc = write(f"# Prep\n\n{PROOFS_OK}\n# Logistics\n\n- CV already sent, do not re-offer it.\n")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("path", [
    "tests/fixtures/w4/output/acme-corp/081026-prep.md",   # the real fixture path
    "tests/fixtures/w4/081026-malformed-stamp.md",         # deliberately broken fixture
    "output/analysis/081226-drafting-integrity-build-log.md",
    "output/analysis/081226-prep-doc-audit.md",            # analysis ABOUT prep docs
    ".claude/skills/prep-interview/SKILL.md",              # documents the format
    "tools/check_prep_doc.py",                             # holds the regex
    "docs/usage.md",
    "output/acme-corp/081226-notes.md",                    # not a prep doc
])
def test_out_of_scope_paths_are_never_blocked(path):
    proc = write(f"{V1_STAMP}\n{PROOFS_COLLAPSE}\n", path=path)
    assert proc.returncode == 0, f"{path}: {proc.stderr}"


@pytest.mark.parametrize("tool_name", ["Read", "Grep", "Bash", "Glob"])
def test_non_write_tools_are_ignored(tool_name):
    proc = run(tool_name, PREP_PATH, content=V1_STAMP)
    assert proc.returncode == 0


# --------------------------------------------------------------- fail-open


def test_fails_open_on_bad_json():
    proc = subprocess.run([sys.executable, str(HOOK)], input="not json",
                          capture_output=True, text=True)
    assert proc.returncode == 0


@pytest.mark.parametrize("payload", [{}, {"file_path": ""}, {"file_path": PREP_PATH}])
def test_fails_open_on_missing_fields(payload):
    body = {"tool_name": "Write", "tool_input": payload}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(body),
                          capture_output=True, text=True)
    assert proc.returncode == 0
