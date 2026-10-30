#!/usr/bin/env python3
"""reply_classify.py - the ONE rule for "did the contact reply?" in a networking note.

Used by:
  - tools/networking_write.py `log`: flips the outreach-log row to Replied only when
    is_reply() is true (the caller's --reply-received / --no-reply-flip still override).
  - tools/day_check.py: counts replies and sends from data/networking.md.

No other tool may keep its own copy of these rules
(tests/scripts/test_reply_classification_consistency.py enforces that).

Why it is built this way. The old rule was a bare-word list ("reply", "replied",
"heard back", ...) with a negation regex cut out first. Every review round found one more
English phrasing that the negation missed ("still waiting for a reply", "nobody has replied",
"won't be replying"), so the list never converged. This module has NO bare-word fallback:
a note counts as a reply only on one of the positive, structured signals below. Anything
else is "not a reply". A missed reply costs one manual `--reply-received`; a false one
writes Replied into the outreach log, which is the worse error.

What counts as a reply (any one):
  1. The interaction type says inbound: "email (inbound)" or "inbound".
  2. The note opens with an inbound marker: "INBOUND", "Inbound", "REPLY from",
     "Reply from", "Response from", "Reply received", "Heard back from",
     "Got a reply from", "Got a response from". "Opens" means the start of the note or
     the start of a sentence in it (after ". ", "! ", "? "), so no negator can sit in
     front ("No reply from Sam" does not match).
  3. The uppercase word INBOUND anywhere (the house convention for logging an inbound
     message inside a longer note: "Backfilled. INBOUND 2:06 PM from the thread").
  4. A contact as the grammatical subject, directly followed by a reply verb: a
     capitalized name or she/he/they, then replied / responded / wrote back /
     called back / texted back / got back. The subject must be the word right before the
     verb (one parenthetical between them is allowed: "Sam (recruiter) replied"), so
     "Sam never replied" and "she hasn't responded" do not match. Never counted:
     Nick or I as the subject, a negative word or an auxiliary as the subject ("Nobody
     replied", "Hasn't replied."), or the form inside a condition ("if Sam replied",
     "whether she responded"). Known gap, accepted: a negation earlier in the clause
     ("I don't think Sam replied") is not seen; none occurs in the live log.
  5. The same subject form with a message verb (emailed / texted / messaged), held
     stricter (Nick, 2026-10-30: "add the reply verbs but keep it strict"). It counts only
     when the interaction type is a message channel (MESSAGE_KINDS), because a note or
     call that reports "Lee texted the founder" is not a message to Nick. What follows
     the verb is an ALLOWLIST, not a list of recipients to reject (review 1, Codex F1:
     a reject list missed "texted the recruiter", "emailed us", "the deck to Robin"):
     the next word must make Nick the recipient (you / me / Nick / back) or be a time
     (a digit, at / on / after / today / a weekday ...), or the words are "the next
     step(s)". Anything else is not counted. Even then, the clause (up to ; . or :)
     must carry no third-party marker: cc, copied, forwarded, group, or "to <Name>".
     Rule 5 never overrides a send (review 1, Codex F2): it is checked after the send
     signals, so the verdicts rules 1-4 and the send rules gave are unchanged.
     "she emailed the next step", "Jo texted 1:20 PM", "Sam emailed you" count.
     Accepted misses: "Jo texted an Acme list", "Sam proactively texted".

What counts as a send (only when no reply signal matched): the uppercase word SENT, or a
note opening "Nick replied", "Nick sent", "Outbound", "Follow-up #<n>", "Cold outreach".

Everything else is None: not counted either way. When unsure, the answer is "not a reply".
"""
from __future__ import annotations

import re

# 1. Interaction types that are inbound by definition.
INBOUND_KINDS = frozenset({"email (inbound)", "inbound"})

# 2. Inbound markers that must open the note or a sentence in it.
_SENTENCE_START = r"(?:^|[.!?]\s+)\s*"
INBOUND_OPENER = re.compile(
    _SENTENCE_START
    + r"(?:inbound|reply from|response from|reply received|heard back from"
      r"|got a (?:reply|response) from)\b",
    re.IGNORECASE)

# 3. The uppercase house marker, anywhere.
INBOUND_UPPER = re.compile(r"\bINBOUND\b")

# 4. Contact-as-subject reply form.
_REPLY_VERB = r"(?:replied|responded|wrote back|called back|texted back|got back)"
SUBJECT_REPLY = re.compile(
    r"(?<![\w'’-])"
    r"(?P<subj>[A-Z][\w'’-]*|she|he|they)(?:\s+\([^()]{1,60}\))?\s+" + _REPLY_VERB + r"\b",
    re.IGNORECASE)
# 5. Message verbs: the same subject slot, plus the word after the verb for the recipient
#    check, and only on a message channel.
SUBJECT_MESSAGE = re.compile(
    r"(?<![\w'’-])"
    r"(?P<subj>[A-Z][\w'’-]*|she|he|they)(?:\s+\([^()]{1,60}\))?\s+(?:emailed|texted|messaged)\b"
    r"(?P<rest>[^;.:]*)(?P<end>[;.:]|$)",
    re.IGNORECASE)
MESSAGE_KINDS = frozenset({
    "email", "email (inbound)", "inbound", "text", "sms", "imessage", "linkedin",
    "linkedin message", "linkedin dm", "slack"})
# The word right after the verb: Nick as the recipient, or a time. Nothing else counts.
TO_NICK_WORDS = frozenset({"you", "me", "nick", "nick's", "nick’s", "back"})
TIME_WORDS = frozenset({
    "at", "on", "after", "before", "around", "about", "this", "today", "yesterday",
    "tonight", "overnight", "earlier", "later", "last", "early", "late", "again",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"})
_NEXT_STEP = re.compile(r"\s+the\s+next\s+steps?\b", re.IGNORECASE)
# Anywhere in the clause: someone other than Nick was on the message.
THIRD_PARTY_IN_CLAUSE = re.compile(
    r"\b(?:cc|cc'd|cc’d|ccd|bcc|copied|copying|forwarded|forwarding|fwd|group)\b"
    r"|\bto\s+(?!Nick\b)[A-Z]")
# Words that can sit in the subject slot but are not a contact who replied.
NOT_A_CONTACT = frozenset({
    "nick", "i", "we", "me", "nobody", "none", "noone", "never", "not", "no", "who",
    "already", "also", "just", "finally", "still", "yet", "then",
    # Auxiliaries, bare or negated: a sentence-initial "Hasn't replied." is capitalized
    # but has no subject at all (review 1, 2026-10-29).
    "has", "have", "had", "did", "does", "do", "is", "was", "were", "will", "would",
    "could", "should", "can", "may", "might", "must",
    "hasnt", "havent", "hadnt", "didnt", "doesnt", "dont", "isnt", "wasnt", "werent",
    "wont", "cant", "couldnt", "wouldnt", "shouldnt",
})
_NEGATED_CONTRACTION = re.compile(r"n['’]t$", re.IGNORECASE)
# A reply form inside a condition is not a reply.
CONDITION_WORDS = frozenset({"if", "whether", "unless", "until"})

# Sends.
OUTBOUND_UPPER = re.compile(r"\bSENT\b")
OUTBOUND_START = re.compile(r"^(nick (replied|sent)\b|outbound\b|follow-up #\d+|cold outreach)",
                            re.IGNORECASE)


def _subject_reply(summary: str, pattern: re.Pattern = SUBJECT_REPLY) -> bool:
    for m in pattern.finditer(summary):
        subj = m.group("subj")
        # Names must be capitalized; only the three pronouns may be lowercase.
        if subj.lower() not in ("she", "he", "they") and not subj[0].isupper():
            continue
        if subj.lower() in NOT_A_CONTACT or _NEGATED_CONTRACTION.search(subj):
            continue
        before = summary[:m.start()].split()
        if before and before[-1].strip(",;:").lower() in CONDITION_WORDS:
            continue
        if pattern is SUBJECT_MESSAGE and not _to_nick(m.group("rest"), m.group("end")):
            continue
        return True
    return False


def _to_nick(rest: str, end: str) -> bool:
    """True only when the clause after a message verb positively reads as sent to Nick."""
    if THIRD_PARTY_IN_CLAUSE.search(rest):
        return False
    if not rest.strip():
        # "She texted." ends the clause; "Sam emailed: ..." opens quoted content.
        return end != ":"
    if _NEXT_STEP.match(rest):
        return True
    first = rest.split()[0].strip(",").lower()  # rest is not blank: handled above
    return first in TO_NICK_WORDS or first in TIME_WORDS or first[:1].isdigit()


def classify(kind: str, summary: str) -> str | None:
    """-> 'reply' | 'send' | None for one interaction (its type and its summary)."""
    k = (kind or "").strip().casefold()
    s = (summary or "").strip()
    if (k in INBOUND_KINDS or INBOUND_OPENER.search(s) or INBOUND_UPPER.search(s)
            or _subject_reply(s)):
        return "reply"
    if OUTBOUND_UPPER.search(s) or OUTBOUND_START.match(s):
        return "send"
    if k in MESSAGE_KINDS and _subject_reply(s, SUBJECT_MESSAGE):
        return "reply"
    return None


def is_reply(kind: str, summary: str) -> bool:
    """True only on a positive reply signal (see the module docstring)."""
    return classify(kind, summary) == "reply"
