"""Retired hooks stay retired: not wired in settings, not on disk, not in a registry.

A retired hook that is still wired keeps blocking, and one that is only unwired keeps
its registry entries (reach list, allowlists, docs table) alive for a file nobody runs.
Each name below was removed on purpose; the reason and the evidence are in
docs/CHANGELOG.md.
"""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# name -> the CHANGELOG line that records why it went
RETIRED = {
    "check_save_claims": "2026-11-23: 0 true positives in 44 blocks",
}


def _hook_commands(settings: dict):
    for groups in (settings.get("hooks") or {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                yield hook.get("command", "")


@pytest.mark.parametrize("name", sorted(RETIRED))
def test_a_retired_hook_is_not_wired(name):
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    wired = [c for c in _hook_commands(settings) if f"tools/{name}.py" in c]
    assert wired == [], f"{name} is retired but still wired: {wired}"


@pytest.mark.parametrize("name", sorted(RETIRED))
def test_a_retired_hook_and_its_tests_are_gone(name):
    left = [p for p in [REPO / "tools" / f"{name}.py",
                        *sorted((REPO / "tests" / "scripts").glob(f"test_{name}*.py"))]
            if p.exists()]
    assert left == [], f"{name} is retired but these remain: {left}"


@pytest.mark.parametrize("name", sorted(RETIRED))
@pytest.mark.parametrize("registry", [
    "docs/hooks.md",
    "tools/mutation-allow.json",
    "tools/count-report-allow.json",
    "tests/scripts/test_tools_announce_reach.py",
    "tests/scripts/test_shared_module_package_import.py",
])
def test_a_retired_hook_has_no_registry_entry(name, registry):
    assert name not in (REPO / registry).read_text(encoding="utf-8"), (
        f"{registry} still lists the retired {name}")


@pytest.mark.parametrize("name", sorted(RETIRED))
def test_the_changelog_records_why(name):
    assert RETIRED[name] in (REPO / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
