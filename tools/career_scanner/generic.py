"""
generic.py - Playwright-based generic career page parser.

Extracts job listings from arbitrary career pages using heuristic CSS
selectors. Best-effort fallback for companies without a known ATS API.

Requires: pip install playwright && playwright install chromium
"""
import sys
from urllib.parse import urljoin


def fetch_generic(careers_url: str, company_name: str) -> list[dict]:
    """Extract job listings from an arbitrary careers page using Playwright.

    Uses heuristic CSS selectors to find job listing links. Best-effort:
    may miss listings on complex SPAs or non-standard page structures.

    Args:
        careers_url: Full URL to the company's careers/jobs page.
        company_name: Company name for the role dicts.

    Returns:
        List of standardized role dicts, or [] on error.
    """
    try:
        from playwright.sync_api import sync_playwright, Error as PlaywrightError
    except ImportError:
        print(
            "Playwright not installed. Run: pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        return []

    roles = []
    pw = None
    browser = None

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page.goto(careers_url, timeout=30000, wait_until="domcontentloaded")

        # Wait briefly for dynamic content
        page.wait_for_timeout(2000)

        # Heuristic selectors, tried in order of specificity
        selectors = [
            # Apply links (most specific)
            'a[href*="apply"]',
            # Job/position/career links
            'a[href*="job"]',
            'a[href*="position"]',
            'a[href*="career"]',
            'a[href*="opening"]',
            # Heading links within list-like containers
            "h2 a, h3 a, h4 a",
            # List item links in job-related containers
            '[class*="job"] li a, [class*="position"] li a, '
            '[class*="opening"] li a, [class*="career"] li a, '
            '[id*="job"] li a, [id*="position"] li a, '
            '[id*="opening"] li a, [id*="career"] li a',
        ]

        seen_urls = set()
        for selector in selectors:
            try:
                links = page.query_selector_all(selector)
            except Exception:
                continue

            for link in links:
                try:
                    text = (link.inner_text() or "").strip()
                    href = link.get_attribute("href") or ""
                except Exception:
                    continue

                if not text or not href or len(text) < 3:
                    continue

                # Skip navigation, social, and generic links
                skip_patterns = [
                    "login", "sign", "about", "blog", "contact",
                    "privacy", "terms", "facebook", "twitter",
                    "linkedin.com", "instagram", "youtube",
                    "mailto:", "javascript:", "#",
                ]
                href_lower = href.lower()
                if any(pat in href_lower for pat in skip_patterns):
                    continue

                # Resolve relative URLs
                full_url = urljoin(careers_url, href)

                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                roles.append({
                    "title": text[:200],  # Cap title length
                    "company": company_name,
                    "department": "",
                    "team": "",
                    "location": "",
                    "remote": False,
                    "employment_type": "",
                    "url": full_url,
                    "apply_url": full_url,
                    "published_at": "",
                    "description_plain": "",
                    "ats": "generic",
                })

    except Exception as e:
        print(f"Generic parser error for {careers_url}: {e}", file=sys.stderr)
        return []
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass

    return roles
