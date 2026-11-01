"""One "did they reply" rule for networking_write.py, remember_apply.py and day_check.py.

The bug class: the reply decision lived in networking_write.py as a bare-word list with a
negation regex cut out first, and day_check.py kept a second, different version. Review
rounds 4-6 of the 2026-10-29 integration each found one more phrasing ("still waiting for
a reply", "nobody has replied", "won't be replying") that the negation missed, and one copy
was fixed while the other was not. The class fix: tools/reply_classify.py, no bare-word
fallback, and these tests.

  1. SINGLE SOURCE OF TRUTH - both consumers import reply_classify and no tool other than
     reply_classify.py defines the reply vocabulary.
  2. CROSS-TOOL PARITY - for the same logged note, networking_write flips the outreach log
     exactly when day_check counts the note as a reply.
  3. THE CONTRACT (2026-10-30) - only the recorded direction in the type counts. The
     summary is not even an argument, so no phrasing can make a note a reply.
"""
import json
import os
import re
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, TOOLS_DIR, write_fixture

sys.path.insert(0, str(TOOLS_DIR))
import day_check as dc  # noqa: E402
import reply_classify as rc  # noqa: E402

CONSUMERS = {"networking_write.py": "from reply_classify import",
             "day_check.py": "from reply_classify import",
             "remember_apply.py": "from reply_classify import"}

# Local definitions that were the two drifting copies. None may reappear outside
# reply_classify.py, in any tool.
_BANNED_LOCAL_DEFS = [
    re.compile(r"^\s*REPLY_SIGNALS\s*=", re.M),
    re.compile(r"^\s*REPLY_NEGATION\s*=", re.M),
    re.compile(r"^\s*INBOUND_SUBJECT_MARKERS\s*=", re.M),
    re.compile(r"^\s*OUTBOUND_REPLY_MARKERS\s*=", re.M),
    re.compile(r"^\s*OTHER_REPLIED\s*=", re.M),
    re.compile(r"^\s*NEGATED\s*=", re.M),
    re.compile(r"^\s*def\s+reply_received\b", re.M),
    re.compile(r"^\s*def\s+classify_summary\b", re.M),
    re.compile(r"^\s*REPLY_KEYWORDS\s*=", re.M),
]

# Reply-detection phrases. One of them alone in a collection is a status value ("Replied" is
# an outreach-log Status); two or more is a classifier copy, whatever it is named (1.F2).
_REPLY_PHRASES = {"replied", "responded", "heard back", "heard back from", "wrote back",
                  "got back", "got back to me", "reply from", "response from", "got a reply",
                  "called back", "texted back"}


# ---------------------------------------------------------------------------
# 1. Single source of truth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fname", sorted(CONSUMERS))
def test_consumer_imports_the_shared_classifier(fname):
    src = (TOOLS_DIR / fname).read_text(encoding="utf-8")
    assert CONSUMERS[fname] in src, f"{fname} must decide replies via tools/reply_classify.py"


def test_no_tool_keeps_its_own_reply_vocabulary():
    offenders = []
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.name == "reply_classify.py":
            continue
        src = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {p.pattern}" for p in _BANNED_LOCAL_DEFS if p.search(src)]
    assert not offenders, offenders


def test_no_tool_holds_a_renamed_reply_vocabulary():
    """Review 1.F2: the guard above bans old NAMES only, so a copy under a new name passed."""
    import ast
    offenders = []
    # reply_classify.py is scanned too: since 2026-10-30 it holds no reply vocabulary.
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            value = getattr(node, "value", None)
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or value is None:
                continue
            words = {n.value.lower() for n in ast.walk(value)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            if len(words & _REPLY_PHRASES) >= 2:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


def test_the_phrase_scan_would_catch_a_renamed_copy():
    import ast
    tree = ast.parse('MY_WORDS = ("replied", "heard back")\nSTATUSES = ("Replied", "Sent")\n')
    hits = [n.lineno for n in tree.body
            if len({c.value.lower() for c in ast.walk(n.value) if isinstance(c, ast.Constant)}
                   & _REPLY_PHRASES) >= 2]
    assert hits == [1]


def test_day_check_uses_the_shared_function_itself():
    assert dc.classify is rc.classify


# ---------------------------------------------------------------------------
# 2. Cross-tool parity, end to end through both CLIs' code paths
# ---------------------------------------------------------------------------

NETWORKING = """\
# Networking

## Contacts

| Name | Company | Role | Relationship | Added | Last Interaction | Email |
| --- | --- | --- | --- | --- | --- | --- |
| Jane Doe | Acme Corp | PM | peer | 2026-01-01 | — | — |

## Interaction Log

### Jane Doe — Acme Corp

"""

OUTREACH = """\
# Outreach Log

| Date | Skill | Channel | Recipient | Company | Subject / Summary | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-18 | follow-up | email | Jane Doe | Acme Corp | Re: intro | Sent |
"""

PARITY_CASES = [
    ("email", "Jane replied 2:00 PM with times"),
    ("email", "INBOUND 9:12 AM: asked for a call"),
    ("email", "Reply from Jane: yes to Tuesday"),
    ("email (inbound)", "Asked for a resume"),
    ("email", "Replied to her intro - yes to a chat"),
    ("email", "Follow-up #1 SENT, reply in the old thread"),
    ("email", "Jane won't be replying until next week"),
    ("email", "still waiting for a reply"),
    ("email", "nobody has replied"),
    ("email", "no worries, replied yesterday"),
    ("email", "2:53 PM she emailed the next step: a video call"),
    ("note", "Jane texted the Acme founder on Friday"),
    ("text", "Jane texted Lee on Friday"),
]


@pytest.mark.parametrize("direction,want", [("in", "reply"), ("out", "send"), ("both", "both")])
@pytest.mark.parametrize("kind,summary", PARITY_CASES)
def test_writer_flips_exactly_when_day_check_counts_a_reply(tmp_path, kind, summary,
                                                            direction, want):
    """The caller's --direction decides both: the writer's flip and day_check's verdict,
    whatever the summary says."""
    if rc.direction(kind) not in (None, direction):
        pytest.skip("the writer refuses a type that contradicts --direction")
    write_fixture(tmp_path, "data/networking.md", NETWORKING)
    write_fixture(tmp_path, "data/outreach-log.md", OUTREACH)
    r = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "networking_write.py"), "--repo-root", str(tmp_path),
         "log", "Jane Doe", "--date", "2026-06-19", "--type", kind, "--summary", summary,
         "--direction", direction, "--no-todo"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr
    flipped = json.loads(r.stdout)["outreach_log_updated"]
    events = dc.networking_events((tmp_path / "data/networking.md").read_text(encoding="utf-8"))
    assert len(events) == 1
    assert events[0]["direction"] == want, (summary, events[0])
    assert flipped is (want in ("reply", "both")), (summary, flipped)


# ---------------------------------------------------------------------------
# 2b. The recorded direction: one place reads and writes the type suffix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,want", [
    ("email (inbound)", "in"), ("Email (Inbound)", "in"), ("inbound", "in"),
    ("text (inbound) ", "in"), ("email (outbound)", "out"), ("LinkedIn (outbound)", "out"),
    ("email (two-way)", "both"), ("Text (Two-Way)", "both"), ("email (twoway)", None),
    ("email", None), ("", None), (None, None), ("email-sent", None),
    ("inbound call", None), ("call (scheduled)", None),
])
def test_direction_reads_only_the_suffix(kind, want):
    assert rc.direction(kind) == want


@pytest.mark.parametrize("kind,want", [
    ("email (inbound)", "email"), ("LinkedIn (outbound)", "LinkedIn"), ("email", "email"),
    ("inbound", "inbound"), ("call (scheduled)", "call (scheduled)"), ("", ""), (None, ""),
    ("text (two-way)", "text"),
])
def test_base_type_drops_only_the_direction_suffix(kind, want):
    assert rc.base_type(kind) == want


@pytest.mark.parametrize("kind,d,want", [
    ("email", "in", "email (inbound)"), ("email", "out", "email (outbound)"),
    ("email", "none", "email"), ("LinkedIn", "in", "LinkedIn (inbound)"),
    ("email (inbound)", "in", "email (inbound)"), ("inbound", "in", "inbound"),
    ("email (outbound)", "out", "email (outbound)"),
    ("email", "both", "email (two-way)"), ("email (two-way)", "both", "email (two-way)"),
])
def test_with_direction_adds_one_suffix_at_most(kind, d, want):
    assert rc.with_direction(kind, d) == want


@pytest.mark.parametrize("kind,d", [
    ("email (inbound)", "out"), ("email (inbound)", "none"), ("inbound", "out"),
    ("email (outbound)", "in"), ("email (outbound)", "none"), ("email", "sideways"),
    ("email (two-way)", "in"), ("email (two-way)", "none"), ("email (inbound)", "both"),
])
def test_with_direction_refuses_a_contradiction(kind, d):
    with pytest.raises(ValueError):
        rc.with_direction(kind, d)


# ---------------------------------------------------------------------------
# 3. The contract: the type decides, the summary cannot
# ---------------------------------------------------------------------------

def test_the_classifier_takes_no_summary():
    import inspect
    for fn in (rc.classify, rc.is_reply, rc.is_send):
        assert list(inspect.signature(fn).parameters) == ["kind"], fn.__name__


@pytest.mark.parametrize("kind,want", [
    ("email (inbound)", "reply"), ("call (inbound)", "reply"), ("note (inbound)", "reply"),
    ("inbound", "reply"), ("LinkedIn (Inbound)", "reply"),
    ("email (outbound)", "send"), ("text (outbound)", "send"),
    ("email (two-way)", "both"), ("call (two-way)", "both"),
    ("email", None), ("note", None), ("", None), (None, None), ("email-sent", None),
    ("inbound call", None), ("email (inbound) follow-up", None),
])
def test_only_the_recorded_direction_counts(kind, want):
    assert rc.classify(kind) == want
    assert rc.is_reply(kind) is (want in ("reply", "both"))
    assert rc.is_send(kind) is (want in ("send", "both"))


def test_two_way_is_both_a_reply_and_a_send():
    """Nick, 2026-10-30 13:42: one note can record a message each way."""
    assert rc.REPLY_VERDICTS == {"reply", "both"} and rc.SEND_VERDICTS == {"send", "both"}


# Every phrasing the old summary rules counted as a reply or a send. Written with a plain
# type, none of them counts now: the direction must be recorded.
@pytest.mark.parametrize("summary", [
    "Jane replied 2:00 PM with times", "INBOUND 9:12 AM: asked for a call",
    "Reply from Jane: yes to Tuesday", "Follow-up #1 SENT", "Cold outreach after applying",
    "Nick replied with times", "2:53 PM she emailed the next step",
])
def test_a_plain_type_is_never_counted_whatever_the_summary(tmp_path, summary):
    text = NETWORKING + f"#### 2026-06-19 | email | {summary}\n"
    assert [e["direction"] for e in dc.networking_events(text)] == [None]


# ---------------------------------------------------------------------------
# 4. Writers outside networking_write.py (review round 2, 2026-10-31)
# ---------------------------------------------------------------------------

SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
# A heading TEMPLATE a skill tells the model to write by hand: a placeholder date.
_TEMPLATE_HEADING = re.compile(r"^\s*#### YYYY-MM-DD \| ([^|]+?) \|", re.M)


# The ONE stated exception (job-search-47 for Nick, 2026-10-31, review rounds 3-4): the
# old meaning. The old summary rules gave a /draft-email entry (thank-you, status update,
# intro request) no verdict, while /follow-up and /cold-outreach entries ("Follow-up #N",
# "Cold outreach") counted as sends. So /draft-email stays plain and the other two record
# (outbound). The timing is NOT the reason: all three log when the draft opens.
PLAIN_TEMPLATE_EXCEPTIONS = {"draft-email": "keep the old meaning: not counted before 2026-10-30"}


def _templates():
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        for m in _TEMPLATE_HEADING.finditer(path.read_text(encoding="utf-8")):
            yield path.parent.name, m.group(1)


def test_every_hand_written_heading_template_records_a_direction():
    """/follow-up and /cold-outreach write their own interaction heading. A plain type there
    makes every send they log count as nothing once the summary is not read (Fable round
    2, F1-F2). Every such template must carry a direction suffix, except the stated ones."""
    found = list(_templates())
    plain = [f"{skill}: {kind!r}" for skill, kind in found
             if rc.direction(kind) is None and skill not in PLAIN_TEMPLATE_EXCEPTIONS]
    assert not plain, plain
    assert {"follow-up", "cold-outreach", "draft-email"} <= {skill for skill, _ in found}


def test_the_draft_email_template_stays_plain_and_says_why():
    """The exception is pinned both ways: /draft-email's template must NOT record a
    direction, and the step that holds it must give the reason (the old meaning) right
    before the template, so nobody 'fixes' it back without seeing why."""
    kinds = [kind for skill, kind in _templates() if skill == "draft-email"]
    assert kinds and all(rc.direction(k) is None for k in kinds), kinds
    text = (SKILLS_DIR / "draft-email" / "SKILL.md").read_text(encoding="utf-8")
    m = _TEMPLATE_HEADING.search(text)
    reason = text[max(0, m.start() - 700):m.start()].lower()
    assert "keep the old meaning" in reason and "/follow-up" in reason, reason[-300:]
    assert set(PLAIN_TEMPLATE_EXCEPTIONS) == {"draft-email"}


def test_the_template_scan_would_catch_a_plain_type():
    assert [m.group(1) for m in _TEMPLATE_HEADING.finditer(
        "#### YYYY-MM-DD | [email/linkedin] | Cold outreach — x\n")] == ["[email/linkedin]"]
    assert rc.direction("[email/linkedin]") is None
    assert rc.direction("[email/linkedin] (outbound)") == "out"
