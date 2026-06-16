"""
open_draft.py — Open a pending email draft in Gmail compose.

Usage:
    python3 tools/open_draft.py

Reads tools/.pending-draft.txt (written automatically by /cold-outreach,
/follow-up, /draft-email, /apply) and opens a Gmail compose window in your
browser with the subject and body pre-filled.

The To field is pre-filled only if the draft file contains a TO: line.
Otherwise leave it blank and fill it in yourself.

Optional ATTACH: lines (one per file, absolute paths) copy the file(s) to
the macOS clipboard so you can press Cmd+V in the Gmail compose window to
attach. Gmail compose URLs do not support attachments directly; clipboard
paste is the workaround. Multiple ATTACH lines are supported but only the
last file ends up on the clipboard (paste it, then update and re-run for
the next).
"""

import os
import subprocess
import sys
import time
import webbrowser
import urllib.parse

DRAFT_FILE = os.path.join(os.path.dirname(__file__), ".pending-draft.txt")
STALE_AFTER_SECONDS = 120


def parse_draft(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    to = ""
    subject = ""
    attachments = []
    body_lines = []
    in_body = False

    for line in content.split("\n"):
        if not in_body:
            if line.startswith("TO:"):
                to = line[3:].strip()
            elif line.startswith("SUBJECT:"):
                subject = line[8:].strip()
            elif line.startswith("ATTACH:"):
                path_value = line[7:].strip()
                if path_value:
                    attachments.append(path_value)
            elif line.startswith("BODY:"):
                in_body = True
                remainder = line[5:].strip()
                if remainder:
                    body_lines.append(remainder)
        else:
            body_lines.append(line)

    while body_lines and not body_lines[-1].strip():
        body_lines.pop()

    return to, subject, attachments, "\n".join(body_lines)


def copy_file_to_clipboard(file_path):
    """Copy a file (not its contents) to the macOS clipboard so Cmd+V in Gmail compose attaches it."""
    abs_path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.exists(abs_path):
        return False, f"file not found: {abs_path}"
    script = f'tell app "Finder" to set the clipboard to (POSIX file "{abs_path}")'
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, result.stderr.strip() or "osascript failed"
    return True, abs_path


def copy_text_to_clipboard(text):
    """Copy plain text to the macOS clipboard via pbcopy."""
    try:
        result = subprocess.run(["pbcopy"], input=text, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def is_reply(subject):
    """Detect a reply subject (case-insensitive 'Re:' prefix)."""
    s = subject.strip().lower()
    return s.startswith("re:") or s.startswith("re :")


def handle_reply(to, subject, body, attachments):
    """
    Reply path: Gmail compose URL doesn't thread into existing conversations
    (only prepends 'Re:' to the subject, opens a new compose window). For replies,
    we pbcopy the body and instruct the user to open the existing thread and paste.
    """
    print("REPLY MODE — subject starts with 'Re:'")
    print()
    print(f"  Original recipient: {to or '(unset — find by sender)'}")
    print(f"  Subject:            {subject}")
    print(f"  Body:               {len(body)} characters")
    print()

    if copy_text_to_clipboard(body):
        print("Body copied to clipboard. Next steps:")
        print("  1. Open Gmail in your browser")
        print(f"  2. Find the original thread (search: from:{to or '<sender>'})")
        print("  3. Hit Reply on the most recent message in that thread")
        print("  4. Cmd+V to paste the body")
        print("  5. Review, then Send")
    else:
        print("WARNING: pbcopy failed. Body is below — copy manually:")
        print()
        print(body)

    if attachments:
        print()
        print(f"NOTE: {len(attachments)} attachment(s) listed. Replies-via-paste don't")
        print("auto-attach. After pasting the body, drag from Finder:")
        for a in attachments:
            print(f"  - {a}")


def main():
    if not os.path.exists(DRAFT_FILE):
        print("No pending draft found.")
        print("Generate one first with /cold-outreach, /follow-up, /draft-email, or /apply.")
        sys.exit(1)

    age = time.time() - os.path.getmtime(DRAFT_FILE)
    if age > STALE_AFTER_SECONDS:
        print(f"ERROR: .pending-draft.txt is {int(age)}s old (limit: {STALE_AFTER_SECONDS}s).")
        print("This file is likely stale from a previous session. Refusing to send to avoid")
        print("opening the wrong draft. Re-write tools/.pending-draft.txt, then re-run.")
        sys.exit(2)

    to, subject, attachments, body = parse_draft(DRAFT_FILE)

    if is_reply(subject):
        handle_reply(to, subject, body, attachments)
        sent_path = DRAFT_FILE + ".sent"
        try:
            os.replace(DRAFT_FILE, sent_path)
            print(f"\nDraft consumed. File renamed to {os.path.basename(sent_path)}.")
            print("Next run requires a fresh tools/.pending-draft.txt.")
        except OSError as e:
            print(f"\nWARNING: could not rename draft file ({e}). Delete it manually before next run.")
        return

    params = {"view": "cm", "fs": "1"}
    if to:
        params["to"] = to
    if subject:
        params["su"] = subject
    if body:
        params["body"] = body

    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"https://mail.google.com/mail/?{query}"

    print("Opening draft in Gmail...")
    print(f"  To:      {to or '(fill in)'}")
    print(f"  Subject: {subject or '(none)'}")
    print(f"  Body:    {len(body)} characters")

    if attachments:
        print(f"  Attach:  {len(attachments)} file(s)")
        for a in attachments:
            print(f"    - {a}")
        last = attachments[-1]
        ok, info = copy_file_to_clipboard(last)
        if ok:
            print()
            print(f"PDF copied to clipboard: {os.path.basename(info)}")
            print("Switch to the Gmail compose tab and press Cmd+V to attach.")
            if len(attachments) > 1:
                print(f"(Note: {len(attachments) - 1} earlier attachment(s) were not copied. "
                      "Attach this one, then re-run with the others or drag from Finder.)")
        else:
            print(f"  WARNING: could not copy to clipboard ({info}). Drag the file in from Finder.")

    webbrowser.open(url)

    sent_path = DRAFT_FILE + ".sent"
    try:
        os.replace(DRAFT_FILE, sent_path)
        print(f"\nDraft consumed. File renamed to {os.path.basename(sent_path)}.")
        print("Next run requires a fresh tools/.pending-draft.txt.")
    except OSError as e:
        print(f"\nWARNING: could not rename draft file ({e}). Delete it manually before next run.")


if __name__ == "__main__":
    main()
