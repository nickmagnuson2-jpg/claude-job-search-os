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

Every ATTACH: file is also copied into a staging folder (~/Documents/to-send
by default, OPEN_DRAFT_STAGING_DIR to override) after the folder is emptied,
and Finder opens with the staged copy selected. The folder then holds only
the file(s) for this draft, so the one to drag in is the only one there.

An ARTIFACT: line (`ARTIFACT: output/<slug>/BUILD-LOG.md` or `ARTIFACT: none (<reason>)`)
is read by tools/check_draft_artifact.py before this script runs and is never sent: like
any header line this parser does not name, it is dropped.
"""

import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from email.utils import getaddresses
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import draft_capture  # noqa: E402

DRAFT_FILE = os.path.join(os.path.dirname(__file__), ".pending-draft.txt")
SOURCE_MARKER = os.path.join(os.path.dirname(__file__), ".pending-draft.source")
STALE_AFTER_SECONDS = 120
# The staging file is renamed once compose has been OPENED. Until 2026-10-06 the suffix
# was `.sent`, which nothing here ever observed: opening a compose window is not sending.
OPENED_SUFFIX = ".opened"
# CVs are all named MMDDYY-magnuson.pdf, so two different ones look the same in a file
# picker. On 2026-10-08 the previous day's CV went out on a reply for that reason.
DEFAULT_STAGING_DIR = os.path.join("~", "Documents", "to-send")
# Only a folder carrying this marker is ever emptied, so pointing the override at an
# ordinary folder cannot delete what is in it.
STAGING_MARKER = ".open-draft-staging"
# A problem string that STARTS with this means the files were staged and only the Finder
# step failed. Matched by prefix, so a path that happens to contain the words cannot trip it.
FINDER_FAILED = "staged, but Finder could not be opened"


def record_draft():
    """Keep the exact draft under an ID before it leaves, and say how to confirm it.

    Never stops the draft from opening: a capture that fails is reported and skipped.
    """
    try:
        with open(DRAFT_FILE, "r", encoding="utf-8") as f:
            text = f.read()
        skill = ""
        if os.path.exists(SOURCE_MARKER):
            with open(SOURCE_MARKER, "r", encoding="utf-8") as f:
                skill = (f.read().splitlines() or [""])[0].strip()
        capture_id = draft_capture.capture(text, kind="email", skill=skill)
        waiting = len(draft_capture.pending(kind="email"))
    except Exception as exc:
        print(f"\nWARNING: draft was NOT captured ({exc}). It still opens; there will be "
              f"no record to compare with what gets sent.")
        return
    print(f"\nDraft captured as {capture_id}.")
    print("After Nick sends it (or decides not to), record which, in ONE of these forms:")
    print(f"  PYTHONIOENCODING=utf-8 python3 tools/draft_capture.py confirm {capture_id} unchanged")
    print(f"  PYTHONIOENCODING=utf-8 python3 tools/draft_capture.py confirm {capture_id} edited "
          f"--body-file <file with the sent text> [--screenshot <image>]")
    print(f"  PYTHONIOENCODING=utf-8 python3 tools/draft_capture.py confirm {capture_id} not-sent")
    print("If he shares a screenshot of the sent mail, transcribe its body to a file exactly, "
          "then use `edited` with --screenshot.")
    print("If the email was revised (edited at send, or earlier drafts replaced in chat), "
          "confirm refuses until you add the edits to memory/lessons.md Section 2 and pass "
          "--lesson \"<text from that row>\", or pass --no-lesson \"<why nothing was learned>\".")
    if waiting > 1:
        print(f"NOTE: {waiting - 1} earlier draft(s) are still unconfirmed "
              f"(`draft_capture.py pending`). Ask Nick what happened to them.")


def consume_draft():
    """Rename the staging file so the next run needs a fresh draft."""
    opened_path = DRAFT_FILE + OPENED_SUFFIX
    try:
        os.replace(DRAFT_FILE, opened_path)
        print(f"\nDraft consumed. File renamed to {os.path.basename(opened_path)}. "
              f"That means compose was opened, not that anything was sent.")
        print("Next run requires a fresh tools/.pending-draft.txt.")
    except OSError as e:
        print(f"\nWARNING: could not rename draft file ({e}). Delete it manually before next run.")


def parse_draft(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    to = ""
    cc = ""
    subject = ""
    attachments = []
    body_lines = []
    in_body = False

    for line in content.split("\n"):
        if not in_body:
            if line.startswith("TO:"):
                to = line[3:].strip()
            elif line.startswith("CC:"):
                cc = line[3:].strip()
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

    return to, cc, subject, attachments, "\n".join(body_lines)


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
    """Copy plain text to the macOS clipboard, then read it back.

    pbcopy's exit code is not evidence: a LinkedIn draft once reported success while the
    clipboard still held the previous email (infrastructure-defects D2). Only a pbpaste
    that returns exactly `text` counts.
    """
    if not text.strip():
        return False  # an empty body delivers nothing, so it must not consume the draft
    try:
        if subprocess.run(["pbcopy"], input=text, text=True).returncode != 0:
            return False
        back = subprocess.run(["pbpaste"], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return back.returncode == 0 and back.stdout == text


def staging_dir():
    return os.path.abspath(os.path.expanduser(
        os.environ.get("OPEN_DRAFT_STAGING_DIR") or DEFAULT_STAGING_DIR))


def reveal_in_finder(path):
    result = subprocess.run(["open", "-R", path], capture_output=True)
    if result.returncode != 0:
        raise OSError(f"open -R exited {result.returncode}")


def stage_attachments(attachments, directory=None, reveal=None):
    """Leave the staging folder holding copies of exactly these files, and nothing else.

    Returns (staged_paths, problems). Never raises: the draft still opens if staging fails.
    """
    directory = directory or staging_dir()
    marker = os.path.join(directory, STAGING_MARKER)
    staged, problems = [], []
    if os.path.islink(directory):
        # Emptying a symlinked folder would delete files in whatever it points at.
        return [], [f"{directory} is a symlink, so nothing was staged. Point "
                    f"OPEN_DRAFT_STAGING_DIR at a real folder."]
    try:
        if not os.path.isdir(directory):
            os.makedirs(directory)
            with open(marker, "w", encoding="utf-8") as f:
                f.write("Emptied and refilled by tools/open_draft.py on every draft.\n")
        elif not os.path.exists(marker):
            if os.listdir(directory):
                return [], [f"{directory} already holds files and was not created by this "
                            f"tool, so nothing was staged. Empty it or set "
                            f"OPEN_DRAFT_STAGING_DIR to another folder."]
            with open(marker, "w", encoding="utf-8") as f:
                f.write("Emptied and refilled by tools/open_draft.py on every draft.\n")
        for name in os.listdir(directory):
            old = os.path.join(directory, name)
            if name != STAGING_MARKER and os.path.isfile(old):
                os.remove(old)
        for i, a in enumerate(attachments):
            src = os.path.abspath(os.path.expanduser(a))
            if not os.path.isfile(src):
                problems.append(f"file not found, not staged: {src}")
                continue
            base = os.path.basename(src)
            name, n = base, i + 1
            while os.path.exists(os.path.join(directory, name)):
                name, n = f"{n}-{base}", n + 1
            dest = os.path.join(directory, name)
            shutil.copy2(src, dest)
            staged.append(dest)
    except OSError as exc:
        problems.append(f"staging failed ({exc})")
    if staged:
        try:
            (reveal or reveal_in_finder)(staged[0])
        except OSError as exc:
            problems.append(f"{FINDER_FAILED} ({exc})")
    return staged, problems


def report_staging(attachments):
    """Stage the draft's attachments and say where they are. Returns the staged paths."""
    staged, problems = stage_attachments(attachments)
    if staged:
        print(f"\nAttachment staged. {os.path.dirname(staged[0])} now holds only:")
        for path in staged:
            print(f"  - {os.path.basename(path)}  ({os.path.getsize(path):,} bytes)")
        if any(problem.startswith(FINDER_FAILED) for problem in problems):
            print("Open that folder yourself and drag from there.")
        else:
            print("Finder is open on it. Drag from there.")
    for problem in problems:
        print(f"  WARNING: {problem}")
    return staged


def is_reply(subject):
    """Detect a reply subject (case-insensitive 'Re:' prefix)."""
    s = subject.strip().lower()
    return s.startswith("re:") or s.startswith("re :")


def thread_search_url(to, subject):
    """A Gmail search URL that lands on the thread this reply belongs to.

    Searches by the subject with its Re:/Fwd: prefixes stripped, narrowed to the
    first recipient address when one parses. Gmail has no escape for a double quote
    inside a quoted phrase, so a subject containing one is searched as its words,
    which is looser but never a different subject.
    """
    base = re.sub(r"^\s*((re|fwd?)\s*:\s*)+", "", subject, flags=re.I).strip()
    if '"' in base:
        words = re.sub(r"[\"()]", " ", base).split()
        query = "subject:(" + " ".join(words) + ")"
    else:
        query = f'subject:"{base}"'
    addresses = [a for _, a in getaddresses([to or ""]) if "@" in a]
    if addresses:
        query += f" (from:{addresses[0]} OR to:{addresses[0]})"
    return "https://mail.google.com/mail/u/0/#search/" + urllib.parse.quote(query, safe="")


def handle_reply(to, subject, body, attachments):
    """
    Reply path: Gmail compose URL doesn't thread into existing conversations
    (only prepends 'Re:' to the subject, opens a new compose window). For replies,
    we pbcopy the body and open a Gmail search for the thread, so the user lands
    on it, hits Reply and pastes. Nothing is ever saved to Gmail Drafts; on
    2026-10-28 the old print-only path read as "the draft is not in Gmail".
    """
    print("REPLY MODE — this draft is not in Gmail Drafts. The body is on your clipboard;")
    print("the thread opens in Gmail: hit Reply on the newest message and paste.")
    print()
    print(f"  Original recipient: {to or '(unset — find by sender)'}")
    print(f"  Subject:            {subject}")
    print(f"  Body:               {len(body)} characters")
    print()

    copied = copy_text_to_clipboard(body)
    if copied:
        url = thread_search_url(to, subject)
        print("Body copied to clipboard (read back and verified). Next steps:")
        print("  1. Gmail opens on a search for the thread (opening now)")
        print("  2. Open the thread and hit Reply on the most recent message")
        print("  3. Cmd+V to paste the body")
        print("  4. Review, then Send")
        print(f"  Search URL: {url}")
        webbrowser.open(url)
    else:
        print_copy_failure(body)

    if attachments:
        print()
        print(f"NOTE: {len(attachments)} attachment(s) listed. Replies-via-paste don't")
        print("auto-attach. After pasting the body, drag the file in. Source:")
        for a in attachments:
            print(f"  - {a}")
        report_staging(attachments)
    return copied


def handle_paste_only(body, attachments):
    """No TO, no CC and no SUBJECT: not an email (a LinkedIn message, or another channel).

    Before this existed such a draft fell into compose mode, opened a Gmail window and put
    nothing on the clipboard (infrastructure-defects D2). Now it opens nothing.
    """
    print("PASTE MODE: no recipient, no CC and no subject, so this is not an email.")
    print(f"  Body: {len(body)} characters")
    print()
    copied = copy_text_to_clipboard(body)
    if copied:
        print("Body copied to clipboard (read back and verified). Paste it into LinkedIn,")
        print("or wherever it is going. Nothing was opened.")
    else:
        print_copy_failure(body)
    if attachments:
        report_staging(attachments)
    return copied


def print_copy_failure(body):
    print("WARNING: the clipboard does not hold this body (pbcopy failed or read back")
    print("different text). The draft is NOT consumed. Body below, copy it manually:")
    print()
    print(body)


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

    to, cc, subject, attachments, body = parse_draft(DRAFT_FILE)

    if is_reply(subject) or not (to or cc or subject):
        if is_reply(subject):
            copied = handle_reply(to, subject, body, attachments)
        else:
            copied = handle_paste_only(body, attachments)
        if copied:  # a paste mode delivers nothing until the clipboard holds the body
            record_draft()
            consume_draft()
        return

    params = {"view": "cm", "fs": "1"}
    if to:
        params["to"] = to
    if cc:
        params["cc"] = cc
    if subject:
        params["su"] = subject
    if body:
        params["body"] = body

    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"https://mail.google.com/mail/?{query}"

    print("Opening draft in Gmail...")
    print(f"  To:      {to or '(fill in)'}")
    if cc:
        print(f"  Cc:      {cc}")
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
        report_staging(attachments)

    webbrowser.open(url)

    record_draft()
    consume_draft()


if __name__ == "__main__":
    main()
