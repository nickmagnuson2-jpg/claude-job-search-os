"""workstream_probe.py: four generic readings for workstream PROBE lines.

The contract that matters: a reading that could not be taken is exit 2 with an error,
never a value of 0. The runner turns exit 2 into a probe error, which always surfaces;
a 0 would read as "nothing there". Fixtures are synthetic.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import workstream_probe as wp  # noqa: E402


def _main(root, *args, capsys):
    code = wp.main([str(a) for a in args], root=root)
    return code, json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------- count-lines

def test_count_lines_counts_matching_lines_only(tmp_path, capsys):
    (tmp_path / "status.md").write_text(
        "- Theme 1: worked to completion\n- Theme 2: started\n- theme 3: worked to completion\n",
        encoding="utf-8")
    assert _main(tmp_path, "count-lines", "status.md", r"^- Theme \d+: worked",
                 capsys=capsys) == (0, {"value": 1})
    assert _main(tmp_path, "count-lines", "status.md", r"^- Theme \d+: worked",
                 "--ignore-case", capsys=capsys) == (0, {"value": 2})
    assert _main(tmp_path, "count-lines", "status.md", "nothing like this",
                 capsys=capsys) == (0, {"value": 0})


def test_count_lines_on_a_missing_or_undecodable_file_is_an_error_not_zero(tmp_path, capsys):
    code, out = _main(tmp_path, "count-lines", "gone.md", "x", capsys=capsys)
    assert code == 2 and "cannot read gone.md: FileNotFoundError" in out["error"]
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe")
    code, out = _main(tmp_path, "count-lines", "bad.md", "x", capsys=capsys)
    assert code == 2 and "UnicodeDecodeError" in out["error"] and "value" not in out


def test_count_lines_rejects_a_pattern_that_does_not_compile(tmp_path, capsys):
    (tmp_path / "a.md").write_text("x\n", encoding="utf-8")
    code, out = _main(tmp_path, "count-lines", "a.md", "(unclosed", capsys=capsys)
    assert code == 2 and "bad pattern" in out["error"]


# ---------------------------------------------------------------- paths

def test_resolve_handles_relative_absolute_home_and_memory_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    assert wp.resolve("data/x.md", root) == root / "data" / "x.md"
    assert wp.resolve(str(tmp_path / "abs.md"), root) == tmp_path / "abs.md"
    assert wp.resolve("~/notes.md", root) == tmp_path / "home" / "notes.md"
    slug = str(root).replace("/", "-")
    assert wp.resolve("memory:rule.md", root) == (
        tmp_path / "cfg" / "projects" / slug / "memory" / "rule.md")


def test_memory_dir_defaults_to_the_home_claude_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(wp.Path, "home", classmethod(lambda cls: tmp_path))
    assert wp.memory_dir(Path("/a/b")) == tmp_path / ".claude" / "projects" / "-a-b" / "memory"


# ---------------------------------------------------------------- frontmatter

RULE = """---
name: feedback_example
metadata:
  occurrences: 7
  promoted: "no"
  reopen_gate: "3rd fire"
---
occurrences: 99 in the body is not the frontmatter
"""


def test_frontmatter_reads_an_integer_and_a_quoted_string(tmp_path, capsys):
    (tmp_path / "rule.md").write_text(RULE, encoding="utf-8")
    assert _main(tmp_path, "frontmatter", "rule.md", "occurrences",
                 capsys=capsys) == (0, {"value": 7})
    assert _main(tmp_path, "frontmatter", "rule.md", "promoted",
                 capsys=capsys) == (0, {"value": "no"})
    assert _main(tmp_path, "frontmatter", "rule.md", "reopen_gate",
                 capsys=capsys) == (0, {"value": "3rd fire"})


def test_frontmatter_errors_on_a_missing_key_block_or_file(tmp_path, capsys):
    (tmp_path / "rule.md").write_text(RULE, encoding="utf-8")
    code, out = _main(tmp_path, "frontmatter", "rule.md", "last_cited", capsys=capsys)
    assert code == 2 and "has no `last_cited`" in out["error"]
    (tmp_path / "plain.md").write_text("occurrences: 3\n", encoding="utf-8")
    code, out = _main(tmp_path, "frontmatter", "plain.md", "occurrences", capsys=capsys)
    assert code == 2 and "no frontmatter block" in out["error"]
    (tmp_path / "open.md").write_text("---\noccurrences: 3\n", encoding="utf-8")
    assert _main(tmp_path, "frontmatter", "open.md", "occurrences", capsys=capsys)[0] == 2
    assert _main(tmp_path, "frontmatter", "gone.md", "occurrences", capsys=capsys)[0] == 2


def test_frontmatter_does_not_match_a_longer_key(tmp_path, capsys):
    (tmp_path / "r.md").write_text("---\nmy_occurrences: 4\n---\n", encoding="utf-8")
    assert _main(tmp_path, "frontmatter", "r.md", "occurrences", capsys=capsys)[0] == 2


# ---------------------------------------------------------------- lift

def _emit(tmp_path, payload, code=0):
    script = tmp_path / "emit.py"
    script.write_text(f"import sys\nsys.stdout.write({payload!r})\nsys.exit({code})\n",
                      encoding="utf-8")
    return [sys.executable, str(script)]


def test_lift_reports_one_length_as_an_integer_and_several_as_a_labelled_line(tmp_path, capsys):
    cmd = _emit(tmp_path, json.dumps({"pending": [1, 2, 3], "done": [], "by": {"a": 1}}))
    assert _main(tmp_path, "lift", "--len", "pending", "--", *cmd,
                 capsys=capsys) == (0, {"value": 3})
    assert _main(tmp_path, "lift", "--len", "pending", "--len", "done", "--len", "by",
                 "--", *cmd, capsys=capsys) == (0, {"value": "pending=3 | done=0 | by=1"})


def test_lift_reads_a_field_through_a_dotted_path_and_keeps_a_zero(tmp_path, capsys):
    cmd = _emit(tmp_path, json.dumps({"count": 0, "summary": {"open": 12}}))
    assert _main(tmp_path, "lift", "--field", "count", "--", *cmd,
                 capsys=capsys) == (0, {"value": 0})
    assert _main(tmp_path, "lift", "--field", "summary.open", "--", *cmd,
                 capsys=capsys) == (0, {"value": 12})


def test_lift_passes_the_commands_own_double_dash_through(tmp_path, capsys):
    script = tmp_path / "args.py"
    script.write_text("import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}))\n",
                      encoding="utf-8")
    code, out = _main(tmp_path, "lift", "--field", "argv", "--", sys.executable, str(script),
                      "list", "--", "--open", capsys=capsys)
    assert (code, out) == (0, {"value": ["list", "--", "--open"]})


def test_lift_runs_the_command_from_the_repo_root(tmp_path, capsys):
    script = tmp_path / "cwd.py"
    script.write_text("import json, os\nprint(json.dumps({'cwd': os.getcwd()}))\n",
                      encoding="utf-8")
    code, out = _main(tmp_path, "lift", "--field", "cwd", "--", sys.executable, str(script),
                      capsys=capsys)
    assert code == 0 and Path(out["value"]).resolve() == tmp_path.resolve()


@pytest.mark.parametrize("args,needle", [
    (["--len", "nope"], "has no `nope`"),
    (["--field", "summary.nope"], "has no `summary.nope`"),
    (["--field", "count.deeper"], "has no `count.deeper`"),
    (["--len", "count"], "is not a list or object"),
    ([], "not both and not neither"),
    (["--len", "pending", "--field", "count"], "not both and not neither"),
])
def test_lift_errors_instead_of_guessing(tmp_path, capsys, args, needle):
    cmd = _emit(tmp_path, json.dumps({"count": 4, "pending": [], "summary": {}}))
    code, out = _main(tmp_path, "lift", *args, "--", *cmd, capsys=capsys)
    assert code == 2 and needle in out["error"], out


def test_lift_errors_on_output_that_is_not_json_and_on_no_command(tmp_path, capsys):
    code, out = _main(tmp_path, "lift", "--field", "x", "--", *_emit(tmp_path, "Traceback", 1),
                      capsys=capsys)
    assert code == 2 and "not JSON (exit 1)" in out["error"]
    code, out = _main(tmp_path, "lift", "--field", "x", capsys=capsys)
    assert code == 2 and "needs a command" in out["error"]


def test_lift_errors_when_the_command_cannot_be_started_or_times_out(tmp_path):
    with pytest.raises(wp.ProbeError, match="did not run: FileNotFoundError"):
        wp.lift([str(tmp_path / "no-such-binary")], tmp_path, ["x"], None)

    def slow(argv, **kw):
        assert kw["timeout"] == wp.LIFT_TIMEOUT
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    with pytest.raises(wp.ProbeError, match="did not run: TimeoutExpired"):
        wp.lift(["x"], tmp_path, ["x"], None, run=slow)


# ---------------------------------------------------------------- const-len

SOURCE = '''KNOWN_MISSING = {
    "a.py": "why",
    "b.py": "why",
}
EMPTY: dict[str, str] = {}
NAMES = ("x", "y", "z")
SEEN = {"p", "q"}
ROWS = ["one"]
COMPUTED = dict(a=1)

def f():
    INNER = {"not": "module level"}
'''


def test_const_len_counts_literals_without_importing_the_file(tmp_path, capsys):
    (tmp_path / "t.py").write_text(SOURCE + "raise SystemExit('never imported')\n",
                                   encoding="utf-8")
    assert _main(tmp_path, "const-len", "t.py:KNOWN_MISSING", capsys=capsys) == (0, {"value": 2})
    assert _main(tmp_path, "const-len", "t.py:EMPTY", capsys=capsys) == (0, {"value": 0})
    assert _main(tmp_path, "const-len", "t.py:NAMES", "t.py:SEEN", "t.py:ROWS", "t.py:EMPTY",
                 capsys=capsys) == (0, {"value": "NAMES=3 | SEEN=2 | ROWS=1 | EMPTY=0"})


@pytest.mark.parametrize("spec,needle", [
    ("t.py:GONE", "has no module-level `GONE`"),
    ("t.py:INNER", "has no module-level `INNER`"),
    ("t.py:COMPUTED", "is not a dict, set, list or tuple literal"),
    ("t.py", "takes FILE:NAME"),
    (":NAME", "takes FILE:NAME"),
    ("t.py:", "takes FILE:NAME"),
    ("gone.py:X", "cannot read gone.py"),
])
def test_const_len_errors_instead_of_reporting_zero(tmp_path, capsys, spec, needle):
    (tmp_path / "t.py").write_text(SOURCE, encoding="utf-8")
    code, out = _main(tmp_path, "const-len", spec, capsys=capsys)
    assert code == 2 and needle in out["error"], out


def test_const_len_errors_on_a_file_that_does_not_parse(tmp_path, capsys):
    (tmp_path / "broken.py").write_text("X = {\n", encoding="utf-8")
    code, out = _main(tmp_path, "const-len", "broken.py:X", capsys=capsys)
    assert code == 2 and "does not parse" in out["error"]


# ---------------------------------------------------------------- the CLI

def test_the_script_runs_as_a_command_and_prints_one_json_object(tmp_path):
    (tmp_path / "a.md").write_text("x\ny\nx\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(REPO / "tools" / "workstream_probe.py"),
                        "count-lines", str(tmp_path / "a.md"), "^x$"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout) == {"value": 2} and r.stderr == ""


def test_an_unknown_subcommand_is_a_usage_error(tmp_path):
    r = subprocess.run([sys.executable, str(REPO / "tools" / "workstream_probe.py"), "guess"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "invalid choice" in r.stderr
