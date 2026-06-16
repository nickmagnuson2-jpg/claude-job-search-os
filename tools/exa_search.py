#!/usr/bin/env python3
"""Exa neural-search wrapper for /research-company Agent 6.

Stdlib only (urllib). Auto-loads .env. Returns structured JSON to stdout.
Uses the /search endpoint with contents=text. Does NOT use /findSimilar
(deprecated 2026-05 per memory feedback_verify_agent_research_before_propagation).

Usage:
  PYTHONIOENCODING=utf-8 python3 tools/exa_search.py "<query>" \
      [--num 6] [--type auto|neural|keyword] [--days 365] \
      [--category company|news|research paper|financial report] \
      [--snippet-chars 1200]

Exit codes: 0 ok, 1 config/HTTP error (JSON error body still printed).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

EXA_ENDPOINT = "https://api.exa.ai/search"


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.is_file():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.replace("export ", "").strip()
                    os.environ.setdefault(k, v.strip())


def _fail(msg: str) -> None:
    print(json.dumps({"query": None, "results": [], "error": msg}))
    sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser(description="Exa neural search wrapper")
    p.add_argument("query")
    p.add_argument("--num", type=int, default=6)
    p.add_argument("--type", default="auto", choices=["auto", "neural", "keyword"])
    p.add_argument("--days", type=int, default=0,
                   help="Restrict to results published within N days (0 = no filter)")
    p.add_argument("--category", default="",
                   help="Exa category filter, e.g. 'company', 'news', 'financial report'")
    p.add_argument("--snippet-chars", type=int, default=1200)
    args = p.parse_args()

    _load_dotenv()
    api_key = os.environ.get("EXA_API_KEY", "").strip()
    if not api_key:
        _fail("EXA_API_KEY not set in .env")

    body = {
        "query": args.query,
        "type": args.type,
        "numResults": max(1, min(args.num, 25)),
        "contents": {"text": {"maxCharacters": max(200, args.snippet_chars)}},
    }
    if args.category:
        body["category"] = args.category
    if args.days and args.days > 0:
        from datetime import datetime, timedelta, timezone
        start = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        body["startPublishedDate"] = start

    req = urllib.request.Request(
        EXA_ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        _fail(f"Exa HTTP {e.code}: {detail or e.reason}")
    except urllib.error.URLError as e:
        _fail(f"Exa network error: {e.reason}")
    except Exception as e:  # noqa: BLE001
        _fail(f"Exa unexpected error: {e}")

    results = []
    for r in payload.get("results", []):
        text = (r.get("text") or "").strip().replace("\n", " ")
        if len(text) > args.snippet_chars:
            text = text[: args.snippet_chars].rsplit(" ", 1)[0] + "…"
        results.append({
            "title": r.get("title") or "(untitled)",
            "url": r.get("url"),
            "published": r.get("publishedDate"),
            "author": r.get("author"),
            "score": r.get("score"),
            "snippet": text,
        })

    print(json.dumps({
        "query": args.query,
        "type": args.type,
        "result_count": len(results),
        "results": results,
        "error": None,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
