"""Guard: every python3 hook command in .claude/settings.json carries the
PYTHONIOENCODING=utf-8 prefix that CLAUDE.md mandates for all tools/*.py.

Origin: fable-audit Theme 3 — 21 hook commands invoked bare `python3 <path>`
without the prefix, so any hook that prints Unicode (e.g. em-dash detection in
check_draft_voice.py) could crash on an ASCII locale, silently disabling the
guard it implements.
"""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"


def _hook_commands(settings: dict):
    cmds = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "command" and isinstance(v, str):
                    cmds.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(settings.get("hooks", {}))
    return cmds


def test_settings_json_is_valid():
    json.loads(SETTINGS.read_text(encoding="utf-8"))


def test_all_python_hook_commands_have_encoding_prefix():
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    offenders = []
    for cmd in _hook_commands(settings):
        # Only python3 invocations of a repo tools/*.py script are in scope.
        if re.search(r"python3?\s+\S*tools/\S+\.py", cmd):
            if "PYTHONIOENCODING=utf-8" not in cmd:
                offenders.append(cmd)
    assert not offenders, "hook commands missing PYTHONIOENCODING=utf-8:\n" + "\n".join(offenders)
