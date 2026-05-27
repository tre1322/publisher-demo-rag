"""Tier 2 ingestion: single-URL or bulk-URL article import.

Publisher or staff pastes one or more article URLs into the admin UI.
System fetches and extracts each article using the same CMS-agnostic
BeautifulSoup selector cascade as the RSS ingestor.

Good for: specific articles, print-to-web sync, one-off imports of
articles that didn't appear in the RSS feed.

Usage:
    ingestor = URLIngestor(timeout=15)
    articles = ingestor.fetch(["https://site.com/article-1", ...])
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.modules.ingestion.rss_ingestor import _BODY_SELECTORS, _STRIP_TAGS, _MIN_BODY_LEN

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GrandNetworkBot/1.0; "
        "+https://grandnetwork.ai/bot)"
    ),
}


def _extract_metadata(soup: BeautifulSoup, url: str) -> dict[str, str]:
    """Extract title, author, publish date from Open Graph / meta tags."""
    meta: dict[str, str] = {}

    # Title: og:title > twitter:title > <title>
    for sel, attr in [
        ('meta[property="og:title"]', "content"),
        ('meta[name="twitter:title"]', "content"),
    ]:
        el = soup.select_one(sel)
        if el and el.get(attr):
            meta["title"] = str(el[attr]).strip()
            break
    if "title" not in meta:
        title_el = soup.select_one("title")
        if title_el:
            meta["title"] = title_el.get_text(strip=True)

    # Publish date: article:published_time > datePublished JSON-LD > time[datetime]
    date_el = soup.select_one('meta[property="article:published_time"]')
    if date_el and date_el.get("content"):
        raw = str(date_el["content"])
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            meta["publish_date"] = dt.date().isoformat()
        except ValueError:
            pass

    if "publish_date" not in meta:
        time_el = soup.select_one("time[datetime]")
        if time_el and time_el.get("datetime"):
            raw = str(time_el["datetime"])
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                meta["publish_date"] = dt.date().isoformat()
            except ValueError:
                pass

    # Author: author meta > byline class
    author_el = soup.select_one('meta[name="author"]')
    if author_el and author_el.get("content"):
        meta["author"] = str(author_el["content"]).strip()
    else:
        for cls in (".author", ".byline", ".entry-author", ".article-author"):
            el = soup.select_one(cls)
            if el:
                meta["author"] = el.get_text(strip=True)
                break

    return meta


def _extract_body_from_soup(soup: BeautifulSoup, url: str) -> str:
    """Same CMS-agnostic body extraction as rss_ingestor."""
    for selector in _BODY_SELECTORS:
        try:
            el = soup.select_one(selector)
        except Exception:
            continue
        if not el:
            continue
        for noise_sel in _STRIP_TAGS:
            for noise_el in el.select(noise_sel):
                noise_el.decompose()
        text = el.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) >= _MIN_BODY_LEN:
            return text
    return ""


class URLIngestor:
    """Fetch and extract articles from explicit URLs.

    Args:
        timeout: HTTP timeout in seconds per URL.
        headers: Optional extra HTTP headers.
    """

    def __init__(
        self,
        timeout: int = 15,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.timeout = timeout
        self.headers = {**_DEFAULT_HEADERS, **(headers or {})}

    def fetch(self, urls: list[str]) -> list[dict]:
        """Return StandardArticle dicts for the given URLs.

        Skips URLs that fail to fetch or yield no body text.

        Returns:
            List of StandardArticle dicts compatible with
            shared_write_layer.write_articles().
        """
        articles: list[dict] = []

        with httpx.Client(
            timeout=self.timeout,
            headers=self.headers,
            follow_redirects=True,
        ) as client:
            for url in urls:
                url = url.strip()
                if not url:
                    continue

                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    html = resp.text
                except Exception as e:
                    logger.warning(f"Failed to fetch {url!r}: {e}")
                    continue

                soup = BeautifulSoup(html, "html.parser")
                meta = _extract_metadata(soup, url)
                body = _extract_body_from_soup(soup, url)

                if not body:
                    logger.warning(f"Body extraction empty for {url!r} — skipped")
                    continue

                articles.append({
                    "headline": meta.get("title", ""),
                    "body_text": body,
                    "byline": meta.get("author", ""),
                    "publish_date": meta.get("publish_date", ""),
                    "url": url,
                    "content_type": "news",       # caller can override
                    "source_pipeline": "url_import",
                    "extraction_confidence": 0.80,
                    "is_stitched": False,
                    "jump_pages": [],
                    "start_page": None,
                })
                logger.info(
                    f"URL import: extracted {len(body)} chars from {url!r} "
                    f"title={meta.get('title', '')!r:.60}"
                )

        logger.info(
            f"URL ingestor: {len(articles)} articles from {len(urls)} URLs"
        )
        return articles
