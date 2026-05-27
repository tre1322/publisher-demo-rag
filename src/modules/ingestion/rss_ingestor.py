"""Tier 1 ingestion: RSS feed + full-text extraction.

Works with any publisher that exposes an RSS/Atom feed — WordPress,
TownNews/BLOX, Lee Enterprises, custom CMSes, everything. No CMS-
specific code required.

Flow:
  1. Parse RSS feed (feedparser) → list of article URLs + metadata
  2. Fetch each article URL (httpx)
  3. Extract body text (BeautifulSoup selector cascade)
  4. Return StandardArticle dicts → shared_write_layer.write_articles()

Usage:
    ingestor = RSSIngestor("https://windomnews.com/feed", timeout=15)
    articles = ingestor.fetch(since="2026-04-01")
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CMS-agnostic body selector cascade
# Tries each selector in order; uses the first that yields ≥ 200 chars.
# ---------------------------------------------------------------------------
_BODY_SELECTORS = [
    "article .entry-content",      # WordPress standard
    "article .post-content",       # WordPress alternate
    ".article-body",               # TownNews / BLOX
    ".story-body",                 # Lee Enterprises
    ".field-items .field-item",    # Drupal
    ".article__body",              # generic BEM
    ".post-body",                  # generic
    "article",                     # HTML5 semantic
    ".entry-content",              # WordPress fallback
    ".post-content",               # generic fallback
    "main article",                # scoped main
    "main",                        # last resort
]

# Tags to strip from extracted body (ads, nav, widgets)
_STRIP_TAGS = [
    "aside", "nav", "footer", "header", "figure", "figcaption",
    "script", "style", "noscript", "iframe", "form", "button",
    ".ad", ".advertisement", ".related-stories", ".related-articles",
    ".newsletter-signup", ".social-share", ".tags", ".author-bio",
    ".comments", "#comments",
]

# Minimum body length to accept an extraction
_MIN_BODY_LEN = 150

# Minimum publication date to accept (skip ancient syndicated content)
_MIN_YEAR = 2010


def _extract_body(html: str, url: str) -> str:
    """Extract article body text from arbitrary news HTML.

    Returns empty string if no selector produces usable content.
    """
    soup = BeautifulSoup(html, "html.parser")

    for selector in _BODY_SELECTORS:
        try:
            el = soup.select_one(selector)
        except Exception:
            continue
        if not el:
            continue

        # Remove noise tags inside the candidate element
        for noise_sel in _STRIP_TAGS:
            for noise_el in el.select(noise_sel):
                noise_el.decompose()

        text = el.get_text(separator="\n", strip=True)
        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) >= _MIN_BODY_LEN:
            logger.debug(
                f"Extracted {len(text)} chars from {url!r} using selector {selector!r}"
            )
            return text

    logger.warning(f"Body extraction failed for {url!r} — all selectors empty")
    return ""


def _parse_date(entry: Any) -> str | None:
    """Return ISO date string from a feedparser entry, or None."""
    # feedparser populates published_parsed (time.struct_time in UTC)
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.date().isoformat()
        except Exception:
            pass
    # Fallback: raw published string → email date parser
    raw = getattr(entry, "published", "") or ""
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            return dt.date().isoformat()
        except Exception:
            pass
    return None


def _infer_section(entry: Any) -> str:
    """Guess section from feed categories."""
    tags = [
        t.get("term", "").lower()
        for t in getattr(entry, "tags", [])
    ]
    if any(t in tags for t in ("sports", "athletics", "prep sports")):
        return "sports"
    if any(t in tags for t in ("obituary", "obituaries", "obits")):
        return "obituary"
    if any(t in tags for t in ("opinion", "editorial", "column", "letter")):
        return "opinion"
    return "news"


class RSSIngestor:
    """Fetch and extract articles from any RSS/Atom feed.

    Args:
        rss_url: Full URL of the RSS or Atom feed.
        timeout: HTTP timeout in seconds for article fetches.
        headers: Optional extra HTTP headers (e.g. auth cookies for
                 subscription-gated sites).
    """

    def __init__(
        self,
        rss_url: str,
        timeout: int = 15,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.rss_url = rss_url
        self.timeout = timeout
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; GrandNetworkBot/1.0; "
                "+https://grandnetwork.ai/bot)"
            ),
            **(headers or {}),
        }

    def fetch(
        self,
        since: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Return StandardArticle dicts for articles published since `since`.

        Args:
            since: ISO date string, e.g. "2026-04-01". Articles on or after
                   this date are returned. None = no date filter.
            limit: Max articles to return (safety cap).

        Returns:
            List of StandardArticle dicts compatible with
            shared_write_layer.write_articles().
        """
        since_date = None
        if since:
            try:
                since_date = datetime.fromisoformat(since).date()
            except ValueError:
                logger.warning(f"Invalid since date {since!r}; ignoring filter")

        logger.info(f"Fetching RSS feed: {self.rss_url}")
        feed = feedparser.parse(self.rss_url)

        if feed.bozo:
            logger.warning(
                f"RSS parse warning for {self.rss_url}: {feed.bozo_exception}"
            )

        entries = feed.entries[:limit]
        logger.info(f"RSS feed has {len(feed.entries)} entries (processing {len(entries)})")

        articles: list[dict] = []

        with httpx.Client(
            timeout=self.timeout,
            headers=self.headers,
            follow_redirects=True,
        ) as client:
            for entry in entries:
                url = getattr(entry, "link", "") or ""
                title = getattr(entry, "title", "") or ""

                # Date filter
                pub_date = _parse_date(entry)
                if pub_date and since_date:
                    from datetime import date as _date
                    try:
                        if _date.fromisoformat(pub_date) < since_date:
                            continue
                    except ValueError:
                        pass

                # Skip very old content
                if pub_date:
                    try:
                        if int(pub_date[:4]) < _MIN_YEAR:
                            continue
                    except (ValueError, IndexError):
                        pass

                if not url:
                    logger.debug(f"Skipping entry with no URL: {title!r}")
                    continue

                # Fetch full article HTML
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    html = resp.text
                except Exception as e:
                    logger.warning(f"Failed to fetch {url!r}: {e}")
                    continue

                body = _extract_body(html, url)
                if not body:
                    logger.debug(f"Skipping {url!r} — body extraction empty")
                    continue

                # Author: try dc:creator, then feed author
                author = (
                    getattr(entry, "author", "")
                    or getattr(entry, "dc_creator", "")
                    or ""
                )

                articles.append({
                    "headline": title,
                    "body_text": body,
                    "byline": author,
                    "publish_date": pub_date or "",
                    "url": url,
                    "content_type": _infer_section(entry),
                    "source_pipeline": "rss",
                    "extraction_confidence": 0.85,
                    "is_stitched": False,
                    "jump_pages": [],
                    "start_page": None,
                })

        logger.info(
            f"RSS ingestor: {len(articles)} articles extracted from "
            f"{len(entries)} feed entries"
        )
        return articles
