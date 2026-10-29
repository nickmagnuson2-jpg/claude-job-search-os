#!/usr/bin/env python3
"""sent_artifact_check.py - flag any artifact that left by Gmail with no record of it.

Proposal 1c-D (output/codify-102326/v2/06-proposals.md). Nick attaches files and presses Send
himself, so no check before Gmail can see what is finally sent. This reads Gmail Sent after
the fact (READ-ONLY: no send, no label change) and, per message, lists:
  (a) every attachment filename on that message's OWN MIME parts (not `has:attachment`,
      which matches a whole thread), walked with gmail_attachments.walk_parts;
  (b) every URL in every text/plain and text/html leaf, HTML href targets included,
      de-duplicated, minus the exact bio URLs with no path (from a gitignored config).

A message with anything left over must match one of:
  1. a BUILD-LOG (output/**/BUILD-LOG.md) whose `sent:` line names that recipient and date
     (a `data/outreach-log.md:<line>` pointer is followed to the row it names);
  2. a CV attachment: exactly MMDDYY-magnuson.pdf or MMDDYY-magnuson-v<N>.pdf, a file of
     that exact name somewhere under output/, with a .yaml of the same stem beside it
     (the RenderCV source). The filename alone is not enough: a build saved under the
     CV's file name would pass a filename test (finding 640.F3);
  3. a line `<gmail message id> | <reason>` in data/sent-artifact-ack.md (missing = empty).
Anything else is a flag.

Reply quotes (Gmail's "On ... wrote:" block, `>` lines, and the HTML gmail_quote element)
are not scanned: the quoted message was scanned when it was sent, and the other party's
links in it are not Nick's artifact. Forwarded content IS scanned, because it is new to the
recipient.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/sent_artifact_check.py --repo-root . [--since YYYY-MM-DD]
      [--json] [--no-state]

--since defaults to the date the state file records (the last run's date, or the oldest
unresolved flag's date, so a flag is shown again until it is resolved), else 7 days back.

--no-state writes nothing at all: no state file, and an expired Gmail token is refreshed in
memory only (no token-file rewrite, no inbox/GMAIL-AUTH-FAILURE.md on failure). The
read-only collector (standup_collect.py --read-only) relies on this.

Exit codes: 0 scanned; 1 scanned with per-message errors (see `errors`); 2 auth or setup
failure (nothing printed on stdout: an auth failure must never read as "0 flags").
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import date, datetime, timedelta
from email.utils import getaddresses
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_report import format_line  # noqa: E402
from gmail_attachments import walk_parts  # noqa: E402
from gmail_fetch import SCOPES, _extract_header, get_or_refresh_creds  # noqa: E402

STATE_NAME = ".sent-artifact-state.json"
BIO_NAME = ".sent-artifact-bio.json"
ACK_REL = Path("data") / "sent-artifact-ack.md"
OUTREACH_REL = Path("data") / "outreach-log.md"
DEFAULT_DAYS = 7

CV_NAME = re.compile(r"^\d{6}-magnuson(?:-v\d+)?\.pdf$")
# Bare domains in plain text ("example.com/planner") carry no scheme, so they are found by
# a TLD list. Scheme URLs, www. URLs and every HTML href are found regardless of TLD.
_TLDS = ("com|net|org|io|ai|app|dev|co|me|so|xyz|page|site|tech|us|info|biz|gg|ly|sh|"
         "to|cc|tv|fm|studio|design|link|cloud|run|build|art|blog|pub|work")
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"')\]]+"
    r"|(?<![@\w.-])(?:[a-z0-9-]+\.)+(?:" + _TLDS + r")\b(?:/[^\s<>\"')\]]*)?",
    re.IGNORECASE)
QUOTE_HEAD = re.compile(r"\n\s*On [^\n]{0,300}?(?:\n[^\n]{0,300}?)?wrote:\s*(?:\n|$)", re.S)
FORWARD_MARK = "Forwarded message"


def normalize_url(u: str) -> str:
    """Lowercase host, no scheme, no leading www., no trailing slash or punctuation."""
    u = u.strip().rstrip(".,;:!?")
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    host, sep, rest = u.partition("/")
    return (host.lower() + sep + rest).rstrip("/")


def is_bio(url: str, bio: set[str]) -> bool:
    """Exact match on a bio URL with no path. A path on a bio domain is NOT exempt."""
    return normalize_url(url) in bio


def strip_plain_quote(text: str) -> str:
    if FORWARD_MARK in text:
        return text
    m = QUOTE_HEAD.search("\n" + text)
    if m:
        text = ("\n" + text)[:m.start()]
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(">"))


class _HtmlUrls(HTMLParser):
    """Collect href targets and URLs in visible text, skipping reply-quote elements."""

    def __init__(self, skip_quotes: bool):
        super().__init__(convert_charrefs=True)
        self.skip_quotes = skip_quotes
        self.depth = 0          # >0 while inside a skipped quote element
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.depth:
            if tag not in ("br", "img", "hr", "meta", "input", "wbr"):
                self.depth += 1
            return
        if self.skip_quotes and tag in ("div", "blockquote") and \
                "gmail_quote" in (a.get("class") or ""):
            self.depth = 1
            return
        href = (a.get("href") or "").strip()
        if href and re.match(r"^(?:https?://|www\.)", href, re.I):
            self.urls.append(href)

    def handle_endtag(self, tag):
        if self.depth and tag not in ("br", "img", "hr", "meta", "input", "wbr"):
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.urls.extend(URL_RE.findall(data))


def html_urls(html: str) -> list[str]:
    p = _HtmlUrls(skip_quotes=FORWARD_MARK not in html)
    p.feed(html)
    p.close()
    return p.urls


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("ascii") + b"===").decode("utf-8", "replace")


def content_id(part: dict) -> str:
    """The part's Content-ID without angle brackets, lowercased; "" when it has none."""
    for h in part.get("headers") or []:
        if (h.get("name") or "").lower() == "content-id":
            return (h.get("value") or "").strip().strip("<>").lower()
    return ""


def is_inline_image(part: dict, referenced: set[str]) -> bool:
    """An image shown in the body (a signature, a pasted screenshot), not a file attached.

    Gmail marks a pasted image `Content-Disposition: attachment` like any other file, so the
    disposition cannot tell them apart. What can: the HTML body references the image's
    Content-ID as `cid:<id>`. Measured 2026-10-26: 14 of 37 flagged sends to networking
    contacts since 2026-08-01 carried nothing but pasted .png parts of that kind.
    """
    cid = content_id(part)
    return ((part.get("mimeType") or "").lower().startswith("image/")
            and bool(cid) and cid in referenced)


CID_RE = re.compile(r"cid:([^\s\"'>)]+)", re.I)


def message_items(msg: dict, fetch_body=None) -> tuple[list[str], list[str]]:
    """(attachment filenames, de-duplicated URLs) from this message's own parts."""
    attachments, urls, seen = [], [], set()
    parts = list(walk_parts(msg.get("payload", {})))
    referenced: set[str] = set()
    texts = {}
    for i, part in enumerate(parts):
        mime = (part.get("mimeType") or "").lower()
        if part.get("filename") or mime not in ("text/plain", "text/html"):
            continue
        body = part.get("body", {}) or {}
        data = body.get("data")
        if not data and body.get("attachmentId") and fetch_body:
            data = fetch_body(body["attachmentId"])
        if data:
            texts[i] = _decode(data)
            if mime == "text/html":
                referenced |= {c.lower() for c in CID_RE.findall(texts[i])}
    for i, part in enumerate(parts):
        if part.get("filename") and not is_inline_image(part, referenced):
            attachments.append(part["filename"])
        if i not in texts:  # attachments and non-text parts were never read as text
            continue
        mime = (part.get("mimeType") or "").lower()
        text = texts[i]
        found = html_urls(text) if mime == "text/html" else \
            URL_RE.findall(strip_plain_quote(text))
        for u in found:
            key = normalize_url(u)
            if key and key not in seen:
                seen.add(key)
                urls.append(u.rstrip(".,;:!?/"))
    return attachments, urls


def cv_index(repo_root: Path) -> set[str]:
    """CV PDF names under output/ that have a same-stem .yaml (RenderCV source) beside them."""
    out = repo_root / "output"
    return {p.name for p in out.rglob("*.pdf")
            if CV_NAME.match(p.name) and p.with_suffix(".yaml").is_file()}


def load_ack(repo_root: Path) -> set[str]:
    path = repo_root / ACK_REL
    if not path.is_file():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = re.sub(r"^\s*[-*]\s*", "", line).strip()
        mid, _, reason = line.partition("|")
        if not line.startswith("#") and mid.strip() and reason.strip():
            ids.add(mid.strip())
    return ids


def build_log_sent_texts(repo_root: Path) -> list[tuple[str, str]]:
    """(each BUILD-LOG `sent:` value with its outreach-log row, that log's whole text)."""
    outreach = repo_root / OUTREACH_REL
    rows = outreach.read_text(encoding="utf-8").splitlines() if outreach.is_file() else []
    texts = []
    for log in sorted((repo_root / "output").rglob("BUILD-LOG.md")):
        whole = log.read_text(encoding="utf-8")
        for line in whole.splitlines():
            m = re.match(r"^\s*(?:[-*]\s*)?(?:\*\*)?sent:(?:\*\*)?\s*(.+)$", line, re.I)
            if not m:
                continue
            val = m.group(1)
            ptr = re.search(r"data/outreach-log\.md:(\d+)", val)
            if ptr and 0 < int(ptr.group(1)) <= len(rows):
                val = val + " " + rows[int(ptr.group(1)) - 1]
            texts.append((val.lower(), whole.lower()))
    return texts


def recipients(headers: list) -> list[tuple[str, str]]:
    raw = [_extract_header(headers, h) for h in ("To", "Cc", "Bcc")]
    return [(n.strip(), a.strip()) for n, a in getaddresses([r for r in raw if r]) if a or n]


def unexplained(items: list[str], recips, msg_date: str, sent_texts, key=str.lower) -> list[str]:
    """The items no matching BUILD-LOG names.

    A log matches when its `sent:` line (with the outreach-log row it points to) carries the
    message's date and a recipient. It then explains only the items its own text names: an
    attachment by file name, a link by its host and path. Matching on recipient and date
    alone let one logged send excuse every other artifact sent to that person that day
    (codex and grok review, 2026-10-26).
    """
    logs = [whole for sent, whole in sent_texts
            if msg_date in sent and any((a and a.lower() in sent) or (n and n.lower() in sent)
                                        for n, a in recips)]
    return [i for i in items if not any(key(i).lower() in whole for whole in logs)]  # logs lowered


def msg_local_date(msg: dict) -> str:
    return datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000).date().isoformat()


def classify(msg: dict, ctx: dict, fetch_body=None) -> dict | None:
    """The flag dict for one message, or None when it passes."""
    attachments, urls = message_items(msg, fetch_body)
    urls = [u for u in urls if not is_bio(u, ctx["bio"])]
    attachments = [a for a in attachments if a not in ctx["cvs"]]
    if msg.get("id") in ctx["ack"]:
        return None
    headers = msg.get("payload", {}).get("headers", [])
    recips = recipients(headers)
    day = msg_local_date(msg)
    attachments = unexplained(attachments, recips, day, ctx["sent_texts"])
    urls = unexplained(urls, recips, day, ctx["sent_texts"], key=normalize_url)
    if not attachments and not urls:
        return None
    return {"id": msg.get("id"), "date": day,
            "to": [a or n for n, a in recips],
            "subject": _extract_header(headers, "Subject"),
            "attachments": attachments, "urls": urls}


def load_bio(tools_dir: Path) -> tuple[set[str], str | None]:
    path = tools_dir / BIO_NAME
    if not path.is_file():
        return set(), f"{path.name} missing: no URL is exempt as a bio link"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {normalize_url(u) for u in data.get("bio_urls", [])}, None


def default_since(state_path: Path, today: date) -> str:
    try:
        st = json.loads(state_path.read_text(encoding="utf-8"))
        return date.fromisoformat(st["next_since"]).isoformat()
    except (OSError, ValueError, KeyError, TypeError):
        return (today - timedelta(days=DEFAULT_DAYS)).isoformat()


def save_state(state_path: Path, today: date, flags: list[dict]) -> None:
    nxt = min([f["date"] for f in flags] + [today.isoformat()])
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last_run": today.isoformat(), "next_since": nxt}),
                   encoding="utf-8")
    tmp.replace(state_path)


def list_sent_ids(service, since: str) -> list[str]:
    after = int(datetime.combine(date.fromisoformat(since), datetime.min.time()).timestamp())
    ids, token = [], None
    while True:
        kw = {"userId": "me", "labelIds": ["SENT"], "q": f"after:{after}", "maxResults": 500}
        if token:
            kw["pageToken"] = token
        resp = service.users().messages().list(**kw).execute()
        ids.extend(m["id"] for m in resp.get("messages", []))
        token = resp.get("nextPageToken")
        if not token:
            return ids


def scan(service, ctx: dict, since: str) -> dict:
    flags, errors = [], list(ctx.get("errors", []))
    ids = list_sent_ids(service, since)
    failed = 0
    msgs = service.users().messages()
    for mid in ids:
        try:
            msg = msgs.get(userId="me", id=mid, format="full").execute()

            def fetch_body(aid, _mid=mid):
                return msgs.attachments().get(userId="me", messageId=_mid,
                                              id=aid).execute().get("data")
            flag = classify(msg, ctx, fetch_body)
        except Exception as e:  # one bad message must not hide the rest
            errors.append(f"{mid}: {type(e).__name__}: {e}")
            failed += 1
            continue
        if flag:
            flags.append(flag)
    reach = format_line(f"Gmail Sent (in:sent) since {since}, each message's own parts",
                        population=len(ids), included=len(flags),
                        excluded={"explained or nothing to check": len(ids) - len(flags) - failed,
                                  "message read failed": failed})
    return {"since": since, "scanned": len(ids), "reach": reach, "flags": flags,
            "errors": errors}


def build_context(repo_root: Path) -> dict:
    bio, err = load_bio(repo_root / "tools")
    return {"bio": bio, "cvs": cv_index(repo_root), "ack": load_ack(repo_root),
            "sent_texts": build_log_sent_texts(repo_root), "errors": [err] if err else []}


def read_only_creds(tools_dir: Path):
    """Credentials for a run that must write nothing (--no-state, the page's 15-minute render).

    gmail_fetch.get_or_refresh_creds rewrites tools/gmail_token.json after a refresh and
    writes inbox/GMAIL-AUTH-FAILURE.md when one fails. Both are right for the fetcher and
    wrong here. This loads the same token, refreshes an expired one in memory only, and on
    any failure raises, which main() reports as exit 2 with nothing written.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    token_path = tools_dir / "gmail_token.json"
    if not token_path.exists():
        raise RuntimeError(f"no Gmail token at {token_path}; run gmail_fetch.py --auth")
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            raise RuntimeError("Gmail token invalid and cannot be refreshed; "
                               "run gmail_fetch.py --auth")
        creds.refresh(Request())  # in memory; the token file is not touched
    return creds


def connect(tools_dir: Path, persist: bool = True):
    from googleapiclient.discovery import build
    creds = get_or_refresh_creds(tools_dir) if persist else read_only_creds(tools_dir)
    return build("gmail", "v1", credentials=creds)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--since", help="YYYY-MM-DD (default: from the state file, else 7 days)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-state", action="store_true",
                    help="write nothing: no state file, no token write, no auth-failure alert")
    a = ap.parse_args(argv)
    root = Path(a.repo_root).resolve()
    tools_dir = root / "tools"
    state_path = tools_dir / STATE_NAME
    today = date.today()
    since = a.since or default_since(state_path, today)
    try:
        date.fromisoformat(since)
    except ValueError:
        print(f"ERROR: --since must be YYYY-MM-DD, got {since!r}", file=sys.stderr)
        return 2
    try:
        service = connect(tools_dir, persist=not a.no_state)
        ctx = build_context(root)
        result = scan(service, ctx, since)
    except (Exception, SystemExit) as e:
        print(f"ERROR: Gmail Sent scan failed (auth or setup), nothing was checked: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if not a.no_state:
        save_state(state_path, today, result["flags"])
    if a.json:
        print(json.dumps(result, indent=1, ensure_ascii=False))
    else:
        print(f"Sent since {since}: {result['scanned']} scanned, "
              f"{len(result['flags'])} flag(s), {len(result['errors'])} error(s)")
        for f in result["flags"]:
            print(f"  {f['date']} {f['id']} to {', '.join(f['to'])}: {f['subject']}")
            for item in f["attachments"] + f["urls"]:
                print(f"      {item}")
        for e in result["errors"]:
            print(f"  error: {e}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
