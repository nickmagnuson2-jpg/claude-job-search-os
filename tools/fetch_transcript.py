#!/usr/bin/env python3
"""fetch_transcript.py - pull a YouTube transcript + metadata into the job-search OS.

Emits a JSON ExtractionBlock to stdout AND (by default) caches a markdown copy at
data/source-transcripts/<video_id>.md. Reusable by /analyze, /reflect, /remember -
the script is the load-bearing extract step; callers consume the JSON.

Dependency: youtube-transcript-api (user-site, installed with --break-system-packages
to match the repo's other optional deps). Metadata (title/author) comes from YouTube's
oEmbed endpoint via stdlib urllib - no API key.

ALWAYS run with PYTHONIOENCODING=utf-8 (repo convention) or it crashes on Unicode.

Output JSON shape (the ExtractionBlock contract):
  {
    "ok": true,
    "source_type": "video",
    "video_id": "...", "url": "...", "slug": "...",
    "title": "...", "author": "...",
    "language": "English (auto-generated)", "language_code": "en",
    "is_auto_generated": true,
    "text": "full plaintext transcript ...",
    "segments": [{"text": "...", "start": 0.32, "duration": 6.8}, ...],
    "cached_path": "data/source-transcripts/<id>.md"   # or null with --no-save
  }
On failure:
  {"ok": false, "error": "human message", "reason_code": "no_captions|private|...",
   "video_id": "...", "url": "..."}

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/fetch_transcript.py <youtube-url>
  PYTHONIOENCODING=utf-8 python3 tools/fetch_transcript.py <url> --languages en es --no-save
"""

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# --- deterministic helpers (unit-tested; no network) -------------------------

# 11-char YouTube id: letters, digits, dash, underscore.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_video_id(url: str):
    """Extract the 11-char video id from any common YouTube URL form, or None.

    Handles watch?v=, youtu.be/, /embed/, /shorts/, /live/, and a bare id.
    """
    if not url:
        return None
    url = url.strip()

    # Bare id passed directly.
    if _VIDEO_ID_RE.match(url):
        return url

    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None

    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    # youtu.be/<id>
    if host.endswith("youtu.be"):
        candidate = path.lstrip("/").split("/")[0]
        return candidate if _VIDEO_ID_RE.match(candidate) else None

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        # watch?v=<id>
        qs = urllib.parse.parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            candidate = qs["v"][0]
            if _VIDEO_ID_RE.match(candidate):
                return candidate
        # /embed/<id>, /shorts/<id>, /live/<id>, /v/<id>
        for prefix in ("/embed/", "/shorts/", "/live/", "/v/"):
            if path.startswith(prefix):
                candidate = path[len(prefix):].split("/")[0]
                if _VIDEO_ID_RE.match(candidate):
                    return candidate
    return None


def segments_to_text(segments) -> str:
    """Join transcript snippets into clean plaintext.

    Caption snippets carry mid-text newlines and inconsistent spacing; collapse
    all whitespace runs to single spaces so downstream prose-reading is clean.
    """
    joined = " ".join(seg.get("text", "") for seg in segments)
    return re.sub(r"\s+", " ", joined).strip()


def slugify(text: str, fallback: str = "video") -> str:
    """lowercase-hyphens slug from a title (repo output convention)."""
    if not text:
        return fallback
    s = text.lower()
    s = re.sub(r"[^\w\s-]", "", s)        # drop punctuation
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:80] or fallback


def to_markdown(block: dict) -> str:
    """Render the ExtractionBlock as a cacheable markdown doc with frontmatter."""
    fm = [
        "---",
        f"video_id: {block['video_id']}",
        f"title: {json.dumps(block.get('title') or '')}",
        f"author: {json.dumps(block.get('author') or '')}",
        f"url: {block['url']}",
        f"language: {json.dumps(block.get('language') or '')}",
        f"is_auto_generated: {str(bool(block.get('is_auto_generated'))).lower()}",
        f"segments: {len(block.get('segments') or [])}",
        "---",
        "",
        f"# {block.get('title') or block['video_id']}",
        "",
        block.get("text", ""),
        "",
    ]
    return "\n".join(fm)


# --- network steps (manual smoke test; not in CI) ----------------------------


def fetch_metadata(url: str) -> dict:
    """Title + author via YouTube oEmbed. Returns {} on any failure (optional data)."""
    try:
        endpoint = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
            {"url": url, "format": "json"}
        )
        with urllib.request.urlopen(endpoint, timeout=10) as resp:
            data = json.load(resp)
        return {"title": data.get("title"), "author": data.get("author_name")}
    except Exception:
        return {}


def fetch_transcript(video_id: str, languages):
    """Fetch the best transcript, preferring `languages`, falling back to any.

    Returns (info_dict, segments_list). Raises the library's typed exceptions,
    which main() maps to reason codes.
    """
    from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound

    ytt = YouTubeTranscriptApi()
    try:
        fetched = ytt.fetch(video_id, languages=languages)
    except NoTranscriptFound:
        # Requested languages unavailable: take whatever the video does have.
        tl = ytt.list(video_id)
        transcript = next(iter(tl))  # first available track
        fetched = transcript.fetch()

    segments = [
        {"text": s.text, "start": s.start, "duration": s.duration} for s in fetched
    ]
    info = {
        "language": fetched.language,
        "language_code": fetched.language_code,
        "is_generated": fetched.is_generated,
    }
    return info, segments


# --- orchestration -----------------------------------------------------------


def build_block(url: str, languages, repo_root: Path, save: bool) -> dict:
    video_id = parse_video_id(url)
    if not video_id:
        return {
            "ok": False,
            "error": f"Could not parse a YouTube video id from: {url}",
            "reason_code": "bad_url",
            "video_id": None,
            "url": url,
        }

    canonical = f"https://www.youtube.com/watch?v={video_id}"

    try:
        info, segments = fetch_transcript(video_id, languages)
    except ImportError:
        return {
            "ok": False,
            "error": "youtube-transcript-api not installed. Run: python3 -m pip "
                     "install --user --break-system-packages youtube-transcript-api",
            "reason_code": "dep_missing",
            "video_id": video_id,
            "url": canonical,
        }
    except Exception as exc:  # map typed library errors to reason codes
        name = type(exc).__name__
        reason = {
            "TranscriptsDisabled": "no_captions",
            "NoTranscriptFound": "no_captions",
            "VideoUnavailable": "private",
            "VideoUnplayable": "private",
            "AgeRestricted": "age_restricted",
            "InvalidVideoId": "bad_url",
            "IpBlocked": "ip_blocked",
            "RequestBlocked": "ip_blocked",
        }.get(name, "fetch_failed")
        return {
            "ok": False,
            "error": f"{name}: {exc}",
            "reason_code": reason,
            "video_id": video_id,
            "url": canonical,
        }

    meta = fetch_metadata(canonical)
    title = meta.get("title")

    block = {
        "ok": True,
        "source_type": "video",
        "video_id": video_id,
        "url": canonical,
        "slug": slugify(title or video_id),
        "title": title,
        "author": meta.get("author"),
        "language": info["language"],
        "language_code": info["language_code"],
        "is_auto_generated": info["is_generated"],
        "text": segments_to_text(segments),
        "segments": segments,
        "cached_path": None,
    }

    if save:
        cache_dir = repo_root / "data" / "source-transcripts"
        cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = cache_dir / f"{video_id}.md"
        out_path.write_text(to_markdown(block), encoding="utf-8")
        # Report a repo-relative path for portability.
        try:
            block["cached_path"] = str(out_path.relative_to(repo_root))
        except ValueError:
            block["cached_path"] = str(out_path)

    return block


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("url", help="YouTube URL or bare 11-char video id")
    parser.add_argument(
        "--languages", nargs="+", default=["en"],
        help="Preferred language codes in priority order (default: en). "
             "Falls back to any available track.",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Do not cache the markdown copy; emit JSON only.",
    )
    parser.add_argument(
        "--repo-root", default=".", help="Repo root for the cache dir (default: cwd).",
    )
    args = parser.parse_args(argv)

    block = build_block(
        args.url,
        languages=args.languages,
        repo_root=Path(args.repo_root).resolve(),
        save=not args.no_save,
    )
    print(json.dumps(block, ensure_ascii=False, indent=2))
    return 0 if block.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
