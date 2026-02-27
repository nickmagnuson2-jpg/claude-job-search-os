"""Shared pytest configuration for preprocessing script tests."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Repository root — tests/scripts/ is two levels below root
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
TOOLS_DIR = REPO_ROOT / "tools"


def run_script(script_name: str, *args, input_dir=None) -> dict:
    """Run a tools/ script and return parsed JSON output.

    Args:
        script_name: Script filename (e.g. 'todo_daily_metrics.py')
        *args: Additional CLI arguments passed to the script
        input_dir: Optional working directory override (default: REPO_ROOT)

    Returns:
        Parsed JSON dict from stdout

    Raises:
        subprocess.CalledProcessError: If script exits non-zero
        json.JSONDecodeError: If stdout is not valid JSON
    """
    script_path = TOOLS_DIR / script_name
    cmd = [sys.executable, str(script_path), *[str(a) for a in args]]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        cwd=str(input_dir or REPO_ROOT),
        check=True,
    )
    return json.loads(result.stdout)


@pytest.fixture
def fixtures_dir() -> Path:
    """Return path to tests/fixtures/ directory."""
    return FIXTURES_DIR


@pytest.fixture
def repo_root() -> Path:
    """Return path to repository root."""
    return REPO_ROOT
