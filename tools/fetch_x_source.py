#!/usr/bin/env python3
"""fetch_x_source.py - pull an X (Twitter) post or X Article into the job-search OS.

Exa, WebFetch, and r.jina.ai returned nothing for X when tested. The FxTwitter API
(api.fxtwitter.com, third party, no auth, no SLA) returned a plain post and an X Article
body when tested live on 2026-10-19. Other payload shapes are handled defensively: they
fail with a reason_code or return ok:true with a warning naming what is missing. Emits a JSON ExtractionBlock to stdout for /analyze to consume, in the same
shape family as tools/fetch_transcript.py.

ALWAYS run with PYTHONIOENCODING=utf-8 (repo convention) or it crashes on Unicode.

Output JSON shape:
  {
    "ok": true,
    "source_type": "x_post",
    "kind": "post" | "article",
    "status_id": "...", "url": "https://x.com/<handle>/status/<id>", "slug": "...",
    "title": "...",                     # article title, or the post's opening words
    "author": {"handle": "...", "name": "...", "followers": 0},
    "created_at": "...",
    "metrics": {"likes": 0, "retweets": 0, "replies": 0, "bookmarks": 0, "views": 0},
    "text": "plaintext; articles keep headers, lists, and embedded prompt/code blocks;
             a quoted post is appended as a > block",
    "warnings": ["..."]                 # anything known to be missing or unrendered
  }
On failure:
  {"ok": false, "error": "human message", "reason_code": "...", "url": "..."}
  reason_code: bad_url | not_found | private | unavailable | no_text | http_error |
               network_error | schema_drift

X Article bodies are Draft.js content: `blocks[]` plus an `entityMap`. An `atomic` block
carries no text of its own; its content (an embedded prompt or code block, an image, a
divider) is the entity named by `entityRanges[0].key`, which matches the entity's
`key` FIELD, not its position in the list. Reading only `blocks[].text` dropped every
embedded prompt in the X Article tested on 2026-10-19.

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/fetch_x_source.py <x-or-twitter-status-url>
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.fxtwitter.com/status/"
TIMEOUT_S = 20

# --- deterministic helpers (unit-tested; no network) -------------------------

_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com",
          "fxtwitter.com", "vxtwitter.com", "fixupx.com"}
_STATUS_PATH_RE = re.compile(r"^/(?:[A-Za-z0-9_]{1,15}|i/web)/status(?:es)?/(?P<id>\d{1,25})(?:/|$)")


def parse_status_url(url: str):
    """Return the status id for an X/Twitter status URL, else None.

    Profiles, searches, lists, and /i/article/<id> links are NOT status URLs and
    return None: the article id is not a status id and the API cannot resolve it.
    """
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _HOSTS:
        return None
    m = _STATUS_PATH_RE.match(parsed.path or "")
    if not m:
        return None
    return m.group("id")


_BLOCK_PREFIX = {
    "header-one": "# ",
    "header-two": "## ",
    "header-three": "### ",
    "unordered-list-item": "- ",
    "ordered-list-item": "1. ",
    "blockquote": "> ",
    "unstyled": "",
}


def _entities_by_key(entity_map):
    """Index Draft.js entities by their `key` field.

    FxTwitter sends a list of {"key", "value"} (verified 2026-10-19). Any other shape
    yields no entities, so every atomic block surfaces a "missing entity" warning
    rather than being dropped silently.
    """
    if not isinstance(entity_map, list):
        return {}
    return {str(e["key"]): e.get("value") or {}
            for e in entity_map if isinstance(e, dict) and "key" in e}


def _render_entity(entity):
    """Return (text_or_None, warning_or_None) for one atomic-block entity."""
    etype = (entity or {}).get("type")
    data = (entity or {}).get("data") or {}
    if etype == "MARKDOWN":
        md = (data.get("markdown") or "").strip()
        return (md, None) if md else (None, "embedded MARKDOWN block has no content")
    if etype == "MEDIA":
        return "[media]", None
    if etype == "DIVIDER":
        return "---", None
    return f"[{etype or 'unknown'} embed]", f"unrendered embed type: {etype}"


def article_to_text(content):
    """Render an X Article's Draft.js content to plaintext. Returns (text, warnings)."""
    warnings = []
    entities = _entities_by_key(content.get("entityMap"))
    parts = []
    for block in content.get("blocks") or []:
        btype = block.get("type", "")
        if btype == "atomic":
            ranges = block.get("entityRanges") or []
            if not ranges:
                warnings.append(f"atomic block {block.get('key')} has no entity")
                continue
            ref = str(ranges[0].get("key"))
            if ref not in entities:
                warnings.append(f"atomic block {block.get('key')} references missing entity {ref}")
                continue
            text, warn = _render_entity(entities[ref])
            if warn and warn not in warnings:
                warnings.append(warn)
            if text:
                parts.append(text)
            continue
        text = (block.get("text") or "").strip()
        if not text:
            continue
        if btype not in _BLOCK_PREFIX:
            msg = f"unknown block type rendered as plain text: {btype}"
            if msg not in warnings:
                warnings.append(msg)
        parts.append(_BLOCK_PREFIX.get(btype, "") + text)
    return "\n\n".join(parts), warnings


def slugify(text: str, fallback: str = "x-post") -> str:
    """lowercase-hyphens slug (repo output convention)."""
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:80] or fallback


def normalize(payload, status_id: str, url: str) -> dict:
    """Map an FxTwitter API payload to the ExtractionBlock (or a failure block)."""
    if not isinstance(payload, dict):
        return _fail("payload is not a JSON object", "schema_drift", url)
    code = payload.get("code")
    if code != 200:
        reason = {404: "not_found", 401: "private"}.get(code, "http_error")
        return _fail(f"API code {code}: {payload.get('message')}", reason, url)
    tweet = payload.get("tweet")
    if not isinstance(tweet, dict) or "text" not in tweet:
        return _fail("payload has no tweet object with text", "schema_drift", url)

    warnings = []
    article = tweet.get("article")
    if article is not None:
        if not (isinstance(article, dict) and isinstance(article.get("content"), dict)):
            return _fail("post has an article object without readable content", "schema_drift", url)
        kind = "article"
        text, warnings = article_to_text(article["content"])
        title = article.get("title") or ""
        if not text:
            return _fail("article content rendered to empty text", "schema_drift", url)
    else:
        kind = "post"
        text = (tweet.get("text") or "").strip()
        if not text:
            return _fail("post has no text (media-only or empty)", "no_text", url)
        title = " ".join(text.split()[:10])
    quoted, quote_warning = render_quote(tweet.get("quote"))
    if quoted:
        text += "\n\n" + quoted
    if quote_warning:
        warnings.append(quote_warning)
    if tweet.get("replying_to_status"):
        warnings.append("this post replies to another; only this post was fetched, not the thread")

    author = tweet.get("author") or {}
    handle = author.get("screen_name")
    return {
        "ok": True,
        "source_type": "x_post",
        "kind": kind,
        "status_id": str(tweet.get("id") or status_id),
        "url": f"https://x.com/{handle}/status/{status_id}" if handle else url,
        "slug": slugify(title),
        "title": title,
        "author": {"handle": handle, "name": author.get("name"),
                   "followers": author.get("followers")},
        "created_at": tweet.get("created_at"),
        "metrics": {k: tweet.get(k) for k in ("likes", "retweets", "replies", "bookmarks", "views")},
        "text": text,
        "warnings": warnings,
    }


def render_quote(quote):
    """Return (quoted_block_or_None, warning_or_None) for a post's `quote` field.

    FxTwitter's documented field (docs.fxembed.com, 2026-10-19): `quote` is a nested
    post object, or a placeholder with no text when the quoted post is unavailable.
    """
    if quote is None:
        return None, None
    if not isinstance(quote, dict):
        return None, "quoted post present in an unreadable shape; not included"
    qtext = (quote.get("text") or "").strip()
    if not qtext:
        why = quote.get("reason") or "no text"
        return None, f"quoted post not included ({why})"
    handle = (quote.get("author") or {}).get("screen_name") or "unknown"
    body = "\n".join("> " + line for line in qtext.splitlines())
    warning = None
    if quote.get("article") is not None:
        warning = "quoted post is an X Article; only its post text is included, fetch it separately"
    return f"> Quoted post by @{handle}:\n{body}", warning


def _fail(error: str, reason_code: str, url: str) -> dict:
    return {"ok": False, "error": error, "reason_code": reason_code, "url": url}


# --- network step (manual smoke test; not in CI) -----------------------------


def http_get(endpoint: str) -> str:
    """Return the response body. Raises urllib errors (HTTPError on non-2xx)."""
    req = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0 job-search-os"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode("utf-8", "replace")


def build_block(url: str, getter=http_get) -> dict:
    status_id = parse_status_url(url)
    if not status_id:
        return _fail(f"Not an X/Twitter status URL (need /<handle>/status/<id>): {url}", "bad_url", url)
    try:
        body = getter(API_BASE + status_id)
    except urllib.error.HTTPError as exc:
        reason = {404: "not_found", 401: "private"}.get(exc.code, "http_error")
        return _fail(f"HTTP {exc.code} from FxTwitter", reason, url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _fail(f"network error reaching FxTwitter: {exc}", "network_error", url)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        # A missing status came back as HTTP 200 with an HTML page (2026-10-19), but an
        # HTML service-error page looks the same, so HTML cannot be read as "not found".
        if body.lstrip().startswith("<"):
            return _fail("FxTwitter returned an HTML page, not JSON: the status may be "
                         "deleted or missing, or the service may be erroring. Open the "
                         "URL in a browser to tell which.", "unavailable", url)
        return _fail("FxTwitter returned invalid JSON", "schema_drift", url)
    return normalize(payload, status_id, url)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("url", help="x.com or twitter.com status URL")
    args = parser.parse_args(argv)
    block = build_block(args.url)
    print(json.dumps(block, ensure_ascii=False, indent=2))
    return 0 if block.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
