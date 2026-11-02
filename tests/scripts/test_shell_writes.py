"""Tests for tools/shell_writes.py, the Bash write-target walk the write gates share.

The gates' own behaviour after a `cd` is tested with each gate (test_eic_review_hardening.py,
test_check_build_log_present.py, and check_public_pii's cd tests). These pin the module's own
contract: which segments walk() yields, which directories it carries, and what resolve() and
bash_write_targets() return.
"""
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import shell_writes as sw  # noqa: E402


def segments(command, starts):
    return [(seg.strip(), cwds) for seg, _env, cwds in sw.walk(command, starts)]


def test_walk_yields_every_segment_but_a_cd(tmp_path):
    got = [seg for seg, _ in segments(f"cd {tmp_path}; echo x > a.md; d=sub; cd \"$d\"; "
                                      "echo y > b.md", [str(tmp_path)])]
    assert got == ["echo x > a.md", "d=sub", "echo y > b.md"]


def test_walk_carries_the_directory_a_cd_moves_to(tmp_path):
    (tmp_path / "sub").mkdir()
    (_, cwds), = [s for s in segments("cd sub; echo x > a.md", [str(tmp_path)])]
    assert str(tmp_path / "sub") in cwds


def test_a_cd_to_an_unsafe_variable_value_adds_no_directory(tmp_path):
    got = segments('d="x*"; cd "$d"; echo y > b.md', [str(tmp_path)])
    assert got[-1] == ("echo y > b.md", [str(tmp_path)])


def test_walk_drops_relative_and_empty_starts(tmp_path):
    (_, cwds), = segments("echo x > a.md", ["", "relative/dir", str(tmp_path)])
    assert cwds == [str(tmp_path)]


def test_resolve_joins_relative_candidates_to_every_cwd_and_skips_empty_ones():
    assert sw.resolve(["", "a.md", "/abs/b.md"], ["/r1", "/r2"]) == [
        "/r1/a.md", "/r2/a.md", "/abs/b.md"]


def test_the_gate_walk_keeps_the_start_directory_after_a_cd(tmp_path):
    """Simplification A: the blocking gates skip the cd-landing probe, so a cd only ADDS a
    candidate. After `cd <existing dir>;` the write is still judged from the repo root too."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    got = sw.bash_write_targets(f"cd {elsewhere}; echo x > a.md", "", root)
    assert str(root / "a.md") in got and str(elsewhere / "a.md") in got


def test_bash_write_targets_includes_copy_destinations(tmp_path):
    got = sw.bash_write_targets("cp /tmp/p.md out/q.md", "", tmp_path)
    assert str(tmp_path / "out" / "q.md") in got


# Codex eic-review r2 F1: the segment parser keeps a ( ) or { } group whole, so a cd inside
# one was never seen. walk() now walks the group's body.

def test_a_cd_inside_a_subshell_or_brace_group_is_followed(tmp_path):
    (tmp_path / "sub").mkdir()
    for cmd in ("(cd sub; echo x > a.md)", "{ cd sub; echo x > a.md; }", "(cd sub && echo x > a.md)"):
        got = sw.bash_write_targets(cmd, "", tmp_path)
        assert str(tmp_path / "sub" / "a.md") in got, cmd


def test_a_group_s_own_redirect_is_judged_from_outside_it(tmp_path):
    (tmp_path / "sub").mkdir()
    got = sw.bash_write_targets("(cd sub; cat x) > out.md", "", tmp_path)
    assert str(tmp_path / "out.md") in got


def test_a_cd_in_a_brace_group_stays_a_candidate_after_it(tmp_path):
    (tmp_path / "sub").mkdir()
    got = sw.bash_write_targets("{ cd sub; }; echo x > a.md", "", tmp_path)
    assert str(tmp_path / "sub" / "a.md") in got and str(tmp_path / "a.md") in got


def test_nested_groups_are_followed(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    got = sw.bash_write_targets("(cd a && (cd b; echo x > f.md))", "", tmp_path)
    assert str(tmp_path / "a" / "b" / "f.md") in got


def test_a_command_substitution_or_plain_segment_is_not_a_group():
    assert sw._group_body("echo $(cd x; pwd) > f") is None
    assert sw._group_body("{x} > f") is None
    assert sw._group_body("( cd a; ls )") == " cd a; ls "
    assert sw._group_body("{ cd a; }") == " cd a; "


def test_walk_yields_a_group_once_after_its_body(tmp_path):
    (tmp_path / "sub").mkdir()
    got = segments("(cd sub; echo x > a.md)", [str(tmp_path)])
    assert [seg for seg, _ in got] == ["echo x > a.md", "(cd sub; echo x > a.md)"]


# Round 3 (Codex F1, Fable F1-F4, F11): prefixes, compound bodies, pushd, ~, a cd with a
# redirect, subshell scope, and a nesting cap.

def _hits(cmd, root, rel):
    return str(root / rel) in sw.bash_write_targets(cmd, "", root)


def test_a_group_after_bang_or_time_is_followed(tmp_path):
    (tmp_path / "sub").mkdir()
    for cmd in ("! (cd sub; echo x > a.md)", "time (cd sub; echo x > a.md)",
                "time -p { cd sub; echo x > a.md; }"):
        assert _hits(cmd, tmp_path, "sub/a.md"), cmd


def test_a_cd_inside_if_for_while_bodies_and_pushd_is_followed(tmp_path):
    (tmp_path / "sub").mkdir()
    for cmd in ("if true; then cd sub; echo x > a.md; fi",
                "for i in 1; do cd sub; echo x > a.md; done",
                "if false; then :; else cd sub; echo x > a.md; fi",
                "while true; do cd sub; echo x > a.md; break; done",
                "pushd sub; echo x > a.md",
                # Round 4 (Codex 14.F1, Fable 16.F1, 16.F3, 17.F3): a case arm's label and elif.
                "case x in x) cd sub; echo x > a.md;; esac",
                "case x in (x|y) :;; *) cd sub; echo x > a.md;; esac",
                "if false; then :; elif cd sub; then echo x > a.md; fi"):
        assert _hits(cmd, tmp_path, "sub/a.md"), cmd


def test_only_a_case_body_loses_its_arm_labels(tmp_path):
    # An if body that opens with a subshell is not a case arm: stripping `(...)` as a label
    # would drop the write inside it (q16 mutation survivor, line 101).
    # The cd makes the target visible only to the walk into the subshell; the whole if
    # segment, judged from outside, finds a.md only at the root.
    (tmp_path / "sub").mkdir()
    assert _hits("if (cd sub && echo x > a.md); then :; fi", tmp_path, "sub/a.md")


def test_an_unterminated_compound_is_one_plain_segment(tmp_path):
    # No closing fi/done: there is no body to walk (q16 mutation survivor, line 98).
    assert [seg for seg, _ in segments("if true", [str(tmp_path)])] == ["if true"]


def test_a_cd_to_home_or_with_a_redirect_is_followed(tmp_path, monkeypatch):
    home = tmp_path / "h"
    (home / "sub").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    assert str(home / "sub" / "a.md") in sw.bash_write_targets("cd ~/sub && echo x > a.md", "", tmp_path)
    # A cd to ~ is a cd: walk() does not yield it as a segment that may write.
    assert [seg for seg, _ in segments("cd ~/sub && echo x > a.md", [str(tmp_path)])] == [
        "echo x > a.md"]
    (tmp_path / "d").mkdir()
    for cmd in ("cd d 2>/dev/null && echo x > a.md", "cd d >/dev/null && echo x > a.md",
                # Round 4 (Codex 14.F2, Fable 16.F2): &> and >& forms, and no space before >.
                "cd d &>/dev/null && echo x > a.md", "cd d &>>/dev/null && echo x > a.md",
                "cd d >&/dev/null && echo x > a.md", "cd d>/dev/null && echo x > a.md"):
        assert _hits(cmd, tmp_path, "d/a.md"), cmd


def test_a_subshell_cd_does_not_outlive_the_subshell_but_a_brace_cd_does(tmp_path):
    (tmp_path / "sub").mkdir()
    assert not _hits("(cd sub; true); echo x > a.md", tmp_path, "sub/a.md")
    assert _hits("(cd sub; true); echo x > a.md", tmp_path, "a.md")
    assert _hits("{ cd sub; }; echo x > a.md", tmp_path, "sub/a.md")


def test_deep_nesting_does_not_crash(tmp_path):
    # Deeper than Python's default recursion limit, so it fails without _MAX_DEPTH (Fable 16.F4).
    cmd = "(" * 1500 + "echo x > a.md" + ")" * 1500
    assert isinstance(sw.bash_write_targets(cmd, "", tmp_path), list)
