#!/usr/bin/env python3
"""
calendar_approval.py — Touch ID approval of one calendar write, shared by its two callers.

  tools/check_calendar_mcp.py   the PreToolUse gate on the claude.ai Calendar connector
  tools/calendar_write.py       the local writer with its own events.owned token

Both ask the same question ("did Nick approve exactly this write, with his finger, just
now?"), so the mechanism lives here once: the dialog text, the osascript dialog, the
signer-line check, sign-then-verify under the calendar-write-approval namespace, and the
hash-only decision log. What counts as a write, and what to do after approval, stays in
each caller. Nick, 2026-10-28: "I want every write to need Touch ID", and for the writer,
"See how I did the Gmail fetch with the airgapped script".

Enforcement tier: a check a gate reads. approve() returns only after a signature made by
Nick's Secure Enclave key over the canonical request verifies against allowed_signers;
every other path raises Refused with a decision code.

Decision log: one JSON line per decision appended to DECISION_LOG ({ts, tool_name,
decision, sha256}). The hash is of the canonical request, so a line can be matched to a
call without holding any of it: never the title, times, invitees or any other event text.
Writing the log never changes a decision; a failure to write is swallowed.
"""
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import approval_signing

# Worst case for the gate, in sequence: payload read 2 s, dialog DIALOG_SECONDS + 10,
# signing SIGN_SECONDS, verify 30 (approval_signing.verify): 162 s, inside the 240 s hook
# timeout in .claude/settings.json (Codex calendar-gate r1 F2).
DIALOG_SECONDS = 60
SIGN_SECONDS = 60
FIELD_CHARS = 160
APPROVE = "Approve with Touch ID"
# Outside the repo (this repo is public) and outside iCloud-synced ~/Documents.
# CALENDAR_GATE_LOG overrides it, for tests.
DECISION_LOG = Path.home() / "Library" / "Logs" / "job-search" / "calendar-gate.jsonl"
# Appends (Grok calendar-write r1 F3): the file may hold other signers, e.g. for git.
SETUP = ("PYTHONIOENCODING=utf-8 python3 tools/approval_signing.py signer-line"
         " >> ~/.ssh/allowed_signers")


def canonical(name: str, fields) -> bytes:
    """The exact bytes signed: what is called and every argument it is called with."""
    return json.dumps({"tool_name": name, "tool_input": fields}, sort_keys=True,
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def signer_allows_calendar(path) -> bool:
    """True when Nick's allowed_signers line has a key and permits the calendar namespace."""
    if approval_signing.principal_key(path) is None:
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if line.lstrip().startswith("#") or not parts:
            continue
        if approval_signing.PRINCIPAL not in parts[0].split(","):
            continue
        m = re.search(r'namespaces="([^"]*)"', line)
        if m is None or approval_signing.CALENDAR_NAMESPACE in m.group(1).split(","):
            return True
    return False


def one_line(value) -> str:
    """A field on one line, capped, control characters gone."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return text if len(text) <= FIELD_CHARS else text[:FIELD_CHARS - 3] + "..."


def invitees(fields: dict) -> list[str]:
    """Everyone this call adds: attendees and the deprecated email lists."""
    out = []
    for key in ("attendees", "addedAttendees"):
        for a in fields.get(key) or []:
            if isinstance(a, dict):
                name = a.get("displayName")
                out.append(f"{name} <{a.get('email')}>" if name else str(a.get("email")))
            else:
                out.append(str(a))
    for key in ("attendeeEmails", "addedAttendeeEmails"):
        out += [str(e) for e in fields.get(key) or []]
    return [one_line(x) for x in out]


LABELS = {"summary": "Title", "startTime": "Start", "endTime": "End", "allDay": "All day",
          "calendarId": "Calendar", "eventId": "Event id", "responseStatus": "Response",
          "location": "Location", "recurrenceData": "Repeats",
          "notificationLevel": "Notifications"}
INVITEE_KEYS = ("attendees", "addedAttendees", "attendeeEmails", "addedAttendeeEmails")


def dialog_text(action: str, fields: dict, note: str = "") -> str:
    """What Nick sees before he approves. A dialog cannot render bold, so invitees sit
    under an all-caps label, with THIS SENDS INVITATIONS when there are any. Each field is
    one capped line, so text inside a title cannot draw a label of its own."""
    lines = [f"A Claude session wants to: {action}", ""]
    shown = {"calendarId": "primary (default)", **fields}
    lines += [f"{label}: {one_line(shown[k])}" for k, label in LABELS.items() if k in shown]
    who = invitees(fields)
    lines.append("")
    if who:
        lines += ["THIS SENDS INVITATIONS.", "INVITEES (each gets an email from you):"]
        lines += [f"  - {w}" for w in who]
    else:
        lines.append("INVITEES: none")
    if note:
        lines.append(note)
    rest = sorted(k for k in fields if k not in LABELS and k not in INVITEE_KEYS)
    if rest:
        lines += ["", "Other fields:"] + [f"  {k}: {one_line(fields[k])}" for k in rest]
    return "\n".join(lines)


def osascript_dialog(text: str) -> str:
    """"approved" only when Nick clicked Approve before the dialog gave up; "timed_out"
    when it gave up; "declined" for anything else. The text goes in as an argument, never
    into the script."""
    script = [
        "on run argv",
        # Joined with +, not implicit concatenation: the AppleScript word "of" inside an
        # f-string reads as a printed count to tests/scripts/test_tools_announce_reach.py.
        'display dialog (item 1 of argv) with title "Calendar write" ' +
        f'buttons {{"Decline", "{APPROVE}"}} default button "Decline" '
        f'cancel button "Decline" with icon caution giving up after {DIALOG_SECONDS}',
        "end run",
    ]
    args = ["osascript"]
    for line in script:
        args += ["-e", line]
    r = subprocess.run(args + ["--", text], capture_output=True, text=True,
                       timeout=DIALOG_SECONDS + 10)
    if "gave up:true" in r.stdout:
        return "timed_out"
    if r.returncode == 0 and f"button returned:{APPROVE}" in r.stdout:
        return "approved"
    return "declined"


class Refused(Exception):
    """A refusal with its decision code, for the caller to log and act on."""

    def __init__(self, decision: str, reason: str):
        super().__init__(reason)
        self.decision, self.reason = decision, reason


def sign(data: bytes, allowed) -> str:
    return approval_signing.sign(data, allowed_signers=allowed, timeout=SIGN_SECONDS,
                                 namespace=approval_signing.CALENDAR_NAMESPACE)


def verify(data: bytes, sig: str, allowed) -> bool:
    return approval_signing.verify(data, sig, allowed,
                                   namespace=approval_signing.CALENDAR_NAMESPACE)


def approve(name, fields, action, dialog, sign, verify, allowed, note="", shown=None) -> None:
    """Return only when Nick approved this exact request with a verified Touch ID
    signature; otherwise raise Refused with a decision code. `shown` is the same request
    in the dialog's vocabulary (LABELS), when the signed form uses other keys."""
    if not signer_allows_calendar(allowed):
        raise Refused("no_signer", f"no signer line allows calendar approvals in {allowed}. "
                                   f"Nick runs once: {SETUP}")
    answer = dialog(dialog_text(action, fields if shown is None else shown, note))
    if answer == "timed_out":
        raise Refused("timed_out", "the approval dialog timed out")
    if answer != "approved":
        raise Refused("declined", "Nick declined")
    data = canonical(name, fields)
    try:
        sig = sign(data)
    except Exception as e:  # noqa: BLE001 -- Touch ID declined, agent unreachable, ...
        raise Refused("sign_failed", f"{type(e).__name__}: {one_line(str(e))}") from e
    if verify(data, sig) is not True:
        raise Refused("verify_failed", "the Touch ID signature did not verify")


def log_decision(name: str, fields, decision: str) -> None:
    """Append one line to the decision log. Never raises: the log cannot change a verdict."""
    try:
        path = Path(os.environ.get("CALENDAR_GATE_LOG") or DECISION_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool_name": name, "decision": decision,
            "sha256": hashlib.sha256(canonical(name, fields)).hexdigest(),
        }, sort_keys=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001 -- logging must never decide anything
        pass
