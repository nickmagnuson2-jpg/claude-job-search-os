"""url_segment.py - one place where an ATS slug becomes a URL path segment.

Every ATS parser (ashby, greenhouse, lever) builds its endpoint from a slug. A raw
space reached http.client, which raised InvalidURL past the parsers' error channel
(2026-11-18, output/funnel-intake/111826-noboard-pilot/report.md). The parsers keep
the raw slug as the role's "company"; only the URL uses this.
"""
import urllib.parse


def quote_segment(slug: str) -> str:
    """The slug as one URL path segment.

    Unquote first so a slug copied from a board URL ("Acme%20AI") is not encoded twice;
    safe="" so a "/" cannot change the path.
    """
    return urllib.parse.quote(urllib.parse.unquote(slug), safe="")
