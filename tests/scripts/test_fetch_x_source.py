"""Deterministic unit tests for tools/fetch_x_source.py.

Covers the no-network parts: status-URL parsing, Draft.js article rendering, payload
normalization, and HTTP/content-type failure mapping (via an injected getter). Fixtures
are synthetic, shaped like the real FxTwitter payloads captured 2026-10-19. The live
fetch is a MANUAL smoke test, per the repo test-suite convention:
  PYTHONIOENCODING=utf-8 python3 tools/fetch_x_source.py <status-url>
"""

import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import fetch_x_source as fx  # noqa: E402


# --- fixtures ---------------------------------------------------------------

def _article_content():
    # entityRanges keys name the entity's `key` FIELD. List positions deliberately
    # differ from keys, as in the real payload, so a positional lookup gets it wrong.
    return {
        "blocks": [
            {"type": "unstyled", "text": "Intro line.", "key": "a", "entityRanges": []},
            {"type": "header-two", "text": "01. first step", "key": "b", "entityRanges": []},
            {"type": "unordered-list-item", "text": "item one", "key": "c", "entityRanges": []},
            {"type": "atomic", "text": " ", "key": "d", "entityRanges": [{"key": 7, "length": 1, "offset": 0}]},
            {"type": "atomic", "text": " ", "key": "e", "entityRanges": [{"key": 3, "length": 1, "offset": 0}]},
            {"type": "atomic", "text": " ", "key": "f", "entityRanges": [{"key": 5, "length": 1, "offset": 0}]},
            {"type": "unstyled", "text": "   ", "key": "g", "entityRanges": []},
            {"type": "ordered-list-item", "text": "numbered", "key": "h", "entityRanges": []},
        ],
        "entityMap": [
            {"key": "3", "value": {"type": "DIVIDER", "data": {}}},
            {"key": "5", "value": {"type": "MEDIA", "data": {"mediaItems": []}}},
            {"key": "7", "value": {"type": "MARKDOWN", "data": {"markdown": "```python\nPROMPT BODY\n```"}}},
        ],
    }


def _payload(article=None, text="hello world from a plain post", **extra):
    tweet = {
        "id": "123", "text": text,
        "author": {"screen_name": "example_user", "name": "Example User", "followers": 10},
        "created_at": "Mon Jan 01 00:00:00 +0000 2026",
        "likes": 1, "retweets": 2, "replies": 3, "bookmarks": 4, "views": 5,
    }
    if article is not None:
        tweet["article"] = article
    tweet.update(extra)
    return {"code": 200, "message": "OK", "tweet": tweet}


def _getter(body=None, exc=None):
    def get(endpoint):
        get.endpoint = endpoint
        if exc is not None:
            raise exc
        return body if body is not None else json.dumps(_payload())
    return get


# --- URL parsing --------------------------------------------------------------

class TestParseStatusUrl:
    def test_x_status(self):
        assert fx.parse_status_url("https://x.com/example_user/status/123?s=20") == "123"

    def test_twitter_www_and_mobile(self):
        assert fx.parse_status_url("https://www.twitter.com/a/status/9") == "9"
        assert fx.parse_status_url("https://mobile.twitter.com/a/status/9/photo/1") == "9"

    def test_i_web_status(self):
        assert fx.parse_status_url("https://x.com/i/web/status/42") == "42"

    def test_profile_rejected(self):
        assert fx.parse_status_url("https://x.com/example_user") is None

    def test_search_rejected(self):
        assert fx.parse_status_url("https://twitter.com/search?q=status") is None

    def test_article_link_rejected(self):
        assert fx.parse_status_url("https://x.com/i/article/1234567890123456789") is None

    def test_other_host_rejected(self):
        assert fx.parse_status_url("https://example.com/a/status/9") is None
        assert fx.parse_status_url("https://notx.com/a/status/9") is None

    def test_empty(self):
        assert fx.parse_status_url("") is None


# --- article rendering ----------------------------------------------------------

class TestArticleToText:
    def test_atomic_blocks_resolve_by_key_field_not_position(self):
        text, warnings = fx.article_to_text(_article_content())
        parts = text.split("\n\n")
        # Block d references key 7 (the MARKDOWN prompt), which sits at list position 2.
        assert parts[3] == "```python\nPROMPT BODY\n```"
        assert parts[4] == "---"
        assert parts[5] == "[media]"
        assert warnings == []

    def test_empty_markdown_entity_warns_and_is_skipped(self):
        content = {
            "blocks": [{"type": "unstyled", "text": "Intro", "key": "a"},
                       {"type": "atomic", "key": "z", "entityRanges": [{"key": 1}]}],
            "entityMap": [{"key": "1", "value": {"type": "MARKDOWN", "data": {}}}],
        }
        text, warnings = fx.article_to_text(content)
        assert text == "Intro"
        assert warnings == ["embedded MARKDOWN block has no content"]

    def test_block_prefixes(self):
        text, _ = fx.article_to_text(_article_content())
        parts = text.split("\n\n")
        assert parts[0] == "Intro line."
        assert parts[1] == "## 01. first step"
        assert parts[2] == "- item one"
        assert parts[-1] == "1. numbered"

    def test_blank_text_blocks_skipped(self):
        text, _ = fx.article_to_text(_article_content())
        assert "\n\n\n\n" not in text
        assert len(text.split("\n\n")) == 7

    def test_non_list_entity_map_warns_instead_of_dropping(self):
        content = _article_content()
        content["entityMap"] = {e["key"]: e for e in content["entityMap"]}
        text, warnings = fx.article_to_text(content)
        assert "PROMPT BODY" not in text
        assert len(warnings) == 3 and all("missing entity" in w for w in warnings)

    def test_absent_entity_map_warns_instead_of_crashing(self):
        content = {"blocks": [{"type": "atomic", "key": "z", "entityRanges": [{"key": 1}]}]}
        text, warnings = fx.article_to_text(content)
        assert text == "" and warnings == ["atomic block z references missing entity 1"]

    def test_malformed_entity_entries_skipped(self):
        content = {
            "blocks": [{"type": "atomic", "key": "z", "entityRanges": [{"key": 1}]}],
            "entityMap": ["junk", {"value": {"type": "DIVIDER"}}, {"key": "1", "value": {"type": "DIVIDER", "data": {}}}],
        }
        text, warnings = fx.article_to_text(content)
        assert text == "---" and warnings == []

    def test_atomic_block_own_text_not_duplicated(self):
        content = {
            "blocks": [{"type": "atomic", "text": "caption", "key": "z", "entityRanges": [{"key": 1}]}],
            "entityMap": [{"key": "1", "value": {"type": "DIVIDER", "data": {}}}],
        }
        text, _ = fx.article_to_text(content)
        assert text == "---"

    def test_unknown_block_type_warns_once(self):
        content = {"blocks": [{"type": "code-block", "text": t, "key": t} for t in ("a", "b")], "entityMap": []}
        text, warnings = fx.article_to_text(content)
        assert text == "a\n\nb"
        assert warnings == ["unknown block type rendered as plain text: code-block"]

    def test_missing_entity_warns(self):
        content = {"blocks": [{"type": "atomic", "key": "z", "entityRanges": [{"key": 99}]}], "entityMap": []}
        text, warnings = fx.article_to_text(content)
        assert text == ""
        assert warnings == ["atomic block z references missing entity 99"]

    def test_atomic_without_entity_warns(self):
        content = {"blocks": [{"type": "atomic", "key": "z", "entityRanges": []}], "entityMap": []}
        _, warnings = fx.article_to_text(content)
        assert warnings == ["atomic block z has no entity"]

    def test_unknown_entity_type_warns_once(self):
        content = {
            "blocks": [{"type": "atomic", "key": k, "entityRanges": [{"key": 1}]} for k in "xy"],
            "entityMap": [{"key": "1", "value": {"type": "TWEET", "data": {}}}],
        }
        text, warnings = fx.article_to_text(content)
        assert text == "[TWEET embed]\n\n[TWEET embed]"
        assert warnings == ["unrendered embed type: TWEET"]



# --- normalization ---------------------------------------------------------------

class TestNormalize:
    def test_plain_post(self):
        b = fx.normalize(_payload(), "123", "u")
        assert b["ok"] is True and b["kind"] == "post"
        assert b["text"] == "hello world from a plain post"
        assert b["url"] == "https://x.com/example_user/status/123"
        assert b["metrics"] == {"likes": 1, "retweets": 2, "replies": 3, "bookmarks": 4, "views": 5}
        assert b["author"]["handle"] == "example_user"
        assert b["warnings"] == []

    def test_article_uses_body_not_tweet_text(self):
        art = {"title": "My Guide", "content": _article_content()}
        b = fx.normalize(_payload(article=art, text="https://t.co/xyz"), "123", "u")
        assert b["kind"] == "article"
        assert b["title"] == "My Guide" and b["slug"] == "my-guide"
        assert "PROMPT BODY" in b["text"] and "t.co" not in b["text"]

    def test_empty_article_is_schema_drift(self):
        art = {"title": "T", "content": {"blocks": [], "entityMap": []}}
        b = fx.normalize(_payload(article=art), "123", "u")
        assert b["ok"] is False and b["reason_code"] == "schema_drift"

    def test_article_without_content_is_schema_drift(self):
        for art in ({"title": "T"}, {"title": "T", "content": None}, {"content": []}, "junk"):
            b = fx.normalize(_payload(article=art, text="https://t.co/x"), "123", "u")
            assert b["ok"] is False and b["reason_code"] == "schema_drift", art

    def test_empty_post_text_is_no_text(self):
        for t in ("", "   ", None):
            b = fx.normalize(_payload(text=t), "123", "u")
            assert b["ok"] is False and b["reason_code"] == "no_text"

    def test_quote_appended_with_attribution(self):
        q = {"text": "line one\nline two", "author": {"screen_name": "other_user"}}
        b = fx.normalize(_payload(quote=q), "123", "u")
        assert b["text"] == ("hello world from a plain post\n\n> Quoted post by @other_user:\n"
                             "> line one\n> line two")
        assert b["warnings"] == []

    def test_unavailable_quote_warns(self):
        q = {"type": "tombstone", "reason": "deleted"}
        b = fx.normalize(_payload(quote=q), "123", "u")
        assert b["text"] == "hello world from a plain post"
        assert b["warnings"] == ["quoted post not included (deleted)"]

    def test_quote_without_reason_or_author(self):
        assert fx.render_quote({}) == (None, ["quoted post not included (no text)"])
        assert fx.render_quote({"text": "x"}) == ("> Quoted post by @unknown:\n> x", [])

    def test_unreadable_quote_warns(self):
        b = fx.normalize(_payload(quote="junk"), "123", "u")
        assert b["warnings"] == ["quoted post present in an unreadable shape; not included"]

    def test_quoted_article_warns(self):
        q = {"text": "https://t.co/q", "author": {"screen_name": "o"}, "article": {"title": "A"}}
        b = fx.normalize(_payload(quote=q), "123", "u")
        assert "> https://t.co/q" in b["text"]
        assert b["warnings"] == ["quoted post is an X Article; only its post text is included, fetch it separately"]

    def test_quote_on_article_is_appended(self):
        art = {"title": "My Guide", "content": _article_content()}
        q = {"text": "quoted", "author": {"screen_name": "o"}}
        b = fx.normalize(_payload(article=art, quote=q), "123", "u")
        assert b["text"].endswith("> Quoted post by @o:\n> quoted")

    def test_poll_rendered(self):
        poll = {"total_votes": 10, "choices": [{"label": "Yes", "count": 7, "percentage": 70},
                                               {"label": "No", "count": 3, "percentage": 30}, {"count": 0}]}
        b = fx.normalize(_payload(poll=poll), "123", "u")
        assert b["text"].endswith("\n\nPoll (10 votes):\n- Yes: 70% (7 votes)\n- No: 30% (3 votes)")
        assert b["warnings"] == []

    def test_poll_defaults(self):
        assert fx.render_poll({"choices": [{"label": "A"}]}) == ("Poll (unknown votes):\n- A: ?% (? votes)", [])

    def test_unreadable_poll_warns(self):
        for poll in ({}, {"choices": []}, "junk"):
            b = fx.normalize(_payload(poll=poll), "123", "u")
            assert b["text"] == "hello world from a plain post"
            assert b["warnings"] == ["poll present but has no readable choices; not included"]

    def test_media_and_community_note_warn(self):
        b = fx.normalize(_payload(media={"all": [{"type": "photo"}]}, community_note={"text": "n"}), "123", "u")
        assert b["warnings"] == ["this post has media (images or video) that is not extracted",
                                 "this post has a community note that is not extracted"]

    def test_empty_media_does_not_warn(self):
        assert fx.normalize(_payload(media={}, community_note=None), "123", "u")["warnings"] == []

    def test_unrecognized_field_warns(self):
        b = fx.normalize(_payload(translation={"text": "hola"}, empty_thing=[]), "123", "u")
        assert b["warnings"] == ["this post has an unrecognized field 'translation' that is not extracted"]

    def test_live_payload_fields_are_all_known(self):
        # Every key in the two live payloads captured 2026-10-19 is metadata or content.
        live = {"url", "id", "text", "raw_text", "author", "replies", "retweets", "likes",
                "bookmarks", "quotes", "created_at", "created_timestamp", "possibly_sensitive",
                "views", "is_note_tweet", "community_note", "lang", "replying_to",
                "replying_to_status", "article", "twitter_card", "color", "provider", "reposted_by"}
        assert live <= fx._POST_FIELDS
        live_article = {"created_at", "modified_at", "id", "title", "preview_text",
                        "cover_media", "content", "media_entities"}
        assert live_article <= fx._ARTICLE_FIELDS

    def test_card_label_does_not_warn_but_card_object_does(self):
        assert fx.normalize(_payload(twitter_card="summary", embed_card="tweet"), "123", "u")["warnings"] == []
        b = fx.normalize(_payload(twitter_card={"title": "LINK"}, embed_card=["x"]), "123", "u")
        assert b["warnings"] == ["this post has a link card ('twitter_card') that is not extracted",
                                 "this post has a link card ('embed_card') that is not extracted"]

    def test_unknown_article_field_warns(self):
        art = {"title": "T", "content": _article_content(), "preview_text": "p",
               "subtitle": "A DEK", "cover_media": {}, "media_entities": []}
        b = fx.normalize(_payload(article=art), "123", "u")
        assert b["ok"] is True
        assert b["warnings"] == ["the article has an unrecognized field 'subtitle' that is not extracted"]

    def test_article_images_warn(self):
        for extra in ({"cover_media": {"media_id": "1"}}, {"media_entities": [{"id": "2"}]}):
            art = {"title": "T", "content": _article_content(), **extra}
            b = fx.normalize(_payload(article=art), "123", "u")
            assert b["warnings"] == ["the article has images that are not extracted"]

    def test_unknown_poll_fields_warn_once(self):
        poll = {"question": "WHICH", "ends_at": "x",
                "choices": [{"label": "A", "count": 1, "description": "D1"},
                            {"label": "B", "description": "D2"}, "junk"]}
        block, warnings = fx.render_poll(poll)
        assert block == "Poll (unknown votes):\n- A: ?% (1 votes)\n- B: ?% (? votes)"
        assert warnings == ["the poll has an unrecognized field 'question' that is not extracted",
                            "a poll choice has an unrecognized field 'description' that is not extracted"]

    def test_nested_quote_and_poll_rendered(self):
        inner = {"text": "inner", "author": {"screen_name": "c"}}
        mid = {"text": "mid", "author": {"screen_name": "b"}, "quote": inner,
               "poll": {"choices": [{"label": "P", "percentage": 1}]}}
        b = fx.normalize(_payload(quote=mid), "123", "u")
        assert b["text"] == ("hello world from a plain post\n\n> Quoted post by @b:\n> mid\n>\n"
                             "> Poll (unknown votes):\n> - P: 1% (? votes)\n>\n> > Quoted post by @c:\n> > inner")
        assert b["warnings"] == []

    def test_quoted_post_adjunct_warnings_name_the_level(self):
        mid = {"text": "mid", "author": {}, "media": {"all": [1]}, "replying_to_status": "5"}
        b = fx.normalize(_payload(quote=mid), "123", "u")
        assert b["warnings"] == ["a quoted post (level 1) has media (images or video) that is not extracted",
                                 "a quoted post (level 1) replies to another post; the thread was not fetched"]

    def test_quote_depth_limit(self):
        q = {"text": "deepest"}
        for _ in range(fx.MAX_QUOTE_DEPTH):
            q = {"text": "level", "quote": q}
        b = fx.normalize(_payload(quote=q), "123", "u")
        assert "deepest" not in b["text"]
        assert b["text"].count("Quoted post by") == fx.MAX_QUOTE_DEPTH
        assert b["warnings"] == [f"quoted posts nested deeper than {fx.MAX_QUOTE_DEPTH} levels not included"]

    def test_null_article_is_a_post(self):
        b = fx.normalize(_payload(article=None), "123", "u")
        assert b["kind"] == "post"

    def test_reply_warns_about_thread(self):
        b = fx.normalize(_payload(replying_to_status="99"), "123", "u")
        assert any("thread" in w for w in b["warnings"])

    def test_api_error_codes(self):
        assert fx.normalize({"code": 404, "message": "x"}, "1", "u")["reason_code"] == "not_found"
        assert fx.normalize({"code": 401, "message": "x"}, "1", "u")["reason_code"] == "private"
        assert fx.normalize({"code": 500, "message": "x"}, "1", "u")["reason_code"] == "http_error"

    def test_missing_tweet_is_schema_drift(self):
        assert fx.normalize({"code": 200}, "1", "u")["reason_code"] == "schema_drift"
        assert fx.normalize({"code": 200, "tweet": {"id": "1"}}, "1", "u")["reason_code"] == "schema_drift"
        assert fx.normalize([], "1", "u")["reason_code"] == "schema_drift"


# --- build_block: transport failures -----------------------------------------------

class TestBuildBlock:
    URL = "https://x.com/example_user/status/123"

    def test_ok_path_hits_status_endpoint(self):
        g = _getter()
        b = fx.build_block(self.URL, getter=g)
        assert b["ok"] is True
        assert g.endpoint == "https://api.fxtwitter.com/status/123"

    def test_bad_url_never_calls_network(self):
        g = _getter(exc=AssertionError("network called"))
        assert fx.build_block("https://x.com/example_user", getter=g)["reason_code"] == "bad_url"

    def test_html_page_is_unavailable_not_not_found(self):
        # A missing status returned HTTP 200 + an HTML page (live, 2026-10-19), but an
        # HTML error page looks the same, so it must not be reported as not_found.
        b = fx.build_block(self.URL, getter=_getter(body="  <!DOCTYPE html><html>"))
        assert b["ok"] is False and b["reason_code"] == "unavailable"

    def test_invalid_json_is_schema_drift(self):
        b = fx.build_block(self.URL, getter=_getter(body="{not json"))
        assert b["reason_code"] == "schema_drift"

    def test_http_errors(self):
        for code, reason in ((404, "not_found"), (401, "private"), (503, "http_error")):
            exc = urllib.error.HTTPError(self.URL, code, "x", {}, None)
            assert fx.build_block(self.URL, getter=_getter(exc=exc))["reason_code"] == reason

    def test_network_error(self):
        b = fx.build_block(self.URL, getter=_getter(exc=urllib.error.URLError("down")))
        assert b["reason_code"] == "network_error"

    def test_timeout(self):
        b = fx.build_block(self.URL, getter=_getter(exc=TimeoutError("slow")))
        assert b["reason_code"] == "network_error"


class TestSlugify:
    def test_empty_uses_fallback(self):
        assert fx.slugify("") == "x-post"
        assert fx.slugify("!!!") == "x-post"

    def test_basic(self):
        assert fx.slugify("EXAMPLE TITLE: a 10-step guide") == "example-title-a-10-step-guide"


class TestMain:
    def _run(self, monkeypatch, capsys, block):
        seen = {}

        def fake(url):
            seen["url"] = url
            return block
        monkeypatch.setattr(fx, "build_block", fake)
        rc = fx.main(["https://x.com/example_user/status/123"])
        return rc, json.loads(capsys.readouterr().out), seen

    def test_success_exit_zero_and_json(self, monkeypatch, capsys):
        rc, out, seen = self._run(monkeypatch, capsys, {"ok": True, "text": "é"})
        assert rc == 0 and out == {"ok": True, "text": "é"}
        assert seen["url"] == "https://x.com/example_user/status/123"

    def test_failure_exit_one(self, monkeypatch, capsys):
        rc, out, _ = self._run(monkeypatch, capsys, {"ok": False, "reason_code": "bad_url"})
        assert rc == 1 and out["reason_code"] == "bad_url"
