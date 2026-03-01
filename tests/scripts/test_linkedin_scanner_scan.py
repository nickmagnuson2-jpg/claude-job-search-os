"""CLI smoke tests for linkedin-scanner/scan.py.

Tests argument parsing and env var validation without opening a browser or
making any API calls.  Scraper, Ranker, and anthropic are mocked in
sys.modules before scan.py is imported, so the module loads cleanly.

anthropic is not installed in the test environment.
Selenium IS installed; WebDriverException imports fine without a browser.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─── Mocks and path setup must precede importing scan ────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = REPO_ROOT / "tools" / "linkedin-scanner"

for _mod in ("anthropic", "Scraper", "Ranker"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

if str(SCANNER_DIR) not in sys.path:
    sys.path.insert(0, str(SCANNER_DIR))

import scan  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────


def _env_without(*keys: str) -> dict:
    """Return a copy of os.environ without the named credential keys."""
    return {k: v for k, v in os.environ.items() if k not in keys}


# ── Argument parsing ──────────────────────────────────────────────────────────

def test_parse_args_defaults():
    """--company is the only required arg; num, output_format, headless have defaults."""
    with patch("sys.argv", ["scan.py", "--company", "TestCo"]):
        args = scan.parse_args()
    assert args.num == 20
    assert args.output_format == "json"
    assert args.headless is False


def test_parse_args_custom_num():
    """--num overrides the default of 20."""
    with patch("sys.argv", ["scan.py", "--company", "TestCo", "--num", "5"]):
        args = scan.parse_args()
    assert args.num == 5


def test_parse_args_csv_format():
    """--output-format csv is accepted and stored correctly."""
    with patch("sys.argv", ["scan.py", "--company", "TestCo", "--output-format", "csv"]):
        args = scan.parse_args()
    assert args.output_format == "csv"


def test_parse_args_headless_flag():
    """--headless flag sets headless=True."""
    with patch("sys.argv", ["scan.py", "--company", "TestCo", "--headless"]):
        args = scan.parse_args()
    assert args.headless is True


# ── Env var validation ────────────────────────────────────────────────────────

def test_missing_all_env_vars_exits_nonzero():
    """All three credential env vars missing → main() exits with code 1."""
    clean_env = _env_without("LINKEDIN_EMAIL", "LINKEDIN_PASSWORD", "ANTHROPIC_API_KEY")
    with patch("sys.argv", ["scan.py", "--company", "TestCo"]):
        with patch.dict("os.environ", clean_env, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                scan.main()
    assert exc_info.value.code == 1


def test_missing_api_key_error_message(capsys):
    """Missing ANTHROPIC_API_KEY → error output names the missing variable."""
    partial_env = _env_without("ANTHROPIC_API_KEY")
    partial_env.update({"LINKEDIN_EMAIL": "test@example.com", "LINKEDIN_PASSWORD": "pass"})
    with patch("sys.argv", ["scan.py", "--company", "TestCo"]):
        with patch.dict("os.environ", partial_env, clear=True):
            with pytest.raises(SystemExit):
                scan.main()
    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY" in captured.out


def test_missing_linkedin_creds_error_message(capsys):
    """Missing LINKEDIN_EMAIL → error output names the missing variable."""
    partial_env = _env_without("LINKEDIN_EMAIL", "LINKEDIN_PASSWORD")
    partial_env["ANTHROPIC_API_KEY"] = "test-key"
    with patch("sys.argv", ["scan.py", "--company", "TestCo"]):
        with patch.dict("os.environ", partial_env, clear=True):
            with pytest.raises(SystemExit):
                scan.main()
    captured = capsys.readouterr()
    assert "LINKEDIN_EMAIL" in captured.out


# ── Prompt template ───────────────────────────────────────────────────────────

def test_prompt_template_exists_and_has_placeholder():
    """rank_profile_template.txt must exist and contain the {profile_summary} slot."""
    template_path = SCANNER_DIR / "src" / "prompts" / "rank_profile_template.txt"
    assert template_path.exists(), f"Template not found: {template_path}"
    content = template_path.read_text(encoding="utf-8")
    assert "{profile_summary}" in content


def test_prompt_template_formats_without_error():
    """Formatting the template with a dummy profile_summary must not raise KeyError."""
    template_path = SCANNER_DIR / "src" / "prompts" / "rank_profile_template.txt"
    content = template_path.read_text(encoding="utf-8")
    formatted = content.format(profile_summary="test candidate summary")
    assert "test candidate summary" in formatted
