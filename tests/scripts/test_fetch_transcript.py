"""Deterministic unit tests for tools/fetch_transcript.py.

Covers the no-network parts: URL->video_id parsing, segment->plaintext join,
slugify, and markdown rendering. The live YouTube fetch is a MANUAL smoke test
(network is flaky, blocked in CI), per the repo test-suite convention:
  PYTHONIOENCODING=utf-8 python3 tools/fetch_transcript.py <url>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import fetch_transcript as ft  # noqa: E402


class TestParseVideoId:
    def test_watch_url(self):
        assert ft.parse_video_id("https://www.youtube.com/watch?v=IREnr4I89Ho") == "IREnr4I89Ho"

    def test_watch_url_extra_params(self):
        url = "https://www.youtube.com/watch?v=IREnr4I89Ho&t=42s&list=PLxyz"
        assert ft.parse_video_id(url) == "IREnr4I89Ho"

    def test_youtu_be_short(self):
        assert ft.parse_video_id("https://youtu.be/IREnr4I89Ho") == "IREnr4I89Ho"

    def test_youtu_be_with_query(self):
        assert ft.parse_video_id("https://youtu.be/IREnr4I89Ho?t=10") == "IREnr4I89Ho"

    def test_embed(self):
        assert ft.parse_video_id("https://www.youtube.com/embed/IREnr4I89Ho") == "IREnr4I89Ho"

    def test_shorts(self):
        assert ft.parse_video_id("https://www.youtube.com/shorts/IREnr4I89Ho") == "IREnr4I89Ho"

    def test_live(self):
        assert ft.parse_video_id("https://www.youtube.com/live/IREnr4I89Ho") == "IREnr4I89Ho"

    def test_bare_id(self):
        assert ft.parse_video_id("IREnr4I89Ho") == "IREnr4I89Ho"

    def test_nocookie_host(self):
        assert ft.parse_video_id("https://www.youtube-nocookie.com/embed/IREnr4I89Ho") == "IREnr4I89Ho"

    def test_whitespace_trimmed(self):
        assert ft.parse_video_id("  https://youtu.be/IREnr4I89Ho  ") == "IREnr4I89Ho"

    def test_non_youtube_url(self):
        assert ft.parse_video_id("https://vimeo.com/123456789") is None

    def test_empty(self):
        assert ft.parse_video_id("") is None
        assert ft.parse_video_id(None) is None

    def test_malformed_id_rejected(self):
        # too short / wrong length must not pass as a bare id
        assert ft.parse_video_id("abc") is None
        assert ft.parse_video_id("https://www.youtube.com/watch?v=short") is None


class TestSegmentsToText:
    def test_join_and_collapse_whitespace(self):
        segs = [
            {"text": "It's here.", "start": 0.0, "duration": 1.0},
            {"text": "The model,\nthe myth,", "start": 1.0, "duration": 2.0},
            {"text": "the legend.", "start": 3.0, "duration": 1.0},
        ]
        assert ft.segments_to_text(segs) == "It's here. The model, the myth, the legend."

    def test_empty_segments(self):
        assert ft.segments_to_text([]) == ""

    def test_missing_text_key(self):
        assert ft.segments_to_text([{"start": 0.0}]) == ""


class TestSlugify:
    def test_basic(self):
        assert ft.slugify("Claude Fable 5 (Mythos)") == "claude-fable-5-mythos"

    def test_punctuation_and_accents(self):
        assert ft.slugify("is the world's best?") == "is-the-worlds-best"

    def test_empty_uses_fallback(self):
        assert ft.slugify("", fallback="video") == "video"
        assert ft.slugify("!!!", fallback="vid") == "vid"

    def test_truncates_long(self):
        assert len(ft.slugify("a " * 100)) <= 80


class TestToMarkdown:
    def test_frontmatter_and_body(self):
        block = {
            "video_id": "IREnr4I89Ho",
            "title": "Claude Fable 5 (Mythos)",
            "author": "How I AI",
            "url": "https://www.youtube.com/watch?v=IREnr4I89Ho",
            "language": "English (auto-generated)",
            "is_auto_generated": True,
            "text": "It's here. The model.",
            "segments": [{"text": "x", "start": 0, "duration": 1}],
        }
        md = ft.to_markdown(block)
        assert md.startswith("---\n")
        assert "video_id: IREnr4I89Ho" in md
        assert "is_auto_generated: true" in md
        assert "segments: 1" in md
        assert "# Claude Fable 5 (Mythos)" in md
        assert "It's here. The model." in md

    def test_title_falls_back_to_id(self):
        block = {
            "video_id": "abc12345678",
            "title": None,
            "author": None,
            "url": "https://www.youtube.com/watch?v=abc12345678",
            "language": "en",
            "is_auto_generated": False,
            "text": "body",
            "segments": [],
        }
        md = ft.to_markdown(block)
        assert "# abc12345678" in md


class TestBuildBlockBadUrl:
    def test_unparseable_url_returns_error_block(self, tmp_path):
        block = ft.build_block(
            "https://vimeo.com/123", languages=["en"], repo_root=tmp_path, save=False
        )
        assert block["ok"] is False
        assert block["reason_code"] == "bad_url"
