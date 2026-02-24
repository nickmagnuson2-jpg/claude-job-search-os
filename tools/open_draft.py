"""
open_draft.py — Open a pending email draft in Gmail compose.

Usage:
    python tools/open_draft.py

Reads tools/.pending-draft.txt (written automatically by /cold-outreach,
/follow-up, and /draft-email) and opens a Gmail compose window in your
browser with the subject and body pre-filled.

The To field is pre-filled only if the draft file contains a TO: line.
Otherwise leave it blank and fill it in yourself.
"""

import os
import sys
import webbrowser
import urllib.parse

DRAFT_FILE = os.path.join(os.path.dirname(__file__), ".pending-draft.txt")


def parse_draft(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    to = ""
    subject = ""
    body_lines = []
    in_body = False

    for line in content.split("\n"):
        if not in_body:
            if line.startswith("TO:"):
                to = line[3:].strip()
            elif line.startswith("SUBJECT:"):
                subject = line[8:].strip()
            elif line.startswith("BODY:"):
                in_body = True
                remainder = line[5:].strip()
                if remainder:
                    body_lines.append(remainder)
        else:
            body_lines.append(line)

    # Strip trailing blank lines from body
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()

    return to, subject, "\n".join(body_lines)


def main():
    if not os.path.exists(DRAFT_FILE):
        print("No pending draft found.")
        print("Generate one first with /cold-outreach, /follow-up, or /draft-email.")
        sys.exit(1)

    to, subject, body = parse_draft(DRAFT_FILE)

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

    webbrowser.open(url)


if __name__ == "__main__":
    main()
