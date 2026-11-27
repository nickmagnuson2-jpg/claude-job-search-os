"""Tests for tools/launchd/install.sh: the standing set, and arming / disarming a job.

WHAT THIS PROTECTS. A job that is deliberately not running (the mutation sweep, between
baselines) used to keep its plist in tools/launchd/. `install` builds its list from that
folder and the health watchdog builds its expected set from the same folder, so the
installer loaded the job again and the watchdog reported it missing every day, with
advice to run the installer (2026-10-06). The fix is a folder, not a list: a disarmed
plist lives in tools/launchd/disarmed/ and neither reader looks there.

Runs the real script against a throwaway tree with a recording stand-in for launchctl.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "launchd" / "install.sh"
PREFIX = "com.nickmagnuson.jobsearch."

sys.path.insert(0, str(REPO / "tools"))
import check_automation_health as cah  # noqa: E402
import job_quiesce as jq  # noqa: E402


def _tree(tmp_path, standing=("gmail-fetch",), disarmed=("mutation-sweep",)):
    src = tmp_path / "repo" / "tools" / "launchd"
    (src / "disarmed").mkdir(parents=True)
    for short in standing:
        (src / f"{PREFIX}{short}.plist").write_text(f"standing {short}", encoding="utf-8")
    for short in disarmed:
        (src / "disarmed" / f"{PREFIX}{short}.plist").write_text(
            f"disarmed {short}", encoding="utf-8")
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    calls = tmp_path / "launchctl.calls"
    fake = tmp_path / "launchctl"
    fake.write_text(f'#!/bin/bash\necho "$@" >> "{calls}"\n', encoding="utf-8")
    fake.chmod(0o755)
    return src, agents, calls, fake


def _sh(tmp_path, *args):
    src, agents, calls, fake = (tmp_path / "repo" / "tools" / "launchd",
                                tmp_path / "LaunchAgents",
                                tmp_path / "launchctl.calls", tmp_path / "launchctl")
    # LAUNCHD_LOGS_DIR too: install clears stale logs in the main checkout's log folder by
    # default, and a test must never touch the real one (S1, 2026-11-25).
    env = {**os.environ, "LAUNCHD_SOURCE_DIR": str(src), "LAUNCHD_TARGET_DIR": str(agents),
           "LAUNCHCTL": str(fake), "LAUNCHD_LOGS_DIR": str(src / "logs")}
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)
    log = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return r, log


def test_install_leaves_a_disarmed_job_alone(tmp_path):
    src, agents, _, _ = _tree(tmp_path)
    r, log = _sh(tmp_path, "install")
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in agents.iterdir()) == [f"{PREFIX}gmail-fetch.plist"]
    assert not any("mutation-sweep" in line for line in log), log
    assert any(line.startswith("load ") and "gmail-fetch" in line for line in log)


def test_arm_moves_the_plist_into_the_standing_set_and_loads_it(tmp_path):
    src, agents, _, _ = _tree(tmp_path)
    r, log = _sh(tmp_path, "arm", "mutation-sweep")
    assert r.returncode == 0, r.stderr
    name = f"{PREFIX}mutation-sweep.plist"
    assert (src / name).read_text(encoding="utf-8") == "disarmed mutation-sweep"
    assert not (src / "disarmed" / name).exists(), "armed and disarmed copies both exist"
    assert (agents / name).read_text(encoding="utf-8") == "disarmed mutation-sweep"
    assert log[-1] == f"load {agents / name}"


def test_disarm_unloads_removes_and_parks_the_plist(tmp_path):
    src, agents, _, _ = _tree(tmp_path, standing=("gmail-fetch", "mutation-sweep"),
                              disarmed=())
    name = f"{PREFIX}mutation-sweep.plist"
    (agents / name).write_text("installed", encoding="utf-8")
    r, log = _sh(tmp_path, "disarm", "mutation-sweep")
    assert r.returncode == 0, r.stderr
    assert log == [f"unload {agents / name}"]
    assert not (agents / name).exists(), "the installed copy is what launchd reads"
    assert not (src / name).exists()
    assert (src / "disarmed" / name).read_text(encoding="utf-8") == "standing mutation-sweep"
    # ...and the installer no longer sees it.
    r, log = _sh(tmp_path, "install")
    assert not (agents / name).exists()


def test_arm_then_disarm_round_trips(tmp_path):
    src, agents, _, _ = _tree(tmp_path)
    name = f"{PREFIX}mutation-sweep.plist"
    assert _sh(tmp_path, "arm", "mutation-sweep")[0].returncode == 0
    assert _sh(tmp_path, "disarm", "mutation-sweep")[0].returncode == 0
    assert (src / "disarmed" / name).read_text(encoding="utf-8") == "disarmed mutation-sweep"
    assert not (src / name).exists() and not (agents / name).exists()


def test_arming_an_unknown_job_fails_and_loads_nothing(tmp_path):
    _tree(tmp_path)
    r, log = _sh(tmp_path, "arm", "no-such-job")
    assert r.returncode == 1
    assert "no-such-job" in r.stderr
    assert log == []


def test_arm_and_disarm_need_a_job_name(tmp_path):
    _tree(tmp_path)
    for verb in ("arm", "disarm"):
        r, log = _sh(tmp_path, verb)
        assert r.returncode == 1 and "Usage" in r.stderr
        assert log == []


def test_disarming_the_last_standing_job_is_allowed(tmp_path):
    """arm / disarm must not go through the empty-standing-set refusal."""
    src, agents, _, _ = _tree(tmp_path, standing=("mutation-sweep",), disarmed=())
    r, _ = _sh(tmp_path, "disarm", "mutation-sweep")
    assert r.returncode == 0, r.stderr
    r, _ = _sh(tmp_path, "install")
    assert r.returncode == 1 and "empty list" in r.stderr


def test_the_watchdog_does_not_expect_a_disarmed_job(tmp_path):
    """Same folder rule, second reader: an unloaded disarmed job is not a fault."""
    _tree(tmp_path)
    assert cah.expected_jobs(tmp_path / "repo") == {"gmail-fetch"}


def test_a_sweep_does_not_quiesce_or_restore_a_disarmed_job(tmp_path):
    """Third reader. Restoring a job nobody loaded would arm it behind Nick's back."""
    _tree(tmp_path, standing=("gmail-fetch",), disarmed=("career-scan",))
    assert jq.quiescible_labels(tmp_path / "repo") == [f"{PREFIX}gmail-fetch"]


def test_the_real_tree_has_no_job_in_both_folders():
    live = REPO / "tools" / "launchd"
    both = sorted({p.name for p in live.glob("*.plist")}
                  & {p.name for p in (live / "disarmed").glob("*.plist")})
    assert not both, f"plist(s) both standing and disarmed: {both}"


# ---------------------------------------------------------------- rendering an awkward path
# Parked P1s 1470.F2 / 1473.F1-F2 / 1475.F1 / 1475.F3 (s1c reviews, 2026-11-25), fixed at the
# source on 11-26: three -c jobs left __REPO_ROOT__ unquoted, so a checkout path with a space
# word-split the python call; and the sed renderer wrote '&' / '#' / '<' unescaped, so '&'
# rewrote the path, '#' aborted the install and '<' broke the plist's XML.
import plistlib  # noqa: E402
import shlex  # noqa: E402
import shutil  # noqa: E402

import pytest  # noqa: E402

AWKWARD = "/private/tmp/s3 render q&a #1 <x>"
# One path per defect, so each fails red on its own: the space alone (word split), '&' alone
# (sed rewrites it to the matched token, silently), and all of them together.
PATHS = ("/private/tmp/s3 render space", "/private/tmp/s3render&amp", AWKWARD)
SHELL_FLAGS = ("-c", "-lc")


def _real_templates(src):
    """Every tracked plist, standing and disarmed, copied flat into a throwaway source dir."""
    real = REPO / "tools" / "launchd"
    names = []
    for p in sorted(real.glob(f"{PREFIX}*.plist")) + sorted((real / "disarmed").glob(f"{PREFIX}*.plist")):
        shutil.copy(p, src / p.name)
        names.append(p.name)
    return names


def _render_all(tmp_path, repo_root):
    src, agents, _, _ = _tree(tmp_path, standing=(), disarmed=())
    names = _real_templates(src)
    env_extra = {"LAUNCHD_REPO_ROOT": repo_root,
                 "LAUNCHD_CLAUDE_PROJECT_DIR": "/private/tmp/s3 proj&dir/-slug"}
    old = dict(os.environ)
    os.environ.update(env_extra)
    try:
        r, log = _sh(tmp_path, "install")
    finally:
        os.environ.clear()
        os.environ.update(old)
    return src, agents, names, r, log


@pytest.mark.parametrize("path", PATHS)
def test_every_template_renders_to_valid_xml_for_an_awkward_path(tmp_path, path):
    src, agents, names, r, _ = _render_all(tmp_path, path)
    assert r.returncode == 0, r.stderr
    for name in names:
        rendered = plistlib.loads((agents / name).read_bytes())   # raises on bad XML
        assert path in repr(rendered) or "__REPO_ROOT__" not in (src / name).read_text(), name


@pytest.mark.parametrize("path", PATHS)
def test_every_shell_command_keeps_the_repo_path_as_one_word(tmp_path, path):
    src, agents, names, r, _ = _render_all(tmp_path, path)
    assert r.returncode == 0, r.stderr
    checked = 0
    for name in names:
        template = plistlib.loads((src / name).read_bytes())["ProgramArguments"]
        rendered = plistlib.loads((agents / name).read_bytes())["ProgramArguments"]
        for i, arg in enumerate(template):
            if i == 0 or template[i - 1] not in SHELL_FLAGS or "__REPO_ROOT__" not in arg:
                continue
            words = shlex.split(rendered[i])
            whole = [w for w in words if w == path or w.startswith(path + "/")]
            assert len(whole) == arg.count("__REPO_ROOT__"), (name, words)
            checked += 1
    assert checked >= 6, checked   # 4 standing + 2 disarmed -c jobs name the repo root


@pytest.mark.parametrize("path", PATHS)
def test_non_shell_arguments_carry_the_path_verbatim(tmp_path, path):
    src, agents, names, r, _ = _render_all(tmp_path, path)
    assert r.returncode == 0, r.stderr
    for name in names:
        template = plistlib.loads((src / name).read_bytes())
        rendered = plistlib.loads((agents / name).read_bytes())
        for key in ("StandardOutPath", "StandardErrorPath", "WorkingDirectory"):
            if key in template:
                assert rendered[key] == template[key].replace("__REPO_ROOT__", path), (name, key)


def test_install_refuses_a_repo_path_it_cannot_quote_and_loads_nothing(tmp_path):
    for bad in ('/private/tmp/a"b', "/private/tmp/a$b", "/private/tmp/a`b", "/private/tmp/a\\b"):
        sub = tmp_path / str(abs(hash(bad)))
        sub.mkdir()
        _, agents, _, r, log = _render_all(sub, bad)
        assert r.returncode != 0, bad
        assert "cannot be quoted" in r.stderr, r.stderr
        assert list(agents.iterdir()) == [], bad
        assert not any(line.startswith("load ") for line in log), log


def test_a_scratch_source_without_a_logs_override_never_clears_the_repo_logs(tmp_path):
    # Parked P1 1475.F2 (s1c, 2026-11-25), fixed 2026-11-26: LOGS_DIR defaulted to the
    # repo's tools/launchd/logs even when LAUNCHD_SOURCE_DIR pointed at a scratch tree, so a
    # scratch install without LAUNCHD_LOGS_DIR deleted the live jobs' logs.
    src, agents, calls, fake = _tree(tmp_path)
    repo = tmp_path / "fake-main"
    live = repo / "tools" / "launchd" / "logs"
    live.mkdir(parents=True)
    (live / "gmail-fetch.log").write_text("live", encoding="utf-8")
    (src / "logs").mkdir()
    (src / "logs" / "gmail-fetch.log").write_text("stale", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "LAUNCHD_LOGS_DIR"}
    env.update({"LAUNCHD_SOURCE_DIR": str(src), "LAUNCHD_TARGET_DIR": str(agents),
                "LAUNCHCTL": str(fake), "LAUNCHD_REPO_ROOT": str(repo),
                "LAUNCHD_CLAUDE_PROJECT_DIR": str(tmp_path / "proj")})
    r = subprocess.run(["bash", str(SCRIPT), "install"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert (live / "gmail-fetch.log").read_text(encoding="utf-8") == "live"
    assert not (src / "logs" / "gmail-fetch.log").exists(), "the scratch tree's own stale log stays"


# ------------------------------------------------- 1498.F1-F3 (Codex s1c r3, fixed 2026-11-26)
def _install_env(tmp_path, repo, project, logs=None):
    """A scratch install's environment; LAUNCHD_LOGS_DIR only when `logs` is given."""
    src, agents, calls, fake = _tree(tmp_path, standing=(), disarmed=())
    _real_templates(src)
    env = {k: v for k, v in os.environ.items() if k != "LAUNCHD_LOGS_DIR"}
    env.update({"LAUNCHD_SOURCE_DIR": str(src), "LAUNCHD_TARGET_DIR": str(agents),
                "LAUNCHCTL": str(fake), "LAUNCHD_REPO_ROOT": repo,
                "LAUNCHD_CLAUDE_PROJECT_DIR": project})
    if logs is not None:
        env["LAUNCHD_LOGS_DIR"] = logs
    return src, agents, calls, env


def _run(env):
    return subprocess.run(["bash", str(SCRIPT), "install"], capture_output=True, text=True,
                          env=env)


@pytest.mark.parametrize("ch", ["\r", "\t", "\x01", "\x7f"])
def test_a_control_character_in_any_value_is_refused_before_anything_loads(tmp_path, ch):
    # 1498.F1: a CR passed the refusal, and XML parsing turns CR into LF, so launchd ran a
    # path that was not the checkout. Every control character is refused, in each value.
    for i, (repo, project) in enumerate([(f"/private/tmp/re{ch}po", "/private/tmp/proj"),
                                         ("/private/tmp/repo", f"/private/tmp/pr{ch}oj/p"),
                                         ("/private/tmp/repo", f"/private/tmp/p/pr{ch}oj")]):
        sub = tmp_path / str(i)
        sub.mkdir()
        _, agents, calls, env = _install_env(sub, repo, project)
        r = _run(env)
        assert r.returncode != 0, (repo, project)
        assert "control character" in r.stderr, r.stderr
        assert list(agents.iterdir()) == []
        assert not calls.exists() or "load " not in calls.read_text(encoding="utf-8")


def test_every_rendered_value_survives_the_plist_parse_unchanged(tmp_path):
    # 1498.F1, read the way launchd reads it: parse the installed plist, compare to the input.
    repo, project = "/private/tmp/s3 q&a <x> #1", "/private/tmp/s3 p&q/<slug>"
    src, agents, _, env = _install_env(tmp_path, repo, project)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    values = {"__REPO_ROOT__": repo, "__CLAUDE_PROJECT_DIR__": project,
              "__CLAUDE_PROJECT_SLUG__": os.path.basename(project)}

    def fill(v):
        if isinstance(v, str):
            for k, x in values.items():
                v = v.replace(k, x)
            return v
        if isinstance(v, list):
            return [fill(x) for x in v]
        if isinstance(v, dict):
            return {k: fill(x) for k, x in v.items()}
        return v
    names = sorted(p.name for p in src.glob(f"{PREFIX}*.plist"))
    assert names
    for name in names:
        assert plistlib.loads((agents / name).read_bytes()) == fill(
            plistlib.loads((src / name).read_bytes())), name


def test_a_shell_character_in_a_parent_of_the_project_dir_is_allowed(tmp_path):
    # 1498.F2: only the project folder's NAME reaches a shell command; the full path is a
    # plain plist string, so '$' or '"' in a parent directory is safe and was refused.
    for i, parent in enumerate(["par$cash", 'par"q', "par`b", "par\\s"]):
        sub = tmp_path / str(i)
        sub.mkdir()
        project = f"/private/tmp/{parent}/project"
        _, agents, _, env = _install_env(sub, "/private/tmp/repo", project)
        r = _run(env)
        assert r.returncode == 0, (parent, r.stderr)
        assert any(project in s for p in agents.iterdir()
                   for s in _strings(plistlib.loads(p.read_bytes()))), parent


def _strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)


def test_a_shell_character_in_the_project_name_is_still_refused(tmp_path):
    _, agents, _, env = _install_env(tmp_path, "/private/tmp/repo", "/private/tmp/p/pro$j")
    r = _run(env)
    assert r.returncode != 0
    assert "cannot be quoted" in r.stderr, r.stderr
    assert list(agents.iterdir()) == []


def test_a_symlinked_scratch_logs_dir_is_refused_and_deletes_nothing(tmp_path):
    # 1498.F3: the scratch default SOURCE_DIR/logs was lexical, and rm follows a symlinked
    # directory, so a logs/ link to another tree deleted that tree's live log.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    src, agents, calls, env = _install_env(tmp_path, str(tmp_path / "fake-main"),
                                           str(tmp_path / "proj"))
    names = sorted(p.name for p in src.glob(f"{PREFIX}*.plist"))
    for name in names:
        short = name[len(PREFIX):-len(".plist")]
        (outside / f"{short}.log").write_text("live", encoding="utf-8")
    (src / "logs").symlink_to(outside, target_is_directory=True)
    r = _run(env)
    assert r.returncode != 0
    assert "outside the scratch source" in r.stderr, r.stderr
    assert all((outside / f"{n[len(PREFIX):-len('.plist')]}.log").exists() for n in names)
    assert list(agents.iterdir()) == []


def test_an_explicit_scratch_logs_dir_outside_the_source_is_refused(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "keep.log").write_text("live", encoding="utf-8")
    src, agents, _, env = _install_env(tmp_path, str(tmp_path / "fake-main"),
                                       str(tmp_path / "proj"), logs=str(outside))
    r = _run(env)
    assert r.returncode != 0
    assert "outside the scratch source" in r.stderr, r.stderr
    assert list(agents.iterdir()) == []


def test_a_scratch_logs_dir_inside_the_source_still_installs(tmp_path):
    src, agents, _, env = _install_env(tmp_path, str(tmp_path / "fake-main"),
                                       str(tmp_path / "proj"))
    (src / "logs").mkdir()
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in agents.iterdir()) == sorted(
        p.name for p in src.glob(f"{PREFIX}*.plist"))
