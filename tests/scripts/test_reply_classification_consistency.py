"""One "did they reply" rule for networking_write.py and day_check.py.

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
  3. THE CONTRACT - positive structured signals count; everything else, including every
     negated phrasing the old rule leaked, does not.
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
             "day_check.py": "from reply_classify import"}

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
]


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


def test_day_check_uses_the_shared_function_itself():
    assert dc.classify_summary is rc.classify


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
]


@pytest.mark.parametrize("kind,summary", PARITY_CASES)
def test_writer_flips_exactly_when_day_check_counts_a_reply(tmp_path, kind, summary):
    write_fixture(tmp_path, "data/networking.md", NETWORKING)
    write_fixture(tmp_path, "data/outreach-log.md", OUTREACH)
    r = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "networking_write.py"), "--repo-root", str(tmp_path),
         "log", "Jane Doe", "--date", "2026-06-19", "--type", kind, "--summary", summary,
         "--no-todo"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr
    flipped = json.loads(r.stdout)["outreach_log_updated"]
    events = dc.networking_events((tmp_path / "data/networking.md").read_text(encoding="utf-8"))
    assert len(events) == 1
    assert flipped is (events[0]["direction"] == "reply"), (summary, flipped, events[0])


# ---------------------------------------------------------------------------
# 3. The contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,summary", [
    ("email (inbound)", "No reply needed, she wrote first"),
    ("inbound", "anything"),
    ("email", "INBOUND 4:46am, a bump on a prior unanswered message"),
    ("email", "Inbound 8:55am, replying to the question: no reply needed"),
    ("email", "Backfilled. INBOUND 2:06 PM from the thread."),
    ("email", "REPLY from Sam 9:19 AM: confirmed."),
    ("email", "Reply received: Sam passed."),
    ("email", "Heard back from Sam: yes to Tuesday"),
    ("email", "Follow-up sent. Heard back from Sam an hour later."),
    ("email", "Got a response from Sam."),
    ("email", "Sam replied 2:00 PM."),
    ("email", "Sam REPLIED 2:00 PM."),
    ("email", "She replied yesterday"),
    ("email", "not interested, she replied"),
    ("email", "She replied, hasn't heard from the HM"),
    ("email", "Sam replied, no worries"),
    ("email", "She replied to my email with availability"),
    ("email", "IMMEDIATE RESPONSE: Sam replied minutes after the cold email."),
    ("email", "Sam responded to the comment."),
    ("text", "Sam texted back within the hour."),
    ("email", "Sam (Talent @ Acme) replied 10:58am with a booking link."),
    ("email", "Brant replied."),
    ("email", "Kent responded at noon."),
    ("email", "Re: Draft post - Sam replied 2:00 PM (cc Lee)."),
    ("email", "Nick replied 1:00 PM; Sam replied 2:00 PM."),
])
def test_positive_structured_signals_are_replies(kind, summary):
    assert rc.classify(kind, summary) == "reply"
    assert rc.is_reply(kind, summary) is True


@pytest.mark.parametrize("summary", [
    # Every phrasing that leaked through the old negation, rounds 1-6 of 2026-10-29.
    "I haven't heard back", "haven't heard back from her", "I havent heard back",
    "I haven’t heard back", "she hadn't responded", "she hasn’t responded",
    "still waiting to hear back", "Sam never replied", "Sam never replied after the intro.",
    "No reply from Sam yet", "No reply yet.", "Nobody responded to the post.",
    "Nobody replied", "Still have not heard back from the team.", "Sam has yet to respond.",
    "she won't reply", "haven't got a reply", "never got a reply from Sam",
    "didn't get a response", "has not received a reply", "still waiting for a reply",
    "Sent cold text. Awaiting reply.", "waiting on a response from Sam",
    "still waiting on her reply", "nobody has replied", "nobody has heard back",
    "no one has ever responded", "not the reply I hoped for",
    "Sam won't be replying until next week",  # the round-6 P0 (895.F1)
    # Review 1 of the class fix (Grok F1): a capitalized auxiliary is not a subject.
    "Hasn't replied.", "Haven’t replied.", "Hadn't responded.", "Hasnt replied.",
    "Follow-up #1 SENT. Hasn't replied.", "Emailed Monday. Didn't respond.",
    "Has replied?", "Still waiting. Haven't heard back.",
    # Nick's own replies.
    "Replied to her intro - yes to a chat", "I replied with times.",
    "Reply SENT 10:55am in the thread.", "Follow-up #2 sent: reply in the thread.",
    # No subject: unsure, so not a reply (--reply-received is the override).
    "no worries, replied yesterday", "Replied 11:08 AM with the status.",
    # Conditions and questions.
    "Ask whether Sam replied to the intro.", "Check if she responded.",
    # Possessive or noun forms are not structured signals.
    "Full text of Sam's reply, captured later.",
])
def test_everything_else_is_not_a_reply(summary):
    assert rc.classify("email", summary) != "reply"
    assert rc.is_reply("email", summary) is False


@pytest.mark.parametrize("kind,summary,want", [
    ("email", "Nick replied 2:00 PM.", "send"),
    ("email", "Follow-up #2 SENT unchanged.", "send"),
    ("text", "OUTBOUND text: a nudge.", "send"),
    ("email", "Cold outreach after applying.", "send"),
    ("email", "Follow-up #2 SENT; no reply from Sam to #1.", "send"),
    ("email", "she sent the deck.", None),
    ("note", "context only", None),
    ("email", "", None),
    ("email", None, None),
])
def test_sends_and_nothing(kind, summary, want):
    assert rc.classify(kind, summary) == want


def test_a_missing_kind_is_not_an_error():
    assert rc.classify(None, "Sam replied") == "reply"
    assert rc.classify(None, "context") is None
