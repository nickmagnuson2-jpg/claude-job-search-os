"""A failed merge must not leave the previous run's output YAML on disk.

The CV pipeline is `cv_merge_theme.py --out X.yaml` then `rendercv render X.yaml`. On
2026-08-25 the merge failed on a YAML parse error, rendercv ran anyway, found the
PREVIOUS run's X.yaml, and rendered it: a real PDF with the right page count, built
from stale content. Removing --out before any step that can fail makes the render that
follows a failed merge fail loudly on a missing file instead.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _load():
    spec = importlib.util.spec_from_file_location("cv_merge_under_test", TOOLS / "cv_merge_theme.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(merge, args) -> object:
    """main()'s outcome: its return code, SystemExit's code, or 'raised' for a parse error."""
    try:
        return merge.main(args)
    except SystemExit as e:
        return e.code
    except yaml.YAMLError:
        return "raised"


@pytest.fixture
def files(tmp_path):
    content = tmp_path / "c.content.yaml"
    content.write_text("cv:\n  name: Casey Doe\n", encoding="utf-8")
    theme = tmp_path / "theme.yaml"
    theme.write_text("design:\n  theme: classic\n", encoding="utf-8")
    out = tmp_path / "out.yaml"
    out.write_text("cv:\n  name: STALE previous run\n", encoding="utf-8")
    return content, theme, out


@pytest.mark.parametrize("failure", ["malformed_content", "missing_content", "missing_theme",
                                     "design_block_in_content"])
def test_a_failed_merge_leaves_no_stale_output(files, failure, capsys):
    content, theme, out = files
    if failure == "malformed_content":
        content.write_text("cv:\n  name: [unclosed\n", encoding="utf-8")
    elif failure == "missing_content":
        content.unlink()
    elif failure == "missing_theme":
        theme.unlink()
    else:
        content.write_text("cv:\n  name: Casey Doe\ndesign:\n  theme: x\n", encoding="utf-8")

    result = _run(_load(), ["--content", str(content), "--theme", str(theme), "--out", str(out)])

    assert result not in (0, None), f"{failure} reported success"
    assert not out.exists(), f"{failure} left the previous run's {out.name} for rendercv to render"


def test_a_successful_merge_replaces_the_stale_output(files, capsys):
    content, theme, out = files
    assert _load().main(["--content", str(content), "--theme", str(theme), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "STALE" not in text and "Casey Doe" in text and "design" in text


def test_out_pointing_at_the_content_file_does_not_delete_the_content(files, capsys):
    """Clearing --out must never destroy an input: same-path was never a supported call,
    but losing the content file is worse than the stale-output failure being prevented."""
    content, theme, _ = files
    assert _load().main(["--content", str(content), "--theme", str(theme), "--out", str(content)]) == 0
    assert "Casey Doe" in content.read_text(encoding="utf-8")
